"""Variant-scoped rules follow frozen selections and preserve common exclusions."""
import copy
import json
from pathlib import Path
import random
import tempfile
import unittest

from prompt_calculus_studio import composition as comp, core, content_history, materials
from prompt_calculus_studio import prompt_groups as groups, prompt_materials as pm, prompt_sources as supply
from prompt_calculus_studio import multi_output, snapshots
from prompt_calculus_studio.canvas_exclusions import affected_nodes
from prompt_calculus_studio.exclusions import effective_excludes
from prompt_calculus_studio.local_documents import category, io


def variant_root(mode='fixed'):
    root = pm.new('text', 'Rule source')
    root['excludes'] = ['shared_block']
    root['variants'] = pm.variants(root)
    first = groups.new_group('A', 'source a')
    second = groups.new_group('B', 'source b')
    root['variants'].update(mode=mode, groups=[first, second],
                            selected=first['id'], start=first['id'])
    pm.set_variant_excludes(root['variants'], first['id'], ['red eyes'])
    pm.set_variant_excludes(root['variants'], second['id'], ['blue eyes'])
    return root


def uses_state(root=None):
    state = core.initial_state()
    state.update(version=7, selections={}, uses={
        'rule': root or variant_root(),
        'target': comp.node('Target', 'shared_block, red eyes, blue eyes, keep')})
    state['settings']['separate_selections'] = False
    return state


def library_state():
    state = uses_state()
    root = state['uses']['rule']
    rule, target = state['items'][:2]
    for module in state['modules']:
        if module['id'] in (rule['module'], target['module']): module['mode'] = 'multiple'
    rule.update(composition=root, prompt=comp.render(root), excludes=list(root['excludes']), material_type='text')
    target.update(prompt=state['uses']['target']['prompt'], excludes=[])
    state.update(uses={}, selections={})
    for item in (rule, target):
        state['selections'].setdefault(item['module'], []).append(item['id'])
    return state, rule, target


