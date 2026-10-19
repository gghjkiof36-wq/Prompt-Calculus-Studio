"""Directory refusal preserves the reachable editor save/recovery workflow."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QSaveFile
from PySide6.QtTest import QTest
import test_workspace_panes as fixture


@unittest.skipUnless(os.name == 'nt', 'Windows directory publication boundary')
class PublicationBoundaryWorkspaceTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def test_directory_race_keeps_shared_draft_undo_and_allows_explicit_save_reopen(self):
        left = self.open('Notes.md', retained=True)
        right = self.w.duplicate_view(left, True)
        before = left.path.read_bytes()
        left.widget.insertPlainText('合成測試草稿\n')
        candidate = left.widget.encoded()
        parent = self.root
        class Writer(QSaveFile):
            def commit(writer):
                ok = super().commit()
                if ok:parent.rename(parent.with_name(parent.name + '-parked'))
                return ok
        with patch('prompt_calculus_studio.workspace_files.QSaveFile', Writer):
            self.assertFalse(self.w.save_tab(right))
        self.assertTrue(left.dirty and right.dirty)
        self.assertEqual(left.path.read_bytes(), before)
        self.assertEqual(right.widget.encoded(), candidate)
        self.assertTrue(left.widget.document().isUndoAvailable())
        receipt = left.widget.last_result
        self.assertFalse(receipt.uncertain)  # Directory rename failed before destination publication.
        copies = [Path(p) for p in receipt.recovery_paths]
        self.assertIn(candidate, [p.read_bytes() for p in copies])
        with patch.object(left.widget, 'save', wraps=left.widget.save) as saves:
            self.w.autosave.flush(left)
            self.w.autosave.schedule(right)
            QTest.qWait(1100)
            self.assertEqual(saves.call_count, 0)
        # Explicit retry is safe once the external operation has stopped.
        self.assertTrue(self.w.save_tab(right), self.errors)
        self.assertEqual(left.path.read_bytes(), candidate)
        self.assertFalse(left.dirty or right.dirty)
        self.assertTrue(all(p.exists() for p in copies))
        left.widget.undo()
        self.assertEqual(right.widget.encoded(), before)
        right.widget.redo()
        self.assertEqual(left.widget.encoded(), candidate)
        self.assertTrue(self.w.save_tab(left))
        for tab in (right, left):
            pane = self.w.pane_for(tab)
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='discard',
                                            make_home=False, pane=pane))
        reopened = self.open('Notes.md', retained=True)
        self.assertEqual(reopened.widget.encoded(), candidate)
