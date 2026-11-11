"""Bounded GUI-owned thumbnail cache; background decoding never creates pixmaps."""
from collections import OrderedDict
import threading
from PySide6.QtCore import QObject,Signal,Slot,QRunnable,QThreadPool,Qt
from PySide6.QtGui import QImageReader

_cache=OrderedDict()


def close_window(window):
    """Join the host's cooperative close barrier before managed files close."""
    jobs=getattr(window,'_source_thumbnail_jobs',set())
    for job in list(jobs):
        if job.done.is_set():jobs.discard(job)
        else:job.cancel.set()
    return bool(jobs)


class Signals(QObject):
    ready=Signal(int,object,object)


class Decode(QRunnable):
    def __init__(self,paths,serial):
        super().__init__();self.paths=paths;self.serial=serial;self.cancel=threading.Event();self.done=threading.Event();self.signals=Signals()

    def run(self):
        reader=None
        try:
            for key,path in self.paths:
                if self.cancel.is_set():return
                reader=QImageReader(str(path));reader.setAutoTransform(True);reader.setAllocationLimit(128)
                size=reader.size()
                if size.isValid():
                    size.scale(144,108,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
                image=reader.read();reader=None
                if self.cancel.is_set():return
                self.signals.ready.emit(self.serial,key,image)
        finally:
            reader=None
            self.done.set()


class Thumbnails(QObject):
    ready=Signal(object,object)
    def __init__(self,parent,window):
        super().__init__(parent);self.window=window;self.job=None;self.serial=0
        parent.destroyed.connect(self.cancel)

    def cancel(self,*_):
        if self.job:self.job.cancel.set()
        self.serial+=1

    def load(self,paths):
        self.cancel();serial=self.serial
        if self.window.is_closed():return
        # Stat/decode also happen off the GUI thread. Immutable managed files
        # use their hash filename; external picker files are keyed by path and
        # their catalog revision (supplied by the picker).
        missing=[]
        for key,path in paths:
            if key in _cache:
                image=_cache.pop(key);_cache[key]=image;self.ready.emit(key,image)
            else:missing.append((key,path))
        job=Decode(missing,serial);self.job=job
        if not hasattr(self.window,'_source_thumbnail_jobs'):self.window._source_thumbnail_jobs=set()
        jobs=self.window._source_thumbnail_jobs
        for previous in list(jobs):
            if previous.done.is_set():jobs.discard(previous)
        jobs.add(job)
        job.signals.ready.connect(self.receive,Qt.ConnectionType.QueuedConnection)
        # Keep the receiver QObject alive until the worker releases its own
        # signals, and disconnect before the dialog's widgets are destroyed.
        self.destroyed.connect(job.cancel.set)
        QThreadPool.globalInstance().start(job)

    @Slot(int,object,object)
    def receive(self,serial,key,image):
        if serial!=self.serial or self.window.is_closed():return
        _cache[key]=image
        while len(_cache)>512:_cache.popitem(last=False)
        self.ready.emit(key,image)
