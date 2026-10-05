"""Recovery after native history disappears, through receipt and UI actions."""
import copy
import threading
import unittest
from types import SimpleNamespace
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.flow_data import add_scheduler,endpoint
import test_083_runtime_repair as runtime_tests


class RecoveryExecutor(runtime_tests.ReceiptExecutor):
    def executor_queue(self):
        return SimpleNamespace(mutex=threading.RLock(),queue=[],
            currently_running={key:[0,key,p['prompt'],p['extra_data']] for key,p in self.pending.items()},
            get_history=lambda prompt_id:{prompt_id:self.history[prompt_id]} if prompt_id in self.history else {})

    def request(self,route,data=None,done=None,failed=None,**kwargs):
        if route not in ('workflow/native/recovery','workflow/native/abandon','workflow/native/cancel'):
            return super().request(route,data,done,failed,**kwargs)
        try:
            queue=self.executor_queue()
            if route.endswith('/recovery'):result=self.native.recovery(data['id'],queue)
            elif route.endswith('/abandon'):result=self.native.abandon(data['id'],queue)
            else:result=self.native.cancel(data['id'],queue,lambda:None)
            if done:done(result)
        except ValueError as exc:
            if failed:failed(str(exc))
            else:raise


class RecoveryTests(unittest.TestCase):
    setUp=runtime_tests.RuntimeRepairTests.setUp
    tearDown=runtime_tests.RuntimeRepairTests.tearDown
    click=runtime_tests.RuntimeRepairTests.click
    text=runtime_tests.RuntimeRepairTests.text

    def transport(self):
        self.executor.__class__=RecoveryExecutor
        self.w.comfy.request=self.executor.request

    def missing_history(self):
        self.transport();self.click()
        runner=self.w.comfy.input_flow;job=next(iter(self.w.comfy.generation.jobs.values()))
        # The native receipt really reached queued; restart/lost history then
        # removes the executor evidence, without inventing a terminal state.
        self.assertEqual(self.executor.native.read(job['id'])['state'],'queued')
        job['created']-=60;self.w.comfy.generation.save(job)
        self.executor.pending.clear();self.w.comfy.running=0;runner.disconnected()
        self.w.comfy.generation.observe(dict(running_ids=[],queued_ids=[]))
        self.assertEqual(job['state'],'unconfirmed')
        return runner,job

    def test_missing_receipt_is_retained_without_blocking_new_work(self):
        runner,job=self.missing_history()
        self.text('new work');self.click()
        self.assertEqual(len(self.executor.submissions),2)
        self.w.comfy.generation.abandon(job['id'])
        self.assertTrue(self.executor.native.read(job['id'])['abandoned'])
        self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['A','new work'])

    def test_recheck_remains_in_history_but_is_not_an_execution_control(self):
        runner,job=self.missing_history()
        self.w.comfy.generation.recheck(job['id'])
        record=self.w.comfy.generation.record(job['id'])
        self.assertEqual(record['recovery']['location'],'missing')
        self.assertTrue(record['recovery']['can_abandon'])
        self.controls.refresh();self.assertFalse(hasattr(self.controls,'recovery_button'))
        self.click();self.assertEqual(len(self.executor.submissions),2)

    def test_detached_scheduler_keeps_items_but_releases_native_button(self):
        self.transport();created=[]
        def connect(state):
            sid=add_scheduler(state);created.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect);sid=created[0];runner=self.w.comfy.input_flow
        self.w.comfy.input_bridge_supported=True;self.w.comfy.running=1;self.click()
        runner.bridge.poll();self.assertEqual(len(runner.bridge.entries()),1)
        self.c.commit(lambda state:model.connect(state,self.out,self.clip,'clip'))
        runner.bridge.poll()
        self.assertEqual(runner.bridge.entries(),[])
        self.assertEqual(len(runner.store.rows(sid)),1)
        self.assertTrue(runner.store.control(sid)['paused'])
        self.assertFalse(self.executor.native.inputs.claim(dict(id='manual',identity=self.executor.live['identity']))['handled'])

    def test_old_unconfirmed_job_does_not_block_new_idle_scheduler_click(self):
        runner,job=self.missing_history();created=[]
        def connect(state):
            sid=add_scheduler(state);created.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect);self.click()
        self.assertEqual(len(self.executor.submissions),2)
        self.assertEqual(job['state'],'unconfirmed')

    def test_delayed_history_reply_cannot_restore_retired_job(self):
        runner,job=self.missing_history();callbacks=[];transport=self.w.comfy.request
        def delayed(route,data=None,done=None,failed=None,**kwargs):
            if route.startswith('/history/'):
                callbacks.append(done);return
            return transport(route,data,done,failed,**kwargs)
        self.w.comfy.request=delayed
        self.w.comfy.generation.observe(dict(running_ids=[],queued_ids=[]))
        self.assertEqual(len(callbacks),1)
        self.w.comfy.generation.abandon(job['id']);callbacks[0]({})
        self.assertEqual(self.w.comfy.generation.record(job['id'])['state'],'failed')
        self.assertFalse(self.w.comfy.generation.jobs)

    def test_scheduler_cancel_missing_job_retires_only_that_item(self):
        self.transport();created=[]
        def connect(state):
            sid=add_scheduler(state);created.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect);sid=created[0];runner=self.w.comfy.input_flow
        self.click();self.text('B');self.click()
        job=next(iter(self.w.comfy.generation.jobs.values()));job['created']-=60
        self.executor.pending.clear();self.w.comfy.running=0
        self.w.comfy.generation.observe(dict(running_ids=[],queued_ids=[]))
        runner.cancel(sid)
        self.assertEqual(len(runner.store.rows(sid)),1)
        self.assertEqual(runner.store.rows(sid)[0]['state'],'waiting')
        runner.resume(sid);self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['A','B'])


if __name__=='__main__':unittest.main()
