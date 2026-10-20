"""Bounded HTML to Markdown conversion; no resource fetch or execution."""
from dataclasses import dataclass, field
import html
from html.parser import HTMLParser
import re
from urllib.parse import urlsplit
from .markdown_syntax import css_style, style_css

MAX_INPUT = 8 * 1024 * 1024
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'source', 'wbr'}
SKIP = {'script', 'style', 'iframe', 'object', 'noscript', 'head', 'button', 'nav', 'form', 'svg'}
OPTIONAL = {'p', 'li', 'td', 'th', 'tr', 'thead', 'tbody', 'tfoot', 'html', 'body'}


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class PasteParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element('body')
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > 100_000 or len(self.stack) > 128:
            raise ValueError('貼上內容的結構過大，請縮小範圍或貼上純文字。')
        if tag in OPTIONAL and self.stack[-1].tag == tag and len(self.stack) > 1:
            self.stack.pop()
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:return
        for index in range(len(self.stack)-1, 0, -1):
            if self.stack[index].tag == tag:
                if any(n.tag not in OPTIONAL for n in self.stack[index+1:]):
                    raise ValueError('HTML 標籤不完整；請重新複製或貼上純文字。')
                del self.stack[index:]
                return
        if tag not in OPTIONAL:raise ValueError('HTML 標籤不完整；請重新複製或貼上純文字。')

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def literal(node):
    if isinstance(node, str):return node
    if node.tag in SKIP:return ''
    return ''.join(literal(child) for child in node.children)


def descendants(node, tag):
    for child in node.children:
        if isinstance(child, Element):
            if child.tag == tag:yield child
            yield from descendants(child, tag)


def fence(content, language=''):
    marker = '`' * max(3, 1+max((len(m[0]) for m in re.finditer('`+', content)), default=0))
    return marker+language+'\n'+content+('' if content.endswith('\n') else '\n')+marker


def escape(text):
    return re.sub(r'([\\`*_\[\]<>])', r'\\\1', text)


def safe_target(value):
    value = value or ''
    if any(ord(c) < 32 for c in value):return ''
    try:scheme = urlsplit(value).scheme
    except ValueError:return ''
    if scheme not in ('', 'http', 'https', 'mailto') or value.startswith(('//', '\\')):return ''
    return value.replace(' ', '%20').replace('(', '%28').replace(')', '%29').replace('<', '%3C').replace('>', '%3E')


def complex_table(node):
    """Retain cell spans as inert source, never guess a rectangular grid."""
    allowed = {'table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption', 'br'}
    def serialize(item):
        if isinstance(item, str):return html.escape(item)
        if item.tag in SKIP:return ''
        children = ''.join(serialize(c) for c in item.children)
        if item.tag not in allowed:return children
        attrs = ''.join(' '+key+'="'+html.escape(str(value), quote=True)+'"'
                        for key, value in item.attrs.items() if key in ('rowspan', 'colspan', 'align'))
        return '<'+item.tag+attrs+'>'+children+('' if item.tag == 'br' else '</'+item.tag+'>')
    return '\n\n> 此表格含合併儲存格，以下保留表格原文。\n\n'+fence(serialize(node), 'html')+'\n\n'


def render_table(node):
    from .markdown_table_cells import encode_cell
    cells = list(descendants(node, 'td'))+list(descendants(node, 'th'))
    if (any(c.attrs.get('rowspan', '1') != '1' or c.attrs.get('colspan', '1') != '1' for c in cells)
            or next(descendants(node, 'table'), None)):
        return complex_table(node)
    rows = [[c for c in row.children if isinstance(c, Element) and c.tag in ('td', 'th')]
            for row in descendants(node, 'tr')]
    rows = [r for r in rows if r]
    if not rows:return '\n\n'+escape(literal(node))+'\n\n'
    width = max(map(len, rows))
    lines = []
    for row in rows:
        values = [encode_cell(render(c, table=True).strip()) for c in row]
        lines.append('| '+' | '.join(values+['']*(width-len(values)))+' |')
    aligns = []
    for cell in rows[0]:
        match = re.search(r'text-align\s*:\s*(left|right|center)', cell.attrs.get('style', ''))
        alignment = match[1] if match else cell.attrs.get('align')
        aligns.append({'left': ':---', 'right': '---:', 'center': ':---:'}.get(alignment, '---'))
    lines.insert(1, '| '+' | '.join(aligns+['---']*(width-len(aligns)))+' |')
    return '\n\n'+'\n'.join(lines)+'\n\n'


def render_children(children, *, table=False):
    pieces = []
    for child in children:
        value = render(child, table=table)
        # HTML indentation between block elements is not a blank paragraph.
        # Join boundaries here; never normalize the generated code's contents.
        if not value:continue
        if isinstance(child, str) and not value.strip() and pieces and pieces[-1].endswith('\n'):
            continue
        if value.startswith('\n\n'):
            if pieces:pieces[-1] = pieces[-1].rstrip(' \t\n')
            pieces.append('\n\n'+value.lstrip('\n'))
        else:
            pieces.append(value)
    return ''.join(pieces)


