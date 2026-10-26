"""Bounded chat Markdown projection, with no filesystem or network resolution."""
from dataclasses import dataclass, field
from functools import lru_cache
import base64
import html
import re

from markdown_it import MarkdownIt
from mdit_py_plugins.footnote import footnote_plugin
from pygments import highlight
from pygments.formatters import HtmlFormatter
from pygments.lexers import get_lexer_by_name

from ..capture_images import png_bytes
from ..markdown_math import math_plugin
from ..markdown_render import safe_tag
from ..markdown_table_html import render_table_html

MAX_RENDER_CHARS = 256_000
MAX_MATH = 128
MAX_FORMULA_CHARS = 12_000
MAX_IMAGES = 8
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_IMAGE_PIXELS = 4_000_000
MAX_IMAGE_SIDE = 8192
MAX_HIGHLIGHT_CHARS = 20_000


@dataclass
class ChatProjection:
    body: str = ''
    math: list = field(default_factory=list)
    codes: list = field(default_factory=list)
    links: list = field(default_factory=list)


def needs_rich(text):
    return len(text) <= MAX_RENDER_CHARS and bool(re.search(
        r'(?m)^ {0,3}(?:`{3}|~{3}|\|)|!\[|\$|\\[([]|</?(?:sup|sub|table)\b', text))


@lru_cache(maxsize=8)
def _raster_png(target):
    """Decode only bounded, self-contained raster data; never resolve a path."""
    try:
        mime, separator, encoded = target.partition(';base64,')
        formats = {'data:image/png': 'PNG', 'data:image/jpeg': 'JPEG', 'data:image/webp': 'WEBP'}
        if not separator or mime not in formats or len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
            return ''
        data = base64.b64decode(encoded, validate=True)
        if len(data) > MAX_IMAGE_BYTES:
            return ''
        # The application already ships Qt's raster decoders. They operate on
        # a bounded memory buffer here, never on a supplied filename or URL.
        from PySide6.QtCore import QBuffer, QByteArray, QIODevice
        from PySide6.QtGui import QImageReader
        buffer = QBuffer()
        buffer.setData(QByteArray(data))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer)
        size = reader.size()
        width, height = size.width(), size.height()
        if (bytes(reader.format()).decode('ascii').upper() != formats[mime]
                or reader.imageCount() != 1 or min(width, height) <= 0
                or max(width, height) > MAX_IMAGE_SIDE or width * height > MAX_IMAGE_PIXELS):
            return ''
        image = reader.read()
        if image.isNull():
            return ''
        output = QBuffer()
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(output, 'PNG'):
            return ''
        encoded_png = bytes(output.data())
        if len(encoded_png) > MAX_IMAGE_BYTES:
            return ''
        canonical = 'data:image/png;base64,' + base64.b64encode(encoded_png).decode('ascii')
        png_bytes(canonical)
        return canonical
    except (ValueError, OSError, ImportError):
        return ''


def project_chat(text):
    """Reuse the document math/safe-HTML grammar, excluding note/file embeds."""
    result = ChatProjection()
    if len(text) > MAX_RENDER_CHARS:
        result.body = '<pre class="plain-long">' + html.escape(text) + '</pre>'
        return result
    md = MarkdownIt('commonmark', {'html': True, 'breaks': False}).enable(['table', 'strikethrough'])
    md.use(math_plugin).use(footnote_plugin)

    def link(target, wiki=False):
        index = len(result.links)
        result.links.append(target)
        return f'pcs-chat:link/{index}'

    md.add_render_rule('link_open', lambda self, tokens, i, options, env:
        '<a href="' + link(tokens[i].attrGet('href') or '') + '">')
    md.add_render_rule('html_inline', lambda self, tokens, i, options, env: safe_tag(tokens[i].content))

    def block_html(self, tokens, i, options, env):
        source = tokens[i].content
        table = render_table_html(source, link)
        return table if table is not None else '<pre class="unknown">' + html.escape(source) + '</pre>'
    md.add_render_rule('html_block', block_html)

    def fence(self, tokens, i, options, env):
        token = tokens[i]
        language = token.info.strip().split(' ', 1)[0][:80]
        code = token.content
        index = len(result.codes)
        result.codes.append(code)
        try:
            painted = (highlight(code, get_lexer_by_name(language), HtmlFormatter(nowrap=True))
                       if language and len(code) <= MAX_HIGHLIGHT_CHARS else html.escape(code))
        except Exception:
            painted = html.escape(code)
        return (f'<section class="code"><header><span>{html.escape(language or "純文字")}</span>'
                f'<a href="pcs-chat:copy/{index}">複製程式碼</a></header><pre><code>{painted}</code></pre></section>')
    md.add_render_rule('fence', fence)
    md.add_render_rule('code_block', fence)

    def math_rule(block):
        def inner(self, tokens, i, options, env):
            source = tokens[i].content
            if len(result.math) >= MAX_MATH or len(source) > MAX_FORMULA_CHARS:
                return '<code class="formula-source">' + html.escape(source) + '</code>'
            index = len(result.math)
            result.math.append(dict(source=source, block=block))
            tag = 'div' if tokens[i].block else 'span'
            return f'<{tag} class="formula" data-math="{index}"></{tag}>'
        return inner
    md.add_render_rule('math_inline', math_rule(False))
    md.add_render_rule('math_inline_double', math_rule(True))
    md.add_render_rule('math_block', math_rule(True))
    images = 0

    def image(self, tokens, i, options, env):
        nonlocal images
        token = tokens[i]
        target, alt = token.attrGet('src') or '', token.content or '圖片'
        images += 1
        raster = _raster_png(target) if images <= MAX_IMAGES else ''
        if raster:
            return '<img src="' + html.escape(raster, quote=True) + '" alt="' + html.escape(alt, quote=True) + '">'
        # Model text never grants access to paths, files, SVG or network URLs.
        return '<span class="missing">' + html.escape(alt) + ' · 圖片尚未提供可顯示的內容</span>'
    md.add_render_rule('image', image)
    md.add_render_rule('table_open', lambda self, tokens, i, options, env: '<div class="pcs-table-scroll"><table>')
    md.add_render_rule('table_close', lambda self, tokens, i, options, env: '</table></div>')
    result.body = md.render(text)
    return result
