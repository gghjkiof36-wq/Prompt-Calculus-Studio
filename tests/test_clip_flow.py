import copy,tempfile,unittest
from pathlib import Path
from prompt_calculus_studio import clip_flow,multi_output as model
from prompt_calculus_studio.core import Storage,validate_state
from prompt_calculus_studio.snapshots import make_snapshot,validate_snapshot,restore_snapshot
from prompt_calculus_studio.generation import submission
from prompt_calculus_studio.workflow_transfer import apply_transfer
from test_multi_output import workspace


class ClipFlowTests(unittest.TestCase):
    def test_legacy_upgrade_backs_up_preserves_text_and_disconnected_output(self):
        old,cid,oid=workspace()
        line=next(c for c in old['multi_output']['connections'] if c['source']=='out2' and c['kind']=='execution')
        model.disconnect(old,line['id']); original=copy.deepcopy(old); before=model.compiled_outputs(old)
        new=clip_flow.upgrade(old)
        self.assertEqual(old,original); self.assertEqual(model.compiled_outputs(new),before)
        self.assertNotIn('out2',model.connected_outputs(new)); self.assertEqual(clip_flow.upgrade(new),new)
        self.assertFalse(any(c['kind'] in ('execution','preview') for c in new['multi_output']['connections']))
        with tempfile.TemporaryDirectory() as folder:
            store=Storage(folder)
            try:
                store.save(old); loaded=store.load_current(multi=True)
                self.assertEqual(model.compiled_outputs(loaded),before); self.assertTrue(list(Path(folder).glob('before-import-*.sqlite3')))
                self.assertEqual(store.load_current(multi=True),loaded)
            finally: store.close()

    def test_clip_rewire_keeps_target_and_submission_snapshot_frozen(self):
        old,cid,oid=workspace(); state=clip_flow.upgrade(old); profile=state['generation']['profiles'][0]
        clip=next(b['clip'] for b in state['multi_output']['bindings'] if b['node']=='7')
        snapshot=make_snapshot(state); payload=submission(profile,snapshot)
        model.connect(state,oid,clip,'clip'); validate_state(state)
        self.assertEqual(next(b['text'] for b in model.bound_texts(state,profile) if b['node']=='7'),'eyes, blue')
        self.assertEqual(payload['prompt']['7']['inputs']['text'],'closed eyes')
        self.assertEqual(payload['prompt']['8']['inputs']['text_g'],'third original')
        validate_snapshot(snapshot)
        restored=restore_snapshot(state,snapshot)
        self.assertEqual(next(b['text'] for b in model.bound_texts(restored,profile) if b['node']=='7'),'closed eyes')

    def test_empty_draft_unique_field_and_disconnect_preserves_workflow_value(self):
        old,cid,oid=workspace(); state=clip_flow.upgrade(old); profile=state['generation']['profiles'][0]
        clip=next(b['clip'] for b in state['multi_output']['bindings'] if b['node']=='7')
        state['multi_output']['outputs']['out2']['draft']=''
        self.assertEqual(next(b['text'] for b in model.bound_texts(state,profile) if b['node']=='7'),'')
        another=clip_flow.add(state)
        with self.assertRaisesRegex(ValueError,'占用'): clip_flow.set_binding(state,'flow',another,('7','text'))
        with self.assertRaises(ValueError): model.connect(state,cid,another,'clip')
        edge=next(c for c in state['multi_output']['connections'] if c['destination']==clip)
        model.disconnect(state,edge['id'])
        payload=submission(profile,make_snapshot(state))
        self.assertEqual(payload['prompt']['7']['inputs']['text'],'original negative')
        self.assertEqual(state['multi_output']['outputs']['out2']['draft'],'')

    def test_old_workflow_exchange_creates_clip_bindings_atomically(self):
        old,cid,oid=workspace(); profile=old['generation']['profiles'][0]
        value=dict(version=1,id='flow',name='flow',graph=profile['graph'],bindings=copy.deepcopy(old['multi_output']['bindings']))
        state=clip_flow.upgrade(old); before=copy.deepcopy(state)
        result,_=apply_transfer(state,value)
        self.assertEqual(state,before); self.assertEqual(len(model.bound_texts(result,profile)),2)
        self.assertTrue(all('clip' in b and 'output' not in b for b in result['multi_output']['bindings']))
        value['bindings'][0]['node']='missing'
        with self.assertRaises(ValueError): apply_transfer(state,value)
        self.assertEqual(state,before)

    def test_broken_clip_and_duplicate_input_rejected(self):
        state=clip_flow.upgrade(workspace()[0]); data=state['multi_output']
        data['bindings'][0]['clip']='missing'
        with self.assertRaises(ValueError): validate_state(state)
        state=clip_flow.upgrade(workspace()[0]); edge=next(c for c in state['multi_output']['connections'] if c['kind']=='clip')
        state['multi_output']['connections'].append(dict(edge,id='duplicate'))
        with self.assertRaises(ValueError): validate_state(state)

    def test_shared_backend_verifies_v3_submission_and_keeps_actual_text(self):
        from test_comfy_integration import Service
        state=clip_flow.upgrade(workspace()[0]); profile=state['generation']['profiles'][0]
        with tempfile.TemporaryDirectory() as folder:
            service=Service(Path(folder)/'service',folder,folder)
            payload=submission(profile,make_snapshot(state)); service.prepare_prompt(payload)
            envelope=payload['extra_data']['extra_pnginfo']['prompt_studio']
            self.assertTrue(envelope['bindings']); self.assertFalse(envelope['problems'])
            self.assertEqual(envelope['texts'],model.bound_texts(state,profile))
            state['uses']['one']['prompt']='later edit'
            self.assertEqual(envelope['texts'][0]['text'],'eyes, blue')
