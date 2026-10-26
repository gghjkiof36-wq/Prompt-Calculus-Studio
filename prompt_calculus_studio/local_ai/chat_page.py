"""Document-owned Qt chat surface. Selection and settings never load a model."""
import math
from pathlib import Path
import weakref

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker
from PySide6.QtGui import QTextDocument, QTextOption
from PySide6.QtWidgets import (
    QApplication, QComboBox, QFileDialog, QFrame, QHBoxLayout, QPlainTextEdit,
    QSizePolicy, QTextBrowser, QVBoxLayout, QWidget,
)

from ..theme import font_pixels, visual_tokens
from ..widgets import ActionHeader, button, label, row, scrolling
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
        self.setMinimumHeight(110)
        self.setMaximumHeight(200)
        self.textChanged.connect(self._fit_height)

    def _fit_height(self):
        lines = min(7, max(3, self.document().blockCount()))
        self.setFixedHeight(max(110, min(200, lines * self.fontMetrics().lineSpacing() + 28)))

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
            else:
                self.setPlainText(value[0])
        finally:
            self._setting_text = False
        self._fit_height()

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
        layout.setContentsMargins(0, 10, 0, 10)
        layout.setSpacing(12)
        self.heading = label('', 'Subtle')
        self.copy_button = button('複製', self._copy, 'Quiet')
        self.copy_button.setAccessibleName('複製這次回覆')
        layout.addLayout(row(self.heading, None, self.copy_button))
        self.content = ChatText()
        self.content.setAccessibleName('模型回覆')
        layout.addWidget(self.content)
        self.reason = label('', 'Subtle', True)
        layout.addWidget(self.reason)
        self._raw = ''
        self._copy_reset = QTimer(self)
        self._copy_reset.setSingleShot(True)
        self._copy_reset.timeout.connect(self._reset_copy)

    def update_attempt(self, attempt, index, total, model_names):
        self._raw = attempt.get('text', '')
        status = ATTEMPT_LABELS.get(attempt.get('status'), '未完成')
        model = attempt.get('model_name') or model_names.get(attempt.get('model'), '先前使用的模型')
        prefix = f'回覆 {index + 1} · ' if total > 1 else ''
        self.heading.setText(prefix + model + ' · ' + status)
        self.copy_button.setEnabled(bool(self._raw))
        self.content.set_content(self._raw or ('正在等待回覆…' if attempt.get('status') == 'receiving' else '尚未收到文字。'))
        self.reason.setText(attempt.get('reason', ''))
        self.reason.setVisible(bool(attempt.get('reason')))

    def _copy(self):
        QApplication.clipboard().setText(self._raw)
        self.copy_button.setText('已複製')
        self._copy_reset.start(1600)

    def _reset_copy(self):
        self.copy_button.setText('複製')


