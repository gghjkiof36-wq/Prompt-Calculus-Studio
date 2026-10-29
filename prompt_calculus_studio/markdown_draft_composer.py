"""A document-local drafting entry, with no model or candidate storage of its own."""
import math

from PySide6.QtCore import QEvent, QRect, QSize, Qt, QTimer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QWidget

from .local_ai.text_assist_panel import TextAssistInput
from .theme import visual_tokens, window_appearance
from .ui_icons import icon
from .widgets import button


class MarkdownDraftComposer(QWidget):
    def __init__(self, surface):
        super().__init__(surface.content_pane)
        self.surface = surface
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self.body = QHBoxLayout(self)
        self.body.setContentsMargins(24, 8, 24, 12)
        self.frame = QFrame(self)
        self.frame.setObjectName('MarkdownDraftComposer')
        self.body.addWidget(self.frame)
        row = QHBoxLayout(self.frame)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(10)
        self.instruction = TextAssistInput(self.frame)
        self.instruction.setObjectName('MarkdownDraftInput')
        self.instruction.setPlaceholderText('描述你想起稿或續寫的內容…')
        self.instruction.setAccessibleName('參考整份文件起稿或續寫')
        self.instruction.setToolTip('參考這份文件，在文末產生草稿。Enter 送出，Shift+Enter 換行。')
        row.addWidget(self.instruction, 1)
        self.start_button = button('', self.send, 'Primary')
        self.start_button.setAccessibleName('送出起稿要求')
        self.start_button.setToolTip('參考整份文件起稿')
        self.start_button.setAutoDefault(False)
        self.start_button.setDefault(False)
        self.start_button.setIconSize(QSize(16, 16))
        row.addWidget(self.start_button, 0, Qt.AlignmentFlag.AlignBottom)
        self._fit_timer = QTimer(self)
        self._fit_timer.setSingleShot(True)
        self._fit_timer.timeout.connect(self.fit)
        self.instruction.textChanged.connect(self.queue_fit)
        self.instruction.compositionChanged.connect(self.queue_fit)
        self.instruction.document().documentLayout().documentSizeChanged.connect(self.queue_fit)
        self.instruction.submitted.connect(self.send)
        self._theme_key = None
        self.refresh()

    def send(self):
        if self.instruction.composing or not self.start_button.isEnabled():
            return None
        return self.surface.inline_assist.start_draft(self.instruction.toPlainText().strip())

    def queue_fit(self, *args):
        self.start_button.setEnabled(bool(self.instruction.toPlainText().strip()) and not self.instruction.composing)
        self._fit_timer.start()

    def refresh(self):
        s = self.surface
        host = getattr(s, 'inline_assist', None)
        visible = s.mode in ('write', 'source') and (host is None or host.panel is None)
        self.setVisible(visible)
        self.setEnabled(not s.page.buffer.opening and not s.page.buffer.composing and not s.editor.isReadOnly())
        tokens = visual_tokens(window_appearance(s))
        key = tuple(sorted(tokens.items()))
        if key != self._theme_key:
            self._theme_key = key
            self.instruction.tokens = tokens
            self.frame.setStyleSheet(f'''
                QFrame#MarkdownDraftComposer {{background:{tokens['raised']};border:1px solid {tokens['line']};border-radius:18px;}}
                QPlainTextEdit#MarkdownDraftInput {{background:transparent;border:0;padding:4px 0;color:{tokens['text']};placeholder-text-color:{tokens['secondary']};selection-background-color:{tokens['selected']};selection-color:{tokens['text']};}}
                QFrame#MarkdownDraftComposer QPushButton {{padding:0;min-width:0;min-height:0;border-radius:15px;}}
            ''')
            self.start_button.setIcon(icon('arrow-up', tokens['on_accent'], 16))
        margins = s.editor.viewportMargins()
        inset = round(s.editor.document().documentMargin())
        self.body.setContentsMargins(margins.left() + inset, 8, margins.right() + inset, 12)
        self.queue_fit()

    def fit(self):
        metrics = self.instruction.fontMetrics()
        line = max(metrics.height(), metrics.lineSpacing())
        control = max(32, line + 6)
        lines = max(1, self.instruction.document().blockCount(), math.ceil(self.instruction.document().size().height()))
        height = min(6, lines) * line + 10
        if not self.instruction.toPlainText() and self.instruction.placeholderText():
            rect = metrics.boundingRect(QRect(0, 0, max(1, self.instruction.viewport().width()), line * 6),
                                        Qt.TextFlag.TextWordWrap, self.instruction.placeholderText())
            height = max(height, min(line * 6, rect.height()) + 10)
        self.instruction.setFixedHeight(height)
        self.start_button.setFixedSize(control, control)
        self.start_button.setEnabled(bool(self.instruction.toPlainText().strip()) and not self.instruction.composing)
        self.frame.setFixedHeight(max(height, control) + 20)
        self.setFixedHeight(self.frame.height() + 20)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, '_fit_timer'):
            self.queue_fit()

    def changeEvent(self, event):
        super().changeEvent(event)
        if hasattr(self, '_fit_timer') and event.type() in (QEvent.Type.FontChange, QEvent.Type.PaletteChange):
            self.refresh()
