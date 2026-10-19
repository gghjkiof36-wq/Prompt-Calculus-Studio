"""Lossless Markdown source operations. No renderer output is a save format."""
from dataclasses import dataclass
import html
import re

MAX_BYTES = 8 * 1024 * 1024
BOM = b'\xef\xbb\xbf'
BREAK = re.compile(r'\r\n|\r|\n|\u2029')


def source_text(document):
    # toPlainText also changes NBSP and Unicode line separators. Raw text only
    # needs Qt's paragraph delimiter converted to the source editor's newline.
    return document.toRawText().replace('\u2029', '\n')


@dataclass(frozen=True)
class SourcePiece:
    """A slice of original bytes decoded as UTF-8; CRLF is one editor position."""
    storage: str
    start: int
    end: int

    def raw(self):
        return self.storage[self.start:self.end]

    def text(self):
        raw=self.raw()
        return BREAK.sub('\n',raw) if '\r' in raw or '\u2029' in raw else raw

    def cut(self, offset):
        raw = self.raw()
        # Only CRLF changes the number of code points; CR and U+2029 do not.
        extra = 0
        for match in re.finditer('\r\n', raw):
            if match.start() - extra >= offset:
                break
            extra += 1
        boundary = self.start + offset + extra
        return SourcePiece(self.storage, self.start, boundary), SourcePiece(self.storage, boundary, self.end)


def split_pieces(pieces, offset):
    before = []
    for index, piece in enumerate(pieces):
        length = len(piece.text())
        if offset <= length:
            a, b = piece.cut(offset)
            return tuple(before + ([a] if a.start < a.end else [])), tuple(
                ([b] if b.start < b.end else []) + list(pieces[index+1:]))
        before.append(piece)
        offset -= length
    if offset:
        raise ValueError('原文位置已失效。')
    return tuple(before), ()


class SourceLedger:
    """Persistent pieces keyed to native Qt undo states, not guessed line diffs.

    Each state shares original/inserted strings. Native undo grouping remains
    authoritative; no second command stack, raw full-document snapshots, or
    normalization of untouched bytes is needed.
    """
    def __init__(self, raw):
        decoded = raw.decode('utf-8-sig')
        self.bom = BOM if raw.startswith(BOM) else b''
        self.newline = '\r\n' if '\r\n' in decoded else '\r' if '\r' in decoded else '\n'
        self.pieces = (SourcePiece(decoded, 0, len(decoded)),) if decoded else ()
        self.states = {0: self.pieces}

    @staticmethod
    def text_of(pieces):
        return ''.join(piece.text() for piece in pieces)

    def replacement(self, start, end, text):
        before, rest = split_pieces(self.pieces, start)
        _, after = split_pieces(rest, end-start)
        raw = text.replace('\n', self.newline)
        middle = (SourcePiece(raw, 0, len(raw)),) if raw else ()
        return before + middle + after

    def changed(self, position, removed, added, text, undo_steps):
        candidate = self.states.get(undo_steps)
        if candidate is not None and self.text_of(candidate) == text:
            self.pieces = candidate
            return
        old = self.text_of(self.pieces)
        start = python_position(old, position)
        end = python_position(old, position+removed)
        inserted_end = python_position(text, position+added)
        pieces = self.replacement(start, end, text[start:inserted_end])
        if self.text_of(pieces) != text:
            raise ValueError('原文映射與編輯器不同步，請保留此稿並另存。')
        self.accept(pieces, undo_steps)

    def accept(self, pieces, undo_steps):
        self.states = {key: value for key, value in self.states.items() if key <= undo_steps}
        self.states[undo_steps] = self.pieces = pieces

    def encoded(self, pieces=None):
        raw = self.bom + ''.join(p.raw() for p in (self.pieces if pieces is None else pieces)).encode('utf-8')
        if len(raw) > MAX_BYTES:
            raise ValueError('Markdown 超過 8 MiB，請縮減內容後保存。')
        return raw


