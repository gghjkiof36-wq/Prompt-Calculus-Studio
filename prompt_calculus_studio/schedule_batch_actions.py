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
    control=store.batch_control(workspace,scheduler,group)
    snapshots=[h for h in store.batch_heads(workspace) if h['scheduler']==scheduler and h['execution_group']==group]
    all_entries=store.entry_descendants(workspace,[r['id'] for r in rows])
    ids={r['id'] for r in all_entries}
    attempts=store.entry_attempts(workspace,ids,active=True)
    runs={r['run'] for r in all_entries}
    runs.update(h['run'] for h in snapshots)
    # Include an automatic group whose next segment has not materialized yet.
    for run in runner.runs(True):
        if any(f.get('node',{}).get('key')==scheduler and
               (f.get('ctx',{}).get('execution_group') or run.get('execution_group') or 'legacy:'+run['id'])==group for f in run['stack']):runs.add(run['id'])
    loaded=[store.read(key) for key in runs]
    if any(r and r['server']!=runner.client.url for r in loaded):raise ValueError('請連線至此批次原本的服務再取消。')
    released=False
    withdrawn=False
    with runner.accepting() as transaction:
        if control.get('serial'):
            from .stage_batches import cursors,restore_cursors
            newer=store.db.execute("SELECT body FROM stage_header WHERE kind='batch_control' AND workspace=? AND position>(SELECT position FROM stage_header WHERE id=?)",(workspace,control['id']))
            import json
            overlap=any(not c.get('withdrawn') and set(c.get('cursor_keys',[])) & set(control.get('cursor_keys',[])) for c in (json.loads(r[0]) for r in newer))
            withdrawn=(not control.get('started') and not any(r['status'] not in ('waiting','retained','removed') for r in all_entries)
                and not overlap and cursors(runner.window.state,control.get('cursor_keys',[]))==control.get('cursor_after',{}))
            if withdrawn:
                restore_cursors(runner.window.state,control.get('cursor_before',{}),runner.window.store.directory,register=transaction.register)
                runner.window.store.save(runner.window.state,commit=False)
                released=True
            else:
                from .stage_batches import release_images
                released=release_images(runner.window.state,control,register=transaction.register)
            for head in snapshots:store.delete_batch_snapshot(head['id'])
        store.set_batch_control(workspace,scheduler,group,cancelled=True,bypassed=False)
        if withdrawn:store.set_batch_control(workspace,scheduler,group,withdrawn=True)
        for row in all_entries:
            if row['id'] in ids and row['status'] in ('waiting','retained'):
                store.patch(row['id'],status='removed' if withdrawn else 'cancelled')
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
                            transaction.register('canvas_functions','images',key)
                            image.pop('supply_reservation',None);released=True
            runner.save(run)
            if any(a['owner']==run['id'] for a in attempts):
                store.patch(run['id'],batch_cancel_resume=run['status']=='running',status='paused',pause_reason='batch_cancel')
        for attempt in attempts:store.patch(attempt['id'],cancel_requested=True)
        if released:runner.window.store.save(runner.window.state,commit=False)
    def publish_cancel():
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
        runner.later()
        runner.notify((f"已撤回第 {control['serial']} 組" if withdrawn else f"已取消第 {control['serial']} 組；位置未退回") if control.get('serial') else '')


    store._transactions.after_commit(publish_cancel)


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
        def preferred(key):
            entry=runner.store.preferred_entry(run['workspace'],key)
            if entry:return entry.get('run')
            # A result-dependent snapshot cannot produce an entry until its
            # upstream Stage runs. Its accepted group still owns its priority;
            # otherwise prioritizing/bypassing before preparation can deadlock.
            groups=runner.store.schedule_groups(run['workspace'],key)
            first=next((g for g in groups if g['pending'] and not g['cancelled'] and not g['bypassed']),None)
            if first:return runner.store.batch_control(run['workspace'],key,first['id']).get('run')
        if any(preferred(key)==run['id'] for key in keys):
            with runner.accepting():
                runner.store.patch(run['id'],status='running',pause_reason='')
            run['status']='running'
