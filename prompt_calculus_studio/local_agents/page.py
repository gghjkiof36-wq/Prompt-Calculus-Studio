"""Project-first Agent and global Skill pages in the work-resources area."""
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtWidgets import QFileDialog, QHeaderView, QSplitter, QTreeWidgetItem, QVBoxLayout, QWidget
from ..local_ai.service import for_workspace
from ..ui_icons import icon
from ..widgets import button
from ..work_resources.reader import column, icon_button, line
from .dialogs import plain
from .editors import AgentEditor, ProjectEditor, listing
from .skill_details import SkillDetails, skill_version
from .store import AgentStore
from .catalog_view import CatalogDelegate


class AgentLibrary(QWidget):
    def __init__(self, resource_page):
        super().__init__(resource_page)
        self.page, self.workspace = resource_page, resource_page.workspace
        self.mode, self.query, self.project_id = None, '', None
        self.selected, self.editor, self.store = None, None, None
        self._detail_requested, self._filling, self.order = False, False, 'name'
        self.selection_timer = QTimer(self)
        self.selection_timer.setSingleShot(True)
        self.selection_timer.timeout.connect(self.restore_selection)
        self.back_button = icon_button('back', '返回', self.back)
        self.settings_button = icon_button('settings', '專案設定', self.edit_project)
        self.delegation_button = button('啟用本機委派', self.toggle_delegation)
        self.delegation_button.hide()
        self.sort_button = icon_button('arrow-down-up', '排序', self.sort_menu)
        self.page.header_row.insertWidget(0, self.back_button)
        self.page.header_row.insertWidget(self.page.header_row.count()-1, self.settings_button)
        self.page.header_row.insertWidget(self.page.header_row.count()-1, self.delegation_button)
        self.back_button.hide(); self.settings_button.hide(); self.sort_button.hide()
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.setChildrenCollapsible(False); self.splitter.setHandleWidth(32)
        self.list_panel, left = column()
        left.setSpacing(16)
        self.search_row = line(left, self.page.filter, self.page.agent_refresh, self.sort_button)
        self.results = listing(['名稱'], '資源清單')
        self.results.setObjectName('AgentCatalog')
        self.results.setHeaderHidden(True)
        self.results.setMouseTracking(True)
        self.results.setItemDelegate(CatalogDelegate(self.results))
        self.results.header().setStretchLastSection(True)
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.setStyleSheet('QTreeWidget {border:0;background:transparent;}')
        self.results.currentItemChanged.connect(self.selected_item)
        self.results.itemActivated.connect(lambda item: self.selected_item(item))
        left.addWidget(self.results, 1)
        self.count = plain('', 'Subtle'); left.addWidget(self.count)
        self.detail_panel, self.details = column()
        self.placeholder = plain('選擇項目以查看內容。')
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.details.addWidget(self.placeholder, 1)
        self.skill_details = SkillDetails(self)
        self.skill_details.updated.connect(self.reload_rows)
        self.details.addWidget(self.skill_details, 1); self.skill_details.hide()
        self.splitter.addWidget(self.list_panel); self.splitter.addWidget(self.detail_panel)
        self.splitter.setStretchFactor(0, 0); self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([280, 800])
        layout.addWidget(self.splitter, 1)
        self.message = plain(''); self.message.hide(); layout.addWidget(self.message)
        self.workspace.installEventFilter(self)
        self.page.host.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Close and watched in (self.workspace, self.page.host):
            if not self.can_leave():
                event.ignore(); return True
        return super().eventFilter(watched, event)

    def show_error(self, text):
        self.message.setText(str(text)); self.message.setVisible(bool(text))

    def can_leave(self):
        return (self.editor is None or self.editor.can_leave()) and self.skill_details.can_leave()

    def populate(self, mode=None, query=None):
        mode = mode or self.mode or 'skill'
        query = self.query if query is None else query
        if mode == self.mode and query != self.query and self.store is not None:
            self.query = query; self.filter_rows(); return
        if mode != self.mode:
            self.clear_editor(); self.selected = None; self._detail_requested = False
            self.skill_details.ticket += 1; self.skill_details.pending = None
            self.skill_details.hide()
        self.mode, self.query = mode, query
        self.reload_rows()

    def reload_rows(self):
        try:
            self.store = AgentStore(self.workspace.data)
            if self.mode == 'agent' and self.project_id:
                self.store.get('projects', self.project_id)
            if self.mode == 'skill':
                entries = self.store.value['skills']
            elif self.project_id:
                entries = [r for r in self.store.value['agents'] if r['project'] == self.project_id]
            else:
                entries = self.store.value['projects']
            self._filling = True; self.results.clear()
            entries = sorted(entries, key=lambda r: ((not r.get('enabled', True)) if self.order == 'enabled' else False, r['name'].casefold()))
            for entry in entries:
                if self.mode == 'skill':
                    version = skill_version(entry)
                    secondary = (f'v{version} · ' if version else '') + f'{len(entry["files"])} 個檔案'
                    status, symbol = (' · 已停用' if not entry['enabled'] else ''), None
                elif self.project_id:
                    secondary = f'{len(entry["skills"])} 個 Skill'
                    status, symbol = '', None
                else:
                    count = sum(a['project'] == entry['id'] for a in self.store.value['agents'])
                    secondary = f'{count} 個 Agent · {entry["cwd"]}'
                    status, symbol = '', 'folder'
                row = QTreeWidgetItem([entry['name'] + status + '\n' + secondary])
                row.setData(0, Qt.ItemDataRole.UserRole, entry['id'])
                row.setData(0, Qt.ItemDataRole.UserRole+1, (entry['name']+' '+entry.get('description', '')+' '+secondary).casefold())
                row.setToolTip(0, entry['name']+'\n'+secondary)
                if symbol: row.setIcon(0, icon(symbol))
                self.results.addTopLevelItem(row)
                if entry['id'] == self.selected: self.results.setCurrentItem(row)
            self._filling = False; self.filter_rows()
            self.show_error('' if entries else '尚未加入 Skill' if self.mode == 'skill' else
                            '這個專案尚未建立 Agent。' if self.project_id else '建立專案，再加入 Agent。')
        except (ValueError, OSError) as exc:
            self._filling = False; self.show_error(exc)
        self.adapt()

    def filter_rows(self):
        visible = 0
        for i in range(self.results.topLevelItemCount()):
            row = self.results.topLevelItem(i)
            show = self.query.strip().casefold() in row.data(0, Qt.ItemDataRole.UserRole+1)
            row.setHidden(not show); visible += show
        total = self.results.topLevelItemCount()
        self.count.setText(f'{visible} / {total}' if visible != total else f'{total} 項')

    def header(self):
        active = self.page.mode != 'files'
        if not active:
            self.back_button.hide(); self.settings_button.hide(); self.sort_button.hide()
            self.delegation_button.hide()
            self.page.header_row.insertWidget(self.page.header_row.count()-1, self.page.filter)
            self.page.filter.show()
            return
        if self.search_row.indexOf(self.page.filter) < 0:
            self.search_row.insertWidget(0, self.page.filter)
        project = self.store.get('projects', self.project_id) if self.store and self.mode == 'agent' and self.project_id else None
        creating_project = isinstance(self.editor, ProjectEditor)
        self.page.title.setText(project['name'] if project else 'Skill' if self.mode == 'skill' else '專案')
        self.page.title.setToolTip(project['name'] if project else '')
        self.page.add_button.setText('加入 Skill' if self.mode == 'skill' else '新增 Agent' if project else '新增專案')
        self.page.add_button.setVisible(not creating_project)
        self.page.filter.setPlaceholderText('搜尋 Skill' if self.mode == 'skill' else '搜尋 Agent' if project else '搜尋專案')
        self.page.filter.setVisible(not creating_project)
        self.settings_button.setVisible(active and project is not None and not creating_project)
        self.delegation_button.setVisible(project is not None and not creating_project)
        if project:
            enabled = project['id'] in getattr(getattr(self.workspace, 'agent_delegation', None), 'enabled', ())
            self.delegation_button.setText('停止本機委派' if enabled else '啟用本機委派')
            self.delegation_button.setToolTip('本機委派已啟用' if enabled else '允許本機主代理向此專案的 Agent 交辦工作')
        self.sort_button.setVisible(active and self.mode == 'skill')
        self.back_button.setVisible(active and (project is not None or creating_project or self.width() < 780 and self._detail_requested))
        self.page.agent_refresh.setVisible(active and not creating_project)

    def clear_editor(self):
        if self.editor is not None:
            self.details.removeWidget(self.editor)
            self.editor.hide(); self.editor.deleteLater(); self.editor = None

    def selected_item(self, row, previous=None):
        if self._filling or row is None: return
        identity = row.data(0, Qt.ItemDataRole.UserRole)
        if identity == self.selected and (self.editor is not None or self.mode == 'skill'):
            self._detail_requested = True; self.adapt(); return
        if not self.can_leave():
            self.restore_selection(); return
        if self.mode == 'agent' and not self.project_id:
            self.project_id = identity; self.selected = None
            self.clear_editor(); self._detail_requested = False
            self.reset_query(); self.reload_rows()
            if self.results.topLevelItemCount(): self.results.setCurrentItem(self.results.topLevelItem(0))
            return
        self.selected = identity
        self.clear_editor(); self.placeholder.hide()
        try:
            if self.mode == 'skill':
                self.skill_details.select(AgentStore(self.workspace.data).get('skills', identity))
                self.skill_details.show()
            else:
                self.skill_details.hide(); self.open_agent(identity, checked=True)
            self._detail_requested = True; self.adapt()
        except (ValueError, OSError) as exc: self.show_error(exc)

    def restore_selection(self):
        self._filling = True
        self.results.setCurrentItem(None)
        for i in range(self.results.topLevelItemCount()):
            row = self.results.topLevelItem(i)
            if row.data(0, Qt.ItemDataRole.UserRole) == self.selected:
                self.results.setCurrentItem(row); break
        self._filling = False

    def attach_editor(self, editor):
        self.clear_editor(); self.skill_details.hide(); self.placeholder.hide()
        self.editor = editor; self.details.addWidget(editor, 1)
        self._detail_requested = True; self.adapt()

    def open_agent(self, identity=None, *, checked=False):
        if not checked and not self.can_leave(): return False
        self.store = AgentStore(self.workspace.data)
        if identity: self.project_id = self.store.get('agents', identity)['project']
        if not self.project_id:
            self.edit_project(new=True); return False
        editor = AgentEditor(self.workspace.data, for_workspace(self.workspace), self.project_id, identity)
        editor.saved.connect(self.agent_saved); editor.cancelled.connect(self.close_detail)
        self.selected = identity; self.reload_rows(); self.attach_editor(editor); self.restore_selection()
        # A project click replaces the tree during its own selection event.
        # Restore the Agent selection after that event finishes as well.
        self.selection_timer.start(0)
        return True

    def agent_saved(self, item):
        self.selected = item['id']; self.reload_rows()

    def edit_project(self, _=False, *, new=False):
        if not self.can_leave(): return
        editor = ProjectEditor(self.workspace.data, None if new else self.project_id)
        editor.saved.connect(self.project_saved); editor.cancelled.connect(self.close_detail)
        self.attach_editor(editor)

    def toggle_delegation(self):
        if self.project_id is None:
            return
        from .delegation import delegation_for
        try:
            service = delegation_for(self.workspace)
            service.set_enabled(self.project_id, self.project_id not in service.enabled)
            self.show_error('')
        except (ValueError, OSError) as exc:
            self.show_error(exc)
        self.header()

    def project_saved(self, item):
        self.project_id = item['id']; self.selected = None
        self.clear_editor(); self._detail_requested = False; self.placeholder.show(); self.reload_rows()

    def close_detail(self):
        self.clear_editor(); self.selected = None; self._detail_requested = False
        self.placeholder.show(); self.skill_details.hide(); self.reload_rows()

    def back(self):
        if not self.can_leave(): return
        if self.width() < 780 and self._detail_requested and not isinstance(self.editor, ProjectEditor):
            self._detail_requested = False; self.adapt(); return
        self.clear_editor(); self._detail_requested = False
        if self.mode == 'agent': self.project_id = None; self.selected = None
        self.reset_query(); self.reload_rows()

    def reset_query(self):
        self.page.filter.blockSignals(True); self.page.filter.clear(); self.page.filter.blockSignals(False)
        self.query = ''

    def adapt(self):
        if not hasattr(self, 'detail_panel'): return
        project_form = isinstance(self.editor, ProjectEditor)
        root = self.mode == 'agent' and self.project_id is None and not project_form
        show_details = not root and self._detail_requested
        wide = self.width() >= 780
        self.list_panel.setMinimumWidth(248 if wide else 0)
        self.list_panel.setMaximumWidth(304 if wide and not root else 16777215)
        self.detail_panel.setVisible(show_details or wide and not root)
        self.list_panel.setVisible(not project_form and (wide or not show_details))
        self.placeholder.setVisible(not show_details)
        self.header()

    def resizeEvent(self, event):
        super().resizeEvent(event); self.adapt()

    def sort_menu(self):
        from ..widgets import RoundMenu
        menu = RoundMenu(self)
        for text, value in [('名稱', 'name'), ('啟用優先', 'enabled')]:
            action = menu.addAction(text, lambda _=False, v=value: self.set_order(v))
            action.setCheckable(True); action.setChecked(value == self.order)
        menu.open_for(self.sort_button); self.popup = menu

    def set_order(self, order):
        self.order = order; self.reload_rows()

    def job(self, title, work, done=None):
        self.show_error(title)
        def success(value):
            self.reload_rows()
            if done: done(value)
        if not self.page.jobs.start(title, work, success, self.show_error, announce=False):
            self.show_error('正在處理其他檔案，請稍候。')

    def refresh(self):
        if not self.can_leave(): return
        selected = self.selected
        if self.mode == 'skill' and selected: self.skill_details.refresh()
        else:
            self.reload_rows()
            if self.mode == 'agent' and self.project_id and selected: self.open_agent(selected)

    def add(self):
        if not self.can_leave(): return
        if self.mode == 'agent':
            self.open_agent() if self.project_id else self.edit_project(new=True)
            return
        path = QFileDialog.getExistingDirectory(self, '加入包含 SKILL.md 的資料夾')
        if path: self.job('正在加入 Skill…', lambda cancel: AgentStore(self.workspace.data).add_skill(path))

    def copy_skill(self, source):
        if self.can_leave():
            self.job('正在建立管理副本…', lambda cancel: AgentStore(self.workspace.data).add_skill(source, managed=True))
