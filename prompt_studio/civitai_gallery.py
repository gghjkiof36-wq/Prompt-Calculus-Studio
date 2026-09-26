"""Image-first settings gallery. Image requests never block the file/search worker."""
import hashlib,os
from collections import OrderedDict
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtCore import Qt,QObject,QSize,QRectF,QBuffer,QByteArray,QIODevice,QUrl,QTimer,Signal,QEvent
from PySide6.QtGui import QPainter,QColor,QPainterPath,QImage,QImageReader,QPixmap,QFont
from PySide6.QtWidgets import QLabel,QListWidget,QStyledItemDelegate,QStyle,QSizePolicy
from PySide6.QtNetwork import QNetworkAccessManager,QNetworkRequest,QNetworkReply
from .civitai import PREVIEW_HOSTS

def read_image(data):
    buffer=QBuffer(); buffer.setData(QByteArray(data)); buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader=QImageReader(buffer); reader.setAutoTransform(True); reader.setAllocationLimit(64)
    size=reader.size()
    if size.isValid() and max(size.width(),size.height())>800:
        size.scale(800,800,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size)
    image=reader.read()
    if image.isNull():raise ValueError('圖片格式無法讀取，請重新整理。')
    return image

class ImageLoader(QObject):
    """Three cancellable asynchronous requests, an LRU disk cache, no bearer tokens."""
    idle=Signal()
    def __init__(self,page):
        super().__init__(page); self.page=page; self.manager=QNetworkAccessManager(self)
        self.pending=OrderedDict(); self.active={}; self.epoch=0
        self.directory=page.window.store.directory/'civitai/previews'; self.directory.mkdir(parents=True,exist_ok=True)
    def fetch(self,key,url,done,failed,priority=False):
        parts=urlsplit(url)
        if parts.scheme!='https' or parts.hostname not in PREVIEW_HOSTS:failed('圖片網址無效'); return
        if key in self.active or key in self.pending:return
        self.pending[key]=(url,done,failed)
        if priority:self.pending.move_to_end(key,last=False)
        self.drain()
    def cancel_except(self,keys):
        self.pending=OrderedDict((k,v) for k,v in self.pending.items() if k in keys)
        for key,entry in list(self.active.items()):
            if key not in keys:entry['cancelled']=True; entry['reply'].abort()
    def cancel(self):
        self.epoch+=1; self.pending.clear()
        for entry in list(self.active.values()):entry['cancelled']=True; entry['reply'].abort()
    def drain(self):
        if self.page.window.closing or not self.page.policy.refresh():self.cancel(); return
        while self.pending and len(self.active)<3:
            key,(url,done,failed)=self.pending.popitem(last=False)
            path=self.directory/(hashlib.sha256(url.encode()).hexdigest()+'.image')
            if path.is_file():
                try:image=read_image(path.read_bytes()); path.touch()
                except (OSError,ValueError):path.unlink(missing_ok=True)
                else:done(image); continue
            request=QNetworkRequest(QUrl(url)); request.setRawHeader(b'User-Agent',b'PromptStudio/0.81-Alpha')
            request.setTransferTimeout(12000)
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            reply=self.manager.get(request)
            entry=dict(reply=reply,data=bytearray(),cancelled=False,done=done,failed=failed,path=path)
            self.active[key]=entry
            def read(k=key,e=entry):
                e['data'].extend(bytes(e['reply'].readAll()))
                if len(e['data'])>24*1024*1024:e['error']='圖片超過預覽大小上限'; e['reply'].abort()
            reply.readyRead.connect(read)
            reply.finished.connect(lambda k=key,e=entry:self.finished(k,e))
    def finished(self,key,entry):
        self.active.pop(key,None); reply=entry['reply']
        try:
            if entry['cancelled'] or self.page.window.closing or not self.page.policy.refresh():return
            if reply.error()!=QNetworkReply.NetworkError.NoError:raise ValueError(entry.get('error') or '圖片載入失敗，請重新整理。')
            entry['data'].extend(bytes(reply.readAll())); image=read_image(entry['data'])
            path=entry['path']; pending=path.with_suffix('.qt-partial'); pending.write_bytes(entry['data']); os.replace(pending,path)
            self.trim(); entry['done'](image)
        except (OSError,ValueError) as exc:entry['failed'](str(exc))
        finally:
            reply.deleteLater()
            if not self.page.window.closing:QTimer.singleShot(0,self.drain)
    def trim(self):
        paths=sorted(self.directory.glob('*.image'),key=lambda p:p.stat().st_mtime,reverse=True); total=0
        for index,path in enumerate(paths):
            total+=path.stat().st_size
            if index>=100 or total>128*1024*1024:path.unlink(missing_ok=True)

