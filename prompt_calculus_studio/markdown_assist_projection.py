"""Disposable in-page review; source and adoptable Markdown stay undecorated."""
from PySide6.QtCore import QRect, QRectF
from PySide6.QtGui import QColor, QPainter, QPen, QTextBlockFormat, QTextCharFormat, QTextCursor

from .markdown_syntax import reading_html
from .markdown_view import ReadingView
from .markdown_text_assist import draft_prefix
from .theme import visual_tokens, window_appearance


class AssistProjection(ReadingView):
    """Render one frozen selection and candidate, measuring real Qt positions."""
    def __init__(self, surface):
        super().__init__(surface)
        self.projected_source = ''
        self.start_position = self.end_position = 0
        self.original_range = self.candidate_range = (0, 0)
        self._controls_height = None
        self._base_top_margin = 0
        self._has_review = False
        self.setAccessibleName('文字協作預覽 · 尚未保留')
        self.setReadOnly(True)
        self.document().setUndoRedoEnabled(False)

    @staticmethod
    def _spacer_format():
        fmt = QTextBlockFormat()
        fmt.setLineHeight(1, QTextBlockFormat.LineHeightTypes.FixedHeight.value)
        return fmt

    def _insert(self, cursor, text):
        cursor.insertHtml(reading_html(text, self.surface.preferences.get('hard_breaks', False)))

    def _format_range(self, start, end, fmt):
        if start >= end:
            return
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        cursor.mergeCharFormat(fmt)

    def project(self, target, candidate, controls_height=36):
        """An empty/None candidate keeps original text, without a fake deletion."""
        source = target.snapshot.text
        drafting = getattr(target, 'kind', 'selection') == 'draft'
        valid = target.start == target.end == len(source) if drafting else 0 <= target.start < target.end <= len(source)
        if not valid:
            raise ValueError('這次文字協作的原文範圍已失效。')
        if candidate is not None and not isinstance(candidate, str):
            raise ValueError('文字協作候選必須是文字。')
        candidate = (candidate or '').replace('\r\n', '\n').replace('\r', '\n').replace('\u2029', '\n')
        before, original, after = source[:target.start], source[target.start:target.end], source[target.end:]
        self.projected_source = before + (draft_prefix(before) if drafting and candidate else '') + (candidate if candidate else original) + after
        scroll = self.verticalScrollBar().value()
        self.apply_preferences()
        document = self.document()
        enabled = document.isLayoutEnabled()
        self._has_review = False
        document.setLayoutEnabled(False)
        try:
            document.clear()
            cursor = QTextCursor(document)
            if before:
                self._insert(cursor, before)
            else:
                # Qt ignores a top margin on the first block. A one-pixel
                # preceding block makes the controls' space part of the page.
                cursor.setBlockFormat(self._spacer_format())
            cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
            self.start_position = cursor.position()
            self._insert(cursor, original)
            self.original_range = (self.start_position, cursor.position())
            if candidate:
                if not drafting:
                    cursor.insertBlock(QTextBlockFormat(), QTextCharFormat())
                start = cursor.position()
                self._insert(cursor, candidate)
                self.candidate_range = (start, cursor.position())
            else:
                self.candidate_range = (cursor.position(), cursor.position())
            self.end_position = cursor.position()
            # Preserve room for the lower border even when this is the final
            # source paragraph; no action bar is reserved below the review.
            cursor.insertBlock(self._spacer_format(), QTextCharFormat())
            if after:
                cursor.setBlockFormat(QTextBlockFormat())
                self._insert(cursor, after)
            block = document.findBlock(self.start_position)
            self._base_top_margin = block.blockFormat().topMargin()
            while block.isValid() and block.position() <= max(self.start_position, self.end_position - 1):
                fmt = block.blockFormat()
                fmt.setLeftMargin(fmt.leftMargin() + 8)
                fmt.setRightMargin(fmt.rightMargin() + 8)
                QTextCursor(block).setBlockFormat(fmt)
                block = block.next()
            last = document.findBlock(max(self.start_position, self.end_position - 1))
            fmt = last.blockFormat()
            fmt.setBottomMargin(max(20, fmt.bottomMargin()))
            QTextCursor(last).setBlockFormat(fmt)
            if candidate:
                tokens = visual_tokens(window_appearance(self))
                old = QTextCharFormat()
                gray = QColor(tokens['secondary'])
                gray.setAlpha(180)
                old.setForeground(gray)
                old.setFontStrikeOut(True)
                self._format_range(*self.original_range, old)
                new = QTextCharFormat()
                light = tokens['scheme'] == 'light'
                new.setForeground(QColor('#245caa' if light else '#9bc4ff'))
                blue = QColor('#4b8ee8')
                blue.setAlpha(28 if light else 36)
                new.setBackground(blue)
                self._format_range(*self.candidate_range, new)
            self._controls_height = None
            self._has_review = True
            self.reserve_controls(controls_height)
        finally:
            document.setLayoutEnabled(enabled)
        self.update_measure()
        self.verticalScrollBar().setValue(scroll)
        self.viewport().update()

    def reserve_controls(self, height):
        height = max(0, int(height))
        if not self._has_review or self._controls_height == height:
            return
        self._controls_height = height
        cursor = QTextCursor(self.document())
        cursor.setPosition(self.start_position)
        fmt = cursor.blockFormat()
        # Eight pixels between the pill and border, and eight inside the border.
        fmt.setTopMargin(self._base_top_margin + height + 16)
        cursor.setBlockFormat(fmt)
        self.viewport().update()

    def review_rect(self):
        """Complete review bounds in viewport coordinates, including offscreen."""
        if not self._has_review:
            return QRect()
        first, last = QTextCursor(self.document()), QTextCursor(self.document())
        first.setPosition(self.start_position)
        last.setPosition(max(self.start_position, self.end_position - 1))
        top = self.cursorRect(first).top() - 8
        bottom = self.cursorRect(last).bottom() + 8
        return QRect(1, top, max(0, self.viewport().width() - 2), max(0, bottom - top + 1))

    def paintEvent(self, event):
        super().paintEvent(event)
        rect = self.review_rect()
        if rect.isEmpty() or not rect.intersects(self.viewport().rect()):
            return
        tokens = visual_tokens(window_appearance(self))
        color = QColor(tokens['text'])
        color.setAlpha(150)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(color, 1))
        painter.setBrush(QColor(0, 0, 0, 0))
        painter.drawRoundedRect(QRectF(rect).adjusted(.5, .5, -.5, -.5), 10, 10)

    def refresh(self):
        # This view owns a frozen review, never the buffer's current projection.
        pass

    def refresh_cached(self):
        pass
