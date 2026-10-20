"""Observe the first painted document, not only the final callback state."""
import json
import unittest
from PySide6.QtCore import QObject, QEvent, QPoint
from PySide6.QtTest import QTest
import test_workspace_panes as fixture


class Frames(QObject):
    def __init__(self, surface):
        super().__init__(surface)
        self.surface = surface
        self.frames = []
        surface.editor.viewport().installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Paint:
            s = self.surface
            body = s.editor.viewport().mapTo(s, QPoint()).x() + round(s.editor.document().documentMargin())
            title = s.page.file_title.mapTo(s, QPoint()).x()
            self.frames.append((body, title, s.editor.font().pointSizeF(), s.editor.viewport().width()))
        return False


class MarkdownFirstFrameTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events

    def test_first_frame_uses_saved_font_and_aligned_column(self):
        self.w.resize(1600, 900)
        self.events()
        (self.w.data/'md-preferences.json').write_text(
            json.dumps(dict(reading_width=680, reading_size=16)), encoding='utf-8')
        path = self.root/'First.md'
        source = '# First\n\n' + ('A paragraph with content.\n\n' * 12)
        path.write_text(source, encoding='utf-8')
        tab = self.w.open_path(path, True)
        surface = tab.widget.md_surface
        frames = Frames(surface)
        QTest.qWait(200)
        self.assertTrue(frames.frames)
        for body, title, size, width in frames.frames:
            self.assertEqual(size, 16)
            self.assertLessEqual(abs(body-title), 1, frames.frames)
        self.assertEqual(len({f[:2] for f in frames.frames}), 1, frames.frames)
        self.assertEqual(tab.widget.toPlainText(), source)
        self.assertFalse(tab.widget.dirty)
        self.assertFalse(tab.widget.document().isUndoAvailable())

    def test_source_and_write_share_centered_column_after_resize(self):
        self.w.resize(1680, 900)
        tab = self.w.open_path(self.root/'Notes.md', True)
        self.events()
        s = tab.widget.md_surface
        before = tab.widget.encoded()
        for width, numbers, wrap in ((1680, False, True), (1450, True, False), (980, False, True)):
            self.w.resize(width, 900)
            s.preferences.update(line_numbers=numbers, wrap=wrap)
            s.apply_preferences()
            self.events()
            columns = []
            for mode in ('write', 'source'):
                s.set_mode(mode)
                self.events()
                margin = max(24, (s.editor.width()-s.preferences['reading_width'])//2)
                self.assertEqual(s.editor.viewportMargins().right(), margin)
                columns.append(s.editor.viewportMargins().left())
                actual = tab.widget.file_title.mapTo(s, QPoint()).x()
                body = s.editor.viewport().mapTo(s, QPoint()).x()+round(s.editor.document().documentMargin())
                self.assertLessEqual(abs(actual-body), 1)
            self.assertEqual(*columns)
        self.assertEqual(tab.widget.encoded(), before)
        self.assertFalse(tab.widget.dirty)


if __name__ == '__main__':
    unittest.main()
