"""Journaled foreign-input continuation; no executable callbacks in the journal."""
import copy
import json
import uuid
from .snapshots import make_snapshot


def pending_foreign(store, attempt):
    """Prove that the missing generation job still precedes its first send.

    A completed input batch remains in the journal after generation starts. It
    cannot exempt a later lost job from S5/S7 or authorize another submission.
    Read only this continuation's headers, never frozen captures/history.
    """
    if (attempt.get('submission_intent')!='not_sent'
            or attempt.get('local_origin',attempt.get('status')) not in ('preparing','unconfirmed')
            or not attempt.get('foreign_batch')):return False
    def header(ident):
        row=store.db.execute('SELECT body FROM stage_header WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else {}
    batch=header(attempt['foreign_batch'])
    continuation=batch.get('continuation',{})
    if (batch.get('kind')!='input_batch' or batch.get('status') not in ('waiting','applied')
            or batch.get('owner')!=attempt['owner'] or batch.get('workspace')!=attempt['workspace']
            or continuation.get('kind')!='foreign' or continuation.get('attempt')!=attempt['id']):return False
    server=store.db.execute("SELECT json_extract(body,'$.server') FROM stage_header WHERE id=? AND kind='run'",(attempt['owner'],)).fetchone()
    steps=batch.get('steps')
    if not server or batch.get('server')!=server[0] or not isinstance(steps,list) or not steps:return False
    for key in steps:
        if not isinstance(key,str):return False
        item=header(key)
        if (item.get('kind')!='input' or item.get('aux_version')!=1
                or item.get('owner')!=attempt['owner'] or item.get('workspace')!=attempt['workspace']
                or item.get('server')!=server[0] or item.get('continuation',{}).get('batch')!=batch['id']
                or (item.get('ended') and item.get('status')!='applied')
                or (batch['status']=='applied' and not item.get('ended'))):return False
    return True


def input_state(runner, workflow, bindings):
    from .multi_output import new_output
    state=copy.deepcopy(runner.window.state);data=state['multi_output'];images=[]
    data.update(canvases={},outputs={},clip_inputs={},image_inputs={},bindings=[],connections=[],schedulers={},stages={},current_output=None)
    data['workflow_order']=dict(visible=False,items=[],established=[])
    state.update(uses={},output_order=[],draft=None,draft_base='');state.pop('workspace_scenes',None)
    state['canvas_functions']=dict(images={},preview=True,preview_attached=False)
    for index,(binding,value) in enumerate(bindings):
        target=binding['target'];key=str(index)
        if binding['kind']=='image':images.append((target['node'],value['value']));continue
        data['outputs']['out_'+key]=dict(new_output('同步文字'),draft=value['value'] if value else '',canvases=[],text_sources=[])
        data['clip_inputs'][key]=dict(name='CLIP 輸入',workflow=workflow,text_source=binding.get('text_source','pcs'))
        data['bindings'].append(dict(workflow=workflow,clip=key,node=target['node'],field=target['field']))
        data['connections'].append(dict(id=key,source='out_'+key,destination=key,kind='clip'))
    return state,images


def begin(runner, groups, done, failed, valid, owner, continuation):
    aux=runner.client.events.aux
    batch=uuid.uuid4().hex
    context=(runner.window.store,runner.window.state['workspace'],runner.client.url,runner.client.epoch)
    def guard():return context==(runner.window.store,runner.window.state['workspace'],runner.client.url,runner.client.epoch) and (valid is None or valid())
    if not guard():failed('資料不一致');return
    steps=[]
    with runner.accepting():
        for workflow,bindings in groups.items():
            state,images=input_state(runner,workflow,bindings)
            snapshot=make_snapshot(state,runner.window.store.library_id)
            item=aux.create('apply',dict(snapshot=snapshot,workflow=workflow,images=[]),owner=owner or '',
                            continuation=dict(batch=batch))
            runner.store.patch(item['id'],input_images=images)
            steps.append(item['id'])
        runner.store.add('input_batch',context[1],owner or '',ident=batch,server=context[2],steps=steps,
                         status='waiting',continuation=continuation or {})
        if continuation and continuation.get('kind')=='foreign':
            runner.store.patch(continuation['attempt'],foreign_batch=batch,foreign_submission=continuation)
    runner.__dict__.setdefault('_input_callbacks',{})[batch]=(done,failed,guard)
    runner.__dict__.setdefault('_input_waiting',set()).add(batch)
    start_next(runner,batch)


def start_next(runner, batch):
    if runner.closed:return
    record=runner.store.read(batch)
    if not record or record['status'] in ('failed','cancelled'):
        runner.__dict__.get('_input_waiting',set()).discard(batch);return
    owner=runner.store.read(record['owner']) if record['owner'] else None
    if owner and owner['status']!='running':
        runner.__dict__.get('_input_waiting',set()).discard(batch);return
    callbacks=runner.__dict__.get('_input_callbacks',{}).get(batch)
    if callbacks and not callbacks[2]():
        cancel_batch(runner,record);return
    if record['status']=='applied':
        runner.__dict__.get('_input_waiting',set()).discard(batch)
        completed(runner,record);return
    item=next((runner.store.read(key) for key in record['steps'] if not runner.store.read(key)['ended']),None)
    if item is None:return
    if runner.applying and runner.applying!=item['id']:return
    if item['sent']:
        runner.__dict__.get('_input_waiting',set()).discard(batch)
        runner.client.events.aux.require(item['id'],'continuation');return
    if not runner.client.dispatch_allowed:return
    from .native_submission import prepare
    from .comfy_aux_operations import fingerprint
    aux=runner.client.events.aux
    ident=item['id'];runner.applying=ident;runner.apply_owner=record['owner'];runner.apply_operation=item
    runner.__dict__.get('_input_waiting',set()).discard(batch)
    def current():
        return not runner.closed and runner.applying==ident and runner.client.connected and (not callbacks or callbacks[2]())
    def failed(error):
        runner.applying=runner.apply_owner=runner.apply_operation=None
        if not item['sent']:
            # Upload/preparation has not sent this native operation. Preserve
            # the preparation error, do not turn it into an automatic retry.
            with runner.accepting():
                changed=runner.store.update(ident,ended=True,status='failed',error=str(error))
                accept(runner,changed)
                runner.store._transactions.after_commit(lambda:aux.published(changed))
        callback=runner.__dict__.get('_input_callbacks',{}).pop(batch,None)
        if callback:callback[1](str(error))
    def submitted(snapshot,uploads):
        if not current():return
        request=dict(id=ident,snapshot=snapshot,workflow=item['request']['workflow'],images=uploads)
        with runner.accepting():runner.store.patch(ident,request=request,request_hash=fingerprint('apply',request))
        item['sent']=True
        aux.send(ident,failed=lambda error:failed(error))
    runner.apply_abort=lambda message:cancel_batch(runner,record)
    try:prepare(runner.client,item['request']['snapshot']['state'],item['request']['workflow'],current,submitted,failed,image_values=item.get('input_images',[]))
    except (OSError,ValueError) as exc:failed(exc)


def accept(runner, item):
    """Inside the receipt's accepting transaction: next-step/result checkpoint."""
    batch=item.get('continuation',{}).get('batch')
    record=runner.store.read(batch) if batch else None
    if not record:return
    steps=[runner.store.read(key) for key in record['steps']]
    failed=any(s['ended'] and s['status']!='applied' for s in steps)
    state='failed' if failed else 'applied' if all(s['ended'] for s in steps) else 'waiting'
    runner.store.patch(batch,status=state)
    if failed:
        for step in steps:
            if not step['sent'] and not step['ended']:
                changed=runner.store.update(step['id'],ended=True,status='cancelled')
                runner.store._transactions.after_commit(lambda changed=changed:runner.client.events.aux.published(changed))
    continuation=record['continuation']
    owner=runner.store.read(record['owner']) if record['owner'] else None
    if continuation.get('kind')=='foreign':
        attempt=runner.store.read(continuation['attempt'])
        if state=='applied':
            runner.store.patch(attempt['id'],foreign_ready=True,
                **(dict(status='preparing',error='') if attempt['status']=='unconfirmed' else {}))
        elif failed and attempt['status'] in ('preparing','unconfirmed'):
            runner.store.patch(attempt['id'],status='cancelled' if owner and owner['status']=='cancelled' else 'failed',error=item.get('error') or '無法確認')
            if owner and owner['status']!='cancelled' and owner.get('pause_reason') not in ('manual','restart'):
                runner.pause(owner['id'],'無法確認',reason='error')
    elif continuation.get('kind')=='free' and owner and owner.get('free_state')=='applying':
        if state in ('applied','failed'):runner.store.patch(owner['id'],free_state=state,free_error=item.get('error',''))


def received(runner, item):
    if runner.applying==item['id']:
        runner.applying=runner.apply_owner=runner.apply_operation=runner.apply_abort=None
    batch=item.get('continuation',{}).get('batch')
    record=runner.store.read(batch) if batch else None
    if not record:return
    if record['status']=='failed':
        callback=runner.__dict__.get('_input_callbacks',{}).pop(batch,None)
        if callback:callback[1](item.get('error') or '無法確認')
    elif batch in runner.__dict__.get('_input_callbacks',{}):
        runner.__dict__.setdefault('_input_waiting',set()).add(batch)
        start_next(runner,batch)


def unconfirmed(runner, item):
    """Same outer acceptance transaction as the operation's deadline receipt."""
    batch=item.get('continuation',{}).get('batch')
    record=runner.store.read(batch) if batch else None
    if not record:return
    continuation=record['continuation']
    if continuation.get('kind')=='foreign':
        attempt=runner.store.read(continuation['attempt'])
        if attempt['status']=='preparing':runner.store.patch(attempt['id'],status='unconfirmed',error='無法確認')
        owner=runner.store.read(record['owner'])
        if owner['status'] not in ('cancelled','complete') and owner.get('pause_reason') not in ('manual','restart'):
            runner.pause(owner['id'],'無法確認',reason='error')


def completed(runner, record):
    callback=runner.__dict__.get('_input_callbacks',{}).pop(record['id'],None)
    if callback:
        if callback[2]():callback[0]()
        return
    continuation=record['continuation']
    if continuation.get('kind')=='foreign':resume_foreign(runner,continuation)
    else:runner.later()


def resume(runner, owner):
    for batch in runner.store.rows('input_batch',owner=owner):
        if batch['status'] in ('waiting','applied'):
            runner.__dict__.setdefault('_input_waiting',set()).add(batch['id'])
            start_next(runner,batch['id'])


def resume_foreign(runner, continuation):
    """Only an explicit Resume may dispatch a recovered, never-sent attempt."""
    from .native_submission import prepare
    ident=continuation['attempt'];attempt=runner.store.read(ident)
    if (not attempt or attempt['status']!='preparing' or not attempt.get('foreign_ready')
            or not pending_foreign(runner.store,attempt) or runner.client.generation.record(ident)):return
    run=runner.store.read(attempt['owner'])
    if not run or run['status']!='running':return
    runner.preparing=ident
    def valid():return not runner.closed and runner.preparing==ident and runner.store.status_value(run['id'])=='running'
    def failed(error):
        if valid():
            runner.preparing=None
            with runner.accepting():
                runner.store.patch(ident,status='failed',error=str(error));runner.pause(run['id'],str(error),reason='error')
    def submit(snapshot,uploads):
        if not valid():return
        snapshot['chain']=continuation['chain']
        if continuation.get('replay'):snapshot['chain_replay']=continuation['replay']
        runner.client.generation.submit_native(snapshot,continuation['workflow'],lambda _:None,failed,valid,images=uploads,ident=ident)
    try:prepare(runner.client,attempt['snapshot_state'],continuation['workflow'],valid,submit,failed,image_values=attempt['images'])
    except (ValueError,OSError) as exc:failed(exc)


def cancel_batch(runner, record):
    runner.__dict__.get('_input_waiting',set()).discard(record['id'])
    runner.__dict__.get('_input_callbacks',{}).pop(record['id'],None)
    for key in record['steps']:
        item=runner.store.read(key)
        if item['ended']:continue
        if item['sent']:runner.client.events.aux.cancel(key)
        else:
            with runner.accepting():
                changed=runner.store.update(key,ended=True,status='cancelled')
                accept(runner,changed)
                runner.store._transactions.after_commit(lambda changed=changed:runner.client.events.aux.published(changed))
    if runner.apply_operation and runner.apply_operation['id'] in record['steps']:
        runner.applying=runner.apply_owner=runner.apply_operation=runner.apply_abort=None
