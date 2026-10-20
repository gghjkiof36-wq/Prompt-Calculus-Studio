"""Read and validate external files without blocking the document event loop."""
from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, Qt
from shiboken6 import isValid
from .local_documents import paths
from .markdown_source import MAX_BYTES


class FileResult(QObject):
    ready=Signal(object,object)


class FileProbe(QRunnable):
    def __init__(self,key,stamp):
        super().__init__();self.key=key;self.stamp=stamp;self.result=FileResult()

    def run(self):
        path,_baseline=self.key
        try:
            target=paths.checked(path.parent,path.name)
            info=target.stat()
            stamp=(str(target),info.st_dev,info.st_ino,info.st_mtime_ns,info.st_size)
            raw=None if stamp==self.stamp else paths.read_bytes(target,MAX_BYTES)[0]
            result=(stamp,raw,'')
        except (ValueError,OSError) as error:
            result=(None,None,str(error))
        self.result.ready.emit(self.key,result)


class ExternalRefresh(QObject):
    applied=Signal(object,str)

    def __init__(self,buffer):
        super().__init__(buffer);self.buffer=buffer;self.job=None

    def request(self):
        buffer=self.buffer
        if self.job or buffer.opening or buffer.composing or getattr(buffer,'operation_error',False):return
        key=(buffer.path,buffer.baseline)
        self.job=FileProbe(key,None if buffer.external_error else buffer.external_stamp)
        self.job.result.ready.connect(self.finished,Qt.ConnectionType.QueuedConnection)
        QThreadPool.globalInstance().start(self.job)

    @Slot(object,object)
    def finished(self,key,result):
        self.job=None;buffer=self.buffer
        if key!=(buffer.path,buffer.baseline):return
        page=next((view for view in buffer.views if isValid(view)),None)
        if page is None:return
        before=page.error
        page.refresh_external(result)
        self.applied.emit(buffer,before)
