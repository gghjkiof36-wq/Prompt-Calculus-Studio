"""TeX delimiters shared by LLM Markdown reading and source protection.

Only the display projection is tokenized. Stored Markdown, TeX spelling and
numeric precision remain the document's source, never a converted equation.
"""
import re


def escaped(text, at):
    before = at - 1
    while before >= 0 and text[before] == '\\':
        before -= 1
    return (at - before - 1) % 2 != 0


def closing(text, marker, start=0):
    """Locate an unescaped delimiter (matrix row breaks remain TeX content)."""
    while True:
        at = text.find(marker, start)
        if at < 0:
            return -1
        if not escaped(text, at):
            return at
        start = at + len(marker)


def bracket_ranges(text):
    """Complete bracket-delimited runs, used for literal source and HTML paste."""
    at = 0
    missing = set()
    for match in re.finditer(r'\\[([]', text):
        start = match.start()
        if start < at or escaped(text, start):
            continue
        end_marker = r'\)' if match.group() == r'\(' else r'\]'
        if end_marker in missing:
            continue
        end = closing(text, end_marker, match.end())
        if end < 0:
            missing.add(end_marker)
        if end >= 0 and text[match.end():end].strip():
            at = end + 2
            yield start, at


def math_ranges(text):
    """Complete TeX runs in HTML text/cells, before Markdown escape layers."""
    at = 0
    missing = set()
    for match in re.finditer(r'\\[([]|\${1,2}', text):
        start = match.start()
        if start < at or escaped(text, start):
            continue
        opening = match.group()
        end_marker = {r'\(': r'\)', r'\[': r'\]'}.get(opening, opening)
        if end_marker in missing or (opening == '$' and start > 0 and text[start - 1].isdigit()):
            continue
        end = closing(text, end_marker, match.end())
        if end < 0:
            missing.add(end_marker)
            continue
        if opening == '$' and end + 1 < len(text) and text[end + 1].isdigit():
            continue
        if text[match.end():end].strip():
            at = end + len(end_marker)
            yield start, at


def math_plugin(md):
    def inline(state, silent):
        opening = state.src[state.pos:state.pos + 2]
        if opening in (r'\(', r'\['):
            end_marker = r'\)' if opening == r'\(' else r'\]'
            display = opening == r'\['
        elif state.src[state.pos] == '$':
            opening = '$$' if opening == '$$' else '$'
            end_marker = opening
            display = opening == '$$'
            # A leading price is not paired with the next price. The explicit
            # position check matters: src[-1] would reject "$2$ = 2" at start.
            if not display and state.pos > 0 and state.src[state.pos - 1].isdigit():
                return False
        else:
            return False
        end = closing(state.src, end_marker, state.pos + len(opening))
        if end < 0 or end + len(end_marker) > state.posMax:
            return False
        if opening == '$' and end + 1 < state.posMax and state.src[end + 1].isdigit():
            return False
        source = state.src[state.pos + len(opening):end]
        if not source.strip():return False
        if not silent:
            token = state.push('math_inline_double' if display else 'math_inline', 'math', 0)
            token.content = source
            token.markup = opening
        state.pos = end + len(end_marker)
        return True

    def block(state, start, end, silent):
        if state.sCount[start] - state.blkIndent >= 4:
            return False
        pos = state.bMarks[start] + state.tShift[start]
        opening = state.src[pos:pos + 2]
        if opening not in (r'\[', '$$'):
            return False
        end_marker = r'\]' if opening == r'\[' else '$$'
        for last in range(start, end):
            # Do not consume a sibling outside this list/quote container.
            if last > start and state.sCount[last] < state.blkIndent and not state.isEmpty(last):
                return False
            begin = pos + 2 if last == start else state.bMarks[last] + state.tShift[last]
            value = state.src[begin:state.eMarks[last]]
            finish = closing(value, end_marker)
            if finish < 0:
                continue
            if value[finish + 2:].strip():
                return False
            # getLines removes Markdown's quote/list prefix, not TeX row breaks.
            source = state.getLines(start, last + 1, state.blkIndent, False).lstrip(' \t')
            source = source[2:source.rfind(end_marker)]
            if not source.strip():
                return False
            if silent:
                return True
            token = state.push('math_block', 'math', 0)
            token.block = True
            token.content = source
            token.markup = opening
            token.map = [start, last + 1]
            state.line = last + 1
            return True
        return False

    md.inline.ruler.before('escape', 'pcs_math', inline)
    # Both display syntaxes need container-aware extraction. The default
    # dollar plugin slices raw source and retains `>` on quoted matrix rows.
    md.block.ruler.before('fence', 'pcs_math', block,
                         {'alt': ['paragraph', 'reference', 'blockquote', 'list']})
