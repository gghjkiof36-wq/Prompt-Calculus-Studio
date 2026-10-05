"""Document replacement and hidden Canvas caches, through actual Qt entry points."""
import copy
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PySide6.QtTest import QTest

from prompt_calculus_studio import clip_flow, drafts, stage_model, workspace_scene
from prompt_calculus_studio.core import validate_state
from prompt_calculus_studio.state_loading import prepare_state
from stage_fixture import APP, StageFixture
from test_multi_output import workspace


class ImportRefreshRepairTests(StageFixture):
    def backup_json(self):
        legacy, _, output = workspace()
        # The old output binding schema is migrated at the import boundary.
        legacy['draft'] = ''
        legacy['multi_output']['outputs'][output]['draft'] = ''
        path = Path(self.tmp.name) / 'synthetic-import.json'
        path.write_text(json.dumps(legacy, ensure_ascii=False), encoding='utf-8')
        return path, prepare_state(legacy, multi=True), output

    def import_file(self, path):
        errors = []
        with patch('prompt_calculus_studio.window.QFileDialog.getOpenFileName', return_value=(str(path), 'JSON')), \
                patch('prompt_calculus_studio.window.ask', return_value=True), patch.object(self.w, 'error', side_effect=errors.append):
            self.w.import_json()
            self.w.changes.flush()
            QTest.qWait(20)
        return errors

    def test_settings_import_replaces_stale_cards_preserves_blank_and_resets_old_history(self):
        old_stage = stage_model.add(self.w.state, workflow='flow')
        self.c.refresh()
        old_clip = self.clip
        self.w.workspace_histories = {'old-only': (list(self.c.undo_stack), [])}
        path, expected, output = self.backup_json()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        self.w.settings('data')
        self.assertFalse(self.c.isVisible())
        self.w.changed('prompt')  # A queued refresh from before replacement.
        self.assertEqual(self.import_file(path), [])
        self.assertNotIn(old_clip, self.c.clips)
        self.assertNotIn(old_stage, self.c.flow_cards)
        self.assertFalse(self.c.undo_stack)
        self.assertFalse(self.c.redo_stack)
        self.assertFalse(self.w.workspace_histories)
        def bindings(state):
            return [dict(workflow=b['workflow'], node=b['node'], field=b['field'],
                         source=clip_flow.source(state, b['clip'])) for b in state['multi_output']['bindings']]
        self.assertEqual(bindings(self.w.state), bindings(expected))
        self.assertEqual(self.w.state['multi_output']['outputs'][output]['draft'], '')
        self.assertEqual(self.w.state['draft'], '')
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        backups = list(Path(self.tmp.name).glob('before-import-*.sqlite3'))
        self.assertTrue(backups)
        with closing(sqlite3.connect(backups[-1])) as db:
            original = json.loads(db.execute('SELECT body FROM document WHERE id=1').fetchone()[0])
        self.assertIn(old_clip, original['multi_output']['clip_inputs'])
        self.w.return_to_prompt(); QTest.qWait(25)
        self.assertEqual(set(self.c.clips), set(self.c.data()['clip_inputs']))
        self.assertEqual(set(self.c.outputs), set(self.c.data()['outputs']))
        self.c.commit(lambda state: state['multi_output']['outputs'][output].update(name='renamed'))
        self.c.undo()
        self.assertEqual(self.c.data()['outputs'][output]['name'], expected['multi_output']['outputs'][output]['name'])
        self.c.redo()
        self.assertEqual(self.c.data()['outputs'][output]['name'], 'renamed')
        self.assertEqual(self.c.data()['outputs'][output]['draft'], '')
        self.assertFalse(self.executor.submissions)
        self.assertNotIn(old_stage, self.c.data()['stages'])

    def test_hidden_workspace_switch_and_undo_remove_old_cards_without_generation(self):
        first = self.w.state['workspace']
        drafts.edit(self.w.state, '', self.out)
        second = workspace_scene.create(self.w.state, 'new blank workspace')
        history = copy.deepcopy(self.c.undo_stack)
        self.w.settings('data')
        # Hidden refresh still updates text; it only defers scene construction.
        self.w.changed('prompt'); self.w.changes.flush()
        self.assertEqual(self.c.outputs[self.out].panel.editor.toPlainText(), '')
        self.w.change_workspace(second); self.w.changes.flush()
        self.assertNotIn(self.clip, self.c.clips)
        self.w.change_workspace(first); self.w.return_to_prompt(); QTest.qWait(25)
        self.assertIn(self.clip, self.c.clips)
        self.assertEqual(self.c.undo_stack, history)
        self.assertEqual(self.c.data()['outputs'][self.out]['draft'], '')
        other = self.c.add_clip()
        other_ports = {key: port for key, port in self.c.ports.items() if key[0] == other}
        self.w.settings('data')
        self.assertTrue(self.c.commit(lambda state: clip_flow.remove(state, self.clip)))
        self.w.changes.flush()
        self.assertNotIn(self.clip, self.c.clips)
        self.assertEqual({key: port for key, port in self.c.ports.items() if key[0] == other}, other_ports)
        self.c.undo(); self.w.changes.flush()
        self.w.return_to_prompt(); QTest.qWait(25)
        self.assertIn(self.clip, self.c.clips)
        self.assertTrue(any(binding['clip'] == self.clip for binding in self.c.data()['bindings']))
        self.assertEqual(self.c.data()['outputs'][self.out]['draft'], '')
        validate_state(self.w.state)
        self.assertFalse(self.executor.submissions)

    def test_deferred_output_update_discards_old_document_cards_before_rendering(self):
        self.w.settings('data')
        _, replacement, _ = self.backup_json()
        self.w.state = replacement
        self.w.changed('prompt')
        self.w.changed('layout')
        self.w.changes.flush()
        self.assertNotIn(self.clip, self.c.clips)
        self.assertFalse(any(port.parentItem() is None for port in self.c.ports.values()))
        self.w.return_to_prompt(); QTest.qWait(25)
        self.assertEqual(set(self.c.clips), set(self.c.data()['clip_inputs']))
        self.assertFalse(self.executor.submissions)

    def test_rejected_import_does_not_clear_document_or_undo(self):
        before = copy.deepcopy(self.w.state)
        history = copy.deepcopy(self.c.undo_stack)
        path = Path(self.tmp.name) / 'unsupported.json'
        rejected = copy.deepcopy(before); rejected['version'] = 999
        path.write_text(json.dumps(rejected), encoding='utf-8')
        self.w.settings('data')
        errors = self.import_file(path)
        self.assertEqual(len(errors), 1)
        self.assertEqual(self.w.state['multi_output'], before['multi_output'])
        self.assertEqual(self.c.undo_stack, history)
        self.assertFalse(self.executor.submissions)
