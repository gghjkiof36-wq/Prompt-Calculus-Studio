"""Download history and direct file actions in the existing Explore area."""
from datetime import date, datetime, timedelta
import os
import sys
from pathlib import Path

from PySide6.QtCore import QProcess, QSize, Qt, QUrl, QTimer
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLineEdit, QProgressBar, QSizePolicy

from .connector_service import connector_for
from .connector_store import ACTIVE, HIDEABLE
from .widgets import label, button, row, panel, ActionHeader, ElidedLabel, StudioDialog
from .ui_icons import icon


EXTENSION_ID = 'migmpicacfjoipmfcgdjocgdbplgldan'
STATE_NAMES = dict(receiving='傳送中', previewing='等待匯入', committing='正在儲存',
                   cancelled='已取消', failed='未完成', unknown='結果待確認')


def record_time(record):
    try:
        return datetime.fromtimestamp(record.get('updated_at', 0))
    except (ValueError, TypeError, OverflowError, OSError):
        return datetime.min


def date_heading(record):
    day = record_time(record).date()
    if day == date.today():
        return '今天'
    if day == date.today() - timedelta(days=1):
        return '昨天'
    return day.strftime('%Y 年 %m 月 %d 日') if day != date.min else '較早'


def record_title(record):
    return record.get('title') or ('正在接收對話' if record['state'] == 'receiving' else '對話')


class HistoryRow(QWidget):
    """One operation owns its own buttons; list selection has no side effects."""
    def __init__(self, page, record):
        super().__init__()
        self.page = page
        self.operation = record['operation_id']
        self.record = {}
        self.setMinimumWidth(0)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 12, 0, 12)
        layout.setSpacing(12)
        self.mark = label('')
        self.mark.setPixmap(icon('file-text').pixmap(QSize(24, 24)))
        self.mark.setFixedSize(28, 32)
        layout.addWidget(self.mark, 0, Qt.AlignmentFlag.AlignTop)
        text = QVBoxLayout()
        text.setSpacing(4)
        self.title = ElidedLabel()
        font = self.title.font()
        font.setBold(True)
        self.title.setFont(font)
        self.metadata = ElidedLabel()
        self.metadata.setObjectName('Subtle')
        self.location = ElidedLabel()
        self.location.setObjectName('Subtle')
        for field in (self.title, self.metadata, self.location):
            field.setMinimumWidth(0)
            field.setTextFormat(Qt.TextFormat.PlainText)
            field.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
            text.addWidget(field)
        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(8)
        text.addWidget(self.progress)
        self.error = label('', 'Subtle', True)
        self.error.setMinimumWidth(0)
        self.error.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        text.addWidget(self.error)
        layout.addLayout(text, 1)
        actions = QHBoxLayout()
        actions.setSpacing(0)
        self.open_button = self.action('external-link', '開啟文件', lambda: page.open_record(self.operation))
        self.reveal_button = self.action('folder', '開啟資料夾', lambda: page.reveal_record(self.operation))
        self.finish_button = self.action('download', '完成匯入', lambda: page.finish_record(self.operation))
        self.cancel_button = self.action('close', '取消傳送', lambda: page.cancel_record(self.operation))
        self.remove_button = self.action('close', '移除紀錄', lambda: page.remove_record(self.operation))
        for control in (self.reveal_button, self.open_button, self.finish_button, self.cancel_button, self.remove_button):
            actions.addWidget(control)
        layout.addLayout(actions, 0)
        layout.setAlignment(actions, Qt.AlignmentFlag.AlignTop)
        self.update_record(record)

    def action(self, name, title, callback):
        control = button('', callback, 'IconButton')
        control.setIcon(icon(name))
        control.setIconSize(QSize(18, 18))
        control.setToolTip(title)
        control.setAccessibleName(title)
        control.setFixedSize(36, 36)
        return control

    def update_record(self, record):
        old_error = self.record.get('message')
        self.record = dict(record)
        title = record_title(record)
        self.title.setText(title)
        self.title.setToolTip(title)
        self.setAccessibleName(title)
        stamp = record_time(record).strftime('%H:%M')
        filename = Path(record['path']).name if record.get('path') else '對話.md'
        state = record['state']
        parts = [filename, stamp]
        if state == 'receiving':
            size = max(1, int(record.get('size', 0)))
            received = max(0, min(size, int(record.get('received', 0))))
            self.progress.setRange(0, size)
            self.progress.setValue(received)
            parts.append(f'傳送中 · {received * 100 // size}%')
        elif state != 'committed':
            parts.append(STATE_NAMES.get(state, '未完成'))
        self.metadata.setText(' · '.join(parts))
        self.metadata.setToolTip(self.metadata.text())
        self.progress.setVisible(state in ('receiving', 'committing'))
        if state == 'committing':
            self.progress.setRange(0, 0)
        location = str(Path(record['path']).parent) if record.get('path') else ''
        self.location.setText(location)
        self.location.setToolTip(location)
        self.location.setVisible(bool(location))
        self.open_button.setVisible(state == 'committed')
        self.reveal_button.setVisible(state == 'committed')
        self.finish_button.setVisible(state == 'previewing')
        self.cancel_button.setVisible(state in ACTIVE)
        self.remove_button.setVisible(state in HIDEABLE)
        if state in ('failed', 'unknown') and record.get('message'):
            self.report(record['message'])
        elif old_error != record.get('message') or state in ACTIVE:
            self.report('')
        elif not self.error.text():
            self.error.hide()

    def report(self, text):
        self.error.setText(text)
        self.error.setVisible(bool(text))


