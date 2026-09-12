"""Drag images from Explorer, a browser or the clipboard without a save dialog."""
import base64
import uuid
from html.parser import HTMLParser
from pathlib import Path
from PySide6.QtCore import Qt, Signal, QUrl, QByteArray, QEvent
from PySide6.QtGui import QImage, QPixmap, QImageReader
from PySide6.QtWidgets import QLabel
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply

MAX_IMAGE_BYTES=25*1024*1024


class ImageSource(HTMLParser):
    def __init__(self): super().__init__(); self.source=''
    def handle_starttag(self,tag,attrs):
        if tag=='img' and not self.source: self.source=dict(attrs).get('src','')


def dropped_source(mime):
    if mime.hasImage(): return 'image',mime.imageData()
    if mime.hasUrls():
        local=[u.toLocalFile() for u in mime.urls() if u.isLocalFile()]
        if local: return 'files',local
    if mime.hasHtml():
        parser=ImageSource(); parser.feed(mime.html())
        if parser.source and QUrl(parser.source).scheme() in ('https','http','data'):
            return 'url',parser.source
    if mime.hasUrls():
        for url in mime.urls():
            if url.scheme() in ('https','http','data'): return 'url',url.toString()
    return None,None


class ImageDropLabel(QLabel):
    pathReady=Signal(str)
    filesReady=Signal(list)
    failed=Signal(str)

    def __init__(self,store,keep_original=False):
        super().__init__('拖曳圖片到這裡，或選擇圖片')
        self.store=store; self.keep_original=keep_original; self.reply=None
        self.setAcceptDrops(True); self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setToolTip('可從瀏覽器或檔案總管拖入圖片；亦可按 Ctrl+V 貼上圖片。')
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)
        self.network=QNetworkAccessManager(self)

    def install_on(self,widget):
        widget.setAcceptDrops(True); widget.installEventFilter(self)
        if hasattr(widget,'viewport'):
            widget.viewport().setAcceptDrops(True); widget.viewport().installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.DragEnter,QEvent.Type.DragMove) and dropped_source(event.mimeData())[0]:
            event.acceptProposedAction(); return True
        if event.type()==QEvent.Type.Drop and dropped_source(event.mimeData())[0]:
            self.dropEvent(event); return True
        return super().eventFilter(watched,event)

    def dragEnterEvent(self,event):
        kind,_=dropped_source(event.mimeData())
        if kind: event.acceptProposedAction()

    def dropEvent(self,event):
        event.acceptProposedAction(); self.receive(event.mimeData())

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_V and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            from PySide6.QtWidgets import QApplication
            self.receive(QApplication.clipboard().mimeData()); event.accept(); return
        super().keyPressEvent(event)

    def receive(self,mime):
        try:
            kind,value=dropped_source(mime)
            if kind=='files':
                self.filesReady.emit(value); self.pathReady.emit(value[0]); return
            if kind=='image':
                image=value.toImage() if isinstance(value,QPixmap) else QImage(value)
                if image.isNull() or image.width()*image.height()>40_000_000: raise ValueError('圖片過大或無法讀取。')
                path=self.incoming('.png')
                if not image.save(str(path),'PNG'): raise ValueError('無法保存拖入的圖片。')
                self.deliver(path); return
            if kind=='url' and value.startswith('data:image/'):
                header,encoded=value.split(',',1)
                if ';base64' not in header or len(encoded)>MAX_IMAGE_BYTES*1.4: raise ValueError('不支援或過大的圖片內容。')
                self.save_bytes(base64.b64decode(encoded,validate=True)); return
            if kind=='url' and QUrl(value).scheme() in ('https','http'):
                if self.reply: self.failed.emit('上一張圖片仍在讀取中。'); return
                request=QNetworkRequest(QUrl(value)); request.setTransferTimeout(15000)
                request.setRawHeader(b'User-Agent',b'PromptStudio/0.6')
                self.buffer=bytearray(); self.reply=self.network.get(request)
                self.reply.readyRead.connect(self.read_data); self.reply.finished.connect(self.downloaded)
                return
            raise ValueError('請拖入圖片本身或圖片檔案；網頁連結不能當作圖片。')
        except Exception as exc: self.failed.emit(str(exc))

    def incoming(self,suffix):
        folder=self.store.directory/('originals' if self.keep_original else 'drop-cache')
        folder.mkdir(exist_ok=True)
        return folder/(uuid.uuid4().hex+suffix)

    def read_data(self):
        self.buffer.extend(bytes(self.reply.readAll()))
        if len(self.buffer)>MAX_IMAGE_BYTES: self.reply.abort()

    def downloaded(self):
        reply=self.reply
        self.read_data(); self.reply=None
        try:
            if len(self.buffer)>MAX_IMAGE_BYTES: raise ValueError('圖片超過 25 MB，已停止讀取。')
            if reply.error()!=QNetworkReply.NetworkError.NoError: raise ValueError('無法讀取圖片連結，請拖入圖片本身或本機檔案。')
            self.save_bytes(self.buffer)
        except Exception as exc: self.failed.emit(str(exc))
        finally: reply.deleteLater(); self.buffer=bytearray()

    def save_bytes(self,data):
        if len(data)>MAX_IMAGE_BYTES: raise ValueError('圖片超過 25 MB。')
        # Use the decoded format, never a remote filename or supplied path.
        from PySide6.QtCore import QBuffer,QIODevice
        buffer=QBuffer(); buffer.setData(QByteArray(data)); buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader=QImageReader(buffer); size=reader.size(); fmt=bytes(reader.format()).decode('ascii')
        if not reader.canRead() or size.width()*size.height()>40_000_000 or fmt not in ('png','jpeg','jpg','webp','bmp','gif'):
            raise ValueError('連結沒有回傳支援的圖片，或圖片尺寸過大。')
        if fmt=='gif':
            image=reader.read(); path=self.incoming('.png'); image.save(str(path),'PNG')
        else:
            path=self.incoming('.'+fmt); path.write_bytes(data)
        self.deliver(path)

    def deliver(self,path):
        try:
            self.filesReady.emit([str(path)]); self.pathReady.emit(str(path))
        finally:
            if not self.keep_original: path.unlink(missing_ok=True)
