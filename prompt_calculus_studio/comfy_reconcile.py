"""Bounded, operation-scoped evidence collection; never submits or interrupts."""
import time
import sqlite3
from urllib.parse import quote
from PySide6.QtCore import QTimer


class Reconcile:
    def __init__(self, reducer, *, clock=time.monotonic):
        self.reducer, self.client, self.clock = reducer, reducer.client, clock
        self.pending = {}
        self.timer = QTimer(self.client)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)

    def require(self, ident, reason, *, event=None, done=None, force=False):
        if getattr(self.reducer,'invariants',None):
            self.reducer.invariants.check(ident)
        job=self.reducer.job(ident)
        if job and (job['state']=='retired' or job['state'] in ('complete','failed') and reason!='parameters'):
            if done:done()
            return
        if ident not in self.pending:
            self.pending[ident] = dict(start=self.clock(), at=self.clock(), tries=0, busy=False,
                                      reason=reason, event=event, done=done, expired=False)
        else:
            row = self.pending[ident]
            if event is not None: row.update(event=event, done=done)
            if force: row.update(at=self.clock(), start=self.clock(), expired=False)
        self.timer.start(0)

    def resolved(self, ident):
        self.pending.pop(ident, None)

    def tick(self):
        if self.client.stopped: return
        now = self.clock()
        for ident, row in list(self.pending.items()):
            if now - row['start'] >= 30 and not row['expired'] and now>=row.get('local_retry',0):
                try:self.reducer.unconfirmed(ident)
                except (sqlite3.Error,OSError) as exc:
                    row.update(local_retry=now+1,error=str(exc))
                else:row['expired'] = True
            if now - row['start'] < 30 and not row['busy'] and not row['expired'] and now >= row['at']:
                self.query(ident, row)
        active = [r for r in self.pending.values() if not r['expired']]
        if active: self.timer.start(250)

    def query(self, ident, row):
        job = self.reducer.job(ident)
        if not job:
            self.resolved(ident); return
        if job['server'] != self.client.url or (self.reducer.identity and job.get('event_server') not in (None,self.reducer.identity['server_instance'])):
            self.reducer.diagnostic('original server unavailable')
            row.update(probed=True,at=row['start']+30)
            self.reducer.probed()
            return
        row['busy'] = True
        row['tries'] += 1
        generation = self.reducer.generation
        def current():
            return not self.client.stopped and generation == self.reducer.generation and self.pending.get(ident) is row
        def failed(error):
            if not current(): return
            row.update(busy=False, at=self.clock()+min(8,2**min(row['tries']-1,3)), error=str(error))
            row['probed']=True
            self.reducer.probed()
            if job['state'] in ('complete','failed'):
                self.reducer.accept_evidence(ident,value,row.get('event'),None,
                    lambda:self.finish(ident,row),failed)
                return
            self.timer.start(250)
        def accepted(value):
            if not current(): return
            if value.get('id') not in (None,ident):failed('Wrong operation receipt');return
            if job.get('queue_job'):
                if value.get('capture') not in (None,job.get('capture')):failed('Wrong frozen capture');return
                if 'recovery' not in value and value.get('state') in ('submitting','unconfirmed'):
                    self.client.request('workflow/queue/recovery',dict(attempt=ident),
                        done=lambda proof:accepted(dict(value,**proof)),failed=failed,
                        deadline=time.monotonic()+min(12,max(.1,30-(self.clock()-row['start']))))
                    return
            row['probed']=True
            self.reducer.probed()
            proof = value.get('recovery', {})
            if proof.get('location') in ('running','queued'):
                value=dict(value,state=proof['location'])
            if value.get('prompt_id'):
                job.update(prompt_id=value['prompt_id'])
            terminal = row.get('event', {}).get('kind','') if row.get('event') else ''
            if value.get('state')=='results_pending':failed(value.get('error','Result files unavailable'));return
            if proof.get('location') == 'history' or terminal in ('native.execution_success','native.execution_error','native.execution_interrupted','operation.terminal') or value.get('state') == 'complete':
                prompt = job.get('prompt_id')
                if not prompt: failed('Missing terminal prompt identity'); return
                def history(result):
                    if not current(): return
                    entry = result.get(prompt)
                    if not entry or not (entry.get('status',{}).get('completed') or entry.get('status',{}).get('status_str')=='error'):
                        failed('History is not terminal'); return
                    self.reducer.accept_evidence(ident, value, row.get('event'), entry,
                        lambda: self.finish(ident, row), failed)
                self.client.request('/history/'+quote(prompt,safe=''), done=history, failed=failed,
                                    deadline=time.monotonic()+min(12,max(.1,30-(self.clock()-row['start']))))
                return
            if proof.get('location') in ('missing','unavailable','submitting') and not value.get('cancel_confirmed'):
                self.reducer.remember_proof(ident, value)
                failed(proof.get('location')); return
            if value.get('state') not in ('queued','running','failed','abandoned') and not value.get('cancel_confirmed'):
                failed('Operation has no conclusive receipt'); return
            self.reducer.accept_evidence(ident, value, row.get('event'), None,
                lambda: self.finish(ident, row), failed)
        queue = bool(job.get('queue_job'))
        route = 'workflow/queue/status' if queue else 'workflow/native/recovery'
        data = dict(attempt=ident) if queue else dict(id=ident)
        if not queue and job.get('event_library'):
            from .comfy_event_protocol import scope
            data.update(proof_scope=dict(scope(),library_id=job['event_library'],workspace_id=job['workspace']),
                        expected_prompt=job.get('prompt_id',''))
        self.client.request(route, data, done=accepted, failed=failed,
                            deadline=time.monotonic()+min(12,max(.1,30-(self.clock()-row['start']))))

    def finish(self, ident, row):
        if self.pending.get(ident) is not row: return
        self.pending.pop(ident)
        self.reducer.probed()
        if not self.pending and self.reducer.snapshot_ready and self.reducer.channel_healthy:self.reducer.ready=True
        if row.get('done'): row['done']()

    def close(self):
        self.timer.stop()
        self.pending.clear()
