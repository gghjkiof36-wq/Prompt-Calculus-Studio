"""STUCK orphan injections, cold repair and cancelled-run lane ownership."""
import copy
import gc
import sqlite3
import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint
import test_event_acceptance_atomic as acceptance
from prompt_calculus_studio.stage_invariants import receipt
from prompt_calculus_studio.workspace_runtime import unfinished


class InvariantTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls: acceptance.AcceptanceTests.setUpClass())
    setUp=acceptance.AcceptanceTests.setUp
    bind=acceptance.AcceptanceTests.bind
    client=acceptance.AcceptanceTests.client
    drain=acceptance.AcceptanceTests.drain
    start=acceptance.AcceptanceTests.start
    terminal=acceptance.AcceptanceTests.terminal
    schedule=acceptance.AcceptanceTests.schedule

    @property
    def runner(self):return self.client.input_flow.chain

    def reopen(self):
        token=self.client.window.runtime_token
        path=self.client.window._event_path
        # Finished/proven-unsent owners are intentionally not auto-recovered
        # in the background. Opening the saved document runs the cold gate.
        with patch.object(self.f,'tick',lambda:None):self.f.reopen(restore_tabs=False)
        gc.collect();self.f.tick()
        if token not in self.f.w.scheduler.clients:
            from pathlib import Path
            self.f.w.open_path(Path(path),new_tab=True)
        self.restored_client=self.f.w.scheduler.clients[token]
        self.bind()
        gc.collect();self.f.tick()

    def orphan(self,status,*,evidence=True,intent='issued'):
        self.schedule(3)
        ident,prompt=self.start()
        self.runner.pause(self.runner.store.read(ident)['owner'])
        self.client.events.reconcile.close()
        self.runner.preparing=None
        with self.runner.accepting():
            self.runner.store.patch(ident,status=status,submission_intent=intent,
                **({} if evidence else dict(operation_evidence=None)))
            self.client.window.store.db.execute('DELETE FROM generation_jobs WHERE id=?',(ident,))
        self.client.generation.jobs.pop(ident,None)
        return ident,prompt

    def check_twice(self,ident):
        state=copy.deepcopy(self.client.window.state)
        parameters=self.runner.store.rows('parameter_state')
        for _ in range(2):self.client.events.invariants.check(ident)
        self.assertEqual(self.client.window.state,state,'repair must not consume another source or enter Canvas state')
        self.assertEqual(self.runner.store.rows('parameter_state'),parameters)

    def click_continue(self):
        controls=self.f.a.host.canvas.execution_bar.controls
        controls.refresh();controls.execute()
        self.f.tick()

    def clear_pending(self):
        controls=self.f.a.host.canvas.execution_bar.controls
        def choose(menu,pos):
            action=next(a for a in menu.actions() if a.text()=='取消此工作區全部流程（含等待項目）')
            action.trigger()
        with patch('prompt_calculus_studio.run_controls.RoundMenu.open_at',choose):controls.stop_menu(QPoint())

    def test_submitted_missing_job_restores_only_durable_owned_payload(self):
        ident,prompt=self.orphan('submitted')
        self.click_continue()
        self.assertEqual(self.client.generation.record(ident)['prompt_id'],prompt)
        self.check_twice(ident)
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertEqual(self.client.generation.record(ident)['prompt_id'],prompt)
        self.assertEqual(len(self.peer.submissions),1)

    def test_submitted_without_evidence_is_visible_and_keeps_lane(self):
        ident,prompt=self.orphan('submitted',evidence=False)
        self.click_continue()
        for _ in range(2):
            self.check_twice(ident)
            self.assertEqual(self.runner.store.read(ident)['error'],'資料不一致')
            self.assertIsNone(self.client.generation.record(ident))
            self.assertTrue(unfinished(self.client.window.store.db))
            self.reopen()
        self.assertEqual(len(self.peer.submissions),1)

    def test_preparing_issued_or_legacy_unknown_never_fails_or_retries(self):
        ident,prompt=self.orphan('preparing',evidence=False,intent='issued')
        self.click_continue()
        for _ in range(2):
            self.check_twice(ident)
            self.assertEqual(self.runner.store.read(ident)['status'],'unconfirmed')
            self.assertEqual(self.runner.store.read(ident)['error'],'無法確認')
            self.reopen()
        self.assertEqual(len(self.peer.submissions),1)

    def test_preparing_without_intent_is_not_proof_of_non_submission(self):
        ident,prompt=self.orphan('preparing',evidence=False,intent=None)
        self.click_continue()
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertEqual(self.runner.store.read(ident)['status'],'unconfirmed')
        self.assertEqual(len(self.peer.submissions),1)

    def test_projection_and_issue_failures_do_not_publish_or_accept(self):
        ident,prompt=self.orphan('submitted')
        checker=self.client.events.invariants
        before=copy.deepcopy(self.runner.store.read(ident))
        checker.fault=lambda point:(_ for _ in ()).throw(sqlite3.OperationalError('commit'))
        with self.assertRaises(sqlite3.Error):checker.check(ident)
        self.assertIsNone(self.client.generation.record(ident))
        self.assertNotIn(ident,self.client.generation.jobs)
        self.assertEqual(self.runner.store.read(ident),before)
        self.runner.store.patch(ident,operation_evidence=None)
        before=copy.deepcopy(self.runner.store.read(ident))
        with self.assertRaises(sqlite3.Error):checker.check(ident)
        self.assertEqual(self.runner.store.read(ident),before)
        checker.fault=None
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertEqual(len(self.peer.submissions),1)

    def test_pump_does_not_run_local_checker(self):
        with patch.object(self.client.events.invariants,'check',side_effect=AssertionError('hot scan')):
            for _ in range(10):self.runner.pump()
        self.assertEqual(len(self.peer.submissions),0)

    def test_preparing_proven_unsent_is_failed_but_never_auto_retries(self):
        # Hold preparation before submit_native; this is the original S7
        # preparing injection, with a durable new-version not-sent fence.
        self.schedule(3)
        held=[]
        with patch('prompt_calculus_studio.native_submission.prepare',side_effect=lambda *a,**k:held.append(a)):
            self.f.click(self.f.a);self.f.tick()
        self.assertTrue(held)
        attempt=self.runner.store.rows('attempt')[0];ident=attempt['id']
        self.assertEqual(attempt['submission_intent'],'not_sent')
        self.runner.preparing=None
        self.runner.pause(attempt['owner']);self.click_continue()
        self.assertEqual(self.runner.store.read(ident)['status'],'failed')
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertEqual(self.runner.store.read(ident)['status'],'failed')
        self.assertEqual(len(self.peer.submissions),0)

    def test_send_fence_failure_cannot_escape_as_an_http_submission(self):
        self.schedule(3)
        db=self.client.window.store.db
        db.execute("CREATE TEMP TRIGGER reject_intent BEFORE INSERT ON generation_jobs BEGIN SELECT RAISE(ABORT,'intent fault'); END")
        try:
            self.f.click(self.f.a);self.f.tick()
        finally:db.execute('DROP TRIGGER reject_intent')
        attempt=self.runner.store.rows('attempt')[0];ident=attempt['id']
        self.assertEqual(attempt['submission_intent'],'not_sent')
        self.assertIsNone(self.client.generation.record(ident))
        self.assertNotIn(ident,self.client.generation.jobs)
        self.assertNotIn(ident,self.client.generation.native_waiting)
        self.runner.preparing=None
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertEqual(len(self.peer.submissions),0)

    def completed_orphan(self,*,result=True):
        ident,prompt=self.orphan('submitted')
        attempt=self.runner.store.read(ident)
        saved=dict(run=attempt['owner'],images=[],text='A',texts=[],attempts=[ident],records=[dict(attempt=ident,text='A',texts=[])])
        self.runner.store.patch(ident,status='complete',**(dict(result=saved) if result else {}))
        return ident,prompt

    def test_complete_result_advances_once_with_receipt_reopen_and_reconcile_twice(self):
        ident,prompt=self.completed_orphan()
        attempt=self.runner.store.read(ident);run_id=attempt['owner']
        before=self.runner.store.read(run_id)
        self.runner.timer.stop()
        with patch.object(self.runner,'later',lambda:None):self.click_continue()
        self.runner.pause(run_id)
        self.check_twice(ident)
        run=self.runner.store.read(run_id)
        self.assertFalse(any(f.get('job')==ident for f in run['stack']))
        self.assertTrue(self.client.events.store.contains(receipt(attempt,'continue')))
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertEqual(self.runner.store.read(run_id)['stack'],run['stack'])
        self.assertEqual(len(self.peer.submissions),1)

    def test_complete_missing_result_is_pending_and_keeps_lane(self):
        ident,prompt=self.completed_orphan(result=False)
        self.click_continue()
        for _ in range(2):
            self.check_twice(ident)
            self.assertEqual(self.runner.store.read(ident)['error'],'結果待確認')
            self.assertTrue(unfinished(self.client.window.store.db))
            self.reopen()
        self.assertEqual(len(self.peer.submissions),1)

    def test_result_manifest_cannot_omit_an_expected_output(self):
        ident,prompt=self.completed_orphan()
        evidence=self.runner.store.read(ident)['operation_evidence']
        evidence['outputs']={'9':dict(images=[dict(filename='missing.png',type='output',subfolder='')])}
        self.runner.store.patch(ident,operation_evidence=evidence)
        self.check_twice(ident)
        self.assertEqual(self.runner.store.read(ident)['error'],'結果待確認')
        self.assertEqual(len(self.peer.submissions),1)

    def test_malformed_payload_is_not_durable_evidence(self):
        ident,prompt=self.orphan('submitted')
        evidence=self.runner.store.read(ident)['operation_evidence'];evidence['payload']=None
        self.runner.store.patch(ident,operation_evidence=evidence)
        self.check_twice(ident)
        self.assertIsNone(self.client.generation.record(ident))
        self.assertEqual(self.runner.store.read(ident)['error'],'資料不一致')
        self.assertEqual(len(self.peer.submissions),1)

    def test_large_payload_evidence_stays_out_of_status_headers(self):
        import json
        ident,prompt=self.start()
        evidence=self.runner.store.read(ident)['operation_evidence']
        evidence['payload']['extra_data']={'synthetic':'x'*(2*1024*1024)}
        self.runner.store.patch(ident,operation_evidence=evidence)
        body,refs=self.client.window.store.db.execute('SELECT body,content_paths FROM stage_header WHERE id=?',(ident,)).fetchone()
        self.assertLess(len(body.encode()),4096)
        self.assertIn('/operation_evidence',json.loads(refs))
        self.assertEqual(self.runner.store.read(ident)['operation_evidence'],evidence)
        self.assertEqual(len(self.peer.submissions),1)

    def test_repair_commit_failure_rolls_back_frame_and_receipt(self):
        ident,prompt=self.completed_orphan()
        attempt=self.runner.store.read(ident);before=self.runner.store.read(attempt['owner'])
        checker=self.client.events.invariants
        checker.fault=lambda point:(_ for _ in ()).throw(sqlite3.OperationalError('repair commit'))
        with self.assertRaises(sqlite3.Error):checker.check(ident)
        self.assertEqual(self.runner.store.read(attempt['owner']),before)
        self.assertFalse(self.client.events.store.contains(receipt(attempt,'continue')))
        checker.fault=None
        for _ in range(2):self.reopen();self.check_twice(ident)
        self.assertFalse(any(f.get('job')==ident for f in self.runner.store.read(attempt['owner'])['stack']))
        self.assertEqual(len(self.peer.submissions),1)

    def test_accepted_continuation_cannot_be_consumed_twice(self):
        ident,prompt=self.completed_orphan();attempt=self.runner.store.read(ident)
        before=self.runner.store.read(attempt['owner'])
        self.check_twice(ident)
        self.runner.save(before)
        self.check_twice(ident)
        self.assertEqual(self.runner.store.read(ident)['error'],'資料不一致')
        self.assertEqual(self.runner.store.read(attempt['owner'])['stack'],before['stack'])
        self.assertEqual(len(self.peer.submissions),1)

    def cancelled_case(self,action,resume):
        self.schedule(3);ident,prompt=self.start();attempt=self.runner.store.read(ident)
        run_id=attempt['owner'];group=self.runner.store.rows('batch_control')[0]
        if not resume:self.runner.pause(run_id)
        self.lost_cancel=True
        # Keep the original HTTP command in flight while retiring its run.
        with patch.object(self.client.events.reconcile,'tick',lambda:None):
            if action=='group':
                from prompt_calculus_studio.schedule_batch_actions import cancel
                cancel(self.runner,group['owner'],group['group'])
            else:self.clear_pending()
            self.runner.store.patch(run_id,status='cancelled',batch_cancel_resume=resume)
        self.assertIn(ident,{j['id'] for j in self.client.events.active()})
        self.assertNotIn(run_id,{r['id'] for r in self.runner.runs(True)})
        self.client.events.reconcile.tick();self.drain();self.check_twice(ident)
        self.assertEqual(self.client.generation.record(ident)['state'],'failed')
        run=self.runner.store.read(run_id)
        self.assertEqual(run['status'],'cancelled')
        self.assertFalse(any(f.get('job')==ident for f in run['stack']))
        self.assertFalse(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)
        self.f.click(self.f.b);self.drain()
        self.assertEqual(sum(s['prompt_id']==prompt for s in self.peer.submissions),1)

    def test_s9b_group_resume_true(self):self.cancelled_case('group',True)
    def test_s9b_group_resume_false(self):self.cancelled_case('group',False)
    def test_s9b_clear_pending_resume_true(self):self.cancelled_case('clear_pending',True)
    def test_s9b_clear_pending_resume_false(self):self.cancelled_case('clear_pending',False)

    def test_cancelled_run_collects_late_success_without_continuing_frame(self):
        ident,prompt=self.start();attempt=self.runner.store.read(ident)
        before=self.runner.store.read(attempt['owner'])
        original=self.client.request
        def hold_cancel(route,*args,**kwargs):
            if route!='workflow/native/cancel':return original(route,*args,**kwargs)
        with patch.object(self.client,'request',side_effect=hold_cancel):self.clear_pending()
        self.peer.finish();self.terminal(prompt);self.drain();self.check_twice(ident)
        run=self.runner.store.read(attempt['owner'])
        self.assertEqual(run['status'],'cancelled')
        self.assertFalse(any(f.get('job')==ident for f in run['stack']))
        self.assertEqual([f.get('index') for f in run['stack']],[f.get('index') for f in before['stack']])
        self.assertEqual(self.runner.store.read(ident)['status'],'complete')
        self.assertFalse(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)

    def test_missing_without_fence_never_retires_or_releases_lane(self):
        ident,prompt=self.start();event=self.terminal(prompt)
        event.update(kind='operation.missing',entity='native_operations:'+ident,payload=dict(state='unconfirmed'))
        for _ in range(2):self.client.events.receive(event,lambda:None)
        self.assertFalse(self.client.generation.record(ident).get('recovery',{}).get('fenced'))
        self.assertTrue(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)


