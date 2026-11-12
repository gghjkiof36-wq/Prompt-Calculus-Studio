"""Owned native/frozen lifecycle acceptance, independent of desktop polling."""
import copy
import hashlib
import json
import sqlite3
import time
from . import comfy_event_protocol as p
from .comfy_event_store import EventStore
from .comfy_reconcile import Reconcile

TERMINAL = ('complete', 'failed', 'retired')


class EventReducer:
    def __init__(self, client):
        self.client, self.window = client, client.window
        self.store = EventStore(self.window.store.db)
        self.identity = None
        self.generation = 0
        self.enabled = False
        self.ready = False
        self.snapshot_ready = False
        self.channel_healthy = True
        self.reconcile = Reconcile(self)
        self.diagnostics = []
        self.probed = lambda: None
        self.queue_jobs = {}
        self.queue_active = set()
        self.queue_attempts = {}
        self.prompt_jobs = {}
        self.display_nodes = {}
        # Build once on owner construction. Mutations publish only after commit;
        # retained queue payloads are never decoded by an event/health callback.
        queue = client.queue.store
        for item in queue.rows(): self.queue_changed(item)
        queue.changed = self.queue_changed
        self.invariants = None
        if self.window.state.get('multi_output',{}).get('version',0)>=7:
            from .stage_invariants import InvariantChecker
            self.invariants = InvariantChecker(client)
            self.invariants.check(notify=False)

    @property
    def chain(self): return self.client.input_flow.chain

    def job(self, ident):
        row = self.client.generation.jobs.get(ident)
        if row: return row
        row = self.client.generation.record(ident)
        if row: return row
        key = self.queue_attempts.get(ident)
        if key:
            item = self.client.queue.store.read(key)
            return dict(id=ident, queue_job=item['id'], state=item['state'],
                    server=item['work']['scope']['server'], created=item['attempts'][-1]['created'],
                    **{key:item[key] for key in ('event_server','event_epoch') if key in item},
                    workspace=item['work']['scope'].get('workspace',self.window.state['workspace']),
                    event_library=item['work']['scope'].get('library',''),
                    capture=item['capture'], prompt_id=item.get('prompt_id',''), payload=item.get('payload',{}))

    def queue_changed(self, item):
        old = self.queue_jobs.pop(item['id'], None)
        if old:
            self.queue_attempts.pop(old, None)
            self.queue_active.discard(old)
        ident = item.get('attempt')
        if ident:
            self.queue_jobs[item['id']] = ident
            self.queue_attempts[ident] = item['id']
            if item['state'] in ('submitting','queued','running','unconfirmed','results_pending'):
                self.queue_active.add(ident)
            if item.get('prompt_id'): self.prompt_jobs[item['prompt_id']] = ident

    def active(self):
        values = [v for v in self.client.generation.jobs.values() if v.get('native_operation') and v['server']==self.client.url]
        for ident in self.queue_active:
            job = self.job(ident)
            if job and job['server']==self.client.url: values.append(job)
        return values

    def running_prompts(self):
        return [j['prompt_id'] for j in self.active() if j['state']=='running' and j.get('prompt_id')]

    def display(self, items):
        # The existing details dialog reads record() every two seconds. It has
        # no progress-bar subscriber; no Canvas/stateChanged broadcast needed.
        for item in items:
            ident = self.prompt_jobs.get(item['prompt_id'])
            job = self.client.generation.jobs.get(ident)
            if ((job and job['state']=='running') or ident in self.queue_active) and item.get('node') is not None:
                node = str(item['node'])
                previous = self.display_nodes.get(ident,{})
                started = previous.get('node_started',time.time()) if node==previous.get('node') else time.time()
                if job and node != job.get('node'):
                    job.update(node=node, node_started=time.time())
                self.display_nodes[ident] = dict(node=node,node_started=started,last_seen=time.time())

    def prompt_job(self, prompt):
        ident = self.prompt_jobs.get(prompt)
        if ident: return ident
        # Only active native owners, never the retained document/queue rows.
        for job in self.client.generation.jobs.values():
            if job.get('prompt_id')==prompt:
                self.prompt_jobs[prompt]=job['id'];return job['id']

    def bind(self, identity):
        if self.identity == identity and self.enabled: return
        self.generation += 1
        self.reconcile.close()
        self.enabled = True
        self.ready = False
        self.snapshot_ready = False
        self.channel_healthy = True
        self.identity = copy.deepcopy(identity)
        self.prompt_jobs.clear()
        self.display_nodes.clear()
        for job in self.active():
            if job.get('prompt_id'): self.prompt_jobs[job['prompt_id']]=job['id']
            if identity and job.get('event_server') not in (None,identity['server_instance']):
                self.diagnostic('server replaced; original owner retained')
            self.reconcile.require(job['id'], 'startup')

    def diagnostic(self, reason, event=None):
        value = dict(reason=reason, entity=(event or {}).get('entity',''))
        if value not in self.diagnostics: self.diagnostics.append(value)
        del self.diagnostics[:-100]

    def event_for(self, job, value):
        identity = self.identity or dict(server_instance='legacy:'+self.client.url, epoch=str(self.client.epoch))
        digest = hashlib.sha256(p.encode(value)).hexdigest()
        return dict(schema=p.SCHEMA, server_instance=identity['server_instance'], epoch=identity['epoch'],
                    seq='reconcile:'+job['id']+':'+digest, entity=('queue_attempts:' if job.get('queue_job') else 'native_operations:')+job['id'],
                    kind='reconcile.receipt', scope=p.scope(), receipt_revision=0, payload=value)

    def receive(self, event, done, *, snapshot=False):
        if self.client.stopped: return
        if not self.identity or event['server_instance'] != self.identity['server_instance'] or (not snapshot and event['epoch'] != self.identity['epoch']):
            self.diagnostic('wrong server/epoch', event); done(); return
        if event['kind']=='native.progress': done(); return
        if event['kind']==p.LOG_GAP:
            self.health(dict(needs_reconcile=True, log_consistent=False))
            done()
            return
        entity = event.get('entity','')
        ident = entity.split(':',1)[1] if entity.startswith(('native_operations:','queue_attempts:')) else None
        if ident is None and event.get('prompt_id'):
            ident = self.prompt_job(event['prompt_id'])
        job = self.job(ident) if ident else None
        if not job or job['server'] != self.client.url:
            self.diagnostic('unowned event',event); done(); return
        owner = event.get('scope',{})
        if owner.get('workspace_id') and owner['workspace_id'] != job.get('workspace', self.window.state['workspace']):
            self.diagnostic('wrong owner',event); done(); return
        expected_library = job.get('event_library')
        if expected_library and owner.get('library_id') and expected_library != owner['library_id']:
            self.diagnostic('wrong library',event); done(); return
        if job.get('event_server') and job['event_server'] != event['server_instance']:
            self.diagnostic('server replaced; original owner retained',event); done(); return
        if event['kind'] in ('native.execution_start','native.executing','native.executed','native.execution_cached') and job['state']=='running':
            if event['kind']=='native.executing':
                self.display([dict(prompt_id=job.get('prompt_id'),node=event.get('payload',{}).get('node'))])
            done(); return
        if self.store.contains(event): done(); return
        previous = job.get('event_revisions',{}).get(entity,0)
        digest=hashlib.sha256(p.encode(event.get('payload',{}))).hexdigest()
        if event.get('receipt_revision')==previous and previous and job.get('event_digests',{}).get(entity) not in (None,digest):
            self.diagnostic('conflicting receipt revision',event)
            self.reconcile.require(ident,'conflicting_revision',done=done,force=True)
            return
        if event.get('receipt_revision',0) and event['receipt_revision'] <= previous:
            done(); return
        value = copy.deepcopy(event.get('payload',{}))
        if event.get('prompt_id'): value['prompt_id']=event['prompt_id']
        kind = event['kind']
        if job['state'] in TERMINAL:
            if job['state']!='retired' and value.get('fetch_required') and kind=='operation.receipt_changed':
                self.reconcile.require(ident,'parameters',event=event,done=done,force=True)
            else:
                self.accept_evidence(ident,value,event,None,done,
                    lambda error:self.reconcile.require(ident,'parameters',event=event,done=done))
            return
        if kind in ('native.execution_success','native.execution_error','native.execution_interrupted','operation.terminal') or value.get('fetch_required'):
            self.reconcile.require(ident,'terminal',event=event,done=done,force=True); return
        if kind=='operation.queued' and not job.get('payload'):
            self.reconcile.require(ident,'queued',event=event,done=done,force=True); return
        if kind in ('native.execution_start','native.executing','native.execution_cached','native.executed'):
            value['state']='running'
            if 'node' in value: value['node']=str(value['node'])
        if kind=='operation.missing' or value.get('missing_fence'):
            fence=value.get('missing_fence',{})
            if not isinstance(fence,dict) or fence.get('epoch')!=event['epoch'] or not fence.get('checked'):
                self.diagnostic('missing without fence',event)
                self.reconcile.require(ident,'missing_without_fence')
                done();return
            value['recovery']=dict(location='missing',fenced=True,can_abandon=True)
            self.remember_proof(ident,value)
            self.reconcile.require(ident,'missing')
            # Missing is evidence of absence, never a terminal without D1.
        self.accept_evidence(ident,value,event,None,done,lambda error:self.reconcile.require(ident,'acceptance',event=event,done=done))

    def accept_evidence(self, ident, value, event, history, done, failed):
        job = self.job(ident)
        if not job: done(); return
        event = event or self.event_for(job, value)
        if self.store.contains(event): done(); return
        if job['state'] in TERMINAL:
            if job['state']!='retired' and value.get('parameter_receipt') and value['parameter_receipt']!=job.get('parameter_receipt') and value.get('prompt_id',job.get('prompt_id'))==job.get('prompt_id'):
                candidate=copy.deepcopy(job)
                candidate['parameter_receipt']=copy.deepcopy(value['parameter_receipt'])
                entity=event.get('entity','')
                candidate.setdefault('event_revisions',{})[entity]=event.get('receipt_revision',0)
                candidate.setdefault('event_digests',{})[entity]=hashlib.sha256(p.encode(event.get('payload',{}))).hexdigest()
                try:
                    with self.chain.accepting():
                        self.write(candidate,event,'parameters')
                        self.store.checkpoint('before_accept_commit')
                        self.chain.store._transactions.after_commit(done)
                except (sqlite3.Error,OSError,ValueError) as exc:failed(exc)
                return
            done(); return
        candidate = copy.deepcopy(job)
        for key in ('payload','prompt_id','parameter_receipt','diagnostics','first_error','recovery','terminal_action','node','results'):
            if key in value: candidate[key]=copy.deepcopy(value[key])
        if candidate.get('prompt_id') and job.get('prompt_id') and candidate['prompt_id']!=job['prompt_id']:
            self.diagnostic('conflicting prompt',event); failed('Conflicting prompt receipt'); return
        if history is not None:
            status = history.get('status',{})
            names = {row[0] for row in status.get('messages',[]) if isinstance(row,(tuple,list)) and row}
            failed_execution = status.get('status_str')=='error' or bool(names & {'execution_error','execution_interrupted'})
            candidate.update(state='failed' if failed_execution else 'complete', outputs=copy.deepcopy(history.get('outputs',{})),
                             events=copy.deepcopy(status.get('messages',[])), finished=time.time(), error=value.get('error',''))
            if 'execution_interrupted' in names: candidate['terminal_action']='cancel'
            if candidate.get('typed_inputs') and not failed_execution:
                from .queued_work import result_state
                graph=candidate.get('payload',{}).get('prompt',{})
                original=history.get('prompt',[])
                executed=original[4] if len(original)>4 and isinstance(original[4],list) else list(graph)
                targets=sorted(set(candidate.get('output_nodes',[]))|{str(k) for k in executed if graph.get(str(k),{}).get('class_type') in ('SaveImage','PreviewImage')})
                checked,reason=result_state(dict(graph=graph,outputs=targets,input_types=candidate.get('payload',{}).get('input_types')),candidate['prompt_id'],history)
                if checked not in ('complete','failed'): failed(reason); return
                candidate.update(state=checked,error=reason,input_check='matched' if checked=='complete' else 'mismatch')
        elif value.get('abandoned') or value.get('state')=='abandoned':
            proof=value.get('recovery',candidate.get('recovery',{}))
            if proof.get('location')!='missing' or not proof.get('fenced'):
                failed('Retirement requires fenced missing'); return
            candidate.update(state='retired',terminal_action='abandon',tombstone=True,finished=time.time())
        elif value.get('cancel_confirmed'):
            candidate.update(state='failed',terminal_action='cancel',cancel_confirmed=True,error='已取消',finished=time.time())
        elif value.get('state')=='failed':
            candidate.update(state='failed',error=value.get('error',''),finished=time.time())
        elif value.get('state') in ('queued','running') and candidate.get('prompt_id'):
            candidate.update(state='running' if candidate['state']=='running' and value['state']=='queued' else value['state'],error='')
        # Receipt identity/revision alone is not a document change. Replaying
        # an unchanged observation is harmless and must not grow the journal.
        if candidate==job: done(); return
        # A legacy endpoint cannot attest a persistent server identity. Its
        # synthetic receipt key must not become an ownership fence on upgrade.
        if self.identity:candidate['event_server']=event['server_instance']
        candidate['event_epoch']=self.identity['epoch'] if self.identity else event['epoch']
        entity=event.get('entity','');revision=event.get('receipt_revision',0)
        if revision>=candidate.get('event_revisions',{}).get(entity,0):
            candidate.setdefault('event_revisions',{})[entity]=revision
            candidate.setdefault('event_digests',{})[entity]=hashlib.sha256(p.encode(event.get('payload',{}))).hexdigest()
        generation=self.generation
        def accept_result():
            if generation!=self.generation or self.client.stopped:
                raise ValueError('Stale result delivery')
            current=self.job(ident)
            if current is None or current['state'] in TERMINAL:
                raise ValueError('Operation already terminal')
            self.write(candidate,event)
        try:
            attempt=self.chain.store.read(ident)
            if candidate['state']=='complete' and attempt and attempt.get('kind')=='attempt':
                self.chain.collect(attempt,candidate,accept=accept_result,
                    committed=lambda:self.published(candidate,done),on_error=failed,
                    before_commit=lambda:self.store.checkpoint('before_frame_commit'))
            else: self.commit(candidate,event,done)
        except (sqlite3.Error,OSError,ValueError) as exc: failed(exc)

    def write(self, job, event, disposition='accepted'):
        self.store.checkpoint('before_job')
        self.client.generation._write_job(job)
        attempt=self.chain.store.read(job['id'])
        if attempt and attempt.get('kind')=='attempt':
            self.chain.store.record_parameters(attempt,job)
            if job.get('payload',{}).get('prompt') and job.get('prompt_id'):
                self.chain.store.patch(attempt['id'],operation_evidence=job,local_issue='')
        self.store.accept(event,'retired' if job['state']=='retired' else disposition)

    def commit(self, job, event, done, *, disposition='accepted'):
        if disposition!='accepted':
            with self.chain.accepting():
                self.store.accept(event,disposition)
                self.chain.store._transactions.after_commit(done)
            return
        with self.chain.accepting():
            self.write(job,event,disposition)
            attempt=self.chain.store.read(job['id'])
            if disposition=='accepted' and attempt and attempt.get('kind')=='attempt':
                if job['state'] in TERMINAL:
                    if attempt.get('cancel_requested'):
                        from .stage_queue_actions import finish_cancel
                        finish_cancel(self.chain,attempt)
                    else:
                        self.chain.store.patch(job['id'],status='cancelled' if job['state']=='retired' else job['state'],error=job.get('error',''))
                        run=self.chain.store.read(attempt['owner'])
                        if job['state']!='complete' and run and run.get('pause_reason') not in ('manual','restart'):
                            self.chain.pause(run['id'],'無法確認' if job['state']=='retired' else job.get('error',''),reason='error')
                elif job['state'] in ('queued','running'):
                    self.chain.store.patch(job['id'],status='submitted',prompt_id=job.get('prompt_id'),error='')
                elif job['state']=='unconfirmed':
                    self.chain.store.patch(job['id'],status='unconfirmed',error=job.get('error',''))
            if job.get('queue_job'):
                self.client.queue.store.update(job['queue_job'],**{k:job[k] for k in ('state','prompt_id','payload','outputs','results','error') if k in job})
            if self.invariants and job['state'] in TERMINAL:
                self.invariants.check(job['id'])
            self.store.checkpoint('before_accept_commit')
            self.chain.store._transactions.after_commit(lambda:self.published(job,done))

    def published(self, job, done):
        generation=self.client.generation
        original=generation.jobs.get(job['id'])
        if original is not None: original.clear(); original.update(copy.deepcopy(job))
        if job['state'] in TERMINAL:
            generation.jobs.pop(job['id'],None)
        elif not job.get('queue_job'): generation.jobs[job['id']]=copy.deepcopy(job)
        if job.get('prompt_id') or job['state'] in TERMINAL:
            if job.get('prompt_id'): self.prompt_jobs[job['prompt_id']]=job['id']
            generation.native_waiting.discard(job['id'])
            if self.chain.preparing==job['id']: self.chain.preparing=None
        generation._set_status('無法確認' if any(v.get('state')=='unconfirmed' and v.get('error')=='無法確認' for v in generation.jobs.values()) else '')
        self.chain.notify(); self.chain.later()
        if job.get('queue_job'):
            if job['state'] in ('failed','unconfirmed','retired'):self.client.queue.dispatching=False
            self.client.queue.notify(job.get('error',''))
            if job['state']=='complete':self.client.queue.pump()
        done()

    def remember_proof(self, ident, value):
        job=self.job(ident)
        if not job or job['state'] in TERMINAL: return
        proof=value.get('recovery')
        if not proof or proof==job.get('recovery'): return
        job=copy.deepcopy(job)
        job['recovery']=copy.deepcopy(proof)
        self.commit(job,self.event_for(job,dict(recovery=proof)),lambda:None)

    def unconfirmed(self, ident):
        job=self.job(ident)
        if not job or job['state'] in TERMINAL or job['state']=='unconfirmed' and job.get('error')=='無法確認': return
        job=copy.deepcopy(job)
        job.update(state='unconfirmed',error='無法確認')
        self.commit(job,self.event_for(job,dict(evidence='expired',state='unconfirmed')),lambda:None)

    def health(self, health):
        if not self.enabled: return
        self.channel_healthy=bool(not health.get('needs_reconcile') and health.get('log_consistent',True) and health.get('state')=='receiving')
        self.ready=bool(self.snapshot_ready and not self.reconcile.pending and self.channel_healthy)
        running=health.get('running')
        for job in self.active():
            if self.identity and job.get('event_server') not in (None,self.identity['server_instance']):continue
            mismatch=running is not None and job['state']=='running' and job.get('prompt_id') not in running
            if health.get('needs_reconcile') or health.get('state') in ('detected','unconfirmed') or mismatch:
                self.reconcile.require(job['id'],'health')
                if health.get('state')=='unconfirmed':
                    try:self.unconfirmed(job['id'])
                    except (sqlite3.Error,OSError) as exc:self.diagnostic('unconfirmed receipt pending: '+str(exc))

    def heartbeat(self):
        # A fresh valid heartbeat permits one new bounded evidence window.
        # A long-running, positively queued/running prompt has no expiry timer.
        for ident,row in list(self.reconcile.pending.items()):
            if row['expired']:
                self.reconcile.require(ident,'recovered_connection',force=True)

    def close(self):
        self.generation+=1
        self.reconcile.close()
