"""Real local result collection with a synthetic ComfyUI executor, no GPU."""
import copy
import unittest
from PySide6.QtTest import QTest
import test_084_stages as stage_tests
from prompt_calculus_studio import multi_output as model,stage_model


class CanvasPreviewContinuityTests(unittest.TestCase):
    setUp=stage_tests.StageTests.setUp
    tearDown=stage_tests.StageTests.tearDown
    stages=stage_tests.StageTests.stages
    click=stage_tests.StageTests.click

    def test_completed_batch_stays_during_next_submission_then_is_replaced(self):
        key=self.stages()[0]
        self.c.commit(lambda s:model.connect(s,key,'__result_preview__','image'))
        self.controls.count.setValue(3);self.click()
        self.executor.finish();QTest.qWait(20)
        view=self.c.results;view.refresh()
        self.assertEqual(len(self.executor.pending),2)
        self.assertEqual(len(view.preview.pictures),1)
        self.assertTrue(all(not p.isNull() for p in view.preview.pictures))
        before=copy.deepcopy(view.input_records);runtime=copy.deepcopy(self.runner.results)
        self.runner.load_results();view.refresh()
        self.assertEqual(view.input_records,before,'reloading history while pending retains the completed batch')
        self.assertEqual(self.runner.results,runtime,'display projection must not rewrite downstream results')
        self.executor.tick();view.refresh();self.assertEqual(view.input_records,before)
        self.executor.finish(1);view.refresh()
        self.assertEqual(len(self.executor.pending),1)
        self.assertEqual(len(view.preview.pictures),1)
        self.assertNotEqual(view.input_records,before)
        newer=copy.deepcopy(view.input_records)
        self.executor.finish(error=True);view.refresh()
        self.assertEqual(view.input_records,newer,'a failed next task keeps the last completed image')

    def test_disconnect_new_source_and_workspace_boundary_clear_the_old_preview(self):
        key=self.stages()[0]
        self.c.commit(lambda s:model.connect(s,key,'__result_preview__','image'))
        self.click();self.executor.finish();view=self.c.results;view.refresh()
        self.assertEqual(len(view.input_records),1)
        line=next(c for c in self.c.data()['connections'] if c['destination']=='__result_preview__')
        self.c.commit(lambda s:model.disconnect(s,line['id']));view.refresh()
        self.assertFalse(view.input_records);self.assertTrue(view.preview.picture.isNull());self.assertFalse(view.preview.pictures)
        other=stage_model.add(self.w.state,workflow='flow')
        self.c.commit(lambda s:model.connect(s,other,'__result_preview__','image'));view.refresh()
        self.assertFalse(view.input_records);self.assertFalse(view.preview.pictures)
        self.c.commit(lambda s:model.connect(s,key,'__result_preview__','image'));view.refresh()
        self.assertEqual(len(view.input_records),1)
        self.runner.workspace_changed(self.w.state['workspace']);view.refresh()
        self.assertFalse(view.input_records);self.assertFalse(view.preview.pictures)
