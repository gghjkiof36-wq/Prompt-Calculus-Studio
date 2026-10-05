"""Retain bounded source pixels and paint smoothly at the actual Canvas scale."""
from PySide6.QtCore import Qt,QRectF,Signal
from PySide6.QtGui import QImageReader,QImage,QPainter
from PySide6.QtWidgets import QSizePolicy
from .image_drop import ImageDropLabel


class SourcePreview(ImageDropLabel):
    clicked=Signal()
    def __init__(self,store):
        super().__init__(store); self.image=QImage(); self.source_key=None
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)

    def load_path(self,path):
        try:
            stat=path.stat() if path else None
            key=(str(path),stat.st_mtime_ns,stat.st_size) if stat else None
        except OSError: key=None
        if key==self.source_key and key is not None: return
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

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.image.isNull(): return
        area=QRectF(self.contentsRect()).adjusted(2,2,-2,-2)
        size=self.image.size().scaled(area.size().toSize(),Qt.AspectRatioMode.KeepAspectRatio)
        target=QRectF(0,0,size.width(),size.height()); target.moveCenter(area.center())
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(target,self.image)

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton: self.clicked.emit()
        else: super().mouseReleaseEvent(event)
