"""Owned cancellation via actual Qt actions and journals; native service is synthetic."""
import copy
import sys
import unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint
from PySide6.QtGui import QContextMenuEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import test_bound_scheduler as bound
import test_schedule_item_actions as actions
from prompt_calculus_studio import prompt_sources as supply
from prompt_calculus_studio.stage_runner import StageRunner


class CancelAllTests(unittest.TestCase):
    setUp=bound.BoundSchedulerTests.setUp
    tearDown=bound.BoundSchedulerTests.tearDown
    stages=bound.BoundSchedulerTests.stages
    click=bound.BoundSchedulerTests.click
    group_source=bound.BoundSchedulerTests.group_source
    bound_text=bound.BoundSchedulerTests.bound_text
    bound_images=bound.BoundSchedulerTests.bound_images
    image_batch=bound.BoundSchedulerTests.image_batch
    submitted_texts=bound.BoundSchedulerTests.submitted_texts
    queue=actions.ScheduleItemActionsTests.queue

    def menu_cancel(self):
        menus=[];errors=[]
        with patch('prompt_calculus_studio.widgets.RoundMenu.open_at',lambda menu,*_:menus.append(menu)), \
                patch.object(sys,'excepthook',lambda *error:errors.append(error)):
            event=QContextMenuEvent(QContextMenuEvent.Reason.Mouse,QPoint(5,5),self.controls.stop.mapToGlobal(QPoint(5,5)))
            QApplication.sendEvent(self.controls.stop,event)
            self.assertEqual(len(menus),1)
            next(a for a in menus[0].actions() if a.text()=='取消此工作區全部流程（含等待項目）').trigger()
            QTest.qWait(20)
        self.assertFalse(errors,errors)

    def retain_cancel_reply(self,*,error=False):
        original=self.w.comfy.request
        def request(route,data=None,done=None,failed=None,**kw):
            if route=='workflow/native/cancel':
                self.executor.cancelled.append(data['id'])
                if error:failed('synthetic reply lost')
                else:done(dict(state='cancelling'))
                return
            return original(route,data,done,failed,**kw)
        self.w.comfy.request=request

    def reopen_runner(self):
        self.runner.close()
        self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)

    def test_stop_context_action_handles_null_feed_and_finishes_all_rows(self):
        _,entries=self.queue();attempt=self.runner.store.rows('attempt')[0]
        self.assertTrue(any(f.get('feed','absent') is None for f in self.runner.current()['stack']))
        self.menu_cancel()
        self.assertEqual(self.executor.cancelled,[attempt['id']])
        self.assertFalse(self.executor.pending)
        self.assertEqual([self.runner.store.read(e['id'])['status'] for e in entries],['cancelled','retained','retained'])
        self.assertEqual([self.runner.store.read(e['id'])['saved'] for e in entries],[e['saved'] for e in entries])
        self.assertEqual(self.runner.store.read(attempt['id'])['status'],'cancelled')
        self.assertFalse(self.runner.store.rows('entry',active=True))
        self.assertEqual(len(self.executor.submissions),1)

    def test_manual_bound_cancel_keeps_consumed_positions_and_accepts_next_input(self):
        config=self.bound_text((1,1,1,1))
        for _ in range(3):self.click()
        root=copy.deepcopy(supply.definitions(self.w.state)[config['source']]['root'])
        rows=self.runner.store.rows('entry')
        self.menu_cancel()
        self.assertEqual(supply.definitions(self.w.state)[config['source']]['root'],root)
        self.assertEqual([self.runner.store.read(e['id'])['saved'] for e in rows],[e['saved'] for e in rows])
        self.reopen_runner();self.click()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 3'])

    def test_finite_bound_feed_releases_slots_without_reusing_consumed_inputs(self):
        config=self.bound_text((1,)*12,batch=True);self.click()
        rows=self.runner.store.rows('entry',active=True);self.assertEqual(len(rows),10)
        self.assertTrue(any(f.get('feed',{}).get('plan') for f in self.runner.current()['stack'] if f.get('feed')))
        root=copy.deepcopy(supply.definitions(self.w.state)[config['source']]['root'])
        self.runner.cancel()
        self.assertEqual(supply.definitions(self.w.state)[config['source']]['root'],root)
        self.assertFalse(self.runner.store.rows('entry',active=True))
        self.click()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 10'])
        self.assertEqual(len(self.runner.store.rows('entry',active=True)),10)
        self.assertEqual([self.runner.store.read(e['id'])['saved'] for e in rows],[e['saved'] for e in rows])

    def test_image_feed_cancellation_preserves_saved_cursor_and_releases_reservation(self):
        image,_,_=self.image_batch(count=12);self.click()
        current=self.w.state['canvas_functions']['images'][image];before=copy.deepcopy(current)
        self.assertEqual(current['index'],10);self.assertIn('supply_reservation',current)
        self.runner.cancel()
        self.assertNotIn('supply_reservation',current)
        self.assertEqual(current['index'],before['index']);self.assertEqual(current['source'],before['source'])
        self.reopen_runner();self.click()
        rows=self.runner.store.rows('entry',active=True)
        self.assertEqual(len(rows),10)
        self.assertEqual(rows[0]['saved']['source_positions'][image]['index'],10)

    def test_all_runs_saved_before_requests_and_foreign_backend_job_untouched(self):
        self.group_source((1,1,1),scheduler=False)
        for _ in range(3):self.click()
        runs=self.runner.runs(True);self.assertEqual(len(runs),3)
        attempts=self.runner.store.rows('attempt');self.assertEqual(len(attempts),3)
        self.executor.pending['foreign']={'prompt_id':'foreign'}
        original=self.w.comfy.request
        def request(route,data=None,done=None,failed=None,**kw):
            if route=='workflow/native/cancel':
                self.assertTrue(all(self.runner.store.read(r['id'])['status']=='cancelled' for r in runs))
            return original(route,data,done,failed,**kw)
        self.w.comfy.request=request;self.runner.cancel()
        self.assertEqual(set(self.executor.cancelled),{a['id'] for a in attempts})
        self.assertEqual(set(self.executor.pending),{'foreign'})
        self.assertTrue(all(self.runner.store.read(a['id'])['status']=='cancelled' for a in attempts))

    def test_specific_run_cancel_leaves_other_runs_intact(self):
        self.group_source((1,1),scheduler=False);self.click();self.click()
        runs=self.runner.runs(True);other=copy.deepcopy(runs[1])
        attempts=self.runner.store.rows('attempt');self.runner.cancel(runs[0]['id'])
        self.assertEqual(self.executor.cancelled,[attempts[0]['id']])
        self.assertEqual(self.runner.store.read(other['id']),other)
        self.assertIn(attempts[1]['prompt_id'],self.executor.pending)

    def test_late_completion_retains_result_but_does_not_dispatch_or_rewind_cursor(self):
        config=self.bound_text((1,1,1));self.controls.count.setValue(3);self.click()
        root=copy.deepcopy(supply.definitions(self.w.state)[config['source']]['root'])
        attempt=self.runner.store.rows('attempt')[0];entries=self.runner.store.rows('entry')
        self.retain_cancel_reply();self.menu_cancel()
        self.assertTrue(self.runner.store.read(attempt['id'])['cancel_requested'])
        self.assertEqual(self.w.comfy.generation.record(attempt['id'])['state'],'queued')
        self.executor.finish()
        finished=self.runner.store.read(attempt['id'])
        self.assertEqual(finished['status'],'complete');self.assertTrue(finished['result']['images'])
        self.assertFalse(finished['cancel_requested'])
        self.assertEqual([self.runner.store.read(e['id'])['status'] for e in entries],['cancelled','retained','retained'])
        self.assertEqual(len(self.executor.submissions),1)
        self.assertEqual(supply.definitions(self.w.state)[config['source']]['root'],root)

    def test_lost_cancel_reply_survives_reopen_and_late_failure_is_reconciled(self):
        self.bound_text();self.click();attempt=self.runner.store.rows('attempt')[0]
        self.retain_cancel_reply(error=True);self.runner.cancel();self.reopen_runner()
        self.assertTrue(self.runner.store.read(attempt['id'])['cancel_requested'])
        with self.assertRaises(ValueError):self.runner.retry(attempt['id'])
        self.runner.resume(attempt['owner']);QTest.qWait(20)
        self.assertEqual(len(self.executor.submissions),1)
        self.executor.finish(error=True)
        current=self.runner.store.read(attempt['id'])
        self.assertEqual(current['status'],'cancelled');self.assertFalse(current['cancel_requested'])
        self.assertFalse(self.runner.runs(True))

    def test_cancel_before_native_receipt_blocks_late_start_reply(self):
        held=[];original=self.w.comfy.request
        def request(route,data=None,done=None,failed=None,**kw):
            if route=='workflow/native/start':
                self.executor.native.poll(self.executor.live);self.executor.native.start(data)
                held.append((data,done));return
            if route=='workflow/native/cancel':
                self.executor.cancelled.append(data['id']);done(self.executor.native.cancel(data['id'],None,None));return
            return original(route,data,done,failed,**kw)
        self.w.comfy.request=request;_,entries=self.queue();self.menu_cancel()
        data,done=held[0];done(self.executor.native.status(data['id']));QTest.qWait(30)
        self.assertFalse(self.executor.native.poll(self.executor.live)['commands'])
        self.assertFalse(self.executor.submissions)
        self.assertEqual(self.runner.store.read(data['id'])['status'],'cancelled')
        self.assertEqual([self.runner.store.read(e['id'])['status'] for e in entries],['cancelled','retained','retained'])

    def test_other_workspace_is_not_cancelled_and_late_result_leaves_new_scene_intact(self):
        self.bound_text();self.click();run=self.runner.current();attempt=self.runner.store.rows('attempt')[0]
        with patch('prompt_calculus_studio.window.QInputDialog.getText',return_value=('other',True)):self.w.new_workspace()
        before=copy.deepcopy(self.runner.store.read(run['id']))
        self.runner.cancel()
        self.assertEqual(self.runner.store.read(run['id']),before);self.assertFalse(self.executor.cancelled)
        with self.assertRaises(ValueError):self.runner.cancel(run['id'])
        self.w.workspace.setCurrentIndex(self.w.workspace.findData(run['workspace']));QTest.qWait(20)
        self.retain_cancel_reply();self.runner.cancel()
        other=next(w['id'] for w in self.w.state['workspaces'] if w['id']!=run['workspace'])
        self.w.workspace.setCurrentIndex(self.w.workspace.findData(other));QTest.qWait(20)
        scene=copy.deepcopy(self.w.state);self.executor.finish(ident=attempt['prompt_id'])
        self.assertEqual(self.w.state,scene);self.assertEqual(len(self.executor.submissions),1)

    def test_cancel_is_not_sent_to_another_server(self):
        self.bound_text();self.click();attempt=self.runner.store.rows('attempt')[0]
        original=self.w.comfy.url;self.w.comfy.url='http://other.invalid'
        try:
            self.runner.cancel()
            self.assertFalse(self.executor.cancelled)
            self.assertTrue(self.runner.store.read(attempt['id'])['cancel_requested'])
            self.assertEqual(self.w.comfy.generation.record(attempt['id'])['state'],'queued')
        finally:self.w.comfy.url=original

    def test_local_write_failure_rolls_back_before_any_cancel_request(self):
        _,entries=self.queue();before=self.runner.store.rows('run')+self.runner.store.rows('entry')+self.runner.store.rows('attempt')
        update=self.runner.store.update
        def fail(ident,**values):
            if ident==entries[1]['id']:raise OSError('synthetic durable-write failure')
            return update(ident,**values)
        with patch.object(self.runner.store,'update',side_effect=fail),self.assertRaises(OSError):self.runner.cancel()
        self.assertEqual(self.runner.store.rows('run')+self.runner.store.rows('entry')+self.runner.store.rows('attempt'),before)
        self.assertFalse(self.executor.cancelled)


if __name__=='__main__':unittest.main()
