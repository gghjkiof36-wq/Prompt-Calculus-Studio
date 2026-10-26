"""Document-owned Qt chat surface. Selection and settings never load a model."""
import math
from pathlib import Path
import weakref

from PySide6.QtCore import Qt, QTimer, Signal, QSignalBlocker, QSize, QEvent, QRectF, QRect, QPoint
from PySide6.QtGui import (
    QAction, QColor, QInputMethodEvent, QTextBlockFormat, QTextCharFormat, QTextCursor,
    QTextDocument, QTextOption, QBrush, QLinearGradient, QPainter, QPen,
)
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QPlainTextEdit,
    QPushButton, QSizePolicy, QTextBrowser, QVBoxLayout, QWidget, QWidgetAction,
    QAbstractScrollArea, QStyle, QStyleOptionButton, QStyleOptionFocusRect,
)

from ..theme import font_pixels, visual_tokens
from ..ui_icons import icon
from ..widgets import ElidedLabel, RoundMenu, button, label, row, scrolling
from ..popup_surface import popup_row_height
from ..workspace_style import workspace_colors
from ..dialog_presentation import reduced_motion
from ..elapsed_animation import ElapsedAnimation
from ..workspace_ambient import system_motion_enabled
from .thinking import split_thinking
from .approval_picker import ApprovalButton
from .attachment_strip import AttachmentStrip, mime_attachments


ATTEMPT_LABELS = {
    'receiving': '接收中', 'complete': '完成', 'stopped': '已停止',
    'interrupted': '已中斷', 'failed': '未完成', 'limited': '達到回覆上限',
}


class ChatComposer(QPlainTextEdit):
    submitted = Signal()
    compositionChanged = Signal()
    attachmentsRequested = Signal(object)
    attachmentError = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.composing = False
        self.tokens = visual_tokens()
        self._placeholder = '輸入訊息…'
        self._commit_guard = False
        self._commit_timer = QTimer(self)
        self._commit_timer.setSingleShot(True)
        self._commit_timer.timeout.connect(self._release_commit)
        self.setObjectName('ChatComposer')
        self.setAccessibleName('對話訊息')
        self.setPlaceholderText(self._placeholder)
        self.setTabChangesFocus(True)
        self.setMinimumHeight(80)
        self.setMaximumHeight(200)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.WidgetWidth)
        self.setAcceptDrops(True)
        self.textChanged.connect(self._fit_height)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)
        self._fit_height()

    def _fit_height(self, *_args):
        lines = max(2, self.document().blockCount(), math.ceil(self.document().size().height()))
        height = max(80, min(200, lines * self.fontMetrics().lineSpacing() + 20))
        if self.height() != height:
            self.setFixedHeight(height)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._fit_height()

    def canInsertFromMimeData(self, mime):
        return mime.hasImage() or any(url.isLocalFile() for url in mime.urls()) or super().canInsertFromMimeData(mime)

    def insertFromMimeData(self, mime):
        try:
            items = mime_attachments(mime)
        except (ValueError, TypeError) as error:
            self.attachmentError.emit(str(error))
            return
        if items is not None:
            self.attachmentsRequested.emit(items)
        else:
            super().insertFromMimeData(mime)

    def dragEnterEvent(self, event):
        if self.canInsertFromMimeData(event.mimeData()):
            event.acceptProposedAction()
        else:
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasImage() or any(url.isLocalFile() for url in event.mimeData().urls()):
            event.acceptProposedAction()
        else:
            super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasImage() or any(url.isLocalFile() for url in event.mimeData().urls()):
            self.insertFromMimeData(event.mimeData())
            event.acceptProposedAction()
        else:
            super().dropEvent(event)

    def _release_commit(self):
        self._commit_guard = False

    def inputMethodEvent(self, event):
        # Guard both preedit and the synchronous commit/textChanged sequence.
        # A candidate-confirming Return must not become a send action.
        self.composing = True
        self.compositionChanged.emit()
        # Match the Markdown editor's active-clause treatment. Keep the IME's
        # underline, cursor and replacement range while replacing white fill.
        attributes = []
        for attribute in event.attributes():
            if attribute.type == QInputMethodEvent.AttributeType.TextFormat:
                formatting = attribute.value.toCharFormat()
                if formatting.background().style() != Qt.BrushStyle.NoBrush:
                    formatting.setBackground(QColor(self.tokens['selected']))
                    formatting.setForeground(QColor(self.tokens['text']))
                    attribute = QInputMethodEvent.Attribute(
                        attribute.type, attribute.start, attribute.length, formatting)
            attributes.append(attribute)
        themed = QInputMethodEvent(event.preeditString(), attributes)
        themed.setCommitString(event.commitString(), event.replacementStart(), event.replacementLength())
        # Qt may still consider the document empty while preedit is painted.
        # Clear the placeholder for that interval; do not put preedit in draft.
        self.setPlaceholderText('' if event.preeditString() else self._placeholder)
        try:
            super().inputMethodEvent(themed)
            event.setAccepted(themed.isAccepted())
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
    """Native plain text with a lazy, closed rich projection when needed."""

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
        self.rich = None
        self._rich_presented = False
        self._rich_ready_height = None
        self._reserved_height = None
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
        if markdown:
            from .chat_render import needs_rich
            if self.rich is not None or needs_rich(value[0]):
                if self.rich is None and self._visible_for_rich():
                    self._start_rich()
                if self.rich is not None:
                    self.rich.set_content(value[0], self._tokens or visual_tokens(), self.font())
                if self._rich_presented:
                    return
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
        if (not self._setting_text and not self._rich_presented
                and self._rich_ready_height is not None and not self.textCursor().hasSelection()):
            self._rich_ready(self._rich_ready_height)

    def _fit_height(self, *_args):
        if self._rich_presented or self._reserved_height is not None:
            return
        height = math.ceil(self.document().size().height()) + 8
        # Very long replies remain readable with an inner scrollbar rather
        # than exceeding Qt's maximum widget geometry.
        self.setFixedHeight(max(self.fontMetrics().height() + 8, min(12000, height)))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.rich is not None:
            self.rich.setGeometry(self.rect())
        self._fit_height()

    def event(self, event):
        result = super().event(event)
        # QAbstractScrollArea forwards resizeEvent from its viewport. Once the
        # native viewport is hidden, track the actual outer widget instead.
        if event.type() == QEvent.Type.Resize and getattr(self, 'rich', None) is not None:
            self.rich.setGeometry(self.rect())
        return result

    def showEvent(self, event):
        super().showEvent(event)
        QTimer.singleShot(0, self.sync_rich_visibility)

    def hideEvent(self, event):
        super().hideEvent(event)
        self.sync_rich_visibility()

    def _visible_for_rich(self):
        if not self.isVisible():
            return False
        ancestor = self.parentWidget()
        while ancestor is not None:
            bounds = QRect(self.mapTo(ancestor, QPoint(0, 0)), self.size())
            if not ancestor.isVisible() or not bounds.intersects(ancestor.rect()):
                return False
            ancestor = ancestor.parentWidget()
        return True

    def _start_rich(self):
        from .chat_rich import RichChatView
        self.rich = RichChatView(self)
        self.rich.setGeometry(self.rect())
        self.rich.contentReady.connect(self._rich_ready)
        self.rich.page().selectionChanged.connect(self.sync_rich_visibility)
        # Keep a stable native projection until the first rich frame arrives.
        self.rich.show()
        self.rich.lower()
        self.viewport().show()
        self.viewport().raise_()

    def sync_rich_visibility(self):
        from shiboken6 import isValid
        if not isValid(self):
            return
        if self._content is None or not self._content[1]:
            return
        from .chat_render import needs_rich
        visible = self._visible_for_rich()
        if self.rich is not None and not visible and not self.rich.page().selectedText():
            # Preserve the measured height when a page leaves the viewport, so
            # scrolling does not bounce as browser resources are released.
            self._reserved_height = self.height()
            previous, self.rich = self.rich, None
            previous.contentReady.disconnect(self._rich_ready)
            previous.page().selectionChanged.disconnect(self.sync_rich_visibility)
            previous.hide()
            previous.deleteLater()
            self._rich_presented = False
            self._rich_ready_height = None
            value, self._content = self._content, None
            self.viewport().show()
            self.set_content(value[0], markdown=True)
        elif self.rich is None and visible and needs_rich(self._content[0]):
            self._start_rich()
            self.rich.set_content(self._content[0], self._tokens or visual_tokens(), self.font())

    def _rich_ready(self, height):
        self._rich_ready_height = height
        if not self._rich_presented and self.textCursor().hasSelection():
            # Keep the visible native selection until its owner releases it.
            # Hiding it here would leave later stream deltas queued forever.
            return
        self._rich_presented = True
        self._reserved_height = None
        self.setFixedHeight(height)
        self.viewport().hide()
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.rich.setGeometry(self.rect())
        self.rich.raise_()

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
            value = self._pending or self._content
            self._content = None
            self.set_content(value[0], markdown=value[1])


