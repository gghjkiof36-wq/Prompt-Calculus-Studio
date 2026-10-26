"""Chat-only snapshot writer. The shared CAS writer remains the authority."""
from threading import Event

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal

from ..local_documents import io

_pool = None


def pool():
    global _pool
    if _pool is None:
        _pool = QThreadPool()
        _pool.setMaxThreadCount(2)
    return _pool


class _Signals(QObject):
    done = Signal(object)


class Checkpoint(QRunnable):
    def __init__(self, token, path, snapshot, receipt):
        super().__init__()
        self.token, self.path = token, path
        self.snapshot, self.receipt = snapshot, receipt
        self.finished = Event()
        self.result = None
        self.signals = _Signals()

    def run(self):
        try:
            self.result = io.save(self.path.parent, self.path.name, self.snapshot, self.receipt)
        except Exception as error:
            self.result = io.SaveResult('failed', error=str(error) if isinstance(error, (OSError, ValueError))
                                        else '對話背景保存失敗；內容仍保留在畫面。')
        finally:
            # A blocking explicit-save barrier can consume this exact result
            # without pumping/reentering the GUI event loop.
            self.finished.set()
            self.signals.done.emit(self.token)
