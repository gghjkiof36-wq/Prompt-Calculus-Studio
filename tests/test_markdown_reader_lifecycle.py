"""Rich reading must remain visible and leave the workspace usable."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.workspace_shell import WorkspaceShell
from run_workspace import open_initial_documents


class RichReaderLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-reader-lifecycle-')
        self.root = Path(self.area.name)/'files'
        self.root.mkdir()
        self.data = Path(self.area.name)/'data'
        self.welcome = self.root/'歡迎使用.md'
        self.original = (
            '# 歡迎使用\n\n正文必須可見。\n\n'
            '| 功能 | 操作 |\n| --- | --- |\n| 筆記 | 編寫與閱讀 |\n\n'
            '[第二份筆記](second.md)\n'
        )
        self.welcome.write_text(self.original, encoding='utf-8')
        self.other = self.root/'second.md'
        self.other.write_text('第二份筆記保持可用。', encoding='utf-8')
        self.window = self.make_window()

    def make_window(self):
        window = WorkspaceShell(self.root, self.data)
        # Exercise Windows QWidget/HWND lifecycle without showing or taking
        # control of the user's desktop. Offscreen also runs these assertions.
        if self.app.platformName() == 'windows':
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.resize(1440, 900)
        return window

    def dispose(self):
        self.window.close()
        self.window.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def tearDown(self):
        self.dispose()
        self.area.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic()+10
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(25)
        self.assertTrue(predicate())

    def assert_reading(self, surface):
        reader = surface.reader
        self.wait(lambda: reader.rich is not None and reader.rich.render_state == 'ready')
        QTest.qWait(50)
        self.assertTrue(reader.isVisible(), 'loaded HTML cannot count as a visible reader')
        self.assertTrue(reader.rich.isVisible())
        self.assertEqual(reader.rich.geometry(), reader.rect())
        target = self.window.childAt(reader.mapTo(self.window, reader.rect().center()))
        self.assertTrue(target is reader.rich or reader.rich.isAncestorOf(target),
                        'the reading content must own its visible hit area')
        result = []
        reader.rich.page().runJavaScript(
            'JSON.stringify({body:document.querySelector("main").innerText,'
            'tables:document.querySelectorAll("main table").length})', result.append)
        self.wait(lambda: bool(result))
        rendered = json.loads(result[0])
        self.assertIn('正文必須可見', rendered['body'])
        self.assertEqual(rendered['tables'], 1)
        self.assertEqual(self.welcome.read_text(encoding='utf-8'), self.original)

    def test_first_welcome_is_visible_before_and_after_window_show(self):
        tab = open_initial_documents(self.window)
        self.window.show()
        self.assert_reading(tab.widget.md_surface)
        self.window.resize(960, 720)
        QTest.qWait(80)
        self.assert_reading(tab.widget.md_surface)

    def test_mode_switches_leave_search_editing_and_other_tabs_usable(self):
        self.window.show()
        tab = self.window.open_path(self.welcome)
        surface = tab.widget.md_surface
        for mode in ('read', 'write', 'source', 'read', 'write', 'read'):
            surface.set_mode(mode)
            QTest.qWait(40)
            if mode == 'read':
                self.assert_reading(surface)
            else:
                self.assertFalse(surface.reader.isVisible())
                self.assertFalse(surface.reader.rich.isVisible())
                self.assertTrue(surface.editor.isVisible())
                self.assertFalse(surface.editor.isReadOnly())
            QTest.mouseClick(surface.search_button, Qt.MouseButton.LeftButton)
            self.assertTrue(surface.find_bar.isVisible())
            surface.query.setText('正文')
            surface.close_panel(surface.find_bar)
            self.assertIsNone(self.app.activeModalWidget())
            self.assertIsNone(self.app.activePopupWidget())
        surface.set_mode('write')
        surface.editor.moveCursor(surface.editor.textCursor().MoveOperation.End)
        surface.editor.insertPlainText('\n新增內容')
        self.assertIn('新增內容', tab.widget.toPlainText())
        tab.widget.undo()
        self.assertEqual(tab.widget.toPlainText(), self.original)
        tab.widget.redo()
        self.assertIn('新增內容', tab.widget.toPlainText())
        tab.widget.undo()
        other = self.window.open_path(self.other)
        QTest.qWait(40)
        self.assertEqual(self.window.active_key, other.key)
        self.assertTrue(other.widget.md_surface.editor.isVisible())
        self.assertFalse(surface.reader.rich.isVisible())

    def test_saved_reading_session_can_reopen_and_switch_modes(self):
        tab = open_initial_documents(self.window)
        self.window.show()
        self.assert_reading(tab.widget.md_surface)
        self.dispose()
        self.window = self.make_window()
        self.window.show()
        tab = self.window.tabs_by_key[self.window.active_key]
        self.assertEqual(tab.path, self.welcome)
        surface = tab.widget.md_surface
        self.wait(lambda: not tab.widget.buffer.opening)
        # Layout restoration finishes on the first show/event-loop turn.
        self.assert_reading(surface)
        surface.set_mode('write')
        QTest.qWait(40)
        self.assertTrue(surface.editor.isVisible())
        surface.set_mode('read')
        self.assert_reading(surface)

    def test_split_reader_keeps_other_document_and_collapse_working(self):
        self.window.show()
        first = self.window.open_path(self.welcome)
        other = self.window.open_path(self.other, pane=self.window.ensure_split())
        first.widget.md_surface.set_mode('read')
        QTest.qWait(80)
        self.assert_reading(first.widget.md_surface)
        self.assertTrue(other.widget.md_surface.reader.isVisible())
        self.window.sidebar.hide()
        QTest.qWait(80)
        self.assert_reading(first.widget.md_surface)
        self.window.sidebar.show()
        QTest.qWait(80)
        self.assertTrue(other.widget.md_surface.reader.isVisible())
        first.widget.md_surface.set_mode('source')
        self.assertFalse(first.widget.md_surface.reader.rich.isVisible())
        self.assertEqual(other.widget.toPlainText(), '第二份筆記保持可用。')


if __name__ == '__main__':
    unittest.main()
