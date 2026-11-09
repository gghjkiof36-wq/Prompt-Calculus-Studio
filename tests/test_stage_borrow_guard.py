"""Borrow ownership, positive controls and the bounded acceptance rollback."""
import copy
import inspect
import json
import operator
import random
import sqlite3
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import _borrow_guard as guard
from prompt_calculus_studio import stage_context as sc, workspace_scene as ws
from prompt_calculus_studio import prompt_materials as materials, prompt_sources as sources
from prompt_calculus_studio.stage_runner import StageRunner
from prompt_calculus_studio.stage_store import StageStore
from test_stage_image_snapshot import document as source_document
from test_prompt_material_resolution import flow, text


def document(readers):
    state = source_document(readers)
    state['generation'] = dict(mode='txt2img', profiles=[], chosen={}, source=None)
    return state


class BorrowContractTests(unittest.TestCase):
    def runner(self, state):
        db = sqlite3.connect(':memory:')
        self.addCleanup(db.close)
        return SimpleNamespace(store=StageStore(db), window=SimpleNamespace(state=state))

    def test_base_preserves_copy_depth_and_no_scene_identity(self):
        # Identity belongs to the product contract, not the guard's detached copy.
        base = inspect.unwrap(sc.borrowed_base)
        live = document({})
        for scene in (None, {}, ws.scene(live)):
            with self.subTest(scene=scene is not None):
                actual = base(live, scene)
                expected = dict(live)
                if scene is not None:
                    ws.load(expected, scene)
                self.assertEqual(actual, expected)
                self.assertIsNot(actual, live)
                for key in live.keys() & actual.keys():
                    self.assertEqual(actual[key] is live[key], expected[key] is live[key], key)
        self.assertIs(base(live, copy_top=False), live)
        self.assertIs(base(live)['uses'], live['uses'])
        frozen = ws.scene(live)
        loaded = base(live, frozen, copy_top=False)
        loaded['uses']['a']['prompt'] = 'private'
        self.assertNotEqual(loaded['uses'], live['uses'])
        self.assertNotEqual(loaded['uses'], frozen['uses'])
        self.assertIs(loaded['generation']['profiles'], live['generation']['profiles'])

    def test_positive_control_blocks_nested_product_write_before_live_changes(self):
        live = document({})
        before = copy.deepcopy(live)
        with guard.installed():
            view = sc.execution_state(live)
            root = view['uses']['a']
            with guard.expect_violation('view/uses/a'):
                materials.apply_progress(root, {root['id']: {'prompt': 'must not escape'}})
            event = guard.events[-1]
            self.assertTrue(any('prompt_materials.py:' in site and ':apply_progress' in site for site in event['site']))
            self.assertIn('product call site:', str(guard.BorrowViolation(event)))
            self.assertEqual(live, before)
            self.assertEqual(view['uses']['a'], before['uses']['a'])

    def test_base_guards_library_and_generation_profiles_but_loaded_scene_is_owned(self):
        live = document({})
        live['library_probe'] = {'nested': [1]}
        live['generation']['profiles'] = [{'id': 'profile'}]
        before = copy.deepcopy(live)
        with guard.installed():
            for scene in (None, ws.scene(live)):
                base = sc.borrowed_base(live, scene)
                with guard.expect_violation('base/library_probe/nested'):
                    base['library_probe']['nested'].append(2)
                with guard.expect_violation('base/generation/profiles'):
                    base['generation']['profiles'].clear()
                if scene is not None:
                    base['uses']['a']['prompt'] = 'owned scene edit'
            direct = sc.borrowed_base(live, copy_top=False)
            with guard.expect_violation('base'):
                direct['new'] = 1
        self.assertEqual(live, before)

    def test_resolve_never_mutates_input_or_frozen_plan_definition(self):
        nested = text('parent', 2)
        nested['children'] = [text('child', 3), text('leaf', 2, 'decrement')]
        combination = materials.new('combination', 'combo')
        combination['combination'].update(input='modules', modules=[nested, flow('abc')])
        disabled = copy.deepcopy(nested)
        disabled['enabled'] = False
        for root in (nested, flow('abc'), combination, disabled):
            with self.subTest(kind=root.get('material_type')):
                before = copy.deepcopy(root)
                frozen = guard.watch(root, ('plan', 'sources', 'root'))
                current, following = materials.resolve(frozen, rng=random.Random(7))
                self.assertEqual(root, before)
                self.assertEqual(frozen, before)
                current['name'] = 'current only'
                following['name'] = 'following only'
                self.assertEqual(root, before)
                self.assertNotEqual(current, following)
        state = document({})
        definition = flow('abc')
        key = sources.add(state, root=definition)
        original = copy.deepcopy(state)
        plan = sources.build(state, [key], count=3)
        self.assertEqual(plan['sources'][key]['root'], definition)
        self.assertEqual(state, original)

    def test_child_saved_items_are_read_only_but_new_keys_are_private(self):
        parent = dict(results={}, sources={}, scene={'uses': {'a': {'prompt': 'scene'}}},
                      saved={'values': {'k': {'value': 'original', 'origin': {'ids': ['a']}}}})
        before = copy.deepcopy(parent)
        with guard.installed():
            child = sc.copy_context(parent)
            child['saved']['values']['new'] = {'value': 'new'}
            child['saved']['values']['new']['value'] = 'owned edit'
            with guard.expect_violation('saved/values/k'):
                child['saved']['values']['k']['value'] = 'forbidden'
            with guard.expect_violation('saved/values/k/origin/ids'):
                child['saved']['values']['k']['origin']['ids'].append('b')
            with guard.expect_violation('ctx/scene/uses/a'):
                child['scene']['uses']['a']['prompt'] = 'forbidden'
            self.assertEqual(json.loads(json.dumps(child))['saved']['values']['new'], {'value': 'owned edit'})
        self.assertEqual(parent, before)

    def test_merge_saved_guards_both_inputs_and_keeps_owned_maps_writable(self):
        outer = {'values': {'a': {'value': 'outer'}}}
        inner = {'values': {'b': {'value': 'inner'}}}
        with guard.installed():
            merged = sc.merge_saved(outer, inner, borrow=True)
            for key in ('a', 'b'):
                with guard.expect_violation('saved/values/' + key):
                    merged['values'][key].update(value='bad')
            merged['values']['c'] = {'value': 'new'}
            owned = sc.merge_saved(outer, inner)
            owned['values']['a']['value'] = 'independent'
        self.assertEqual(outer['values']['a']['value'], 'outer')
        self.assertEqual(inner['values']['b']['value'], 'inner')

    def test_capture_borrow_guards_canvas_and_root_records_only(self):
        live = document({})
        output = live['multi_output']['current_output']
        plan = {'stages': {'s': {'bindings': [{'key': 'binding', 'source': output, 'kind': 'clip'}],
                                  'scheduler': None, 'auto': None}}}
        live['multi_output']['stages']['s'] = {'parameters': None}
        before = copy.deepcopy(live)
        with guard.installed():
            saved = sc.capture(live, plan, ['s'], '.', borrow=True)
            records = saved['values']['binding']['origin']['canvas_records']
            cid, record = next(iter(records.items()))
            with guard.expect_violation('capture/binding/canvas/' + cid):
                record['canvas']['members'].clear()
            with guard.expect_violation('capture/binding/roots/a'):
                record['roots']['a']['prompt'] = 'bad'
            owned = sc.capture(live, plan, ['s'], '.')
            owned['values']['binding']['origin']['canvas_records'][cid]['roots']['a']['prompt'] = 'private'
        self.assertEqual(live, before)

    def test_scene_and_readonly_load_cover_renamed_generation_fields(self):
        live = document({})
        live['generation']['chosen'] = {'nested': [1]}
        live['generation']['profiles'] = [{'id': 'p'}]
        before = copy.deepcopy(live)
        with guard.installed():
            scene = sc.execution_scene(live)
            with guard.expect_violation('scene/uses/a'):
                scene['uses']['a']['prompt'] = 'bad'
            with guard.expect_violation('scene/generation_local/chosen/nested'):
                scene['generation_local']['chosen']['nested'].append(2)
            plain_scene = ws.scene(live)
            target = dict(live)
            ws.load(target, plain_scene, readonly=True)
            with guard.expect_violation('scene_validate/uses/a'):
                target['uses']['a']['prompt'] = 'bad'
            with guard.expect_violation('scene_validate/generation/chosen/nested'):
                target['generation']['chosen']['nested'].append(2)
            with guard.expect_violation('scene_validate/generation/profiles'):
                target['generation']['profiles'].clear()
        self.assertEqual(live, before)

    def test_accepting_restores_all_documented_progress_branches(self):
        state = document({'image': {'index': 0}})
        supplier = sources.add(state, root=flow('ab'))
        before = copy.deepcopy(state)
        runner = self.runner(state)
        with self.assertRaisesRegex(sqlite3.OperationalError, 'failed acceptance'):
            with StageRunner.accepting(runner, suppliers=[supplier]):
                frozen = sources.build(state, [supplier], count=1)
                sources.reserve(state, frozen)
                state['uses']['derived'] = {'prompt': 'new'}
                state['text_positions']['derived'] = [1, 2]
                canvas = next(iter(state['multi_output']['canvases'].values()))
                canvas['members'].append('derived')
                canvas['name'] = 'unaccepted'
                state['canvas_functions']['images']['image']['index'] = 1
                state['generation']['mode'] = 'unaccepted'
                state['multi_output']['outputs'][state['multi_output']['current_output']]['draft'] = 'unaccepted'
                ws.capture(state)
                runner.store.add('entry', state['workspace'], status='waiting')
                raise sqlite3.OperationalError('failed acceptance')
        self.assertEqual(state, before)
        self.assertEqual(runner.store.rows('entry'), [])

    def test_nested_write_is_blocked_inside_accepting_before_rollback(self):
        state = document({})
        before = copy.deepcopy(state)
        runner = self.runner(state)
        with guard.installed(), guard.expect_violation('view/uses/a'):
            with StageRunner.accepting(runner, suppliers=[]):
                view = sc.execution_state(state)
                try:
                    view['uses']['a']['prompt'] = 'not recoverable by shallow backup'
                except guard.BorrowViolation:
                    # This assertion runs before accepting gets to its except.
                    self.assertEqual(state, before)
                    self.assertEqual(view['uses']['a']['prompt'], before['uses']['a']['prompt'])
                    raise
        self.assertEqual(state, before)

    def test_known_limit_unguarded_nested_write_survives_shallow_rollback(self):
        state = document({})
        runner = self.runner(state)
        # Document the limitation on synthetic data, even in a guarded suite.
        raw_execution_state = inspect.unwrap(sc.execution_state)
        with patch.object(sc, 'execution_state', raw_execution_state):
            with self.assertRaisesRegex(ValueError, 'rollback'):
                with StageRunner.accepting(runner, suppliers=[]):
                    raw_execution_state(state)['uses']['a']['prompt'] = 'leaked'
                    raise ValueError('rollback')
        self.assertEqual(state['uses']['a']['prompt'], 'leaked')

    def test_mutating_container_apis_fail_before_change_and_deepcopy_is_owned(self):
        operations = [
            ({'a': 1}, lambda d: operator.setitem(d, 'a', 2)),
            ({'a': 1}, lambda d: operator.delitem(d, 'a')),
            ({'a': 1}, lambda d: d.pop('a')),
            ({'a': 1}, lambda d: d.popitem()),
            ({'a': 1}, lambda d: d.clear()),
            ({'a': 1}, lambda d: d.update(a=2)),
            ({'a': 1}, lambda d: d.setdefault('b', 2)),
            ({'a': 1}, lambda d: operator.ior(d, {'b': 2})),
            ([1, 2], lambda v: operator.setitem(v, slice(None), [3])),
            ([1, 2], lambda v: operator.delitem(v, slice(None))),
            ([1, 2], lambda v: v.append(3)),
            ([1, 2], lambda v: v.extend([3])),
            ([1, 2], lambda v: v.insert(0, 3)),
            ([1, 2], lambda v: v.remove(1)),
            ([1, 2], lambda v: v.pop()),
            ([1, 2], lambda v: v.clear()),
            ([1, 2], lambda v: v.sort()),
            ([1, 2], lambda v: v.reverse()),
            ([1, 2], lambda v: operator.iadd(v, [3])),
            ([1, 2], lambda v: operator.imul(v, 2)),
        ]
        for value, mutate in operations:
            with self.subTest(api=mutate):
                watched = guard.watch(value, ('api',))
                with guard.expect_violation('api'):
                    mutate(watched)
                self.assertEqual(watched, value)
        owned = copy.deepcopy(guard.watch({'nested': [1]}, ('copy',)))
        owned['nested'].append(2)
        self.assertEqual(owned, {'nested': [1, 2]})

    def test_install_is_idempotent_and_patches_runner_aliases(self):
        from prompt_calculus_studio import stage_runner as sr
        with guard.installed():
            original = sc.execution_state
            guard.install()
            self.assertIs(sc.execution_state, original)
            for name in ('execution_state', 'execution_scene', 'copy_context', 'capture', 'merge_saved', 'borrowed_base'):
                self.assertIs(getattr(sr, name), getattr(sc, name))


if __name__ == '__main__':
    unittest.main()
