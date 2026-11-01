"""A bounded, screen-sized local image view shared by recent and Canvas images."""
from pathlib import Path
from PySide6.QtCore import Qt,QSize,QEvent
from PySide6.QtGui import QImageReader,QPixmap,QShortcut,QKeySequence
from PySide6.QtWidgets import QLabel,QSizePolicy
from .widgets import StudioDialog,button,row,label
from .ui_icons import icon


def viewer_size(available):
    return QSize(max(1,min(available.width()-40,round(available.width()*.90))),
                 max(1,min(available.height()-40,round(available.height()*.90))))


class ImageViewerDialog(StudioDialog):
    def __init__(self,parent,path,name='',*,records=None,selected_id=None,on_select=None):
        super().__init__(parent)
        self.records=[dict(id=r['id'],path=r['path'],name=r.get('name','')) for r in (records or [])]
        self.index=next((i for i,r in enumerate(self.records) if r['id']==selected_id),0)
        self.on_select=on_select
        self.setWindowTitle(name or Path(path).name)
        self.heading.setToolTip(self.windowTitle());self.heading.setMinimumWidth(0)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        available=parent.screen().availableGeometry().size() if parent else self.screen().availableGeometry().size()
        self.resize(viewer_size(available))
        self.display=QLabel();self.display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.display.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Ignored);self.display.setMinimumSize(1,1)
        self.display.setAccessibleName('完整圖片預覽')
        self.display.installEventFilter(self)
        self.body.addWidget(self.display,1)
        self.previous=button('',lambda:self.step(-1),'IconButton')
        self.next=button('',lambda:self.step(1),'IconButton');self.position=label('','Subtle')
        for control,title,glyph in ((self.previous,'上一張','chevron-left'),(self.next,'下一張','chevron-right')):
            control.setIcon(icon(glyph));control.setProperty('iconName',glyph)
            control.setAccessibleName(title);control.setToolTip(title);control.setFixedSize(36,36)
            control.setVisible(bool(self.records))
        self.close_button=button('關閉',self.reject)
        self.body.addLayout(row(self.previous,self.position,self.next,None,self.close_button))
        self.shortcuts=[]
        for key,delta in ((Qt.Key.Key_Left,-1),(Qt.Key.Key_Right,1)):
            shortcut=QShortcut(QKeySequence(key),self)
            shortcut.activated.connect(lambda d=delta:self.step(d));self.shortcuts.append(shortcut)
        self.available=available
        self.load_image(path,name,strict=not self.records)
        self.update_navigation()

    def load_image(self,path,name='',*,strict=False):
        self.setWindowTitle(name or Path(path).name);self.heading.setToolTip(self.windowTitle())
        reader=QImageReader(str(path));reader.setAutoTransform(True);reader.setAllocationLimit(128)
        size=reader.size()
        self.picture=QPixmap()
        if not size.isValid():
            if strict:raise ValueError('原圖已移動、暫存已清除或圖片無法讀取。')
            self.display.setText('圖片已移動或無法讀取，可切換其他圖片。');return
        # Decode once at the useful screen resolution; resizing reuses pixels.
        limit=self.available*self.devicePixelRatioF();size.scale(limit,Qt.AspectRatioMode.KeepAspectRatio)
        reader.setScaledSize(size);self.picture=QPixmap.fromImage(reader.read())
        if self.picture.isNull():
            if strict:raise ValueError('圖片無法讀取。')
            self.display.setText('圖片無法讀取，可切換其他圖片。');return
        self.fit_image()

    def update_navigation(self):
        self.previous.setEnabled(self.index>0)
        self.next.setEnabled(self.index<len(self.records)-1)
        self.position.setText(f'{self.index+1} / {len(self.records)}' if self.records else '')

    def step(self,delta):
        target=self.index+delta
        if not 0<=target<len(self.records):return
        self.index=target;record=self.records[target]
        self.load_image(record['path'],record['name']);self.update_navigation()
        if self.on_select:self.on_select(record['id'])

    def fit_image(self):
        if hasattr(self,'picture') and not self.picture.isNull():
            self.display.setPixmap(self.picture.scaled(self.display.size(),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))

    def showEvent(self,event):
        super().showEvent(event);self.layout().activate();self.fit_image()

    def resizeEvent(self,event):
        super().resizeEvent(event);self.fit_image()

    def eventFilter(self,watched,event):
        if watched is self.display and event.type()==QEvent.Type.Resize:self.fit_image()
        return super().eventFilter(watched,event)


def show_image(parent,path,name='',**options):
    try:dialog=ImageViewerDialog(parent,path,name,**options)
    except ValueError as exc:
        parent.notice(str(exc));return
    dialog.exec()
