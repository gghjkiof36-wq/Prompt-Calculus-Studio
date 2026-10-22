"""Content transactions without a QWidget, QApplication or alternate history."""
import copy
import unittest
from unittest.mock import patch

from prompt_calculus_studio import materials, drafts
from prompt_calculus_studio.core import initial_state, validate_state
from prompt_calculus_studio.document_operations import DocumentOperations, MULTI_HISTORY_FIELDS
from prompt_calculus_studio.workspace_scene import history_projection
from prompt_calculus_studio.state_loading import prepare_state


class DocumentOperationsTests(unittest.TestCase):
    def setUp(self):
        self.state = prepare_state(initial_state(), multi=True)
        self.state['settings'].update(online=False, comfy_enabled=False)
        self.history = {}
        self.events = []
        self.notices = []
        self.now = 10
        self.ops = self.operations()

    def project(self, state):
        return history_projection(state, MULTI_HISTORY_FIELDS)

    def operations(self, **hooks):
        def replace(value):
            self.events.append(('replace', value['items'][0]['prompt']))
            self.state = value
        return DocumentOperations(
            state=lambda: self.state, replace_state=replace, history=self.history,
            project=self.project,
            checkpoint=lambda old: self.events.append(('checkpoint', copy.deepcopy(old))),
            sync=lambda scope: self.events.append(('sync', scope, len(self.history['undo_stack']))),
            notice=self.notices.append, is_layout_only=lambda a, b: False,
            clock=lambda: self.now, **hooks)

    def edit(self, value):
        return lambda state: materials.save(state, dict(id=state['items'][0]['id'], prompt=value))

    def test_commit_publishes_once_after_validation_and_keeps_original_independent(self):
        original = self.state
        before = self.project(original)
        self.assertTrue(self.ops.commit(self.edit('accepted'), label='Edit content'))
        self.assertNotEqual(original['items'][0]['prompt'], 'accepted')
        self.assertEqual(self.state['items'][0]['prompt'], 'accepted')
        self.assertEqual(self.history['undo_stack'], [(before, self.project(self.state))])
        self.assertEqual(self.events, [('replace', 'accepted'), ('sync', 'prompt', 1)])
        self.assertEqual(self.state['content_history']['entries'][-1]['label'], 'Edit content')
        output = self.state['multi_output']['current_output']
        self.assertTrue(self.ops.commit(lambda value: drafts.edit(value, '', output)))
        self.assertEqual(self.state['multi_output']['outputs'][output]['draft'], '')
        self.assertIsNone(original['multi_output']['outputs'][output]['draft'])

    def test_noop_invalid_operation_and_invalid_record_leave_state_history_untouched(self):
        original = self.state
        before = copy.deepcopy(original)
        self.assertIsNone(self.ops.commit(lambda value: None))
        self.assertFalse(self.ops.commit(lambda value: value['items'].append(copy.deepcopy(value['items'][0]))))
        def reject_journal(value):
            validate_state(value)
            if 'content_history' in value:
                raise ValueError('journal rejected')
        with patch('prompt_calculus_studio.document_operations.validate_state', side_effect=reject_journal):
            self.assertFalse(self.ops.commit(self.edit('rejected')))
        self.assertIs(self.state, original)
        self.assertEqual(self.state, before)
        self.assertEqual(self.history['undo_stack'], [])
        self.assertEqual(self.events, [])
        self.assertEqual(self.notices[-1], 'journal rejected')

    def test_programming_and_checkpoint_io_errors_propagate_without_publication(self):
        original = self.state
        with self.assertRaisesRegex(RuntimeError, 'operation bug'):
            self.ops.commit(lambda value: (_ for _ in ()).throw(RuntimeError('operation bug')))
        self.assertIs(self.state, original)
        self.state = initial_state()
        with patch.object(self.ops, 'checkpoint', side_effect=OSError('disk failed')):
            with self.assertRaisesRegex(OSError, 'disk failed'):
                self.ops.commit(lambda value: value.update(version=2))
        self.assertEqual(self.state['version'], 1)
        self.assertFalse(self.history['undo_stack'])

    def test_upgrade_checkpoints_old_state_before_publishing_and_notifies_last(self):
        self.state = initial_state()
        original = copy.deepcopy(self.state)
        self.assertTrue(self.ops.commit(lambda value: value.update(version=2)))
        self.assertEqual(self.events[0], ('checkpoint', original))
        self.assertEqual(self.events[-1], ('sync', 'prompt', 1))
        self.assertEqual(self.state['version'], 2)

    def test_two_editors_share_coalescing_redo_clear_and_single_history(self):
        other = self.operations()
        before = self.project(self.state)
        self.assertTrue(self.ops.commit(self.edit('first'), merge_key='material'))
        self.now += .1
        self.assertTrue(other.commit(self.edit('second'), merge_key='material'))
        self.assertIs(self.ops.history, other.history)
        self.assertEqual(self.history['undo_stack'], [(before, self.project(self.state))])
        self.assertEqual(len(self.state['content_history']['entries']), 1)
        self.history['redo_stack'].append(('old', 'redo'))
        self.now += .7
        self.assertTrue(other.commit(self.edit('third'), merge_key='material'))
        self.assertEqual(len(self.history['undo_stack']), 2)
        self.assertFalse(self.history['redo_stack'])

    def test_library_metadata_uses_library_sync_without_prompt_normalization(self):
        before = copy.deepcopy(self.state)
        category = self.state['modules'][0]['id']
        with patch('prompt_calculus_studio.document_operations.composition.prune_instances', side_effect=AssertionError('library must not normalize')):
            self.assertTrue(self.ops.commit(lambda value: materials.rename_category(value, category, 'Renamed')))
        self.assertEqual(self.events[-1], ('sync', 'library', 1))
        for key in ('uses', 'multi_output', 'draft', 'draft_base', 'output_order'):
            self.assertEqual(self.state.get(key), before.get(key), key)

    def test_flow_hooks_preserve_prepare_sync_route_and_final_projection_order(self):
        def begin():
            self.events.append('begin')
            return {'initial': self.state['items'][0]['prompt']}
        def prepare(value, context):
            self.events.append(('prepare', context['initial']))
            value['items'][0]['notes'] = 'projected'
        def complete(context):
            self.events.append('route')
            self.state['items'][0]['notes'] = 'runtime refresh'
        self.ops = self.operations(begin=begin, prepare=prepare, complete=complete)
        self.assertTrue(self.ops.commit(self.edit('edit')))
        self.assertEqual([v[0] if isinstance(v, tuple) else v for v in self.events],
                         ['begin', 'prepare', 'replace', 'sync', 'route'])
        self.assertEqual(self.history['undo_stack'][-1][1], self.project(self.state))
        self.assertEqual(self.history['last_state'], self.project(self.state))
        with patch.object(self.ops, 'complete', side_effect=ValueError('runtime failure')):
            with self.assertRaisesRegex(ValueError, 'runtime failure'):
                self.ops.commit(self.edit('accepted before runtime failure'))
        self.assertEqual(self.state['items'][0]['prompt'], 'accepted before runtime failure')
        self.assertFalse(self.notices)

    def test_history_limits_keep_latest_entry_and_cache_only_retained_identities(self):
        self.history['undo_stack'] = [({'index': i}, {'index': i+1}) for i in range(105)]
        self.ops.trim_history()
        self.assertEqual(len(self.history['undo_stack']), 100)
        self.assertEqual(self.history['undo_stack'][0][0]['index'], 5)
        large = 'x' * 16_000_000
        self.history['undo_stack'] = [(large, 1), (large, 2), (large, 3)]
        self.ops.trim_history()
        self.assertEqual(self.history['undo_stack'], [(large, 3)])
        self.assertEqual(set(self.ops._undo_sizes), {id(self.history['undo_stack'][0])})


if __name__ == '__main__':
    unittest.main()
