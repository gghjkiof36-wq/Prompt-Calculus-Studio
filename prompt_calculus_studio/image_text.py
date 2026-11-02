"""An in-image text frame: native text selection/IME, four resize handles."""
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QTextOption
from PySide6.QtWidgets import QFrame, QTextEdit, QWidget
from .image_edits import text_font


class TextEntry(QTextEdit):
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            self.parentWidget().finished.emit(False);event.accept();return
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self.parentWidget().finished.emit(True);event.accept();return
        super().keyPressEvent(event)

    def wheelEvent(self,event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            view=self.parentWidget().view
            position=QPointF(view.mapFromGlobal(event.globalPosition().toPoint()))
            delta=event.angleDelta().y() or event.pixelDelta().y()
            view.zoom(1.2**(delta/120),position);event.accept();return
        super().wheelEvent(event)


class ImageTextBox(QWidget):
    edited=Signal()
    finished=Signal(bool)
    HANDLE=6

    def __init__(self,view,rect):
        super().__init__(view)
        self.view=view;self.image_rect=QRectF(rect);self.drag=None;self._syncing=False
        self.color=view.color;self.font_size=view.font_size
        self.setMouseTracking(True);self.setAccessibleName('文字框，拖曳四角調整大小')
        self.entry=TextEntry(self);self.entry.setObjectName('ImageTextEntry')
        self.entry.setAccessibleName('圖片上的文字');self.entry.setAcceptRichText(False)
        self.entry.setFrameShape(QFrame.Shape.NoFrame)
        self.entry.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.entry.document().setDocumentMargin(0);self.entry.document().setUseDesignMetrics(True)
        self.entry.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.entry.viewport().setAutoFillBackground(False)
        self.entry.textChanged.connect(self.content_changed)
        self.sync_view();self.show();self.entry.setFocus(Qt.FocusReason.MouseFocusReason)

    def text(self):return self.entry.toPlainText()

    def set_color(self,value):
        self.color=value;self.sync_view();self.edited.emit()

    def set_font_size(self,value):
        self.font_size=value;self.sync_view();self.content_changed()

    def sync_view(self):
        if self._syncing:return
        self._syncing=True
        try:
            rect=self.view.view_transform().mapRect(self.image_rect).toAlignedRect()
            self.setGeometry(rect.adjusted(-self.HANDLE,-self.HANDLE,self.HANDLE,self.HANDLE))
            self.entry.setGeometry(self.HANDLE,self.HANDLE,rect.width(),rect.height())
            font=text_font(round(self.font_size))
            # Fractional points keep wrapping stable while the image is zoomed.
            font.setPointSizeF(self.font_size*self.view.scale*72/self.entry.logicalDpiY())
            self.entry.document().setDefaultFont(font);self.entry.setFont(font)
            self.entry.setStyleSheet('QTextEdit#ImageTextEntry { background: transparent; border: 0; border-radius: 0; padding: 0; color: '+self.color+'; }')
            self.raise_();self.update()
        finally:self._syncing=False

    def content_changed(self):
        if self._syncing:return
        # Grow with typed lines when there is room, without changing the font.
        height=self.entry.document().size().height()/max(.01,self.view.scale)
        room=self.view.edits.state.size[1]-self.image_rect.top()
        if height>self.image_rect.height()+1 and room>self.image_rect.height()+1:
            self.image_rect.setHeight(min(room,height+2));self.sync_view()
        self.edited.emit()

    def frame_rect(self):
        return QRectF(self.rect()).adjusted(self.HANDLE-.5,self.HANDLE-.5,-self.HANDLE+.5,-self.HANDLE+.5)

    def corners(self):
        rect=self.frame_rect()
        return (rect.topLeft(),rect.topRight(),rect.bottomRight(),rect.bottomLeft())

    def corner_at(self,point):
        return next((i for i,p in enumerate(self.corners()) if abs(p.x()-point.x())<=self.HANDLE and abs(p.y()-point.y())<=self.HANDLE),None)

    def paintEvent(self,event):
        painter=QPainter(self);color=self.palette().highlight().color()
        painter.setPen(QPen(color,1,Qt.PenStyle.DashLine));painter.drawRect(self.frame_rect())
        painter.setPen(QPen(color,1));painter.setBrush(self.palette().base())
        for point in self.corners():painter.drawRect(QRectF(point.x()-3,point.y()-3,6,6))

    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton:return
        corner=self.corner_at(event.position())
        self.drag=(corner,event.globalPosition(),QRectF(self.image_rect))
        self.entry.setFocus(Qt.FocusReason.MouseFocusReason);event.accept()

    def mouseMoveEvent(self,event):
        if self.drag is None:
            corner=self.corner_at(event.position())
            cursor=Qt.CursorShape.SizeFDiagCursor if corner in (0,2) else Qt.CursorShape.SizeBDiagCursor if corner in (1,3) else Qt.CursorShape.SizeAllCursor
            self.setCursor(cursor);return
        corner,start,original=self.drag
        delta=(event.globalPosition()-start)/max(.01,self.view.scale)
        rect=QRectF(original);w,h=self.view.edits.state.size
        if corner is None:
            rect.moveLeft(max(0,min(w-rect.width(),original.left()+delta.x())))
            rect.moveTop(max(0,min(h-rect.height(),original.top()+delta.y())))
        else:
            minimum_w=min(24.,original.width());minimum_h=min(20.,original.height())
            if corner in (0,3):rect.setLeft(max(0,min(original.right()-minimum_w,original.left()+delta.x())))
            else:rect.setRight(min(w,max(original.left()+minimum_w,original.right()+delta.x())))
            if corner in (0,1):rect.setTop(max(0,min(original.bottom()-minimum_h,original.top()+delta.y())))
            else:rect.setBottom(min(h,max(original.top()+minimum_h,original.bottom()+delta.y())))
        self.image_rect=rect;self.sync_view();self.edited.emit();event.accept()

    def mouseReleaseEvent(self,event):
        self.drag=None;event.accept()
