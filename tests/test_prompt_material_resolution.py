"""01 rev5 selection contracts, independent of widgets and real services."""
import copy
import random
import unittest
from prompt_calculus_studio import composition as comp, prompt_groups as groups
from prompt_calculus_studio import prompt_materials as pm, prompt_sources as supply


def text(name, count=1, mode='increment'):
    root = pm.new('text', name)
    root['variants'] = pm.variants(root)
    rows = [groups.new_group(f'{name}-{i}', f'{name}-{i}') for i in range(1, count+1)]
    root['variants'].update(mode=mode, start=rows[0]['id'], selected=rows[0]['id'], groups=rows)
    return root


def flow(letters='abcdef', mode='increment'):
    root = pm.new('flow', '流程')
    rows = [groups.new_group(letter, letter) for letter in letters]
    root['sequence'].update(mode=mode, start=rows[0]['id'], selected=rows[0]['id'], groups=rows)
    return root


def state_with(reader=None, canvas=None):
    state = dict(version=7, canvas_functions=dict(text_sources={}, images={}), uses={})
    keys = []
    if reader:
        keys.append(supply.add(state, root=reader))
    if canvas:
        state['uses']['canvas'] = canvas; keys.append('use:canvas')
    return state, keys


def outputs(state, plan):
    result = []
    for picks in plan['rounds']:
        projected = copy.deepcopy(state); supply.project(projected, plan, picks)
        result.append([comp.render(supply.source(projected, key)['root']) for key in plan['sources']])
    return result


