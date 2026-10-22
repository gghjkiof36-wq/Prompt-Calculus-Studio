"""Qt-free local task cancellation and terminal-result contract."""
import threading
import unittest

from prompt_calculus_studio.job_outcomes import JobOutcome, run_job


class JobOutcomeTests(unittest.TestCase):
    def test_pre_cancel_does_not_enter_a_writer(self):
        cancel = threading.Event(); cancel.set()
        called = []
        result = run_job(lambda event: called.append(True) or JobOutcome.completed('written'), cancel)
        self.assertEqual(result.state, 'cancelled')
        self.assertEqual(called, [])

    def test_unwrapped_read_result_retains_late_cancel_rejection(self):
        cancel = threading.Event()
        def read(event):
            event.set()
            return {'would_update_display': True}
        result = run_job(read, cancel)
        self.assertEqual(result.state, 'cancelled')
        self.assertIsNone(result.value)

    def test_completed_writer_receipt_survives_late_cancel(self):
        cancel = threading.Event()
        published = {'path': 'already-published', 'owner': 'original'}
        def write(event):
            event.set()
            return JobOutcome.completed(published)
        result = run_job(write, cancel)
        self.assertEqual(result.state, 'completed')
        self.assertIs(result.value, published)

    def test_partial_batch_receipt_keeps_cancelled_and_remaining_items(self):
        report = {'results': [{'status': 'Passed'}, {'status': 'Cancelled'}], 'remaining': 3}
        cancel = threading.Event()
        def batch(event):
            event.set()
            return JobOutcome.completed(report)
        result = run_job(batch, cancel)
        self.assertEqual(result.value, report)
        self.assertEqual(result.value['remaining'], 3)

    def test_explicit_failure_is_not_rewritten_by_late_cancel(self):
        cancel = threading.Event()
        def write(event):
            event.set()
            return JobOutcome.failed('publication uncertain; recovery retained')
        result = run_job(write, cancel)
        self.assertEqual((result.state, result.message), ('failed', 'publication uncertain; recovery retained'))

    def test_worker_exception_keeps_its_actual_failure_after_cancel(self):
        cancel = threading.Event()
        def write(event):
            event.set()
            raise OSError('disk write failed')
        result = run_job(write, cancel)
        self.assertEqual((result.state, result.message), ('failed', 'disk write failed'))

    def test_cooperative_cancellation_keeps_existing_message(self):
        for error in (InterruptedError('cancelled batch'), ValueError('已取消備份；既有 ZIP 未變更。')):
            with self.subTest(type=type(error).__name__):
                def work(event):raise error
                result = run_job(work, threading.Event())
                self.assertEqual((result.state, result.message), ('cancelled', str(error)))

    def test_none_success_and_empty_error_are_distinct(self):
        self.assertEqual(run_job(lambda event: None, threading.Event()).state, 'completed')
        def work(event):raise RuntimeError()
        result = run_job(work, threading.Event())
        self.assertEqual((result.state, result.message), ('failed', 'RuntimeError'))
