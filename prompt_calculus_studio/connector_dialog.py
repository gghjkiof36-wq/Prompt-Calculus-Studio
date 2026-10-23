"""External capture presented through the existing conversation import sheet."""
from PySide6.QtCore import Qt, Slot

from .conversation_dialog import ConversationImportDialog
from .conversation_import import ImportResult
from .widgets import button


class ConnectorImportDialog(ConversationImportDialog):
    def __init__(self, service, record):
        self.service = service
        self.operation = record['operation_id']
        self.capture_title = record['title']
        super().__init__(service.workspace, service.settings['destination'])
        self.setWindowTitle('從 Chrome 匯入對話')
        self.arrange_button = button('開始整理', self.arrange, 'Primary')
        self.footer.layout().insertWidget(self.footer.layout().count() - 1, self.arrange_button)
        self.arrange_button.hide()
        self.load_source(record['source'])

    def show_contents(self, loaded):
        super().show_contents(loaded)
        if loaded:
            self.source_button.hide()
            self.source_name.setText(self.capture_title)
            self.source_name.setToolTip(self.capture_title)
            self.search.hide()
            self.split.widget(0).hide()
            self.branches.hide()
            self.count.hide()

    def update_selection(self, *_):
        super().update_selection()
        self.count.hide()
        if not self.completed:
            self.import_button.setText('匯入對話')

    @Slot(object, str)
    def job_finished(self, result, error):
        kind = self.job_kind
        if kind == 'write':
            # This bound UI slot is connected before the worker starts.
            self.service.write_finished(self.operation)
        super().job_finished(result, error)
        if kind == 'read' and self.archive:
            self.check_visible(True)
            self.import_button.setFocus()
        if isinstance(result, ImportResult) and (result.completed or result.skipped):
            self.arrange_button.show()
            self.arrange_button.setFocus()
            self.import_button.setObjectName('Quiet')
            self.import_button.style().unpolish(self.import_button)
            self.import_button.style().polish(self.import_button)
            self.import_button.setText('關閉')
            self.source_button.hide()
            self.split.hide()
            self.location_row.hide()
            self.show_status('已保存' if result.completed else '這份對話已保存')
            self.setMinimumSize(480, 210)
            self.resize(600, 210)

    def submit(self):
        if self.completed:
            return self.accept()
        if self.job or not self.archive:
            return
        try:
            self.service.commit(self)
        except (ValueError, OSError) as error:
            self.show_status(str(error))

    def arrange(self):
        try:
            tab = self.service.open_saved(self.operation)
            if tab is not None:
                from .conversation_review_dialog import open_review
                open_review(tab.widget.md_surface)
                self.accept()
        except (ValueError, OSError) as error:
            self.show_status(str(error))

    def reject(self):
        if not self.job or self.job_kind != 'write':
            self.service.store.cancel(self.operation)
            self.service.changed.emit()
        super().reject()
