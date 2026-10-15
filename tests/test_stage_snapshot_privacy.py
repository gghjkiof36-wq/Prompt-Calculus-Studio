"""Inspect the actual submitted envelope and its synthetic PNG, not a fabricated snapshot."""
import copy
import json
import unittest
from pathlib import Path
import test_bound_scheduler as bound
from test_snapshot_privacy import add_material
from prompt_calculus_studio import composition as comp,materials,prompt_materials as pm
from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.pnginfo import png_metadata
from prompt_calculus_studio.snapshots import image_snapshots,portable_state,restore_snapshot,validate_snapshot


class StageSnapshotPrivacyTests(unittest.TestCase):
    setUp=bound.BoundSchedulerTests.setUp
    tearDown=bound.BoundSchedulerTests.tearDown
    stages=bound.BoundSchedulerTests.stages
    click=bound.BoundSchedulerTests.click
    group_source=bound.BoundSchedulerTests.group_source
    bound_text=bound.BoundSchedulerTests.bound_text

    def test_unconnected_text_notes_alias_variants_and_combination_never_leave_library(self):
        self.bound_text();markers=['SYNTHETIC_PRIVATE_'+field for field in ('PROMPT','NOTE','ALIAS','VARIANT','COMBINATION')]
        def setup(state):
            key=add_material(state,markers[0]);item=materials.item(state,key)
            item.update(notes=markers[1],aliases=[markers[2]])
            variant=pm.new('text','unused variant');variant['variants']=pm.variants(variant)
            variant['variants']['groups'][0]['text']=markers[3];add_material(state,'unused variant',variant)
            combination=pm.new('combination','unused combination')
            combination['combination']['modules']=[comp.node('unused member',markers[4])]
            add_material(state,'unused combination',combination)
        self.assertTrue(self.c.commit(setup));original=copy.deepcopy(self.w.state['items']);self.click()
        self.assertEqual(len(self.executor.submissions),1)
        payload=self.executor.submissions[0];encoded=json.dumps(payload,ensure_ascii=False)
        for marker in markers:self.assertNotIn(marker,encoded)
        snapshots=[b['snapshot'] for b in payload['extra_data']['extra_pnginfo']['prompt_studio']['bindings']]
        self.assertTrue(snapshots)
        for snapshot in snapshots:validate_snapshot(snapshot);restore_snapshot(initial_state(),snapshot)
        self.executor.finish()
        images=list((Path(self.tmp.name)/'stages-executor').glob('*.png'));self.assertTrue(images)
        metadata=png_metadata(images[0]);self.assertTrue(image_snapshots(metadata))
        for marker in markers:
            self.assertNotIn(marker,json.dumps(metadata,ensure_ascii=False))
            self.assertIn(marker,json.dumps(portable_state(self.w.state),ensure_ascii=False))
        self.assertEqual(self.w.state['items'],original)
        self.w.persist();self.assertEqual(self.w.store.load()['items'],original)

    def test_used_structure_reference_closure_and_bound_field_survive(self):
        self.stages();ids=[]
        def setup(state):
            child=add_material(state,'used child')
            root=pm.new('combination','used combination');leaf=comp.node('used leaf','used child');leaf['source_id']=child
            root['combination']['modules']=[leaf];parent=add_material(state,'used combination',root)
            root['source_id']=parent;state['uses']['root']=root;ids.extend((child,parent))
            add_material(state,'SYNTHETIC_UNUSED_ORIGINAL')
        self.assertTrue(self.c.commit(setup));self.click();payload=self.executor.submissions[0]
        self.assertEqual(payload['prompt']['6']['inputs']['text'],'used child')
        envelope=payload['extra_data']['extra_pnginfo']['prompt_studio'];snap=envelope['bindings'][0]['snapshot']
        self.assertEqual({i['id'] for i in snap['state']['items']},set(ids))
        root=next(iter(snap['state']['uses'].values()));self.assertEqual(root['combination']['modules'][0]['source_id'],ids[0])
        restored=restore_snapshot(initial_state(),snap)
        self.assertEqual(comp.render(next(iter(restored['uses'].values()))),'used child')

    def test_explicit_empty_bound_prompt_is_preserved(self):
        self.stages()
        self.assertTrue(self.c.commit(lambda state:state.update(draft='')));self.click()
        payload=self.executor.submissions[0];self.assertEqual(payload['prompt']['6']['inputs']['text'],'')
        snap=payload['extra_data']['extra_pnginfo']['prompt_studio']['bindings'][0]['snapshot']
        self.assertEqual(snap['final_prompt'],'');self.assertTrue(snap['manual_draft'])
        self.assertEqual(restore_snapshot(initial_state(),snap)['draft'],'')


if __name__=='__main__':unittest.main()
