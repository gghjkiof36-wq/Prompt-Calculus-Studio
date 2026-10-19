"""Coalesce large plain-text projections without doing Qt work off the UI thread."""
from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, Qt
from .markdown_syntax import reading_html


class ProjectionResult(QObject):
    ready=Signal(object,str,str)


class ProjectionJob(QRunnable):
    def __init__(self,request):
        super().__init__();self.request=request;self.result=ProjectionResult()

    def run(self):
        key,text,hard_breaks,lines=self.request
        try:
            body=reading_html(text,hard_breaks,lines=lines,web=True)
            self.result.ready.emit(key,body,'')
        except Exception as error:
            self.result.ready.emit(key,'',str(error))


class PlainReadingProjection(QObject):
    ready=Signal(object,str,str)

    def __init__(self,parent):
        super().__init__(parent);self.job=None;self.pending=None

    def request(self,key,text,hard_breaks,lines):
        self.pending=(key,text,hard_breaks,lines)
        if self.job is None:self.start()

    def start(self):
        self.job=ProjectionJob(self.pending)
        self.job.result.ready.connect(self.finished,Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.job)

    @Slot(object,str,str)
    def finished(self,key,body,error):
        self.job=None
        if self.pending is None:return
        if self.pending[0]!=key:self.start();return
        self.pending=None;self.ready.emit(key,body,error)
