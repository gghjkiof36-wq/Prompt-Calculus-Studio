"""One selected skill at a time; catalog browsing never reads every source."""
from pathlib import Path
import re

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import QCheckBox, QFormLayout, QLineEdit, QPlainTextEdit, QSizePolicy, QStackedWidget, QTabWidget, QTextBrowser, QVBoxLayout, QWidget

from ..widgets import ElidedLabel, RoundMenu, button, panel, scrolling
from ..work_resources.reader import column, icon_button, line
from .dialogs import plain
from .editors import confirm_draft, listing
from .sources import checked, digest, read_bytes, text_bytes
from .store import AgentStore


class OfflineText(QTextBrowser):
    def loadResource(self, kind, url):
        # Native Markdown renderer, with no network or implicit file reads.
        return None


def skill_version(skill):
    meta = skill.get('metadata', {}).get('metadata', {})
    version = meta.get('version') if isinstance(meta, dict) else None
    return str(version) if isinstance(version, (str, int, float)) else ''


def presentation(raw):
    """Read only declared display strings from the already enrolled small file."""
    import yaml
    try:
        if any(isinstance(t, (yaml.AliasToken, yaml.AnchorToken)) for t in yaml.scan(raw)):
            return {}
        value = yaml.safe_load(raw)
        interface = value.get('interface', {}) if isinstance(value, dict) else {}
        return {key: text.strip() for key, text in interface.items()
                if key in ('display_name', 'short_description') and isinstance(text, str) and 0 < len(text.strip()) <= 300}
    except (yaml.YAMLError, AttributeError, RecursionError):
        return {}


