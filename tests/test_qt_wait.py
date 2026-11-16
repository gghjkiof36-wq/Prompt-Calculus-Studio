"""Normal-event-loop waits must service both GUI events and Python workers."""
import gc
import threading
import unittest
import weakref

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication

from _qt_wait import wait_ms, wait_until


class _WorkerResult(QObject):
    ready = Signal()


class QtWaitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_fixed_wait_delivers_timer_callback(self):
        events = []
        QTimer.singleShot(0, lambda: events.append('timer'))
        wait_ms(10)
        self.assertEqual(events, ['timer'])

    def test_completed_condition_releases_its_predicate(self):
        class Predicate:
            def __init__(self, mode):
                self.mode = mode
                self.calls = 0

            def __call__(self):
                self.calls += 1
                if self.mode == 'error' and self.calls >= 3:
                    raise ValueError('expected')
                return self.mode == 'ready' and self.calls >= 3

        for mode in ('ready', 'timeout', 'error'):
            with self.subTest(mode=mode):
                predicate = Predicate(mode)
                reference = weakref.ref(predicate)
                if mode == 'error':
                    with self.assertRaisesRegex(ValueError, 'expected'):
                        wait_until(predicate, timeout_ms=100, interval_ms=1)
                else:
                    self.assertEqual(wait_until(predicate, timeout_ms=10, interval_ms=1), mode == 'ready')
                del predicate
                gc.collect()
                self.assertIsNone(reference(), 'Timer callbacks must not retain a finished fixture')

    def test_zero_wait_still_processes_queued_events(self):
        events = []
        QTimer.singleShot(0, lambda: events.append('timer'))
        wait_ms(0)
        self.assertEqual(events, ['timer'])

    def test_condition_services_python_worker_and_queued_signal(self):
        gate = threading.Event()
        delivered = []
        result = _WorkerResult()
        result.ready.connect(lambda: delivered.append('worker'))

        def work():
            if gate.wait(2):
                result.ready.emit()

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        QTimer.singleShot(0, gate.set)
        try:
            self.assertTrue(wait_until(lambda: bool(delivered), timeout_ms=1000))
        finally:
            gate.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(delivered, ['worker'])

    def test_fixed_wait_services_python_worker_and_queued_signal(self):
        gate = threading.Event()
        delivered = []
        result = _WorkerResult()
        result.ready.connect(lambda: delivered.append('worker'))

        def work():
            if gate.wait(2):
                result.ready.emit()

        worker = threading.Thread(target=work, daemon=True)
        worker.start()
        QTimer.singleShot(0, gate.set)
        try:
            wait_ms(500)
        finally:
            gate.set()
            worker.join(2)
        self.assertFalse(worker.is_alive())
        self.assertEqual(delivered, ['worker'])

    def test_condition_timeout_and_immediate_success(self):
        self.assertFalse(wait_until(lambda: False, timeout_ms=5))
        self.assertFalse(wait_until(lambda: False, timeout_ms=0))
        self.assertTrue(wait_until(lambda: True, timeout_ms=0))

    def test_condition_callback_exception_reaches_test(self):
        calls = []

        def predicate():
            calls.append(None)
            if len(calls) > 1:
                raise ValueError('predicate failed')
            return False

        with self.assertRaisesRegex(ValueError, 'predicate failed'):
            wait_until(predicate, timeout_ms=1000)


if __name__ == '__main__':
    unittest.main()
