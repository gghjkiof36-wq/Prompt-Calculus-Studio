"""Sequential child inference within the parent's single owned worker command."""
import json
from threading import Event
import time

from .engine import Cancelled


def approval(worker, request, cancel):
    def ask(action):
        ticket = dict(action=action, done=Event(), allow=False)
        worker.review.emit(request, ticket)
        deadline = time.monotonic() + 120
        try:
            while not ticket['done'].wait(.1):
                cancel.check()
                if time.monotonic() > deadline:
                    return False
            cancel.check()
            return ticket['allow'] is True
        finally:
            ticket['done'].set()
    return ask


def handoff(worker, parent, command):
    ticket = dict(done=Event(), abandoned=False)
    worker.nested.emit(parent, command, ticket)
    # Finish admission even if cancellation races with the GUI's save. The
    # accepted child must receive a terminal event, not be stranded receiving.
    if not ticket['done'].wait(20):
        ticket['abandoned'] = True
        raise ValueError('子工作交接逾時，請查看原對話與委派工作紀錄。')
    if ticket.get('error'):
        raise ValueError(ticket['error'])
    return ticket['response']


def parent_result(record):
    from ..local_agents.transcript import clip
    result = record.get('result') or {}
    draft, truncated = clip(result.get('draft', ''), 6000)
    limits = result.get('limits') or {}
    truncated |= bool(limits.get('draft_truncated') or limits.get('evidence_truncated'))
    evidence, used = [], 0
    for event in result.get('evidence', []):
        content, cut = clip(event['content'], 3000)
        if used + len(content.encode()) > 6000:
            truncated = True
            break
        evidence.append(dict(operation=event['operation'], target=event['target'],
                             content=content, sha256=event['sha256'], status=event['status']))
        used += len(content.encode())
        truncated |= cut
    return dict(task_id=record['task_id'], agent_name=record.get('agent_name'), task=record.get('task'),
                state=record['state'], saved=result.get('saved', False), draft=draft,
                reason=record.get('error') or result.get('reason', ''), evidence=evidence,
                truncated=truncated, review_required=True)


def invoke(worker, runtime, parent, model, params, cancel, deadline, agent, task, call_id):
    from ..local_agents.execution import run
    from .service import _runtime_matches, _display_error
    response = handoff(worker, parent, dict(command='submit', agent_id=agent['id'], task=task, call_id=call_id))
    record = response['result']
    if 'execution' not in response:
        from ..local_agents.delegation_store import TERMINAL
        if record['state'] not in TERMINAL:
            raise ValueError('子工作尚未接受執行，也沒有可取回的完成結果。')
        return parent_result(record)
    child, child_model, messages, child_params = response['execution']
    child_cancel = response['cancel']
    worker.activity.emit(parent, dict(operation='subagent', target=record['task_id'], sha256='', skill='',
        status='working', content=json.dumps(dict(task_id=record['task_id'], agent_name=agent['name'], task=task), ensure_ascii=False)))
    pieces = []
    def delta(text):
        child_cancel.check()
        pieces.append(text)
        worker.text.emit(child, text)
    try:
        child_cancel.check()
        if not _runtime_matches(runtime, child_model['id'], child_params):
            worker._publish(runtime, 'loading')
            runtime.start(child_model, child_params, child_cancel)
        worker._publish(runtime, 'generating')
        outcome, detail = run(runtime, messages, child_params, child_cancel, child.input['agent'], delta,
                              lambda event: worker.activity.emit(child, event), approval(worker, child, child_cancel),
                              deadline=deadline)
        child_cancel.check()
    except Exception as exc:
        if child_cancel.event.is_set() or isinstance(exc, Cancelled):
            outcome, detail = 'cancelled', ''.join(pieces)
        else:
            outcome, detail = 'error', _display_error(exc)
    finally:
        # Even a tool/transport failure can leave server inference running.
        try:
            runtime.ensure_idle()
        except Exception as exc:
            # Preserve the child owner even when engine cleanup aborts the parent.
            worker.result.emit(child, 'error', _display_error(exc))
            raise
    worker.result.emit(child, outcome, detail)
    receipt = handoff(worker, parent, dict(command='result', task_id=record['task_id']))['result']
    cancel.check()
    if not _runtime_matches(runtime, model['id'], params):
        worker._publish(runtime, 'loading')
        runtime.start(model, params, cancel)
    worker._publish(runtime, 'generating')
    return parent_result(receipt)
