"""Unconnected terminals consume only the result of their own explicit run."""
import unittest
from PySide6.QtTest import QTest
import stage_fixture as fixture
import test_084_stages as stages
from prompt_calculus_studio import multi_output as model
from prompt_calculus_studio.flow_data import add_image_input


class FreeResultTests(unittest.TestCase):
    setUp=stages.StageTests.setUp
    tearDown=fixture.StageFixture.tearDown
    click=fixture.StageFixture.click
    workflows=fixture.StageFixture.workflows
    stages=stages.StageTests.stages

    def downstream(self):
        stage=self.stages()[0];reader=self.c.functions.add_image(enhanced=False);keys=[]
        def edit(state):
            keys.append(add_image_input(state));target=state['multi_output']['image_inputs'][keys[0]]
            target.update(workflow='B',node='10');model.connect(state,stage,reader,'image')
            state['canvas_functions']['images'][reader]['output_node']='9'
            model.connect(state,reader,keys[0],'image')
        self.assertTrue(self.c.commit(edit),self.notices)
        return stage,reader,keys[0]

    def test_unconnected_downstream_applies_after_result_without_running_b(self):
        stage,reader,target=self.downstream();self.click()
        self.assertEqual(self.workflows(),['flow']);self.assertFalse(self.executor.applied)
        self.assertFalse(any('尚未產生' in n for n in self.notices),self.notices)
        self.executor.finish();QTest.qWait(30)
        self.assertEqual(self.workflows(),['flow'])
        self.assertEqual([r['workflow'] for r in self.executor.applied],['B'],self.notices)
        image=self.runner.results[stage]['images'][0]
        self.assertIn(image['sha256'],self.executor.graphs['B']['10']['inputs']['image'])
        run=self.runner.runs()[0]
        self.assertEqual((run['status'],run['free_state']),('complete','applied'),self.notices)

    def test_repeat_clicks_do_not_use_previous_preview(self):
        stage,reader,target=self.downstream();self.click();self.executor.finish();QTest.qWait(20)
        self.click();self.assertEqual(len(self.executor.applied),1)
        self.executor.finish();QTest.qWait(20)
        self.assertEqual(len(self.executor.applied),2)
        attempts=self.runner.store.rows('attempt')
        self.assertNotEqual(attempts[0]['result']['images'][0]['reference']['prompt_id'],attempts[1]['result']['images'][0]['reference']['prompt_id'])
        self.assertIn(attempts[1]['result']['images'][0]['sha256'],self.executor.graphs['B']['10']['inputs']['image'])
        self.assertEqual(self.workflows(),['flow','flow'])

    def test_deleted_free_input_never_receives_late_result(self):
        stage,reader,target=self.downstream();self.click()
        def remove(state):
            state['multi_output']['image_inputs'].pop(target)
            state['multi_output']['connections']=[c for c in state['multi_output']['connections'] if c['destination']!=target]
        self.assertTrue(self.c.commit(remove),self.notices)
        self.executor.finish();QTest.qWait(20)
        self.assertFalse(self.executor.applied)
        self.assertEqual(self.runner.runs()[0]['free_state'],'retained')

    def test_cancelled_run_does_not_apply_late_result(self):
        self.downstream();self.click();ident=self.runner.runs()[0]['id'];pending=[]
        request=self.w.comfy.request
        def delayed(route,*args,**kwargs):
            if route.startswith('desktop/results?'):pending.append(lambda:request(route,*args,**kwargs))
            else:return request(route,*args,**kwargs)
        self.w.comfy.request=delayed
        self.executor.finish();self.assertTrue(pending)
        self.runner.cancel(ident)
        for response in pending:response()
        QTest.qWait(20)
        self.assertFalse(self.executor.applied)
        self.assertEqual(self.runner.runs()[0]['status'],'cancelled')

    def test_failed_free_apply_keeps_generation_complete_and_does_not_resend(self):
        self.downstream();self.click();request=self.w.comfy.request
        def reject(route,data=None,done=None,failed=None,**kwargs):
            if route=='workflow/native/apply':failed('目標工作流尚未開啟。')
            else:return request(route,data,done,failed,**kwargs)
        self.w.comfy.request=reject;self.executor.finish();QTest.qWait(20)
        run=self.runner.runs()[0]
        self.assertEqual((run['status'],run['free_state']),('complete','failed'),self.notices)
        self.assertIn('同步失敗',run['message']);self.assertIn('尚未開啟',run['free_error'])
        self.executor.tick();self.assertEqual(self.workflows(),['flow'])
        self.assertIsNone(self.runner.current())


if __name__=='__main__':unittest.main()
