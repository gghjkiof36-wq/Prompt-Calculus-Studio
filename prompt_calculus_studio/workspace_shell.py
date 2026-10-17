"""Single-pane local documents, using the established PCS editing surfaces."""
from dataclasses import dataclass
from pathlib import Path
import uuid

from PySide6.QtCore import Qt, QSize, QUrl
from PySide6.QtGui import QShortcut, QKeySequence, QDesktopServices
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QFrame,
    QVBoxLayout, QHBoxLayout, QStackedWidget, QTreeWidget, QTreeWidgetItem,
    QHeaderView, QPlainTextEdit, QListWidget, QListWidgetItem, QMessageBox,
    QDialog, QFileDialog)

from .core import Storage, initial_state, DEFAULT_SETTINGS
from .document_host import DesktopDocument, EmbeddedHost
from .document_tabs import DocumentTabs
from .local_documents import index, paths
from .theme import stylesheet, visual_tokens, widget_palette
from .ui_icons import icon
from .widgets import button, label, RoundMenu


@dataclass
class Tab:
    key: str
    title: str
    widget: QWidget
    retained: bool = False
    document: DesktopDocument | None = None
    path: Path | None = None
    route: str = ''


class WorkspaceTop(QFrame):
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
        self.root = paths.root_path(root)
        self.data = Path(data).absolute()
        self.data.mkdir(parents=True, exist_ok=True)
        self.session = self.data / 'sessions' / uuid.uuid4().hex
        self.session.mkdir(parents=True)
        self.tabs_by_key = {}
        self.utility = None
        self.history = []
        self.history_at = -1
        self.traversing = False
        self.active_key = None
        self.settings = dict(DEFAULT_SETTINGS, material='solid', online=False, comfy_enabled=False)
        # Chrome has application appearance only; document state always stays
        # with its own host and is never swapped into the shell.
        self.state = {'settings': self.settings}
        self.setWindowTitle('Prompt Calculus Studio · 分頁檢視版')
        self.resize(1440, 900)
        self.setMinimumSize(720, 480)
        self.setPalette(widget_palette(self.settings))
        self.build()
        from .window_chrome import install_expanded_chrome
        self.window_chrome = install_expanded_chrome(self)
        if self.window_chrome:
            self.caption_inset(self.window_chrome.caption_right_inset)
            self.window_chrome.insetChanged.connect(self.caption_inset)
        self.style_shell()
        self.refresh_tree()
        self.new_tab()
        for sequence, callback in (('Ctrl+T', self.new_tab), ('Ctrl+W', self.close_current),
                                   ('Ctrl+S', self.save_current), ('Alt+Left', lambda: self.go(-1)),
                                   ('Alt+Right', lambda: self.go(1))):
            shortcut = QShortcut(QKeySequence(sequence), self)
            shortcut.activated.connect(callback)

    def tool(self, name, title, callback):
        control = button('', callback, 'Quiet')
        control.setIcon(icon(name))
        control.setIconSize(QSize(19, 19))
        control.setFixedSize(38, 36)
        control.setToolTip(title)
        control.setAccessibleName(title)
        return control

    def build(self):
        surface = QWidget()
        surface.setObjectName('DocumentWorkspace')
        self.setCentralWidget(surface)
        root_layout = QHBoxLayout(surface)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.rail = QFrame()
        self.rail.setObjectName('DocumentRail')
        self.rail.setFixedWidth(52)
        rail = QVBoxLayout(self.rail)
        rail.setContentsMargins(7, 5, 7, 9)
        rail.setSpacing(6)
        self.collapse = self.tool('menu', '收合內容側欄', self.toggle_sidebar)
        rail.addWidget(self.collapse)
        self.back = self.tool('back', '返回 (Alt+Left)', lambda: self.go(-1))
        self.forward = self.tool('forward', '前進 (Alt+Right)', lambda: self.go(1))
        rail.addWidget(self.back)
        rail.addWidget(self.forward)
        rail.addSpacing(12)
        self.routes = {}
        for key, title, symbol in (('home', 'Home', 'home'), ('gallery', '媒體庫', 'media'),
                                   ('explore', '探索', 'compass'), ('export', '匯出', 'export')):
            entry = self.tool(symbol, title, lambda checked=False, route=key: self.open_route(route))
            entry.setCheckable(True)
            rail.addWidget(entry)
            self.routes[key] = entry
        rail.addStretch()
        entry = self.tool('settings', '設定', lambda: self.open_route('settings'))
        entry.setCheckable(True)
        self.routes['settings'] = entry
        rail.addWidget(entry)
        root_layout.addWidget(self.rail)
        self.sidebar = QFrame()
        self.sidebar.setObjectName('DocumentSidebar')
        self.sidebar.setFixedWidth(244)
        side = QVBoxLayout(self.sidebar)
        side.setContentsMargins(12, 12, 8, 10)
        side.setSpacing(12)
        heading = QHBoxLayout()
        heading.addWidget(label('內容', 'Heading'))
        heading.addStretch()
        self.tree_more = self.tool('more-horizontal', '內容選單', self.root_menu)
        heading.addWidget(self.tree_more)
        side.addLayout(heading)
        self.tree = QTreeWidget()
        self.tree.setObjectName('DocumentTree')
        self.tree.setColumnCount(2)
        self.tree.setHeaderHidden(True)
        self.tree.setRootIsDecorated(True)
        self.tree.setUniformRowHeights(True)
        self.tree.setIndentation(15)
        self.tree.setFrameShape(QFrame.Shape.NoFrame)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.tree.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        self.tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self.tree_menu)
        self.tree.itemClicked.connect(lambda item, col: self.open_item(item))
        self.tree.itemDoubleClicked.connect(lambda item, col: self.open_item(item, retained=True))
        self.tree.itemActivated.connect(lambda item, col: self.open_item(item, retained=True))
        side.addWidget(self.tree, 1)
        root_layout.addWidget(self.sidebar)
        central = QWidget()
        central.setObjectName('DocumentPane')
        pane = QVBoxLayout(central)
        pane.setContentsMargins(0, 0, 0, 0)
        pane.setSpacing(0)
        self.top = WorkspaceTop()
        self.top.setObjectName('DocumentTabStrip')
        top = QHBoxLayout(self.top)
        top.setContentsMargins(6, 4, 10, 0)
        top.setSpacing(10)
        self.tab_strip = DocumentTabs()
        self.tab_strip.newRequested.connect(self.new_tab)
        self.tab_strip.bar.currentChanged.connect(self.activate)
        self.tab_strip.bar.tabCloseRequested.connect(self.close_tab)
        self.tab_strip.bar.tabBarDoubleClicked.connect(self.retain_index)
        self.tab_strip.bar.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tab_strip.bar.customContextMenuRequested.connect(self.tab_menu)
        top.addWidget(self.tab_strip, 1)
        self.connection = button('ComfyUI 未連線', lambda: self.open_route('settings'), 'Quiet')
        self.connection.setAccessibleName('ComfyUI 連線狀態與設定')
        top.addWidget(self.connection)
        pane.addWidget(self.top)
        self.pages = QStackedWidget()
        self.pages.setObjectName('DocumentPages')
        pane.addWidget(self.pages, 1)
        root_layout.addWidget(central, 1)

    def style_shell(self):
        t = visual_tokens(self.settings)
        self.setStyleSheet(stylesheet(self.settings) + f'''
            QWidget#DocumentWorkspace, QWidget#DocumentPane, QStackedWidget#DocumentPages {{ background:{t['base']}; }}
            QFrame#DocumentRail, QFrame#DocumentSidebar {{ background:{t['sidebar']}; }}
            QFrame#DocumentSidebar {{ border-right:1px solid {t['line']}; }}
            QFrame#DocumentTabStrip {{ background:{t['chrome']}; border:0; }}
            QTreeWidget#DocumentTree {{ background:transparent; border:0; outline:0; }}
            QTreeWidget#DocumentTree::item {{ height:34px; padding:0 3px; border-radius:5px; }}
            QTreeWidget#DocumentTree::item:selected {{ background:{t['selected']}; color:{t['text']}; }}
            QTabBar#DocumentTabBar {{ background:transparent; border:0; }}
            QTabBar#DocumentTabBar::tab {{ background:transparent; color:{t['secondary']}; border:0; border-radius:0; padding:8px 12px; margin:0; }}
            QTabBar#DocumentTabBar::tab:selected {{ background:{t['base']}; color:{t['text']}; border-top-left-radius:9px; border-top-right-radius:9px; }}
            QTabBar#DocumentTabBar::tab:selected:hover {{ background:{t['base']}; }}
            QTabBar#DocumentTabBar::tab:!selected:hover {{ background:{t['raised']}; border-radius:7px; }}
            QTabBar#DocumentTabBar QToolButton {{ padding:0; border:0; border-radius:4px; background:{t['chrome']}; }}
            QTabBar#DocumentTabBar QToolButton:hover {{ background:{t['raised']}; }}
            QFrame#DocumentTools {{ background:{t['base']}; border:0; }}
        ''')

    def caption_inset(self, inset):
        self.top.layout().setContentsMargins(6, 4, 10+inset, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'connection'):
            self.connection.setText('ComfyUI' if self.width() < 1050 else 'ComfyUI 未連線')
            self.connection.setToolTip('ComfyUI 未連線；開啟連線設定')

    def refresh_tree(self):
        self.tree.clear()
        self.entries = [entry for entry in index.scan(self.root)
                        if not any(part.casefold() == 'assets' for part in Path(entry.relative).parts[:-1])]
        root_item = QTreeWidgetItem([self.root.name, ''])
        root_item.setIcon(0, icon('folder'))
        root_item.setToolTip(0, str(self.root))
        self.tree.addTopLevelItem(root_item)
        folders = {'.': root_item}
        # Empty folders are meaningful navigation, without parsing file bodies.
        pending = [self.root]
        while pending:
            folder = pending.pop()
            for path in sorted(folder.iterdir(), key=lambda p: p.name.casefold()):
                if path.name.casefold() in ('.obsidian', '.pcs', 'assets') or paths.is_link(path) or not path.is_dir():
                    continue
                relative = path.relative_to(self.root)
                parent = folders[str(relative.parent)]
                item = QTreeWidgetItem([path.name, ''])
                item.setIcon(0, icon('folder'))
                parent.addChild(item)
                folders[str(relative)] = item
                pending.append(path)
        types = {'category': '分類', 'workflow': '工作流', 'markdown': 'MD'}
        for entry in self.entries:
            relative = Path(entry.relative)
            if str(relative.parent) not in folders:
                continue
            item = QTreeWidgetItem([relative.stem, types[entry.kind]])
            item.setData(0, Qt.ItemDataRole.UserRole, entry.relative)
            item.setToolTip(0, str(self.root / relative))
            folders[str(relative.parent)].addChild(item)
        root_item.setExpanded(True)

    def open_item(self, item, retained=False):
        relative = item.data(0, Qt.ItemDataRole.UserRole)
        if relative:
            self.open_path(paths.checked(self.root, relative), retained)

    def add_tab(self, tab, replace_preview=False):
        bar = self.tab_strip.bar
        if replace_preview:
            for i in range(bar.count()):
                current = self.tabs_by_key[bar.tabData(i)]
                if not current.retained and not (current.document and (current.document.dirty or current.document.draft_dirty)):
                    self.close_tab(i, decision='discard', make_home=False)
                    break
        self.tabs_by_key[tab.key] = tab
        if self.pages.indexOf(tab.widget) < 0:
            self.pages.addWidget(tab.widget)
        bar.blockSignals(True)
        at = bar.addTab(tab.title)
        bar.setTabData(at, tab.key)
        bar.blockSignals(False)
        self.update_tab(tab)
        bar.setCurrentIndex(at)
        self.activate(at)
        return tab

    def index_of(self, key):
        bar = self.tab_strip.bar
        return next((i for i in range(bar.count()) if bar.tabData(i) == key), -1)

    def update_tab(self, tab):
        at = self.index_of(tab.key)
        if at < 0:
            return
        document = tab.document
        dirty = document and (document.dirty or document.draft_dirty)
        if dirty:
            tab.retained = True
        self.tab_strip.decorate(at, tab.title, tab.retained, bool(dirty), str(tab.path or ''))
        if document:
            tab.save_button.setEnabled(document.dirty)
            tab.message.setText(document.error or ('未保存' if dirty else '已保存'))
            tab.recovery_button.setVisible(bool(document.error))

    def open_path(self, path, retained=False):
        path = Path(path).absolute()
        try:
            paths.checked(self.root, path.relative_to(self.root).as_posix())
            for tab in self.tabs_by_key.values():
                if tab.path == path:
                    if retained:
                        tab.retained = True
                    self.update_tab(tab)
                    self.tab_strip.bar.setCurrentIndex(self.index_of(tab.key))
                    return tab
            if path.suffix.lower() == '.md':
                return self.open_markdown(path, retained)
            document = DesktopDocument(path, self.session, self)
            for existing in self.tabs_by_key.values():
                if existing.document is None:
                    continue
                old = existing.document.receipt
                new = document.receipt
                if (old.device, old.inode) == (new.device, new.inode):
                    document.deleteLater()
                    self.tab_strip.bar.setCurrentIndex(self.index_of(existing.key))
                    return existing
                if existing.document.key == document.key:
                    raise ValueError('另一個位置的同身分文件已開啟，請先關閉原頁或建立獨立副本。')
            document.prepare()
            host = EmbeddedHost(document.data, document)
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            toolbar = QFrame()
            toolbar.setObjectName('DocumentTools')
            actions = QHBoxLayout(toolbar)
            actions.setContentsMargins(18, 7, 18, 7)
            save = button('保存文件', document.save, 'Quiet')
            actions.addWidget(save)
            actions.addWidget(self.tool('undo', '復原', host.canvas.undo))
            actions.addWidget(self.tool('forward', '重做', host.canvas.redo))
            actions.addStretch()
            message = label('已保存', 'Subtle')
            actions.addWidget(message)
            recovery = button('檢視回復版本', lambda: self.show_recovery(document), 'Quiet')
            recovery.hide()
            actions.addWidget(recovery)
            layout.addWidget(toolbar)
            layout.addWidget(host, 1)
            tab = Tab(document.key, path.stem, page, retained, document, path)
            tab.save_button, tab.message, tab.recovery_button = save, message, recovery
            document.changed.connect(lambda: self.update_tab(tab))
            document.editing.connect(lambda: self.retain(tab))
            return self.add_tab(tab, replace_preview=not retained)
        except (ValueError, OSError, KeyError) as error:
            self.report_error(str(error))
            return None

    def open_markdown(self, path, retained):
        raw = path.read_bytes()
        if len(raw) > 8*1024*1024:
            raise ValueError('Markdown 超過本輪閱讀上限 8 MiB。')
        page = QPlainTextEdit()
        page.setReadOnly(True)
        page.setPlainText(raw.decode('utf-8-sig'))
        page.setAccessibleName(path.name + '，唯讀內容')
        page.setFrameShape(QFrame.Shape.NoFrame)
        return self.add_tab(Tab('md:' + str(path), path.stem, page, retained, path=path), replace_preview=not retained)

    def new_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(36, 30, 36, 24)
        layout.setSpacing(16)
        layout.addWidget(label('開啟內容', 'DialogTitle'))
        listing = QListWidget()
        listing.setFrameShape(QFrame.Shape.NoFrame)
        listing.setAccessibleName('可開啟的本機文件')
        for entry in self.entries:
            item = QListWidgetItem(entry.relative)
            item.setData(Qt.ItemDataRole.UserRole, entry.relative)
            listing.addItem(item)
        listing.itemActivated.connect(lambda item: self.open_path(self.root / item.data(Qt.ItemDataRole.UserRole), True))
        listing.itemDoubleClicked.connect(lambda item: self.open_path(self.root / item.data(Qt.ItemDataRole.UserRole), True))
        layout.addWidget(listing, 1)
        layout.addWidget(button('開啟本機文件…', self.choose_file, 'Quiet'))
        return self.add_tab(Tab('new:' + uuid.uuid4().hex, '新分頁', page, True))

    def choose_file(self):
        filename, _ = QFileDialog.getOpenFileName(self, '開啟本機文件', str(self.root), 'PCS 文件 (*.pcs-category *.pcs-workflow *.md)')
        if filename:
            self.open_path(filename, True)

    def open_route(self, route):
        if route == 'home':
            for tab in self.tabs_by_key.values():
                if tab.key.startswith('new:'):
                    self.tab_strip.bar.setCurrentIndex(self.index_of(tab.key))
                    return
            return self.new_tab()
        key = 'utility:' + route
        if key in self.tabs_by_key:
            self.tab_strip.bar.setCurrentIndex(self.index_of(key))
            return
        if self.utility is None:
            data = self.session / 'app-services'
            store = Storage(data)
            state = initial_state()
            state['settings'].update(online=False, comfy_enabled=False, material='solid')
            store.save(state)
            store.close()
            self.utility = EmbeddedHost(data)
        names = dict(gallery='媒體庫', explore='探索', export='匯出', settings='設定')
        self.add_tab(Tab(key, names[route], self.utility, True, route=route))

    def activate(self, position):
        key = self.tab_strip.bar.tabData(position)
        tab = self.tabs_by_key.get(key)
        if tab is None:
            return
        self.active_key = key
        self.pages.setCurrentWidget(tab.widget)
        if tab.route:
            host = self.utility
            if tab.route == 'gallery':
                host.show_page(host.gallery)
            elif tab.route == 'export':
                host.show_page(host.clean_export)
            elif tab.route == 'explore':
                host.settings('explore')
            else:
                host.settings()
            host.studio_navigation.hide()
            host.canvas_header.hide()
        for route, button_ in self.routes.items():
            button_.setChecked(route == tab.route or route == 'home' and key.startswith('new:'))
        if not self.traversing and (not self.history or self.history[self.history_at] != key):
            self.history = self.history[:self.history_at+1] + [key]
            self.history_at = len(self.history)-1
        self.back.setEnabled(self.history_at > 0)
        self.forward.setEnabled(self.history_at+1 < len(self.history))

    def go(self, delta):
        target = self.history_at + delta
        while 0 <= target < len(self.history):
            key = self.history[target]
            if key in self.tabs_by_key:
                self.history_at = target
                self.traversing = True
                self.tab_strip.bar.setCurrentIndex(self.index_of(key))
                self.activate(self.index_of(key))
                self.traversing = False
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
        return self.close_tab(self.tab_strip.bar.currentIndex())

    def save_current(self):
        tab = self.tabs_by_key.get(self.active_key)
        return bool(tab and tab.document and tab.document.save())

    def prepare_close(self, tab, decision=None):
        document = tab.document
        if document:
            if not document.host.flush_editor_drafts():
                return False
            document.update_dirty()
        if document and (document.dirty or document.draft_dirty):
            if decision is None:
                choice = QMessageBox(self)
                choice.setWindowTitle('保存變更')
                choice.setText('「' + tab.title + '」有未保存的變更。')
                save = choice.addButton('保存', QMessageBox.ButtonRole.AcceptRole)
                discard = choice.addButton('捨棄變更', QMessageBox.ButtonRole.DestructiveRole)
                cancel = choice.addButton('取消', QMessageBox.ButtonRole.RejectRole)
                choice.setDefaultButton(cancel)
                choice.exec()
                decision = 'save' if choice.clickedButton() is save else 'discard' if choice.clickedButton() is discard else 'cancel'
            if decision == 'cancel' or decision == 'save' and not document.save():
                return False
        return True

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
        menu.addAction('保留此分頁', lambda: self.retain_index(at))
        menu.addAction('關閉分頁', lambda: self.close_tab(at))
        menu.open_at(self.tab_strip.bar.mapToGlobal(point))

    def tree_menu(self, point):
        item = self.tree.itemAt(point)
        if item is None or not item.data(0, Qt.ItemDataRole.UserRole):
            return
        menu = RoundMenu(self)
        menu.addAction('在新分頁開啟', lambda: self.open_item(item, True))
        menu.open_at(self.tree.viewport().mapToGlobal(point))

    def root_menu(self):
        menu = RoundMenu(self)
        menu.addAction('重新整理', self.refresh_tree)
        menu.addAction('在檔案總管開啟', lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.root))))
        menu.open_at(self.tree_more.mapToGlobal(self.tree_more.rect().bottomLeft()))

    def toggle_sidebar(self):
        self.sidebar.setVisible(self.sidebar.isHidden())
        title = '展開內容側欄' if self.sidebar.isHidden() else '收合內容側欄'
        self.collapse.setToolTip(title)
        self.collapse.setAccessibleName(title)

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
        QMessageBox.warning(self, '無法開啟文件', message)

    def closeEvent(self, event):
        # Resolve every dirty file first. Cancelling window close must not
        # silently remove earlier clean tabs or their native editor histories.
        for tab in list(self.tabs_by_key.values()):
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
        event.accept()
