"""Captured file commands shared by the tree, toolbar and new-tab page."""
from pathlib import Path
from PySide6.QtCore import Qt, QFile
from PySide6.QtWidgets import QApplication, QLineEdit, QHBoxLayout, QFileDialog, QDialog, QDialogButtonBox, QMessageBox

from .widgets import StudioDialog, button, label, dialog_buttons, RoundMenu
from .workspace_files import create_file
from .workspace_locations import CommandContext


PATH_ROLE = Qt.ItemDataRole.UserRole + 1
KIND_ROLE = Qt.ItemDataRole.UserRole + 2
ROOT_ROLE = Qt.ItemDataRole.UserRole + 3
NAMES = dict(category='prompt管理頁', workflow='工作流', markdown='文字文件', folder='資料夾')


class NewEntryDialog(StudioDialog):
    def __init__(self, parent, kind, destination, create):
        super().__init__(parent)
        self.setWindowTitle('新增' + NAMES[kind])
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
        from .workspace_file_operations import WorkspaceFileOperations
        self.files = WorkspaceFileOperations(workspace)

    def context(self, origin, item=None, tab=None):
        w = self.workspace
        if origin == 'home':
            item = self.visible_selection()
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
        destination = path if kind in ('root', 'folder') else path.parent if path else root
        document = tab.document if tab else None
        return CommandContext(origin, path, kind, root, destination,
                              path.parent if path and kind not in ('root', 'folder') else None,
                              tab.key if tab else None, document.key if document else None,
                              w.active_pane.key, w.active_key)

    def selected_context(self, origin='toolbar'):
        return self.context(origin, self.visible_selection())

    def visible_selection(self):
        tree = self.workspace.tree
        if not tree.isVisible():
            return None
        item = tree.currentItem()
        parent = item.parent() if item else None
        while parent:
            if not parent.isExpanded() or parent.isHidden():
                return None
            parent = parent.parent()
        return item if item and not item.isHidden() else None

    def create_at(self, kind, name, directory):
        parent = self.workspace.locations.directory(directory)
        return create_file(parent, kind, name)

    def new(self, kind, context, name=None, directory=None):
        w = self.workspace
        if name is None:
            from .workspace_creation import start_creation
            return start_creation(self, kind, context)
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
        return w.open_context(path, context)

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
            menu.addAction('新增' + NAMES[kind] + ('…' if kind == 'folder' else ''),
                           lambda checked=False, k=kind: self.new(k, context))

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
        menu = self.menu_for(context)
        menu.open_at(w.tree.viewport().mapToGlobal(point))

    def menu_for(self, context):
        w = self.workspace
        menu = RoundMenu(w)
        if context.kind in ('root', 'folder', ''):
            self.add_creation_actions(menu.addMenu('新增'), context)
            if context.kind == 'folder':
                menu.addSeparator()
                self.files.add_actions(menu, context)
            menu.addSeparator()
            if context.path:
                menu.addAction('在檔案總管開啟', lambda: w.show_location(context.path))
            if context.kind in ('root', ''):
                from .workspace_menu_action import trailing_action
                trailing_action(menu, w, '重新整理', 'update', w.refresh_tree)
                if context.kind == '':
                    menu.addAction('加入本機資料夾…', w.choose_root)
                if context.kind == 'root':
                    menu.addAction('移除側欄連結（保留原檔）', lambda: w.remove_root(context.path))
        else:
            tab = w.tabs_by_key.get(context.tab_key)
            title = '切換至已開分頁' if tab else '在新分頁開啟'
            menu.addAction(title, lambda: w.open_path(context.path, True, new_tab=True))
            menu.addSeparator()
            self.files.add_actions(menu, context)
            menu.addSeparator()
            menu.addAction('在檔案總管中顯示', lambda: w.show_location(context.path.parent))
        if context.path:
            menu.addSeparator()
            menu.addAction('複製路徑', lambda: QApplication.clipboard().setText(str(context.path)))
            if context.kind != 'root':
                from .ui_icons import icon
                from .theme import visual_tokens
                action = menu.addAction(icon('trash', visual_tokens(w.display_settings())['destructive_text']),
                                        '移至資源回收筒…', lambda: self.trash(context))
                action.setEnabled(not self.files.busy(context.path))
        return menu

    def trash(self, context):
        w, path = self.workspace, context.path
        if path is None:
            return False
        # Linked roots have a separate unlink command. Do not trash a folder
        # containing another enrolled root or follow a replaced symlink.
        from .local_documents import paths
        try:
            path = paths.checked(path.parent, path.name)
            if any(root == path or path in root.parents for root in w.locations.roots):
                raise ValueError('這個位置包含側欄根資料夾；請使用「移除側欄連結」。')
            if not path.exists():
                raise ValueError('這個項目已不存在，請重新整理側欄。')
        except (OSError, ValueError) as error:
            w.report_error(str(error))
            return False
        affected = [tab for tab in w.all_pages()
                    if tab.path and (tab.path == path or path in tab.path.parents)]
        if any(not w.prepare_close(tab, decision='cancel') for tab in affected):
            w.report_error('這個項目有未保存的變更；請先保存或關閉相關分頁，再移至資源回收筒。')
            return False
        op = None
        skip = False
        try:
            op = self.files.begin(context)
            if w.confirm_trash:
                from .workspace_file_dialogs import TrashDialog
                prompt = TrashDialog(w, path)
                if prompt.exec() != QDialog.DialogCode.Accepted:
                    return False
                skip = prompt.skip.isChecked()
            self.files.check_source(op)
            moved = QFile.moveToTrash(str(path))
            # Current PySide returns bool when no trash-path output is requested;
            # older bindings may return (success, path).
            if isinstance(moved, tuple):
                moved = moved[0]
            if not moved:
                raise ValueError('無法移至資源回收筒；檔案與分頁均已保留。')
        except (OSError, ValueError) as error:
            w.report_error(str(error))
            return False
        finally:
            if op is not None:
                self.files.end(op)
        if skip:
            w.set_trash_confirmation(False)
        for tab in affected:
            at = w.index_of(tab.key)
            if at >= 0:
                w.close_tab(at, decision='discard', pane=w.pane_for(tab))
            elif tab.key in w.saved_pages:
                w.dispose_page(tab)
                w.saved_pages.pop(tab.key)
        w.recent_files = [p for p in w.recent_files if Path(p) != path and path not in Path(p).parents]
        w.save_recent()
        from .workspace_home import WorkspaceHome
        for tab in w.tabs_by_key.values():
            if isinstance(tab.widget, WorkspaceHome):
                tab.widget.set_recent(w.recent_files)
        w.refresh_tree()
        return True
