"""Bounded ChatGPT observations projected onto the existing conversation reader.

This is an ordered observation, not a claim about the provider's complete tree.
Transport timestamps belong to the caller's receipt, never the content revision.
"""
from datetime import datetime
import hashlib
import re
from urllib.parse import parse_qsl, quote, urlsplit, urlunsplit

from .conversation_archive import Limits, canonical, conversation

MAX_CAPTURE_BYTES = 16 * 1024 * 1024
MAX_MESSAGES = 5000
MAX_MESSAGE_BYTES = 2 * 1024 * 1024
MAX_RESOURCES = 128
MAX_MESSAGE_RESOURCES = 32
SCOPES = frozenset(('loaded', 'current_branch', 'selection'))
METHODS = frozenset(('dom', 'structured'))
ROLES = frozenset(('user', 'assistant', 'system', 'developer', 'tool'))
FIELDS = frozenset(('version', 'provider', 'conversation_id', 'title', 'url', 'method',
                    'scope', 'messages', 'adapter_version', 'captured_at'))
META_FIELDS = frozenset(('version', 'provider', 'url', 'method', 'scope', 'source_scope',
                         'adapter_version', 'message_ids', 'message_identity', 'graph', 'complete'))
SCOPE_LABELS = {'loaded': '已載入內容', 'current_branch': '目前分支', 'selection': '選取訊息'}
IDENTIFIER = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,255}\Z')
RESOURCE_LABELS = {'citation': '來源', 'image': '圖片', 'chart': '圖表',
                   'attachment': '附件', 'embed': '嵌入內容'}
PRIVATE_QUERY_KEYS = frozenset(('token', 'access_token', 'auth', 'authorization', 'signature', 'sig',
                                 'x-amz-signature', 'x-amz-credential', 'x-goog-signature', 'x-goog-credential'))


def _text(value, name, limit, *, empty=False):
    if not isinstance(value, str) or (not empty and not value.strip()) or '\x00' in value:
        raise ValueError('擷取內容的' + name + '無效。')
    try:
        size = len(value.encode('utf-8'))
    except UnicodeError as error:
        raise ValueError('擷取內容包含無效文字。') from error
    if size > limit:
        raise ValueError('擷取內容的' + name + '超過上限。')
    return value


def _identifier(value):
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        raise ValueError('擷取訊息識別格式無效。')
    return value


def _url(value):
    _text(value, '來源網址', 4096)
    if any(ord(char) < 33 for char in value):
        raise ValueError('擷取來源網址無效。')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme != 'https' or parsed.hostname not in ('chatgpt.com', 'chat.openai.com')
                or parsed.username or parsed.password or parsed.port not in (None, 443)):
            raise ValueError()
    except ValueError as error:
        raise ValueError('請使用 ChatGPT 的對話網址。') from error
    # Query strings and fragments are not conversation identity or saved content.
    return urlunsplit(('https', parsed.hostname, parsed.path or '/', '', ''))


def _resource_url(value):
    if value is None:
        return None
    _text(value, '項目網址', 8192)
    if len(value) > 2048 or any(ord(char) < 33 for char in value):
        raise ValueError('項目網址無效。')
    try:
        parsed = urlsplit(value)
        if (parsed.scheme not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or any(key.casefold() in PRIVATE_QUERY_KEYS for key, _ in parse_qsl(parsed.query))):
            raise ValueError()
        parsed.port  # Reject malformed ports before saving the pointer.
    except ValueError as error:
        raise ValueError('項目網址不支援，請保留原對話連結。') from error
    return value


def _resources(value):
    if not isinstance(value, list) or len(value) > MAX_MESSAGE_RESOURCES:
        raise ValueError('每則訊息最多保留 32 個項目。')
    result = []
    for item in value:
        if not isinstance(item, dict) or set(item) != {'kind', 'label', 'url'}:
            raise ValueError('擷取項目欄位不支援。')
        kind = item['kind']
        if not isinstance(kind, str) or kind not in RESOURCE_LABELS:
            raise ValueError('擷取項目類型不支援。')
        label = _text(item['label'], '項目名稱', 1200)
        if len(label) > 300:
            raise ValueError('項目名稱超過上限。')
        result.append(dict(kind=kind, label=label, url=_resource_url(item['url'])))
    return result


def _resource_markdown(resources, source_url):
    lines = []
    for item in resources:
        label = re.sub(r'\s+', ' ', item['label']).strip()
        label = re.sub(r'([\\`*_{}\[\]()#+.!|<>])', r'\\\1', label)
        url = quote(item['url'] or source_url, safe=':/?#@!$&*+,;=%[]')
        kind = RESOURCE_LABELS[item['kind']]
        lines.append('- ' + kind + ('' if item['kind'] == 'citation' else '（僅來源）')
                     + '：[' + label + '](' + url + ')')
    return '\n'.join(lines)


