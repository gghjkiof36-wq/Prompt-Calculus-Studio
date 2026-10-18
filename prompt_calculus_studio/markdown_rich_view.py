"""Offline rich projection with a closed resource boundary and no document scripts."""
import base64
import html
import json
import os
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from PySide6.QtCore import QObject,Slot,Signal,QUrl,QByteArray,QBuffer,QIODevice,QSize,Qt
from PySide6.QtGui import QImageReader,QColor
from PySide6.QtWidgets import QApplication
if os.environ.get('QT_QPA_PLATFORM')=='offscreen':
    os.environ.setdefault('QTWEBENGINE_CHROMIUM_FLAGS','--disable-gpu')
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEnginePage,QWebEngineProfile,QWebEngineSettings,QWebEngineUrlRequestInterceptor
from PySide6.QtWebChannel import QWebChannel
from PySide6.QtPdf import QPdfDocument
from .markdown_render import render,local_target
from .theme import visual_tokens,window_appearance

ASSETS=Path(__file__).parent/'assets/markdown'
IMAGES={'.png','.jpg','.jpeg','.gif','.webp','.bmp'}
AUDIO={'.wav','.mp3','.ogg','.flac','.m4a'}
VIDEO={'.mp4','.webm','.ogv'}


class ResourceGate(QWebEngineUrlRequestInterceptor):
    def __init__(self,parent):super().__init__(parent);self.allowed=set();self.blocked=[]
    def interceptRequest(self,info):
        url=info.requestUrl();scheme=url.scheme();allowed=False
        if scheme in ('qrc','data','about'):allowed=True
        elif scheme=='file':
            path=Path(url.toLocalFile()).resolve()
            allowed=path.is_relative_to(ASSETS.resolve()) or path in self.allowed
        if not allowed:
            self.blocked.append(url.toString());self.blocked=self.blocked[-50:];info.block(True)


class PreviewPage(QWebEnginePage):
    action=Signal(str)
    def acceptNavigationRequest(self,url,kind,main):
        if url.scheme()=='pcs-action':self.action.emit(url.path());return False
        if kind==QWebEnginePage.NavigationType.NavigationTypeLinkClicked:
            if url.fragment() and url.adjusted(QUrl.UrlFormattingOption.RemoveFragment)==self.url().adjusted(QUrl.UrlFormattingOption.RemoveFragment):return True
            self.action.emit('notice/連結操作在後續文件關係功能提供。');return False
        return url.scheme() in ('file','qrc','about','data')
    def createWindow(self,kind):return None
    def javaScriptConsoleMessage(self,level,message,line,source):
        self.last_console=(str(level),message,line)


class ContentBridge(QObject):
    def __init__(self,view):super().__init__(view);self.view=view
    @Slot(result=str)
    def document(self):return json.dumps(self.view.payload,ensure_ascii=False)
    @Slot(str)
    def finished(self,value):self.view.render_state=value;self.view.rendered.emit()


