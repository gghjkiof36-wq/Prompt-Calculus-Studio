"""Main desktop window. UI calls small data helpers; no tensor or AI runtime."""
import copy
import json
import os
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSize, QPoint, QUrl, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
    QSplitter, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QPlainTextEdit,
    QTabWidget, QStackedWidget, QFileDialog, QAbstractItemView, QDialog, QSizeGrip, QSizePolicy, QFrame, QGraphicsOpacityEffect,QScrollArea,QLayout)
from .core import Storage, uid, build_prompt, compose_details, item_prompt, apply_workspace, validate_state, DEFAULT_SETTINGS, output_groups, reorder_output, TEMPORARY_GROUP
from .completion import CompletionService, PromptEdit
from .media import Catalog
from .jobs import Jobs
from .widgets import label, button, row, panel, ask, preview_path, WindowShell, thumb_icon, information, SplitterFold, ActionHeader, ElidedLabel, InputDialog as QInputDialog, RoundMenu as QMenu, ComboBox as QComboBox
from .views import PromptDelegate, PromptList, BuilderTree, DETAIL_ROLE
from .dialogs import ItemDialog, SettingsDialog, WorkspaceDialog, ModuleDialog, ClearDraftDialog
from .pages import ModelPage, GalleryPage
from .theme import stylesheet, apply_backdrop, update_window_shape
from .display import DisplayRecovery
from .comfy_client import ComfyClient
from .recent import RecentPage
from .run_controls import RunControls
from .export_page import ExportPage
from .text_canvas import TextCanvas, NodeDialog
from . import composition as composition
from .core import activate_selection_view, separate_selections
from .generation import direct_mode
from .generation_panel import GenerationPanel
from .settings_page import SettingsPage,InterfaceChoice
from .canvas_results import CanvasResults
from .changes import ChangeCoordinator
from .widgets import StudioDialog,widget_global_position


