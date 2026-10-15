import copy
import random
import unittest

from prompt_calculus_studio import composition as comp, prompt_groups as groups
from prompt_calculus_studio.prompt_tags import apply, tokens


def sample():
    root = comp.node('旅行')
    rows = [groups.new_group(n, t) for n, t in [('A', 'red, (hair:1.2)'), ('B', 'blue'), ('C', 'green')]]
    for row, count in zip(rows, [2, 1, 3]):
        row['repeat'] = count
    root['sequence'] = dict(version=1, mode='increment', start=rows[0]['id'], selected=rows[0]['id'], groups=rows)
    return root


class Groups(unittest.TestCase):
    def test_six_reservations_retry_does_not_touch_definition(self):
        root = sample(); before = copy.deepcopy(root); seq = root['sequence']; pos = groups.cursor(seq)
        plans = []
        for _ in range(groups.remaining(seq)):
            record, pos = groups.take(seq, pos)
            plans.append(groups.resolve_round(root, record, str(len(plans))))
        self.assertEqual([comp.render(r) for r in plans], ['red, (hair:1.2)'] * 2 + ['blue'] + ['green'] * 3)
        root['sequence']['groups'][0]['text'] = 'edited later'
        self.assertEqual(comp.render(plans[0]), 'red, (hair:1.2)')
        self.assertEqual(before['sequence'], seq | {'groups': before['sequence']['groups']})

    def test_delete_start_requires_choice_reorder_keeps_identity(self):
        seq = sample()['sequence']; start = seq['start']; replacement = seq['groups'][1]['id']
        seq['groups'].reverse(); self.assertEqual(seq['start'], start)
        with self.assertRaises(ValueError): groups.remove(seq, start)
        self.assertEqual(len(seq['groups']), 3)
        groups.remove(seq, start, replacement); self.assertEqual(seq['start'], replacement)

    def test_copy_clones_inactive_drafts_and_keeps_library_independent(self):
        root = sample(); row = root['sequence']['groups'][0]
        row['modules'] = [comp.node('原件', 'old')]
        cloned = comp.clone_node(root)
        self.assertNotEqual(row['modules'][0]['id'], cloned['sequence']['groups'][0]['modules'][0]['id'])
        cloned['sequence']['groups'][0]['modules'][0]['prompt'] = 'local'
        self.assertEqual(row['modules'][0]['prompt'], 'old')

    def test_current_round_override_not_other_repeat(self):
        root = sample(); seq = root['sequence']; first, pos = groups.take(seq, groups.cursor(seq))
        root.update(round_id='draft', round_override=dict(round='draft', group=first['group'], text='this round'))
        one = groups.resolve_round(root, first, '1', use_override=True)
        second, _ = groups.take(seq, pos); two = groups.resolve_round(root, second, '2')
        self.assertEqual(comp.render(one), 'this round'); self.assertEqual(comp.render(two), 'red, (hair:1.2)')
        comp.validate_node(one)

    def test_zip_expanded_counts_and_fixed_random_limits(self):
        seq = sample()['sequence']; self.assertEqual(groups.paired_length([20, groups.remaining(seq)]), 20)
        seq['groups'][0]['repeat'] = 26
        self.assertEqual(groups.paired_length([20, groups.remaining(seq)]), 30)
        seq['mode'] = 'fixed'; self.assertEqual(groups.paired_length([20, groups.remaining(seq)]), 20)
        seq['mode'] = 'random'
        self.assertEqual(groups.paired_length([groups.remaining(seq)]), 10)
        self.assertEqual(groups.paired_length([None], 7), 7)

    def test_decrement_remaining_and_random_repeat(self):
        seq = sample()['sequence']; seq.update(mode='decrement', start=seq['groups'][1]['id'])
        self.assertEqual(groups.remaining(seq), 6)
        seq['mode'] = 'random'; pos = groups.cursor(seq); rng = random.Random(4)
        record, pos = groups.take(seq, pos, rng)
        group = next(g for g in seq['groups'] if g['id'] == record['group'])
        for _ in range(group['repeat'] - 1):
            again, pos = groups.take(seq, pos, rng); self.assertEqual(again['group'], record['group'])
        self.assertEqual(pos['repeat'], 0)

    def test_conversion_preserves_legacy_render_and_child_drafts(self):
        root = comp.node('舊素材', 'red', children=[comp.node('子項', 'blue')]); root['weight'] = 15
        original = comp.render(root); root['sequence'] = groups.from_node(root)
        comp.validate_node(root); self.assertEqual(comp.render(root), original)
        seq = root['sequence']; seq['groups'][0]['input'] = 'text'
        self.assertEqual(comp.render(root), '(red:1.5)')
        seq['groups'][0]['input'] = 'modules'; self.assertEqual(comp.render(root), original)

    def test_inactive_duplicate_node_id_is_invalid(self):
        root = sample(); child = comp.node('x', 'x')
        for row in root['sequence']['groups'][:2]: row['modules'] = [copy.deepcopy(child)]
        with self.assertRaises(ValueError): comp.validate_node(root)

    def test_prose_is_not_tag_filtered_or_weight_wrapped(self):
        root = comp.node('root', '', children=[comp.node('a', 'She ran, then stopped, then stopped.')])
        root['children'][0]['content_kind'] = 'text'; root.update(deduplicate=True, weight=20)
        self.assertEqual(comp.render(root), 'She ran, then stopped, then stopped.')


class ExactTags(unittest.TestCase):
    def test_escaped_nested_weighted_contexts(self):
        source = r'red, red, (red:1.2), (red:1.3), (red, blue:1.2), red\,blue, "a,b"'
        result = apply(source, deduplicate=True)
        self.assertEqual(result, r'red, (red:1.2), (red:1.3), (red, blue:1.2), red\,blue, "a,b"')
        self.assertEqual(apply(result, ['red']), r'(red:1.2), (red:1.3), (red, blue:1.2), red\,blue, "a,b"')

    def test_disable_is_local_and_reversible(self):
        child = comp.node('x', 'red, blue'); child['disabled_tags'] = ['red']
        root = comp.node('root', 'red', children=[child])
        self.assertEqual(comp.render(root), 'red, blue')
        child['disabled_tags'] = []; self.assertEqual(comp.render(root), 'red, red, blue')
        root['deduplicate'] = True; self.assertEqual(comp.render(root), 'red, blue')
        self.assertEqual(child['prompt'], 'red, blue')

    def test_unbalanced_text_stays_opaque(self):
        self.assertEqual(tokens('(red, blue'), ['(red, blue'])


if __name__ == '__main__': unittest.main()
