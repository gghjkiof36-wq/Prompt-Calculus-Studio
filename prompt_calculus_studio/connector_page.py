"""Chrome connection and imported conversations in the existing Explore area."""
import os
from pathlib import Path

from PySide6.QtCore import QProcess, Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QFileDialog

from .connector_service import connector_for
from .widgets import label, button, row


EXTENSION_ID = 'migmpicacfjoipmfcgdjocgdbplgldan'
STATE_NAMES = dict(receiving='傳送中', previewing='等待匯入', committing='保存中',
                   committed='已匯入', cancelled='已取消', failed='未完成', unknown='待查看')


class ConnectorPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.workspace = getattr(window, 'workspace_owner', None)
        self.process = None
        self.records_by_id = {}
        self.service = connector_for(self.workspace) if self.workspace else None
        body = QVBoxLayout(self)
        body.setSpacing(18)
        body.addWidget(label('ChatGPT 對話', 'DialogTitle'))
        self.state = label('', 'Subtle')
        self.toggle = button('設定 Chrome', self.setup, 'Primary')
        body.addLayout(row(self.state, None, self.toggle))
        self.setup_area = QWidget()
        setup = QVBoxLayout(self.setup_area)
        setup.setContentsMargins(0, 0, 0, 0)
        setup.setSpacing(12)
        self.install_button = button('安裝連接程式', self.install, 'Primary')
        setup.addLayout(row(label('1. 連接 PCS', 'Heading'), None, self.install_button))
        setup.addLayout(row(label('2. 載入 Chrome 擴充', 'Heading'), None,
                            button('開啟擴充資料夾', self.open_extension)))
        setup.addWidget(label('Chrome 擴充功能 → 開發人員模式 → 載入未封裝項目', 'Subtle', True))
        self.connection_button = button('停用連接', self.change_connection, 'Quiet')
        setup.addLayout(row(None, self.connection_button))
        body.addWidget(self.setup_area)
        self.setup_area.hide()
        self.destination = QLineEdit()
        self.destination.setReadOnly(True)
        self.destination.setAccessibleName('對話保存位置')
        body.addLayout(row(label('儲存至'), self.destination, button('變更…', self.choose_destination)))
        body.addLayout(row(label('最近傳入', 'Heading'), None, button('匯入檔案…', self.import_file, 'Quiet')))
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋對話')
        self.search.setAccessibleName('搜尋已傳入對話')
        self.search.textChanged.connect(self.filter_list)
        body.addWidget(self.search)
        self.items = QListWidget()
        self.items.setAccessibleName('最近傳入對話')
        self.items.setMinimumHeight(220)
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.items.currentItemChanged.connect(self.selection_changed)
        self.items.itemActivated.connect(lambda *_: self.open_selected())
        body.addWidget(self.items, 1)
        self.empty = label('從 Chrome 選取對話後，按「送到 PCS」。', 'Subtle', True)
        body.addWidget(self.empty)
        self.open_button = button('開啟對話', self.open_selected, 'Primary')
        self.arrange_button = button('整理對話', self.arrange)
        self.reveal_button = button('所在資料夾', self.reveal, 'Quiet')
        self.cancel_button = button('取消傳送', self.cancel, 'Quiet')
        body.addLayout(row(self.open_button, self.arrange_button, None, self.cancel_button, self.reveal_button))
        self.status = label('', 'Subtle', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        body.addWidget(self.status)
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

    def package(self):
        return Path(os.environ.get('PCS_CONNECTOR_PACKAGE', str(Path(__file__).resolve().parent.parent / 'connector-package')))

    def setup(self):
        self.setup_area.setVisible(not self.setup_area.isVisible())

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
        self.state.setText('可接收 Chrome 對話' if self.service and self.service.ready else '尚未連接')
        if self.service:
            self.destination.setText(self.service.settings['destination'])
            self.destination.setCursorPosition(0)
            self.toggle.setText('連接設定' if self.service.ready else '設定 Chrome')
            self.connection_button.setText('停用連接' if self.service.ready else '啟用連接')
            if self.service.error:
                self.report(self.service.error)
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
        self.empty.setVisible(not has)
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
        if current is not None and current.isHidden():
            self.items.setCurrentItem(first)
        self.empty.setText('沒有符合的對話' if query else '從 Chrome 選取對話後，按「送到 PCS」。')
        self.empty.setVisible(first is None)
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
