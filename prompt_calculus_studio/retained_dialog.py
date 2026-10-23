"""Nonmodal, cancellable file work scoped to an originating Markdown view."""
from pathlib import Path
from PySide6.QtCore import Qt, QThreadPool, Slot, QEvent
from PySide6.QtWidgets import QPlainTextEdit
from shiboken6 import isValid
from .conversation_dialog import ImportJob
from .markdown_attachments import root_for
from .widgets import StudioDialog, label, button, row, ActionHeader
from .retained_files import directory_identity


class RetainedSheet(StudioDialog):
    def __init__(self, surface, title):
        super().__init__(surface)
        self.surface = surface
        self.workspace = surface.page.file_title.workspace
        self.root = Path(root_for(surface)).absolute()
        self.root_identity = directory_identity(self.root)
        self.document = surface.page.path
        self.snapshot, self.start, self.end = surface.selection()
        self.mode = surface.mode
        self.job = None
        self.stopping = False
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(title)
        self.resize(720, 680)
        self.status = label('', 'Muted', wrap=True)
        self.cancel_button = button('關閉', self.reject)
        if self.workspace is not None:
            sheets = self.workspace.__dict__.setdefault('_retained_sheets', [])
            sheets.append(self)
            self.destroyed.connect(lambda: sheets.remove(self) if self in sheets else None)

    def current(self, editing=False):
        if not isValid(self.surface) or not isValid(self.surface.page):
            return False
        if self.workspace is not None and (not isValid(self.workspace) or self.workspace.layout_store.stopped):
            return False
        try: same_root = directory_identity(self.root) == self.root_identity
        except (ValueError,OSError):same_root = False
        return (same_root and self.document == self.surface.page.path and self.root == Path(root_for(self.surface)).absolute()
                and self.snapshot == self.surface.page.buffer.snapshot()
                and (not editing or (self.surface.mode != 'read' and not self.surface.page.buffer.composing
                                     and self.surface.page.isVisible())))

    def require_current(self, editing=False):
        if not self.current(editing):
            raise ValueError('原文件、位置或編輯狀態已改變。請回到文件重新開啟此操作；原文未修改。')

    def changeEvent(self, event):
        super().changeEvent(event)
        workspace = getattr(self, 'workspace', None)
        if workspace is not None and event.type() == QEvent.Type.StyleChange:
            from .theme import visual_tokens, font_pixels
            from .module_chrome import apply_module_dialog
            settings = workspace.display_settings()
            context = (visual_tokens(settings), max(16, font_pixels(settings['ui_size'])))
            if getattr(self, '_module_dialog_context', None) != context:
                apply_module_dialog(self, *context)

    def finish_layout(self, *actions):
        self.body.addWidget(self.status)
        self.body.addWidget(ActionHeader(self.cancel_button, *actions))

    def run(self, function, done):
        if self.job:
            return
        self.job = ImportJob(function)
        self.callback = done
        self.stopping = False
        self.job.signals.progress.connect(self.status.setText)
        self.job.signals.finished.connect(self.finished_job)
        self.destroyed.connect(lambda _=None, flag=self.job.cancel: flag.set())
        self.cancel_button.setText('取消處理')
        self.set_busy(True)
        QThreadPool.globalInstance().start(self.job)

    def set_busy(self, busy):
        pass

    @Slot(object, str)
    def finished_job(self, result, error):
        self.job = None
        self.set_busy(False)
        self.cancel_button.setText('關閉')
        if self.stopping:
            super().reject()
            return
        if error:
            self.status.setText(error)
            return
        try:
            self.callback(result)
        except (ValueError, OSError, KeyError, TypeError) as exc:
            self.status.setText(str(exc))

    def reject(self):
        if self.job:
            self.stopping = True
            self.job.cancel.set()
            self.status.setText('正在取消…')
            self.hide()
            return
        super().reject()

    def stop_for_workspace_close(self):
        self.reject()


def plain_editor(readonly=False):
    editor = QPlainTextEdit()
    editor.setReadOnly(readonly)
    editor.setMinimumSize(120, 100)
    return editor
