"""First-run Markdown welcome respects explicit launches and saved sessions."""
from pathlib import Path
import tempfile
import unittest
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from run_workspace import open_initial_documents
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class WorkspaceWelcomeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-welcome-')
        self.root = Path(self.area.name)/'files'
        self.root.mkdir()
        self.data = Path(self.area.name)/'data'
        self.welcome = self.root/'歡迎使用.md'
        self.welcome.write_text('# 歡迎使用 PCS\n\n在這裡開始練習。', encoding='utf-8')
        self.note = self.root/'自己的筆記.md'
        self.note.write_text('保留這份筆記', encoding='utf-8')
        self.window = WorkspaceShell(self.root, self.data)
        self.window.show()
        QTest.qWait(30)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()
        self.area.cleanup()

    def test_first_run_opens_ordinary_editable_markdown_in_reading_mode(self):
        original = self.welcome.read_bytes()
        tab = open_initial_documents(self.window)
        QTest.qWait(30)
        self.assertEqual(tab.path, self.welcome)
        self.assertEqual(self.window.active_key, tab.key)
        self.assertEqual(tab.widget.md_surface.mode, 'read')
        self.assertIn('在這裡開始練習', tab.widget.toPlainText())
        tab.widget.md_surface.set_mode('write')
        self.assertFalse(tab.widget.isReadOnly())
        self.assertEqual(self.welcome.read_bytes(), original)

    def test_explicit_file_launch_does_not_insert_the_welcome(self):
        tab = open_initial_documents(self.window, left_path=self.note)
        self.assertEqual(tab.path, self.note)
        self.assertFalse(any(t.path == self.welcome for t in self.window.tabs_by_key.values()))

    def test_saved_session_does_not_reopen_a_closed_welcome(self):
        self.window.open_path(self.note)
        self.assertTrue(self.window.close())
        self.window.deleteLater()
        self.app.processEvents()
        self.window = WorkspaceShell(self.root, self.data)
        self.window.show()
        QTest.qWait(30)
        self.assertTrue(self.window.layout_restored)
        self.assertIsNone(open_initial_documents(self.window))
        self.assertEqual(self.window.tabs_by_key[self.window.active_key].path, self.note)
        self.assertFalse(any(t.path == self.welcome for t in self.window.tabs_by_key.values()))

    def test_existing_content_without_welcome_stays_unchanged(self):
        self.welcome.unlink()
        before = {p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}
        self.assertIsNone(open_initial_documents(self.window))
        self.assertEqual({p.name: p.read_bytes() for p in self.root.iterdir() if p.is_file()}, before)
