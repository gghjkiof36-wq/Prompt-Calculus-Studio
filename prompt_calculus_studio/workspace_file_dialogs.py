"""Compact file forms and a cancellable, IME-aware sidebar name editor."""
from pathlib import Path
from PySide6.QtCore import Qt, QEvent, QTimer, QSize
from PySide6.QtWidgets import (QApplication, QLineEdit, QWidget, QVBoxLayout,
    QTreeWidget, QTreeWidgetItem, QDialog, QDialogButtonBox)
from .widgets import StudioDialog, label, button, dialog_buttons
from .workspace_commands import NewEntryDialog
from .workspace_file_io import named_path
from .workspace_sidebar import WorkspaceTree
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
        box = QVBoxLayout(self)
        box.setContentsMargins(6, 0, 4, 0)
        box.setSpacing(2)
        self.name = NameEdit(source.stem if source.is_file() else source.name)
        self.name.setAccessibleName('重新命名「' + source.name + '」')
        box.addWidget(self.name)
        self.error = label('', 'Subtle', True)
        self.error.hide()
        box.addWidget(self.error)
        self.name.installEventFilter(self)
        self.name.returnPressed.connect(self.submit)
        item.setText(0, '')
        tree.setItemWidget(item, 0, self)
        item.setSizeHint(0, QSize(0, 36))
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
            self.item.setSizeHint(0, QSize(0, 82))
            self.name.setFocus()
            return
        self.cancel()

    def cancel(self):
        if self.done:
            return
        self.done = True
        self.item.setText(0, self.original_text)
        self.tree.removeItemWidget(self.item, 0)
        self.item.setSizeHint(0, QSize())
        self.finish()


class MoveDialog(StudioDialog):
    def __init__(self, workspace, source, root, commit):
        super().__init__(workspace)
        self.workspace, self.source, self.root, self.commit = workspace, source, root, commit
        self.setWindowTitle('移動到')
        self.resize(580, 520)
        self.body.addWidget(label(str(source), 'Subtle', True))
        self.tree = WorkspaceTree(workspace)
        self.tree.setObjectName('MoveDestinationTree')
        self.tree.setStyleSheet('QTreeWidget#MoveDestinationTree::item {height:32px; padding:0 5px;}')
        self.tree.setIconSize(QSize(15, 15))
        self.tree.setIndentation(15)
        self.tree.setUniformRowHeights(True)
        self.tree.setHeaderHidden(True)
        self.tree.setAccessibleName('目的資料夾')
        self.body.addWidget(self.tree, 1)
        self.destination = label('', 'Subtle', True)
        self.destination.setAccessibleName('移動後完整位置')
        self.body.addWidget(self.destination)
        self.error = label('', 'Subtle', True)
        self.error.hide()
        self.body.addWidget(self.error)
        self.new_folder = button('新增資料夾…', self.create_folder, 'Quiet')
        self.body.addWidget(self.new_folder)
        self.buttons = dialog_buttons(self, self.submit)
        self.move_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.move_button.setText('移動')
        self.body.addWidget(self.buttons)
        self.tree.currentItemChanged.connect(self.update_destination)
        self.populate(source.parent)

    def populate(self, selected):
        self.tree.clear()
        nodes = {}
        for path in [self.root, *(p for p, k in self.workspace.locations.inventory(self.root) if k == 'folder')]:
            if path == self.source or self.source in path.parents:
                continue
            item = QTreeWidgetItem([path.name])
            item.setIcon(0, icon('folder'))
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
            item.setToolTip(0, str(path))
            if path.parent in nodes:
                nodes[path.parent].addChild(item)
            else:
                self.tree.addTopLevelItem(item)
            nodes[path] = item
            item.setExpanded(True)
        self.tree.setCurrentItem(nodes.get(selected, nodes.get(self.root)))
        self.update_destination()

    def selected(self):
        item = self.tree.currentItem()
        return Path(item.data(0, Qt.ItemDataRole.UserRole)) if item else None

    def update_destination(self, *_):
        selected = self.selected()
        self.destination.setText(str(selected/self.source.name) if selected else '')
        same = selected == self.source.parent
        self.move_button.setEnabled(selected is not None and not same)
        self.move_button.setToolTip('位置相同' if same else '')

    def create_folder(self):
        selected = self.selected()
        dialog = NewEntryDialog(self, 'folder', selected,
            lambda name, directory: self.create_destination(name, directory))
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.populate(dialog.created_path)

    def create_destination(self, name, directory):
        parent = self.workspace.locations.directory(directory)
        if parent != self.root and self.root not in parent.parents:
            raise ValueError('請在目前根資料夾內建立目的資料夾。')
        if parent == self.source or self.source in parent.parents:
            raise ValueError('不能將目的資料夾建在正在移動的資料夾內。')
        return self.workspace.commands.create_at('folder', name, parent)

    def submit(self):
        try:
            self.commit(self.selected())
        except (ValueError, OSError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.accept()
