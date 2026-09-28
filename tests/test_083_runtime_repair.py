"""UI -> real submission journal -> backend receipt -> queue/history completion.

The ComfyUI executor is a local fixture; no GPU or user data is used. Tests never
set a desktop job's terminal state or call InputRunner.finished directly.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_studio.window import Window
from prompt_studio import multi_output as model,clip_flow
from prompt_studio.composition import node
from prompt_studio.flow_data import add_scheduler,endpoint
from test_comfy_integration import Service
from test_multi_output import workflow

APP=QApplication.instance() or QApplication([])


class ReceiptExecutor:
    def __init__(self,window,root):
        self.w=window;self.client=window.comfy;self.service=Service(root/'service',root,root)
        self.native=self.service.native_queue;self.pending={};self.history={};self.submissions=[]
        self.live=dict(session='fixture-tab',epoch=0,identity=dict(workflow='flow',path='',frontend_id='native-A'),
                       client_id='fixture-web',ready=True,bindings_protocol=1,navigation_protocol=1)
        self.native.poll(self.live)
        self.graph=copy.deepcopy(workflow()['graph']);self.graph['5']['inputs']['batch_size']=2
        self.graph['9']=dict(class_type='PreviewImage',inputs=dict(images=['5',0]))
        self.client.request=self.request
        self.client.connected=True;self.client.native_supported=True;self.client.flow_supported=True
        self.client.snapshot_versions=[4];self.client.direct_supported=True
        self.client.stateChanged.emit()

    def request(self,route,data=None,done=None,failed=None,**kwargs):
        try:
            if route=='workflow/native/start':
                self.native.poll(self.live)
                self.native.start(data)
                command=self.native.poll(self.live)['commands'][0]
                graph=copy.deepcopy(self.graph)
                for binding in command.get('texts',[]):
                    if binding.get('text_source')!='web':graph[binding['node']]['inputs'][binding['field']]=binding['text']
                for binding in command.get('images',[]):graph[binding['node']]['inputs']['image']=binding['text']
                visual=dict(id='native-A',nodes=[])
                self.native.prepare(dict(command,session='fixture-tab',client_id='fixture-web',output=graph,workflow=visual))
                visual['extra']=dict(pcs_native_operation=command['id'])
                payload=dict(prompt=graph,client_id='fixture-web',extra_data=dict(extra_pnginfo=dict(workflow=visual)))
                self.service.prepare_prompt(payload)
                assert payload['prompt'],payload
                for field in ('cfg','denoise'):payload['prompt']['3']['inputs'][field]=float(payload['prompt']['3']['inputs'][field])
                self.native.reply(dict(self.live,id=command['id'],prompt_id=payload['prompt_id']))
                self.pending[payload['prompt_id']]=payload;self.submissions.append(copy.deepcopy(payload))
                self.client.running=len(self.pending)
                result=self.native.status(command['id'])
            elif route.startswith('/history/'):result={k:v for k,v in self.history.items() if k==route.split('/')[-1]}
            elif route=='workflow/native/status':result=self.native.reconcile(data['id'],set(self.pending)|set(self.history))
            elif route=='workflow/inputs/publish':result=self.native.inputs.publish(data)
            elif route=='/upload/image':
                import re
                name=re.search(b'filename="([^"]+)"',kwargs['raw']).group(1).decode()
                result=dict(name=name,subfolder='fixture',type='input')
            elif route=='desktop/images':result={q['key']:dict(error='fixture has no image files') for q in data['queries']}
            else:raise AssertionError('Unexpected transport route '+route)
            if done:done(result)
        except (ValueError,AssertionError) as exc:
            if failed:failed(str(exc))
            else:raise

    def finish(self,*,mismatch=False,missing=False):
        assert self.pending,repr(self.w.notice.__self__)
        ident,payload=next(iter(self.pending.items()));self.pending.pop(ident)
        graph=copy.deepcopy(payload['prompt'])
        if mismatch:graph['3']['inputs']['seed']+=1
        self.history[ident]=dict(prompt=[0,ident,graph,payload['extra_data'],['9']],
            outputs={} if missing else {'9':dict(images=[dict(filename='one.png',subfolder='',type='temp'),dict(filename='two.png',subfolder='',type='temp')])},
            status=dict(completed=True,status_str='success',messages=[]))
        self.client.running=len(self.pending);status=dict(running_ids=list(self.pending),queued_ids=[])
        self.native.occupied([],[],lambda key:{key:self.history[key]} if key in self.history else {})
        self.client.generation.observe(status);self.client.input_flow.observe(status)
        QTest.qWait(15)


class RuntimeRepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict('os.environ',{'PROMPT_STUDIO_V08':'1'});env.start();self.addCleanup(env.stop)
        self.w=Window(self.tmp.name);self.w.comfy.timer.stop();self.w.comfy.enabled=False
        self.w.state['settings'].update(online=False,material='solid');self.notices=[];self.w.notice=self.notices.append
        self.w.error=self.notices.append
        self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(20)
        self.c=self.w.canvas;self.cid=next(iter(self.c.data()['canvases']));self.out=self.c.data()['current_output']
        profile=workflow();profile['frontend_id']='native-A'
        self.w.generation_panel.save_profile(profile);self.clip=next(iter(self.c.data()['clip_inputs']))
        def setup(state):
            state.setdefault('uses',{})['test-root']=node('test','A')
            model.assign(state,'test-root',self.cid);clip_flow.set_binding(state,'flow',self.clip,('6','text'))
        self.c.commit(setup)
        self.executor=ReceiptExecutor(self.w,Path(self.tmp.name)/'executor')
        self.controls=self.c.execution_bar.controls;self.controls.refresh()

    def tearDown(self):self.w.close();APP.processEvents()

    def click(self):
        self.controls.refresh();self.assertTrue(self.controls.run_button.isEnabled())
        QTest.mouseClick(self.controls.run_button,Qt.MouseButton.LeftButton);QTest.qWait(5)

    def text(self,value):self.c.commit(lambda s:s['uses']['test-root'].update(prompt=value))

    def test_three_clicks_submit_immediately_before_any_gpu_completion(self):
        self.click();self.text('B');self.click();self.text('C');self.click()
        self.assertEqual(len(self.executor.submissions),3,self.notices)
        self.assertEqual(len(self.executor.pending),3)
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['A','B','C'])
        for _ in range(3):self.executor.finish()
        self.assertTrue(all(j['state']=='complete' and j['output_count']==2 for j in self.w.comfy.generation.records()))
        self.assertFalse(self.w.comfy.generation.jobs);self.click();self.assertEqual(len(self.executor.submissions),4)
        self.executor.finish()

    def test_count_three_is_three_immediate_submissions_from_ui(self):
        self.controls.count.setValue(3);self.click();QTest.qWait(35)
        self.assertEqual(len(self.executor.submissions),3,self.notices)
        for _ in range(3):self.executor.finish()
        self.assertIsNone(self.w.comfy.input_flow.current)

    def test_terminal_mismatch_does_not_block_a_new_explicit_click(self):
        self.click();self.executor.finish(mismatch=True)
        record=self.w.comfy.generation.records()[0]
        self.assertEqual(record['state'],'failed');self.assertEqual(record['execution_state'],'complete')
        self.assertFalse(self.w.comfy.generation.jobs)
        self.click();self.assertEqual(len(self.executor.submissions),2);self.executor.finish()

    def test_missing_designated_output_is_terminal_error(self):
        self.click();self.executor.finish(missing=True)
        self.assertEqual(self.w.comfy.generation.records()[0]['state'],'failed')
        self.assertFalse(self.w.comfy.generation.jobs)

    def test_manual_claim_and_automatic_dispatch_share_item(self):
        created=[]
        def connect(state):
            sid=add_scheduler(state);created.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect);sid=created[0];runner=self.w.comfy.input_flow
        self.executor.client.running=1 # Foreign workflow owns the executor until it completes.
        self.click();self.text('B');self.click()
        self.executor.client.input_bridge_supported=True;runner.bridge.poll()
        claim=self.executor.native.inputs.claim(dict(id='manual-click',identity=self.executor.live['identity']))
        self.assertTrue(claim['handled']);runner.bridge.poll()
        self.assertEqual(len(self.executor.submissions),0)
        foreign=self.executor.native.inputs.claim(dict(id='foreign',identity=dict(workflow='another',frontend_id='other',path='')))
        self.assertFalse(foreign['handled'])
        self.executor.client.running=0;runner.observe(dict(running_ids=[],queued_ids=[]))
        self.executor.finish();self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['A','B'])
        self.assertEqual(len(runner.store.rows(sid,history=True)),2)
        self.assertIn('native',[j['entry_point'] for j in self.w.comfy.generation.records()])

    def test_workspace_switch_preserves_already_submitted_native_jobs(self):
        first=self.w.state['workspace'];self.click();self.click()
        self.assertEqual(len(self.executor.submissions),2)
        with patch('prompt_studio.window.QInputDialog.getText',return_value=('empty',True)):self.w.new_workspace()
        self.executor.finish();self.executor.finish()
        self.assertTrue(all(j['workspace']==first and j['state']=='complete' for j in self.w.comfy.generation.records()))
        self.w.workspace.setCurrentIndex(self.w.workspace.findData(first));QTest.qWait(10)
        self.assertEqual(len(self.executor.submissions),2)

    def test_manual_claim_is_idempotent_and_paused_head_cannot_fall_through(self):
        created=[]
        def connect(state):
            sid=add_scheduler(state);created.append(sid);port=endpoint(sid,'clip1')
            model.connect(state,self.out,port,'clip');model.connect(state,port,self.clip,'clip')
        self.c.commit(connect);sid=created[0];runner=self.w.comfy.input_flow
        self.executor.client.input_bridge_supported=True;self.executor.client.running=1
        self.click();self.text('B');self.click()
        for _ in range(3):self.executor.native.inputs.claim(dict(id='one-click',identity=self.executor.live['identity']))
        runner.bridge.poll();runner.bridge.poll();runner.pause(sid);runner.bridge.poll()
        self.assertFalse(self.executor.native.inputs.claim(dict(id='paused',identity=self.executor.live['identity']))['handled'])
        self.assertEqual(len(runner.store.rows(sid)),2)
        self.executor.client.running=0;runner.resume(sid);self.executor.finish();self.executor.finish()
        self.assertEqual(len(self.executor.submissions),2)

    def image_batch(self,count,schedule):
        from test_comfy_integration import png
        from prompt_studio.flow_data import add_image_input
        from prompt_studio.image_source import set_items
        files=[]
        for i in range(count):
            path=Path(self.tmp.name)/f'image{i+1}.png'
            png(path,{'prompt':{'s':dict(inputs={'positive':['p',0]}),'p':dict(inputs={'text':f'image {i+1}'})}});files.append(path)
        key=self.c.functions.add_image(files[0]);sources=[self.w.generation_panel.import_source(p) for p in files]
        created=[]
        def setup(state):
            set_items(state,key,sources,self.w.store.directory)
            state['generation']['profiles'][0]['graph']['10']=dict(class_type='LoadImage',inputs={'image':'old.png'})
            model.connect(state,key,self.cid,'clip');state['multi_output']['canvases'][self.cid]['members']=[]
            image=add_image_input(state);state['multi_output']['image_inputs'][image].update(workflow='flow',node='10')
            if schedule:
                sid=add_scheduler(state);created.append(sid)
                model.connect(state,self.out,endpoint(sid,'clip1'),'clip');model.connect(state,endpoint(sid,'clip1'),self.clip,'clip')
                model.connect(state,key,endpoint(sid,'image1'),'image');model.connect(state,endpoint(sid,'image1'),image,'image')
            else:model.connect(state,key,image,'image')
        self.c.commit(setup);self.executor.graph['10']=dict(class_type='LoadImage',inputs={'image':'old.png'})
        return key,sources,created[0] if created else None

    def test_twelve_image_buffer_refills_and_keeps_text_image_pairs(self):
        key,sources,sid=self.image_batch(12,True);self.click();runner=self.w.comfy.input_flow
        self.assertEqual(len(runner.store.rows(sid))+runner.direct_count(runner.store.scoped(sid)),10)
        self.click();self.assertEqual(len(runner.store.rows(sid))+runner.direct_count(runner.store.scoped(sid)),10)
        for _ in range(12):
            self.assertLessEqual(len(runner.store.rows(sid))+runner.direct_count(runner.store.scoped(sid)),10);self.executor.finish()
        payloads=self.executor.submissions;self.assertEqual(len(payloads),12,self.notices)
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in payloads],[f'image {i+1}' for i in range(12)])
        for source,payload in zip(sources,payloads):self.assertIn(source['sha256'],payload['prompt']['10']['inputs']['image'])
        self.assertEqual(len(runner.store.rows(sid)),0);self.assertEqual(len(runner.store.rows(sid,history=True)),11)

    def test_unscheduled_images_submit_each_click_without_gpu_wait(self):
        key,_,_=self.image_batch(3,False);self.click();self.click();self.click()
        self.c.outputs[self.out].panel.editor.setPlainText('manual next')
        self.executor.finish();self.executor.finish();self.executor.finish()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in self.executor.submissions],['image 1','image 2','image 3'])


if __name__=='__main__':unittest.main()
