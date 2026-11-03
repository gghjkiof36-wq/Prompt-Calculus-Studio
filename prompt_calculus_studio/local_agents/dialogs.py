"""Existing PCS dialogs and Markdown reading surface for local agent settings."""
import copy
import json
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QFileDialog, QFormLayout, QHeaderView, QLineEdit,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSizePolicy, QTabWidget, QTreeWidget, QTreeWidgetItem)

from ..widgets import ComboBox, ElidedLabel, RoundMenu, StudioDialog, button, label
from ..work_resources.reader import column, icon_button, line
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
        self.resize(780, 680)
        self.store, self.service = AgentStore(data), service
        self.identity = identity
        self._project_key, self._project_drafts = None, {}
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
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for title, control in [('名稱', self.name), ('模型', self.model)]:
            control.setAccessibleName('Agent ' + title)
            form.addRow(title, control)
        self.project.setAccessibleName('Agent 專案')
        project_row, project_layout = column()
        self.edit_project = icon_button('pencil', '編輯專案名稱與位置', self.toggle_project_details)
        self.edit_project.setCheckable(True)
        line(project_layout, self.project, self.edit_project)
        form.addRow('專案', project_row)
        self.body.addLayout(form)
        self.project_path = ElidedLabel('')
        self.project_path.setObjectName('Subtle')
        self.project_path.setMinimumWidth(0)
        self.project_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        project_layout.addWidget(self.project_path)
        self.project_name = QLineEdit()
        self.project_name.setPlaceholderText('專案名稱')
        self.project_name.setAccessibleName('專案名稱')
        self.cwd = QLineEdit()
        self.cwd.setReadOnly(True)
        self.cwd.textChanged.connect(self.cwd.setToolTip)
        self.cwd.setPlaceholderText('選擇實際工作目錄')
        self.cwd.setAccessibleName('Agent 工作目錄')
        self.project_details, details = column()
        details_form = QFormLayout()
        details_form.setHorizontalSpacing(16)
        details_form.setVerticalSpacing(12)
        details_form.addRow('專案名稱', self.project_name)
        folder_row, folder_layout = column()
        line(folder_layout, self.cwd, button('選擇…', self.pick_project, 'Quiet'))
        details_form.addRow('工作目錄', folder_row)
        # Keep the two forms on the same input edge when project details open.
        form_labels = [layout.itemAt(i, QFormLayout.ItemRole.LabelRole).widget()
                       for layout in (form, details_form) for i in range(layout.rowCount())]
        label_width = max(item.sizeHint().width() for item in form_labels)
        for item in form_labels:
            item.setMinimumWidth(label_width)
        details.addLayout(details_form)
        self.body.addWidget(self.project_details)
        self.sections = QTabWidget()
        self.sections.setAccessibleName('Agent 技能與專案來源')
        self.skills_page, skills_layout = column()
        self.sources_page, sources_layout = column()
        self.sections.addTab(self.skills_page, 'Skill')
        self.sections.addTab(self.sources_page, '專案規則與參考')
        self.body.addWidget(self.sections, 1)
        self.skill_filter = QLineEdit()
        self.skill_filter.setPlaceholderText('搜尋 Skill')
        self.skill_filter.setAccessibleName('搜尋可用 Skill')
        self.skill_filter.setClearButtonEnabled(True)
        self.skill_count = plain('', 'Subtle')
        line(skills_layout, self.skill_filter, None, self.skill_count)
        self.remove_source_button = icon_button('trash', '移除選取來源', self.remove_source)
        self.project_sharing = plain('', 'Subtle')
        line(sources_layout, self.project_sharing, None, button('加入…', self.add_source_menu, 'Quiet'), self.remove_source_button)
        self.sources = QTreeWidget()
        self.sources.setAccessibleName('專案規則與參考來源')
        self.sources.setHeaderLabels(['來源', '用途'])
        self.sources.setRootIsDecorated(False)
        self.sources.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.sources.setUniformRowHeights(True)
        self.sources.setStyleSheet('QTreeWidget {border:0;background:transparent;} QTreeWidget::item {padding:8px 6px; min-height:24px;}')
        self.sources.header().setStretchLastSection(False)
        self.sources.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sources.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.sources.setColumnWidth(1, 78)
        sources_layout.addWidget(self.sources, 1)
        self.sources.itemSelectionChanged.connect(self.update_counts)
        self.skills = QListWidget()
        self.skills.setAccessibleName('Agent 可用 Skill')
        self.skills.setUniformItemSizes(True)
        self.skills.setStyleSheet('QListWidget {border:0;background:transparent;} QListWidget::item {padding:10px 8px; min-height:24px;}')
        for skill in self.store.value['skills']:
            item = QListWidgetItem(skill['name'] + (' · 已停用' if not skill['enabled'] else ''))
            item.setData(Qt.ItemDataRole.UserRole, skill['id'])
            item.setData(Qt.ItemDataRole.UserRole + 1, skill['description'])
            item.setToolTip(skill['description'] + '\n' + skill['source'])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if skill['id'] in agent.get('skills', []) else Qt.CheckState.Unchecked)
            self.skills.addItem(item)
        skills_layout.addWidget(self.skills, 1)
        self.skills.itemChanged.connect(self.update_counts)
        self.skill_filter.textChanged.connect(self.filter_skills)
        self.message = plain('')
        self.message.hide()
        self.body.addWidget(self.message)
        line(self.body, None, button('取消', self.reject, 'Quiet'), button('儲存', self.save, 'Primary'))
        self.project.currentIndexChanged.connect(self.load_project)
        self.project.setCurrentIndex(max(0, self.project.findData(agent.get('project', ''))))
        self.load_project()

    def toggle_project_details(self):
        expanded = self.edit_project.isChecked() or not self.project.currentData()
        self.project_details.setVisible(expanded)
        self.project_path.setVisible(not expanded)

    def update_counts(self):
        if not hasattr(self, 'skills'):
            return
        selected = sum(self.skills.item(i).checkState() == Qt.CheckState.Checked for i in range(self.skills.count()))
        self.skill_count.setText(f'已選 {selected}')
        self.sections.setTabText(1, f'專案規則與參考 · {self.sources.topLevelItemCount()}')
        self.remove_source_button.setEnabled(bool(self.sources.selectedItems()))

    def filter_skills(self, text):
        query = text.strip().casefold()
        for i in range(self.skills.count()):
            item = self.skills.item(i)
            item.setHidden(query not in (item.text() + ' ' + item.data(Qt.ItemDataRole.UserRole + 1)).casefold())

    def show_error(self, text):
        self.message.setText(text)
        self.message.setVisible(bool(text))

    def load_project(self):
        if self._project_key is not None:
            self._project_drafts[self._project_key] = dict(name=self.project_name.text(), cwd=self.cwd.text(),
                sources=[dict(path=self.sources.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0],
                              kind=self.sources.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[1])
                         for i in range(self.sources.topLevelItemCount())])
        key = self.project.currentData()
        self.sources.clear()
        project = self._project_drafts.get(key)
        if project is None:
            project = self.store.get('projects', key) if key else {}
        self._project_key = key
        shared = sum(item['project'] == key and item['id'] != self.identity for item in self.store.value['agents']) if key else 0
        self.project_sharing.setText(f'修改會影響共用此專案的另 {shared} 個 Agent。' if shared else '')
        self.project_sharing.setVisible(bool(shared))
        self.edit_project.setToolTip('編輯專案名稱與位置' + (f'（與另 {shared} 個 Agent 共用）' if shared else ''))
        self.project_name.setText(project.get('name', ''))
        self.cwd.setText(project.get('cwd', ''))
        self.project_path.setText(project.get('cwd', ''))
        self.project_path.setToolTip(project.get('cwd', ''))
        self.edit_project.setChecked(False)
        self.edit_project.setEnabled(bool(key))
        self.toggle_project_details()
        for item in project.get('sources', []):
            self.add_source_row(item['path'], item['kind'])
        self.update_counts()

    def add_source_row(self, path, kind):
        if any(self.sources.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole)[0] == path for i in range(self.sources.topLevelItemCount())):
            return
        display = Path(path).parent.name + ' / ' + Path(path).name
        item = QTreeWidgetItem([display, '規則' if kind == 'rule' else '參考'])
        item.setToolTip(0, path)
        item.setData(0, Qt.ItemDataRole.UserRole, (path, kind))
        self.sources.addTopLevelItem(item)
        self.update_counts()

    def pick_project(self):
        folder = QFileDialog.getExistingDirectory(self, '選擇專案工作目錄', self.cwd.text())
        if folder:
            self.cwd.setText(folder)
            self.project_path.setText(folder)
            self.project_path.setToolTip(folder)
            if not self.project_name.text():
                self.project_name.setText(Path(folder).name)

    def add_source_menu(self):
        menu = RoundMenu(self)
        for title, kind in [('規則文件', 'rule'), ('參考文件', 'reference')]:
            action = menu.addAction(title)
            action.triggered.connect(lambda checked=False, k=kind: self.add_source(k))
        menu.addSeparator()
        menu.addAction('PCS 組別…', self.pcs_preset)
        menu.open_for(self.sender())
        self.source_menu = menu

    def add_source(self, kind='reference'):
        paths, _ = QFileDialog.getOpenFileNames(self, '加入規則或參考文件', self.cwd.text(), 'Markdown (*.md);;文字 (*.txt)')
        for path in paths:
            self.add_source_row(path, kind)

    def remove_source(self):
        for item in self.sources.selectedItems():
            self.sources.takeTopLevelItem(self.sources.indexOfTopLevelItem(item))
        self.update_counts()

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
                self.show_error('請先選擇 PCS 的實際工作樹。')
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
            skills = [self.skills.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.skills.count())
                      if self.skills.item(i).checkState() == Qt.CheckState.Checked]
            self.store.save_configuration(self.name.text(), self.model.currentData(), skills,
                self.project_name.text(), self.cwd.text(), sources,
                identity=self.identity, project_identity=self.project.currentData() or None)
        except ValueError as exc:
            self.show_error(str(exc)); return False
        except OSError as exc:
            self.show_error(f'無法確認儲存結果，請重新開啟設定確認。\n{exc}'); return False
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
        workspace.open_route('explore', 'local_agents')
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
