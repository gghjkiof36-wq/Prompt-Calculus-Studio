"""Read-only export presentation, independent of the native Windows item style."""
from PySide6.QtCore import Qt,QRectF,QSize
from PySide6.QtGui import QPainter,QColor,QFontMetrics
from PySide6.QtWidgets import QStyledItemDelegate,QHeaderView,QStyle

class ExportDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(option.rect).adjusted(1,3,-3,-3)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#353535' if selected else '#202020'))
        painter.drawRoundedRect(rect,7,7); painter.setFont(option.font)
        brush=index.data(Qt.ItemDataRole.ForegroundRole)
        painter.setPen(brush.color() if brush else QColor('#eeeeee'))
        area=rect.adjusted(10,0,-10,0)
        painter.drawText(area,Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(option.font).elidedText(str(index.data() or ''),Qt.TextElideMode.ElideRight,max(0,int(area.width()))))
        painter.restore()

    def sizeHint(self,option,index): return QSize(100,max(44,QFontMetrics(option.font).height()+20))

class ExportHeader(QHeaderView):
    def __init__(self,parent): super().__init__(Qt.Orientation.Horizontal,parent)

    def paintEvent(self,event):
        painter=QPainter(self.viewport()); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.viewport().rect(),QColor('#111111'))
        rect=QRectF(self.viewport().rect()).adjusted(1,0,-3,-2)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#303030')); painter.drawRoundedRect(rect,8,8)
        painter.setFont(self.font()); painter.setPen(QColor('#eeeeee'))
        for index in range(self.count()):
            if self.isSectionHidden(index): continue
            area=QRectF(self.sectionViewportPosition(index)+10,0,self.sectionSize(index)-20,rect.height())
            text=str(self.model().headerData(index,Qt.Orientation.Horizontal) or '')
            painter.drawText(area,Qt.AlignmentFlag.AlignCenter,self.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(0,int(area.width()))))
