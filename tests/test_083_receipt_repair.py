"""Input normalization, scoped cancellation, and late receipt protection."""
import copy
import threading
import unittest
from types import SimpleNamespace
from prompt_calculus_studio.native_graph import graph_matches,input_schema
import test_native_queue as native_tests


class ReceiptRepairTests(unittest.TestCase):
    setUp=native_tests.NativeQueueTests.setUp
    prepared=native_tests.NativeQueueTests.prepared

    def queue_job(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        self.queue.reply(dict(self.live,id='operation-1',prompt_id=payload['prompt_id']))
        return [0,payload['prompt_id'],payload['prompt'],payload['extra_data']]

    def test_numeric_types_are_declared_and_seed_text_links_stay_exact(self):
        graph={'1':dict(class_type='KSampler',inputs=dict(cfg=2,seed=9007199254740993,positive=['2',0]))}
        actual=copy.deepcopy(graph);actual['1']['inputs']['cfg']=2.0
        self.assertTrue(graph_matches(graph,actual))
        for field,value in [('seed',9007199254740992),('seed',float(9007199254740993)),('cfg',True),('positive',['2',0.0])]:
            changed=copy.deepcopy(actual);changed['1']['inputs'][field]=value
            self.assertFalse(graph_matches(graph,changed),field)
        class Custom:
            @staticmethod
            def INPUT_TYPES():return dict(required=dict(scale=('FLOAT',{}),seed=('INT',{}),text=('STRING',{})))
        graph={'1':dict(class_type='Custom',inputs=dict(scale=2,seed=4,text='2'))}
        actual=copy.deepcopy(graph);actual['1']['inputs']['scale']=2.0
        self.assertFalse(graph_matches(graph,actual))
        schema=input_schema(graph,{'Custom':Custom});self.assertTrue(graph_matches(graph,actual,schema))
        actual['1']['inputs']['text']=2;self.assertFalse(graph_matches(graph,actual,schema))

    def test_cancel_pending_job_leaves_foreign_running_and_queued_work(self):
        own=self.queue_job();foreign=[1,'manual-job',{'x':{}},{}]
        q=SimpleNamespace(mutex=threading.RLock(),currently_running={'foreign':foreign},queue=[own,foreign])
        interrupted=[];result=self.queue.cancel('operation-1',q,lambda:interrupted.append(True))
        self.assertEqual(result['state'],'failed');self.assertEqual(q.queue,[foreign]);self.assertFalse(interrupted)
        self.assertEqual(q.currently_running,{'foreign':foreign})

    def test_running_numeric_normalization_can_cancel_only_owned_job(self):
        own=self.queue_job();own[2]['3']['inputs']['cfg']=float(own[2]['3']['inputs']['cfg'])
        q=SimpleNamespace(mutex=threading.RLock(),currently_running={'own':own},queue=[]);interrupted=[]
        self.assertEqual(self.queue.cancel('operation-1',q,lambda:interrupted.append(True))['state'],'cancelling')
        self.assertEqual(interrupted,[True])
        own[3]['extra_pnginfo']['prompt_studio']['generation']['native_operation']='another'
        with self.assertRaisesRegex(ValueError,'歸屬不符'):self.queue.cancel('operation-1',q,lambda:interrupted.append(True))
        self.assertEqual(interrupted,[True])

    def test_terminal_mismatch_releases_occupancy_but_cannot_abandon_active(self):
        own=self.queue_job();q=SimpleNamespace(mutex=threading.RLock(),currently_running={'own':own},queue=[],get_history=lambda prompt_id:{})
        with self.assertRaisesRegex(ValueError,'仍在'):self.queue.abandon('operation-1',q)
        q.currently_running={};changed=copy.deepcopy(own);changed[2]['3']['inputs']['seed']+=1
        entry=dict(prompt=changed,status=dict(completed=True,status_str='success'))
        self.assertFalse(self.queue.occupied([],[],lambda key:{key:entry}))
        self.assertEqual(self.queue.abandon('operation-1',q)['state'],'abandoned')
        self.queue.reply(dict(self.live,id='operation-1',prompt_id='late'))
        self.assertEqual(self.queue.read('operation-1')['state'],'failed')

    def test_cannot_abandon_before_pending_native_submit_is_cancelled(self):
        self.queue.start(self.request)
        q=SimpleNamespace(mutex=threading.RLock(),currently_running={},queue=[],get_history=lambda prompt_id:{})
        with self.assertRaisesRegex(ValueError,'待提交'):self.queue.abandon('operation-1',q)
        self.assertEqual(self.queue.cancel('operation-1',q,lambda:None)['state'],'failed')
        self.assertEqual(self.queue.abandon('operation-1',q)['state'],'abandoned')

    def test_manual_claim_uses_active_workspace_and_retains_paused_other_workspace(self):
        entry=dict(owner='current',head='A',workflow='flow',frontend_id='native-A',path='folder/A.json',paused=False)
        self.queue.inputs.publish(dict(client='one-db',session='desktop',entries=[dict(entry,owner='old',head='B',paused=True),entry]))
        result=self.queue.inputs.claim(dict(id='manual',identity=self.live['identity']))
        self.assertTrue(result['handled']);self.assertEqual(result['item'],'A')
        self.queue.inputs.publish(dict(client='one-db',session='desktop',entries=[dict(entry,paused=True)]))
        self.assertFalse(self.queue.inputs.claim(dict(id='paused',identity=self.live['identity']))['handled'])

    def test_inflight_submit_never_uses_empty_queue_as_proof_until_restart(self):
        from unittest.mock import patch
        payload=self.prepared();self.service.prepare_prompt(payload)
        q=SimpleNamespace(mutex=threading.RLock(),currently_running={},queue=[],get_history=lambda prompt_id:{})
        with patch('time.time',return_value=self.queue.read('operation-1')['created']+40):self.queue.status('operation-1')
        self.assertEqual(self.queue.recovery('operation-1',q)['recovery']['location'],'submitting')
        with self.assertRaisesRegex(ValueError,'待提交'):self.queue.abandon('operation-1',q)
        self.assertEqual(self.queue.cancel('operation-1',q,lambda:None)['state'],'settled')
        restarted=type(self.queue)(self.service)
        self.assertEqual(restarted.abandon('operation-1',q)['state'],'abandoned')
        self.assertTrue(restarted.read('operation-1')['queue_terminal'])

    def test_acknowledged_but_missing_task_cancel_retires_without_interrupt(self):
        own=self.queue_job();interrupted=[]
        q=SimpleNamespace(mutex=threading.RLock(),currently_running={},queue=[],get_history=lambda prompt_id:{})
        self.assertEqual(self.queue.recovery('operation-1',q)['recovery']['location'],'missing')
        self.assertEqual(self.queue.cancel('operation-1',q,lambda:interrupted.append(True))['state'],'abandoned')
        self.assertFalse(interrupted)
        self.assertFalse(self.queue.occupied([],[],lambda key:{}))

    def test_terminal_history_cancel_keeps_result_for_desktop_reconciliation(self):
        own=self.queue_job();entry=dict(prompt=own,status=dict(completed=True,status_str='success'))
        q=SimpleNamespace(mutex=threading.RLock(),currently_running={},queue=[],get_history=lambda prompt_id:{prompt_id:entry})
        result=self.queue.cancel('operation-1',q,lambda:None)
        self.assertEqual(result['state'],'settled');self.assertEqual(result['recovery']['location'],'history')
        self.assertNotIn('abandoned',self.queue.read('operation-1'))

    def test_expired_copy_does_not_keep_ownership_of_native_button(self):
        from unittest.mock import patch
        import time
        entry=dict(owner='old',head='A',workflow='flow',frontend_id='native-A',path='folder/A.json',paused=True)
        self.queue.inputs.publish(dict(client='old-copy',session='desktop',entries=[entry]))
        with patch('time.time',return_value=time.time()+9):
            self.assertFalse(self.queue.inputs.claim(dict(id='manual',identity=self.live['identity']))['handled'])
        self.assertFalse(self.queue.inputs.claim(dict(id='still-live',identity=self.live['identity']))['handled'])

    def test_expired_transport_does_not_veto_new_click_or_accept_late_old_payload(self):
        payload=self.prepared()
        old=self.queue.read('operation-1');old['created']-=60;self.queue.save(old)
        self.queue.poll(self.live)
        fresh=self.queue.start(dict(self.request,id='operation-2'))
        self.assertEqual(fresh['state'],'pending')
        self.assertEqual(self.queue.read('operation-1')['state'],'unconfirmed')
        self.service.prepare_prompt(payload)
        self.assertEqual(payload['prompt'],{})


if __name__=='__main__':unittest.main()
