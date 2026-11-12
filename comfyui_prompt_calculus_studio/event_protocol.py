"""Extension projections: compact receipts, never document/graph/image copies."""
import hashlib
from .shared.comfy_event_protocol import (SCHEMA, CAPABILITY, DOMAIN, LOG_GAP, MAX_EVENTS,
    MAX_PAGE_BYTES, MAX_PAYLOAD_BYTES, RETENTION_COUNT, RETENTION_BYTES,
    RETENTION_SECONDS, EVIDENCE_SECONDS, DIAGNOSTIC_SECONDS, MAX_WAIT,
    encode, cursor, scope, scope_digest, matches_scope, limits)

NATIVE_EVENTS = frozenset(('execution_start', 'execution_cached', 'executing',
    'progress', 'progress_state', 'executed', 'execution_success',
    'execution_error', 'execution_interrupted', 'status'))
TERMINAL_EVENTS = frozenset(('execution_success', 'execution_error', 'execution_interrupted'))
# All integration.sqlite3 source tables. Deletions are durable tombstone events.
WRITERS = {'collections': 'key', 'jobs': 'id', 'workflow_transfers': 'id',
    'native_operations': 'id', 'native_diagnostics': 'id', 'queue_captures': 'id',
    'queue_attempts': 'id', 'background_states': 'key', 'background_operations': 'id',
    'background_receipts': 'id', 'input_bridge': 'client', 'input_requests': 'id'}


def receipt_scope(value):
    result = scope()
    snapshot = value.get('snapshot', {})
    key = value.get('key', value.get('result', {}).get('key', {}))
    source = value.get('scope', {})
    if not isinstance(key, dict): key = {}
    for name, candidate in (
        ('library_id', snapshot.get('library_id') or key.get('library_id') or source.get('library_id') or source.get('library') or value.get('library')),
        ('workspace_id', snapshot.get('state', {}).get('workspace') or key.get('workspace_id') or source.get('workspace_id') or source.get('workspace'))):
        if isinstance(candidate, str) and candidate: result[name] = candidate
    return result


def project(table, ident, value, deleted=False):
    """Every writer has a named event; bulky originals stay in their receipt table."""
    payload = {k: value[k] for k in ('state', 'phase', 'error', 'first_error', 'action',
        'terminal_action', 'prompt_id', 'cancel_requested', 'cancel_confirmed', 'abandoned',
        'missing_fence', 'submit_inflight', 'submit_process', 'transport_recorded', 'queue_terminal', 'terminal_result', 'created',
        'deadline_at', 'diagnostic_at', 'cancel_deadline_at', 'cancel_unconfirmed', 'status', 'owner', 'head', 'client', 'session',
        'revision', 'op_id', 'request_hash', 'key', 'parameter_receipt') if k in value}
    # The digest also tells a consumer that a large graph/transfer changed.
    payload['receipt_sha256'] = hashlib.sha256(encode(value)).hexdigest()
    payload['receipt_ref'] = dict(table=table, id=ident)
    if deleted: payload['deleted'] = True
    names = {'jobs': 'operation.submit_observed', 'workflow_transfers': 'workflow.transfer_available',
        'queue_captures': 'operation.captured', 'native_diagnostics': 'operation.diagnostic',
        'background_states': 'workflow.state_changed', 'background_operations': 'operation.background_changed',
        'background_receipts': 'workflow.receipt', 'input_bridge': 'input.changed',
        'input_requests': 'input.claim_received', 'collections': 'result.collected'}
    kind = names.get(table, 'operation.receipt_changed')
    if table in ('native_operations', 'queue_attempts'):
        state = value.get('state')
        kind = {'awaiting_browser': 'operation.created', 'pending': 'operation.created',
                'prepared': 'operation.prepared', 'submitted': 'operation.submit_observed',
                'submitting': 'operation.submit', 'queued': 'operation.queued'}.get(state, kind)
        if value.get('abandoned'): kind = 'operation.retired'
        elif value.get('missing_fence'): kind = 'operation.missing'
        elif value.get('cancel_confirmed'): kind = 'operation.cancel_confirmed'
        elif value.get('terminal_result'): kind = 'operation.terminal'
        elif value.get('cancel_requested'): kind = 'operation.cancel_requested'
        if value.get('cancel_unconfirmed') and not value.get('cancel_confirmed'):kind='operation.cancel_unconfirmed'
    if table == 'workflow_transfers' and value.get('status') != 'pending': kind = 'workflow.transfer_acked'
    if table == 'input_requests' and deleted: kind = 'input.apply_receipt'
    if deleted and table != 'input_requests': kind += '.deleted'
    if len(encode(payload)) > MAX_PAYLOAD_BYTES:
        payload = dict(receipt_ref=dict(table=table, id=ident),
                       receipt_sha256=hashlib.sha256(encode(value)).hexdigest(), fetch_required=True)
    return kind, payload, receipt_scope(value)


def native_payload(event, data):
    # Error inputs/tracebacks can contain prompts or entire tensors. They are
    # already available through the authenticated history route, not the log.
    if event == 'execution_error':
        data = {k: v for k, v in data.items() if k not in ('current_inputs', 'current_outputs', 'traceback')}
    raw = encode(data)
    if len(raw) > MAX_PAYLOAD_BYTES:
        return dict(prompt_id=data.get('prompt_id'), node=data.get('node'),
                    fetch_required=True, payload_sha256=hashlib.sha256(raw).hexdigest())
    return data
