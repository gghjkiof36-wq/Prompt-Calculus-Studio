"""Bounded, offline Codex rollout projection. The JSONL carrier stays authoritative."""
from collections import Counter
from datetime import datetime
import math
from pathlib import Path

from .conversation_archive import checkpoint, conversation, decode_json


def timestamp(value):
    if type(value) in (int, float) and math.isfinite(value):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
        except (ValueError, OverflowError, OSError):
            pass
    return None


def parse(raw, path, limits, cancel=None):
    rows, meta = [], None
    counts = Counter()
    for line_number, line in enumerate(raw.splitlines(), 1):
        checkpoint(cancel)
        if not line.strip():
            continue
        try:
            record = decode_json(line)
        except (ValueError, UnicodeError) as error:
            raise ValueError(f'Codex 封存第 {line_number} 行不是完整 JSON。') from error
        if not isinstance(record, dict) or not isinstance(record.get('payload'), dict):
            raise ValueError(f'Codex 封存第 {line_number} 行缺少 type／payload。')
        kind, payload = record.get('type'), record['payload']
        counts[str(kind) + '/' + str(payload.get('type', ''))] += 1
        if kind == 'session_meta':
            if meta is not None:
                raise ValueError('一份 Codex rollout 只能包含一個 session_meta。')
            meta = payload
        rows.append((line_number, record))
        if len(rows) > limits.nodes * 10:
            raise ValueError('Codex 封存記錄超過本次讀取上限。')
    if not meta or not isinstance(meta.get('id') or meta.get('session_id'), str):
        raise ValueError('這不是含 session_meta 的 Codex rollout JSONL。')

    # Desktop visible events and model-input messages are different projections.
    # Use the visible event stream per role when present; never deduplicate by text.
    event_roles = set()
    for _, record in rows:
        p = record['payload']
        item = p.get('item', {})
        if record.get('type') == 'event_msg' and p.get('type') == 'item_completed' and isinstance(item, dict):
            if item.get('type') in ('UserMessage', 'AgentMessage'):
                event_roles.add('user' if item['type'] == 'UserMessage' else 'assistant')
    mapping, seen, excluded, unknown = {}, {}, Counter(), Counter()
    previous = None
    attachments = {}
    warnings = []
    for number, record in rows:
        checkpoint(cancel)
        p, typ = record['payload'], record.get('type')
        item, role, stamp = None, None, None
        if typ == 'event_msg' and p.get('type') == 'item_completed':
            candidate = p.get('item', {})
            if isinstance(candidate, dict) and candidate.get('type') in ('UserMessage', 'AgentMessage'):
                item = candidate
                role = 'user' if item['type'] == 'UserMessage' else 'assistant'
                time_value = p.get('started_at_ms')
                stamp = timestamp(time_value / 1000) if type(time_value) in (int, float) else None
        elif typ == 'response_item' and p.get('type') == 'message':
            role = p.get('role')
            metadata = p.get('internal_chat_message_metadata_passthrough') or {}
            kinds = metadata.get('content_item_kinds', []) if isinstance(metadata, dict) else []
            if role not in ('user', 'assistant') or p.get('channel') == 'analysis':
                excluded['指令／內部訊息'] += 1
                continue
            if role in event_roles:
                excluded['模型輸入／輸出投影'] += 1
                continue
            if role == 'user' and kinds and not any(k.startswith('user.') for k in kinds if isinstance(k, str)):
                excluded['環境／注入內容'] += 1
                continue
            item = p
            stamp = timestamp(metadata.get('create_time')) if isinstance(metadata, dict) else None
        if item is None:
            excluded[str(typ) + '/' + str(p.get('type', ''))] += 1
            continue
        key = item.get('id')
        key = str(key) if key else 'line-' + str(number)
        content = item.get('content', [])
        if not isinstance(content, list):
            raise ValueError(f'Codex 封存第 {number} 行的訊息內容不是清單。')
        signature = (role, content)
        if key in seen:
            if seen[key] != signature:
                raise ValueError(f'Codex 訊息識別重複但內容不同（第 {number} 行）；原件未更動。')
            excluded['相同訊息識別的重複記錄'] += 1
            continue
        seen[key] = signature
        parts = []
        for index, part in enumerate(content):
            if not isinstance(part, dict):
                unknown[type(part).__name__] += 1
                continue
            kind = part.get('type', '')
            if kind in ('Text', 'text', 'input_text', 'output_text') and isinstance(part.get('text'), str):
                parts.append(part['text'])
            elif kind in ('image', 'input_image', 'local_image'):
                ref = f'codex-attachment-{number}-{index + 1}'
                value = part.get('path') or part.get('image_url') or part.get('url')
                if isinstance(value, dict):
                    value = value.get('url')
                attachments[ref] = value if isinstance(value, str) else ''
                parts.append({'content_type': 'image_asset_pointer', 'asset_pointer': ref})
            else:
                unknown[str(kind) or '未知內容'] += 1
                parts.append('> 此項非文字內容保留在原始 Codex 封存。')
        if not parts:
            parts = ['（來源未提供可解析的文字）']
        if stamp is None:
            stamp = timestamp(record.get('timestamp'))
        message = dict(id=key, author={'role': role}, content={'content_type': 'multimodal_text', 'parts': parts},
                       create_time=stamp, metadata={'codex_record': number})
        mapping[key] = dict(id=key, parent=previous, children=[], message=message)
        if previous:
            mapping[previous]['children'].append(key)
        previous = key
        if len(mapping) > limits.nodes:
            raise ValueError('Codex 對話超過訊息數量上限。')
    if not mapping:
        raise ValueError('這份 Codex 封存沒有可解析的使用者／助理訊息。')
    warnings.append('正文採用可見對話事件；有事件的角色不再併入模型輸入／輸出投影。' if event_roles else
                    '此封存沒有可見對話事件，正文採用 response_item 的使用者／助理文字。')
    warnings.append('工具執行、系統／開發者指令、推理、壓縮摘要及其他記錄保留於原始封存，未轉成問答。')
    if unknown:
        warnings.append('未解析內容：' + '、'.join(f'{key} × {value}' for key, value in sorted(unknown.items())))
    title = meta.get('title') or meta.get('thread_name') or Path(path).stem
    source = dict(provider='codex', format='rollout-jsonl', cli_version=meta.get('cli_version'),
                  source_name=Path(path).name, counts=dict(counts), excluded=dict(excluded),
                  unknown=dict(unknown), attachments=attachments, visible_roles=sorted(event_roles))
    value = dict(id=meta.get('id') or meta['session_id'], title=str(title), mapping=mapping,
                 current_node=previous, pcs_codex=source)
    result = conversation(value, limits)
    result.warnings.extend(warnings)
    return result


