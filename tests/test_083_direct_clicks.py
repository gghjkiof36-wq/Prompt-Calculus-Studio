"""Click -> real native receipt -> native backlog; no GPU completion shortcut."""
import copy
import json
import time
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton
from prompt_studio import multi_output as model
from prompt_studio.flow_data import add_scheduler,endpoint
from prompt_studio.input_runner import InputRunner
import test_083_runtime_repair as runtime
from test_083_recovery import RecoveryExecutor


class DirectClickTests(unittest.TestCase):
    setUp=runtime.RuntimeRepairTests.setUp
    tearDown=runtime.RuntimeRepairTests.tearDown
    click=runtime.RuntimeRepairTests.click
    text=runtime.RuntimeRepairTests.text

    def schedule(self):
        created=[]
        def connect(state):
            sid=add_scheduler(state);created.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect)
        return created[0]

    def test_idle_schedule_runs_now_busy_clicks_save_only_later_inputs(self):
        sid=self.schedule();runner=self.w.comfy.input_flow
        self.click();self.assertEqual(len(self.executor.submissions),1)
        self.assertEqual(runner.store.rows(sid),[])
        self.text('B');self.click();self.text('C');self.click();self.text('D')
        self.assertEqual(len(self.executor.submissions),1)
        self.assertEqual(len(runner.store.rows(sid)),2)
        self.executor.graph['3']['inputs']['steps']=31
        self.executor.finish();self.assertEqual(len(self.executor.submissions),2)
        self.executor.finish();self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['A','B','C'])
        self.assertEqual(self.executor.submissions[1]['prompt']['3']['inputs']['steps'],31)

    def test_slow_receipt_three_clicks_preserve_three_distinct_immediate_submits(self):
        transport=self.w.comfy.request;receipts=[]
        def delay(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/start' and not receipts:
                return transport(route,data,lambda result:receipts.append((done,result)),failed,**kwargs)
            return transport(route,data,done,failed,**kwargs)
        self.w.comfy.request=delay
        self.click();self.text('B');self.click();self.text('C');self.click();self.text('D')
        self.assertEqual(len(self.executor.submissions),1)
        receipts[0][0](receipts[0][1])
        deadline=time.monotonic()+2
        while len(self.executor.pending)<3 and time.monotonic()<deadline:QTest.qWait(10)
        self.assertEqual(len(self.executor.pending),3)
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['A','B','C'])
        for _ in range(3):self.executor.finish()

    def test_old_paused_credits_and_lost_history_never_gate_new_clicks(self):
        self.click();job=next(iter(self.w.comfy.generation.jobs.values()));job['created']-=60
        self.executor.pending.clear();self.w.comfy.running=0
        runner=self.w.comfy.input_flow;runner.disconnected()
        self.w.comfy.generation.observe(dict(running_ids=[],queued_ids=[]))
        self.assertEqual(job['state'],'unconfirmed')
        owner=runner.owner(None,'flow')
        runner.store.set_control(owner,credits=17,paused=True,route=runner.route('flow',None),active=dict(operation=job['id']))
        recovered=InputRunner(self.w.comfy);self.w.comfy.input_flow=recovered
        self.assertEqual(recovered.store.control(owner)['legacy_demands'],17)
        self.assertEqual(recovered.store.control(owner)['credits'],0)
        self.text('new explicit click');self.click()
        self.assertEqual(len(self.executor.submissions),2)
        self.executor.finish();self.assertEqual(len(self.executor.submissions),2)
        self.assertEqual(self.w.comfy.generation.record(job['id'])['state'],'unconfirmed')

    def test_cancel_direct_uses_exact_task_and_keeps_run_available(self):
        self.executor.__class__=RecoveryExecutor;self.w.comfy.request=self.executor.request
        self.click();job=next(iter(self.w.comfy.generation.jobs.values()))
        calls=[];transport=self.w.comfy.request
        def record(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/cancel':calls.append(copy.deepcopy(data))
            return transport(route,data,done,failed,**kwargs)
        self.w.comfy.request=record
        QTest.mouseClick(self.controls.stop,Qt.MouseButton.LeftButton);QTest.qWait(10)
        self.assertEqual(calls,[dict(id=job['id'])])
        self.assertTrue(self.controls.run_button.isEnabled())
        self.text('after cancel');self.click();self.assertEqual(len(self.executor.submissions),2)

    def test_legacy_frozen_queue_record_cannot_veto_a_new_native_submission(self):
        with patch.object(self.executor.service.work_queue,'unresolved',return_value=[dict(id='old',state='unconfirmed')]):
            self.click()
        self.assertEqual(len(self.executor.submissions),1,self.notices)
        self.executor.finish()

    def test_upgrade_keeps_old_buffer_readable_without_pausing_fresh_clicks(self):
        sid=self.schedule();runner=self.w.comfy.input_flow;self.w.comfy.running=1
        self.click();item=runner.store.rows(sid)[0];owner=item['owner']
        control=runner.store.control(owner);control.pop('execution_revision');control['paused']=True
        with self.w.store.db:self.w.store.db.execute('UPDATE input_queue_control SET body=? WHERE owner=?',(json.dumps(control),owner))
        self.w.comfy.running=0;self.w.comfy.input_flow=runner=InputRunner(self.w.comfy)
        retained=runner.store.rows(sid,history=True)[0]
        self.assertEqual(retained['inputs'],item['inputs']);self.assertEqual(retained['state'],'retained')
        self.assertEqual(runner.store.rows(sid),[])
        self.text('fresh A');self.click();self.text('fresh B');self.click()
        self.executor.finish();self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['fresh A','fresh B'])

    def test_bottom_bar_has_four_controls_and_task_count_opens_history(self):
        self.w.resize(1024,760);QTest.qWait(25)
        bar=self.c.execution_bar;bar.place();self.controls.refresh()
        visible=[b for b in bar.findChildren(QPushButton) if b.isVisible()]
        self.assertEqual(set(visible),{self.controls.run_button,self.controls.stop,self.controls.activity})
        self.assertLess(bar.width(),540)
        self.assertTrue(self.controls.count.isVisible())
        self.assertGreaterEqual(self.controls.count.lineEdit().width(),self.controls.count.fontMetrics().horizontalAdvance('100')+2)
        with patch.object(self.w.generation_panel,'history') as history:
            QTest.mouseClick(self.controls.activity,Qt.MouseButton.LeftButton)
            history.assert_called_once()


if __name__=='__main__':unittest.main()
