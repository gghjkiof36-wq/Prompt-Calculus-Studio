"""Existing PCS dialogs and Markdown reading surface for local agent settings."""
import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QCheckBox, QFileDialog, QFormLayout, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QTreeWidget, QTreeWidgetItem)

from ..widgets import ComboBox, RoundMenu, StudioDialog, button, label
from ..work_resources.reader import line
from .store import AgentStore, pcs_rules
from .sources import checked, read_bytes


def plain(text, role='Muted'):
    result = label(text, role, True)
    result.setTextFormat(Qt.TextFormat.PlainText)
    return result


class AgentDialog(StudioDialog):
    def __init__(self, parent, data, service, identity=None):
        super().__init__(parent)
        self.setWindowTitle('Agent 設定' if identity else '新增 Agent')
        self.resize(760, 730)
        self.store, self.service = AgentStore(data), service
        self.identity = identity
        agent = self.store.get('agents', identity) if identity else {}
        self.name = QLineEdit(agent.get('name', ''))
        self.name.setPlaceholderText('例如：本地 03-1')
        self.model = ComboBox()
        self.model.addItem('選擇本地模型', '')
        for model in service.models:
            self.model.addItem(model['name'], model['id'])
        self.model.setCurrentIndex(max(0, self.model.findData(agent.get('model', service.selected_model))))
        self.project = ComboBox()
        self.project.addItem('新增專案…', '')
        for project in self.store.value['projects']:
            self.project.addItem(project['name'], project['id'])
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        for title, control in [('名稱', self.name), ('模型', self.model), ('專案', self.project)]:
            control.setAccessibleName('Agent ' + title)
            form.addRow(title, control)
        self.body.addLayout(form)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText('專案名稱')
        self.cwd = QLineEdit()
        self.cwd.setReadOnly(True)
        self.cwd.textChanged.connect(self.cwd.setToolTip)
        self.cwd.setPlaceholderText('選擇實際工作目錄')
        self.cwd.setAccessibleName('Agent 工作目錄')
        line(self.body, self.project_name, self.cwd, button('選擇…', self.pick_project, 'Quiet'))
        line(self.body, plain('規則與參考', 'SectionTitle'), None,
             button('PCS 組別…', self.pcs_preset, 'Quiet'),
             button('加入…', self.add_source_menu, 'Quiet'), button('移除', self.remove_source, 'Quiet'))
        self.sources = QTreeWidget()
        self.sources.setHeaderLabels(['用途', '來源'])
        self.sources.setRootIsDecorated(False)
        self.sources.setMinimumHeight(112)
        self.sources.setStyleSheet('QTreeWidget::item {padding:5px 6px; min-height:24px;}')
        self.sources.setColumnWidth(0, 78)
        self.body.addWidget(self.sources, 1)
        self.body.addWidget(plain('可用 Skill', 'SectionTitle'))
        self.skills = QListWidget()
        self.skills.setMinimumHeight(100)
        self.skills.setStyleSheet('QListWidget::item {padding:6px 8px; min-height:24px;}')
        for skill in self.store.value['skills']:
            item = QListWidgetItem(skill['name'] + (' · 已停用' if not skill['enabled'] else ''))
            item.setData(Qt.ItemDataRole.UserRole, skill['id'])
            item.setToolTip(skill['description'] + '\n' + skill['source'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if skill['id'] in agent.get('skills', []) else Qt.CheckState.Unchecked)
            self.skills.addItem(item)
        self.body.addWidget(self.skills, 1)
        self.message = plain('')
        self.body.addWidget(self.message)
        line(self.body, None, button('取消', self.reject, 'Quiet'), button('儲存', self.save, 'Primary'))
        self.project.currentIndexChanged.connect(self.load_project)
        self.project.setCurrentIndex(max(0, self.project.findData(agent.get('project', ''))))
        self.load_project()

    def load_project(self):
        key = self.project.currentData()
        self.sources.clear()
        project = self.store.get('projects', key) if key else {}
        self.project_name.setText(project.get('name', ''))
        self.cwd.setText(project.get('cwd', ''))
        for item in project.get('sources', []):
            self.add_source_row(item['path'], item['kind'])

    def add_source_row(self, path, kind):
        if any(self.sources.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0] == path for i in range(self.sources.topLevelItemCount())):
            return
        display = Path(path).parent.name + ' / ' + Path(path).name
        item = QTreeWidgetItem(['規則' if kind == 'rule' else '參考', display])
        item.setToolTip(1, path)
        item.setData(0, Qt.ItemDataRole.UserRole, (path, kind))
        self.sources.addTopLevelItem(item)

    def pick_project(self):
        folder = QFileDialog.getExistingDirectory(self, '選擇專案工作目錄', self.cwd.text())
        if folder:
            self.cwd.setText(folder)
            if not self.project_name.text():
                self.project_name.setText(Path(folder).name)

    def add_source_menu(self):
        menu = RoundMenu(self)
        for title, kind in [('規則文件', 'rule'), ('參考文件', 'reference')]:
            action = menu.addAction(title)
            action.triggered.connect(lambda checked=False, k=kind: self.add_source(k))
        menu.open_for(self.sender())
        self.source_menu = menu

    def add_source(self, kind='reference'):
        paths, _ = QFileDialog.getOpenFileNames(self, '加入規則或參考文件', self.cwd.text(), 'Markdown (*.md);;文字 (*.txt)')
        for path in paths:
            self.add_source_row(path, kind)

    def remove_source(self):
        for item in self.sources.selectedItems():
            self.sources.takeTopLevelItem(self.sources.indexOfTopLevelItem(item))

    def pcs_preset(self):
        # Explicit shortcut. No scanning all group directories or handoff history.
        popup = StudioDialog(self)
        popup.setWindowTitle('PCS 組別規則')
        group = QLineEdit('03-1')
        group.setAccessibleName('PCS 組別編號')
        popup.body.addWidget(group)
        def choose():
            name = group.text().strip()
            if not __import__('re').fullmatch(r'\d{2}(?:-\d+)?', name):
                return
            candidates = [Path(self.cwd.text()), *Path(self.cwd.text()).parents]
            root = next((p for p in candidates if (p / 'obsidian工作區' / '交接管理庫' / '交接管理庫').is_dir()), None)
            if root is None:
                self.message.setText('請先選擇 PCS 的實際工作樹。')
            else:
                for path, kind in pcs_rules(root, name):
                    self.add_source_row(path, kind)
                if not self.name.text():
                    self.name.setText('本地 ' + name)
            popup.accept()
        line(popup.body, None, button('加入', choose, 'Primary'))
        popup.open()
        self.preset_dialog = popup

    def save(self):
        try:
            if not 1 <= len(self.name.text().strip()) <= 120 or not self.model.currentData():
                raise ValueError('請填寫 Agent 名稱並選擇本地模型。')
            sources = [self.sources.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.sources.topLevelItemCount())]
            project = self.store.save_project(self.project_name.text(), self.cwd.text(), sources, self.project.currentData() or None)
            skills = [self.skills.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.skills.count())
                      if self.skills.item(i).checkState() == Qt.CheckState.Checked]
            self.store.save_agent(self.name.text(), project['id'], self.model.currentData(), skills, self.identity)
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc)); return False
        self.accept()
        return True