def prepare_attachments(item, source, cancel=None):
    """Only self-contained images or relative files under the selected carrier root."""
    import base64
    import hashlib
    from .capture_images import png_bytes, MAX_IMAGE_BYTES, MAX_IMAGES, MAX_CAPTURE_BYTES
    from .local_documents import paths
    from .retained_files import read_local
    links, files, warnings = {}, {}, []
    total = 0
    refs = item.raw.get('pcs_codex', {}).get('attachments', {})
    for ref, value in refs.items():
        checkpoint(cancel)
        try:
            if value.startswith('data:image/png;base64,'):
                raw = png_bytes(value, cancel)
                suffix = '.png'
            elif value and not Path(value).is_absolute() and ':' not in value:
                relative = paths.relative_parts(value)
                root = source.attachment_root or source.path.parent
                file = paths.checked(root, '/'.join(relative))
                suffix = file.suffix.lower()
                if suffix not in ('.png', '.jpg', '.jpeg', '.webp', '.gif'):
                    raise ValueError('此附件格式未轉出')
                if source.attachment_identity is not None:
                    from .work_resources.source_io import read_source
                    raw, _ = read_source(root, '/'.join(relative), source.attachment_identity, MAX_IMAGE_BYTES, cancel)
                else:
                    raw = read_local(file, MAX_IMAGE_BYTES, cancel)
                if suffix == '.png':
                    png_bytes('data:image/png;base64,' + base64.b64encode(raw).decode('ascii'), cancel)
            else:
                raise ValueError('封存未包含附件內容；外部路徑與網址未自動讀取')
            name = 'assets/' + hashlib.sha256(raw).hexdigest() + suffix
            if name not in files:
                if len(files) >= MAX_IMAGES or total + len(raw) > MAX_CAPTURE_BYTES:
                    raise ValueError('可轉出圖片上限為 128 張／16 MiB，此附件留在來源')
                total += len(raw)
            files[name], links[ref] = raw, name
        except (OSError, ValueError) as error:
            links[ref] = None
            warnings.append(ref + '：' + str(error))
    return links, files, warnings
