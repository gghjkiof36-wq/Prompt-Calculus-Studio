"""Close only after native composition and durable draft preservation finish."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtGui import QInputMethodEvent, QTextCursor

from prompt_calculus_studio.local_documents import io
import test_workspace_shell as fixture


class CloseProtectionTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    open = fixture.WorkspaceShellTests.open
    events = fixture.WorkspaceShellTests.events

    def commit_text(self, page, text):
        event = QInputMethodEvent()
        event.setCommitString(text)
        self.app.sendEvent(page, event)

    def test_uncommitted_preedit_keeps_clean_shared_views_open(self):
        left = self.open('Notes', 'md', True)
        right = self.w.duplicate_view(left, True)
        original = left.path.read_bytes()
        self.app.sendEvent(left.widget, QInputMethodEvent('中文', []))
        self.assertTrue(left.widget.buffer.composing)
        self.assertFalse(left.dirty)
        notices = []
        # A request to the active input context does not guarantee that a
        # different shared view's native composition has completed.
        with patch('prompt_calculus_studio.workspace_shell.QApplication.inputMethod',
                   return_value=SimpleNamespace(commit=lambda: None)), \
             patch.object(self.w, 'show_notice', notices.append), \
             patch.object(self.w.close_recovery, 'preserve', wraps=self.w.close_recovery.preserve) as preserve:
            self.assertFalse(self.w.close())
            preserve.assert_not_called()
        self.assertTrue(self.w.isVisible())
        self.assertIn(left.key, self.w.tabs_by_key)
        self.assertIn(right.key, self.w.tabs_by_key)
        self.assertIs(left.widget.buffer, right.widget.buffer)
        self.assertTrue(left.widget.buffer.composing)
        self.assertEqual(left.path.read_bytes(), original)
        self.assertTrue(any('組字' in notice for notice in notices))
        self.commit_text(left.widget, '中文')
        self.assertFalse(left.widget.buffer.composing)

    def test_successful_close_commit_preserves_shared_undo_save_and_reopen(self):
        left = self.open('Notes', 'md', True)
        right = self.w.duplicate_view(left, True)
        left.widget.moveCursor(QTextCursor.MoveOperation.End)
        self.app.sendEvent(left.widget, QInputMethodEvent('中文', []))
        with patch('prompt_calculus_studio.workspace_shell.QApplication.inputMethod',
                   return_value=SimpleNamespace(commit=lambda: self.commit_text(left.widget, '中文'))):
            self.assertTrue(self.w.close_tab(self.w.index_of(right.key), pane=self.w.pane_for(right)))
        expected = left.widget.encoded()
        self.assertTrue(expected.endswith('中文'.encode()))
        self.assertEqual(left.path.read_bytes(), expected)
        left.widget.undo()
        self.assertFalse(left.widget.toPlainText().endswith('中文'))
        left.widget.redo()
        self.assertTrue(left.widget.toPlainText().endswith('中文'))
        self.assertTrue(self.w.save_tab(left))
        self.assertTrue(self.w.close_tab(self.w.index_of(left.key), pane=self.w.pane_for(left)))
        reopened = self.open('Notes', 'md', True)
        self.assertEqual(reopened.widget.encoded(), expected)
        self.assertFalse(reopened.dirty)

    def test_failed_markdown_save_and_recovery_keeps_window_and_native_history(self):
        tab = self.open('Notes', 'md', True)
        original = tab.path.read_bytes()
        tab.widget.insertPlainText('keep this draft ')
        expected = tab.widget.encoded()
        native = tab.widget.document()
        notices = []
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False), \
             patch.object(self.w.close_recovery.cache, 'write', side_effect=OSError('all recovery targets failed')), \
             patch.object(self.w, 'show_notice', notices.append):
            self.assertFalse(self.w.close())
        self.assertTrue(self.w.isVisible())
        self.assertIn(tab.key, self.w.tabs_by_key)
        self.assertIs(tab.widget.document(), native)
        self.assertTrue(tab.dirty)
        self.assertEqual(tab.path.read_bytes(), original)
        self.assertEqual(tab.widget.encoded(), expected)
        self.assertIn('all recovery targets failed', notices)
        tab.widget.undo()
        self.assertEqual(tab.widget.encoded(), original)
        tab.widget.redo()
        self.assertEqual(tab.widget.encoded(), expected)
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        reopened = self.open('Notes', 'md', True)
        self.assertEqual(reopened.widget.encoded(), expected)
        self.assertEqual(tab.path.read_bytes(), expected)

    def test_unflushed_document_closes_only_after_durable_recovery(self):
        tab = self.open(retained=True)
        original = tab.path.read_bytes()
        self.assertTrue(tab.host.document_operations.commit(
            lambda state: state['items'][0].update(prompt='recover committed document edit')))
        with patch.object(tab.document, 'flush_views', return_value=False), \
             patch.object(self.w.close_recovery.cache, 'write', side_effect=OSError('all recovery targets failed')):
            self.assertFalse(self.w.close())
        self.assertTrue(self.w.isVisible())
        self.assertIn(tab.key, self.w.tabs_by_key)
        self.assertEqual(tab.path.read_bytes(), original)
        self.assertEqual(tab.document.state['items'][0]['prompt'], 'recover committed document edit')
        with patch.object(tab.document, 'flush_views', return_value=False):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertEqual(tab.path.read_bytes(), original)
        reopened = self.open(retained=True)
        self.assertEqual(reopened.document.state['items'][0]['prompt'], 'recover committed document edit')
        self.assertTrue(self.w.save_tab(reopened))
        saved = io.load(self.root, reopened.path.name).document
        self.assertEqual(saved['payload']['items'][0]['prompt'], 'recover committed document edit')


if __name__ == '__main__':
    unittest.main()
