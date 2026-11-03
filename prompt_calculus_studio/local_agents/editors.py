"""Project-owned forms. Drafts are accepted only by the existing registry writer."""
from pathlib import Path
import re

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QFileDialog, QFormLayout, QHeaderView,
    QLineEdit, QMessageBox, QTabWidget, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget)

from ..widgets import ComboBox, RoundMenu, StudioDialog, button
from ..work_resources.reader import column, icon_button, line
from .dialogs import plain
from .store import AgentStore, pcs_common_rules, pcs_role_rules
from .catalog_view import CatalogDelegate


def confirm_draft(parent, save):
    prompt = QMessageBox(parent)
    prompt.setWindowTitle('尚未儲存')
    prompt.setText('是否儲存目前的變更？')
    yes = prompt.addButton('儲存', QMessageBox.ButtonRole.AcceptRole)
    no = prompt.addButton('捨棄變更', QMessageBox.ButtonRole.DestructiveRole)
    prompt.addButton('取消', QMessageBox.ButtonRole.RejectRole)
    prompt.exec()
    if prompt.clickedButton() == yes:
        return 'saved' if save() else 'cancelled'
    return 'discarded' if prompt.clickedButton() == no else 'cancelled'


def listing(headers, name):
    view = QTreeWidget()
    view.setObjectName('ResourceList')
    view.header().setObjectName('ResourceHeader')
    view.setAccessibleName(name)
    view.setRootIsDecorated(False)
    view.setUniformRowHeights(True)
    view.setHeaderLabels(headers)
    view.setTextElideMode(Qt.TextElideMode.ElideRight)
    view.setStyleSheet('QTreeWidget {border:0;background:transparent;} '
                      'QTreeWidget::item {padding:8px 6px; min-height:24px;}')
    view.header().setStretchLastSection(True)
    view.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Interactive)
    view.setColumnWidth(0, 220)
    return view


class DraftEditor(QWidget):
    saved = Signal(object)
    cancelled = Signal()

    def __init__(self, data, identity):
        super().__init__()
        self.store, self.identity = AgentStore(data), identity
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)
        self.message = plain('')
        self.message.hide()
        self.save_button = button('儲存', self.save, 'Primary')
        self.cancel_button = button('取消', self.cancel, 'Quiet')
        self.draft_state = plain('', 'Subtle')

    def finish_form(self):
        self.body.addWidget(self.message)
        line(self.body, self.draft_state, None, self.cancel_button, self.save_button)
        self.baseline = self.value()
        self.changed()

    @property
    def dirty(self):
        return self.value() != self.baseline

    def changed(self, *_):
        if not hasattr(self, 'baseline'):
            return
        self.draft_state.setText('未儲存' if self.dirty else '')
        self.save_button.setEnabled(self.dirty or self.identity is None)

    def show_error(self, error):
        self.message.setText(str(error))
        self.message.setVisible(bool(error))

    def can_leave(self):
        if not self.dirty:
            return True
        answer = confirm_draft(self, self.save)
        if answer == 'discarded':
            self.restore(self.baseline)
        return answer != 'cancelled'

    def cancel(self):
        if self.can_leave():
            self.cancelled.emit()


