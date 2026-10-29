"""Review decoration stays in a disposable Qt document and exact selection."""
import unittest
from unittest.mock import patch

from PySide6.QtGui import QFont, QPalette, QTextCursor, QTextDocument

from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.markdown_assist_projection import AssistProjection
from prompt_calculus_studio.markdown_syntax import reading_html
from prompt_calculus_studio.markdown_text_assist import MarkdownTextTarget
from prompt_calculus_studio.theme import stylesheet, widget_palette
import test_workspace_panes as fixture


class MarkdownAssistProjectionTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        self.old_style, self.old_font = self.app.styleSheet(), QFont(self.app.font())
        self.old_palette = QPalette(self.app.palette())
        settings = initial_state()['settings']
        settings['reduce_motion'] = True
        self.app.setStyleSheet(stylesheet(settings))
        self.app.setPalette(widget_palette(settings))
        self.app.setFont(QFont('Microsoft JhengHei UI', 12))
        fixture.WorkspacePaneTests.setUp(self)
        self.w.resize(1260, 850)
        self.events()

    def tearDown(self):
        try:
            fixture.WorkspacePaneTests.tearDown(self)
        finally:
            self.app.setStyleSheet(self.old_style)
            self.app.setFont(self.old_font)
            self.app.setPalette(self.old_palette)

    def review(self, source, selected=None):
        raw = b'\xef\xbb\xbf' + source.replace('\n', '\r\n').encode('utf-8')
        (self.root/'Review.md').write_bytes(raw)
        tab = self.open('Review.md', retained=True)
        surface = tab.widget.md_surface
        surface.apply_mode('write')
        snapshot = tab.widget.buffer.snapshot()
        start = snapshot.text.index(selected) if selected is not None else 0
        end = start + len(selected) if selected is not None else len(snapshot.text)
        target = MarkdownTextTarget(surface, snapshot, start, end)
        view = AssistProjection(surface)
        surface.stack.addWidget(view)
        surface.stack.setCurrentWidget(view)
        self.events()
        return tab, target, view, raw

    def text_range(self, view, bounds):
        cursor = QTextCursor(view.document())
        cursor.setPosition(bounds[0])
        cursor.setPosition(bounds[1], QTextCursor.MoveMode.KeepAnchor)
        return cursor.selectedText().replace('\u2029', '\n')

    def format_at(self, view, position):
        cursor = QTextCursor(view.document())
        cursor.setPosition(position)
        cursor.setPosition(position + 1, QTextCursor.MoveMode.KeepAnchor)
        return cursor.charFormat()

    def rendered_text(self, source):
        document = QTextDocument()
        document.setHtml(reading_html(source))
        return document.toPlainText()

    def test_pending_keeps_original_outline_without_false_deletion(self):
        tab, target, view, raw = self.review('# 標題\n\n前文 原來😀內容 後文。\n', '原來😀內容')
        for pending in (None, ''):
            view.project(target, pending)
            self.events()
            self.assertEqual(view.projected_source, target.snapshot.text)
            self.assertEqual(self.text_range(view, view.original_range), '原來😀內容')
            self.assertEqual(*view.candidate_range)
            self.assertFalse(self.format_at(view, view.original_range[0]).fontStrikeOut())
            self.assertFalse(view.review_rect().isEmpty())
        self.assertEqual(tab.widget.encoded(), raw)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_midparagraph_emoji_and_markdown_ranges_do_not_style_surroundings(self):
        old = '**舊的😀粗體**\n\n- 清單一\n- 清單二'
        source = '前文🚀 保留。' + old + '後文🪴 保留。\n'
        candidate = '## 新標題\n\n新的🧭正文\n\n- 新的清單'
        tab, target, view, raw = self.review(source, old)
        view.project(target, candidate)
        self.events()
        self.assertEqual(self.text_range(view, view.original_range), self.rendered_text(old))
        self.assertEqual(self.text_range(view, view.candidate_range), self.rendered_text(candidate))
        self.assertEqual(view.start_position, view.original_range[0])
        self.assertEqual(view.end_position, view.candidate_range[1])
        self.assertTrue(self.format_at(view, view.original_range[0]).fontStrikeOut())
        new = self.format_at(view, view.candidate_range[0])
        self.assertFalse(new.fontStrikeOut())
        self.assertGreater(new.background().color().alpha(), 0)
        self.assertLess(new.background().color().alpha(), 50)
        for outside in ('前文🚀', '後文🪴'):
            cursor = view.document().find(outside)
            self.assertFalse(cursor.isNull())
            self.assertFalse(cursor.charFormat().fontStrikeOut())
            self.assertNotEqual(cursor.charFormat().background(), new.background())
        self.assertEqual(view.projected_source, source[:target.start] + candidate + source[target.end:])
        self.assertEqual(tab.widget.encoded(), raw)

    def test_identical_candidate_is_a_real_review_and_stream_replaces_old_candidate(self):
        tab, target, view, raw = self.review('前文\n\n原文😀\n\n後文', '原文😀')
        for candidate in ('原文😀', '# 完整的新候選🪴\r\n\r\n第二段', '短文'):
            view.project(target, candidate)
            self.events()
            self.assertTrue(self.format_at(view, view.original_range[0]).fontStrikeOut())
            normalized = candidate.replace('\r\n', '\n')
            self.assertEqual(self.text_range(view, view.candidate_range), self.rendered_text(normalized))
            self.assertEqual(view.projected_source, '前文\n\n' + normalized + '\n\n後文')
        self.assertNotIn('完整的新候選', view.toPlainText())
        self.assertEqual(tab.widget.encoded(), raw)

    def test_first_and_final_review_have_real_control_and_border_space(self):
        tab, target, view, raw = self.review('全文原文')
        view.project(target, '全文候選', controls_height=36)
        self.events()
        first = view.review_rect()
        self.assertGreaterEqual(first.top(), 36 + 8)
        self.assertLess(first.bottom(), view.document().size().height())
        view.reserve_controls(80)
        self.events()
        self.assertEqual(view.review_rect().top() - first.top(), 44)
        height = view.document().size().height()
        view.reserve_controls(80)
        self.assertEqual(view.document().size().height(), height)
        view.reserve_controls(0)
        self.events()
        self.assertGreaterEqual(view.review_rect().top(), 8)
        self.assertEqual(tab.widget.encoded(), raw)

    def test_suffix_has_space_after_border_without_bottom_action_margin(self):
        _, target, view, _ = self.review('# 標題\n\n原文\n\n下一段仍在框外。', '原文')
        view.project(target, '候選文字')
        self.events()
        suffix = view.document().find('下一段')
        self.assertFalse(suffix.isNull())
        self.assertGreaterEqual(view.cursorRect(suffix).top() - view.review_rect().bottom(), 8)
        last = view.document().findBlock(view.end_position - 1)
        self.assertEqual(last.blockFormat().bottomMargin(), 20)

    def test_scroll_resize_zoom_and_refresh_keep_frozen_review(self):
        source = ('前段文字\n\n' * 50) + '指定原文\n\n後段。'
        tab, target, view, raw = self.review(source, '指定原文')
        candidate = '新的中文段落需要持續換行。' * 20
        view.project(target, candidate)
        self.events()
        initial = view.review_rect()
        bar = view.verticalScrollBar()
        bar.setValue(min(200, bar.maximum()))
        self.assertEqual(view.review_rect().top(), initial.top() - bar.value())
        for width, zoom in ((900, 60), (1600, 200), (1260, 100)):
            self.w.resize(width, 850)
            view.surface.zoom_percent = zoom
            view.project(target, candidate)
            self.events()
            self.assertEqual(view.document().textWidth(), view.viewport().width())
            self.assertEqual(view.horizontalScrollBar().maximum(), 0)
            self.assertEqual(view.review_rect().width(), view.viewport().width() - 2)
        visible = view.toPlainText()
        with patch.object(tab.widget.buffer, 'snapshot', side_effect=AssertionError('must stay frozen')):
            view.refresh()
            view.refresh_cached()
        self.assertEqual(view.toPlainText(), visible)
        self.assertEqual(tab.widget.encoded(), raw)

    def test_light_and_dark_candidate_formats_are_readable_and_border_paints(self):
        _, target, view, _ = self.review('原文', '原文')
        for palette in ('graphite', 'paper'):
            self.w.settings['visual_palette'] = palette
            self.app.setStyleSheet(stylesheet(self.w.settings))
            self.app.setPalette(widget_palette(self.w.settings))
            self.w.style_shell()
            view.surface.apply_appearance()
            view.project(target, '候選')
            self.events()
            color = self.format_at(view, view.candidate_range[0]).foreground().color()
            self.assertEqual(color.lightness() < 128, palette == 'paper')
            self.assertFalse(view.viewport().grab().isNull())

    def test_adoption_uses_clean_markdown_and_preserves_one_source_undo(self):
        tab, target, view, raw = self.review('原文前\n\n**改寫這段**\n\n原文後', '**改寫這段**')
        candidate = '## 新的內容\n\n候選😀正文'
        view.project(target, candidate)
        clean = view.projected_source
        surface = view.surface
        surface.stack.setCurrentWidget(surface.editor)
        target.apply('replace', candidate)
        self.assertEqual(tab.widget.buffer.snapshot().text, clean)
        self.assertNotIn('改寫這段', clean)
        self.assertNotIn('text-decoration', tab.widget.toPlainText())
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), raw)


if __name__ == '__main__':
    unittest.main()