class VariantExclusionTests(unittest.TestCase):
    def test_legacy_common_rules_keep_v1_and_do_not_mutate(self):
        root = pm.new('text', 'Legacy')
        root.update(prompt='legacy', excludes=['shared_block'])
        root['variants'] = pm.variants(root)
        before = copy.deepcopy(root)
        self.assertIs(pm.validate_variants(root['variants']), root['variants'])
        self.assertEqual(effective_excludes(root), ('shared_block',))
        self.assertEqual(core.build_prompt(uses_state(root)), 'legacy, red eyes, blue eyes, keep')
        self.assertEqual(root, before)
        pm.set_variant_excludes(root['variants'], root['variants']['selected'], [])
        self.assertEqual(root, before)

    def test_setter_is_atomic_and_keeps_independent_drafts(self):
        root = variant_root(); seq = root['variants']; first, second = seq['groups']
        first['name'] = ''  # A user may still be editing another field.
        values = ['  red eyes  ', 'red eyes', 'smile']
        pm.set_variant_excludes(seq, first['id'], values)
        values.append('later')
        self.assertEqual(first['excludes'], ['red eyes', 'smile'])
        self.assertEqual(second['excludes'], ['blue eyes'])
        self.assertEqual(root['excludes'], ['shared_block'])
        pm.set_variant_excludes(seq, first['id'], [])
        self.assertNotIn('excludes', first)
        self.assertEqual(seq['version'], 2)
        self.assertEqual(first['name'], '')
        with self.assertRaises(ValueError): pm.validate_variants(seq)
        for ident, invalid in [('missing', []), (first['id'], ['']), (first['id'], 'red'),
                               (first['id'], [False]), (first['id'], None)]:
            with self.subTest(ident=ident, invalid=invalid):
                before = copy.deepcopy(seq)
                with self.assertRaises(ValueError): pm.set_variant_excludes(seq, ident, invalid)
                self.assertEqual(seq, before)

    def test_optional_fields_are_strict_and_underdeclared_v1_is_rejected(self):
        for invalid in (None, 'red', [''], [' '], [False], [4], {}):
            root = variant_root(); root['variants']['groups'][1]['excludes'] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ValueError): comp.validate_node(root)
        root = variant_root(); root['variants']['version'] = 1
        with self.assertRaisesRegex(ValueError, '第 2 版'): comp.validate_node(root)
        for invalid in (True, 0, 3, '2'):
            root['variants']['version'] = invalid
            with self.subTest(version=invalid), self.assertRaises(ValueError): comp.validate_node(root)

    def test_v2_is_only_accepted_for_text_variants(self):
        seq = variant_root()['variants']
        with self.assertRaises(ValueError): groups.validate(seq)
        with self.assertRaises(ValueError): groups.cursor(seq)
        for field, kind in [('sequence', 'flow'), ('combination', 'combination')]:
            root = pm.new(kind, 'Wrong owner')
            root[field] = copy.deepcopy(seq if field == 'sequence' else seq['groups'][0])
            with self.subTest(field=field), self.assertRaises(ValueError): comp.validate_node(root)
        root = variant_root(); group = root['variants']['groups'][0]
        group.update(input='modules', modules=[comp.node('Wrong content', 'text')])
        with self.assertRaises(ValueError): comp.validate_node(root)
        seq = copy.deepcopy(seq); seq['version'] = 1
        root = pm.new('flow', 'Flow'); root['sequence'] = seq
        with self.assertRaisesRegex(ValueError, '只有文字變體'): comp.validate_node(root)

    def test_list_rules_affect_other_selected_materials_and_switch_by_variant(self):
        state, rule, target = library_state(); before = copy.deepcopy(state)
        text, affected = core.compose_details(state)
        self.assertEqual(text, 'source a, blue eyes, keep')
        self.assertEqual(affected[target['id']]['tags'], ['shared_block', 'red eyes'])
        self.assertEqual(state, before)
        seq = rule['composition']['variants']; seq['selected'] = seq['groups'][1]['id']
        self.assertEqual(core.build_prompt(state), 'source b, red eyes, keep')
        self.assertEqual(target['prompt'], 'shared_block, red eyes, blue eyes, keep')
        self.assertEqual(rule['excludes'], ['shared_block'])

    def test_canvas_preview_matches_compiler_and_does_not_cross_canvas(self):
        state = multi_output.migrate(uses_state())
        first = next(iter(state['multi_output']['canvases']))
        state['uses']['elsewhere'] = comp.node('Elsewhere', 'shared_block, red eyes')
        state['multi_output']['canvases']['other'] = multi_output.new_canvas('Other')
        multi_output.assign(state, 'elsewhere', 'other')
        before = copy.deepcopy(state)
        self.assertEqual(core.build_prompt(multi_output.canvas_projection(state, first)), 'source a, blue eyes, keep')
        self.assertEqual(core.build_prompt(multi_output.canvas_projection(state, 'other')), 'shared_block, red eyes')
        affected = affected_nodes(state)
        self.assertEqual(affected['target']['tags'], ['shared_block', 'red eyes'])
        self.assertNotIn('elsewhere', affected)
        self.assertEqual(state, before)

    def test_disabled_or_overlaid_source_does_not_emit_hidden_variant_rules(self):
        state = uses_state(); root = state['uses']['rule']
        root['enabled'] = False
        self.assertEqual(core.build_prompt(state), 'shared_block, red eyes, blue eyes, keep')
        root['enabled'] = True
        root['overlays'] = [comp.node('Replacement', 'replacement', excludes=['keep'])]
        self.assertEqual(core.build_prompt(state), 'replacement, shared_block, red eyes, blue eyes')
        root['overlays'][0]['enabled'] = False
        self.assertEqual(core.build_prompt(state), 'source a, blue eyes, keep')

    def test_only_effective_nested_flow_group_contributes_exclusions(self):
        root = pm.new('flow', 'Flow'); seq = root['sequence']
        one = groups.new_group('Used', modules=[variant_root()])
        unused = variant_root(); unused['excludes'] = ['keep']
        two = groups.new_group('Unused', modules=[unused])
        seq.update(groups=[one, two], selected=one['id'], start=one['id'])
        self.assertEqual(core.build_prompt(uses_state(root)), 'source a, blue eyes, keep')
        seq['selected'] = two['id']
        self.assertEqual(core.build_prompt(uses_state(root)), 'source a, shared_block, blue eyes')

    def test_increment_decrement_and_repeats_use_consumed_variant(self):
        for mode in ('increment', 'decrement'):
            root = variant_root(mode); root['variants']['groups'][0]['repeat'] = 2
            outputs = []
            for _ in range(4):
                current, root = pm.resolve(root)
                outputs.append(core.build_prompt(uses_state(current)))
            with self.subTest(mode=mode):
                self.assertEqual(outputs, ['source a, blue eyes, keep'] * 2 +
                                 ['source b, red eyes, keep', 'source a, blue eyes, keep'])
                comp.validate_node(root)

    def test_random_selection_ignores_original_ui_selected_variant(self):
        class ChooseSecond:
            def choice(self, rows): return rows[1]
        root = variant_root('random'); before = copy.deepcopy(root)
        current, following = pm.resolve(root, rng=ChooseSecond())
        self.assertEqual(core.build_prompt(uses_state(current)), 'source b, red eyes, keep')
        self.assertEqual(root, before)
        self.assertEqual(current['variants']['selected'], root['variants']['groups'][1]['id'])
        comp.validate_node(following)

    def test_random_without_replacement_retains_each_variants_rules(self):
        root = variant_root('random'); root['variants']['random_repeats'] = False
        rng = random.Random(42); results = []
        for _ in range(4):
            current, root = pm.resolve(root, rng=rng)
            results.append(core.build_prompt(uses_state(current)))
        expected = ['source a, blue eyes, keep', 'source b, red eyes, keep']
        self.assertEqual(sorted(results[:2]), sorted(expected))
        self.assertEqual(sorted(results[2:]), sorted(expected))

    def test_frozen_supply_plan_survives_edits_retry_and_json_roundtrip(self):
        outer = pm.new('flow', 'Reader'); group = outer['sequence']['groups'][0]
        group.update(input='modules', modules=[variant_root('increment')])
        state = uses_state(); key = supply.add(state, root=outer)
        plan = json.loads(json.dumps(supply.build(state, [key], count=3)))
        live = supply.source(state, key)['root']['sequence']['groups'][0]['modules'][0]
        for variant in live['variants']['groups']:
            pm.set_variant_excludes(live['variants'], variant['id'], ['keep'])
            variant['text'] = 'edited later'
        live['variants']['selected'] = live['variants']['groups'][1]['id']
        before = copy.deepcopy(state)
        def replay():
            result = []
            for picks in plan['rounds']:
                projected = copy.deepcopy(state); supply.project(projected, plan, picks)
                projected['uses']['rule'] = supply.source(projected, key)['root']
                result.append(core.build_prompt(projected))
            return result
        expected = ['source a, blue eyes, keep', 'source b, red eyes, keep', 'source a, blue eyes, keep']
        self.assertEqual(replay(), expected); self.assertEqual(replay(), expected)
        supply.reserve(state, plan)
        self.assertEqual(state, before)  # A changed definition cannot consume the old cursor.

    def test_clone_and_duplicate_do_not_alias_local_or_common_rules(self):
        root = variant_root(); clone = comp.clone_node(root)
        self.assertNotEqual(clone['id'], root['id'])
        clone['excludes'].append('copy only')
        clone['variants']['groups'][0]['excludes'].append('copy local')
        duplicate = groups.duplicate(root['variants'], root['variants']['groups'][0]['id'])
        duplicate['excludes'].append('duplicate local')
        self.assertEqual(root['excludes'], ['shared_block'])
        self.assertEqual(root['variants']['groups'][0]['excludes'], ['red eyes'])
        self.assertNotEqual(duplicate['id'], root['variants']['groups'][0]['id'])

    def test_snapshot_roundtrip_retains_inactive_variant_and_frozen_text(self):
        state = uses_state(); snapshot = snapshots.make_snapshot(state)
        snapshot = json.loads(json.dumps(snapshot)); snapshots.validate_snapshot(snapshot)
        state['uses']['rule']['variants']['groups'][0]['excludes'].append('keep')
        restored = snapshots.restore_snapshot(core.initial_state(), snapshot)
        self.assertEqual(core.build_prompt(restored), 'source a, blue eyes, keep')
        root = next(value for value in restored['uses'].values() if value.get('variants'))
        self.assertEqual(root['variants']['groups'][1]['excludes'], ['blue eyes'])
        self.assertEqual(snapshot['schema_version'], 7)
        self.assertEqual(root['variants']['version'], 2)

    def test_category_save_close_reopen_preserves_optional_variant_rules(self):
        state, rule, _ = library_state()
        document = category.from_state(state, rule['module'])
        with tempfile.TemporaryDirectory(prefix='pcs-variant-exclusions-') as folder:
            root = Path(folder)
            result = io.create(root, 'Synthetic.pcs-category', document)
            self.assertEqual(result.status, 'saved', result.error)
            loaded = io.load(root, 'Synthetic.pcs-category')
            saved = next(item for item in loaded.document['payload']['items'] if item['id'] == rule['id'])
            self.assertEqual(saved['composition']['variants'], rule['composition']['variants'])
            self.assertEqual(saved['excludes'], ['shared_block'])

    def test_history_and_signature_capture_only_variant_rule_edit(self):
        state, rule, _ = library_state(); before = copy.deepcopy(state)
        old_fingerprint = materials.fingerprint(rule)
        old_signature = pm.signature(rule['composition'])
        seq = rule['composition']['variants']; pm.set_variant_excludes(seq, seq['selected'], ['keep'])
        self.assertNotEqual(materials.fingerprint(rule), old_fingerprint)
        self.assertNotEqual(pm.signature(rule['composition']), old_signature)
        self.assertTrue(content_history.record(state, before, copy.deepcopy(state), 'Variant exclusion'))
        entry = state['content_history']['entries'][-1]
        content_history.restore(state, entry['id'], rule['id'], previous=True)
        restored = materials.item(state, rule['id'])
        self.assertEqual(restored['composition']['variants'], before['items'][0]['composition']['variants'])
        self.assertEqual(restored['excludes'], ['shared_block'])

    def test_exact_tag_filtering_and_prose_protection_are_unchanged(self):
        state = uses_state(); state['uses']['target']['prompt'] = '(RED_EYES, blue eyes:1.2), red eyeshadow, keep'
        state['uses']['prose'] = comp.node('Prose', 'red eyes, shared_block are words here.')
        state['uses']['prose']['content_kind'] = 'text'
        result = core.build_prompt(state)
        self.assertIn('(blue eyes:1.2), red eyeshadow, keep', result)
        self.assertIn('red eyes, shared_block are words here.', result)
        self.assertNotIn('RED_EYES', result)


if __name__ == '__main__': unittest.main()
