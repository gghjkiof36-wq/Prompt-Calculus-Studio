"""Compact destination confirmation for older Chrome connector clients."""
from PySide6.QtCore import Qt, Slot, QThreadPool
from PySide6.QtWidgets import QFileDialog, QLineEdit
from shiboken6 import isValid

from .conversation_archive import load_archive
from .conversation_dialog import ImportJob
from .conversation_import import Destination, ImportResult
from .widgets import StudioDialog, ElidedLabel, label, button, row


class ConnectorImportDialog(StudioDialog):
    def __init__(self, service, record):
        super().__init__(service.workspace)
        self.service = service
        self.workspace = service.workspace
        self.operation = record['operation_id']
        self.archive = None
        self.destination = Destination.choose(service.settings['destination'])
        self.job = None
        self.job_kind = ''
        self.close_when_done = False
        self._owns_backdrop = True
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle('儲存到 PCS')
        self.setMinimumWidth(400)
        self.resize(560, 260)

        self.source_name = ElidedLabel(record['title'])
        self.source_name.setTextFormat(Qt.TextFormat.PlainText)
        self.source_name.setToolTip(record['title'])
        self.body.addWidget(self.source_name)
        self.body.addWidget(label('儲存位置', 'Subtle'))
        self.location = QLineEdit(str(self.destination.path))
        self.location.setReadOnly(True)
        self.location.setAccessibleName('儲存位置')
        self.location.setToolTip(str(self.destination.path))
        self.location.setCursorPosition(0)
        self.location_button = button('選擇位置…', self.choose_destination)
        self.body.addLayout(row(self.location, self.location_button))
        self.status = label('正在讀取…', 'Subtle', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.status)
        self.cancel_button = button('取消', self.reject)
        self.import_button = button('儲存', self.submit, 'Primary')
        self.body.addLayout(row(None, self.cancel_button, self.import_button))
        self.start_job(lambda cancel, progress: load_archive(record['source'], cancel, progress), 'read')

    def choose_destination(self):
        path = QFileDialog.getExistingDirectory(self, '選擇儲存位置', str(self.destination.path))
        if not path:
            return
        try:
            self.service.check_isolation(path)
            self.destination = Destination.choose(path)
        except (ValueError, OSError) as error:
            self.show_status(str(error))
            return
        self.location.setText(str(self.destination.path))
        self.location.setToolTip(str(self.destination.path))
        self.location.setCursorPosition(0)

    def start_job(self, function, kind):
        if self.job is not None:
            raise ValueError('正在處理這份對話。')
        self.job = ImportJob(function)
        self.job_kind = kind
        self.job.signals.progress.connect(self.show_status)
        self.job.signals.finished.connect(self.job_finished)
        self.destroyed.connect(lambda _=None, flag=self.job.cancel: flag.set())
        self.location_button.setEnabled(False)
        self.import_button.setEnabled(False)
        self.show_status('正在讀取…' if kind == 'read' else '儲存中…')
        QThreadPool.globalInstance().start(self.job)

    @Slot(str)
    def show_status(self, text):
        self.status.setText(text)

    @Slot(object, str)
    def job_finished(self, result, error):
        kind = self.job_kind
        self.job = None
        if kind == 'write':
            self.service.write_finished(self.operation)
        if kind == 'read' and not error and not self.close_when_done:
            self.archive = result
            self.status.clear()
            self.location_button.setEnabled(True)
            self.import_button.setEnabled(True)
            self.import_button.setFocus()
        elif kind == 'write':
            record = self.service.store.get(self.operation) or {}
            if record.get('state') == 'committed':
                if isValid(self.workspace) and not self.workspace.layout_store.stopped:
                    target = self.service.store.saved_path(self.operation)
                    if self.workspace.locations.root_for(target) is None:
                        self.workspace.add_root(self.destination.path)
                    self.workspace.refresh_tree()
                self.accept()
                return
            if not error and isinstance(result, ImportResult):
                error = result.failed[0][2] if result.failed else '儲存未完成。'
            self.show_status(error or '無法確認儲存結果，請稍後從最近保存查看。')
            self.cancel_button.setText('關閉')
        elif error:
            self.show_status(error)
            self.cancel_button.setText('關閉')
        if self.close_when_done:
            super().reject()

    def submit(self):
        if self.job is not None or self.archive is None:
            return
        try:
            self.service.commit(self)
        except (ValueError, OSError) as error:
            self.show_status(str(error))

    def reject(self):
        if self.job is not None:
            self.close_when_done = True
            self.job.cancel.set()
            if self.job_kind != 'write':
                self.service.store.cancel(self.operation)
                self.service.changed.emit()
            self.hide()
            return
        self.service.store.cancel(self.operation)
        self.service.changed.emit()
        super().reject()

    def stop_for_workspace_close(self):
        self.reject()

    def closeEvent(self, event):
        if self.job is not None:
            event.ignore()
            self.reject()
            return
        super().closeEvent(event)
