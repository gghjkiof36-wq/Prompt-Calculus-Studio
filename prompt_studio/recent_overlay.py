"""Canvas-local temporary result browser. Closing never acts on the canvas."""
from PySide6.QtCore import QEvent, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QRegion
from PySide6.QtWidgets import QApplication, QFrame, QVBoxLayout, QWidget


class RecentOverlay(QWidget):
    finished=Signal(int)

    def __init__(self,parent,page):
        super().__init__(parent)
        self.page=page; self._finished=False; self.previous_focus=QApplication.focusWidget()
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus); self.setAccessibleName('最近生成')
        self.surface=QFrame(self); self.surface.setObjectName('RecentOverlaySurface')
        self.body=QVBoxLayout(self.surface); self.body.setContentsMargins(0,0,0,0); self.body.addWidget(page)
        parent.installEventFilter(self)
        self.fit(); self.show(); self.raise_(); page.show(); self.setFocus()

    def fit(self):
        self.setGeometry(self.parentWidget().rect())
        gap=24 if self.width()>=1000 else 12
        width=min(1600,max(1,self.width()-gap*2)); height=max(1,self.height()-gap*2)
        self.surface.setGeometry((self.width()-width)//2,gap,width,height)
        path=QPainterPath(); path.addRoundedRect(QRectF(self.surface.rect()),18,18)
        self.surface.setMask(QRegion(path.toFillPolygon().toPolygon()))

    def eventFilter(self,watched,event):
        if watched is self.parentWidget() and event.type()==QEvent.Type.Resize:self.fit()
        return super().eventFilter(watched,event)

    def paintEvent(self,event):
        painter=QPainter(self); color=QColor(0,0,0,144)
        painter.fillRect(self.rect(),color)

    def mousePressEvent(self,event):
        # Consume the whole outside click; never start a drag in the canvas.
        event.accept()

    def mouseReleaseEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and not self.surface.geometry().contains(event.position().toPoint()):self.reject()
        event.accept()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.reject();event.accept()
        else:super().keyPressEvent(event)

    def finish(self,result):
        if self._finished:return
        self._finished=True; self.parentWidget().removeEventFilter(self); self.hide(); self.finished.emit(result)
        from shiboken6 import isValid
        focus=self.previous_focus
        if focus is not None and isValid(focus) and focus.isVisible():focus.setFocus()

    def accept(self):self.finish(1)
    def reject(self):self.finish(0)
    def closeEvent(self,event):self.reject();event.accept()
    def hideEvent(self,event):
        super().hideEvent(event)
        if not self._finished:self.finish(0)
