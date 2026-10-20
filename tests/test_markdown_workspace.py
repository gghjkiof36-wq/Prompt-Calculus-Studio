"""A foundation through workspace timers, file commands and split views."""
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QInputMethodEvent, QTextCursor
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_source import SourceEdit
from prompt_calculus_studio.workspace_shell import WorkspaceShell
import test_workspace_layout as layout_fixture
import test_workspace_panes as fixture


class MarkdownWorkspaceTests(unittest.TestCase):
    def poll_external(self):
        """Wait for the real background poll before checking its visible result."""
        self.w.autosave.poll_markdown()
        for _ in range(200):
            self.events()
            if all(getattr(getattr(getattr(tab.widget,'buffer',None),'external_refresh',None),'job',None) is None
                   for tab in self.w.all_pages()):
                return
        self.fail('External file check did not finish within the bounded wait.')
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def test_visible_editor_autosave_modes_preferences_and_restart(self):
        self.w.autosave.delay_ms=35
        left=self.open('Empty.md');page=left.widget;s=page.md_surface
        self.app.sendEvent(s.editor,QInputMethodEvent('中文',[]))
        commit=QInputMethodEvent();commit.setCommitString('中文筆記');self.app.sendEvent(s.editor,commit)
        cursor=s.editor.textCursor();cursor.select(QTextCursor.SelectionType.Document);s.editor.setTextCursor(cursor)
        s.format_selection({'bold':True,'color':'#123456','size':16})
        right=self.w.duplicate_view(left,True);right.widget.md_surface.set_mode('read')
        QTest.qWait(120);saved=left.path.read_bytes()
        self.assertIn('中文筆記'.encode(),saved);self.assertFalse(left.dirty)
        self.assertEqual(len(self.w.autosave.timers),1)
        s.set_preference('reading_size',14);s.set_mode('source')
        self.assertEqual(left.path.read_bytes(),saved)
        self.assertTrue(page.document().isUndoAvailable())
        expected=(s.editor.textCursor().position(),s.editor.textCursor().anchor())
        layout_fixture.WorkspaceLayoutTests.restart(self)
        pages=[tab.widget for tab in self.w.tabs_by_key.values() if tab.path and tab.path.name=='Empty.md']
        self.assertEqual(len(pages),2)
        self.assertEqual([p.md_surface.mode for p in pages],['source','read'])
        self.assertEqual((pages[0].textCursor().position(),pages[0].textCursor().anchor()),expected)
        self.assertEqual(pages[0].md_surface.preferences['reading_size'],14)
        self.assertEqual(pages[0].encoded(),saved);self.assertIs(pages[0].buffer,pages[1].buffer)
        self.assertIn('中文筆記',pages[1].md_surface.reader.toPlainText())

    def test_style_transaction_uses_one_autosave_and_shared_undo(self):
        self.w.autosave.delay_ms = 35
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        source = left.path.read_bytes()
        left.widget.apply_text_style(2, 7, family='Microsoft JhengHei UI', size=14, color='#112233')
        QTest.qWait(110)
        self.assertFalse(left.dirty)
        styled = left.path.read_bytes()
        self.assertIn(b'<span', styled)
        self.assertEqual(len(self.w.autosave.timers), 1)
        right.widget.undo()
        QTest.qWait(110)
        self.assertEqual(left.path.read_bytes(), source)
        self.assertTrue(left.widget.document().isRedoAvailable())
        left.widget.redo()
        QTest.qWait(110)
        self.assertEqual(left.path.read_bytes(), styled)

    def test_preedit_pauses_autosave_file_commands_and_close_until_commit(self):
        self.w.autosave.delay_ms = 35
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        original = left.path.read_bytes()
        left.widget.moveCursor(QTextCursor.MoveOperation.End)
        left.widget.insertPlainText('before ')
        self.app.sendEvent(left.widget, QInputMethodEvent('中文', []))
        QTest.qWait(110)
        self.w.autosave.flush(right)
        self.assertEqual(left.path.read_bytes(), original)
        self.assertFalse(self.w.prepare_close(right))
        context = self.w.commands.context('tab', tab=right)
        with self.assertRaises(ValueError):
            self.w.commands.files.begin(context, rescue=True)
        commit = QInputMethodEvent()
        commit.setCommitString('中文')
        self.app.sendEvent(left.widget, commit)
        QTest.qWait(110)
        self.assertTrue(left.path.read_bytes().endswith('before 中文'.encode()))
        self.assertFalse(left.dirty)

    def test_external_clean_reload_then_conflict_rescued_by_save_as(self):
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        original_path = left.path
        left.path.write_bytes(b'# external\n')
        self.poll_external()
        self.assertEqual(right.widget.toPlainText(), '# external\n')
        left.widget.insertPlainText('local ')
        left.path.write_bytes(b'# external second\n')
        self.poll_external()
        self.assertTrue(left.dirty)
        self.assertIn('外部修改', right.widget.error)
        context = self.w.commands.context('tab', tab=right)
        target = self.w.commands.files.run('save_as', context, name='Rescued', directory=self.root)
        self.assertIsNotNone(target)
        self.assertEqual(left.path, right.path)
        self.assertEqual(original_path.read_bytes(), b'# external second\n')
        self.assertIn('local ', target.read_text(encoding='utf-8-sig'))
        right.widget.undo()
        self.w.autosave.flush(left)
        self.assertEqual(original_path.read_bytes(), b'# external second\n')
        self.assertNotIn('local ', target.read_text(encoding='utf-8-sig'))

    def test_transient_external_replace_recovers_autosave_without_manual_save(self):
        self.w.autosave.delay_ms = 35
        tab = self.open('Notes.md')
        original = tab.path.read_bytes()
        self.poll_external()
        parked = tab.path.with_suffix('.parked')
        tab.path.rename(parked)
        self.poll_external()
        self.assertTrue(tab.widget.error)
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        tab.widget.insertPlainText('pending')
        QTest.qWait(80)
        self.assertFalse(tab.path.exists())
        parked.rename(tab.path)  # Same identity, mtime and bytes as last success.
        self.poll_external()
        self.assertEqual(tab.widget.error, '')
        QTest.qWait(110)
        self.assertEqual(tab.path.read_bytes(), original+b'pending')
        self.assertFalse(tab.dirty)

    def test_failed_write_undo_then_clean_explicit_retry_restores_autosave(self):
        self.w.autosave.delay_ms = 35
        tab = self.open('Notes.md')
        original = tab.path.read_bytes()
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            tab.widget.insertPlainText('failed')
            QTest.qWait(110)
        self.assertTrue(tab.widget.error)
        tab.widget.undo()
        self.assertFalse(tab.dirty)
        self.assertTrue(self.w.save_tab(tab))
        self.assertFalse(tab.widget.error)
        self.assertEqual(tab.path.read_bytes(), original)
        tab.widget.insertPlainText('next')
        QTest.qWait(110)
        self.assertEqual(tab.path.read_bytes(), original+b'next')
        self.assertFalse(tab.dirty)


if __name__ == '__main__':
    unittest.main()
