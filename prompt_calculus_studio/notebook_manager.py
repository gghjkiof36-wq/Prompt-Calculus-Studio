"""Notebook services. Workers receive immutable text, never editor widgets."""
from pathlib import Path
import threading
from PySide6.QtCore import QObject, QRunnable, QThreadPool, QTimer, Signal, Qt, QSize, QRect
from PySide6.QtGui import QKeySequence, QShortcut, QPalette
from PySide6.QtWidgets import QToolButton, QStyle, QStyleOptionToolButton, QStylePainter
from .widgets import RoundMenu as QMenu
from .notebook_index import inventory, relations
from .notebook_store import NotebookStore
from .notebook_panel import NotebookPanel, MODES
from .workspace_files import MarkdownPage
from .ui_icons import icon


class NotebookSelector(QToolButton):
    """A single title row; the disclosure belongs beside the label's centre."""
    def sizeHint(self):
        return QSize(self.fontMetrics().horizontalAdvance(self.text())+34,max(32,self.fontMetrics().height()+8))

    def paintEvent(self,event):
        option=QStyleOptionToolButton();self.initStyleOption(option)
        option.features=QStyleOptionToolButton.ToolButtonFeature.None_
        option.text=''
        painter=QStylePainter(self);painter.drawComplexControl(QStyle.ComplexControl.CC_ToolButton,option)
        painter.setPen(option.palette.color(QPalette.ColorRole.ButtonText))
        painter.drawText(self.rect().adjusted(6,0,-22,0),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,self.text())
        self.arrow.paint(painter,QRect(self.width()-19,(self.height()-12)//2,12,12))


class IndexSignals(QObject):
    ready=Signal(object,object,object,object)


class IndexJob(QRunnable):
    def __init__(self,root,previous,overrides,current,links,cancelled):
        super().__init__(); self.root=root; self.previous=previous; self.overrides=overrides
        self.current=current; self.links=links; self.cancelled=cancelled; self.signals=IndexSignals()

    def run(self):
        try:
            records,errors=inventory(self.root,self.previous,self.overrides,self.cancelled)
            related=relations(self.root,records,self.current) if self.links and self.current and not self.cancelled.is_set() else None
        except Exception as error:records,errors,related={},[str(error)],None
        if not self.cancelled.is_set():self.signals.ready.emit(self,records,related,errors)


class NotebookManager(QObject):
    def __init__(self,workspace):
        super().__init__(workspace); self.w=workspace; self.store=NotebookStore(workspace.data)
        self.cache={}; self.job=None; self.cancelled=None; self.closed=False; self.dialogs=[]
        self.panel=NotebookPanel(self); self.w.sidebar.layout().addWidget(self.panel,1)
        heading=self.w.tree_heading.layout(); old=heading.takeAt(0).widget(); old.deleteLater()
        self.selector=NotebookSelector(); self.selector.setObjectName('NotebookSelector'); self.selector.setText('內容')
        self.selector.setAccessibleName('側欄檢視'); self.selector.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu=QMenu(self.selector,persistent=True)
        for key,title in MODES.items():menu.addAction(title,lambda checked=False,value=key:self.show(value))
        self.selector.setMenu(menu); heading.insertWidget(0,self.selector)
        self.timer=QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.refresh)
        self.root_timer=QTimer(self); self.root_timer.setInterval(2500); self.root_timer.timeout.connect(self.poll); self.root_timer.start()
        self.last_context=None
        self.search_shortcut=QShortcut(QKeySequence('Ctrl+Shift+F'),workspace)
        self.search_shortcut.activated.connect(lambda:self.show('search'))
        self.actions=None
        self.style()
        for tab in workspace.all_pages():
            if isinstance(tab.widget,MarkdownPage):self.opened(tab.widget)

    def style(self):
        from .theme import visual_tokens, font_pixels
        tokens=visual_tokens(dict(self.w.settings,**self.w.appearance_preview))
        from .module_chrome import dialog_colors
        t=dialog_colors(tokens)
        self.panel.setStyleSheet(f'''QFrame#NotebookPanel {{ background:transparent; }}
            QTreeWidget#NotebookResults {{ background:transparent; border:0; outline:0; font-size:14px; }}
            QTreeWidget#NotebookResults::item {{ padding:4px 6px; border:0; border-radius:4px; }}
            QTreeWidget#NotebookResults::item:selected {{ background:{t['hover']}; }}
            QTreeWidget#NotebookResults::item:hover {{ background:{t['field']}; }}
            QLineEdit,QComboBox {{ min-height:28px; font-size:14px; padding:2px 6px; }}
            QToolButton {{ min-width:24px; max-width:28px; min-height:28px; padding:0; border:0; background:transparent; }}
            QToolButton:hover,QToolButton:checked {{ background:{t['hover']}; }}
            QLabel {{ font-size:12px; }}''')
        self.selector.arrow=icon('chevron-down',tokens['secondary'])
        self.selector.setStyleSheet(f'''QToolButton {{ border:0; background:transparent; font-size:{font_pixels(self.w.display_settings()['ui_size'])}px; font-weight:600; padding:0; border-radius:6px; }}
            QToolButton:hover,QToolButton:pressed {{ background:{t['hover']}; }}
            QToolButton:focus {{ border:1px solid {tokens['secondary']}; }}
            QToolButton::menu-indicator {{ image:none; width:0; height:0; }}''')

    def surface(self):
        tab=self.w.tabs_by_key.get(self.w.active_key)
        return tab.widget.md_surface if tab and isinstance(tab.widget,MarkdownPage) else None

    def current_path(self):
        surface=self.surface(); return surface.page.path if surface else None

    def current_root(self):
        return self.w.locations.root_for(self.current_path()) or self.w.locations.current

    def show(self,mode):
        if self.w.active_route!='home':return
        self.panel.mode=mode; self.selector.setText(MODES[mode]); self.w.sidebar.show()
        self.invalidate()
        self.w.tree.setVisible(mode=='files'); self.panel.setVisible(mode!='files')
        for button in (self.w.tree_new,self.w.tree_folder,self.w.tree_fold,self.w.tree_more):button.setVisible(mode=='files')
        self.panel.search_host.setVisible(mode=='search'); self.panel.roots()
        if mode=='search':self.panel.query.setFocus()
        if mode!='files':self.refresh()

    def schedule(self,*_):
        # Invalidate at the change, not after the debounce: a completed old job
        # can already be queued while the new scope is waiting for its timer.
        self.invalidate()
        if not self.closed and self.panel.mode!='files':self.timer.start(300)

    def invalidate(self):
        if self.cancelled:self.cancelled.set()
        self.job=None
        if getattr(self.panel,'_context',None)!=self.panel.context():
            self.panel.records={};self.panel.results.clear();self.panel.show_status('')
            self.panel._context=None

    def poll(self):
        context=(self.current_path(),tuple(self.w.locations.roots))
        if context!=self.last_context:
            self.last_context=context; self.panel.roots(); self.schedule()
        elif self.panel.isVisible() and not self.job:self.schedule()

    def refresh(self):
        self.timer.stop()
        if self.closed or self.panel.mode=='files':return
        self.invalidate()
        root=self.panel.selected_root()
        if not root:return
        if self.cancelled:self.cancelled.set()
        self.cancelled=threading.Event(); overrides={}
        for tab in self.w.all_pages():
            if isinstance(tab.widget,MarkdownPage) and tab.path and root in tab.path.parents and tab.widget.dirty:
                overrides[str(tab.path)]=tab.widget.buffer.snapshot().text
        self.job=IndexJob(root,self.cache.get(str(root),{}),overrides,self.current_path(),self.panel.mode=='links',self.cancelled)
        self.job.context=self.panel.context()
        self.job.signals.ready.connect(self.ready); QThreadPool.globalInstance().start(self.job)

    def ready(self,job,records,related,errors):
        if self.closed or job is not self.job:return
        if job.cancelled.is_set() or job.context!=self.panel.context():
            self.invalidate();return
        self.job=None; self.cache[str(job.root)]=records; self.panel.populate(records,related,errors)

    def open_note(self,path,position=0,heading=None):
        try:self.w.locations.file(path)
        except (ValueError,OSError) as error:self.w.show_notice(str(error)); return
        self.w.open_path(path,retained=True)
        surface=self.surface()
        if surface:
            if heading:
                from .markdown_commands import heading_sections
                position=next((s[0] for s in heading_sections(surface.page.buffer.snapshot().text) if s[3]==heading),position)
            surface.navigate(position)

    def scope(self):return 'bookmarks:'+str(self.panel.selected_root() or self.current_root())
    def bookmarks(self):return self.store.get(self.scope(),[])
    def bookmark(self,heading=False):
        surface=self.surface()
        if not surface:return
        value={'title':surface.page.path.stem,'path':str(surface.page.path)}
        if heading:
            from .markdown_commands import heading_sections
            position=surface.selection()[1]; sections=heading_sections(surface.page.buffer.snapshot().text)
            section=next((s for s in reversed(sections) if s[0]<=position),None)
            if section:value.update(title=value['title']+' › '+section[3],heading=section[3],position=section[0])
        self.add_bookmark(value)
    def bookmark_search(self):
        if self.panel.query.text().strip():self.add_bookmark({'title':self.panel.query.text(),'query':self.panel.query.text(),'case':self.panel.case.isChecked()})
    def add_bookmark(self,value):
        entries=self.bookmarks()
        if value not in entries:entries.append(value); self.store.put(self.scope(),entries)
        if self.panel.mode=='bookmarks':self.schedule()
    def remove_bookmark(self,index):
        entries=self.bookmarks()
        if 0<=index<len(entries):entries.pop(index); self.store.put(self.scope(),entries); self.schedule()
    def preview(self,path,position=0):
        from .notebook_dialogs import PreviewDialog
        self.keep(PreviewDialog(self,path,position))
    def keep(self,dialog):
        self.dialogs.append(dialog); dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.destroyed.connect(lambda:self.dialogs.remove(dialog) if dialog in self.dialogs else None); dialog.show()
    def operations(self):
        if self.actions is None:
            from .notebook_actions import NotebookActions
            self.actions=NotebookActions(self)
        return self.actions
    def entries(self,surface):
        entries=[(title+' · 筆記',lambda value=key:self.show(value)) for key,title in MODES.items() if key!='files']
        entries.extend([('加入書籤',self.bookmark),('將目前標題加入書籤',lambda:self.bookmark(True)),
                        ('預覽游標處的連結',lambda:self.operations().preview_link(surface))])
        return entries+self.operations().entries(surface)
    def saved(self,page):
        try:self.store.capture(page.path,page.encoded())
        except Exception as error:self.w.show_notice('無法保存復原版本：'+str(error))
        self.schedule()
    def opened(self,page):
        if page.error:return
        try:self.store.capture(page.path,page.baseline,'開啟時',True)
        except Exception as error:self.w.show_notice('無法保存復原版本：'+str(error))
    def stop(self):
        self.closed=True; self.timer.stop(); self.root_timer.stop()
        if self.cancelled:self.cancelled.set()
