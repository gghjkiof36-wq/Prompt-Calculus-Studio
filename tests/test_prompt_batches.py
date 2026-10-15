"""Rev5 batch acceptance through the real Stage journal and native receipt path."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtTest import QTest
from prompt_calculus_studio import prompt_sources as supply, composition as comp
import test_prompt_pipeline as pipeline_fixture
from test_prompt_material_resolution import text, flow


class BatchExecutionTests(unittest.TestCase):
    setUp=pipeline_fixture.PromptPipelineTests.setUp
    tearDown=pipeline_fixture.PromptPipelineTests.tearDown
    stages=pipeline_fixture.PromptPipelineTests.stages
    click=pipeline_fixture.PromptPipelineTests.click
    workflows=pipeline_fixture.PromptPipelineTests.workflows
    submitted_texts=pipeline_fixture.PromptPipelineTests.submitted_texts
    pipeline=pipeline_fixture.PromptPipelineTests.pipeline

    def setup_batch(self,scheduled=True,length=10):
        config=self.pipeline(scheduled=scheduled)
        def setup(state):
            state['uses']['root']=text('1',20)
            source=supply.source(state,config['reader']);source['root']=flow('abcdefghij')
            source.update(count_mode='specified',limit=length)
        self.assertTrue(self.c.commit(setup),self.notices)
        return config

    def complete_all(self,total):
        for index in range(total):
            self.assertTrue(self.executor.pending,(index,self.notices))
            self.executor.finish()
        self.assertFalse(self.executor.pending,self.notices)

    def test_three_click_iterations_ten_each_canvas_advances_three_direct_and_scheduled(self):
        for scheduled in (True,False):
            with self.subTest(scheduled=scheduled):
                if not scheduled:
                    self.tearDown();self.setUp()
                self.setup_batch(scheduled)
                self.runner.start(3);QTest.qWait(40)
                self.complete_all(30)
                self.assertEqual(self.submitted_texts(),[c+f'\n1-{i}' for i in range(1,4) for c in 'abcdefghij'])
                self.assertEqual(comp.render(self.w.state['uses']['root']),'1-4')
                self.assertEqual(len(self.runner.store.rows('entry')),30 if scheduled else 0)

    def test_capacity_refill_uses_original_canvas_and_definition_cursor_survives_reopen(self):
        config=self.setup_batch(length=13)
        self.click()
        self.assertEqual(len(self.runner.store.rows('entry')),10)
        source=supply.source(self.w.state,config['reader'])
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')
        self.assertEqual(comp.render(source['root']),'a')
        # Ten accepted values are durable even if the delayed document timer has not fired.
        self.assertEqual(comp.render(self.w.store.load()['uses']['root']),'1-2')
        def edit(state):
            state['uses']['root']['variants']['groups'][0]['text']='changed canvas'
            supply.source(state,config['reader'])['root']['sequence']['groups'][0]['text']='changed source'
        self.assertTrue(self.c.commit(edit))
        self.complete_all(13)
        self.assertEqual(self.submitted_texts(),[c+'\n1-1' for c in 'abcdefghijabc'])

    def test_failed_queue_persistence_rolls_back_rows_and_cursors(self):
        self.setup_batch(length=6)
        before=copy.deepcopy(self.w.state);original=self.runner.store.add;entries=[0]
        def fail_second(kind,*args,**kwargs):
            if kind=='entry':
                entries[0]+=1
                if entries[0]==2:raise OSError('synthetic journal write failure')
            return original(kind,*args,**kwargs)
        with patch.object(self.runner.store,'add',side_effect=fail_second):
            self.click()
        self.assertEqual(self.runner.store.rows('entry'),[])
        self.assertEqual(self.w.state['uses']['root'],before['uses']['root'])
        self.assertEqual(supply.definitions(self.w.state),supply.definitions(before))
        self.assertEqual(self.executor.submissions,[])
        self.assertTrue(any('synthetic journal' in note for note in self.notices))

    def test_validation_failure_before_run_does_not_consume(self):
        config=self.setup_batch(length=6)
        source=supply.source(self.w.state,config['reader']);source['unselected']=True
        before=copy.deepcopy(self.w.state)
        with self.assertRaisesRegex(ValueError,'選擇'):self.runner.start(1)
        self.assertEqual(self.runner.store.rows('run'),[])
        self.assertEqual(self.w.state,before)

    def test_same_reader_fanout_to_two_fields_still_generates_once_per_round(self):
        from prompt_calculus_studio import multi_output as model,clip_flow,scheduler_wiring as wiring
        config=self.setup_batch(length=6);refs={}
        def connect(state):
            output=model.ident('out_');state['multi_output']['outputs'][output]=dict(model.new_output('負面'),canvases=[],text_sources=[])
            wiring.connect(state,config['reader'],output,'clip')
            clip=clip_flow.add(state);refs['clip']=clip;clip_flow.set_binding(state,'flow',clip,('7','text'))
            wiring.connect(state,output,clip,'clip');wiring.connect(state,clip,config['scheduler']+'::bindings','control')
        self.assertTrue(self.c.commit(connect),self.notices);self.click();self.complete_all(6)
        self.assertEqual(self.submitted_texts(),[c+'\n1-1' for c in 'abcdef'])
        self.assertEqual([p['prompt']['7']['inputs']['text'] for p in self.executor.submissions],list('abcdef'))
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'g')

    def test_cancel_saved_six_then_next_run_starts_at_seven(self):
        config=self.setup_batch(length=6);self.click()
        run=self.runner.current();self.executor.finish();self.executor.finish()
        self.runner.cancel(run['id'])
        self.assertFalse(self.executor.pending)
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'g')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')
        self.click();self.assertEqual(self.submitted_texts()[-1],'g\n1-2',self.notices)

    def test_failure_retry_uses_same_value_without_advancing_saved_position(self):
        config=self.setup_batch(length=6);self.click();self.executor.finish(error=True)
        attempt=self.runner.store.rows('attempt')[0]
        before=copy.deepcopy(supply.source(self.w.state,config['reader'])['root'])
        self.runner.retry(attempt['id']);QTest.qWait(40)
        self.assertEqual(self.submitted_texts(),['a\n1-1']*2)
        self.assertEqual(supply.source(self.w.state,config['reader'])['root'],before)

    def test_reopen_pauses_then_refills_original_batch_without_resampling(self):
        from prompt_calculus_studio.stage_runner import StageRunner
        config=self.setup_batch(length=13);self.click();run=self.runner.current()
        self.runner.close();self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)
        self.executor.finish()
        self.assertEqual(self.runner.current()['status'],'paused')
        self.assertEqual(len(self.executor.submissions),1)
        def edit(state):
            state['uses']['root']['variants']['groups'][0]['text']='new canvas'
            supply.source(state,config['reader'])['root']['sequence']['groups'][0]['text']='new reader'
        self.assertTrue(self.c.commit(edit))
        self.runner.resume(run['id']);QTest.qWait(40);self.complete_all(12)
        self.assertEqual(self.submitted_texts(),[c+'\n1-1' for c in 'abcdefghijabc'])

    def test_text_ten_image_six_cycles_and_second_batch_resumes_image_five(self):
        from pathlib import Path
        from prompt_calculus_studio import multi_output as model,scheduler_wiring as wiring
        from prompt_calculus_studio.image_source import set_items
        from prompt_calculus_studio.image_bindings import import_source
        from prompt_calculus_studio.flow_data import add_image_input
        from test_comfy_integration import png
        config=self.setup_batch();items=[]
        for i in range(6):
            path=Path(self.tmp.name)/f'pair-{i}.png';png(path,{'pair':i});items.append(import_source(path,self.w.store.directory))
        source=self.c.functions.add_image(source=items[0])
        def connect(state):
            set_items(state,source,items,self.w.store.directory,True)
            profile=next(p for p in state['generation']['profiles'] if p['id']=='flow')
            profile['graph']['10']=dict(class_type='LoadImage',inputs=dict(image='native.png'))
            self.executor.graphs['flow']['10']=copy.deepcopy(profile['graph']['10'])
            image=add_image_input(state);state['multi_output']['image_inputs'][image].update(workflow='flow',node='10')
            wiring.connect(state,source,image,'image');wiring.connect(state,image,config['scheduler']+'::bindings','control')
        self.assertTrue(self.c.commit(connect),self.notices);self.runner.start(2);QTest.qWait(40);self.complete_all(20)
        pictures=[p['prompt']['10']['inputs']['image'] for p in self.executor.submissions]
        for i,picture in enumerate(pictures):self.assertIn(items[i%6]['sha256'],picture)
        self.assertEqual(self.submitted_texts(),[c+f'\n1-{i}' for i in (1,2) for c in 'abcdefghij'])
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],2)


if __name__=='__main__':unittest.main()
