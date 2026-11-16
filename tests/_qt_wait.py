"""Test waits that run Qt's normal event loop alongside Python workers.

The B4 FIX2/FIX3 investigation identified a qWait/background-worker lock-wait
risk. These helpers enter QEventLoop.exec(), like the application's main loop,
so queued GUI callbacks and Python workers can both advance. This does not
assume a particular binding lock, or that every qWait call would deadlock.
"""
import sys

from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, Qt


def _timer(parent, interval_ms, *, single_shot=True):
    timer = QTimer(parent)
    timer.setTimerType(Qt.TimerType.PreciseTimer)
    timer.setSingleShot(single_shot)
    timer.setInterval(max(0, int(interval_ms)))
    return timer


def _loop():
    if QCoreApplication.instance() is None:
        raise RuntimeError('Qt waits require an existing QCoreApplication')
    return QEventLoop()


def wait_ms(milliseconds):
    """Process normal Qt events for the same fixed duration as qWait(ms)."""
    loop = _loop()
    timer = _timer(loop, milliseconds)
    timer.timeout.connect(loop.quit)
    timer.start()
    try:
        loop.exec()
    finally:
        timer.stop()


def wait_until(predicate, timeout_ms=1000, interval_ms=5):
    """Return whether predicate became true, keeping Qt and workers running.

    The predicate is evaluated on the calling GUI thread. Exceptions propagate
    to the test instead of being swallowed by Qt's timer callback machinery.
    """
    if predicate():
        return True
    if timeout_ms <= 0:
        return False
    loop = _loop()
    poll = _timer(loop, max(1, interval_ms), single_shot=False)
    deadline = _timer(loop, timeout_ms)
    ready = False
    error = None

    def check():
        nonlocal ready, error
        try:
            ready = bool(predicate())
        except BaseException:
            error = sys.exc_info()
        if ready or error is not None:
            loop.quit()

    def finish():
        check()
        loop.quit()

    poll.timeout.connect(check)
    deadline.timeout.connect(finish)
    poll.start()
    deadline.start()
    try:
        loop.exec()
    finally:
        poll.stop()
        deadline.stop()
        # Qt retains Python callbacks. Break the timer -> closure -> loop cycle
        # before returning so a predicate cannot keep an entire fixture alive.
        poll.timeout.disconnect(check)
        deadline.timeout.disconnect(finish)
    if error is not None:
        raise error[1].with_traceback(error[2])
    return ready
