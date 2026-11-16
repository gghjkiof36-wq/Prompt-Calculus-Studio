"""Durable native read/input intents, one receipt acceptance and bounded queries.

The journal owns lifecycle and continuations. HTTP callbacks only request
evidence; UI callbacks are disposable, after-commit notifications.
"""
import copy
import hashlib
import json
import sqlite3
import time
import uuid
from pathlib import PurePosixPath
from PySide6.QtCore import QTimer
from . import comfy_event_protocol as protocol

ROUTES = dict(open='workflow/native/open', inspect='workflow/native/inspect',
              capture='workflow/queue/capture', apply='workflow/native/apply')
SUCCESS = dict(open='opened', inspect='inspected', capture='captured', apply='applied')
LONG_PROBE_DELAYS = (30, 60, 120, 300)


def fingerprint(action, request):
    return hashlib.sha256(json.dumps([action, request], sort_keys=True,
        ensure_ascii=False, allow_nan=False).encode()).hexdigest()


class AuxOperations:
    def __init__(self, reducer, *, clock=time.monotonic):
        self.reducer, self.client, self.window = reducer, reducer.client, reducer.window
        self.store = self.client.input_flow.chain.store
        self.clock = clock
        self.pending, self.callbacks, self.active = {}, {}, {}
        self.acked = set()
        self.known = set()
        self.healthy = True
        self.timer = QTimer(self.client)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)
        # Cold owner construction only. No scan in pump, health or delivery.
        for ident, ended in self.store.db.execute("SELECT id,json_extract(body,'$.ended') FROM stage_header WHERE kind IN ('input','aux_operation') AND json_extract(body,'$.aux_version')=1"):
            self.known.add(ident)
            if not ended:self.active[ident] = self.store.read(ident)

    @property
    def enabled(self):return self.reducer.enabled and self.reducer.identity is not None and hasattr(self.runner,'accepting')

    @property
    def runner(self):return self.client.input_flow.chain

    def owns(self, ident):return isinstance(ident,str) and ident in self.known

    def token(self):
        return getattr(self.window, 'runtime_token', None) or self.window.store.library_id

    def register_owner(self):
        workspace = getattr(self.client, 'workspace', None)
        channel = workspace.comfy_connection.channel if workspace is not None else getattr(getattr(self.client,'event_session',None),'channel',None)
        if channel is not None:
            channel.command.emit(dict(kind='aux_owner', owner=self.token(), entities=['native_operations:'+key for key in self.known]))

    def published(self, item):
        self.known.add(item['id'])
        if item['ended']:self.active.pop(item['id'], None)
        else:self.active[item['id']] = item

    def create(self, action, request, *, continuation=None, owner='', ident=None):
        if action not in ROUTES:raise ValueError('Unsupported auxiliary action')
        if not self.enabled:raise ValueError('需要更新擴充')
        identity = self.reducer.identity
        request = copy.deepcopy(request)
        ident = ident or request.get('id') or uuid.uuid4().hex
        request['id'] = ident
        snapshot = request.get('snapshot', {})
        workspace = snapshot.get('state', {}).get('workspace', self.window.state['workspace'])
        library = snapshot.get('library_id') or self.window.store.library_id
        if action == 'inspect':target = dict(request['target'])
        else:
            profile = next(p for p in snapshot['state']['generation']['profiles'] if p['id']==request['workflow'])
            target = dict(workflow=profile['id'], path=profile.get('origin',{}).get('path',''), frontend_id=profile.get('frontend_id',''))
        with self.runner.accepting():
            item = self.store.add('input' if action=='apply' else 'aux_operation', workspace, owner,
                ident=ident, aux_version=1, action=action, server=self.client.url,
                event_server=identity['server_instance'], event_epoch=identity['epoch'],
                runtime_owner=self.token(), library=library, target=target,
                request=request, request_hash=fingerprint(action,request), continuation=continuation or {},
                sent=False, ended=False, status='preparing', error='')
            if owner:
                run = self.store.read(owner)
                self.store.patch(owner, input_ids=list(dict.fromkeys([*run.get('input_ids', []), ident])))
            self.store._transactions.after_commit(lambda:self.published(item))
            self.store._transactions.after_commit(self.register_owner)
        return item

    def start(self, action, request, *, continuation=None, owner='', done=None, failed=None):
        workspace = getattr(self.client, 'workspace', None)
        if workspace is not None and not workspace.scheduler.acquire_operation(self.client):
            from .comfy_client import RequestFailure
            if failed:failed(RequestFailure('無法確認', rejected=True))
            return None
        item = self.create(action, request, continuation=continuation, owner=owner)
        self.send(item['id'], done=done, failed=failed)
        return item['id']

    def send(self, ident, *, done=None, failed=None):
        item = self.store.read(ident)
        if item['ended']:return
        if done or failed:self.callbacks[ident] = (done, failed)
        if item['sent']:
            self.require(ident, 'original_intent');return
        now = time.time()
        with self.runner.accepting():
            item = self.store.update(ident, sent=True, status='submitted', sent_at=now, deadline_at=now+30)
            self.store._transactions.after_commit(lambda:self.published(item))
        self.require(ident, 'sent', delay=12)
        generation = self.reducer.generation
        def ack(_):
            if generation!=self.reducer.generation:return
            if item['action']=='capture' and not item['continuation'].get('enqueue',True):self.acked.add(ident)
            saved=self.store.read(ident)
            if saved['ended'] and item['action']=='capture' and not item['continuation'].get('enqueue',True):
                self.notify_callback(ident,value=saved['result'] if saved['status']=='captured' else None,
                    error=None if saved['status']=='captured' else saved.get('error') or '無法確認')
            else:self.require(ident, 'ack', force=True)
        def lost(error):
            if generation!=self.reducer.generation:return
            # HTTP rejected is evidence to verify, never a terminal receipt.
            if getattr(error,'rejected',False):
                with self.runner.accepting():self.store.patch(ident, http_rejected=True, http_error=str(error))
            if item['action']=='capture' and not item['continuation'].get('enqueue', True):
                self.notify_callback(ident, error='無法確認')
            self.require(ident, 'ack_lost', force=True)
        self.client.request(ROUTES[item['action']], item['request'], done=ack, failed=lost)

    def bind(self):
        self.pending.clear();self.timer.stop()
        self.register_owner()
        for ident,item in self.active.items():
            if item['sent']:self.require(ident, 'startup')

    def require(self, ident, reason, *, event=None, done=None, force=False, delay=0):
        if ident not in self.active:
            if done:done()
            return
        now = self.clock()
        row = self.pending.get(ident)
        if row is None:
            item=self.active[ident]
            age=max(0,time.time()-item.get('sent_at',time.time())) if reason=='startup' else 0
            if reason=='startup' and item.get('status')=='unconfirmed':age=max(30,age)
            row=self.pending[ident]=dict(start=now-min(30,age), at=now+delay, tries=0, busy=False,
                expired=False, done=[], long_step=0, startup_probe=reason=='startup')
        if done:row['done'].append(done)
        if event is not None:row['event']=event
        if force:
            # New evidence permits one query, not a new 30-second window.
            row['at']=now
        row['reason']=reason
        self.schedule()

    def schedule(self):
        due=[(r.get('until',r['at']) if r['busy'] else r['at']) if r['expired'] else
             min(r.get('until',r['at']) if r['busy'] else r['at'],r['start']+30)
             for r in self.pending.values()]
        if due:self.timer.start(max(0,int((min(due)-self.clock())*1000)))
        else:self.timer.stop()

    def tick(self):
        if self.client.stopped:return
        now=self.clock()
        for ident,row in list(self.pending.items()):
            if row['busy'] and now>=row.get('until',now+1):
                row.update(busy=False,probed=True,serial=row.get('serial',0)+1)
                self.retry_at(row)
                handle=row.pop('handle',None)
                if handle is not None:handle.abort()
                self.reducer.probed()
            if not row['expired'] and now>=row['start']+30:
                try:self.unconfirmed(ident)
                except (sqlite3.Error,OSError):row['at']=now+1;row['start']=now-29;continue
                row['expired']=True
                row['at']=now if row.pop('startup_probe',False) else now+LONG_PROBE_DELAYS[0]
                self.finish_waiters(row)
            if not row['busy'] and now>=row['at']:self.query(ident,row)
        self.schedule()

    def retry_at(self, row):
        if row['expired']:
            step=min(row['long_step'],len(LONG_PROBE_DELAYS)-1)
            row['at']=self.clock()+LONG_PROBE_DELAYS[step]
            row['long_step']=min(step+1,len(LONG_PROBE_DELAYS)-1)
        else:row['at']=self.clock()+min(8,2**min(row['tries']-1,3))

    def query(self, ident, row):
        item=self.active.get(ident)
        if not item:return
        identity=self.reducer.identity
        if not identity or item['server']!=self.client.url or item['event_server']!=identity['server_instance']:
            row['probed']=True
            if row['expired']:self.retry_at(row)
            else:row['at']=row['start']+30
            self.reducer.probed();return
        row['busy']=True;row['tries']+=1
        row['serial']=row.get('serial',0)+1;serial=row['serial']
        timeout=12 if row['expired'] else min(12,max(.1,row['start']+30-self.clock()))
        row['until']=self.clock()+timeout
        generation=self.reducer.generation
        def current():return not self.client.stopped and generation==self.reducer.generation and self.pending.get(ident) is row and row['serial']==serial
        def failed(error):
            if not current():return
            row.update(busy=False,probed=True,error=str(error))
            self.retry_at(row)
            self.reducer.probed();self.schedule()
        def received(value):
            if not current():return
            row.update(busy=False,probed=True)
            self.reducer.probed()
            try:
                ended=self.accept(ident,value,event=row.get('event'))
            except (ValueError,KeyError,TypeError,OSError,sqlite3.Error) as exc:failed(exc);return
            if ended:
                self.pending.pop(ident,None);self.finish_waiters(row)
            elif row['expired']:self.retry_at(row)
            else:row['at']=self.clock()+8
            self.schedule()
        handle=self.client.request('workflow/native/recovery', dict(id=ident,
            proof_scope=dict(protocol.scope(), library_id=item['library'], workspace_id=item['workspace'])),
            done=received,failed=failed,deadline=time.monotonic()+timeout)
        if current() and row['busy']:row['handle']=handle

    def receive(self, event, done, *, snapshot=False):
        ident=event.get('entity','').removeprefix('native_operations:')
        if ident not in self.known:return False
        item=self.store.read(ident)
        scope=event.get('scope',{})
        expected_scope=dict(library_id=item['library'],workspace_id=item['workspace'])
        if (event['server_instance']!=item['event_server'] or
            event['epoch']!=self.reducer.identity['epoch'] and not snapshot or
            item['runtime_owner']!=self.token() or item['server']!=self.client.url or
            any(scope.get(k) not in (None,v) for k,v in expected_scope.items())):
            self.reducer.diagnostic('wrong auxiliary owner/server/epoch',event);done();return True
        if item['ended']:done();return True
        payload=event.get('payload',{})
        if payload.get('request_hash') not in (None,item['request_hash']):
            self.reducer.diagnostic('wrong auxiliary request',event);done();return True
        # event_ref is notification. Full receipt always comes from its original
        # bounded endpoint, including terminal events and replacement snapshots.
        if payload.get('state') in ('pending','awaiting_browser','delivered') and not payload.get('cancel_requested'):
            notice=(event['server_instance'],event['epoch'],event['seq'])
            row=self.pending.get(ident)
            if row is None or row.get('notice')!=notice:
                self.require(ident,'event',force=True)
                self.pending[ident]['notice']=notice
            done();return True
        self.require(ident,'event',event=event,done=done,force=True)
        return True

    def evidence_event(self, item, value):
        return dict(server_instance=self.reducer.identity['server_instance'],epoch=self.reducer.identity['epoch'],
            seq='aux:'+item['id']+':'+hashlib.sha256(protocol.encode(value)).hexdigest(),
            entity='native_operations:'+item['id'],kind='reconcile.aux',receipt_revision=0,payload=value)

    def accept(self, ident, value, *, event=None):
        item=self.store.read(ident)
        if item['ended']:return True
        identity=self.reducer.identity
        evidence=value.get('event_identity',{})
        if (item['runtime_owner']!=self.token() or item['server']!=self.client.url or
            evidence.get('server_instance')!=item['event_server'] or evidence.get('epoch')!=identity['epoch'] or
            value.get('id')!=ident or value.get('request_hash')!=item['request_hash'] or value.get('action')!=item['action']):
            raise ValueError('資料不一致')
        actual=value.get('target',{})
        if any(v and actual.get(k)!=v for k,v in item['target'].items()):raise ValueError('資料不一致')
        scope=value.get('scope',{})
        expected_scope=dict(library_id=item['library'],workspace_id=item['workspace'])
        if any(scope.get(k) not in (None,v) for k,v in expected_scope.items()):raise ValueError('資料不一致')
        state=value.get('state');proof=value.get('recovery',{})
        ended=state==SUCCESS[item['action']] or state=='failed' and not value.get('cancel_requested') or value.get('cancel_confirmed') is True
        if value.get('cancel_requested') and not value.get('cancel_confirmed') and state!=SUCCESS[item['action']]:ended=False
        missing=proof.get('location')=='missing' and proof.get('fenced') and value.get('missing_fence',{}).get('epoch')==identity['epoch']
        if missing and (item.get('cancel_requested') or item.get('http_rejected')):
            state='retired' if item.get('cancel_requested') else 'failed';ended=True
        if not ended:return False
        if value.get('cancel_confirmed'):state='cancelled'
        receipt=self.evidence_event(item,value)
        if self.reducer.store.contains(receipt):return True
        result=copy.deepcopy(value)
        with self.runner.accepting():
            self.reducer.store.checkpoint('before_aux_continuation')
            if state==SUCCESS[item['action']]:
                if item['action']=='inspect':
                    from .workflow_import import read_profile
                    inspection=dict(value['inspection'],format='prompt_studio_native_inspection',version=1)
                    spec=item['continuation'];path=item['target']['path']
                    result['profile']=read_profile(inspection,spec.get('name') or PurePosixPath(path).stem,
                        spec.get('workflow') or uuid.uuid5(uuid.NAMESPACE_URL,item['server']+'/workflows/'+path).hex,
                        dict(server=item['server'],path=path),modern=self.window.state.get('multi_output',{}).get('version',0)>=7)
                elif item['action']=='capture' and item['continuation'].get('enqueue',True):
                    result['queue_item']=self.client.queue.store.add(value['work'],ident)
            item=self.store.update(ident,ended=True,status=state,result=result,error=value.get('error',''),
                                   event_epoch=identity['epoch'],continuation_accepted=True)
            if item['action']=='apply':self.runner.accept_input_continuation(item)
            self.reducer.store.accept(receipt,'aux_continuation')
            if event is not None and not self.reducer.store.contains(event):self.reducer.store.accept(event,'aux_event')
            self.reducer.store.checkpoint('before_aux_commit')
            self.store._transactions.after_commit(lambda:self.completed(item))
        return True

    def completed(self, item):
        self.published(item)
        if item['action']=='apply':self.runner.input_accepted(item)
        success=item['status']==SUCCESS[item['action']]
        template=item['action']=='capture' and not item['continuation'].get('enqueue',True)
        if not template or not success or item['id'] in self.acked:
            self.notify_callback(item['id'],value=item['result'] if success else None,error=None if success else item.get('error') or '無法確認')
        self.runner.notify();self.runner.later()

    def notify_callback(self, ident, *, value=None, error=None):
        self.acked.discard(ident)
        done,failed=self.callbacks.pop(ident,(None,None))
        if error is not None:
            if failed:failed(error)
        elif done:done(value)

    def unconfirmed(self, ident):
        item=self.active.get(ident)
        if not item or item.get('error')=='無法確認':return
        value=dict(state='unconfirmed',reason='evidence_expired')
        with self.runner.accepting():
            item=self.store.update(ident,status='unconfirmed',error='無法確認')
            if item['action']=='apply':
                from .comfy_aux_inputs import unconfirmed
                unconfirmed(self.runner,item)
            event=self.evidence_event(item,value)
            if not self.reducer.store.contains(event):self.reducer.store.accept(event,'aux_unconfirmed')
            self.store._transactions.after_commit(lambda:self.published(item))
            self.store._transactions.after_commit(lambda:self.notify_callback(ident,error='無法確認'))
            self.store._transactions.after_commit(self.runner.notify)

    def cancel(self, ident):
        item=self.active.get(ident)
        if not item or item['server']!=self.client.url:return
        if ident in self.pending and self.pending[ident].get('cancelling') and not self.pending[ident]['expired']:return
        with self.runner.accepting():
            item=self.store.update(ident,cancel_requested=True)
            self.store._transactions.after_commit(lambda:self.published(item))
        self.require(ident,'cancel',force=True)
        self.pending[ident]['cancelling']=True
        generation=self.reducer.generation
        def received(reason):
            if generation!=self.reducer.generation:return
            self.require(ident,reason,force=True)
        self.client.request('workflow/native/cancel',dict(id=ident),
            done=lambda _:received('cancel_ack'),failed=lambda _:received('cancel_ack_lost'))

    def health(self, health):
        healthy=not health.get('needs_reconcile') and health.get('state')=='receiving'
        recovered=healthy and not self.healthy
        self.healthy=healthy
        if recovered:
            for ident in self.pending:self.require(ident,'recovered_connection',force=True)
        elif health.get('needs_reconcile') or health.get('state') in ('detected','unconfirmed'):
            for ident,item in self.active.items():
                if item['sent']:self.require(ident,'health')

    def heartbeat(self):
        # A heartbeat is health, not new evidence for every stuck operation.
        if not self.healthy:
            self.healthy=True
            for ident in self.pending:self.require(ident,'recovered_connection',force=True)
        self.schedule()

    @staticmethod
    def finish_waiters(row):
        callbacks,row['done']=row['done'],[]
        for done in callbacks:done()

    def close(self):
        self.timer.stop();self.pending.clear();self.callbacks.clear();self.acked.clear()
