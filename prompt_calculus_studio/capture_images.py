"""Materialize bounded, self-contained capture PNGs without changing originals."""
import base64
from bisect import bisect_right
import binascii
from dataclasses import replace
import hashlib
import io
from pathlib import Path
import re
import struct
import zlib

from .clean_metadata import PNG, png_parts
from .conversation_archive import canonical, checkpoint
from .local_documents import paths
from .retained_files import read_local

MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 16_000_000
MAX_IMAGES = 128
MAX_CAPTURE_BYTES = 16 * 1024 * 1024
PREFIX = 'data:image/png;base64,'
ASSET = re.compile(r'assets/([0-9a-f]{64})\.png\Z')
# Browser canvas PNGs need no compressed metadata or animation. Unknown chunks
# are rejected, not stripped or passed to a second, less bounded decoder.
CHUNKS = frozenset((b'IHDR', b'PLTE', b'IDAT', b'IEND', b'tRNS', b'sRGB',
                    b'gAMA', b'cHRM', b'pHYs', b'sBIT'))


def _source_segments(text):
    """Supplement the normal scanner with parsed footnote prose, not code.

    The editor's lightweight line mask intentionally treats indented lines as
    code. Footnote continuation prose needs its actual block-token context.
    Offset maps contain one entry per line, never one Python object per byte.
    """
    yield text, lambda offset: offset
    if not re.search(r'(?m)^ {0,3}\[\^[^]\n]+\]:', text):
        return
    from markdown_it import MarkdownIt
    from mdit_py_plugins.footnote import footnote_plugin
    from .markdown_properties import frontmatter
    parser = MarkdownIt('commonmark').use(footnote_plugin, move_to_end=False)
    front_end, _, _ = frontmatter(text)
    lines = text.splitlines(keepends=True)
    offsets, position = [], 0
    for line in lines:
        offsets.append(position); position += len(line)
    depth = 0
    for token in parser.parse(text):
        if token.type == 'footnote_reference_open':
            depth += 1
        elif token.type == 'footnote_reference_close':
            depth -= 1
        elif depth and token.type == 'inline' and token.map:
            begin, end = token.map
            if begin >= len(offsets) or offsets[begin] < front_end:
                continue
            pieces, starts, origins, size = [], [], [], 0
            for number in range(begin, min(end, len(lines))):
                raw = lines[number]
                cut = len(raw) - len(raw.lstrip(' \t'))
                piece = raw[cut:]
                if not piece:
                    continue
                starts.append(size); origins.append(offsets[number] + cut)
                pieces.append(piece); size += len(piece)
            def source_offset(offset, starts=starts, origins=origins):
                index = bisect_right(starts, offset) - 1
                return origins[index] + offset - starts[index]
            if pieces:
                yield ''.join(pieces), source_offset


def _scan_source(text):
    from .markdown_links import links, prose_mask
    found, candidates = {}, set()
    for segment, source_offset in _source_segments(text):
        for link in links(segment):
            mapped = replace(link, start=source_offset(link.start), end=source_offset(link.end),
                             target_start=source_offset(link.target_start), target_end=source_offset(link.target_end))
            found[(mapped.start, mapped.end)] = mapped
        for candidate in re.finditer(r'!\[(?:\\.|[^\]\\])*\]\(\s*<?data:', prose_mask(segment), re.I):
            candidates.add(source_offset(candidate.start()))
    return sorted(found.values(), key=lambda link: link.start), candidates


def source_links(text):
    """Read-only asset references, including standard footnote continuation."""
    return _scan_source(text)[0]


