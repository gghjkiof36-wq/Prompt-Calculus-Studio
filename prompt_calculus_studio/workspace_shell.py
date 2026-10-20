"""Single-pane local documents, using the established PCS editing surfaces."""
from dataclasses import dataclass
from pathlib import Path
import uuid
import json

from PySide6.QtCore import Qt, QSize, QUrl, QTimer, QSaveFile, QIODevice, QEvent
from PySide6.QtGui import QShortcut, QKeySequence, QDesktopServices, QColor
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QPlainTextEdit, QListWidget, QListWidgetItem, QMessageBox,
    QDialog, QFileDialog, QSplitter)

from .core import Storage, initial_state, DEFAULT_SETTINGS
from .document_host import DesktopDocument, EmbeddedHost
from .document_tabs import DocumentTabs
from .local_documents import paths
from .theme import stylesheet, visual_tokens, widget_palette, apply_backdrop
from .ui_icons import icon
from .widgets import button, label, RoundMenu, WindowShell
from .workspace_home import WorkspaceHome
from .workspace_files import MarkdownPage
from .workspace_locations import WorkspaceLocations, path_key, remembered_root
from .workspace_commands import WorkspaceCommands, PATH_ROLE, KIND_ROLE, ROOT_ROLE
from .workspace_style import sidebar_style, workspace_colors
from .workspace_sidebar import WorkspaceSidebarResize, WorkspaceTree
from .workspace_surface import WorkspaceSurface
from .workspace_destinations import WorkspaceNavigation
from .workspace_panes import WorkspacePanes


DISPLAY_KEYS = ('font_family', 'ui_size', 'prompt_size', 'material',
                'mica_transparency', 'acrylic_transparency', 'clear_transparency', 'menu_transparency',
                'visual_palette', 'accent', 'density', 'reduce_motion')
MATERIAL_KEYS = frozenset(('material', 'mica_transparency', 'acrylic_transparency', 'clear_transparency',
                           'menu_transparency'))


@dataclass
class Tab:
    key: str
    title: str
    widget: QWidget
    retained: bool = False
    document: DesktopDocument | None = None
    path: Path | None = None
    route: str = ''
    slot: str = ''
    pane: str = ''
    host: EmbeddedHost | None = None

    @property
    def dirty(self):
        return bool(self.document and (self.document.dirty or self.document.draft_dirty)
                    or isinstance(self.widget, MarkdownPage) and self.widget.dirty)


class WorkspaceTop(QFrame):
    def contextMenuEvent(self, event):
        from .window_menu import open_window_menu
        if open_window_menu(self.window(), event.globalPos()):
            event.accept()
        else:
            super().contextMenuEvent(event)

    def paintEvent(self, event):
        super().paintEvent(event)
        for strip in self.findChildren(DocumentTabs):
            if strip.isVisible():
                strip.paint_outer_feet(self)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.window().windowHandle():
            self.window().windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            window = self.window()
            window.showNormal() if window.isMaximized() else window.showMaximized()
        super().mouseDoubleClickEvent(event)


