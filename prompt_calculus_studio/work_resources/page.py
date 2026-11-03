"""A centered file browser: real folders, on-demand reading, per-dialogue export."""
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QAbstractItemView, QHeaderView, QHBoxLayout, QLineEdit, QTreeWidgetItem, QWidget
from shiboken6 import isValid

from ..management_reading import PREFERRED_CONTENT_WIDTH, READING_MARGIN, READING_NARROW_MARGIN
from ..widgets import ComboBox, RoundMenu, button, label
from ..ui_icons import icon
from . import bundles, navigation
from .actions import SourceActions
from .catalog import Catalog
from .reader import ResourceReader, column, line, icon_button
from .list_view import ResourceList

PAGE_SIZE = 50


def display_date(value):
    if 'T' not in value:
        return value
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).astimezone().strftime('%Y-%m-%d %H:%M')
    except ValueError:
        return value[:16]


class WorkResourcesPage(SourceActions, QWidget):
    def __init__(self, host):
        super().__init__()
        self.host, self.workspace = host, host.workspace_owner
        self.store = Catalog(self.workspace.data)
        self.jobs = host.jobs
        self.jobs.became_idle.connect(self.on_idle)
        self.offset = self.ticket = 0
        self.search_pending = False
        self.source_id = ''
        self.location = None
        self.root_rows = []
        self.source_rows = self.store.sources()
        self.picks = {}
        self.reader = None
        self.shown_rows = []
        self.total = 0
        self.order, self.current_kind = 'name', 'all'
        self.outer = QHBoxLayout(self)
        self.outer.setContentsMargins(READING_MARGIN, READING_MARGIN, READING_MARGIN, READING_MARGIN)
        self.content, layout = column()
        self.content.setMaximumWidth(PREFERRED_CONTENT_WIDTH)
        self.outer.addStretch()
        self.outer.addWidget(self.content, 1)
        self.outer.addStretch()
        self.add_button = button('加入檔案', self.add_current, 'Primary')
        self.manage = icon_button('more-horizontal', '管理來源', self.manage_sources)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('搜尋名稱或內容')
        self.filter.setClearButtonEnabled(True)
        self.filter.setAccessibleName('搜尋工作資源')
        self.filter.setMaximumWidth(320)
        self.filter.setMinimumWidth(120)
        self.filter.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        self.mode = ComboBox()
        self.mode.setAccessibleName('工作資源模式')
        for title, value in [('檔案', 'files'), ('Agent', 'agent'), ('Skill', 'skill')]:
            self.mode.addItem(title, value)
        line(layout, label('工作資源', 'DialogTitle'), self.mode, None, self.filter, self.add_button)
        self.mode_layout = layout
        self.file_body, layout = column()
        self.mode_layout.addWidget(self.file_body, 1)
        self.agent_library = None
        self.kind = ComboBox()
        self.kind.setAccessibleName('檔案篩選')
        for title, value in [('全部檔案', 'all'), ('文件', 'document'), ('對話', 'archive'), ('規則與定義', 'definitions')]:
            self.kind.addItem(title, value)
        self.kind.currentIndexChanged.connect(lambda: self.set_kind(self.kind.currentData()))
        self.sort = icon_button('arrow-down-up', '排序：名稱', self.sort_menu)
        self.refresh = icon_button('refresh', '重新整理', self.refresh_list)
        self.new_folder_button = icon_button('plus', '新增資料夾', self.new_folder)
        line(layout, self.kind, None, self.new_folder_button, self.sort, self.refresh, self.manage)
        self.back_button = icon_button('back', '返回上層', self.go_back)
        self.context = label('', 'SectionTitle')
        self.context.setTextFormat(Qt.TextFormat.PlainText)
        self.context.setWordWrap(True)
        self.breadcrumb, breadcrumb = column()
        line(breadcrumb, self.back_button, self.context, None)
        layout.addWidget(self.breadcrumb)
        self.breadcrumb.hide()
        self.results = ResourceList(self)
        self.results.setObjectName('ResourceList')
        self.results.setAccessibleName('工作資源清單')
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setHeaderLabels(['', '名稱', '來源', '更新時間'])
        self.results.setIconSize(QSize(20, 20))
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, width in [(0, 34), (2, 180), (3, 152)]:
            self.results.header().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.results.setColumnWidth(col, width)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.context_menu)
        self.results.itemActivated.connect(self.open_hit)
        self.results.itemChanged.connect(self.item_checked)
        layout.addWidget(self.results, 1)
        self.empty = label('', 'Muted', True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.empty)
        self.empty.hide()
        self.previous = icon_button('chevron-left', '上一頁', lambda: self.turn(-1))
        self.next = icon_button('chevron-right', '下一頁', lambda: self.turn(1))
        self.page_number = label('', 'Muted')
        self.clear = icon_button('close', '清除勾選', self.clear_selection)
        self.export = button('輸出 Markdown', self.export_selection, 'Primary')
        line(layout, self.page_number, self.previous, self.next, None, self.clear, self.export)
        self.clear.hide()
        self.export.hide()
        self.status = label('', 'Muted', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.cancel_button = button('取消處理', self.cancel_job, 'Quiet')
        self.status_row, status_layout = column()
        line(status_layout, self.status, None, self.cancel_button)
        layout.addWidget(self.status_row)
        self.status_row.hide()
        self.cancel_button.hide()
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.search)
        self.filter.textChanged.connect(self.reset_search)
        self.results.toggleSelection.connect(self.toggle_highlighted)
        QShortcut(QKeySequence.StandardKey.Find, self).activated.connect(self.filter.setFocus)
        self.mode.currentIndexChanged.connect(self.set_mode)
        self.mode.setCurrentIndex(max(0, self.mode.findData(getattr(self.workspace, 'work_resources_mode', 'files'))))
        self.reset_search()

    def add_current(self):
        if self.mode.currentData() == 'files':
            self.add_menu()
        else:
            self.agent_library.add()

    def set_mode(self):
        mode = self.mode.currentData()
        self.workspace.work_resources_mode = mode
        self.file_body.setVisible(mode == 'files')
        self.add_button.setText({'files': '加入檔案', 'agent': '新增 Agent', 'skill': '加入 Skill'}[mode])
        self.filter.setPlaceholderText('搜尋名稱或內容' if mode == 'files' else '搜尋名稱或用途')
        if mode != 'files' and self.agent_library is None:
            from ..local_agents.page import AgentLibrary
            self.agent_library = AgentLibrary(self)
            self.mode_layout.addWidget(self.agent_library, 1)
        if self.agent_library is not None:
            self.agent_library.setVisible(mode != 'files')
            if mode != 'files':
                self.agent_library.populate(mode, self.filter.text())
        if mode == 'files':
            self.reset_search()

    @staticmethod
    def key(row):
        return row['kind'], row['source'], row['item'], row['sha'], row['story']

    @staticmethod
    def selectable(row):
        return row['kind'] == 'archive' and bool(row['story'])

    def say(self, text):
        self.status.setText(text)
        self.status_row.setVisible(bool(text))

    def job(self, title, work, done):
        if self.jobs.active or self.results.file_drag.ops:
            self.say('正在處理，請稍候。')
            return False
        self.say(title)
        self.cancel_button.show()
        def success(value):
            if not isValid(self) or self.host.closing:
                return
            self.cancel_button.hide()
            self.say('')
            done(value)
        def failed(error):
            if isValid(self):
                self.cancel_button.hide()
                self.say(error)
        return self.jobs.start(title, work, success, failed, announce=False)

    def cancel_job(self):
        if self.jobs.active:
            self.jobs.active.cancel.set()
            self.say('正在取消…')

    def on_idle(self):
        if self.search_pending:
            self.search_timer.start(0)

    def set_kind(self, kind):
        self.current_kind = kind
        self.kind.blockSignals(True)
        self.kind.setCurrentIndex(self.kind.findData(kind))
        self.kind.blockSignals(False)
        self.reset_search()

    def refresh_list(self):
        # Index only explicitly enrolled roots, and only on demand.
        rows = self.store.sources()
        targets = [s['id'] for s in rows if not self.source_id or s['id'] == self.source_id]
        def work(cancel):
            errors = []
            for sid in targets:
                if cancel.is_set():
                    break
                try:
                    self.store.rebuild(sid, cancel)
                except (ValueError, OSError) as error:
                    errors.append(str(error))
            return errors
        def done(errors):
            self.reset_search()
            if errors:
                self.say('；'.join(errors))
        self.job('正在重新整理…', work, done)

    def reset_search(self, *_):
        if self.mode.currentData() != 'files':
            if self.agent_library is not None:
                self.agent_library.populate(self.mode.currentData(), self.filter.text())
            return
        self.offset = 0
        self.ticket += 1
        self.search_pending = True
        self.search_timer.start()

    def turn(self, step):
        self.offset = max(0, self.offset + PAGE_SIZE * step)
        self.ticket += 1
        self.search()

    def sort_menu(self):
        popup = RoundMenu(self)
        popup.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        for title, order in [('名稱', 'name'), ('最新在前', 'newest'), ('最舊在前', 'oldest')]:
            action = popup.addAction(title, lambda _=False, value=order: self.set_order(value))
            action.setCheckable(True)
            action.setChecked(order == self.order)
        self.popup = popup
        popup.open_for(self.sort)

    def set_order(self, order):
        self.order = order
        self.sort.setToolTip('排序：' + {'name': '名稱', 'newest': '最新在前', 'oldest': '最舊在前'}[order])
        self.reset_search()

    def search(self):
        if self.jobs.active or self.results.file_drag.ops:
            self.search_pending = True
            return
        self.search_pending = False
        owner = self.ticket
        query, kind = self.filter.text().strip(), self.current_kind
        offset, order, source = self.offset, self.order, self.source_id
        location, roots = self.location, tuple(self.workspace.locations.roots)
        def work(cancel):
            root_rows = navigation.roots(self.store, roots)
            rows, total = navigation.browse(self.store, root_rows, location, query, kind, offset,
                                             PAGE_SIZE, order, cancel, source)
            return rows, total, root_rows, self.store.sources()
        def done(result):
            if owner != self.ticket:
                return
            rows, total, self.root_rows, self.source_rows = result
            self.shown_rows, self.total = rows, total
            if not rows and offset and total:
                self.offset = (total - 1) // PAGE_SIZE * PAGE_SIZE
                self.search_pending = True
                return
            self.render_rows()
        self.job('正在讀取清單…', work, done)

    def render_rows(self):
        self.results.blockSignals(True)
        self.results.clear()
        sources = {s['id']: s for s in self.source_rows}
        for row in self.shown_rows:
            name = Path(row['title']).name if row['kind'] == 'document' else row['title']
            source = sources.get(row['source'], {})
            origin = Path(source.get('path', row['location'])).name
            if source.get('kind') == 'copy':
                origin = Path(source['path']).parent.name
            elif source.get('kind') == 'definition':
                origin = '管理副本'
            location = row.get('absolute_path', row['location'])
            if row['kind'] == 'document':
                rel = Path(row['title'])
                if str(rel.parent) != '.':
                    origin += ' / ' + str(rel.parent)
                purpose = navigation.purpose(location)
                if purpose != 'document':
                    origin = navigation.PURPOSES[purpose] + ' · ' + origin
            elif row['kind'] == 'folder':
                origin = '工作區' if Path(row['location']) in self.workspace.locations.roots else Path(row['location']).parent.name
            item = QTreeWidgetItem(['', name, origin, display_date(row['date'])])
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsUserCheckable)
            item.setIcon(1, icon({'document': 'file-text', 'archive': 'message-square', 'folder': 'folder'}[row['kind']]))
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            item.setToolTip(1, row['title'] + ('\n' + row['state'] if row['state'] != '可用' else ''))
            item.setToolTip(2, location)
            if self.results.file_drag.context(row) is None:
                item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            if self.selectable(row):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if self.key(row) in self.picks else Qt.CheckState.Unchecked)
            self.results.addTopLevelItem(item)
        self.results.blockSignals(False)
        self.page_number.setText(f'{self.offset + 1}–{self.offset + len(self.shown_rows)} / {self.total:,}' if self.total else '0 項')
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + PAGE_SIZE < self.total)
        self.empty.setVisible(not self.total)
        self.empty.setText('沒有符合的項目' if self.filter.text() else '這裡還沒有檔案')
        self.update_context()
        self.update_selection()

    def update_context(self):
        self.breadcrumb.setVisible(self.location is not None or bool(self.source_id))
        if self.location:
            root = next((r for r in self.root_rows if r['source'] == self.source_id), None)
            path = Path(self.location)
            self.context.setText(str(path.relative_to(Path(root['location']).parent)) if root else path.name)
            self.context.setToolTip(str(path))
        else:
            source = next((s for s in self.source_rows if s['id'] == self.source_id), None)
            self.context.setText(Path(source['path']).name if source else '')
        self.filter.setPlaceholderText('搜尋此資料夾與子資料夾' if self.location else '搜尋名稱或內容')
        destination = self.location or self.workspace.locations.current
        self.new_folder_button.setEnabled(bool(destination and self.workspace.locations.root_for(destination)))
        self.arrange()

    def choose_row(self, row):
        if self.selectable(row):
            self.picks[self.key(row)] = {'row': dict(row)}
            self.update_selection()

    def item_checked(self, item, column):
        if column != 0:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if not row or not self.selectable(row):
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            self.choose_row(row)
        else:
            self.picks.pop(self.key(row), None)
        self.update_selection()

    def update_selection(self):
        self.results.blockSignals(True)
        for index in range(self.results.topLevelItemCount()):
            item = self.results.topLevelItem(index)
            row = item.data(0, Qt.ItemDataRole.UserRole)
            if self.selectable(row):
                item.setCheckState(0, Qt.CheckState.Checked if self.key(row) in self.picks else Qt.CheckState.Unchecked)
        self.results.blockSignals(False)
        count = len(self.picks)
        self.clear.setVisible(bool(count))
        self.export.setVisible(bool(count))
        self.export.setText(f'輸出 Markdown · {count}')

    def toggle_highlighted(self):
        rows = [item.data(0, Qt.ItemDataRole.UserRole) for item in self.results.selectedItems()]
        rows = [row for row in rows if self.selectable(row)]
        clear = rows and all(self.key(row) in self.picks for row in rows)
        for row in rows:
            if clear:
                self.picks.pop(self.key(row), None)
            else:
                self.picks.setdefault(self.key(row), {'row': dict(row)})
        self.update_selection()

    def clear_selection(self):
        self.picks.clear()
        self.update_selection()

    def context_menu(self, position):
        item = self.results.itemAt(position)
        if item is None:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        options = [('開啟', lambda: self.open_row(row)), *self.file_options(row)]
        self.menu(self.results, options, self.results.viewport().mapToGlobal(position))

    def open_hit(self, item, *_):
        self.open_row(item.data(0, Qt.ItemDataRole.UserRole))

    def close_reader(self):
        if self.reader is not None and isValid(self.reader):
            if not self.reader.close():
                return False
        self.reader = None
        return True

    def open_row(self, row, *, separate=False):
        if row['kind'] == 'folder':
            self.enter_folder(Path(row['location']), row['source'])
            return
        if row['kind'] == 'document':
            self.open_document(row, separate=separate)
            return
        if not row['story']:
            self.say(row['state'])
            return
        owner = self.ticket
        def done(loaded):
            if owner == self.ticket and self.isVisible() and self.close_reader():
                self.reader = ResourceReader(self, row, loaded)
                self.reader.show()
        self.job('正在開啟內容…', lambda cancel: bundles.load_story(self.store, row, cancel), done)

    def enter_folder(self, path, sid=''):
        self.location, self.source_id = Path(path), sid
        self.filter.clear()
        self.reset_search()

    def go_back(self):
        if self.location and str(self.location) not in [r['location'] for r in self.root_rows]:
            self.location = self.location.parent
        else:
            self.location, self.source_id = None, ''
        self.filter.clear()
        self.reset_search()

    def arrange(self):
        width = self.content.width()
        self.results.setColumnHidden(2, width < 700)
        self.results.setColumnHidden(3, width < 520)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margin = READING_NARROW_MARGIN if event.size().width() < 760 else READING_MARGIN
        self.outer.setContentsMargins(margin, margin, margin, margin)
        if hasattr(self, 'results'):
            self.arrange()
