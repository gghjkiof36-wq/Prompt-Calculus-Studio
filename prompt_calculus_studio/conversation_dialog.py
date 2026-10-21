"""A selective local import sheet with bounded preview and cancellable file jobs."""
from pathlib import Path
import threading
import time

from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (QApplication, QFileDialog, QLineEdit, QListWidget, QListWidgetItem,
                              QSplitter, QTextBrowser, QVBoxLayout, QWidget, QProgressBar, QSizePolicy, QFrame)
from shiboken6 import isValid
from .widgets import StudioDialog, label, button, row, ComboBox, ElidedLabel
from .conversation_archive import Cancelled, load_archive
from .conversation_import import Destination, ImportResult, import_selected
from .conversation_markdown import preview_text


class ImportSignals(QObject):
    progress = Signal(str)
    finished = Signal(object, str)


class ImportJob(QRunnable):
    def __init__(self, function):
        super().__init__()
        self.function = function
        self.cancel = threading.Event()
        self.signals = ImportSignals()

    def run(self):
        last = 0.
        def progress(text):
            nonlocal last
            now = time.monotonic()
            if now-last >= .05:
                last = now
                self.signals.progress.emit(text)
        try:
            result = self.function(self.cancel, progress)
            self.signals.finished.emit(result, '')
        except Cancelled:self.signals.finished.emit(None, '已取消，沒有新增完成項目。')
        except Exception as error:self.signals.finished.emit(None, str(error))


class OfflinePreview(QTextBrowser):
    def __init__(self):
        super().__init__()
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setReadOnly(True)

    def loadResource(self, kind, url):
        # The export is untrusted. Preview never reads files or contacts URLs.
        return None


