import asyncio
import copy
import importlib
import tempfile
import unittest
from pathlib import Path
from test_comfy_integration import Service
from test_background_state import graphs,module
from test_multi_output import workspace
from prompt_studio.multi_output import bind,bound_texts
from prompt_studio.snapshots import make_snapshot

BackgroundExecution=importlib.import_module('integration_test.background_execution').BackgroundExecution
BackgroundEvents=importlib.import_module('integration_test.background_events').BackgroundEvents
digest=importlib.import_module('integration_test.native_queue').digest


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);root=Path(self.tmp.name)
        self.service=Service(root,root,root);self.events=BackgroundEvents(lambda *_:None)
        self.runner=BackgroundExecution(self.service,self.events)
        self.state,_,_=workspace();self.visual,self.output,self.seed=graphs()
        self.profile=self.state['generation']['profiles'][0]
        self.profile.update(graph=copy.deepcopy(self.output),sampler='1',size='2',frontend_id=self.visual['id'],
            origin=dict(path='A.json',server='http://127.0.0.1:8188'))
        bind(self.state,'flow','out2','4','text')
        self.snapshot=make_snapshot(self.state,'library')
        self.key=dict(library_id='library',workspace_id=self.state['workspace'],workflow_id='flow',frontend_id=self.visual['id'],path='A.json')
        self.library=dict(state=self.snapshot['state'],library_id='library',connection=dict(enabled=True,server='http://127.0.0.1:8188'))
        self.service.read_library=lambda **kwargs:self.library
        self.server='http://127.0.0.1:8188'
        lease=self.runner.edit('lease',dict(key=self.key,session='tab',base_revision=0),self.server)
        self.capture=dict(key=self.key,**{k:lease[k] for k in ('lease_id','lease_epoch','server_epoch')},
            op_id='capture',base_revision=0,edit_seq=1,visual=self.visual,output=self.output,seed=self.seed,capability=module.CAPABILITY)
        self.capture['source_receipt']=dict(owner='/'.join(self.key[k] for k in ('library_id','workspace_id','workflow_id')),
            source_revision=digest(bound_texts(self.snapshot['state'],self.profile)),texts=[dict(node=b['node'],field=b['field'],
                class_type=self.output[b['node']]['class_type'],text=self.output[b['node']]['inputs'][b['field']],mode='manual')
                for b in bound_texts(self.snapshot['state'],self.profile)])
        self.receipt=self.runner.edit('capture',self.capture,self.server)
        self.release=dict(key=self.key,**{k:lease[k] for k in ('lease_id','lease_epoch','server_epoch')},
            op_id='release',edit_seq=1,revision=self.receipt['revision'],digest=self.receipt['digest'])
        self.request=dict(id='normal-pcs-operation',snapshot=self.snapshot,workflow='flow')

    def run_request(self,post,verify=lambda *_:True):
        return asyncio.run(self.runner.start(self.request,self.server,post,verify))

    def test_background_normal_journal_uses_unsaved_two_image_graph_and_native_marker(self):
        self.runner.edit('release',self.release,self.server)
        posts=[]
        async def post(payload):
            posts.append(copy.deepcopy(payload));self.service.prepare_prompt(payload)
            return 200,dict(prompt_id=payload['prompt_id'])
        result=self.run_request(post)
        self.assertEqual(result['state'],'queued');self.assertEqual(len(posts),1)
        self.assertEqual(posts[0]['prompt']['2']['inputs'],dict(width=1216,height=832,batch_size=2))
        self.assertEqual(result['payload']['prompt']['6']['inputs']['text'],'literal positive')
        stored=self.service.native_queue.read(self.request['id'])
        self.assertEqual(stored['background']['revision'],1)
        self.assertEqual(self.service.background.snapshot(dict(key=self.key))['revision'],2)
        # Normal entry idempotency terminates at the same native operation.
        self.assertEqual(self.service.native_queue.start(self.request)['prompt_id'],result['prompt_id'])

    def test_open_or_dirty_writer_never_posts_old_version(self):
        async def post(_):self.fail('must not submit')
        with self.assertRaisesRegex(ValueError,'not_released'):self.run_request(post)

    def test_changed_pcs_source_requires_resync_instead_of_overwriting_manual_web_text(self):
        self.runner.edit('release',self.release,self.server)
        self.state['uses']['one']['prompt']='later PCS edit'
        self.request['snapshot']=make_snapshot(self.state,'library')
        async def post(_):self.fail('must not submit')
        with self.assertRaisesRegex(ValueError,'source_changed'):self.run_request(post)

    def test_lost_post_reply_stays_uncertain_and_does_not_advance(self):
        self.runner.edit('release',self.release,self.server)
        async def post(payload):
            self.service.prepare_prompt(payload);raise TimeoutError('lost reply')
        result=self.run_request(post)
        self.assertEqual(result['state'],'unconfirmed')
        self.assertEqual(self.service.background.snapshot(dict(key=self.key))['revision'],1)

    def test_backend_graph_mismatch_does_not_advance_or_claim_synchronization(self):
        self.runner.edit('release',self.release,self.server)
        async def post(payload):
            self.service.prepare_prompt(payload);return 200,dict(prompt_id=payload['prompt_id'])
        result=self.run_request(post,lambda *_:False)
        self.assertEqual(result['state'],'unconfirmed')
        self.assertEqual(self.service.background.snapshot(dict(key=self.key))['revision'],1)

    def test_capture_and_release_receipts_are_bound_to_the_same_effective_state(self):
        self.runner.edit('release',self.release,self.server)
        context=self.runner.context(dict(identity=self.key),self.server)
        self.assertEqual(context['capture_op_id'],'capture');self.assertEqual(context['release_op_id'],'release')
        self.assertEqual(context['accepted']['source_revision'],digest(bound_texts(self.snapshot['state'],self.profile)))

    def test_wrong_workspace_and_native_id_are_not_replaced_by_same_name(self):
        wrong=dict(self.capture,key=dict(self.key,workspace_id='other'))
        with self.assertRaisesRegex(ValueError,'切換'):self.runner.edit('capture',wrong,self.server)

    def test_restart_unknown_cannot_become_ready_through_an_available_browser(self):
        self.runner.edit('release',self.release,self.server)
        self.service.background=module.BackgroundState(self.service)
        self.runner=BackgroundExecution(self.service,self.events)
        with self.assertRaisesRegex(ValueError,'鎖定'):
            self.runner.context(dict(cold=True,identity=self.key),self.server)
        with self.assertRaisesRegex(ValueError,'鎖定'):
            self.runner.guard_start(self.request,self.server)

    def test_different_configured_library_cannot_bypass_background_guard_with_available_browser(self):
        self.library['library_id']='different-library'
        with self.assertRaisesRegex(ValueError,'切換'):self.runner.guard_start(self.request,self.server)

    def test_post_queue_http_500_keeps_unresolved_and_prevents_second_operation(self):
        self.runner.edit('release',self.release,self.server)
        async def post(payload):
            self.service.prepare_prompt(payload);return 500,dict(error='after queue.put')
        result=self.run_request(post)
        self.assertEqual(result['state'],'unconfirmed')
        current=self.service.background.snapshot(dict(key=self.key))
        self.assertEqual(current['phase'],'uncertain');self.assertTrue(current['unresolved'])
        self.request['id']='do-not-retry'
        with self.assertRaisesRegex(ValueError,'not_released'):self.run_request(post)
        with self.assertRaisesRegex(ValueError,'鎖定'):self.runner.guard_start(self.request,self.server)

    def test_native_validation_rejection_is_a_proven_pre_queue_failure(self):
        self.runner.edit('release',self.release,self.server)
        async def post(payload):return 400,dict(error=dict(type='prompt_outputs_failed_validation'))
        with self.assertRaisesRegex(ValueError,'拒絕'):self.run_request(post)
        current=self.service.background.snapshot(dict(key=self.key))
        self.assertEqual(current['phase'],'released');self.assertFalse(current['unresolved'])

    def test_new_pcs_source_cannot_label_a_prior_web_graph_as_current(self):
        self.state['uses']['one']['prompt']='PCS changed while Web sync was delayed'
        self.library['state']=make_snapshot(self.state,'library')['state']
        newer=copy.deepcopy(self.capture);newer.update(op_id='capture-race',base_revision=1,edit_seq=2)
        with self.assertRaisesRegex(ValueError,'文字來源'):self.runner.edit('capture',newer,self.server)
        # Even a current source hash cannot claim the old value was PCS-applied.
        newer['source_receipt']['source_revision']=digest(bound_texts(self.library['state'],self.profile))
        for item in newer['source_receipt']['texts']:item['mode']='pcs'
        with self.assertRaisesRegex(ValueError,'內容不符'):self.runner.edit('capture',newer,self.server)
        self.assertEqual(self.service.background.snapshot(dict(key=self.key))['revision'],1)

    def test_source_receipt_restores_only_the_record_of_the_actual_committed_capture(self):
        self.runner.edit('release',self.release,self.server)
        context=self.runner.context(dict(cold=True,identity=self.key),self.server)
        self.assertEqual(context['source_receipt'],self.capture['source_receipt'])

    def test_lost_capture_ack_is_recovered_exactly_even_if_pcs_source_has_changed(self):
        self.state['uses']['one']['prompt']='B after committed A'
        self.library['state']=make_snapshot(self.state,'library')['state']
        recovered=self.runner.edit('capture',self.capture,self.server)
        self.assertEqual(recovered,self.receipt)
        self.assertEqual(self.service.background.snapshot(dict(key=self.key))['revision'],1)

    def test_definitive_source_rejection_names_only_its_uncommitted_operation(self):
        newer=copy.deepcopy(self.capture);newer.update(op_id='reject-this-only',base_revision=1,edit_seq=2)
        self.state['uses']['one']['prompt']='new B'
        self.library['state']=make_snapshot(self.state,'library')['state']
        with self.assertRaises(ValueError) as caught:self.runner.edit('capture',newer,self.server)
        self.assertEqual(caught.exception.operation_id,'reject-this-only')
        with self.service.connect() as db:
            self.assertIsNone(db.execute('SELECT body FROM background_receipts WHERE id=?',('reject-this-only',)).fetchone())

if __name__=='__main__':unittest.main()
