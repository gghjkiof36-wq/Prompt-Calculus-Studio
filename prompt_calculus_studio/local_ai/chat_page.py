"""Document-owned Qt chat surface. Selection and settings never load a model."""
import math
from pathlib import Path
import weakref

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker, QSize
from PySide6.QtGui import (
    QAction, QColor, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextDocument, QTextOption,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QPlainTextEdit,
    QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from ..theme import font_pixels, visual_tokens
from ..ui_icons import icon
from ..widgets import RoundMenu, button, label, row, scrolling
from ..workspace_style import workspace_colors
from .chat_settings import ChatSettingsDialog


ATTEMPT_LABELS = {
    'receiving': '接收中', 'complete': '完成', 'stopped': '已停止',
    'interrupted': '已中斷', 'failed': '未完成', 'limited': '達到回覆上限',
}


class ChatComposer(QPlainTextEdit):
    submitted = Signal()
    compositionChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False
        self._commit_guard = False
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._release_commit)
        self.setObjectName('ChatComposer')
        self.setAccessibleName('對話訊息')
        self.setPlaceholderText('輸入訊息…')
        self.setTabChangesFocus(True)
        self.setMinimumHeight(80)
        self.setMaximumHeight(180)
        self.textChanged.connect(self._fit_height)
        self._fit_height()

    def _fit_height(self):
        lines = min(7, max(2, self.document().blockCount()))
        self.setFixedHeight(max(80, min(180, lines * self.fontMetrics().lineSpacing() + 20)))

    def _release_commit(self):
        self._commit_guard = False

    def inputMethodEvent(self, event):
        # Guard both preedit and the synchronous commit/textChanged sequence.
        # A candidate-confirming Return must not become a send action.
        self.composing = True
        self.compositionChanged.emit()
        try:
            super().inputMethodEvent(event)
        finally:
            self.composing = bool(event.preeditString())
            if event.commitString():
                self._commit_guard = True
                self._commit_timer.start(0)
            self.compositionChanged.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.composing or self._commit_guard:
                event.accept()
                return
            if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                if not event.isAutoRepeat():
                    self.submitted.emit()
                event.accept()
                return
        super().keyPressEvent(event)


