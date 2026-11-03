"""Source maintenance and explicit original/copy actions for the resource browser."""
from pathlib import Path
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHeaderView, QLineEdit, QTreeWidget, QTreeWidgetItem

from ..job_outcomes import JobOutcome
from ..widgets import RoundMenu, StudioDialog, button
from . import bundles
from .reader import line, viewer


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
                     '對話封存 (*.zip *.json *.pcs-chat);;所有檔案 (*)')[0])
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
            self.clear_group()
            self.set_kind('all')
            self.filter.clear()
            self.rebuild(sid)
        except (ValueError, OSError) as error:
            self.say(str(error))

    def rebuild(self, sid):
        def done(_):
            self.source_rows = self.store.sources()
            self.reset_search()
            self.say(self.store.source(sid)['state'])
        self.job('正在整理檔案…', lambda cancel: JobOutcome.completed(self.store.rebuild(sid, cancel)), done)

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
        def selected():
            item = listing.currentItem()
            return item.data(0, Qt.ItemDataRole.UserRole) if item else None
        def browse():
            source = selected()
            if not source:
                return
            self.source_id = source['id']
            self.clear_group()
            self.set_kind('all')
            self.filter.clear()
            self.reset_search()
            dialog.accept()
        def operation(name):
            source = selected()
            if not source:
                return
            if self.jobs.active:
                self.say('正在處理，請稍候。')
                return
            sid = source['id']
            if name == 'remove':
                # This only detaches the source. Existing saved groups and originals stay intact.
                self.store.remove(sid)
                self.picks = {key: pick for key, pick in self.picks.items() if pick['row']['source'] != sid}
                if self.source_id == sid:
                    self.source_id = ''
                self.source_rows = self.store.sources()
                self.reset_search()
                self.update_selection()
                populate()
            else:
                dialog.accept()
                if name == 'scan':
                    self.rebuild(sid)
                else:
                    self.choose_source(source['kind'], sid)
        view = button('查看檔案', browse, 'Primary')
        scan = button('重新掃描', lambda: operation('scan'), 'Quiet')
        relink = button('重新指定位置', lambda: operation('relink'), 'Quiet')
        remove = button('移出清單', lambda: operation('remove'), 'Quiet')
        def update_buttons(*_):
            for control in (view, scan, relink, remove):
                control.setEnabled(selected() is not None)
        line(dialog.body, scan, relink, remove, None, view)
        search.textChanged.connect(populate)
        listing.itemSelectionChanged.connect(update_buttons)
        listing.itemActivated.connect(lambda *_: browse())
        populate()
        dialog.exec()

    def file_options(self, row):
        if row['kind'] != 'document':
            return [('匯出封存原件', lambda: self.export_original(row))]
        options = [('開啟原文分頁', lambda: self.open_original(row))]
        if self.store.source(row['source'])['kind'] == 'copy':
            options.append(('編輯工作區副本', lambda: self.open_managed(row)))
        if Path(row['title']).suffix.casefold() == '.md':
            options.append(('複製到工作區', lambda: self.copy(row)))
        return options

    def file_menu(self, row, control, parent=None):
        self.menu(control, self.file_options(row), parent=parent)

    def export_original(self, row):
        destination = QFileDialog.getExistingDirectory(self, '選擇保存原件的資料夾')
        if not destination:
            return
        from ..retained_files import directory_identity
        try:
            ident = directory_identity(destination)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        self.job('正在保存封存原件…', lambda cancel: JobOutcome.completed(
            bundles.export_original(self.store, row, destination, ident, cancel)),
            lambda path: self.say('原件已保存於 ' + str(path)))

    def open_original(self, row):
        def done(result):
            if not self.isVisible():
                return
            raw, path = result
            from ..workspace_shell import Tab
            from .catalog import token
            view = viewer(str(path))
            view.setPlainText(raw.decode('utf-8-sig'))
            view.setToolTip(str(path))
            self.close_reader()
            self.workspace.add_tab(Tab('reference:' + token(), path.name + ' · 原文', view, True), replace_current=False)
        self.job('正在核對原件…', lambda cancel: bundles.document(self.store, row, cancel), done)

    def open_managed(self, row):
        from .source_io import verified_stream
        from .catalog import root_identity
        from ..conversation_archive import fingerprint
        try:
            src = self.store.source(row['source'])
            if src['kind'] != 'copy':
                raise ValueError('請先複製到工作區。')
            path = Path(src['path'])
            with verified_stream(path.parent, path.name, root_identity(src)) as (stream, _):
                if fingerprint(stream, None) != row['sha']:
                    raise ValueError('副本已變更，請重新掃描。')
                self.close_reader()
                self.workspace.open_path(path, True)
        except (ValueError, OSError) as error:
            self.say(str(error))

    def copy(self, row):
        destination = self.workspace.locations.current
        if not destination:
            self.say('請先在工作區選擇內容資料夾。')
            return
        from ..retained_files import directory_identity
        try:
            identity = directory_identity(destination)
        except (OSError, ValueError) as error:
            self.say(str(error))
            return
        def done(result):
            path, sid = result
            self.workspace.refresh_tree()
            self.source_rows = self.store.sources()
            self.reset_search()
            if self.isVisible() and self.workspace.locations.current == destination:
                rows = self.store.results(sid)
                if rows:
                    self.open_managed(rows[0])
        self.job('正在建立副本…', lambda cancel: JobOutcome.completed(
            bundles.copy_markdown(self.store, row, destination, cancel, expected=identity)), done)
