"""Captured file commands shared by the tree, toolbar and new-tab page."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QLineEdit, QHBoxLayout, QFileDialog, QDialog, QDialogButtonBox, QMessageBox

from .widgets import StudioDialog, button, label, dialog_buttons, RoundMenu
from .workspace_files import create_file
from .workspace_locations import CommandContext


PATH_ROLE = Qt.ItemDataRole.UserRole + 1
KIND_ROLE = Qt.ItemDataRole.UserRole + 2
ROOT_ROLE = Qt.ItemDataRole.UserRole + 3
NAMES = dict(category='分類', workflow='Canvas 工作流', markdown='Markdown 筆記', folder='資料夾')


class NewEntryDialog(StudioDialog):
    def __init__(self, parent, kind, destination, create):
        super().__init__(parent)
        self.setWindowTitle('新增 ' + NAMES[kind])
        self.resize(640, 410)
        self.created_path = None
        self.create = create
        self.name = QLineEdit()
        self.name.setAccessibleName('名稱')
        self.name.setPlaceholderText('輸入名稱')
        self.body.addWidget(label('名稱'))
        self.body.addWidget(self.name)
        self.directory = QLineEdit(str(destination) if destination else '')
        self.directory.setAccessibleName('儲存位置')
        self.directory.setPlaceholderText('選擇資料夾或輸入完整路徑')
        self.body.addWidget(label('儲存位置'))
        location = QHBoxLayout()
        location.addWidget(self.directory, 1)
        self.browse = button('選擇…', self.choose_directory, 'Quiet')
        self.browse.setAccessibleName('選擇儲存資料夾')
        location.addWidget(self.browse)
        self.body.addLayout(location)
        self.full_location = label(self.directory.text(), 'Subtle', True)
        self.full_location.setTextFormat(Qt.TextFormat.PlainText)
        self.full_location.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.full_location.setAccessibleName('完整儲存位置')
        self.directory.textChanged.connect(self.full_location.setText)
        self.body.addWidget(self.full_location)
        self.error = label('', 'Subtle', True)
        self.error.setAccessibleName('新增結果')
        self.error.hide()
        self.body.addWidget(self.error)
        self.buttons = dialog_buttons(self, self.submit)
        self.submit_button = self.buttons.button(QDialogButtonBox.StandardButton.Save)
        self.submit_button.setText('建立')
        self.submit_button.setDefault(True)
        self.body.addWidget(self.buttons)
        self.name.setFocus()

    def choose_directory(self):
        value = QFileDialog.getExistingDirectory(self, '選擇儲存位置', self.directory.text())
        if value:
            self.directory.setText(value)

    def submit(self):
        try:
            self.created_path = self.create(self.name.text(), self.directory.text())
        except (ValueError, OSError) as error:
            self.error.setText(str(error))
            self.error.show()
            return
        self.accept()


class WorkspaceCommands:
    def __init__(self, workspace):
        self.workspace = workspace

    def context(self, origin, item=None, tab=None):
        w = self.workspace
        path = tab.path if tab else None
        kind = 'file' if path else ''
        root = w.locations.root_for(path) if path else w.locations.current
        if item is not None:
            value = item.data(0, PATH_ROLE)
            path = Path(value) if value else None
            kind = item.data(0, KIND_ROLE) or ''
            value = item.data(0, ROOT_ROLE)
            root = Path(value) if value else root
            tab = w.tab_for_path(path) if path and kind not in ('root', 'folder') else None
        # New-tab actions deliberately ignore the last selected child folder.
        if origin == 'home':
            path, kind = None, ''
            root = w.locations.current
        destination = path if kind in ('root', 'folder') else path.parent if path else root
        document = tab.document if tab else None
        return CommandContext(origin, path, kind, root, destination,
                              path.parent if path and kind not in ('root', 'folder') else None,
                              tab.key if tab else None, document.key if document else None)

    def selected_context(self, origin='toolbar'):
        return self.context(origin, self.workspace.tree.currentItem())

    def create_at(self, kind, name, directory):
        parent = self.workspace.locations.directory(directory)
        return create_file(parent, kind, name)

    def new(self, kind, context, name=None, directory=None):
        w = self.workspace
        if name is None:
            dialog = NewEntryDialog(w, kind, context.destination,
                                    lambda n, p: self.create_at(kind, n, p))
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return None
            path = dialog.created_path
        else:
            try:
                path = self.create_at(kind, name, directory if directory is not None else context.destination)
            except (ValueError, OSError) as error:
                w.report_error(str(error))
                return None
        w.refresh_tree()
        if kind == 'folder':
            if not w.select_path(path):
                self.offer_folder_link(path)
            return path
        return w.open_path(path, True)

    def offer_folder_link(self, path):
        prompt = QMessageBox(self.workspace)
        prompt.setWindowTitle('資料夾已建立')
        prompt.setText('已建立於：\n' + str(path))
        prompt.setInformativeText('將這個資料夾加入側欄，即可在這裡繼續整理內容。')
        add = prompt.addButton('加入側欄並定位', QMessageBox.ButtonRole.AcceptRole)
        prompt.addButton('完成', QMessageBox.ButtonRole.RejectRole)
        prompt.exec()
        if prompt.clickedButton() is add:
            self.workspace.add_root(path)

    def add_creation_actions(self, menu, context, folders=True):
        for kind in ('category', 'workflow', 'markdown', 'folder') if folders else ('category', 'workflow', 'markdown'):
            menu.addAction(NAMES[kind] + '…', lambda checked=False, k=kind: self.new(k, context))

    def creation_menu(self):
        w = self.workspace
        context = self.selected_context()
        menu = RoundMenu(w)
        self.add_creation_actions(menu, context, folders=False)
        menu.open_at(w.tree_new.mapToGlobal(w.tree_new.rect().bottomLeft()))

    def tree_menu(self, point):
        w = self.workspace
        item = w.tree.itemAt(point)
        w.tree.setCurrentItem(item)
        context = self.context('tree', item)
        menu = RoundMenu(w)
        if context.kind in ('root', 'folder', ''):
            self.add_creation_actions(menu.addMenu('新增'), context)
            if context.path:
                menu.addAction('在檔案總管開啟', lambda: w.show_location(context.path))
            if context.kind in ('root', ''):
                menu.addAction('重新整理', w.refresh_tree)
                menu.addAction('加入本機資料夾…', w.choose_root)
                if context.kind == 'root':
                    menu.addAction('移除側欄連結（保留原檔）', lambda: w.remove_root(context.path))
        else:
            tab = w.tabs_by_key.get(context.tab_key)
            title = '切換至已開分頁' if tab else '在新分頁開啟'
            menu.addAction(title, lambda: w.open_path(context.path, True))
            if tab:
                menu.addAction('保存文件', lambda: w.save_tab(tab))
            menu.addAction('顯示所在資料夾', lambda: w.show_location(context.path.parent))
        menu.open_at(w.tree.viewport().mapToGlobal(point))
