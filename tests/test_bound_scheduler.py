"""Bound inputs retain their targets and values across queued native executions."""
import copy
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio import multi_output as model, stage_model, prompt_sources as supply
from prompt_calculus_studio.flow_data import add_scheduler
import test_prompt_supply as prior
from test_prompt_supply import definition


class BoundSchedulerTests(unittest.TestCase):
    def setUp(self):
        prior.SupplyTests.setUp(self)
        self.w.comfy.snapshot_versions.append(6)
    tearDown=prior.SupplyTests.tearDown
    stages=prior.SupplyTests.stages
    click=prior.SupplyTests.click
    workflows=prior.SupplyTests.workflows
    image_batch=prior.SupplyTests.image_batch
    group_source=prior.SupplyTests.group_source
    submitted_texts=prior.SupplyTests.submitted_texts

    def bound_text(self,counts=(2,1,3),batch=False):
        config=self.group_source(counts,batch,scheduler=False)
        stage=next(iter(self.c.data()['stages']))
        def setup(state):
            q=add_scheduler(state);config.update(scheduler=q,stage=stage)
            for edge in list(state['multi_output']['connections']):
                if edge['source']==self.clip and edge['destination']==stage:model.disconnect(state,edge['id'])
            model.connect(state,self.clip,q+'::bindings','control')
            model.connect(state,q+'::bindings',stage,'control')
        self.assertTrue(self.c.commit(setup),self.notices)
        return config

    def bound_images(self,count=3):
        image,items,config=self.image_batch(count=count,auto=False)
        terminal=next(k for k,v in self.c.data()['image_inputs'].items() if v['workflow']=='B')
        def setup(state):
            q=add_scheduler(state);config.update(scheduler=q,terminal=terminal)
            for edge in list(state['multi_output']['connections']):
                if edge['source']==terminal and edge['destination']==config['stage']:model.disconnect(state,edge['id'])
            model.connect(state,terminal,q+'::bindings','control')
            model.connect(state,q+'::bindings',config['stage'],'control')
        self.assertTrue(self.c.commit(setup),self.notices)
        return image,items,config

    def test_manual_repeats_freeze_bound_values_and_targets(self):
        config=self.bound_text()
        plan=stage_model.compile_plan(self.w.state);binding=plan['stages'][config['stage']]['bindings'][0]
        target=binding['target']
        self.assertEqual((binding['key'],target['workflow'],target['node'],target['field']),(self.clip,'flow','6','text'))
        self.assertEqual(binding['scheduler'],config['scheduler'])
        for _ in range(3):self.click()
        rows=self.runner.store.rows('entry')
        self.assertEqual([r['saved']['values'][self.clip]['value'] for r in rows],['prompt 0','prompt 0','prompt 1'],self.notices)
        for _ in range(3):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 0','prompt 1'],self.notices)
        self.assertFalse(self.executor.applied,'bound terminals must not also synchronize as free inputs')

    def test_batch_refill_and_cancel_keep_frozen_content(self):
        config=self.bound_text((11,1),batch=True);self.click()
        rows=self.runner.store.rows('entry',active=True);self.assertEqual(len(rows),10,self.notices)
        self.runner.remove_entry(rows[1]['id'])
        supply.definitions(self.w.state)[config['source']]['root']['sequence']['groups'][0]['text']='later edit'
        for _ in range(11):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0']*10+['prompt 1'],self.notices)
        self.assertEqual(self.runner.store.read(rows[1]['id'])['status'],'removed')
        self.assertIsNone(self.runner.current())

    def test_retry_keeps_same_round_and_content(self):
        config=self.bound_text((1,1));self.click();self.executor.finish(error=True)
        attempt=self.runner.store.rows('attempt')[0]
        self.executor.graphs['flow']['6']['inputs']['text']='changed native field'
        self.runner.retry(attempt['id']);QTest.qWait(30);self.executor.finish()
        self.click();self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 0','prompt 1'],self.notices)

    def test_bound_image_batch_preserves_each_image(self):
        image,items,config=self.bound_images();self.click()
        rows=self.runner.store.rows('entry')
        self.assertEqual([r['saved']['values'][config['terminal']]['value'] for r in rows],items,self.notices)
        for _ in range(3):self.executor.finish()
        self.assertEqual(len({r['prompt']['10']['inputs']['image'] for r in self.executor.submissions}),3,self.notices)
        self.assertEqual(self.w.state['canvas_functions']['images'][image]['index'],2)

    def test_mixed_bound_image_and_raw_text_zip_shortest(self):
        image,items,config=self.bound_images(5)
        def setup(state):
            source=supply.add(state,root=definition((2,1)));supply.definitions(state)[source]['supply']='batch'
            model.connect(state,source,config['scheduler']+'::clip1','clip')
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        rows=self.runner.store.rows('entry');self.assertEqual(len(rows),3,self.notices)
        self.assertEqual([r['saved']['values'][config['terminal']]['value'] for r in rows],items[:3])
        for _ in range(3):self.executor.finish()
        self.assertEqual(self.w.state['canvas_functions']['images'][image]['index'],3)

    def test_same_source_two_bindings_uses_one_round(self):
        from prompt_calculus_studio import clip_flow
        config=self.bound_text(batch=True);other=[]
        def setup(state):
            profile=next(p for p in state['generation']['profiles'] if p['id']=='flow')
            profile['graph']['7']=dict(class_type='CLIPTextEncode',inputs=dict(text='native negative'))
            self.executor.graphs['flow']['7']=copy.deepcopy(profile['graph']['7'])
            key=clip_flow.add(state);other.append(key);clip_flow.set_binding(state,'flow',key,('7','text'))
            model.connect(state,self.out,key,'clip');model.connect(state,key,config['scheduler']+'::bindings','control')
        self.assertTrue(self.c.commit(setup),self.notices)
        self.assertEqual(supply.scheduler_sources(self.w.state,config['scheduler']),[config['source']])
        self.click();self.assertEqual(len(self.runner.store.rows('entry')),6,self.notices)
        for _ in range(6):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 0','prompt 1']+['prompt 2']*3)
        self.assertEqual([p['prompt']['7']['inputs']['text'] for p in self.executor.submissions],self.submitted_texts())

    def test_bound_text_holds_bypass_image_for_batch_and_advances_once(self):
        from prompt_calculus_studio.clip_flow import add,set_binding
        image,items,config=self.image_batch(count=3,auto=False)
        def setup(state):
            source=supply.add(state,root=definition((3,)));supply.definitions(state)[source]['supply']='batch'
            profile=next(p for p in state['generation']['profiles'] if p['id']=='B')
            profile['graph']['6']=dict(class_type='CLIPTextEncode',inputs=dict(text='native B'))
            self.executor.graphs['B']['6']=copy.deepcopy(profile['graph']['6'])
            clip=add(state);set_binding(state,'B',clip,('6','text'));q=add_scheduler(state)
            model.connect(state,source,clip,'clip');model.connect(state,clip,q+'::bindings','control')
            model.connect(state,q+'::bindings',config['stage'],'control')
        self.assertTrue(self.c.commit(setup),self.notices);self.click()
        for _ in range(3):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0']*3,self.notices)
        self.assertEqual(len({p['prompt']['10']['inputs']['image'] for p in self.executor.submissions}),1)
        self.assertEqual(self.w.state['canvas_functions']['images'][image]['index'],1)

    def test_runner_reopen_resumes_frozen_bound_batch_without_recounting(self):
        from prompt_calculus_studio.stage_runner import StageRunner
        config=self.bound_text((2,1),batch=True);self.click();run=self.runner.current()
        entries=copy.deepcopy(self.runner.store.rows('entry'))
        self.runner.close();self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)
        self.executor.finish();self.assertEqual(self.runner.current()['status'],'paused')
        supply.definitions(self.w.state)[config['source']]['root']['sequence']['groups'][0]['text']='later edit'
        self.runner.resume(run['id']);QTest.qWait(30)
        for _ in range(2):self.executor.finish()
        self.assertEqual(self.submitted_texts(),['prompt 0','prompt 0','prompt 1'],self.notices)
        self.assertEqual([e['saved'] for e in self.runner.store.rows('entry')],[e['saved'] for e in entries])
        self.assertIsNone(self.runner.current())

    def test_real_qt_binding_ports_connect_and_undo(self):
        keys={}
        def setup(state):
            keys['stage']=stage_model.add(state,(1150,0));keys['scheduler']=add_scheduler(state,(650,0))
            state['text_positions'][self.clip]=[200,0]
        self.assertTrue(self.c.commit(setup),self.notices)
        view=self.c.view;view.resetTransform();view.scale(.6,.6);view.centerOn(900,200);QTest.qWait(20)
        def drag(first,last):
            a=view.mapFromScene(self.c.ports[first].scenePos());b=view.mapFromScene(self.c.ports[last].scenePos())
            QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a);QTest.mouseMove(view.viewport(),b,30)
            QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b);QTest.qWait(30)
        q=keys['scheduler']+'::bindings';stage=keys['stage']
        drag((self.clip,'control',True),(q,'control',False))
        drag((q,'control',True),(stage,'control',False))
        self.assertEqual(self.c.data()['stages'][stage]['workflow'],'flow',self.notices)
        self.assertEqual(stage_model.connected_terminals(self.w.state),{self.clip})
        self.assertEqual(self.w.state['version'],6)
        edge=next(e for e in self.c.data()['connections'] if e['destination']==q)
        self.assertIs(self.c.line_port(edge,False),self.c.ports[(q,'control',False,self.clip)])
        view.setFocus();QTest.keyClick(view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertEqual(stage_model.connected_terminals(self.w.state),set())
        QTest.keyClick(view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(20)
        self.assertEqual(stage_model.connected_terminals(self.w.state),{self.clip})

    def test_queued_target_change_pauses_without_rewriting_saved_entries(self):
        from prompt_calculus_studio import clip_flow
        config=self.bound_text((3,),batch=True)
        def extra_field(state):
            profile=next(p for p in state['generation']['profiles'] if p['id']=='flow')
            profile['graph']['7']=dict(class_type='CLIPTextEncode',inputs=dict(text='untouched'))
            self.executor.graphs['flow']['7']=copy.deepcopy(profile['graph']['7'])
        self.assertTrue(self.c.commit(extra_field));self.click()
        run=copy.deepcopy(self.runner.current());entries=copy.deepcopy(self.runner.store.rows('entry'))
        self.assertTrue(self.c.commit(lambda s:clip_flow.set_binding(s,'flow',self.clip,('7','text'))))
        self.executor.finish();QTest.qWait(25)
        self.assertEqual(len(self.executor.submissions),1,self.notices)
        current=self.runner.store.read(run['id'])
        self.assertEqual(current['plan'],run['plan'])
        self.assertEqual([e['saved'] for e in self.runner.store.rows('entry')],[e['saved'] for e in entries])
        self.assertEqual(current['status'],'paused')
        self.assertEqual(current['plan']['stages'][config['stage']]['bindings'][0]['target']['node'],'6')

    def test_new_schema_is_required_and_binding_state_survives_reopen(self):
        from prompt_calculus_studio.window import Window
        from prompt_calculus_studio.core import validate_state
        config=self.bound_text();original=stage_model.compile_plan(self.w.state)
        old=copy.deepcopy(self.w.state);old['version']=5
        with self.assertRaises(ValueError):validate_state(old)
        self.w.comfy.snapshot_versions=[1,2,3,4,5];self.assertFalse(self.w.comfy.can_run)
        self.w.persist();self.w.close();QTest.qWait(20)
        self.w=Window(self.tmp.name);self.w.comfy.timer.stop();self.w.comfy.enabled=False
        self.w.show();self.w.set_interface_mode('canvas');QTest.qWait(25);self.c=self.w.canvas
        self.assertEqual(stage_model.compile_plan(self.w.state),original)
        self.assertIn((config['scheduler']+'::bindings','control',True),self.c.ports)


if __name__=='__main__':unittest.main()
