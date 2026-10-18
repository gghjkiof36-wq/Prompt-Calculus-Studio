"""Document-scoped, conflict-checked shortcut preferences."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import QKeySequenceEdit,QFormLayout,QWidget,QScrollArea,QFrame
from .notebook_dialogs import Sheet

NAMES={'Ctrl+F':'搜尋','Ctrl+H':'取代','Ctrl+K':'編輯連結','Ctrl+Return':'開啟連結',
       'Ctrl+;':'筆記屬性','Ctrl+Shift+P':'命令','Alt+F10':'工具列'}
RESERVED=['Ctrl+S','Ctrl+Shift+S','Ctrl+O','Ctrl+T','Ctrl+W','Ctrl+Shift+F','Ctrl+D','Ctrl+Z','Ctrl+Y',
          'Ctrl+C','Ctrl+X','Ctrl+V','Ctrl+A','Ctrl+B','Ctrl+I','Ctrl+U','Ctrl+Space','Ctrl++','Ctrl+=','Ctrl+-','Ctrl+0',
          'Alt+Left','Alt+Right','Alt+F4','Ctrl+Alt+Up','Ctrl+Alt+Down']
def canonical(value):return QKeySequence(value).toString(QKeySequence.SequenceFormat.PortableText)
def apply_shortcuts(surface):
    workspace=surface.page.file_title.workspace
    if workspace is None or not hasattr(workspace,'notebook'):return
    saved=workspace.notebook.store.get('markdown-shortcuts',{})
    for default,shortcut in surface.command_shortcuts.items():shortcut.setKey(QKeySequence(saved.get(default,default)))

class ShortcutDialog(Sheet):
    def __init__(self,surface):
        super().__init__(surface,'編輯快捷鍵',520,490);self.surface=surface;self.fields={}
        fields=QWidget();form=QFormLayout(fields);form.setVerticalSpacing(10)
        for default,shortcut in surface.command_shortcuts.items():
            field=QKeySequenceEdit(shortcut.key());field.setMaximumSequenceLength(1);field.setAccessibleName(NAMES[default])
            self.fields[default]=field;form.addRow(NAMES[default],field)
        scroll=QScrollArea();scroll.setWidgetResizable(True);scroll.setFrameShape(QFrame.Shape.NoFrame);scroll.setWidget(fields);self.body.addWidget(scroll,1)
        self.footer(self.save,'儲存')
    def save(self):
        values={key:field.keySequence().toString(QKeySequence.SequenceFormat.PortableText) for key,field in self.fields.items()}
        used={};reserved={canonical(value) for value in RESERVED}
        for key,value in values.items():
            if not value:continue
            if value in used:raise ValueError('快捷鍵重複：'+NAMES[used[value]]+'、'+NAMES[key])
            if value in reserved:raise ValueError('快捷鍵已用於編輯或工作區：'+value)
            if not any(mod in value for mod in ('Ctrl+','Alt+','Meta+')) and not value.startswith('F'):
                raise ValueError('請使用包含 Ctrl、Alt 或功能鍵的組合。')
            used[value]=key
        w=self.surface.page.file_title.workspace
        if w:
            w.notebook.store.put('markdown-shortcuts',values)
            for tab in w.all_pages():
                if hasattr(tab.widget,'md_surface'):apply_shortcuts(tab.widget.md_surface)
