"""Acceptance-layer evidence only; no frontend, transport or GPU claims."""
import copy
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path

from prompt_studio.core import Storage, initial_state
from prompt_studio.generation import (initialize_effective_profile,
    accept_effective_profile, validate_profile)


def fixture():
    return dict(id='A', name='A', mode='txt2img', multi_text=True,
        graph={'size':dict(class_type='EmptyLatentImage', inputs=dict(width=832,height=1216,batch_size=1)),
               'pos':dict(class_type='CLIPTextEncode',inputs=dict(text='old positive')),
               'neg':dict(class_type='CLIPTextEncode',inputs=dict(text='old negative'))},
        size='size',values=dict(width=1024),seed_mode='fixed')


def operation(profile, ident='edit-1', source='web'):
    value=profile['pcs_effective']
    return dict(operation_id=ident,scope=copy.deepcopy(value['scope']),epoch=value['epoch'],
        base_revision=value['revision'],source=source,graph=copy.deepcopy(profile['graph']),text_edits=[])


class EffectiveWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.scope=dict(library='library',workspace='workspace',workflow='A',server='http://127.0.0.1:8188')
        self.profile=initialize_effective_profile(fixture(),self.scope,
            [dict(node='pos',field='text',text='PCS original'),dict(node='neg',field='text',text='PCS original')])

    def test_legacy_values_are_applied_once_and_not_claimed_as_web_sync(self):
        self.assertEqual(self.profile['graph']['size']['inputs']['width'],1024)
        self.assertEqual(self.profile['pcs_effective']['source'],'legacy-selection')
        self.assertEqual(initialize_effective_profile(self.profile,self.scope,[]),self.profile)
        newer=operation(self.profile); newer['graph']['size']['inputs']['width']=1536
        accepted,_=accept_effective_profile(self.profile,newer)
        self.assertEqual(accepted['values']['width'],1536)
        stale=copy.deepcopy(accepted); stale['values']['width']=832
        with self.assertRaisesRegex(ValueError,'舊值'): validate_profile(stale)
        self.assertEqual(self.profile['graph']['size']['inputs']['width'],1024)

    def test_two_clip_destinations_and_empty_manual_value_keep_sources_separate(self):
        edit=operation(self.profile)
        edit['graph']['pos']['inputs']['text']=''
        edit['graph']['neg']['inputs']['text']='manual C'
        edit['text_edits']=[['pos','text'],['neg','text']]
        accepted,_=accept_effective_profile(self.profile,edit)
        self.assertEqual(accepted['graph']['pos']['inputs']['text'],'')
        self.assertEqual(accepted['graph']['neg']['inputs']['text'],'manual C')
        self.assertEqual({v['source'] for v in accepted['pcs_effective']['texts']},{'web'})
        self.assertEqual(self.profile['graph']['pos']['inputs']['text'],'PCS original')
        undeclared=operation(self.profile); undeclared['graph']['pos']['inputs']['text']='hidden edit'
        with self.assertRaisesRegex(ValueError,'明確編輯'): accept_effective_profile(self.profile,undeclared)

    def test_same_base_conflict_and_same_operation_retry(self):
        first=operation(self.profile); first['graph']['size']['inputs']['height']=1536
        accepted,receipt=accept_effective_profile(self.profile,first)
        repeated,repeated_receipt=accept_effective_profile(accepted,first)
        self.assertEqual(repeated,accepted); self.assertEqual(receipt,repeated_receipt)
        competitor=operation(self.profile,'edit-2'); competitor['graph']['size']['inputs']['width']=768
        with self.assertRaisesRegex(ValueError,'版本衝突'): accept_effective_profile(accepted,competitor)
        first['graph']['size']['inputs']['height']=1024
        with self.assertRaisesRegex(ValueError,'不同內容'): accept_effective_profile(accepted,first)

    def test_other_workspace_server_and_recreated_epoch_cannot_write(self):
        for field in ('library','workspace','workflow','server'):
            edit=operation(self.profile); edit['scope'][field]='other'
            with self.subTest(field=field),self.assertRaises(ValueError): accept_effective_profile(self.profile,edit)
        recreated=initialize_effective_profile(fixture(),self.scope)
        with self.assertRaisesRegex(ValueError,'舊世代'): accept_effective_profile(recreated,operation(self.profile))

    def test_visual_layout_is_preserved_but_not_falsely_marked_current(self):
        edit=operation(self.profile); edit['workflow']={'nodes':[],'extra':{'layout':'fixture'}}
        accepted,_=accept_effective_profile(self.profile,edit)
        next_edit=operation(accepted,'edit-2','pcs'); next_edit['graph']['size']['inputs']['width']=768
        newer,_=accept_effective_profile(accepted,next_edit)
        self.assertEqual(newer['pcs_workflow'],edit['workflow'])
        self.assertEqual(newer['pcs_workflow_revision'],2)
        self.assertEqual(newer['pcs_effective']['revision'],3)

    def test_changed_graph_and_unknown_protocol_fail_validation(self):
        altered=copy.deepcopy(self.profile); altered['graph']['pos']['inputs']['text']='tampered'
        with self.assertRaisesRegex(ValueError,'摘要'): validate_profile(altered)
        altered=copy.deepcopy(self.profile); altered['pcs_effective']['protocol']=2
        with self.assertRaisesRegex(ValueError,'協定'): validate_profile(altered)