def validate_capture(capture):
    """Return a fresh, whitelist-only conversation dict or reject the capture."""
    if not isinstance(capture, dict) or set(capture) != FIELDS:
        raise ValueError('擷取資料欄位不支援。')
    if type(capture['version']) is not int or capture['version'] != 1 or capture['provider'] != 'chatgpt':
        raise ValueError('擷取資料版本或來源不支援。')
    if (not isinstance(capture['method'], str) or capture['method'] not in METHODS
            or not isinstance(capture['scope'], str) or capture['scope'] not in SCOPES
            or capture['adapter_version'] != '1'):
        raise ValueError('擷取方式、範圍或擴充版本不支援。')
    title = _text(capture['title'], '標題', 2048)
    url = _url(capture['url'])
    platform_id = capture['conversation_id']
    if platform_id is not None:
        _identifier(platform_id)
    stamp = _text(capture['captured_at'], '時間', 64)
    try:
        parsed = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
        if parsed.tzinfo is None or 'T' not in stamp:
            raise ValueError()
    except ValueError as error:
        raise ValueError('擷取時間須包含時區。') from error
    messages = capture['messages']
    if not isinstance(messages, list) or not 1 <= len(messages) <= MAX_MESSAGES:
        raise ValueError('擷取訊息數須介於 1 與 5000 則。')
    clean, seen, content_bytes, resource_count = [], set(), 0, 0
    for row in messages:
        if (not isinstance(row, dict) or not {'id', 'role', 'content'} <= set(row)
                or set(row) - {'id', 'role', 'content', 'resources'}):
            raise ValueError('擷取訊息欄位不支援。')
        key = _identifier(row['id'])
        if key in seen:
            raise ValueError('擷取資料有重複訊息識別。')
        if not isinstance(row['role'], str) or row['role'] not in ROLES:
            raise ValueError('擷取訊息角色不支援。')
        text = _text(row['content'], '訊息', MAX_MESSAGE_BYTES, empty=True)
        resources = _resources(row.get('resources', []))
        resource_count += len(resources)
        if resource_count > MAX_RESOURCES:
            raise ValueError('擷取內容最多保留 128 個項目。')
        if resources:
            text += ('\n\n' if text else '') + _resource_markdown(resources, url)
            _text(text, '訊息', MAX_MESSAGE_BYTES)
        content_bytes += len(text.encode('utf-8'))
        if content_bytes > MAX_CAPTURE_BYTES:
            raise ValueError('擷取資料超過 16 MiB，請減少選取訊息。')
        clean.append(dict(id=key, role=row['role'], content=text, resources=resources))
        seen.add(key)
    if len(canonical(capture)) > MAX_CAPTURE_BYTES:
        raise ValueError('擷取資料超過 16 MiB，請減少選取訊息。')
    ids = [row['id'] for row in clean]
    metadata = dict(version=1, provider='chatgpt', url=url, method=capture['method'],
                    scope=capture['scope'], source_scope=capture['scope'], adapter_version='1',
                    message_ids=ids, graph='observed-order', complete=False,
                    message_identity='capture-local' if capture['method'] == 'dom' else 'source-observed')
    mapping = {}
    for index, row in enumerate(clean):
        key = row['id']
        mapping[key] = dict(id=key, parent=ids[index-1] if index else None,
                            children=ids[index+1:index+2],
                            message=dict(id=key, author=dict(role=row['role']),
                                         content=dict(content_type='text', parts=[row['content']])))
        if row['resources']:
            mapping[key]['message']['metadata'] = dict(pcs_capture_resources=row['resources'])
    result = dict(title=title, mapping=mapping, current_node=ids[-1], pcs_capture=metadata)
    # Without a provider ID this identifies this observation, not a guessed chat.
    source_id = platform_id or 'pcs-capture-' + hashlib.sha256(canonical(result)).hexdigest()
    result['conversation_id'] = source_id
    conversation(result, Limits())
    return result


def subset_capture(metadata, original_ids, selected_ids):
    """Carry bounded provenance; intersect scope without copying an opaque payload."""
    if not isinstance(metadata, dict) or set(metadata) != META_FIELDS:
        raise ValueError('擷取來源範圍格式不支援。')
    if (type(metadata['version']) is not int or metadata['version'] != 1
            or metadata['provider'] != 'chatgpt' or metadata['adapter_version'] != '1'
            or not isinstance(metadata['method'], str) or metadata['method'] not in METHODS
            or not isinstance(metadata['scope'], str) or metadata['scope'] not in SCOPES
            or not isinstance(metadata['source_scope'], str) or metadata['source_scope'] not in SCOPES
            or metadata['message_identity'] != ('capture-local' if metadata['method'] == 'dom' else 'source-observed')
            or metadata['graph'] != 'observed-order'
            or metadata['complete'] is not False or _url(metadata['url']) != metadata['url']):
        raise ValueError('擷取來源範圍無效。')
    ids = metadata['message_ids']
    if (not isinstance(ids, list) or not 1 <= len(ids) <= MAX_MESSAGES
            or any(not isinstance(key, str) or not IDENTIFIER.fullmatch(key) for key in ids)
            or len(set(ids)) != len(ids) or set(ids) != set(original_ids)):
        raise ValueError('擷取訊息範圍與來源不符。')
    selected = set(selected_ids)
    if not selected or not selected <= set(ids):
        raise ValueError('選取訊息不在擷取範圍內。')
    result = dict(metadata, message_ids=[key for key in ids if key in selected])
    if selected != set(ids):
        result['scope'] = 'selection'
    return result


def capture_summary(capture):
    raw = validate_capture(capture)
    metadata = raw['pcs_capture']
    return dict(title=raw['title'], message_count=len(metadata['message_ids']),
                scope=metadata['scope'], scope_label=SCOPE_LABELS[metadata['scope']],
                method=metadata['method'], conversation_id=raw['conversation_id'],
                revision=hashlib.sha256(canonical(raw)).hexdigest())
