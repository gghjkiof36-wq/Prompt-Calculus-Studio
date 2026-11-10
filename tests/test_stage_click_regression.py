"""Q2: each click accepts a distinct group, including before the first pump."""
import unittest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
import test_prompt_batches as batches


class ClickRegressionTests(unittest.TestCase):
    setUp=batches.BatchExecutionTests.setUp
    tearDown=batches.BatchExecutionTests.tearDown
    stages=batches.BatchExecutionTests.stages
    click=batches.BatchExecutionTests.click
    workflows=batches.BatchExecutionTests.workflows
    submitted_texts=batches.BatchExecutionTests.submitted_texts
    pipeline=batches.BatchExecutionTests.pipeline
    setup_batch=batches.BatchExecutionTests.setup_batch

    def test_two_clicks_without_event_loop_create_only_one_batch(self):
        count=10
        self.setup_batch(length=count);self.controls.refresh()
        QTest.mouseClick(self.controls.run_button,Qt.MouseButton.LeftButton)
        # Intentionally no processEvents/qWait between these two physical clicks.
        QTest.mouseClick(self.controls.run_button,Qt.MouseButton.LeftButton)
        QTest.qWait(40)
        completed=0
        for _ in range(3*count):
            if not self.executor.pending:
                QTest.qWait(60)
                if not self.executor.pending:break
            self.executor.finish();completed+=1
        observed=dict(entries=len(self.runner.store.rows('entry')),
                      submissions=len(self.executor.submissions),generations=completed)
        self.assertFalse(self.executor.pending,observed)
        self.assertEqual(observed,dict(entries=2*count,submissions=2*count,generations=2*count),repr(observed))
        self.assertEqual(len({e['execution_group'] for e in self.runner.store.rows('entry')}),2)
        self.assertEqual(self.submitted_texts(),[letter+f'\n1-{cycle}' for cycle in (1,2) for letter in 'abcdefghij'])
