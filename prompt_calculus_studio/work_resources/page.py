"""A single resource list: find, check, save. Reading is always on demand."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Qt, QTimer, QSize
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (QAbstractItemView, QButtonGroup, QHeaderView,
    QLineEdit, QTreeWidgetItem, QVBoxLayout, QWidget)
from shiboken6 import isValid

from ..job_outcomes import JobOutcome
from ..management_reading import READING_MARGIN, READING_NARROW_MARGIN
from ..widgets import InputDialog, RoundMenu, button, label
from ..ui_icons import icon
from . import bundles
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
        self.host = host
        self.workspace = host.workspace_owner
        self.store = Catalog(self.workspace.data)
        self.jobs = host.jobs
        self.jobs.became_idle.connect(self.on_idle)
        self.offset = self.ticket = 0
        self.search_pending = False
        self.source_id = ''
        self.source_rows = self.store.sources()
        self.picks = {}
        self.saved_cache = None
        self.group = None
        self.reader = None
        self.shown_rows = []
        self.total = 0
        self.order = 'name'
        self.current_kind = 'all'
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(READING_MARGIN, READING_MARGIN, READING_MARGIN, READING_MARGIN)
        self.outer.setSpacing(12)
        self.add_button = button('加入檔案', self.add_menu, 'Primary')
        self.manage = icon_button('more-horizontal', '管理來源', self.manage_sources)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('搜尋名稱或內容')
        self.filter.setClearButtonEnabled(True)
        self.filter.setAccessibleName('搜尋工作資源')
        self.filter.setMaximumWidth(320)
        self.filter.setMinimumWidth(120)
        self.filter.addAction(icon('search'), QLineEdit.ActionPosition.LeadingPosition)
        line(self.outer, label('工作資源', 'DialogTitle'), None, self.filter, self.add_button)
        self.sort = icon_button('arrow-down-up', '排序：名稱', self.sort_menu)
        self.refresh = icon_button('refresh', '重新整理', self.refresh_list)
        self.kind_buttons = {}
        self.kind_group = QButtonGroup(self)
        for title, value in [('全部檔案', 'all'), ('文件', 'document'), ('對話', 'archive'), ('已存組合', 'bundle')]:
            control = button(title, lambda _=False, kind=value: self.set_kind(kind), 'Navigation')
            control.setCheckable(True)
            control.setChecked(value == self.current_kind)
            self.kind_group.addButton(control)
            self.kind_buttons[value] = control
        line(self.outer, *self.kind_buttons.values(), None, self.sort, self.refresh, self.manage)
        self.back_button = icon_button('back', '返回所有檔案', self.go_back)
        self.context = label('', 'SectionTitle')
        self.context.setTextFormat(Qt.TextFormat.PlainText)
        self.context.setWordWrap(True)
        self.breadcrumb, breadcrumb = column()
        line(breadcrumb, self.back_button, self.context, None)
        self.outer.addWidget(self.breadcrumb)
        self.breadcrumb.hide()
        self.results = ResourceList(self)
        self.results.setObjectName('ResourceList')
        self.results.setAccessibleName('工作資源清單')
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setAlternatingRowColors(False)
        self.results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.results.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.results.setHeaderLabels(['', '名稱', '來源', '更新時間'])
        self.results.setIconSize(QSize(20, 20))
        self.results.header().setObjectName('ResourceHeader')
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        for col, width in [(0, 34), (2, 200), (3, 152)]:
            self.results.header().setSectionResizeMode(col, QHeaderView.ResizeMode.Fixed)
            self.results.setColumnWidth(col, width)
        self.results.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.results.customContextMenuRequested.connect(self.context_menu)
        self.results.itemActivated.connect(self.open_hit)
        self.results.itemChanged.connect(self.item_checked)
        self.outer.addWidget(self.results, 1)
        self.empty = label('', 'Muted', True)
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.outer.addWidget(self.empty)
        self.empty.hide()
        self.previous = icon_button('chevron-left', '上一頁', lambda: self.turn(-1))
        self.next = icon_button('chevron-right', '下一頁', lambda: self.turn(1))
        self.page_number = label('', 'Muted')
        self.clear = icon_button('close', '清除勾選', self.clear_selection)
        self.save = button('保存組合', self.save_selection, 'Primary')
        line(self.outer, self.page_number, self.previous, self.next, None, self.clear, self.save)
        self.clear.hide()
        self.save.hide()
        self.status = label('', 'Muted', True)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.cancel_button = button('取消處理', self.cancel_job, 'Quiet')
        self.status_row, status_layout = column()
        line(status_layout, self.status, None, self.cancel_button)
        self.outer.addWidget(self.status_row)
        self.status_row.hide()
        self.cancel_button.hide()
        self.search_timer = QTimer(self)
        self.search_timer.setSingleShot(True)
        self.search_timer.setInterval(180)
        self.search_timer.timeout.connect(self.search)
        self.filter.textChanged.connect(self.reset_search)
        self.results.toggleSelection.connect(self.toggle_highlighted)
        shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        shortcut.activated.connect(self.filter.setFocus)
        self.reset_search()

    @staticmethod
    def key(row):
        return row['kind'], row['source'], row['item'], row['sha'], row['story']

    @staticmethod
    def selectable(row):
        return (row['kind'] == 'document' and row['state'] == '可用' or
                row['kind'] == 'archive' and bool(row['story']))

    def say(self, text):
        self.status.setText(text)
        self.status_row.setVisible(bool(text))

    def job(self, title, work, done):
        if self.jobs.active:
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

    def clear_group(self):
        self.group = None

    def set_kind(self, kind):
        self.current_kind = kind
        self.kind_buttons[kind].setChecked(True)
        self.clear_group()
        if kind == 'bundle':
            self.source_id = ''
        self.filter.clear()
        self.reset_search()

    def refresh_list(self):
        self.saved_cache = None
        self.source_rows = self.store.sources()
        self.reset_search()

    def reset_search(self, *_):
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
        for title, order in [('名稱排序', 'name'), ('最新在前', 'newest'), ('最舊在前', 'oldest')]:
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
        if self.jobs.active:
            self.search_pending = True
            return
        self.search_pending = False
        owner = self.ticket
        query, kind = self.filter.text().strip(), self.current_kind
        offset, order, source = self.offset, self.order, self.source_id
        group, cache = self.group, self.saved_cache
        def work(cancel):
            saved = None
            if group:
                rows = [dict(kind='snapshot', item=str(i), title=ref['title'], date=group['record']['saved'],
                         state=ref['scope'], scope=ref['scope'], source=ref['source'], sha=ref['version'],
                         story=ref.get('story', ''), location='', ref=ref, node='')
                        for i, ref in enumerate(group['record']['selections'])]
            elif kind == 'bundle':
                saved = cache if cache is not None else bundles.saved_rows(self.store, cancel)
                rows = saved
            else:
                rows, total = self.store.browse(query, kind, offset, PAGE_SIZE, order, source, cancel)
                return rows, total, None
            rows = [row for row in rows if query.casefold() in (row['title'] + ' ' + row['date'] + ' ' + row['location']).casefold()]
            rows = sorted(rows, key=lambda row: (row['title'].casefold(), row['item']) if order == 'name'
                          else (row['date'], row['title'].casefold(), row['item']), reverse=order == 'newest')
            return rows[offset:offset + PAGE_SIZE], len(rows), saved
        def done(result):
            rows, total, saved = result
            if owner != self.ticket:
                return
            if saved is not None:
                self.saved_cache = saved
            self.shown_rows, self.total = rows, total
            if not rows and offset and total:
                self.offset = (total - 1) // PAGE_SIZE * PAGE_SIZE
                self.search_pending = True
                return
            self.render_rows()
        self.job('正在讀取清單…', work, done)

    def scope(self, row):
        if row['kind'] == 'bundle':
            return f'{row["count"]} 項'
        if row['kind'] == 'snapshot':
            return row['scope']
        pick = self.picks.get(self.key(row), {})
        if row['kind'] == 'archive':
            leaves = pick.get('leaves')
            return f'{len(leaves)} 個分支' if leaves else '目前分支' if row['story'] else '無法讀取'
        spans = pick.get('spans')
        return f'{len(spans)} 段文字' if spans else '全文' if row['state'] == '可用' else row['state']

    def render_rows(self):
        self.results.blockSignals(True)
        self.results.clear()
        sources = {row['id']: row for row in self.source_rows}
        for row in self.shown_rows:
            location = row['location']
            name = row['title']
            if row['kind'] == 'document':
                rel = Path(name)
                name = rel.name
                src = sources.get(row['source'])
                root = Path(src['path'] if src else location)
                location = str(root / rel.parent) if not src or src['kind'] == 'folder' else str(root.parent)
            src = sources.get(row['source'])
            origin = Path(src['path']).name if src else Path(location).name if location else ''
            if row['kind'] == 'document' and str(Path(row['title']).parent) != '.':
                origin += ' / ' + str(Path(row['title']).parent)
            if row['kind'] == 'bundle':
                origin = f'{row["count"]} 項'
            item = QTreeWidgetItem(['', name, origin, display_date(row['date'])])
            item.setIcon(1, icon({'document': 'file-text', 'archive': 'message-square',
                                 'bundle': 'folder', 'snapshot': 'file-text'}[row['kind']]))
            item.setData(0, Qt.ItemDataRole.UserRole, row)
            item.setToolTip(1, row['title'] + '\n' + self.scope(row))
            item.setToolTip(2, location)
            if self.selectable(row):
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
                item.setCheckState(0, Qt.CheckState.Checked if self.key(row) in self.picks else Qt.CheckState.Unchecked)
            self.results.addTopLevelItem(item)
        self.results.blockSignals(False)
        self.page_number.setText(f'{self.offset + 1}–{self.offset + len(self.shown_rows)} / {self.total:,} 項' if self.total else '0 項')
        self.previous.setEnabled(self.offset > 0)
        self.next.setEnabled(self.offset + PAGE_SIZE < self.total)
        self.empty.setVisible(not self.total)
        self.empty.setText('沒有符合的項目' if self.filter.text() else
                           '尚未保存組合。勾選檔案後，按「保存組合」。' if self.current_kind == 'bundle' else
                           '加入資料夾或匯入對話封存，即可在這裡勾選內容。')
        self.update_context()
        self.update_selection()

    def update_context(self):
        grouped = bool(self.group)
        self.results.headerItem().setText(2, '項目' if self.current_kind == 'bundle' and not grouped else '來源')
        self.results.headerItem().setText(3, '保存時間' if self.current_kind == 'bundle' else '更新時間')
        self.breadcrumb.setVisible(grouped or bool(self.source_id))
        self.back_button.setToolTip('返回已存組合' if grouped else '返回所有檔案')
        if grouped:
            self.context.setText(self.group['record']['title'])
        else:
            source = next((s for s in self.source_rows if s['id'] == self.source_id), None)
            self.context.setText(Path(source['path']).name if source else '')
        self.filter.setPlaceholderText('搜尋組合內的項目' if grouped else
                                      '搜尋組合名稱' if self.current_kind == 'bundle' else '搜尋名稱或內容')
        self.arrange()

    def choose_row(self, row, *, span=None, leaf=None):
        key = self.key(row)
        pick = self.picks.setdefault(key, {'row': dict(row)})
        if span is not None:
            spans = set(pick.get('spans', []))
            spans.add(tuple(span))
            merged = []
            for start, end in sorted(spans):
                if merged and start <= merged[-1][1]:
                    merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
                else:
                    merged.append((start, end))
            pick['spans'] = merged
        elif leaf:
            pick['leaves'] = list(dict.fromkeys([*pick.get('leaves', []), leaf]))
        else:
            pick.pop('spans', None)
            pick.pop('leaves', None)
        self.update_selection()

    def item_checked(self, item, column):
        if column != 0:
            return
        row = item.data(0, Qt.ItemDataRole.UserRole)
        if not row or not self.selectable(row):
            return
        if item.checkState(0) == Qt.CheckState.Checked:
            if self.key(row) not in self.picks:
                self.choose_row(row)
        else:
            self.picks.pop(self.key(row), None)
        self.update_selection()

    def update_selection(self):
        self.results.blockSignals(True)
        for index in range(self.results.topLevelItemCount()):
            item = self.results.topLevelItem(index)
            row = item.data(0, Qt.ItemDataRole.UserRole)
            name = Path(row['title']).name if row['kind'] == 'document' else row['title']
            pick = self.picks.get(self.key(row), {})
            if pick.get('spans') or pick.get('leaves'):
                name += ' · ' + self.scope(row)
            item.setText(1, name)
            item.setToolTip(1, row['title'] + '\n' + self.scope(row))
            if self.selectable(row):
                selected = self.key(row) in self.picks
                item.setCheckState(0, Qt.CheckState.Checked if selected else Qt.CheckState.Unchecked)
        self.results.blockSignals(False)
        count = len(self.picks)
        self.clear.setVisible(bool(count))
        self.save.setVisible(bool(count))
        self.save.setText(f'保存組合 · {count}')

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
        options = [('檢視內容', lambda: self.open_row(row))]
        if row['kind'] in ('document', 'archive'):
            options += self.file_options(row)
        self.menu(self.results, options, self.results.viewport().mapToGlobal(position))

    def open_hit(self, item, *_):
        self.open_row(item.data(0, Qt.ItemDataRole.UserRole))

    def close_reader(self):
        if self.reader is not None and isValid(self.reader):
            self.reader.close()
        self.reader = None

    def open_row(self, row):
        owner = self.ticket
        if row['kind'] == 'bundle':
            def done(result):
                if owner != self.ticket:
                    return
                self.group = {'record': result[0], 'contents': result[1]}
                self.filter.clear()
                self.reset_search()
            self.job('正在開啟組合…', lambda cancel: bundles.open_bundle(self.store, row['item'], cancel), done)
            return
        if row['kind'] == 'snapshot':
            if self.group:
                self.show_reader(row, None, frozen=(row['ref'], self.group['contents'][int(row['item'])]))
            return
        if row['kind'] == 'archive' and not row['story']:
            self.say(row['state'])
            return
        def done(loaded):
            if owner == self.ticket and self.isVisible():
                self.show_reader(row, loaded)
        self.job('正在開啟內容…', lambda cancel: bundles.document(self.store, row, cancel)
                 if row['kind'] == 'document' else bundles.load_story(self.store, row, cancel), done)

    def show_reader(self, row, loaded, **kwargs):
        self.close_reader()
        self.reader = ResourceReader(self, row, loaded, **kwargs)
        self.reader.show()

    def go_back(self):
        self.clear_group()
        self.source_id = ''
        self.filter.clear()
        self.reset_search()

    def save_selection(self):
        if not self.picks or self.jobs.active:
            return
        chosen = deepcopy(self.picks)
        title, accepted = InputDialog.getText(self, '保存組合', f'為勾選的 {len(chosen)} 項內容命名', '')
        if not accepted:
            return
        if not title.strip() or len(title) > 160:
            self.say('請輸入 1 至 160 字的組合名稱。')
            return
        def work(cancel):
            selected = bundles.materialize(self.store, list(chosen.values()), cancel)
            return JobOutcome.completed(bundles.save(self.store, selected, title, cancel))
        def done(key):
            # New selections made while saving belong to the next operation.
            for identity, value in chosen.items():
                if self.picks.get(identity) == value:
                    self.picks.pop(identity)
            self.saved_cache = None
            self.clear_group()
            self.source_id = ''
            self.set_kind('bundle')
            self.filter.clear()
            self.set_order('newest')
            self.reset_search()
            self.update_selection()
            self.say(f'已保存「{title.strip()}」')
        self.job('正在保存組合…', work, done)

    def arrange(self):
        width = self.width()
        self.results.setColumnHidden(0, bool(self.group) or self.current_kind == 'bundle')
        self.results.setColumnHidden(2, width < 760)
        self.results.setColumnHidden(3, width < 580)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        margin = READING_NARROW_MARGIN if event.size().width() < 760 else READING_MARGIN
        self.outer.setContentsMargins(margin, margin, margin, margin)
        if hasattr(self, 'results'):
            self.arrange()
