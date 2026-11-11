"""Wait for the real background import and queued GUI completion, never mock IO."""
import time
from PySide6.QtTest import QTest


def wait_import(window,timeout=10):
    limit=time.monotonic()+timeout
    while window.jobs.active and time.monotonic()<limit:QTest.qWait(5)
    assert window.jobs.active is None,'Image import did not finish'
    QTest.qWait(1)
