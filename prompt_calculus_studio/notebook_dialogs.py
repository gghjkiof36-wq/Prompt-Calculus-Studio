"""Compact notebook sheets using the application's existing dialog finish."""
from datetime import datetime
from pathlib import Path
import difflib,copy
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor, QColor, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import (QLineEdit,QListWidget,QListWidgetItem,QPlainTextEdit,QFormLayout,
    QComboBox,QSplitter,QWidget,QVBoxLayout,QCheckBox,QTextBrowser)
from .widgets import StudioDialog,label,button,row
from .markdown_links import Notes
from .markdown_source import SourceEdit,SourceLedger
from .local_documents import paths

def decoded(raw):
    ledger=SourceLedger(raw); return ledger.text_of(ledger.pieces)


class Sheet(StudioDialog):
    def __init__(self,parent,title,width=560,height=380):
        super().__init__(parent); self.setWindowTitle(title); self.resize(width,height)
        self.body.setSpacing(12); self.error=label('','Subtle',True); self.error.hide()
        self.setStyleSheet(self.styleSheet()+'''QLineEdit,QComboBox { min-height:28px; padding:2px 8px; }
            QListWidget { background:transparent; border:0; outline:0; }
            QListWidget::item { padding:8px; border-radius:4px; }
            QPlainTextEdit { padding:10px; }''')
    def footer(self,callback,title='套用'):
        self.body.addWidget(self.error); self.apply_button=button(title,lambda:self.run(callback),'Primary')
        self.body.addLayout(row(None,self.apply_button,button('取消',self.reject)))
    def run(self,callback):
        try:
            result=callback()
            if result is not False:self.accept()
        except (OSError,ValueError,UnicodeError) as error:self.error.setText(str(error)); self.error.show()


class NameDialog(Sheet):
    def __init__(self,parent,title,field,callback):
        super().__init__(parent,title,440,190); form=QFormLayout(); self.name=QLineEdit()
        self.name.setAccessibleName(field); form.addRow(field,self.name); self.body.addLayout(form)
        self.footer(lambda:callback(self.name.text().strip()),'建立'); self.name.returnPressed.connect(self.apply_button.click)


class ChoiceDialog(Sheet):
    def __init__(self,parent,title,choices,callback):
        super().__init__(parent,title,500,420); self.choices=choices; self.query=QLineEdit()
        self.query.setAccessibleName('篩選'); self.query.setPlaceholderText('搜尋'); self.body.addWidget(self.query)
        self.list=QListWidget(); self.body.addWidget(self.list,1)
        def populate():
            self.list.clear()
            for title,value in choices:
                if self.query.text().casefold() in title.casefold():
                    item=QListWidgetItem(title); item.setData(Qt.ItemDataRole.UserRole,value); self.list.addItem(item)
            if self.list.count():self.list.setCurrentRow(0)
        self.query.textChanged.connect(populate); populate()
        def choose():
            item=self.list.currentItem()
            if not item:return False
            return callback(item.data(Qt.ItemDataRole.UserRole))
        self.footer(choose,'選擇'); self.list.itemActivated.connect(lambda _:self.apply_button.click())


class NotePreview(QWidget):
    def __init__(self,surface,workspace=None,path=None):
        super().__init__();self.surface=surface;self.text='';self.path=None
        if surface is None:
            from types import SimpleNamespace
            from .markdown_source import SourceSnapshot
            self.surface=SimpleNamespace(mode='read',preferences=dict(reading_family='Microsoft JhengHei UI',reading_size=12,reading_width=880,line_height=150),
                page=SimpleNamespace(path=path,file_title=SimpleNamespace(workspace=workspace),buffer=SimpleNamespace(snapshot=lambda:SourceSnapshot(self.text,0))),
                update_statistics=lambda:None,search_changed=lambda:None,report=lambda message:None)
        from .markdown_rich_view import RichView
        self.rich=RichView(self,interactive=False)
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.addWidget(self.rich)
    def clear(self):self.set_note('')
    def set_note(self,text,path=None):
        self.text=text;self.path=path;self.refresh()
    def refresh(self):
        self.rich.document_path=self.path;self.rich.refresh(self.text)
        from .theme import window_appearance,visual_tokens
        tokens=visual_tokens(window_appearance(self))
        self.rich.payload['palette']['base']=tokens['surface']
        self.rich.payload.update(size=11,width=620,lineHeight=155,preview=True)
    def hideEvent(self,event):
        self.rich.pause();super().hideEvent(event)


