"""Keyboard-first file opening with an immediate cache and background inventory."""
from pathlib import Path
import threading

from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal, Qt, QEvent, QSize
from PySide6.QtGui import QPalette, QColor
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QFrame, QFileDialog

from .widgets import StudioDialog, button, label, row
from .workspace_file_dialogs import NameEdit
from .workspace_locations import KINDS, path_key
from .ui_icons import icon
from .theme import visual_tokens, font_pixels, widget_palette


class ScanSignals(QObject):
    ready = Signal(object, object)


class FileScan(QRunnable):
    def __init__(self, locations, roots, cancelled):
        super().__init__()
        self.locations, self.roots, self.cancelled = locations, roots, cancelled
        self.signals = ScanSignals()

    def run(self):
        entries, errors = [], []
        for root in self.roots:
            try:
                for path, kind in self.locations.inventory(root):
                    if self.cancelled.is_set():return
                    if kind != 'folder':entries.append((path, kind))
            except (OSError, ValueError):
                errors.append(root.name)
        if not self.cancelled.is_set():self.signals.ready.emit(entries, errors)


class FileSearchDialog(StudioDialog):
    def __init__(self, workspace, context):
        super().__init__(workspace)
        self.w, self.context = workspace, context
        self.setWindowTitle('開啟檔案')
        self.resize(min(820, workspace.width()-48), 440)
        self.heading.hide()
        self.search = NameEdit()
        self.search.setObjectName('WorkspaceFileQuery')
        self.search.setPlaceholderText('搜尋檔名或路徑…')
        self.search.setAccessibleName('搜尋檔案')
        self.heading_row.insertWidget(0, self.search, 1)
        self.search.installEventFilter(self)
        self.listing = QListWidget()
        self.listing.setObjectName('WorkspaceFileResults')
        self.listing.setAccessibleName('檔案搜尋結果')
        self.listing.setFrameShape(QFrame.Shape.NoFrame)
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.listing.setIconSize(QSize(18, 18))
        self.listing.installEventFilter(self)
        self.body.addWidget(self.listing, 1)
        self.status = label('', 'Subtle', True)
        self.body.addWidget(self.status)
        self.browse = button('從電腦選擇…', self.choose_other, 'Quiet')
        self.create = button('建立筆記', self.create_note, 'Quiet')
        self.create.hide()
        self.body.addLayout(row(self.browse, None, self.create))
        from .module_chrome import apply_module_dialog, dialog_colors
        settings = dict(workspace.settings, **workspace.appearance_preview)
        tokens = visual_tokens(settings)
        apply_module_dialog(self, tokens, max(16, font_pixels(settings['ui_size'])))
        t = dialog_colors(tokens)
        palette = widget_palette(settings)
        palette.setColor(QPalette.ColorRole.Window, QColor(t['base']))
        self.setPalette(palette)
        self._prompt_instant_show = True
        self.setStyleSheet(self.styleSheet()+f'''
            QLineEdit#WorkspaceFileQuery {{ background:transparent; border:0; padding:6px 0; font-size:{font_pixels(settings['ui_size']+1)}px; }}
            QListWidget#WorkspaceFileResults {{ background:transparent; border:0; outline:0; }}
            QListWidget#WorkspaceFileResults::item {{ padding:8px 10px; border-radius:6px; }}
            QListWidget#WorkspaceFileResults::item:hover {{ background:{t['hover']}; }}
            QListWidget#WorkspaceFileResults::item:selected {{ background:{t['selected']}; color:{t['text']}; }}
        ''')
        self.cancelled = threading.Event()
        self.finished.connect(lambda _: self.cancelled.set())
        cancelled = self.cancelled
        self.destroyed.connect(lambda *_: cancelled.set())
        self.recent = {path_key(p): i for i, p in enumerate(workspace.recent_files)}
        self.extra = [(Path(p), KINDS[Path(p).suffix.casefold()]) for p in workspace.recent_files
                      if Path(p).suffix.casefold() in KINDS]
        self.extra += [(tab.path, KINDS[tab.path.suffix.casefold()]) for tab in workspace.all_pages()
                       if tab.path and tab.path.suffix.casefold() in KINDS]
        self.entries, self.scan_errors = {}, []
        self.loading = True
        self.set_entries(workspace.entries, [])
        self.search.textChanged.connect(self.filter)
        self.listing.itemClicked.connect(lambda _: self.open_selected())
        self.scan = FileScan(workspace.locations, tuple(workspace.locations.roots), self.cancelled)
        self.scan.signals.ready.connect(self.scanned)
        QThreadPool.globalInstance().start(self.scan)
        self.search.setFocus()

    def scanned(self, entries, errors):
        if self.cancelled.is_set():return
        self.loading = False
        self.set_entries(entries, errors)

    def set_entries(self, entries, errors):
        self.entries = {path_key(path): (path, kind) for path, kind in self.extra+list(entries)}
        self.scan_errors = errors
        self.filter()

    def display_path(self, path):
        root = self.w.locations.root_for(path)
        if root is None:return str(path)
        relative = path.relative_to(root).with_suffix('').as_posix()
        return (root.name+'/' if len(self.w.locations.roots) > 1 else '')+relative

    def filter(self, *_):
        selected = self.listing.currentItem()
        selected = selected.data(Qt.ItemDataRole.UserRole) if selected else None
        tokens = self.search.text().casefold().replace('\\', '/').split()
        query = ' '.join(tokens)
        self.create.setVisible(bool(self.search.text().strip()) and hasattr(self.w,'notebook'))
        found = []
        for key, (path, kind) in self.entries.items():
            title = self.display_path(path)
            if not all(part in path.as_posix().casefold() for part in tokens):continue
            stem = path.stem.casefold()
            score = 0 if stem == query else 1 if stem.startswith(query) else 2
            found.append(((score, self.recent.get(key, 100000), title.casefold()), path, kind, title))
        found.sort(key=lambda entry: entry[0])
        self.listing.clear()
        for _, path, kind, title in found[:100]:
            item = QListWidgetItem(title)
            item.setIcon(icon(dict(workflow='workflow', category='list', markdown='file-text')[kind], 'navigation'))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            description = dict(workflow='工作流', category='Prompt 管理頁', markdown='文字文件')[kind]
            item.setToolTip(description+' · '+str(path))
            item.setData(Qt.ItemDataRole.AccessibleTextRole, title+'，'+description)
            item.setSizeHint(QSize(0, 44))
            self.listing.addItem(item)
            if str(path) == selected:self.listing.setCurrentItem(item)
        if self.listing.currentRow() < 0 and self.listing.count():self.listing.setCurrentRow(0)
        message = '正在更新檔案清單…' if self.loading else ''
        if not found and not self.loading:message = '找不到符合的檔案。' if tokens else '尚未加入可開啟的檔案。'
        if len(found) > 100:message = '顯示前 100 個結果，輸入更多文字可縮小範圍。'
        if self.scan_errors:message = '部分資料夾無法讀取：'+'、'.join(self.scan_errors)
        self.status.setText(message)
        self.status.setVisible(bool(message))
        self.listing.setVisible(bool(found))
        self.listing.setFixedHeight(min(6, max(1, len(found)))*44+4)
        # Keep a short result set compact; resizing keeps the input's top edge
        # in place while typing instead of recentering the search each time.
        self.layout().activate()
        self.resize(self.width(), self.layout().sizeHint().height())

    def eventFilter(self, watched, event):
        if watched in (getattr(self, 'search', None), getattr(self, 'listing', None)) and event.type() == QEvent.Type.KeyPress:
            if self.search.composing:return False
            key = event.key()
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.open_selected()
                return True
            if watched is self.search and key in (Qt.Key.Key_Up, Qt.Key.Key_Down):
                delta = -1 if key == Qt.Key.Key_Up else 1
                self.listing.setCurrentRow(max(0, min(self.listing.count()-1, self.listing.currentRow()+delta)))
                return True
        return super().eventFilter(watched, event)

    def open_selected(self):
        item = self.listing.currentItem()
        if item is not None:self.open_path(item.data(Qt.ItemDataRole.UserRole))
        elif self.create.isVisible():self.create_note()

    def create_note(self):
        try:
            from .notebook_actions import create_note
            root=self.context.root or self.w.locations.current
            path=create_note(root,self.search.text().strip())
            self.w.refresh_tree()
            if self.w.open_context(path,self.context):self.accept()
        except (OSError,ValueError) as error:
            self.status.setText(str(error));self.status.show()

    def open_path(self, path):
        try:
            path = self.w.locations.file(path)
        except (ValueError, OSError) as error:
            self.status.setText(str(error))
            self.status.show()
            return
        if self.w.open_context(path, self.context):self.accept()

    def choose_other(self):
        path, _ = QFileDialog.getOpenFileName(self, '開啟檔案', str(self.w.locations.current or ''),
                                             'PCS 檔案 (*.pcs-category *.pcs-workflow *.pcs-chat *.md)')
        if path:self.open_path(path)
