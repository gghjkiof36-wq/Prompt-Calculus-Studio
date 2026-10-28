"""Versioned chat payload. No credentials, service addresses or executable tools."""
import copy
import math
import uuid
from .schema import fail, identifier, new_document

FEATURE = 'pcs.chat.v1'
REVISION_FEATURE = 'pcs.chat-revisions.v1'
REASONING_FEATURE = 'pcs.chat-reasoning.v1'
STATES = {'receiving', 'complete', 'stopped', 'interrupted', 'failed', 'limited'}
MAX_TEXT = 1_000_000


def empty():
    return new_document('chat', dict(version=1, draft='', system_prompt='', turns=[]))


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) - {'draft_attachments', 'revisions'} != {'version', 'draft', 'system_prompt', 'turns'} or type(payload['version']) is not int or payload['version'] != 1:
        fail('unsupported', '不支援此對話版本。')
    if not isinstance(payload['draft'], str) or len(payload['draft']) > MAX_TEXT:
        fail('limit', '對話草稿過長。')
    if not isinstance(payload['system_prompt'], str) or len(payload['system_prompt']) > MAX_TEXT:
        fail('limit', '對話指示過長。')
    revisions = payload.get('revisions', [])
    if not isinstance(revisions, list) or len(revisions) > 100:
        fail('limit', '此對話的編輯紀錄已達上限。')
    revision_ids = set()
    for revision in revisions:
        if (not isinstance(revision, dict) or set(revision) != {'id', 'reason', 'turn_id', 'system_prompt', 'turns'}
                or not identifier(revision['id']) or revision['id'] in revision_ids
                or revision['reason'] not in ('edit', 'restore')
                or not isinstance(revision['system_prompt'], str) or len(revision['system_prompt']) > MAX_TEXT):
            fail('invalid', '對話編輯紀錄格式無效。')
        revision_ids.add(revision['id'])
        _validate_turns(revision['turns'], terminal_context=True)
        if (revision['turn_id'] is not None and revision['turn_id'] not in {t['id'] for t in revision['turns']}
                or revision['reason'] == 'edit' and revision['turn_id'] is None
                or any(a['status'] == 'receiving' for t in revision['turns'] for a in t['attempts'])):
            fail('invalid', '對話編輯紀錄的訊息或狀態無效。')
    seen = set()
    from .chat_attachments import validate_list
    validate_list(payload.get('draft_attachments', []), seen)
    _validate_turns(payload['turns'], seen=seen, terminal_context='revisions' in payload)


def _validate_turns(turns, *, seen=None, terminal_context=False):
    if not isinstance(turns, list) or len(turns) > 10000:
        fail('limit', '對話訊息過多。')
    seen = set() if seen is None else seen
    from .chat_attachments import validate_list
    def ident(value):
        if not identifier(value) or value in seen:
            fail('invalid', '對話訊息身分無效或重複。')
        seen.add(value)
    prior = {}
    receiving = 0
    for turn in turns:
        if not isinstance(turn, dict) or set(turn) - {'attachments'} != {'id', 'text', 'attempts'}:
            fail('invalid', '對話訊息格式無效。')
        ident(turn['id'])
        validate_list(turn.get('attachments', []), seen)
        if not isinstance(turn['text'], str) or (not turn['text'].strip() and not turn.get('attachments')) or len(turn['text']) > MAX_TEXT:
            fail('invalid', '對話訊息內容無效。')
        if not isinstance(turn['attempts'], list) or len(turn['attempts']) > 100:
            fail('limit', '此訊息的回覆次數過多。')
        for attempt in turn['attempts']:
            if not isinstance(attempt, dict) or set(attempt) - {'reasoning'} != {'id', 'request', 'model', 'model_name', 'profile_epoch', 'parameters', 'system_prompt', 'context', 'text', 'status', 'reason'}:
                fail('invalid', '對話回覆格式無效。')
            ident(attempt['id']); ident(attempt['request'])
            if not identifier(attempt['profile_epoch']) or not isinstance(attempt['status'], str) or attempt['status'] not in STATES:
                fail('invalid', '對話回覆狀態無效。')
            if attempt['status'] == 'receiving':
                receiving += 1
                if receiving > 1 or turn is not turns[-1] or attempt is not turn['attempts'][-1]:
                    fail('invalid', '僅最後一則回覆可以接收中。')
            for key in ('text', 'model', 'model_name', 'reason', 'system_prompt'):
                if not isinstance(attempt[key], str) or len(attempt[key]) > MAX_TEXT:
                    fail('limit', '對話回覆過長。')
            params = attempt['parameters']
            if (not isinstance(params, dict) or set(params) != {'context_size', 'max_tokens', 'temperature', 'top_p', 'seed', 'gpu_layers'}
                    or type(params['context_size']) is not int or not 256 <= params['context_size'] <= 1048576
                    or type(params['max_tokens']) is not int or not 1 <= params['max_tokens'] <= min(262144, params['context_size'] - 1)
                    or type(params['temperature']) not in (int, float) or not 0 <= params['temperature'] <= 2 or not math.isfinite(params['temperature'])
                    or type(params['top_p']) not in (int, float) or not 0.01 <= params['top_p'] <= 1 or not math.isfinite(params['top_p'])
                    or type(params['seed']) is not int or not -1 <= params['seed'] <= 4294967295
                    or type(params['gpu_layers']) is not int or not -1 <= params['gpu_layers'] <= 999):
                fail('invalid', '對話回覆參數無效。')
            if 'reasoning' in attempt:
                from ..local_ai.reasoning import normalize_reasoning
                try:
                    normalize_reasoning(attempt['reasoning'], max_tokens=params['max_tokens'])
                except ValueError as exc:
                    fail('invalid', str(exc))
            refs = attempt['context']
            if not isinstance(refs, list) or len(refs) != len(prior):
                fail('invalid', '對話上下文關聯不完整。')
            for ref, (user_id, usable) in zip(refs, prior.items()):
                if (not isinstance(ref, dict) or set(ref) != {'user_id', 'attempt_id'}
                        or ref['user_id'] != user_id or not isinstance(ref['attempt_id'], str)
                        or ref['attempt_id'] not in usable):
                    fail('invalid', '對話上下文關聯無效。')
        prior[turn['id']] = {a['id'] for a in turn['attempts']
                             if a['status'] != 'receiving' and (terminal_context or a['status'] == 'complete')}
        if terminal_context and not turn['attempts']:
            prior[turn['id']].add('')


