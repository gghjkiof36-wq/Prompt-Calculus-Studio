"""Qt-free execution-channel wire contract. No transport, reducer or dispatch.

Scopes are filters inside the authenticated local/default-user server, not an
alternative authorization mechanism. Sequence numbers are decimal strings on
the wire (including zero); they must never pass through a JavaScript float.
"""
import hashlib
import json
import re

SCHEMA = 'pcs.execution-events.v1'
CAPABILITY = 'execution_events_v1'
DOMAIN = 'comfy.execution'
LOG_GAP = 'log.gap'
MAX_EVENTS = 128
MAX_PAGE_BYTES = 256 * 1024
MAX_PAYLOAD_BYTES = 64 * 1024
RETENTION_COUNT = 100_000
RETENTION_BYTES = 64 * 1024 * 1024
RETENTION_SECONDS = 24 * 3600
EVIDENCE_SECONDS = 30
DIAGNOSTIC_SECONDS = 120
MAX_WAIT = 8.0


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def cursor(value):
    if not isinstance(value, str) or not re.fullmatch(r'0|[1-9][0-9]{0,18}', value):
        raise ValueError('Invalid event cursor')
    number = int(value)
    if number > 2**63 - 1:
        raise ValueError('Event cursor overflow')
    return number


def scope(value=None):
    value = value if value is not None else dict(domain=DOMAIN, user_scope='default')
    if not isinstance(value, dict) or set(value) - {
            'domain', 'user_scope', 'library_id', 'workspace_id', 'entities'}:
        raise ValueError('Invalid event scope')
    if value.get('domain') != DOMAIN or value.get('user_scope') != 'default':
        raise ValueError('Unsupported event scope')
    result = dict(domain=DOMAIN, user_scope='default')
    for key in ('library_id', 'workspace_id'):
        if key in value:
            if not isinstance(value[key], str) or not 1 <= len(value[key]) <= 200:
                raise ValueError('Invalid scope identity')
            result[key] = value[key]
    if 'entities' in value:
        items = value['entities']
        if (not isinstance(items, list) or not 1 <= len(items) <= 128 or
                any(not isinstance(i, str) or not 1 <= len(i) <= 256 for i in items)):
            raise ValueError('Invalid scope entities')
        result['entities'] = sorted(set(items))
    return result


def scope_digest(value=None):
    return hashlib.sha256(encode(scope(value))).hexdigest()


def matches_scope(item, requested):
    actual = item.get('scope', {})
    # A loss can have unknown ownership. Every subscriber in this authenticated
    # server/user domain must reconcile, including entity-filtered subscribers.
    if item.get('kind') == LOG_GAP:
        return all(actual.get(k) == requested.get(k) for k in ('domain', 'user_scope'))
    return (all(actual.get(k) == requested[k] for k in
                ('domain', 'user_scope', 'library_id', 'workspace_id') if k in requested)
            and ('entities' not in requested or item.get('entity') in requested['entities']))


def recognize(value):
    """Return negotiated identity, or None for an old extension; no side effects."""
    if not isinstance(value, dict) or CAPABILITY not in value.get('capabilities', []):
        return None
    channel = value.get('event_channel', {})
    if channel.get('schema') != SCHEMA or channel.get('transport') != 'long-poll':
        raise ValueError('Unsupported execution channel')
    result = {}
    for key in ('server_instance', 'epoch'):
        if not isinstance(channel.get(key), str) or not 1 <= len(channel[key]) <= 100:
            raise ValueError('Missing server identity')
        result[key] = channel[key]
    if channel.get('limits') != limits():
        raise ValueError('Unsupported event limits')
    extension = value.get('extension_info', {})
    if not isinstance(extension.get('root'), str) or not extension['root']:
        raise ValueError('Missing extension installation root')
    result.update(scope=scope(), scope_digest=scope_digest(), extension_info=dict(extension))
    if 'result_manifest_v1' in value.get('capabilities', []):result['result_manifest_v1']=True
    if result.get('result_manifest_v1') and 'result_delivery_v1' in value['capabilities']:result['result_delivery_v1']=True
    return result


