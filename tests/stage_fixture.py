"""Synthetic native receipts/history, managed PNGs and actual Qt Execute events.

No test fabricates a completed desktop job or invokes ChainRunner.collect as a
completion shortcut. The fake executor replaces ComfyUI/GPU only.
"""
import copy
import tempfile
import unittest
from pathlib import Path
from urllib.parse import unquote
from unittest.mock import patch
from PySide6.QtCore import Qt,QTimer,QPoint,QPointF
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton
from prompt_studio.window import Window
from prompt_studio import multi_output as model,clip_flow
from prompt_studio.composition import node
from test_multi_output import workflow
from test_083_runtime_repair import ReceiptExecutor
from test_comfy_integration import png

APP=QApplication.instance() or QApplication([])


def pure_profile(name):
    return dict(id=name,name=name,frontend_id='native-'+name,mode='img2img',multi_text=True,values={},graph={
        '10':dict(class_type='LoadImage',inputs=dict(image='native.png')),
        '11':dict(class_type='ImageScale',inputs=dict(image=['10',0],width=128,height=128,upscale_method='nearest-exact',crop='disabled')),
        '9':dict(class_type='PreviewImage',inputs=dict(images=['11',0]))})


class ChainExecutor(ReceiptExecutor):
    def __init__(self,window,root):
        root.mkdir(parents=True,exist_ok=True);self.root=root
        super().__init__(window,root)
        self.graphs={p['id']:copy.deepcopy(p['graph']) for p in window.state['generation']['profiles']}
        self.identities={p['id']:dict(workflow=p['id'],path=p.get('origin',{}).get('path',''),frontend_id=p['frontend_id']) for p in window.state['generation']['profiles']}
        self.live['workflows']=list(self.identities.values());self.results={};self.result_failure=False;self.cancelled=[]
        self.native.poll(self.live)

    def request(self,route,data=None,done=None,failed=None,**kwargs):
        if route not in ('workflow/native/start','workflow/native/cancel') and not route.startswith('desktop/results?'):
            return super().request(route,data,done,failed,**kwargs)
        try:
            if route=='workflow/native/start':
                self.native.poll(self.live);self.native.start(data)
                command=self.native.poll(self.live)['commands'][0];target=self.identities[data['workflow']]
                if self.live['identity']!=target:
                    self.native.activate(dict(command,session=self.live['session'],client_id=self.live['client_id'],
                                             opened_identity=target,opened_epoch=self.live['epoch']+1))
                    self.live.update(identity=copy.deepcopy(target),epoch=self.live['epoch']+1)
                graph=copy.deepcopy(self.graphs[data['workflow']])
                for binding in command.get('texts',[]):
                    if binding.get('text_source')!='web':graph[binding['node']]['inputs'][binding['field']]=binding['text']
                for binding in command.get('images',[]):graph[binding['node']]['inputs']['image']=binding['text']
                for key,value in (command.get('replay') or {}).items():
                    for field in ('seed','noise_seed'):
                        if field in value['inputs']:graph[key]['inputs'][field]=value['inputs'][field]
                visual=dict(id=target['frontend_id'],nodes=[])
                self.native.prepare(dict(command,session=self.live['session'],client_id=self.live['client_id'],output=graph,workflow=visual))
                visual['extra']=dict(pcs_native_operation=command['id'])
                payload=dict(prompt=graph,client_id=self.live['client_id'],extra_data=dict(extra_pnginfo=dict(workflow=visual)))
                self.service.prepare_prompt(payload);assert payload['prompt'],payload
                self.native.reply(dict(command,session=self.live['session'],prompt_id=payload['prompt_id']))
                self.pending[payload['prompt_id']]=payload;self.submissions.append(copy.deepcopy(payload))
                self.client.running=len(self.pending);result=self.native.status(command['id'])
            elif route=='workflow/native/cancel':
                self.cancelled.append(data['id']);result=self.native.status(data['id'])
                prompt=result.get('prompt_id')
                if prompt in self.pending:self.finish(ident=prompt,error=True)
            else:
                if self.result_failure:raise ValueError('fixture result unavailable')
                result=self.results.get(unquote(route.split('prompt_id=')[1]),[])
            if done:done(result)
        except (ValueError,AssertionError) as exc:
            if failed:failed(str(exc))
            else:raise

    def finish(self,count=1,*,ident=None,error=False,missing=False,extra=False,duplicate=False,nodes=None):
        ident=ident or next(iter(self.pending));payload=self.pending.pop(ident)
        rows=[];outputs={};nodes=nodes or ['9']
        for node in nodes:
            images=[]
            for i in range(count):
                name=f'{ident}-{node}-{i}.png';path=self.root/name
                png(path,dict(prompt=payload['prompt'],**payload['extra_data']['extra_pnginfo']))
                if not duplicate: # Distinct metadata, identical pixels; order is logical.
                    png(path,dict(prompt=payload['prompt'],position=i,output_node=node,**payload['extra_data']['extra_pnginfo']))
                image=dict(filename=name,subfolder='',type='temp');images.append(image)
                rows.append(dict(prompt_id=ident,node_id=node,image=image,path=str(path)))
            if not missing:outputs[node]=dict(images=images)
        if extra:outputs['99']=dict(images=[dict(filename='foreign.png',subfolder='',type='temp')])
        self.results[ident]=list(reversed(rows))
        self.history[ident]=dict(prompt=[0,ident,copy.deepcopy(payload['prompt']),payload['extra_data'],nodes],outputs=outputs,
            status=dict(completed=True,status_str='error' if error else 'success',messages=[]))
        self.tick()

    def tick(self):
        self.client.running=len(self.pending);status=dict(running_ids=list(self.pending),queued_ids=[])
        self.native.occupied([],[],lambda key:{key:self.history[key]} if key in self.history else {})
        self.client.generation.observe(status);self.client.input_flow.observe(status);QTest.qWait(30)


class StageFixture(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        env=patch.dict('os.environ',{'PROMPT_STUDIO_V08':'1'});env.start();self.addCleanup(env.stop)
        self.w=Window(self.tmp.name);self.w.comfy.timer.stop();self.w.comfy.enabled=False
        self.w.state['settings'].update(online=False,material='solid');self.notices=[];self.w.notice=self.notices.append;self.w.error=self.notices.append
        self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(10)
        self.c=self.w.canvas;self.cid=next(iter(self.c.data()['canvases']));self.out=self.c.data()['current_output']
        profile=workflow();profile['frontend_id']='native-A';profile['graph']['9']=dict(class_type='PreviewImage',inputs=dict(images=['5',0]))
        for p in (profile,pure_profile('B'),pure_profile('C')):self.w.generation_panel.save_profile(p)
        self.clip=next(iter(self.c.data()['clip_inputs']))
        def setup(s):
            s['uses']['root']=node('A prompt','white shirt');model.assign(s,'root',self.cid)
            clip_flow.set_binding(s,'flow',self.clip,('6','text'))
        self.c.commit(setup)
        self.executor=ChainExecutor(self.w,Path(self.tmp.name)/'executor');self.runner=self.w.comfy.input_flow.chain
        self.controls=self.c.execution_bar.controls

    def tearDown(self):self.w.close();APP.processEvents()

    def click(self):
        self.controls.refresh();self.assertTrue(self.controls.run_button.isEnabled())
        QTest.mouseClick(self.controls.run_button,Qt.MouseButton.LeftButton);QTest.qWait(25)

    def workflows(self):return [p['extra_data']['extra_pnginfo']['prompt_studio']['generation']['workflow_id'] for p in self.executor.submissions]
