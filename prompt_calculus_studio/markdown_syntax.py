"""Bounded Markdown presentation metadata. Source offsets never come from HTML."""
from dataclasses import dataclass
from functools import lru_cache
import html
import re


@dataclass(frozen=True)
class Span:
    start: int
    end: int
    first: int
    last: int
    style: dict
    literal: bool = False


COLORS = ('#f2d675', '#eead82', '#efa3b6', '#b9a4e8', '#90bbe8', '#9dc6a0')
TEXT_COLORS_LIGHT = ('#806000', '#99501b', '#ae3154', '#7449a8', '#2e649d', '#306b39')


def luminance(color):
    values = [int(color[i:i+2],16)/255 for i in (1,3,5)]
    rgb = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in values]
    return sum(v*w for v,w in zip(rgb,(.2126,.7152,.0722)))


def text_colors(background):
    return TEXT_COLORS_LIGHT if luminance(background) > .5 else COLORS


def display_style(style):
    """Choose default highlight ink for presentation only, never saved source."""
    result = dict(style)
    if result.get('background') and not result.get('color'):
        level = luminance(result['background'])
        dark, light = '#202422', '#f5f5f5'
        result['color'] = dark if (level+.05)/(luminance(dark)+.05) >= (luminance(light)+.05)/(level+.05) else light
    return result


TAG_STYLES = {'b': {'bold': True}, 'strong': {'bold': True}, 'i': {'italic': True},
    'em': {'italic': True}, 'u': {'underline': True}, 's': {'strike': True},
    'del': {'strike': True}, 'sub': {'vertical': 'sub'}, 'sup': {'vertical': 'super'},
    'mark': {'background': COLORS[0]}, 'span': {}, 'p': {}, 'div': {}}


def css_style(raw):
    values = {}
    for item in raw.split(';'):
        if ':' not in item:
            continue
        key, value = (v.strip() for v in item.split(':', 1))
        if key in ('color', 'background-color') and re.fullmatch(r'#[\da-fA-F]{6}', value):
            values['color' if key == 'color' else 'background'] = value.lower()
        elif key == 'font-family' and len(value) <= 100 and not any(c in value for c in '<>\\;'):
            values['family'] = value.strip('"\'')
        elif key == 'font-size' and re.fullmatch(r'\d+(?:\.\d+)?pt', value):
            number = float(value[:-2])
            if 6 <= number <= 96:values['size'] = number
        elif key == 'font-weight' and value in ('bold', '700', 'normal', '400'):values['bold'] = value in ('bold','700')
        elif key == 'font-style' and value in ('italic','normal'):values['italic'] = value == 'italic'
        elif key == 'text-decoration':
            if value == 'none':values.update(underline=False,strike=False)
            if 'underline' in value:values['underline'] = True
            if 'line-through' in value:values['strike'] = True
        elif key == 'vertical-align' and value in ('sub', 'super'):values['vertical'] = value
        elif key == 'text-align' and value in ('left', 'center', 'right', 'justify'):values['align'] = value
        elif key == 'margin-left' and re.fullmatch(r'\d{1,2}em', value):values['indent'] = min(12, int(value[:-2]))
    return values


