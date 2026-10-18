"""Child-surface motion timed from the input, not a delayed first Qt tick."""
from PySide6.QtCore import (QObject, QTimer, QElapsedTimer, QVariantAnimation,
                           QAbstractAnimation, Qt, Signal)


class ElapsedAnimation(QObject):
    """Use Qt interpolation with a monotonic, precise frame clock.

    A busy first layout can delay the unified animation driver's first tick.
    Starting its timeline there adds that delay to the whole transition. Each
    frame here uses elapsed time since start; a missed frame does not lengthen
    the interaction or require replaying stale frames.
    """
    State = QAbstractAnimation.State
    valueChanged = Signal(object)
    finished = Signal()
    stateChanged = Signal(object, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._values = QVariantAnimation(self)
        self._values.valueChanged.connect(lambda value: self.valueChanged.emit(value))
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)
        self._clock = QElapsedTimer()
        self._offset = 0
        self._running = False

    def setDuration(self, value): self._values.setDuration(value)
    def duration(self): return self._values.duration()
    def setEasingCurve(self, value): self._values.setEasingCurve(value)
    def setStartValue(self, value): self._values.setStartValue(value)
    def setEndValue(self, value): self._values.setEndValue(value)
    def startValue(self): return self._values.startValue()
    def endValue(self): return self._values.endValue()
    def currentValue(self): return self._values.currentValue()
    def currentTime(self): return self._values.currentTime()
    def state(self): return self.State.Running if self._running else self.State.Stopped

    def start(self):
        self.stop()
        self._offset = 0
        self._clock.start()
        self._running = True
        self._values.setCurrentTime(0)
        self.stateChanged.emit(self.State.Running, self.State.Stopped)
        self._timer.start()

    def stop(self):
        was_running = self._running
        self._running = False
        self._timer.stop()
        if was_running:self.stateChanged.emit(self.State.Stopped, self.State.Running)

    def setCurrentTime(self, value):
        if self._running:
            self._offset = value
            self._clock.restart()
        self._advance(value)

    def _tick(self):
        if self._running:self._advance(self._offset + self._clock.elapsed())

    def _advance(self, value):
        final = self._running and value >= self.duration()
        if final:self.stop()
        self._values.setCurrentTime(min(self.duration(), max(0, value)))
        if final:self.finished.emit()
