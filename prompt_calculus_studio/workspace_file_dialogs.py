"""Compact file forms and a cancellable, IME-aware sidebar name editor."""
from pathlib import Path
from PySide6.QtCore import Qt, QEvent, QTimer, QSize
from PySide6.QtWidgets import (QApplication, QLineEdit, QWidget, QVBoxLayout, QSizePolicy,
    QHBoxLayout, QDialogButtonBox)
from .widgets import StudioDialog, label, button, dialog_buttons
from .workspace_commands import NewEntryDialog
from .workspace_file_io import named_path
from .ui_icons import icon


class NameEdit(QLineEdit):
    def __init__(self, value='', parent=None):
        super().__init__(value, parent)
        self.composing = False

    def inputMethodEvent(self, event):
        self.composing = bool(event.preeditString())
        super().inputMethodEvent(event)

    def keyPressEvent(self, event):
        if self.composing and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            event.accept()
            return
        super().keyPressEvent(event)


class SaveAsDialog(NewEntryDialog):
    def __init__(self, parent, kind, source, commit):
        super().__init__(parent, kind, source.parent, commit)
        self.setWindowTitle('另存新檔')
        self.submit_button.setText('另存')
        self.name.setText(source.stem + ' 副本')
        self.name.selectAll()
        self.source = source
        self.name.textChanged.connect(self.update_destination)
        self.directory.textChanged.connect(self.update_destination)
        self.update_destination()

    def update_destination(self):
        name = self.name.text()
        if not name.casefold().endswith(self.source.suffix.casefold()):
            name += self.source.suffix
        self.full_location.setText(str(Path(self.directory.text())/name))
        self.full_location.setAccessibleName('另存後完整位置')


class RenameDialog(StudioDialog):
    def __init__(self, parent, source, commit):
        super().__init__(parent)
        self.setWindowTitle('重新命名')
        self.resize(480, 250)
        self.source, self.commit = source, commit
        self.body.addWidget(label(str(source), 'Subtle', True))
        self.name = NameEdit(source.stem if source.is_file() else source.name)
        self.name.setAccessibleName('新名稱')
        self.body.addWidget(self.name)
        self.error = label('', 'Subtle', True)
        self.error.hide()
        self.body.addWidget(self.error)
        self.buttons = dialog_buttons(self, self.submit)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('改名')
        self.body.addWidget(self.buttons)
        self.name.returnPressed.connect(self.submit)
        self.name.selectAll()
        self.name.setFocus()

    def submit(self):
        if self.name.composing:
            return
        try:
            self.commit(self.name.text())
        except (ValueError, OSError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.accept()


class InlineRename(QWidget):
    def __init__(self, tree, item, source, commit, finish):
        super().__init__(tree)
        self.tree, self.item = tree, item
        self.original_text = item.text(0)
        self.commit, self.finish = commit, finish
        self.done = False
        self.uniform_rows = tree.uniformRowHeights()
        tree.setUniformRowHeights(False)
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 4, 0)
        box.setSpacing(2)
        self.name = NameEdit(source.stem if source.is_file() else source.name)
        self.name.setAccessibleName('重新命名「' + source.name + '」')
        box.addWidget(self.name)
        self.error = label('', 'Subtle', True)
        self.error.hide()
        box.addWidget(self.error)
        self.error.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.error.setTextFormat(Qt.TextFormat.PlainText)
        self.name.installEventFilter(self)
        self.name.returnPressed.connect(self.submit)
        item.setText(0, '')
        tree.setItemWidget(item, 0, self)
        self.resize_row()
        self.name.setFocus()
        self.name.selectAll()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
            self.cancel()
            return True
        if event.type() == QEvent.Type.FocusOut:
            QTimer.singleShot(0, self.cancel_if_unfocused)
        return super().eventFilter(watched, event)

    def cancel_if_unfocused(self):
        if not self.done and QApplication.focusWidget() is not self.name:
            self.cancel()

    def submit(self):
        if self.done or self.name.composing:
            return
        try:
            self.commit(self.name.text())
        except (ValueError, OSError) as error:
            self.error.setText(str(error))
            self.error.show()
            self.resize_row()
            self.name.setFocus()
            return
        self.cancel()

    def resize_row(self):
        if self.done:
            return
        self.name.ensurePolished()
        height = max(32, self.name.sizeHint().height())
        self.name.setFixedHeight(height)
        if not self.error.isHidden():
            width = max(40, self.width()-10)
            height += max(self.error.fontMetrics().lineSpacing(), self.error.heightForWidth(width))+2
        # The tree can grow the row before updating its index widget. Keep the
        # editor tall enough for both controls throughout that geometry change.
        self.setMinimumHeight(height)
        hint = QSize(0, height+4)
        if self.item.sizeHint(0) != hint:
            self.item.setSizeHint(0, hint)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.resize_row()

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.item.setText(0, self.original_text)
        self.tree.removeItemWidget(self.item, 0)
        self.item.setSizeHint(0, QSize())
        self.tree.setUniformRowHeights(self.uniform_rows)
        self.finish()
