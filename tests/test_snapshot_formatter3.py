"""New text semantics must not invalidate frozen historical output."""
import copy
import unittest
from prompt_calculus_studio import composition as comp, prompt_materials as pm
from prompt_calculus_studio.core import initial_state,build_prompt
from prompt_calculus_studio.snapshots import make_snapshot,validate_snapshot,restore_snapshot
from prompt_calculus_studio.multi_output import compiled_outputs


class FormatterTests(unittest.TestCase):
    def state(self):
        from test_multi_output import workspace
        state,canvas,output=workspace()
        root=pm.new('text','sample');root['prompt']='test,';root['variants']=pm.variants(root)
        root['weight']=11;root['children']=[comp.node('child','child')]
        state['uses']['one']=root
        state['version']=7
        return state

    def historical(self,state):
        snapshot=make_snapshot(state);snapshot['formatter_version']=2
        with comp.snapshot_format(2):
            snapshot['generated_prompt']=build_prompt(snapshot['state'])
            snapshot['final_prompt']=snapshot['state']['draft'] if snapshot['manual_draft'] else snapshot['generated_prompt']
            snapshot['outputs']=compiled_outputs(snapshot['state'])
        return snapshot

    def test_old_and_new_formats_validate_and_restore_frozen_values(self):
        state=self.state();current=make_snapshot(state);old=self.historical(state)
        self.assertNotEqual(current['generated_prompt'],old['generated_prompt'])
        self.assertEqual(current['formatter_version'],3)
        self.assertIn('child',current['generated_prompt']);self.assertNotIn('child',old['generated_prompt'])
        before=copy.deepcopy(old);validate_snapshot(old)
        restored=restore_snapshot(initial_state(),old)
        self.assertEqual(old,before)
        self.assertEqual({k:v['final_prompt'] for k,v in compiled_outputs(restored).items()},
                         {k:v['final_prompt'] for k,v in old['outputs'].items()})
        self.assertEqual(compiled_outputs(restore_snapshot(initial_state(),current)),current['outputs'])

    def test_transition_format2_and_explicit_empty_draft_stay_readable(self):
        state=self.state();state['draft']=''
        snapshot=make_snapshot(state);snapshot['formatter_version']=2
        validate_snapshot(snapshot)
        self.assertEqual(restore_snapshot(initial_state(),snapshot)['draft'],'')

    def test_mixed_renderers_and_edited_prompt_are_rejected(self):
        state=self.state();new=make_snapshot(state);old=self.historical(state)
        mixed=copy.deepcopy(old);mixed['outputs']=new['outputs']
        with self.assertRaises(ValueError):validate_snapshot(mixed)
        bad=copy.deepcopy(new);bad['generated_prompt']+='tamper'
        with self.assertRaises(ValueError):validate_snapshot(bad)
        old['formatter_version']=3
        with self.assertRaises(ValueError):validate_snapshot(old)

    def test_format_context_does_not_leak_to_later_edits(self):
        root=pm.new('text','sample');root['prompt']='test,';root['variants']=pm.variants(root);root['weight']=11
        current=comp.render(root)
        with comp.snapshot_format(2):self.assertIn('test,',comp.render(root))
        self.assertEqual(current,'(test:1.1)');self.assertEqual(comp.render(root),current)
