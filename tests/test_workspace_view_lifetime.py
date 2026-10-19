"""089-003: normal tab close must disconnect every shared-document receiver."""
import sys
import traceback
import unittest
from unittest.mock import patch

from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from shiboken6 import isValid
import test_workspace_panes as fixture


class WorkspaceViewLifetimeTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def check_close(self, close_left):
        raw = b'# Normal editing\n\nShared text.\n'
        path = self.root/'Notes.md';path.write_bytes(raw)
        self.w.autosave.delay_ms = 80
        errors = []
        def capture(kind, error, tb):errors.append(''.join(traceback.format_exception(kind,error,tb)))
        with patch.object(sys, 'excepthook', capture):
            left = self.open('Notes.md', retained=True)
            right = self.w.duplicate_view(left, True)
            QTest.qWait(120)
            closed, survivor = (left,right) if close_left else (right,left)
            closed_page = closed.widget
            observer = closed_page._workspace_observer
            closed_surface = closed_page.md_surface
            self.assertTrue(self.w.close_tab(self.w.index_of(closed.key), pane=self.w.pane_for(closed)))
            QTest.qWait(120)  # Real event loop deletion; no forced DeferredDelete.
            self.assertFalse(isValid(closed_page))
            self.assertFalse(isValid(observer))
            self.assertFalse(isValid(closed_surface))
            self.w.update_tab(closed)  # A stale queued notification is harmless.
            live = survivor.widget.md_surface.editor
            live.moveCursor(QTextCursor.MoveOperation.End)
            QTest.keyClicks(live, 'X');QTest.qWait(180)
            self.assertEqual(path.read_bytes(), raw+b'X')
            self.assertFalse(survivor.dirty)
            survivor.widget.undo();QTest.qWait(180)
            self.assertEqual(path.read_bytes(), raw)
            survivor.widget.redo();QTest.qWait(180)
            self.assertEqual(path.read_bytes(), raw+b'X')
            added = self.w.duplicate_view(survivor, True)
            QTest.qWait(100)
            self.assertIs(added.widget.buffer, survivor.widget.buffer)
            QTest.keyClicks(added.widget.md_surface.editor, 'Y');QTest.qWait(180)
            saved = path.read_bytes()
            self.assertEqual(saved, survivor.widget.encoded())
            for tab in (survivor, added):
                self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab)))
                QTest.qWait(120)
                self.assertFalse(isValid(tab.widget))
            reopened = self.open('Notes.md', retained=True)
            self.assertEqual(reopened.widget.encoded(), saved)
            other = self.open('Empty.md', retained=True, new_tab=True)
            QTest.keyClicks(other.widget.md_surface.editor, 'unrelated');QTest.qWait(180)
            self.assertEqual(path.read_bytes(), saved)
            self.assertEqual(reopened.widget.encoded(), saved)
            self.assertFalse(errors, '\n'.join(errors))

    def test_close_right_and_continue_editing(self):self.check_close(False)
    def test_close_left_and_continue_editing(self):self.check_close(True)
