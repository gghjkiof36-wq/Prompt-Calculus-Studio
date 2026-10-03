"""Subtle separators with a comfortable, transparent drag target."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter,QColor,QPen
from PySide6.QtWidgets import QSplitter,QSplitterHandle
from .views import widget_colors


class GuideHandle(QSplitterHandle):
    def paintEvent(self,event):
        splitter=self.splitter()
        index=next((i for i in range(1,splitter.count()) if splitter.handle(i) is self),None)
        if splitter.guided_handles is not None and index not in splitter.guided_handles: return
        painter=QPainter(self); pen=QPen(QColor(widget_colors(self)['divider']),1); pen.setCosmetic(True); painter.setPen(pen)
        if self.orientation()==Qt.Orientation.Horizontal:
            # A short guide between columns, independent of the panel outline.
            end=min(self.height()-18,218)
            if end>18: painter.drawLine(self.width()//2,18,self.width()//2,end)
        elif self.width()>16:
            painter.drawLine(8,self.height()//2,self.width()-8,self.height()//2)


class QuietSplitter(QSplitter):
    def __init__(self,orientation=Qt.Orientation.Horizontal,parent=None,guided_handles=None):
        super().__init__(orientation,parent); self.guided_handles=guided_handles
        self.setHandleWidth(9)
    def createHandle(self): return GuideHandle(self.orientation(),self)
