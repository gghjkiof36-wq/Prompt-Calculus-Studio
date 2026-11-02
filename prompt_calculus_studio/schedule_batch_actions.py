"""Persistent, scheduler-scoped batch intentions over owned queue entries."""
def entries(runner,scheduler,group):
    workspace=runner.window.state['workspace']
    return runner.store.entry_summaries(workspace,scheduler,group=group)


def require_group(runner,scheduler,group):
    if scheduler not in runner.window.state.get('multi_output',{}).get('schedulers',{}):
        raise ValueError('此預排程已不存在。')
    groups=runner.store.schedule_groups(runner.window.state['workspace'],scheduler)
    value=next((g for g in groups if g['id']==group),None)
    if value is None:raise ValueError('此批次已結束或更新。')
    return value


def reorder(runner,scheduler,ids,expected):
    runner.store.reorder_groups(runner.window.state['workspace'],scheduler,ids,expected)
    runner.later();runner.notify()


def prioritize(runner,scheduler,group):
    value=require_group(runner,scheduler,group)
    if value['cancelled'] or not (value['pending'] or value['counts'].get('waiting') or value['counts'].get('running')):
        raise ValueError('此批次沒有等待執行的項目。')
    groups=runner.store.schedule_groups(runner.window.state['workspace'],scheduler)
    ids=[g['id'] for g in groups]
    with runner.store.transaction():
        runner.store.set_batch_control(runner.window.state['workspace'],scheduler,group,bypassed=False)
        runner.store.reorder_groups(runner.window.state['workspace'],scheduler,[group]+[g for g in ids if g!=group],ids)
    runner.later();runner.notify()


def bypass(runner,scheduler,group):
    value=require_group(runner,scheduler,group)
    if value['cancelled']:raise ValueError('這個批次已取消。')
    rows=entries(runner,scheduler,group)
    if not value['bypassed'] and any(r['status']=='running' and runner.entry_status(r) in ('failed','unconfirmed','result_error','cancelling') for r in rows):
        raise ValueError('請先處理此批次尚未結束的項目。')
    runner.store.set_batch_control(runner.window.state['workspace'],scheduler,group,bypassed=not value['bypassed'])
    runner.later();runner.notify()


def cancel(runner,scheduler,group):
    value=require_group(runner,scheduler,group)
    if value['cancelled']:return
    workspace=runner.window.state['workspace'];store=runner.store
    rows=entries(runner,scheduler,group)
    all_entries=store.entry_descendants(workspace,[r['id'] for r in rows])
    ids={r['id'] for r in all_entries}
    attempts=store.entry_attempts(workspace,ids,active=True)
    runs={r['run'] for r in all_entries}
    # Include an automatic group whose next segment has not materialized yet.
    for run in runner.runs(True):
        if any(f.get('node',{}).get('key')==scheduler and
               (f.get('ctx',{}).get('execution_group') or run.get('execution_group') or 'legacy:'+run['id'])==group for f in run['stack']):runs.add(run['id'])
    loaded=[store.read(key) for key in runs]
    if any(r and r['server']!=runner.client.url for r in loaded):raise ValueError('請連線至此批次原本的服務再取消。')
    released=False
    with runner.accepting():
        store.set_batch_control(workspace,scheduler,group,cancelled=True,bypassed=False)
        for row in all_entries:
            if row['id'] in ids and row['status'] in ('waiting','retained'):
                store.patch(row['id'],status='cancelled')
        for run in loaded:
            if not run:continue
            for frame in run['stack']:
                matches=(frame.get('node',{}).get('key')==scheduler and
                         (frame.get('ctx',{}).get('execution_group') or run.get('execution_group') or 'legacy:'+run['id'])==group)
                if matches or frame.get('ctx',{}).get('parent') in ids:
                    feed=frame.get('feed') or {}
                    if feed:feed['cancelled']=True
                    plan=feed.get('plan') or {}
                    # Release only this cancelled scope's future image range.
                    for key,spec in plan.get('sources',{}).items():
                        image=runner.window.state.get('canvas_functions',{}).get('images',{}).get(key,{})
                        if spec['kind']=='image' and image.get('supply_reservation',{}).get('plan')==plan['id']:
                            image.pop('supply_reservation',None);released=True
            runner.save(run)
            if any(a['owner']==run['id'] for a in attempts):
                store.patch(run['id'],batch_cancel_resume=run['status']=='running',status='paused',pause_reason='batch_cancel')
        for attempt in attempts:store.patch(attempt['id'],cancel_requested=True)
        if released:runner.window.store.save(runner.window.state,commit=False)
    if runner.preparing in {a['id'] for a in attempts}:runner.preparing=None
    from .stage_queue_actions import request_cancel,finish_entry
    # A running scope can sit between two Stages without an outstanding job.
    for row in rows:
        if row['status']=='running' and not any(a.get('parent') in ids and a['owner']==row['run'] for a in attempts):
            finish_entry(runner,row['id'],'cancelled',pause=False)
    for run in loaded:
        if run and any(a['owner']==run['id'] for a in attempts):runner.cancel_inputs(run)
    for attempt in attempts:request_cancel(runner,store.read(attempt['id']))
    if released:runner.window.changed('prompt',refresh=False)
    runner.later();runner.notify()


def cancelled_parent(runner,attempt):
    parent=runner.store.read(attempt.get('parent')) if attempt.get('parent') else None
    while parent:
        group=parent.get('execution_group') or 'legacy:'+parent['run']
        if runner.store.batch_control(parent['workspace'],parent['scheduler'],group).get('cancelled'):
            return parent
        parent=runner.store.read(parent.get('parent')) if parent.get('parent') else None


def finish_cancelled_batch(runner,run_id):
    run=runner.store.read(run_id)
    if not run or run.get('pause_reason')!='batch_cancel':return
    attempts=runner.store.rows('attempt',owner=run_id,active=True)
    if any(a.get('cancel_requested') or a['status'] in ('failed','unconfirmed','result_error') for a in attempts):return
    if run.get('batch_cancel_resume') and not any(runner.store.scheduler_paused(run['workspace'],key) for key in runner.scheduler_keys(run['plan'])):
        runner.store.patch(run_id,status='running',pause_reason='',batch_cancel_resume=False)
        runner.later()


def promote_waiting(runner,runs):
    """A reordered future run may proceed only when its scheduler is free."""
    for run in runs:
        if run['status']!='waiting':continue
        keys=runner.scheduler_keys(run['plan'])
        if any(runner.store.scheduler_paused(run['workspace'],key) for key in keys):continue
        if any((runner.store.preferred_entry(run['workspace'],key) or {}).get('run')==run['id'] for key in keys):
            runner.store.patch(run['id'],status='running',pause_reason='');run['status']='running'
