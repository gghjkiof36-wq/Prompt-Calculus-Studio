"""Explicit file operations, using the shared workspace owner and file transactions."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHeaderView, QLineEdit, QTreeWidget, QTreeWidgetItem

from ..job_outcomes import JobOutcome
from ..widgets import RoundMenu, StudioDialog, button
from . import bundles, navigation
from .reader import line, ResourceReader


class SourceActions:
    def menu(self, control, options, position=None, parent=None):
        popup = RoundMenu(parent or self)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for text, action in options:
            popup.addAction(text, action)
        self.popup = popup
        popup.open_at(position) if position is not None else popup.open_for(control)

    def add_menu(self):
        self.menu(self.add_button, [('加入資料夾', lambda: self.choose_source('folder')),
                                   ('匯入對話封存', lambda: self.choose_source('archive'))])

    def choose_source(self, kind, sid=None):
        value = (QFileDialog.getExistingDirectory(self, '選擇來源資料夾') if kind == 'folder' else
                 QFileDialog.getOpenFileName(self, '選擇來源檔案', '',
                     '對話封存 (*.zip *.json *.jsonl *.pcs-chat);;所有檔案 (*)')[0])
        if value:
            self.add_source(value, kind, sid)

    def add_source(self, path, kind, sid=None):
        if self.jobs.active:
            self.say('正在處理，請稍候。')
            return
        try:
            if sid:
                self.store.relink(sid, path)
            else:
                sid = self.store.register(path, kind)
            self.source_id = sid
            self.location = Path(path) if kind == 'folder' else None
            self.set_kind('all')
            self.filter.clear()
            self.rebuild(sid)
        except (ValueError, OSError) as error:
            self.say(str(error))

    def rebuild(self, sid):
        self.job('正在整理檔案…', lambda cancel: JobOutcome.completed(self.store.rebuild(sid, cancel)),
                 lambda _: self.reset_search())

    def manage_sources(self):
        dialog = StudioDialog(self)
        dialog.setWindowTitle('管理來源')
        dialog.resize(780, 540)
        search = QLineEdit()
        search.setPlaceholderText('搜尋名稱或路徑')
        search.setClearButtonEnabled(True)
        dialog.body.addWidget(search)
        listing = QTreeWidget()
        listing.setObjectName('ResourceList')
        listing.setRootIsDecorated(False)
        listing.setHeaderLabels(['名稱', '位置', '狀態'])
        listing.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        listing.setColumnWidth(0, 160)
        dialog.body.addWidget(listing, 1)
        def selected():
            item = listing.currentItem()
            return item.data(0, Qt.ItemDataRole.UserRole) if item else None
        def populate():
            listing.clear()
            query = search.text().casefold()
            for source in self.store.sources():
                if query not in source['path'].casefold():
                    continue
                path = Path(source['path'])
                item = QTreeWidgetItem([path.name, str(path.parent), source['state']])
                item.setData(0, Qt.ItemDataRole.UserRole, source)
                item.setToolTip(1, str(path))
                listing.addTopLevelItem(item)
            update_buttons()
        def browse():
            source = selected()
            if source:
                self.source_id = source['id']
                self.location = Path(source['path']) if source['kind'] == 'folder' else None
                self.set_kind('all')
                self.filter.clear()
                self.reset_search()
                dialog.accept()
        def operation(name):
            source = selected()
            if not source or self.jobs.active:
                return
            sid = source['id']
            if name == 'remove':
                # Detach only; retained originals and historic saved records remain on disk.
                self.store.remove(sid)
                self.picks = {key: pick for key, pick in self.picks.items() if pick['row']['source'] != sid}
                if self.source_id == sid:
                    self.source_id, self.location = '', None
                self.reset_search()
                self.update_selection()
                populate()
            else:
                dialog.accept()
                self.rebuild(sid) if name == 'scan' else self.choose_source(source['kind'], sid)
        view = button('查看檔案', browse, 'Primary')
        scan = button('重新掃描', lambda: operation('scan'), 'Quiet')
        relink = button('重新指定位置', lambda: operation('relink'), 'Quiet')
        remove = button('移出清單', lambda: operation('remove'), 'Quiet')
        def update_buttons(*_):
            source = selected()
            for control in (view, scan, relink, remove):
                control.setEnabled(source is not None)
            # Workspace roots remain owned by the existing Home folder manager.
            if source and Path(source['path']) in self.workspace.locations.roots:
                relink.setEnabled(False)
                remove.setEnabled(False)
            if source and source['kind'] in ('copy', 'definition'):
                relink.setEnabled(False)
        line(dialog.body, scan, relink, remove, None, view)
        search.textChanged.connect(populate)
        listing.itemSelectionChanged.connect(update_buttons)
        listing.itemActivated.connect(lambda *_: browse())
        populate()
        dialog.exec()

    def path_for(self, row):
        return Path(row['location']) if row['kind'] == 'folder' else navigation.document_path(self.store, row)

    def file_options(self, row):
        if row['kind'] == 'archive':
            return [('輸出 Markdown', lambda: self.export_rows([row])),
                    ('匯出封存原件', lambda: self.export_original(row))]
        path = self.path_for(row)
        options = []
        if row['kind'] == 'document' and path.suffix.lower() == '.md':
            options.append(('獨立視窗閱讀', lambda: self.open_document(row, separate=True)))
            if navigation.purpose(path) != 'document':
                if self.store.source(row['source'])['kind'] != 'definition':
                    options.append(('建立管理副本', lambda: self.copy(row)))
            elif self.workspace.locations.root_for(path) is None:
                options.append(('在首頁開啟副本', lambda: self.copy(row)))
        managed = self.workspace.locations.root_for(path)
        if managed and path != managed:
            options.extend([('重新命名', lambda: self.file_operation(row, 'rename')),
                            ('移動到…', lambda: self.file_operation(row, 'move'))])
        return options

    def file_menu(self, row, control, parent=None):
        self.menu(control, self.file_options(row), parent=parent)

    def file_operation(self, row, action, *, name=None, directory=None):
        from ..workspace_locations import CommandContext
        path = self.path_for(row)
        root = self.workspace.locations.root_for(path)
        if root is None or root == path:
            self.say('外部來源保留原件；請先建立工作區副本。')
            return None
        if action == 'move' and directory is None:
            value = QFileDialog.getExistingDirectory(self, '移動到資料夾', str(root))
            if not value:
                return None
            directory = Path(value)
        if directory is not None and self.workspace.locations.root_for(directory) is None:
            self.say('請選擇已加入首頁的工作區資料夾。')
            return None
        context = CommandContext('resources', path, 'folder' if path.is_dir() else 'file', root, path.parent)
        result = self.workspace.commands.files.run(action, context, name=name, directory=directory)
        if result and self.location and (self.location == path or path in self.location.parents):
            self.location = result / self.location.relative_to(path)
        self.refresh_list()
        return result

    def new_folder(self):
        from ..workspace_commands import NewEntryDialog
        destination = self.location or self.workspace.locations.current
        if not destination or self.workspace.locations.root_for(destination) is None:
            return
        def create(name, directory):
            if self.workspace.locations.root_for(Path(directory)) is None:
                raise ValueError('請選擇已加入首頁的工作區資料夾。')
            return self.workspace.commands.create_at('folder', name, directory)
        dialog = NewEntryDialog(self, 'folder', destination, create)
        if dialog.exec() and dialog.created_path:
            self.workspace.refresh_tree()
            self.enter_folder(dialog.created_path.parent, self.source_id)
            self.refresh_list()

    def open_home(self, path):
        if not self.close_reader():
            return
        tab = self.workspace.open_path(path, retained=True)
        surface = getattr(getattr(tab, 'widget', None), 'md_surface', None)
        if surface is not None:
            surface.apply_mode('read', focus=False)

    def open_document(self, row, *, separate=False):
        owner = self.ticket
        def done(result):
            if owner != self.ticket or not self.isVisible():
                return
            raw, path = result
            regular = navigation.purpose(path) == 'document'
            if not separate and regular and path.suffix.lower() == '.md' and self.workspace.locations.root_for(path):
                self.open_home(path)
                return
            if not self.close_reader():
                return
            if path.suffix.lower() == '.md':
                from .markdown_preview import MarkdownPreview
                editable = self.store.source(row['source'])['kind'] == 'definition'
                home = (lambda: self.open_home(path)) if regular and self.workspace.locations.root_for(path) else None
                self.reader = MarkdownPreview(self, path, raw, editable=editable, open_home=home,
                                              copy=None if home or editable else lambda: self.copy(row))
            else:
                self.reader = ResourceReader(self, row, result)
            self.reader.show()
        self.job('正在開啟文件…', lambda cancel: bundles.document(self.store, row, cancel), done)

    def copy(self, row):
        from .exports import copy_definition
        from ..retained_files import directory_identity
        definition = navigation.purpose(self.path_for(row)) != 'document'
        destination = self.workspace.locations.current
        if not definition and not destination:
            self.say('請先在首頁加入內容資料夾。')
            return
        try:
            expected = directory_identity(destination) if not definition else None
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        def done(result):
            path, sid = result
            self.workspace.refresh_tree()
            self.reset_search()
            if definition:
                rows = self.store.results(sid)
                if rows:
                    self.open_document(rows[0], separate=True)
            else:
                self.open_home(path)
        self.job('正在建立副本…', lambda cancel: JobOutcome.completed(
            copy_definition(self.store, row, cancel) if definition else
            bundles.copy_markdown(self.store, row, destination, cancel, expected=expected)), done)

    def export_original(self, row):
        from ..retained_files import directory_identity
        destination = QFileDialog.getExistingDirectory(self, '選擇保存原件的資料夾')
        if not destination:
            return
        try:
            ident = directory_identity(destination)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.job('正在保存封存原件…', lambda cancel: JobOutcome.completed(
            bundles.export_original(self.store, row, destination, ident, cancel)),
            lambda path: self.say('原件已保存於 ' + str(path)))

    def export_selection(self):
        self.export_rows([pick['row'] for pick in self.picks.values()])

    def export_rows(self, rows):
        from ..conversation_import import Destination
        from .exports import export_selected
        if not rows or self.jobs.active:
            return
        rows = [dict(row) for row in rows]
        value = QFileDialog.getExistingDirectory(self, '輸出 Markdown 到資料夾',
                                                str(self.workspace.locations.current or ''))
        if not value:
            return
        try:
            destination = Destination.choose(value)
            self.workspace.locations.permitted(destination.path)
        except (ValueError, OSError) as error:
            self.say(str(error))
            return
        def done(result):
            self.last_export = result
            for index in result.exported:
                self.picks.pop(self.key(rows[index]), None)
            if result.completed:
                if self.workspace.locations.root_for(result.completed[0]) is None:
                    self.workspace.add_root(destination.path)
                self.workspace.refresh_tree()
                if len(rows) == 1 and len(result.completed) == 1 and not result.failed:
                    self.open_home(result.completed[0])
            self.reset_search()
            self.update_selection()
            self.show_export_result(result, destination.path)
        self.job('正在輸出 Markdown…', lambda cancel: JobOutcome.completed(
            export_selected(self.store, rows, destination, cancel)), done)

    def show_export_result(self, result, destination):
        from ..widgets import label
        dialog = StudioDialog(self)
        dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        dialog.setWindowTitle('輸出結果')
        dialog.resize(760, 500)
        heading = label(f'完成 {len(result.completed)} 份' + (' · 已取消' if result.cancelled else ''), 'SectionTitle')
        dialog.body.addWidget(heading)
        location = label(str(destination), 'Muted', True)
        location.setTextFormat(Qt.TextFormat.PlainText)
        dialog.body.addWidget(location)
        listing = QTreeWidget()
        listing.setRootIsDecorated(False)
        listing.setHeaderLabels(['對話', '結果'])
        listing.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        listing.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for path in result.completed:
            item = QTreeWidgetItem([result.titles.get(str(path), path.parent.name), '已輸出'])
            item.setToolTip(1, str(path))
            listing.addTopLevelItem(item)
        for title, path, error in result.failed:
            item = QTreeWidgetItem([title, error])
            item.setToolTip(1, path + '\n' + error)
            listing.addTopLevelItem(item)
        for title in result.unprocessed:
            listing.addTopLevelItem(QTreeWidgetItem([title, '尚未處理']))
        dialog.body.addWidget(listing, 1)
        def show_folder():
            self.set_kind('all')
            self.enter_folder(destination)
            self.workspace.open_route('explore', 'work_resources')
            dialog.accept()
        line(dialog.body, None, button('查看資料夾', show_folder, 'Primary'))
        self.export_result = dialog
        dialog.show()
