import copy
import json
import math
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from prompt_studio import composition as c
from prompt_studio.core import initial_state, build_prompt, compose_details, validate_state, apply_workspace
from prompt_studio.snapshots import make_snapshot, restore_snapshot, validate_snapshot, portable_state


def example():
    state = initial_state(); item = state['items'][0]
    state['selections'] = {item['module']: [item['id']]}
    eyes = c.node('眼睛', 'red eyes'); hair = c.node('頭髮', 'long hair')
    root = c.node('精靈', '1girl', children=[eyes, hair, c.node('耳朵', 'pointy ears')])
    item['composition'] = copy.deepcopy(root); item['prompt'] = c.render(root)
    state['instances'] = {item['id']: root}; state['version'] = 2
    return state, item, root, eyes, hair


class CompositionTests(unittest.TestCase):
    def test_explicit_canvas_modules_match_requested_prompt_and_snapshot(self):
        s=initial_state(); girl,background=s['items'][0],s['items'][-1]
        s['selections']={girl['module']:[girl['id']],background['module']:[background['id']]}
        root=c.instance(s,girl['id']); root.update(name='長髮紅色衣服女孩',prompt='1girl',grouped=True)
        root['children']=[c.node(tag,tag) for tag in ('red clothes','red eyes','long hair','smile','dancing')]
        bg=c.instance(s,background['id']); bg.update(prompt='simple background',grouped=True)
        bg['children']=[c.node('白色背景','white background')]
        expected='(1girl, red clothes, red eyes, long hair, smile, dancing:1.0), (simple background, white background:1.0)'
        self.assertEqual(build_prompt(s),expected)
        restored=restore_snapshot(initial_state(),make_snapshot(s))
        self.assertEqual(build_prompt(restored),expected)
        root['children'][-1]['enabled']=False
        self.assertNotIn('dancing',build_prompt(s))
        s['weights']={girl['id']:12}
        self.assertTrue(build_prompt(s).startswith('(1girl, red clothes, red eyes, long hair, smile:1.2), '))
        root['overlays']=[c.node('替換角色','1boy')]
        self.assertTrue(build_prompt(s).startswith('(1boy:1.2), '))

    def test_nested_group_keeps_own_weight_and_disabled_parent_removes_branch(self):
        inner=c.node('動作','smile',children=[c.node('舞蹈','dancing')],grouped=True)
        inner['weight']=12
        root=c.node('角色','1girl',children=[inner],grouped=True)
        self.assertEqual(c.render(root),'(1girl, (smile, dancing:1.2):1.0)')
        inner['enabled']=False
        self.assertEqual(c.render(root),'(1girl:1.0)')

    def test_legacy_exact_prompt_and_source_preserved(self):
        s = initial_state(); item = s['items'][0]; item['prompt'] = '  (red eyes:1.2),  <lora:abc:0.75>\nfoo_bar  '
        s['selections'] = {item['module']: [item['id']]}
        old = copy.deepcopy(s)
        self.assertEqual(build_prompt(s), item['prompt'].strip())
        self.assertEqual(s, old)
        self.assertEqual(make_snapshot(s)['schema_version'], 1)

    def test_add_and_overlay_stack_restore_independently(self):
        s, item, root, eyes, hair = example(); original = copy.deepcopy(item)
        hair['children'].append(c.node('髮飾', 'hair ornament'))
        eyes['overlays'].append(c.node('眼罩', 'blindfold'))
        eyes['overlays'].append(c.node('護目鏡', 'goggles'))
        self.assertEqual(build_prompt(s), '1girl, goggles, long hair, hair ornament, pointy ears')
        eyes['overlays'][-1]['enabled'] = False
        self.assertIn('blindfold', build_prompt(s)); self.assertNotIn('red eyes', build_prompt(s))
        eyes['overlays'].clear()
        self.assertIn('red eyes', build_prompt(s)); self.assertEqual(item, original)
        eyes['enabled'] = False
        self.assertNotIn('red eyes', build_prompt(s)); self.assertIn('long hair, hair ornament', build_prompt(s))

    def test_disabled_or_covered_exclusion_source_does_not_filter(self):
        s, item, root, eyes, hair = example()
        rule = c.node('閉眼', 'closed eyes', excludes=['red eyes']); root['children'].append(rule)
        self.assertNotIn('red eyes', build_prompt(s))
        self.assertEqual(compose_details(s)[1][item['id']]['tags'], ['red eyes'])
        rule['enabled'] = False; self.assertIn('red eyes', build_prompt(s))
        rule['enabled'] = True; rule['overlays'].append(c.node('覆蓋', 'smile'))
        self.assertIn('red eyes', build_prompt(s))

    def test_instance_edits_and_library_edits_are_independent(self):
        s, item, root, eyes, hair = example()
        item['composition']['children'][0]['prompt'] = 'blue eyes'
        self.assertIn('red eyes', build_prompt(s))
        s['instances'].clear(); self.assertIn('blue eyes', build_prompt(s))
        current = c.instance(s, item['id']); current['children'][0]['prompt'] = 'gold eyes'
        self.assertEqual(item['composition']['children'][0]['prompt'], 'blue eyes')

    def test_multiple_copies_and_detach_keep_content(self):
        original = c.node('眼睛', 'red eyes'); a = c.clone_node(original); b = c.clone_node(original)
        a['prompt'] = 'blue eyes'; self.assertNotEqual(a['id'], b['id']); self.assertEqual(b['prompt'], 'red eyes')
        group = c.node('組合', children=[a, b]); root = c.node('根', children=[group])
        before = c.render(root); c.detach(root, group['id']); self.assertEqual(c.render(root), before)
        self.assertEqual(len(root['children']), 2)

    def test_snapshot_is_self_contained_and_restores_historical_content(self):
        s, item, root, eyes, hair = example(); eyes['overlays'].append(c.node('眼罩', 'blindfold'))
        s['draft'] = 'My exact manual draft\n, keep this'; snapshot = make_snapshot(s)
        self.assertEqual(snapshot['schema_version'], 2)
        item['composition']['prompt'] = 'Changed original'; root['prompt'] = 'Changed current'
        validate_snapshot(snapshot)
        result = restore_snapshot(s, json.loads(json.dumps(snapshot)))
        self.assertEqual(build_prompt(result), snapshot['generated_prompt'])
        self.assertEqual(result['draft'], 'My exact manual draft\n, keep this')
        self.assertIn('blindfold', build_prompt(result)); self.assertNotIn('Changed', build_prompt(result))
        self.assertNotIn('text_positions', portable_state(result))

    def test_workspace_restores_only_fixed_instances(self):
        s, item, root, eyes, hair = example(); workspace = s['workspaces'][0]
        other = s['items'][2]; s['selections'][other['module']] = [other['id']]
        c.instance(s, other['id'])['prompt'] = 'keep moving'
        workspace.update(fixed=[item['module']], picks={item['module']: [item['id']]}, instances={item['id']: copy.deepcopy(root)})
        eyes['prompt'] = 'edited eyes'; s['draft'] = 'keep manual'
        apply_workspace(s, workspace['id'])
        self.assertIn('red eyes', build_prompt(s)); self.assertIn('keep moving', build_prompt(s))
        self.assertEqual(s['draft'], 'keep manual')

    def test_invalid_graphs_are_rejected_before_render(self):
        root = c.node('root'); root['children'].append(root)
        with self.assertRaises(ValueError): c.validate_node(root)
        root = c.node('root'); child = c.node('child'); root['children'] = [child, child]
        with self.assertRaises(ValueError): c.validate_node(root)
        s, item, root, eyes, hair = example(); s['version'] = 1
        with self.assertRaises(ValueError): validate_state(s)
        s['version'] = 2; s['text_positions'] = {'a': [math.nan, 0]}
        with self.assertRaises(ValueError): validate_state(s)
        s.pop('text_positions'); root['weight'] = True
        with self.assertRaises(ValueError): validate_state(s)

    def test_new_snapshot_is_not_silently_accepted_as_old(self):
        s, *_ = example(); snapshot = make_snapshot(s); snapshot['schema_version'] = 1
        with self.assertRaises(ValueError): validate_snapshot(snapshot)


if __name__ == '__main__': unittest.main()
