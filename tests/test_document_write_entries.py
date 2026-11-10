"""Content entry contracts; real SQLite transactions and positive controls."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch

from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.stage_context import execution_scope
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.generation_panel import GenerationPanel
from prompt_calculus_studio.workflow_manager import WorkflowManager
from prompt_calculus_studio.document_operations import DocumentOperations, MULTI_HISTORY_FIELDS
from prompt_calculus_studio.workspace_scene import history_projection
import _borrow_guard as guard


class Owner:
    update_document = Window.update_document
    adopt_document = Window.adopt_document

    def __init__(self, store):
        self.store = store
        self.state = prepare_state(initial_state(), multi=True)
        self.events = []
        self.document_operations = None

    def document_changed(self, fields, *, reason, kind='content'):
        self.events.append((fields, reason, kind))


class DocumentWriteEntryTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.store = Storage(area.name)
        self.addCleanup(self.store.close)
        self.owner = Owner(self.store)

    def test_update_preserves_live_aliases_and_does_not_create_history(self):
        owner = self.owner
        generation = owner.state.setdefault('generation', {'profiles': [{'name': 'Before'}]})
        profile = generation['profiles'][0]
        owner.update_document(lambda s: profile.update(name='After'), fields=['generation'], reason='rename')
        self.assertIs(owner.state['generation'], generation)
        self.assertEqual(profile['name'], 'After')
        self.assertNotIn('content_history', owner.state)
        self.assertEqual(owner.events, [(frozenset({'generation'}), 'rename', 'content')])

    def test_execution_failure_rolls_back_update_and_journal_without_notification(self):
        owner = self.owner
        before = copy.deepcopy(owner.state)
        from prompt_calculus_studio.stage_store import StageStore
        journal = StageStore(self.store.db)
        existing = journal.add('entry', owner.state['workspace'], status='ready', payload={'text':'before'})
        before_rows = self.store.db.execute('SELECT * FROM stage_journal ORDER BY position').fetchall()
        with self.assertRaisesRegex(RuntimeError, 'injected'):
            with execution_scope(owner, journal):
                owner.update_document(lambda s: s['items'][0].update(prompt='changed'),
                    fields=['items'], reason='probe')
                journal.update(existing['id'], status='complete', payload={'text':'changed'})
                journal.add_captured('batch_snapshot', owner.state['workspace'], state=owner.state)
                journal.add('entry', owner.state['workspace'], status='pending')
                raise RuntimeError('injected')
        self.assertEqual(owner.state, before)
        self.assertEqual(self.store.db.execute('SELECT * FROM stage_journal ORDER BY position').fetchall(), before_rows)
        self.assertEqual(owner.events, [])

    def test_adoption_refused_in_execution_but_normal_outside_and_detached(self):
        owner = self.owner
        before = copy.deepcopy(owner.state)
        target = copy.deepcopy(before)
        target['items'][0]['prompt'] = 'adopted'
        history = copy.deepcopy(target)
        target['items'] = history['items']
        callback = Mock()
        with execution_scope(owner, self.store):
            with self.assertRaisesRegex(RuntimeError, 'workflow-import'):
                owner.adopt_document(target, reason='workflow-import', before=callback)
            self.assertEqual(owner.state, before)
        callback.assert_not_called()
        owner.adopt_document(target, reason='workflow-import', before=callback)
        callback.assert_called_once_with()
        self.assertEqual(owner.state, target)
        self.assertIsNot(owner.state['items'], history['items'])
        self.assertEqual(owner.events[-1], ('ALL', 'workflow-import', 'content'))

    def test_update_refuses_a_different_live_document_on_the_same_transaction(self):
        other = Owner(self.store)
        before = copy.deepcopy(other.state)
        action = Mock()
        with execution_scope(self.owner, self.store):
            with self.assertRaisesRegex(RuntimeError, 'other-document'):
                other.update_document(action, fields=['items'], reason='other-document')
        action.assert_not_called()
        self.assertEqual(other.state, before)
        self.assertEqual(other.events, [])

    def test_execution_notification_matches_transaction_changes_and_import_waits(self):
        owner = self.owner
        with execution_scope(owner, self.store) as tx:
            tx.register('items', 0)
            owner.state['items'][0]['prompt'] = 'changed'
            tx.register('dictionary')
            owner.state['dictionary'] = {'新': 'new'}
            self.assertEqual(owner.events, [])
        self.assertEqual(owner.events, [(frozenset(p[0] for p in tx.changes), 'execution', 'content')])
        target = copy.deepcopy(owner.state)
        target['draft'] = ''
        with execution_scope(owner, self.store):
            owner.adopt_document(target, reason='execution-import')
            self.assertNotEqual(owner.state, target)
        self.assertEqual(owner.state, target)

    def test_commit_reports_actual_top_fields_without_another_document_copy(self):
        owner = self.owner
        ops = DocumentOperations(state=lambda: owner.state, replace_state=lambda s: setattr(owner, 'state', s),
            history={}, project=lambda s: history_projection(s, MULTI_HISTORY_FIELDS),
            checkpoint=Mock(), sync=Mock(), notice=Mock(), is_layout_only=lambda a,b: False,
            changed=owner.document_changed)
        owner.document_operations = ops
        before = copy.deepcopy(owner.state)
        original_copy = copy.deepcopy
        copies = []
        def copying(value, *args, **kwargs):
            if value is before_live: copies.append(value)
            return original_copy(value, *args, **kwargs)
        before_live = owner.state
        with patch('copy.deepcopy', side_effect=copying):
            self.assertTrue(ops.commit(lambda s: s['items'][0].update(prompt='edited'), label='edit'))
        expected = frozenset(k for k in before.keys() | owner.state.keys()
                            if k not in before or k not in owner.state or before[k] != owner.state[k])
        self.assertEqual(owner.events, [(expected, 'edit', 'content')])
        self.assertEqual(len(copies), 1)
        self.assertEqual(len(ops.history['undo_stack']), 1)

    def test_guard_catches_direct_handler_and_original_rename(self):
        owner = self.owner
        owner.state['generation'] = dict(profiles=[dict(id='x', name='Before')])
        with guard.installed():
            owner.state = guard._live_wrap(owner.state, owner)
            with guard.expect_violation('items'), guard.live_handler(owner):
                owner.state['items'].append({'id': 'direct-handler'})
            # Exact baseline handler, extracted before the refactor; globals
            # retain its product module so this is not a fixture exemption.
            source = Path(__file__).with_name('fixtures').joinpath('workflow_rename_8e9e87e.py').read_text(encoding='utf-8')
            namespace = dict(__name__='prompt_calculus_studio.workflow_manager', InputDialog=NS(getText=lambda *a,**k: ('After', True)))
            exec(compile(source, 'prompt_calculus_studio/workflow_manager.py', 'exec'), namespace)
            manager = NS(window=owner, selected=lambda: owner.state['generation']['profiles'][0], refresh=Mock())
            owner.generation_panel = NS(changed=Mock())
            with guard.expect_violation('generation'):
                namespace['rename'](manager)
            self.assertEqual(owner.state['generation']['profiles'][0]['name'], 'Before')
            with patch('prompt_calculus_studio.workflow_manager.InputDialog.getText', return_value=('After', True)):
                WorkflowManager.rename(manager)
            self.assertEqual(owner.state['generation']['profiles'][0]['name'], 'After')

    def test_guard_rejects_undeclared_top_field_and_all_mutator_shapes(self):
        owner = self.owner
        with guard.installed():
            owner.state = guard._live_wrap(owner.state, owner)
            with guard.expect_violation('items'):
                owner.update_document(lambda s: s['items'][0].update(prompt='bad'), fields=['dictionary'], reason='bad')
            for action in (lambda: owner.state['items'].extend([{}]),
                           lambda: owner.state['items'].__setitem__(slice(None), []),
                           lambda: owner.state['items'][0].pop('prompt'),
                           lambda: owner.state.update(items=[])):
                with self.subTest(action=action), guard.expect_violation('items'), guard.live_handler(owner):
                    action()

    def test_legacy_allowlist_cannot_exempt_a_formal_host(self):
        owner = self.owner
        owner.state['draft'] = None
        owner.updating = False
        owner.reset_copy_feedback = Mock()
        owner.final = NS(toPlainText=lambda:'legacy draft')
        owner.update_draft_status = Mock()
        owner.changed = Mock()
        with guard.installed():
            owner.state = guard._live_wrap(owner.state, owner)
            owner.compact_editor = True
            with guard.expect_violation('draft_base'):
                Window.final_edited(owner)
            owner.compact_editor = False
            Window.final_edited(owner)
            self.assertEqual(owner.state['draft'], 'legacy draft')

    def test_getter_does_not_insert_defaults_and_profile_write_matches_baseline(self):
        from test_multi_output import workflow
        from prompt_calculus_studio.generation import store_profile
        owner = self.owner
        owner.state.pop('generation', None)
        before = copy.deepcopy(owner.state)
        panel = NS(window=owner, changed=Mock())
        self.assertEqual(GenerationPanel.settings(panel), dict(mode='txt2img', profiles=[], chosen={}, source=None))
        self.assertEqual(owner.state, before)
        expected = copy.deepcopy(before)
        store_profile(expected, workflow())
        GenerationPanel.save_profile(panel, workflow())
        self.assertEqual(owner.state, expected)
        self.assertEqual(owner.events[-1], (frozenset({'generation'}), 'workflow-profile', 'content'))

    def test_missing_defaults_materialize_only_on_legacy_assignment(self):
        owner = self.owner
        panel = NS(window=owner)
        for field in ('mode', 'chosen'):
            with self.subTest(field=field):
                owner.state.pop('generation', None)
                before = copy.deepcopy(owner.state)
                defaults = GenerationPanel.settings(panel)
                self.assertEqual(owner.state, before)
                self.assertEqual(copy.deepcopy(defaults), dict(mode='txt2img',profiles=[],chosen={},source=None))
                if field == 'mode': defaults['mode'] = 'img2img'
                else: defaults.setdefault('chosen', {})['txt2img'] = 'first'
                expected = dict(mode='txt2img', profiles=[], chosen={}, source=None)
                expected[field] = 'img2img' if field == 'mode' else {'txt2img':'first'}
                self.assertEqual(owner.state['generation'], expected)
                self.assertEqual(owner.events[-1], (frozenset({'generation'}), 'generation-default-write', 'content'))


if __name__ == '__main__': unittest.main()