class ThinkingToggle(QPushButton):
    """A text-only activity sweep using PCS motion and visibility policy."""

    def __init__(self, callback, parent=None):
        super().__init__('思考', parent)
        self.setObjectName('Quiet')
        self.setCheckable(True)
        self.clicked.connect(callback)
        self._thinking = False
        self._phase = 0.
        self._tokens = visual_tokens()
        self._system_motion = system_motion_enabled()
        self._watched = weakref.WeakSet()
        self.motion = ElapsedAnimation(self)
        self.motion.setDuration(2200)
        self.motion.setStartValue(0.)
        self.motion.setEndValue(1.)
        self.motion.valueChanged.connect(self._advance)
        self.motion.finished.connect(self._sync_motion)
        QApplication.instance().applicationStateChanged.connect(self._sync_motion)

    def set_thinking(self, value):
        self._thinking = bool(value)
        self.setText('思考中' if self._thinking else '思考')
        self._sync_motion()

    def apply_appearance(self, tokens):
        self._tokens = tokens
        self._sync_motion()
        self.update()

    def _can_animate(self):
        host = self.window()
        return (self._thinking and not reduced_motion(self) and self._system_motion
                and self.isVisible() and not self.visibleRegion().isEmpty()
                and not host.isMinimized() and host.isActiveWindow())

    def _sync_motion(self, *_):
        running = self.motion.state() == self.motion.State.Running
        if self._can_animate():
            if not running:
                self.motion.start()
        elif running:
            self.motion.stop()
            self._phase = 0.
            self.update()

    def _advance(self, value):
        if not self._can_animate():
            self._sync_motion()
            return
        self._phase = float(value)
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        ancestor = self.parentWidget()
        while ancestor is not None:
            if ancestor not in self._watched:
                self._watched.add(ancestor)
                ancestor.installEventFilter(self)
                if isinstance(ancestor, QAbstractScrollArea):
                    ancestor.verticalScrollBar().valueChanged.connect(self._sync_motion)
                    ancestor.horizontalScrollBar().valueChanged.connect(self._sync_motion)
            ancestor = ancestor.parentWidget()
        self._sync_motion()

    def hideEvent(self, event):
        self.motion.stop()
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        kind = event.type()
        if kind == QEvent.Type.ActivationChange:
            self._system_motion = system_motion_enabled()
        if kind in (QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.Move,
                    QEvent.Type.Resize, QEvent.Type.WindowStateChange,
                    QEvent.Type.ActivationChange):
            self._sync_motion()
        return False

    def paintEvent(self, event):
        self._sync_motion()
        option = QStyleOptionButton()
        self.initStyleOption(option)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        self.style().drawControl(QStyle.ControlElement.CE_PushButtonBevel, option, painter, self)
        rect = QRectF(self.style().subElementRect(QStyle.SubElement.SE_PushButtonContents, option, self))
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        icon_width = option.iconSize.width() + 4 if not option.icon.isNull() else 0
        left = rect.left() + (rect.width() - text_width - icon_width) / 2
        if icon_width:
            icon_rect = QRectF(left, rect.center().y() - option.iconSize.height() / 2,
                               option.iconSize.width(), option.iconSize.height())
            option.icon.paint(painter, icon_rect.toRect())
        text_rect = QRectF(left + icon_width, rect.top(), text_width + 1, rect.height())
        if self.motion.state() == self.motion.State.Running:
            band = max(28., text_width * .7)
            center = text_rect.left() - band + self._phase * (text_width + 2 * band)
            sweep = QLinearGradient(center - band / 2, 0, center + band / 2, 0)
            sweep.setColorAt(0., QColor(self._tokens['muted']))
            sweep.setColorAt(.5, QColor(self._tokens['text']))
            sweep.setColorAt(1., QColor(self._tokens['muted']))
            painter.setPen(QPen(QBrush(sweep), 0))
        else:
            painter.setPen(QColor(self._tokens['text']))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
        if self.hasFocus():
            focus = QStyleOptionFocusRect()
            focus.initFrom(self)
            focus.rect = self.style().subElementRect(QStyle.SubElement.SE_PushButtonFocusRect, option, self)
            self.style().drawPrimitive(QStyle.PrimitiveElement.PE_FrameFocusRect, focus, painter, self)


