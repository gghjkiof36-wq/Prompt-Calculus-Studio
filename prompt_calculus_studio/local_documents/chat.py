"""Versioned chat payload. No credentials, service addresses or executable tools."""
import copy
import math
import uuid
from .schema import fail, identifier, new_document

FEATURE = 'pcs.chat.v1'
STATES = {'receiving', 'complete', 'stopped', 'interrupted', 'failed', 'limited'}
MAX_TEXT = 1_000_000


def empty():
    return new_document('chat', dict(version=1, draft='', system_prompt='', turns=[]))


def validate_payload(payload):
    if not isinstance(payload, dict) or set(payload) - {'draft_attachments'} != {'version', 'draft', 'system_prompt', 'turns'} or type(payload['version']) is not int or payload['version'] != 1:
        fail('unsupported', '不支援此對話版本。')
    if not isinstance(payload['draft'], str) or len(payload['draft']) > MAX_TEXT:
        fail('limit', '對話草稿過長。')
    if not isinstance(payload['system_prompt'], str) or len(payload['system_prompt']) > MAX_TEXT:
        fail('limit', '對話指示過長。')
    turns = payload['turns']
    if not isinstance(turns, list) or len(turns) > 10000:
        fail('limit', '對話訊息過多。')
    seen = set()
    from .chat_attachments import validate_list
    validate_list(payload.get('draft_attachments', []), seen)
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
            if not isinstance(attempt, dict) or set(attempt) != {'id', 'request', 'model', 'model_name', 'profile_epoch', 'parameters', 'system_prompt', 'context', 'text', 'status', 'reason'}:
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
            refs = attempt['context']
            if not isinstance(refs, list) or len(refs) != len(prior):
                fail('invalid', '對話上下文關聯不完整。')
            for ref, (user_id, usable) in zip(refs, prior.items()):
                if (not isinstance(ref, dict) or set(ref) != {'user_id', 'attempt_id'}
                        or ref['user_id'] != user_id or not isinstance(ref['attempt_id'], str)
                        or ref['attempt_id'] not in usable):
                    fail('invalid', '對話上下文關聯無效。')
        prior[turn['id']] = {a['id'] for a in turn['attempts'] if a['status'] == 'complete'}


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
    for item in result.get('draft_attachments', []):
        item['id'] = str(uuid.uuid4())
    for turn in result['turns']:
        old = turn['id']
        turn['id'] = remap[old] = str(uuid.uuid4())
        for item in turn.get('attachments', []):
            item['id'] = str(uuid.uuid4())
        for attempt in turn['attempts']:
            old = attempt['id']
            attempt['id'] = remap[old] = str(uuid.uuid4())
            attempt['request'] = str(uuid.uuid4())
            for ref in attempt['context']:
                ref['user_id'] = remap[ref['user_id']]
                ref['attempt_id'] = remap[ref['attempt_id']]
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
    validate_payload(result)
    return result


def context_messages(payload, text, *, retry=False):
    """All prior usable turns; an incomplete turn must be retried explicitly."""
    history = payload['turns'][:-1] if retry else payload['turns']
    messages = []
    from .chat_attachments import message
    if payload['system_prompt']:
        messages.append(dict(role='system', content=payload['system_prompt']))
    for turn in history:
        answer = next((a for a in reversed(turn['attempts']) if a['status'] == 'complete'), None)
        if answer is None:
            raise ValueError('上一則回覆尚未完成，請先重試，或另開一份對話。')
        messages.extend([dict(role='user', content=message(turn['text'], turn.get('attachments', []))),
                         dict(role='assistant', content=answer['text'])])
    attachments = payload['turns'][-1].get('attachments', []) if retry else payload.get('draft_attachments', [])
    messages.append(dict(role='user', content=message(text, attachments)))
    return messages


def context_references(payload, *, retry=False):
    history = payload['turns'][:-1] if retry else payload['turns']
    return [dict(user_id=t['id'], attempt_id=next(a['id'] for a in reversed(t['attempts'])
                                                if a['status'] == 'complete')) for t in history]


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
    return '\n'.join(lines)
