"""Gallery-only image presentation; source images and icons stay unchanged."""
import json

from PySide6.QtCore import QEvent, QRectF, QSize, Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPainterPath, QPen, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QApplication, QFrame, QListWidget, QPlainTextEdit, QScrollBar, QSizePolicy, QSplitter, QSplitterHandle, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTabWidget, QToolTip

from .metadata_view import readable_metadata
from .image_drop import ImageDropLabel
from .theme import visual_tokens
from .widgets import ElidedLabel, StudioDialog, button, label, row


class MetadataReadingHighlighter(QSyntaxHighlighter):
    """Typography only: preserve the existing text and single-blank-line sections."""
    def __init__(self,document,tokens):
        super().__init__(document)
        self.heading=QTextCharFormat(); self.heading.setFontWeight(QFont.Weight.Bold)
        self.heading.setForeground(QColor(tokens['text']))
        self.secondary=QTextCharFormat(); self.secondary.setForeground(QColor(tokens['secondary']))

    def highlightBlock(self,text):
        title,separator,_=text.partition(' · 節點 ')
        if separator and title in ('載入模型','提示詞','採樣器','LoRA','生成設定'):
            self.setFormat(0,len(title),self.heading)
            self.setFormat(len(title),len(text)-len(title),self.secondary)
        elif text in ('圖片備註','手動附上的工作區資料','Prompt Calculus Studio 模組快照'):
            self.setFormat(0,len(text),self.heading)
        elif text=='（此為圖片的內嵌資料）':
            self.setFormat(0,len(text),self.secondary)
        else:
            colon=text.find('：')
            if 0<colon<25:self.setFormat(0,colon+1,self.secondary)


class ImageMetadataDialog(StudioDialog):
    """A copyable reading view of the selected image's existing saved data."""
    def __init__(self,parent,record):
        super().__init__(parent)
        self.setWindowTitle('圖片資料')
        window=parent.window
        self.resize(min(850,window.width()-40),min(650,window.height()-40))
        self.filename=ElidedLabel(record.get('name','')); self.filename.setObjectName('Subtle')
        self.filename.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.filename.setToolTip(record.get('name',''))
        if window.height()<=640:
            content=self.heading.parentWidget().layout(); content.setContentsMargins(18,14,18,14); content.setSpacing(10)
            content.itemAt(0).layout().insertWidget(1,self.filename,1)
            self.body.setSpacing(10)
        else:self.body.addWidget(self.filename)
        self.tabs=QTabWidget(); self.body.addWidget(self.tabs,1)
        summary=readable_metadata(record).rstrip()
        manual=record.get('manual')
        if manual is not None:
            names={'source':'來源','attached':'附加時間','workspace':'工作區','prompt':'提示詞','parameters':'參數','note':'說明'}
            lines=['','手動附上的工作區資料']
            if isinstance(manual,dict):
                for key,value in manual.items():
                    text=json.dumps(value,ensure_ascii=False,indent=2) if isinstance(value,(dict,list)) else str(value)
                    lines.append(names.get(key,key)+'：'+text)
            else:lines.append(json.dumps(manual,ensure_ascii=False,indent=2))
            summary+='\n'+'\n'.join(lines)
        self.summary=self.add_text('閱讀摘要',summary)
        self.summary_highlighter=MetadataReadingHighlighter(self.summary.document(),visual_tokens(window.state['settings']))
        self.raw=self.add_text('原始內嵌',json.dumps(record.get('metadata',{}).get('raw',{}),ensure_ascii=False,indent=2))
        self.raw.setStyleSheet('font-family:"Cascadia Mono","Consolas","Microsoft JhengHei UI";')
        self.copy_button=button('複製內容',self.copy_content,'Quiet')
        self.body.addLayout(row(self.copy_button,None,button('關閉',self.accept)))

    def add_text(self,title,text):
        editor=QPlainTextEdit(); editor.setReadOnly(True); editor.setPlainText(text)
        editor.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tabs.addTab(editor,title); return editor

    def copy_content(self):
        QApplication.clipboard().setText(self.tabs.currentWidget().toPlainText())


class GallerySplitterHandle(QSplitterHandle):
    def sizeHint(self):
        size=super().sizeHint(); splitter=self.splitter()
        index=splitter.indexOf(self)
        if index>=0 and splitter.widget(index).objectName()=='MediaContent':size.setWidth(0)
        return size


class GallerySplitter(QSplitter):
    def createHandle(self):
        return GallerySplitterHandle(self.orientation(),self)