class PreviewDialog(Sheet):
    def __init__(self,manager,path,position=0):
        super().__init__(manager.w,path.stem,600,440)
        self.setWindowModality(Qt.WindowModality.NonModal)
        record=manager.panel.records.get(str(path)); text=record.text if record else Notes.disk(path)
        preview=NotePreview(manager.surface(),manager.w,path);preview.set_note(text[max(0,position):position+20000],path)
        self.body.addWidget(preview,1)
        self.body.addLayout(row(None,button('開啟',lambda:(manager.open_note(path,position),self.accept()),'Primary')))


class NotebookSettings(Sheet):
    def __init__(self,actions):
        super().__init__(actions.w,'範本與日誌',560,330); config=actions.config(); self.fields={}
        form=QFormLayout(); form.setVerticalSpacing(14)
        for key,title,default in [('templates','範本資料夾','範本'),('daily_folder','日誌資料夾','日誌'),
                                  ('daily_format','日誌檔名','YYYY-MM-DD')]:
            field=QLineEdit(config.get(key,default)); field.setAccessibleName(title); self.fields[key]=field; form.addRow(title,field)
        self.template=QComboBox(); self.template.addItem('無','')
        for p in actions.available():self.template.addItem(p.stem,p.relative_to(actions.root()).as_posix())
        self.template.setCurrentIndex(max(0,self.template.findData(config.get('daily_template','')))); form.addRow('日誌範本',self.template)
        self.body.addLayout(form)
        def save():
            value={key:field.text().strip() for key,field in self.fields.items()}
            paths.relative_parts(value['templates']); paths.relative_parts(value['daily_folder'])
            from .notebook_actions import moment_format
            paths.relative_parts(moment_format(datetime.now(),value['daily_format'])+'.md')
            value['daily_template']=self.template.currentData(); actions.m.store.put('notebook:'+str(actions.root()),value)
        self.footer(save,'儲存')


class DiffColors(QSyntaxHighlighter):
    def __init__(self,document,owner):
        super().__init__(document);self.owner=owner;self.enabled=True
    def highlightBlock(self,text):
        if not self.enabled:return
        from .theme import visual_tokens,window_appearance
        light=QColor(visual_tokens(window_appearance(self.owner))['base']).lightnessF()>.5
        added,removed,section=('#25632b','#9b3131','#2d5278') if light else ('#8ac48d','#e89595','#9db9d1')
        color=added if text.startswith('+') else removed if text.startswith('-') else section if text.startswith('@@') else None
        if color:
            format=QTextCharFormat(); format.setForeground(QColor(color)); self.setFormat(0,len(text),format)


class RecoveryDialog(Sheet):
    def __init__(self,manager,surface):
        super().__init__(manager.w,'檔案復原',820,540); self.m=manager; self.surface=surface; self.path=surface.page.path
        self.snapshot=surface.page.buffer.snapshot(); self.current=surface.page.encoded()
        manager.store.capture(self.path,self.current,'目前版本',True)
        self.files=QComboBox(); self.files.setAccessibleName('復原檔案'); root=manager.current_root()
        for path in manager.store.paths():
            if root and root in path.parents:self.files.addItem(path.relative_to(root).as_posix(),str(path))
        self.files.setCurrentIndex(self.files.findData(str(self.path))); self.body.addWidget(self.files)
        split=QSplitter(); self.list=QListWidget(); self.list.setMinimumWidth(150); split.addWidget(self.list)
        self.preview=QPlainTextEdit(); self.preview.setReadOnly(True); self.highlighter=DiffColors(self.preview.document(),self)
        split.addWidget(self.preview); split.setSizes([190,550]); self.body.addWidget(split,1)
        self.difference=QCheckBox('比較差異'); self.difference.setChecked(True); self.body.addWidget(self.difference)
        self.footer(self.restore,'復原'); self.files.currentIndexChanged.connect(self.populate)
        self.list.currentItemChanged.connect(self.select); self.difference.toggled.connect(self.select); self.populate()
    def populate(self,*_):
        self.list.clear(); value=self.files.currentData()
        if not value:return
        for ident,at,reason,size in self.m.store.versions(Path(value)):
            item=QListWidgetItem(datetime.fromtimestamp(at).strftime('%m/%d %H:%M:%S')+'\n'+reason)
            item.setData(Qt.ItemDataRole.UserRole,ident); self.list.addItem(item)
        if self.list.count():self.list.setCurrentRow(0)
    def select(self,*_):
        item=self.list.currentItem()
        if not item:return
        try:
            old=decoded(self.m.store.raw(item.data(Qt.ItemDataRole.UserRole)))
            path=Path(self.files.currentData()); current=self.snapshot.text if path==self.path else Notes.disk(path) if path.exists() else ''
            self.apply_button.setEnabled(old!=current or not path.exists())
            text=''.join(difflib.unified_diff(old.splitlines(True),current.splitlines(True),fromfile='復原版本',tofile='目前版本')) if self.difference.isChecked() else old
            self.highlighter.enabled=self.difference.isChecked();self.preview.setPlainText(text or '沒有差異');self.highlighter.rehighlight()
        except (OSError,ValueError,UnicodeError) as error:self.error.setText(str(error)); self.error.show()
    def restore(self):
        item=self.list.currentItem()
        if not item:return False
        path=Path(self.files.currentData()); root=self.m.current_root()
        if root is None or root not in path.parents:raise ValueError('復原位置不在目前資料夾。')
        path=paths.checked(root,path.relative_to(root).as_posix())
        raw=self.m.store.raw(item.data(Qt.ItemDataRole.UserRole)); text=decoded(raw)
        if path==self.path:
            if self.surface.page.buffer.snapshot()!=self.snapshot:raise ValueError('目前文件已變更，請重新開啟復原。')
            self.surface.apply([SourceEdit(0,len(self.snapshot.text),text)])
        elif path.exists():
            self.m.open_note(path); target=self.m.surface(); before=target.page.buffer.snapshot()
            self.m.store.capture(path,target.page.encoded(),'復原前',True)
            target.apply([SourceEdit(0,len(before.text),text)])
        else:
            from .notebook_actions import create_note
            created=create_note(root,path.relative_to(root).as_posix(),raw); self.m.w.refresh_tree(); self.m.open_note(created)
        return True