def limits():
    return dict(events=MAX_EVENTS, page_bytes=MAX_PAGE_BYTES, payload_bytes=MAX_PAYLOAD_BYTES,
                retention_count=RETENTION_COUNT, retention_bytes=RETENTION_BYTES,
                retention_seconds=RETENTION_SECONDS, progress_hz=5, wait_seconds=MAX_WAIT)


def validate_page(value, identity, requested_scope, since):
    """Validate coverage before a future transport is allowed to persist a cursor."""
    requested_scope = scope(requested_scope)
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('Invalid event page')
    if any(value.get(k) != identity.get(k) for k in ('server_instance', 'epoch')):
        raise ValueError('Wrong event server or epoch')
    if value.get('scope_digest') != scope_digest(requested_scope):
        raise ValueError('Wrong event scope')
    start, end, floor = cursor(value.get('from_cursor')), cursor(value.get('covered_to')), cursor(value.get('oldest_seq'))
    latest = cursor(value.get('latest_seq'))
    if (value.get('reset_required') or start != cursor(since) or
            end < start or end > latest or floor > start + 1):
        raise ValueError('Event coverage requires snapshot')
    events = value.get('events')
    if not isinstance(events, list) or len(events) > MAX_EVENTS or len(encode(value)) > MAX_PAGE_BYTES:
        raise ValueError('Oversized event page')
    previous = start
    for item in events:
        if not isinstance(item, dict) or not isinstance(item.get('scope'), dict):
            raise ValueError('Invalid event envelope')
        seq = cursor(item.get('seq'))
        if not previous < seq <= end or not matches_scope(item, requested_scope):
            raise ValueError('Invalid event order or scope')
        if any(item.get(k) != value[k] for k in ('server_instance', 'epoch', 'schema')):
            raise ValueError('Mixed event identity')
        if len(encode(item.get('payload'))) > MAX_PAYLOAD_BYTES:
            raise ValueError('Oversized event payload')
        previous = seq
    return end


def validate_header(value, identity, requested_scope):
    if not isinstance(value, dict) or value.get('schema') != SCHEMA:
        raise ValueError('Invalid channel header')
    if any(value.get(k) != identity.get(k) for k in ('server_instance', 'epoch')):
        raise ValueError('Wrong event server or epoch')
    if value.get('scope_digest') != scope_digest(requested_scope):
        raise ValueError('Wrong event scope')
    cursor(value.get('latest_seq'))
    cursor(value.get('oldest_seq'))
    if type(value.get('log_consistent')) is not bool: raise ValueError('Missing log consistency')


def validate_snapshot(value, identity, requested_scope, *, token='', after='', as_of=None):
    validate_header(value, identity, requested_scope)
    current = value.get('snapshot_token')
    if not isinstance(current, str) or not 1 <= len(current) <= 100 or (token and current != token):
        raise ValueError('Wrong snapshot token')
    watermark = cursor(value.get('as_of_seq'))
    if watermark > cursor(value['latest_seq']) or (as_of is not None and str(as_of) != value['as_of_seq']):
        raise ValueError('Wrong snapshot watermark')
    if value.get('after') != after or type(value.get('done')) is not bool:
        raise ValueError('Wrong snapshot coverage')
    items = value.get('receipts')
    if not isinstance(items, list) or len(items) > MAX_EVENTS or len(encode(value)) > MAX_PAGE_BYTES:
        raise ValueError('Oversized snapshot')
    previous = after
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('scope'), dict):
            raise ValueError('Invalid snapshot envelope')
        entity = item.get('entity')
        if not isinstance(entity, str) or not previous < entity or not matches_scope(item, requested_scope):
            raise ValueError('Wrong snapshot order or scope')
        # Durable receipts can originate in an older epoch. Their envelope is
        # retained verbatim; snapshot header/as_of identify the current view.
        if item.get('schema') != SCHEMA or item.get('server_instance') != identity['server_instance']:
            raise ValueError('Wrong snapshot receipt identity')
        if len(encode(item.get('payload'))) > MAX_PAYLOAD_BYTES: raise ValueError('Oversized snapshot receipt')
        if item.get('epoch') == identity['epoch'] and cursor(item.get('seq')) > watermark:
            raise ValueError('Receipt newer than snapshot')
        previous = entity
    if value.get('next_after') != (previous if items else '') or (not value['done'] and not items):
        raise ValueError('Incomplete snapshot coverage')
