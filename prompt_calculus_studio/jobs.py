"""One background file worker, cooperative cancellation and Qt signal results."""
import threading
from .job_outcomes import run_job
from PySide6.QtCore import QObject, Signal, QRunnable, QThreadPool

class JobSignals(QObject):
    finished = Signal(object, str)

class Job(QRunnable):
    def __init__(self, function, *, finish_accepted=False):
        super().__init__()
        self.function = function
        self.finish_accepted = finish_accepted
        self.cancel = threading.Event()
        self.signals = JobSignals()

    def run(self):
        outcome = run_job(self.function, self.cancel, finish_accepted=self.finish_accepted)
        self.signals.finished.emit(outcome.value, outcome.message)

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

    def start(self, label, function, callback, failed=None, *, finish_accepted=False):
        if self.active:
            self.window.notice("檔案工作仍在處理中，請稍候或取消。")
            return False
        self.active = Job(function, finish_accepted=finish_accepted)
        self.callback = callback
        self.error_callback=failed
        self.active.signals.finished.connect(self.finished)
        self.window.notice(label)
        self.pool.start(self.active)
        return True

    def finished(self, result, error):
        callback = self.callback; failed=self.error_callback
        self.active, self.callback, self.error_callback = None,None,None
        try:
            if error:
                if failed: failed(error)
                elif error.startswith("已取消") or self.window.closing: self.window.notice(error)
                else: self.window.error(error)
            else:
                try:
                    callback(result)
                except Exception as exc:
                    self.window.error(str(exc))
        finally:
            # Receivers may fail or start the next job. Never clear that new
            # active job, and never let a receiver error strand idle listeners.
            if self.active is None and not self.window.closing:
                self.became_idle.emit()