class WorkspacesDialog(Sheet):
    def __init__(self,manager):
        super().__init__(manager.w,'工作區布局',540,390); self.m=manager
        self.name=QLineEdit(); self.name.setAccessibleName('布局名稱'); self.name.setPlaceholderText('布局名稱')
        self.save_button=button('保存目前布局',lambda:self.run_save());self.body.addLayout(row(self.name,self.save_button))
        self.list=QListWidget(); self.body.addWidget(self.list,1)
        self.previous=None;self.delete_button=button('刪除',self.remove,'Quiet');self.undo_button=button('復原',self.undo,'Quiet')
        self.undo_button.setToolTip('復原上次布局變更');self.undo_button.hide()
        self.load_button=button('載入',lambda:self.run(self.load),'Primary')
        self.body.addWidget(self.error); self.body.addLayout(row(self.delete_button,self.undo_button,None,self.load_button))
        self.name.textChanged.connect(self.update_buttons);self.list.currentItemChanged.connect(self.update_buttons)
        self.populate()
    def update_buttons(self,*_):
        self.save_button.setEnabled(bool(self.name.text().strip()))
        selected=self.list.currentItem() is not None
        self.delete_button.setEnabled(selected);self.load_button.setEnabled(selected)
    def populate(self):
        self.values=self.m.store.get('workspaces',{}); self.list.clear(); self.list.addItems(sorted(self.values))
        if self.list.count():self.list.setCurrentRow(0)
        self.update_buttons()
    def run_save(self):
        name=self.name.text().strip()
        if not name:return
        value=self.m.w.layout_store.snapshot(); value['sidebar']=self.m.panel.mode
        value['sidebar_visible']=self.m.w.sidebar.isVisible()
        self.previous=copy.deepcopy(self.values);self.values[name]=value
        self.m.store.put('workspaces',self.values);self.populate();self.undo_button.show()
        self.list.setCurrentRow(sorted(self.values).index(name))
    def remove(self):
        item=self.list.currentItem()
        if item:
            self.previous=copy.deepcopy(self.values);self.values.pop(item.text(),None)
            self.m.store.put('workspaces',self.values);self.populate();self.undo_button.show()
    def undo(self):
        if self.previous is None:return
        self.m.store.put('workspaces',self.previous);self.previous=None;self.populate();self.undo_button.hide()
    def load(self):
        item=self.list.currentItem()
        if not item:return False
        w=self.m.w
        if w.commands.files.pending:raise ValueError('請先完成目前的文件操作。')
        for tab in w.all_pages():
            if not w.prepare_close(tab):return False
        value=self.values[item.text()]
        if not w.layout_store.restore(value):return False
        self.m.show(value.get('sidebar','files')); w.sidebar.setVisible(value.get('sidebar_visible',True)); w.layout_store.save()
