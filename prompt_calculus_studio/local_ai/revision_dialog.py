"""Read one user-message version without changing the active conversation."""
import weakref

from PySide6.QtCore import QEvent, QTimer, Qt, Signal
from PySide6.QtWidgets import QFrame, QGridLayout, QHBoxLayout, QVBoxLayout, QWidget

from ..management_reading import READING_CONTENT_WIDTH
from ..theme import visual_tokens, window_appearance
from ..ui_icons import icon
from ..widgets import StudioDialog, button, label, row, scrolling
from .attachment_strip import AttachmentStrip


class MessageVersionsDialog(StudioDialog):
    branchRequested = Signal(str, str)

    def __init__(self, document, turn_id, parent=None):
        super().__init__(parent)
        self._finished = False
        # Imported only when a dialog is opened, after ChatPage is initialized.
        from .chat_page import ChatText
        self._document = weakref.ref(document)
        self._source = weakref.ref(parent) if parent is not None else lambda: None
        self._identity = (document.key, document.owner_epoch, str(document.path))
        self._payload = document.payload
        self._turns = document.payload['turns']
        self._turn_count = len(self._turns)
        self._revision_count = len(document.payload.get('revisions', []))
        self.turn_id = turn_id
        self.versions = sorted(document.message_versions(turn_id), key=lambda item: item['number'], reverse=True)
        if not self.versions:
            raise ValueError('找不到這則訊息的版本。')
        self._count = len(self.versions)
        self.current_index = next((i for i, value in enumerate(self.versions) if value['current']), 0)
        self.selected_index = self.current_index
        self.setWindowTitle('版本')
        self.set_outside_dismiss(True)
        self._prompt_instant_show = True
        owner = parent.window() if parent is not None else None
        self.resize(min(900, max(360, owner.width() - 48)) if owner else 900,
                    min(740, max(320, owner.height() - 64)) if owner else 700)

        self.reading = QFrame()
        self.reading.setObjectName('ChatVersionReading')
        self.reading.setMaximumWidth(READING_CONTENT_WIDTH)
        reading_layout = QVBoxLayout(self.reading)
        reading_layout.setContentsMargins(0, 8, 0, 12)
        reading_layout.setSpacing(12)
        self.preview = ChatText()
        self.preview.setAccessibleName('訊息版本內容')
        self.attachments = AttachmentStrip(read_only=True)
        reading_layout.addWidget(self.preview)
        reading_layout.addWidget(self.attachments)
        reading_layout.addStretch()
        host = QWidget()
        host_layout = QHBoxLayout(host)
        host_layout.setContentsMargins(12, 0, 12, 0)
        host_layout.addStretch()
        host_layout.addWidget(self.reading, 1)
        host_layout.addStretch()
        self.scroll = scrolling(host)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().valueChanged.connect(self.preview.sync_rich_visibility)
        self.scroll.verticalScrollBar().rangeChanged.connect(self.preview.sync_rich_visibility)
        self.body.addWidget(self.scroll, 1)

        self.previous_button = button('', lambda: self.select_version(self.selected_index + 1), 'Quiet')
        self.previous_button.setIcon(icon('chevron-left', size=16))
        self.previous_button.setAccessibleName('上一個版本')
        self.next_button = button('', lambda: self.select_version(self.selected_index - 1), 'Quiet')
        self.next_button.setIcon(icon('chevron-right', size=16))
        self.next_button.setAccessibleName('下一個版本')
        for control in (self.previous_button, self.next_button):
            control.setFixedSize(30, 30)
        self.version_label = label('')
        self.version_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.navigation = QWidget()
        self.navigation.setLayout(row(self.previous_button, self.version_label, self.next_button))
        self.current_button = button('返回目前版本', lambda: self.select_version(self.current_index))
        self.branch_button = button('在新對話中建立分支', self._branch, 'Primary')
        self.footer = QWidget()
        self.footer_layout = QGridLayout(self.footer)
        self.footer_layout.setContentsMargins(0, 0, 0, 0)
        self.footer_layout.setHorizontalSpacing(10)
        self.footer_layout.setVerticalSpacing(8)
        self._narrow_footer = None
        self.body.addWidget(self.footer)
        tokens = visual_tokens(window_appearance(parent))
        self.preview.apply_appearance(tokens)
        self.attachments.apply_appearance(tokens)
        self.setStyleSheet(self.styleSheet() + '''
            QFrame#ChatVersionReading, QTextBrowser#ChatText {background:transparent;border:0;}
            QTextBrowser#ChatText {padding:0;}
            QPushButton {padding:6px 10px;}
        ''')
        document.changed.connect(self._sync_source)
        if parent is not None:
            parent.installEventFilter(self)
            parent.destroyed.connect(self.reject)
        self.finished.connect(self.deleteLater)
        self.select_version(self.current_index)

    def done(self, result):
        if self._finished:
            return
        self._finished = True
        super().done(result)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if not hasattr(self, 'footer_layout'):
            return
        narrow = self.width() < 680
        if narrow == self._narrow_footer:
            return
        self._narrow_footer = narrow
        for widget in (self.navigation, self.current_button, self.branch_button):
            self.footer_layout.removeWidget(widget)
        self.footer_layout.setColumnStretch(0, 1)
        self.footer_layout.addWidget(self.navigation, 0, 0, Qt.AlignmentFlag.AlignRight)
        self.footer_layout.addWidget(self.current_button, 0, 1)
        if narrow:
            self.footer_layout.addWidget(self.branch_button, 1, 0, 1, 2, Qt.AlignmentFlag.AlignRight)
        else:
            self.footer_layout.addWidget(self.branch_button, 0, 2)

    def eventFilter(self, watched, event):
        if watched is self._source() and event.type() in (QEvent.Type.Hide, QEvent.Type.Close):
            self.reject()
        return super().eventFilter(watched, event)

    def _valid_source(self):
        document = self._document()
        if (document is None or document._disposed
                or (document.key, document.owner_epoch, str(document.path)) != self._identity
                or document.payload is not self._payload or document.payload['turns'] is not self._turns
                or len(self._turns) != self._turn_count
                or len(document.payload.get('revisions', [])) != self._revision_count):
            return False
        try:
            return document.message_version_count(self.turn_id) == self._count
        except (ValueError, KeyError):
            return False

    def _sync_source(self):
        if not self._valid_source():
            self.reject()
            return False
        source = self._source()
        self.branch_button.setEnabled(not self._document().edit_locked and
                                      not (source and (source.composing or source.has_uncommitted_draft)))
        return True

    def select_version(self, index):
        if not 0 <= index < len(self.versions) or not self._sync_source():
            return
        self.selected_index = index
        value = self.versions[index]
        # Switching versions is explicit navigation. Clear this preview's old
        # selection so it cannot defer the newly selected document projection.
        cursor = self.preview.textCursor()
        cursor.clearSelection()
        self.preview.setTextCursor(cursor)
        if self.preview.rich is not None:
            self.preview.rich.page().runJavaScript('getSelection().removeAllRanges()')
        self.preview.set_content(value['text'])
        self.attachments.sync(value.get('attachments', []))
        self.version_label.setText(f"版本 {value['number']}")
        self.previous_button.setEnabled(index < len(self.versions) - 1)
        self.next_button.setEnabled(index > 0)
        self.current_button.setEnabled(index != self.current_index)
        self.scroll.verticalScrollBar().setValue(0)
        QTimer.singleShot(0, self.preview.sync_rich_visibility)

    def _branch(self):
        if not self._sync_source() or not self.branch_button.isEnabled():
            return
        version_id = self.versions[self.selected_index]['id']
        self.accept()
        self.branchRequested.emit(self.turn_id, version_id)