class SourcePanel(QWidget):
    changed = Signal()

    def __init__(self, cwd, sources, *, role=False):
        super().__init__()
        self.cwd, self.role = cwd, role
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0); layout.setSpacing(12)
        self.remove_button = icon_button('trash', '移除選取來源', self.remove)
        line(layout, plain('Agent 專屬來源' if role else '專案共用來源', 'SectionTitle'), None,
             button('加入', self.menu, 'Quiet'), self.remove_button)
        self.sources = listing(['來源', '用途'], '規則與參考來源')
        self.sources.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.sources.header().setStretchLastSection(False)
        self.sources.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.sources.header().setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        self.sources.setColumnWidth(1, 76)
        self.sources.itemSelectionChanged.connect(lambda: self.remove_button.setEnabled(bool(self.sources.selectedItems())))
        self.remove_button.setEnabled(False)
        layout.addWidget(self.sources, 1)
        self.message = plain(''); self.message.hide(); layout.addWidget(self.message)
        self.restore([(s['path'], s['kind']) for s in sources])

    def value(self):
        return [self.sources.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) for i in range(self.sources.topLevelItemCount())]

    def restore(self, sources):
        self.sources.clear()
        for path, kind in sources:
            self.add_row(path, kind)

    def add_row(self, path, kind):
        if any(p == path for p, _ in self.value()):
            return
        row = QTreeWidgetItem([Path(path).parent.name + ' / ' + Path(path).name, '規則' if kind == 'rule' else '參考'])
        row.setData(0, Qt.ItemDataRole.UserRole, (path, kind)); row.setToolTip(0, path)
        self.sources.addTopLevelItem(row); self.changed.emit()

    def menu(self):
        popup = RoundMenu(self)
        for text, kind in [('規則文件', 'rule'), ('參考文件', 'reference')]:
            popup.addAction(text, lambda _=False, k=kind: self.add_files(k))
        popup.addSeparator()
        popup.addAction('PCS 組別…' if self.role else 'PCS 共用規則', self.pcs_preset)
        popup.open_for(self.sender()); self.popup = popup

    def add_files(self, kind):
        paths, _ = QFileDialog.getOpenFileNames(self, '加入規則或參考', self.cwd(), '文字 (*.md *.txt)')
        for path in paths:
            self.add_row(path, kind)

    def remove(self):
        for row in self.sources.selectedItems():
            self.sources.takeTopLevelItem(self.sources.indexOfTopLevelItem(row))
        self.changed.emit()

    def pcs_preset(self):
        location = Path(self.cwd())
        root = next((p for p in [location, *location.parents] if (p/'obsidian工作區/交接管理庫/交接管理庫').is_dir()), None)
        if root is None:
            self.message.setText('請先選擇 PCS 的工作資料夾。'); self.message.show(); return
        self.message.hide()
        if not self.role:
            for path, kind in pcs_common_rules(root):
                self.add_row(path, kind)
            return
        popup = StudioDialog(self)
        popup.setWindowTitle('PCS 組別規則')
        group = QLineEdit(); group.setPlaceholderText('組別，例如 03-1')
        popup.body.addWidget(group)
        def choose():
            if not re.fullmatch(r'\d{2}(?:-\d+)?', group.text().strip()):
                return
            for path, kind in pcs_role_rules(root, group.text().strip()):
                self.add_row(path, kind)
            popup.accept()
        line(popup.body, None, button('加入', choose, 'Primary'))
        popup.open(); self.preset_dialog = popup


