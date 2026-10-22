"""Jobs owns runner state; callbacks own business state and may re-enter."""
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.jobs import Jobs
from prompt_calculus_studio.job_outcomes import JobOutcome

APP = QApplication.instance() or QApplication([])


class Host(QObject):
    def __init__(self):
        super().__init__()
        self.closing = False
        self.notices, self.errors = [], []
    def notice(self, value):self.notices.append(value)
    def error(self, value):self.errors.append(value)


class JobsLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.host = Host()
        self.jobs = Jobs(self.host)
        self.idle = []
        self.jobs.became_idle.connect(lambda: self.idle.append(True))

    def tearDown(self):
        self.assertTrue(self.jobs.pool.waitForDone(1000))
        self.jobs.active = None

    def arm(self, done=lambda _: None, failed=None):
        self.jobs.active = object()
        self.jobs.callback, self.jobs.error_callback = done, failed

    def test_failed_callback_error_propagates_but_still_emits_idle(self):
        def failed(message):raise RuntimeError('failed callback')
        self.arm(failed=failed)
        with self.assertRaisesRegex(RuntimeError, 'failed callback'):
            self.jobs.finished(None, 'worker failure')
        self.assertIsNone(self.jobs.active)
        self.assertEqual(self.idle, [True])

    def test_success_callback_and_error_reporter_failures_still_settle(self):
        def done(value):raise ValueError('callback failed')
        self.arm(done)
        with patch.object(self.host, 'error', side_effect=RuntimeError('reporter failed')):
            with self.assertRaisesRegex(RuntimeError, 'reporter failed'):
                self.jobs.finished('result', '')
        self.assertIsNone(self.jobs.active)
        self.assertEqual(self.idle, [True])

    def test_cancellation_notice_failure_still_settles(self):
        self.arm()
        with patch.object(self.host, 'notice', side_effect=RuntimeError('notice failed')):
            with self.assertRaisesRegex(RuntimeError, 'notice failed'):
                self.jobs.finished(None, '已取消操作。')
        self.assertIsNone(self.jobs.active)
        self.assertEqual(self.idle, [True])

    def test_callback_can_start_next_job_without_spurious_idle_or_state_loss(self):
        held = []
        def done(value):
            self.assertTrue(self.jobs.start('next', lambda cancel: 'next result', lambda _: None))
        self.arm(done)
        with patch.object(self.jobs.pool, 'start', side_effect=held.append):
            self.jobs.finished('old result', '')
        self.assertEqual(len(held), 1)
        self.assertIs(self.jobs.active, held[0])
        self.assertEqual(self.idle, [])

    def test_closing_keeps_accepted_completion_but_does_not_drain_more_jobs(self):
        delivered = []
        self.arm(delivered.append)
        self.host.closing = True
        self.jobs.finished({'owner': 'original'}, '')
        self.assertEqual(delivered, [{'owner': 'original'}])
        self.assertEqual(self.idle, [])
        self.assertIsNone(self.jobs.active)

    def test_real_pool_delivers_committed_receipt_once_after_late_cancel(self):
        delivered, failed = [], []
        def write(cancel):
            cancel.set()
            return JobOutcome.completed({'published': True})
        self.assertTrue(self.jobs.start('write', write, delivered.append, failed.append))
        end = time.monotonic() + 5
        while self.jobs.active is not None and time.monotonic() < end:QTest.qWait(10)
        self.assertIsNone(self.jobs.active)
        self.assertEqual(delivered, [{'published': True}])
        self.assertEqual(failed, [])
        self.assertEqual(self.idle, [True])