class Preview(QLabel):
    def __init__(self):
        super().__init__(); self.image=QImage(); self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed); self.setMinimumWidth(0); self.setFixedHeight(140)
    def clear(self):
        self.image=QImage(); super().clear(); self.setFixedHeight(140); self.update()
    def set_image(self,image):
        self.image=image; self.setText(''); self.fit_height(); self.update()
    def fit_height(self):
        if not self.image.isNull():self.setFixedHeight(min(420,max(150,round(self.width()*self.image.height()/self.image.width()))))
    def resizeEvent(self,event):super().resizeEvent(event); self.fit_height()
    def paintEvent(self,event):
        if self.image.isNull():super().paintEvent(event); return
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        area=QRectF(self.contentsRect()); painter.fillRect(area,QColor('#171b21'))
        size=self.image.size().scaled(area.size().toSize(),Qt.AspectRatioMode.KeepAspectRatio)
        target=QRectF(0,0,size.width(),size.height()); target.moveCenter(area.center()); painter.drawImage(target,self.image)

class CardDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        rect=QRectF(option.rect).adjusted(5,5,-5,-5); path=QPainterPath(); path.addRoundedRect(rect,12,12)
        selected=bool(option.state & QStyle.StateFlag.State_Selected); hover=bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.fillPath(path,QColor('#2b3542' if selected else '#242a32' if hover else '#20252d'))
        painter.setClipPath(path); art=QRectF(rect.left(),rect.top(),rect.width(),rect.height()-92)
        data=index.data(Qt.ItemDataRole.UserRole) or {}; versions=data.get('modelVersions') or [{}]
        icon=index.data(Qt.ItemDataRole.DecorationRole)
        if icon and not icon.isNull():
            pixmap=icon.pixmap(450,450); size=pixmap.size().scaled(art.size().toSize(),Qt.AspectRatioMode.KeepAspectRatioByExpanding)
            target=QRectF(0,0,size.width(),size.height()); target.moveCenter(art.center()); painter.setClipRect(art); painter.drawPixmap(target,pixmap,QRectF(pixmap.rect())); painter.setClipping(False)
        else:
            painter.fillRect(art,QColor('#2a3039')); painter.setPen(QColor('#838e9e')); painter.drawText(art,Qt.AlignmentFlag.AlignCenter,'無預覽' if index.data(Qt.ItemDataRole.UserRole+1) or not versions[0].get('images') else '載入圖片…')
        font=QFont(option.font); font.setPixelSize(max(12,min(16,font.pixelSize() if font.pixelSize()>0 else 14))); painter.setFont(font)
        x=rect.left()+12; width=rect.width()-24; y=art.bottom()+10; metrics=painter.fontMetrics()
        painter.setPen(QColor('#f2f4f8')); painter.drawText(QRectF(x,y,width,24),Qt.AlignmentFlag.AlignLeft,metrics.elidedText(data.get('name','未命名'),Qt.TextElideMode.ElideRight,int(width)))
        font.setPixelSize(max(11,font.pixelSize()-1)); painter.setFont(font); metrics=painter.fontMetrics(); painter.setPen(QColor('#b2bdca'))
        rating=(data.get('stats') or {}).get('thumbsUpCount'); rating_text=f'♥ {rating:,}' if type(rating) is int else ''
        rating_width=metrics.horizontalAdvance(rating_text)+12 if rating_text else 0
        creator=(data.get('creator') or {}).get('username',''); painter.drawText(QRectF(x,y+25,width-rating_width,21),Qt.AlignmentFlag.AlignLeft,metrics.elidedText(creator,Qt.TextElideMode.ElideRight,int(width-rating_width)))
        painter.drawText(QRectF(x,y+25,width,21),Qt.AlignmentFlag.AlignRight,rating_text)
        meta=data.get('type','')+' · '+versions[0].get('baseModel','未提供'); painter.drawText(QRectF(x,y+48,width,22),Qt.AlignmentFlag.AlignLeft,metrics.elidedText(meta,Qt.TextElideMode.ElideRight,int(width)))
        if '已安裝此版本' in (index.data(Qt.ItemDataRole.DisplayRole) or ''):
            badge=QRectF(x,rect.top()+12,48,22); painter.fillRect(badge,QColor('#183f32')); painter.setPen(QColor('#beefd4')); painter.drawText(badge,Qt.AlignmentFlag.AlignCenter,'已安裝')
        painter.setClipping(False); painter.setPen(QColor('#a8c7ef' if selected else '#39414c')); painter.drawPath(path); painter.restore()
    def sizeHint(self,option,index):return self.parent().gridSize()

class Gallery(QListWidget):
    def __init__(self):
        super().__init__(); self.setViewMode(QListWidget.ViewMode.IconMode); self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setObjectName('CivitaiGallery'); self.setStyleSheet('QListWidget#CivitaiGallery::item {padding:0; margin:0; border:0;}')
        self.setMovement(QListWidget.Movement.Static); self.setSpacing(0); self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.setItemDelegate(CardDelegate(self)); self.setGridSize(QSize(220,350)); self.setIconSize(QSize(320,320))
        self.viewport().installEventFilter(self)
    def eventFilter(self,watched,event):
        if watched is self.viewport() and event.type()==QEvent.Type.Resize:QTimer.singleShot(0,self.reflow)
        return super().eventFilter(watched,event)
    def resizeEvent(self,event):
        super().resizeEvent(event); self.reflow()
    def reflow(self):
        width=max(160,self.viewport().width()-4); columns=max(1,width//218); cell=min(290,width//columns)
        size=QSize(cell,round(cell*1.15)+92)
        if size!=self.gridSize():self.setGridSize(size); self.doItemsLayout()
