"""Inline edit instructions and review actions; the Markdown owner previews text."""
import math

from PySide6.QtCore import Qt, QEvent, QRect, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QInputMethodEvent
from PySide6.QtWidgets import QBoxLayout, QFrame, QLayout, QPlainTextEdit, QSizePolicy, QVBoxLayout, QWidget

from ..theme import visual_tokens, window_appearance
from ..ui_icons import icon
from ..widgets import ElidedLabel, button, row


class TextAssistInput(QPlainTextEdit):
    compositionChanged = Signal()
    submitted = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False
        self._commit_guard = QTimer(self)
        self._commit_guard.setSingleShot(True)
        self._commit_guard.setInterval(0)
        self.tokens = visual_tokens()
        self._placeholder = '描述你想如何編輯…'
        self.setPlaceholderText(self._placeholder)
        self.document().setDocumentMargin(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def setPlaceholderText(self, text):
        self._placeholder = text
        super().setPlaceholderText('' if self.composing else text)

    def inputMethodEvent(self, event):
        if self.isReadOnly():
            return super().inputMethodEvent(event)
        was_composing = self.composing
        self.composing = True
        self.compositionChanged.emit()
        attributes = []
        for attribute in event.attributes():
            if attribute.type == QInputMethodEvent.AttributeType.TextFormat:
                formatting = attribute.value.toCharFormat()
                if formatting.background().style() != Qt.BrushStyle.NoBrush:
                    formatting.setBackground(QColor(self.tokens['selected']))
                    formatting.setForeground(QColor(self.tokens['text']))
                    attribute = QInputMethodEvent.Attribute(attribute.type, attribute.start, attribute.length, formatting)
            attributes.append(attribute)
        themed = QInputMethodEvent(event.preeditString(), attributes)
        themed.setCommitString(event.commitString(), event.replacementStart(), event.replacementLength())
        # Preedit is painted while the native document may still be empty.
        super().setPlaceholderText('' if event.preeditString() else self._placeholder)
        try:
            super().inputMethodEvent(themed)
            event.setAccepted(themed.isAccepted())
        finally:
            self.composing = bool(event.preeditString())
            if was_composing and event.commitString() and not self.composing:
                # Some IMEs forward the confirming Return after the commit event.
                self._commit_guard.start()
            self.compositionChanged.emit()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.composing or self._commit_guard.isActive():
                event.accept()
            elif event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                super().keyPressEvent(event)
            else:
                if not self.isReadOnly() and not event.isAutoRepeat():
                    self.submitted.emit()
                event.accept()
        elif self.composing and event.key() == Qt.Key.Key_Escape:
            event.accept()
        else:
            super().keyPressEvent(event)


class TextAssistPanel(QFrame):
    closed = Signal()
    geometry_needed = Signal()

    def __init__(self, parent, controller, apply_callback):
        if parent is None:
            raise ValueError('文字協作面板必須屬於原文件頁面。')
        super().__init__(parent, Qt.WindowType.Widget)
        self.controller = controller
        self.apply_callback = apply_callback
        self._closed = False
        self._apply_error = ''
        self._generation_input = None
        self._last_instruction = ''
        self._reviewing = False
        self._geometry_signature = None
        self._theme_signature = None
        self.setObjectName('TextAssistPanel')
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(10, 10, 10, 10)
        self.body.setSpacing(0)
        self.body.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.instruction = TextAssistInput(self)
        self.instruction.setObjectName('TextAssistInstruction')
        self.instruction.setAccessibleName('修改要求')
        self.instruction.setPlaceholderText('描述你想如何編輯…')
        self.start_button = button('', self._start, 'Primary')
        self.stop_button = button('停止', controller.stop, 'Quiet')
        self.close_button = button('', self.dismiss, 'IconButton')
        for control, title in ((self.start_button, '送出修改要求'), (self.stop_button, '停止產生'),
                               (self.close_button, '關閉文字協作')):
            control.setToolTip(title)
            control.setAccessibleName(title)
            control.setAutoDefault(False)
            control.setDefault(False)
            control.setIconSize(QSize(16, 16))
        self.controls = QWidget()
        controls = row()
        controls.setContentsMargins(0, 0, 0, 0)
        controls.setSpacing(4)
        controls.addWidget(self.instruction, 1)
        controls.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignBottom)
        controls.addWidget(self.close_button, 0, Qt.AlignmentFlag.AlignBottom)
        self.controls.setLayout(controls)
        self.body.addWidget(self.controls)

        self.review_controls = QWidget()
        review = row()
        review.setContentsMargins(0, 0, 0, 0)
        review.setSpacing(6)
        self.review_controls.setLayout(review)
        self.status = ElidedLabel()
        self.status.setObjectName('TextAssistStatus')
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setAccessibleName('文字協作狀態')
        self.status.setMinimumWidth(0)
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        review.addWidget(self.status, 1)
        self.review_actions = QWidget()
        actions = row()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(2)
        self.review_actions.setLayout(actions)
        review.addWidget(self.review_actions, 0, Qt.AlignmentFlag.AlignVCenter)
        self.keep_button = button('保留', self._apply, 'Primary')
        self.retry_button = button('重試', self._retry, 'Quiet')
        self.delete_button = button('刪除', self.dismiss, 'Quiet')
        self.keep_button.setAccessibleName('保留候選並取代原文')
        self.retry_button.setAccessibleName('依相同要求重試')
        self.delete_button.setAccessibleName('刪除候選並還原原文')
        actions.addWidget(self.delete_button)
        actions.addWidget(self.stop_button)
        actions.addWidget(self.retry_button)
        actions.addWidget(self.keep_button)
        self.body.addWidget(self.review_controls)
        for control in (self.keep_button, self.retry_button, self.delete_button, self.stop_button):
            control.setAutoDefault(False)
            control.setDefault(False)
            control.setIconSize(QSize(14, 14))

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(40)
        self._refresh_timer.timeout.connect(self.refresh)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self._sync_metrics)
        self.instruction.document().documentLayout().documentSizeChanged.connect(self._queue_fit)
        self.instruction.textChanged.connect(self.refresh)
        self.instruction.compositionChanged.connect(self.refresh)
        self.instruction.submitted.connect(self._start)
        controller.changed.connect(self._changed)
        self.destroyed.connect(controller.dispose)
        self.setTabOrder(self.instruction, self.start_button)
        self.setTabOrder(self.start_button, self.close_button)
        self.setTabOrder(self.close_button, self.delete_button)
        self.setTabOrder(self.delete_button, self.stop_button)
        self.setTabOrder(self.stop_button, self.retry_button)
        self.setTabOrder(self.retry_button, self.keep_button)
        self._apply_theme()
        self.refresh()

    def _apply_theme(self):
        tokens = visual_tokens(window_appearance(self.parentWidget()))
        signature = (self._reviewing, tuple(sorted(tokens.items())))
        if signature == self._theme_signature:
            return
        self._theme_signature = signature
        self.instruction.tokens = tokens
        radius = 18 if self._reviewing else 24
        self.setStyleSheet(f'''
            QFrame#TextAssistPanel {{background:{tokens['raised']};border:1px solid {tokens['line']};border-radius:{radius}px;}}
            QPlainTextEdit#TextAssistInstruction {{background:transparent;border:0;padding:4px;color:{tokens['text']};placeholder-text-color:{tokens['secondary']};selection-background-color:{tokens['selected']};selection-color:{tokens['text']};}}
            QLabel#TextAssistStatus {{color:{tokens['secondary']};}}
            QFrame#TextAssistPanel QPushButton {{min-width:0;min-height:0;padding:3px 7px;border-radius:12px;}}
            QFrame#TextAssistPanel QPushButton#IconButton {{padding:6px;}}
        ''')
        self.start_button.setIcon(icon('arrow-up', tokens['on_accent'], 16))
        self.stop_button.setIcon(icon('stop-filled', tokens['text'], 14))
        self.close_button.setIcon(icon('close', tokens['text'], 16))
        self.retry_button.setIcon(icon('refresh-cw', tokens['text'], 14))
        self.delete_button.setIcon(icon('close', tokens['text'], 14))

    def _input_signature(self):
        return self.instruction.toPlainText().strip(), self.controller.selected_model_id

    def _input_matches_candidate(self):
        return self._generation_input is None or self._generation_input == self._input_signature()

    def _changed(self):
        if self._closed or not self.isVisible():
            return
        if self.controller.busy:
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()
        else:
            self._refresh_timer.stop()
            self.refresh()

    def _queue_fit(self, *_):
        if not self._closed and not self._reviewing and self.isVisible():
            self._fit_timer.start(0)

    def _action_widths(self):
        metrics = self.keep_button.fontMetrics()
        padding = self.keep_button.minimumSizeHint().width() - metrics.horizontalAdvance(self.keep_button.text())
        keep = metrics.horizontalAdvance('生成中…') + padding
        middle = max(self.stop_button.minimumSizeHint().width(), self.retry_button.minimumSizeHint().width())
        return self.delete_button.minimumSizeHint().width(), middle, keep

    def _actions_width(self):
        return sum(self._action_widths()) + 4

    def _metrics(self, width):
        metrics = self.instruction.fontMetrics()
        line = max(metrics.height(), metrics.lineSpacing())
        lines = max(1, self.instruction.document().blockCount(),
                    math.ceil(self.instruction.document().size().height()))
        # Native text layout needs a small cursor allowance beyond its line box.
        instruction = min(6, lines) * line + 10
        control = max(32, line + 6)
        if not self.instruction.toPlainText() and self.instruction.placeholderText():
            text_width = max(1, width - 20 - control * 2 - 8 - 10)
            placeholder = metrics.boundingRect(QRect(0, 0, text_width, line * 6),
                                              Qt.TextFlag.TextWordWrap, self.instruction.placeholderText())
            instruction = max(instruction, min(line * 6, placeholder.height()) + 10)
        action = max(28, self.keep_button.fontMetrics().height() + 6)
        status_width = self.status.fontMetrics().horizontalAdvance(self.status.text()) if not self.status.isHidden() else 0
        stacked = bool(status_width and self._actions_width() + status_width + 6 > width - 8)
        return instruction, control, action, line, stacked

    def preferred_width(self, available):
        if not self._reviewing:
            return max(1, min(500, available))
        width = max(230, self._actions_width() + 8)
        if not self.status.isHidden():
            width = max(width, min(300, width + self.status.fontMetrics().horizontalAdvance(self.status.text()) + 6))
        return max(1, min(width, available))

    def preferred_height(self, width):
        instruction, control, action, line, stacked = self._metrics(width)
        return (8 + action + (line + 4 if stacked else 0) if self._reviewing
                else 20 + max(instruction, control))

    def sizeHint(self):
        width = self.preferred_width(500)
        return QSize(width, self.preferred_height(width))

    def minimumSizeHint(self):
        return QSize(0, 0)

    def _sync_metrics(self):
        instruction, control, action, line, stacked = self._metrics(self.width())
        self.body.setContentsMargins(*(4, 4, 4, 4) if self._reviewing else (10, 10, 10, 10))
        self.instruction.setFixedHeight(instruction)
        self.controls.setFixedHeight(max(instruction, control))
        self.review_controls.setFixedHeight(action + (line + 4 if stacked else 0))
        self.review_controls.layout().setSpacing(4 if stacked else 6)
        self.review_controls.layout().setDirection(QBoxLayout.Direction.TopToBottom if stacked else QBoxLayout.Direction.LeftToRight)
        self.review_controls.layout().setStretch(0, 0 if stacked else 1)
        self.status.setFixedHeight(line)
        for widget in (self.start_button, self.close_button):
            widget.setFixedSize(control, control)
        for widget in (self.keep_button, self.stop_button, self.retry_button, self.delete_button):
            widget.setFixedHeight(action)
        delete, middle, keep = self._action_widths()
        self.delete_button.setFixedWidth(delete)
        self.stop_button.setFixedWidth(middle)
        self.retry_button.setFixedWidth(middle)
        self.keep_button.setFixedWidth(keep)
        signature = (self._reviewing, instruction if not self._reviewing else 0, control, action,
                     line, stacked, self.preferred_width(500))
        if signature != self._geometry_signature:
            self._geometry_signature = signature
            self.updateGeometry()
            self.geometry_needed.emit()

    def refresh(self):
        if self._closed:
            return
        state = self.controller.state
        busy = self.controller.busy
        self._reviewing = bool(self._reviewing or self._generation_input is not None or state != 'idle')
        if self._reviewing:
            self._fit_timer.stop()
        self._apply_theme()
        self.instruction.setVisible(not self._reviewing)
        self.controls.setVisible(not self._reviewing)
        self.review_controls.setVisible(self._reviewing)
        available = state not in {'invalidated', 'stale', 'closed', 'disposed'}
        can_apply = self.controller.can_apply and self._input_matches_candidate()
        self.instruction.setReadOnly(busy or not available)
        self.start_button.setEnabled(available and not busy and bool(self.instruction.toPlainText().strip())
                                     and not self.instruction.composing and bool(self.controller.selected_model_id)
                                     and self.controller.service.can_accept)
        self.stop_button.setVisible(busy)
        self.stop_button.setEnabled(state != 'stopping')
        self.retry_button.setVisible(not busy)
        self.retry_button.setEnabled(available and not busy and bool(self._last_instruction)
                                     and self.controller.service.can_accept)
        self.keep_button.setEnabled(can_apply)
        captions = {'loading': '準備中…', 'generating': '生成中…', 'stopping': '停止中…',
                    'stopped': '已停止'}
        status = self._apply_error or self.controller.error or captions.get(state, '')
        if self.controller.can_apply and not self._input_matches_candidate():
            status = '需求或模型已變更，請重新產生候選。'
        if not status and state == 'idle':
            if not self.controller.selected_model_id:
                status = '請先從文件選單選擇模型。'
            elif not self.controller.service.can_accept:
                status = '本機模型正忙碌，請稍後再試。'
        self.status.setText(status)
        inline_state = busy and not self._apply_error and not self.controller.error
        self.keep_button.setText(captions.get(state, '生成中…') if inline_state else '保留')
        self.status.setVisible(self._reviewing and bool(status) and not inline_state)
        self.status.setToolTip(status)
        self.status.setAccessibleDescription(status)
        self.start_button.setToolTip(status if not self._reviewing and status else '送出修改要求')
        self._sync_metrics()

    def _submit(self, instruction):
        self._apply_error = ''
        self._last_instruction = instruction
        self._generation_input = (instruction, self.controller.selected_model_id)
        self.controller.start(*self._generation_input)
        self.refresh()

    def _start(self):
        if self.start_button.isEnabled() and not self.instruction.composing:
            self._submit(self.instruction.toPlainText().strip())

    def _retry(self):
        if not self.retry_button.isEnabled():
            return
        self.instruction.blockSignals(True)
        self.instruction.setPlainText(self._last_instruction)
        self.instruction.blockSignals(False)
        self._submit(self._last_instruction)

    def _apply(self):
        if not self._input_matches_candidate():
            self.refresh()
            return
        try:
            candidate = self.controller.candidate_for_apply()
            if self.apply_callback('replace', candidate) is False:
                self._apply_error = '無法套用，請重新選取原文。'
                self.refresh()
                return
        except (ValueError, OSError) as error:
            self._apply_error = str(error)
            self.refresh()
            return
        self.dismiss()

    def focus_input(self):
        self.instruction.setFocus(Qt.FocusReason.OtherFocusReason)

    def dismiss(self):
        if not self._closed:
            self.close()

    def closeEvent(self, event):
        if not self._closed:
            self._closed = True
            self._refresh_timer.stop()
            self._fit_timer.stop()
            self.controller.dispose()
            self.closed.emit()
        super().closeEvent(event)

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)

    def hideEvent(self, event):
        self._refresh_timer.stop()
        self._fit_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_refresh_timer'):
            self._sync_metrics()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, '_refresh_timer') and event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange):
            self._apply_theme()
            self._sync_metrics()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()
            event.accept()
        else:
            super().keyPressEvent(event)
