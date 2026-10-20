"""Coalesce large plain-text projections without doing Qt work off the UI thread."""
from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, Qt
from .markdown_syntax import reading_html
import uuid


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


class RichResult(QObject):
    ready = Signal(object, object, str)


class RichJob(QRunnable):
    def __init__(self, request):
        super().__init__()
        self.request = request
        self.result = RichResult()

    def run(self):
        key, text, path, root, hard_breaks, overrides = self.request
        try:
            from .markdown_render import render
            from .markdown_links import Notes
            notes = Notes(root, lambda target: overrides[target] if target in overrides else Notes.disk(target))
            marker = uuid.uuid4().hex
            projection = render(text, path, root,
                lambda entry, index: f'<!--pcs-resource:{marker}:{index}-->', hard_breaks, notes)
            self.result.ready.emit(key, (projection, marker), '')
        except Exception as error:
            self.result.ready.emit(key, None, str(error))


class RichReadingProjection(QObject):
    ready = Signal(object, object, str)

    def __init__(self, parent):
        super().__init__(parent)
        self.job = self.pending = None

    def request(self, *request):
        self.pending = request
        if self.job is None:
            self.start()

    def start(self):
        self.job = RichJob(self.pending)
        self.job.result.ready.connect(self.finished, Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.job)

    @Slot(object, object, str)
    def finished(self, key, result, error):
        self.job = None
        if self.pending is None:
            return
        if self.pending[0] != key:
            self.start()
            return
        self.pending = None
        self.ready.emit(key, result, error)