class ChatAttempt(QFrame):
    branchRequested = Signal(str)
    exportMarkdownRequested = Signal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName('ChatAssistant')
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self.thinking = QFrame()
        self.thinking.setObjectName('ChatThinking')
        thinking_layout = QVBoxLayout(self.thinking)
        thinking_layout.setContentsMargins(0, 0, 0, 0)
        thinking_layout.setSpacing(8)
        self.thinking_toggle = ThinkingToggle(self._sync_thinking)
        self.thinking_toggle.setAccessibleName('展開思考')
        self.thinking_copy = button('', self._copy_thinking, 'IconButton')
        self.thinking_copy.setIcon(icon('copy', size=16))
        self.thinking_copy.setFixedSize(30, 30)
        self.thinking_copy.setToolTip('複製思考')
        self.thinking_copy.setAccessibleName('複製思考')
        thinking_layout.addLayout(row(self.thinking_toggle, self.thinking_copy, None))
        self.thinking_body = QFrame()
        self.thinking_body.setObjectName('ChatThinkingBody')
        thinking_body_layout = QVBoxLayout(self.thinking_body)
        thinking_body_layout.setContentsMargins(12, 0, 0, 0)
        self.thinking_content = ChatText()
        self.thinking_content.setProperty('thinking', True)
        self.thinking_content.setAccessibleName('思考內容')
        thinking_body_layout.addWidget(self.thinking_content)
        thinking_layout.addWidget(self.thinking_body)
        layout.addWidget(self.thinking)
        self.heading = label('', 'Subtle')
        self.copy_button = button('', self._copy, 'IconButton')
        self.copy_button.setIcon(icon('copy', size=16))
        self.copy_button.setFixedSize(30, 30)
        self.copy_button.setToolTip('複製回覆')
        self.copy_button.setAccessibleName('複製這次回覆')
        self.branch_button = button('', lambda: self.branchRequested.emit(self._attempt_id), 'IconButton')
        self.branch_button.setIcon(icon('git-branch', size=16))
        self.branch_button.setFixedSize(30, 30)
        self.branch_button.setToolTip('從這裡建立分支')
        self.branch_button.setAccessibleName('從這次回覆建立分支')
        self.more_button = button('', self._open_reply_actions, 'IconButton')
        self.more_button.setIcon(icon('more-horizontal', size=16))
        self.more_button.setFixedSize(30, 30)
        self.more_button.setAccessibleName('回覆操作')
        self.more_button.setToolTip('回覆操作')
        self.content = ChatText()
        self.content.setAccessibleName('模型回覆')
        layout.addWidget(self.content)
        self.reason = label('', 'Subtle', True)
        layout.addWidget(self.reason)
        layout.addLayout(row(self.copy_button, self.branch_button, self.more_button, self.heading, None))
        self._attempt_id = ''
        self._raw = ''
        self._answer = self._thinking_text = ''
        self._last_receiving = None
        self._has_thinking = False
        self._copy_reset = QTimer(self)
        self._copy_reset.setSingleShot(True)
        self._copy_reset.timeout.connect(self._reset_copy)
        self._thinking_copy_reset = QTimer(self)
        self._thinking_copy_reset.setSingleShot(True)
        self._thinking_copy_reset.timeout.connect(self._reset_thinking_copy)

    def update_attempt(self, attempt, index, total, model_names):
        self._attempt_id = attempt.get('id', '')
        self.branch_button.setVisible(bool(self._attempt_id))
        self._raw = attempt.get('text', '')
        receiving = attempt.get('status') == 'receiving'
        parts = split_thinking(self._raw, receiving=receiving)
        self._answer, self._thinking_text = parts.answer, parts.thinking
        if parts.has_thinking and not self._has_thinking:
            self.thinking_toggle.setChecked(receiving)
        # Collapse once at the lifecycle boundary, not on later refreshes.
        if self._last_receiving is True and not receiving:
            self.thinking_toggle.setChecked(False)
        self._last_receiving = receiving
        self._has_thinking = parts.has_thinking
        self.thinking.setVisible(parts.has_thinking)
        self.thinking_toggle.set_thinking(parts.has_thinking and receiving and not parts.closed)
        self.thinking_content.set_content(parts.thinking)
        self.thinking_copy.setEnabled(bool(parts.thinking))
        self._sync_thinking()
        status = ATTEMPT_LABELS.get(attempt.get('status'), '未完成')
        model = attempt.get('model_name') or model_names.get(attempt.get('model'), '先前使用的模型')
        self.heading.setText(status)
        self.heading.setVisible(attempt.get('status') != 'complete')
        self.copy_button.setToolTip(f'複製回覆 · {model}' + (f' · 第 {index + 1} 次' if total > 1 else ''))
        self.copy_button.setEnabled(bool(parts.answer.strip()))
        self.copy_button.setVisible(bool(parts.answer.strip()))
        self.more_button.setVisible(bool(parts.answer.strip()) and bool(self._attempt_id))
        self.content.setVisible(bool(parts.answer.strip()) or not (parts.has_thinking or parts.pending_open))
        self.content.set_content(parts.answer or ('正在等待回覆…' if receiving else '尚未收到文字。'))
        self.reason.setText(attempt.get('reason', ''))
        self.reason.setVisible(bool(attempt.get('reason')))

    def _copy(self):
        QApplication.clipboard().setText(self._answer)
        self.copy_button.setIcon(icon('check', size=16))
        self.copy_button.setAccessibleName('回覆已複製')
        self._copy_reset.start(1600)

    def _open_reply_actions(self):
        menu = RoundMenu(self)
        menu.addAction('另存為 Markdown…', lambda: self.exportMarkdownRequested.emit(self._attempt_id))
        menu.open_for(self.more_button)
        self._reply_menu = menu

    def _reset_copy(self):
        self.copy_button.setIcon(icon('copy', size=16))
        self.copy_button.setAccessibleName('複製這次回覆')

    def _sync_thinking(self):
        expanded = self.thinking_toggle.isChecked()
        self.thinking_body.setVisible(expanded)
        self.thinking_copy.setVisible(expanded and bool(self._thinking_text))
        self.thinking_toggle.setIcon(icon('chevron-down' if expanded else 'chevron-right', size=14))
        self.thinking_toggle.setAccessibleName('收合思考' if expanded else '展開思考')
        self.thinking_toggle.setToolTip('收合思考' if expanded else '展開思考')

    def _copy_thinking(self):
        QApplication.clipboard().setText(self._thinking_text)
        self.thinking_copy.setIcon(icon('check', size=16))
        self.thinking_copy.setAccessibleName('思考已複製')
        self._thinking_copy_reset.start(1600)

    def _reset_thinking_copy(self):
        self.thinking_copy.setIcon(icon('copy', size=16))
        self.thinking_copy.setAccessibleName('複製思考')

    def apply_appearance(self, tokens):
        self.content.apply_appearance(tokens)
        self.thinking_content.apply_appearance(tokens)
        self.thinking_toggle.apply_appearance(tokens)


