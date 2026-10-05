import copy
import threading
import unittest
from types import SimpleNamespace as NS
import test_native_queue as native_fixture
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.snapshots import make_snapshot


class InputNativeTests(unittest.TestCase):
    setUp=native_fixture.NativeQueueTests.setUp
    prepared=native_fixture.NativeQueueTests.prepared

    def task(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        prompt=payload['prompt_id'];self.queue.reply(dict(self.live,id='operation-1',prompt_id=prompt))
        return (1,prompt,payload['prompt'],payload['extra_data'])

    def test_pending_cancellation_cannot_clear_foreign_tasks(self):
        task=self.task();foreign=(2,'manual',{},{});q=NS(mutex=threading.RLock(),queue=[task,foreign],currently_running={})
        calls=[];result=self.queue.cancel('operation-1',q,lambda:calls.append(True))
        self.assertEqual(q.queue,[foreign]);self.assertEqual(result['state'],'failed');self.assertEqual(calls,[])

    def test_running_cancellation_and_late_repeat_never_interrupt_next(self):
        task=self.task();q=NS(mutex=threading.RLock(),queue=[],currently_running={0:task});calls=[]
        self.assertEqual(self.queue.cancel('operation-1',q,lambda:calls.append(True))['state'],'cancelling')
        q.currently_running={1:(2,'manual',{}, {})}
        self.assertEqual(self.queue.cancel('operation-1',q,lambda:calls.append(True))['state'],'settled');self.assertEqual(calls,[True])

    def test_matching_prompt_id_without_ownership_proof_is_not_cancelled(self):
        task=self.task();foreign=(task[0],task[1],task[2],{});q=NS(mutex=threading.RLock(),queue=[foreign],currently_running={})
        with self.assertRaisesRegex(ValueError,'歸屬'):self.queue.cancel('operation-1',q,lambda:self.fail('foreign interrupted'))
        self.assertEqual(q.queue,[foreign])

    def test_cancel_before_dispatch_refuses_late_native_prepare(self):
        self.queue.start(self.request);command=self.queue.poll(self.live)['commands'][0]
        q=NS(mutex=threading.RLock(),queue=[],currently_running={})
        self.queue.cancel('operation-1',q,lambda:self.fail())
        with self.assertRaisesRegex(ValueError,'結束'):self.queue.prepare(dict(command,session='tab',client_id='real-web-sid'))

    def test_image_only_and_multiple_image_inputs_use_native_payload_and_metadata(self):
        state=prepare_state(self.snapshot['state'],multi=True);data=state['multi_output']
        data['bindings']=[];data['clip_inputs']={};data['connections']=[c for c in data['connections'] if c['kind']!='clip']
        profile=state['generation']['profiles'][0]
        for node in ('20','21'):profile['graph'][node]=dict(class_type='LoadImage',inputs={'image':'old.png'})
        data['image_inputs']={'input':dict(name='Image',workflow='flow',node='20')}
        self.request.update(snapshot=make_snapshot(state),images=[dict(node='20',image='input/A.png'),dict(node='21',image='input/B.png')])
        self.live['bindings_protocol']=1;self.queue.poll(self.live);self.queue.start(self.request)
        command=self.queue.poll(self.live)['commands'][0];self.assertEqual(command['texts'],[])
        graph=copy.deepcopy(profile['graph'])
        for image in command['images']:graph[image['node']]['inputs']['image']=image['text']
        self.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=graph,workflow=dict(id='native-A',nodes=[])))
        payload=dict(prompt=graph,client_id='real-web-sid',extra_data=dict(extra_pnginfo=dict(workflow=dict(extra=dict(pcs_native_operation=command['id'])))))
        self.service.prepare_prompt(payload)
        self.assertEqual(payload['prompt'],graph);self.assertIn('prompt_studio',payload['extra_data']['extra_pnginfo'])
        self.assertEqual(payload['extra_data']['extra_pnginfo']['prompt_studio']['texts'],[])


if __name__=='__main__':unittest.main()