class OriginalSurveyTests(unittest.TestCase):
    """The exact StageTests graph used by survey.py: one Stage + scheduler."""
    def inject(self,status,message):
        from prompt_calculus_studio.comfy_client import ComfyClient
        from prompt_calculus_studio.flow_data import add_scheduler
        from prompt_calculus_studio import multi_output as model
        import test_084_stages
        def offline(client,route,*a,failed=None,**kw):
            if failed:failed('offline fixture')
        case=test_084_stages.StageTests()
        with patch.object(ComfyClient,'request',offline):
            case.setUp()
            try:
                stage=case.stages()[0]
                def setup(state):
                    key=add_scheduler(state);model.connect(state,stage,key+'::flow','flow')
                self.assertTrue(case.c.commit(setup))
                case.controls.count.setValue(3);case.click()
                attempt=case.runner.store.rows('attempt')[0];ident=attempt['id']
                self.assertEqual(len(case.executor.submissions),1)
                case.executor.pending.clear();case.w.comfy.running=0
                case.w.comfy.generation.jobs.clear()
                with case.w.store.db:case.w.store.db.execute('DELETE FROM generation_jobs WHERE id=?',(ident,))
                case.runner.store.patch(ident,status=status,operation_evidence=None,submission_intent=None)
                case.runner.preparing=None;case.runner.pause(attempt['owner'])
                case.controls.execute()
                for _ in range(2):case.w.comfy.events.invariants.check(ident)
                self.assertEqual(case.runner.store.read(ident)['error'],message)
                panel=case.c.flow_cards[stage].panel;panel.refresh()
                self.assertEqual(panel.status.text(),message)
                self.assertEqual(len(case.executor.submissions),1)
            finally:case.tearDown();case.doCleanups()

    def test_s5_original_orphan_submitted(self):self.inject('submitted','資料不一致')
    def test_s6_original_orphan_complete(self):self.inject('complete','結果待確認')
    def test_s7_original_orphan_preparing(self):self.inject('preparing','無法確認')


if __name__=='__main__':unittest.main()
