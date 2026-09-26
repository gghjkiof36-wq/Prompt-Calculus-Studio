import copy,os,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QObject,QEvent,QPointF
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio import multi_output as model
from prompt_studio.generation import active_profile,submission,validate_profile
from prompt_studio.workflow_import import infer_profile
from prompt_studio.workflow_transfer import import_transfer
from prompt_studio.snapshots import make_snapshot
from test_generation import workflow

APP=QApplication.instance() or QApplication([])

class ExecutionInputTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.env.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(20)
        self.c=self.w.canvas; self.oid=self.c.data()['current_output']; self.cid=next(iter(self.c.containers))
        self.file=Path(self.temp.name)/'source.png'; image=QImage(64,48,QImage.Format.Format_RGB32); image.fill(0xff4488aa); image.save(str(self.file))
        self.w.comfy.connected=True; self.w.comfy.direct_supported=True
        self.calls=[]
        self.transport=patch.object(self.w.comfy,'request',side_effect=lambda route,data=None,done=None,failed=None,**kw:self.calls.append((route,copy.deepcopy(data),done,kw)))
        self.transport.start()
    def tearDown(self):
        self.w.comfy.generation.batch=None; self.w.comfy.run_id=''; self.transport.stop()
        self.w.close(); APP.processEvents(); self.env.stop(); self.temp.cleanup()
    def configure(self,mode='img2img',category=None):
        profile=workflow(mode); profile['multi_text']=True
        if category: profile['mode']=category
        self.w.generation_panel.save_profile(profile); model.bind(self.w.state,profile['id'],self.oid,'2','text')
        self.w.comfy.stateChanged.emit(); return active_profile(self.w.state)
    def execute(self):
        self.calls.clear(); self.w.comfy.generation.run(1); return self.calls
    def test_text_only_preserves_image_workflow_and_original_image(self):
        profile=self.configure(); self.w.generation_panel.set_source(str(self.file))
        edge=next(c for c in self.c.data()['connections'] if c['kind']=='image'); model.disconnect(self.w.state,edge['id'])
        calls=self.execute(); self.assertEqual(calls[0][0],'/prompt')
        payload=calls[0][1]; self.assertEqual(payload['prompt']['4']['inputs']['image'],'old.png')
        self.assertIsNone(payload['extra_data']['extra_pnginfo']['prompt_studio_request']['snapshot']['state']['generation']['source'])
        self.assertNotIn('source',payload['extra_data']['extra_pnginfo']['prompt_studio_request']['generation'])
        self.assertEqual(active_profile(self.w.state),profile)
    def test_image_without_binding_reports_error_before_network(self):
        profile=self.configure('txt2img'); self.w.generation_panel.set_source(str(self.file))
        self.assertEqual(active_profile(self.w.state),profile)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError,'圖片無法載入工作流'): self.w.comfy.generation.run(1)
        self.assertEqual(self.calls,[]); self.assertIsNone(self.w.comfy.generation.batch)
    def test_image_binding_accepts_input_regardless_of_category_and_freezes_it(self):
        profile=self.configure(category='txt2img'); self.w.generation_panel.set_source(str(self.file))
        calls=self.execute(); self.assertEqual(calls[0][0],'/upload/image')
        edge=next(c for c in self.c.data()['connections'] if c['kind']=='image'); model.disconnect(self.w.state,edge['id'])
        profile['values']['denoise']=.9
        calls[0][2](dict(name='frozen.png',subfolder='prompt_studio',type='input'))
        payload=self.calls[1][1]; self.assertEqual(payload['prompt']['4']['inputs']['image'],'prompt_studio/frozen.png')
        self.assertEqual(payload['prompt']['6']['inputs']['denoise'],.65)
    def test_canvas_input_without_outgoing_image_only_sends_text(self):
        self.configure(); key=self.c.functions.add_image(path=str(self.file))
        model.connect(self.w.state,key,self.cid,'image'); self.assertEqual(self.execute()[0][0],'/prompt')
    def test_broken_connected_image_does_not_silently_use_original(self):
        self.configure(); key=self.c.functions.add_image(attached=True)
        self.calls.clear()
        with self.assertRaisesRegex(ValueError,'沒有圖片'): self.w.comfy.generation.run(1)
        self.assertEqual(self.calls,[])
    def test_image_binding_can_be_unset_without_rejecting_text(self):
        profile=self.configure(); profile['image']=''; validate_profile(profile)
        self.assertEqual(self.execute()[0][0],'/prompt')
    def test_explicit_image_binding_is_independent_of_selected_sampler_branch(self):
        profile=self.configure('txt2img'); profile['image']='9'
        profile['graph']['9']=dict(class_type='LoadImage',inputs=dict(image='another-branch.png'))
        self.w.generation_panel.set_source(str(self.file)); self.execute()
        self.calls[0][2](dict(name='input.png',subfolder='prompt_studio',type='input'))
        self.assertEqual(self.calls[1][1]['prompt']['9']['inputs']['image'],'prompt_studio/input.png')
    def test_reused_source_is_near_target_and_replace_is_undoable(self):
        profile=self.configure('txt2img'); before=copy.deepcopy(profile)
        self.c.commit(lambda s:s.setdefault('text_positions',{}).__setitem__(self.oid,[7000,-4000]))
        self.w.use_image_for_generation(str(self.file),self.oid)
        edge=next(c for c in self.c.data()['connections'] if c['kind']=='image'); key=edge['source']; card=self.c.functions.cards[key]
        a=card.sceneBoundingRect(); b=self.c.outputs[self.oid].sceneBoundingRect()
        self.assertLess((a.center()-b.center()).manhattanLength(),b.width()+b.height()+500)
        self.assertFalse(card.panel.attach_button.isVisible()); self.assertIn(key,self.w.state['text_positions'])
        self.assertEqual(active_profile(self.w.state),before)
        other=Path(self.temp.name)/'other.png'; im=QImage(64,48,QImage.Format.Format_RGB32); im.fill(0xffaa8844); im.save(str(other))
        old=copy.deepcopy(self.c.functions.data()['images'][key]); self.w.use_image_for_generation(str(other),self.oid)
        self.assertEqual(len(self.c.functions.cards),1); self.c.restore_history(self.c.undo_stack,self.c.redo_stack,False)
        self.assertEqual(self.c.functions.data()['images'][key],old)
        self.w.persist(); self.assertEqual(self.w.store.load()['text_positions'][key],self.w.state['text_positions'][key])
    def test_count_changes_never_reveal_activity_or_resize_run_button(self):
        self.configure('txt2img'); self.w.comfy.stateChanged.emit(); QTest.qWait(30)
        bar=self.w.run_controls; events=[]
        class Watch(QObject):
            def eventFilter(self,obj,event):
                if event.type()==QEvent.Type.Show: events.append('show')
                return False
        watcher=Watch(); bar.activity.installEventFilter(watcher)
        sizes=[]
        for value in (1,2,10,99,100,1,3):
            bar.count.setValue(value); bar.refresh(); QTest.qWait(10); sizes.append((bar.run_button.width(),bar.run_button.height()))
        self.assertEqual(len(set(sizes)),1,sizes); self.assertEqual(events,[])

