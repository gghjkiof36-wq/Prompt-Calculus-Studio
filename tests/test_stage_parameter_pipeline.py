"""Real Qt Run events through native receipts/history/assets; executor is a fixture."""
import copy
import unittest
from PySide6.QtTest import QTest
import test_084_stages as stage_tests
from stage_parameter_fixture import add_samplers,intention
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.flow_data import add_scheduler
from prompt_calculus_studio.stage_parameters import effective
from prompt_calculus_studio.pnginfo import png_metadata


class StageParameterPipelineTests(unittest.TestCase):
    setUp=stage_tests.StageTests.setUp
    tearDown=stage_tests.StageTests.tearDown
    click=stage_tests.StageTests.click
    workflows=stage_tests.StageTests.workflows
    stages=stage_tests.StageTests.stages
    image_batch=stage_tests.StageTests.image_batch

    def parameters(self,workflow):
        profile=next(p for p in self.w.state['generation']['profiles'] if p['id']==workflow)
        if '5' not in profile['graph']:profile['graph']['5']=copy.deepcopy(self.executor.graphs['flow']['5'])
        add_samplers(profile);self.executor.graphs[workflow]=copy.deepcopy(profile['graph'])
        self.executor.live['parameters_protocol']=1;self.executor.native.poll(self.executor.live)
        return profile

    def test_two_tasks_keep_cfg_three_four_while_native_unspecified_fields_stay_live(self):
        profile=self.parameters('flow');stage=self.stages()[0];q=add_scheduler(self.w.state)
        self.c.commit(lambda s:(model.connect(s,stage,q+'::flow','flow'),s['multi_output']['stages'][stage].update(parameters=intention(profile,value=3))))
        self.click();self.click();entries=self.runner.store.rows('entry')
        self.assertEqual(len(entries),2,self.notices)
        self.runner.store.edit_parameters(entries[1]['id'],stage,intention(profile,value=4),entries[1]['saved'])
        self.c.commit(lambda s:s['multi_output']['stages'][stage].update(parameters=intention(profile,value=7)))
        self.executor.graphs['flow']['5']['inputs']['width']=1024
        self.executor.finish();self.executor.finish()
        self.assertIsNone(self.runner.current(),self.notices)
        self.assertEqual([p['prompt']['35']['inputs']['cfg'] for p in self.executor.submissions],[3,4])
        self.assertEqual(self.executor.submissions[1]['prompt']['5']['inputs']['width'],1024)
        self.assertTrue(all(p['prompt']['58']['inputs']['cfg']==8 for p in self.executor.submissions))
        attempts=self.runner.store.rows('attempt');self.assertTrue(all(a['status']=='complete' for a in attempts),self.notices)
        for a in attempts:
            job=self.w.comfy.generation.record(a['id'])
            self.assertEqual(job['parameter_receipt']['owner']['stage'],stage)
            self.assertEqual(job['parameter_receipt']['fields'][0]['actual'],job['payload']['prompt']['35']['inputs']['cfg'])
        for rows in self.executor.results.values():
            metadata=png_metadata(rows[0]['path']);self.assertIn(metadata['raw']['prompt']['35']['inputs']['cfg'],(3,4))

    def test_nested_twelve_images_two_outer_items_inherit_original_parameters_on_refill(self):
        _,_,parts=self.image_batch(count=12,outer=True);stage=parts['stage'];profile=self.parameters('B')
        self.c.commit(lambda s:s['multi_output']['stages'][stage].update(parameters=intention(profile,value=3)))
        self.click();self.click()
        outer=[e for e in self.runner.store.rows('entry') if e['scheduler']==parts['outer']]
        self.assertEqual(len(outer),2,self.notices)
        self.runner.store.edit_parameters(outer[1]['id'],stage,intention(profile,value=4),outer[1]['saved'])
        self.c.commit(lambda s:s['multi_output']['stages'][stage].update(parameters=intention(profile,value=7)))
        for i in range(24):
            self.assertTrue(self.executor.pending,(i,self.notices));self.executor.finish()
            self.assertLessEqual(sum(e['scheduler']==parts['inner'] for e in self.runner.store.rows('entry',active=True)),10)
        self.assertEqual([p['prompt']['35']['inputs']['cfg'] for p in self.executor.submissions],[3]*12+[4]*12,self.notices)
        self.assertIsNone(self.runner.current(),self.notices)
        child=[e for e in self.runner.store.rows('entry') if e['scheduler']==parts['inner']]
        self.assertEqual([effective(e['saved'],stage)['patches'][0]['value'] for e in child],[3]*12+[4]*12)
        self.assertTrue(all(a['status']=='complete' for a in self.runner.store.rows('attempt')))
