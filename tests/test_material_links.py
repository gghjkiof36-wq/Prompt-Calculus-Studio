import copy
import unittest
from prompt_calculus_studio import composition as comp, materials, material_links as links
from prompt_calculus_studio import prompt_materials as pm, prompt_sources as supply
from prompt_calculus_studio.local_documents import category,identity
from test_material_editor import state
from test_prompt_material_resolution import text


class MaterialLinkTests(unittest.TestCase):
    def setUp(self):
        self.state=state();self.cat=self.state['modules'][0]['id']
        self.original=dict(id='original',name='Text',module=self.cat,prompt='',material_type='text',composition=text('Text',3))
        materials.save(self.state,self.original)
        source=materials.item(self.state,'original');self.member=links.linked_copy(source)
        self.member.update(source_id=source['id'],source_module=source['module'],source_revision=materials.fingerprint(source))
        self.combo=pm.new('combination','Combined');self.combo['combination']['modules']=[self.member]
        self.state.setdefault('uses',{})['combined']=self.combo
        self.state['uses']['independent']=comp.clone_node(self.member)
        self.state['uses']['independent'].pop(links.FIELD)

    def edit(self,fn):
        value=copy.deepcopy(materials.item(self.state,'original'));fn(value)
        materials.save(self.state,value)

    def test_source_edits_update_member_but_keep_local_controls_custom_text_and_frozen_run(self):
        self.member.update(weight=12,collapsed=True)
        seq=self.member['variants'];seq['selected']=seq['groups'][1]['id']
        seq['groups'][2]['text']='local override'
        original_id=self.member['id'];ordinary=copy.deepcopy(self.state['uses']['independent'])
        plan=supply.build(self.state,['use:combined'],count=1)
        frozen=copy.deepcopy(plan)
        self.edit(lambda v:v['composition']['variants']['groups'][1].update(text='updated source'))
        self.assertEqual(self.member['id'],original_id);self.assertEqual(self.member['weight'],12)
        self.assertTrue(self.member['collapsed']);self.assertEqual(seq['selected'],self.member['variants']['selected'])
        self.assertEqual(comp.render(self.member),'(updated source:1.2)')
        self.assertEqual(self.member['variants']['groups'][2]['text'],'local override')
        self.assertEqual(self.state['uses']['independent'],ordinary);self.assertEqual(plan,frozen)
        projected=copy.deepcopy(self.state);supply.project(projected,plan,plan['rounds'][0])
        self.assertEqual(comp.render(projected['uses']['combined']),'(Text-2:1.2)')

    def test_variant_delete_and_append_keep_valid_selection_and_clone_links(self):
        seq=self.member['variants'];seq['selected']=seq['groups'][1]['id']
        def change(v):
            groups=v['composition']['variants']['groups'];groups.pop(1)
            from prompt_calculus_studio.prompt_groups import new_group
            groups.append(new_group('new','new text'))
        self.edit(change)
        self.assertEqual(comp.render(self.member),'Text-1');comp.validate_node(self.member)
        clone=comp.clone_node(self.combo);member=clone['combination']['modules'][0]
        self.assertNotEqual(member['id'],self.member['id'])
        self.assertIn(member['id'],member[links.FIELD]['ids'].values())
        self.state['uses']['cloned']=clone
        self.edit(lambda v:v['composition']['variants']['groups'][0].update(text='again'))
        self.assertEqual(comp.render(clone),'again');comp.validate_node(clone)

    def test_legacy_member_keeps_local_text_and_missing_source_retains_content(self):
        self.member.pop(links.FIELD)
        self.member['variants']['groups'][0]['text']='old snapshot'
        links.sync_local(self.state,{})
        self.assertEqual(comp.render(self.member),'old snapshot')
        self.edit(lambda v:v['composition']['variants']['groups'][1].update(text='later edit'))
        self.assertEqual(self.member['variants']['groups'][1]['text'],'later edit')
        self.assertEqual(comp.render(self.member),'old snapshot')
        before=copy.deepcopy(self.member);materials.remove(self.state,'original')
        links.sync_local(self.state,{})
        self.assertEqual(self.member,before)

    def test_invalid_link_is_rejected_at_load_boundary_before_clone(self):
        for value in ([],dict(item='x',document=None,base={},ids=[]),
                      dict(item='x',document=None,base={},ids={'x':'y'})):
            root=comp.node('Bad','text');root[links.FIELD]=value
            with self.assertRaises(ValueError):comp.validate_node(root)

    def test_source_cycle_keeps_finite_snapshot_without_repeated_growth(self):
        original=copy.deepcopy(materials.item(self.state,'original'))
        combo=pm.new('combination','Recursive copy')
        combo['combination']['modules']=[links.linked_copy(original)]
        original['composition']['children']=[combo]
        materials.save(self.state,original)
        before=copy.deepcopy(self.state)
        for _ in range(4):links.sync_local(self.state,{})
        self.assertEqual(self.state,before)

    def test_indirect_cycle_through_text_children_keeps_snapshots(self):
        a=copy.deepcopy(materials.item(self.state,'original'))
        b=dict(id='other',name='Other',module=self.cat,prompt='',material_type='text',composition=text('Other',2))
        a['composition']['children']=[links.linked_copy(b)]
        b['composition']['children']=[links.linked_copy(a)]
        self.state['items']=[v for v in self.state['items'] if v['id']!='original']+[a,b]
        before=copy.deepcopy(self.combo)
        for _ in range(4):links.sync_local(self.state,{})
        self.assertEqual(self.combo,before)

    def test_nested_member_tracks_its_own_source_after_parent_merge(self):
        value=dict(id='child',name='Child',module=self.cat,prompt='old child',material_type='text')
        materials.save(self.state,value)
        child=links.linked_copy(materials.item(self.state,'child'))
        child.update(source_id='child',source_module=self.cat)
        self.member['children']=[child]
        value['prompt']='new child';materials.save(self.state,value)
        self.assertIn('new child',comp.render(self.combo))
        self.edit(lambda v:v['composition']['variants']['groups'][0].update(text='parent changed'))
        self.assertIn('parent changed',comp.render(self.combo));self.assertIn('new child',comp.render(self.combo))

    def test_new_nested_source_keeps_its_link_and_can_update_later(self):
        value=dict(id='child',name='Child',module=self.cat,prompt='',material_type='text',composition=text('Child',2))
        materials.save(self.state,value)
        source=materials.item(self.state,'child');child=links.linked_copy(source)
        child.update(source_id='child',source_module=self.cat)
        self.edit(lambda v:v['composition'].update(children=[child]))
        nested=self.member['children'][0]
        self.assertEqual(nested[links.FIELD]['item'],'child');comp.validate_node(nested)
        value['composition']['variants']['groups'][0]['text']='changed child'
        materials.save(self.state,value)
        self.assertIn('changed child',comp.render(self.combo))

    def test_source_edit_reconciles_random_bag_but_does_not_revive_stale_cursor(self):
        seq=self.member['variants'];ids=[g['id'] for g in seq['groups']]
        seq.update(mode='random',random_repeats=False,selected=ids[-1])
        position=dict(group=ids[-1],repeat=0,draws=3,exhausted=False,bag=ids,bag_index=2,ready=True)
        self.member['selection_cursor']=dict(signature=pm.signature(self.member),position=position)
        self.edit(lambda v:v['composition']['variants']['groups'].pop(1))
        self.assertEqual(pm.position(self.member)['group'],ids[-1]);comp.validate_node(self.member)
        self.assertEqual(self.member['selection_cursor']['position']['bag_index'],1)
        self.member['selection_cursor']['signature']='stale'
        self.member['selection_cursor']['position']['group']=ids[0]
        self.edit(lambda v:v['composition']['variants']['groups'][0].update(text='new'))
        self.assertEqual(pm.position(self.member)['group'],ids[-1])

    def test_source_nested_edits_preserve_placement_identity_and_local_weights(self):
        source=materials.item(self.state,'original')
        child=pm.new('text','Child');child['prompt']='child text';source['composition']['children']=[child]
        linked=links.linked_copy(source);linked['children'][0]['weight']=14
        before_id=linked['children'][0]['id'];source['composition']['children'][0]['prompt']='new child'
        links.update(linked,source)
        self.assertEqual(linked['children'][0]['id'],before_id)
        self.assertEqual(linked['children'][0]['weight'],14)
        self.assertIn('new child',comp.render(linked));comp.validate_node(linked)

    def test_duplicate_category_remaps_owned_links_and_preserves_external_provenance(self):
        materials.save(self.state,dict(id='combo',name='Combined',module=self.cat,prompt='',composition=self.combo,material_type='combination'))
        saved=category.from_state(self.state,self.cat)
        copied=identity.duplicate(saved).document
        loaded=category.to_state(copied)
        original=next(v for v in loaded['items'] if v['name']=='Text')
        combo=next(v for v in loaded['items'] if v['name']=='Combined')
        member=combo['composition']['combination']['modules'][0]
        self.assertEqual(member[links.FIELD]['item'],original['id'])
        provenance=member['source_id']
        edited=copy.deepcopy(original);edited['composition']['variants']['groups'][0]['text']='copied source'
        materials.save(loaded,edited)
        self.assertEqual(comp.render(member),'copied source');self.assertEqual(member['source_id'],provenance)
        self.assertEqual(comp.render(self.member),'Text-1')