class WorkspaceShell(WorkspacePanes, QMainWindow):
    def __init__(self, root, data):
        super().__init__()
        # Choose the texture-capable backing store before the first native
        # window is created. Lazy WebEngine readers can then join the ordinary
        # widget hierarchy without recreating the shell or native child layers.
        # This empty, hidden widget loads no QML, document or browser page.
        from PySide6.QtQuickWidgets import QQuickWidget
        self._compositor = QQuickWidget(self)
        self._compositor.setFixedSize(0, 0)
        self._compositor.hide()
        from .tooltips import install_tooltip_styling
        install_tooltip_styling()
        self.data = Path(data).absolute()
        self.root = remembered_root(root, (self.data/'workspace-layout.json').is_file())
        self.data.mkdir(parents=True, exist_ok=True)
        self.session = self.data / 'sessions' / uuid.uuid4().hex
        self.session.mkdir(parents=True)
        self.tabs_by_key = {}
        self.saved_pages = {}
        self.utility = None
        self.history = []
        self.history_at = -1
        self.traversing = False
        self.active_key = None
        self.active_route = 'home'
        self.sidebar_preference = None
        self.sidebar_auto_hide = True
        self.sidebar_open_delay = 450
        self.sidebar_close_delay = 650
        self.confirm_trash = True
        self.sidebar_width = 264
        self.show_empty_folders = True
        self.navigation_position = 'side'
        self.locations = WorkspaceLocations(self.root, self.data, self.read_navigation_json('linked-folders.json'))
        self.commands = WorkspaceCommands(self)
        from .workspace_autosave import WorkspaceAutosave
        self.autosave = WorkspaceAutosave(self)
        self.recent_files = self.locations.recent(self.read_navigation_json('recent-files.json'))
        self.recent_timer = QTimer(self)
        self.recent_timer.setSingleShot(True)
        self.recent_timer.timeout.connect(self.save_recent)
        self.default_settings = dict(DEFAULT_SETTINGS, ui_size=12, online=False, comfy_enabled=False)
        self.settings = dict(self.default_settings)
        self.load_ui_preferences()
        self.appearance_preview = {}
        self.native_material = False
        self._material_ready = False
        self.material_refresh = QTimer(self)
        self.material_refresh.setSingleShot(True)
        self.material_refresh.timeout.connect(self.finish_material_show)
        # Chrome has application appearance only; document state always stays
        # with its own host and is never swapped into the shell.
        self.state = {'settings': self.settings}
        from .workspace_connection import WorkspaceConnection
        self.comfy_connection = WorkspaceConnection(self)
        from .workspace_runtime import WorkspaceRuntime
        from .workspace_scheduler import WorkspaceScheduler
        self.runtimes = WorkspaceRuntime(self)
        self.scheduler = WorkspaceScheduler(self)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        from .releases import window_title
        self.setWindowTitle(window_title())
        self.resize(1440, 900)
        self.setMinimumSize(720, 480)
        self.setPalette(widget_palette(self.settings))
        self.build()
        self.init_panes()
        from .workspace_materials import WorkspaceMaterials
        self.material_transfer = WorkspaceMaterials(self)
        self.arrange_navigation()
        from .window_chrome import install_expanded_chrome
        self.window_chrome = install_expanded_chrome(self)
        if self.window_chrome:
            self.caption_inset(self.window_chrome.caption_right_inset)
            self.window_chrome.insetChanged.connect(self.caption_inset)
        self.style_shell()
        self.sidebar_resize = WorkspaceSidebarResize(self)
        from .workspace_sidebar_peek import WorkspaceSidebarPeek
        self.sidebar_peek = WorkspaceSidebarPeek(self)
        self.refresh_tree()
        self.new_tab()
        from .workspace_pointer_navigation import WorkspacePointerNavigation
        self.pointer_navigation = WorkspacePointerNavigation(self)
        from .workspace_layout import WorkspaceLayout
        self.layout_store = WorkspaceLayout(self)
        self.layout_restored = self.layout_store.restore()
        from .notebook_manager import NotebookManager
        self.notebook = NotebookManager(self)
        for sequence, callback in (('Ctrl+T', self.new_tab), ('Ctrl+W', self.close_current),
                                   ('Ctrl+O', self.choose_file),
                                   ('Ctrl+S', self.save_current), ('Alt+Left', lambda: self.go(-1)),
                                   ('Ctrl+Shift+S', self.commands.files.save_as_current),
                                   ('Alt+Right', lambda: self.go(1))):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)
        if self.comfy_connection.enabled:
            QTimer.singleShot(0, self.ensure_utility)
        self.runtimes.recover()
        self.scheduler.ready = True

    def read_navigation_json(self, name):
        try:
            return json.loads((self.data/name).read_text(encoding='utf-8'))
        except (ValueError, OSError):
            return None

    def tool(self, name, title, callback):
        control = button('', callback, 'Quiet')
        control.setIcon(icon(name, 'navigation'))
        control.setIconSize(QSize(16, 16))
        control.setFixedSize(38, 36)
        control.setToolTip(title)
        control.setAccessibleName(title)
        return control

    def build(self):
        surface = WindowShell(self)
        surface.setObjectName('DocumentWorkspace')
        self.setCentralWidget(surface)
        root_layout = QVBoxLayout(surface)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        # Global navigation and document tabs share one continuous window frame.
        self.top = WorkspaceTop()
        self.top.setObjectName('DocumentTabStrip')
        top = QHBoxLayout(self.top)
        top.setContentsMargins(7, 4, 10, 0)
        top.setSpacing(0)
        self.collapse = self.tool('panel-left', '收合側欄', self.toggle_sidebar)
        self.back = self.tool('back', '上一頁 (Alt+Left)', lambda: self.go(-1))
        self.forward = self.tool('forward', '下一頁 (Alt+Right)', lambda: self.go(1))
        self.nav_controls = QWidget()
        controls = QHBoxLayout(self.nav_controls)
        controls.setContentsMargins(0, 0, 0, 4)
        controls.setSpacing(4)
        for control in (self.collapse, self.back, self.forward):
            controls.addWidget(control)
        top.addWidget(self.nav_controls)
        top.addSpacing(12)
        self.tab_strip = DocumentTabs()
        self.tab_strip.newRequested.connect(self.new_tab)
        self.tab_strip.bar.currentChanged.connect(self.activate)
        self.tab_strip.bar.tabBarClicked.connect(self.activate)
        self.tab_strip.bar.tabCloseRequested.connect(self.close_tab)
        self.tab_strip.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_strip.bar.customContextMenuRequested.connect(self.tab_menu)
        top.addWidget(self.tab_strip, 1)
        root_layout.addWidget(self.top)
        self.tab_row = WorkspaceTop()
        self.tab_row.setObjectName('DocumentTabStrip')
        tabs = QHBoxLayout(self.tab_row)
        tabs.setContentsMargins(16, 0, 16, 0)
        tabs.setSpacing(0)
        self.tab_row.hide()

        from .connection_pill import ConnectionPill
        self.connection = ConnectionPill(self.request_connection)
        self.connection_dot = self.connection.dot
        self.connection_label = self.connection.brand
        self.refresh_connection()
        top.addSpacing(10)
        top.addWidget(self.connection)

        from PySide6.QtWidgets import QGridLayout
        workspace = QGridLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        self.workspace_layout = workspace
        root_layout.addLayout(workspace, 1)
        self.rail = WorkspaceNavigation(self)
        self.routes = self.rail.routes
        workspace.addWidget(self.rail, 0, 0, 2, 1)
        workspace.addWidget(self.tab_row, 0, 1)
        self.workspace_stack = WorkspaceSurface(self)
        workspace.addWidget(self.workspace_stack, 1, 1)
        workspace.setRowStretch(1, 1)
        workspace.setColumnStretch(1, 1)
        from .workspace_notice import WorkspaceNotice
        self.notification = WorkspaceNotice(self.workspace_stack)
        self.home_split = QSplitter(Qt.Orientation.Horizontal)
        self.home_split.setObjectName('DocumentSplit')
        self.home_split.setChildrenCollapsible(False)
        self.home_split.setHandleWidth(0)
        self.workspace_stack.addWidget(self.home_split)
        self.sidebar = QFrame()
        self.sidebar.setObjectName('DocumentSidebar')
        self.sidebar.setProperty('pcsWorkspaceSidebar', True)
        self.sidebar.setFixedWidth(self.sidebar_width)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 16, 8, 10)
        side.setSpacing(12)
        self.tree_heading = QFrame()
        self.tree_heading.setObjectName('WorkspaceTreeHeader')
        self._tree_header_docked = False
        heading = QHBoxLayout(self.tree_heading)
        heading.setContentsMargins(0, 0, 0, 0)
        heading.setSpacing(0)
        heading.addWidget(label('內容', 'Heading'))
        heading.addStretch()
        self.tree_new = self.tool('plus', '新增內容', self.commands.creation_menu)
        self.tree_folder = self.tool('folder', '新增資料夾',
                                     lambda: self.commands.new('folder', self.commands.selected_context()))
        self.tree_more = self.tool('more-horizontal', '內容選單', self.root_menu)
        self.tree_fold = self.tool('unfold-all', '全部展開', lambda: self.tree_state.toggle_all())
        for control in (self.tree_new, self.tree_folder, self.tree_fold, self.tree_more):
            control.setFixedSize(32, 32)
            heading.addWidget(control)
        side.addWidget(self.tree_heading)
        from .workspace_headers import SidebarHeaders
        self.sidebar_headers = SidebarHeaders(self)
        self.sidebar_headers.register(self.sidebar, self.tree_heading)
        self.tree = WorkspaceTree(self)
        self.tree.setObjectName('DocumentTree')
        self.tree.setColumnCount(1)
        self.tree.setIconSize(QSize(15, 15))
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(15)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setStretchLastSection(True)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.itemClicked.connect(lambda item, col: self.open_item(item))
        self.tree.itemDoubleClicked.connect(lambda item, col: self.open_item(item, retained=True))
        self.tree.itemActivated.connect(lambda item, col: self.open_item(item, retained=True))
        self.tree.currentItemChanged.connect(self.navigation_selection_changed)
        from .workspace_file_drag import WorkspaceFileDrag
        self.tree.file_drag = WorkspaceFileDrag(self.tree)
        from .workspace_tree_state import WorkspaceTreeState
        self.tree_state = WorkspaceTreeState(self)
        side.addWidget(self.tree, 1)
        self.home_split.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.pages.setObjectName('DocumentPages')
        self.home_split.addWidget(self.pages)
        self.home_split.setStretchFactor(0, 0)
        self.home_split.setStretchFactor(1, 1)
        self.home_split.setSizes([260, 1100])

    def schedule_material_show(self):
        # Qt can recreate/reset the native surface while showing its first
        # backing store. Until that finishes, paint a solid frame, not an
        # accidental unblurred surface for a saved Mica/Acrylic preference.
        self._material_ready = False
        self._material_key = None
        self.native_material = False
        if self.centralWidget():self.centralWidget().update()
        if self.isVisible():self.material_refresh.start(0)

    def finish_material_show(self):
        if self.isVisible():
            self._material_ready = True
            self.update_material()

    def showEvent(self,event):
        super().showEvent(event)
        self.schedule_material_show()

    def event(self,event):
        result = super().event(event)
        if event.type() == QEvent.Type.WinIdChange and hasattr(self,'material_refresh'):
            self.schedule_material_show()
        return result

    def update_material(self):
        """Preview the painted tint without repolishing the widget tree."""
        appearance = self.display_settings()
        if not self._material_ready or not self.isVisible():return
        tokens = visual_tokens(appearance)
        material_key = (int(self.winId()), appearance['material'], tokens['scheme'], tokens['line'])
        if material_key != getattr(self, '_material_key', None):
            self.native_material = apply_backdrop(self, appearance['material'], appearance)
            self._material_key = material_key
        self.centralWidget().update()

    def style_shell(self):
        appearance = self.display_settings()
        self.update_material()
        t = visual_tokens(appearance)
        palette = widget_palette(appearance)
        self.pane_split.seam_color = QColor(t['text'])
        if self.palette() != palette:self.setPalette(palette)
        if QApplication.instance().palette() != palette:QApplication.instance().setPalette(palette)
        for pane in self.panes:
            self.style_tab_strip(pane.strip, appearance)
        self.tree.row_colors = dict(t, **workspace_colors(appearance))
        sheet = stylesheet(appearance) + f'''
            QFrame#DocumentWorkspace, QFrame#DocumentRail {{ background:transparent; border:0; }}
            QWidget#DocumentPane, QStackedWidget#DocumentPages {{ background:{t['base']}; }}
            QFrame#DocumentSidebar {{ background:{t['sidebar']}; border:0; }}
            QSplitter#DocumentSplit::handle {{ background:{t['sidebar']}; }}
            QSplitter#DocumentSplit::handle:hover {{ background:{t['selected']}; }}
            QFrame#DocumentTabStrip {{ background:transparent; border:0; }}
            QFrame#DocumentRail QPushButton:checked {{ background:{t['selected']}; color:{t['text']}; }}
            QFrame#DocumentRail QPushButton {{ padding:0; }}
            QTreeWidget#DocumentTree {{ background:transparent; border:0; outline:0; }}
            QTreeWidget#DocumentTree::item {{ height:32px; padding:0 5px; border-radius:4px; }}
            QTreeWidget#DocumentTree::item:selected {{ background:{t['selected']}; color:{t['text']}; }}
            QTreeWidget#DocumentTree::item:hover {{ background:{t['hover']}; }}
            QTabBar#DocumentTabBar {{ background:transparent; border:0; }}
            QTabBar#DocumentTabBar::tab {{ background:transparent; color:{t['secondary']}; border:0; border-radius:0; padding:8px 12px; margin:0; }}
            QTabBar#DocumentTabBar::tab:selected {{ background:{t['base']}; color:{t['text']}; border-top-left-radius:9px; border-top-right-radius:9px; }}
            QTabBar#DocumentTabBar::tab:selected:hover {{ background:{t['base']}; }}
            QTabBar#DocumentTabBar::tab:!selected:hover {{ background:transparent; }}
            QTabBar#DocumentTabBar QToolButton {{ padding:0; border:0; border-radius:4px; background:{t['chrome']}; }}
            QTabBar#DocumentTabBar QToolButton:hover {{ background:{t['raised']}; }}
        ''' + sidebar_style(appearance)
        if self.styleSheet() != sheet:self.setStyleSheet(sheet)
        peek=getattr(self,'sidebar_peek',None)
        if peek and peek.is_open:peek._apply_surface()
        if hasattr(self,'notebook'):self.notebook.style()
        self.refresh_connection()
        self.sync_navigation_density()
        self.workspace_stack.sync_surface()
        for tab in self.all_pages():
            if isinstance(tab.widget, WorkspaceHome):
                tab.widget.apply_appearance(appearance)
            elif tab.document:
                tab.host.apply_theme(preserve_layout=True)

    def style_tab_strip(self, strip, appearance=None):
        appearance = appearance or self.display_settings()
        t = visual_tokens(appearance)
        colors = workspace_colors(appearance)
        strip.bar.active_fill = QColor(colors['content'])
        strip.bar.sidebar_fill = QColor(colors['sidebar'])
        strip.bar.hover_fill = QColor(t['raised'])
        strip.bar.close_fill = QColor(t['selected'])
        strip.bar.scroll_fill = QColor(t['chrome'])
        strip.bar.active_ink = QColor(t['text'])
        strip.bar.idle_ink = QColor(t['secondary'])

    def load_ui_preferences(self):
        try:
            with (self.data/'workspace-ui.json').open('rb') as stream:
                raw = stream.read(16385)
            if len(raw)>16384:
                return
            saved = json.loads(raw)
            if not isinstance(saved, dict):
                return
            for key in DISPLAY_KEYS:
                value = saved.get(key)
                if key=='font_family' and isinstance(value,str) and 0<len(value)<=120:
                    self.settings[key]=value
                elif key=='material' and value in ('solid','mica','acrylic','clear'):
                    self.settings[key]=value
                elif key=='visual_palette' and value in ('graphite','mist','paper'):
                    self.settings[key]=value
                elif key=='accent' and value in ('neutral','blue','green'):
                    self.settings[key]=value
                elif key=='density' and value in ('comfortable','compact'):
                    self.settings[key]=value
                elif key=='reduce_motion' and type(value) is bool:
                    self.settings[key]=value
                elif key in ('ui_size','prompt_size') and type(value) is int and 9<=value<=22:
                    self.settings[key]=value
                elif key.endswith('_transparency') and type(value) is int and 0<=value<=100:
                    self.settings[key]=value
            width=saved.get('sidebar_width')
            if type(width) is int and 220<=width<=480:
                self.sidebar_width=width
            expanded=saved.get('sidebar_expanded')
            if type(expanded) is bool:
                self.sidebar_preference=expanded
            if type(saved.get('sidebar_auto_hide')) is bool:
                self.sidebar_auto_hide=saved['sidebar_auto_hide']
            for key,maximum in (('sidebar_open_delay',1200),('sidebar_close_delay',2000)):
                value=saved.get(key)
                if type(value) is int and 100<=value<=maximum:setattr(self,key,value)
            position=saved.get('navigation_position')
            if position in ('side','top'):
                self.navigation_position=position
            if type(saved.get('show_empty_folders')) is bool:
                self.show_empty_folders = saved['show_empty_folders']
            if type(saved.get('confirm_trash')) is bool:
                self.confirm_trash = saved['confirm_trash']
        except (ValueError,OSError):
            pass

    def display_settings(self):
        return {key:self.appearance_preview.get(key,self.settings[key]) for key in DISPLAY_KEYS}

    def save_ui_preferences(self):
        saved = {key:self.settings[key] for key in DISPLAY_KEYS}
        saved.update(sidebar_width=self.sidebar_width, sidebar_expanded=self.sidebar_preference,
                     sidebar_auto_hide=self.sidebar_auto_hide, sidebar_open_delay=self.sidebar_open_delay,
                     sidebar_close_delay=self.sidebar_close_delay,
                     navigation_position=self.navigation_position, show_empty_folders=self.show_empty_folders,
                     confirm_trash=self.confirm_trash)
        output=QSaveFile(str(self.data/'workspace-ui.json'))
        output.setDirectWriteFallback(False)
        if output.open(QIODevice.OpenModeFlag.WriteOnly):
            output.write(json.dumps(saved,ensure_ascii=False).encode('utf-8'))
            output.commit()

    def set_trash_confirmation(self, enabled):
        self.confirm_trash = bool(enabled)
        self.save_ui_preferences()
        from .deferred_page import page_ready
        page = getattr(self.utility, 'settings_page', None)
        preferences = getattr(page, 'preferences', None) if page_ready(page) else None
        checkbox = getattr(preferences, 'confirm_trash', None)
        if checkbox is not None and checkbox.isChecked() != self.confirm_trash:
            checkbox.setChecked(self.confirm_trash)

    def sync_display_settings(self, settings, preview=None):
        previous = self.display_settings()
        changed=any(self.settings[key]!=settings[key] for key in DISPLAY_KEYS)
        self.settings.update({key:settings[key] for key in DISPLAY_KEYS})
        overlay={key:value for key,value in (preview or {}).items() if key in DISPLAY_KEYS}
        self.appearance_preview=overlay
        appearance = self.display_settings()
        differences = {key for key in DISPLAY_KEYS if previous[key] != appearance[key]}
        if differences:
            # Opacity affects the frame/popups only. Reapplying inherited QSS
            # here blocks every slider step on hundreds of hidden controls.
            if differences <= MATERIAL_KEYS:self.update_material()
            else:self.style_shell()
        if changed:
            self.save_ui_preferences()

    def sidebar_target(self):
        if self.active_route=='home':
            return self.sidebar
        return self.utility.context_sidebar_target() if self.utility else None

    def set_navigation_position(self, position):
        if position not in ('side', 'top') or position == self.navigation_position:
            return
        self.navigation_position = position
        from .deferred_page import page_ready
        page = getattr(self.utility, 'settings_page', None)
        selector = getattr(page, 'navigation_position', None) if page_ready(page) else None
        if selector is not None:
            selector.blockSignals(True)
            selector.setCurrentIndex(selector.findData(position))
            selector.blockSignals(False)
        self.arrange_navigation()
        self.sync_sidebar()
        self.save_ui_preferences()

    def arrange_navigation(self):
        horizontal = self.navigation_position == 'top'
        split = len(self.panes) == 2
        self.tabs_in_row = True
        top = self.top.layout()
        top.removeWidget(self.pane_tab_row)
        self.tab_row.layout().removeWidget(self.pane_tab_row)
        self.pane_tab_row.hide()
        top.removeWidget(self.rail)
        top.removeWidget(self.nav_controls)
        top.removeWidget(self.connection)
        self.workspace_layout.removeWidget(self.rail)
        for pane in self.panes:
            pane.strip.pane_menu_always = split
            pane.strip.overflow.setToolTip('分頁選單' if split else '所有分頁')
            pane.strip.overflow.setAccessibleName(pane.strip.overflow.toolTip())
            top.removeWidget(pane.strip)
            self.tab_row.layout().removeWidget(pane.strip)
            pane.container.layout().removeWidget(pane.strip)
            pane.strip.set_tab_height(36 if horizontal else 40)
        self.rail.arrange(self.navigation_position)
        top.insertWidget(0, self.nav_controls)
        if not hasattr(self, 'chrome_space'):
            self.chrome_space = QWidget()
        top.removeWidget(self.chrome_space)
        self.chrome_space.hide()
        if horizontal:
            top.insertWidget(2, self.rail, 1)
        else:
            self.workspace_layout.addWidget(self.rail, 0, 0, 2, 1)
            top.insertWidget(2, self.chrome_space, 1)
            self.chrome_space.show()
        top.addWidget(self.connection)
        if split:
            for pane in self.panes:
                pane.strip.setParent(self.pane_tab_row)
                pane.strip.set_tab_height(36)
            self.tab_row.layout().addWidget(self.pane_tab_row, 1)
            self.pane_tab_row.show()
        else:
            self.panes[0].strip.set_tab_height(36)
            self.tab_row.layout().addWidget(self.panes[0].strip, 1)
        self.tab_row.setVisible(self.tabs_in_row)
        self.rail.show()
        self.nav_controls.show()
        self.connection.show()
        for pane in self.panes:pane.strip.show()
        self.sync_navigation_density()
        QTimer.singleShot(0, self.pane_tab_row.sync)

    def sync_navigation_density(self):
        horizontal = self.navigation_position == 'top'
        margins = self.top.layout().contentsMargins()
        self.nav_controls.layout().setContentsMargins(0, 0, 0, 0)
        available = (self.width()-margins.left()-margins.right()
                     -self.nav_controls.sizeHint().width()-self.connection.width()-22)
        self.rail.adapt_width(available)

    def sync_tab_inset(self):
        if self.tabs_in_row:
            target = self.sidebar_target()
            sidebar = target.width() if target is not None and target.isVisible() and not target.property('pcsSidebarBorrowed') else 0
            inset = sidebar if sidebar or self.navigation_position == 'side' or self.active_route == 'home' else 16
            if self.tab_row.layout().contentsMargins().left() != inset:
                self.tab_row.layout().setContentsMargins(inset, 0, 16, 0)
                self.tab_row.layout().activate()
                self.tab_strip.fit()
        self.sync_tree_header()
        self.pane_tab_row.sync()

    def sync_tree_header(self):
        self.sidebar_headers.sync()

    def caption_inset(self, inset):
        self.top.layout().setContentsMargins(7, 4, 10+inset, 0)
        self.sync_navigation_density()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'connection'):
            self.refresh_connection()
            self.sync_navigation_density()
            self.sync_sidebar()

    def refresh_tree(self):
        if self.commands.files.inline is not None:
            self.commands.files.inline.cancel()
            return
        old_items = getattr(self, 'path_items', {})
        expanded = self.tree_state.expanded
        selected_path = self.tree_state.selected
        scroll = self.tree_state.scroll
        self.tree_state.restoring = True
        self.tree.blockSignals(True)
        self.tree.clear()
        self.file_items = {}
        self.path_items = {}
        self.entries = []
        types = {'category': '分類', 'workflow': '工作流', 'markdown': '筆記'}
        for root in self.locations.roots:
            root_item = QTreeWidgetItem([root.name])
            self.set_tree_location(root_item, root, 'root', root)
            root_item.setIcon(0, icon('folder', 'navigation'))
            self.tree.addTopLevelItem(root_item)
            folders = {path_key(root): root_item}
            try:
                inventory = list(self.locations.inventory(root))
                occupied = {parent for path, kind in inventory if kind != 'folder' for parent in path.parents}
                for path, kind in inventory:
                    if (kind == 'folder' and not self.show_empty_folders and path not in occupied
                            and not any(path == p or path in p.parents for p in self.tree_state.revealed)):
                        continue
                    item = QTreeWidgetItem([path.name if kind == 'folder' else path.stem])
                    self.set_tree_location(item, path, kind, root)
                    folders[path_key(path.parent)].addChild(item)
                    if kind == 'folder':
                        item.setIcon(0, icon('folder', 'navigation'))
                        folders[path_key(path)] = item
                        item.setExpanded(path_key(path) in expanded)
                    else:
                        item.setData(0, Qt.ItemDataRole.UserRole, str(path))
                        item.setToolTip(0, types[kind] + ' · ' + str(path))
                        self.entries.append((path, kind))
                        self.file_items[str(path)] = item
                        if path.is_relative_to(self.root):
                            self.file_items[path.relative_to(self.root).as_posix()] = item
            except (OSError, ValueError) as error:
                root_item.setToolTip(0, str(root) + '\n無法完整讀取：' + str(error))
                self.show_notice('無法完整讀取資料夾：' + str(root))
            root_item.setExpanded((not old_items and not self.tree_state.loaded) or path_key(root) in expanded)
            if root_item.isExpanded():
                expanded.add(path_key(root))
        from PySide6.QtCore import QSignalBlocker
        # Restoring a hidden selection must not reopen its collapsed ancestors.
        with QSignalBlocker(self.tree.selectionModel()):
            self.tree.setCurrentItem(self.path_items.get(path_key(selected_path)) if selected_path else None)
        self.tree.blockSignals(False)
        self.tree_state.restoring = False
        self.tree_state.loaded = True
        self.tree_state.rebuild_branches()
        self.tree.doItemsLayout()
        self.tree_state.restore_scroll(scroll)
        for tab in self.all_pages():
            if isinstance(tab.widget, WorkspaceHome):
                tab.widget.set_recent(self.recent_files)

    def set_tree_location(self, item, path, kind, root):
        item.setData(0, PATH_ROLE, str(path))
        item.setData(0, KIND_ROLE, kind)
        item.setData(0, ROOT_ROLE, str(root))
        item.setToolTip(0, str(path))
        self.path_items[path_key(path)] = item

    def navigation_selection_changed(self, item, previous=None):
        value = item.data(0, ROOT_ROLE) if item else None
        if value and Path(value) != self.locations.current:
            self.locations.current = Path(value)
            try:
                self.save_locations()
            except OSError as error:
                self.show_notice('目前資料夾已切換，但無法保存導覽位置：' + str(error))

    def select_path(self, path):
        item = self.path_items.get(path_key(path))
        if item is None:
            return False
        self.tree.setCurrentItem(item)
        parent = item.parent()
        while parent is not None:
            parent.setExpanded(True)
            parent = parent.parent()
        if item.data(0, KIND_ROLE) in ('root', 'folder'):
            item.setExpanded(True)
        self.tree.scrollToItem(item)
        return True

    def open_item(self, item, retained=False):
        value = item.data(0, Qt.ItemDataRole.UserRole)
        if value:
            self.open_path(Path(value), retained)
        elif not retained and item.data(0, KIND_ROLE) in ('root', 'folder'):
            item.setExpanded(not item.isExpanded())

    def all_pages(self):
        return [*self.tabs_by_key.values(), *self.saved_pages.values()]



    def prepare_navigation(self, tab):
        if self.autosave.editing_dialog(tab):
            self.show_notice('請先完成目前表單的確認或取消。')
            return False
        return self.prepare_close(tab)


    def prune_saved_pages(self):
        needed = {entry[1] for entry in self.history if entry[0] == 'document' and self.valid_visit(entry)}
        for key, tab in list(self.saved_pages.items()):
            if key not in needed and self.dispose_page(tab):
                self.saved_pages.pop(key)



    def document_changed(self, tab):
        if getattr(getattr(tab.widget, 'buffer', None), 'opening', False):
            return
        self.update_tab(tab)
        self.autosave.schedule(tab)
        if hasattr(self, 'notebook'):self.notebook.schedule()


    def open_markdown(self, path, retained, *, new_tab=False):
        page = MarkdownPage(path)
        if hasattr(self,'notebook'):self.notebook.opened(page)
        tab = Tab('md:' + uuid.uuid4().hex, path.stem, page, retained, path=path)
        from .workspace_panes import PageObserver
        PageObserver(self,tab)
        return self.add_tab(tab, replace_current=not new_tab)


    def new_document(self, kind, name=None, directory=None, context=None):
        return self.commands.new(kind, context or self.commands.context('home'), name, directory)

    def remember_recent(self, path):
        location = str(path)
        recent = [location]+[p for p in self.recent_files if path_key(p) != path_key(path)]
        if recent[:20] == self.recent_files:
            return
        self.recent_files = recent[:20]
        self.recent_timer.start(500)
        for tab in self.all_pages():
            if isinstance(tab.widget, WorkspaceHome):
                tab.widget.set_recent(self.recent_files)

    def save_recent(self):
        output = QSaveFile(str(self.data/'recent-files.json'))
        output.setDirectWriteFallback(False)
        if output.open(QIODevice.OpenModeFlag.WriteOnly):
            output.write(json.dumps(self.recent_files, ensure_ascii=False).encode('utf-8'))
            output.commit()

    def save_locations(self):
        output = QSaveFile(str(self.data/'linked-folders.json'))
        output.setDirectWriteFallback(False)
        raw = json.dumps(self.locations.snapshot(), ensure_ascii=False, indent=2).encode('utf-8')
        if not output.open(QIODevice.OpenModeFlag.WriteOnly):
            raise OSError(output.errorString())
        if output.write(raw) != len(raw):
            output.cancelWriting()
            raise OSError('無法保存側欄資料夾連結。')
        if not output.commit():
            raise OSError(output.errorString())

    def add_root(self, directory):
        previous = list(self.locations.roots), self.locations.current
        try:
            root = self.locations.directory(directory)
            if root not in self.locations.roots:
                self.locations.roots.append(root)
            self.locations.current = root
            self.save_locations()
        except (ValueError, OSError) as error:
            self.locations.roots, self.locations.current = previous
            self.report_error(str(error))
            return False
        self.refresh_tree()
        self.select_path(root)
        return True

    def remove_root(self, root):
        previous = list(self.locations.roots), self.locations.current
        self.locations.roots = [p for p in self.locations.roots if p != root]
        if self.locations.current == root:
            self.locations.current = next(iter(self.locations.roots), None)
        try:
            self.save_locations()
        except OSError as error:
            self.locations.roots, self.locations.current = previous
            self.report_error(str(error))
            return False
        self.refresh_tree()
        return True

    def choose_root(self):
        folder = QFileDialog.getExistingDirectory(self, '加入本機資料夾', str(self.locations.current or ''))
        if folder:
            return self.add_root(folder)

    def show_location(self, path):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def choose_file(self):
        from .workspace_file_search import FileSearchDialog
        existing = getattr(self, 'file_search_dialog', None)
        if existing is not None and existing.isVisible():
            existing.search.setFocus()
            return existing
        dialog = FileSearchDialog(self, self.commands.context('page'))
        self.file_search_dialog = dialog
        def finished(_):
            if self.file_search_dialog is dialog:self.file_search_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()
        return dialog

    def open_route(self, route, section=None):
        if route == 'home':
            if self.tab_strip.bar.count():
                self.activate(self.tab_strip.bar.currentIndex())
                return
            return self.new_tab()
        self.ensure_utility()
        if route == 'gallery':
            self.utility.show_page(self.utility.gallery)
        elif route == 'export':
            self.utility.show_page(self.utility.clean_export)
        else:
            self.utility.settings(section or ('explore' if route == 'explore' else 'appearance'))

    def ensure_utility(self):
        if self.utility is None:
            data = self.session / 'app-services'
            state = initial_state()
            state['settings'].update({key:self.settings[key] for key in DISPLAY_KEYS})
            state['settings'].update(online=False, comfy_enabled=False)
            # This is a new session, not a historical database migration.
            # Prepare once in memory instead of writing an old-format seed,
            # reopening it, backing it up and migrating it on the first click.
            from .state_loading import prepare_state
            state = prepare_state(state, multi=True)
            self.utility = EmbeddedHost(data, workspace=self, service_state=state)
            self.utility.bind_workspace(self)
            self.workspace_stack.addWidget(self.utility)
            self.utility.comfy.stateChanged.connect(self.refresh_connection)
        return self.utility

    def request_connection(self):
        tab = self.tabs_by_key.get(self.active_key)
        host = tab.document.host if tab and tab.document else self.ensure_utility()
        host.comfy.recheck_connection()
        self.refresh_connection()

    def refresh_connection(self):
        self.comfy_connection.activate_document()
        tab = self.tabs_by_key.get(self.active_key)
        host = tab.document.host if tab and tab.document else self.utility
        self.connection.refresh(self.display_settings(), getattr(host, 'comfy', None))

    def utility_navigated(self, route, section=None):
        self.active_route = route
        self.active_key = 'utility:' + route
        self.utility.prepare_workspace_sidebar()
        self.workspace_stack.setCurrentWidget(self.utility)
        for pane in self.panes:
            pane.strip.bar.workspace_active = False
        self.sync_sidebar()
        self.record_visit(('utility', route, section))

    def sync_sidebar(self):
        expanded = self.width() >= 980 if self.sidebar_preference is None else self.sidebar_preference
        peek=getattr(self,'sidebar_peek',None)
        if peek and peek.is_open and (expanded or peek.target is not self.sidebar_target()):peek.close()
        if hasattr(self, 'sidebar_resize') and self.sidebar_resize.motion_target is not None:
            self.sidebar_resize.finish_motion()
        if self.active_route == 'home':
            if not self.sidebar.property('pcsSidebarBorrowed'):self.sidebar.setVisible(expanded)
        elif self.utility:
            target = self.utility.context_sidebar_target()
            if target is not None and not target.property('pcsSidebarBorrowed'):
                self.utility._set_sidebar_in_layout(target, expanded)
        for route, entry in self.routes.items():
            entry.setChecked(route == self.active_route)
        self.collapse.setToolTip('收合側欄' if expanded else '展開側欄')
        self.collapse.setAccessibleName(self.collapse.toolTip())
        if hasattr(self,'sidebar_resize'):
            self.sidebar_resize.schedule()
        self.sync_tab_inset()
        self.refresh_connection()
        self.workspace_stack.sync_surface()
        if peek:peek.position_edge()

    def record_visit(self, entry):
        if not self.traversing and (not self.history or self.history[self.history_at] != entry):
            self.history = self.history[:self.history_at+1] + [entry]
            self.history_at = len(self.history)-1
        self.prune_saved_pages()
        self.back.setEnabled(any(self.valid_visit(e) for e in self.history[:self.history_at]))
        self.forward.setEnabled(any(self.valid_visit(e) for e in self.history[self.history_at+1:]))

    def valid_visit(self, entry):
        return entry[0] == 'utility' or (entry[1] in self.tabs_by_key or entry[1] in self.saved_pages) and any(
            tab.slot == entry[2] for tab in self.tabs_by_key.values())

    def activate(self, position):
        key = self.tab_strip.bar.tabData(position)
        tab = self.tabs_by_key.get(key)
        if tab is None:
            return
        if self.active_route == 'home' and self.active_key == key and self.pages.currentWidget() is tab.widget:
            return
        if self.utility and self.active_route in ('explore', 'settings'):
            self.utility.settings_page.flush()
        self.active_route = 'home'
        self.active_key = key
        self.workspace_stack.setCurrentWidget(self.home_split)
        for pane in self.panes:
            pane.strip.bar.workspace_active = True
            pane.strip.bar.pane_active = pane is self.active_pane
            pane.strip.bar.update()
        self.pages.setCurrentWidget(tab.widget)
        # Stable editors are siblings of the route stack. Its Show event can
        # precede the pane becoming visible, so restore BOTH current pages
        # after the route switch, not only the tab that was clicked.
        for pane in self.panes:pane.pages.sync_page()
        self.pane_tab_row.sync()
        if tab.path:
            self.tree.setCurrentItem(self.path_items.get(path_key(tab.path)))
        if tab.path:
            self.remember_recent(tab.path)
        self.sync_sidebar()
        self.record_visit(('document', key, tab.slot))

    def go(self, delta):
        target = self.history_at + delta
        while 0 <= target < len(self.history):
            entry = self.history[target]
            if self.valid_visit(entry):
                previous = self.history_at
                self.traversing = True
                try:
                    self.history_at = target
                    if entry[0] == 'utility':
                        self.open_route(entry[1], entry[2])
                    elif entry[1] in self.saved_pages:
                        target_tab = next(t for t in self.tabs_by_key.values() if t.slot == entry[2])
                        self.use_pane(self.pane_for(target_tab))
                        at = self.index_of(target_tab.key)
                        if self.add_tab(self.saved_pages[entry[1]], replace_current=True, position=at) is None:
                            self.history_at = previous
                    else:
                        self.focus_tab(self.tabs_by_key[entry[1]])
                finally:
                    self.traversing = False
                self.record_visit(self.history[self.history_at])
                return
            target += delta

    def retain(self, tab):
        tab.retained = True
        self.update_tab(tab)

    def retain_index(self, index_):
        tab = self.tabs_by_key.get(self.tab_strip.bar.tabData(index_))
        if tab:
            self.retain(tab)

    def close_current(self):
        return self.close_tab(self.tab_strip.bar.currentIndex()) if self.active_route == 'home' else False

    def save_current(self):
        if self.active_route != 'home':
            return False
        tab = self.tabs_by_key.get(self.active_key)
        return self.save_tab(tab) if tab else False

    def save_tab(self, tab):
        if id(tab) in self.autosave.paused or self.autosave.key(tab) in self.autosave.saving:
            return False
        if getattr(tab.document or tab.widget, 'operation_error', False):
            self.show_notice((tab.document or tab.widget).error)
            return False
        self.autosave.stop(tab)
        self.autosave.saving.add(self.autosave.key(tab))
        try:
            if tab.document:
                saved = tab.document.save()
            elif isinstance(tab.widget, MarkdownPage):
                saved = tab.widget.save()
            else:
                return False
        finally:
            self.autosave.saving.discard(self.autosave.key(tab))
        self.update_tab(tab)
        if not saved:
            reason = tab.document.error if tab.document else tab.widget.error
            pending = getattr(tab.document, 'pending_material_uses', set())
            if pending and pending.intersection(tab.document.state.get('uses', {})):
                self.material_transfer.error('已加入，尚未自動儲存：'+(reason or '請先完成目前的編輯。')
                                             +'\n內容已保留。Ctrl+S 重試；Ctrl+Shift+S 另存。')
            else:
                self.show_notice('「' + tab.title + '」尚未儲存：' + (reason or '請先完成目前的編輯。')
                                 + '\n內容已保留。Ctrl+S 重試；Ctrl+Shift+S 另存。')
        elif tab.document:
            tab.document.pending_material_uses = set()
        elif saved and hasattr(self, 'notebook') and isinstance(tab.widget, MarkdownPage):
            self.notebook.saved(tab.widget)
        return saved

    def prepare_close(self, tab, decision=None):
        if id(tab) in self.autosave.paused:
            self.show_notice('請先完成或取消目前的文件操作。')
            return False
        if decision == 'discard':
            return True
        if getattr(getattr(tab.widget, 'buffer', None), 'composing', False):
            self.show_notice('請先完成目前的中文組字。')
            return False
        if getattr(tab.document or tab.widget, 'operation_error', False):
            self.show_notice((tab.document or tab.widget).error)
            return False
        document = tab.document
        if document:
            if decision == 'save':
                for host in document.hosts:
                    dialog = getattr(getattr(host, 'prompt_editor', None), 'active_dialog', None)
                    if dialog is not None and not dialog.save():return False
            if not document.flush_views():
                self.show_notice('請先完成素材表單的確認或取消，內容已保留。')
                return False
            document.update_dirty()
        if decision == 'cancel':
            return not tab.dirty
        return not tab.dirty or self.save_tab(tab)



    def tree_menu(self, point):
        self.commands.tree_menu(point)

    def root_menu(self):
        menu = RoundMenu(self)
        from .workspace_menu_action import trailing_action
        trailing_action(menu, self, '重新整理', 'update', self.refresh_tree)
        menu.addAction('加入本機資料夾…', self.choose_root)
        trailing_action(menu, self, '顯示空資料夾', 'check', self.toggle_empty_folders, self.show_empty_folders)
        root = self.locations.current
        if root:
            menu.addAction('開啟根資料夾位置', lambda: self.show_location(root))
        menu.open_at(self.tree_more.mapToGlobal(self.tree_more.rect().bottomLeft()))

    def toggle_empty_folders(self):
        self.show_empty_folders = not self.show_empty_folders
        self.refresh_tree()
        self.save_ui_preferences()

    def reveal_file(self, path):
        self.sidebar_preference = True
        self.open_route('home')
        self.sync_sidebar()
        self.select_path(path)

    def toggle_sidebar(self):
        expanded = self.width() >= 980 if self.sidebar_preference is None else self.sidebar_preference
        self.sidebar_preference = not expanded
        # Resizing every animation frame repeatedly lays out the live Canvas
        # and its forms. A disclosure click should settle in a single layout.
        self.sidebar_resize.finish_motion()
        self.sync_sidebar()
        self.collapse.setToolTip('展開側欄' if expanded else '收合側欄')
        self.collapse.setAccessibleName(self.collapse.toolTip())
        self.save_ui_preferences()

    def set_sidebar_hover(self, enabled, open_delay, close_delay):
        self.sidebar_auto_hide=bool(enabled)
        self.sidebar_open_delay=max(100,min(1200,int(open_delay)))
        self.sidebar_close_delay=max(100,min(2000,int(close_delay)))
        self.sidebar_peek.configure()
        self.save_ui_preferences()

    def show_recovery(self, document):
        dialog = QDialog(self)
        dialog.setWindowTitle('檢視回復版本')
        dialog.resize(850, 600)
        layout = QVBoxLayout(dialog)
        layout.addWidget(label(document.error, 'Subtle', True))
        choices = QListWidget()
        viewer = QPlainTextEdit()
        viewer.setReadOnly(True)
        candidates = [str(document.path)] + list(document.last_result.recovery_paths if document.last_result else ())
        candidates += list(getattr(document,'recovery_paths',()))
        for path in dict.fromkeys(candidates):
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            choices.addItem(item)
        def show(current, previous):
            try:
                with Path(current.data(Qt.ItemDataRole.UserRole)).open('rb') as stream:
                    raw = stream.read(1024*1024+1)
                text = raw[:1024*1024].decode('utf-8-sig', errors='replace')
                if len(raw) > 1024*1024:
                    text += '\n\n（此處僅顯示前 1 MiB；完整內容保留在上列檔案。）'
                viewer.setPlainText(text)
            except OSError as error:
                viewer.setPlainText(str(error))
        choices.currentItemChanged.connect(show)
        layout.addWidget(choices, 1)
        layout.addWidget(viewer, 3)
        choices.setCurrentRow(0)
        dialog.exec()

    def report_error(self, message):
        QMessageBox.warning(self, '無法完成操作', message)

    def show_notice(self, message):
        self.notification.present(message)

    def closeEvent(self, event):
        # Resolve every dirty file first. Cancelling window close must not
        # silently remove earlier clean tabs or their native editor histories.
        if self.commands.files.pending:
            self.show_notice('請先完成或取消目前的文件操作。')
            event.ignore()
            return
        if self.commands.files.inline is not None:
            self.commands.files.inline.cancel()
        checked = set()
        for tab in self.all_pages():
            owner = id(tab.document or getattr(tab.widget, "buffer", tab.widget))
            if owner in checked:continue
            checked.add(owner)
            if not self.prepare_close(tab):
                event.ignore()
                return
        if not self.layout_store.save():
            event.ignore()
            return
        if self.utility and not self.utility.close():
            event.ignore()
            return
        self.layout_store.stopped = True
        if hasattr(self, 'notebook'):self.notebook.stop()
        for pane in self.panes:
            while pane.strip.bar.count():
                if not self.close_tab(0, decision='discard', make_home=False, pane=pane):
                    event.ignore()
                    return
        self.scheduler.stop()
        for document in self.documents:
            document.refresh_timer.stop()
            for host in reversed(document.hosts):
                host.close()
        for document in self.documents:
            for execution in [document.execution, *document.retired_executions]:
                if execution is not None:execution.close()
        self.runtimes.close()
        # Shutdown can run a bounded event loop while waiting for a cancel
        # receipt. Keep every owner's UI alive until all clients have stopped.
        for document in self.documents:
            for host in document.hosts:host.deleteLater()
            document.deleteLater()
        from .application_events import unlisten
        unlisten(self.pane_focus)
        unlisten(self.tab_drag)
        unlisten(self.pointer_navigation)
        self.recent_timer.stop()
        self.autosave.external_poll.stop()
        self.tree_state.save()
        self.save_recent()
        self.save_ui_preferences()
        self.sidebar_peek.shutdown()
        self.sidebar_resize.shutdown()
        unlisten(self.centralWidget())
        unlisten(self.commands.files)
        event.accept()
