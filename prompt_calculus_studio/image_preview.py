"""Retain bounded source pixels and paint smoothly at the actual Canvas scale."""
from time import monotonic
from contextlib import contextmanager
from os import stat as file_stat
from PySide6.QtCore import Qt,QRectF,Signal,QTimer
from PySide6.QtGui import QImageReader,QImage,QPainter
from PySide6.QtWidgets import QSizePolicy,QApplication
from .image_drop import ImageDropLabel
from .preview_requests import use_worker,decoded_bytes,worker_batch
from .preview_loader import scope

_source_batch = None


def load_sources(requests):
    """One synchronous Canvas refresh, classified as one image batch."""
    owners={};changed=[]
    for widget,path,source in requests:
        host=widget.preview_window
        host_key=(id(host),id(widget.store))
        if host_key not in owners:owners[host_key]=scope(host,widget.store)
        owner=owners[host_key];prepared=None
        if widget.source_key is not None and path:
            text=str(path)
            try:
                stat=file_stat(text);key=(text,stat.st_mtime_ns,stat.st_size)
            except OSError:stat=key=None
            if key==widget.source_key and key is not None and (widget.pending_scope is None or widget.pending_scope==owner):
                continue
            prepared=(stat,key)
        changed.append((widget,path,source,owner,prepared))
    # Warm hits never classify, enter an adapter, or rebuild a request. The
    # existing replacement check remains one stat per card, including hits.
    if not changed:return
    deferred=worker_batch([r[1] for r in changed],[r[2] for r in changed])
    for index,(widget,path,source,owner,prepared) in enumerate(changed):
        widget._load_path(path,source,index in deferred,owner,prepared)


@contextmanager
def source_batch():
    global _source_batch
    previous=_source_batch
    if previous is not None:
        yield
        return
    requests=[];_source_batch=requests
    try:yield
    finally:_source_batch=previous
    load_sources(requests)


class SourcePreview(ImageDropLabel):
    clicked=Signal()
    def __init__(self,store,window=None):
        super().__init__(store); self.image=QImage(); self.source_key=None
        self.preview_window=window;self.adapter=None
        self.pending_path=None
        self.pending_version=None;self.pending_bytes=0;self.pending_scope=None
        self.load_timer=QTimer(self);self.load_timer.setSingleShot(True)
        self.load_timer.timeout.connect(self.request_preview)
        self._press=None; self._last_click=None
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)

    def load_path(self,path,source=None):
        if _source_batch is not None:
            _source_batch.append((self,path,source));return
        from . import preview_requests
        background=preview_requests.WORKER_BATCH_MIN<=1
        self._load_path(path,source,background)

    def _load_path(self,path,source=None,background=False,owner=None,prepared=None):
        # A new large source already has receipt dimensions. Its existing
        # zero-delay dispatch can obtain the stamp one card at a time, avoiding
        # a bulk stat callback. Warm and small-image call sites stay unchanged.
        cold=bool(background and self.source_key is None and path and
                  decoded_bytes(source) and use_worker(path,0,source))
        if prepared is not None:
            stat,key=prepared
        elif cold:
            stat=None;key=(str(path),None,None)
        else:
            try:
                stat=file_stat(str(path)) if path else None
                key=(str(path),stat.st_mtime_ns,stat.st_size) if stat else None
            except OSError: key=None
        if key==self.source_key and key is not None:
            if self.pending_scope is None:return
            if self.pending_scope==(owner if owner is not None else scope(self.preview_window,self.store)):return
        if self.pending_path is not None:self.load_timer.stop()
        self.pending_path=None;self.pending_scope=None
        if self.adapter is not None:self.adapter.load([])
        if key and background and (cold or use_worker(path,stat.st_size,source)):
            same_source=self.source_key and key[0]==self.source_key[0]
            self.source_key=key
            if not same_source:self.image=QImage()
            self.setText('')
            self.pending_path=path
            self.pending_version=(stat.st_mtime_ns,stat.st_size,stat.st_ctime_ns,stat.st_ino) if stat else None
            self.pending_bytes=decoded_bytes(source)
            self.pending_scope=owner if owner is not None else scope(self.preview_window,self.store)
            self.load_timer.start(0)
            self.update();return
        self.source_key=key; self.image=QImage()
        if key:
            reader=QImageReader(str(path)); reader.setAutoTransform(True); reader.setAllocationLimit(128)
            size=reader.size()
            if size.isValid():
                if max(size.width(),size.height())>2048:
                    size.scale(2048,2048,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size)
                self.image=reader.read()
        self.setText('' if not self.image.isNull() else ('圖片無法讀取' if path else '尚未選擇圖片\n點選或拖入'))
        self.update()

    def request_preview(self):
        path=self.pending_path
        self.pending_path=None
        if path is None or (self.preview_window is not None and self.preview_window.is_closed()):return
        from .preview_loader import PreviewAdapter,scope
        # The deferred request belongs to the document that called load_path,
        # not whichever document happens to own the window when the timer runs.
        if self.pending_scope!=scope(self.preview_window,self.store):
            self.source_key=None;self.image=QImage()
            self.setText('');self.update();return
        if self.pending_version is None:
            try:
                stat=path.stat()
            except OSError:
                self.source_key=None;self.image=QImage()
                self.setText('圖片無法讀取');self.update();return
            self.pending_version=(stat.st_mtime_ns,stat.st_size,stat.st_ctime_ns,stat.st_ino)
            self.source_key=(str(path),stat.st_mtime_ns,stat.st_size)
        if self.adapter is None:
            self.adapter=PreviewAdapter(self,self.preview_window,self.store,'source')
            self.adapter.ready.connect(self.image_ready)
        self.adapter.load([(0,path,'',2048,2048,'shrink',self.pending_bytes)],versions={0:self.pending_version})

    def hideEvent(self,event):
        self.load_timer.stop()
        super().hideEvent(event)

    def showEvent(self,event):
        super().showEvent(event)
        if self.pending_path is not None:self.load_timer.start(0)

    def image_ready(self,slot,image,status):
        self.image=image
        self.setText('' if not image.isNull() else '圖片無法讀取')
        self.update()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.image.isNull(): return
        area=QRectF(self.contentsRect()).adjusted(2,2,-2,-2)
        size=self.image.size().scaled(area.size().toSize(),Qt.AspectRatioMode.KeepAspectRatio)
        target=QRectF(0,0,size.width(),size.height()); target.moveCenter(area.center())
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target,self.image)

    def mousePressEvent(self,event):
        self._press=None
        if event.button()==Qt.MouseButton.LeftButton:
            # Accept the press so the graphics proxy also delivers its release.
            self._press=event.globalPosition(); event.accept()
        else:super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self._press is not None:
            if (event.globalPosition()-self._press).manhattanLength()>=QApplication.startDragDistance():self._press=None
            event.accept()
        else:super().mouseMoveEvent(event)

    def activate(self,event):
        self._last_click=(monotonic(),event.globalPosition())
        self.clicked.emit()

    def mouseReleaseEvent(self,event):
        pressed=self._press; self._press=None
        if event.button()==Qt.MouseButton.LeftButton:
            if (pressed is not None and self.rect().contains(event.position().toPoint()) and
                    (event.globalPosition()-pressed).manhattanLength()<QApplication.startDragDistance()):self.activate(event)
            event.accept()
        else:super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return super().mouseDoubleClickEvent(event)
        self._press=None
        last=self._last_click
        if (last is None or (monotonic()-last[0])*1000>QApplication.doubleClickInterval() or
                (event.globalPosition()-last[1]).manhattanLength()>=QApplication.startDragDistance()):self.activate(event)
        event.accept()