def python_position(text, utf16_offset):
    return len(text.encode('utf-16-le')[:utf16_offset*2].decode('utf-16-le'))


@dataclass(frozen=True)
class SourceSnapshot:
    text: str
    revision: int
    document_id: str = ''


@dataclass(frozen=True)
class SourceEdit:
    """Python Unicode offsets into one specific SourceSnapshot."""
    start: int
    end: int
    replacement: str


def edited_text(snapshot, edits):
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    end, previous_start = -1, -1
    for edit in ordered:
        if (not 0 <= edit.start <= edit.end <= len(snapshot.text)
                or edit.start < end or edit.start == previous_start):
            raise ValueError('編輯範圍重疊或已失效。')
        if '\r' in edit.replacement or '\u2029' in edit.replacement:
            raise ValueError('編輯交易請使用換行字元 LF。')
        end = edit.end
        previous_start = edit.start
    text = snapshot.text
    for edit in reversed(ordered):
        text = text[:edit.start] + edit.replacement + text[edit.end:]
    return text, ordered


def utf16_position(text, offset):
    return len(text[:offset].encode('utf-16-le')) // 2


def literal_ranges(text):
    """Conservative exclusions for A's minimal text-style command.

    This is not the B/C Markdown grammar. Unsupported selections stay editable
    as source instead of being rewritten as if they were ordinary prose.
    """
    fence = None
    offset = 0
    for line in text.splitlines(keepends=True):
        marker = re.match(r'^[ \t>]*(`{3,}|~{3,})(.*)', line)
        if marker:
            chars, rest = marker.groups()
            if fence is None:
                fence = (offset, chars[0], len(chars))
            elif chars[0] == fence[1] and len(chars) >= fence[2] and not rest.strip():
                yield fence[0], offset + len(line)
                fence = None
        offset += len(line)
    if fence is not None:
        yield fence[0], len(text)
    patterns = (
        r'\A---\n.*?(?:\n(?:---|\.\.\.)(?:\n|$)|\Z)',
        r'`+[^`\n]*`+', r'\$\$.*?\$\$', r'(?<!\\)\$(?:\\.|[^$\n])*\$',
        r'<!--.*?(?:-->|\Z)', r'%%.*?(?:%%|\Z)', r'<[^>]+>',
        r'&(?:#[0-9]+|#x[0-9a-fA-F]+|[a-zA-Z][a-zA-Z0-9]+);',
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.S):
            yield match.span()


def text_style_edit(snapshot, start, end, *, family=None, size=None, color=None):
    selected = snapshot.text[start:end]
    if not 0 <= start < end <= len(snapshot.text) or not selected.strip():
        raise ValueError('請選取要排版的文字。')
    if ('\n' in selected or any(c in selected for c in '<>[]`*$|\\\t')
            or selected != selected.strip(' ') or '  ' in selected
            or any(start < b and end > a for a, b in literal_ranges(snapshot.text))):
        raise ValueError('此選取包含語法或受保護內容，請使用原文編輯。')
    styles = []
    if family is not None:
        if not re.fullmatch(r'[\w \-]{1,80}', family):
            raise ValueError('字體名稱不適用於文字樣式。')
        styles.append('font-family:' + family)
    if size is not None:
        if isinstance(size, bool) or not isinstance(size, (int, float)) or not 6 <= size <= 96:
            raise ValueError('字號須介於 6 與 96。')
        styles.append(f'font-size:{size:g}pt')
    if color is not None:
        if not re.fullmatch(r'#[0-9a-fA-F]{6}', color):
            raise ValueError('請使用六位色碼。')
        styles.append('color:' + color.lower())
    if not styles:
        raise ValueError('尚未指定文字樣式。')
    # Escape selected prose, never interpret it as executable or active HTML.
    return SourceEdit(start, end, '<span style="' + ';'.join(styles) + '">'
                      + html.escape(selected, quote=False) + '</span>')