class ChatTurn(QFrame):
    branchRequested = Signal(str, str)
    exportMarkdownRequested = Signal(str, str)

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
        self._turn_id = ''
        self.user_copy_button = button('', self._copy_user, 'IconButton')
        self.user_copy_button.setIcon(icon('copy', size=16))
        self.user_copy_button.setFixedSize(30, 30)
        self.user_copy_button.setToolTip('複製訊息')
        self.user_copy_button.setAccessibleName('複製這則訊息')
        self._user_copy_reset = QTimer(self)
        self._user_copy_reset.setSingleShot(True)
        self._user_copy_reset.timeout.connect(self._reset_user_copy)
        self.user_more_button = button('', self._open_user_actions, 'IconButton')
        self.user_more_button.setIcon(icon('more-horizontal', size=16))
        self.user_more_button.setFixedSize(30, 30)
        self.user_more_button.setToolTip('更多操作')
        self.user_more_button.setAccessibleName('訊息的更多操作')
        self.user_group = QWidget()
        user_group_layout = QVBoxLayout(self.user_group)
        user_group_layout.setContentsMargins(0, 0, 0, 0)
        user_group_layout.setSpacing(4)
        user_group_layout.addWidget(self.user, 0, Qt.AlignmentFlag.AlignRight)
        self.user_attachments = AttachmentStrip(read_only=True)
        user_group_layout.addWidget(self.user_attachments, 0, Qt.AlignmentFlag.AlignRight)
        user_actions = QHBoxLayout()
        user_actions.setContentsMargins(0, 0, 0, 0)
        user_actions.setSpacing(4)
        user_actions.addStretch()
        user_actions.addWidget(self.user_copy_button)
        user_actions.addWidget(self.user_more_button)
        user_group_layout.addLayout(user_actions)
        self.stack.addWidget(self.user_group)
        self.history_button = button('', self._toggle_history, 'Quiet')
        self.history_button.setCheckable(True)
        self.stack.addWidget(self.history_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.attempts = {}
        self._attempt_order = []
        self._user_source = ''
        self._display_signature = None

    def update_turn(self, turn, model_names, tokens):
        signature = (turn['id'], turn.get('text', ''), tuple(
            tuple(attempt.get(key, '') for key in ('id', 'text', 'status', 'reason', 'model', 'model_name'))
            for attempt in turn.get('attempts', [])), tuple(model_names.items()),
            tuple((item.get('id'), item.get('name'), item.get('size')) for item in turn.get('attachments', [])))
        if signature == self._display_signature:
            return
        self._display_signature = signature
        self._turn_id = turn['id']
        self._user_source = turn.get('text', '')
        self.user_copy_button.setVisible(bool(self._user_source))
        self.user_text.set_content(self._user_source, markdown=False)
        self.user.setVisible(bool(self._user_source.strip()))
        self.user_attachments.sync(turn.get('attachments', []))
        self.user_attachments.apply_appearance(tokens)
        self._fit_user()
        attempts = turn.get('attempts', [])
        self._attempt_order = [attempt['id'] for attempt in attempts]
        present = set()
        for index, attempt in enumerate(attempts):
            identity = attempt['id']
            present.add(identity)
            if identity not in self.attempts:
                widget = ChatAttempt()
                widget.branchRequested.connect(lambda attempt_id: self.branchRequested.emit(self._turn_id, attempt_id))
                widget.exportMarkdownRequested.connect(lambda attempt_id: self.exportMarkdownRequested.emit(self._turn_id, attempt_id))
                widget.apply_appearance(tokens)
                self.attempts[identity] = widget
                self.stack.addWidget(widget)
            self.attempts[identity].update_attempt(attempt, index, len(attempts), model_names)
        for identity in set(self.attempts) - present:
            widget = self.attempts.pop(identity)
            self.stack.removeWidget(widget)
            widget.deleteLater()
        self._sync_history()

    def _copy_user(self):
        QApplication.clipboard().setText(self._user_source)
        self.user_copy_button.setIcon(icon('check', size=16))
        self.user_copy_button.setAccessibleName('訊息已複製')
        self._user_copy_reset.start(1600)

    def _reset_user_copy(self):
        self.user_copy_button.setIcon(icon('copy', size=16))
        self.user_copy_button.setAccessibleName('複製這則訊息')

    def _open_user_actions(self):
        menu = RoundMenu(self)
        menu.addAction('從這裡建立分支', lambda: self.branchRequested.emit(self._turn_id, ''))
        menu.open_for(self.user_more_button)
        self._user_menu = menu

    def _fit_user(self):
        measure = self.user_text.fontMetrics()
        natural = max((measure.horizontalAdvance(line) for line in self._user_source.splitlines()), default=48) + 36
        self.user.setFixedWidth(max(72, min(natural, round(self.width() * .8))))
        self.user_attachments.setFixedWidth(max(180, min(480, round(self.width() * .8))))

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


class ChatModelMenu(RoundMenu):
    """Searchable model choices on the shared non-blocking popup surface."""

    def __init__(self, page):
        super().__init__(page)
        self.page = page
        tokens = page._tokens
        self.choices = []
        self.setObjectName('ChatModelMenu')
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(8, 4, 8, 6)
        header_layout.setSpacing(6)
        header_layout.addWidget(label('模型', 'Subtle'))
        self.search = QLineEdit()
        self.search.setObjectName('ChatModelSearch')
        self.search.setAccessibleName('搜尋對話模型')
        self.search.setPlaceholderText('搜尋模型')
        self.search.setClearButtonEnabled(True)
        self.search.addAction(icon('search', tokens['secondary'], 16), QLineEdit.ActionPosition.LeadingPosition)
        header_layout.addWidget(self.search)
        width = min(440, max(300, page.width() - 32))
        header.setFixedWidth(width)
        search_action = QWidgetAction(self)
        search_action.setDefaultWidget(header)
        self.addAction(search_action)
        self.search.installEventFilter(self)
        self.search.textChanged.connect(self._filter)
        locked = page.service.state in {'loading', 'generating', 'stopping', 'unloading', 'closed'}
        for model in page.service.models:
            action = QWidgetAction(self)
            action.setText(model['name'])
            action.setData(model['id'])
            action.setCheckable(True)
            action.setChecked(model['id'] == page.service.selected_model)
            action.setEnabled(not locked)
            entry = QFrame()
            entry.setObjectName('ChatModelEntry')
            entry.setFixedWidth(width)
            entry.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            entry.setMinimumHeight(popup_row_height(self.fontMetrics(), 18))
            layout = QHBoxLayout(entry)
            layout.setContentsMargins(10, 5, 10, 5)
            layout.setSpacing(8)
            mark = label('')
            mark.setPixmap(icon('server', tokens['secondary'], 18).pixmap(18, 18))
            layout.addWidget(mark)
            caption = ElidedLabel(model['name'])
            caption.setTextFormat(Qt.TextFormat.PlainText)
            caption.setMinimumWidth(0)
            layout.addWidget(caption, 1)
            check = label('')
            check.setFixedSize(18, 18)
            if action.isChecked():
                check.setPixmap(icon('check', tokens['text'], 18).pixmap(18, 18))
            layout.addWidget(check)
            entry.setToolTip(model['name'] + '\n' + model['path'])
            entry.setStyleSheet(
                'QFrame#ChatModelEntry {border:0; border-radius:7px; background:transparent;}'
                'QFrame#ChatModelEntry[active="true"] {background:' + tokens['popup_active'] + ';}'
                'QFrame#ChatModelEntry QLabel {background:transparent; padding:0; border:0;}')
            action.setDefaultWidget(entry)
            action.triggered.connect(lambda _checked=False, identity=model['id']: self._choose(identity))
            self.addAction(action)
            self.choices.append((action, entry, model['name'].casefold()))
        self.empty_action = self.addAction('尚未加入模型' if not self.choices else '沒有符合的模型')
        self.empty_action.setEnabled(False)
        self.addSeparator()
        self.manage_action = self.addAction('管理模型…')
        self.manage_action.triggered.connect(self._manage)
        self.manage_action.setEnabled(page.service.state != 'closed')
        self.hovered.connect(self._hover)
        self._filter()

    def _filter(self, *_args):
        query = self.search.text().strip().casefold()
        visible = 0
        for action, _entry, name in self.choices:
            match = not query or all(word in name for word in query.split())
            action.setVisible(match)
            visible += int(match)
        self.empty_action.setVisible(not visible)

    def _hover(self, active):
        for action, entry, _name in self.choices:
            selected = action is active
            if entry.property('active') != selected:
                entry.setProperty('active', selected)
                entry.style().unpolish(entry)
                entry.style().polish(entry)
                entry.update()

    def eventFilter(self, watched, event):
        if watched is self.search and event.type() == QEvent.Type.KeyPress:
            if event.key() == Qt.Key.Key_Escape:
                self.close()
                return True
            if event.key() == Qt.Key.Key_Down:
                first = next((action for action, _entry, _name in self.choices
                              if action.isVisible() and action.isEnabled()), self.manage_action)
                self.setFocus()
                self.setActiveAction(first)
                return True
        return super().eventFilter(watched, event)

    def open_for(self, control, view=None, *, align_right=False):
        super().open_for(control, view, align_right=align_right)
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def _choose(self, identity):
        self.close()
        self.page._select_model(identity)

    def _manage(self):
        self.close()
        self.page._add_model()


class ChatPage(QWidget):
    """A view of a shared ChatDocument; background ownership stays in the doc."""
    branchRequested = Signal(str, str)
    pcsAttachmentsRequested = Signal()
    exportMarkdownRequested = Signal(str, str)

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
        self._history_limit = 30
        self._ui_error = ''
        self._dismissed_error = ''
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        self._scroll_timer = QTimer(self)
        self._scroll_timer.setSingleShot(True)
        self._scroll_timer.timeout.connect(self._scroll_to_bottom)
        self._visibility_timer = QTimer(self)
        self._visibility_timer.setSingleShot(True)
        self._visibility_timer.timeout.connect(self._sync_rich_visibility)
        self._history_anchor = None
        self._anchor_timer = QTimer(self)
        self._anchor_timer.setSingleShot(True)
        self._anchor_timer.timeout.connect(self._restore_history_anchor)
        self._anchor_finish = QTimer(self)
        self._anchor_finish.setSingleShot(True)
        self._anchor_finish.timeout.connect(self._finish_history_anchor)
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
        self.settings_action = QAction('模型設定…', self)
        self.settings_action.triggered.connect(self.open_settings)
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
        self.history_button = button('載入較早的訊息', self._load_earlier, 'Quiet')
        self.messages.addWidget(self.history_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self.messages.addStretch()
        self.scroll = scrolling(self.transcript)
        self.scroll.setObjectName('ChatScroll')
        self.scroll.verticalScrollBar().valueChanged.connect(self._scroll_position_changed)
        self.scroll.verticalScrollBar().rangeChanged.connect(self._scroll_range_changed)
        self.scroll.verticalScrollBar().sliderPressed.connect(self._cancel_history_anchor)
        self.scroll.verticalScrollBar().actionTriggered.connect(self._cancel_history_anchor)
        self.column.addWidget(self.scroll, 1)

        self.composer_frame = QFrame()
        self.composer_frame.setObjectName('ChatComposerFrame')
        composer_layout = QVBoxLayout(self.composer_frame)
        composer_layout.setContentsMargins(16, 12, 12, 10)
        composer_layout.setSpacing(4)
        self.attachments = AttachmentStrip()
        self.attachments.removeRequested.connect(self._remove_attachment)
        composer_layout.addWidget(self.attachments)
        self.composer = ChatComposer()
        self.composer.setPlainText(document.payload.get('draft', ''))
        self.composer.textChanged.connect(self._draft_changed)
        self.composer.compositionChanged.connect(self._composition_changed)
        self.composer.submitted.connect(self._send)
        self.composer.attachmentsRequested.connect(self._add_attachments)
        self.composer.attachmentError.connect(self._approval_error)
        composer_layout.addWidget(self.composer)
        self.status = label('', 'Subtle')
        self.status.setAccessibleName('本機模型狀態')
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.approval_button = ApprovalButton(self.service)
        self.approval_button.errorOccurred.connect(self._approval_error)
        self.add_button = button('', self._open_attachments, 'IconButton')
        self.add_button.setIcon(icon('plus', size=20))
        self.add_button.setFixedSize(36, 36)
        self.add_button.setToolTip('加入附件')
        self.add_button.setAccessibleName('加入附件')
        self.model_button = ChatModelButton(self._open_models)
        self.stop_button = button('', self._stop, 'ChatSend')
        self.stop_button.setIcon(icon('stop', 'on-accent', 18))
        self.stop_button.setAccessibleName('停止回覆')
        self.stop_button.setToolTip('停止回覆')
        self.send_button = button('', self._send, 'ChatSend')
        self.send_button.setIcon(icon('arrow-up', 'on-accent', 20))
        self.send_button.setAccessibleName('送出訊息')
        self.send_button.setToolTip('送出訊息')
        for control in (self.send_button, self.stop_button):
            control.setFixedSize(40, 40)
            control.setIconSize(QSize(20, 20))
        controls = row(self.add_button, self.approval_button, self.status, self.model_button,
                       self.stop_button, self.send_button)
        controls.setStretch(2, 1)
        controls.setSpacing(8)
        composer_layout.addLayout(controls)
        self.column.addWidget(self.composer_frame)
        self.error_frame = QFrame()
        self.error_frame.setObjectName('ChatErrorFrame')
        error_layout = QHBoxLayout(self.error_frame)
        error_layout.setContentsMargins(12, 9, 10, 9)
        self.error_icon = label('')
        self.error_icon.setFixedSize(20, 20)
        error_layout.addWidget(self.error_icon, 0, Qt.AlignmentFlag.AlignTop)
        self.error_label = label('', 'ChatError', True)
        self.error_label.setTextFormat(Qt.TextFormat.PlainText)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.setAccessibleName('對話錯誤')
        error_layout.addWidget(self.error_label, 1)
        self.error_retry = button('重試', self._retry, 'Quiet')
        error_layout.addWidget(self.error_retry)
        self.dismiss_error = button('', self._dismiss_error, 'IconButton')
        self.dismiss_error.setIcon(icon('x', size=14))
        self.dismiss_error.setFixedSize(26, 26)
        self.dismiss_error.setAccessibleName('關閉錯誤提示')
        error_layout.addWidget(self.dismiss_error)
        self.column.addWidget(self.error_frame)
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
        self._visibility_timer.start(0)
        if self._history_anchor is not None:
            return
        if self._rendering or self._scroll_timer.isActive():
            return
        bar = self.scroll.verticalScrollBar()
        self._follow_output = value >= bar.maximum() - 40

    def _scroll_range_changed(self, _minimum, _maximum):
        self._visibility_timer.start(0)
        if self._history_anchor is not None:
            self._anchor_timer.start(0)
            # Finishing is debounced from the last actual layout/range change,
            # not from the start of inserting a potentially large batch.
            self._anchor_finish.start(250)
            return
        if self._follow_output:
            self._scroll_timer.start(0)

    def _scroll_to_bottom(self):
        if self._follow_output:
            bar = self.scroll.verticalScrollBar()
            bar.setValue(bar.maximum())

    def _sync_rich_visibility(self):
        for turn in self._turns.values():
            for attempt in turn.attempts.values():
                attempt.content.sync_rich_visibility()
                attempt.thinking_content.sync_rich_visibility()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._visibility_timer.start(0)

    def hideEvent(self, event):
        self._cancel_history_anchor()
        super().hideEvent(event)

    def _cancel_history_anchor(self, *_args):
        self._history_anchor = None
        self._anchor_timer.stop()
        self._anchor_finish.stop()

    def _load_earlier(self):
        bar = self.scroll.verticalScrollBar()
        anchor = next((turn for turn in self._turns.values()
                       if turn.mapTo(self.scroll.viewport(), QPoint(0, 0)).y() + turn.height() > 0), None)
        anchor_y = anchor.mapTo(self.scroll.viewport(), QPoint(0, 0)).y() if anchor else None
        self._history_anchor = (anchor, anchor_y) if anchor else None
        self._follow_output = False
        self._history_limit += 30
        self.refresh()
        # Newly inserted widgets are above the current transcript. Retain the
        # same reading position after Qt has assigned their native heights.
        self._anchor_timer.start(0)
        self._anchor_finish.start(250)

    def _restore_history_anchor(self):
        if self._history_anchor is None:
            return True
        anchor, position = self._history_anchor
        self.messages.activate()
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.value() + anchor.mapTo(self.scroll.viewport(), QPoint(0, 0)).y() - position)
        # QScrollArea can publish its new range after the layout above has
        # already placed children. Do not release an anchor clamped by that
        # still-old range; the next rangeChanged restores it correctly.
        return abs(anchor.mapTo(self.scroll.viewport(), QPoint(0, 0)).y() - position) < 2

    def _finish_history_anchor(self):
        if self._restore_history_anchor():
            self._history_anchor = None
        else:
            self._anchor_finish.start(250)

    def refresh(self):
        payload = self.chat_document.payload
        turns = payload.get('turns', [])
        earlier = max(0, len(turns) - self._history_limit)
        self.history_button.setVisible(bool(earlier))
        self.history_button.setText(f'載入較早的訊息 · 尚有 {earlier} 則')
        self.empty_heading.setVisible(not turns)
        self.scroll.setVisible(bool(turns))
        self.column.setStretch(0, 0 if turns else 1)
        self.column.setStretch(self.column.count() - 1, 0 if turns else 1)
        draft = payload.get('draft', '')
        if not self.composer.composing and not self.has_uncommitted_draft and self.composer.toPlainText() != draft:
            cursor = self.composer.textCursor()
            # QTextCursor uses UTF-16 offsets, including emoji surrogate pairs.
            position = min(cursor.position(), len(draft.encode('utf-16-le', errors='surrogatepass')) // 2)
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
        for position, turn in enumerate(turns[earlier:]):
            identity = turn['id']
            present.add(identity)
            if identity not in self._turns:
                widget = ChatTurn()
                widget.branchRequested.connect(self._request_branch)
                widget.exportMarkdownRequested.connect(self._request_export_reply)
                self._turns[identity] = widget
                self.messages.insertWidget(position + 1, widget)
            widget = self._turns[identity]
            if self.messages.itemAt(position + 1).widget() is not widget:
                self.messages.insertWidget(position + 1, widget)
            self._turns[identity].update_turn(turn, model_names, self._tokens)
        for identity in set(self._turns) - present:
            widget = self._turns.pop(identity)
            self.messages.removeWidget(widget)
            widget.deleteLater()
        self._follow_output = follow
        self._rendering = False
        if follow:
            self._scroll_timer.start(0)
        self._visibility_timer.start(0)
        self._update_actions()

    def _update_actions(self):
        state, busy = self.service.state, self.chat_document.busy
        importing = getattr(self.chat_document, 'attachments_busy', False)
        attachments = getattr(self.chat_document, 'draft_attachments', [])
        self.attachments.sync(attachments, busy=importing or busy)
        self.add_button.setEnabled(not busy and not importing and state != 'closed')
        ready = self.service.can_submit
        self.model_button.setEnabled(state != 'closed')
        self.stop_button.setVisible(bool(busy))
        self.stop_button.setEnabled(bool(busy) and state not in {'stopping', 'unloading'})
        self.send_button.setVisible(not busy)
        self.send_button.setEnabled(ready and not busy and not importing and not self.composing and not self.has_uncommitted_draft
                                    and bool(self.composer.toPlainText().strip() or attachments))
        self.retry_action.setEnabled(bool(self.chat_document.payload.get('turns')) and ready and not busy
                                     and not importing and not self.composing and not self.has_uncommitted_draft)
        self.export_action.setEnabled(not self.composing and not self.has_uncommitted_draft)
        for turn in self._turns.values():
            turn.user_more_button.setEnabled(not self.composing and not self.has_uncommitted_draft)
            for attempt in turn.attempts.values():
                attempt.branch_button.setEnabled(not self.composing and not self.has_uncommitted_draft)
                attempt.more_button.setEnabled(not self.composing and not self.has_uncommitted_draft)
        descriptions = {
            'loading': '準備模型中…' if busy else '另一對話準備中…',
            'ready': '',
            'generating': '' if busy else '另一對話正在回覆',
            'stopping': '停止中…' if busy else '另一對話正在停止',
            'unloading': '模型處理中…',
            'closed': '引擎已關閉',
        }
        status = '加入附件中…' if importing else descriptions.get(state, '')
        self.status.setText(status)
        self.status.setToolTip(status)
        turns = self.chat_document.payload.get('turns', [])
        attempts = turns[-1].get('attempts', []) if turns else []
        notice = self.chat_document.notice
        if (attempts and attempts[-1].get('status') in {'stopped', 'limited'}
                and notice == attempts[-1].get('reason')):
            notice = ''
        error = self._ui_error or notice or self.chat_document.error or self.service.error
        self.error_label.setText(str(error or ''))
        self.error_frame.setVisible(bool(error) and str(error) != self._dismissed_error)
        self.error_retry.setVisible(bool(attempts) and attempts[-1].get('status') == 'failed')
        self.error_retry.setEnabled(self.retry_action.isEnabled())

    def _action(self, callback):
        self._ui_error = ''
        self._dismissed_error = ''
        try:
            result = callback()
        except (ValueError, OSError, RuntimeError) as exc:
            self._ui_error = str(exc)
            result = False
        self.refresh()
        return result

    def _approval_error(self, text):
        self._dismissed_error = ''
        self._ui_error = text
        self._update_actions()

    def _dismiss_error(self):
        self._dismissed_error = self.error_label.text()
        self._update_actions()

    def _open_attachments(self):
        menu = RoundMenu(self)
        menu.addAction('加入檔案…', self._choose_attachments)
        menu.addAction('PCS 文件…', self.pcsAttachmentsRequested.emit)
        menu.open_for(self.add_button)
        self._attachments_menu = menu

    def _choose_attachments(self):
        paths, _selected = QFileDialog.getOpenFileNames(self, '加入附件', '',
            '文字與圖片 (*.txt *.md *.json *.csv *.py *.js *.ts *.html *.css *.yaml *.yml *.xml *.log *.png *.jpg *.jpeg *.webp);;所有檔案 (*)')
        if paths:
            self._add_attachments(paths)

    def _add_attachments(self, items):
        self._action(lambda: self.chat_document.add_attachments(items))

    def _remove_attachment(self, identity):
        self._action(lambda: self.chat_document.remove_attachment(identity))

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
        menu.addSeparator()
        menu.addAction(self.settings_action)
        menu.open_for(self.more_button, align_right=True)
        self._actions_menu = menu

    def _request_branch(self, turn_id, attempt_id):
        if not self.composing and not self.has_uncommitted_draft:
            self.branchRequested.emit(turn_id, attempt_id)

    def _request_export_reply(self, turn_id, attempt_id):
        if not self.composing and not self.has_uncommitted_draft:
            self.exportMarkdownRequested.emit(turn_id, attempt_id)

    def _open_models(self):
        menu = ChatModelMenu(self)
        menu.open_for(self.model_button, align_right=True)
        self._models_menu = menu

    def _add_model(self):
        from .chat_settings import open_model_settings
        open_model_settings(self.workspace, self.chat_document)

    def open_settings(self):
        self._add_model()

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
        self.approval_button.apply_appearance(tokens)
        self.attachments.apply_appearance(tokens)
        self.error_icon.setPixmap(icon('alert-circle', tokens['error'], 18).pixmap(18, 18))
        self.composer.tokens = tokens
        self.send_button.setIcon(icon('arrow-up', tokens['on_accent'], 20))
        self.stop_button.setIcon(icon('stop', tokens['on_accent'], 18))
        surface = workspace_colors(settings)['content']
        title_size = font_pixels(settings.get('ui_size', 12) + 9)
        self.setStyleSheet(f'''
            QWidget#LocalChatPage {{ background:{surface}; }}
            QWidget#ChatColumn, QWidget#ChatTranscript, QScrollArea#ChatScroll,
            QTextBrowser#ChatText, QFrame#ChatTurn, QFrame#ChatAssistant,
            QFrame#ChatThinking {{ background:transparent; border:0; }}
            QFrame#ChatThinkingBody {{ background:transparent; border:0; border-left:1px solid {tokens['divider']}; }}
            QTextBrowser#ChatText[thinking="true"] {{ color:{tokens['secondary']}; }}
            QFrame#ChatUser {{ background:{tokens['surface']}; border:0; border-radius:16px; }}
            QFrame#ChatComposerFrame {{ background:{tokens['surface']}; border:1px solid {tokens['divider']}; border-radius:24px; }}
            QPlainTextEdit#ChatComposer {{ background:transparent; border:0; padding:4px; }}
            QTextBrowser#ChatText {{ padding:0; color:{tokens['text']}; }}
            QLabel#ChatEmptyTitle {{ font-size:{title_size}px; font-weight:500; color:{tokens['text']}; }}
            QPushButton#ChatModelButton {{ background:{tokens['raised']}; color:{tokens['text']}; border:0; border-radius:18px; padding:0 12px; }}
            QPushButton#ChatModelButton:hover {{ background:{tokens['hover']}; }}
            QPushButton#ChatModelButton:focus {{ border:1px solid {tokens['accent_outline']}; }}
            QPushButton#ChatSend {{ background:{tokens['accent']}; border:0; border-radius:20px; padding:0; }}
            QPushButton#ChatSend:hover {{ background:{tokens['accent_outline']}; }}
            QPushButton#ChatSend:focus {{ border:2px solid {tokens['text']}; }}
            QPushButton#ChatSend:disabled {{ background:{tokens['raised']}; }}
            QFrame#ChatErrorFrame {{background:{tokens['surface']};border:1px solid {tokens['error']};border-radius:12px;}}
            QLabel#ChatError {{ color:{tokens['text']};background:transparent;border:0; }}
        ''')
        for turn in self._turns.values():
            turn.user_attachments.apply_appearance(tokens)
            for attempt in turn.attempts.values():
                attempt.apply_appearance(tokens)
