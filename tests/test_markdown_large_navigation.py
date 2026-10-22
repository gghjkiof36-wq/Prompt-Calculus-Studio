"""Large-file navigation survives the later application of paragraph heights."""
import time
import unittest

from PySide6.QtCore import Qt, QThreadPool
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest

import test_workspace_panes as fixture


class LargeMarkdownNavigationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def tearDown(self):
        self.w.notebook.stop()
        self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
        fixture.WorkspacePaneTests.tearDown(self)

    def large(self):
        # More than 10,000 actual paragraphs: a small-file test misses the
        # delayed height changes, even if its total byte count is large.
        text = ('# Opening section\r\n' +
                ('Short paragraph. ' + '0123456789 ' * 4 + '\r\n\r\n') * 5100 +
                '# Final section\r\nTAIL_NEEDLE\r\n')
        raw = b'\xef\xbb\xbf' + text.encode('utf-8')
        path = self.root / 'Notes.md'
        path.write_bytes(raw)
        surface = self.open('Notes.md').widget.md_surface
        self.assertGreater(surface.editor.document().blockCount(), 10000)
        self.assertTrue(surface.editor.lazy_paragraphs)
        return surface, raw

    def settle(self):
        # Let the normal 0-ms presentation timer, layout, and posted paints
        # finish. Do not call present_visible/ensureCursorVisible as a repair.
        for _ in range(3):
            self.events()

    def wait(self, predicate, message):
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(10)
        self.assertTrue(predicate(), message)

    def assert_visible(self, editor):
        self.assertTrue(editor.textCursor().block().isVisible())
        self.assertTrue(editor.viewport().rect().intersects(editor.cursorRect()),
                        (editor.textCursor().blockNumber(), editor.cursorRect(),
                         editor.viewport().rect(), editor.verticalScrollBar().value()))

    def find_tail(self, surface):
        surface.open_find()
        surface.query.setText('TAIL_NEEDLE')
        self.wait(lambda: not surface.search_highlight.searching, 'source search did not finish')
        surface.find_next()
        self.settle()
        self.assertEqual(surface.editor.textCursor().selectedText(), 'TAIL_NEEDLE')
        self.assert_visible(surface.editor)

    def test_tail_search_and_direction_keys_keep_cursor_visible_after_lazy_layout(self):
        surface, raw = self.large()
        surface.set_mode('source')
        self.find_tail(surface)
        surface.query.clear()
        surface.close_panel(surface.find_bar)
        editor = surface.editor
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        self.settle()
        self.assertEqual(editor.textCursor().position(), editor.document().characterCount() - 1)
        self.assert_visible(editor)
        for key in (Qt.Key.Key_Up,) * 12 + (Qt.Key.Key_Down,) * 12:
            QTest.keyClick(editor, key)
            self.settle()
            self.assert_visible(editor)
        self.assertEqual(surface.page.encoded(), raw)
        self.assertEqual(surface.page.path.read_bytes(), raw)
        self.assertFalse(surface.page.dirty)
        self.assertFalse(surface.page.document().isUndoAvailable())

    def test_return_from_full_reading_to_source_end_preserves_bytes_and_undo(self):
        surface, raw = self.large()
        surface.set_mode('read')
        self.wait(lambda: surface.reader.rich is not None and
                  surface.reader.rich.render_state == 'ready', 'full reading did not become ready')
        surface.set_mode('source')
        editor = surface.editor
        editor.setFocus()
        QTest.keyClick(editor, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
        self.settle()
        self.assertEqual(editor.textCursor().position(), editor.document().characterCount() - 1)
        self.assert_visible(editor)
        QTest.keyClicks(editor, 'changed')
        self.settle()
        self.assertEqual(surface.page.encoded(), raw + b'changed')
        QTest.keyClick(editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.settle()
        self.assertEqual(surface.page.encoded(), raw)
        QTest.keyClick(editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.settle()
        self.assertEqual(surface.page.encoded(), raw + b'changed')
        self.assertEqual(surface.page.path.read_bytes(), raw)
        self.assert_visible(editor)

    def test_find_unfolds_a_real_collapsed_section_without_losing_tail_navigation(self):
        surface, raw = self.large()
        editor = surface.editor
        first = editor.document().begin()
        point = editor.fold_control_rect(first).center().toPoint()
        QTest.mouseClick(editor.gutter, Qt.MouseButton.LeftButton, pos=point)
        self.settle()
        self.assertIn(0, editor.folded)
        self.assertFalse(editor.document().findBlockByNumber(1).isVisible())
        self.find_tail(surface)
        self.assertEqual(editor.folded, set())
        self.assertTrue(editor.document().findBlockByNumber(1).isVisible())
        self.assertEqual(surface.page.encoded(), raw)
        self.assertEqual(surface.page.path.read_bytes(), raw)
        self.assertFalse(surface.page.document().isUndoAvailable())

    def test_manual_scroll_away_from_cursor_does_not_jump_back_after_presentation(self):
        surface, raw = self.large()
        surface.set_mode('source')
        editor = surface.editor
        editor.moveCursor(QTextCursor.MoveOperation.Start)
        self.settle()
        self.assertEqual(editor.textCursor().position(), 0)
        # A scrollbar operation deliberately leaves the caret at the start.
        # Later formatting must preserve the user's tail viewport instead of
        # treating every presentation pass as a request to reveal the caret.
        editor.verticalScrollBar().setValue(editor.verticalScrollBar().maximum())
        self.settle()
        self.assertEqual(editor.textCursor().position(), 0)
        self.assertGreater(editor.firstVisibleBlock().blockNumber(), 10000)
        self.assertFalse(editor.viewport().rect().intersects(editor.cursorRect()))
        at = editor.firstVisibleBlock().blockNumber()
        self.settle()
        self.assertEqual(editor.firstVisibleBlock().blockNumber(), at)
        self.assertEqual(surface.page.encoded(), raw)
        self.assertFalse(surface.page.dirty)


if __name__ == '__main__':
    unittest.main()
