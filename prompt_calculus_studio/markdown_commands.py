"""Source-preserving B text and paragraph commands; one transaction per action."""
import html
import re
from .markdown_source import SourceEdit
from .markdown_syntax import spans, line_info, style_css


def selection_lines(text, start, end):
    return [item for item in line_info(text) if item['start'] <= start <= item['end']
            or start < item['start'] < end]


def prose_ranges(text, start, end):
    result = []
    for line in selection_lines(text, start, end):
        if line['protected']:
            continue
        a, b = max(start, line['start']), min(end, line['end'])
        prefix = line['heading'] or line['listing']
        if prefix:
            a = max(a, line['start']+prefix.start(3 if line['heading'] else 4))
        blocked = []
        for span in spans(line['text']):
            base = line['start']
            if span.literal:blocked.append((base+span.start, base+span.end))
            else:blocked.extend(((base+span.start, base+span.first), (base+span.last, base+span.end)))
        # Keep unknown tags, link targets and escaped punctuation untouched.
        for match in re.finditer(r'<[^>]*>|\]\([^)]*\)|!?\[|\]', line['text']):
            blocked.append((line['start']+match.start(), line['start']+match.end()))
        cursor = a
        for x, y in sorted(blocked):
            if y <= cursor or x >= b:continue
            if cursor < x:result.append((cursor, min(x, b)))
            cursor = max(cursor, y)
        if cursor < b:result.append((cursor, b))
    return [(a, b) for a, b in result if a < b and text[a:b].strip()]


def format_edits(snapshot, start, end, style):
    css = style_css(style)
    if not css:raise ValueError('尚未指定文字樣式。')
    if any(c in css for c in '<>"\\'):raise ValueError('這個字體或樣式值不適用。')
    for match in re.finditer(r'&(?:#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);', snapshot.text):
        if start < match.end() and end > match.start() and not (start <= match.start() and end >= match.end()):
            raise ValueError('請選取完整的文字字元，避免切斷 entity 語法。')
    result = []
    for a, b in prose_ranges(snapshot.text, start, end):
        text = snapshot.text[a:b]
        # These are original source fragments, not HTML-escaped display text.
        # Existing entities and Markdown remain source inside a safe wrapper.
        result.append(SourceEdit(a, b, '<span style="'+html.escape(css, quote=True)+'">'+text+'</span>'))
    if not result:raise ValueError('請選取可排版的正文；程式碼與公式使用原文編輯。')
    return result


def _merge_changes(removals, insertions):
    removals = sorted(set(removals))
    merged = []
    for a, b in removals:
        if merged and a <= merged[-1][1]:merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:merged.append((a, b))
    result = []
    for a, b in merged:
        # Nested delimiter removals can absorb an outer wrapper's split point.
        # Keep every insertion in source order at the merged boundary.
        boundaries = sorted(at for at in insertions if a <= at <= b)
        value = ''.join(''.join(insertions.pop(at)) for at in boundaries)
        result.append(SourceEdit(a, b, value))
    result.extend(SourceEdit(at, at, ''.join(values)) for at, values in insertions.items())
    return sorted(result, key=lambda item: (item.start, item.end))


def clear_edits(snapshot, start, end, keys=None):
    removals, insertions = [], {}
    for line in selection_lines(snapshot.text, start, end):
        if line['protected']:continue
        base = line['start']
        for span in spans(line['text']):
            if span.literal or not span.style:continue
            if keys is not None and not keys.intersection(span.style):continue
            a, b, first, last = (base+v for v in (span.start, span.end, span.first, span.last))
            if start >= last or end <= first:continue
            # Splitting Markdown delimiters can create ambiguous *** or ____.
            # Only the affected wrapper is split into explicit, readable HTML;
            # prose and link targets remain original source slices.
            opening = '<span style="'+html.escape(style_css(span.style), quote=True)+'">'
            closing = '</span>'
            removals.extend(((a, first), (last, b)))
            if start > first:
                insertions.setdefault(a, []).append(opening)
                insertions.setdefault(start, []).insert(0, closing)
            if end < last:
                insertions.setdefault(end, []).append(opening)
                insertions.setdefault(b, []).insert(0, closing)
            remaining = {key:value for key,value in span.style.items() if keys is not None and key not in keys}
            if remaining:
                first_selected, last_selected = max(start, first), min(end, last)
                insertions.setdefault(first_selected, []).append('<span style="'+html.escape(style_css(remaining), quote=True)+'">')
                insertions.setdefault(last_selected, []).insert(0, closing)
    return _merge_changes(removals, insertions)