class ChatText(QTextBrowser):
    """Selectable Markdown without HTML, resource fetching, or active links."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatText')
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setReadOnly(True)
        self.setOpenLinks(False)
        self.setOpenExternalLinks(False)
        self.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse |
                                     Qt.TextInteractionFlag.TextSelectableByKeyboard)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().setDocumentMargin(0)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._content = None
        self._pending = None
        self._setting_text = False
        self._tokens = None
        self.selectionChanged.connect(self._selection_changed)

    def loadResource(self, resource_type, url):
        # Model text is untrusted. Neither local file references nor remote
        # image/font/style references are read just by displaying a reply.
        return None

    def set_content(self, text, *, markdown=True):
        value = (str(text), markdown)
        if value == self._content:
            return
        if self.textCursor().hasSelection():
            self._pending = value
            return
        self._content, self._pending = value, None
        self._setting_text = True
        try:
            if markdown:
                features = (QTextDocument.MarkdownFeature.MarkdownDialectGitHub |
                            QTextDocument.MarkdownFeature.MarkdownNoHTML)
                self.document().setMarkdown(value[0], features)
                self._space_markdown()
            else:
                self.setPlainText(value[0])
        finally:
            self._setting_text = False
        self._fit_height()

    def _space_markdown(self):
        # QTextDocument's Markdown reader does not apply HTML CSS paragraph
        # rules. Set display-only block spacing without converting to HTML.
        cursor = QTextCursor(self.document())
        cursor.beginEditBlock()
        block = self.document().begin()
        while block.isValid():
            cursor.setPosition(block.position())
            formatting = block.blockFormat()
            code = formatting.nonBreakableLines()
            formatting.setLineHeight(135, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            formatting.setTopMargin(0)
            formatting.setBottomMargin(3 if block.textList() else 12)
            if code:
                previous, following = block.previous(), block.next()
                first = not previous.isValid() or not previous.blockFormat().nonBreakableLines()
                last = not following.isValid() or not following.blockFormat().nonBreakableLines()
                formatting.setTopMargin(8 if first else 0)
                formatting.setBottomMargin(12 if last else 0)
                formatting.setLeftMargin(12)
                formatting.setRightMargin(12)
                if self._tokens:
                    formatting.setBackground(QColor(self._tokens['field']))
                cursor.select(QTextCursor.SelectionType.BlockUnderCursor)
                characters = QTextCharFormat()
                characters.setFontFamilies(['Cascadia Code', 'Consolas', 'monospace'])
                cursor.mergeCharFormat(characters)
                cursor.clearSelection()
            cursor.setBlockFormat(formatting)
            block = block.next()
        cursor.endEditBlock()

    def _selection_changed(self):
        if not self._setting_text and self._pending and not self.textCursor().hasSelection():
            value = self._pending
            self.set_content(value[0], markdown=value[1])

    def _fit_height(self, *_args):
        height = math.ceil(self.document().size().height()) + 8
        # Very long replies remain readable with an inner scrollbar rather
        # than exceeding Qt's maximum widget geometry.
        self.setFixedHeight(max(self.fontMetrics().height() + 8, min(12000, height)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def apply_appearance(self, tokens):
        self._tokens = tokens
        self.document().setDefaultStyleSheet(
            'p { margin-top: 5px; margin-bottom: 12px; }'
            'li { margin-bottom: 5px; }'
            f"pre {{ background-color:{tokens['field']}; white-space:pre-wrap; }}"
            "pre, code { font-family:'Cascadia Code','Consolas',monospace; }"
            f"blockquote {{ color:{tokens['muted']}; }}")
        # Reapply only this view's display, preserving raw response text.
        if self._content is not None:
            value = self._content
            self._content = None
            self.set_content(value[0], markdown=value[1])


class ChatAttempt(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatAssistant')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.heading = label('', 'Subtle')
        self.copy_button = button('', self._copy, 'IconButton')
        self.copy_button.setIcon(icon('copy', size=16))
        self.copy_button.setFixedSize(30, 30)
        self.copy_button.setToolTip('複製回覆')
        self.copy_button.setAccessibleName('複製這次回覆')
        self.content = ChatText()
        self.content.setAccessibleName('模型回覆')
        layout.addWidget(self.content)
        self.reason = label('', 'Subtle', True)
        layout.addWidget(self.reason)
        layout.addLayout(row(self.copy_button, self.heading, None))
        self._raw = ''
        self._copy_reset = QTimer(self)
        self._copy_reset.setSingleShot(True)
        self._copy_reset.timeout.connect(self._reset_copy)

    def update_attempt(self, attempt, index, total, model_names):
        self._raw = attempt.get('text', '')
        status = ATTEMPT_LABELS.get(attempt.get('status'), '未完成')
        model = attempt.get('model_name') or model_names.get(attempt.get('model'), '先前使用的模型')
        self.heading.setText(status)
        self.heading.setVisible(attempt.get('status') != 'complete')
        self.copy_button.setToolTip(f'複製回覆 · {model}' + (f' · 第 {index + 1} 次' if total > 1 else ''))
        self.copy_button.setEnabled(bool(self._raw))
        self.content.set_content(self._raw or ('正在等待回覆…' if attempt.get('status') == 'receiving' else '尚未收到文字。'))
        self.reason.setText(attempt.get('reason', ''))
        self.reason.setVisible(bool(attempt.get('reason')))

    def _copy(self):
        QApplication.clipboard().setText(self._raw)
        self.copy_button.setIcon(icon('check', size=16))
        self.copy_button.setAccessibleName('回覆已複製')
        self._copy_reset.start(1600)

    def _reset_copy(self):
        self.copy_button.setIcon(icon('copy', size=16))
        self.copy_button.setAccessibleName('複製這次回覆')


class ChatTurn(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatTurn')
        self.stack = QVBoxLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 12)
        self.stack.setSpacing(28)
        self.user = QFrame()
        self.user.setObjectName('ChatUser')
        user_layout = QVBoxLayout(self.user)
        user_layout.setContentsMargins(16, 10, 16, 10)
        self.user_text = ChatText()
        self.user_text.setAccessibleName('你的訊息')
        user_layout.addWidget(self.user_text)
        self.stack.addWidget(self.user, 0, Qt.AlignmentFlag.AlignRight)
        self.history_button = button('', self._toggle_history, 'Quiet')
        self.history_button.setCheckable(True)
        self.stack.addWidget(self.history_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.attempts = {}
        self._attempt_order = []
        self._user_source = ''

    def update_turn(self, turn, model_names, tokens):
        self._user_source = turn.get('text', '')
        self.user_text.set_content(self._user_source, markdown=False)
        self._fit_user()
        attempts = turn.get('attempts', [])
        self._attempt_order = [attempt['id'] for attempt in attempts]
        present = set()
        for index, attempt in enumerate(attempts):
            identity = attempt['id']
            present.add(identity)
            if identity not in self.attempts:
                widget = ChatAttempt()
                widget.content.apply_appearance(tokens)
                self.attempts[identity] = widget
                self.stack.addWidget(widget)
            self.attempts[identity].update_attempt(attempt, index, len(attempts), model_names)
        for identity in set(self.attempts) - present:
            widget = self.attempts.pop(identity)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self._sync_history()

    def _fit_user(self):
        measure = self.user_text.fontMetrics()
        natural = max((measure.horizontalAdvance(line) for line in self._user_source.splitlines()), default=48) + 36
        self.user.setFixedWidth(max(72, min(natural, round(self.width() * .8))))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_user()

    def _toggle_history(self):
        self._sync_history()

    def _sync_history(self):
        previous = len(self._attempt_order) - 1
        expanded = self.history_button.isChecked()
        self.history_button.setVisible(previous > 0)
        self.history_button.setText(('收起' if expanded else '查看') + f'先前 {previous} 次回覆')
        for index, identity in enumerate(self._attempt_order):
            self.attempts[identity].setVisible(expanded or index == previous)


class ChatModelButton(QPushButton):
    """A compact menu anchor; the full model identity remains accessible."""

    def __init__(self, callback, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatModelButton')
        self.setIcon(icon('chevron-down', size=14))
        self.setIconSize(QSize(14, 14))
        self.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.setFixedHeight(36)
        self.setMinimumWidth(110)
        self.setMaximumWidth(250)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        self._caption = '選擇模型'
        self.clicked.connect(callback)

    def set_model(self, name):
        self._caption = name or '選擇模型'
        self.setToolTip(self._caption)
        self.setAccessibleName('對話模型：' + self._caption)
        self._fit_caption()

    def _fit_caption(self):
        self.setText(self.fontMetrics().elidedText(self._caption, Qt.TextElideMode.ElideMiddle, max(65, self.width() - 44)))

    def sizeHint(self):
        return QSize(min(250, max(110, self.fontMetrics().horizontalAdvance(self._caption) + 44)), 36)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_caption()


class ChatPage(QWidget):
    """A view of a shared ChatDocument; background ownership stays in the doc."""

    def __init__(self, document, workspace, parent=None):
        super().__init__(parent)
        self.chat_document, self.workspace = document, workspace
        document.views.add(self)
        reference = weakref.ref(self)
        def forget_view():
            view = reference()
            if view is not None:
                document.views.discard(view)
        self.destroyed.connect(forget_view)
        self.service = document.service
        self.setObjectName('LocalChatPage')
        self._turns = {}
        self._ui_error = ''
        self._settings_dialog = None
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._follow_output = True
        self.has_uncommitted_draft = False
        self._rendering = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 12, 24, 16)
        outer.setSpacing(8)
        self.more_button = button('', self._open_actions, 'IconButton')
        self.more_button.setIcon(icon('more-horizontal'))
        self.more_button.setFixedSize(32, 32)
        self.more_button.setToolTip('對話操作')
        self.more_button.setAccessibleName('對話操作')
        outer.addLayout(row(None, self.more_button))
        self.export_action = QAction('匯出 Markdown…', self)
        self.export_action.triggered.connect(self._export)
        self.retry_action = QAction('重試上一則', self)
        self.retry_action.triggered.connect(self._retry)
        centered = QHBoxLayout()
        centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch()
        column = QWidget()
        column.setObjectName('ChatColumn')
        column.setMaximumWidth(820)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        centered.addWidget(column, 1)
        centered.addStretch()
        outer.addLayout(centered, 1)
        self.column = QVBoxLayout(column)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(12)
        self.column.addStretch(1)
        self.empty_heading = label('開始一段對話', 'ChatEmptyTitle')
        self.empty_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_heading.setContentsMargins(0, 0, 0, 20)
        self.column.addWidget(self.empty_heading)

        self.transcript = QWidget()
        self.transcript.setObjectName('ChatTranscript')
        self.messages = QVBoxLayout(self.transcript)
        self.messages.setContentsMargins(0, 20, 12, 20)
        self.messages.setSpacing(32)
        self.messages.addStretch()
        self.scroll = scrolling(self.transcript)
        self.scroll.setObjectName('ChatScroll')
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_position_changed)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._scroll_range_changed)
        self.column.addWidget(self.scroll, 1)

        self.composer_frame = QFrame()
        self.composer_frame.setObjectName('ChatComposerFrame')
        composer_layout = QVBoxLayout(self.composer_frame)
        composer_layout.setContentsMargins(16, 12, 12, 10)
        composer_layout.setSpacing(4)
        self.composer = ChatComposer()
        self.composer.setPlainText(document.payload.get('draft', ''))
        self.composer.textChanged.connect(self._draft_changed)
        self.composer.compositionChanged.connect(self._composition_changed)
        self.composer.submitted.connect(self._send)
        composer_layout.addWidget(self.composer)
        self.settings_button = button('', self.open_settings, 'IconButton')
        self.settings_button.setIcon(icon('sliders-horizontal', size=18))
        self.settings_button.setFixedSize(32, 32)
        self.settings_button.setToolTip('對話設定')
        self.settings_button.setAccessibleName('對話設定')
        self.status = label('', 'Subtle')
        self.status.setAccessibleName('本機模型狀態')
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.load_button = button('載入', self._prepare_model, 'ChatLoad')
        self.load_button.setFixedHeight(32)
        self.load_button.setToolTip('明確載入所選模型')
        self.model_button = ChatModelButton(self._open_models)
        self.stop_button = button('', self._stop, 'ChatSend')
        self.stop_button.setIcon(icon('stop', 'on-accent', 18))
        self.stop_button.setAccessibleName('停止回覆')
        self.stop_button.setToolTip('停止回覆')
        self.send_button = button('', self._send, 'ChatSend')
        self.send_button.setIcon(icon('arrow-up', 'on-accent', 20))
        self.send_button.setAccessibleName('送出訊息')
        self.send_button.setToolTip('Enter 送出；Shift + Enter 換行。')
        for control in (self.send_button, self.stop_button):
            control.setFixedSize(40, 40)
            control.setIconSize(QSize(20, 20))
        controls = row(self.settings_button, self.status, self.load_button, self.model_button,
                       self.stop_button, self.send_button)
        controls.setStretch(1, 1)
        controls.setSpacing(8)
        composer_layout.addLayout(controls)
        self.column.addWidget(self.composer_frame)
        self.composer_help = label('Enter 送出 · Shift + Enter 換行', 'Subtle')
        self.composer_help.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.column.addWidget(self.composer_help)
        self.error_label = label('', 'ChatError', True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.setAccessibleName('對話錯誤')
        self.column.addWidget(self.error_label)
        self.column.addStretch(1)
        document.changed.connect(self._schedule_refresh)
        self.service.changed.connect(self._schedule_refresh)
        self.apply_appearance(workspace.display_settings())
        self.refresh()

    @property
    def composing(self):
        return self.composer.composing

    def undo(self):
        self.composer.undo()

    def redo(self):
        self.composer.redo()

    def _schedule_refresh(self):
        # Merge stream fragments into bounded UI updates, not one rich-text
        # document relayout per token.
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(70)

    def _draft_changed(self):
        if not self.composer.composing:
            self.has_uncommitted_draft = self.chat_document.set_draft(self.composer.toPlainText()) is False
        self._update_actions()

    def _composition_changed(self):
        if not self.composer.composing:
            self._draft_changed()
            # Composition may end by cancelling preedit without changing text.
            # Wake autosave even then, e.g. a reply completed during preedit.
            self.chat_document.changed.emit()
        self._update_actions()

    def _scroll_position_changed(self, value):
        if self._rendering or self._scroll_timer.isActive():
            return
        bar = self.scroll.verticalScrollBar()
        self._follow_output = value >= bar.maximum() - 40

    def _scroll_range_changed(self, _minimum, _maximum):
        if self._follow_output:
            self._scroll_timer.start(0)

    def _scroll_to_bottom(self):
        if self._follow_output:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

    def refresh(self):
        payload = self.chat_document.payload
        turns = payload.get('turns', [])
        self.empty_heading.setVisible(not turns)
        self.scroll.setVisible(bool(turns))
        self.column.setStretch(0, 0 if turns else 1)
        self.column.setStretch(self.column.count() - 1, 0 if turns else 1)
        draft = payload.get('draft', '')
        if not self.composer.composing and not self.has_uncommitted_draft and self.composer.toPlainText() != draft:
            cursor = self.composer.textCursor()
            position = min(cursor.position(), len(draft))
            with QSignalBlocker(self.composer):
                self.composer.setPlainText(draft)
                cursor = self.composer.textCursor()
                cursor.setPosition(position)
                self.composer.setTextCursor(cursor)
            self.composer._fit_height()
        models = self.service.models
        model_names = {model['id']: model['name'] for model in models}
        self.model_button.set_model(model_names.get(self.service.selected_model, ''))
        present = set()
        follow = self._follow_output
        self._rendering = True
        for turn in turns:
            identity = turn['id']
            present.add(identity)
            if identity not in self._turns:
                widget = ChatTurn()
                self._turns[identity] = widget
                self.messages.insertWidget(self.messages.count() - 1, widget)
            self._turns[identity].update_turn(turn, model_names, self._tokens)
        for identity in set(self._turns) - present:
            widget = self._turns.pop(identity)
            self.messages.removeWidget(widget)
            widget.deleteLater()
        self._follow_output = follow
        self._rendering = False
        if follow:
            self._scroll_timer.start(0)
        self._update_actions()

    def _update_actions(self):
        state, busy = self.service.state, self.chat_document.busy
        locked = state in {'loading', 'generating', 'stopping', 'unloading', 'closed'}
        ready = state == 'ready' and not self.service.needs_reload
        self.model_button.setEnabled(state != 'closed')
        self.load_button.setVisible(not locked and bool(self.service.selected_model) and not ready)
        self.load_button.setText('設定引擎' if state == 'unavailable' else '重新載入' if state == 'ready' else '載入')
        self.load_button.setEnabled(not locked)
        self.stop_button.setVisible(bool(busy))
        self.stop_button.setEnabled(bool(busy) and state not in {'stopping', 'unloading'})
        self.send_button.setVisible(not busy)
        self.send_button.setEnabled(ready and not busy and not self.composing and not self.has_uncommitted_draft
                                    and bool(self.composer.toPlainText().strip()))
        self.retry_action.setEnabled(bool(self.chat_document.payload.get('turns')) and ready and not busy
                                     and not self.composing and not self.has_uncommitted_draft)
        self.export_action.setEnabled(not self.composing and not self.has_uncommitted_draft)
        descriptions = {
            'unavailable': '引擎未設定' if self.service.selected_model else '',
            'unloaded': '尚未載入' if self.service.selected_model else '',
            'loading': '載入中…',
            'ready': '',
            'generating': '' if busy else '另一對話正在回覆',
            'stopping': '停止中…' if busy else '另一對話正在停止',
            'unloading': '卸載中…',
            'error': '',
            'closed': '引擎已關閉',
        }
        status = '設定已變更' if state == 'ready' and self.service.needs_reload else descriptions.get(state, '')
        self.status.setText(status)
        self.status.setToolTip(status)
        error = self._ui_error or self.chat_document.notice or self.chat_document.error or self.service.error
        self.error_label.setText(str(error or ''))
        self.error_label.setVisible(bool(error))

    def _action(self, callback):
        self._ui_error = ''
        try:
            result = callback()
        except (ValueError, OSError, RuntimeError) as exc:
            self._ui_error = str(exc)
            result = False
        self.refresh()
        return result

    def _send(self):
        if not self.send_button.isEnabled() or self.composing:
            return
        self._follow_output = True
        self._action(lambda: self.chat_document.send(self.composer.toPlainText()))

    def _retry(self):
        if self.retry_action.isEnabled():
            self._follow_output = True
            self._action(self.chat_document.retry)

    def _stop(self):
        if self.chat_document.busy:
            self._action(self.chat_document.stop)

    def _select_model(self, selected):
        if selected and selected != self.service.selected_model:
            self._action(lambda: self.service.select_model(selected))

    def _open_actions(self):
        self._update_actions()
        menu = RoundMenu(self)
        menu.addAction(self.retry_action)
        menu.addAction(self.export_action)
        menu.open_for(self.more_button, align_right=True)
        self._actions_menu = menu

    def _open_models(self):
        menu = RoundMenu(self)
        state = self.service.state
        locked = state in {'loading', 'generating', 'stopping', 'unloading', 'closed'}
        for model in self.service.models:
            action = menu.addAction(model['name'])
            action.setCheckable(True)
            action.setChecked(model['id'] == self.service.selected_model)
            action.setEnabled(not locked)
            action.setToolTip(model['path'])
            action.triggered.connect(lambda _checked=False, identity=model['id']: self._select_model(identity))
        if self.service.models:
            menu.addSeparator()
        action = menu.addAction('管理模型…')
        action.triggered.connect(self._add_model)
        action.setEnabled(state != 'closed')
        if self.service.selected_model:
            if state != 'ready' or self.service.needs_reload:
                action = menu.addAction('設定引擎…' if state == 'unavailable' else '重新載入' if state == 'ready' else '載入模型')
                action.setEnabled(not locked)
                action.triggered.connect(self._prepare_model)
            if state in {'ready', 'loading'}:
                action = menu.addAction('取消載入' if state == 'loading' else '卸載模型')
                action.triggered.connect(self._unload)
        menu.open_for(self.model_button, align_right=True)
        self._models_menu = menu

    def _prepare_model(self):
        if self.service.state == 'unavailable':
            self.open_settings()
        else:
            self._load()

    def _add_model(self):
        self.workspace.open_route('explore', 'local_models')

    def _load(self):
        self._action(self.service.load)

    def _unload(self):
        self._action(self.service.unload)

    def open_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.show()
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = ChatSettingsDialog(self.chat_document, self)
        self._settings_dialog = dialog
        self.destroyed.connect(dialog.reject)
        dialog.finished.connect(self._settings_closed)
        dialog.show()

    def _settings_closed(self, _result):
        dialog, self._settings_dialog = self._settings_dialog, None
        if dialog is not None:
            dialog.deleteLater()
        self.refresh()

    def _export(self):
        if self.composing or self.has_uncommitted_draft:
            return
        directory = QFileDialog.getExistingDirectory(self, '選擇 Markdown 匯出資料夾')
        if not directory:
            return
        path = self._action(lambda: self.chat_document.export_markdown(Path(directory)))
        if isinstance(path, (Path, str)) and path:
            self.status.setText('已匯出')
            self.status.setToolTip(str(path))

    def apply_appearance(self, settings):
        tokens = self._tokens = visual_tokens(settings)
        self.send_button.setIcon(icon('arrow-up', tokens['on_accent'], 20))
        self.stop_button.setIcon(icon('stop', tokens['on_accent'], 18))
        surface = workspace_colors(settings)['content']
        title_size = font_pixels(settings.get('ui_size', 12) + 9)
        self.setStyleSheet(f'''
            QWidget#LocalChatPage {{ background:{surface}; }}
            QWidget#ChatColumn, QWidget#ChatTranscript, QScrollArea#ChatScroll,
            QTextBrowser#ChatText, QFrame#ChatTurn, QFrame#ChatAssistant {{ background:transparent; border:0; }}
            QFrame#ChatUser {{ background:{tokens['surface']}; border:0; border-radius:16px; }}
            QFrame#ChatComposerFrame {{ background:{tokens['surface']}; border:1px solid {tokens['divider']}; border-radius:14px; }}
            QPlainTextEdit#ChatComposer {{ background:transparent; border:0; padding:4px; }}
            QTextBrowser#ChatText {{ padding:0; color:{tokens['text']}; }}
            QLabel#ChatEmptyTitle {{ font-size:{title_size}px; font-weight:500; color:{tokens['text']}; }}
            QPushButton#ChatModelButton {{ background:{tokens['raised']}; color:{tokens['text']}; border:0; border-radius:18px; padding:0 12px; }}
            QPushButton#ChatModelButton:hover {{ background:{tokens['hover']}; }}
            QPushButton#ChatModelButton:focus {{ border:1px solid {tokens['accent_outline']}; }}
            QPushButton#ChatLoad {{ background:transparent; border:0; color:{tokens['text']}; padding:0 6px; }}
            QPushButton#ChatLoad:hover {{ background:{tokens['hover']}; border-radius:8px; }}
            QPushButton#ChatLoad:focus {{ border:1px solid {tokens['accent_outline']}; border-radius:8px; }}
            QPushButton#ChatSend {{ background:{tokens['accent']}; border:0; border-radius:20px; padding:0; }}
            QPushButton#ChatSend:hover {{ background:{tokens['accent_outline']}; }}
            QPushButton#ChatSend:focus {{ border:2px solid {tokens['text']}; }}
            QPushButton#ChatSend:disabled {{ background:{tokens['raised']}; }}
            QLabel#ChatError {{ color:{tokens['error']}; }}
        ''')
        for turn in self._turns.values():
            for attempt in turn.attempts.values():
                attempt.content.apply_appearance(tokens)
