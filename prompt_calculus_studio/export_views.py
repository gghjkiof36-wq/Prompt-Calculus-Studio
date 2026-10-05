"""Read-only export presentation, independent of the native Windows item style."""
from PySide6.QtCore import Qt,QRectF,QSize
from PySide6.QtGui import QPainter,QColor,QFontMetrics
from PySide6.QtWidgets import QStyledItemDelegate,QHeaderView,QStyle
from .theme import visual_tokens


def _tokens(widget):
    # A transparent item-view style can supply a black Base even when the
    # containing PCS window has a light palette. Use the same source as its UI.
    while widget is not None:
        state=getattr(widget,'state',None)
        if isinstance(state,dict): return visual_tokens(state.get('settings'))
        owner=getattr(widget,'window',None)
        state=getattr(owner,'state',None)
        if isinstance(state,dict): return visual_tokens(state.get('settings'))
        widget=widget.parent()
    return visual_tokens()


def _separator(painter,rect,tokens):
    color=QColor(tokens['divider'])
    painter.fillRect(QRectF(rect.left(),rect.bottom()-1,rect.width(),1),color)

class ExportDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(option.rect); tokens=_tokens(self.parent())
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        painter.fillRect(rect,QColor(tokens['selected' if selected else 'base']))
        _separator(painter,rect,tokens); painter.setFont(option.font)
        brush=index.data(Qt.ItemDataRole.ForegroundRole)
        painter.setPen(brush.color() if brush else QColor(tokens['text']))
        area=rect.adjusted(10,0,-10,0)
        painter.drawText(area,Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,
                         QFontMetrics(option.font).elidedText(str(index.data() or ''),Qt.TextElideMode.ElideRight,max(0,int(area.width()))))
        painter.restore()

    def sizeHint(self,option,index): return QSize(100,max(44,QFontMetrics(option.font).height()+20))

class ExportHeader(QHeaderView):
    def __init__(self,parent): super().__init__(Qt.Orientation.Horizontal,parent)

    def paintEvent(self,event):
        painter=QPainter(self.viewport()); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        tokens=_tokens(self); rect=QRectF(self.viewport().rect())
        painter.fillRect(rect,QColor(tokens['base'])); _separator(painter,rect,tokens)
        painter.setFont(self.font()); painter.setPen(QColor(tokens['secondary']))
        for index in range(self.count()):
            if self.isSectionHidden(index): continue
            area=QRectF(self.sectionViewportPosition(index)+10,0,self.sectionSize(index)-20,rect.height())
            text=str(self.model().headerData(index,Qt.Orientation.Horizontal) or '')
            painter.drawText(area,Qt.AlignmentFlag.AlignVCenter|Qt.AlignmentFlag.AlignLeft,self.fontMetrics().elidedText(text,Qt.TextElideMode.ElideRight,max(0,int(area.width()))))