class ChatTurn(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatTurn')
        self.stack = QVBoxLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 16)
        self.stack.setSpacing(18)
        self.user = QFrame()
        self.user.setObjectName('ChatUser')
        user_layout = QVBoxLayout(self.user)
        user_layout.setContentsMargins(18, 14, 18, 14)
        user_layout.setSpacing(6)
        user_layout.addWidget(label('你', 'Subtle'))
        self.user_text = ChatText()
        self.user_text.setAccessibleName('你的訊息')
        user_layout.addWidget(self.user_text)
        self.stack.addWidget(self.user)
        self.attempts = {}

    def update_turn(self, turn, model_names, tokens):
        self.user_text.set_content(turn.get('text', ''), markdown=False)
        attempts = turn.get('attempts', [])
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
        self._last_models = None
        self.has_uncommitted_draft = False
        self._rendering = False
        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 18, 24, 18)
        outer.setSpacing(16)
        settings_button = button('對話設定', self.open_settings, 'Quiet')
        self.export_button = button('匯出 Markdown…', self._export, 'Quiet')
        outer.addWidget(ActionHeader(label('本地對話', 'Heading'), settings_button, self.export_button))
        centered = QHBoxLayout()
        centered.setContentsMargins(0, 0, 0, 0)
        centered.addStretch()
        column = QWidget()
        column.setObjectName('ChatColumn')
        column.setMaximumWidth(960)
        column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        centered.addWidget(column, 1)
        centered.addStretch()
        outer.addLayout(centered, 1)
        self.column = QVBoxLayout(column)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(16)
        self.column.addStretch(1)
        self.empty_heading = label('開始一段對話', 'ChatEmptyTitle')
        self.empty_heading.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.column.addWidget(self.empty_heading)
        self.empty_hint = label('選擇已加入的模型並載入，即可在這裡交談。', 'Subtle', True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.column.addWidget(self.empty_hint)

        self.transcript = QWidget()
        self.transcript.setObjectName('ChatTranscript')
        self.messages = QVBoxLayout(self.transcript)
        self.messages.setContentsMargins(0, 0, 14, 0)
        self.messages.setSpacing(18)
        self.messages.addStretch()
        self.scroll = scrolling(self.transcript)
        self.scroll.setObjectName('ChatScroll')
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_position_changed)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._scroll_range_changed)
        self.column.addWidget(self.scroll, 1)

        self.composer_frame = QFrame()
        self.composer_frame.setObjectName('ChatComposerFrame')
        composer_layout = QVBoxLayout(self.composer_frame)
        composer_layout.setContentsMargins(16, 12, 16, 12)
        composer_layout.setSpacing(10)
        self.composer = ChatComposer()
        self.composer.setPlainText(document.payload.get('draft', ''))
        self.composer.textChanged.connect(self._draft_changed)
        self.composer.compositionChanged.connect(self._composition_changed)
        self.composer.submitted.connect(self._send)
        composer_layout.addWidget(self.composer)
        self.model_combo = QComboBox()
        self.model_combo.setAccessibleName('對話模型')
        self.model_combo.setMinimumWidth(140)
        self.model_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.model_combo.setMinimumContentsLength(14)
        self.model_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.model_combo.currentIndexChanged.connect(self._select_model)
        self.add_model_button = button('加入模型…', self._add_model, 'Quiet')
        self.load_button = button('載入', self._load, 'Quiet')
        self.unload_button = button('卸載', self._unload, 'Quiet')
        composer_layout.addWidget(ActionHeader(self.model_combo, self.add_model_button, self.load_button, self.unload_button))
        self.retry_button = button('重試上一則', self._retry, 'Quiet')
        self.retry_button.setToolTip('重新回答最後一則訊息，並保留先前回覆。')
        self.stop_button = button('停止', self._stop, 'Quiet')
        self.send_button = button('送出', self._send, 'Primary')
        self.send_button.setToolTip('Enter 送出；Shift + Enter 換行。')
        self.composer_help = label('Enter 送出 · Shift + Enter 換行', 'Subtle')
        composer_layout.addWidget(ActionHeader(self.composer_help, self.retry_button, self.stop_button, self.send_button))
        self.column.addWidget(self.composer_frame)
        self.status = label('', 'Subtle', True)
        self.status.setAccessibleName('本機模型狀態')
        self.column.addWidget(self.status)
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
        self.empty_hint.setVisible(not turns)
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
        signature = [(model['id'], model['name']) for model in models]
        if signature != self._last_models:
            with QSignalBlocker(self.model_combo):
                self.model_combo.clear()
                self.model_combo.addItem('選擇模型' if models else '尚未加入模型', '')
                for model in models:
                    self.model_combo.addItem(model['name'], model['id'])
                    self.model_combo.setItemData(self.model_combo.count() - 1, model['path'], Qt.ItemDataRole.ToolTipRole)
            self._last_models = signature
        with QSignalBlocker(self.model_combo):
            index = self.model_combo.findData(self.service.selected_model)
            self.model_combo.setCurrentIndex(max(0, index))
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
        ready = state == 'ready'
        self.model_combo.setEnabled(not locked)
        self.add_model_button.setEnabled(not locked)
        self.load_button.setVisible(state not in {'generating', 'stopping', 'unloading'})
        self.load_button.setText('重新載入' if ready else '載入')
        self.load_button.setEnabled(not locked and bool(self.service.selected_model))
        self.unload_button.setVisible(state in {'loading', 'ready', 'generating', 'stopping', 'unloading'})
        self.unload_button.setText('取消載入' if state == 'loading' else '卸載')
        self.unload_button.setEnabled(ready or state == 'loading')
        self.stop_button.setVisible(bool(busy))
        self.stop_button.setEnabled(bool(busy) and state not in {'stopping', 'unloading'})
        self.send_button.setVisible(not busy)
        self.send_button.setEnabled(ready and not busy and not self.composing and not self.has_uncommitted_draft
                                    and bool(self.composer.toPlainText().strip()))
        self.retry_button.setVisible(bool(self.chat_document.payload.get('turns')))
        self.retry_button.setEnabled(ready and not busy and not self.composing and not self.has_uncommitted_draft)
        self.export_button.setEnabled(not self.composing and not self.has_uncommitted_draft)
        model = next((m['name'] for m in self.service.models if m['id'] == self.service.loaded_model), '')
        descriptions = {
            'unavailable': '尚未準備本機引擎。請在「對話設定」選擇引擎，並加入 GGUF 模型。',
            'unloaded': '模型尚未載入。選擇模型後，按「載入」。',
            'loading': '正在載入模型…',
            'ready': (model + ' · 已載入') if model else '模型已載入',
            'generating': '正在接收回覆…' if busy else '另一份對話正在生成，完成後即可送出。',
            'stopping': '正在停止回覆…' if busy else '另一份對話正在停止回覆…',
            'unloading': '正在卸載模型…',
            'error': '本機引擎需要處理，請依下方訊息檢查設定。',
            'closed': '本機引擎已關閉。',
        }
        capacity = self.service.settings.get('context_size', 4096)
        self.status.setText(descriptions.get(state, '本機引擎尚未就緒。') + f'  上下文容量 {capacity:,} tokens')
        if ready and self.service.needs_reload:
            self.send_button.setEnabled(False)
            self.retry_button.setEnabled(False)
            self.status.setText('模型或載入設定已變更，按「重新載入」後即可送出。' + f'  上下文容量 {capacity:,} tokens')
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
        if self.retry_button.isEnabled():
            self._follow_output = True
            self._action(self.chat_document.retry)

    def _stop(self):
        if self.chat_document.busy:
            self._action(self.chat_document.stop)

    def _select_model(self, _index):
        selected = self.model_combo.currentData()
        if selected and selected != self.service.selected_model:
            self._action(lambda: self.service.select_model(selected))
        elif not selected:
            self.refresh()

    def _add_model(self):
        filename, _ = QFileDialog.getOpenFileName(self, '加入已下載的模型', '', 'GGUF 模型 (*.gguf)')
        if filename:
            self._action(lambda: self.service.register_model(filename))

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
        if self.composing:
            return
        directory = QFileDialog.getExistingDirectory(self, '選擇 Markdown 匯出資料夾')
        if not directory:
            return
        path = self._action(lambda: self.chat_document.export_markdown(Path(directory)))
        if isinstance(path, (Path, str)) and path:
            self.status.setText('已匯出：' + str(path))

    def apply_appearance(self, settings):
        tokens = self._tokens = visual_tokens(settings)
        surface = workspace_colors(settings)['content']
        title_size = font_pixels(settings.get('ui_size', 12) + 6)
        self.setStyleSheet(f'''
            QWidget#LocalChatPage {{ background:{surface}; }}
            QWidget#ChatColumn, QWidget#ChatTranscript, QScrollArea#ChatScroll,
            QTextBrowser#ChatText, QFrame#ChatTurn, QFrame#ChatAssistant {{ background:transparent; border:0; }}
            QFrame#ChatUser {{ background:{tokens['surface']}; border:0; border-radius:12px; }}
            QFrame#ChatComposerFrame {{ background:{tokens['surface']}; border:1px solid {tokens['divider']}; border-radius:14px; }}
            QPlainTextEdit#ChatComposer {{ background:transparent; border:0; padding:4px; }}
            QTextBrowser#ChatText {{ padding:0; color:{tokens['text']}; }}
            QLabel#ChatEmptyTitle {{ font-size:{title_size}px; font-weight:600; color:{tokens['text']}; }}
            QLabel#ChatError {{ color:{tokens['error']}; }}
        ''')
        for turn in self._turns.values():
            for attempt in turn.attempts.values():
                attempt.content.apply_appearance(tokens)
