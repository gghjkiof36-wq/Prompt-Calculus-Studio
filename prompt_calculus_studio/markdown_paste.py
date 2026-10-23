"""Clipboard MIME selection, shared by source and writing views."""
from .markdown_html import from_html, MAX_INPUT

MARKDOWN_MIMES = ('text/markdown', 'text/x-markdown')


def html_text_fallback(converted, plain):
    """Empty/format-only HTML must not discard the clipboard's actual text."""
    if converted.strip() or plain is None:return converted
    if len(plain.encode('utf-8')) > MAX_INPUT:
        raise ValueError('貼上內容超過 8 MiB；請分段貼上。')
    return plain.replace('\r\n', '\n').replace('\r', '\n')


def clipboard_text(mime):
    for kind in MARKDOWN_MIMES:
        if mime.hasFormat(kind):
            raw = bytes(mime.data(kind))
            if len(raw) > MAX_INPUT:
                raise ValueError('貼上內容超過 8 MiB；請分段貼上。')
            return raw.decode('utf-8-sig').replace('\r\n', '\n').replace('\r', '\n')
    if mime.hasHtml():return html_text_fallback(from_html(mime.html()), mime.text() if mime.hasText() else None)
    return mime.text().replace('\r\n', '\n').replace('\r', '\n') if mime.hasText() else None
