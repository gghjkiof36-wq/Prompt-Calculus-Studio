"""Conditional local Undo for unsubmitted schedule records only.

Native execution is never rewound. Restoring a record pauses its owner so the
user can inspect it before explicitly resuming the original run.
"""
import copy
import json

MARKER='__pending_schedule_edit__'


def append(canvas, before, after):
    canvas.undo_stack.append((before,after))
    trim=getattr(canvas,'trim_history',None)
    if callable(trim):trim()
    else:
        canvas.undo_stack=canvas.undo_stack[-100:]
        while len(canvas.undo_stack)>1 and len(json.dumps(canvas.undo_stack,ensure_ascii=False,default=str).encode('utf-8'))>32_000_000:
            canvas.undo_stack.pop(0)
    canvas.redo_stack.clear();canvas._last_edit=None


def capture(store, ids):
    result={}
    for ident in ids:
        item=store.read(ident)
        row=store.db.execute('SELECT position FROM stage_journal WHERE id=?',(ident,)).fetchone()
        if not item or row is None:raise ValueError('預排程項目已不存在。')
        result[ident]=dict(item=item,position=row[0])
    return result


def untouched(store, values):
    ids=set(values)
    for value in values.values():
        item=value['item']
        if item['kind']!='entry' or item['status'] not in ('waiting','removed') or item.get('prompt_id'):
            return False
        if any(a.get('parent') in ids for a in store.rows('attempt',item['workspace'])):return False
        if any(e.get('parent') in ids for e in store.rows('entry',item['workspace'])):return False
    return True


def perform(canvas, ids, operation, label='修改等待項目'):
    runner=canvas.window.comfy.input_flow.chain;store=runner.store
    before=capture(store,ids);eligible=untouched(store,before)
    result=operation()
    after=capture(store,ids)
    if eligible and untouched(store,after) and before!=after:
        shared=dict(workspace=canvas.window.state['workspace'],server=runner.client.url,
                    directory=str(canvas.window.store.directory),label=label)
        append(canvas,{MARKER:dict(shared,records=copy.deepcopy(before))},
               {MARKER:dict(shared,records=copy.deepcopy(after))})
    return result


def created(canvas, ident):
    """Undo a local copy by retaining its record as removed, never deleting it."""
    runner=canvas.window.comfy.input_flow.chain
    after=capture(runner.store,[ident])
    if not untouched(runner.store,after):return
    before=copy.deepcopy(after);before[ident]['item']['status']='removed'
    shared=dict(workspace=canvas.window.state['workspace'],server=runner.client.url,
                directory=str(canvas.window.store.directory),label='複製等待項目')
    append(canvas,{MARKER:dict(shared,records=before)}, {MARKER:dict(shared,records=after)})


def restore(canvas, source, destination, after):
    if not source or MARKER not in source[-1][0]:return False
    before,later=source[-1];expected,target=(before,later) if after else (later,before)
    expected=expected[MARKER];target=target[MARKER]
    runner=canvas.window.comfy.input_flow.chain;store=runner.store
    try:
        if (expected['workspace']!=canvas.window.state['workspace'] or expected['server']!=runner.client.url or
                expected['directory']!=str(canvas.window.store.directory)):
            raise ValueError('工作區或連線已改變，未復原其他預排程。')
        current=capture(store,expected['records'])
        if current!=expected['records'] or not untouched(store,current):
            raise ValueError('此項已開始執行或已被修改，不能回復為未提交狀態。')
        runs={v['item']['run'] for v in current.values()}
        for ident in runs:
            run=store.read(ident)
            if not run or run['status'] in ('complete','cancelled') or run['server']!=runner.client.url:
                raise ValueError('原流程已結束或連線不符，未重建或重送。')
        waiting=store.rows('entry',expected['workspace'],active=True)
        for scheduler in {v['item']['scheduler'] for v in target['records'].values()}:
            count=sum(r['scheduler']==scheduler and r['id'] not in current for r in waiting)
            count+=sum(v['item']['scheduler']==scheduler and v['item']['status']=='waiting' for v in target['records'].values())
            if count>10:raise ValueError('預排程已滿十項，請先移除其他等待項目。')
        with store.db:
            for ident in runs:runner.pause(ident,'已復原等待項目；檢查內容後按「繼續」。')
            for ident,value in target['records'].items():
                item=value['item'];store.set_scheduler_paused(item['workspace'],item['scheduler'],True)
                store.db.execute('UPDATE stage_journal SET position=?,body=? WHERE id=?',
                                 (value['position'],json.dumps(item,ensure_ascii=False),ident))
        source.pop();destination.append((before,later));canvas._last_edit=None;runner.notify('已復原等待項目；流程保持暫停。')
        canvas.window.notice('已復原等待項目；檢查內容後按「繼續」。')
    except ValueError as exc:canvas.window.notice(str(exc))
    return True
