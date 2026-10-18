"""An isolated rich projection exports only fully rendered, local content."""
from pathlib import Path
import json
from shiboken6 import isValid
from PySide6.QtCore import QObject,QTimer,QSaveFile,QIODevice,QMarginsF,Signal,Qt
from PySide6.QtGui import QPageLayout,QPageSize
from PySide6.QtWidgets import QFileDialog,QWidget,QVBoxLayout


class PdfExport(QObject):
    finished=Signal(bool,str)
    def __init__(self,surface,path):
        super().__init__(surface);self.surface=surface;self.path=Path(path);self.complete=False;self.printing=False
        if self.path.suffix.lower()!='.pdf':raise ValueError('請使用 .pdf 副檔名。')
        if self.path==surface.page.path:raise ValueError('不能覆寫 Markdown 原稿。')
        self.before=self.path.read_bytes() if self.path.exists() else None
        from .markdown_rich_view import RichView
        # WebEngine needs a laid-out viewport to finish downloading math fonts.
        # This offscreen host renders without replacing the editor or flashing.
        self.host=QWidget();self.host.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.host.resize(900,1000);layout=QVBoxLayout(self.host);layout.setContentsMargins(0,0,0,0)
        self.view=RichView(surface.reader);layout.addWidget(self.view);self.host.show()
        self.destroyed.connect(self.host.deleteLater)
        self.view.rendered.connect(self.ready)
        self.timeout=QTimer(self);self.timeout.setSingleShot(True);self.timeout.timeout.connect(lambda:self.finish(False,'PDF 匯出逾時。'))
        self.timeout.start(45000)
        self.view.refresh(surface.page.buffer.snapshot().text)
        self.view.payload['palette'].update(base='#ffffff',text='#202124',secondary='#555555',surface='#f4f4f4',border='#d0d0d0',accent='#554827',info='#315c8b')
        self.view.payload['light']=True
    def ready(self):
        if self.complete or self.printing or self.view.render_state!='ready':return
        self.view.page().setVisible(True)
        self.printing=True
        title=json.dumps(self.surface.page.path.stem)
        script='''(async()=>{
          await document.fonts.ready;
          await Promise.all([...document.images].map(img=>img.complete?Promise.resolve():new Promise(resolve=>{img.onload=resolve;img.onerror=resolve;})));
          const failures=[...document.querySelectorAll('.missing,.render-error')].map(e=>e.textContent);
          document.querySelectorAll('img').forEach(img=>{if(!img.complete||!img.naturalWidth)failures.push(img.alt||'圖片');});
          if(failures.length)return JSON.stringify({error:failures.slice(0,8).join('、')});
          document.querySelectorAll('details').forEach(el=>el.open=true);
          document.documentElement.classList.add('pdf-export');
          let title=document.createElement('h1');title.className='pdf-title';title.textContent=TITLE;
          document.querySelector('main').prepend(title);
          return JSON.stringify({ready:true});
        })()'''.replace('TITLE',title)
        self.view.page().runJavaScript('window.pcsPdfState=null;'+script+'.then(value=>window.pcsPdfState=value).catch(error=>window.pcsPdfState=JSON.stringify({error:String(error)}))')
        self.poll=QTimer(self);self.poll.setInterval(80);self.poll.timeout.connect(self.check);self.poll.start()
    def check(self):
        if not self.complete:self.view.page().runJavaScript('window.pcsPdfState',self.checked)
    def checked(self,result):
        if not isValid(self) or self.complete or not result:return
        self.poll.stop();value=json.loads(result)
        if value.get('error'):self.finish(False,'PDF 未匯出：'+value['error']);return
        layout=QPageLayout(QPageSize(QPageSize.PageSizeId.A4),QPageLayout.Orientation.Portrait,QMarginsF(16,16,16,16),QPageLayout.Unit.Millimeter)
        self.view.page().printToPdf(self.write,layout)
    def write(self,data):
        if not isValid(self) or self.complete:return
        try:
            raw=bytes(data)
            if not raw.startswith(b'%PDF-') or len(raw)<500:raise ValueError('PDF 產生失敗。')
            current=self.path.read_bytes() if self.path.exists() else None
            if current!=self.before:raise ValueError('目的檔案已變更，請重新匯出。')
            output=QSaveFile(str(self.path));output.setDirectWriteFallback(False)
            if not output.open(QIODevice.OpenModeFlag.WriteOnly):raise OSError(output.errorString())
            if output.write(raw)!=len(raw):output.cancelWriting();raise OSError(output.errorString())
            if (self.path.read_bytes() if self.path.exists() else None)!=self.before:
                output.cancelWriting();raise ValueError('目的檔案已變更，請重新匯出。')
            if not output.commit():raise OSError(output.errorString())
            self.finish(True,str(self.path))
        except (OSError,ValueError) as error:self.finish(False,str(error))
    def finish(self,ok,message):
        if self.complete:return
        self.complete=True;self.timeout.stop()
        if hasattr(self,'poll'):self.poll.stop()
        self.finished.emit(ok,message);self.host.close();self.host.deleteLater()


def export_pdf(surface):
    if getattr(surface,'pdf_export',None) and not surface.pdf_export.complete:return
    path,_=QFileDialog.getSaveFileName(surface,'匯出 PDF',str(surface.page.path.with_suffix('.pdf')),'PDF (*.pdf)')
    if not path:return
    surface.pdf_export=PdfExport(surface,path)
    surface.pdf_export.finished.connect(lambda ok,message:surface.report('已匯出 PDF：'+message if ok else message))
