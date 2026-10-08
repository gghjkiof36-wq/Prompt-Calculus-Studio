"""Local reading rhythm for the three management information panels."""
from math import ceil
from PySide6.QtCore import Qt,QEvent,QObject,QPointF,QSize
from PySide6.QtGui import QPainter,QTextLayout,QTextOption,QTextCursor,QTextBlockFormat
from PySide6.QtWidgets import QLabel,QFrame,QVBoxLayout,QSizePolicy,QCheckBox,QStyleOptionButton,QStyle,QTextEdit
from .theme import font_pixels,visual_tokens


PREFERRED_CONTENT_WIDTH = 920


def text_layout(text,font,width,spacing=1.6):
    layout=QTextLayout(text.replace('\n','\u2028'),font)
    option=QTextOption();option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
    layout.setTextOption(option);layout.beginLayout();height=0
    while True:
        line=layout.createLine()
        if not line.isValid():break
        line.setLineWidth(max(1,width))
        advance=line.height()*spacing
        line.setPosition(QPointF(0,height+(advance-line.height())/2));height+=advance
    layout.endLayout()
    return layout,ceil(height)


class ReadingText(QLabel):
    """Plain text keeps natural, font-measured line height and wraps long IDs."""
    def __init__(self,window,text='',level='body',subtle=False):
        super().__init__(text);self.host=window;self.level=level
        if subtle:self.setObjectName('Subtle')
        self.setTextFormat(Qt.TextFormat.PlainText);self.setWordWrap(True)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.setMinimumWidth(0);self.sync_font()

    def sync_font(self):
        settings=dict(self.host.state['settings']);settings.update(getattr(self.host,'appearance_preview',{}))
        points=settings['ui_size']+{'page':5,'panel':3,'group':1,'body':0}[self.level]
        style=f'font-size:{font_pixels(points)}px;font-weight:{600 if self.level!="body" else 400};'
        if self.styleSheet()!=style:self.setStyleSheet(style)

    def changeEvent(self,event):
        super().changeEvent(event)
        if hasattr(self,'host') and event.type() in (QEvent.Type.StyleChange,QEvent.Type.FontChange):
            self.sync_font();self.updateGeometry();self.update()

    def setText(self,text):
        super().setText(text);self.setAccessibleName(text);self.updateGeometry();self.update()

    def minimumSizeHint(self):return QSize(0,ceil(self.fontMetrics().height()*1.6))
    def sizeHint(self):
        width=min(600,max((self.fontMetrics().horizontalAdvance(line) for line in self.text().split('\n')),default=0))
        return QSize(width,self.heightForWidth(width))
    def heightForWidth(self,width):
        margins=self.contentsMargins()
        return text_layout(self.text(),self.font(),width-margins.left()-margins.right())[1]+margins.top()+margins.bottom()
    def paintEvent(self,event):
        painter=QPainter(self);painter.setPen(self.palette().color(self.foregroundRole()))
        painter.setClipRect(self.contentsRect())
        text_layout(self.text(),self.font(),self.contentsRect().width())[0].draw(painter,QPointF(self.contentsRect().topLeft()))


class ReadingPanel(QFrame):
    def __init__(self,window):
        super().__init__();self.host=window;self.setObjectName('SettingsGroup')
        self.body=QVBoxLayout(self);self.body.setContentsMargins(24,24,24,24);self.body.setSpacing(0)
        self.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        self.setMinimumWidth(0)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        margin=12 if self.host.height()<=480 else 16 if self.width()<640 else 24
        if self.body.contentsMargins().left()!=margin:self.body.setContentsMargins(margin,margin,margin,margin)