def png_bytes(uri, cancel=None):
    """Validate the complete PNG container and bounded scanline stream."""
    checkpoint(cancel)
    if not uri.startswith(PREFIX):
        raise ValueError('擷取圖片只支援自包含的 PNG。')
    encoded = uri[len(PREFIX):]
    if len(encoded) > ((MAX_IMAGE_BYTES + 2) // 3) * 4:
        raise ValueError('擷取圖片超過單張 2 MiB。')
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as error:
        raise ValueError('擷取圖片的 Base64 無效。') from error
    if not data or len(data) > MAX_IMAGE_BYTES or not data.startswith(PNG):
        raise ValueError('擷取圖片不是有效 PNG，或超過單張 2 MiB。')
    if base64.b64encode(data).decode('ascii') != encoded:
        raise ValueError('擷取圖片的 Base64 格式不完整。')
    stream = io.BytesIO(data)
    chunks = list(png_parts(stream, len(data)))
    header = chunks[0]
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', header.prefix)
    depths = {0: (1, 2, 4, 8, 16), 2: (8, 16), 3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}
    if (not width or not height or width * height > MAX_PIXELS or color not in depths
            or depth not in depths[color] or compression or filtering or interlace not in (0, 1)):
        raise ValueError('擷取圖片尺寸或格式無效；最多 1,600 萬像素。')
    seen, compressed, ended, palette = set(), [], False, None
    for part in chunks:
        checkpoint(cancel)
        kind = part.kind
        if kind not in CHUNKS or kind in seen and kind != b'IDAT':
            raise ValueError('擷取 PNG 包含不支援或重複的區塊。')
        if kind == b'IDAT':
            if ended or color == 3 and palette is None:
                raise ValueError('擷取 PNG 圖片區塊順序無效。')
            compressed.append(data[part.payload:part.payload + part.length])
        elif compressed:
            ended = True
        if kind == b'PLTE':
            if compressed or color in (0, 4) or not part.length or part.length % 3 or part.length > 768:
                raise ValueError('擷取 PNG 色盤無效。')
            palette = part.length // 3
            if color == 3 and palette > 2 ** depth:
                raise ValueError('擷取 PNG 色盤超過色深。')
        if kind == b'tRNS':
            valid = {0: part.length == 2, 2: part.length == 6,
                     3: palette is not None and 0 < part.length <= palette}
            if compressed or not valid.get(color, False):
                raise ValueError('擷取 PNG 透明度區塊無效。')
        lengths = {b'sRGB': 1, b'gAMA': 4, b'cHRM': 32, b'pHYs': 9,
                   b'sBIT': {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}[color]}
        if kind in lengths and (compressed or part.length != lengths[kind]):
            raise ValueError('擷取 PNG 顏色或尺寸區塊無效。')
        seen.add(kind)
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    grids = ((0, 0, 1, 1),) if not interlace else (
        (0, 0, 8, 8), (4, 0, 8, 8), (0, 4, 4, 8), (2, 0, 4, 4),
        (0, 2, 2, 4), (1, 0, 2, 2), (0, 1, 1, 2))
    rows = []
    for x, y, dx, dy in grids:
        w, h = max(0, (width - x + dx - 1) // dx), max(0, (height - y + dy - 1) // dy)
        if w and h:
            rows.append((h, (w * channels * depth + 7) // 8))
    expected = sum(count * (size + 1) for count, size in rows)
    inflater, produced = zlib.decompressobj(), 0
    pass_index, count, row_size, remaining = 0, rows[0][0], rows[0][1], 0

    def consume(block):
        nonlocal produced, pass_index, count, row_size, remaining
        produced += len(block)
        if produced > expected:
            raise ValueError('擷取 PNG 解壓後超過宣告尺寸。')
        offset = 0
        while offset < len(block):
            if not remaining:
                if block[offset] > 4:
                    raise ValueError('擷取 PNG 掃描列無效。')
                offset += 1
                remaining = row_size
            amount = min(remaining, len(block) - offset)
            remaining -= amount
            offset += amount
            if not remaining:
                count -= 1
                if not count and pass_index + 1 < len(rows):
                    pass_index += 1
                    count, row_size = rows[pass_index]

    try:
        for part in compressed:
            pending = part
            while pending:
                checkpoint(cancel)
                block = inflater.decompress(pending, min(65536, expected - produced + 1))
                consume(block)
                pending = inflater.unconsumed_tail
                if inflater.unused_data:
                    raise ValueError('擷取 PNG 圖片壓縮資料含額外內容。')
        if not inflater.eof or produced != expected or remaining or count:
            raise ValueError('擷取 PNG 圖片資料不完整。')
    except zlib.error as error:
        raise ValueError('擷取 PNG 圖片壓縮資料無效。') from error
    checkpoint(cancel)
    return data


def prepare_capture_images(item, cancel=None):
    """Return a rendering-only conversation copy and relative-path PNG bytes."""
    if 'pcs_capture' not in item.raw:
        return item, {}
    from .connector_capture import subset_capture
    subset_capture(item.raw['pcs_capture'], item.raw['mapping'], item.raw['mapping'])
    if len(canonical(item.raw)) > MAX_CAPTURE_BYTES:
        raise ValueError('擷取資料超過 16 MiB，請減少選取訊息。')
    assets, targets, changes = {}, {}, {}

    def project(text):
        if 'data:' not in text.casefold():
            return text
        replacements, recognized = [], set()
        links, candidates = _scan_source(text)
        for link in links:
            checkpoint(cancel)
            if not link.embed or link.wiki or not link.target.casefold().startswith('data:'):
                continue
            uri = link.target
            recognized.add(link.start)
            if uri not in targets:
                data = png_bytes(uri, cancel)
                name = 'assets/' + hashlib.sha256(data).hexdigest() + '.png'
                assets[name] = data
                if len(assets) > MAX_IMAGES:
                    raise ValueError('擷取圖片超過 128 張，請縮小選取範圍。')
                targets[uri] = name
            replacements.append((link.target_start, link.target_end, targets[uri]))
        for start in candidates:
            if start not in recognized:
                raise ValueError('擷取圖片的 Markdown 或 PNG 資料不完整。')
        for start, end, value in reversed(replacements):
            text = text[:start] + value + text[end:]
        return text

    for key, node in item.raw['mapping'].items():
        checkpoint(cancel)
        message = node.get('message')
        if not message:
            continue
        content = message.get('content')
        updated = content
        if isinstance(content, str):
            updated = project(content)
        elif isinstance(content, dict):
            if isinstance(content.get('parts'), list):
                parts = [project(part) if isinstance(part, str) else
                         dict(part, text=project(part['text'])) if isinstance(part, dict) and isinstance(part.get('text'), str)
                         else part for part in content['parts']]
                updated = dict(content, parts=parts)
            elif isinstance(content.get('text'), str):
                updated = dict(content, text=project(content['text']))
        if updated != content:
            changes[key] = dict(node, message=dict(message, content=updated))
    if not changes:
        return item, assets
    return replace(item, raw=dict(item.raw, mapping=dict(item.raw['mapping'], **changes))), assets


def capture_image_hashes(raw, cancel=None):
    """Derive expected files from the immutable source, not a mutable receipt."""
    from .conversation_archive import Limits, conversation
    if 'pcs_capture' not in raw:
        return {}
    _, images = prepare_capture_images(conversation(raw, Limits()), cancel)
    return {name: hashlib.sha256(data).hexdigest() for name, data in images.items()}


def verify_capture_images(folder, receipt, cancel=None, *, expected=None):
    """A completed/reused receipt cannot mask missing or changed capture PNGs."""
    manifest = receipt.get('capture_images', {})
    if not isinstance(manifest, dict) or len(manifest) > MAX_IMAGES:
        raise ValueError('擷取圖片清單無效。')
    if expected is not None and manifest != expected:
        raise ValueError('擷取圖片清單與原始對話不一致。')
    files = receipt.get('files', ())
    for name, digest in manifest.items():
        checkpoint(cancel)
        match = ASSET.fullmatch(name) if isinstance(name, str) else None
        if not match or digest != match[1] or name not in files:
            raise ValueError('擷取圖片回執與文件清單不一致。')
        data = read_local(paths.checked(Path(folder), name), MAX_IMAGE_BYTES, cancel)
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError('已保存的擷取圖片內容已改變。')