def move_section_edits(snapshot, start, end, destination):
    if not 0 <= start < end <= len(snapshot.text) or not 0 <= destination <= len(snapshot.text):
        raise ValueError('章節位置已變更，請重新拖曳。')
    if start <= destination <= end:return []
    fragment = snapshot.text[start:end]
    if destination < len(snapshot.text) and not fragment.endswith('\n'):fragment += '\n'
    if destination and snapshot.text[destination-1] != '\n':fragment = '\n'+fragment
    return [SourceEdit(start, end, ''), SourceEdit(destination, destination, fragment)]


def paragraph_edits(snapshot, start, end, command, value=None):
    edits = []
    for item in selection_lines(snapshot.text, start, end):
        if item['protected']:continue
        text = item['text'];a, b = item['start'], item['end']
        if command == 'heading':
            level = int(value)
            first,last=item['content_start'],item['content_end']
            old = re.compile(r' {0,3}#{1,6}\s+').match(text,first,last)
            edits.append(SourceEdit(a+first, a+(old.end() if old else first), '#'*level+' ' if level else ''))
        elif command in ('bullet', 'ordered', 'task', 'quote'):
            old = re.match(r'^(\s*)(?:[-+*]|\d+[.)])\s+(?:\[[ xX]\]\s+)?', text)
            if command == 'quote':old = re.match(r'^(\s*)>\s?', text)
            indent = old.group(1) if old else re.match(r'^\s*', text).group()
            prefix = {'bullet': '- ', 'ordered': '1. ', 'task': '- [ ] ', 'quote': '> '}[command]
            edits.append(SourceEdit(a, a+(old.end() if old else len(indent)), indent+prefix))
        elif command == 'indent':edits.append(SourceEdit(a, a, ' '*int(value or 4)))
        elif command == 'outdent':
            prefix = re.match(r'^(?:\t| {1,'+str(int(value or 4))+r'})', text)
            if prefix:edits.append(SourceEdit(a, a+prefix.end(), ''))
        elif command in ('align', 'paragraph_indent'):
            style = {'align': str(value)} if command == 'align' else {'indent': int(value)}
            containers = [span for span in spans(text) if span.start == 0 and span.end == len(text)
                          and (re.match(r'<(?:p|div)\b',text,re.I) or 'align' in span.style or 'indent' in span.style)]
            if containers:
                span = containers[0];style = dict(span.style, **style)
                if not style_css(style):
                    edits.extend([SourceEdit(a,a+span.first,''),SourceEdit(a+span.last,b,'')])
                else:edits.append(SourceEdit(a, a+span.first, '<p style="'+style_css(style)+'">'))
            else:edits.append(SourceEdit(a, b, '<p style="'+style_css(style)+'">'+text+'</p>'))
    return edits


def continuation(text, position):
    start = text.rfind('\n', 0, position)+1
    line = text[start:position]
    match = re.match(r'^(\s*)([-+*]|\d+[.)])\s+(?:\[([ xX])\]\s+)?(.*)$', line)
    if match:
        indent, marker, checked, body = match.groups()
        if not body.strip():return SourceEdit(start, position, '')
        if marker[0].isdigit():marker = str(int(marker[:-1])+1)+marker[-1]
        return SourceEdit(position, position, '\n'+indent+marker+' '+('[ ] ' if checked is not None else ''))
    quote = re.match(r'^(\s*>\s?)(.*)$', line)
    if quote:
        return SourceEdit(start, position, '') if not quote.group(2).strip() else SourceEdit(position, position, '\n'+quote.group(1))
    return SourceEdit(position, position, '\n'+re.match(r'^\s*', line).group())


def heading_sections(text,lines=None):
    lines = line_info(text) if lines is None else lines;result = [];parents=[]
    for item in lines:
        if item['heading']:
            depth = len(item['heading'].group(2))
            while parents and result[parents[-1]][2]>=depth:
                result[parents.pop()][1]=item['start']
            parents.append(len(result));result.append([item['start'],len(text),depth,item['heading'].group(3),item['number']])
    return [tuple(section) for section in result]
