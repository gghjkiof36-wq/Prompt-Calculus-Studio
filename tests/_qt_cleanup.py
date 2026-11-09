"""Wait for the real Window shutdown before deleting a fixture's directory."""
import time

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QTest

from _modal_guard import install

install()


def close_window(window, timeout=2.0):
    window.close()
    deadline = time.monotonic() + timeout
    while True:
        QCoreApplication.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        active = window.jobs.active
        if getattr(window, '_close_completed', False) and not active:
            return
        if time.monotonic() >= deadline:
            completion = getattr(window, 'completion', None)
            raise AssertionError(
                f'Window shutdown exceeded {timeout}s: '
                f'_close_completed={getattr(window, "_close_completed", False)}, '
                f'jobs.active={active!r}, function={getattr(active, "function", None)!r}, '
                f'completion.tasks={getattr(completion, "tasks", None)!r}, '
                f'recent.collecting={getattr(window.recent, "collecting", None)!r}')
        QTest.qWait(10)
