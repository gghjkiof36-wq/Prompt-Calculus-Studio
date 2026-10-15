import copy
import json
import unittest
from prompt_calculus_studio import core,composition as comp,prompt_groups as groups,prompt_sources as supply,prompt_materials as pm
from prompt_calculus_studio.state_loading import prepare_state
from prompt_calculus_studio.snapshots import portable_state
from test_prompt_material_resolution import flow,text


class MigrationTests(unittest.TestCase):
    def test_legacy_names_ids_body_drafts_weight_exclusions_and_cursor_survive(self):
        state=core.initial_state();item=state['items'][0]
        root=comp.node('舊名稱','cat, cat',children=[comp.node('子項','garden')])
        root.update(deduplicate=True,weight=15,excludes=['conflict'])
        item['composition']=root;expected=comp.render(root)
        reader=supply.add(state,root=flow())
        source=supply.source(state,reader)['root'];source.pop('material_type',None)
        source['sequence'].pop('random_repeats',None)
        source['sequence']['groups'][0]['repeat']=3
        position=groups.cursor(source['sequence']);position['repeat']=1
        source['supply_cursor']=dict(signature=supply.legacy_signature(source),position=position)
        source['round_drafts']={source['sequence']['selected']:''}
        before=copy.deepcopy(state);migrated=prepare_state(state)
        self.assertEqual(state,before);self.assertEqual(migrated['version'],7)
        converted=migrated['items'][0];self.assertEqual(converted['id'],item['id'])
        self.assertEqual(converted['name'],item['name']);self.assertEqual(converted['material_type'],'combination')
        self.assertEqual(comp.render(converted['composition']),expected)
        after=supply.source(migrated,reader)['root'];self.assertEqual(pm.position(after),position)
        self.assertEqual(after['round_drafts'],source['round_drafts'])
        self.assertEqual(prepare_state(migrated),migrated)

    def test_variants_combination_round_trip_and_unknown_type_rejection(self):
        state=core.initial_state();root=pm.new('combination','組合')
        root['combination']['modules']=[text('item',3)];state['uses']={'copy':root};state['version']=7
        core.validate_state(state)
        restored=json.loads(json.dumps(state,ensure_ascii=False));core.validate_state(restored)
        self.assertEqual(restored,state)
        incompatible=copy.deepcopy(state);incompatible['uses']['copy']['material_type']='future'
        with self.assertRaisesRegex(ValueError,'類型不支援'):prepare_state(incompatible)
        self.assertEqual(incompatible['uses']['copy']['material_type'],'future')

    def test_detached_duplicate_retains_repeat_offset_but_does_not_share_progress(self):
        root=text('copy',3);root['variants']['groups'][0]['repeat']=3
        _,root=pm.resolve(root);cloned=comp.clone_node(root)
        self.assertNotEqual(cloned['id'],root['id']);self.assertEqual(pm.position(cloned)['repeat'],1)
        _,cloned=pm.resolve(cloned);self.assertEqual(pm.position(cloned)['repeat'],2)
        self.assertEqual(pm.position(root)['repeat'],1)