class Window(QMainWindow):
    def __init__(self, data_dir):
        super().__init__()
        # Keep WS_CAPTION / WS_THICKFRAME. Alpha painting into a native frame
        # uses DWM redirection; FramelessWindowHint would use WS_EX_LAYERED.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        from .releases import window_title
        self.setWindowTitle(window_title())
        self.setWindowIcon(QIcon(str(Path(__file__).parent/"assets"/"studio.ico")))
        self.resize(1440,900); self.setMinimumSize(960,620)
        self.fresh_install=not (Path(data_dir)/'studio.sqlite3').exists()
        self.store=Storage(data_dir); self.state=self.store.load_current(multi=os.environ.get('PROMPT_STUDIO_V08')=='1'); self.catalog=Catalog(self.store)
        interface=self.state['settings'].get('interface_mode','ask')
        if interface=='ask' and not self.fresh_install: self.state['settings']['interface_mode']=self.state['selection_view']
        elif interface in ('list','canvas'): activate_selection_view(self.state,interface)
        self.completion=CompletionService(self); self.jobs=Jobs(self)
        self.updating=False; self.closing=False; self.library_page=0; self.first_models=True
        self.navigation_ready=False; self.navigation_locked=False; self.navigation_history=[('home',None)]; self.navigation_index=0; self.navigation_buttons=[]
        available={m["id"] for m in self.state["modules"]}
        self.current_module=self.state.get("current_module")
        if self.current_module not in available:
            self.current_module=self.state["items"][0]["module"] if self.state["items"] else next(iter(available),None)
        self.save_timer=QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self.persist)
        self.changes=ChangeCoordinator(self)
        self.filter_timer=QTimer(self); self.filter_timer.setSingleShot(True); self.filter_timer.timeout.connect(self.refresh_library)
        self.host_shell=WindowShell(self); self.setCentralWidget(self.host_shell)
        surface_layout=QVBoxLayout(self.host_shell); surface_layout.setContentsMargins(0,0,0,0)
        self.surface_stack=QStackedWidget(); surface_layout.addWidget(self.surface_stack)
        shell=QWidget(); self.list_shell=shell; self.surface_stack.addWidget(shell)
        outer=QVBoxLayout(shell); outer.setContentsMargins(24,8,24,8); outer.setSpacing(4)
        content=QWidget(); content.setMaximumWidth(1560)
        center=QHBoxLayout(); center.setContentsMargins(0,0,0,0); center.addStretch(); center.addWidget(content,1); center.addStretch(); outer.addLayout(center,1)
        main=QVBoxLayout(content); main.setContentsMargins(0,12,0,0); main.setSpacing(16)
        self.workspace=QComboBox(); self.workspace.setMinimumWidth(190); self.workspace.setMaximumWidth(310); self.workspace.currentIndexChanged.connect(self.switch_workspace)
        self.tagline=label("工作區","Eyebrow")
        workspace_menu=button("⋯",self.workspace_menu,"Quiet"); workspace_menu.setFixedWidth(38); workspace_menu.setToolTip("工作區設定、新增與刪除")
        main.addLayout(row(self.tagline,self.workspace,workspace_menu,
            button("設定",self.settings,"Quiet"),None))
        self.comfy_status=label('未連線時可複製 Prompt；連線並綁定後由桌面直接運行。','Subtle',True); main.addWidget(self.comfy_status)
        self.tabs=QTabWidget(); main.addWidget(self.tabs,1)
        self.tabs.addTab(self.make_prompt_page(),"提示詞")
        self.models=ModelPage(self)
        self.gallery=GalleryPage(self); self.tabs.addTab(self.gallery,"媒體庫")
        self.recent=RecentPage(self); self.tabs.addTab(self.recent,'最近生成')
        self.clean_export=ExportPage(self); self.tabs.addTab(self.clean_export,'匯出')
        self.comfy=ComfyClient(self); self.comfy.stateChanged.connect(self.comfy_changed); self.comfy.resultsReceived.connect(self.recent.receive_results)
        self.tabs.currentChanged.connect(self.page_changed)
        self.status=label("","Subtle",True)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.cancel_button=button("取消背景工作",self.cancel_jobs,"Quiet"); self.cancel_button.hide()
        outer.addLayout(row(self.status,None,self.cancel_button,QSizeGrip(self)))
        self.make_canvas_surface()
        self.settings_page=SettingsPage(self); self.surface_stack.addWidget(self.settings_page)
        self.welcome=InterfaceChoice(self,True); self.surface_stack.addWidget(self.welcome)
        self.refresh_workspaces(); self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.apply_theme()
        if self.state.get('selection_view')=='canvas': self.enter_canvas()
        self.display_recovery=DisplayRecovery(self)
        self.navigation_ready=True
        for sequence,handler in [("Ctrl+F",self.focus_search),("Ctrl+Shift+C",self.copy_final),("Ctrl+,",self.settings)]:
            shortcut=QShortcut(QKeySequence(sequence),self); shortcut.activated.connect(handler)
        for sequence,handler in [('Alt+Left',self.go_back),('Alt+Right',self.go_forward),('Escape',self.escape_page)]:
            shortcut=QShortcut(QKeySequence(sequence),self); shortcut.activated.connect(handler)
        self.store.save(self.state)

    def make_prompt_page(self):
        page=QFrame(); page.setObjectName("WorkspaceSurface"); layout=QVBoxLayout(page); layout.setContentsMargins(12,16,12,16)
        self.quick=PromptEdit(self.completion); self.quick.setObjectName("QuickSearch"); self.quick.setFixedHeight(54)
        self.quick.setPlaceholderText("搜尋素材，或輸入 Tag／中文…")
        self.quick.textChanged.connect(self.search_changed); self.quick.accepted.connect(self.accept_quick)
        self.scope=QComboBox(); self.scope.addItems(["全部模組","目前模組"]); self.scope.currentIndexChanged.connect(self.search_changed)
        self.prompt_modes=QTabWidget(); layout.addWidget(self.prompt_modes,1)
        self.prompt_modes.tabBar().hide()
        list_page=QWidget(); list_layout=QVBoxLayout(list_page); list_layout.setContentsMargins(0,0,0,0)
        self.prompt_modes.addTab(list_page,'清單')
        from .quiet_splitter import QuietSplitter
        self.split=QuietSplitter(guided_handles={1}); list_layout.addWidget(self.split,1)
        self.module_panel,modules=panel("SidePanel"); self.split.addWidget(self.module_panel)
        self.module_panel.setMinimumWidth(150); self.module_panel.setMaximumWidth(280)
        modules.setContentsMargins(8,12,8,12)
        modules.addLayout(row(label("模組","Heading"),None,button("＋",self.new_module,"Quiet")))
        self.module_list=QListWidget(); self.module_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.module_list.setSpacing(3); self.module_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.module_list.currentItemChanged.connect(self.select_module); self.module_list.model().rowsMoved.connect(self.module_order_changed)
        self.module_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.module_list.customContextMenuRequested.connect(self.module_context)
        modules.addWidget(self.module_list,1)
        modules.addWidget(button("編輯模組",self.edit_module,"Quiet"))
        self.library_panel,library=panel(); self.split.addWidget(self.library_panel)
        self.library_title=label("素材庫","Heading")
        library.setContentsMargins(8,12,14,12)
        toggle=button("☰",self.toggle_modules,"Quiet"); toggle.setToolTip("顯示／收合模組"); toggle.setFixedWidth(36)
        library.addLayout(row(toggle,self.library_title,None,button("＋ 新增",self.new_item)))
        self.library_hint=label("","Subtle",True); library.addWidget(self.library_hint)
        library.addWidget(self.quick)
        library.addLayout(row(self.scope,None,button("＋ 加入片段",lambda:self.add_temporary(self.quick.toPlainText(),clear=True),"Quiet")))
        self.library=PromptList(); self.library.weightRequested.connect(self.adjust_weight); self.library.setIconSize(QSize(88,88)); self.library.itemClicked.connect(self.toggle_item); self.library.itemDoubleClicked.connect(lambda item:self.edit_item(item.data(Qt.ItemDataRole.UserRole)))
        self.library.model().rowsMoved.connect(self.library_order_changed)
        self.library.setItemDelegate(PromptDelegate(self)); self.library.setMouseTracking(True)
        self.library.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.library.customContextMenuRequested.connect(self.item_context)
        library.addWidget(self.library,1)
        self.library_count=label("","Subtle")
        self.library_previous=button("‹",lambda:self.turn_library(-1),"Quiet"); self.library_next=button("›",lambda:self.turn_library(1),"Quiet")
        library.addLayout(row(self.library_count,None,self.library_previous,self.library_next))
        builder,right=panel("InsetPanel"); self.builder_panel=builder
        self.builder_scroll=QScrollArea(); self.builder_scroll.setWidgetResizable(True); self.builder_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.builder_scroll.setMinimumWidth(340); self.builder_scroll.setWidget(builder)
        right.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.split.addWidget(self.builder_scroll); self.split.setSizes([190,565,485])
        self.split.setChildrenCollapsible(False)
        self.builder_toggle=button("收合",self.toggle_builder,"Quiet")
        self.selection_count=label("","Eyebrow")
        self.selection_heading=label("目前組合","Heading")
        right.addLayout(row(self.selection_heading,self.selection_count,None,self.builder_toggle))
        self.builder_hint=label("","Subtle",True); self.builder_hint.hide()
        self.builder_split=QSplitter(Qt.Orientation.Vertical); right.addWidget(self.builder_split,1)
        self.selected=BuilderTree(); self.selected.setMinimumHeight(90)
        self.selected.moveRequested.connect(self.reorder_builder); self.selected.removeRequested.connect(self.remove_builder_entry); self.selected.rejected.connect(self.notice)
        self.selected.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.selected.customContextMenuRequested.connect(self.builder_context)
        self.selected.itemDoubleClicked.connect(self.open_composition)
        self.builder_split.addWidget(self.selected)
        output=QWidget(); out=QVBoxLayout(output); out.setContentsMargins(0,6,0,0); out.setSpacing(10)
        out.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        divider=QFrame(); divider.setObjectName("SoftDivider"); divider.setFixedHeight(1); out.addWidget(divider)
        self.draft_status=label("","Subtle",True)
        self.save_prompt=button("存入素材庫",lambda:self.new_item(self.final.toPlainText()),"Quiet")
        self.clear_draft=button("清除內容",self.regenerate,"ClearDraft")
        self.clear_draft.setToolTip("清除手動版本，恢復目前組合的提示詞")
        out.addWidget(ActionHeader(label("最終 Prompt 輸出","Heading"),self.save_prompt,self.clear_draft))
        out.addWidget(self.draft_status)
        self.final=QPlainTextEdit(); self.final.setObjectName("Prompt"); self.final.setPlaceholderText("選擇素材，或搜尋後加入臨時片段。\n也可以直接在這裡編輯提示詞。")
        self.final.setMinimumHeight(130)
        self.final.textChanged.connect(self.final_edited); out.addWidget(self.final,1)
        self.builder_split.addWidget(output); self.builder_split.setSizes([250,330])
        self.conflict_notice=label('','ConflictNotice',True); self.conflict_notice.hide(); out.addWidget(self.conflict_notice)
        self.generation_panel=GenerationPanel(self); right.addWidget(self.generation_panel)
        self.run_controls=RunControls(self,copy_fallback=True); self.copy_button=self.run_controls.run_button; right.addWidget(self.run_controls)
        self.copy_button.setMinimumHeight(44)
        self.copy_timer=QTimer(self); self.copy_timer.setSingleShot(True); self.copy_timer.timeout.connect(self.reset_copy_feedback)
        self.latest_preview=button('查看最近生成',lambda:self.show_page(self.recent),'Quiet'); self.latest_preview.setIconSize(QSize(96,72)); self.latest_preview.hide(); right.addWidget(self.latest_preview)
        self.module_fold=SplitterFold(self.split,self.module_panel)
        self.builder_fold=SplitterFold(self.builder_split,self.selected)
        self.draft_active=None; self.draft_fade=QVariantAnimation(self); self.draft_fade.setDuration(180)
        self.draft_fade.setEasingCurve(QEasingCurve.Type.OutCubic); self.draft_fade.valueChanged.connect(self.set_combination_opacity)
        self.selection_effects=[]
        for widget in (self.selection_heading,self.selection_count,self.builder_hint):
            effect=QGraphicsOpacityEffect(widget); effect.setOpacity(1); widget.setGraphicsEffect(effect); self.selection_effects.append(effect)
        if 'multi_output' in self.state:
            from .multi_canvas import MultiCanvas
            self.canvas=MultiCanvas(self)
        else: self.canvas=TextCanvas(self)
        self.prompt_modes.addTab(QWidget(),'Canvas')
        self.prompt_modes.currentChanged.connect(lambda index:self.enter_canvas() if index==1 else None)
        return page

    def make_canvas_surface(self):
        self.canvas.results=CanvasResults(self)
        self.canvas_shell=QWidget(); layout=QVBoxLayout(self.canvas_shell)
        layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.canvas_header=QWidget(); header=QHBoxLayout(self.canvas_header)
        header.setContentsMargins(8,2,8,2); header.setSpacing(0)
        self.canvas_header.setStyleSheet('QPushButton { padding:4px 10px; } QComboBox { padding:4px 34px 4px 10px; }')
        layout.addWidget(self.canvas_header)
        self.canvas_workspace=QComboBox(); self.canvas_workspace.setFixedWidth(156)
        self.canvas_workspace.currentIndexChanged.connect(self.canvas_workspace_changed)
        self.canvas_status=label('','CanvasNotice',True)
        self.canvas_status.setStyleSheet('QLabel#CanvasNotice { color:#c4ccd8; background:rgba(28,31,38,235); border-radius:6px; padding:6px 10px; }')
        self.canvas.view.set_status_widget(self.canvas_status)
        self.canvas_mode=False
        self.canvas_connect=button('連線',lambda:self.settings('workflows'),'Quiet')
        self.canvas_backup=button('資料',self.data_menu,'Quiet')
        header.addWidget(self.canvas_workspace)
        header.addWidget(self.navigation_button(-1)); header.addWidget(self.navigation_button(1))
        header.addWidget(button('⋯',self.workspace_menu,'Quiet'))
        header.addWidget(button('畫布',self.return_to_prompt,'Quiet'))
        self.canvas_navigation={}
        for title,page in [('媒體庫',self.gallery),('匯出',self.clean_export)]:
            if title=='匯出':
                self.canvas_settings=button('設定',self.settings,'Quiet'); header.addWidget(self.canvas_settings)
            entry=button(title,lambda checked=False,p=page:self.show_page(p),'Quiet')
            self.canvas_navigation[title]=entry; header.addWidget(entry)
        self.canvas_connection_status=ElidedLabel(self.comfy_status.text()); self.canvas_connection_status.setObjectName('Subtle')
        self.canvas_connection_status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.canvas_connection_status.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter)
        self.canvas_connection_status.setToolTip(self.comfy_status.text())
        header.addWidget(self.canvas_connection_status,1)
        self.canvas_connect.hide(); self.canvas_backup.hide()
        self.canvas_content=QStackedWidget(); self.canvas_content.addWidget(self.canvas); self.canvas_auxiliary=None
        layout.addWidget(self.canvas_content,1); self.surface_stack.addWidget(self.canvas_shell)

    def restore_canvas_page(self):
        if self.canvas_auxiliary is None: return
        page,index,title=self.canvas_auxiliary; self.canvas_auxiliary=None
        self.canvas_content.removeWidget(page); self.tabs.blockSignals(True)
        self.tabs.insertTab(index,page,title); self.tabs.blockSignals(False)

    def start_interface(self):
        if self.state['settings'].get('interface_mode')=='ask': self.surface_stack.setCurrentWidget(self.welcome)
        else: self.return_to_prompt()

    def set_interface_mode(self,mode):
        if mode not in ('list','canvas'): return
        self.state['settings']['interface_mode']=mode
        if mode=='canvas': self.enter_canvas()
        else: self.leave_canvas()
        self.persist()

    def return_to_prompt(self):
        if self.state.get('selection_view')=='canvas': self.enter_canvas()
        else: self.leave_canvas()

    def enter_canvas(self):
        if not hasattr(self,'canvas_shell'): return
        self.canvas_mode=True
        self.latest_preview.hide()
        self.activate_prompt_view('canvas')
        self.generation_panel.refresh()
        self.restore_canvas_page(); self.canvas_content.setCurrentWidget(self.canvas)
        self.surface_stack.setCurrentWidget(self.canvas_shell)
        self.refresh_builder(); self.changed(); QTimer.singleShot(0,self.canvas.fit)
        self.canvas.functions.mode_changed()
        self.record_navigation('home')

    def leave_canvas(self,checked=False,retain_mode=False):
        self.canvas_mode=retain_mode
        self.restore_canvas_page()
        self.canvas.release_output()
        self.surface_stack.setCurrentWidget(self.list_shell); self.prompt_modes.setCurrentIndex(0)
        if not retain_mode:
            self.activate_prompt_view('list'); self.refresh_builder(); self.changed()
        if not retain_mode: self.tabs.setCurrentIndex(0)
        if not retain_mode: self.show_latest_generated()
        self.generation_panel.refresh(); self.record_navigation('home')

    def show_page(self,page):
        if page is self.models: self.settings('models'); return
        if self.canvas_mode and page is self.recent:
            self.show_recent_sheet(); return
        if self.canvas_mode:
            self.restore_canvas_page(); index=self.tabs.indexOf(page)
            if index>=0:
                title=self.tabs.tabText(index); self.tabs.blockSignals(True); self.tabs.removeTab(index); self.tabs.blockSignals(False)
                self.canvas_auxiliary=(page,index,title); self.canvas_content.addWidget(page)
            self.surface_stack.setCurrentWidget(self.canvas_shell); self.canvas_content.setCurrentWidget(page)
        else: self.tabs.setCurrentWidget(page)
        if page is self.gallery: self.gallery.refresh_albums()
        if page is self.recent: self.recent.refresh_destinations(); self.recent.request_refresh()
        self.record_navigation('page',next((name for name in ('gallery','recent','clean_export') if getattr(self,name) is page),None))

    def show_recent_sheet(self):
        if self.state.get('multi_output',{}).get('version',0)>=7:
            from PySide6.QtWidgets import QDockWidget
            if not getattr(self,'recent_dock',None):
                self.recent_dock=QDockWidget('最近生成',self);self.recent_dock.setAllowedAreas(Qt.DockWidgetArea.RightDockWidgetArea)
                self.recent_dock.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
                self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea,self.recent_dock)
                self.recent_dock.setMinimumWidth(350)
                def restore_recent(visible):
                    if not visible and self.recent_dock.widget():
                        self.recent.setParent(None);self.tabs.insertTab(self.recent_dock_index,self.recent,'最近生成')
                self.recent_dock.visibilityChanged.connect(restore_recent)
            if self.recent_dock.widget():self.recent_dock.hide();return
            self.recent_dock_index=max(0,self.tabs.indexOf(self.recent));self.tabs.removeTab(self.tabs.indexOf(self.recent))
            self.recent_dock.setWidget(self.recent);self.recent_dock.show();self.recent.show();self.recent.request_refresh();return
        from .widgets import DismissibleSheet
        if getattr(self,'recent_sheet',None): self.recent_sheet.raise_(); return
        self.restore_canvas_page()
        index=self.tabs.indexOf(self.recent); title=self.tabs.tabText(index)
        self.tabs.blockSignals(True); self.tabs.removeTab(index); self.tabs.blockSignals(False)
        sheet=DismissibleSheet(self); self.recent_sheet=sheet; sheet.setWindowTitle('最近生成')
        sheet.resize(round(self.width()*.86),round(self.height()*.86))
        sheet.body.setContentsMargins(0,0,0,0); sheet.body.addWidget(self.recent)
        self.recent.heading.hide(); self.recent.show()
        def restore(_):
            sheet.body.removeWidget(self.recent); self.recent.setParent(None)
            self.tabs.blockSignals(True); self.tabs.insertTab(index,self.recent,title); self.tabs.blockSignals(False)
            self.recent.heading.show()
            self.recent_sheet=None
            self.canvas.results.refresh()
            if self.recent.record and self.recent.record['id'] in self.canvas.results.ids:
                self.canvas.results.images.setCurrentRow(self.canvas.results.ids.index(self.recent.record['id']))
            sheet.deleteLater()
        sheet.finished.connect(restore)
        self.recent.refresh_destinations(); self.recent.request_refresh(); sheet.show()

    def navigation_button(self,step):
        entry=button('‹' if step<0 else '›',self.go_back if step<0 else self.go_forward,'Quiet')
        entry.setToolTip('上一頁 · Alt+←' if step<0 else '下一頁 · Alt+→'); entry.setFixedWidth(30)
        self.navigation_buttons.append((entry,step)); return entry

    def record_navigation(self,kind,detail=None):
        if not self.navigation_ready or self.navigation_locked: return
        route=(kind,detail)
        if self.navigation_history[self.navigation_index]!=route:
            self.navigation_history=self.navigation_history[:self.navigation_index+1]+[route]
            self.navigation_history=self.navigation_history[-60:]; self.navigation_index=len(self.navigation_history)-1
        self.update_navigation()

    def update_navigation(self):
        pairs=list(self.navigation_buttons)
        if hasattr(self,'settings_page'): pairs.extend(((self.settings_page.back_button,-1),(self.settings_page.forward_button,1)))
        for widget,step in pairs: widget.setEnabled(0<=self.navigation_index+step<len(self.navigation_history))

    def go_back(self): self.visit_history(-1)
    def go_forward(self): self.visit_history(1)
    def visit_history(self,step):
        index=self.navigation_index+step
        if not 0<=index<len(self.navigation_history): return
        self.navigation_index=index; kind,detail=self.navigation_history[index]; self.navigation_locked=True
        try:
            if kind=='settings': self.settings(detail)
            else:
                if self.settings_page.preferences is not None: self.settings_page.cancel()
                if kind=='home': self.return_to_prompt()
                elif kind=='page': self.show_page(getattr(self,detail))
        finally: self.navigation_locked=False; self.update_navigation()

    def escape_page(self):
        if getattr(self.canvas,'editor_page',None): self.canvas.editor_page.close_editor()
        elif self.surface_stack.currentWidget() is self.settings_page: self.settings_page.cancel()
        elif self.surface_stack.currentWidget() is not self.welcome: self.return_to_prompt()

    def activate_prompt_view(self,view):
        if view!=self.state.get('selection_view','list') and separate_selections(self.state):
            self.canvas.undo_stack.clear(); self.canvas.redo_stack.clear(); self.canvas.last_state=None
        activate_selection_view(self.state,view)

    def canvas_workspace_changed(self):
        if not hasattr(self,'canvas_workspace'): return
        index=self.workspace.findData(self.canvas_workspace.currentData())
        if index>=0: self.workspace.setCurrentIndex(index)

    def notice(self,text):
        if hasattr(self,"status"): self.status.setText(text)
        if hasattr(self,'canvas_status'):
            self.canvas_status.setText(text); self.canvas_status.setToolTip(text)
            self.canvas.view.position_status()
        if hasattr(self,"cancel_button"): self.cancel_button.setVisible(bool(self.jobs.active))

    def error(self,text):
        information(self,"未能完成",text)

    def changed(self,scope='prompt',refresh=True):
        self.changes.request(scope,refresh)

    def connect_comfy(self):
        self.settings('workflows')

    def comfy_changed(self):
        self.comfy_status.setText(self.comfy.message); self.run_controls.refresh(); self.recent.update_save_button()
        self.comfy_status.setStyleSheet('color:#e4ba59;' if self.comfy.control_interrupted else '')
        if hasattr(self.canvas,'results'): self.canvas.results.update_save()
        if hasattr(self.canvas,'execution_bar'):self.canvas.execution_bar.update_progress()
        if hasattr(self,'canvas_connection_status'):
            self.canvas_connection_status.setText(self.comfy.message)
            self.canvas_connection_status.setToolTip(self.comfy.message)
            self.canvas_connection_status.setStyleSheet(self.comfy_status.styleSheet())

    def open_export(self,paths):
        self.show_page(self.clean_export); self.clean_export.load_sources(paths)

    def use_image_for_generation(self,path,output=None):
        if not self.generation_panel.set_source(path,output): return
        if getattr(self,'recent_sheet',None): self.recent_sheet.accept()
        if self.state.get('selection_view')=='canvas': self.enter_canvas()
        else: self.leave_canvas()

    def show_latest_generated(self,record=None):
        if hasattr(self.canvas,'results'): self.canvas.results.refresh()
        if record is None:
            records=self.catalog.rows('recent',limit=1)
            record=records[0] if records else None
        if record is None: self.latest_preview.hide(); return
        self.latest_preview.setIcon(thumb_icon(self.store,record.get('thumb'),96)); self.latest_preview.setVisible(not self.canvas_mode)

    def persist(self):
        try:
            self.remember_canvas_view()
            self.store.save(self.state)
            return True
        except Exception as exc:
            self.error("資料儲存失敗，請先匯出備份。\n"+str(exc))
            return False

    def apply_theme(self, preserve_layout=False, refresh_fonts=False):
        appearance=dict(self.state["settings"])
        appearance.update(getattr(self,"appearance_preview",{}))
        self.native_material=apply_backdrop(self,appearance["material"])
        if not self.native_material: appearance["material"]="solid"
        if refresh_fonts: self.setStyleSheet("")
        self.setStyleSheet(stylesheet(appearance))
        self.run_controls.configure_geometry()
        if hasattr(self,'settings_page'):
            self.settings_page.civitai.configure_fonts(); self.settings_page.workflow_manager.update_list_height()
        update_window_shape(self)
        self.tagline.setVisible(appearance["ui_size"]<16)
        self.quick.setFixedHeight(max(54,int(appearance["ui_size"]*2.4+22)))
        self.library.doItemsLayout()
        self.selected.doItemsLayout()
        if not preserve_layout and ((appearance["ui_size"]>=16 and self.height()<820) or self.height()<740):
            self.builder_fold.set_expanded(False,animated=False); self.builder_hint.hide(); self.builder_toggle.setText("展開組合")
        if not preserve_layout and appearance["ui_size"]>=16 and self.width()<1160: self.module_fold.set_expanded(False,animated=False)

    def showEvent(self,event):
        super().showEvent(event); self.apply_theme(preserve_layout=True)
        if hasattr(self,"display_recovery"): self.display_recovery.bind_window()

    def event(self,event):
        if event.type() in (QEvent.Type.DevicePixelRatioChange,QEvent.Type.ScreenChangeInternal):
            if hasattr(self,"display_recovery"): self.display_recovery.schedule("qt-display-change")
        return super().event(event)

    def nativeEvent(self,event_type,message):
        if hasattr(self,"display_recovery"):
            self.display_recovery.native_event(event_type,message)
        return super().nativeEvent(event_type,message)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"state"): update_window_shape(self)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,"state"):
            update_window_shape(self)

    def refresh_workspaces(self):
        self.workspace.blockSignals(True); self.workspace.clear()
        for workspace in self.state["workspaces"]: self.workspace.addItem(workspace["name"],workspace["id"])
        self.workspace.setCurrentIndex(self.workspace.findData(self.state["workspace"])); self.workspace.blockSignals(False)
        if hasattr(self,'canvas_workspace'):
            self.canvas_workspace.blockSignals(True); self.canvas_workspace.clear()
            for workspace in self.state['workspaces']: self.canvas_workspace.addItem(workspace['name'],workspace['id'])
            self.canvas_workspace.setCurrentIndex(self.canvas_workspace.findData(self.state['workspace']))
            self.canvas_workspace.blockSignals(False)

    def switch_workspace(self):
        if self.workspace.currentData():
            target=self.workspace.currentData()
            if target==self.state['workspace']:return
            self.change_workspace(target)
            if hasattr(self,'canvas_workspace'):
                self.canvas_workspace.blockSignals(True)
                self.canvas_workspace.setCurrentIndex(self.canvas_workspace.findData(self.state['workspace']))
                self.canvas_workspace.blockSignals(False)
            self.notice('已切換工作區；原工作區的等待工作已保留並暫停。')

    def remember_canvas_view(self):
        if hasattr(self,'canvas') and self.canvas.isVisible():
            view=self.canvas.view;point=view.mapToScene(view.viewport().rect().center())
            self.state['canvas_view']=[view.transform().m11(),point.x(),point.y()]

    def change_workspace(self,target):
        previous=self.state['workspace'];self.remember_canvas_view()
        if hasattr(self,'comfy'):self.comfy.input_flow.workspace_changed(previous)
        if hasattr(self,'canvas'):
            canvas=self.canvas
            histories=getattr(self,'workspace_histories',{})
            histories[previous]=(canvas.undo_stack,canvas.redo_stack)
            self.workspace_histories=histories
            canvas.undo_stack,canvas.redo_stack=histories.get(target,([],[]));canvas.last_state=None
            canvas.root_id=None;canvas.path=[]
        apply_workspace(self.state,target)
        if hasattr(self,'comfy') and hasattr(self.comfy.input_flow.chain,'load_results'):self.comfy.input_flow.chain.load_results()
        self.refresh_workspaces();self.refresh_library();self.refresh_builder();self.generation_panel.refresh()
        if hasattr(self,'canvas'):self.canvas.restore_view()
        self.changed()

    def workspace_settings(self):
        WorkspaceDialog(self).exec()

    def workspace_menu(self):
        menu=QMenu(self); menu.addAction("工作區設定",self.workspace_settings); menu.addAction("新增空白工作區…",self.new_workspace)
        menu.addAction('複製目前工作區…',lambda:self.new_workspace(duplicate=True))
        menu.addSeparator(); menu.addAction("刪除目前工作區…",self.delete_workspace); menu.open_at(self.cursor().pos())

    def new_workspace(self,checked=False,duplicate=False):
        name,ok=QInputDialog.getText(self,"新增工作區","名稱，例如：Anima2")
        if ok and name.strip():
            from .workspace_scene import create,upgrade
            upgrade(self.state);ident=create(self.state,name.strip(),duplicate)
            self.change_workspace(ident)

    def delete_workspace(self):
        if len(self.state["workspaces"])==1: self.notice("至少保留一個工作區。"); return
        if ask(self,"刪除工作區","移除此工作區及參數歷史？素材庫、圖片與模型不會刪除。"):
            removed=self.state['workspace'];target=next(w['id'] for w in self.state['workspaces'] if w['id']!=removed)
            self.change_workspace(target)
            self.state['workspaces']=[w for w in self.state['workspaces'] if w['id']!=removed]
            self.state.get('workspace_scenes',{}).get('items',{}).pop(removed,None)
            getattr(self,'workspace_histories',{}).pop(removed,None)
            self.refresh_workspaces();self.changed()

    def settings(self,section='interface'):
        if not isinstance(section,str): section='interface'
        self.settings_page.open(section); self.surface_stack.setCurrentWidget(self.settings_page)

    def refresh_modules(self):
        self.module_list.blockSignals(True); self.module_list.clear()
        for module in self.state["modules"]:
            mode="單選" if module["mode"]=="single" else "複選"
            item=QListWidgetItem(module['name']); item.setToolTip(f"{mode} · 拖曳調整順序，右鍵複製或編輯")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setData(Qt.ItemDataRole.UserRole,module["id"]); self.module_list.addItem(item)
            if module["id"]==self.current_module: self.module_list.setCurrentItem(item)
        self.module_list.blockSignals(False)

    def select_module(self,item):
        self.current_module=item.data(Qt.ItemDataRole.UserRole) if item else None; self.library_page=0
        self.state["current_module"]=self.current_module; self.changed()
        self.refresh_library()

    def module_order_changed(self):
        if self.updating: return
        mapping={m["id"]:m for m in self.state["modules"]}
        self.state["modules"]=[mapping[self.module_list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.module_list.count())]
        self.refresh_builder(); self.changed()

    def new_module(self):
        dialog=ModuleDialog(self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            module=dict(id=uid(),name=dialog.name.text().strip(),mode=dialog.mode.currentData())
            self.state["modules"].append(module); self.current_module=module["id"]; self.refresh_modules(); self.refresh_library(); self.changed()

    def edit_module(self):
        module=next((m for m in self.state["modules"] if m["id"]==self.current_module),None)
        if not module: return
        dialog=ModuleDialog(self,module)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            module.update(name=dialog.name.text().strip(),mode=dialog.mode.currentData())
            if module["mode"]=="single":
                for picks in [self.state["selections"]]+[w["picks"] for w in self.state["workspaces"]]:
                    if module["id"] in picks: picks[module["id"]]=picks[module["id"]][:1]
            self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.changed()

    def delete_module(self):
        mid=self.current_module
        module=next((m for m in self.state["modules"] if m["id"]==mid),None)
        if not module or not ask(self,"刪除模組",f"刪除「{module['name']}」及其全部提示詞項目？此動作不會刪除模型或圖片原檔。"): return
        self.state["modules"]=[m for m in self.state["modules"] if m["id"]!=mid]
        if self.state.get("temporary_before")==mid: self.state["temporary_before"]=None
        self.state["items"]=[i for i in self.state["items"] if i["module"]!=mid]; self.state["selections"].pop(mid,None)
        for w in self.state["workspaces"]:
            w["fixed"]=[m for m in w["fixed"] if m!=mid]; w["picks"].pop(mid,None)
        self.current_module=self.state["modules"][0]["id"] if self.state["modules"] else None
        self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.changed()

    def module_context(self,pos):
        item=self.module_list.itemAt(pos)
        if not item: return
        self.module_list.setCurrentItem(item); menu=QMenu(self)
        menu.addAction("複製本模組已選提示詞",lambda:self.copy_module(self.current_module))
        menu.addAction("複製本模組全部提示詞",lambda:self.copy_module(self.current_module,True))
        menu.addSeparator()
        menu.addAction('向上移動',lambda:self.move_module(-1)); menu.addAction('向下移動',lambda:self.move_module(1)); menu.addAction('移到最上方',lambda:self.move_module(-10000))
        menu.addSeparator(); menu.addAction("編輯名稱與選擇模式",self.edit_module); menu.addAction("清除此模組選擇",self.clear_module); menu.addAction("刪除模組…",self.delete_module)
        menu.open_at(self.module_list.viewport().mapToGlobal(pos))

    def copy_module(self,mid,all_items=False):
        items={i["id"]:i for i in self.state["items"]}
        ids=[i["id"] for i in self.state["items"] if i["module"]==mid] if all_items else self.state["selections"].get(mid,[])
        self.copy_text(", ".join(items[i]["prompt"] for i in ids))

    def move_module(self,delta):
        index=next((i for i,m in enumerate(self.state['modules']) if m['id']==self.current_module),-1)
        if index<0: return
        modules=self.state['modules']; target=max(0,min(len(modules)-1,index+delta))
        modules.insert(target,modules.pop(index)); self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.changed()

    def move_library_item(self,ident,delta):
        original=next(i for i in self.state['items'] if i['id']==ident)
        group=[i for i in self.state['items'] if i['module']==original['module']]
        index=next(n for n,i in enumerate(group) if i['id']==ident); target=max(0,min(len(group)-1,index+delta))
        group.insert(target,group.pop(index)); ordered=iter(group)
        self.state['items']=[next(ordered) if i['module']==original['module'] else i for i in self.state['items']]
        self.refresh_library(); self.changed()

    def clear_module(self):
        self.state["selections"][self.current_module]=[]; self.refresh_library(); self.refresh_builder(); self.changed()

    def search_changed(self):
        self.library_page=0; self.filter_timer.start(150)

    def turn_library(self,delta):
        self.library_page=max(0,self.library_page+delta); self.refresh_library()

    def refresh_library(self):
        if not hasattr(self,"library"): return
        self.library.clear()
        query=self.quick.toPlainText().strip().casefold()
        modules={m["id"]:m for m in self.state["modules"]}
        _,affected=compose_details(self.state)
        found=[]
        for item in self.state["items"]:
            if (not query or self.scope.currentIndex()==1) and item["module"]!=self.current_module: continue
            if query and query not in " ".join([item["name"],item["prompt"],item["notes"],*item["aliases"]]).casefold(): continue
            found.append(item)
        self.library_page=min(self.library_page,max(0,(len(found)-1)//60))
        self.library_title.setText("搜尋結果" if query else modules.get(self.current_module,{}).get("name","素材庫"))
        mode=modules.get(self.current_module,{}).get("mode")
        self.library_hint.setText("名稱、中文別名與 Prompt 都能搜尋" if query else "單選 · 選擇另一項會替換目前選擇" if mode=="single" else "複選 · 將常用項目自由組合")
        for item in found[self.library_page*60:(self.library_page+1)*60]:
            chosen=item["id"] in self.state["selections"].get(item["module"],[])
            prompt=item["prompt"].replace("\n"," ")
            text=("[已選]  " if chosen else "＋  ")+item["name"]+"\n"+prompt[:140]+("…" if len(prompt)>140 else "")
            if query: text+="\n"+modules[item["module"]]["name"]
            widget=QListWidgetItem(text); widget.setData(Qt.ItemDataRole.UserRole,item["id"]); widget.setToolTip(item["prompt"])
            widget.setFlags(widget.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            widget.setIcon(thumb_icon(self.store,item.get("preview"),88))
            detail=dict(item,chosen=chosen,module_name=modules[item["module"]]["name"],weight=self.state.get('weights',{}).get(item['id'],10))
            detail['affected']=affected.get(item['id'])
            if detail['affected']:
                widget.setToolTip(item['prompt']+'\n\n暫時停用：'+', '.join(detail['affected']['tags'])+'\n來源：'+', '.join(detail['affected']['by']))
            detail['prompt']=item_prompt(self.state,item); widget.setData(DETAIL_ROLE,detail)
            self.library.addItem(widget)
        self.library_count.setText(f"{len(found)} 個項目 · 第 {self.library_page+1} 頁")
        self.library_previous.setVisible(len(found)>60); self.library_next.setVisible(len(found)>60)
        self.library_previous.setEnabled(self.library_page>0); self.library_next.setEnabled((self.library_page+1)*60<len(found))

    def library_order_changed(self,*_):
        ids=[self.library.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.library.count())]
        visible=set(ids); items={i['id']:i for i in self.state['items']}
        ordered=iter(ids)
        self.state['items']=[items[next(ordered)] if i['id'] in visible else i for i in self.state['items']]
        self.changed()

    def adjust_weight(self,ident,delta):
        if self.state['draft'] is not None:
            self.notice('正在使用手動版本；清除手動內容後才能調整權重。'); return
        weights=self.state.setdefault('weights',{})
        weights[ident]=max(0,min(1000,weights.get(ident,10)+delta))
        self.refresh_library(); self.refresh_builder(); self.changed()

    def toggle_item(self,widget):
        item=next(i for i in self.state["items"] if i["id"]==widget.data(Qt.ItemDataRole.UserRole))
        module=next(m for m in self.state["modules"] if m["id"]==item["module"])
        selected=self.state["selections"].setdefault(module["id"],[])
        if item["id"] in selected: selected.remove(item["id"])
        elif module["mode"]=="single": self.state["selections"][module["id"]]=[item["id"]]
        else: selected.append(item["id"])
        self.refresh_library(); self.refresh_builder(); self.changed()

    def new_item(self,initial=""):
        if isinstance(initial,bool): initial=""
        if not self.state["modules"]: self.notice("請先新增模組。"); return
        dialog=ItemDialog(self,initial=initial)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.state["items"].append(dialog.item); self.current_module=dialog.item["module"]
            self.refresh_modules(); self.refresh_library(); self.changed()

    def edit_item(self,ident):
        original=next(i for i in self.state["items"] if i["id"]==ident)
        if 'composition' in original:
            dialog=NodeDialog(self,original['composition'],'編輯素材原型的本層內容')
            dialog.description.setText('修改素材庫原型的本層內容；已建立的當次副本保持原樣。子項可在 Canvas 調整後另存新素材。')
            if dialog.exec()==QDialog.DialogCode.Accepted:
                value=copy.deepcopy(original['composition']); value.update(dialog.values())
                original.update(name=value['name'],prompt=composition.render(value),composition=value)
                self.refresh_library(); self.refresh_builder(); self.changed()
            return
        dialog=ItemDialog(self,original)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            if original["module"]!=dialog.item["module"]: self.remove_selection(ident)
            self.state["items"]=[dialog.item if i["id"]==ident else i for i in self.state["items"]]
            self.refresh_library(); self.refresh_builder(); self.changed()

    def remove_selection(self,ident):
        for picks in [self.state["selections"]]+[w["picks"] for w in self.state["workspaces"]]:
            for mid in picks: picks[mid]=[i for i in picks[mid] if i!=ident]

    def delete_item(self,ident):
        if ask(self,"刪除提示詞","刪除此素材庫項目？相關模型與原圖不會刪除。"):
            self.remove_selection(ident); self.state["items"]=[i for i in self.state["items"] if i["id"]!=ident]
            self.refresh_library(); self.refresh_builder(); self.changed()

    def item_context(self,pos):
        widget=self.library.itemAt(pos)
        if not widget: return
        ident=widget.data(Qt.ItemDataRole.UserRole); item=next(i for i in self.state["items"] if i["id"]==ident)
        menu=QMenu(self); menu.addAction("複製此提示詞",lambda:self.copy_text(item["prompt"])); menu.addAction("加入／取消選擇",lambda:self.toggle_item(widget))
        menu.addSeparator(); menu.addAction('向上移動',lambda:self.move_library_item(ident,-1)); menu.addAction('向下移動',lambda:self.move_library_item(ident,1)); menu.addAction('移到最上方',lambda:self.move_library_item(ident,-10000))
        menu.addSeparator()
        menu.addAction("編輯提示詞與預覽圖",lambda:self.edit_item(ident)); menu.addSeparator(); menu.addAction("刪除項目…",lambda:self.delete_item(ident)); menu.open_at(self.library.viewport().mapToGlobal(pos))

    def accept_quick(self,_row):
        self.add_temporary(self.quick.toPlainText(),clear=True)

    def add_temporary(self,text,clear=False):
        text=text.strip().strip(",， ")
        if not text: self.notice("先輸入或選取要加入的提示詞。"); return
        self.state.setdefault("temporary",[]).append(text)
        if clear: self.quick.clear(); self.quick.completer.popup().hide()
        self.refresh_builder(); self.changed()
        self.notice("已加入臨時片段。"+ ("正在使用手動版本；清除手動內容後，會套用更新的組合。" if self.state["draft"] is not None else "可在右側組合中排序、複製或移除。"))

    def refresh_builder(self):
        if not hasattr(self,"selected"): return
        composition.prune_instances(self.state)
        if self.state.get('uses'): self.state.setdefault('prompt_layout','paragraphs')
        self.state["output_order"]=output_groups(self.state,include_hidden=True)
        self.reset_copy_feedback()
        collapsed={self.selected.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)[1] for i in range(self.selected.topLevelItemCount()) if not self.selected.topLevelItem(i).isExpanded()}
        self.selected.clear(); items={i["id"]:i for i in self.state["items"]}
        modules={m["id"]:m for m in self.state["modules"]}; count=0
        for mid in output_groups(self.state):
            if mid==TEMPORARY_GROUP:
                if not self.state["temporary"]: continue
                group=QTreeWidgetItem(["臨時片段"]); group.setData(0,Qt.ItemDataRole.UserRole,("group",TEMPORARY_GROUP)); self.selected.addTopLevelItem(group)
                for index,text in enumerate(self.state["temporary"]):
                    child=QTreeWidgetItem([text]); child.setToolTip(0,text); child.setData(0,Qt.ItemDataRole.UserRole,("temporary",index)); group.addChild(child); count+=1
                group.setExpanded(mid not in collapsed)
                continue
            if mid in self.state.get('uses',{}):
                root=self.state['uses'][mid]
                name=root['name']+(f" · ×{root['weight']/10:.1f}" if root['weight']!=10 else '')
                if not root['enabled']: name+=' · 停用'
                child=QTreeWidgetItem([name]); child.setData(0,Qt.ItemDataRole.UserRole,('use',mid))
                child.setToolTip(0,composition.render(root)); self.selected.addTopLevelItem(child); count+=1
                self.add_composition_children(child,root,mid); child.setExpanded(mid not in collapsed)
                continue
            module=modules[mid]; ids=self.state["selections"].get(mid,[])
            if not ids: continue
            group=QTreeWidgetItem([module["name"]]); group.setData(0,Qt.ItemDataRole.UserRole,("group",mid)); self.selected.addTopLevelItem(group)
            for ident in ids:
                weight=self.state.get('weights',{}).get(ident,10)
                root=composition.root_for(self.state,items[ident])
                name=root['name']+(f' · ×{weight/10:.1f}' if weight!=10 else '')
                if not root['enabled']: name+=' · 停用'
                elif composition.active_overlay(root): name+=' · 已覆蓋'
                child=QTreeWidgetItem([name]); child.setData(0,Qt.ItemDataRole.UserRole,("item",ident)); child.setToolTip(0,item_prompt(self.state,items[ident],composition.render(root))); group.addChild(child); count+=1
                self.add_composition_children(child,root,ident)
            group.setExpanded(mid not in collapsed)
        self.selection_count.setText(f"{count} 項")
        generated=build_prompt(self.state)
        _,affected=compose_details(self.state)
        affected_names={i['id']:i['name'] for i in self.state['items']}
        affected_names.update({k:v['name'] for k,v in self.state.get('uses',{}).items()})
        notices=[affected_names.get(key,'臨時片段')+'：'+', '.join(detail['tags']) for key,detail in affected.items()]
        self.conflict_summary='；'.join(notices)
        self.conflict_notice.setVisible(bool(notices))
        self.updating=True
        self.final.setPlainText(self.state["draft"] if self.state["draft"] is not None else generated)
        self.updating=False
        self.update_draft_status()
        self.canvas.refresh()

    def add_composition_children(self,parent,root,ident,inherited=False):
        active=composition.active_overlay(root)
        for part in root['children']+root['overlays']:
            overlay=part in root['overlays']
            muted=inherited or not root['enabled'] or not part['enabled'] or (part is not active if overlay else bool(active))
            name=part['name']+(' · 覆蓋中' if overlay and part is active and not muted else ' · 未生效' if muted else ' · 已覆蓋' if composition.active_overlay(part) else '')
            if part['weight']!=10: name+=f" · ×{part['weight']/10:.1f}"
            child=QTreeWidgetItem([name]); child.setData(0,Qt.ItemDataRole.UserRole,('node',(ident,part['id'])))
            child.setToolTip(0,part['prompt']); parent.addChild(child)
            self.add_composition_children(child,part,ident,muted); child.setExpanded(True)
        if root['children'] or root['overlays']: parent.setExpanded(True)

    def open_composition(self,item,*_):
        kind,ident=item.data(0,Qt.ItemDataRole.UserRole)
        if kind in ('item','use'): self.canvas.open_root(ident)
        elif kind=='node': self.canvas.focus_node(self.canvas_key(ident))

    def canvas_key(self,ident):
        root_id,node_id=ident; key=root_id+':'+node_id
        self.canvas.entries[key]=(root_id,node_id)
        return key

    def update_draft_status(self):
        if hasattr(self,'generation_panel'): self.generation_panel.refresh()
        draft=self.state["draft"] is not None
        self.conflict_notice.setText(('手動版本保留原文；自動組合將停用：' if draft else '已暫時停用衝突 Tag：')+getattr(self,'conflict_summary',''))
        has_canvas_output=any(key in self.state.get('uses',{}) for key in output_groups(self.state))
        text="正在使用手動版本" if draft else ""
        if draft and build_prompt(self.state)!=self.state.get("draft_base",""): text+=" · 清除後套用更新的組合"
        self.draft_status.setText(text); self.draft_status.setObjectName("Draft" if draft else "Subtle")
        self.draft_status.setVisible(draft)
        self.draft_status.style().unpolish(self.draft_status); self.draft_status.style().polish(self.draft_status)
        self.clear_draft.setEnabled(draft)
        self.selected.setEnabled(not draft)
        if self.draft_active!=draft:
            self.draft_fade.stop(); target=0.42 if draft else 1.0
            if self.draft_active is None: self.set_combination_opacity(target)
            else:
                self.draft_fade.setStartValue(self.selected.activity); self.draft_fade.setEndValue(target); self.draft_fade.start()
            self.draft_active=draft
        self.copy_button.setEnabled(bool(self.final.toPlainText().strip()))
        self.save_prompt.setEnabled(bool(self.final.toPlainText().strip()))
        if hasattr(self,'comfy'): self.run_controls.refresh()

    def set_combination_opacity(self,value):
        self.selected.activity=value; self.selected.viewport().update()
        for effect in self.selection_effects: effect.setOpacity(value)

    def final_edited(self):
        if self.updating: return
        self.reset_copy_feedback()
        from .drafts import edit
        edit(self.state,self.final.toPlainText()); self.update_draft_status(); self.changed()

    def regenerate(self):
        if self.state["draft"] is None: return
        if self.state["settings"].get("confirm_clear_draft",True):
            dialog=ClearDraftDialog(self)
            if dialog.exec()!=QDialog.DialogCode.Accepted: return
            if dialog.dont_ask_again.isChecked(): self.state["settings"]["confirm_clear_draft"]=False
        from .drafts import clear
        clear(self.state); self.refresh_builder(); self.changed()

    def builder_context(self,pos):
        if not self.selected.isEnabled(): return
        item=self.selected.itemAt(pos)
        if not item: return
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data: return
        self.selected.setCurrentItem(item)
        kind,ident=data; menu=QMenu(self)
        position=widget_global_position(self.selected.viewport(),pos,self.canvas.view)
        if kind=='use':
            self.canvas.context(ident,position,[ident]); return
        if kind=='node':
            key=self.canvas_key(ident); self.canvas.context(key,position,[key]); return
        menu.addAction("向上移動",lambda:self.selected.move_current(-1)); menu.addAction("向下移動",lambda:self.selected.move_current(1)); menu.addSeparator()
        if kind=="group":
            if ident==TEMPORARY_GROUP: menu.addAction("複製所有臨時片段",lambda:self.copy_text(", ".join(self.state["temporary"])))
            else: menu.addAction("複製本模組已選提示詞",lambda:self.copy_module(ident))
        elif kind=="item":
            menu.addAction('在 Canvas 編輯當次組合',lambda:self.canvas.open_root(ident))
            prompt=next(i["prompt"] for i in self.state["items"] if i["id"]==ident)
            menu.addAction("複製",lambda:self.copy_text(prompt)); menu.addAction("編輯",lambda:self.edit_item(ident))
            def remove():
                for mid,ids in self.state["selections"].items(): self.state["selections"][mid]=[i for i in ids if i!=ident]
                self.refresh_library(); self.refresh_builder(); self.changed()
            menu.addAction("取消選擇",remove)
        else:
            prompt=self.state["temporary"][ident]; menu.addAction("複製",lambda:self.copy_text(prompt)); menu.addAction("存入素材庫",lambda:self.new_item(prompt))
            def remove_temp():
                self.state["temporary"].pop(ident); self.refresh_builder(); self.changed()
            menu.addAction("移除此片段",remove_temp)
        menu.open_at(position)

    def reorder_builder(self,source,target,after=False):
        if source[0]==target[0]=='node':
            self.canvas.reorder(self.canvas_key(source[1]),self.canvas_key(target[1]),after); return
        try:
            if self.canvas_mode:
                moves=[]
                if not self.canvas.commit(lambda state:moves.append(reorder_output(state,tuple(source),tuple(target),after))): return
                moved=moves[0]
            else:
                moved=reorder_output(self.state,tuple(source),tuple(target),after)
                self.refresh_builder(); self.changed()
        except ValueError as exc: self.notice(str(exc)); return
        for n in range(self.selected.topLevelItemCount()):
            group=self.selected.topLevelItem(n)
            for item in [group]+[group.child(i) for i in range(group.childCount())]:
                if tuple(item.data(0,Qt.ItemDataRole.UserRole))==moved:
                    self.selected.setCurrentItem(item); self.selected.scrollToItem(item)
        self.notice("順序已更新。"+("正在使用手動版本；清除手動內容後套用。" if self.state["draft"] is not None else "最終 Prompt 已同步更新。"))

    def remove_builder_entry(self,key):
        kind,ident=key
        if kind=='use': self.canvas.remove(ident); return
        if kind=='node': self.canvas.remove(self.canvas_key(ident)); return
        if kind=="temporary": self.state["temporary"].pop(ident)
        elif kind=="item":
            for ids in self.state["selections"].values():
                if ident in ids: ids.remove(ident)
        self.refresh_library(); self.refresh_builder(); self.changed()

    def copy_text(self,text):
        if not text.strip(): self.notice("目前沒有可複製的提示詞。"); return False
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text); self.notice("已複製到剪貼簿。")
        return True

    def copy_final(self):
        if hasattr(self,'comfy') and self.comfy.can_run:
            count=1 if self.state.get('multi_output',{}).get('version',1)==4 else self.state['settings'].get('comfy_count',1)
            if direct_mode(self.state) or self.canvas_mode or self.recent.ensure_destination(): self.comfy.run(count)
            return
        if self.copy_text(self.final.toPlainText()):
            self.copy_button.setText("✓ 已複製"); self.copy_button.setProperty("feedback",True)
            self.copy_button.style().unpolish(self.copy_button); self.copy_button.style().polish(self.copy_button)
            self.copy_timer.start(1400)

    def reset_copy_feedback(self):
        self.copy_timer.stop(); self.copy_button.setText("複製完整 Prompt"); self.copy_button.setProperty("feedback",False)
        self.copy_button.style().unpolish(self.copy_button); self.copy_button.style().polish(self.copy_button)
        if hasattr(self,'run_controls'): self.run_controls.refresh()

    def toggle_modules(self): self.module_fold.toggle()
    def toggle_builder(self):
        self.builder_fold.toggle()
        self.builder_hint.setVisible(self.builder_fold.expanded)
        self.builder_toggle.setText("收合" if self.builder_fold.expanded else "展開組合")
    def focus_search(self):
        if self.canvas.isVisible(): self.canvas.palette(); return
        self.tabs.setCurrentIndex(0); self.quick.setFocus(); self.quick.selectAll()

    def page_changed(self,index):
        if index==0 and getattr(self,'canvas_mode',False): self.enter_canvas()
        page=self.tabs.widget(index)
        if page is self.gallery: self.gallery.refresh_albums()
        if page is self.recent: self.recent.refresh_destinations(); self.recent.request_refresh()

    def cancel_jobs(self):
        self.gallery.queue=[]
        if self.jobs.active: self.jobs.active.cancel.set(); self.notice("已要求取消，正在完成目前檔案的收尾。")

    def data_menu(self):
        self.settings('data')

    def backup_zip(self):
        if self.jobs.active or self.gallery.importing: self.notice("請先等待背景工作完成。"); return
        path=QFileDialog.getSaveFileName(self,"備份資料（不含連結的外部原圖與模型檔）","prompt-studio-backup.zip","ZIP (*.zip)")[0]
        if not path: return
        try:
            self.models.save(); self.persist(); snapshot=self.store.backup()
            from .backup import archive_data
            self.jobs.start("正在備份資料庫、縮圖與應用內的圖片副本…",lambda cancel:archive_data(self.store.directory,snapshot,path,cancel),lambda _:self.notice("ZIP 備份完成；外部連結的原圖與模型需另外備份。"))
        except Exception as exc: self.error(str(exc))

    def export_json(self):
        path=QFileDialog.getSaveFileName(self,"匯出資料索引","prompt-studio.json","JSON (*.json)")[0]
        if not path: return
        try:
            self.models.save(); self.persist()
            # Stream metadata rather than keeping the whole image catalog in RAM.
            with Path(path).open("w",encoding="utf-8") as output:
                output.write('{"format":"prompt-studio","version":1,"state":')
                json.dump(self.state,output,ensure_ascii=False)
                output.write(',"resources":[')
                for index,r in enumerate(self.store.db.execute("SELECT * FROM resources")):
                    if index: output.write(",")
                    json.dump(dict(id=r[0],kind=r[1],parent=r[2],name=r[3],body=json.loads(r[4])),output,ensure_ascii=False)
                output.write("]}")
            self.notice("JSON 已匯出；圖片檔案不內嵌其中，完整備份請另存 data 資料夾與原圖。")
        except Exception as exc: self.error(str(exc))

    def import_json(self):
        if self.jobs.active or self.settings_page.civitai.browse_jobs.active or self.gallery.importing or self.recent.collecting: self.notice("請先等待背景工作完成。"); return
        path=QFileDialog.getOpenFileName(self,"匯入資料索引","","JSON (*.json)")[0]
        if not path: return
        try:
            if Path(path).stat().st_size>64*1024*1024: raise ValueError("JSON 超過 64 MB，請改用資料庫備份還原。")
            bundle=json.loads(Path(path).read_text(encoding="utf-8-sig"))
            state=validate_state(bundle["state"] if bundle.get("format")=="prompt-studio" else bundle)
            if 'multi_output' in state and 'multi_output' not in self.state:
                raise ValueError('這是多畫布資料，請在 v0.8 Alpha 程式中匯入。')
            from .state_loading import prepare_state
            state=prepare_state(state,multi='multi_output' in self.state)
            resources=bundle.get("resources",[]) if bundle.get("format")=="prompt-studio" else []
            from .validation import validate_resources
            validate_resources(resources)
            if not ask(self,"匯入資料",f"以備份取代目前的素材與檔案索引？\n共 {len(state['items'])} 個提示詞、{len(resources)} 筆模型／圖片資料。\n\n會先建立 SQLite 備份；不複製、搬移或刪除外部原檔。"): return
            self.save_timer.stop(); self.models.save(); self.changes.stop(); backup=self.store.backup()
            self.completion.serial+=1; self.completion.timer.stop()
            with self.store.db:
                self.store.db.execute("DELETE FROM resources")
                for r in resources: self.store.db.execute("INSERT INTO resources VALUES (?,?,?,?,?)",(r["id"],r["kind"],r["parent"],r["name"],json.dumps(r["body"],ensure_ascii=False)))
                self.store.db.execute("INSERT OR REPLACE INTO document VALUES (1,?)",(json.dumps(state,ensure_ascii=False),))
            self.state=self.store.load(); self.current_module=self.state["modules"][0]["id"] if self.state["modules"] else None
            # JSON replaces the document, not one undoable Canvas edit. Old
            # histories/editors contain owner IDs from the previous document.
            self.canvas.undo_stack.clear(); self.canvas.redo_stack.clear(); self.canvas.last_state=None
            self.workspace_histories={}; self.canvas.root_id=None; self.canvas.path=[]
            editor=getattr(self.canvas,'editor_page',None)
            if editor is not None:
                if editor.animation:editor.animation.stop()
                self.canvas.editor_page=None;self.surface_stack.removeWidget(editor);editor.deleteLater()
            self.models.record=None; self.models.root.setText(self.state["settings"]["model_root"])
            self.models.loading=True; self.models.category.clear(); self.models.category.addItems(self.state["settings"]["model_categories"]); self.models.loading=False
            self.models.refresh_categories(); self.models.refresh()
            self.settings_page.reload_state()
            self.comfy.disconnect()
            self.recent.pending.clear(); self.recent.saves.clear(); self.recent.record=None
            self.recent.known={r[0] for r in self.catalog.db.execute("SELECT id FROM resources WHERE kind='recent'")}
            self.recent.refresh_destinations(); self.recent.refresh()
            self.gallery.album=None; self.gallery.refresh_albums(); self.refresh_workspaces(); self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.apply_theme()
            self.notice(f"已匯入；匯入前的資料庫備份：{backup.name}。如需生圖，請重新連接 ComfyUI。")
        except Exception as exc: self.error(str(exc))

    def closeEvent(self,event):
        if hasattr(self,'settings_page'):
            self.settings_page.civitai.stop_images()
            if self.settings_page.preferences is not None:self.settings_page.flush()
        browsing=hasattr(self,'settings_page') and self.settings_page.civitai.browse_jobs.active
        if self.jobs.active or browsing or self.completion.tasks or self.gallery.importing or (hasattr(self,'recent') and self.recent.collecting):
            event.ignore(); self.closing=True; self.gallery.queue=[]
            if self.jobs.active: self.jobs.active.cancel.set()
            if browsing:browsing.cancel.set()
            self.completion.serial+=1; self.completion.timer.stop()
            self.notice("正在收尾背景工作，完成後關閉。")
            QTimer.singleShot(300,self.close); return
        try: self.models.save()
        except Exception as exc:
            self.error(str(exc)); event.ignore(); return
        self.save_timer.stop()
        if not self.persist(): event.ignore(); return
        self.comfy.shutdown(); self.recent.refresh_timer.stop(); self.display_recovery.stop()
        self.draft_fade.stop(); self.module_fold.animation.stop(); self.builder_fold.animation.stop()
        from PySide6.QtWidgets import QApplication
        QApplication.instance().removeEventFilter(self.host_shell)
        self.canvas.release_output(); self.canvas.output=None; self.canvas.cards={}; self.canvas.view.scene().clear()
        self.changes.stop(); self.store.close(); event.accept()
