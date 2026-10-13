"""A bounded, screen-sized local image view shared by recent and Canvas images."""
from pathlib import Path
from PySide6.QtCore import Qt,QSize,QEvent
from PySide6.QtGui import QImageReader,QPixmap
from PySide6.QtWidgets import QLabel,QSizePolicy
from .widgets import StudioDialog,button,row


def viewer_size(available):
    return QSize(max(1,min(available.width()-40,round(available.width()*.90))),
                 max(1,min(available.height()-40,round(available.height()*.90))))


class ImageViewerDialog(StudioDialog):
    def __init__(self,parent,path,name=''):
        super().__init__(parent)
        self.setWindowTitle(name or Path(path).name)
        self.heading.setToolTip(self.windowTitle());self.heading.setMinimumWidth(0)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        available=parent.screen().availableGeometry().size() if parent else self.screen().availableGeometry().size()
        self.resize(viewer_size(available))
        self.display=QLabel();self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);self.display.setMinimumSize(1,1)
        self.display.setAccessibleName('完整圖片預覽')
        self.display.installEventFilter(self)
        self.body.addWidget(self.display,1)
        self.close_button=button('關閉',self.reject);self.body.addLayout(row(None,self.close_button))
        reader=QImageReader(str(path));reader.setAutoTransform(True);reader.setAllocationLimit(128)
        size=reader.size()
        if not size.isValid():raise ValueError('原圖已移動、暫存已清除或圖片無法讀取。')
        # Decode once at the useful screen resolution; resizing reuses pixels.
        limit=available*self.devicePixelRatioF();size.scale(limit,Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size);self.picture=QPixmap.fromImage(reader.read())
        if self.picture.isNull():raise ValueError('圖片無法讀取。')

    def fit_image(self):
        if hasattr(self,'picture') and not self.picture.isNull():
            self.display.setPixmap(self.picture.scaled(self.display.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def showEvent(self,event):
        super().showEvent(event);self.layout().activate();self.fit_image()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.fit_image()

    def eventFilter(self,watched,event):
        if watched is self.display and event.type()==QEvent.Type.Resize:self.fit_image()
        return super().eventFilter(watched,event)


def show_image(parent,path,name=''):
    try:dialog=ImageViewerDialog(parent,path,name)
    except ValueError as exc:
        parent.notice(str(exc));return
    dialog.exec()
