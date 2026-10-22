"""Explicit link actions and live source dependencies for Markdown views."""
from pathlib import Path
from urllib.parse import urlsplit
from PySide6.QtCore import QFileSystemWatcher,QTimer,QUrl,Qt
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QDialog, QVBoxLayout, QLineEdit, QFormLayout, QDialogButtonBox, QListWidget, QCompleter, QLabel
from .widgets import ComboBox as QComboBox
from .markdown_links import Notes,links,region
from .markdown_source import SourceEdit,python_position
from .widgets import StudioDialog,style_completion


def reading_root(surface):
    """Logical scope shared by rendering, request identity and view caching."""
    workspace=surface.page.file_title.workspace;path=surface.page.path
    root=workspace.locations.root_for(path) if workspace else path.parent
    return root or path.parent


def notes_for(surface):
    workspace=surface.page.file_title.workspace
    def read(target):
        tab=workspace.tab_for_path(target) if workspace else None
        return tab.widget.buffer.snapshot().text if tab and hasattr(tab.widget,'buffer') else Notes.disk(target)
    return Notes(reading_root(surface),read)


def link_at(surface,position=None):
    snapshot=surface.page.buffer.snapshot()
    if position is None:position=python_position(snapshot.text,surface.editor.textCursor().position())
    return next((entry for entry in links(snapshot.text) if entry.start<=position<=entry.end),None)


def open_document(surface,path):
    """Follow a note link without duplicating an already open document view."""
    workspace=surface.page.file_title.workspace
    if workspace is None:return
    path=workspace.locations.file(path)
    tab=workspace.tab_for_path(path)
    if tab is not None:
        if not tab.retained:workspace.retain(tab)
        workspace.focus_tab(tab)
        return tab
    return workspace.open_path(path,retained=True,new_tab=True)


def open_source(surface,path,position=0,read=None):
    workspace=surface.page.file_title.workspace
    if workspace is None:return
    existing=workspace.tab_for_path(path)
    tab=open_document(surface,path)
    if tab is None:return
    target=getattr(tab.widget,'md_surface',None)
    if target is None:return
    if existing is None or read is not None:
        target.set_mode('read' if (surface.mode=='read' if read is None else read) else 'write')
    target.navigate_when_ready(position)
    return tab


def open_link(surface,target,source=None,wiki=False):
    try:
        address=urlsplit(target)
        if address.scheme in ('http','https','mailto'):
            QDesktopServices.openUrl(QUrl(target));return
        if address.scheme or address.netloc:raise ValueError('這個連結格式不能開啟。')
        notes=notes_for(surface);source=Path(source or surface.page.path)
        candidates,anchor=notes.resolve(target,source,wiki)
        if not candidates:raise ValueError('找不到連結目標：'+target)
        def choose(path):
            if path.suffix.lower()!='.md':
                from .workspace_locations import KINDS
                if path.suffix.lower() in KINDS:return open_document(surface,path)
                # Never launch arbitrary executables/scripts from note links.
                if path.suffix.lower() not in ('.pdf','.png','.jpg','.jpeg','.gif','.webp','.bmp','.wav','.mp3','.ogg','.flac','.m4a','.mp4','.webm','.ogv'):
                    raise ValueError('此檔案類型不能從筆記直接開啟。')
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)));return
            position=region(notes.text(path),anchor)[0] if anchor else 0
            return open_source(surface,path,position)
        if len(candidates)==1:return choose(candidates[0])
        dialog=StudioDialog(surface);dialog.setWindowModality(Qt.WindowModality.NonModal)
        dialog.setWindowTitle('選擇連結目標');dialog.resize(520,360)
        layout=dialog.body;layout.addWidget(QLabel('有同名筆記，請選擇位置。'))
        items=QListWidget();layout.addWidget(items)
        for path in candidates:items.addItem(path.relative_to(notes.root).as_posix())
        def picked(item):
            try:choose(candidates[items.row(item)]);dialog.accept()
            except (ValueError,OSError) as error:surface.report(str(error))
        items.itemActivated.connect(picked);items.itemDoubleClicked.connect(picked)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose);dialog.show();surface.link_picker=dialog
    except (ValueError,OSError) as error:surface.report(str(error))


