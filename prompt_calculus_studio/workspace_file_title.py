"""The visible filename is also its inline rename entrance."""
from PySide6.QtCore import Qt, QEvent, QTimer, Signal
from .widgets import ElidedLabel
from .workspace_file_dialogs import NameEdit


class FileTitle(ElidedLabel):
    filenameChanged = Signal(str)
    def __init__(self, text='', parent=None):
        super().__init__(text, parent)
        self.setObjectName('DialogTitle')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.workspace = self.tab = self.name = None
        self.done = True
        self.setToolTip('重新命名')

    def bind(self, workspace, tab):
        self.workspace, self.tab = workspace, tab
        self.setText(tab.title)

    def setText(self, text):
        # Category names inside the document are independent of its filename.
        tab = getattr(self, 'tab', None)
        value = tab.path.stem if tab and tab.path else text
        super().setText(value)
        self.setAccessibleName('檔案名稱：'+value+'；按 Enter 重新命名')
        self.filenameChanged.emit(value)

    def request_rename(self):
        if self.workspace and self.tab and self.done:
            self.workspace.commands.files.run('rename', self.workspace.commands.context('page', tab=self.tab))

    def paintEvent(self, event):
        # The inline editor owns the text while renaming, even with a clear fill.
        if getattr(self, 'done', True):super().paintEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.request_rename()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F2):
            self.request_rename()
            event.accept()
        else:
            super().keyPressEvent(event)

    def start(self, source, commit, finish):
        self.commit, self.finish, self.done = commit, finish, False
        self.name = NameEdit(source.stem, self)
        self.name.setAccessibleName('檔案名稱')
        self._resting_minimum = self.minimumSize()
        self.name.ensurePolished()
        self.setMinimumSize(max(220,self.minimumWidth()),
                            max(self.minimumHeight(),self.name.sizeHint().height()))
        self.name.setGeometry(self.rect())
        self.name.installEventFilter(self)
        self.name.returnPressed.connect(self.submit)
        self.name.show()
        self.update()
        self.name.setFocus()
        self.name.selectAll()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.name is not None:
            self.name.setGeometry(self.rect())

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            # Consume the committing key even when submit hides the editor.
            # Otherwise it can bubble to the heading and begin a second rename.
            self.submit()
            return True
        if event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self, self.finish_unfocused)
        return super().eventFilter(watched, event)

    def finish_unfocused(self):
        if not self.done and not self.name.hasFocus():
            # Clicking away commits a valid name; invalid names keep the file
            # unchanged and restore the heading. Enter allows correction.
            self.submit(cancel_invalid=True)

    def submit(self, *, cancel_invalid=False):
        if self.done or self.name.composing:
            return
        try:
            self.commit(self.name.text())
        except (ValueError, OSError) as error:
            self.workspace.show_notice(str(error))
            if not cancel_invalid:
                self.name.setFocus()
                return
        self.cancel()

    def cancel(self):
        if self.done:
            return
        self.done = True
        editor, self.name = self.name, None
        editor.hide()
        editor.deleteLater()
        self.setMinimumSize(self._resting_minimum)
        self.finish()
        self.setText(self.tab.title)


def title_for(tab):
    if tab.document and tab.document.kind == 'category':
        return tab.host.prompt_editor.title
    return getattr(tab.widget, 'file_title', None)
