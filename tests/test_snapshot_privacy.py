"""Portable generation snapshots retain the referenced library closure only."""
import copy
import json
import unittest
from prompt_calculus_studio import composition as comp, materials, prompt_materials as pm, prompt_sources as supply
from prompt_calculus_studio.core import initial_state,validate_state,build_prompt,uid
from prompt_calculus_studio.snapshots import make_snapshot,portable_state,restore_snapshot,validate_snapshot
from prompt_calculus_studio.state_loading import prepare_state


def add_material(state,name,root=None):
    return materials.save(state,dict(name=name,module=state['modules'][0]['id'],prompt=name,
                                    **({'composition':root} if root else {})))


class SnapshotPrivacyTests(unittest.TestCase):
    def test_referenced_nested_library_closure_and_full_backup_are_distinct(self):
        state=prepare_state(initial_state(),multi=True)
        child=add_material(state,'used leaf')
        root=pm.new('combination','used combination');module=comp.node('leaf','used leaf');module['source_id']=child
        root['combination']['modules']=[module];parent=add_material(state,'used combination',root)
        placed=copy.deepcopy(root);placed['source_id']=parent
        reader=pm.new('flow','used reader');reader['sequence']['groups'][0]['modules']=[placed]
        key=supply.add(state,root=reader)
        unused=add_material(state,'SYNTHETIC_UNUSED_LIBRARY')
        before=copy.deepcopy(state);snapshot=make_snapshot(state)
        self.assertEqual({i['id'] for i in snapshot['state']['items']},{parent,child})
        self.assertNotIn('SYNTHETIC_UNUSED_LIBRARY',json.dumps(snapshot))
        self.assertIn(unused,{i['id'] for i in portable_state(state)['items']})
        self.assertEqual(state,before)
        restored=restore_snapshot(initial_state(),snapshot)
        self.assertEqual(comp.render(supply.source(restored,key)['root']),comp.render(placed))

    def test_inactive_workspace_and_stale_fixed_roots_do_not_expand_snapshot(self):
        state=prepare_state(initial_state(),multi=True)
        private=add_material(state,'SYNTHETIC_OTHER_WORKSPACE')
        root=comp.node('private','SYNTHETIC_OTHER_WORKSPACE');root['source_id']=private
        state['workspaces'][0].update(uses={'stale':root},fixed_uses=['stale'])
        from prompt_calculus_studio.workspace_scene import create
        other=create(state,'other')
        next(w for w in state['workspaces'] if w['id']==other)['uses']={'private':copy.deepcopy(root)}
        validate_state(state);before=copy.deepcopy(state)
        snap=make_snapshot(state)
        self.assertNotIn('SYNTHETIC_OTHER_WORKSPACE',json.dumps(snap))
        self.assertEqual(len(snap['state']['workspaces']),1)
        self.assertEqual(state,before)

    def test_all_included_output_list_selections_are_retained(self):
        from test_multi_output import workspace
        state,_,_=workspace();first,second=state['items'][:2]
        state['selections']={first['module']:[first['id']]}
        state['multi_output']['outputs']['out2']['list_state']={'selections':{second['module']:[second['id']]}}
        snap=make_snapshot(state)
        self.assertEqual({i['id'] for i in snap['state']['items']},{first['id'],second['id']})
        validate_snapshot(snap);restore_snapshot(initial_state(),snap)

    def test_old_complete_snapshot_and_empty_manual_draft_remain_readable(self):
        state=prepare_state(initial_state(),multi=True);state['draft']=''
        snapshot=make_snapshot(state)
        # This is the pre-fix wire shape: the whole library in a valid snapshot.
        snapshot['state']['items']=portable_state(state)['items']
        validate_snapshot(snapshot)
        restored=restore_snapshot(initial_state(),snapshot)
        self.assertEqual(restored['draft'],'');self.assertTrue(snapshot['manual_draft'])
        self.assertEqual(build_prompt(restored),snapshot['generated_prompt'])


if __name__=='__main__':unittest.main()