class LinkDialog(StudioDialog):
    def __init__(self,surface,embed=False):
        super().__init__(surface);self.surface=surface;self.snapshot,start,end=surface.selection()
        current=link_at(surface)
        self.start,self.end=(current.start,current.end) if current else (start,end)
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle('編輯連結' if current else '插入連結');self.resize(560,310)
        layout=self.body;form=QFormLayout();form.setVerticalSpacing(12);layout.addLayout(form)
        self.target=QLineEdit(current.target if current else '');self.target.setAccessibleName('連結目標')
        self.target.setPlaceholderText('筆記、筆記#標題、筆記#^區塊 或 https://…')
        self.label=QLineEdit(current.label if current else self.snapshot.text[start:end]);self.label.setAccessibleName('連結顯示文字')
        self.kind=QComboBox();self.kind.addItem('筆記連結','wiki');self.kind.addItem('Markdown 連結','markdown');self.kind.addItem('嵌入筆記／區塊','embed')
        self.kind.setCurrentIndex(2 if embed or current and current.embed else 0 if not current or current.wiki else 1)
        form.addRow('目標',self.target);form.addRow('顯示文字',self.label);form.addRow('形式',self.kind)
        self.error=QLabel();self.error.setWordWrap(True);layout.addWidget(self.error)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('套用')
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('Primary')
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.accepted.connect(self.submit);buttons.rejected.connect(self.reject);layout.addWidget(buttons)
        try:
            notes=notes_for(surface);completion=QCompleter([p.relative_to(notes.root).with_suffix('').as_posix() for p in notes.files()],self)
            completion.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive);completion.setFilterMode(Qt.MatchFlag.MatchContains);self.target.setCompleter(completion)
            style_completion(completion,self.target)
        except (ValueError,OSError):pass
        self.target.setFocus()
    def submit(self):
        target=self.target.text().strip();label=self.label.text();kind=self.kind.currentData()
        try:
            if not target or any(c in target for c in '\n\r<>"'):raise ValueError('請填入有效的連結目標。')
            if kind in ('wiki','embed'):
                if any(c in target+label for c in '[]|'):raise ValueError('筆記連結中不能包含中括號或直線。')
                value=('!' if kind=='embed' else '')+'[['+target+('|'+label if label else '')+']]'
            else:
                from urllib.parse import quote
                target=quote(target,safe=':/?#&=%+@._~-')
                value='['+(label or self.target.text()).replace('\\','\\\\').replace(']','\\]')+']('+target+')'
            self.surface.page.buffer.apply(self.snapshot,[SourceEdit(self.start,self.end,value)])
            self.surface.refresh();self.accept()
        except (ValueError,OSError) as error:self.error.setText(str(error))


class RelationWatch(QFileSystemWatcher):
    def __init__(self,reader):
        super().__init__(reader);self.reader=reader
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.setInterval(180);self.timer.timeout.connect(self.refresh)
        self.fileChanged.connect(lambda _:self.timer.start());self.directoryChanged.connect(lambda _:self.timer.start())
    def bind(self,dependencies,root):
        # Watch just embedded sources and directories; content updates don't
        # rescan or rewrite every note in the workspace.
        wanted={str(p) for p in dependencies if p.exists()}|{str(p.parent) for p in dependencies if p.parent.exists()}|{str(root)}
        current=set(self.files()+self.directories())
        if current-wanted:self.removePaths(list(current-wanted))
        if wanted-current:self.addPaths(list(wanted-current))
    def refresh(self):
        invalidate=getattr(self.reader,'_invalidate_cache',None)
        if callable(invalidate):invalidate()
        if self.reader.isVisible() and self.reader.surface.mode=='read':self.reader.refresh()
