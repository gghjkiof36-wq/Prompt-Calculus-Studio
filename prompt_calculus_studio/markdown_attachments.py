"""Explicit attachment insertion. Prepared files are immutable and survive Undo."""
import base64
import hashlib
import os
import uuid
from pathlib import Path
from urllib.parse import quote,urlsplit
from PySide6.QtCore import QBuffer,QByteArray,QIODevice,QUrl,QSaveFile,QTimer
from PySide6.QtGui import QImage,QImageReader
from PySide6.QtWidgets import QFileDialog
from .local_documents import paths
from .local_documents.directory_pin import pinned_target
from .markdown_source import SourceEdit

SUPPORTED={'.png','.jpg','.jpeg','.gif','.webp','.bmp','.wav','.mp3','.ogg','.flac','.m4a','.mp4','.webm','.ogv','.pdf'}


def root_for(surface):
    workspace=surface.page.file_title.workspace
    return Path(workspace.root) if workspace else surface.page.path.parent


def prepare(surface,source=None,image=None):
    root=Path(root_for(surface)).absolute();paths.root_path(root)
    if image is not None:
        if image.isNull():raise ValueError('剪貼簿沒有可用的圖片。')
        buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer,'PNG'):raise ValueError('圖片準備失敗。')
        raw=bytes(buffer.data());suffix='.png'
    else:
        original=Path(source).absolute();suffix=original.suffix.lower()
        if suffix not in SUPPORTED:raise ValueError('這個格式尚不支援插入。')
        if any(part.casefold()=='.obsidian' for part in original.parts):raise ValueError('不讀取 .obsidian 設定目錄。')
        for component in (original,*original.parents):
            if component.exists() and paths.is_link(component):raise ValueError('附件不能經過連結或重解析點。')
        if original.resolve().is_relative_to(root):
            # Existing attachments retain the user's folder arrangement.
            checked=paths.checked(root,original.relative_to(root).as_posix())
            if not checked.is_file():raise ValueError('找不到附件。')
            return checked
        raw,_=paths.read_bytes(original,256*1024*1024)
    with pinned_target(root,'assets/'+hashlib.sha256(raw).hexdigest()+suffix,create_parents=True) as target:
        if target.exists():
            if paths.read_bytes(target,256*1024*1024)[0]!=raw:raise ValueError('同名附件內容不同，沒有覆蓋。')
        else:
            # Exclusive creation prevents replacing a resource shared by another note.
            temporary=target.parent/('.md-prepare-'+uuid.uuid4().hex+'.tmp')
            try:
                with temporary.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                try:os.link(temporary,target)
                except FileExistsError:
                    if paths.read_bytes(target,256*1024*1024)[0]!=raw:raise ValueError('附件目的地已改變，請重試。')
            finally:
                if temporary.exists():temporary.unlink()
        return target


def insert(surface,files=None,image=None):
    if surface.mode=='read' or surface.page.buffer.composing:return False
    snapshot,start,end=surface.selection()
    try:
        targets=[prepare(surface,image=image)] if image is not None else [prepare(surface,source=path) for path in files or []]
        if not targets:return False
        references=[]
        for path in targets:
            relative=os.path.relpath(path,surface.page.path.parent).replace('\\','/')
            references.append('![]('+quote(relative,safe='/.-_')+')')
        text='\n\n'.join(references)
        before=snapshot.text[:start];after=snapshot.text[end:]
        # Insert attachments as separate paragraphs, even at a line boundary
        # or inside prose. Count Shift+Enter separators as existing line breaks.
        leading=len(before)-len(before.rstrip('\n\u2028'))
        trailing=len(after)-len(after.lstrip('\n\u2028'))
        if before:text='\n'*max(0,2-leading)+text
        text+='\n'*max(0,2-trailing)
        surface.page.buffer.apply(snapshot,[SourceEdit(start,end,text)])
        surface.navigate(start+len(text));surface.refresh();return True
    except (OSError,ValueError) as error:surface.report(str(error));return False


def choose(surface):
    if surface.mode=='read' or surface.page.buffer.composing:return
    files,_=QFileDialog.getOpenFileNames(surface,'插入圖片、影音或 PDF',str(surface.page.path.parent),
        '支援的附件 (*.png *.jpg *.jpeg *.gif *.webp *.bmp *.wav *.mp3 *.ogg *.flac *.m4a *.mp4 *.webm *.ogv *.pdf)')
    if files:insert(surface,files=files)


def load_remote(surface,entry,view):
    """Only a user click reaches the network. Cache image bytes for this view only."""
    from PySide6.QtNetwork import QNetworkAccessManager,QNetworkRequest,QNetworkReply
    target=entry['target'];url=QUrl(target)
    ticket=view.version;snapshot=surface.page.buffer.snapshot()
    if url.scheme() not in ('http','https'):return
    if not hasattr(view,'remote_images'):view.remote_images={};view.pending_images={}
    if target in view.pending_images:return
    if not hasattr(view,'network'):view.network=QNetworkAccessManager(view)
    request=QNetworkRequest(url)
    request.setTransferTimeout(20000)
    request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
    reply=view.network.get(request);view.pending_images[target]=reply
    QTimer.singleShot(20000,reply,reply.abort)
    chunks=bytearray()
    def consume():
        chunks.extend(bytes(reply.readAll()))
        if len(chunks)>32*1024*1024:reply.abort()
    reply.readyRead.connect(consume)
    def finish():
        from shiboken6 import isValid
        if not isValid(view):return
        view.pending_images.pop(target,None)
        if ticket!=view.version or snapshot!=surface.page.buffer.snapshot():reply.deleteLater();return
        if reply.error()!=QNetworkReply.NetworkError.NoError:surface.report('圖片未載入：'+reply.errorString())
        else:
            encoded=QBuffer();encoded.setData(QByteArray(bytes(chunks)));encoded.open(QIODevice.OpenModeFlag.ReadOnly)
            decoder=QImageReader(encoded);size=decoder.size()
            image=decoder.read() if decoder.canRead() and 0<size.width()*size.height()<=64_000_000 else QImage()
            if image.isNull():surface.report('遠端內容不是可顯示的圖片。')
            else:
                buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG')
                view.remote_images[target]='data:image/png;base64,'+base64.b64encode(bytes(buffer.data())).decode('ascii')
                view.reader.refresh()
        reply.deleteLater()
    reply.finished.connect(finish)