def has_reasoning(payload):
    """Feature presence also covers archived paths, including malformed input."""
    paths = [payload]
    revisions = payload.get('revisions', [])
    if isinstance(revisions, list):
        paths.extend(revision for revision in revisions if isinstance(revision, dict))
    for path in paths:
        turns = path.get('turns', [])
        if not isinstance(turns, list):
            continue
        for turn in turns:
            attempts = turn.get('attempts', []) if isinstance(turn, dict) else []
            if isinstance(attempts, list) and any(isinstance(a, dict) and 'reasoning' in a for a in attempts):
                return True
    return False


def sync_features(document):
    from .chat_attachments import sync_feature
    sync_feature(document)
    features = document['required_features']
    if 'revisions' in document['payload']:
        if REVISION_FEATURE not in features:
            features.append(REVISION_FEATURE)
    elif REVISION_FEATURE in features:
        features.remove(REVISION_FEATURE)
    if has_reasoning(document['payload']):
        if REASONING_FEATURE not in features:
            features.append(REASONING_FEATURE)
    elif REASONING_FEATURE in features:
        features.remove(REASONING_FEATURE)
    return document


def interrupt(payload):
    """Reopen/clone never resumes an uncertain accepted request."""
    changed = False
    for turn in payload['turns']:
        for attempt in turn['attempts']:
            if attempt['status'] == 'receiving':
                attempt.update(status='interrupted', reason='上次接收已中斷；可手動重試。')
                changed = True
    return changed


def clone_payload(payload):
    result = copy.deepcopy(payload)
    interrupt(result)
    remap = {}
    def fresh(value):
        if value not in remap:
            remap[value] = str(uuid.uuid4())
        return remap[value]

    def remap_turns(turns):
        for turn in turns:
            turn['id'] = fresh(turn['id'])
            for item in turn.get('attachments', []):
                item['id'] = fresh(item['id'])
            for attempt in turn['attempts']:
                attempt['id'] = fresh(attempt['id'])
                attempt['request'] = fresh(attempt['request'])
                for ref in attempt['context']:
                    ref['user_id'] = fresh(ref['user_id'])
                    if ref['attempt_id']:
                        ref['attempt_id'] = fresh(ref['attempt_id'])

    for item in result.get('draft_attachments', []):
        item['id'] = fresh(item['id'])
    remap_turns(result['turns'])
    for revision in result.get('revisions', []):
        revision['id'] = str(uuid.uuid4())
        remap_turns(revision['turns'])
        if revision['turn_id'] is not None:
            revision['turn_id'] = fresh(revision['turn_id'])
    return result


