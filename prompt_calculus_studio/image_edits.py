"""Small reversible geometry/vector edits; pixels are materialized only on save."""
from dataclasses import dataclass, field, replace
from functools import lru_cache
import json
import math
import os
from pathlib import Path
from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QSaveFile, QIODevice
from PySide6.QtGui import (QColor, QFont, QImage, QImageReader, QImageWriter, QPainter,
                          QPainterPath, QPen, QTransform, QTextDocument, QTextOption,
                          QAbstractTextDocumentLayout, QPalette)


@dataclass(frozen=True)
class Mark:
    kind: str
    points: tuple
    color: str
    width: float
    text: str = ''
    transform: QTransform = field(default_factory=QTransform)


@dataclass(frozen=True)
class EditState:
    size: tuple
    transform: QTransform
    marks: tuple = ()


def file_stamp(path):
    stat=Path(path).stat()
    return stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns


class ImageEdits:
    def __init__(self, size):
        self.states=[EditState((size.width(),size.height()),QTransform())]
        self.index=0
        self.saved=self.state

    @property
    def state(self):return self.states[self.index]
    @property
    def dirty(self):return self.state!=self.saved

    def push(self, state):
        if state==self.state:return False
        self.states[self.index+1:]=[state];self.index+=1
        return True

    def undo(self):
        if self.index:self.index-=1;return True
        return False

    def redo(self):
        if self.index+1<len(self.states):self.index+=1;return True
        return False

    def reset(self):return self.push(self.states[0])

    def geometry(self, operation):
        w,h=self.state.size
        transforms={
            'right':(QTransform(0,1,-1,0,h,0),(h,w)),
            'left':(QTransform(0,-1,1,0,0,w),(h,w)),
            'half':(QTransform(-1,0,0,-1,w,h),(w,h)),
            'horizontal':(QTransform(-1,0,0,1,w,0),(w,h)),
            'vertical':(QTransform(1,0,0,-1,0,h),(w,h)),
        }
        delta,size=transforms[operation]
        return self.push(replace(self.state,size=size,transform=self.state.transform*delta))

    def crop(self, rect):
        rect=rect.normalized().toAlignedRect().intersected(QRectF(0,0,*self.state.size).toRect())
        if rect.width()<2 or rect.height()<2:return False
        delta=QTransform.fromTranslate(-rect.x(),-rect.y())
        return self.push(replace(self.state,size=(rect.width(),rect.height()),transform=self.state.transform*delta))

    def mark(self, kind, points, color, width, text=''):
        inverse,valid=self.state.transform.inverted()
        if not valid:return False
        # Keep a mark upright in the orientation where it was drawn; later
        # transforms move the mark and photograph together.
        mark=Mark(kind,tuple(points),color,width,text,inverse)
        return self.push(replace(self.state,marks=self.state.marks+(mark,)))

    def paint(self, painter, image, view=QTransform(), extra=None):
        painter.save();painter.setTransform(view)
        painter.setClipRect(QRectF(0,0,*self.state.size))
        painter.setTransform(self.state.transform*view)
        # Quarter turns and crops at native size must not resample pixels.
        resampling=image.size()!=QSize(*self.states[0].size) or abs(view.m11()-1.)>1e-8
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform,resampling)
        painter.drawImage(QRectF(0,0,*self.states[0].size),image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for mark in self.state.marks:
            painter.save();painter.setTransform(mark.transform*self.state.transform*view)
            paint_mark(painter,mark);painter.restore()
        painter.restore()
        if extra is not None:
            painter.save();painter.setTransform(view);painter.setClipRect(QRectF(0,0,*self.state.size))
            paint_mark(painter,extra);painter.restore()


@lru_cache(maxsize=128)
def text_outline(text,size):
    path=QPainterPath();path.addText(QPointF(),text_font(size),text)
    return path


def text_font(size):
    font=QFont('Microsoft JhengHei UI');font.setPixelSize(max(1,size))
    return font


@lru_cache(maxsize=64)
def text_document(text,size,width):
    document=QTextDocument();document.setDocumentMargin(0);document.setUseDesignMetrics(True)
    document.setDefaultFont(text_font(size))
    option=QTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    document.setDefaultTextOption(option);document.setPlainText(text);document.setTextWidth(width)
    return document


def paint_mark(painter, mark):
    points=[QPointF(*point) for point in mark.points]
    if not points:return
    pen=QPen(QColor(mark.color),mark.width,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush)
    if mark.kind=='text':
        painter.save();painter.translate(points[0])
        if len(points)>1:
            rect=QRectF(QPointF(),points[1]-points[0]).normalized()
            painter.setClipRect(rect,Qt.ClipOperation.IntersectClip)
            context=QAbstractTextDocumentLayout.PaintContext();context.clip=rect
            context.palette.setColor(QPalette.ColorRole.Text,QColor(mark.color))
            document=text_document(mark.text,max(10,round(mark.width)),round(rect.width(),2))
            document.documentLayout().draw(painter,context)
        else:painter.fillPath(text_outline(mark.text,max(10,round(mark.width))),QColor(mark.color))
        painter.restore()
    elif mark.kind=='rectangle' and len(points)>1:
        painter.drawRect(QRectF(points[0],points[-1]).normalized())
    else:
        path=QPainterPath(points[0])
        for point in points[1:]:path.lineTo(point)
        if len(points)==1:painter.drawPoint(points[0])
        else:painter.drawPath(path)
        if mark.kind=='arrow' and len(points)>1:
            end,start=points[-1],points[0];angle=math.atan2(end.y()-start.y(),end.x()-start.x())
            length=max(12,mark.width*4)
            for side in (-.5,.5):
                painter.drawLine(end,end-QPointF(math.cos(angle+side)*length,math.sin(angle+side)*length))


def save_copy(source, destination, edits, expected_stamp):
    """Atomic PNG copy with original generation metadata and no source writes."""
    source,destination=Path(source),Path(destination)
    if (source.resolve()==destination.resolve() or destination.exists() and os.path.samefile(source,destination)):
        raise ValueError('請使用新的檔名，以保留原圖。')
    if destination.suffix.lower()!='.png':raise ValueError('編輯副本請使用 PNG 格式。')
    if file_stamp(source)!=expected_stamp:raise ValueError('原圖已變更，請重新開啟後再編輯。')
    reader=QImageReader(str(source));reader.setAutoTransform(True);reader.setAllocationLimit(128)
    image=reader.read()
    if image.isNull():raise ValueError(reader.errorString() or '無法讀取原圖。')
    if (image.width(),image.height())!=edits.states[0].size:raise ValueError('原圖尺寸已變更，請重新開啟。')
    output=QImage(QSize(*edits.state.size),QImage.Format.Format_ARGB32_Premultiplied)
    if output.isNull():raise ValueError('圖片過大，無法建立編輯副本。')
    output.fill(Qt.GlobalColor.transparent);output.setColorSpace(image.colorSpace())
    painter=QPainter(output)
    try:edits.paint(painter,image)
    finally:painter.end()
    from .pnginfo import png_metadata
    metadata=png_metadata(source).get('raw',{})
    for key,value in metadata.items():output.setText(key,value if isinstance(value,str) else json.dumps(value,ensure_ascii=False))
    output.setText('pcs_image_edit','Cropped, transformed or annotated in PCS; generation metadata describes the source image.')
    if file_stamp(source)!=expected_stamp:raise ValueError('原圖在讀取期間變更，請重新開啟。')
    target=QSaveFile(str(destination));target.setDirectWriteFallback(False)
    if not target.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError(target.errorString())
    writer=QImageWriter(target,b'png')
    if not writer.write(output):
        target.cancelWriting();target.commit();raise OSError(writer.errorString())
    if not target.commit():raise OSError(target.errorString())
    edits.saved=edits.state
    return destination
