"""Bounded, display-only HTML tables. Source attributes never become CSS/code."""
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
import html
from html.parser import HTMLParser
import math
import re

from .markdown_html import safe_target


MAX_BYTES = 2 * 1024 * 1024
MAX_NODES = 20_000
MAX_DEPTH = 64
VOID = frozenset(('area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
                  'link', 'meta', 'param', 'source', 'track', 'wbr'))
DROP = frozenset(('script', 'style', 'iframe', 'object', 'embed', 'svg', 'math',
                  'form', 'button', 'input', 'select', 'textarea', 'template',
                  'noscript', 'head', 'audio', 'video', 'picture'))
ALLOWED = frozenset(('table', 'thead', 'tbody', 'tfoot', 'tr', 'th', 'td', 'caption',
                     'p', 'div', 'h1', 'h2', 'h3', 'h4', 'h5', 'h6', 'ul', 'ol',
                     'li', 'blockquote', 'strong', 'b', 'em', 'i', 'u', 's', 'del',
                     'code', 'pre', 'br', 'hr', 'sup', 'sub', 'small', 'span', 'a', 'meter'))
TABLE_CLASSES = frozenset(('pcs-metric-grid', 'pcs-summary-card', 'pcs-score-card'))


@dataclass
class Element:
    tag: str
    attrs: dict = field(default_factory=dict)
    children: list = field(default_factory=list)


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element('root')
        self.stack = [self.root]
        self.count = 0

    def handle_starttag(self, tag, attrs):
        self.count += 1
        if self.count > MAX_NODES or len(self.stack) >= MAX_DEPTH:
            raise ValueError('table structure limit')
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID:
            return
        if len(self.stack) == 1 or self.stack[-1].tag != tag:
            raise ValueError('unbalanced table structure')
        self.stack.pop()

    def handle_data(self, data):
        self.stack[-1].children.append(data)

    def handle_decl(self, decl):
        raise ValueError('declaration outside table structure')


def _span(name, value):
    if isinstance(value, str) and re.fullmatch(r'[0-9]{1,5}', value):
        number = int(value)
        minimum, maximum = (0, 65534) if name == 'rowspan' else (1, 1000)
        if minimum <= number <= maximum:
            return str(number)
    return None


def _meter(attrs):
    numbers = {}
    for name in ('min', 'value', 'max'):
        value = attrs.get(name)
        if not isinstance(value, str) or len(value) > 64:
            return None
        if not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?', value):
            return None
        try:
            number = Decimal(value)
        except InvalidOperation:
            return None
        if not number.is_finite() or not math.isfinite(float(number)):
            return None
        numbers[name] = number
    if (not numbers['min'] < numbers['max'] or not float(numbers['min']) < float(numbers['max'])
            or not numbers['min'] <= numbers['value'] <= numbers['max']):
        return None
    return [(name, attrs[name]) for name in ('min', 'value', 'max')]


def _local_style(tag, table_class):
    """Only fixed PCS rules, using the reading view's existing theme tokens."""
    if tag == 'table':
        if table_class == 'pcs-metric-grid':
            return 'width:100%;table-layout:fixed;border-collapse:separate;border-spacing:0'
        if table_class == 'pcs-summary-card':
            return ('width:100%;border-collapse:separate;border-spacing:0;'
                    'border:1px solid var(--border);border-radius:12px;overflow:hidden')
        if table_class == 'pcs-score-card':
            return 'width:100%;border-collapse:separate;border-spacing:0'
    if tag == 'meter':
        return 'width:100%;height:1em;vertical-align:middle'
    if not table_class:
        return ''
    if tag in ('td', 'th'):
        if table_class == 'pcs-metric-grid':
            return 'min-width:0;vertical-align:top;padding:8px 16px 16px 0;border:0'
        if table_class == 'pcs-score-card':
            return 'vertical-align:top;padding:4px 0;border:0'
        return 'vertical-align:top;padding:6px 16px;border:0'
    if tag == 'small':
        return 'color:var(--secondary)'
    if tag == 'p':
        return 'margin:0 0 8px'
    if tag in ('h1', 'h2', 'h3', 'h4', 'h5', 'h6'):
        return 'margin:8px 0'
    return ''


def render_table_html(raw, link_action):
    """Return safe table HTML, or None when the complete block is unsupported.

    Text stays literal: Markdown markers and TeX inside an HTML cell are not
    reparsed. Inline HTML supplies formatting; no source or saved file changes.
    """
    if not re.match(r'\s*<table(?:\s|>)', raw, re.I):
        return None
    if len(raw) > MAX_BYTES or len(raw.encode('utf-8')) > MAX_BYTES:
        return None
    parser = TableParser()
    try:
        parser.feed(raw)
        parser.close()
        if len(parser.stack) != 1:
            return None
    except (ValueError, RecursionError):
        return None

    def render(node, table_class=None):
        if isinstance(node, str):
            return html.escape(node)
        tag = node.tag
        if tag in DROP:
            return ''
        attrs = []
        if tag in ('ol', 'li'):
            name = 'start' if tag == 'ol' else 'value'
            value = node.attrs.get(name)
            if isinstance(value, str) and re.fullmatch(r'-?[0-9]{1,9}', value):
                attrs.append((name, value))
        if tag == 'table':
            # A nested ordinary table does not inherit its parent's card style.
            table_class = node.attrs.get('class')
            if table_class not in TABLE_CLASSES:
                table_class = None
            if table_class:
                attrs.append(('class', table_class))
        if tag in ('td', 'th'):
            for name in ('colspan', 'rowspan'):
                value = _span(name, node.attrs.get(name))
                if value is not None:
                    attrs.append((name, value))
            scope = node.attrs.get('scope')
            if tag == 'th' and scope in ('row', 'col', 'rowgroup', 'colgroup'):
                attrs.append(('scope', scope))
        style = _local_style(tag, table_class)
        if style:
            attrs.append(('style', style))
        children = ''.join(render(child, table_class) for child in node.children)
        if tag == 'meter':
            numbers = _meter(node.attrs)
            if numbers is None:
                return children
            attrs.extend(numbers)
            if node.attrs.get('class') == 'pcs-meter-danger':
                attrs.append(('class', 'pcs-meter-danger'))
        if tag == 'strong' and node.attrs.get('class') == 'pcs-metric-value':
            attrs.append(('class', 'pcs-metric-value'))
        if tag == 'a':
            target = node.attrs.get('href') or ''
            target = safe_target(target) if len(target) <= 8192 else ''
            if not target:
                return children
            attrs.append(('href', link_action(target)))
        if tag not in ALLOWED:
            return children
        attributes = ''.join(' '+name+'="'+html.escape(value, quote=True)+'"' for name, value in attrs)
        output = '<'+tag+attributes+'>'+children+('' if tag in VOID else '</'+tag+'>')
        return '<div class="pcs-table-scroll">'+output+'</div>' if tag == 'table' else output

    return ''.join(render(child) for child in parser.root.children)
