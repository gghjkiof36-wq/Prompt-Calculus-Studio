"""Offline A/B submission protocol and the single workflow order seen by users."""
import copy
from pathlib import Path
from unittest.mock import patch
import unittest
from PySide6.QtCore import QPointF,Qt
from PySide6.QtWidgets import QPlainTextEdit
import test_multi_canvas as canvas_tests
from test_multi_canvas import APP
from test_multi_output import workflow
from test_comfy_integration import png
from prompt_studio import multi_output as model,clip_flow,workflow_flow
from prompt_studio.core import validate_state


class WorkflowTests(unittest.TestCase):
    setUp=canvas_tests.MultiCanvasTests.setUp
    tearDown=canvas_tests.MultiCanvasTests.tearDown
    add=canvas_tests.MultiCanvasTests.add

    def prepare(self):
        a=workflow('A'); a['graph']['9']=dict(class_type='SaveImage',inputs={})
        b=workflow('B'); b.update(mode='img2img',image='10')
        b['graph']['10']=dict(class_type='LoadImage',inputs=dict(image='unchanged.png'))
        self.w.generation_panel.save_profile(a); self.w.generation_panel.save_profile(b)
        self.add('A frozen'); self.ca=next(iter(self.canvas.clips))
        self.canvas.commit(lambda s:clip_flow.set_binding(s,'A',self.ca,('6','text')))
        negative=self.canvas.add_clip(QPointF(1600,0),self.oid)
        self.canvas.commit(lambda s:clip_flow.set_binding(s,'A',negative,('7','text')))
        cid=self.canvas.add_canvas(QPointF(0,900)); self.ob=self.canvas.add_output(QPointF(1000,900)); self.add('B frozen',cid)
        self.canvas.commit(lambda s:model.connect(s,cid,self.ob,'text'))
        self.cb=self.canvas.add_clip(QPointF(1600,900),self.ob)
        self.canvas.commit(lambda s:clip_flow.set_binding(s,'B',self.cb,('6','text')))
        self.image=self.canvas.functions.add_image(position=QPointF(-500,900))
        self.canvas.commit(lambda s:workflow_flow.bind_image(s,self.image,'A','9'))
        self.canvas.commit(lambda s:model.connect(s,self.image,self.ob,'image'))
        self.canvas.outputs[self.oid].panel.editor.setPlainText('A frozen')
        self.canvas.outputs[self.ob].panel.editor.setPlainText('B frozen')
        self.client=self.w.comfy; self.runner=self.client.generation
        self.client.connected=True; self.client.direct_supported=True; self.client.images_supported=True; self.client.flow_supported=True
        self.requests=[]
        def request(route,data=None,done=None,failed=None,**kw):
            self.requests.append(dict(route=route,data=copy.deepcopy(data),done=done,failed=failed,**kw))
        self.transport=patch.object(self.client,'request',request); self.transport.start(); self.addCleanup(self.transport.stop)
        self.source=Path(self.tmp.name)/'upstream.png'; png(self.source,{})

    def take(self,route):
        value=next(r for r in self.requests if r['route']==route); self.requests.remove(value); return value

    def complete(self,ident,error=False):
        self.runner.observe(dict(running_ids=[],queued_ids=[]))
        self.take('/history/'+ident)['done']({ident:dict(status=dict(completed=True,status_str='error' if error else 'success'),outputs={'9':dict(images=[dict(filename='upstream.png',type='output',subfolder='')])})})

    def resolve_image(self,upstream):
        request=self.take('desktop/images'); self.assertEqual(request['data']['queries'][0]['prompt_id'],upstream)
        request['done']({self.image:dict(path=str(self.source),size=self.source.stat().st_size,mtime=self.source.stat().st_mtime_ns,origin='result')})
        upload=self.take('/upload/image'); self.assertIn(self.source.read_bytes(),upload['raw'])
        upload['done'](dict(name='frozen.png',subfolder='prompt_studio',type='input'))

    def test_two_rounds_deduplicate_clips_wait_for_actual_completion_and_freeze_ui(self):
        self.prepare(); self.runner.run(2); submitted=[]
        a=self.take('/prompt'); submitted.append(a['data'])
        self.assertEqual(a['data']['prompt']['6']['inputs']['text'],'A frozen')
        self.assertEqual(a['data']['prompt']['7']['inputs']['text'],'A frozen')
        a['done'](dict(prompt_id='a1')); self.assertFalse(self.requests)
        self.canvas.outputs[self.ob].panel.editor.setPlainText('later B edit')
        self.canvas.commit(lambda s:workflow_flow.set_position(s,'B',1))
        self.runner.observe(dict(running_ids=['a1'],queued_ids=[],executing_node='3'))
        self.assertFalse(self.requests); self.assertIn(self.ca,self.canvas.active_keys)
        self.assertTrue(self.canvas.execution_bar.progress.isVisible())
        self.complete('a1'); self.resolve_image('a1')
        b=self.take('/prompt'); submitted.append(b['data'])
        self.assertEqual(b['data']['prompt']['6']['inputs']['text'],'B frozen')
        self.assertEqual(b['data']['prompt']['7']['inputs']['text'],'original negative')
        self.assertEqual(b['data']['prompt']['10']['inputs']['image'],'prompt_studio/frozen.png')
        b['done'](dict(prompt_id='b1')); self.complete('b1')
        a=self.take('/prompt'); submitted.append(a['data']); a['done'](dict(prompt_id='a2'))
        self.complete('a2'); self.resolve_image('a2')
        b=self.take('/prompt'); submitted.append(b['data']); b['done'](dict(prompt_id='b2')); self.complete('b2')
        markers=[p['extra_data']['extra_pnginfo']['prompt_studio_request']['generation'] for p in submitted]
        self.assertEqual([m['workflow_id'] for m in markers],['A','B','A','B'])
        self.assertEqual([m['round'] for m in markers],[1,1,2,2])
        self.assertTrue(all(m['workflow_order']==['A','B'] for m in markers))
        self.assertIsNone(self.runner.batch); self.assertFalse(self.requests)
        self.assertFalse(self.canvas.active_keys); self.assertFalse(self.canvas.execution_bar.progress.isVisible())
        self.assertEqual(model.compile_output(self.w.state,self.ob)['final_prompt'],'later B edit')
        from test_comfy_integration import Service
        service=Service(Path(self.tmp.name)/'service',self.tmp.name,self.tmp.name)
        for payload in submitted:
            service.prepare_prompt(payload)
            self.assertFalse(payload['extra_data']['extra_pnginfo']['prompt_studio']['problems'])

    def test_cancel_during_lookup_rejects_late_image_and_never_submits_b(self):
        self.prepare(); self.runner.run(1); self.take('/prompt')['done'](dict(prompt_id='a'))
        self.complete('a'); lookup=self.take('desktop/images')
        self.runner.finish_batch('cancelled')
        before=set(Path(self.tmp.name).rglob('*'))
        lookup['done']({self.image:dict(path=str(self.source),size=1,mtime=1)})
        self.assertFalse(self.requests); self.assertEqual(set(Path(self.tmp.name).rglob('*')),before)

    def test_failure_stops_later_workflows_and_missing_target_stops_before_a(self):
        self.prepare(); self.runner.run(1); self.take('/prompt')['done'](dict(prompt_id='a'))
        self.complete('a',True); self.assertFalse(self.requests); self.assertIsNone(self.runner.batch)
        self.w.state['generation']['profiles'][1].pop('image')
        with self.assertRaisesRegex(ValueError,'LoadImage'):self.runner.run(1)
        self.assertFalse(self.requests); self.assertIsNone(self.runner.pipeline)

    def test_order_card_and_clip_control_share_order_persist_and_undo(self):
        self.prepare(); self.canvas.show_workflow_order(QPointF(900,-500))
        panel=self.canvas.order_card.panel
        self.assertEqual(workflow_flow.workflow_ids(self.w.state),['A','B'])
        self.assertFalse(panel.findChildren(QPlainTextEdit))
        self.assertFalse(any(p.key==workflow_flow.ORDER_CARD for p in self.canvas.ports.values()))
        self.canvas.clips[self.cb].panel.order.setValue(1)
        self.assertEqual(workflow_flow.workflow_ids(self.w.state),['B','A'])
        self.assertEqual(panel.list.item(0).data(Qt.ItemDataRole.UserRole),'B')
        self.assertEqual(self.canvas.clips[self.ca].panel.order.value(),2)
        order=self.canvas.clips[self.ca].panel.order
        APP.processEvents(); self.assertGreaterEqual(order.lineEdit().width(),order.fontMetrics().horizontalAdvance('50'))
        self.canvas.undo(); self.assertEqual(workflow_flow.workflow_ids(self.w.state),['A','B'])
        self.canvas.redo(); expected=copy.deepcopy(self.canvas.data()); self.w.persist()
        self.w.close(); APP.processEvents()
        from prompt_studio.window import Window
        self.w=Window(self.tmp.name); self.assertEqual(self.w.state['multi_output'],expected)

    def test_image_binding_is_optional_display_switch_keeps_preview_and_manual_source(self):
        self.prepare()
        source=self.w.generation_panel.import_source(self.source)
        def manual(s):
            workflow_flow.bind_image(s,self.image,None,None)
            s['canvas_functions']['images'][self.image].update(source=source,show_image=False)
            model.connect(s,self.image,model.PREVIEW,'image')
        self.canvas.commit(manual)
        self.assertFalse(self.canvas.functions.cards[self.image].panel.preview.isVisible())
        self.assertEqual(self.canvas.results.input_record['path'],str(Path(self.tmp.name)/source['relative']))
        self.canvas.commit(lambda s:workflow_flow.bind_image(s,self.image,'A','9'))
        self.assertEqual(self.w.state['canvas_functions']['images'][self.image]['source'],source)
        self.canvas.undo(); self.assertEqual(self.canvas.functions.current_source(self.image),source)
        invalid=copy.deepcopy(self.w.state); invalid['multi_output']['clip_inputs']=[]
        with self.assertRaises(ValueError):validate_state(invalid)

    def test_only_connected_clip_workflows_execute_image_binding_does_not_add_a(self):
        self.prepare()
        lines=[l['id'] for l in self.canvas.data()['connections'] if l['kind']=='clip' and l['destination']!=self.cb]
        self.canvas.commit(lambda s:[model.disconnect(s,key) for key in lines])
        self.assertEqual([p['id'] for p in workflow_flow.execution_profiles(self.w.state)],['B'])
        self.runner.run(1)
        lookup=self.take('desktop/images'); self.assertIsNone(lookup['data']['queries'][0]['prompt_id'])
        self.assertFalse(any(r['route']=='/prompt' for r in self.requests)); self.runner.finish_batch()
