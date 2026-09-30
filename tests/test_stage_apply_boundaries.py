"""Delayed input replies cannot cross a cancelled run or workspace boundary.

Generation goes through the production native receipt and queue/history path;
only transport replies are delayed. No task is directly marked complete.
"""
import unittest
import sqlite3
import json
import time
from contextlib import closing
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import test_084_free_results as free
from prompt_studio import workspace_scene


class StageApplyBoundaryTests(unittest.TestCase):
    setUp=free.FreeResultTests.setUp
    tearDown=free.FreeResultTests.tearDown
    downstream=free.FreeResultTests.downstream
    click=free.FreeResultTests.click
    workflows=free.FreeResultTests.workflows
    stages=free.FreeResultTests.stages

    def hold_plain_apply(self,confirm_cancel=True):
        original=self.w.comfy.request;held=[];cancelled=[];cancel_replies=[]
        def delay(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/apply':
                self.executor.native.poll(self.executor.live)
                self.executor.native.start(data,action='apply');held.append((data,done));return
            if route=='workflow/native/cancel':
                cancelled.append(data['id'])
                if confirm_cancel:
                    result=self.executor.native.cancel(data['id'],None,None)
                    if done:done(result)
                else:cancel_replies.append(done)
                return
            return original(route,data,done,failed,**kwargs)
        self.w.comfy.request=delay;self.click();self.assertTrue(held)
        self.assertFalse(self.runner.runs());self.assertTrue(self.w.comfy.input_flow.has_work())
        self.assertEqual(self.runner.apply_operation['owner'],'')
        ident=held[0][0]['id'];self.assertEqual(self.executor.native.status(ident)['state'],'pending')
        return ident,held,cancelled,cancel_replies

    def assert_plain_cancelled(self,ident,held,cancelled):
        self.assertEqual(cancelled,[ident]);self.assertEqual(self.executor.native.poll(self.executor.live)['commands'],[])
        for data,done in held:
            if done:done(self.executor.native.status(data['id']))
        QTest.qWait(30)
        self.assertFalse(self.executor.applied);self.assertIsNone(self.runner.applying)
        self.assertFalse(self.runner.apply_queue);self.assertFalse(self.workflows())

    def test_no_stage_pending_apply_can_be_cancelled_using_the_existing_button(self):
        ident,held,cancelled,_=self.hold_plain_apply()
        controls=self.c.execution_bar.controls
        self.assertTrue(controls.stop.isEnabled())
        QTest.mouseClick(controls.stop,Qt.MouseButton.LeftButton)
        self.assert_plain_cancelled(ident,held,cancelled)
        self.assertFalse(self.w.comfy.input_flow.has_work())
        self.assertEqual(self.runner.store.read(ident)['status'],'cancelled')

    def test_no_stage_pending_apply_is_cancelled_before_switching_workspace(self):
        ident,held,cancelled,_=self.hold_plain_apply()
        target=workspace_scene.create(self.w.state,'other workspace');self.w.change_workspace(target)
        self.assert_plain_cancelled(ident,held,cancelled)
        self.assertEqual(self.w.state['workspace'],target)
        self.assertFalse(self.w.comfy.input_flow.has_work())

    def test_no_stage_pending_apply_is_cancelled_before_window_transport_shutdown(self):
        ident,held,cancelled,_=self.hold_plain_apply()
        self.w.close();self.assertTrue(self.runner.closed);self.assertTrue(self.w.comfy.stopped)
        self.assert_plain_cancelled(ident,held,cancelled)

    def test_close_without_cancel_ack_is_bounded_and_preserves_unknown_receipt(self):
        ident,held,cancelled,cancel_replies=self.hold_plain_apply(confirm_cancel=False)
        filename=self.w.store.db.execute('PRAGMA database_list').fetchone()[2]
        started=time.monotonic();self.w.close();self.assertLess(time.monotonic()-started,1.5)
        self.assertEqual(cancelled,[ident]);self.assertTrue(self.runner.closed)
        with closing(sqlite3.connect(filename)) as db:
            record=json.loads(db.execute('SELECT body FROM stage_journal WHERE id=?',(ident,)).fetchone()[0])
        self.assertEqual((record['status'],record['cancel_state']),('unconfirmed','requested'))
        self.assertTrue(record['sent']);self.assertFalse(record['ended'])
        # A late transport reply after Qt/store close must not call UI or DB.
        for done in cancel_replies:done(dict(state='failed'))
        for _,done in held:done(dict(state='applied'))
        self.assertFalse(self.executor.applied)

    def test_legacy_chain_without_apply_tracking_still_shuts_down(self):
        from prompt_studio.comfy_client import ComfyClient
        events=[]
        client=SimpleNamespace(input_flow=SimpleNamespace(chain=SimpleNamespace(),disconnected=lambda:events.append('flow')),
            queue=SimpleNamespace(disconnected=lambda:events.append('queue')),
            generation=SimpleNamespace(disconnected=lambda:events.append('generation')),
            timer=SimpleNamespace(stop=lambda:None),sync_timer=SimpleNamespace(stop=lambda:None),replies=set())
        client.flush_input_cancellations=lambda:ComfyClient.flush_input_cancellations(client)
        ComfyClient.shutdown(client)
        self.assertTrue(client.stopped);self.assertEqual(events,['flow','queue','generation'])

    def hold_result_upload(self):
        self.downstream();self.click()
        original=self.w.comfy.request;pending=[]
        def delayed(route,*args,**kwargs):
            if route=='/upload/image':pending.append(lambda:original(route,*args,**kwargs))
            else:return original(route,*args,**kwargs)
        self.w.comfy.request=delayed
        self.executor.finish()
        self.assertEqual(self.workflows(),['flow'])
        self.assertTrue(pending)
        run=self.runner.runs()[0]
        self.assertEqual(run['free_state'],'applying')
        self.assertEqual(self.runner.store.rows('attempt',owner=run['id'])[0]['status'],'complete')
        self.assertFalse(self.executor.applied)
        return run,pending

    def test_cancel_during_upload_must_not_apply(self):
        run,pending=self.hold_result_upload()
        self.runner.cancel(run['id'])
        for reply in pending:reply()
        QTest.qWait(30)
        self.assertFalse(self.executor.applied)
        self.assertEqual(self.runner.store.read(run['id'])['status'],'cancelled')
        self.assertIsNone(self.runner.applying)

    def test_workspace_switch_during_upload_must_not_apply(self):
        run,pending=self.hold_result_upload()
        target=workspace_scene.create(self.w.state,'other workspace')
        self.w.change_workspace(target)
        for reply in pending:reply()
        QTest.qWait(30)
        self.assertFalse(self.executor.applied)
        self.assertIsNone(self.runner.applying)
        self.assertEqual(self.w.state['workspace'],target)

    def test_disconnect_then_resume_releases_apply_slot_without_resend(self):
        run,pending=self.hold_result_upload()
        self.w.comfy.connected=False;self.runner.disconnected()
        self.w.comfy.connected=True;self.runner.resume(run['id'])
        for reply in pending:reply()
        QTest.qWait(80);self.executor.tick()
        current=self.runner.store.read(run['id'])
        self.assertEqual(current['status'],'complete',current)
        self.assertEqual(current['free_state'],'failed')
        self.assertIsNone(self.runner.applying)
        self.assertFalse(self.runner.apply_queue)
        self.assertFalse(self.executor.applied)
        self.assertEqual(self.workflows(),['flow'])

    def test_failed_click_does_not_drop_next_independent_apply(self):
        original=self.w.comfy.request;pending=[];calls=[]
        def delayed_first(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/apply':
                calls.append(data)
                if len(calls)==1:pending.append(failed);return
            return original(route,data,done,failed,**kwargs)
        self.w.comfy.request=delayed_first
        self.click();self.assertTrue(pending)
        self.c.commit(lambda s:s['uses']['root'].update(prompt='second click prompt'))
        self.click();self.assertEqual(len(self.runner.apply_queue),1)
        pending[0]('first apply failed');QTest.qWait(80)
        self.assertEqual(len(self.executor.applied),1,self.notices)
        self.assertEqual(self.executor.graphs['flow']['6']['inputs']['text'],'second click prompt')
        self.assertFalse(self.workflows())
        self.assertIsNone(self.runner.applying)
        self.assertFalse(self.runner.apply_queue)

    def test_changed_free_route_cancels_accepted_apply_before_browser_delivery(self):
        for change in ('disconnect','output_node'):
            with self.subTest(change=change):
                f=free.FreeResultTests('test_unconnected_downstream_applies_after_result_without_running_b')
                f.setUp()
                try:
                    stage,reader,target=f.downstream();f.click()
                    original=f.w.comfy.request;held=[];cancelled=[]
                    before=f.executor.graphs['B']['10']['inputs']['image']
                    def delay(route,data=None,done=None,failed=None,**kwargs):
                        if route=='workflow/native/apply':
                            # Accept through the real journal, but hold browser
                            # delivery. Cancelling merely a delayed UI reply is
                            # insufficient to prevent this graph mutation.
                            f.executor.native.poll(f.executor.live)
                            f.executor.native.start(data,action='apply')
                            held.append((data,done));return
                        if route=='workflow/native/cancel':
                            cancelled.append(data['id'])
                            result=f.executor.native.cancel(data['id'],None,None)
                            if done:done(result)
                            return
                        return original(route,data,done,failed,**kwargs)
                    f.w.comfy.request=delay;f.executor.finish();f.assertTrue(held)
                    ident=held[0][0]['id']
                    f.assertEqual(f.executor.native.status(ident)['state'],'pending')
                    def edit(state):
                        if change=='disconnect':
                            state['multi_output']['connections']=[c for c in state['multi_output']['connections'] if c['destination']!=target]
                        else:state['canvas_functions']['images'][reader]['output_node']='missing-result-node'
                    f.assertTrue(f.c.commit(edit),f.notices)
                    f.assertIn(ident,cancelled)
                    f.assertEqual(f.executor.native.poll(f.executor.live)['commands'],[])
                    for data,done in held:
                        if done:done(f.executor.native.status(data['id']))
                    QTest.qWait(30)
                    f.assertEqual(f.executor.graphs['B']['10']['inputs']['image'],before)
                    f.assertFalse(f.executor.applied);f.assertIsNone(f.runner.applying)
                finally:f.tearDown();f.doCleanups()


if __name__=='__main__':unittest.main()