class SkillDetails(QWidget):
    updated = Signal()

    def __init__(self, library):
        super().__init__(library)
        self.library, self.jobs = library, library.page.jobs
        self.skill = None
        self.ticket = 0
        self.pending = None
        self.editor = None
        self.path, self.raw = None, b''
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(0, 0, 0, 0)
        self.body.setSpacing(16)
        self.name = ElidedLabel('')
        self.name.setObjectName('Heading'); self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.enabled = QCheckBox('啟用')
        self.enabled.setAccessibleName('啟用 Skill')
        self.enabled.toggled.connect(self.enable)
        self.more = icon_button('more-horizontal', 'Skill 操作', self.menu)
        title_row = line(self.body, self.name, self.enabled, self.more)
        title_row.setStretch(0, 1)
        self.description = ElidedLabel('')
        self.description.setMinimumWidth(0)
        self.description.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.description.setObjectName('Subtle')
        self.body.addWidget(self.description)
        self.pages = QStackedWidget()
        overview, summary = column()
        summary.setSpacing(20)
        self.read_button = button('查看內容', self.show_reader, 'Quiet')
        line(summary, plain('詳細資訊', 'Heading'), None, self.read_button)
        info, rows = panel('SettingsGroup')
        self.info_form = QFormLayout()
        self.info_form.setHorizontalSpacing(32); self.info_form.setVerticalSpacing(24)
        self.info_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        rows.addLayout(self.info_form)
        self.version = plain('')
        self.info_form.addRow(plain('版本', 'Subtle'), self.version)
        self.usage = plain('')
        self.info_form.addRow(plain('使用方式', 'Subtle'), self.usage)
        self.file_count = button('', self.show_files, 'Quiet')
        self.file_count.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        self.info_form.addRow(plain('檔案', 'Subtle'), self.file_count)
        self.source = ElidedLabel('')
        self.source.setMinimumWidth(0)
        self.source.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.info_form.addRow(plain('來源', 'Subtle'), self.source)
        summary.addWidget(info)
        summary.addStretch()
        self.pages.addWidget(scrolling(overview))
        reader, reading = column()
        line(reading, button('返回詳情', self.show_overview, 'Quiet'), None)
        self.tabs = QTabWidget()
        self.tabs.setAccessibleName('Skill 內容與附檔')
        self.document, document = column()
        document.setContentsMargins(0, 16, 0, 0)
        self.document_layout = document
        self.current_file = plain('SKILL.md', 'Subtle')
        self.find = QLineEdit()
        self.find.setPlaceholderText('搜尋內容')
        self.find.setAccessibleName('搜尋 Skill 內容')
        self.find.setClearButtonEnabled(True)
        self.find.setMaximumWidth(220)
        self.find.returnPressed.connect(self.find_next)
        self.find.textChanged.connect(self.find_first)
        line(document, self.current_file, None, self.find)
        self.view = OfflineText()
        self.view.setOpenLinks(False)
        self.view.setOpenExternalLinks(False)
        self.view.setAccessibleName('Skill 正文')
        self.view.setStyleSheet('QTextBrowser {border:0;background:transparent;}')
        self.view.anchorClicked.connect(self.follow_link)
        document.addWidget(self.view, 1)
        self.files = listing(['檔案', '大小'], 'Skill 附檔')
        self.files.itemActivated.connect(lambda row: self.open_file(row.text(0)))
        self.tabs.addTab(self.document, '內容')
        self.tabs.addTab(self.files, '附檔')
        reading.addWidget(self.tabs, 1)
        self.pages.addWidget(reader)
        self.body.addWidget(self.pages, 1)
        self.message = plain('')
        self.message.hide()
        self.body.addWidget(self.message)
        self.edit_button = button('編輯副本', self.edit, 'Quiet')
        self.save_button = button('儲存', self.save, 'Primary')
        self.cancel_button = button('取消編輯', self.discard, 'Quiet')
        line(reading, None, self.edit_button, self.cancel_button, self.save_button)
        self.save_button.hide(); self.cancel_button.hide(); self.edit_button.hide()
        self.read_timer = QTimer(self)
        self.read_timer.setSingleShot(True)
        self.read_timer.setInterval(100)
        self.read_timer.timeout.connect(self.start_read)
        self.jobs.became_idle.connect(self.start_read)

    @property
    def dirty(self):
        return self.editor is not None and self.editor.dirty

    def show_error(self, text):
        self.message.setText(str(text)); self.message.setVisible(bool(text))

    def can_leave(self):
        if not self.dirty:
            return True
        result = confirm_draft(self, self.save)
        if result == 'discarded':
            self.discard()
        return result != 'cancelled'

    def clear_editor(self):
        if self.editor is not None:
            self.document_layout.removeWidget(self.editor)
            self.editor.hide(); self.editor.deleteLater(); self.editor = None
        self.view.show()
        self.save_button.hide(); self.cancel_button.hide()

    def select(self, skill):
        self.ticket += 1; self.pending = None; self.read_timer.stop()
        self.clear_editor()
        self.skill = skill
        self.name.setText(skill['name'])
        self.name.setToolTip(skill['name'])
        self.description.setText(skill['description'])
        self.description.setToolTip(skill['description'])
        source = Path(skill['source'])
        self.source.setText(source.parent.name + ' / ' + source.name)
        self.source.setToolTip(skill['source'])
        self.version.setText(skill_version(skill))
        self.info_form.setRowVisible(self.version, bool(self.version.text()))
        self.usage.setText('指定使用' if skill.get('manual') else '可自動選用')
        self.file_count.setText(f'{len(skill["files"])} 個檔案')
        self.enabled.blockSignals(True); self.enabled.setChecked(skill['enabled']); self.enabled.blockSignals(False)
        from PySide6.QtWidgets import QTreeWidgetItem
        self.files.clear()
        for entry in sorted(skill['files'], key=lambda e: (e['path'] != 'SKILL.md', e['path'].casefold())):
            row = QTreeWidgetItem([entry['path'], f'{entry["size"]:,} B'])
            row.setToolTip(0, entry['path']); self.files.addTopLevelItem(row)
        self.tabs.setTabText(1, f'檔案 · {len(skill["files"])}')
        self.open_file('SKILL.md', reveal=False)
        self.pages.setCurrentIndex(0)

    def show_reader(self):
        self.pages.setCurrentIndex(1)

    def show_files(self):
        self.show_reader(); self.tabs.setCurrentIndex(1)

    def show_overview(self):
        if self.can_leave():
            self.pages.setCurrentIndex(0)

    def open_file(self, relative, *, reveal=True):
        if not self.skill or not self.can_leave():
            return False
        if relative not in {entry['path'] for entry in self.skill['files']}:
            self.show_error('此檔案不在 Skill 的附檔清單中。'); return False
        self.ticket += 1
        self.clear_editor(); self.view.clear(); self.edit_button.hide()
        self.path, self.raw = None, b''
        self.current_file.setText(relative); self.find.clear()
        self.tabs.setCurrentIndex(0)
        if reveal:
            self.show_reader()
        self.show_error('正在讀取…')
        self.pending = (self.ticket, dict(self.skill), relative)
        self.read_timer.start()
        return True

    def start_read(self):
        if self.pending is None or self.jobs.active or self.read_timer.isActive():
            return
        token, skill, relative = self.pending
        self.pending = None
        def work(cancel):
            root = Path(skill['source']).resolve(strict=True)
            if str(root) != skill['root']:
                raise ValueError('技能來源連結已變更，請重新加入。')
            path = checked(root, relative)
            display = {}
            if relative == 'SKILL.md':
                entry = next((e for e in skill['files'] if e['path'] == 'agents/openai.yaml' and e['size'] <= 32768), None)
                if entry:
                    try:
                        raw = read_bytes(checked(root, entry['path']), 32768)
                        if digest(raw) == entry['sha256']:
                            display = presentation(raw)
                    except (ValueError, OSError):
                        pass  # Optional presentation must not hide the selected document.
            return path, read_bytes(path), display
        def done(result):
            if token != self.ticket:
                return
            self.path, self.raw, display = result
            if display.get('display_name'):
                self.name.setText(display['display_name'])
            if display.get('short_description'):
                self.description.setText(display['short_description'])
            try:
                self.render()
            except (ValueError, OSError) as exc:
                self.show_error(exc)
        def failed(error):
            if token == self.ticket:
                self.show_error(error)
        if not self.jobs.start('讀取 Skill', work, done, failed, announce=False):
            self.pending = (token, skill, relative)

    def render(self):
        text = text_bytes(self.raw)
        if self.path.suffix.casefold() == '.md':
            if self.path.name == 'SKILL.md':
                text = re.sub(r'\A---\r?\n.*?\r?\n---(?:\r?\n|$)', '', text, count=1, flags=re.S)
            self.view.setMarkdown(text)
        else:
            self.view.setPlainText(text)
        self.edit_button.setVisible(self.skill['managed'] and self.path.suffix.casefold() == '.md')
        self.show_error('')

    def find_first(self):
        self.view.moveCursor(self.view.textCursor().MoveOperation.Start)
        self.find_next()

    def find_next(self):
        if self.find.text() and not self.view.find(self.find.text()):
            self.view.moveCursor(self.view.textCursor().MoveOperation.Start)
            self.view.find(self.find.text())

    def follow_link(self, url):
        if url.scheme() or url.isLocalFile():
            return
        relative = (Path(self.current_file.text()).parent / url.path()).as_posix()
        self.open_file(relative)

    def enable(self, enabled):
        if self.skill is None:
            return
        try:
            AgentStore(self.library.workspace.data).enable_skill(self.skill['id'], enabled)
            self.skill['enabled'] = enabled
            self.updated.emit()
        except (ValueError, OSError) as exc:
            self.enabled.blockSignals(True); self.enabled.setChecked(not enabled); self.enabled.blockSignals(False)
            self.show_error(exc)

    def menu(self):
        if not self.skill:
            return
        popup = RoundMenu(self)
        popup.addAction('重新讀取來源', self.refresh)
        if not self.skill['managed']:
            popup.addAction('建立管理副本', lambda: self.library.copy_skill(self.skill['source']))
        popup.open_for(self.more); self.popup = popup

    def refresh(self):
        if not self.can_leave():
            return
        identity = self.skill['id']
        token = self.ticket
        reading = self.pages.currentIndex() == 1
        def done(item):
            self.updated.emit()
            if token == self.ticket and self.skill['id'] == identity:
                self.select(item)
                if reading:
                    self.show_reader()
        self.library.job('正在讀取 Skill…', lambda cancel: AgentStore(self.library.workspace.data).refresh_skill(identity), done)

    def edit(self):
        if not self.path or self.editor is not None or not self.skill['managed']:
            return
        try:
            from ..workspace_files import MarkdownPage
            editor = MarkdownPage(self.path)
            if editor.baseline != self.raw:
                editor.deleteLater()
                raise ValueError('檔案已變更，請重新讀取後編輯。')
            self.editor = editor
            editor.file_title.setEnabled(False)
            editor.md_surface.apply_mode('write', focus=False)
            editor.md_surface.mode_box.hide(); editor.md_surface.view_tools.hide()
            self.document_layout.addWidget(editor, 1)
            self.view.hide(); self.edit_button.hide()
            self.save_button.show(); self.cancel_button.show()
        except (ValueError, OSError) as exc:
            self.show_error(exc)

    def save(self):
        if self.editor is None:
            return True
        if not self.editor.save():
            self.show_error(self.editor.error); return False
        self.raw = self.editor.baseline
        self.clear_editor(); self.render()
        self.refresh()
        return True

    def discard(self):
        self.clear_editor()
        if self.path is not None:
            self.render()