def render(node, *, table=False):
    if isinstance(node, str):
        value = escape(re.sub(r'\s+', ' ', node))
        return re.sub(r'(\\+)\|', lambda m: '\\'*(len(m[1])//2)+'|', value) if table else value
    tag, attrs = node.tag, node.attrs
    if tag == 'script' and (attrs.get('type') or '').startswith('math/tex'):
        formula = ''.join(c for c in node.children if isinstance(c, str))
        return '\n\n$$\n'+formula+'\n$$\n\n' if 'mode=display' in attrs['type'] else '$'+formula+'$'
    if tag in SKIP or attrs.get('role') in ('toolbar', 'button', 'navigation'):return ''
    classes = (attrs.get('class') or '').split()
    if tag == 'math' or attrs.get('data-latex') or attrs.get('data-tex') or any(c in classes for c in ('katex', 'katex-display', 'math', 'math-inline', 'math-display')):
        formula = attrs.get('data-latex') or attrs.get('data-tex')
        if not formula:
            formula = next((literal(n) for n in descendants(node, 'annotation')
                            if n.attrs.get('encoding') in ('application/x-tex', 'application/x-latex')), None)
        if formula:
            block = attrs.get('display') == 'block' or any(c in classes for c in ('katex-display', 'math-display'))
            return '\n\n$$\n'+formula+'\n$$\n\n' if block else '$'+formula+'$'
        return escape(literal(node))
    if tag == 'pre':
        code = next(descendants(node, 'code'), node)
        match = re.search(r'(?:language-|lang-)([\w.+-]+)', code.attrs.get('class', ''))
        return '\n\n'+fence(literal(node), match[1] if match else '')+'\n\n'
    if tag == 'code':
        value = literal(node)
        marker = '`'*(1+max((len(m[0]) for m in re.finditer('`+', value)), default=0))
        pad = ' ' if value.startswith(('`', ' ')) or value.endswith(('`', ' ')) else ''
        return marker+pad+value+pad+marker
    if tag == 'table':return render_table(node)
    if tag in ('ul', 'ol'):
        try:number = int(attrs.get('start', '1'))
        except ValueError:number = 1
        items = []
        for item in node.children:
            if not isinstance(item, Element) or item.tag != 'li':continue
            if tag == 'ol' and str(item.attrs.get('value', '')).lstrip('-').isdigit():
                number = int(item.attrs['value'])
            marker = f'{number}. ' if tag == 'ol' else '- '
            content = render_children(item.children, table=table).strip()
            lines = content.splitlines() or ['']
            items.append(marker+lines[0]+''.join('\n'+' '*len(marker)+line if line else '\n' for line in lines[1:]))
            number += 1
        return '\n\n'+'\n'.join(items)+'\n\n'
    if tag == 'input':
        return '[x] ' if attrs.get('type') == 'checkbox' and 'checked' in attrs else '[ ] ' if attrs.get('type') == 'checkbox' else ''
    if tag == 'br':return '\n' if table else '  \n'
    if tag == 'hr':return '\n\n---\n\n'
    if tag == 'img':
        label = escape(attrs.get('alt') or '圖片')
        target = safe_target(attrs.get('src'))
        return '!['+label+']('+target+')' if target else '['+label+'：來源未提供可用圖片連結]'
    value = render_children(node.children, table=table)
    if tag == 'blockquote':
        return '\n\n'+'\n'.join('> '+line for line in value.strip().splitlines())+'\n\n'
    if tag == 'a':
        target = safe_target(attrs.get('href'))
        return '['+value+']('+target+')' if target else value
    marks = {'b': '**', 'strong': '**', 'i': '*', 'em': '*', 'del': '~~', 's': '~~'}
    if tag in marks:return marks[tag]+value+marks[tag]
    if tag == 'u':return '<u>'+value+'</u>'
    if re.fullmatch(r'h[1-6]', tag):return '\n\n'+'#'*int(tag[1])+' '+value.strip()+'\n\n'
    style = css_style(attrs.get('style') or '')
    if style:value = '<span style="'+html.escape(style_css(style), quote=True)+'">'+value+'</span>'
    if tag in ('p', 'div', 'section', 'article'):return '\n\n'+value.strip()+'\n\n' if value.strip() else ''
    return value


def from_html(source):
    if len(source.encode('utf-8')) > MAX_INPUT:
        raise ValueError('貼上內容超過 8 MiB；請縮小範圍或貼上純文字。')
    parser = PasteParser()
    parser.feed(source)
    parser.close()
    if any(n.tag not in OPTIONAL for n in parser.stack[1:]):
        raise ValueError('HTML 標籤不完整；請重新複製或貼上純文字。')
    result = render(parser.root).strip()
    if len(result.encode('utf-8')) > MAX_INPUT:
        raise ValueError('轉換結果超過文件容量；請分段貼上。')
    return result