def spans(text):
    """Paired inline syntax, excluding code. Unknown HTML remains visible source."""
    if not re.search(r'[<$%`*_~=]',text):return []
    result = []
    code = [match.span() for match in re.finditer(r'\]\([^\n]*?\)|https?://[^\s<>]+', text)]
    for match in re.finditer(r'(?<!\\)\$(?:\\.|[^$\n])+\$|<!--.*?(?:-->|$)|%%.*?(?:%%|$)', text):
        item = Span(match.start(), match.end(), match.start(), match.end(), {'code': True}, True)
        code.append((item.start, item.end));result.append(item)
    for match in re.finditer(r'(`+)(.+?)\1(?!`)', text):
        a, b = match.span(2)
        item = Span(match.start(), match.end(), a, b, {'code': True}, True)
        code.append((item.start, item.end));result.append(item)
    def protected(a, b):return any(a < y and b > x for x, y in code)
    stack = []
    for match in re.finditer(r'<(/?)([a-zA-Z][\w-]*)(\s[^<>]*?)?>', text):
        if protected(*match.span()):continue
        closing, tag, attrs = match.groups();tag = tag.lower()
        if tag not in TAG_STYLES:continue
        if not closing:
            style = dict(TAG_STYLES[tag])
            css = re.fullmatch(r'\s+style\s*=\s*(["\'])(.*?)\1\s*', attrs or '', re.I)
            if (attrs or '').strip() and css is None:style = None
            if css:
                declarations = html.unescape(css.group(2))
                supported = {'color','background-color','font-family','font-size','font-weight','font-style','text-decoration','vertical-align','text-align','margin-left'}
                if any(':' not in part or part.split(':',1)[0].strip() not in supported for part in declarations.split(';') if part.strip()):style = None
                else:style.update(css_style(declarations))
            stack.append((tag, match.start(), match.end(), style))
        elif stack and stack[-1][0] == tag:
            _, start, first, style = stack.pop()
            if style is not None:result.append(Span(start, match.end(), first, match.start(), style))
    # Consume longest markers first, so single emphasis cannot steal a bold edge.
    used = list(code)+[match.span() for match in re.finditer(r'<[^>]*>', text)]
    for marker, style in (('***', {'bold': True, 'italic': True}), ('___', {'bold': True, 'italic': True}),
                          ('**', {'bold': True}), ('__', {'bold': True}),
                          ('~~', {'strike': True}), ('==', {'background': COLORS[0]}),
                          ('*', {'italic': True}), ('_', {'italic': True})):
        escaped = re.escape(marker)
        pattern = re.compile(r'(?<![\\'+re.escape(marker[0])+r'])'+escaped+r'(?!'+re.escape(marker[0])+r')(?=\S)(.+?)(?<=\S)(?<!\\)'+escaped+r'(?!'+re.escape(marker[0])+r')')
        for match in pattern.finditer(text):
            a, b = match.span()
            first, last = match.span(1)
            if marker.startswith('_') and ((a and text[a-1].isalnum()) or (b < len(text) and text[b].isalnum())):continue
            if protected(a, first) or protected(last, b):continue
            if any(x <= a < y or x < b <= y for x, y in used):continue
            result.append(Span(a, b, first, last, style))
            used.extend(((a, first), (last, b)))
    return sorted(result, key=lambda item: (item.start, -item.end))


_FENCE=re.compile(r'^[ \t>]*(`{3,}|~{3,})(.*)')
_HEADING=re.compile(r'( {0,3})(#{1,6})\s+(.*?)(?:\s+#+\s*)?$')
_LISTING=re.compile(r'(\s*)([-+*]|\d+[.)])\s+(?:\[([ xX])\]\s+)?(.*)$')


def _parse_line_shape(line,protected):
    container=next((span for span in spans(line) if span.start==0 and span.end==len(line)
                   and (re.match(r'<(?:p|div)\b',line,re.I) or 'align' in span.style or 'indent' in span.style)),None) if line.startswith('<') and not protected else None
    first,last=(container.first,container.last) if container else (0,len(line))
    return container,first,last,None if protected else _HEADING.match(line,first,last),None if protected else _LISTING.match(line,first,last)


_cached_line_shape=lru_cache(maxsize=2048)(_parse_line_shape)


def _line_shape(line,protected):
    return (_cached_line_shape if len(line)<=1024 else _parse_line_shape)(line,protected)


def line_info(text):
    """Line metadata is shared by folding, outline, presentation and commands."""
    offset, fence, yaml, math = 0, None, text.startswith('---\n'), False
    result, counters = [], {}
    for number, raw in enumerate(text.split('\n')):
        line = raw
        protected = False
        marker = _FENCE.match(line)
        if yaml:
            protected = True
            if number and line in ('---', '...'):yaml = False
        elif marker:
            protected = True
            token, rest = marker.groups()
            if fence is None:fence = (token[0], len(token))
            elif token[0] == fence[0] and len(token) >= fence[1] and not rest.strip():fence = None
        elif fence:protected = True
        elif line.strip() == '$$':math = not math;protected = True
        elif math:protected = True
        elif re.match(r'^(?: {4}|\t)', line) and not re.match(r'^\s*(?:[-+*]|\d+[.)])\s+', line):protected = True
        container,first,last,heading,listing=_line_shape(line,protected)
        display_marker = None
        if listing:
            indent, marker = len(listing.group(1)), listing.group(2)
            counters = {depth:value for depth,value in counters.items() if depth <= indent}
            if marker[0].isdigit():
                value = counters.get(indent,int(marker[:-1]));display_marker=str(value)+marker[-1];counters[indent]=value+1
            else:counters.pop(indent,None)
        elif line.strip():counters.clear()
        result.append(dict(number=number, start=offset, end=offset+len(line), text=line,
                           protected=protected, heading=heading, listing=listing, display_marker=display_marker,
                           container=container, content_start=first, content_end=last))
        offset += len(line)+1
    return result


def escaped_markers(text, items=None):
    items = spans(text) if items is None else items
    return [match.start() for match in re.finditer(r'\\[!"#$%&\x27()*+,\-./:;<=>?@\[\\\]^_`{|}~]',text)
            if not any(item.literal and item.start <= match.start() < item.end for item in items)]


