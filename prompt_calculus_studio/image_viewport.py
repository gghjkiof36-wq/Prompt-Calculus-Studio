"""Image-only painting, cursor-anchored zoom and lightweight editing gestures."""
import math
from pathlib import Path
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QImageReader, QPainter, QPainterPath, QPen, QTransform
from PySide6.QtWidgets import QWidget, QSizePolicy
from .image_edits import ImageEdits, Mark, file_stamp


class ImageViewport(QWidget):
    changed=Signal()
    zoomChanged=Signal(float)
    cropChanged=Signal(bool)
    toolChanged=Signal(str)
    failed=Signal(str)
    PREVIEW_PIXELS=8*1024*1024

    def __init__(self,parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored)
        self.setMinimumSize(1,1);self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName('完整圖片預覽');self.setMouseTracking(True)
        self.image=QImage();self.edits=None;self.message='';self.scale=1.;self.offset=QPointF();self.fitted=True
        self.tool='move';self.color='#ff4545';self.stroke=4.;self.font_size=32.
        self.gesture=[];self.crop_rect=None;self._pan=None;self._space=False
        self.text_box=None
        self.refine=QTimer(self);self.refine.setSingleShot(True);self.refine.setInterval(120)
        self.refine.timeout.connect(self.refine_image)

    def text(self):return self.message

    def load(self,path):
        self.finish_text(False)
        self.refine.stop();self.path=Path(path);self.image=QImage();self.edits=None
        self.cancel_gesture();self.set_tool('move');self.message=''
        try:
            self.stamp=file_stamp(self.path)
            reader=QImageReader(str(path));reader.setAutoTransform(True);reader.setAllocationLimit(128)
            size=reader.size()
            if not size.isValid():raise ValueError('圖片已移動或無法讀取，可切換其他圖片。')
            self.native=QSize(size)
            if reader.transformation().value & 4:self.native.transpose()
            self.edits=ImageEdits(self.native)
            self.fit()
            self.decode(max(self.scale*self.devicePixelRatioF(),256/max(self.native.width(),self.native.height())))
            if self.image.isNull():raise ValueError('圖片無法讀取，可切換其他圖片。')
        except (OSError,ValueError) as exc:
            self.edits=None;self.message=str(exc) if isinstance(exc,ValueError) else '圖片已移動或無法讀取，可切換其他圖片。'
        self.changed.emit();self.update()
        return self.edits is not None

    def decode(self,scale):
        w,h=self.native.width(),self.native.height()
        scale=min(1.,scale,math.sqrt(self.PREVIEW_PIXELS/(w*h)))
        target=QSize(max(1,round(w*scale)),max(1,round(h*scale)))
        if not self.image.isNull() and self.image.width()>=target.width()-1 and self.image.height()>=target.height()-1:return
        if file_stamp(self.path)!=self.stamp:raise ValueError('原圖已變更，請重新開啟。')
        reader=QImageReader(str(self.path));reader.setAutoTransform(True);reader.setAllocationLimit(128)
        if reader.transformation().value & 4:target.transpose()
        reader.setScaledSize(target)
        image=reader.read()
        if not image.isNull():self.image=image;self.update()

    def refine_image(self):
        if not self.edits or not self.isVisible():return
        try:self.decode(self.scale*self.devicePixelRatioF())
        except (OSError,ValueError) as exc:self.failed.emit(str(exc))

    def view_transform(self):
        w,h=self.edits.state.size if self.edits else (1,1)
        return QTransform(self.scale,0,0,self.scale,
                          (self.width()-w*self.scale)/2+self.offset.x(),
                          (self.height()-h*self.scale)/2+self.offset.y())

    def image_point(self,point):
        inverse,_=self.view_transform().inverted();p=inverse.map(QPointF(point))
        w,h=self.edits.state.size
        return QPointF(max(0,min(w,p.x())),max(0,min(h,p.y())))

    def fit(self):
        if not self.edits:return
        w,h=self.edits.state.size
        self.scale=min(self.width()/w,self.height()/h,1.)
        self.fitted=True;self.offset=QPointF();self.zoomChanged.emit(self.scale)
        self.sync_text_box();self.update();self.refine.start()

    def zoom(self,factor,anchor=None):
        if not self.edits:return
        anchor=QPointF(anchor) if anchor is not None else QPointF(self.rect().center())
        inverse,_=self.view_transform().inverted();position=inverse.map(anchor)
        self.scale=max(.01,min(16.,self.scale*factor));self.fitted=False
        w,h=self.edits.state.size
        origin=anchor-position*self.scale
        self.offset=origin-QPointF((self.width()-w*self.scale)/2,(self.height()-h*self.scale)/2)
        self.clamp_offset();self.zoomChanged.emit(self.scale);self.sync_text_box();self.update();self.refine.start()

    def clamp_offset(self):
        if not self.edits:return
        w,h=self.edits.state.size
        x=max(0,(w*self.scale-self.width())/2);y=max(0,(h*self.scale-self.height())/2)
        self.offset=QPointF(max(-x,min(x,self.offset.x())),max(-y,min(y,self.offset.y())))

    def set_tool(self,tool):
        self.finish_text()
        self.cancel_gesture();self.tool=tool
        self.setCursor(Qt.CursorShape.OpenHandCursor if tool=='move' else Qt.CursorShape.CrossCursor)
        self.toolChanged.emit(tool)

    def cancel_gesture(self):
        self.gesture=[];self.crop_rect=None;self._pan=None;self.cropChanged.emit(False);self.update()

    def edit(self,operation):
        self.finish_text()
        if self.edits and operation():
            self.cancel_gesture();self.fit();self.changed.emit()

    def apply_crop(self):
        if self.crop_rect is not None:
            self.edit(lambda:self.edits.crop(self.crop_rect));self.set_tool('move')

    @property
    def pending_text(self):return self.text_box is not None and bool(self.text_box.text().strip())

    def sync_text_box(self):
        if self.text_box is not None:self.text_box.sync_view()

    def set_color(self,value):
        self.color=value
        if self.text_box is not None:self.text_box.set_color(value)

    def set_font_size(self,value):
        self.font_size=value
        if self.text_box is not None:self.text_box.set_font_size(value)

    def begin_text(self,rect):
        from .image_text import ImageTextBox
        rect=rect.normalized().intersected(QRectF(0,0,*self.edits.state.size))
        if rect.width()*self.scale<12 or rect.height()*self.scale<12:return
        self.text_box=ImageTextBox(self,rect)
        self.text_box.edited.connect(self.changed);self.text_box.finished.connect(self.finish_text)

    def finish_text(self,accept=True):
        box=self.text_box
        if box is None:return
        self.text_box=None
        if accept and box.text().strip() and self.edits:
            rect=box.image_rect
            self.edits.mark('text',((rect.left(),rect.top()),(rect.right(),rect.bottom())),box.color,box.font_size,box.text())
        box.hide();box.deleteLater();self.update();self.changed.emit()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    def paintEvent(self,event):
        painter=QPainter(self)
        if self.edits and not self.image.isNull():
            extra=Mark(self.tool,tuple((p.x(),p.y()) for p in self.gesture),self.color,self.stroke) if self.gesture and self.tool not in ('crop','text') else None
            self.edits.paint(painter,self.image,self.view_transform(),extra)
            if self.tool=='text' and len(self.gesture)>1:
                painter.setPen(QPen(self.palette().highlight().color(),1,Qt.PenStyle.DashLine))
                painter.drawRect(self.view_transform().mapRect(QRectF(self.gesture[0],self.gesture[-1]).normalized()))
            if self.crop_rect is not None:
                rect=self.view_transform().mapRect(self.crop_rect)
                shade=QPainterPath();shade.addRect(QRectF(self.rect()));shade.addRect(rect)
                painter.fillPath(shade,QColor(0,0,0,120))
                painter.setPen(QPen(QColor('white'),1));painter.drawRect(rect)
                painter.setPen(QPen(QColor(255,255,255,100),1))
                for part in (1,2):
                    x=rect.x()+rect.width()*part/3;y=rect.y()+rect.height()*part/3
                    painter.drawLine(QPointF(x,rect.top()),QPointF(x,rect.bottom()))
                    painter.drawLine(QPointF(rect.left(),y),QPointF(rect.right(),y))
        elif self.message:
            painter.setPen(self.palette().windowText().color());painter.drawText(self.rect().adjusted(24,24,-24,-24),Qt.AlignmentFlag.AlignCenter|Qt.TextFlag.TextWordWrap,self.message)

    def wheelEvent(self,event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            delta=event.angleDelta().y() or event.pixelDelta().y()
            self.zoom(1.2**(delta/120),event.position());event.accept();return
        if self.edits:
            delta=event.pixelDelta() if not event.pixelDelta().isNull() else event.angleDelta()/2
            self.offset+=QPointF(delta);self.clamp_offset();self.sync_text_box();self.update()
        event.accept()

    def mousePressEvent(self,event):
        if not self.edits:return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        if event.button()==Qt.MouseButton.MiddleButton or event.button()==Qt.MouseButton.LeftButton and (self.tool=='move' or self._space):
            self._pan=event.position();self.setCursor(Qt.CursorShape.ClosedHandCursor)
        elif event.button()==Qt.MouseButton.LeftButton:
            self.finish_text()
            point=self.image_point(event.position())
            self.gesture=[point]
        event.accept()

    def mouseMoveEvent(self,event):
        if self._pan is not None:
            self.offset+=event.position()-self._pan;self._pan=event.position();self.clamp_offset();self.sync_text_box();self.update()
        elif self.gesture:
            point=self.image_point(event.position())
            if self.tool=='pen':
                if (point-self.gesture[-1]).manhattanLength()*self.scale>=2:self.gesture.append(point)
            else:self.gesture=self.gesture[:1]+[point]
            if self.tool=='crop':
                self.crop_rect=QRectF(self.gesture[0],point).normalized();self.cropChanged.emit(self.crop_rect.width()>=2 and self.crop_rect.height()>=2)
            self.update()
        event.accept()

    def mouseReleaseEvent(self,event):
        if self._pan is not None:
            self._pan=None;self.setCursor(Qt.CursorShape.OpenHandCursor if self.tool=='move' else Qt.CursorShape.CrossCursor)
        elif self.gesture:
            if self.tool=='text' and len(self.gesture)>1:
                self.begin_text(QRectF(self.gesture[0],self.gesture[-1]))
            elif self.tool!='crop' and self.tool!='text':
                self.edits.mark(self.tool,tuple((p.x(),p.y()) for p in self.gesture),self.color,self.stroke)
                self.changed.emit()
            self.gesture=[];self.update()
        event.accept()

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape and self.text_box is not None:self.finish_text(False);event.accept();return
        if event.key()==Qt.Key.Key_Space:self._space=True;event.accept();return
        if event.key()==Qt.Key.Key_Escape and (self.gesture or self.crop_rect is not None):self.cancel_gesture();event.accept();return
        if event.key() in (Qt.Key.Key_Enter,Qt.Key.Key_Return) and self.crop_rect is not None:self.apply_crop();event.accept();return
        super().keyPressEvent(event)

    def keyReleaseEvent(self,event):
        if event.key()==Qt.Key.Key_Space:self._space=False;event.accept();return
        super().keyReleaseEvent(event)

    def focusOutEvent(self,event):
        self._space=False;self._pan=None
        super().focusOutEvent(event)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.fitted:self.fit()
        else:self.clamp_offset();self.sync_text_box();self.update()

    def hideEvent(self,event):
        self.refine.stop();self._pan=None;self._space=False
        super().hideEvent(event)
