"""File/close entry points must drain a chat writer before changing its path."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.workspace_file_operations import WorkspaceFileOperations
from prompt_calculus_studio.workspace_panes import WorkspacePanes
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class ChatCheckpointBarriers(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'source.pcs-chat'; self.path.write_bytes(b'before')
        self.doc = SimpleNamespace(kind='chat', finish_checkpoint=Mock(return_value=True))
        self.tab = SimpleNamespace(path=self.path, key='view', document=self.doc, widget=object())
        self.auto = SimpleNamespace(key=lambda t:'owner', saving=set(), paused=set(),
            editing_dialog=lambda t:False, stop=Mock(), schedule=Mock())
        self.workspace = SimpleNamespace(all_pages=lambda:[self.tab], autosave=self.auto, show_notice=Mock())
        self.files = SimpleNamespace(w=self.workspace, busy=lambda p:False, pending=[])
        self.context = SimpleNamespace(path=self.path, kind='chat')

    def test_file_operation_pauses_then_drains_before_capturing_stamp(self):
        def finish():
            self.assertIn(id(self.tab), self.auto.paused)
            self.path.write_bytes(b'checkpoint accepted'); return True
        self.doc.finish_checkpoint.side_effect = finish
        with patch('prompt_calculus_studio.workspace_file_operations.stamp', side_effect=lambda p:p.read_bytes()):
            op = WorkspaceFileOperations.begin(self.files, self.context)
        self.assertEqual(op.expected, b'checkpoint accepted'); self.assertIn(op, self.files.pending)

    def test_unfinished_writer_prevents_operation_and_releases_pause(self):
        self.doc.finish_checkpoint.return_value = False
        with patch('prompt_calculus_studio.workspace_file_operations.stamp') as stamp:
            with self.assertRaisesRegex(ValueError, '正在保存'):
                WorkspaceFileOperations.begin(self.files, self.context)
            stamp.assert_not_called()
        self.assertFalse(self.files.pending); self.assertFalse(self.auto.paused)
        self.auto.schedule.assert_called_once_with(self.tab)

    def test_multiple_views_drain_same_owner_once(self):
        second = SimpleNamespace(path=self.path, key='second', document=self.doc)
        self.workspace.all_pages = lambda:[self.tab, second]
        WorkspaceFileOperations.begin(self.files, self.context)
        self.doc.finish_checkpoint.assert_called_once()

    def test_discard_close_still_rejects_inflight_writer(self):
        self.doc.finish_checkpoint.return_value = False
        self.assertFalse(WorkspaceShell.prepare_close(self.workspace, self.tab, decision='discard'))
        self.workspace.show_notice.assert_called_once()

    def test_prepared_disposal_cannot_delete_an_inflight_owner(self):
        self.doc.finish_checkpoint.return_value = False
        self.assertFalse(WorkspacePanes.dispose_page(self.workspace, self.tab, prepared=True))
        self.workspace.show_notice.assert_called_once()

    def test_inline_edit_prevents_file_operation_before_pause_or_writer(self):
        self.tab.widget = SimpleNamespace(has_uncommitted_edit=True)
        with self.assertRaisesRegex(ValueError, '正在編輯'):
            WorkspaceFileOperations.begin(self.files, self.context, allow_editing=True)
        self.auto.stop.assert_not_called()
        self.doc.finish_checkpoint.assert_not_called()
        self.assertFalse(self.auto.paused)
        self.assertEqual(self.path.read_bytes(), b'before')

    def test_inline_edit_prevents_close_navigation_and_saved_page_disposal(self):
        self.tab.widget = SimpleNamespace(has_uncommitted_edit=True, discard_inline_edit=Mock())
        self.assertFalse(WorkspaceShell.prepare_close(self.workspace, self.tab))
        self.assertFalse(WorkspaceShell.prepare_close(self.workspace, self.tab, stop_chat=False))
        self.assertFalse(WorkspacePanes.dispose_page(self.workspace, self.tab))
        self.tab.widget.discard_inline_edit.assert_not_called()
        self.doc.finish_checkpoint.assert_not_called()

    def test_explicit_discard_waits_for_writer_before_discarding_inline_edit(self):
        self.tab.widget = SimpleNamespace(has_uncommitted_edit=True, discard_inline_edit=Mock())
        self.doc.finish_checkpoint.return_value = False
        self.assertFalse(WorkspaceShell.prepare_close(self.workspace, self.tab, decision='discard'))
        self.tab.widget.discard_inline_edit.assert_not_called()
        self.doc.finish_checkpoint.return_value = True
        self.doc.composing = False
        self.doc.flush_views = Mock(return_value=True)
        self.doc.stop = Mock()
        self.assertTrue(WorkspaceShell.prepare_close(self.workspace, self.tab, decision='discard'))
        self.tab.widget.discard_inline_edit.assert_called_once()

    def test_unchanged_editor_releases_lease_before_close_and_file_stamp(self):
        self.tab.widget = SimpleNamespace(has_uncommitted_edit=False, discard_inline_edit=Mock())
        self.doc.composing = False
        self.doc.flush_views = Mock(return_value=True)
        self.doc.stop = Mock()
        self.assertTrue(WorkspaceShell.prepare_close(self.workspace, self.tab, decision='discard'))
        self.tab.widget.discard_inline_edit.assert_called_once()
        self.tab.widget.discard_inline_edit.reset_mock()
        def stamp_after_release(path):
            self.tab.widget.discard_inline_edit.assert_called_once()
            return path.read_bytes()
        with patch('prompt_calculus_studio.workspace_file_operations.stamp', side_effect=stamp_after_release):
            WorkspaceFileOperations.begin(self.files, self.context, allow_editing=True)

    def test_edit_changed_while_draining_writer_is_not_discarded(self):
        self.tab.widget = SimpleNamespace(has_uncommitted_edit=False, discard_inline_edit=Mock())
        self.doc.composing = False
        self.doc.flush_views = Mock(return_value=True)
        def finish():
            self.tab.widget.has_uncommitted_edit = True
            return True
        self.doc.finish_checkpoint.side_effect = finish
        self.assertFalse(WorkspaceShell.prepare_close(self.workspace, self.tab))
        self.tab.widget.has_uncommitted_edit = False
        with patch('prompt_calculus_studio.workspace_file_operations.stamp') as stamp:
            with self.assertRaisesRegex(ValueError, '正在編輯'):
                WorkspaceFileOperations.begin(self.files, self.context, allow_editing=True)
            stamp.assert_not_called()
        self.tab.widget.discard_inline_edit.assert_not_called()
        self.assertFalse(self.auto.paused)