class ConversationImportDialog(StudioDialog):
    def __init__(self, workspace, directory):
        super().__init__(workspace)
        self.workspace = workspace
        self.destination = Destination.choose(directory)
        self.pane = workspace.active_pane
        self.archive = None
        self.job = None
        self.job_kind = ''
        self.close_when_done = False
        self.completed = False
        self.completion_label = '完成'
        self.completion_summary = ''
        self._owns_backdrop = True  # This utility sheet deliberately stays nonmodal.
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('匯入對話')
        self.resize(600, 340)
        self.setMinimumSize(480, 300)
        self.setAcceptDrops(True)
        self.drop_area = QFrame()
        self.drop_area.setObjectName('DialogEditor')
        drop = QVBoxLayout(self.drop_area)
        drop.setContentsMargins(24, 28, 24, 28)
        drop.setSpacing(16)
        drop.addStretch()
        drop.addWidget(label('拖入 ZIP 或 JSON', 'Heading'), 0, Qt.AlignmentFlag.AlignHCenter)
        self.drop_button = button('選擇檔案…', self.choose_source, 'Primary')
        drop.addWidget(self.drop_button, 0, Qt.AlignmentFlag.AlignHCenter)
        drop.addStretch()
        self.body.addWidget(self.drop_area, 1)
        self.source_button = button('更換檔案…', self.choose_source, 'Quiet')
        self.source_button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.source_name = ElidedLabel('')
        self.source_name.setObjectName('Subtle')
        self.source_name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.source_name.setTextFormat(Qt.TextFormat.PlainText)
        self.source_row = QWidget()
        self.source_row.setLayout(row(self.source_name, self.source_button))
        self.source_row.layout().setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.source_row)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋對話標題…')
        self.search.setAccessibleName('搜尋對話標題')
        self.search.textChanged.connect(self.filter_items)
        self.body.addWidget(self.search)
        self.split = QSplitter(Qt.Orientation.Horizontal)
        self.split.setChildrenCollapsible(False)
        left, right = QWidget(), QWidget()
        left_box, right_box = QVBoxLayout(left), QVBoxLayout(right)
        for box in (left_box, right_box):
            box.setContentsMargins(0, 0, 0, 0)
            box.setSpacing(8)
        self.select_visible = button('全選', lambda: self.check_visible(True), 'Quiet')
        self.clear_selection = button('清除', lambda: self.check_visible(False), 'Quiet')
        left_box.addLayout(row(self.select_visible, self.clear_selection))
        self.list = QListWidget()
        self.list.setProperty('moduleChoices', True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.setAccessibleName('對話；勾選方框加入匯入')
        self.list.setMinimumWidth(180)
        self.list.setMinimumHeight(110)
        self.list.setUniformItemSizes(True)
        self.list.currentRowChanged.connect(self.show_conversation)
        self.list.itemChanged.connect(self.update_selection)
        left_box.addWidget(self.list, 1)
        self.branches = ComboBox()
        self.branches.setAccessibleName('預覽分支')
        self.branches.currentIndexChanged.connect(self.show_branch)
        right_box.addWidget(self.branches)
        self.preview = OfflinePreview()
        self.preview.setAccessibleName('對話預覽')
        self.preview.setMinimumHeight(130)
        self.preview.setMinimumWidth(160)
        self.preview.document().setDocumentMargin(16)
        right_box.addWidget(self.preview, 1)
        self.split.addWidget(left)
        self.split.addWidget(right)
        self.split.setSizes([310, 550])
        self.body.addWidget(self.split, 1)
        self.location = QLineEdit(str(self.destination.path))
        self.location.setReadOnly(True)
        self.location.setAccessibleName('匯入位置')
        self.location.setToolTip(str(self.destination.path))
        self.location.setCursorPosition(0)
        self.location.setMinimumWidth(0)
        self.location_button = button('選擇位置…', self.choose_destination)
        self.location_row = QWidget()
        self.location_row.setLayout(row(label('儲存至'), self.location, self.location_button))
        self.location_row.layout().setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.location_row)
        self.status = label('', 'Subtle', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        self.body.addWidget(self.status)
        self.progress = QProgressBar()
        self.progress.setRange(0, 0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(4)
        self.progress.hide()
        self.body.addWidget(self.progress)
        self.count = label('已選 0 段對話', 'Subtle', True)
        self.count.setMinimumWidth(110)
        self.cancel_button = button('取消', self.reject)
        self.import_button = button('匯入選取對話', self.submit, 'Primary')
        self.open_folder = button('開啟資料夾', self.open_destination)
        self.open_folder.hide()
        self.footer = QWidget()
        self.footer.setLayout(row(self.count, None, self.open_folder, self.cancel_button, self.import_button))
        self.footer.layout().setContentsMargins(0, 0, 0, 0)
        self.body.addWidget(self.footer)
        self.show_contents(False)
        self.update_selection()

    def show_contents(self, loaded):
        self.drop_area.setVisible(not loaded)
        for control in (self.source_row, self.search, self.split, self.location_row, self.footer):
            control.setVisible(loaded)
        if loaded:
            self.setMinimumSize(480, 560)
            if self.width() <= 600:
                bounds=self.parentWidget().size() if self.parentWidget() else self.screen().availableGeometry().size()
                self.resize(min(960,max(480,bounds.width()-64)),min(740,max(560,bounds.height()-64)))

    @staticmethod
    def dropped_source(mime):
        urls = mime.urls()
        if len(urls) != 1 or not urls[0].isLocalFile():return None
        path = Path(urls[0].toLocalFile())
        return path if path.suffix.casefold() in ('.zip', '.json') and path.is_file() else None

    def dragEnterEvent(self, event):
        if not self.job and self.dropped_source(event.mimeData()):event.acceptProposedAction()
        else:event.ignore()

    def dropEvent(self, event):
        path = self.dropped_source(event.mimeData())
        if path and not self.job:
            event.acceptProposedAction();self.load_source(path)
        else:event.ignore()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'split'):
            orientation = Qt.Orientation.Vertical if self.width() < 740 else Qt.Orientation.Horizontal
            if self.split.orientation() != orientation:
                self.split.setOrientation(orientation)
                self.split.setSizes([180, 260] if orientation == Qt.Orientation.Vertical else [310, 550])

    def choose_source(self):
        path, _ = QFileDialog.getOpenFileName(self, '選擇對話封存', '', 'ChatGPT 對話 (*.zip *.json)')
        if path:self.load_source(path)

    def load_source(self, path):
        if self.job:return
        self.archive = None
        self.completed = False
        self.source_button.setText('更換檔案…')
        self.show_contents(False)
        self.branches.show()
        self.split.widget(0).show()
        self.location_button.show()
        self.search.clear()
        self.list.clear()
        self.branches.clear()
        self.open_folder.hide()
        self.source_name.setText(Path(path).name)
        self.source_name.setToolTip(str(path))
        self.preview.setPlainText('正在讀取對話清單…')
        self.start_job(lambda cancel, progress: load_archive(path, cancel, progress), 'read')

    def start_job(self, function, kind):
        self.job = ImportJob(function)
        self.job_kind = kind
        self.close_when_done = False
        self.job.signals.progress.connect(self.show_status)
        self.job.signals.finished.connect(self.job_finished)
        self.destroyed.connect(lambda _=None, flag=self.job.cancel: flag.set())
        self.progress.show()
        self.show_status('正在讀取對話…' if kind == 'read' else '正在準備匯入…')
        self.cancel_button.setText('取消處理')
        self.set_busy(True)
        QThreadPool.globalInstance().start(self.job)

    def set_busy(self, busy):
        self.source_button.setEnabled(not busy)
        self.drop_button.setEnabled(not busy)
        for control in (self.search, self.list, self.branches,
                        self.location_button, self.select_visible, self.clear_selection):
            control.setEnabled(not busy and not self.completed)
        self.update_selection()

    @Slot(str)
    def show_status(self, text):
        if self.close_when_done and self.job:
            text = '正在停止，已完成的對話會保留…'
        self.status.setText(text)
        self.status.show()

    @Slot(object, str)
    def job_finished(self, result, error):
        kind = self.job_kind
        self.job = None
        self.progress.hide()
        self.set_busy(False)
        self.cancel_button.setText('關閉')
        if isinstance(result, ImportResult):
            self.completed = True
            self.completion_label = '關閉' if result.cancelled or result.failed else '完成'
            self.set_busy(False)
            self.completion_summary = (f'完成 {len(result.completed)} 段 · 略過 {len(result.skipped)} 段'
                                       + (f' · 未完成 {len(result.failed)} 段' if result.failed else '')
                                       + (f' · 未處理 {len(result.unprocessed)} 段' if result.unprocessed else '')
                                       + (' · 已取消' if result.cancelled else ''))
            self.show_status(self.completion_summary)
            self.source_button.setText('選擇另一份…')
            self.search.hide()
            self.branches.hide()
            self.split.widget(0).hide()
            self.location_button.hide()
            lines = []
            for title, entries in (('已完成', result.completed), ('相同內容，已略過', result.skipped)):
                if entries:lines.extend([title, *['• '+result.titles.get(str(path), path.parent.name) for path in entries], ''])
            for title, folder, reason in result.failed:lines.extend([title+'：'+reason, folder, ''])
            if result.unprocessed:lines.extend(['尚未處理', *['• '+title for title in result.unprocessed]])
            self.preview.setPlainText('\n'.join(lines))
            self.open_folder.show()
            if isValid(self.workspace) and not self.workspace.layout_store.stopped:
                if result.completed:
                    if self.workspace.locations.root_for(result.completed[0]) is None:
                        self.workspace.add_root(self.destination.path)
                    self.workspace.refresh_tree()
                    if not self.close_when_done:
                        pane = self.pane if self.pane in self.workspace.panes else self.workspace.active_pane
                        self.workspace.open_path(result.completed[0], retained=True, new_tab=True, pane=pane)
        elif error:
            self.show_status(error)
            if kind == 'read':self.preview.setPlainText('無法載入這份封存。\n\n'+error+'\n\n請選擇另一份 ZIP／JSON。')
        elif kind == 'read' and not self.close_when_done:
            self.archive = result
            self.show_contents(True)
            self.list.blockSignals(True)
            for index, item in enumerate(result.conversations):
                entry = QListWidgetItem(item.title.replace('\n', ' ')+f'  ·  {len(item.leaves)} 分支')
                entry.setData(Qt.ItemDataRole.UserRole, index)
                entry.setToolTip(item.title)
                entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                entry.setCheckState(Qt.CheckState.Unchecked)
                self.list.addItem(entry)
            self.list.blockSignals(False)
            self.list.setCurrentRow(0)
            self.filter_items()
            self.status.hide()
        self.update_selection()
        if self.close_when_done:super().reject()

    def filter_items(self):
        query = self.search.text().casefold().strip()
        for index in range(self.list.count()):
            item = self.list.item(index)
            item.setHidden(query not in item.text().casefold())
        self.update_selection()

    def selected(self):
        return tuple(self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())
                     if self.list.item(i).checkState() == Qt.CheckState.Checked)

    def check_visible(self, checked):
        self.list.blockSignals(True)
        for index in range(self.list.count()):
            item = self.list.item(index)
            if not checked or not item.isHidden():
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)
        self.list.blockSignals(False)
        self.update_selection()

    def update_selection(self, *_):
        count = len(self.selected())
        self.count.setVisible(not self.completed)
        self.count.setText(f'{self.list.count()} 段對話' if self.archive else '')
        self.import_button.setText(self.completion_label if self.completed else f'匯入 {count} 段' if count else '匯入')
        self.import_button.setEnabled(not self.job and (self.completed or bool(count and self.archive)))
        self.cancel_button.setVisible(not self.completed)
        self.clear_selection.setEnabled(bool(count and not self.job and not self.completed))
        self.select_visible.setEnabled(bool(self.list.count() and not self.job and not self.completed))

    def show_conversation(self, row_index):
        self.branches.blockSignals(True)
        self.branches.clear()
        entry = self.list.item(row_index)
        if entry and self.archive:
            item = self.archive.conversations[entry.data(Qt.ItemDataRole.UserRole)]
            for number, leaf in enumerate(item.leaves, 1):
                suffix = ' · 目前路徑' if leaf == item.current else ''
                self.branches.addItem(f'分支 {number}{suffix}', leaf)
        self.branches.blockSignals(False)
        self.branches.setVisible(self.branches.count() > 1)
        self.show_branch()

    def show_branch(self, *_):
        entry = self.list.currentItem()
        if not self.archive or not entry or self.branches.currentData() is None:return
        item = self.archive.conversations[entry.data(Qt.ItemDataRole.UserRole)]
        self.preview.setMarkdown(preview_text(item, self.branches.currentData()))

    def choose_destination(self):
        path = QFileDialog.getExistingDirectory(self, '選擇匯入位置', str(self.destination.path))
        if path:
            try:self.destination = Destination.choose(path)
            except (OSError, ValueError) as error:self.show_status(str(error));return
            self.location.setText(str(self.destination.path))
            self.location.setToolTip(str(self.destination.path))

    def open_destination(self):
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.destination.path)))

    def submit(self):
        if self.completed:return self.accept()
        if self.job or not self.archive or not self.selected():return
        source, selected, destination = self.archive, self.selected(), self.destination
        self.start_job(lambda cancel, progress: import_selected(source, selected, destination, cancel, progress), 'write')

    def reject(self):
        if self.job:
            self.close_when_done = True
            self.job.cancel.set()
            self.show_status('正在停止…')
            self.cancel_button.setEnabled(False)
            self.hide()
            return
        super().reject()

    def stop_for_workspace_close(self):
        if self.job:
            self.close_when_done = True
            self.job.cancel.set()
            self.hide()
        else:self.close()


def open_import(surface):
    workspace = surface.page.file_title.workspace
    if workspace is None:return surface.report('請從工作區文件開啟對話匯入。')
    current = getattr(workspace, '_conversation_import', None)
    if current is not None and isValid(current):
        current.show()
        current.raise_()
        current.activateWindow()
        return current
    dialog = ConversationImportDialog(workspace, surface.page.path.parent)
    workspace._conversation_import = dialog
    dialog.show()
    return dialog