class RichView(QWebEngineView):
    rendered=Signal()
    def __init__(self,reader):
        super().__init__(reader);self.reader=reader;self.surface=reader.surface
        self.setAccessibleName('Markdown 內容閱讀區');self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.payload={};self.projection=None;self.render_state='loading';self.pdf_pages={};self.ready=False;self.version=0;self.snapshot=None
        self.profile=QWebEngineProfile(QApplication.instance())
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.gate=ResourceGate(self.profile);self.profile.setUrlRequestInterceptor(self.gate)
        self.profile.downloadRequested.connect(lambda request:request.cancel())
        page=PreviewPage(self.profile,self);self.setPage(page);page.action.connect(self.action)
        settings=page.settings()
        for key,value in [('JavascriptEnabled',True),('JavascriptCanOpenWindows',False),('JavascriptCanAccessClipboard',False),('LocalStorageEnabled',False),('LocalContentCanAccessRemoteUrls',False),('LocalContentCanAccessFileUrls',True),('PluginsEnabled',False),('PdfViewerEnabled',False),('PlaybackRequiresUserGesture',True),('WebGLEnabled',False)]:
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute,key),value)
        self.channel=QWebChannel(page);self.bridge=ContentBridge(self);self.channel.registerObject('content',self.bridge);page.setWebChannel(self.channel)
        page.selectionChanged.connect(self.surface.update_statistics)
        self.loadFinished.connect(self.loaded)
        self.setUrl(QUrl.fromLocalFile(str(ASSETS/'index.html')))
        # Profiles live through their pages; release only after the page's QObject.
        page.destroyed.connect(self.profile.deleteLater)

    def loaded(self,ok):
        if ok:
            self.ready=True;self.page().runJavaScript('window.refreshDocument && window.refreshDocument()')
        elif not self.ready:self.surface.report('內容預覽未載入；原文仍可編輯。')

    def resource(self,entry,index):
        target=entry['target'];local=Path(entry['path']) if entry['path'] else None
        label=entry['alt'] or (local.name if local else target)
        def failure(reason):return '<span class="resource missing">'+html.escape(label)+' · '+html.escape(reason)+'</span>'
        if local is None:
            if urlsplit(target).scheme in ('http','https'):
                cached=getattr(self,'remote_images',{}).get(target)
                if cached:return f'<img src="{cached}" alt="{html.escape(label,quote=True)}">'
                return failure('遠端資源尚未載入')+f' <a class="source" href="pcs-action:remote/{index}">載入這個資源</a>'
            return failure('資源位置不在目前工作區')
        if not local.is_file():return failure('找不到檔案')
        suffix=local.suffix.lower();width=entry['width'];height=entry['height']
        dims=(f' width="{max(16,min(1600,width))}"' if width else '')+(f' height="{max(16,min(1200,height))}"' if height else '')
        if suffix in IMAGES:
            image=QImageReader(str(local));size=image.size()
            if not image.canRead() or size.width()*size.height()>64_000_000:return failure('圖片無法顯示或尺寸過大')
            self.gate.allowed.add(local.resolve())
            return f'<img src="{html.escape(QUrl.fromLocalFile(str(local)).toString(),quote=True)}" alt="{html.escape(label,quote=True)}"{dims}>'
        if suffix in AUDIO|VIDEO:
            self.gate.allowed.add(local.resolve());tag='audio' if suffix in AUDIO else 'video'
            return f'<span class="media"><{tag} controls preload="none"{dims} src="{html.escape(QUrl.fromLocalFile(str(local)).toString(),quote=True)}"></{tag}><span class="media-error" hidden>無法播放這個檔案或編碼。</span></span>'
        if suffix=='.pdf':
            if local.stat().st_size>64*1024*1024:return failure('PDF 超過 64 MiB，請開啟原檔閱讀')
            pdf=QPdfDocument(self);error=pdf.load(str(local))
            if error!=QPdfDocument.Error.None_ or not pdf.pageCount():pdf.deleteLater();return failure('PDF 無法讀取或需要密碼')
            params=parse_qs(urlsplit(target).fragment);requested=params.get('page',['1'])[0]
            number=self.pdf_pages.get(target,int(requested) if requested.isdigit() else 1);number=max(1,min(pdf.pageCount(),number))
            original=pdf.pagePointSize(number-1);w=max(100,min(1200,width or 580));h=int(w*original.height()/max(1,original.width()))
            image=pdf.render(number-1,QSize(w,min(1800,h)));buffer=QBuffer();buffer.open(QIODevice.OpenModeFlag.WriteOnly);image.save(buffer,'PNG')
            encoded=bytes(buffer.data()).decode('latin1');encoded=base64.b64encode(encoded.encode('latin1')).decode('ascii')
            total=pdf.pageCount();pdf.deleteLater()
            return (f'<span class="pdf"><img src="data:image/png;base64,{encoded}" alt="{html.escape(label)} 第 {number} 頁"{dims}>'
                f'<span class="pdf-controls"><a href="pcs-action:pdf/{index}/{max(1,number-1)}">上一頁</a>　{number} / {total}　<a href="pcs-action:pdf/{index}/{min(total,number+1)}">下一頁</a></span></span>')
        return failure('這個格式尚不支援內嵌顯示')

    def refresh(self,text):
        self.version+=1;self.snapshot=self.surface.page.buffer.snapshot()
        self.gate.allowed.clear();workspace=self.surface.page.file_title.workspace
        root=Path(workspace.root) if workspace else self.surface.page.path.parent
        self.projection=render(text,self.surface.page.path,root,self.resource,self.surface.preferences.get('hard_breaks',False))
        tokens=dict(visual_tokens(window_appearance(self)));preferences=self.surface.preferences
        tokens['base']=tokens.get('canvas_background',tokens['base']);tokens['border']=tokens.get('line','#444444')
        self.payload=dict(html=self.projection.body.replace('pcs-action:',f'pcs-action:{self.version}/'),math=self.projection.math,diagrams=self.projection.diagrams,
            palette=tokens,font=preferences['reading_family'],size=preferences['reading_size'],width=preferences['reading_width'],lineHeight=preferences['line_height'],light=QColor(tokens['base']).lightnessF()>.5)
        self.render_state='loading'
        if self.ready:self.page().runJavaScript('window.refreshDocument && window.refreshDocument()')

    def action(self,command):
        parts=command.split('/')
        try:
            if int(parts.pop(0))!=self.version or self.snapshot!=self.surface.page.buffer.snapshot():return
            if parts[0] in ('line','table'):
                number=int(parts[1]);text=self.surface.page.buffer.snapshot().text
                position=sum(len(line) for line in text.splitlines(keepends=True)[:number])
                self.surface.set_mode('write');self.surface.navigate(position)
                if parts[0]=='table':self.surface.edit_table()
            elif parts[0]=='copy':
                QApplication.clipboard().setText(self.projection.codes[int(parts[1])]);self.surface.report('已複製程式碼原文。')
            elif parts[0]=='pdf':
                self.pdf_pages[self.projection.attachments[int(parts[1])]['target']]=int(parts[2]);self.reader.refresh()
            elif parts[0]=='remote':
                from .markdown_attachments import load_remote
                load_remote(self.surface,self.projection.attachments[int(parts[1])],self)
            elif parts[0]=='notice':self.surface.report('/'.join(parts[1:]))
        except (IndexError,ValueError,OSError) as error:self.surface.report('操作未完成：'+str(error))

    def pause(self):self.page().runJavaScript("document.querySelectorAll('audio,video').forEach(element=>element.pause())")
