"""One background file worker, cooperative cancellation and Qt signal results."""
import threading
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool

class JobSignals(QObject):
    finished = Signal(object, str)

class Job(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.cancel = threading.Event()
        self.signals = JobSignals()

    def run(self):
        try:
            result=self.function(self.cancel)
            if self.cancel.is_set(): raise ValueError('已取消操作。')
            self.signals.finished.emit(result, "")
        except Exception as exc:
            self.signals.finished.emit(None,str(exc))

class Jobs(QObject):
    became_idle = Signal()
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(1)
        self.active = None
        self.callback = None
        self.error_callback=None

    def start(self, label, function, callback, failed=None):
        if self.active:
            self.window.notice("檔案工作仍在處理中，請稍候或取消。")
            return False
        self.active = Job(function)
        self.callback = callback
        self.error_callback=failed
        self.active.signals.finished.connect(self.finished)
        self.window.notice(label)
        self.pool.start(self.active)
        return True

    def finished(self, result, error):
        callback = self.callback; failed=self.error_callback
        self.active, self.callback, self.error_callback = None,None,None
        if error:
            if failed: failed(error)
            elif error.startswith("已取消") or self.window.closing: self.window.notice(error)
            else: self.window.error(error)
        else:
            try:
                callback(result)
            except Exception as exc:
                self.window.error(str(exc))
        if self.active is None and not self.window.closing:
            self.became_idle.emit()
