import copy,importlib,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from test_multi_output import workspace
from test_comfy_integration import Service
from prompt_studio.core import Storage
from prompt_studio import clip_flow,workflow_flow,multi_output,composition
from prompt_studio.generation import submission
from prompt_studio.snapshots import make_snapshot

sync=importlib.import_module('integration_test.workflow_state')
SERVER='http://127.0.0.1:8188'


class LiveTextTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        root=Path(self.tmp.name)
        self.store=Storage(root/'desktop'); self.addCleanup(self.store.db.close)
        self.service=Service(root/'server',root/'output',root/'temp',str(self.store.directory))
        state,self.cid,self.oid=workspace()
        self.state=workflow_flow.upgrade(clip_flow.upgrade(state))
        self.state['settings'].update(comfy_enabled=True,comfy_url=SERVER)
        self.profile=self.state['generation']['profiles'][0]
        self.profile['origin']=dict(server=SERVER,path='folder/A.json')
        self.query=dict(path='folder/A.json')
    def live(self):
        self.store.save(self.state)
        return sync.live_state(self.query,self.service.read_library(include_connection=True),SERVER)
    def test_saved_edits_sync_without_submission_and_frozen_job_is_unchanged(self):
        job=submission(self.profile,make_snapshot(self.state)); frozen=copy.deepcopy(job)
        before=self.live()
        self.state['uses']['new']=composition.node('standing','standing')
        multi_output.assign(self.state,'new',self.cid)
        after=self.live()
        self.assertIn('standing',after['texts'][0]['text']); self.assertNotEqual(before['revision'],after['revision'])
        self.state['uses']['new']['enabled']=False
        self.assertNotIn('standing',self.live()['texts'][0]['text'])
        self.state['draft']=''
        self.assertEqual(self.live()['texts'][0]['text'],'')
        self.assertEqual(job,frozen)
        self.assertNotIn('8',[t['node'] for t in after['texts']])
        self.assertNotIn('connection',self.service.read_library())
    def test_unbinding_and_disconnection_never_fall_back_to_old_task_text(self):
        first=self.live(); self.state['multi_output']['bindings']=[]
        self.assertEqual(self.live()['texts'],[])
        self.state['settings']['comfy_enabled']=False; self.assertIsNone(self.live())
        self.assertEqual(first['name'],'flow')
    def test_identity_checks_server_path_native_id_and_duplicate_profile(self):
        self.query['path']='other/A.json'; self.assertIsNone(self.live())
        self.query['path']='folder/A.json'; self.profile['frontend_id']='native'
        self.assertIsNone(self.live()); self.query['frontend_id']='native'; self.assertIsNotNone(self.live())
        self.state['settings']['comfy_url']='http://127.0.0.1:8189'; self.assertIsNone(self.live())
        self.state['settings']['comfy_url']=SERVER
        other=copy.deepcopy(self.profile); other['id']='duplicate'; self.state['generation']['profiles'].append(other)
        self.assertIsNone(self.live())
    def test_source_revision_matches_native_bound_text_digest_for_unicode_empty_and_unbind(self):
        digest=importlib.import_module('integration_test.native_queue').digest
        bound_texts=importlib.import_module('integration_test.shared.multi_output').bound_texts
        for draft in ('光與影', ''):
            with self.subTest(draft=draft):
                self.state['draft']=draft
                live=self.live()
                library=self.service.read_library(include_connection=True)
                profile=library['state']['generation']['profiles'][0]
                self.assertEqual(live['source_revision'],digest(bound_texts(library['state'],profile)))
        self.state['multi_output']['bindings']=[]
        live=self.live();self.assertEqual(live['texts'],[]);self.assertIsNone(live['source_revision'])
    def test_source_change_has_new_hash_without_changing_the_previously_returned_live_receipt(self):
        first=self.live();frozen=copy.deepcopy(first)
        self.state['draft']='new PCS source B'
        second=self.live()
        self.assertNotEqual(first['source_revision'],second['source_revision'])
        self.assertEqual(first,frozen)
    def test_compile_failure_is_not_an_acknowledged_empty_source(self):
        with patch('integration_test.shared.multi_output.bound_texts',side_effect=ValueError('fixture invalid binding')):
            live=self.live();self.assertEqual(live['texts'],[]);self.assertIsNone(live['source_revision'])

