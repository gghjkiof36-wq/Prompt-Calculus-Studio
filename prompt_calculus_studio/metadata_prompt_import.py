"""Read positive PNG prompts into an existing material draft, never the source."""
import io
from pathlib import Path
from PySide6.QtCore import QObject, QEvent, Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QFileDialog, QPlainTextEdit, QDialog
from .pnginfo import png_metadata_stream
from .image_source import metadata_sources
from .widgets import StudioDialog, ComboBox, button, label, row

MAX_BYTES=25*1024*1024


def positive_prompts(path):
    with Path(path).open('rb') as stream:raw=stream.read(MAX_BYTES+1)
    if len(raw)>MAX_BYTES:raise ValueError('圖片超過 25 MiB，請選擇較小的原始 PNG 圖片。')
    if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
        raise ValueError('目前支援 PNG 內嵌的 ComfyUI、Stable Diffusion 與 PCS 提示詞；請選擇原始 PNG 圖片。')
    metadata=png_metadata_stream(io.BytesIO(raw))
    rows=[];seen=set()
    for entry in metadata_sources(metadata):
        text=entry['text']
        if entry['role']!='positive' or not text.strip() or text in seen:continue
        seen.add(text)
        source='parameters' if entry['node']=='__parameters__' else '#'+entry['node']+' / '+entry['field']
        rows.append((source,text))
    if not rows:
        warning=' '.join(metadata.get('warnings',[]))
        raise ValueError(warning or '圖片中沒有可辨識的正面 Prompt。請使用保留生成參數的原始 PNG；截圖或重新儲存的圖片可能沒有 metadata。')
    return rows


class ImportPromptDialog(StudioDialog):
    def __init__(self,parent,choices,existing):
        super().__init__(parent);self.setWindowTitle('匯入圖片正面 Prompt');self.resize(640,450)
        self.sources=ComboBox();self.sources.setAccessibleName('正面 Prompt 來源')
        for title,text in choices:self.sources.addItem(title,text)
        if len(choices)>1:
            self.body.addWidget(label('圖片包含多個正面 Prompt，請選擇要匯入的來源。','Subtle',True))
            self.body.addWidget(self.sources)
            self.sources.setCurrentIndex(-1)
        else:self.sources.hide()
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True)
        self.preview.setAccessibleName('圖片正面 Prompt 預覽');self.body.addWidget(self.preview,1)
        self.mode=ComboBox();self.mode.addItem('附加到現有內容','append');self.mode.addItem('取代現有內容','replace')
        self.mode.setAccessibleName('匯入方式')
        if existing:self.body.addWidget(self.mode)
        else:self.mode.setCurrentIndex(1);self.mode.hide()
        self.apply=button('匯入',self.accept,'Primary')
        self.body.addLayout(row(None,button('取消',self.reject,'Quiet'),self.apply))
        self.sources.currentIndexChanged.connect(self.refresh);self.refresh()

    def refresh(self,*_):
        text=self.sources.currentData()
        self.preview.setPlainText(text or '');self.apply.setEnabled(text is not None)


class MetadataPromptImport(QObject):
    def __init__(self,editor):
        super().__init__(editor);self.editor=editor;self.active_dialog=None

    def install_on(self,widget):
        widget.setAcceptDrops(True);widget.installEventFilter(self)
        if hasattr(widget,'viewport'):
            widget.viewport().setAcceptDrops(True);widget.viewport().installEventFilter(self)

    def eventFilter(self,watched,event):
        if event.type() not in (QEvent.Type.DragEnter,QEvent.Type.DragMove,QEvent.Type.Drop):return False
        mime=event.mimeData()
        if not (mime.hasUrls() or mime.hasImage()):return False
        if event.type()!=QEvent.Type.Drop:event.acceptProposedAction();return True
        urls=mime.urls()
        if len(urls)==1 and urls[0].isLocalFile():self.load(urls[0].toLocalFile())
        else:self.notice('請一次拖入一張本機原始 PNG 圖片；網頁連結或剪貼簿影像可能沒有 metadata。')
        event.acceptProposedAction();return True

    def notice(self,text):
        self.editor.image_import_status.setText(text);self.editor.image_import_status.show()
        self.editor.fit_content()
        self.editor.layoutChanged.emit()

    def choose(self):
        path,_=QFileDialog.getOpenFileName(self.editor,'讀取圖片正面 Prompt','','PNG 圖片 (*.png)')
        if path:self.load(path)

    def load(self,path):
        editor=self.editor;target=editor.current_group
        if target is None:return False
        before=editor.direct_text.toPlainText()
        try:choices=positive_prompts(path)
        except (OSError,ValueError) as error:self.notice(str(error));return False
        text=choices[0][1];mode='replace'
        if len(choices)>1 or before:
            dialog=ImportPromptDialog(editor,choices,bool(before));self.active_dialog=dialog
            try:
                if dialog.exec()!=QDialog.DialogCode.Accepted:return False
                text=dialog.sources.currentData();mode=dialog.mode.currentData()
            finally:self.active_dialog=None;dialog.deleteLater()
        if editor.current_group is not target or editor.direct_text.toPlainText()!=before:
            self.notice('目前組合內容已變更，請重新匯入。');return False
        if text is None:return False
        if mode=='append':text=before+('' if before.endswith('\n') else '\n')+text
        cursor=editor.direct_text.textCursor();cursor.beginEditBlock()
        cursor.select(QTextCursor.SelectionType.Document);cursor.insertText(text);cursor.endEditBlock()
        editor.direct_text.setTextCursor(cursor);editor.direct_text.setFocus()
        self.notice('已讀取正面 Prompt。')
        return True