class GalleryDetailHandle(QSplitterHandle):
    def sizeHint(self):
        size=super().sizeHint(); size.setWidth(32); return size

    def paintEvent(self,event):
        window=self.window(); colors=visual_tokens(window.state['settings'])
        color=QColor(colors['divider'])
        painter=QPainter(self);pen=QPen(color,1);pen.setCosmetic(True);painter.setPen(pen)
        painter.drawLine(self.width()//2,0,self.width()//2,self.height())


class GalleryDetailSplitter(QSplitter):
    def createHandle(self):return GalleryDetailHandle(self.orientation(),self)


class GalleryDetailCard(QFrame):
    resized=Signal()

    def resizeEvent(self,event):
        super().resizeEvent(event); self.resized.emit()

    def paintEvent(self,event):
        colors=visual_tokens(self.window().state['settings'])
        line=QColor(colors['line']); line.setAlpha(140)
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(line,1)); painter.setBrush(QColor(colors['surface']))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5,.5,-.5,-.5),16,16)


class GalleryPreview(ImageDropLabel):
    """Fit a cached preview to the card without rereading the original on resize."""
    def __init__(self,store):
        super().__init__(store,keep_original=True)
        self._source=None; self._fitting=False
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)

    def setPixmap(self,pixmap):
        self._source=pixmap
        if pixmap.isNull():super().setPixmap(pixmap)
        self.fit_preview()

    def fit_preview(self):
        if self._fitting:return
        self._fitting=True
        try:
            source=self._source
            maximum=max(180,min(320,round(self.window().height()*(.42 if self.property('horizontalCard') else .36))))
            if source is None or source.isNull():
                self.setFixedHeight(min(200,maximum))
                return
            natural=source.deviceIndependentSize(); width=max(1,self.contentsRect().width())
            height=min(maximum,max(120,round(width*natural.height()/natural.width())))
            self.setFixedHeight(height)
            ratio=source.devicePixelRatioF()
            fitted=source.scaled(round(width*ratio),round(height*ratio),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            super().setPixmap(fitted)
        finally:self._fitting=False

    def resizeEvent(self,event):
        super().resizeEvent(event); self.fit_preview()


class GalleryScrollBar(QScrollBar):
    def paintEvent(self,event):
        if self.maximum()>self.minimum():super().paintEvent(event)


class MediaGalleryView(QListWidget):
    zoomRequested=Signal(int)
    viewportResized=Signal()
    viewportAboutToResize=Signal()

    def __init__(self):
        super().__init__()
        self.setVerticalScrollBar(GalleryScrollBar(Qt.Orientation.Vertical,self))
        self._wheel_remainder=0
        self._reading_gutter=0
        self._gutter_timer=QTimer(self); self._gutter_timer.setSingleShot(True)
        self._gutter_timer.timeout.connect(self.apply_reading_gutter)
        self.verticalScrollBar().installEventFilter(self)

    def set_reading_gutter(self,gap):
        self._reading_gutter=gap
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn if gap else Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.apply_reading_gutter(); self._gutter_timer.start(0)

    def apply_reading_gutter(self):
        # Reserve a stable scrollbar lane so wrapping cannot toggle the scrollbar
        # and feed a different width back into the column count.
        right=self._reading_gutter
        if self.viewportMargins().right()!=right:self.setViewportMargins(0,0,right,0)

    def eventFilter(self,watched,event):
        if watched is self.verticalScrollBar() and event.type() in (QEvent.Type.Show,QEvent.Type.Hide):self._gutter_timer.start(0)
        return super().eventFilter(watched,event)

    def wheelEvent(self,event):
        if self.viewMode()==self.ViewMode.IconMode and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            self._wheel_remainder+=event.angleDelta().y()
            steps=int(self._wheel_remainder/120)
            if steps:
                self._wheel_remainder-=steps*120; self.zoomRequested.emit(steps)
            event.accept(); return
        super().wheelEvent(event)

    def resizeEvent(self,event):
        self.viewportAboutToResize.emit()
        super().resizeEvent(event)
        self._gutter_timer.start(0)
        self.viewportResized.emit()


class MediaGalleryDelegate(QStyledItemDelegate):
    def __init__(self,gallery):
        super().__init__(gallery.images)
        self.gallery=gallery

    def sizeHint(self,option,index):
        if self.gallery.view_mode=='images':
            size=self.gallery.images.gridSize()
            return size if size.isValid() else QSize(230,230)
        if self.gallery.list_columns>1:return self.gallery.images.gridSize()
        return QSize(200,max(38,option.fontMetrics.lineSpacing()+18))

    def helpEvent(self,event,view,option,index):
        record=index.data(Qt.ItemDataRole.UserRole) or {}
        if self.gallery.record and record.get('id')==self.gallery.record['id']:
            QToolTip.showText(event.globalPos(),'目前檢視：'+record.get('name',''),view)
            return True
        return super().helpEvent(event,view,option,index)

    def paint(self,painter,option,index):
        option=QStyleOptionViewItem(option); self.initStyleOption(option,index)
        tokens=visual_tokens(self.gallery.window.state['settings'])
        grid=self.gallery.view_mode=='images'
        record=index.data(Qt.ItemDataRole.UserRole) or {}
        viewing=bool(self.gallery.record and record.get('id')==self.gallery.record['id'])
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        focused=bool(option.state & QStyle.StateFlag.State_HasFocus)
        hovered=bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect=QRectF(option.rect).adjusted(1,1,-1,-1)
        columns=getattr(self.gallery,'list_columns',1)
        if not grid and columns>1:
            column=index.row()%columns
            rect.adjust(10 if column else 0,0,-10 if column<columns-1 else 0,0)
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.rect)
        if selected or hovered or viewing:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(tokens['selected' if viewing else 'hover']))
            painter.drawRoundedRect(rect,12,12)
        if selected or focused or viewing:
            painter.setBrush(Qt.BrushStyle.NoBrush); painter.setPen(QPen(QColor(tokens['accent' if viewing else 'control']),1.5 if viewing else 1))
            painter.drawRoundedRect(rect.adjusted(.5,.5,-.5,-.5),12,12)
        elif not grid:
            divider=QColor(tokens['divider'])
            pen=QPen(divider,1);pen.setCosmetic(True);painter.setPen(pen)
            painter.drawLine(rect.left()+16,rect.bottom(),rect.right()-12,rect.bottom())
        line=option.fontMetrics.lineSpacing()
        painter.setFont(option.font)
        if grid:
            icon_box=rect.adjusted(9,9,-9,-9)
            image_rect=icon_box
            image=option.icon.pixmap(self.gallery.images.iconSize(),painter.device().devicePixelRatioF(),QIcon.Mode.Normal,QIcon.State.Off)
            if not image.isNull():
                natural=image.deviceIndependentSize()
                scale=min(icon_box.width()/natural.width(),icon_box.height()/natural.height())
                image_rect=QRectF(0,0,natural.width()*scale,natural.height()*scale); image_rect.moveCenter(icon_box.center())
                clip=QPainterPath(); clip.addRoundedRect(image_rect,min(12,image_rect.width()/2),min(12,image_rect.height()/2))
                painter.save(); painter.setClipPath(clip,Qt.ClipOperation.IntersectClip)
                painter.drawPixmap(image_rect,image,QRectF(image.rect())); painter.restore()
            if viewing:
                badge_width=option.fontMetrics.horizontalAdvance('檢視中')+12; badge_height=line+4
                painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(tokens['accent']))
                if image_rect.width()>=badge_width+12 and image_rect.height()>=badge_height+12:
                    badge=QRectF(image_rect.right()-badge_width-5,image_rect.top()+5,badge_width,badge_height)
                    painter.drawRoundedRect(badge,6,6); painter.setPen(QColor(tokens['on_accent']))
                    painter.drawText(badge,Qt.AlignmentFlag.AlignCenter,'檢視中')
                else:
                    painter.drawEllipse(QRectF(icon_box.right()-14,icon_box.top()+4,10,10))
            painter.restore(); return
        text_left=rect.left()+16
        text_top=rect.top()+(rect.height()-line)/2
        width=max(0,int(rect.right()-text_left-8))
        if viewing and not grid and width>option.fontMetrics.horizontalAdvance('檢視中')+48:
            marker_width=option.fontMetrics.horizontalAdvance('檢視中')
            painter.setPen(QColor(tokens['accent']))
            painter.drawText(QRectF(rect.right()-marker_width-12,text_top,marker_width,line),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,'檢視中')
            width=max(0,width-marker_width-16)
        title=option.text.split('\n',1)[0]
        title=option.fontMetrics.elidedText(title,Qt.TextElideMode.ElideRight,width)
        painter.setPen(QColor(tokens['text']))
        painter.drawText(QRectF(text_left,text_top,width,line),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,title)
        painter.restore()
