"""Drive a real modal dialog after it exists, and propagate callback assertions."""
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from _modal_guard import install

install()


def drive_dialog(action, dialog_type, interact):
    errors, seen = [], []
    timer = QTimer()
    timer.setSingleShot(True)

    def visit():
        dialog = QApplication.activeModalWidget()
        if not isinstance(dialog, dialog_type):
            timer.start(10)
            return
        seen.append(type(dialog).__name__)
        try:
            interact(dialog)
        except BaseException as error:
            errors.append(error)
            dialog.reject()

    timer.timeout.connect(visit)
    timer.start(10)
    try:
        result = action()
    finally:
        timer.stop()
    if errors:
        raise errors[0]
    assert seen, f'{dialog_type.__name__} was not opened by the tested action'
    return result
