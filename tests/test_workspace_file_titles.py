"""Real rename gestures, validation geometry and the first management paint."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QEvent, QObject
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.workspace_file_title import title_for
from prompt_calculus_studio.workspace_locations import path_key
import test_workspace_shell as fixture


class FirstPaint(QObject):
    def __init__(self):
        super().__init__()
        self.frames = []
    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.Paint:
            self.frames.append(obj.geometry())
        return False


class FileTitleTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    events = fixture.WorkspaceShellTests.events

    def tearDown(self):
        if self.w.commands.files.inline is not None:
            self.w.commands.files.inline.cancel()
        fixture.WorkspaceShellTests.tearDown(self)

    def test_heading_click_rename_cancels_conflicts_preserves_dirty_text_and_other_view(self):
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        for filename in ('Scene.pcs-category', 'Notes.md'):
            with self.subTest(filename=filename):
                tab = self.w.open_path(self.root/filename, new_tab=True)
                other = self.w.open_path(tab.path, new_tab=True)
                self.events()
                if filename.endswith('.md'):
                    other.widget.insertPlainText('kept draft\n')
                    draft = other.widget.toPlainText()
                title = title_for(other)
                self.assertEqual(title.text(), other.path.stem)
                QTest.mouseClick(title, Qt.MouseButton.LeftButton)
                self.assertIsNone(QApplication.activeModalWidget())
                self.events()
                self.assertGreaterEqual(title.name.height(),title.name.sizeHint().height())
                self.assertGreaterEqual(title.name.width(),160)
                title.name.clear()
                QTest.keyClick(title.name, Qt.Key.Key_Return)
                self.assertFalse(title.done)
                self.assertTrue(tab.path.exists())
                QTest.keyClick(title.name, Qt.Key.Key_Escape)
                self.assertIsNone(self.w.commands.files.inline)
                QTest.mouseClick(title, Qt.MouseButton.LeftButton)
                name = '改名後的管理頁' if tab.document else '改名後的筆記'
                title.name.setText(name)
                QTest.keyClick(title.name, Qt.Key.Key_Return)
                self.events()
                self.assertEqual(tab.path, other.path)
                self.assertEqual(title_for(tab).text(), name)
                self.assertFalse(self.w.sidebar.isVisible())
                if not tab.document:
                    self.assertEqual(tab.widget.toPlainText(), draft)
                    self.assertTrue(tab.dirty)
                    self.assertTrue(self.w.save_tab(tab), (self.w.autosave.paused,
                        self.w.autosave.saving, tab.widget.error, tab.widget.operation_error))
                    self.assertIn(b'kept draft', tab.path.read_bytes())

    def test_empty_sidebar_name_wraps_without_squeezing_editor(self):
        self.w.sidebar_preference = True
        self.w.sidebar_width = 220
        self.w.sync_sidebar()
        path = self.root/'Street.pcs-workflow'
        self.w.select_path(path)
        self.w.commands.files.run('rename', self.w.commands.context('tree', self.w.tree.currentItem()))
        editor = self.w.commands.files.inline
        for size in (12, 18):
            font = editor.font(); font.setPointSize(size); editor.setFont(font)
            editor.name.clear()
            QTest.keyClick(editor.name, Qt.Key.Key_Return)
            self.events()
            self.assertGreaterEqual(editor.name.height(), editor.name.sizeHint().height())
            self.assertGreaterEqual(editor.error.height(), editor.error.heightForWidth(editor.error.width()))
            self.assertLessEqual(editor.error.geometry().bottom(), editor.height())
        editor.cancel()
        self.assertTrue(path.exists())

    def test_management_first_paint_matches_final_geometry_after_all_tabs_closed(self):
        from prompt_calculus_studio.document_host import DocumentBrowser
        watches = []
        original = DocumentBrowser.__init__
        def build(browser, *args, **kwargs):
            original(browser, *args, **kwargs)
            watch = FirstPaint(); browser.browser.installEventFilter(watch)
            watches.append((browser, watch))
        with patch.object(DocumentBrowser, '__init__', build):
            for _ in range(2):
                self.w.open_path(self.root/'Scene.pcs-category')
                self.events()
                browser, watch = watches[-1]
                self.assertTrue(watch.frames)
                self.assertEqual(watch.frames[0], browser.browser.geometry())
                while self.w.tab_strip.bar.count():
                    self.w.close_tab(0, decision='discard', make_home=False)
                self.w.new_tab()
                self.events()