def branch_payload(payload, turn_id, attempt_id=None):
    """Snapshot history through an exact user message or assistant attempt.

    This prepares a prefix with its source IDs. identity.duplicate() gives the
    final independent document fresh IDs and interrupts copied receiving state.
    A user-only branch keeps its question without answers, ready for an explicit
    retry. Unsent composer text belongs to the source and is never copied.
    """
    validate_payload(payload)
    position = next((index for index, turn in enumerate(payload['turns']) if turn['id'] == turn_id), None)
    if position is None:
        raise ValueError('找不到這則訊息，無法建立分支。')
    selected = payload['turns'][position]
    if attempt_id is None:
        attempts = []
    else:
        attempt_index = next((index for index, attempt in enumerate(selected['attempts'])
                              if attempt['id'] == attempt_id), None)
        if attempt_index is None:
            raise ValueError('找不到這次回覆，無法建立分支。')
        attempts = selected['attempts'][:attempt_index + 1]
    prefix = payload['turns'][:position] + [dict(selected, attempts=attempts)]
    result = dict(version=payload['version'], draft='', system_prompt=payload['system_prompt'],
                  turns=copy.deepcopy(prefix))
    if 'revisions' in payload:
        result['revisions'] = copy.deepcopy(payload['revisions'])
    validate_payload(result)
    return result


def context_messages(payload, text, *, retry=False):
    """Freeze the latest terminal answer, including an explicitly stopped partial."""
    history = payload['turns'][:-1] if retry else payload['turns']
    messages = []
    from .chat_attachments import message
    if payload['system_prompt']:
        messages.append(dict(role='system', content=payload['system_prompt']))
    for turn in history:
        answer = context_answer(turn)
        messages.append(dict(role='user', content=message(turn['text'], turn.get('attachments', []))))
        if answer is not None and answer['text']:
            from ..local_ai.thinking import split_thinking
            parts = split_thinking(answer['text'])
            # Only an unfinished reasoning-enabled attempt can still be inside
            # its reasoning envelope. A completed or thinking-disabled answer
            # may use an opening tag literally; preserve that text as context.
            unfinished_reasoning = (answer['status'] in {'stopped', 'interrupted', 'failed', 'limited'}
                                    and answer.get('reasoning', {}).get('enable_thinking') is not False
                                    and parts.has_thinking and not parts.closed)
            if not unfinished_reasoning:
                messages.append(dict(role='assistant', content=answer['text']))
    attachments = payload['turns'][-1].get('attachments', []) if retry else payload.get('draft_attachments', [])
    messages.append(dict(role='user', content=message(text, attachments)))
    return messages


def context_references(payload, *, retry=False):
    history = payload['turns'][:-1] if retry else payload['turns']
    return [dict(user_id=t['id'], attempt_id=answer['id'] if (answer := context_answer(t)) else '') for t in history]


def context_answer(turn):
    if turn['attempts'] and turn['attempts'][-1]['status'] == 'receiving':
        raise ValueError('請等待目前回覆完成，或先停止生成。')
    return turn['attempts'][-1] if turn['attempts'] else None


def needs_terminal_context(payload, *, retry=False):
    history = payload['turns'][:-1] if retry else payload['turns']
    return any((answer := context_answer(turn)) is None or answer['status'] != 'complete' for turn in history)


def archive_revision(payload, *, reason, turn_id=None):
    """Retain one immutable path inside this file, independent of its draft."""
    revisions = payload.setdefault('revisions', [])
    if len(revisions) >= 100:
        raise ValueError('此對話的編輯紀錄已達上限；請先匯出保留。')
    revisions.append(dict(id=str(uuid.uuid4()), reason=reason, turn_id=turn_id,
                          system_prompt=payload['system_prompt'], turns=copy.deepcopy(payload['turns'])))


def markdown(payload, title):
    from .chat_attachments import markdown as attachment_markdown
    labels = {'complete':'完成', 'stopped':'已停止', 'interrupted':'中斷', 'failed':'失敗', 'limited':'達到上限', 'receiving':'接收中'}
    lines = ['# ' + title.replace('\n', ' '), '']
    for turn in payload['turns']:
        lines.extend(['## 你', '', turn['text'], ''])
        lines.extend(attachment_markdown(turn.get('attachments', [])))
        for attempt in turn['attempts']:
            lines.extend(['## 回覆 · '+labels[attempt['status']], '', attempt['text'], ''])
            if attempt['reason']:lines.extend([attempt['reason'], ''])
    if payload['draft'] or payload.get('draft_attachments'):
        lines.extend(['## 未送出草稿', '', payload['draft'], ''])
        lines.extend(attachment_markdown(payload.get('draft_attachments', [])))
    for index, revision in enumerate(payload.get('revisions', []), 1):
        archived = dict(version=1, draft='', system_prompt=revision['system_prompt'], turns=revision['turns'])
        lines.extend(['---', '', markdown(archived, f'編輯紀錄 {index}'), ''])
    return '\n'.join(lines)
