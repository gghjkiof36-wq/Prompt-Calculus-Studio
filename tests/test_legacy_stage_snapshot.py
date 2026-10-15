"""Fixed synthetic Stage projection made by pre-fix 0.8.8 candidate 5.

The fixture was emitted by 0252a039's real BoundScheduler, before the restore
fix. SHA256: 3e135623131259d7cc796a1b74c04a79985e972bec0dcc3879c6256fac1a7c82.
No current-code reconstruction is used as the old-format fixture.
"""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from prompt_calculus_studio.core import initial_state,build_prompt,Storage,validate_state
from prompt_calculus_studio.multi_output import compiled_outputs
from prompt_calculus_studio.snapshots import restore_snapshot,validate_snapshot,make_snapshot,_legacy_stage_source

FIXTURE=Path(__file__).parent/'fixtures'/'legacy_stage_snapshot_088.json'


class LegacyStageSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.snapshot=json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_fixed_stage_restores_frozen_content_bindings_and_order_without_mutation(self):
        self.assertEqual(hashlib.sha256(FIXTURE.read_bytes()).hexdigest(),
                         '3e135623131259d7cc796a1b74c04a79985e972bec0dcc3879c6256fac1a7c82')
        original=copy.deepcopy(self.snapshot);current=initial_state();current['draft']='local manual'
        before=copy.deepcopy(current);restored=restore_snapshot(current,self.snapshot)
        self.assertEqual(current,before);self.assertEqual(self.snapshot,original)
        self.assertEqual(build_prompt(restored),'prompt 0')
        self.assertEqual(compiled_outputs(restored),original['outputs'])
        source=original['state'];data=source['multi_output']
        for field in ('bindings','clip_inputs','stages','connections'):
            self.assertEqual(restored['multi_output'][field],data[field])
        # IDs can be remapped, but ordered roots and their surviving provenance
        # are identical apart from the explicit material type migration.
        old=[source['uses'][key] for key in source['output_order']]
        new=[restored['uses'][key] for key in restored['output_order']]
        self.assertEqual(len(new),len(old))
        for a,b in zip(old,new):
            for field in ('sequence','round_id','supply_origin','selection_cursor','supply_cursor'):
                self.assertEqual(a.get(field),b.get(field),field)
        with tempfile.TemporaryDirectory() as directory:
            store=Storage(directory)
            try:
                store.save(restored);reopened=store.load_current(multi=True)
                self.assertEqual(compiled_outputs(reopened),original['outputs'])
                self.assertEqual(reopened['uses'],restored['uses'])
            finally:store.close()
        self.assertEqual(compiled_outputs(restore_snapshot(initial_state(),make_snapshot(restored))),original['outputs'])
        validate_state(restored)

    def test_explicit_empty_and_normal_manual_drafts_stay_distinct(self):
        for draft in ('','manual frozen'):
            with self.subTest(draft=draft):
                snap=copy.deepcopy(self.snapshot);s=snap['state'];key=s['multi_output']['current_output']
                s['draft']=draft;s['draft_base']='saved base';s['multi_output']['outputs'][key]['draft']=draft
                snap.update(final_prompt=draft,manual_draft=True,outputs=compiled_outputs(s));validate_snapshot(snap)
                restored=restore_snapshot(initial_state(),snap)
                self.assertEqual(restored['draft'],draft);self.assertEqual(restored['draft_base'],'saved base')
                self.assertEqual(compiled_outputs(restored),snap['outputs'])

    def test_two_outputs_keep_distinct_drafts_and_binding_targets(self):
        snap=copy.deepcopy(self.snapshot);state=snap['state'];data=state['multi_output']
        second=copy.deepcopy(data['outputs']['stage_text_0']);second['draft']=''
        data['outputs']['stage_text_1']=second
        data['clip_inputs']['second_clip']=dict(name='Second',workflow='flow',text_source='pcs')
        data['bindings'].append(dict(workflow='flow',clip='second_clip',node='7',field='text'))
        data['connections'].extend([
            dict(id='second_canvas',source=second['canvas'],destination='stage_text_1',kind='text'),
            dict(id='second_binding',source='stage_text_1',destination='second_clip',kind='clip')])
        snap['outputs']=compiled_outputs(state);validate_snapshot(snap)
        restored=restore_snapshot(initial_state(),snap)
        self.assertEqual(compiled_outputs(restored),snap['outputs'])
        self.assertEqual(restored['multi_output']['bindings'],data['bindings'])
        # Check the noncurrent output too: the root prompt alone cannot detect
        # a lost empty draft or a mismatched second bound field.
        self.assertEqual(compiled_outputs(restored)['stage_text_1']['final_prompt'],'')

    def test_unrecognized_cache_signature_or_live_graph_is_not_repaired(self):
        for change in ('signature','error','projection','members','raw','live'):
            with self.subTest(change=change):
                snap=copy.deepcopy(self.snapshot);s=snap['state'];d=s['multi_output'];canvas=next(iter(d['canvases'].values()))
                if change=='signature':canvas['source_revision']='0'*64
                elif change=='error':canvas['source_error']='unresolved source'
                elif change=='projection':d['workflow_order']['visible']=True
                elif change=='members':canvas['source_members']=[]
                elif change=='raw':canvas['raw_prompt']='unsupported raw source'
                else:s['_execution_inputs']={'not-frozen':{}}
                self.assertIs(_legacy_stage_source(snap),s)
        snap=copy.deepcopy(self.snapshot);next(iter(snap['state']['multi_output']['canvases'].values()))['source_revision']='0'*64
        current=initial_state();before=copy.deepcopy(current)
        with self.assertRaises(ValueError):restore_snapshot(current,snap)
        self.assertEqual(current,before)

    def test_mismatched_text_and_output_are_rejected_before_conversion(self):
        for change in ('generated','output','root'):
            with self.subTest(change=change):
                snap=copy.deepcopy(self.snapshot)
                if change=='generated':snap['generated_prompt']='forged'
                elif change=='output':snap['outputs']['stage_text_0']['final_prompt']='forged'
                else:next(iter(snap['state']['uses'].values()))['sequence']['groups'][0]['text']='forged'
                original=copy.deepcopy(snap);current=initial_state();before=copy.deepcopy(current)
                with self.assertRaises(ValueError):restore_snapshot(current,snap)
                self.assertEqual(current,before);self.assertEqual(snap,original)

    def test_current_stage_projection_needs_no_legacy_conversion(self):
        snap=copy.deepcopy(self.snapshot)
        for canvas in snap['state']['multi_output']['canvases'].values():
            for field in ('source_members','source_revision','raw_prompt','source_error'):canvas.pop(field,None)
        self.assertIs(_legacy_stage_source(snap),snap['state'])
        restored=restore_snapshot(initial_state(),snap)
        self.assertEqual(compiled_outputs(restored),snap['outputs'])


if __name__=='__main__':unittest.main()
