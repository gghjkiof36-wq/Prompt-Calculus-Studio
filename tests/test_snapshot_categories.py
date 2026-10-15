"""Generation category projection preserves references, ordering and local folders."""
import copy
import json
import unittest
from prompt_calculus_studio import composition as comp,materials,material_navigation as nav
from prompt_calculus_studio.core import initial_state,validate_state,build_prompt
from prompt_calculus_studio.snapshots import make_snapshot,portable_state,restore_snapshot,validate_snapshot
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.multi_output import compiled_outputs
from test_multi_output import workspace


class SnapshotCategoryTests(unittest.TestCase):
    def test_unused_categories_folders_and_empty_references_are_not_portable_generation_data(self):
        state=prepare_state(initial_state(),multi=True)
        used=materials.add_category(state,'needed category');unused=materials.add_category(state,'SYNTHETIC_UNUSED_CATEGORY')
        folder=nav.add_folder(state,'needed folder');nav.move(state,used,folder);nav.move(state,unused,folder)
        nav.add_folder(state,'SYNTHETIC_UNUSED_FOLDER')
        state['uses']['kept']=comp.node('kept','exact text');state['uses']['kept']['source_module']=used
        state['selections'][unused]=[];state['temporary_before']=unused
        active=next(w for w in state['workspaces'] if w['id']==state['workspace'])
        active['picks'][unused]=[];active['fixed'].append(unused)
        for output in state['multi_output']['outputs'].values():
            output['list_state']=dict(selections={unused:[]},temporary_before=unused)
        original=copy.deepcopy(state);snapshot=make_snapshot(state);clean=snapshot['state']
        self.assertEqual({m['id'] for m in clean['modules']},{used})
        self.assertEqual(clean[nav.FIELD]['folders'],[dict(id=folder,name='needed folder',categories=[used])])
        self.assertNotIn('SYNTHETIC_UNUSED',json.dumps(snapshot))
        self.assertNotIn(unused,json.dumps(snapshot));self.assertIsNone(clean['temporary_before'])
        validate_snapshot(snapshot);self.assertEqual(state,original)
        full=portable_state(state)
        self.assertEqual(full['modules'],state['modules']);self.assertEqual(full[nav.FIELD],state[nav.FIELD])

    def test_nested_source_categories_and_referenced_items_keep_their_folders_on_restore(self):
        state=prepare_state(initial_state(),multi=True)
        a=materials.add_category(state,'first needed');b=materials.add_category(state,'second needed')
        folder=nav.add_folder(state,'used folder');nav.move(state,b,folder);nav.move(state,a,folder)
        child=materials.save(state,dict(name='child',prompt='actual prompt',module=b))
        root=comp.node('parent');leaf=comp.node('child','actual prompt')
        leaf.update(source_id=child,source_module=b);root['children']=[leaf];root['source_module']=a
        state['uses']['kept']=root
        snapshot=make_snapshot(state)
        self.assertEqual({m['id'] for m in snapshot['state']['modules']},{a,b})
        current=initial_state();local=nav.add_folder(current,'local folder');nav.move(current,current['modules'][0]['id'],local)
        before=copy.deepcopy(current);restored=restore_snapshot(current,snapshot)
        self.assertEqual(current,before);self.assertEqual(nav.folder(restored,local),nav.folder(current,local))
        imported=next(f for f in nav.view(restored)['folders'] if f['name']=='used folder')
        names={m['id']:m['name'] for m in restored['modules']}
        self.assertEqual([names[key].removesuffix(' · 歷史快照') for key in imported['categories']],['second needed','first needed'])
        restored_root=next(iter(restored['uses'].values()))
        self.assertIn(restored_root['source_module'],names)
        self.assertIn(restored_root['children'][0]['source_module'],names)
        self.assertEqual(comp.render(restored_root),'actual prompt')

    def test_restore_does_not_move_existing_local_category_into_snapshot_folder(self):
        state=initial_state();item=state['items'][0];mid=item['module']
        state['selections']={mid:[item['id']]};remote=nav.add_folder(state,'historical folder');nav.move(state,mid,remote)
        snapshot=make_snapshot(state);current=copy.deepcopy(state)
        nav.rename_folder(current,remote,'current custom folder');before=copy.deepcopy(current[nav.FIELD])
        restored=restore_snapshot(current,snapshot)
        self.assertEqual(restored[nav.FIELD],before)

    def test_other_output_selections_and_empty_anchor_keep_exact_text_and_binding(self):
        state,_,_=workspace();state['selection_view']='list';state['settings']['separate_selections']=True
        private=materials.add_category(state,'SYNTHETIC_EMPTY_ANCHOR')
        a=materials.add_category(state,'A');b=materials.add_category(state,'B');c=materials.add_category(state,'C')
        items={mid:materials.save(state,dict(name=mid,prompt=text,module=mid)) for mid,text in ((a,'alpha'),(b,'beta'),(c,'gamma'))}
        state['selections']={a:[items[a]],c:[items[c]],private:[]}
        state['output_order']=[c,a];state['temporary']=['temporary'];state['temporary_before']=private
        state['multi_output']['outputs']['out2']['list_state']=dict(selections={a:[items[a]],b:[items[b]],c:[items[c]],private:[]},temporary=['second temporary'],temporary_before=private)
        before=compiled_outputs(state);original=copy.deepcopy(state);snapshot=make_snapshot(state)
        self.assertNotIn('SYNTHETIC_EMPTY_ANCHOR',json.dumps(snapshot))
        self.assertEqual(snapshot['outputs'],before);self.assertEqual(state,original)
        self.assertEqual(snapshot['state']['multi_output']['bindings'],state['multi_output']['bindings'])
        restored=restore_snapshot(initial_state(),snapshot);validate_state(restored)
        self.assertEqual(build_prompt(restored),snapshot['generated_prompt'])
        from prompt_calculus_studio.multi_output import select_output,capture_current
        for key in ('out2','out3',restored['multi_output']['current_output']):
            select_output(restored,key);capture_current(restored)
        self.assertEqual(compiled_outputs(restored),before)
        restored=prepare_state(restored,multi=True)
        self.assertEqual(compiled_outputs(restored),before)
        imported=next(m['id'] for m in restored['modules'] if m['name']=='B · 歷史快照')
        materials.remove_category(restored,imported);validate_state(restored)
        resaved=make_snapshot(restored)
        self.assertNotIn(imported,json.dumps(resaved['state'].get('category_positions',{})))
        for output in resaved['state']['multi_output']['outputs'].values():
            self.assertNotIn(imported,output['list_state'].get('category_positions',{}))

    def test_no_used_categories_is_valid_and_explicit_empty_draft_remains_empty(self):
        state=prepare_state(initial_state(),multi=True);state['draft']=''
        private=materials.add_category(state,'SYNTHETIC_NO_MATERIALS');state['selections'][private]=[]
        snapshot=make_snapshot(state)
        self.assertEqual(snapshot['state']['modules'],[]);self.assertEqual(snapshot['final_prompt'],'')
        self.assertEqual(restore_snapshot(initial_state(),snapshot)['draft'],'')

    def test_invalid_position_metadata_is_rejected_before_restoring(self):
        state,_,_=workspace();snapshot=make_snapshot(state);before=copy.deepcopy(state)
        for nested in (False,True):
            for invalid in ('invalid',{'unused':True},{'unused':-1}):
                with self.subTest(nested=nested,invalid=invalid):
                    changed=copy.deepcopy(snapshot)
                    owner=next(iter(changed['state']['multi_output']['outputs'].values()))['list_state'] if nested else changed['state']
                    owner['category_positions']=invalid
                    with self.assertRaises(ValueError):restore_snapshot(state,changed)
                    self.assertEqual(state,before)
        # A deleted category can leave a harmless historic position hint.
        changed=copy.deepcopy(snapshot)
        next(iter(changed['state']['multi_output']['outputs'].values()))['list_state']['category_positions']['retired-category']=1
        restored=restore_snapshot(state,changed)
        self.assertEqual(build_prompt(restored),snapshot['generated_prompt']);self.assertEqual(state,before)

    def test_legacy_shared_list_and_canvas_keep_each_outputs_original_order(self):
        state,_,_=workspace();state['settings']['separate_selections']=False
        keys=[materials.add_category(state,name) for name in ('SYNTHETIC_EMPTY','A','B','C')]
        a,b,c=keys[1:];items={mid:materials.save(state,dict(name=text,prompt=text,module=mid)) for mid,text in ((a,'alpha'),(b,'beta'),(c,'gamma'))}
        state['selections']={a:[items[a]],c:[items[c]]};state['output_order']=[c,a]
        state['multi_output']['outputs']['out2']['list_state']=dict(selections={a:[items[a]],b:[items[b]],c:[items[c]]})
        state=prepare_state(state,multi=True)
        before=compiled_outputs(state);snapshot=make_snapshot(state)
        self.assertNotIn('SYNTHETIC_EMPTY',json.dumps(snapshot));self.assertEqual(snapshot['outputs'],before)
        restored=restore_snapshot(initial_state(),snapshot)
        self.assertEqual(compiled_outputs(restored),before)


if __name__=='__main__':unittest.main()
