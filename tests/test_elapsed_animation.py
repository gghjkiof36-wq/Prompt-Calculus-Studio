"""A busy first frame must not postpone the animation's whole timeline."""
import time
import unittest
from PySide6.QtCore import QEasingCurve
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_calculus_studio.elapsed_animation import ElapsedAnimation


class ElapsedAnimationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.motion = ElapsedAnimation()
        self.motion.setDuration(200)
        self.motion.setEasingCurve(QEasingCurve.Type.Linear)
        self.motion.setStartValue(0.)
        self.motion.setEndValue(1.)
        self.finished = []
        self.motion.finished.connect(lambda: self.finished.append(True))

    def tearDown(self):
        self.motion.stop()
        self.motion.deleteLater()

    def test_delayed_first_event_catches_up_then_finishes_once(self):
        self.motion.start()
        # Model synchronous layout work before the event loop's first frame.
        time.sleep(.09)
        self.app.processEvents()
        self.assertGreaterEqual(self.motion.currentTime(), 75)
        self.assertGreater(self.motion.currentValue(), .35)
        QTest.qWait(160)
        self.assertEqual(self.motion.currentValue(), 1.)
        self.assertEqual(self.motion.state(), self.motion.State.Stopped)
        self.assertEqual(self.finished, [True])
        QTest.qWait(30)
        self.assertEqual(self.finished, [True])

    def test_reverse_keeps_current_value_and_stop_cancels_later_frames(self):
        self.motion.start()
        self.motion.setCurrentTime(80)
        current = self.motion.currentValue()
        self.motion.stop()
        self.motion.setStartValue(current)
        self.motion.setEndValue(0.)
        self.motion.setDuration(100)
        self.motion.start()
        self.assertEqual(self.motion.currentValue(), current)
        self.motion.setCurrentTime(50)
        self.assertLess(self.motion.currentValue(), current)
        self.motion.stop()
        value = self.motion.currentValue()
        QTest.qWait(130)
        self.assertEqual(self.motion.currentValue(), value)
        self.assertEqual(self.finished, [])
