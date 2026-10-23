"""Chrome connection and imported conversations in the existing Explore area."""
import os
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QFileDialog, QSizePolicy

from .connector_service import connector_for
from .widgets import label, button, row, panel, ActionHeader, ElidedLabel, StudioDialog
from .ui_icons import icon


EXTENSION_ID = 'migmpicacfjoipmfcgdjocgdbplgldan'
STATE_NAMES = dict(receiving='傳送中', previewing='等待匯入', committing='保存中',
                   committed='已匯入', cancelled='已取消', failed='未完成', unknown='待查看')


class ConnectorPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.workspace = getattr(window, 'workspace_owner', None)
        self.process = None
        self.setup_dialog = None
        self.records_by_id = {}
        self.service = connector_for(self.workspace) if self.workspace else None
        body = QVBoxLayout(self)
        body.setSpacing(24)
        self.toggle = button('加入 Chrome', self.setup)
        body.addWidget(ActionHeader(label('ChatGPT 對話', 'DialogTitle'), self.toggle))
        self.state = label('', 'Subtle')
        body.addWidget(self.state)
        self.conversations, contents = panel('SettingsGroup')
        self.conversations.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        body.addWidget(self.conversations)
        contents.addWidget(ActionHeader(label('最近傳入', 'Heading'),
                                       button('匯入檔案…', self.import_file)))
        self.destination = ElidedLabel()
        self.destination.setObjectName('Subtle')
        self.destination.setMinimumWidth(0)
        self.destination.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.destination.setAccessibleName('對話保存位置')
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋對話')
        self.search.setAccessibleName('搜尋已傳入對話')
        self.search.textChanged.connect(self.filter_list)
        contents.addWidget(self.search)
        self.items = QListWidget()
        self.items.setAccessibleName('最近傳入對話')
        self.items.setFixedHeight(112)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.items.currentItemChanged.connect(self.selection_changed)
        self.items.itemActivated.connect(lambda *_: self.open_selected())
        contents.addWidget(self.items)
        self.empty_area = QWidget()
        self.empty_area.setFixedHeight(180)
        empty_layout = QVBoxLayout(self.empty_area)
        empty_layout.setContentsMargins(0, 24, 0, 24)
        empty_layout.setSpacing(12)
        empty_layout.addStretch()
        mark = label('')
        mark.setPixmap(icon('quote').pixmap(QSize(32, 32)))
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(mark)
        self.empty = label('尚無對話', 'Subtle')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty)
        empty_layout.addStretch()
        contents.addWidget(self.empty_area)
        self.open_button = button('開啟對話', self.open_selected, 'Primary')
        self.arrange_button = button('整理對話', self.arrange)
        self.reveal_button = button('所在資料夾', self.reveal, 'Quiet')
        self.cancel_button = button('取消傳送', self.cancel, 'Quiet')
        self.actions = ActionHeader(self.open_button, self.arrange_button, self.cancel_button, self.reveal_button)
        contents.addWidget(self.actions)
        location = row(label('保存位置', 'Subtle'), self.destination,
                       button('變更…', self.choose_destination, 'Quiet'))
        location.setStretch(1, 1)
        contents.addLayout(location)
        self.status = label('', 'Subtle', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        body.addWidget(self.status)
        body.addStretch(1)
        if self.service:
            self.service.changed.connect(self.refresh)
            try:
                self.service.ensure_store()
            except (ValueError, OSError) as error:
                self.report(str(error))
        self.refresh()

    def report(self, text):
        self.status.setText(text)
        self.status.setVisible(bool(text))
        if self.setup_dialog is not None:
            self.setup_status.setText(text)
            self.setup_status.setVisible(bool(text))

    def package(self):
        return Path(os.environ.get('PCS_CONNECTOR_PACKAGE', str(Path(__file__).resolve().parent.parent / 'connector-package')))

    def setup(self):
        if self.setup_dialog is None:
            self.setup_dialog = StudioDialog(self.workspace or self)
            self.setup_dialog.setWindowTitle('加入 Chrome')
            self.setup_dialog.resize(600, 360)
            self.setup_dialog.body.setSpacing(24)
            self.install_button = button('連接 PCS', self.install, 'Primary')
            self.install_state = label('', 'Subtle')
            self.setup_dialog.body.addWidget(ActionHeader(label('1. 連接 PCS', 'Heading'),
                                                         self.install_state, self.install_button))
            extension, extension_body = panel('SettingsGroup')
            extension_body.addWidget(label('2. 載入 Chrome 擴充', 'Heading'))
            extension_body.addWidget(label('開啟「開發人員模式」，再選「載入未封裝項目」。', 'Subtle', True))
            extension_body.addLayout(row(button('開啟擴充頁', self.open_chrome),
                                         button('開啟擴充資料夾', self.open_extension), None))
            self.setup_dialog.body.addWidget(extension)
            self.setup_status = label('', 'Subtle', True)
            self.setup_status.hide()
            self.setup_dialog.body.addWidget(self.setup_status)
            self.connection_button = button('停用 Chrome 傳入', self.change_connection, 'Quiet')
            self.setup_dialog.body.addLayout(row(self.connection_button, None,
                                                  button('關閉', self.setup_dialog.reject)))
        self.refresh_setup()
        self.setup_dialog.show()

    def refresh_setup(self):
        if self.setup_dialog is None:
            return
        ready = bool(self.service and self.service.ready)
        self.install_state.setText('已就緒' if ready else '')
        self.install_state.setVisible(ready)
        self.install_button.setVisible(not ready)
        self.connection_button.setVisible(ready)

    def open_chrome(self):
        candidates = [Path(os.environ.get(name, '')) / 'Google/Chrome/Application/chrome.exe'
                      for name in ('PROGRAMFILES', 'PROGRAMFILES(X86)', 'LOCALAPPDATA')]
        for executable in candidates:
            if executable.is_file():
                started, _ = QProcess.startDetached(str(executable), ['chrome://extensions'])
                if started:
                    return
        self.report('請在 Chrome 開啟 chrome://extensions')

    def change_connection(self):
        if not self.service:
            return
        try:
            self.service.set_enabled(not self.service.ready)
        except (ValueError, OSError) as error:
            self.report(str(error))

    def install(self):
        if not self.service or self.process is not None:
            return
        package = self.package()
        installer, host = package / 'pcs-connector-install.exe', package / 'pcs-connector-host.exe'
        if not installer.is_file() or not host.is_file():
            self.report('找不到連接程式。請開啟完整的擴充套件。')
            return
        self.process = QProcess(self)
        self.process.setProgram(str(installer))
        self.process.setArguments(['--host', str(host), '--extension-id', EXTENSION_ID])
        self.process.finished.connect(self.installed)
        self.process.errorOccurred.connect(lambda *_: self.install_failed())
        self.install_button.setEnabled(False)
        self.report('正在設定連接…')
        self.process.start()

    def install_failed(self):
        if self.process is None:
            return
        self.report('連接程式未能啟動，請重試。')
        self.process.deleteLater()
        self.process = None
        self.install_button.setEnabled(True)

    def installed(self, code, status):
        if self.process is None:
            return
        detail = bytes(self.process.readAllStandardError()).decode('utf-8', errors='replace').strip()
        self.process.deleteLater()
        self.process = None
        self.install_button.setEnabled(True)
        if code != 0 or status != QProcess.ExitStatus.NormalExit:
            self.report(detail or '連接設定失敗，請重試。')
            return
        try:
            self.service.set_enabled(True)
            self.report('連接程式已就緒。')
        except (ValueError, OSError) as error:
            self.report(str(error))

    def open_extension(self):
        folder = self.package() / 'chrome'
        if not folder.is_dir():
            folder = Path(__file__).resolve().parent.parent / 'extensions' / 'chrome'
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))

    def choose_destination(self):
        if not self.service:
            return
        folder = QFileDialog.getExistingDirectory(self, '對話保存位置', self.service.settings['destination'])
        if folder:
            try:
                self.service.set_destination(folder)
            except (ValueError, OSError) as error:
                self.report(str(error))

    def import_file(self):
        if not self.workspace:
            return
        from .conversation_dialog import ConversationImportDialog
        current = getattr(self.workspace, '_conversation_import', None)
        from shiboken6 import isValid
        if current is not None and isValid(current):
            current.show()
            current.raise_()
            return
        dialog = ConversationImportDialog(self.workspace, self.service.settings['destination'])
        self.workspace._conversation_import = dialog
        dialog.show()

    def refresh(self):
        key = self.selected()
        self.state.setText('Chrome 傳入已開啟' if self.service and self.service.ready else 'Chrome 傳入尚未開啟')
        if self.service:
            destination = self.service.settings['destination']
            here = self.workspace is not None and Path(destination) == self.workspace.root
            self.destination.setText('目前工作區' if here else Path(destination).name or destination)
            self.destination.setToolTip(destination)
            if self.service.error:
                self.report(self.service.error)
        self.refresh_setup()
        self.items.clear()
        self.records_by_id.clear()
        records = self.service.store.records() if self.service and self.service.store else []
        for record in records[:100]:
            title = record.get('title', '正在接收對話' if record['state'] == 'receiving' else '對話')
            item = QListWidgetItem(title + '\n' + STATE_NAMES.get(record['state'], record['state']))
            self.records_by_id[record['operation_id']] = record
            item.setData(Qt.ItemDataRole.UserRole, record['operation_id'])
            self.items.addItem(item)
            if record['operation_id'] == key:
                self.items.setCurrentItem(item)
        if self.items.currentRow() < 0 and self.items.count():
            self.items.setCurrentRow(0)
        has = bool(self.items.count())
        self.search.setVisible(has)
        self.items.setVisible(has)
        self.filter_list()
        self.selection_changed()

    def filter_list(self):
        query = self.search.text().strip().casefold()
        first = None
        for index in range(self.items.count()):
            item = self.items.item(index)
            item.setHidden(query not in item.text().casefold())
            if not item.isHidden() and first is None:
                first = item
        current = self.items.currentItem()
        if current is None or current.isHidden():
            self.items.setCurrentItem(first)
        self.empty.setText('沒有符合的對話' if query else '尚無對話')
        self.empty_area.setVisible(first is None)
        self.items.setVisible(first is not None)
        visible_count = sum(not self.items.item(index).isHidden() for index in range(self.items.count()))
        row_height = max(56, self.items.sizeHintForRow(self.items.row(first))) if first else 56
        self.items.setFixedHeight(min(360, max(112, visible_count * row_height + 8)))
        self.selection_changed()

    def selected_record(self):
        item = self.items.currentItem()
        return self.records_by_id.get(item.data(Qt.ItemDataRole.UserRole)) if item and not item.isHidden() else None

    def selected(self):
        record = self.selected_record()
        return record['operation_id'] if record else None

    def selection_changed(self, *_):
        record = self.selected_record()
        state = record['state'] if record else None
        for control in (self.open_button, self.arrange_button, self.reveal_button):
            control.setVisible(state == 'committed')
        self.open_button.setVisible(state in ('committed', 'previewing'))
        self.open_button.setText('查看預覽' if state == 'previewing' else '開啟對話')
        self.cancel_button.setVisible(state in ('receiving', 'previewing'))
        self.actions.setVisible(state in ('committed', 'previewing', 'receiving'))

    def open_selected(self):
        record = self.selected_record()
        if not record:
            return
        try:
            if record['state'] == 'previewing':
                self.service.show_preview(record['operation_id'])
            elif record['state'] == 'committed':
                self.service.open_saved(record['operation_id'])
        except (ValueError, OSError) as error:
            self.report(str(error))

    def reveal(self):
        if self.selected():
            try:
                self.service.open_saved(self.selected(), reveal=True)
            except (ValueError, OSError) as error:
                self.report(str(error))

    def arrange(self):
        if not self.selected():
            return
        try:
            tab = self.service.open_saved(self.selected())
            if tab:
                from .conversation_review_dialog import open_review
                open_review(tab.widget.md_surface)
        except (ValueError, OSError) as error:
            self.report(str(error))

    def cancel(self):
        if self.selected():
            self.service.dispatch(dict(command='cancel', operation_id=self.selected()))
