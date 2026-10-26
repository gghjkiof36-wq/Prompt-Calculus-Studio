"""View-only separation of a leading model reasoning envelope.

The persisted response remains the source of truth. This parser consumes the
whole accumulated response, so tags split across streamed chunks need no
separate mutable buffer and cannot leak into the displayed text for one frame.
"""
from dataclasses import dataclass


_OPEN = '<think>'
_CLOSE = '</think>'


@dataclass(frozen=True)
class ThinkingParts:
    thinking: str = ''
    answer: str = ''
    has_thinking: bool = False
    closed: bool = False
    pending_open: bool = False


def _leading_content(text: str) -> str:
    """Skip a BOM/blank lines, while preserving Markdown indented code."""
    body = text.removeprefix('\ufeff')
    while '\n' in body:
        line, _, rest = body.partition('\n')
        if line.strip(' \t\r'):
            break
        body = rest
    indentation = len(body) - len(body.lstrip(' '))
    if indentation >= 4 or body[indentation:].startswith('\t'):
        return body
    return body[indentation:]


def split_thinking(text: str, *, receiving: bool = False) -> ThinkingParts:
    """Recognize one exact leading ``<think>`` block, never tags in prose.

    In-flight prefixes of delimiters stay hidden until they are disambiguated.
    A terminal incomplete opening tag is ordinary text; an incomplete closing
    tag remains part of the readable thinking text. No source bytes are edited.
    """
    body = _leading_content(text)
    if not body.startswith(_OPEN):
        if receiving and body and _OPEN.startswith(body):
            return ThinkingParts(pending_open=True)
        return ThinkingParts(answer=text)
    content = body[len(_OPEN):]
    end = content.find(_CLOSE)
    if end >= 0:
        return ThinkingParts(content[:end].strip('\r\n'), content[end + len(_CLOSE):].strip('\r\n'), True, True)
    if receiving:
        for size in range(min(len(_CLOSE) - 1, len(content)), 0, -1):
            if content.endswith(_CLOSE[:size]):
                content = content[:-size]
                break
    return ThinkingParts(thinking=content.strip('\r\n'), has_thinking=True)
