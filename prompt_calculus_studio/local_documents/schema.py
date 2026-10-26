"""Versioned, bounded JSON envelopes; optional data is never projected away."""
from __future__ import annotations

import copy
import hashlib
import json
import math
import uuid

MAX_BYTES = 32 * 1024 * 1024
MAX_DEPTH = 256  # Core's 32 nested groups can occupy several JSON levels each.
MAX_VALUES = 1_000_000
SUFFIXES = {'category': '.pcs-category', 'workflow': '.pcs-workflow', 'chat': '.pcs-chat'}
LIBRARY_FIELD = 'library_categories'
LIBRARY_FEATURE = 'pcs.canvas-library.v1'


class DocumentError(ValueError):
    def __init__(self, code, message, *, details=None):
        super().__init__(message)
        self.code = code
        self.details = details


def fail(code, message, **details):
    raise DocumentError(code, message, details=details or None)


def identifier(value):
    try:
        return isinstance(value, str) and str(uuid.UUID(value)) == value
    except (ValueError, AttributeError):
        return False


def check_json(value):
    """Check before copying/serializing; reject cycles, non-JSON and non-finite values."""
    count = 0
    ancestors = set()

    def visit(item, depth):
        nonlocal count
        count += 1
        if depth > MAX_DEPTH or count > MAX_VALUES:
            fail('limit', '文件過深或項目過多。')
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) not in (dict, list) or id(item) in ancestors:
            fail('invalid', '文件包含循環或不支援的 JSON 值。')
        ancestors.add(id(item))
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                fail('invalid', 'JSON 欄位名稱必須是文字。')
            values = item.values()
        else:
            values = item
        for child in values:
            visit(child, depth + 1)
        ancestors.remove(id(item))

    visit(value, 0)


def validate(document):
    check_json(document)
    if not isinstance(document, dict) or document.get('format') != 'pcs-document':
        fail('unsupported', '不是 PCS 可攜文件。')
    if type(document.get('file_schema_version')) is not int or document['file_schema_version'] != 1:
        fail('unsupported', '不支援此文件版本；原件保留。')
    if document.get('kind') not in SUFFIXES:
        fail('unsupported', '不支援此文件種類。')
    fields = {'format', 'file_schema_version', 'kind', 'document_id', 'revision',
              'payload', 'assets', 'extensions', 'required_features'}
    if set(document) - fields:
        fail('unsupported', '未知外層欄位；可選資料請放 namespaced extensions。')
    if not identifier(document.get('document_id')):
        fail('invalid', '文件 ID 必須是標準 UUID。')
    revision = document.get('revision')
    if (not isinstance(revision, dict) or set(revision) != {'id', 'parent'} or
            not identifier(revision.get('id')) or
            revision.get('parent') is not None and not identifier(revision['parent']) or
            revision.get('parent') == revision.get('id')):
        fail('invalid', '文件修訂無效。')
    features = document.get('required_features', [])
    if (not isinstance(features, list) or any(feature not in (LIBRARY_FEATURE, 'pcs.chat.v1', 'pcs.chat-attachments.v1') for feature in features)
            or len(features) != len(set(features))):
        fail('unsupported', '文件需要尚未支援的必要語義。')
    if (document['kind'] == 'chat') != ('pcs.chat.v1' in features):
        fail('invalid', '對話必要語義標記不一致。')
    extensions = document.get('extensions')
    if not isinstance(extensions, dict) or any('.' not in key or not key.strip('.') for key in extensions):
        fail('invalid', '擴充資料須使用命名空間。')
    if not isinstance(document.get('payload'), dict):
        fail('invalid', '文件缺少內容。')
    from .chat_attachments import FEATURE as ATTACHMENT_FEATURE, has_fields
    if ((document['kind'] == 'chat' and has_fields(document['payload']))
            != (ATTACHMENT_FEATURE in features)):
        fail('invalid', '對話附件必要語義標記不一致。')
    state = document['payload'].get('state')
    has_library = document['kind'] == 'workflow' and isinstance(state, dict) and LIBRARY_FIELD in state
    if has_library != (LIBRARY_FEATURE in features):
        fail('invalid', '匯入素材庫的必要語義標記不一致。')
    try:
        if document['kind'] == 'category':
            from .category import validate_payload
        elif document['kind'] == 'chat':
            from .chat import validate_payload
        else:
            from .workflow import validate_payload
        validate_payload(document['payload'])
        from .assets import validate_manifest
        validate_manifest(document)
    except DocumentError:
        raise
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as error:
        raise DocumentError('invalid', '文件內容無效：' + str(error)) from error
    return document


def new_document(kind, payload):
    value = dict(format='pcs-document', file_schema_version=1, kind=kind,
                 document_id=str(uuid.uuid4()), revision=dict(id=str(uuid.uuid4()), parent=None),
                 payload=copy.deepcopy(payload), assets=[], extensions={},
                 required_features=['pcs.chat.v1'] if kind == 'chat' else [LIBRARY_FEATURE] if kind == 'workflow' and LIBRARY_FIELD in payload.get('state', {}) else [])
    if kind == 'chat':
        from .chat_attachments import sync_feature
        sync_feature(value)
    # Asset manifests are prepared separately; missing attachments are explicit issues.
    return validate(value)


def encode(document):
    validate(document)
    try:
        data = (json.dumps(document, ensure_ascii=False, sort_keys=True,
                           indent=2, allow_nan=False) + '\n').encode('utf-8')
    except (UnicodeError, ValueError) as error:
        raise DocumentError('invalid', '文件不是有效 UTF-8 JSON。') from error
    if len(data) > MAX_BYTES:
        fail('limit', '文件超過 32 MiB。')
    return data


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail('invalid', 'JSON 有重複欄位：' + key)
        result[key] = value
    return result


def decode(data):
    if not isinstance(data, bytes) or len(data) > MAX_BYTES:
        fail('limit', '文件超過 32 MiB 或不是 bytes。')
    try:
        result = json.loads(data.decode('utf-8-sig'), object_pairs_hook=_object,
                            parse_constant=lambda value: fail('invalid', 'JSON 含非有限數值。'))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as error:
        raise DocumentError('invalid', '文件不是可讀的 UTF-8 JSON；原件保留。') from error
    return validate(result)


def digest(data):
    return hashlib.sha256(data).hexdigest()
