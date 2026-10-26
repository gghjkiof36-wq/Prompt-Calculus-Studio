"""Branch publication and navigation failures never mutate the source owner."""
from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.local_documents.io import SaveResult
from prompt_calculus_studio.workspace_chat import _branch_name, create_branch


class WorkspaceChatBranches(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name)
        self.source = self.root / '原對話.pcs-chat'; self.source.write_bytes(b'original source')
        self.candidate = {'document_id': 'independent', 'payload': {'turns': [], 'draft': ''}}
        self.document = SimpleNamespace(path=self.source, kind='chat', _disposed=False, error='',
                                        branch_document=Mock(return_value=deepcopy(self.candidate)), save=Mock(), stop=Mock())
        self.tab = SimpleNamespace(key='source-view', document=self.document)
        self.fresh = SimpleNamespace(key='branch-view')
        self.workspace = SimpleNamespace(
            tabs_by_key={self.tab.key: self.tab}, locations=SimpleNamespace(directory=lambda path: path),
            show_notice=Mock(), report_error=Mock(), refresh_tree=Mock(), pane_for=Mock(return_value='source-pane'),
            open_path=Mock(return_value=self.fresh), last_open_error='stale error')
        self.published = []

    def tearDown(self):
        self.document.save.assert_not_called(); self.document.stop.assert_not_called()
        self.assertEqual(self.source.read_bytes(), b'original source'); self.temp.cleanup()

    def write(self, parent, name, candidate):
        self.assertEqual(candidate, self.candidate)
        target = Path(parent) / name; target.write_bytes(b'new independent branch'); self.published.append(target)
        return SaveResult('saved', document=candidate, published=True)

    def branch(self, attempt='attempt-1'):
        return create_branch(self.workspace, self.tab, 'turn-1', attempt)

    def test_collision_uses_numbered_new_file_and_opens_same_pane(self):
        existing = self.root / '原對話 - 分支.pcs-chat'; existing.write_bytes(b'keep existing')
        with patch('prompt_calculus_studio.workspace_chat.io.create', side_effect=self.write):
            self.assertIs(self.branch(), self.fresh)
        self.assertEqual(existing.read_bytes(), b'keep existing')
        self.assertEqual(self.published[0].name, '原對話 - 分支(1).pcs-chat')
        self.workspace.open_path.assert_called_once_with(self.published[0], True, new_tab=True, pane='source-pane', quiet=True)
        self.assertEqual(self.workspace.last_open_error, '')
        self.workspace.show_notice.assert_not_called()

    def test_exclusive_create_race_advances_without_overwrite(self):
        count = 0
        def write(parent, name, candidate):
            nonlocal count
            count += 1
            if count == 1:
                (Path(parent) / name).write_bytes(b'external winner')
                return SaveResult('conflict', error='exists', name_collision=True)
            return self.write(parent, name, candidate)
        with patch('prompt_calculus_studio.workspace_chat.io.create', side_effect=write):
            self.assertIs(self.branch(), self.fresh)
        self.assertEqual(count, 2); self.assertEqual(self.published[0].name, '原對話 - 分支(1).pcs-chat')
        self.assertEqual((self.root / '原對話 - 分支.pcs-chat').read_bytes(), b'external winner')

    def test_write_failure_does_not_open_or_claim_success(self):
        result = SaveResult('failed', error='disk full', recovery_paths=('candidate.recovery',))
        with patch('prompt_calculus_studio.workspace_chat.io.create', return_value=result): self.assertIsNone(self.branch())
        self.workspace.open_path.assert_not_called(); self.workspace.show_notice.assert_not_called()
        message = self.workspace.report_error.call_args.args[0]
        self.assertIn('disk full', message); self.assertIn('candidate.recovery', message)

    def test_published_readback_failure_preserves_file_and_reports_uncertainty(self):
        def fail(parent, name, candidate):
            (Path(parent) / name).write_bytes(b'published but unreadable')
            return SaveResult('failed', error='readback failed', published=True)
        with patch('prompt_calculus_studio.workspace_chat.io.create', side_effect=fail): self.assertIsNone(self.branch())
        target = self.root / '原對話 - 分支.pcs-chat'
        self.assertEqual(target.read_bytes(), b'published but unreadable'); self.workspace.open_path.assert_not_called()
        self.assertIn(str(target), self.workspace.report_error.call_args.args[0]); self.workspace.show_notice.assert_not_called()

    def test_saved_but_open_failed_keeps_new_file_and_reports_location(self):
        self.workspace.open_path.return_value = None
        with patch('prompt_calculus_studio.workspace_chat.io.create', side_effect=self.write): self.assertIsNone(self.branch())
        self.assertTrue(self.published[0].is_file()); self.workspace.show_notice.assert_not_called()
        self.assertIn(str(self.published[0]), self.workspace.report_error.call_args.args[0])
        self.assertNotIn('stale error', self.workspace.report_error.call_args.args[0])

    def test_closed_source_or_uncommitted_input_cannot_publish(self):
        self.workspace.tabs_by_key.clear()
        with patch('prompt_calculus_studio.workspace_chat.io.create') as create:
            self.assertIsNone(self.branch()); create.assert_not_called()
        self.document.branch_document.assert_not_called()
        self.workspace.tabs_by_key[self.tab.key] = self.tab
        self.document.branch_document.side_effect = ValueError('finish input')
        with patch('prompt_calculus_studio.workspace_chat.io.create') as create:
            self.assertIsNone(self.branch()); create.assert_not_called()
        self.assertIn('finish input', self.workspace.report_error.call_args.args[0])

    def test_user_cut_passes_none_and_source_save_error_is_not_cleared(self):
        self.document.error = 'source save failed'
        with patch('prompt_calculus_studio.workspace_chat.io.create', side_effect=self.write):
            self.assertIs(self.branch(''), self.fresh)
        self.document.branch_document.assert_called_once_with('turn-1', None)
        self.assertEqual(self.document.error, 'source save failed')
        self.assertIn('原對話仍有保存錯誤', self.workspace.show_notice.call_args.args[0])

    def test_long_unicode_name_stays_within_windows_component_limit(self):
        name = _branch_name('長名字😀' * 100)
        self.assertLessEqual(len(name.encode('utf-16-le')), 510)
        self.assertTrue(name.endswith(' - 分支.pcs-chat'))


if __name__ == '__main__': unittest.main()
