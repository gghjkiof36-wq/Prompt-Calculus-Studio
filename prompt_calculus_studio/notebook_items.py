"""Two-line results and aligned property rows in the shared sidebar."""
from PySide6.QtCore import Qt,QSize,QRect
from PySide6.QtGui import QColor,QFont
from PySide6.QtWidgets import QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication

DETAIL=Qt.ItemDataRole.UserRole+1
KIND=Qt.ItemDataRole.UserRole+2


class NotebookDelegate(QStyledItemDelegate):
    def __init__(self,panel):super().__init__(panel.results);self.panel=panel
    def sizeHint(self,option,index):
        if index.data(KIND)=='property':return QSize(0,34)
        return QSize(0,54 if index.data(DETAIL) else 30)
    def paint(self,painter,option,index):
        detail=index.data(DETAIL);kind=index.data(KIND)
        if not detail and kind!='property':return super().paint(painter,option,index)
        opt=QStyleOptionViewItem(option);self.initStyleOption(opt,index);title=opt.text;opt.text=''
        style=opt.widget.style() if opt.widget else QApplication.style();style.drawControl(QStyle.ControlElement.CE_ItemViewItem,opt,painter,opt.widget)
        from .theme import visual_tokens
        t=visual_tokens(dict(self.panel.manager.w.settings,**self.panel.manager.w.appearance_preview))
        painter.save();rect=option.rect.adjusted(7,3,-7,-3);font=QFont(option.font);font.setPixelSize(14)
        if kind=='property':
            painter.setFont(font);painter.setPen(QColor(t['secondary']));width=min(82,rect.width()//2)
            painter.drawText(QRect(rect.left(),rect.top(),width,rect.height()),Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,width-8))
            painter.setPen(QColor(t['text']));value_rect=rect.adjusted(width,0,0,0)
            painter.drawText(value_rect,Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(detail or '—',Qt.TextElideMode.ElideRight,value_rect.width()))
        else:
            font.setWeight(QFont.Weight.Medium);painter.setFont(font);painter.setPen(QColor(t['text']))
            painter.drawText(QRect(rect.left(),rect.top(),rect.width(),23),Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(title,Qt.TextElideMode.ElideRight,rect.width()))
            font.setWeight(QFont.Weight.Normal);font.setPixelSize(12);painter.setFont(font);painter.setPen(QColor(t['secondary']))
            painter.drawText(QRect(rect.left(),rect.top()+24,rect.width(),20),Qt.AlignmentFlag.AlignVCenter,painter.fontMetrics().elidedText(detail or '',Qt.TextElideMode.ElideRight,rect.width()))
        painter.restore()
