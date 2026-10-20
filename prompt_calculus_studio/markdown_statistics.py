"""Shared word counts; large manuscripts never recount in a key handler."""
import re
from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, QTimer, Qt
from .markdown_syntax import visible_text


def word_count(text):
    # Yield the GIL between matches; findall holds it for an entire manuscript.
    return sum(1 for _ in re.finditer(r'[\u3400-\u9fff]|\w+(?:[\x27-]\w+)*',visible_text(text)))


class Result(QObject):
    ready=Signal(object,int)


class CountJob(QRunnable):
    def __init__(self,snapshot):
        super().__init__();self.snapshot=snapshot;self.result=Result()
    def run(self):self.result.ready.emit(self.snapshot,word_count(self.snapshot.text))


class WordStatistics(QObject):
    updated=Signal()
    def __init__(self,buffer):
        super().__init__(buffer);self.snapshot=None;self.count=None;self.pending=None;self.job=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(250);self.timer.timeout.connect(self.start)

    def request(self,snapshot):
        if snapshot is self.snapshot:return self.count
        if len(snapshot.text)<256_000:
            self.snapshot=snapshot;self.count=word_count(snapshot.text);self.pending=None;self.timer.stop();return self.count
        if snapshot is not self.pending:
            self.pending=snapshot
            if not self.job:self.timer.start()
        return None

    def start(self):
        if self.job or self.pending is None:return
        self.job=CountJob(self.pending);self.job.result.ready.connect(self.finished,Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.job)

    @Slot(object,int)
    def finished(self,snapshot,count):
        self.job=None
        if snapshot is self.pending:
            self.snapshot=snapshot;self.count=count;self.pending=None;self.updated.emit()
        elif self.pending is not None:self.timer.start()