class EffectiveStorageTests(unittest.TestCase):
    def setUp(self):
        root=Path(__file__).resolve().parents[1]
        self.directory=tempfile.TemporaryDirectory(dir=root/'qa')
        self.store=Storage(self.directory.name)
        self.state=initial_state()
        scope=dict(library=str(uuid.uuid5(uuid.NAMESPACE_URL,str((self.store.directory/'studio.sqlite3').resolve()).casefold())),
            workspace=self.state['workspace'],workflow='A',server='http://127.0.0.1:8188')
        self.profile=initialize_effective_profile(fixture(),scope)
        self.state['generation']=dict(mode='txt2img',profiles=[self.profile],chosen={},source=None)
        self.store.save(self.state)

    def tearDown(self):
        self.store.db.close(); self.directory.cleanup()

    def test_commit_reopen_and_lost_ack_retry_keep_one_revision(self):
        edit=operation(self.profile); edit['graph']['size']['inputs']['width']=1536
        accepted,receipt=self.store.commit_workflow_update(self.state,'A',edit)
        self.assertEqual(self.state['generation']['profiles'][0]['pcs_effective']['revision'],1)
        self.store.db.close(); self.store=Storage(self.directory.name)
        reopened=self.store.load()
        self.assertEqual(reopened,accepted)
        repeated,again=self.store.commit_workflow_update(reopened,'A',edit)
        self.assertEqual(again,receipt)
        self.assertEqual(repeated['generation']['profiles'][0]['pcs_effective']['revision'],2)

    def test_save_failure_does_not_return_receipt_or_mutate_memory(self):
        edit=operation(self.profile); edit['graph']['size']['inputs']['width']=1536
        before=copy.deepcopy(self.state)
        self.store.db.execute("CREATE TRIGGER fail_save BEFORE INSERT ON document BEGIN SELECT RAISE(ABORT,'fixture save failure'); END")
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError): self.store.commit_workflow_update(self.state,'A',edit)
        self.assertEqual(self.state,before); self.assertEqual(self.store.load(),before)

    def test_stale_desktop_candidate_cannot_overwrite_committed_document(self):
        first=operation(self.profile); first['graph']['size']['inputs']['width']=1536
        accepted,_=self.store.commit_workflow_update(self.state,'A',first)
        other=operation(self.profile,'edit-2'); other['graph']['size']['inputs']['height']=1024
        with self.assertRaisesRegex(ValueError,'保存版本'): self.store.commit_workflow_update(self.state,'A',other)
        self.assertEqual(self.store.load(),accepted)


class EffectiveSubmissionTests(unittest.TestCase):
    def setUp(self):
        from test_multi_output import workspace
        from prompt_studio import clip_flow, multi_output
        old,_,output=workspace(); self.state=clip_flow.upgrade(old)
        clip=next(b['clip'] for b in self.state['multi_output']['bindings'] if b['node']=='7')
        multi_output.connect(self.state,output,clip,'clip')
        profile=self.state['generation']['profiles'][0]
        scope=dict(library='fixture-library',workspace=self.state['workspace'],workflow=profile['id'],server='http://127.0.0.1:8188')
        profile=initialize_effective_profile(profile,scope,multi_output.bound_texts(self.state,profile))
        edit=operation(profile)
        edit['graph']['6']['inputs']['text']=''
        edit['graph']['7']['inputs']['text']='independent C'
        edit['graph']['3']['inputs']['steps']=30
        edit['text_edits']=[['6','text'],['7','text']]
        self.profile,_=accept_effective_profile(profile,edit)
        self.state['generation']['profiles'][0]=self.profile

    def payload(self):
        from prompt_studio.snapshots import make_snapshot
        from prompt_studio.generation import submission
        return submission(self.profile,make_snapshot(self.state,'fixture-library'))

    def test_actual_payload_and_service_preserve_two_manual_destinations_and_original(self):
        from test_comfy_integration import Service
        payload=self.payload()
        self.assertEqual(payload['prompt']['6']['inputs']['text'],'')
        self.assertEqual(payload['prompt']['7']['inputs']['text'],'independent C')
        self.assertEqual(payload['prompt']['3']['inputs']['steps'],30)
        marker=payload['extra_data']['extra_pnginfo']['prompt_studio_request']
        self.assertEqual({v['text'] for v in marker['source_texts']},{'eyes, blue'})
        original=copy.deepcopy(marker['snapshot'])
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]/'qa') as directory:
            root=Path(directory); service=Service(root/'service',root/'output',root/'temp')
            result=service.prepare_prompt(payload)['extra_data']['extra_pnginfo']['prompt_studio']
        self.assertEqual(result['problems'],[])
        self.assertEqual(result['bindings'][0]['snapshot'],original)
        self.assertEqual(result['generation']['effective']['revision'],2)

    def test_validator_rejects_tampered_parameters_or_effective_revision(self):
        from prompt_studio.generation import validate_effective_submission
        for tamper in ('graph','revision','source'):
            payload=self.payload(); direct=payload['extra_data']['extra_pnginfo']['prompt_studio_request']
            if tamper=='graph': payload['prompt']['3']['inputs']['steps']=20
            elif tamper=='revision': direct['generation']['effective']['revision']=1
            else: direct['texts'][0]['source']='pcs'
            with self.subTest(tamper=tamper),self.assertRaises(ValueError):
                validate_effective_submission(direct,payload['prompt'])

    def test_snapshot_freezes_effective_values_after_next_accepted_edit(self):
        payload=self.payload()
        edit=operation(self.profile,'next'); edit['graph']['3']['inputs']['steps']=35
        self.state['generation']['profiles'][0],_=accept_effective_profile(self.profile,edit)
        self.assertEqual(payload['prompt']['3']['inputs']['steps'],30)
        self.assertEqual(self.state['generation']['profiles'][0]['graph']['3']['inputs']['steps'],35)


if __name__=='__main__': unittest.main()