def visible_text(text):
    hidden = set()
    for item in spans(text):
        hidden.update(range(item.start, item.first));hidden.update(range(item.last, item.end))
    hidden.update(escaped_markers(text) if '\\' in text else [])
    if not hidden:return html.unescape(text)
    return html.unescape(''.join(c for i, c in enumerate(text) if i not in hidden))


def style_css(style):
    values = []
    for key, css in (('family', 'font-family'), ('size', 'font-size'), ('color', 'color'),
                     ('background', 'background-color'), ('align', 'text-align')):
        if key in style:
            value = str(style[key])+('pt' if key == 'size' else '')
            values.append(css+':'+value)
    if 'bold' in style:values.append('font-weight:'+('bold' if style['bold'] else 'normal'))
    if 'italic' in style:values.append('font-style:'+('italic' if style['italic'] else 'normal'))
    decorations = [value for key, value in (('underline', 'underline'), ('strike', 'line-through')) if style.get(key)]
    if decorations:values.append('text-decoration:'+' '.join(decorations))
    elif 'underline' in style or 'strike' in style:values.append('text-decoration:none')
    if style.get('vertical'):values.append('vertical-align:'+style['vertical'])
    if style.get('indent'):values.append('margin-left:'+str(style['indent'])+'em')
    return ';'.join(values)


def inline_html(text):
    """Render only recognized styles; never pass document HTML attributes through."""
    items = spans(text)
    if not items and '\\' not in text:
        # Most manuscript prose has no inline formatting. Preserve the same
        # entity escaping without building a Python style run per character.
        return html.escape(html.unescape(text))
    hidden = set()
    for item in items:
        hidden.update(range(item.start, item.first));hidden.update(range(item.last, item.end))
    hidden.update(escaped_markers(text,items))
    output, run, previous = [], [], None
    def flush():
        if not run:return
        content = html.escape(html.unescape(''.join(run)))
        if previous and previous.get('code'):content = '<code>'+content+'</code>'
        css = style_css(display_style(previous or {}))
        output.append('<span style="'+html.escape(css, quote=True)+'">'+content+'</span>' if css else content)
        run.clear()
    for index, character in enumerate(text):
        if index in hidden:continue
        active = {}
        for item in items:
            if item.first <= index < item.last:active.update(item.style)
        if active != previous:flush();previous = active
        run.append(character)
    flush()
    return ''.join(output)


def reading_html(text, hard_breaks=False, *, lines=None, web=False):
    """B text presentation; advanced blocks retain source until C handles them."""
    output, paragraph = [], []
    def flush():
        if paragraph:
            output.append('<p>'+(' <br/>' if hard_breaks else ' ').join(paragraph)+'</p>')
            paragraph.clear()
    for item in line_info(text) if lines is None else lines:
        line, number = item['text'], item['number']
        if item['protected']:
            flush();output.append('<pre id="line-'+str(number)+'">'+html.escape(line)+'</pre>');continue
        if not line.strip():flush();continue
        heading, listing = item['heading'], item['listing']
        if heading:
            flush();level = len(heading.group(2));css=style_css(item['container'].style) if item['container'] else ''
            output.append(f'<h{level} id="line-{number}" style="'+html.escape(css,quote=True)+'">'+inline_html(heading.group(3))+f'</h{level}>')
        elif listing:
            flush();indent, marker, checked, body = listing.groups()
            if checked is not None:
                ticked=checked.lower()=='x'
                lead=('<input type="checkbox" disabled'+(' checked' if ticked else '')+'>') if web else ('<img src="pcs-task:'+('checked' if ticked else 'open')+'" width="14" height="14"/>')
            else:lead='•' if marker in '-+*' else item['display_marker']
            output.append(f'<p id="line-{number}" style="margin-left:{len(indent)*8}px">'+lead+' '+inline_html(body)+'</p>')
        elif re.fullmatch(r'\s{0,3}([-*_])(?:\s*\1){2,}\s*', line):
            flush();output.append('<hr/>')
        elif line.lstrip().startswith('>'):
            flush();output.append('<blockquote>'+inline_html(re.sub(r'^\s*>\s?', '', line))+'</blockquote>')
        else:
            containers = [span for span in spans(line) if span.start == 0 and span.end == len(line) and ('align' in span.style or 'indent' in span.style)]
            if containers:
                flush();item = containers[0]
                output.append('<p style="'+html.escape(style_css(item.style), quote=True)+'">'+inline_html(line[item.first:item.last])+'</p>')
            else:
                hard_break = line.endswith('  ') or (line.endswith('\\') and not line.endswith('\\\\'))
                content = line[:-2] if line.endswith('  ') else line[:-1] if hard_break else line
                paragraph.append(inline_html(content)+('<br/>' if hard_break else ''))
    flush()
    return '<html><body>'+''.join(output)+'</body></html>'