class ImportedSeedTests(unittest.TestCase):
    def test_api_default_random_and_saved_workflow_policy_survives_transfer(self):
        from test_multi_output import workspace
        state,cid,oid=workspace(); graph=workflow('txt2img')['graph']
        self.assertEqual(infer_profile(graph,'api')['seed_mode'],'random')
        for stored,expected in [('fixed','fixed'),('randomize','random'),('increment','increment'),('decrement','decrement')]:
            ui=dict(nodes=[dict(id=6,type='KSampler',widgets_values=[123,stored,28,2.,'euler','normal',1.])])
            value=dict(version=1,id='incoming',name='incoming',graph=graph,workflow=ui,bindings=[])
            profile,bindings=import_transfer(state,value); self.assertEqual(profile['seed_mode'],expected)
    def test_fixed_seed_is_explicit_and_random_changes_between_submissions(self):
        from test_multi_output import workspace
        state,cid,oid=workspace(); profile=state['generation']['profiles'][0]; snap=make_snapshot(state)
        profile['seed_mode']='random'
        with patch('prompt_studio.generation.secrets.randbits',side_effect=[111,222]):
            self.assertEqual([submission(profile,snap)['prompt']['3']['inputs']['seed'] for _ in range(2)],[111,222])
        profile['seed_mode']='fixed'; self.assertEqual(submission(profile,snap)['prompt']['3']['inputs']['seed'],2)

if __name__=='__main__': unittest.main()
