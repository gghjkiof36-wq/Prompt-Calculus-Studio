"""Fresh browser acknowledgement before an operation can own a native graph."""
import asyncio
import copy
import json
import time
import uuid


async def cancel_operation(queue,ident,native_queue,interrupt,notify,timeout=2.0):
    """A delivered apply is stopped before reporting confirmed cancellation.

    Missing/closed browser acknowledgement is bounded and cannot block Run.
    The persisted failed receipt also rejects a late command's permission check.
    """
    result=queue.cancel(ident,native_queue,interrupt)
    if result.get('state')!='cancelling':return result
    operation=queue.read(ident)
    if not operation.get('cancel_requested'):return result
    payload={k:operation[k] for k in ('id','session','epoch','identity','client_id')}
    deadline=time.monotonic()+timeout;sent=float('-inf')
    while True:
        current=queue.read(ident)
        if current.get('cancel_confirmed'):return dict(ok=True,state='failed')
        now=time.monotonic()
        if now-sent>=.5:
            notify('prompt_studio_native_cancel',payload);sent=now
        if now>=deadline:return dict(ok=True,state='cancelling',reason='已停止後續派送，等待原生頁面確認輸入操作已停止。')
        await asyncio.sleep(.025)


async def handshake(queue, target, notify, ident, timeout=7.0):
    """Waiting here cannot generate: no command has been dispatched."""
    probe=uuid.uuid4().hex; deadline=time.monotonic()+timeout; sent=float('-inf')
    def identities(live):
        values=[live['identity'],*live.get('workflows',[])]
        return [item for item in values if (queue.target_matches(item,target) if target.get('frontend_id') else
                item.get('path')==target.get('path') and bool(item.get('frontend_id')))]
    reason='native_not_ready'; message='ComfyUI 網頁尚未回覆，請確認網頁與擴充已開啟。'
    while time.monotonic()<deadline:
        if queue.read(ident)['state']!='awaiting_browser':return None
        now=time.monotonic()
        if now-sent>=.5:
            notify('prompt_studio_native_probe',dict(probe=probe));sent=now
        replies=[(key,live) for key,live in queue.sessions.items()
                 if live.get('probe')==probe and now-live['seen']<3]
        matches=[(key,live) for key,live in replies if identities(live)]
        if len(matches)>1:
            reason='native_ambiguous';message='同一工作流同時開在多個 ComfyUI 網頁，無法決定提交位置。';break
        # A previously known duplicate must answer too. Do not pick whichever
        # browser happened to respond first to this acknowledgement request.
        unseen=any(identities(live) and now-live['seen']<10 and live.get('probe')!=probe
                   for live in queue.sessions.values())
        if len(matches)==1 and not unseen:
            _,live=matches[0]
            actual=identities(live)
            if len({item['frontend_id'] for item in actual})!=1:
                reason='native_ambiguous';message='同一路徑有多個原生工作流身分，未讀取或提交。';break
            if live['ready']:
                return dict(target,frontend_id=actual[0]['frontend_id'])
            reason='native_busy';message=live.get('reason') or 'ComfyUI 正在完成輸入或提交，稍後可再執行。'
        elif replies and not unseen:
            reason='native_target_missing';message='已開啟的 ComfyUI 網頁中找不到綁定的工作流，請開啟該工作流。'
        await asyncio.sleep(.05)
    observation=dict(id=ident,reason=reason,target=copy.deepcopy(target),created=time.time(),sessions=[
        dict(session=key,identity=copy.deepcopy(live['identity']),workflows=copy.deepcopy(live.get('workflows',[])),
             age=round(time.monotonic()-live['seen'],3),epoch=live['epoch'],ready=live['ready'],acknowledged=live.get('probe')==probe)
        for key,live in queue.sessions.items()])
    with queue.service.connect() as db:
        db.execute('INSERT OR REPLACE INTO native_diagnostics VALUES (?,?)',(ident,json.dumps(observation,ensure_ascii=False)))
    raise ValueError(message+' ['+reason+']')


async def begin(queue,value,server,action,notify,timeout=7.0):
    """One ownership lane for inspection, switching, input application and Run.

    A repeated request ID returns its existing receipt, including unknowns;
    only an undelivered new request is allowed to wait for a browser handshake.
    """
    from .native_queue import digest
    queue.require_supported()
    target=queue.request_target(value,server,inspect=action=='inspect')
    fingerprint=digest(value if action=='queue' else [action,value])
    deadline=time.monotonic()+timeout
    with queue.service.connect() as db:
        old=db.execute('SELECT body FROM native_operations WHERE id=?',(value['id'],)).fetchone()
    if old:
        if json.loads(old[0])['request_hash']!=fingerprint:raise ValueError('同一操作識別碼的內容不同。')
        return queue.status(value['id'])
    # Cancellation can arrive during the handshake or while another operation
    # owns the graph. Persist that click before the first await, so cancel has
    # an exact receipt and a late browser acknowledgement cannot resurrect it.
    queue.save(dict(id=value['id'],request_hash=fingerprint,created=time.time(),state='awaiting_browser',action=action,target=copy.deepcopy(target),error=''))
    acquired=False
    try:
        try:await asyncio.wait_for(queue.entry_lock.acquire(),timeout);acquired=True
        except TimeoutError:raise ValueError('另一項原生操作仍在準備，未提交。 [native_busy]')
        if queue.read(value['id'])['state']!='awaiting_browser':return queue.status(value['id'])
        # A concurrent inspect must not navigate while an earlier command is
        # delivered. Wait only for its transport, never for generation itself.
        while True:
            if queue.read(value['id'])['state']!='awaiting_browser':return queue.status(value['id'])
            with queue.service.connect() as db:
                pending=[r[0] for r in db.execute("SELECT id FROM native_operations WHERE json_extract(body,'$.state') IN ('pending','delivered','prepared','submitted')")]
            if not any(queue.status(ident)['state'] in ('pending','delivered','prepared','submitted') for ident in pending):break
            if time.monotonic()>=deadline:raise ValueError('另一項原生操作仍在提交，未建立另一份要求。 [native_busy]')
            await asyncio.sleep(.05)
        target=await handshake(queue,target,notify,value['id'],max(.01,deadline-time.monotonic()))
        if queue.read(value['id'])['state']!='awaiting_browser':return queue.status(value['id'])
        result=queue.start_inspect(value,target) if action=='inspect' else queue.start(value,server,action,admitted=True)
        operation=queue.read(value['id'])
        if operation['state']=='pending':
            # The handshake poll ran BEFORE this command existed. Wake the
            # selected frontend after persistence; hidden-tab timers may sleep.
            # This is a read notification, never another submission or payload.
            notify('prompt_studio_native_pending',{k:operation[k] for k in ('id','session','client_id')})
        return result
    except (ValueError,asyncio.CancelledError) as exc:
        operation=queue.read(value['id'])
        if operation['state']=='awaiting_browser':
            operation.update(state='failed',error=str(exc) or '原生操作準備已取消，尚未派送。');queue.save(operation)
        raise
    finally:
        if acquired:queue.entry_lock.release()
