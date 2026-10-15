"""The same Prompt placement keeps its selections across separate Stage routes."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtTest import QTest
from prompt_calculus_studio import prompt_sources as supply, prompt_materials as pm
from prompt_calculus_studio import composition as comp, multi_output as model
from prompt_calculus_studio import clip_flow, stage_model, scheduler_wiring as wiring
from prompt_calculus_studio.flow_data import add_scheduler
import test_prompt_batches as batch_fixture
from test_prompt_material_resolution import flow


class StageFanoutTests(unittest.TestCase):
    setUp=batch_fixture.BatchExecutionTests.setUp
    tearDown=batch_fixture.BatchExecutionTests.tearDown
    stages=batch_fixture.BatchExecutionTests.stages
    click=batch_fixture.BatchExecutionTests.click
    workflows=batch_fixture.BatchExecutionTests.workflows
    submitted_texts=batch_fixture.BatchExecutionTests.submitted_texts
    pipeline=batch_fixture.BatchExecutionTests.pipeline
    setup_batch=batch_fixture.BatchExecutionTests.setup_batch
    complete_all=batch_fixture.BatchExecutionTests.complete_all

    def fanout(self, scheduled=True, length=3, second_reader=False, mixed=False, direct_first=False):
        config=self.setup_batch(scheduled=scheduled,length=length)
        def connect(state):
            profile=next(p for p in state['generation']['profiles'] if p['id']=='B')
            profile['graph']['6']=dict(class_type='CLIPTextEncode',inputs=dict(text='native B'))
            self.executor.graphs['B']['6']=copy.deepcopy(profile['graph']['6'])
            clip=clip_flow.add(state);clip_flow.set_binding(state,'B',clip,('6','text'))
            stage=stage_model.add(state,(1600,0),'B');config['stage_b']=stage
            state['multi_output']['stages'][stage].update(name='B',output='9')
            output=self.out
            if second_reader:
                output=model.ident('out_')
                state['multi_output']['outputs'][output]=dict(model.new_output('Other'),canvases=[],text_sources=[])
                wiring.connect(state,config['reader'],output,'clip')
                other=supply.add(state,root=flow('uvwxy'));config['other']=other
                wiring.connect(state,other,output,'clip');wiring.connect(state,self.cid,output,'text')
            wiring.connect(state,output,clip,'clip')
            if scheduled and not mixed:
                scheduler=add_scheduler(state)
                wiring.connect(state,clip,scheduler+'::bindings','control')
                wiring.connect(state,scheduler+'::bindings',stage,'control')
            else:wiring.connect(state,clip,stage,'control')
            first,last=(stage,config['stage']) if direct_first else (config['stage'],stage)
            wiring.connect(state,first,last,'done')
        self.assertTrue(self.c.commit(connect),self.notices)
        return config

    def values_by_workflow(self):
        return {name:[text for owner,text in zip(self.workflows(),self.submitted_texts()) if owner==name]
                for name in ('flow','B')}

    def test_scheduled_fanout_matches_direct_and_consumes_each_placement_once(self):
        results=[]
        for scheduled in (True,False):
            if not scheduled:self.tearDown();self.setUp()
            config=self.fanout(scheduled)
            self.click();self.complete_all(6)
            results.append(self.values_by_workflow())
            self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'d')
            self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')
        self.assertEqual(results[0],results[1])
        self.assertEqual(results[0],dict(flow=[v+'\n1-1' for v in 'abc'],B=[v+'\n1-1' for v in 'abc']))

    def test_random_reader_is_resolved_once_per_shared_round(self):
        config=self.fanout()
        reader=supply.source(self.w.state,config['reader'])['root']
        reader['sequence']['mode']='random'
        resolve=pm.resolve;calls=[]
        def counted(root,**kwargs):
            calls.append(root['id']);return resolve(root,**kwargs)
        with patch.object(pm,'resolve',side_effect=counted):
            self.click();self.complete_all(6)
        values=self.values_by_workflow()
        self.assertEqual(values['flow'],values['B'])
        self.assertEqual(calls.count(reader['id']),3)
        self.assertEqual(calls.count(self.w.state['uses']['root']['id']),1)

    def test_refill_and_reopen_keep_shared_selection_receipts(self):
        from prompt_calculus_studio.stage_runner import StageRunner
        config=self.fanout(length=13);self.click();run=self.runner.current()
        self.runner.close();self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)
        self.executor.finish()
        self.assertEqual(self.runner.current()['status'],'paused')
        self.runner.resume(run['id']);QTest.qWait(40);self.complete_all(25)
        values=self.values_by_workflow();expected=[c+'\n1-1' for c in 'abcdefghijabc']
        self.assertEqual(values,dict(flow=expected,B=expected))
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'d')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')

    def test_cancel_does_not_consume_the_second_branch_or_reuse_old_cache(self):
        config=self.fanout();self.click();run=self.runner.current()
        self.runner.cancel(run['id'])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'d')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')
        self.click();self.complete_all(6)
        self.assertEqual(self.values_by_workflow()['B'],[v+'\n1-2' for v in 'def'])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'g')

    def test_longer_branch_extends_shared_values_and_independent_source_keeps_own_choices(self):
        config=self.fanout(second_reader=True)
        self.click();self.complete_all(8)
        values=self.values_by_workflow()
        self.assertEqual(values['flow'],[c+'\n1-1' for c in 'abc'])
        self.assertEqual(values['B'],[a+'\n'+b+'\n1-1' for a,b in zip('abcde','uvwxy')])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'f')
        self.assertEqual(comp.render(supply.source(self.w.state,config['other'])['root']),'u')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')

    def test_failed_entry_write_retries_the_original_shared_prefix(self):
        self.fanout();original=self.runner.store.add;entries=[]
        def fail_second(kind,*args,**kwargs):
            if kind=='entry':
                entries.append(kind)
                if len(entries)==2:raise OSError('synthetic fanout write failure')
            return original(kind,*args,**kwargs)
        with patch.object(self.runner.store,'add',side_effect=fail_second):self.click()
        self.assertEqual(self.runner.store.rows('entry'),[])
        run=self.runner.current();self.runner.resume(run['id']);QTest.qWait(40);self.complete_all(6)
        self.assertEqual(self.values_by_workflow(),dict(flow=[v+'\n1-1' for v in 'abc'],B=[v+'\n1-1' for v in 'abc']))

    def test_mixed_routes_match_with_direct_stage_first_or_last(self):
        for direct_first in (False,True):
            if direct_first:self.tearDown();self.setUp()
            config=self.fanout(mixed=True,direct_first=direct_first)
            self.click();self.complete_all(6)
            self.assertEqual(self.values_by_workflow(),dict(flow=[v+'\n1-1' for v in 'abc'],B=[v+'\n1-1' for v in 'abc']))
            self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'d')
            self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')

    def test_mixed_direct_prefix_cancel_preserves_only_saved_values(self):
        config=self.fanout(mixed=True,direct_first=True)
        self.click();self.assertEqual(self.submitted_texts(),['a\n1-1'])
        self.runner.cancel(self.runner.current()['id'])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'b')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')
        self.click();self.complete_all(6)
        self.assertEqual(self.values_by_workflow()['flow'],[v+'\n1-2' for v in 'bcd'])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'e')

    def test_mixed_direct_reopen_keeps_original_shared_batch(self):
        from prompt_calculus_studio.stage_runner import StageRunner
        config=self.fanout(mixed=True,direct_first=True);self.click();run=self.runner.current()
        self.runner.close();self.w.comfy.input_flow.chain=self.runner=StageRunner(self.w.comfy.input_flow)
        self.executor.finish()
        self.assertEqual(self.runner.current()['status'],'paused')
        self.runner.resume(run['id']);QTest.qWait(40);self.complete_all(5)
        self.assertEqual(self.values_by_workflow(),dict(flow=[v+'\n1-1' for v in 'abc'],B=[v+'\n1-1' for v in 'abc']))
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'d')

    def test_mixed_failed_attempt_write_does_not_consume_unsaved_prefix(self):
        config=self.fanout(mixed=True,direct_first=True)
        original=self.runner.store.add
        def fail_attempt(kind,*args,**kwargs):
            if kind=='attempt':raise OSError('synthetic direct attempt write failure')
            return original(kind,*args,**kwargs)
        with patch.object(self.runner.store,'add',side_effect=fail_attempt):self.click()
        self.assertEqual(self.runner.store.rows('attempt'),[])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'a')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-1')
        self.runner.resume(self.runner.current()['id']);QTest.qWait(40);self.complete_all(6)
        self.assertEqual(self.values_by_workflow(),dict(flow=[v+'\n1-1' for v in 'abc'],B=[v+'\n1-1' for v in 'abc']))

    def test_direct_canvas_only_branch_keeps_one_shared_value(self):
        config=self.fanout(mixed=True)
        def canvas_only(state):
            clip=stage_model.compile_plan(state)['stages'][config['stage_b']]['bindings'][0]['key']
            output=model.ident('out_')
            state['multi_output']['outputs'][output]=dict(model.new_output('Canvas only'),canvases=[],text_sources=[])
            wiring.connect(state,self.cid,output,'text');wiring.connect(state,output,clip,'clip')
        self.assertTrue(self.c.commit(canvas_only),self.notices)
        self.click();self.complete_all(4)
        self.assertEqual(self.values_by_workflow()['B'],['1-1'])
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')

    def test_mixed_image_fanout_resumes_next_image_on_second_batch(self):
        from pathlib import Path
        from prompt_calculus_studio.image_source import set_items
        from prompt_calculus_studio.image_bindings import import_source
        from prompt_calculus_studio.flow_data import add_image_input
        from test_comfy_integration import png
        config=self.fanout(mixed=True,direct_first=True);items=[]
        for i in range(2):
            path=Path(self.tmp.name)/f'fanout-image-{i}.png';png(path,{'pair':i})
            items.append(import_source(path,self.w.store.directory))
        source=self.c.functions.add_image(source=items[0])
        def connect(state):
            set_items(state,source,items,self.w.store.directory,True)
            for workflow,stage in (('flow',config['stage']),('B',config['stage_b'])):
                profile=next(p for p in state['generation']['profiles'] if p['id']==workflow)
                profile['graph']['10']=dict(class_type='LoadImage',inputs=dict(image='native.png'))
                self.executor.graphs[workflow]['10']=copy.deepcopy(profile['graph']['10'])
                image=add_image_input(state);state['multi_output']['image_inputs'][image].update(workflow=workflow,node='10')
                wiring.connect(state,source,image,'image')
                destination=config['scheduler']+'::bindings' if workflow=='flow' else stage
                wiring.connect(state,image,destination,'control')
        self.assertTrue(self.c.commit(connect),self.notices)
        self.runner.start(2);QTest.qWait(40);self.complete_all(12)
        expected=[c+'\n1-'+str(i) for i,letters in ((1,'abc'),(2,'def')) for c in letters]
        self.assertEqual(self.values_by_workflow(),dict(flow=expected,B=expected))
        for workflow in ('flow','B'):
            pictures=[p['prompt']['10']['inputs']['image'] for owner,p in zip(self.workflows(),self.executor.submissions) if owner==workflow]
            for i,picture in enumerate(pictures):self.assertIn(items[i%2]['sha256'],picture)
        self.assertEqual(self.w.state['canvas_functions']['images'][source]['index'],0)

    def test_mixed_direct_branch_pairs_its_additional_reader_without_resampling(self):
        config=self.fanout(mixed=True,second_reader=True)
        other=supply.source(self.w.state,config['other'])['root'];resolve=pm.resolve;calls=[]
        def counted(root,**kwargs):
            calls.append(root['id']);return resolve(root,**kwargs)
        with patch.object(pm,'resolve',side_effect=counted):self.click();self.complete_all(8)
        self.assertEqual(self.values_by_workflow()['B'],[a+'\n'+b+'\n1-1' for a,b in zip('abcde','uvwxy')])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'f')
        self.assertEqual(comp.render(self.w.state['uses']['root']),'1-2')
        self.assertEqual(calls.count(other['id']),5)

    def test_mixed_short_definition_cycles_to_fill_longer_direct_branch(self):
        config=self.fanout(mixed=True,second_reader=True)
        supply.source(self.w.state,config['reader'])['root']=flow('abc')
        self.click();self.complete_all(8)
        self.assertEqual(self.values_by_workflow()['B'],[a+'\n'+b+'\n1-1' for a,b in zip('abcab','uvwxy')])
        self.assertEqual(comp.render(supply.source(self.w.state,config['reader'])['root']),'c')


if __name__=='__main__':unittest.main()
