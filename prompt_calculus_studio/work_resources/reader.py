"""On-demand, offline reading. No source content is rendered in the resource browser."""
from PySide6.QtCore import Qt, QEvent, QSize
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor
from PySide6.QtWidgets import QHBoxLayout, QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget
from shiboken6 import isValid

from ..conversation_archive import decode_json
from ..markdown_source import SourceLedger, python_position, utf16_position
from ..widgets import ComboBox, StudioDialog, button, label
from ..ui_icons import icon
from . import bundles

CHUNK = 60000


def column(parent=None):
    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(12)
    return widget, layout


def line(layout, *widgets):
    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    for widget in widgets:
        row.addStretch() if widget is None else row.addWidget(widget)
    layout.addLayout(row)
    return row


def viewer(name):
    view = QPlainTextEdit()
    view.setReadOnly(True)
    view.setAccessibleName(name)
    return view


def icon_button(name, title, callback):
    control = button('', callback, 'IconButton')
    control.setIcon(icon(name))
    control.setIconSize(QSize(20, 20))
    control.setFixedSize(36, 36)
    control.setToolTip(title)
    control.setAccessibleName(title)
    return control


def conversation_text(messages, focused=''):
    chunks, position, focus = [], 0, None
    roles = {'user': '你', 'assistant': '助理', 'system': '系統', 'tool': '工具', 'developer': '開發者'}
    for message in messages:
        if message['role'] == 'root':
            continue
        # Technical node identifiers belong in retained metadata, never in the reading flow.
        part = f"{roles.get(message['role'], message['role'])}\n{message['text']}\n\n"
        if message['node_id'] == focused:
            focus = position
        chunks.append(part)
        position += len(part)
    return ''.join(chunks), focus


def content_text(ref, raw):
    return conversation_text(decode_json(raw)['messages'])[0] if ref['kind'] == 'branch' else raw.decode('utf-8-sig')


