"""Independent window backed by the existing Markdown editor and reading surface."""
from PySide6.QtCore import Qt, QEvent
from PySide6.QtWidgets import QPushButton, QMessageBox
from ..widgets import StudioDialog, button, label
from ..workspace_files import MarkdownPage
from .navigation import purpose, PURPOSES
from .reader import line


class MarkdownPreview(StudioDialog):
    def __init__(self, parent, path, raw, *, editable=False, open_home=None, copy=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(path.name)
        self.resize(940, 740)
        self.setMinimumSize(520, 440)
        self.editor = MarkdownPage(path)
        if self.editor.baseline != raw:
            self.editor.deleteLater()
            raise ValueError('文件剛被更改，請重新整理後開啟。')
        self.editor.file_title.setEnabled(False)
        self.surface = self.editor.md_surface
        self.surface.apply_mode('read', focus=False)
        self.surface.mode_box.hide()
        self.surface.view_tools.hide()
        if not editable:
            self.editor.save = lambda: False
            self.surface.editor.setReadOnly(True)
        role = purpose(path)
        self.info = label(('管理副本 · ' if editable else '') + PURPOSES[role] + ' · ' + str(path.parent), 'Muted', True)
        self.info.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.info)
        self.body.addWidget(self.editor, 1)
        self.feedback = label('', 'Muted', True)
        self.feedback.setTextFormat(Qt.TextFormat.PlainText)
        self.body.addWidget(self.feedback)
        options = []
        if editable:
            options += [button('編輯副本', lambda: self.surface.apply_mode('write'), 'Quiet'),
                        button('儲存', self.save, 'Primary')]
        elif copy:
            options.append(button('建立管理副本' if role != 'document' else '在首頁開啟副本', copy, 'Quiet'))
        if open_home:
            options.append(button('在首頁開啟', open_home, 'Quiet'))
        if options:
            line(self.body, None, *options)
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)
        self.editable = editable

    def save(self):
        saved = self.editor.save()
        self.feedback.setText('已儲存' if saved else self.editor.error)
        return saved

    def reject(self):
        if self.editable and self.editor.dirty:
            prompt = QMessageBox(self)
            prompt.setWindowTitle('尚未儲存')
            prompt.setText('是否儲存管理副本的變更？')
            save = prompt.addButton('儲存', QMessageBox.ButtonRole.AcceptRole)
            discard = prompt.addButton('捨棄變更', QMessageBox.ButtonRole.DestructiveRole)
            prompt.addButton('取消', QMessageBox.ButtonRole.RejectRole)
            prompt.exec()
            if prompt.clickedButton() == save:
                if not self.save():
                    return
            elif prompt.clickedButton() != discard:
                return
        super().reject()

    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
        else:
            super().keyPressEvent(event)
