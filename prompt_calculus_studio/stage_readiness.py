"""Read-only scheduling decisions; retained history never grants dispatch rights.

Transport ownership remains in workspace_runtime.unfinished, independently of
these preparation/display decisions. No journal status is rewritten here.
"""
import json


def run_states(store, run_ids):
    """One operation-local header read; never cache across a journal change."""
    if not run_ids:
        return {}
    rows = store.db.execute("""SELECT id,json_extract(body,'$.status'),json_extract(body,'$.pause_reason')
        FROM stage_header WHERE id IN (SELECT value FROM json_each(?))""", (json.dumps(list(run_ids)),))
    return {ident: (status, reason) for ident, status, reason in rows}


def readiness_runs(store, workspace):
    """Only the fields used by run_can_progress, without hydrating saved scenes.

    The interpreter still reads/validates its owned full record before execution.
    Only the top frame participates in the scheduling predicate.
    """
    rows = store.db.execute("""SELECT id,json_extract(body,'$.status','$.plan','$.execution_group',
        '$.stack[#-1].kind','$.stack[#-1].initialized','$.stack[#-1].active','$.stack[#-1].owner',
        '$.stack[#-1].node.key','$.stack[#-1].ctx.execution_group',
        '$.stack[#-1].feed.cancelled','$.stack[#-1].feed.next','$.stack[#-1].feed.total'),
        json_extract(projection,'$.error')
        FROM stage_header INDEXED BY stage_header_open_runs WHERE kind='run' AND workspace=?
        AND json_extract(body,'$.status') NOT IN ('complete','cancelled') ORDER BY position""", (workspace,))
    from .journal_codec import raise_projection_error
    for ident, raw, error in rows:
        raise_projection_error(error)
        status, plan, group, kind, initialized, active, owner, key, frame_group, cancelled, next_, total = json.loads(raw)
        frame = dict(kind=kind, initialized=initialized, active=active, owner=owner, node=dict(key=key),
                     ctx=dict(execution_group=frame_group), feed=dict(cancelled=cancelled, next=next_ or 0, total=total or 0))
        yield dict(id=ident, workspace=workspace, status=status, plan=plan or {}, execution_group=group,
                   stack=[frame] if kind is not None else [])


def run_can_progress(store, run):
    if run['status'] not in ('running', 'waiting'):
        return False
    if run['status'] == 'waiting':
        # promote_waiting runs inside pump, after workspace acquisition. A
        # runnable future group must be able to reach that promotion even when
        # every older running run is bypassed and has released the lane.
        plan = run.get('plan', {})
        keys = set(plan.get('scopes', {})) | {s['scheduler'] for s in plan.get('stages', {}).values() if s.get('scheduler')}
        if any(store.scheduler_paused(run['workspace'], key) for key in keys):
            return False
        for key in keys:
            controls = store.batch_controls(run['workspace'], key)
            groups = {g['id'] for g in store.schedule_groups(run['workspace'], key)
                      if not (g['bypassed'] or g['cancelled'])}
            if any(c.get('run') == run['id'] and group in groups for group, c in controls.items()):
                return True
            if any(e['run'] == run['id'] and e['execution_group'] in groups
                   for e in store.entry_states(run['workspace'], key)):
                return True
        return False
    stack = run.get('stack', [])
    if not stack or stack[-1]['kind'] != 'loop':
        return run['status'] == 'running'
    frame = stack[-1]
    if not frame.get('initialized') or frame.get('active'):
        return True
    workspace, scheduler = run['workspace'], frame['node']['key']
    controls = store.batch_controls(workspace, scheduler)
    # The readiness decision needs ownership/state/group, not image previews or
    # a second entry scan just to recover the group key.
    from .stage_store import ACTIVE
    from .journal_codec import raise_projection_error
    rows = []
    query = """SELECT owner,json_extract(body,'$.status'),
        COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run')),
        json_extract(projection,'$.error') FROM stage_header
        WHERE kind='entry' AND workspace=? AND json_extract(body,'$.scheduler')=?
        AND json_extract(body,'$.status') IN ("""+','.join('?' for _ in ACTIVE)+')'
    for owner, status, group, error in store.db.execute(query, (workspace, scheduler, *ACTIVE)):
        raise_projection_error(error)
        rows.append(dict(owner=owner, status=status, execution_group=group))
    # Running scopes still have local continuation work, even if bypass was
    # requested for their remaining entries. Transport safety is separate.
    if any(row['owner'] == frame['owner'] and row['status'] == 'running' for row in rows):
        return True
    pending = False
    for row in rows:
        if row['owner'] != frame['owner']:
            continue
        pending = True
        control = controls.get(row['execution_group'], {})
        if row['status'] == 'waiting' and not (control.get('bypassed') or control.get('cancelled')):
            return True
    heads = store.batch_heads(workspace, owner=frame['owner']) if store.pending_batches(frame['owner']) else []
    for head in heads:
        pending = True
        control = controls.get(head['execution_group'], {})
        if not (control.get('bypassed') or control.get('cancelled')):
            return True
    feed = frame.get('feed') or {}
    if not feed.get('cancelled') and feed.get('next', 0) < feed.get('total', 0):
        pending = True
        group = frame.get('ctx', {}).get('execution_group') or run.get('execution_group') or 'legacy:'+run['id']
        if not controls.get(group, {}).get('bypassed'):
            return True
    # Allow stack cleanup/completion when nothing remains in this scope.
    return not pending


def group_wait_reasons(store, workspace, scheduler, groups):
    reasons = {}
    pending = []
    for group in groups:
        if group['cancelled']: reasons[group['id']] = ''
        elif group['bypassed']: reasons[group['id']] = '已繞過'
        elif group['counts'].get('retained'): reasons[group['id']] = '等待：保留項目'
        else: pending.append(group['id'])
    if pending:
        controls = store.batch_controls(workspace, scheduler)
        runs = {group: controls.get(group, {}).get('run') for group in pending}
        states = run_states(store, {run for run in runs.values() if run})
        reasons.update({group: '等待：保留項目' if states.get(run) == ('paused', 'route') else ''
                        for group, run in runs.items()})
    return reasons


def group_wait_reason(store, workspace, scheduler, group):
    return group_wait_reasons(store, workspace, scheduler, [group])[group['id']]


def waiting_reason(chain):
    workspace = chain.window.state['workspace']
    schedulers = chain.window.state.get('multi_output', {}).get('schedulers', {})
    reasons = []
    for key in schedulers:
        groups = [g for g in chain.store.schedule_groups(workspace, key) if g['pending'] and not g['cancelled']]
        reasons.extend(group_wait_reasons(chain.store, workspace, key, groups).values())
    if reasons and all(reason == '已繞過' for reason in reasons):
        return '已繞過'
    if '等待：保留項目' in reasons:
        return '等待：保留項目'
    return ''
