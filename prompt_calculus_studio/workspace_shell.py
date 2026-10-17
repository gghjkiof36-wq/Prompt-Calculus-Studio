"""Single-pane local documents, using the established PCS editing surfaces."""
from dataclasses import dataclass
from pathlib import Path
import uuid
import json

from PySide6.QtCore import Qt, QSize, QUrl, QTimer, QSaveFile, QIODevice
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
from .workspace_locations import WorkspaceLocations, path_key
from .workspace_commands import WorkspaceCommands, PATH_ROLE, KIND_ROLE, ROOT_ROLE
from .workspace_style import sidebar_style, workspace_colors
from .workspace_sidebar import WorkspaceSidebarResize, WorkspaceTree
from .workspace_surface import WorkspaceSurface
from .workspace_destinations import WorkspaceNavigation


DISPLAY_KEYS = ('font_family', 'ui_size', 'prompt_size', 'material',
                'mica_transparency', 'acrylic_transparency', 'menu_transparency',
                'visual_palette', 'accent', 'density')


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

    @property
    def dirty(self):
        return bool(self.document and (self.document.dirty or self.document.draft_dirty)
                    or isinstance(self.widget, MarkdownPage) and self.widget.dirty)


class WorkspaceTop(QFrame):
    def paintEvent(self, event):
        super().paintEvent(event)
        for strip in self.findChildren(DocumentTabs, options=Qt.FindChildOption.FindDirectChildrenOnly):
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