class AgentEditor(DraftEditor):
    def __init__(self, data, service, project, identity=None):
        super().__init__(data, identity)
        self.project = self.store.get('projects', project)
        agent = self.store.get('agents', identity) if identity else {}
        if agent and agent['project'] != project:
            raise ValueError('此 Agent 不屬於目前專案。')
        self.body.setSpacing(24)
        self.name = QLineEdit(agent.get('name', ''))
        self.name.setPlaceholderText('Agent 名稱')
        self.name.setAccessibleName('Agent 名稱')
        self.model = ComboBox()
        self.model.setAccessibleName('Agent 模型')
        self.model.addItem('選擇模型', '')
        for model in service.models:
            self.model.addItem(model['name'], model['id'])
        model_key = agent.get('model', service.selected_model)
        if model_key and self.model.findData(model_key) < 0:
            self.model.addItem('原模型已移除', model_key)
        self.model.setCurrentIndex(max(0, self.model.findData(model_key)))
        fields, form = column()
        form.setSpacing(8)
        name_label = plain('名稱', 'Subtle'); name_label.setBuddy(self.name)
        model_label = plain('模型', 'Subtle'); model_label.setBuddy(self.model)
        form.addWidget(name_label); form.addWidget(self.name)
        form.addSpacing(12)
        form.addWidget(model_label); form.addWidget(self.model)
        self.body.addWidget(fields)
        self.sections = QTabWidget()
        skill_page, skill_body = column()
        skill_body.setContentsMargins(0, 16, 0, 0)
        self.skill_filter = QLineEdit()
        self.skill_filter.setPlaceholderText('搜尋 Skill')
        self.skill_filter.setAccessibleName('搜尋可指派 Skill')
        self.skill_filter.setClearButtonEnabled(True)
        self.skill_count = plain('', 'Subtle')
        line(skill_body, self.skill_filter, self.skill_count)
        self.skills = listing(['Skill'], 'Agent 可用 Skill')
        self.skills.setObjectName('AgentSkillList')
        self.skills.setHeaderHidden(True)
        self.skills.setMouseTracking(True)
        self.skills.setItemDelegate(CatalogDelegate(self.skills))
        self.skills.setStyleSheet('QTreeWidget {border:0;background:transparent;}')
        self.selected = list(agent.get('skills', []))
        for skill in sorted(self.store.value['skills'], key=lambda s: (s['id'] not in self.selected, s['name'].casefold())):
            description = skill['description'].replace('\n', ' ')
            row = QTreeWidgetItem([skill['name'] + (' · 已停用' if not skill['enabled'] else '') + '\n' + description])
            row.setData(0, Qt.ItemDataRole.UserRole, skill['id'])
            row.setToolTip(0, skill['name'] + '\n' + skill['description'])
            row.setFlags(row.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            row.setCheckState(0, Qt.CheckState.Checked if skill['id'] in self.selected else Qt.CheckState.Unchecked)
            self.skills.addTopLevelItem(row)
        skill_body.addWidget(self.skills, 1)
        self.empty = plain('先到 Skill 加入技能，再指派給 Agent。')
        self.empty.setVisible(not self.skills.topLevelItemCount())
        skill_body.addWidget(self.empty)
        self.sources = SourcePanel(lambda: self.project['cwd'], agent.get('sources', []), role=True)
        self.sections.addTab(skill_page, 'Skill')
        self.sections.addTab(self.sources, '規則與參考')
        self.body.addWidget(self.sections, 1)
        self.finish_form()
        self.skills.itemChanged.connect(self.skill_changed)
        self.skill_filter.textChanged.connect(self.filter_skills)
        self.name.textChanged.connect(self.changed)
        self.model.currentIndexChanged.connect(self.changed)
        self.sources.changed.connect(self.changed)
        self.update_count()

    def value(self):
        return dict(name=self.name.text(), model=self.model.currentData(), skills=list(self.selected), sources=self.sources.value())

    def restore(self, value):
        self.name.setText(value['name'])
        self.model.setCurrentIndex(max(0, self.model.findData(value['model'])))
        self.selected = list(value['skills'])
        self.skills.blockSignals(True)
        for i in range(self.skills.topLevelItemCount()):
            row = self.skills.topLevelItem(i)
            row.setCheckState(0, Qt.CheckState.Checked if row.data(0, Qt.ItemDataRole.UserRole) in self.selected else Qt.CheckState.Unchecked)
        self.skills.blockSignals(False)
        self.sources.restore(value['sources'])
        self.update_count(); self.changed(); self.show_error('')

    def update_count(self):
        self.skill_count.setText(f'已指派 {len(self.selected)}')

    def filter_skills(self, text):
        query = text.strip().casefold()
        for i in range(self.skills.topLevelItemCount()):
            row = self.skills.topLevelItem(i)
            row.setHidden(query not in row.text(0).casefold())

    def skill_changed(self, row, column):
        key = row.data(0, Qt.ItemDataRole.UserRole)
        if row.checkState(0) == Qt.CheckState.Checked:
            if key not in self.selected:
                self.selected.append(key)
        elif key in self.selected:
            self.selected.remove(key)
        self.update_count(); self.changed()

    def save(self):
        try:
            value = self.value()
            item = self.store.save_agent(value['name'], self.project['id'], value['model'], value['skills'], self.identity, sources=value['sources'])
        except (ValueError, OSError) as exc:
            self.show_error(exc); return False
        self.identity = item['id']
        self.baseline = self.value()
        self.changed(); self.show_error('')
        self.saved.emit(item)
        return True


class ProjectEditor(DraftEditor):
    def __init__(self, data, identity=None):
        super().__init__(data, identity)
        project = self.store.get('projects', identity) if identity else {}
        self.body.addWidget(plain('專案設定' if identity else '新增專案', 'Heading'))
        self.name = QLineEdit(project.get('name', ''))
        self.name.setPlaceholderText('專案名稱')
        self.name.setAccessibleName('專案名稱')
        self.cwd = QLineEdit(project.get('cwd', ''))
        self.cwd.setReadOnly(True)
        self.cwd.setPlaceholderText('選擇工作資料夾')
        self.cwd.setAccessibleName('專案工作目錄')
        self.cwd.textChanged.connect(self.cwd.setToolTip)
        self.cwd.setToolTip(self.cwd.text())
        folder, layout = column()
        line(layout, self.cwd, icon_button('folder', '選擇工作資料夾', self.pick_folder))
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.setVerticalSpacing(12)
        form.addRow('名稱', self.name)
        form.addRow('位置', folder)
        self.body.addLayout(form)
        self.sources = SourcePanel(self.cwd.text, project.get('sources', []))
        self.body.addWidget(self.sources, 1)
        self.finish_form()
        self.sources.changed.connect(self.changed)
        self.name.textChanged.connect(self.changed)
        self.cwd.textChanged.connect(self.changed)

    def value(self):
        return dict(name=self.name.text(), cwd=self.cwd.text(),
                    sources=self.sources.value())

    def restore(self, value):
        self.name.setText(value['name']); self.cwd.setText(value['cwd'])
        self.sources.restore(value['sources'])
        self.changed(); self.show_error('')

    def pick_folder(self):
        folder = QFileDialog.getExistingDirectory(self, '選擇專案工作資料夾', self.cwd.text())
        if folder:
            self.cwd.setText(folder)
            if not self.name.text():
                self.name.setText(Path(folder).name)

    def save(self):
        try:
            value = self.value()
            item = self.store.save_project(value['name'], value['cwd'], value['sources'], self.identity)
        except (ValueError, OSError) as exc:
            self.show_error(exc); return False
        self.identity = item['id']; self.baseline = self.value()
        self.changed(); self.show_error(''); self.saved.emit(item)
        return True
