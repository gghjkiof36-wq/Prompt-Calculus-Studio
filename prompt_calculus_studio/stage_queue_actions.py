"""Item-scoped schedule actions; keep sibling inputs and native receipts."""


def context(runner,ident):
    item=runner.store.read(ident)
    if not item or item.get('kind')!='entry':raise ValueError('此預排程項目已不存在。')
    run=runner.store.read(item['run']);entries=runner.store.rows('entry',item['workspace']);ids={ident}
    while True:
        children={e['id'] for e in entries if e.get('parent') in ids}
        if children<=ids:break
        ids.update(children)
    attempts=[a for a in runner.store.rows('attempt',owner=item['run']) if a.get('parent') in ids]
    return item,run,ids,attempts


def can_remove(runner,ident):
    try:item,run,ids,attempts=context(runner,ident)
    except ValueError:return False
    if item['workspace']!=runner.window.state['workspace']:return False
    if item['status']=='waiting':return True
    if any((runner.client.generation.record(a['id']) or {}).get('state','failed') not in ('complete','failed') for a in attempts):return False
    return bool(runner.entry_status(item)=='failed' and attempts and
        all(not a.get('cancel_requested') and a['status'] in ('complete','failed','cancelled','removed','retried') for a in attempts))


def finish_entry(runner,ident,status):
    item,run,ids,attempts=context(runner,ident)
    if item['status'] not in ('waiting','running'):return
    index=next((i for i,f in enumerate(run['stack']) if f.get('kind')=='loop' and f.get('active')==ident),None)
    if item['status']=='running' and index is None:raise ValueError('此項目的執行位置已改變，請重新整理紀錄。')
    with runner.store.db:
        if index is not None:
            # Remove this child's continuation, never siblings/outer frames.
            # Partial results of a discarded item must not feed downstream.
            run['stack']=run['stack'][:index+1]
            frame=run['stack'][-1];frame['active']=None;frame['done']+=1;runner.save(run)
        for entry in runner.store.rows('entry',item['workspace']):
            if entry['id'] in ids and entry['status'] in ('waiting','running'):runner.store.update(entry['id'],status=status)
        for attempt in attempts:
            if attempt['status'] in ('failed','cancelled'):
                runner.store.update(attempt['id'],status='removed',previous_status=attempt['status'])
    if index is not None:runner.pause(run['id'],'此項已結束；其他等待項目保留，按「繼續」接續。')
    runner.later();runner.notify()


def remove_entry(runner,ident):
    if not can_remove(runner,ident):raise ValueError('只能移除等待或已確認失敗的項目；提交未確認時請先核對原任務。')
    finish_entry(runner,ident,'removed')


def finish_cancel(runner,attempt):
    if not attempt.get('cancel_requested'):return
    if attempt.get('parent'):
        runner.store.update(attempt['id'],status='cancelled',cancel_requested=False)
        finish_entry(runner,attempt['parent'],'cancelled')
    else:
        # No schedule boundary: retain the stopped Stage for explicit retry,
        # since downstream cannot continue without its required result.
        runner.store.update(attempt['id'],status='failed',cancel_requested=False,error='使用者取消目前工作；後續已暫停。')
    if runner.preparing==attempt['id']:runner.preparing=None
    runner.notify('已取消目前項目；其他等待項目保留並暫停。')


def cancel_attempt(runner,attempt):
    run=runner.store.read(attempt['owner'])
    if run['workspace']!=runner.window.state['workspace'] or run['server']!=runner.client.url:
        raise ValueError('工作區或連線已變更，未取消其他工作。')
    if attempt.get('cancel_requested'):runner.notify('正在核對這一項的取消結果；後續項目保留。');return
    runner.pause(run['id']);runner.store.update(attempt['id'],cancel_requested=True)
    if runner.preparing==attempt['id']:runner.preparing=None
    runner.cancel_inputs(run)
    job=runner.client.generation.record(attempt['id'])
    if not job or job['state'] in ('complete','failed'):
        finish_cancel(runner,runner.store.read(attempt['id']));return
    store=runner.store
    def received(result):
        if runner.closed or runner.store is not store:return
        current=store.read(attempt['id'])
        if not current or not current.get('cancel_requested'):return
        if result.get('state')=='abandoned':runner.client.generation.retired(attempt['id'],result)
        elif result.get('state')=='failed':
            generation=runner.client.generation;original=generation.jobs.get(attempt['id']) or generation.record(attempt['id'])
            if original:
                original.update(state='failed',terminal_action='cancel',error='使用者取消目前項目。')
                generation.save(original);generation.jobs.pop(attempt['id'],None);generation.native_waiting.discard(attempt['id'])
            finish_cancel(runner,current)
        else:
            # A cancellation request is not a terminal receipt. Keep tracking.
            runner.client.generation.recheck(attempt['id'])
            runner.notify('已要求取消這一項，等待原任務確認；後續項目保留。')
    def failed(error):
        if not runner.closed and runner.store is store:
            store.update(attempt['id'],cancel_requested=False)
            runner.notify('取消尚未確認：'+str(error)+'；後續項目保留並暫停。')
    runner.client.request('workflow/native/cancel',dict(id=attempt['id']),done=received,failed=failed)


def cancel_current(runner,entry_id=None):
    if entry_id:
        item,run,ids,attempts=context(runner,entry_id)
        if item['workspace']!=runner.window.state['workspace']:raise ValueError('工作區已切換，未取消其他工作。')
        if can_remove(runner,entry_id):remove_entry(runner,entry_id);return
    else:
        owners={r['id'] for r in runner.runs(True) if r['server']==runner.client.url}
        attempts=[a for a in runner.store.rows('attempt',runner.window.state['workspace'],active=True) if a['owner'] in owners]
    attempt=next((a for a in attempts if a['status'] in ('preparing','submitted','unconfirmed','collecting')),None)
    if attempt is None:attempt=next((a for a in attempts if a['status']=='failed'),None)
    if attempt:cancel_attempt(runner,attempt);return
    if runner.applying:runner.cancel_apply('已取消目前輸入同步。')
    for run in runner.runs(True):
        if run['server']==runner.client.url and (not entry_id or run['id']==item['run']):runner.pause(run['id'])
    runner.notify('目前沒有可取消的生成；等待項目已保留並暫停。')