class SkillDialog(StudioDialog):
    def __init__(self, parent, data, identity):
        super().__init__(parent)
        self.library = parent
        self.data, self.identity = data, identity
        self.store = AgentStore(data)
        self.skill = self.store.get('skills', identity)
        self.setWindowTitle(self.skill['name'])
        self.resize(760, 620)
        description = self.skill['description']
        summary = plain(description if len(description) <= 260 else description[:257] + '…')
        summary.setToolTip(description)
        self.body.addWidget(summary)
        self.body.addWidget(plain(('管理副本' if self.skill['managed'] else '引用來源') + ' · ' + self.skill['source']))
        self.enabled = QCheckBox('啟用 Skill')
        self.enabled.setChecked(self.skill['enabled'])
        self.enabled.toggled.connect(self.enable)
        self.body.addWidget(self.enabled)
        details = self.skill['metadata']
        constraints = []
        if self.skill['manual']:
            constraints.append('僅限 @ 手動使用')
        declared = details.get('allowed-tools', details.get('compatibility', ''))
        if declared:
            constraints.append('來源要求：' + str(declared))
        constraints.append('本地提供技能／專案讀取與搜尋；其他宿主工具及腳本尚未接入。')
        self.body.addWidget(plain('\n'.join(constraints)))
        self.files = QListWidget()
        self.files.setStyleSheet('QListWidget::item {padding:6px 8px; min-height:24px;}')
        self.files.setAccessibleName('Skill 附檔')
        for entry in sorted(self.skill['files'], key=lambda e: (e['path'] != 'SKILL.md', e['path'])):
            item = QListWidgetItem(entry['path'])
            self.files.addItem(item)
        self.files.itemActivated.connect(lambda item: self.open_file(item.text()))
        self.body.addWidget(self.files, 1)
        self.message = plain('')
        self.body.addWidget(self.message)
        line(self.body, button('重新讀取', self.refresh, 'Quiet'), None,
             button('建立管理副本', self.copy, 'Quiet') if not self.skill['managed'] else None,
             button('閱讀 Skill', lambda: self.open_file('SKILL.md'), 'Primary'))

    def enable(self, enabled):
        try:
            self.store.enable_skill(self.identity, enabled)
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))
            self.enabled.blockSignals(True)
            self.enabled.setChecked(not enabled)
            self.enabled.blockSignals(False)

    def refresh(self):
        try:
            self.store.reload()
            self.skill = self.store.refresh_skill(self.identity)
            self.files.clear()
            self.files.addItems(sorted([e['path'] for e in self.skill['files']], key=lambda p: (p != 'SKILL.md', p)))
            self.message.setText('已重新讀取')
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))

    def copy(self):
        # Route the complete folder through the management page's background job.
        self.library.copy_skill(self.skill['source'])
        self.accept()

    def open_file(self, relative):
        try:
            root = Path(self.skill['source']).resolve(strict=True)
            if str(root) != self.skill['root']:
                raise ValueError('技能來源連結已變更，請重新加入。')
            path = checked(root, relative)
            if path.suffix.casefold() != '.md':
                # Other attachments remain inert, searchable text; scripts never run.
                from .sources import text_bytes
                dialog = StudioDialog(self)
                dialog.setWindowTitle(relative)
                view = QPlainTextEdit(text_bytes(read_bytes(path)))
                view.setReadOnly(True)
                dialog.body.addWidget(view)
                dialog.resize(820, 620)
            else:
                from ..work_resources.markdown_preview import MarkdownPreview
                dialog = MarkdownPreview(self, path, read_bytes(path), editable=self.skill['managed'])
            dialog.finished.connect(lambda *_: self.refresh() if self.skill['managed'] else None)
            dialog.open()
            self.reader = dialog
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc))


