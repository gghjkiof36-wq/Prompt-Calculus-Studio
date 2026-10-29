"""Native styled reading columns reflow to their viewport, including previews."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QPalette
from PySide6.QtWidgets import QTextEdit

from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.markdown_text_assist import open_text_assist
from prompt_calculus_studio.theme import stylesheet, widget_palette
from test_markdown_text_assist import FakeService
import test_workspace_panes as fixture


PROSE = '雨停之後，街道慢慢恢復聲音。' * 10
SOURCE = '# 團隊筆記\n\n' + PROSE + '\n\n- ' + PROSE + '\n\n> ' + PROSE + '\n'


class MarkdownReadingWrapTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        self.previous_style = self.app.styleSheet()
        self.previous_font = QFont(self.app.font())
        self.previous_palette = QPalette(self.app.palette())
        settings = initial_state()['settings']
        settings['reduce_motion'] = True
        self.app.setPalette(widget_palette(settings))
        self.app.setStyleSheet(stylesheet(settings))
        self.app.setFont(QFont('Microsoft JhengHei UI', 12))
        fixture.WorkspacePaneTests.setUp(self)
        self.w.resize(1260, 850)
        self.events()

    def tearDown(self):
        try:
            fixture.WorkspacePaneTests.tearDown(self)
        finally:
            self.app.setStyleSheet(self.previous_style)
            self.app.setPalette(self.previous_palette)
            self.app.setFont(self.previous_font)

    def page(self, source=SOURCE):
        raw = b'\xef\xbb\xbf' + source.replace('\n', '\r\n').encode('utf-8')
        (self.root/'Reading.md').write_bytes(raw)
        tab = self.open('Reading.md', retained=True)
        return tab, raw

    def assert_wrapped(self, view):
        self.events()
        self.assertEqual(view.lineWrapMode(), QTextEdit.LineWrapMode.WidgetWidth)
        self.assertEqual(view.horizontalScrollBarPolicy(), Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.assertEqual(view.document().textWidth(), view.viewport().width())
        self.assertLessEqual(view.document().size().width(), view.viewport().width())
        self.assertEqual(view.horizontalScrollBar().maximum(), 0)
        blocks = []
        block = view.document().begin()
        while block.isValid():
            if len(block.text()) > 100:
                self.assertFalse(block.blockFormat().nonBreakableLines())
                self.assertGreater(block.layout().lineCount(), 1)
                blocks.append(block.layout().lineCount())
            block = block.next()
        self.assertTrue(blocks)
        return blocks

    def test_reading_column_reflows_initial_content_and_resizes(self):
        tab, raw = self.page()
        surface = tab.widget.md_surface
        surface.apply_mode('read')
        self.assertFalse(surface.reader.advanced)
        self.assert_wrapped(surface.reader)
        for width in (900, 1800, 1000, 1260):
            with self.subTest(width=width):
                self.w.resize(width, 850)
                self.assert_wrapped(surface.reader)
        self.assertEqual(tab.widget.encoded(), raw)
        self.assertFalse(tab.dirty)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_source_no_wrap_does_not_leak_into_reader_after_edits(self):
        tab, _ = self.page('# 開始\n\n原文。\n')
        surface = tab.widget.md_surface
        surface.apply_mode('source')
        surface.preferences['wrap'] = False
        surface.apply_preferences()
        surface.editor.selectAll()
        surface.editor.insertPlainText(SOURCE)
        before = tab.widget.buffer.snapshot()
        encoded = tab.widget.encoded()
        for mode in ('read', 'write', 'read', 'source', 'read'):
            surface.apply_mode(mode)
            if mode == 'read':
                self.assert_wrapped(surface.reader)
        self.assertEqual(tab.widget.buffer.snapshot(), before)
        self.assertEqual(tab.widget.encoded(), encoded)
        surface.apply_mode('source')
        tab.widget.undo()
        self.assertEqual(tab.widget.buffer.snapshot().text, '# 開始\n\n原文。\n')

    def test_column_preferences_and_zoom_reflow_real_lines(self):
        tab, raw = self.page()
        surface = tab.widget.md_surface
        surface.apply_mode('read')
        surface.preferences['reading_width'] = 1200
        surface.apply_preferences()
        wide = self.assert_wrapped(surface.reader)[0]
        surface.preferences['reading_width'] = 480
        surface.apply_preferences()
        narrow = self.assert_wrapped(surface.reader)[0]
        self.assertGreater(narrow, wide)
        surface.set_zoom(60)
        small = self.assert_wrapped(surface.reader)[0]
        surface.set_zoom(200)
        large = self.assert_wrapped(surface.reader)[0]
        self.assertGreater(large, small)
        surface.set_zoom(100)
        surface.preferences.update(reading_width=880, reading_size=18)
        surface.apply_preferences()
        self.assert_wrapped(surface.reader)
        self.assertEqual(tab.widget.encoded(), raw)

    def test_completed_inline_projection_uses_same_column_without_source_edits(self):
        tab, raw = self.page('# 團隊筆記\n\n待改寫的段落。\n\n後面的原文。\n')
        surface = tab.widget.md_surface
        surface.apply_mode('write')
        snapshot = tab.widget.buffer.snapshot()
        start = snapshot.text.index('待改寫')
        end = snapshot.text.index('\n', start)
        service = FakeService()
        with patch('prompt_calculus_studio.local_ai.service.for_workspace', return_value=service):
            panel = open_text_assist(surface, snapshot, start, end)
            self.assertIsNotNone(panel)
            self.assertTrue(panel.controller.start('改寫這段文字'))
            service.complete(PROSE + '\n\n- ' + PROSE)
            self.events()
            projection = surface.inline_assist.projection
            self.assertIsNotNone(projection)
            self.assert_wrapped(projection)
            for width in (900, 1600, 1260):
                self.w.resize(width, 850)
                self.assert_wrapped(projection)
            self.assertEqual(tab.widget.buffer.snapshot(), snapshot)
            self.assertEqual(tab.widget.encoded(), raw)
            self.assertFalse(tab.widget.document().isUndoAvailable())
            surface.inline_assist.dismiss()


if __name__ == '__main__':
    unittest.main()