class ReadingPath(QTextEdit):
    """Read-only paths remain selectable/copyable while growing with wrapping."""
    def __init__(self,window):
        super().__init__();self.host=window;self.setReadOnly(True);self.setAcceptRichText(False)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        policy=self.sizePolicy();policy.setHeightForWidth(True);self.setSizePolicy(policy)
        self.document().setDocumentMargin(0);self.setMinimumWidth(0);self.sync_surface()
    def sync_surface(self):
        settings=dict(self.host.state['settings']);settings.update(getattr(self.host,'appearance_preview',{}))
        colors=visual_tokens(settings)
        style=f'QTextEdit {{background:{colors["field"]};border:1px solid {colors["line"]};border-radius:8px;padding:8px;}}'
        if self.styleSheet()!=style:self.setStyleSheet(style)
    def text(self):return self.toPlainText()
    def setText(self,text):
        if self.toPlainText()==text:return
        self.setPlainText(text)
        cursor=self.textCursor();cursor.select(QTextCursor.SelectionType.Document)
        block=QTextBlockFormat();block.setLineHeight(160,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
        cursor.mergeBlockFormat(block);cursor.clearSelection();cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.setTextCursor(cursor);self.update_height()
    def heightForWidth(self,width):return max(36,text_layout(self.toPlainText() or self.placeholderText(),self.font(),width-20)[1]+20)
    def minimumSizeHint(self):return QSize(0,max(36,self.fontMetrics().height()+16))
    def sizeHint(self):return QSize(400,self.heightForWidth(400))
    def update_height(self):
        self.setMinimumHeight(self.heightForWidth(self.width()));self.updateGeometry()
    def resizeEvent(self,event):
        super().resizeEvent(event);self.update_height()
    def changeEvent(self,event):
        super().changeEvent(event)
        if hasattr(self,'host') and event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):
            self.sync_surface();self.update_height()


class ControlHeight(QObject):
    def __init__(self,control):
        super().__init__(control);self.control=control;control.installEventFilter(self);self.update()
    def update(self):
        self.control.setMinimumHeight(max(36,self.control.fontMetrics().height()+16))
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.Polish):self.update()
        return False


def readable_control(control):
    ControlHeight(control)
    return control


class PageHeadingRole(QObject):
    def __init__(self,heading,window,current_view):
        super().__init__(heading);self.heading=heading;self.host=window;self.current_view=current_view
        heading.installEventFilter(self)
    def refresh(self):
        settings=dict(self.host.state['settings']);settings.update(getattr(self.host,'appearance_preview',{}))
        style=f'font-size:{font_pixels(settings["ui_size"]+5)}px;font-weight:600;' if self.current_view() in ('extension','automatic','tools') else ''
        if self.heading.styleSheet()!=style:self.heading.setStyleSheet(style)
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.StyleChange,QEvent.Type.FontChange):self.refresh()
        return False


class ReadingCheckBox(QCheckBox):
    """Keep the native check state/callback with a wrapping, readable caption."""
    def __init__(self,text):
        super().__init__(text);self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        policy=self.sizePolicy();policy.setHeightForWidth(True);self.setSizePolicy(policy)
        readable_control(self)
    def text_offset(self):
        return self.style().pixelMetric(QStyle.PixelMetric.PM_IndicatorWidth,None,self)+8
    def minimumSizeHint(self):return QSize(0,max(36,self.fontMetrics().height()+16))
    def sizeHint(self):
        width=self.fontMetrics().horizontalAdvance(self.text())+self.text_offset()
        return QSize(width,self.heightForWidth(width))
    def heightForWidth(self,width):
        return max(36,text_layout(self.text(),self.font(),width-self.text_offset())[1]+8)
    def hitButton(self,pos):return self.rect().contains(pos)
    def paintEvent(self,event):
        painter=QPainter(self);option=QStyleOptionButton();self.initStyleOption(option);option.text=''
        self.style().drawControl(QStyle.ControlElement.CE_CheckBox,option,painter,self)
        layout,height=text_layout(self.text(),self.font(),self.width()-self.text_offset())
        painter.setPen(self.palette().color(self.foregroundRole()))
        layout.draw(painter,QPointF(self.text_offset(),max(0,(self.height()-height)/2)))