class BindingDialog(StudioDialog):
    def __init__(self, page):
        super().__init__(page)
        self.setWindowTitle('協作設定')
        self.resize(580, 260)
        self.document = page.chat_document
        self.owner = self.document.key, self.document.owner_epoch
        self.agents = ComboBox()
        self.agents.addItem('一般對話', '')
        store = AgentStore(self.document.workspace.data)
        for agent in store.value['agents']:
            self.agents.addItem(agent['name'], agent['id'])
        current = self.document.payload.get('agent_id', '')
        self.agents.setCurrentIndex(max(0, self.agents.findData(current)))
        self.body.addWidget(self.agents)
        self.details = plain('')
        self.body.addWidget(self.details)
        self.agents.currentIndexChanged.connect(self.describe)
        self.describe()
        self.message = plain('')
        self.body.addWidget(self.message)
        line(self.body, button('管理 Agent', self.manage, 'Quiet'), None, button('套用', self.apply, 'Primary'))

    def describe(self):
        key = self.agents.currentData()
        if not key:
            self.details.setText('使用原有的聊天模型與設定。')
            return
        store = AgentStore(self.document.workspace.data)
        agent = store.get('agents', key)
        project = store.get('projects', agent['project'])
        skills = [store.get('skills', identity) for identity in agent['skills']]
        names = ['@' + s['name'] for s in skills if s['enabled'] and s['invocable']]
        self.details.setText(project['name'] + '\n' + ('、'.join(names) if names else '未設定可手動指定的 Skill'))

    def manage(self):
        workspace = self.document.workspace
        workspace.work_resources_mode = 'agent'
        workspace.open_route('explore', 'work_resources')
        self.accept()

    def apply(self):
        doc = self.document
        if doc._disposed or (doc.key, doc.owner_epoch) != self.owner:
            self.message.setText('來源對話已關閉或另存。'); return
        try:
            if not doc.set_agent(self.agents.currentData()):
                raise ValueError(doc.error or '請先結束本輪回覆，再切換 Agent。')
        except (ValueError, OSError) as exc:
            self.message.setText(str(exc)); return
        self.accept()


def show_evidence(parent, evidence):
    dialog = StudioDialog(parent)
    dialog.setWindowTitle('本輪讀取紀錄')
    dialog.resize(860, 670)
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setPlainText('\n\n'.join(e['operation'] + ' · ' + e['target'] + '\n' + e['content']
                                 for e in evidence.get('events', []) if e['operation'] != 'accepted'))
    dialog.body.addWidget(view)
    dialog.open()
    parent.evidence_dialog = dialog


def request_read_approval(document, request, ticket):
    from PySide6.QtWidgets import QWidget
    parent = document.workspace if isinstance(document.workspace, QWidget) else None
    dialog = StudioDialog(parent)
    dialog.setWindowTitle('核准讀取 · ' + document.title)
    dialog.resize(560, 260)
    action = ticket['action']
    titles = {'read_file': '讀取專案檔案', 'search_files': '搜尋專案', 'read_skill': '讀取 Skill',
              'read_source': '讀取規則或參考', 'list_skills': '搜尋 Skill'}
    dialog.body.addWidget(plain(titles.get(action.operation, action.operation), 'SectionTitle'))
    dialog.body.addWidget(plain(action.scope['arguments']))
    line(dialog.body, None, button('拒絕', dialog.reject, 'Quiet'), button('允許本次', dialog.accept, 'Primary'))
    def done(result):
        ticket['allow'] = result == dialog.DialogCode.Accepted and document._owns(request)
        ticket['done'].set()
        timer.stop()
    timer = QTimer(dialog)
    timer.setInterval(200)
    timer.timeout.connect(lambda: dialog.reject() if not document._owns(request) or ticket['done'].is_set() else None)
    dialog.finished.connect(done)
    timer.start()
    dialog.open()
    document._approval_dialog = dialog