class ResourceReader(StudioDialog):
    def event(self, event):
        if event.type() == QEvent.Type.ShortcutOverride and event.key() == Qt.Key.Key_Escape:
            event.accept()
            return True
        return super().event(event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.reject()
            event.accept()
            return
        super().keyPressEvent(event)

    def __init__(self, page, row, loaded, *, frozen=None):
        super().__init__(page)
        self.page, self.row, self.frozen = page, row, frozen
        self.story = loaded if row['kind'] == 'archive' else None
        self.text, self.start = '', 0
        self.setWindowTitle(row['title'])
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setWordWrap(True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.resize(940, 720)
        self.setMinimumSize(480, 400)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText('搜尋內容')
        self.filter.setClearButtonEnabled(True)
        self.filter.setAccessibleName('搜尋內容')
        self.previous_match = button('上一處', lambda: self.find(-1), 'Quiet')
        self.next_match = button('下一處', lambda: self.find(1), 'Quiet')
        line(self.body, self.filter, self.previous_match, self.next_match)
        self.filter.returnPressed.connect(lambda: self.find(1))
        self.filter.textChanged.connect(self.reset_find)
        shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        shortcut.activated.connect(self.filter.setFocus)
        self.branches = ComboBox()
        self.branches.setAccessibleName('對話分支')
        self.body.addWidget(self.branches)
        self.branches.hide()
        self.preview = viewer('原文內容')
        self.body.addWidget(self.preview, 1)
        self.preview.selectionChanged.connect(self.selection_changed)
        self.preview_back = button('上一段', lambda: self.slice(-1), 'Quiet')
        self.preview_next = button('下一段', lambda: self.slice(1), 'Quiet')
        self.chunk_label = label('', 'Muted')
        line(self.body, self.preview_back, None, self.chunk_label, None, self.preview_next)
        self.detail = viewer('來源與選取範圍')
        self.detail.setMaximumHeight(140)
        self.body.addWidget(self.detail)
        self.detail.hide()
        self.details_button = button('詳細資料', lambda: self.detail.setVisible(not self.detail.isVisible()), 'Quiet')
        self.actions = button('更多', lambda: page.file_menu(row, self.actions, self), 'Quiet')
        self.message = label('', 'Muted', True)
        self.message.setTextFormat(Qt.TextFormat.PlainText)
        self.use = button('勾選整份', self.choose, 'Primary')
        line(self.body, self.details_button, self.actions, self.message, None, self.use)
        if frozen:
            ref, raw = frozen
            self.actions.hide()
            self.use.setText('核對來源')
            self.detail.setPlainText(self.reference_detail(ref))
            self.message.setText('保存時的內容 · ' + ref['scope'])
            self.set_text(content_text(ref, raw))
        elif self.story:
            for i, leaf in enumerate(self.story.branches, 1):
                self.branches.addItem(f'分支 {i}' + (' · 目前' if leaf == self.story.current else ''), leaf)
            chosen = page.picks.get(page.key(row), {}).get('leaves')
            leaf = chosen[-1] if chosen else self.story.branch_for(row['node']) if row['node'] else self.story.current
            self.branches.setCurrentIndex(max(0, self.branches.findData(leaf)))
            self.branches.setVisible(self.branches.count() > 1)
            self.branches.currentIndexChanged.connect(self.show_branch)
            self.show_branch()
        else:
            raw, path = loaded
            ledger = SourceLedger(raw)
            self.set_text(ledger.text_of(ledger.pieces))
            self.detail.setPlainText(f'{path}\n{len(raw):,} bytes\n版本 {row["sha"]}')
        self.reset_find()
        self.selection_changed()
        # Enter in the search field must search, not activate the dialog's close/default button.
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)
            control.setDefault(False)

    @staticmethod
    def reference_detail(ref):
        scope = (f'選取 {ref["range"][1] - ref["range"][0]:,} bytes / 共 {ref["total_bytes"]:,} bytes'
                 if ref['kind'] == 'document' else
                 f'選取 {ref["nodes"]} 則 · 其餘分支 {ref["excluded_nodes"]} 則未選用')
        return '\n'.join([scope, '版本 ' + ref['version'], *ref['notes']])

    def show_branch(self):
        leaf = self.branches.currentData()
        messages = self.story.selected(leaf)
        text, focus = conversation_text(messages, self.row['node'])
        self.set_text(text, focus or 0)
        notes = list(self.story.notes)
        for message in messages:
            notes.extend(message['notes'])
            notes.extend(a['reference'] + ' · ' + a['state'] for a in message['attachments'])
        self.detail.setPlainText('\n'.join([f'選取 {len(messages)} 則 · 其餘分支 {len(self.story.nodes) - len(messages)} 則未選用',
            '版本 ' + self.row['sha'], *dict.fromkeys(notes)]))
        self.message.setText(f'{len(messages)} 則 · ' + self.branches.currentText())
        self.selection_changed()

    def set_text(self, text, position=0):
        self.text = text
        self.start = position // CHUNK * CHUNK
        self.slice(0, position)

    def slice(self, step, position=None, end=None):
        self.start = max(0, min(max(0, (len(self.text) - 1) // CHUNK * CHUNK), self.start + step * CHUNK))
        # A found phrase may cross a chunk boundary. Extend this one view to keep it selectable.
        chunk = self.text[self.start:max(self.start + CHUNK, end or 0)]
        self.preview.setPlainText(chunk)
        multiple = len(self.text) > CHUNK
        self.preview_back.setVisible(multiple)
        self.preview_next.setVisible(multiple)
        self.chunk_label.setVisible(multiple)
        self.preview_back.setEnabled(self.start > 0)
        self.preview_next.setEnabled(self.start + CHUNK < len(self.text))
        self.chunk_label.setText(f'{self.start // CHUNK + 1} / {max(1, (len(self.text) + CHUNK - 1) // CHUNK)}')
        if position is not None:
            cursor = self.preview.textCursor()
            cursor.setPosition(utf16_position(chunk, position - self.start))
            if end is not None:
                cursor.setPosition(utf16_position(chunk, end - self.start), QTextCursor.MoveMode.KeepAnchor)
            self.preview.setTextCursor(cursor)
            self.preview.centerCursor()

    def reset_find(self):
        self.match = None
        enabled = bool(self.filter.text())
        self.previous_match.setEnabled(enabled)
        self.next_match.setEnabled(enabled)

    def find(self, direction):
        import re
        query = self.filter.text()
        if not query:
            return
        # Search the entire text, not just the visible chunk. Preserve Unicode character offsets.
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        if direction > 0:
            found = pattern.search(self.text, self.match[1] if self.match else 0) or pattern.search(self.text)
        else:
            end = self.match[0] if self.match else len(self.text)
            def previous(end):
                while end > 0:
                    start = max(0, end - CHUNK - len(query))
                    candidate = None
                    for hit in pattern.finditer(self.text, start, end):
                        candidate = hit
                    if candidate is not None:
                        return candidate
                    end = max(0, end - CHUNK)
                return None
            found = previous(end) or previous(len(self.text))
        if found is None:
            self.message.setText('沒有符合的文字')
            return
        self.match = found.span()
        self.start = found.start() // CHUNK * CHUNK
        self.slice(0, found.start(), found.end())
        self.message.setText('')

    def selection_changed(self):
        if self.frozen:
            return
        self.use.setVisible(bool(self.story))
        self.use.setText('勾選此對話')

    def choose(self):
        if self.frozen:
            ref, _ = self.frozen
            self.page.job('正在核對來源…', lambda cancel: bundles.current_state(self.page.store, ref, cancel),
                          lambda state: self.message.setText(state) if isValid(self) else None)
            return
        self.page.choose_row(self.row)
        self.message.setText('已勾選')
