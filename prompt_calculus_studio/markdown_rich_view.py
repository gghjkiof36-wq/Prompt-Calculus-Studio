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
        if self.view.presentation_current(version):
            self.view.presented_version=version
            self.view.sync_title_alignment()
    @Slot(str,int)
    def finished(self,value,version):
        if self.view.presentation_current(version):
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
        self.read_request=None
        # Invalidate immediately, before the watcher's existing debounced refresh.
        self.relations.fileChanged.connect(self.dependency_changed)
        self.relations.directoryChanged.connect(self.dependency_changed)
        self.input_timer=QTimer(self);self.input_timer.setInterval(180)
        self.input_timer.timeout.connect(self.check_inputs)
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
        self.search_ticket=getattr(self,'search_ticket',0)+1
        previous=getattr(self,'pending_search',None)
        reset=bool(previous and (previous[4] or previous[:2]!=(query,case_sensitive)))
        self.pending_search=(query,case_sensitive,direction,self.search_ticket,reset)
        if not hasattr(self,'search_timer'):
            self.search_timer=QTimer(self);self.search_timer.setSingleShot(True)
            self.search_timer.timeout.connect(self.dispatch_search)
        if direction:
            # Navigation consumes the newest query immediately. Never combine
            # next/previous steps: their order matters at the first match.
            self.search_timer.stop();self.dispatch_search()
        else:
            # Opening find and entering/replacing its query can happen in one
            # GUI turn. Only the latest refresh needs to scan the rendered DOM.
            self.search_timer.start(0)

    def dispatch_search(self):
        pending=self.pending_search;self.pending_search=None
        if pending is None:return
        query,case_sensitive,direction,ticket,reset=pending
        if ticket!=self.search_ticket:return
        def finished(value):
            from shiboken6 import isValid
            if not isValid(self) or ticket!=self.search_ticket or not value:return
            result=json.loads(value);s=self.surface
            if s.mode!='read' or not s.find_bar.isVisible() or s.query.text()!=query:return
            s.match_count.setText(f"{result['active']}/{result['total']}" if query else '')
            s.find_previous.setEnabled(result['total']>0);s.find_forward.setEnabled(result['total']>0)
        if self.ready:
            args=json.dumps([query,case_sensitive,direction],ensure_ascii=False)
            # A -> B -> A in one GUI turn must reset A's former match index,
            # even when B's expensive scan was never dispatched.
            clear=reset and getattr(self,'search_identity',None)==(query,case_sensitive)
            script=('window.markSearch("",false);' if clear else '')
            self.search_identity=(query,case_sensitive)
            self.page().runJavaScript('(()=>{if(!window.markSearch)return null;'+script+'return window.markSearch(...'+args+');})()',finished)

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

    def open_inputs(self):
        workspace=self.surface.page.file_title.workspace
        result={}
        if workspace:
            for tab in workspace.all_pages():
                buffer=getattr(tab.widget,'buffer',None)
                if buffer is not None:result[buffer.path]=(buffer,buffer.snapshot())
        return result

    def attached(self):
        if not self.interactive:return True
        workspace=self.surface.page.file_title.workspace
        return workspace is None or any(tab.widget is self.surface.page for tab in workspace.all_pages())

    def inputs_current(self,request):
        current=self.open_inputs();captured=request['buffers']
        receipt=request.get('receipt')
        keys=receipt.read_paths if receipt is not None else set(current)|set(captured)
        for path in keys:
            old=captured.get(path);new=current.get(path)
            if old is None or new is None:
                if old is not new:return False
            elif old[0] is not new[0] or old[1] is not new[1]:return False
        return True

    def request_current(self,version,*,inputs=True,mode=True):
        from .markdown_relations import reading_root
        request=self.read_request
        return bool(request and not request['invalid'] and version==self.version==request['version']
            and request['buffer'] is self.surface.page.buffer
            and request['snapshot'] is self.surface.page.buffer.snapshot()
            and request['path']==self.surface.page.path and self.attached()
            and request['root']==reading_root(self.surface)
            and (not mode or not self.interactive or self.surface.mode=='read')
            and (not inputs or self.inputs_current(request)))

    def presentation_current(self,version):
        if version!=self.version:return False
        if self.read_request is None:return True
        return self.read_request['stage']=='published' and self.request_current(version,mode=False)

    def discard_request(self,*,retry=False):
        request=self.read_request
        if request is None:return
        request['invalid']=True
        request.pop('result',None)
        self.hydration=None
        if hasattr(self,'hydration_timer'):self.hydration_timer.stop()
        if hasattr(self,'projection_worker'):self.projection_worker.discard()
        self.input_timer.stop()
        if request['stage']!='published':
            self.gate.allowed.clear();self.image_sources.clear();self.render_state='stale'
        # Preserve the accepted hidden-view contract: invalidate its key, but
        # only the existing visible/read debounce may start a replacement.
        self.reader._render_key=None
        if retry and self.attached():self.relations.timer.start()

    def dependency_changed(self,*_):
        self.discard_request(retry=True)

    def check_inputs(self):
        request=self.read_request
        if request is None or request['invalid']:
            self.input_timer.stop();return
        if not self.request_current(request['version'],inputs='receipt' in request,
                mode=request['stage']!='published'):
            self.discard_request(retry=True)

    def start_projection(self,text,root):
        buffers=self.open_inputs()
        self.read_request=dict(version=self.version,buffer=self.surface.page.buffer,
            snapshot=self.snapshot,path=self.surface.page.path,buffers=buffers,
            root=root,stage='render',invalid=False)
        if not hasattr(self,'projection_worker'):
            from .markdown_read_projection import RichReadingProjection
            self.projection_worker=RichReadingProjection(self)
            self.projection_worker.ready.connect(self.projection_ready)
            self.projection_worker.verified.connect(self.projection_verified)
        self.input_timer.start()
        self.projection_worker.request(self.version,text,self.document_path or self.surface.page.path,
            root,self.surface.preferences.get('hard_breaks',False),
            {path:value[1].text for path,value in buffers.items()})

    def refresh(self,text,*,projection=None):
        if hasattr(self,'projection_worker'):self.projection_worker.discard()
        self.read_request=None;self.input_timer.stop();self.relations.timer.stop()
        self.hydration=None
        if hasattr(self,'hydration_timer'):self.hydration_timer.stop()
        self.version+=1;self.snapshot=self.surface.page.buffer.snapshot()
        self.gate.allowed.clear();self.image_sources.clear()
        from .markdown_relations import notes_for
        notes=notes_for(self.surface);root=notes.root
        self.render_state='loading'
        if projection is None and self.interactive and (
                len(text)>128_000 or self.surface.editor.lazy_presentation):
            self.start_projection(text,root);return
        from .markdown_read_receipt import EmbedHandoff,handoff_embed
        try:
            result=projection if projection is not None else render(text,self.document_path or self.surface.page.path,
                root,self.resource,self.surface.preferences.get('hard_breaks',False),notes,
                before_embed=handoff_embed if self.interactive else None)
        except EmbedHandoff:
            # The aborted prefix may have hydrated ordinary media. These are
            # request-local indexes; remote caches and PDF page choices survive.
            self.gate.allowed.clear();self.image_sources.clear()
            self.start_projection(text,root);return
        self.publish(result,root)

    @Slot(object,object,str)
    def projection_ready(self,version,result,error):
        if version!=self.version:return
        if not self.request_current(version,inputs=False):
            self.discard_request(retry=True);return
        if error:
            if not self.inputs_current(self.read_request):
                self.discard_request(retry=True);return
            self.discard_request()
            self.render_state='error';self.reader._render_key=None
            self.surface.report('內容預覽未完成；原文仍可編輯。');return
        if not result or len(result)!=3:return
        projection,marker,receipt=result
        request=self.read_request;request['receipt']=receipt
        request['buffers']={path:value for path,value in request['buffers'].items() if path in receipt.read_paths}
        if not self.request_current(version):
            self.discard_request(retry=True);return
        from .local_documents import paths
        try:
            paths.root_path(receipt.root)
            wanted=receipt.watch_paths()
        except (OSError,ValueError):
            self.discard_request();self.relations.timer.stop();self.render_state='error'
            self.surface.report('無法確認嵌入來源的根目錄；原文仍可編輯。');return
        self.relations.bind(wanted,receipt.root)
        expected={str(p) for p in wanted if p.exists()}|{str(p.parent) for p in wanted if p.parent.exists()}|{str(receipt.root)}
        if not expected.issubset(set(self.relations.files()+self.relations.directories())):
            self.discard_request();self.render_state='error'
            self.surface.report('無法監看嵌入來源；原文仍可編輯。');return
        request.update(stage='verify',result=(projection,marker))
        if not receipt.reads and not receipt.probes and receipt.names is None and not receipt.directories:
            self.projection_verified(version,True,'')
        else:self.projection_worker.verify(version,receipt)

    @Slot(object,object,str)
    def projection_verified(self,version,valid,error):
        if version!=self.version:return
        if not self.request_current(version):
            self.discard_request(retry=True);return
        if error:
            self.discard_request();self.render_state='error'
            self.surface.report('無法確認嵌入來源；原文仍可編輯。');return
        if not valid:
            self.discard_request(retry=True);return
        projection,marker=self.read_request.pop('result')
        self.read_request['stage']='hydrate'
        self.hydration=(version,projection,marker,[],0)
        if not hasattr(self,'hydration_timer'):
            self.hydration_timer=QTimer(self);self.hydration_timer.setSingleShot(True)
            self.hydration_timer.timeout.connect(self.hydrate)
        self.hydration_timer.start(0)

    def hydrate(self):
        if self.hydration is None:return
        version,projection,marker,resources,index=self.hydration
        if version!=self.version:return
        if not self.request_current(version):
            self.discard_request(retry=True);return
        deadline=time.perf_counter()+.006
        while index<len(projection.attachments) and time.perf_counter()<deadline:
            resources.append(self.resource(projection.attachments[index],index));index+=1
        if index<len(projection.attachments):
            self.hydration=(version,projection,marker,resources,index)
            self.hydration_timer.start(1);return
        projection.body=re.sub(r'<!--pcs-resource:'+marker+r':(\d+)-->',
            lambda match:resources[int(match.group(1))],projection.body)
        # No event-loop yield between this final identity check and publication.
        if not self.request_current(version):
            self.discard_request(retry=True);return
        self.publish(projection,self.read_request['root'])
        self.hydration=None

    def publish(self,projection,root):
        if self.read_request is not None and not self.request_current(self.version):
            self.discard_request(retry=True);return
        self.projection=projection
        if self.read_request is None:self.relations.bind(self.projection.dependencies,root)
        else:
            self.read_request['stage']='published'
            if not self.read_request['receipt'].read_paths:self.input_timer.stop()
        tokens=dict(visual_tokens(window_appearance(self)));preferences=self.surface.preferences
        tokens['base']=tokens.get('canvas_background',tokens['base']);tokens['border']=tokens.get('line','#444444')
        self.payload=dict(version=self.version,html=self.projection.body.replace('pcs-action:',f'pcs-action:{self.version}/'),math=self.projection.math,diagrams=self.projection.diagrams,
            palette=tokens,font=preferences['reading_family'],size=preferences['reading_size'],width=preferences['reading_width'],lineHeight=preferences['line_height'],light=QColor(tokens['base']).lightnessF()>.5)
        self.render_state='loading'
        if self.ready:self.page().runJavaScript('window.refreshDocument && window.refreshDocument()')

    def action(self,command):
        if not self.interactive:return
        if self.read_request is not None and not self.request_current(self.version):
            self.discard_request(retry=True);return
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
