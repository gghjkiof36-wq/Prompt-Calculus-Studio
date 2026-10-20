"""Offline rich projection with a closed resource boundary and no document scripts."""
import base64
import html
import json
import os
import mimetypes
import re
import time
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
from PySide6.QtCore import QObject,Slot,Signal,QUrl,QByteArray,QBuffer,QIODevice,QSize,Qt,QTimer
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
    @Slot(int)
    def presented(self,version):
        if version==self.view.version:
            self.view.presented_version=version
            self.view.sync_title_alignment()
    @Slot(str,int)
    def finished(self,value,version):
        if version==self.view.version:
            self.view.render_state=value;self.view.rendered.emit()
    @Slot(str)
    def activate(self,command):self.view.action(command)


class RichView(QWebEngineView):
    rendered=Signal()
    def __init__(self,reader,*,document_path=None,interactive=True):
        super().__init__(reader);self.reader=reader;self.surface=reader.surface
        self.document_path=document_path;self.interactive=interactive
        self.setAccessibleName('Markdown 內容閱讀區');self.setContextMenuPolicy(Qt.ContextMenuPolicy.NoContextMenu)
        self.payload={};self.projection=None;self.render_state='loading';self.pdf_pages={};self.ready=False;self.version=0;self.presented_version=None;self.snapshot=None;self.image_sources={}
        from .markdown_relations import RelationWatch
        self.relations=RelationWatch(reader)
        self.profile=QWebEngineProfile(QApplication.instance())
        self.profile.setHttpCacheType(QWebEngineProfile.HttpCacheType.MemoryHttpCache)
        self.profile.setPersistentCookiesPolicy(QWebEngineProfile.PersistentCookiesPolicy.NoPersistentCookies)
        self.gate=ResourceGate(self.profile);self.profile.setUrlRequestInterceptor(self.gate)
        self.profile.downloadRequested.connect(lambda request:request.cancel())
        page=PreviewPage(self.profile,self);self.setPage(page);page.action.connect(self.action)
        page.setBackgroundColor(QColor(visual_tokens(window_appearance(self))['base']))
        settings=page.settings()
        for key,value in [('JavascriptEnabled',True),('JavascriptCanOpenWindows',False),('JavascriptCanAccessClipboard',False),('LocalStorageEnabled',False),('LocalContentCanAccessRemoteUrls',False),('LocalContentCanAccessFileUrls',True),('PluginsEnabled',False),('PdfViewerEnabled',False),('PlaybackRequiresUserGesture',True),('WebGLEnabled',False)]:
            settings.setAttribute(getattr(QWebEngineSettings.WebAttribute,key),value)
        self.channel=QWebChannel(page);self.bridge=ContentBridge(self);self.channel.registerObject('content',self.bridge);page.setWebChannel(self.channel)
        page.selectionChanged.connect(self.surface.update_statistics)
        self.loadFinished.connect(self.loaded)
        self.alignment_timer=QTimer(self);self.alignment_timer.setSingleShot(True)
        self.alignment_timer.timeout.connect(self.sync_title_alignment)
        self.alignment_ticket=0
        self.rendered.connect(self.sync_title_alignment)
        self.rendered.connect(self.surface.search_changed)
        self.setUrl(QUrl.fromLocalFile(str(ASSETS/'index.html')))
        # Profiles live through their pages; release only after the page's QObject.
        page.destroyed.connect(self.profile.deleteLater)

    def loaded(self,ok):
        if ok:
            self.ready=True;self.page().runJavaScript('window.refreshDocument && window.refreshDocument()')
        elif not self.ready:self.surface.report('內容預覽未載入；原文仍可編輯。')

    def sync_title_alignment(self):
        if not self.interactive or not self.ready or self.surface.mode!='read' or not self.isVisible():return
        self.alignment_ticket+=1;ticket=self.alignment_ticket
        width=self.width();scale=self.zoomFactor()
        def apply(value):
            from shiboken6 import isValid
            if (not isValid(self) or not self.isVisible() or self.surface.mode!='read' or not value
                    or ticket!=self.alignment_ticket or width!=self.width() or scale!=self.zoomFactor()):return
            left,right,viewport=json.loads(value)
            # Chromium resizes asynchronously after the QWidget. A result can
            # belong to the old viewport even though this request is newest.
            if abs(viewport*scale-width)>2:
                self.alignment_timer.start(16);return
            self.content_margins=(round(left*scale),max(0,round(self.width()-right*scale)))
            self.surface.update_title_layout()
        self.page().runJavaScript('(()=>{const r=document.querySelector("main")?.getBoundingClientRect();return r?JSON.stringify([r.left,r.right,innerWidth]):null})()',apply)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'alignment_timer'):self.alignment_timer.start(0)

    def search(self, query, case_sensitive=False, direction=0):
        self.search_ticket=getattr(self,'search_ticket',0)+1;ticket=self.search_ticket
        def finished(value):
            from shiboken6 import isValid
            if not isValid(self) or ticket!=self.search_ticket or not value:return
            result=json.loads(value);s=self.surface
            if s.mode!='read' or not s.find_bar.isVisible() or s.query.text()!=query:return
            s.match_count.setText(f"{result['active']}/{result['total']}" if query else '')
            s.find_previous.setEnabled(result['total']>0);s.find_forward.setEnabled(result['total']>0)
        if self.ready:
            args=json.dumps([query,case_sensitive,direction],ensure_ascii=False)
            self.page().runJavaScript('window.markSearch && window.markSearch(...'+args+')',finished)

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
        suffix=local.suffix.lower();width=entry['width'];height=entry['height']
        dims=(f' width="{max(16,min(1600,width))}"' if width else '')+(f' height="{max(16,min(1200,height))}"' if height else '')
        if suffix in IMAGES:
            url=self.image_sources.get(local)
            if url is None:
                if not local.is_file():return failure('找不到檔案')
                image=QImageReader(str(local));size=image.size()
                if not image.canRead() or size.width()*size.height()>64_000_000:return failure('圖片無法顯示或尺寸過大')
                self.gate.allowed.add(local.resolve());url=QUrl.fromLocalFile(str(local)).toString()
                if len(self.image_sources)<2048:self.image_sources[local]=url
            return f'<img src="{html.escape(url,quote=True)}" alt="{html.escape(label,quote=True)}"{dims}>'
        if not local.is_file():return failure('找不到檔案')
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

    def refresh(self,text,*,projection=None):
        self.hydration=None
        if hasattr(self,'hydration_timer'):self.hydration_timer.stop()
        self.version+=1;self.snapshot=self.surface.page.buffer.snapshot()
        self.gate.allowed.clear();self.image_sources.clear();workspace=self.surface.page.file_title.workspace
        from .markdown_relations import notes_for
        notes=notes_for(self.surface);root=notes.root
        self.render_state='loading'
        if projection is None and self.interactive and len(text)>128_000:
            # Capture open-note edits on the GUI thread. The parser only receives
            # immutable text/path values, never a callback into a QWidget.
            overrides={}
            if workspace:
                for tab in workspace.all_pages():
                    buffer=getattr(tab.widget,'buffer',None)
                    if buffer is not None:overrides[buffer.path]=buffer.snapshot().text
            if not hasattr(self,'projection_worker'):
                from .markdown_read_projection import RichReadingProjection
                self.projection_worker=RichReadingProjection(self)
                self.projection_worker.ready.connect(self.projection_ready)
            self.projection_worker.request(self.version,text,self.document_path or self.surface.page.path,
                root,self.surface.preferences.get('hard_breaks',False),overrides)
            return
        self.publish(projection if projection is not None else render(text,self.document_path or self.surface.page.path,root,self.resource,self.surface.preferences.get('hard_breaks',False),notes),root)

    @Slot(object,object,str)
    def projection_ready(self,version,result,error):
        if version!=self.version or self.snapshot!=self.surface.page.buffer.snapshot():return
        if self.surface.mode!='read':
            self.reader._render_key=None;return
        if error:
            self.render_state='error';self.reader._render_key=None
            self.surface.report('內容預覽未完成；原文仍可編輯。');return
        projection,marker=result
        self.hydration=(version,projection,marker,[],0)
        if not hasattr(self,'hydration_timer'):
            self.hydration_timer=QTimer(self);self.hydration_timer.setSingleShot(True)
            self.hydration_timer.timeout.connect(self.hydrate)
        self.hydration_timer.start(0)

    def hydrate(self):
        if self.hydration is None:return
        version,projection,marker,resources,index=self.hydration
        if version!=self.version or self.snapshot!=self.surface.page.buffer.snapshot():return
        if self.surface.mode!='read':
            self.reader._render_key=None;return
        deadline=time.perf_counter()+.006
        while index<len(projection.attachments) and time.perf_counter()<deadline:
            resources.append(self.resource(projection.attachments[index],index));index+=1
        if index<len(projection.attachments):
            self.hydration=(version,projection,marker,resources,index)
            self.hydration_timer.start(1);return
        projection.body=re.sub(r'<!--pcs-resource:'+marker+r':(\d+)-->',
            lambda match:resources[int(match.group(1))],projection.body)
        from .markdown_relations import notes_for
        self.publish(projection,notes_for(self.surface).root)
        self.hydration=None

    def publish(self,projection,root):
        self.projection=projection
        self.relations.bind(self.projection.dependencies,root)
        tokens=dict(visual_tokens(window_appearance(self)));preferences=self.surface.preferences
        tokens['base']=tokens.get('canvas_background',tokens['base']);tokens['border']=tokens.get('line','#444444')
        self.payload=dict(version=self.version,html=self.projection.body.replace('pcs-action:',f'pcs-action:{self.version}/'),math=self.projection.math,diagrams=self.projection.diagrams,
            palette=tokens,font=preferences['reading_family'],size=preferences['reading_size'],width=preferences['reading_width'],lineHeight=preferences['line_height'],light=QColor(tokens['base']).lightnessF()>.5)
        self.render_state='loading'
        if self.ready:self.page().runJavaScript('window.refreshDocument && window.refreshDocument()')

    def action(self,command):
        if not self.interactive:return
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
            elif parts[0]=='link':
                from .markdown_relations import open_link
                entry=self.projection.links[int(parts[1])]
                open_link(self.surface,entry['target'],entry['path'],entry['wiki'])
            elif parts[0]=='source':
                from .markdown_relations import open_source,notes_for
                entry=self.projection.sources[int(parts[1])];path=Path(entry['path']);notes=notes_for(self.surface)
                text=notes.text(path);position=sum(len(line) for line in text.splitlines(keepends=True)[:entry['line']])
                open_source(self.surface,path,position,read=False)
            elif parts[0]=='notice':self.surface.report('/'.join(parts[1:]))
        except (IndexError,ValueError,OSError) as error:self.surface.report('操作未完成：'+str(error))

    def pause(self):self.page().runJavaScript("document.querySelectorAll('audio,video').forEach(element=>element.pause())")