class WorkspaceShell(QMainWindow):
    def __init__(self, root, data):
        super().__init__()
        from .tooltips import install_tooltip_styling
        install_tooltip_styling()
        self.root = paths.root_path(root)
        self.data = Path(data).absolute()
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
        # Chrome has application appearance only; document state always stays
        # with its own host and is never swapped into the shell.
        self.state = {'settings': self.settings}
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setWindowTitle('Prompt Calculus Studio · 分頁檢視版')
        self.resize(1440, 900)
        self.setMinimumSize(720, 480)
        self.setPalette(widget_palette(self.settings))
        self.build()
        self.arrange_navigation()
        from .window_chrome import install_expanded_chrome
        self.window_chrome = install_expanded_chrome(self)
        if self.window_chrome:
            self.caption_inset(self.window_chrome.caption_right_inset)
            self.window_chrome.insetChanged.connect(self.caption_inset)
        self.style_shell()
        self.sidebar_resize = WorkspaceSidebarResize(self)
        self.refresh_tree()
        self.new_tab()
        for sequence, callback in (('Ctrl+T', self.new_tab), ('Ctrl+W', self.close_current),
                                   ('Ctrl+S', self.save_current), ('Alt+Left', lambda: self.go(-1)),
                                   ('Ctrl+Shift+S', self.commands.files.save_as_current),
                                   ('Alt+Right', lambda: self.go(1))):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

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
        root_layout.addWidget(self.tab_row)
        self.tab_row.hide()

        from .connection_pill import ConnectionPill
        self.connection = ConnectionPill(self.request_connection)
        self.connection_dot = self.connection.dot
        self.connection_label = self.connection.brand
        self.refresh_connection()
        top.addSpacing(10)
        top.addWidget(self.connection)

        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(0)
        self.workspace_layout = workspace
        root_layout.addLayout(workspace, 1)
        self.rail = WorkspaceNavigation(self)
        self.routes = self.rail.routes
        workspace.addWidget(self.rail)
        self.workspace_stack = WorkspaceSurface(self)
        workspace.addWidget(self.workspace_stack, 1)
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
        for control in (self.tree_new, self.tree_folder, self.tree_more):
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
        side.addWidget(self.tree, 1)
        self.home_split.addWidget(self.sidebar)
        self.pages = QStackedWidget()
        self.pages.setObjectName('DocumentPages')
        self.home_split.addWidget(self.pages)
        self.home_split.setStretchFactor(0, 0)
        self.home_split.setStretchFactor(1, 1)
        self.home_split.setSizes([260, 1100])

    def style_shell(self):
        appearance = dict(self.settings, **self.appearance_preview)
        material_key = (appearance['material'], visual_tokens(appearance)['scheme'])
        if material_key != getattr(self, '_material_key', None):
            self.native_material = apply_backdrop(self, appearance['material'], appearance)
            self._material_key = material_key
        self.centralWidget().update()
        t = visual_tokens(appearance)
        palette = widget_palette(appearance)
        self.setPalette(palette)
        QApplication.instance().setPalette(palette)
        self.tab_strip.bar.active_fill = QColor(workspace_colors(appearance)['content'])
        self.tab_strip.bar.sidebar_fill = QColor(workspace_colors(appearance)['sidebar'])
        self.tab_strip.bar.hover_fill = QColor(t['raised'])
        self.tab_strip.bar.close_fill = QColor(t['selected'])
        self.tab_strip.bar.scroll_fill = QColor(t['chrome'])
        self.tab_strip.bar.active_ink = QColor(t['text'])
        self.tab_strip.bar.idle_ink = QColor(t['secondary'])
        self.tree.row_colors = dict(t, **workspace_colors(appearance))
        self.setStyleSheet(stylesheet(appearance) + f'''
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
        ''' + sidebar_style(appearance))
        self.refresh_connection()
        self.sync_navigation_density()
        self.workspace_stack.sync_surface()
        for tab in self.all_pages():
            if isinstance(tab.widget, WorkspaceHome):
                tab.widget.apply_appearance(appearance)
            elif tab.document:
                tab.document.host.apply_theme(preserve_layout=True)

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
                elif key=='material' and value in ('solid','mica','acrylic'):
                    self.settings[key]=value
                elif key=='visual_palette' and value in ('graphite','mist','paper'):
                    self.settings[key]=value
                elif key=='accent' and value in ('neutral','blue','green'):
                    self.settings[key]=value
                elif key=='density' and value in ('comfortable','compact'):
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
            position=saved.get('navigation_position')
            if position in ('side','top'):
                self.navigation_position=position
            if type(saved.get('show_empty_folders')) is bool:
                self.show_empty_folders = saved['show_empty_folders']
        except (ValueError,OSError):
            pass

    def display_settings(self):
        return {key:self.appearance_preview.get(key,self.settings[key]) for key in DISPLAY_KEYS}

    def save_ui_preferences(self):
        saved = {key:self.settings[key] for key in DISPLAY_KEYS}
        saved.update(sidebar_width=self.sidebar_width, sidebar_expanded=self.sidebar_preference,
                     navigation_position=self.navigation_position, show_empty_folders=self.show_empty_folders)
        output=QSaveFile(str(self.data/'workspace-ui.json'))
        output.setDirectWriteFallback(False)
        if output.open(QIODevice.OpenModeFlag.WriteOnly):
            output.write(json.dumps(saved,ensure_ascii=False).encode('utf-8'))
            output.commit()

    def sync_display_settings(self, settings, preview=None):
        changed=any(self.settings[key]!=settings[key] for key in DISPLAY_KEYS)
        self.settings.update({key:settings[key] for key in DISPLAY_KEYS})
        overlay={key:value for key,value in (preview or {}).items() if key in DISPLAY_KEYS}
        if changed or self.appearance_preview!=overlay:
            self.appearance_preview=overlay
            self.style_shell()
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
        selector = getattr(getattr(self.utility, 'settings_page', None), 'navigation_position', None)
        if selector is not None:
            selector.blockSignals(True)
            selector.setCurrentIndex(selector.findData(position))
            selector.blockSignals(False)
        self.arrange_navigation()
        self.sync_sidebar()
        self.save_ui_preferences()

    def arrange_navigation(self):
        horizontal = self.navigation_position == 'top'
        top = self.top.layout()
        top.removeWidget(self.tab_strip)
        top.removeWidget(self.rail)
        self.tab_row.layout().removeWidget(self.tab_strip)
        self.workspace_layout.removeWidget(self.rail)
        self.rail.arrange(self.navigation_position)
        self.tab_strip.set_tab_height(36 if horizontal else 40)
        if horizontal:
            top.insertWidget(2, self.rail, 1)
            self.tab_row.layout().addWidget(self.tab_strip, 1)
        else:
            top.insertWidget(2, self.tab_strip, 1)
            self.workspace_layout.insertWidget(0, self.rail)
        self.tab_row.setVisible(horizontal)
        self.rail.show()
        self.tab_strip.show()
        self.sync_navigation_density()

    def sync_navigation_density(self):
        horizontal = self.navigation_position == 'top'
        margins = self.top.layout().contentsMargins()
        self.nav_controls.layout().setContentsMargins(0, 0, 0, 0 if horizontal else 4)
        available = (self.width()-margins.left()-margins.right()
                     -self.nav_controls.sizeHint().width()-self.connection.width()-22)
        self.rail.adapt_width(available)

    def sync_tab_inset(self):
        if self.navigation_position == 'top':
            target = self.sidebar_target()
            inset = target.width() if target is not None and target.isVisible() else 16
            if self.tab_row.layout().contentsMargins().left() != inset:
                self.tab_row.layout().setContentsMargins(inset, 0, 16, 0)
                self.tab_row.layout().activate()
                self.tab_strip.fit()
        self.sync_tree_header()

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
        expanded = {key for key, item in old_items.items() if item.isExpanded()}
        selected = self.tree.currentItem()
        selected_path = selected.data(0, PATH_ROLE) if selected else None
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
                    if kind == 'folder' and not self.show_empty_folders and path not in occupied:
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
            root_item.setExpanded(not old_items or path_key(root) in expanded)
        self.tree.setCurrentItem(self.path_items.get(path_key(selected_path)) if selected_path else None)
        self.tree.blockSignals(False)
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

    def all_pages(self):
        return [*self.tabs_by_key.values(), *self.saved_pages.values()]

    def tab_for_path(self, path, include_history=False):
        if path is None:
            return None
        return next((tab for tab in (self.all_pages() if include_history else self.tabs_by_key.values())
                     if tab.path and path_key(tab.path) == path_key(path)), None)

    def add_tab(self, tab, replace_current=False, position=None):
        bar = self.tab_strip.bar
        at = bar.currentIndex() if position is None else position
        current = self.tabs_by_key.get(bar.tabData(at)) if replace_current else None
        if current:
            if not self.prepare_navigation(current):
                if tab.key not in self.saved_pages:
                    self.dispose_page(tab)
                return None
            self.autosave.stop(current)
            self.saved_pages[current.key] = self.tabs_by_key.pop(current.key)
            bar.preview_keys.discard(current.key)
            tab.slot = current.slot
        else:
            tab.slot = uuid.uuid4().hex
        self.saved_pages.pop(tab.key, None)
        self.tabs_by_key[tab.key] = tab
        if self.pages.indexOf(tab.widget) < 0:
            self.pages.addWidget(tab.widget)
        bar.blockSignals(True)
        if current:
            bar.setTabText(at, tab.title)
        else:
            at = bar.addTab(tab.title)
        bar.setTabData(at, tab.key)
        bar.blockSignals(False)
        self.update_tab(tab)
        bar.setCurrentIndex(at)
        self.activate(at)
        return tab

    def prepare_navigation(self, tab):
        if self.autosave.editing_dialog(tab):
            self.show_notice('請先完成目前表單的確認或取消。')
            return False
        return self.prepare_close(tab)

    def dispose_page(self, tab):
        self.autosave.forget(tab)
        if tab.document:
            if not tab.document.host.close():
                return False
            tab.document.deleteLater()
        self.pages.removeWidget(tab.widget)
        tab.widget.deleteLater()
        return True

    def prune_saved_pages(self):
        needed = {entry[1] for entry in self.history if entry[0] == 'document' and self.valid_visit(entry)}
        for key, tab in list(self.saved_pages.items()):
            if key not in needed and self.dispose_page(tab):
                self.saved_pages.pop(key)

    def index_of(self, key):
        bar = self.tab_strip.bar
        return next((i for i in range(bar.count()) if bar.tabData(i) == key), -1)

    def update_tab(self, tab):
        at = self.index_of(tab.key)
        if at < 0:
            return
        document = tab.document
        dirty = tab.dirty
        if dirty:
            tab.retained = True
        location = str(tab.path) if tab.path else ''
        self.tab_strip.decorate(at, tab.title, True, bool(dirty), location)
        error = document.error if document else tab.widget.error if isinstance(tab.widget,MarkdownPage) else ''
        if error:
            self.tab_strip.bar.setTabToolTip(at,self.tab_strip.bar.tabToolTip(at)+'\n'+error)

    def document_changed(self, tab):
        self.update_tab(tab)
        self.autosave.schedule(tab)

    def open_path(self, path, retained=False, *, new_tab=False):
        try:
            path = self.locations.file(path)
            for tab in self.all_pages():
                if tab.path and (tab.path == path or (tab.path.exists() and path.exists() and tab.path.samefile(path))):
                    if retained:
                        tab.retained = True
                    if self.index_of(tab.key) < 0:
                        return self.add_tab(tab, replace_current=not new_tab)
                    self.update_tab(tab)
                    self.tab_strip.bar.setCurrentIndex(self.index_of(tab.key))
                    self.activate(self.index_of(tab.key))
                    return tab
            if path.suffix.lower() == '.md':
                return self.open_markdown(path, retained, new_tab=new_tab)
            document = DesktopDocument(path, self.session, self)
            for existing in self.all_pages():
                if existing.document is None:
                    continue
                old = existing.document.receipt
                new = document.receipt
                if (old.device, old.inode) == (new.device, new.inode):
                    document.deleteLater()
                    self.tab_strip.bar.setCurrentIndex(self.index_of(existing.key))
                    self.activate(self.index_of(existing.key))
                    return existing
                if existing.document.key == document.key:
                    document.deleteLater()
                    raise ValueError('另一個位置的同身分文件已開啟，請先關閉原頁或建立獨立副本。')
            document.prepare()
            host = EmbeddedHost(document.data, document)
            host.bind_workspace(self)
            host.comfy.stateChanged.connect(self.refresh_connection)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            layout.addWidget(host, 1)
            tab = Tab(document.key, path.stem, page, retained, document, path)
            document.changed.connect(lambda: self.document_changed(tab))
            document.editing.connect(lambda: self.retain(tab))
            return self.add_tab(tab, replace_current=not new_tab)
        except (ValueError, OSError, KeyError) as error:
            self.report_error(str(error))
            return None

    def open_markdown(self, path, retained, *, new_tab=False):
        page = MarkdownPage(path)
        tab = Tab('md:' + uuid.uuid4().hex, path.stem, page, retained, path=path)
        page.document().modificationChanged.connect(lambda _: self.update_tab(tab))
        page.textChanged.connect(lambda: self.document_changed(tab))
        return self.add_tab(tab, replace_current=not new_tab)

    def new_tab(self):
        page = WorkspaceHome(self.root, self.recent_files, self.settings, self.choose_file,
                             lambda path: self.open_path(Path(path), True),
                             lambda kind: self.commands.new(kind, self.commands.context('home')))
        return self.add_tab(Tab('new:' + uuid.uuid4().hex, '新分頁', page, True))

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
        filename, _ = QFileDialog.getOpenFileName(self, '開啟本機文件', str(self.locations.current or ''), 'PCS 文件 (*.pcs-category *.pcs-workflow *.md)')
        if filename:
            self.open_path(filename, True)

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
            store = Storage(data)
            state = initial_state()
            state['settings'].update({key:self.settings[key] for key in DISPLAY_KEYS})
            state['settings'].update(online=False, comfy_enabled=False)
            store.save(state)
            store.close()
            self.utility = EmbeddedHost(data, workspace=self)
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
        tab = self.tabs_by_key.get(self.active_key)
        host = tab.document.host if tab and tab.document else self.utility
        self.connection.refresh(self.display_settings(), getattr(host, 'comfy', None))

    def utility_navigated(self, route, section=None):
        self.active_route = route
        self.active_key = 'utility:' + route
        self.utility.prepare_workspace_sidebar()
        self.workspace_stack.setCurrentWidget(self.utility)
        self.tab_strip.bar.workspace_active = False
        self.tab_strip.bar.update()
        self.sync_sidebar()
        self.record_visit(('utility', route, section))

    def sync_sidebar(self):
        expanded = self.width() >= 980 if self.sidebar_preference is None else self.sidebar_preference
        if self.active_route == 'home':
            self.sidebar.setVisible(expanded)
        elif self.utility:
            target = self.utility.context_sidebar_target()
            if target is not None:
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
        self.tab_strip.bar.workspace_active = True
        self.tab_strip.bar.update()
        self.pages.setCurrentWidget(tab.widget)
        self.tree.setCurrentItem(self.path_items.get(path_key(tab.path)) if tab.path else None)
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
                        at = next(self.index_of(t.key) for t in self.tabs_by_key.values() if t.slot == entry[2])
                        if self.add_tab(self.saved_pages[entry[1]], replace_current=True, position=at) is None:
                            self.history_at = previous
                    else:
                        self.tab_strip.bar.setCurrentIndex(self.index_of(entry[1]))
                        self.activate(self.index_of(entry[1]))
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
        if id(tab) in self.autosave.paused or tab.key in self.autosave.saving:
            return False
        if getattr(tab.document or tab.widget, 'operation_error', False):
            self.show_notice((tab.document or tab.widget).error)
            return False
        self.autosave.stop(tab)
        self.autosave.saving.add(tab.key)
        try:
            if tab.document:
                saved = tab.document.save()
            elif isinstance(tab.widget, MarkdownPage):
                saved = tab.widget.save()
            else:
                return False
        finally:
            self.autosave.saving.discard(tab.key)
        self.update_tab(tab)
        if not saved:
            reason = tab.document.error if tab.document else tab.widget.error
            self.show_notice('「' + tab.title + '」尚未儲存：' + (reason or '請先完成目前的編輯。')
                             + '\n頁面與編輯內容已保留，可按 Ctrl+S 重試。')
        return saved

    def prepare_close(self, tab, decision=None):
        if id(tab) in self.autosave.paused:
            self.show_notice('請先完成或取消目前的文件操作。')
            return False
        if decision == 'discard':
            return True
        if getattr(tab.document or tab.widget, 'operation_error', False):
            self.show_notice((tab.document or tab.widget).error)
            return False
        document = tab.document
        if document:
            if not document.host.flush_editor_drafts():
                return False
            document.update_dirty()
        if decision == 'cancel':
            return not tab.dirty
        return not tab.dirty or self.save_tab(tab)

    def close_tab(self, position, decision=None, make_home=True):
        bar = self.tab_strip.bar
        tab = self.tabs_by_key.get(bar.tabData(position))
        if tab is None or not self.prepare_close(tab, decision):
            return False
        document = tab.document
        if document:
            dialog = getattr(getattr(document.host, 'prompt_editor', None), 'active_dialog', None)
            if dialog is not None:
                dialog.reject()
            if not document.host.close():
                return False
        self.autosave.forget(tab)
        bar.blockSignals(True)
        bar.removeTab(position)
        bar.preview_keys.discard(tab.key)
        self.tabs_by_key.pop(tab.key)
        bar.blockSignals(False)
        if tab.widget is not self.utility:
            self.pages.removeWidget(tab.widget)
            tab.widget.deleteLater()
        if document:
            document.deleteLater()
        self.prune_saved_pages()
        if bar.count():
            self.activate(bar.currentIndex())
        elif make_home:
            self.new_tab()
        self.tab_strip.fit()
        return True

    def tab_menu(self, point):
        at = self.tab_strip.bar.tabAt(point)
        if at < 0:
            return
        menu = RoundMenu(self)
        tab = self.tabs_by_key[self.tab_strip.bar.tabData(at)]
        if tab.document or isinstance(tab.widget,MarkdownPage):
            menu.addAction('保存文件 · Ctrl+S', lambda:self.save_tab(tab))
            editor = tab.document.host.canvas if tab.document else tab.widget
            menu.addAction('復原 · Ctrl+Z', editor.undo)
            menu.addAction('重做 · Ctrl+Y', editor.redo)
            if tab.document and tab.document.error:
                menu.addAction('檢視回復版本', lambda:self.show_recovery(tab.document))
            for location in getattr(tab.document or tab.widget, 'recovery_paths', []):
                path = Path(location)
                if path.is_file() and path.suffix.casefold() in ('.md', '.pcs-category', '.pcs-workflow'):
                    menu.addAction('開啟保留的文件：'+path.name, lambda checked=False, p=path: self.open_path(p, True, new_tab=True))
            context = self.commands.context('tab', tab=tab)
            menu.addSeparator()
            self.commands.files.add_actions(menu, context)
            menu.addAction('在側欄中定位', lambda: self.reveal_file(tab.path)).setEnabled(context.root is not None)
            menu.addAction('顯示所在資料夾', lambda: self.show_location(context.path.parent))
            if context.root is None:
                menu.addAction('將所在資料夾加入側欄', lambda: self.add_root(context.path.parent))
            menu.addSeparator()
        menu.addAction('關閉分頁', lambda: self.close_tab(self.index_of(tab.key)))
        menu.open_at(self.tab_strip.bar.mapToGlobal(point))

    def tree_menu(self, point):
        self.commands.tree_menu(point)

    def root_menu(self):
        menu = RoundMenu(self)
        menu.addAction('重新整理', self.refresh_tree)
        menu.addAction('加入本機資料夾…', self.choose_root)
        menu.addAction('全部展開', self.tree.expandAll)
        menu.addAction('全部收合', self.tree.collapseAll)
        folders = menu.addMenu('資料夾顯示')
        show_empty = folders.addAction('顯示空資料夾', self.toggle_empty_folders)
        show_empty.setCheckable(True)
        show_empty.setChecked(self.show_empty_folders)
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
        self.sync_sidebar()
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
        for path in dict.fromkeys(candidates):
            item = QListWidgetItem(Path(path).name)
            item.setToolTip(path)
            item.setData(Qt.ItemDataRole.UserRole, path)
            choices.addItem(item)
        def show(current, previous):
            try:
                raw = Path(current.data(Qt.ItemDataRole.UserRole)).read_bytes()
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
        for tab in self.all_pages():
            if not self.prepare_close(tab):
                event.ignore()
                return
        while self.tab_strip.bar.count():
            if not self.close_tab(0, decision='discard', make_home=False):
                event.ignore()
                return
        if self.utility and not self.utility.close():
            event.ignore()
            return
        self.recent_timer.stop()
        self.save_recent()
        self.save_ui_preferences()
        self.sidebar_resize.shutdown()
        QApplication.instance().removeEventFilter(self.centralWidget())
        QApplication.instance().removeEventFilter(self.commands.files)
        event.accept()
