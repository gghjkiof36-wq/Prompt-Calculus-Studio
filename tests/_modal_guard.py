"""Test-only deadline for real Qt modal loops; never turn a timeout into cancel.

Install before discovery. Normal singleShot-driven exec calls run unchanged.
PCS_TEST_MODAL_GUARD=0 is reserved for the guard-on/off comparison runner.
"""
import functools
import inspect
import os
import sys
import traceback

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QDialog, QMenu, QMessageBox

events = []
current_test = None
_installed = False
_timeout_ms = 5000


def _test_id():
    if current_test:
        return current_test
    for frame in inspect.stack():
        case = frame.frame.f_locals.get('self')
        if hasattr(case, '_testMethodName'):
            return case.id()
    return '<outside unittest>'


def _wrap(original):
    @functools.wraps(original)
    def guarded(dialog, *args, **kwargs):
        timer = QTimer()
        timer.setSingleShot(True)
        timer.setTimerType(Qt.TimerType.PreciseTimer)
        expired = []
        caller = ''.join(traceback.format_stack()[:-1])
        identity = dict(test_id=_test_id(), dialog=type(dialog).__name__,
                        object_name=dialog.objectName(), title=dialog.windowTitle())

        def timeout():
            record = dict(identity, stack=caller, timeout_ms=_timeout_ms)
            events.append(record)
            message = (f"Modal timeout after {_timeout_ms / 1000:g}s: "
                       f"{identity['dialog']} objectName={identity['object_name']!r} "
                       f"title={identity['title']!r} test={identity['test_id']}\n{caller}")
            expired.append(message)
            print(message, file=sys.stderr, flush=True)
            if isinstance(dialog, QDialog):
                dialog.reject()
            else:
                dialog.close()

        timer.timeout.connect(timeout)
        timer.start(_timeout_ms)
        try:
            result = original(dialog, *args, **kwargs)
        finally:
            timer.stop()
        if expired:
            raise AssertionError(expired[0])
        return result
    return guarded


def install(timeout_ms=5000):
    global _installed, _timeout_ms
    if os.environ.get('PCS_TEST_MODAL_GUARD') == '0':
        return
    _timeout_ms = timeout_ms
    if _installed:
        return
    # Capture all native entry points before assigning: QMessageBox can inherit
    # QDialog.exec depending on the PySide version. PCS Dialog delegates here.
    originals = [(cls, name, getattr(cls, name)) for cls in (QDialog, QMessageBox, QMenu)
                 for name in ('exec', 'exec_') if hasattr(cls, name)]
    for cls, name, original in originals:
        setattr(cls, name, _wrap(original))
    _installed = True
