"""Agent and Skill destinations sharing the resources reading column."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QHeaderView, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from ..local_ai.service import for_workspace
from .store import AgentStore
from .dialogs import AgentDialog, SkillDialog, plain


class AgentLibrary(QWidget):
    def __init__(self, resource_page):
        super().__init__(resource_page)
        self.page = resource_page
        self.workspace = resource_page.workspace
        self.mode, self.query = 'skill', ''
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)
        self.results = QTreeWidget()
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.setHeaderLabels(['名稱', '用途／專案', '狀態'])
        self.results.header().setStretchLastSection(False)
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
        self.results.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.results.header().setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        self.results.setColumnWidth(0, 230)
        self.results.setColumnWidth(2, 112)
        self.results.itemActivated.connect(self.open_item)
        self.results.setAlternatingRowColors(False)
        self.results.setStyleSheet('QTreeWidget {border:0;background:transparent;} QTreeWidget::item {padding:10px 8px;} QHeaderView::section {background:transparent;padding:8px;}')
        layout.addWidget(self.results, 1)
        self.message = plain('')
        layout.addWidget(self.message)

    def populate(self, mode=None, query=None):
        current = self.results.currentItem()
        selected = current.data(0, Qt.ItemDataRole.UserRole) if current is not None else None
        self.mode = mode or self.mode
        self.query = self.query if query is None else query
        self.results.clear()
        self.results.setHeaderLabels(['名稱', '用途' if self.mode == 'skill' else '專案', '狀態'])
        try:
            store = AgentStore(self.workspace.data)
            rows = store.value['skills' if self.mode == 'skill' else 'agents']
            for item in rows:
                description = item['description'] if self.mode == 'skill' else store.get('projects', item['project'])['name']
                if self.query.casefold() not in (item['name'] + ' ' + description).casefold():
                    continue
                state = ('已啟用' if item['enabled'] else '已停用') if self.mode == 'skill' else f'{len(item["skills"])} 個 Skill'
                if self.mode == 'skill' and item['managed']:
                    state += ' · 副本'
                row = QTreeWidgetItem([item['name'], description, state])
                row.setData(0, Qt.ItemDataRole.UserRole, item['id'])
                row.setToolTip(0, item.get('source', item['name']))
                row.setToolTip(1, description)
                self.results.addTopLevelItem(row)
                if item['id'] == selected:
                    self.results.setCurrentItem(row)
            self.message.setText(('尚未加入 Skill' if self.mode == 'skill' else '尚未設定 Agent') if not rows else
                                 '沒有符合的結果' if not self.results.topLevelItemCount() else '')
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))

    def job(self, title, work):
        self.message.setText(title)
        def done(_):
            self.populate()
        def failed(error):
            self.message.setText(str(error))
        if not self.page.jobs.start(title, work, done, failed, announce=False):
            self.message.setText('正在處理其他檔案，請稍候。')

    def refresh(self):
        if self.mode != 'skill':
            self.populate(); return
        def work(cancel):
            store = AgentStore(self.workspace.data)
            errors = []
            for skill in list(store.value['skills']):
                if cancel.is_set():
                    break
                try:
                    store.refresh_skill(skill['id'])
                except (ValueError, OSError) as exc:
                    errors.append(skill['name'] + '：' + str(exc))
            if errors:
                raise ValueError('\n'.join(errors))
        self.job('正在讀取技能來源…', work)

    def add(self):
        if self.mode == 'agent':
            self.open_agent(); return
        path = QFileDialog.getExistingDirectory(self, '加入包含 SKILL.md 的資料夾')
        if path:
            self.job('正在加入 Skill…', lambda cancel: AgentStore(self.workspace.data).add_skill(path))

    def copy_skill(self, source):
        self.job('正在建立完整管理副本…', lambda cancel: AgentStore(self.workspace.data).add_skill(source, managed=True))

    def open_agent(self, identity=None):
        try:
            dialog = AgentDialog(self, self.workspace.data, for_workspace(self.workspace), identity)
            dialog.finished.connect(lambda *_: self.populate())
            dialog.open()
            self.dialog = dialog
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))

    def open_item(self, item):
        identity = item.data(0, Qt.ItemDataRole.UserRole)
        if self.mode == 'agent':
            self.open_agent(identity)
        else:
            try:
                dialog = SkillDialog(self, self.workspace.data, identity)
                dialog.finished.connect(lambda *_: self.populate())
                dialog.open()
                self.dialog = dialog
            except (ValueError, OSError) as exc:
                self.message.setText(str(exc))
