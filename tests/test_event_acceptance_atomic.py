"""Channel acceptance through real Workspace controls and actual native receipts."""
import copy
import json
import sqlite3
import gc
import sys
import traceback
import threading
from types import SimpleNamespace,MethodType
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import test_workspace_execution as fixture
from channel_fixture import Clock
from prompt_calculus_studio.comfy_event_protocol import scope, scope_digest


class AcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixture.WorkspaceExecutionTests.setUpClass()

    def setUp(self):
        self.f=fixture.WorkspaceExecutionTests('runTest');self.f.setUp()
        sys.excepthook=lambda kind,value,tb:self.f.errors.append(''.join(traceback.format_exception(kind,value,tb)))
        def cleanup():
            for client in self.f.w.scheduler.clients.values():client.events.store.fault=None
            self.f.tick()
            self.f.tearDown()
        self.addCleanup(cleanup)
        self.clock=Clock();self.lost_cancel=False;self.history_error=False;self.lost_submit=False
        self.calls=[]
        self.peer=self.f.peer
        log=self.peer.service.events
        self.identity=dict(server_instance=log.server_instance,epoch=log.epoch)
        self.since='0'
        old=self.f.transport_request
        def request(client,route,data=None,done=None,failed=None,raw=None,content_type='application/json',**options):
            self.calls.append(route)
            if route in ('workflow/native/recovery','workflow/native/cancel','workflow/native/abandon'):
                entries=[(0,p['prompt_id'],p['prompt'],p['extra_data']) for p in self.peer.pending.values()]
                queue=SimpleNamespace(mutex=threading.RLock(),currently_running={},queue=entries,
                    get_history=lambda prompt_id=None:{k:v for k,v in self.peer.history.items() if prompt_id is None or k==prompt_id})
                try:
                    if route.endswith('/cancel'):
                        result=self.peer.native.cancel(data['id'],queue,lambda:None)
                        remaining={r[1] for r in queue.queue}
                        for prompt in list(self.peer.pending):
                            if prompt not in remaining:self.peer.pending.pop(prompt)
                        if self.lost_cancel:return
                    elif route.endswith('/abandon'):result=self.peer.native.abandon(data['id'],queue)
                    else:
                        try:self.peer.native.reconcile(data['id'],entries)
                        except ValueError:pass
                        result=self.peer.native.recovery(data['id'],queue,proof_scope=data.get('proof_scope'),expected_prompt=data.get('expected_prompt',''))
                    if done:done(result)
                except Exception as exc:
                    if failed:failed(exc)
                    else:raise
                return
            if route.startswith('/history/') and self.history_error:
                if failed:failed('HTTP 500')
                return
            return old(client,route,data,None if self.lost_submit and route=='workflow/native/start' else done,failed,raw,content_type)
        self.f.transport_request=request
        self.bind()

    def bind(self):
        for client in self.f.w.scheduler.clients.values():
            client.events.bind(self.identity);client.events.ready=client.events.snapshot_ready=True
            client.events.reconcile.clock=self.clock

    @property
    def client(self):return self.restored_client if hasattr(self,'restored_client') else self.f.a.host.comfy

    def drain(self):
        log=self.peer.service.events
        page=log.events(self.since,0,(log.server_instance,log.epoch),scope(),scope_digest())
        self.since=page['covered_to']
        for event in page['events']:
            for client in self.f.w.scheduler.clients.values():client.events.receive(event,lambda:None)
        self.f.tick()
        return page['events']

    def start(self):
        self.f.click(self.f.a);self.drain()
        self.assertEqual(len(self.peer.submissions),1,self.f.notices)
        job=next(iter(self.client.generation.jobs.values()))
        self.assertTrue(job.get('prompt_id'),job)
        return job['id'],job['prompt_id']

    def terminal(self,prompt):
        log=self.peer.service.events
        with log.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            event=log.append(db,'native.execution_success',dict(prompt_id=prompt),entity='execution:'+prompt,prompt_id=prompt)
        log.committed();return event

    def test_terminal_collection_and_duplicate_replay_are_one_acceptance(self):
        ident,prompt=self.start();self.peer.finish()
        event=self.terminal(prompt);self.drain()
        record=self.client.generation.record(ident)
        self.assertEqual(record['state'],'complete',record)
        attempt=self.client.input_flow.chain.store.read(ident)
        self.assertEqual(attempt['status'],'complete')
        before=copy.deepcopy(attempt['result'])
        for _ in range(2):self.client.events.receive(event,lambda:None)
        self.assertEqual(self.client.input_flow.chain.store.read(ident)['result'],before)
        self.assertEqual(len(self.peer.submissions),1)

    def check_acceptance_failure_recovery(self,point):
        ident,prompt=self.start();self.peer.finish();event=self.terminal(prompt)
        db=self.client.window.store.db
        before=list(db.execute('SELECT body FROM generation_jobs WHERE id=?',(ident,)))
        attempt=copy.deepcopy(self.client.input_flow.chain.store.read(ident))
        self.client.events.store.fault=lambda name: (_ for _ in ()).throw(sqlite3.OperationalError(point)) if name==point else None
        self.client.events.receive(event,lambda:None);self.client.events.reconcile.tick()
        self.assertEqual(list(db.execute('SELECT body FROM generation_jobs WHERE id=?',(ident,))),before)
        self.assertEqual(self.client.input_flow.chain.store.read(ident),attempt)
        self.assertFalse(self.client.events.store.contains(event))
        self.client.events.store.fault=None
        self.history_error=True
        for _ in range(2):
            self.reopen()
            self.assertNotEqual(self.client.input_flow.chain.store.read(ident)['status'],'complete')
            self.assertFalse(self.client.events.store.contains(event))
        self.history_error=False
        self.client.events.reconcile.close()
        self.client.events.receive(event,lambda:None);self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'complete')
        result=copy.deepcopy(self.client.input_flow.chain.store.read(ident)['result'])
        run=copy.deepcopy(self.client.input_flow.chain.store.read(attempt['owner']))
        for _ in range(2):self.client.events.receive(event,lambda:None)
        self.assertEqual(self.client.input_flow.chain.store.read(ident)['result'],result)
        self.assertEqual(self.client.input_flow.chain.store.read(attempt['owner']),run)
        self.assertEqual(len(self.peer.submissions),1)

    def test_before_job_failure_reopen_twice_replay_twice(self):
        self.check_acceptance_failure_recovery('before_job')

    def test_before_receipt_failure_reopen_twice_replay_twice(self):
        self.check_acceptance_failure_recovery('before_receipt')

    def test_after_receipt_failure_reopen_twice_replay_twice(self):
        self.check_acceptance_failure_recovery('after_receipt')

    def test_before_frame_failure_reopen_twice_replay_twice(self):
        self.check_acceptance_failure_recovery('before_frame_commit')

    def test_cancel_ack_lost_through_cross_button_keeps_manual_pause(self):
        ident,prompt=self.start();self.lost_cancel=True
        controls=self.f.a.host.canvas.execution_bar.controls
        controls.refresh()
        QTest.mouseClick(controls.stop,Qt.MouseButton.LeftButton)
        self.f.tick();self.drain()
        attempt=self.client.input_flow.chain.store.read(ident)
        self.assertFalse(attempt.get('cancel_requested'),attempt)
        self.assertEqual(self.client.generation.record(ident)['terminal_action'],'cancel')
        run=self.client.input_flow.chain.store.read(attempt['owner'])
        self.assertEqual(run['pause_reason'],'manual')
        self.assertEqual(len(self.peer.submissions),1)

    def test_history_500_becomes_unconfirmed_and_recovers_same_prompt(self):
        ident,prompt=self.start();self.peer.finish();self.history_error=True
        self.terminal(prompt);self.drain()
        for second in range(31):
            self.clock.now+=1;self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'unconfirmed')
        self.assertEqual(self.client.generation.record(ident)['error'],'無法確認')
        self.history_error=False
        self.client.generation.recheck(ident);self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'complete')
        self.assertEqual(len(self.peer.submissions),1)

    def test_wrong_server_owner_epoch_cannot_change_job(self):
        ident,prompt=self.start();event=self.terminal(prompt)
        before=self.client.generation.record(ident)
        for key,value in (('server_instance','foreign'),('epoch','stale'),('scope',dict(scope(),workspace_id='other'))):
            bad=copy.deepcopy(event);bad[key]=value
            self.client.events.receive(bad,lambda:None)
            self.assertEqual(self.client.generation.record(ident),before)
        self.assertEqual(len(self.peer.submissions),1)

    def test_fenced_missing_retires_locally_and_late_terminal_cannot_revive(self):
        ident,prompt=self.start()
        self.client.events.remember_proof(ident,dict(recovery=dict(location='unavailable',can_abandon=False)))
        self.client.generation.abandon(ident)
        self.assertNotEqual(self.client.generation.record(ident)['state'],'retired')
        self.peer.pending.clear()
        self.client.generation.recheck(ident);self.client.events.reconcile.tick()
        proof=self.client.generation.record(ident)['recovery']
        self.assertEqual(proof['location'],'missing');self.assertTrue(proof['fenced'])
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'],'retired')
        event=self.terminal(prompt)
        self.client.events.receive(event,lambda:None)
        self.assertEqual(self.client.generation.record(ident)['state'],'retired')
        self.assertEqual(len(self.peer.submissions),1)

    def test_restart_missing_operation_fences_late_submit_and_never_resends(self):
        ident,prompt=self.start();self.peer.pending.clear()
        with self.peer.service.connect() as db:db.execute('DELETE FROM native_operations WHERE id=?',(ident,))
        self.client.events.bind(dict(self.identity,epoch='new-connection'))
        self.client.events.reconcile.tick()
        proof=self.client.generation.record(ident).get('recovery',{})
        self.assertTrue(proof.get('fenced'),proof)
        self.clock.now=31;self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'unconfirmed')
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'],'retired')
        self.assertEqual(len(self.peer.submissions),1)

    def schedule(self,count):
        from prompt_calculus_studio.flow_data import add_scheduler
        from prompt_calculus_studio import multi_output as model
        def configure(state):
            key=add_scheduler(state)
            model.connect(state,next(iter(state['multi_output']['stages'])),key+'::flow','flow')
        self.assertTrue(self.f.a.host.canvas.commit(configure))
        self.f.a.host.canvas.execution_bar.controls.count.setValue(count)

    def finish_all(self,limit=10):
        for _ in range(limit):
            if not self.peer.pending:self.f.tick()
            if not self.peer.pending:return
            prompt=next(iter(self.peer.pending));self.peer.finish(ident=prompt);self.terminal(prompt);self.drain()

    def test_s9a_three_items_cross_resume_and_other_document_lane(self):
        self.schedule(3);ident,prompt=self.start();self.lost_cancel=True
        controls=self.f.a.host.canvas.execution_bar.controls
        controls.refresh();QTest.mouseClick(controls.stop,Qt.MouseButton.LeftButton);self.drain()
        self.assertFalse(self.client.input_flow.chain.store.read(ident).get('cancel_requested'))
        self.f.click(self.f.b);self.drain();self.finish_all()
        self.f.click(self.f.a);self.drain();self.finish_all()
        self.assertEqual(len(self.peer.submissions),4)
        self.assertEqual(sum(p['prompt_id']==prompt for p in self.peer.submissions),1)
        self.assertTrue(all(r['status']=='complete' for r in self.client.input_flow.chain.runs()))

    def reopen(self):
        token=self.client.window.runtime_token
        # Restore the durable execution owner without reopening its Canvas.
        with patch.object(self.f,'tick',lambda:None):self.f.reopen(restore_tabs=False)
        self.restored_client=self.f.w.scheduler.clients[token]
        self.bind()
        for client in self.f.w.scheduler.clients.values():client.events.reconcile.tick()
        gc.collect();self.f.tick()

    def test_s2_lost_start_ack_restart_twice_replay_twice(self):
        self.lost_submit=True
        ident,prompt=self.start()
        for _ in range(2):
            self.reopen()
            self.assertEqual(self.client.generation.record(ident)['prompt_id'],prompt)
            run=self.client.input_flow.chain.store.read(self.client.input_flow.chain.store.read(ident)['owner'])
            self.assertEqual(run['pause_reason'],'restart')
        self.peer.finish();event=self.terminal(prompt);self.drain()
        saved=copy.deepcopy(self.client.input_flow.chain.store.read(ident)['result'])
        for _ in range(2):self.client.events.receive(event,lambda:None)
        self.assertEqual(self.client.input_flow.chain.store.read(ident)['result'],saved)
        self.assertEqual(len(self.peer.submissions),1)

    def test_terminal_before_queued_and_late_ack_parameters_are_idempotent(self):
        self.peer.live['parameters_protocol']=1
        from stage_parameter_fixture import intention
        def parameters(state):
            profile=state['generation']['profiles'][0]
            stage=next(iter(state['multi_output']['stages'].values()))
            stage['parameters']=intention(profile,node='5',field='batch_size',value=1)
        self.assertTrue(self.f.a.host.canvas.commit(parameters))
        self.lost_submit=True
        self.f.click(self.f.a)
        self.assertTrue(self.peer.pending,self.f.notices)
        prompt=next(iter(self.peer.pending));ident=next(iter(self.client.generation.jobs))
        self.peer.finish();event=self.terminal(prompt)
        self.client.events.receive(event,lambda:None);self.client.events.reconcile.tick();self.f.tick()
        self.drain()  # older submit and queued arrive after terminal acceptance
        job=self.client.generation.record(ident)
        self.assertEqual(job['state'],'complete')
        self.assertTrue(job.get('parameter_receipt'))
        self.assertEqual(self.client.input_flow.chain.store.read(ident)['status'],'complete')
        self.assertEqual(len(self.peer.submissions),1)

    def test_gap_inconsistent_heartbeat_and_new_epoch_probe_original_only(self):
        ident,prompt=self.start()
        for health in (dict(needs_reconcile=True),dict(log_consistent=False,needs_reconcile=True),dict(state='detected'),dict(running=[],state='receiving')):
            self.client.events.health(health);self.client.events.reconcile.tick()
        self.client.events.bind(dict(self.identity,epoch='new'))
        self.assertFalse(self.client.dispatch_allowed)
        self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['prompt_id'],prompt)
        self.assertEqual(len(self.peer.submissions),1)
        self.assertNotIn('desktop/interrupt',self.calls)

    def test_old_extension_blocks_new_dispatch_but_finishes_inflight(self):
        ident,prompt=self.start();self.client.event_capable=False
        self.client.events.bind(None)
        self.assertFalse(self.client.can_run);self.assertFalse(self.client.dispatch_allowed)
        self.peer.finish();self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'complete')
        self.assertEqual(len(self.peer.submissions),1)

    def test_old_inflight_without_server_identity_survives_extension_upgrade(self):
        ident,prompt=self.start()
        job=self.client.generation.jobs[ident]
        job.pop('event_server',None);job.pop('event_epoch',None)
        self.client.generation.save(job)
        self.client.event_capable=False;self.client.events.bind(None)
        self.client.events.reconcile.tick()
        self.assertIsNone(self.client.generation.record(ident).get('event_server'))
        self.client.event_capable=True;self.client.events.bind(self.identity)
        self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['event_server'],self.identity['server_instance'])
        self.peer.finish();self.client.events.receive(self.terminal(prompt),lambda:None)
        self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'complete')
        self.assertEqual(len(self.peer.submissions),1)

    def route_capacity(self,cancel):
        from test_stage_sched_readiness import SchedulingReadinessTests as Readiness
        for name in ('scheduler','rewire','preserved'):setattr(self.f,name,MethodType(getattr(Readiness,name),self.f))
        def finish():
            prompt=next(iter(self.peer.pending));self.peer.finish();self.terminal(prompt);self.drain()
        def drain(expected):
            self.finish_all(expected+8)
            self.assertEqual(len(self.peer.submissions),expected)
        self.f.finish=finish;self.f.drain=drain
        Readiness.route_case(self.f,cancel=cancel)

    def test_repro_capacity_same_synthetic_graph_with_channel(self):self.route_capacity(False)
    def test_repro_cancel_capacity_same_cross_entry_with_channel(self):self.route_capacity(True)

    def test_cancel_accept_commit_failure_rolls_back_memory_and_replays(self):
        ident,prompt=self.start();self.lost_cancel=True
        self.client.events.store.fault=lambda point: (_ for _ in ()).throw(sqlite3.OperationalError('commit failure')) if point=='before_accept_commit' else None
        controls=self.f.a.host.canvas.execution_bar.controls
        controls.refresh();QTest.mouseClick(controls.stop,Qt.MouseButton.LeftButton);self.f.tick()
        self.assertIn(ident,self.client.generation.jobs)
        self.assertTrue(self.client.input_flow.chain.store.read(ident).get('cancel_requested'))
        self.client.events.store.fault=None
        self.client.generation.recheck(ident);self.client.events.reconcile.tick();self.drain()
        self.assertNotIn(ident,self.client.generation.jobs)
        self.assertFalse(self.client.input_flow.chain.store.read(ident).get('cancel_requested'))
        self.assertEqual(len(self.peer.submissions),1)

    def batch_cancel(self,resume):
        from prompt_calculus_studio.schedule_batch_actions import cancel
        self.schedule(3);ident,prompt=self.start();runner=self.client.input_flow.chain
        run=runner.runs(True)[0]
        if not resume:runner.pause(run['id'])
        group=runner.store.rows('batch_control')[0]
        cancel(runner,group['owner'],group['group']);self.drain()
        current=runner.store.read(run['id'])
        self.assertFalse(runner.store.read(ident).get('cancel_requested'))
        self.assertNotIn(ident,self.client.generation.jobs)
        self.assertFalse(current.get('batch_cancel_resume'))
        if not resume:self.assertEqual(current['pause_reason'],'batch_cancel')
        self.assertEqual(len(self.peer.submissions),1)

    def test_batch_cancel_resume_true(self):self.batch_cancel(True)
    def test_batch_cancel_resume_false(self):self.batch_cancel(False)

    def test_sqlite_commit_failure_reopen_twice_does_not_advance_frame(self):
        ident,prompt=self.start();self.peer.finish();event=self.terminal(prompt)
        store=self.client.window.store
        before=copy.deepcopy(self.client.input_flow.chain.store.read(ident))
        def authorizer(action,one,two,database,trigger):
            return sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_TRANSACTION and one=='COMMIT' else sqlite3.SQLITE_OK
        store.db.set_authorizer(authorizer)
        try:
            self.client.events.receive(event,lambda:None);self.client.events.reconcile.tick()
        finally:store.db.set_authorizer(None)
        self.assertEqual(self.client.input_flow.chain.store.read(ident),before)
        self.assertFalse(self.client.events.store.contains(event))
        self.history_error=True
        for _ in range(2):self.reopen()
        self.assertNotEqual(self.client.input_flow.chain.store.read(ident)['status'],'complete')
        self.history_error=False
        self.client.generation.recheck(ident);self.client.events.reconcile.tick();self.f.tick()
        result=copy.deepcopy(self.client.input_flow.chain.store.read(ident)['result'])
        for _ in range(2):self.client.events.receive(event,lambda:None)
        self.assertEqual(self.client.input_flow.chain.store.read(ident)['result'],result)
        self.assertEqual(len(self.peer.submissions),1)

    def test_late_parameter_state_and_event_receipt_rollback_together(self):
        from stage_parameter_fixture import add_samplers,intention
        self.peer.live['parameters_protocol']=1
        def parameters(state):
            profile=state['generation']['profiles'][0];add_samplers(profile)
            config=intention(profile,node='35',field='seed',value=10)
            config['patches'][0].update(seed_mode='increment',seed_timing='after',intent_id='seed-intention')
            next(iter(state['multi_output']['stages'].values()))['parameters']=config
        self.assertTrue(self.f.a.host.canvas.commit(parameters))
        self.peer.graphs['flow']=copy.deepcopy(self.f.a.host.state['generation']['profiles'][0]['graph'])
        ident,prompt=self.start();runner=self.client.input_flow.chain
        job=self.client.generation.record(ident);receipt=copy.deepcopy(job['parameter_receipt'])
        receipt['fields'][0]['next_value']=11
        self.peer.finish();self.client.events.receive(self.terminal(prompt),lambda:None)
        self.client.events.reconcile.tick();self.f.tick()
        frame=copy.deepcopy(runner.store.read(ident)['result'])
        self.assertEqual(self.client.generation.record(ident)['state'],'complete')
        log=self.peer.service.events
        with log.connection() as db:
            event=log.append(db,'operation.queued',dict(state='queued',prompt_id=prompt,parameter_receipt=receipt),entity='native_operations:'+ident,prompt_id=prompt)
        before=copy.deepcopy(runner.store.rows('parameter_state'))
        self.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('full')) if name=='after_receipt' else None
        self.client.events.receive(event,lambda:None)
        self.assertEqual(runner.store.rows('parameter_state'),before)
        self.assertFalse(self.client.events.store.contains(event))
        self.client.events.store.fault=None;self.client.events.reconcile.close()
        self.client.events.receive(event,lambda:None)
        accepted=copy.deepcopy(runner.store.rows('parameter_state'))
        self.assertEqual(accepted[0]['next_seed'],11)
        for _ in range(2):self.client.events.receive(event,lambda:None)
        self.assertEqual(runner.store.rows('parameter_state'),accepted)
        self.assertEqual(runner.store.read(ident)['result'],frame)
        self.assertEqual(len(self.peer.submissions),1)

    def test_replaced_server_freezes_original_job_until_evidence_deadline(self):
        ident,prompt=self.start()
        self.client.events.bind(dict(self.identity,server_instance='replacement'))
        self.client.events.reconcile.clock=self.clock
        self.client.events.reconcile.pending[ident]['start']=self.clock()
        self.client.events.reconcile.pending[ident]['at']=self.clock()
        self.client.events.reconcile.tick()
        self.assertFalse(self.client.events.ready)
        self.assertEqual(self.client.generation.record(ident)['state'],'queued')
        self.clock.now+=30
        self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'unconfirmed')
        self.assertEqual(self.client.generation.record(ident)['prompt_id'],prompt)
        self.assertEqual(len(self.peer.submissions),1)

    def test_frozen_job_queue_and_receipt_are_one_transaction(self):
        from test_work_queue import QueueTests
        from prompt_calculus_studio.queued_work import seal
        fixture=QueueTests('runTest');fixture.setUp();self.addCleanup(fixture.doCleanups)
        work=fixture.capture()
        work['scope'].update(server=self.client.url,library=self.client.window.store.library_id)
        work=seal(work)
        queue=self.client.queue.store;item=queue.add(work,work['capture']);item=queue.claim(self.client.url)
        ident=item['attempt'];prompt='frozen-prompt'
        value=dict(id=ident,capture=item['capture'],state='queued',prompt_id=prompt,payload=dict(prompt=work['graph']))
        with patch.object(self.client,'request',side_effect=lambda route,data=None,done=None,**kw:done(value)):
            self.client.events.reconcile.require(ident,'test');self.client.events.reconcile.tick()
        self.assertEqual(queue.read(item['id'])['state'],'queued')
        log=self.peer.service.events
        with log.connection() as db:event=log.append(db,'operation.cancel_confirmed',dict(cancel_confirmed=True),entity='queue_attempts:'+ident,prompt_id=prompt)
        self.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('full')) if name=='before_accept_commit' else None
        self.client.events.receive(event,lambda:None)
        self.assertEqual(queue.read(item['id'])['state'],'queued')
        self.assertEqual(self.client.generation.record(ident)['state'],'queued')
        self.assertFalse(self.client.events.store.contains(event))
        self.client.events.store.fault=None;self.client.events.reconcile.close()
        self.client.events.receive(event,lambda:None)
        self.assertEqual(queue.read(item['id'])['state'],'failed')
        self.assertTrue(self.client.events.store.contains(event))


if __name__=='__main__':unittest.main()