class ConnectorPage(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.workspace = getattr(window, 'workspace_owner', None)
        self.process = None
        self.setup_dialog = None
        self.records_by_id = {}
        self.rows = {}
        self.group_labels = []
        self.history_signature = None
        self.visible_limit = 100
        self.service = connector_for(self.workspace) if self.workspace else None
        body = QVBoxLayout(self)
        body.setSpacing(16)
        self.toggle = button('加入 Chrome', self.setup)
        body.addWidget(ActionHeader(label('下載紀錄', 'DialogTitle'),
                                   button('匯入檔案…', self.import_file, 'Quiet'), self.toggle))
        self.state = label('', 'Subtle')
        body.addWidget(self.state)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋紀錄')
        self.search.setAccessibleName('搜尋下載紀錄')
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self.filter_list)
        body.addWidget(self.search)
        self.history = QWidget()
        self.history.setAccessibleName('下載紀錄')
        self.history.setMinimumWidth(0)
        self.history_layout = QVBoxLayout(self.history)
        self.history_layout.setContentsMargins(0, 0, 0, 0)
        self.history_layout.setSpacing(4)
        body.addWidget(self.history)
        self.empty = label('沒有下載紀錄', 'Subtle')
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setMinimumHeight(140)
        body.addWidget(self.empty)
        self.more_button = button('顯示更多', self.show_more, 'Quiet')
        body.addWidget(self.more_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.status = label('', 'Subtle', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.hide()
        body.addWidget(self.status)
        body.addStretch(1)
        self.poll = QTimer(self)
        self.poll.setInterval(600)
        self.poll.timeout.connect(self.refresh)
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
        base = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent.parent
        return Path(os.environ.get('PCS_CONNECTOR_PACKAGE', str(base / 'connector-package')))

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
        self.state.setText('Chrome 已連接' if self.service and self.service.ready else 'Chrome 尚未連接')
        if self.service:
            if self.service.error:
                self.report(self.service.error)
        self.refresh_setup()
        try:
            records = self.service.store.history_records() if self.service and self.service.store else []
        except (ValueError, OSError) as error:
            self.report(str(error))
            return
        self.records_by_id = {record['operation_id']: record for record in records}
        self.render_history()
        if self.isVisible() and any(record['state'] in ACTIVE for record in records):
            if not self.poll.isActive():
                self.poll.start()
        else:
            self.poll.stop()

    def filter_list(self, *_):
        self.visible_limit = 100
        self.render_history()

    def show_more(self):
        self.visible_limit += 100
        self.render_history()

    def render_history(self):
        query = self.search.text().strip().casefold()
        matches = []
        for record in self.records_by_id.values():
            searchable = ' '.join((record_title(record), record.get('path', ''),
                                   STATE_NAMES.get(record['state'], ''), date_heading(record)))
            if query in searchable.casefold():
                matches.append(record)
        shown = matches[:self.visible_limit]
        signature = tuple((record['operation_id'], date_heading(record)) for record in shown)
        if signature != self.history_signature:
            self.history_signature = signature
            while self.history_layout.count():
                child = self.history_layout.takeAt(0).widget()
                if child is not None:
                    child.hide()
                    child.deleteLater()
            self.rows = {}
            self.group_labels = []
            previous = None
            for record in shown:
                heading = date_heading(record)
                if heading != previous:
                    group = label(heading, 'Heading')
                    group.setContentsMargins(0, 16 if previous else 4, 0, 4)
                    self.history_layout.addWidget(group)
                    self.group_labels.append(group)
                    previous = heading
                widget = HistoryRow(self, record)
                self.rows[record['operation_id']] = widget
                self.history_layout.addWidget(widget)
        else:
            for record in shown:
                self.rows[record['operation_id']].update_record(record)
        self.empty.setText('沒有符合的紀錄' if query else '沒有下載紀錄')
        self.empty.setVisible(not shown)
        self.history.setVisible(bool(shown))
        self.more_button.setVisible(len(matches) > len(shown))

    def showEvent(self, event):
        super().showEvent(event)
        self.refresh()

    def hideEvent(self, event):
        self.poll.stop()
        super().hideEvent(event)

    def perform(self, operation, callback):
        if not self.service:
            return
        try:
            callback()
            widget = self.rows.get(operation)
            if widget and widget.record['state'] not in ('failed', 'unknown'):
                widget.report('')
        except (ValueError, OSError) as error:
            widget = self.rows.get(operation)
            if widget:
                widget.report(str(error))
            else:
                self.report(str(error))

    def open_record(self, operation):
        self.perform(operation, lambda: self.service.open_saved(operation))

    def reveal_record(self, operation):
        self.perform(operation, lambda: self.service.open_saved(operation, reveal=True))

    def finish_record(self, operation):
        self.perform(operation, lambda: self.service.show_preview(operation))

    def cancel_record(self, operation):
        self.perform(operation, lambda: self.service.dispatch(dict(command='cancel', operation_id=operation)))

    def remove_record(self, operation):
        def remove():
            self.service.ensure_store().hide_history(operation)
            self.service.changed.emit()
        self.perform(operation, remove)
