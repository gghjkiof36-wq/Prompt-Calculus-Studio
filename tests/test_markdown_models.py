"""Durable per-Markdown model ownership through real workspace file actions."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QLockFile

from prompt_calculus_studio import markdown_models as models
from prompt_calculus_studio.workspace_locations import path_key
import test_workspace_panes as fixture


class MarkdownModelTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def setUp(self):
        fixture.WorkspacePaneTests.setUp(self)
        self.service = SimpleNamespace(selected_model='a', models=[
            dict(id='a', name='A'), dict(id='b', name='B'), dict(id='c', name='C')])
        self.settings = self.w.data / 'md-models.json'

    def saved(self):
        return json.loads(self.settings.read_text(encoding='utf-8'))['models']

    def test_snapshot_is_per_file_and_never_follows_chat_selection(self):
        first = self.open('Notes.md', retained=True)
        surface = first.widget.md_surface
        self.assertEqual(models.markdown_model_id(surface, self.service), 'a')
        self.service.selected_model = 'b'
        second = self.open('Empty.md', retained=True)
        self.assertEqual(models.markdown_model_id(second.widget.md_surface, self.service), 'b')
        self.assertEqual(models.markdown_model_id(surface, self.service), 'a')
        models.set_markdown_model(surface, self.service, 'c')
        self.assertEqual(self.service.selected_model, 'b')
        self.assertEqual(self.saved(), {path_key(first.path): 'c', path_key(second.path): 'b'})

    def test_no_initial_model_and_explicit_clear_remain_unselected(self):
        tab = self.open('Notes.md', retained=True)
        self.service.selected_model = ''
        self.assertEqual(models.markdown_model_id(tab.widget.md_surface, self.service), '')
        self.service.selected_model = 'b'
        self.assertEqual(models.markdown_model_id(tab.widget.md_surface, self.service), '')
        models.set_markdown_model(tab.widget.md_surface, self.service, 'a')
        models.set_markdown_model(tab.widget.md_surface, self.service, '')
        self.assertEqual(self.saved()[path_key(tab.path)], '')
        self.assertEqual(self.service.selected_model, 'b')

    def test_close_reopen_with_new_document_identity_uses_saved_choice(self):
        tab = self.open('Notes.md', retained=True)
        old_id = tab.widget.buffer.document_id
        models.set_markdown_model(tab.widget.md_surface, self.service, 'b')
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='discard', make_home=False))
        self.events()
        self.service.selected_model = 'c'
        reopened = self.open('Notes.md', retained=True)
        self.assertNotEqual(reopened.widget.buffer.document_id, old_id)
        self.assertEqual(models.markdown_model_id(reopened.widget.md_surface, self.service), 'b')

    def test_fresh_process_reads_the_file_choice_without_chat_selection(self):
        tab = self.open('Notes.md', retained=True)
        models.set_markdown_model(tab.widget.md_surface, self.service, 'b')
        source = Path(models.__file__).parent.parent
        environment = dict(os.environ, QT_QPA_PLATFORM='offscreen',
                           PYTHONPATH=os.pathsep.join((str(source/'vendor'), str(source))))
        script = '''
from pathlib import Path
from types import SimpleNamespace
import sys
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.markdown_models import markdown_model_id
app = QApplication([])
workspace = WorkspaceShell(Path(sys.argv[1]), Path(sys.argv[2]))
tab = workspace.open_path(Path(sys.argv[1])/'Notes.md', retained=True)
service = SimpleNamespace(selected_model='c', models=[dict(id='b'), dict(id='c')])
print(markdown_model_id(tab.widget.md_surface, service))
workspace.close()
'''
        result = subprocess.run([sys.executable, '-c', script, str(self.root), str(self.w.data)],
                                env=environment, cwd=source, text=True, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), 'b')

    def test_shared_views_notify_only_after_save_without_editing_source(self):
        first = self.open('Notes.md', retained=True)
        second = self.w.duplicate_view(first, True)
        other = self.open('Empty.md', retained=True)
        self.events()
        surface = first.widget.md_surface
        self.assertEqual(models.markdown_model_id(surface, self.service), 'a')
        before = first.path.read_bytes()
        snapshot = first.widget.buffer.snapshot()
        history = first.widget.document().isUndoAvailable()
        with patch.object(surface.inline_assist, 'model_changed') as left, \
                patch.object(second.widget.md_surface.inline_assist, 'model_changed') as right, \
                patch.object(other.widget.md_surface.inline_assist, 'model_changed') as unrelated:
            left.side_effect = lambda: self.assertEqual(self.saved()[path_key(first.path)], 'b')
            models.set_markdown_model(surface, self.service, 'b')
            left.assert_called_once_with()
            right.assert_called_once_with()
            unrelated.assert_not_called()
            models.set_markdown_model(surface, self.service, 'b')
            self.assertEqual(left.call_count, 1)
        self.assertEqual(models.markdown_model_id(second.widget.md_surface, self.service), 'b')
        self.assertEqual(first.widget.buffer.snapshot(), snapshot)
        self.assertEqual(first.widget.document().isUndoAvailable(), history)
        self.assertFalse(first.dirty)
        self.assertEqual(first.path.read_bytes(), before)

    def test_save_as_and_rename_inherit_before_any_new_lookup(self):
        first = self.open('Notes.md', retained=True)
        second = self.w.duplicate_view(first, True)
        self.events()
        original = first.path
        before = original.read_bytes()
        models.set_markdown_model(first.widget.md_surface, self.service, 'b')
        self.service.selected_model = 'c'
        context = self.w.commands.context('tab', tab=second)
        copied = self.w.commands.files.run('save_as', context, name='ModelCopy', directory=self.root)
        self.assertEqual(copied, self.root / 'ModelCopy.md', self.errors)
        self.assertEqual(self.saved()[path_key(copied)], 'b')
        self.assertEqual(self.saved()[path_key(original)], 'b')
        renamed = self.w.commands.files.run('rename', self.w.commands.context('tab', tab=first), name='Renamed')
        self.assertEqual(self.saved()[path_key(renamed)], 'b')
        self.assertEqual(models.markdown_model_id(second.widget.md_surface, self.service), 'b')
        self.assertEqual(original.read_bytes(), before)
        reopened = self.open('Notes.md', retained=True)
        self.assertEqual(models.markdown_model_id(reopened.widget.md_surface, self.service), 'b')

    def test_missing_saved_model_is_reported_and_can_be_reselected(self):
        tab = self.open('Notes.md', retained=True)
        models.set_markdown_model(tab.widget.md_surface, self.service, 'b')
        before = self.settings.read_bytes()
        self.service.models = [dict(id='a', name='A')]
        with self.assertRaisesRegex(ValueError, '已不在清單'):
            models.markdown_model_id(tab.widget.md_surface, self.service)
        self.assertEqual(self.settings.read_bytes(), before)
        models.set_markdown_model(tab.widget.md_surface, self.service, 'a')
        self.assertEqual(models.markdown_model_id(tab.widget.md_surface, self.service), 'a')

    def test_save_as_over_reused_path_preference_inherits_source(self):
        tab = self.open('Notes.md', retained=True)
        models.set_markdown_model(tab.widget.md_surface, self.service, 'b')
        mapping = self.saved()
        mapping[path_key(self.root/'Reused.md')] = 'c'
        self.settings.write_text(json.dumps(dict(version=1, models=mapping)), encoding='utf-8')
        copied = self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=tab),
                                           name='Reused', directory=self.root)
        self.assertEqual(self.saved()[path_key(copied)], 'b')

    def test_save_as_mapping_failure_is_retried_without_changing_markdown(self):
        tab = self.open('Notes.md', retained=True)
        surface = tab.widget.md_surface
        models.set_markdown_model(surface, self.service, 'b')
        before = self.settings.read_bytes()
        original = tab.path.read_bytes()
        with patch.object(models, '_write_mapping', side_effect=OSError('disk full')):
            copied = self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=tab),
                                               name='Retry', directory=self.root)
            self.assertEqual(copied.read_bytes(), original)
            self.assertEqual(self.settings.read_bytes(), before)
            with self.assertRaises(ValueError):
                models.markdown_model_id(surface, self.service)
        self.assertEqual(models.markdown_model_id(surface, self.service), 'b')
        self.assertEqual(self.saved()[path_key(copied)], 'b')

    def test_invalid_selection_never_changes_saved_choice(self):
        tab = self.open('Notes.md', retained=True)
        surface = tab.widget.md_surface
        models.set_markdown_model(surface, self.service, 'b')
        before = self.settings.read_bytes()
        for bad in ('missing', None, 42, True):
            with self.subTest(value=bad), self.assertRaises(ValueError):
                models.set_markdown_model(surface, self.service, bad)
        self.assertEqual(self.settings.read_bytes(), before)

    def test_corrupt_unknown_and_invalid_mapping_are_preserved(self):
        tab = self.open('Notes.md', retained=True)
        raw_cases = [b'{', b'[]', b'{"version":2,"models":{}}',
                     b'{"version":true,"models":{}}',
                     json.dumps(dict(version=1, models={path_key(tab.path): True})).encode(),
                     b'{"version":1,"models":{"relative.md":"a"}}']
        for raw in raw_cases:
            with self.subTest(raw=raw):
                self.settings.write_bytes(raw)
                with self.assertRaises(ValueError):
                    models.markdown_model_id(tab.widget.md_surface, self.service)
                with self.assertRaises(ValueError):
                    models.set_markdown_model(tab.widget.md_surface, self.service, 'b')
                self.assertEqual(self.settings.read_bytes(), raw)

    def test_write_failure_keeps_old_choice_and_does_not_notify(self):
        tab = self.open('Notes.md', retained=True)
        surface = tab.widget.md_surface
        models.set_markdown_model(surface, self.service, 'a')
        before = self.settings.read_bytes()
        with patch.object(models, '_write_mapping', side_effect=OSError('disk full')), \
                patch.object(surface.inline_assist, 'model_changed') as notified:
            with self.assertRaises(ValueError):
                models.set_markdown_model(surface, self.service, 'b')
            notified.assert_not_called()
        self.assertEqual(self.settings.read_bytes(), before)
        self.assertEqual(models.markdown_model_id(surface, self.service), 'a')

    def test_atomic_commit_failure_is_visible_and_preserves_original(self):
        tab = self.open('Notes.md', retained=True)
        surface = tab.widget.md_surface
        models.set_markdown_model(surface, self.service, 'a')
        before = self.settings.read_bytes()
        output = Mock()
        output.open.return_value = True
        output.write.side_effect = len
        output.commit.return_value = False
        with patch.object(models, 'QSaveFile', return_value=output):
            with self.assertRaisesRegex(ValueError, '尚未保存'):
                models.set_markdown_model(surface, self.service, 'b')
        self.assertEqual(self.settings.read_bytes(), before)

    def test_busy_settings_lock_is_visible_and_preserves_original(self):
        tab = self.open('Notes.md', retained=True)
        surface = tab.widget.md_surface
        models.set_markdown_model(surface, self.service, 'a')
        before = self.settings.read_bytes()
        lock = QLockFile(str(self.settings) + '.lock')
        self.assertTrue(lock.tryLock(0))
        try:
            with self.assertRaisesRegex(ValueError, '正在使用'):
                models.set_markdown_model(surface, self.service, 'b')
        finally:
            lock.unlock()
        self.assertEqual(self.settings.read_bytes(), before)

    def test_existing_lookup_does_not_rewrite_preferences(self):
        tab = self.open('Notes.md', retained=True)
        surface = tab.widget.md_surface
        models.markdown_model_id(surface, self.service)
        with patch.object(models, '_write_mapping') as saved:
            self.assertEqual(models.markdown_model_id(surface, self.service), 'a')
            self.assertEqual(models.markdown_model_id(surface, self.service), 'a')
            saved.assert_not_called()


if __name__ == '__main__':
    unittest.main()
