"""Saved document snapshots and source-bound file actions, without inference."""
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.workspace_chat import choose_pcs_attachments, export_markdown


class ChatFileActions(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory(); self.root = Path(self.temp.name); self.addCleanup(self.temp.cleanup)
        self.path = self.root / '原對話.pcs-chat'; self.path.write_bytes(b'original')
        self.document = SimpleNamespace(kind='chat', _disposed=False, key='doc', owner_epoch='epoch', path=self.path,
            payload=dict(turns=[dict(id='turn', attempts=[dict(id='attempt', status='complete', text='<think>private reasoning</think>\n# 正文\n答案🙂')])]),
            add_attachments=Mock(return_value=True), save=Mock(), stop=Mock())
        self.tab = SimpleNamespace(key='view', document=self.document); self.fresh = SimpleNamespace(key='new')
        self.workspace = SimpleNamespace(tabs_by_key={'view': self.tab},
            locations=SimpleNamespace(root_for=lambda p: self.root if p.is_relative_to(self.root) else None, directory=lambda p: p),
            show_notice=Mock(), report_error=Mock(), refresh_tree=Mock(), pane_for=Mock(return_value='source-pane'),
            open_path=Mock(return_value=self.fresh), last_open_error='')

    def tearDown(self):
        self.document.save.assert_not_called(); self.document.stop.assert_not_called()
        self.assertEqual(self.path.read_bytes(), b'original')

    def test_pcs_picker_passes_descriptors_without_reading_and_retains_source_owner(self):
        selected = self.root / '還未讀取.md'
        with patch('PySide6.QtWidgets.QFileDialog.getOpenFileNames', return_value=([str(selected)], '')):
            self.assertTrue(choose_pcs_attachments(self.workspace, self.tab))
        self.document.add_attachments.assert_called_once_with([dict(source_type='pcs', path=str(selected), workspace_root=str(self.root))])

    def test_pcs_picker_rejects_owner_retarget_during_dialog(self):
        def choose(*args):
            self.document.owner_epoch = 'new'; return [str(self.root/'file.md')], ''
        with patch('PySide6.QtWidgets.QFileDialog.getOpenFileNames', side_effect=choose):
            self.assertFalse(choose_pcs_attachments(self.workspace, self.tab))
        self.document.add_attachments.assert_not_called()

    def test_pcs_picker_cancel_or_outside_never_submits(self):
        for selection in ([], [str(self.root.parent/'outside.md')]):
            with patch('PySide6.QtWidgets.QFileDialog.getOpenFileNames', return_value=(selection, '')):
                self.assertFalse(choose_pcs_attachments(self.workspace, self.tab))
        self.document.add_attachments.assert_not_called()

    def export(self):
        return export_markdown(self.workspace, self.tab, 'turn', 'attempt')

    def test_markdown_body_only_exclusive_numbered_save_and_open(self):
        target = self.root/'reply.md'; target.write_bytes(b'keep')
        with patch('PySide6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            self.assertIs(self.export(), self.fresh)
        created = self.root/'reply(1).md'
        self.assertEqual(created.read_text(encoding='utf-8'), '# 正文\n答案🙂'); self.assertEqual(target.read_bytes(), b'keep')
        self.workspace.open_path.assert_called_once_with(created, True, new_tab=True, pane='source-pane', quiet=True)

    def test_export_retarget_or_cancel_never_writes(self):
        def choose(*args, **kwargs):
            self.document.owner_epoch = 'changed'; return str(self.root/'reply.md'), ''
        with patch('PySide6.QtWidgets.QFileDialog.getSaveFileName', side_effect=choose): self.assertIsNone(self.export())
        self.assertFalse((self.root/'reply.md').exists()); self.workspace.open_path.assert_not_called()

    def test_export_missing_or_thinking_only_never_opens_picker(self):
        self.document.payload['turns'][0]['attempts'][0]['text'] = '<think>still thinking'
        with patch('PySide6.QtWidgets.QFileDialog.getSaveFileName') as picker:
            self.assertIsNone(self.export()); picker.assert_not_called()

    def test_export_write_failure_no_success_and_open_failure_keeps_file(self):
        target = self.root/'reply.md'
        with patch('PySide6.QtWidgets.QFileDialog.getSaveFileName', return_value=(str(target), '')):
            with patch('prompt_calculus_studio.workspace_file_io.create_markdown', side_effect=OSError('disk full')):
                self.assertIsNone(self.export()); self.workspace.open_path.assert_not_called()
            self.workspace.open_path.return_value = None
            self.assertIsNone(self.export()); self.assertTrue(target.is_file())
        self.assertIn('已保存，但無法開啟', self.workspace.report_error.call_args.args[0])
