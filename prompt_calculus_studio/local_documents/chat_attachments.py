"""Bounded self-contained chat snapshots. No Qt, paths, services or execution."""
from functools import lru_cache
import hashlib
import re

from .schema import fail, identifier

FEATURE = 'pcs.chat-attachments.v1'
MAX_ATTACHMENTS = 8
MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_TEXT_CHARS = 200_000
MAX_IMAGE_BYTES = 2 * 1024 * 1024
MAX_TOTAL_BYTES = 16 * 1024 * 1024
IMAGE_UNSUPPORTED = '目前模型不支援圖片，請移除圖片後送出。'


@lru_cache(maxsize=8)
def image_bytes(data_uri):
    # The existing decoder validates all PNG chunks and the bounded pixel
    # stream, without loading Qt or resolving an external resource.
    from ..capture_images import png_bytes
    return png_bytes(data_uri)


def validate_list(values, seen=None):
    if not isinstance(values, list) or len(values) > MAX_ATTACHMENTS:
        fail('limit', '每則訊息最多加入 8 個附件。')
    seen = set() if seen is None else seen
    total = 0
    for item in values:
        if not isinstance(item, dict):
            fail('invalid', '對話附件格式無效。')
        kind = item.get('kind')
        fields = {'id', 'name', 'mime', 'size', 'kind', 'sha256', 'text' if kind == 'text' else 'data_uri'}
        if kind not in ('text', 'image') or set(item) - {'source'} != fields:
            fail('invalid', '對話附件格式無效。')
        if not identifier(item['id']) or item['id'] in seen:
            fail('invalid', '對話附件身分無效或重複。')
        seen.add(item['id'])
        if (not isinstance(item['name'], str) or not item['name'].strip() or len(item['name']) > 255
                or any(ord(c) < 32 for c in item['name'])):
            fail('invalid', '附件名稱無效。')
        if 'source' in item and (not isinstance(item['source'], str) or len(item['source']) > 256):
            fail('invalid', '附件來源說明無效。')
        if not isinstance(item['sha256'], str) or not re.fullmatch('[0-9a-f]{64}', item['sha256']):
            fail('invalid', '附件摘要無效。')
        if kind == 'text':
            if item['mime'] != 'text/plain' or not isinstance(item['text'], str) or len(item['text']) > MAX_TEXT_CHARS:
                fail('limit', '文字附件最多 200,000 字元。')
            try:
                raw = item['text'].encode('utf-8')
            except UnicodeError as error:
                raise ValueError('文字附件不是有效的 UTF-8。') from error
        else:
            if item['mime'] != 'image/png' or not isinstance(item['data_uri'], str):
                fail('invalid', '圖片附件必須是自包含的 PNG。')
            raw = image_bytes(item['data_uri'])
        if type(item['size']) is not int or item['size'] != len(raw) or len(raw) > MAX_FILE_BYTES:
            fail('limit', '附件大小無效或過大。')
        if hashlib.sha256(raw).hexdigest() != item['sha256']:
            fail('invalid', '附件內容與摘要不同。')
        total += len(raw)
    if total > MAX_TOTAL_BYTES:
        fail('limit', '這則訊息的附件總量超過 16 MiB。')


def has_fields(payload):
    turns = payload.get('turns', [])
    return 'draft_attachments' in payload or (isinstance(turns, list) and any(
        isinstance(turn, dict) and 'attachments' in turn for turn in turns))


def sync_feature(document):
    features = document['required_features']
    if has_fields(document['payload']):
        if FEATURE not in features:
            features.append(FEATURE)
    elif FEATURE in features:
        features.remove(FEATURE)
    return document


def message(text, values):
    """Text snapshots stay inside the user role. Vision is explicitly absent."""
    if any(item['kind'] == 'image' for item in values):
        raise ValueError(IMAGE_UNSUPPORTED)
    parts = [text] if text else []
    for item in values:
        parts.append(f'--- 使用者附件：{item["name"]} ---\n{item["text"]}\n--- 附件結束 ---')
    return '\n\n'.join(parts)


def markdown(values):
    lines = []
    for item in values:
        name = re.sub(r'([\\`*_{}\[\]()#+.!<>|])', r'\\\1', item['name'])
        lines.extend(['### 附件 · ' + name, ''])
        lines.extend([item['text'] if item['kind'] == 'text' else f'![{name}]({item["data_uri"]})', ''])
    return lines