class ResolutionTests(unittest.TestCase):
    def test_canvas_independent_cycles_do_not_end_or_cartesian_expand(self):
        for size in (5, 20):
            canvas = pm.new('combination', '畫布組合')
            canvas['combination']['modules'] = [flow('abcdefghij'), text('1', size)]
            values = []
            for _ in range(11):
                consumed, canvas = pm.resolve(canvas); values.append(comp.render(consumed))
            self.assertEqual(values[4], 'e, 1-5')
            self.assertEqual(values[5], f'f, 1-{1 if size == 5 else 6}')
            self.assertEqual(values[10], f'a, 1-{1 if size == 5 else 11}')

    def test_reader_cycles_canvas_once_per_batch_and_resume(self):
        state, keys = state_with(flow('abcdefghij'), text('1', 20))
        for expected in ('1-1', '1-2'):
            plan = supply.build(state, keys)
            self.assertEqual(outputs(state, plan), [[c, expected] for c in 'abcdefghij'])
            supply.reserve(state, plan)
        self.assertEqual(comp.render(state['uses']['canvas']), '1-3')

    def test_max_length_shorter_cycles_fixed_does_not_contribute(self):
        state, keys = state_with(flow('abcdefghij'))
        keys.append(supply.add(state, root=flow()))
        self.assertEqual(outputs(state, supply.build(state, keys)),
                         [[a, b] for a, b in zip('abcdefghij', 'abcdefabcd')])
        source = supply.source(state, keys[0]); source['root']['sequence']['mode'] = 'fixed'
        source['root']['sequence']['selected'] = source['root']['sequence']['groups'][2]['id']
        self.assertEqual(outputs(state, supply.build(state, keys)), [['c', b] for b in 'abcdef'])
        source['root']['sequence']['mode'] = 'random'
        self.assertEqual(len(supply.build(state, keys)['rounds']), 6)

    def test_middle_start_wraps_full_cycle_and_specified_is_exact(self):
        root = flow('abcdefghij'); seq = root['sequence']; seq['selected'] = seq['groups'][6]['id']
        state, keys = state_with(root)
        self.assertEqual(outputs(state, supply.build(state, keys)), [[c] for c in 'ghijabcdef'])
        source = supply.source(state, keys[0]); source.update(count_mode='specified', limit=13)
        self.assertEqual(len(supply.build(state, keys)['rounds']), 13)
        self.assertEqual(len(supply.build(state, keys, count=5)['rounds']), 5)

    def test_repeat_partial_boundary_resumes_remaining_repetitions(self):
        root = flow('ab'); root['sequence']['groups'][0]['repeat'] = 4
        root['sequence']['groups'][1]['repeat'] = 3
        state, keys = state_with(root)
        plan = supply.build(state, keys, count=5)
        self.assertEqual(outputs(state, plan), [[c] for c in 'aaaab'])
        supply.reserve(state, plan)
        self.assertEqual(outputs(state, supply.build(state, keys, count=3)), [[c] for c in 'bba'])

    def test_only_persisted_prefix_consumes_and_replaying_commit_is_idempotent(self):
        state, keys = state_with(flow('abcdefghij'))
        original = copy.deepcopy(state); plan = supply.build(state, keys)
        self.assertEqual(state, original)
        supply.reserve(state, plan, through=6)
        self.assertEqual(comp.render(supply.source(state, keys[0])['root']), 'g')
        saved = copy.deepcopy(state); supply.reserve(state, plan, through=6)
        self.assertEqual(state, saved)
        # Cancelled saved rows never return their consumed selections.
        self.assertEqual(outputs(state, supply.build(state, keys, count=1)), [['g']])

    def test_unvisited_groups_pause_inner_cursor_fixed_outer_still_advances_inner(self):
        root = flow('ab', 'fixed'); seq = root['sequence']
        seq['groups'][0].update(input='modules', modules=[text('a', 4)])
        seq['groups'][1].update(input='modules', modules=[text('b', 4)])
        consumed, root = pm.resolve(root); self.assertEqual(comp.render(consumed), 'a-1')
        consumed, root = pm.resolve(root); self.assertEqual(comp.render(consumed), 'a-2')
        root['sequence']['selected'] = seq['groups'][1]['id']; root.pop('selection_cursor', None)
        consumed, root = pm.resolve(root); self.assertEqual(comp.render(consumed), 'b-1')
        root['sequence']['selected'] = seq['groups'][0]['id']; root.pop('selection_cursor', None)
        consumed, root = pm.resolve(root); self.assertEqual(comp.render(consumed), 'a-3')

    def test_random_without_replacement_shuffles_items_then_repeats(self):
        root = flow('abc', 'random'); seq = root['sequence']; seq['random_repeats'] = False
        seq['groups'][1]['repeat'] = 3
        values = []; rng = random.Random(123)
        for _ in range(10):
            current, root = pm.resolve(root, rng=rng); values.append(comp.render(current))
        for cycle in (values[:5], values[5:]):
            self.assertEqual(sorted(cycle), ['a', 'b', 'b', 'b', 'c'])
            at = cycle.index('b'); self.assertEqual(cycle[at:at+3], ['b']*3)
        comp.validate_node(root)

    def test_all_fixed_auto_ten_and_internal_variants_keep_changing(self):
        root = flow('a', 'fixed'); root['sequence']['groups'][0].update(input='modules', modules=[text('1', 3)])
        state, keys = state_with(root)
        self.assertEqual(outputs(state, supply.build(state, keys)), [[f'1-{i%3+1}'] for i in range(10)])

    def test_same_placement_fanout_once_two_copies_independent_and_library_unchanged(self):
        original = text('1', 3); before = copy.deepcopy(original)
        state, keys = state_with(canvas=copy.deepcopy(original))
        state['uses']['other'] = comp.clone_node(original)
        plan = supply.build(state, [*keys, *keys], count=1)
        supply.reserve(state, plan)
        self.assertEqual(comp.render(state['uses']['canvas']), '1-2')
        self.assertEqual(comp.render(state['uses']['other']), '1-1')
        self.assertEqual(original, before)

    def test_combination_dedup_scoped_exact_weighted_and_prose_preserved(self):
        root = pm.new('combination', '組合'); group = root['combination']
        group['modules'] = [comp.node('a', 'park, (park:1.2), park'), comp.node('b', 'park, red')]
        group['deduplicate'] = True
        self.assertEqual(comp.render(root), 'park, (park:1.2), red')
        parent = comp.node('both', children=[root, comp.clone_node(root)])
        self.assertEqual(comp.render(parent), 'park, (park:1.2), red, park, (park:1.2), red')
        group['input'] = 'text'; group['text'] = 'park, park'; group['content_kind'] = 'text'
        self.assertEqual(comp.render(root), 'park, park')
        comp.validate_node(root)

    def test_drafts_empty_override_and_inactive_input_survive(self):
        root = flow('a'); group = root['sequence']['groups'][0]
        group.update(input='modules', modules=[text('1', 3)], text='inactive text')
        root['round_drafts'] = {group['id']: ''}
        consumed, following = pm.resolve(root)
        self.assertEqual(comp.render(consumed), '')
        self.assertEqual(comp.render(following), '1-1')
        self.assertEqual(following['sequence']['groups'][0]['text'], 'inactive text')

    def test_edits_after_freeze_change_neither_saved_values_nor_new_definition_cursor(self):
        state, keys = state_with(flow()); plan = supply.build(state, keys)
        supply.source(state, keys[0])['root']['sequence']['groups'][0]['text'] = 'edited'
        before = copy.deepcopy(state); supply.reserve(state, plan)
        self.assertEqual(state, before)
        self.assertEqual(outputs(state, plan), [[c] for c in 'abcdef'])


if __name__ == '__main__': unittest.main()
