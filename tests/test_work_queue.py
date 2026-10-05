"""Real persistence/native capture/PNG chain, with an offline Comfy transport."""
import asyncio
import copy
import json
import sqlite3
import unittest
from unittest.mock import patch
import test_binding_submission as bindings_tests
from test_comfy_integration import png, Service
from prompt_calculus_studio.work_queue import WorkQueue
from prompt_calculus_studio.queued_work import validate
from prompt_calculus_studio.snapshots import image_snapshots, restore_snapshot, make_snapshot
from prompt_calculus_studio.pnginfo import png_metadata


class QueueTests(unittest.TestCase):
    setUp = bindings_tests.BindingSubmissionTests.setUp
    upgrade = bindings_tests.BindingSubmissionTests.upgrade

    def capture(self, ident='capture-1', image=False):
        if self.snapshot['state']['multi_output'].get('version',1)<4:
            self.upgrade()
        self.service.work_queue.input_root=self.root
        self.live['capture_protocol']=1
        self.queue.poll(self.live)
        profile=self.snapshot['state']['generation']['profiles'][0]
        profile['graph']['9']=dict(class_type='SaveImage',inputs=dict(images=['5',0],filename_prefix='fixture'))
        if image:
            png(self.root/'original.png',{})
            profile['graph']['20']=dict(class_type='LoadImage',inputs=dict(image='original.png'))
        request=dict(self.request,id=ident)
        self.queue.start(request,'http://127.0.0.1:8188','capture')
        command=self.queue.poll(self.live)['commands'][0]
        graph=copy.deepcopy(profile['graph'])
        graph['5']['inputs']['batch_size']=10
        for text in command['texts']:
            graph[text['node']]['inputs'][text['field']]=text['text']
        visual=dict(id='native-A',nodes=[dict(id=int(k),type=v['class_type'],widgets_values=list(v['inputs'].values())) for k,v in graph.items()])
        self.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=graph,workflow=visual))
        result=self.queue.status(ident)
        self.assertEqual(result['state'],'captured')
        return result['work']

    def request_for(self, work, attempt='attempt-1'):
        return dict(capture=work['capture'],sha256=work['sha256'],job='job-'+attempt,attempt=attempt)

    def post(self, loss=False):
        async def send(payload):
            self.sent.append(copy.deepcopy(payload))
            self.service.prepare_prompt(payload)
            self.assertTrue(payload['prompt'])
            if loss:
                raise OSError('response lost')
            return 200,dict(prompt_id=payload['prompt_id'])
        self.sent=[]
        return send

    def run_submit(self, work, attempt='attempt-1', post=None):
        return asyncio.run(self.service.work_queue.submit(self.request_for(work,attempt),
            'http://127.0.0.1:8188',post or self.post()))

    def completed_history(self, result, work, count=10):
        images=[]
        for index in range(count):
            name=result['id']+'-'+str(index)+'.png'
            png(self.root/name, result['payload']['extra_data']['extra_pnginfo'])
            images.append(dict(filename=name,subfolder='',type='output'))
        return {result['prompt_id']:dict(prompt=[0,result['prompt_id'],copy.deepcopy(work['graph']),{}],
            outputs={'9':dict(images=images)},status=dict(completed=True,status_str='success'))}

    def test_three_batch10_work_items_keep_frozen_values_after_closed_web_and_edits(self):
        work=self.capture()
        self.queue.sessions.clear()
        self.snapshot['state']['generation']['profiles'][0]['graph']['5']['inputs']['width']=32
        calls=[]
        async def post(payload):
            calls.append(copy.deepcopy(payload))
            self.service.prepare_prompt(payload)
            return 200,dict(prompt_id=payload['prompt_id'])
        for index in range(3):
            result=self.run_submit(work,'a'+str(index),post)
            self.assertEqual(result['state'],'queued')
            history=self.completed_history(result,work)
            completed=self.service.work_queue.reconcile(result['id'],set(),set(),history)
            self.assertEqual(completed['state'],'complete')
            self.assertEqual(len(completed['results']),10)
        self.assertEqual(len(calls),3)
        self.assertTrue(all(p['prompt']['5']['inputs']['batch_size']==10 for p in calls))
        self.assertTrue(all(p['prompt']['5']['inputs']['width']==832 for p in calls))

    def test_lost_reply_restart_reconcile_never_posts_again(self):
        work=self.capture()
        result=self.run_submit(work,post=self.post(loss=True))
        self.assertEqual(result['state'],'unconfirmed')
        self.service=Service(self.root,self.root,self.root,input_root=self.root)
        calls=len(self.sent)
        again=self.run_submit(work,post=self.post(loss=True))
        self.assertEqual(again['prompt_id'],result['prompt_id'])
        self.assertEqual(len(self.sent),0)
        self.assertEqual(calls,1)
        completed=self.service.work_queue.reconcile(result['id'],set(),set(),self.completed_history(result,work))
        self.assertEqual(completed['state'],'complete')

    def test_backend_occupancy_blocks_new_attempt_and_native_but_not_external_manual(self):
        work=self.capture()
        self.run_submit(work)
        with self.assertRaisesRegex(ValueError,'未結案'):
            self.run_submit(work,'another')
        with self.assertRaisesRegex(ValueError,'未結案'):
            self.queue.start(dict(self.request,id='native-other'))
        # Capturing the next immutable job is allowed while this job is active;
        # it stores a waiting version and does not submit a second GPU request.
        sent_before=len(self.sent)
        next_work=self.capture('capture-while-running')
        self.assertNotEqual(work['capture'],next_work['capture'])
        self.assertEqual(len(self.sent),sent_before)
        manual=dict(prompt={'x':dict(class_type='SaveImage',inputs={})},extra_data=dict(extra_pnginfo=dict(workflow={})))
        self.service.prepare_prompt(manual)
        self.assertIn('x',manual['prompt'])

    def test_capture_from_inactive_workflow_records_resolved_target_and_epoch(self):
        first=self.capture()
        target=copy.deepcopy(self.live['identity'])
        self.live.update(identity=dict(workflow='',path='B.json',frontend_id='native-B'),workflows=[target],navigation_protocol=1)
        self.queue.poll(self.live)
        self.queue.start(dict(self.request,id='capture-from-B'),'http://127.0.0.1:8188','capture')
        command=self.queue.poll(self.live)['commands'][0]
        value=dict(command,session='tab',client_id='real-web-sid',output=first['graph'],workflow=first['visual'])
        self.queue.activate(dict(value,opened_identity=target,opened_epoch=1))
        self.queue.prepare(value)
        work=self.queue.status(command['id'])['work']
        self.assertEqual(work['scope']['frontend_id'],'native-A')
        self.assertEqual(work['proof']['identity']['frontend_id'],'native-A')
        self.assertEqual(work['proof']['epoch'],1)
        self.assertEqual(work['generation']['native_identity']['frontend_id'],'native-A')

    def test_assets_content_addressed_and_corruption_stops_without_submit(self):
        work=self.capture(image=True)
        asset=work['assets'][0]
        (self.root/'original.png').unlink()
        (self.root/asset['uploaded']).unlink()
        result=self.run_submit(work)
        self.assertEqual(result['state'],'queued')
        self.assertTrue((self.root/asset['uploaded']).is_file())
        self.service.work_queue.reconcile(result['id'],set(),set(),self.completed_history(result,work))
        (self.service.work_queue.assets/asset['name']).write_bytes(b'changed')
        result=self.run_submit(work,'corrupt')
        self.assertEqual(result['state'],'failed')
        self.assertEqual(self.sent,[])

    def test_terminal_and_all_outputs_and_files_required_no_executed_shortcut(self):
        work=self.capture()
        result=self.run_submit(work)
        history=self.completed_history(result,work)
        entry=history[result['prompt_id']]
        entry['status']['completed']=False
        self.assertEqual(self.service.work_queue.reconcile(result['id'],set(),set(),history)['state'],'running')
        entry['status']['completed']=True
        missing=self.root/entry['outputs']['9']['images'][0]['filename']
        raw=missing.read_bytes();missing.unlink()
        self.assertEqual(self.service.work_queue.reconcile(result['id'],set(),set(),history)['state'],'results_pending')
        missing.write_bytes(raw)
        self.assertEqual(self.service.work_queue.reconcile(result['id'],set(),set(),history)['state'],'complete')
        self.assertEqual(len(self.sent),1)

    def test_partial_standard_batch_cannot_advance_even_with_terminal_history(self):
        work=self.capture();result=self.run_submit(work)
        partial=self.completed_history(result,work,count=1)
        pending=self.service.work_queue.reconcile(result['id'],set(),set(),partial)
        self.assertEqual(pending['state'],'failed')
        self.assertIn('未輸出完整圖片',pending['error'])
        complete=self.completed_history(result,work,count=10)
        self.assertEqual(self.service.work_queue.reconcile(result['id'],set(),set(),complete)['state'],'failed')
        self.assertEqual(len(self.sent),1)

    def test_png_source_roundtrip_preserves_draft_and_live_workspace(self):
        work=self.capture()
        before=copy.deepcopy(self.snapshot['state'])
        result=self.run_submit(work)
        history=self.completed_history(result,work)
        filename=history[result['prompt_id']]['outputs']['9']['images'][0]['filename']
        metadata=png_metadata(self.root/filename)
        bindings=image_snapshots(metadata)
        self.assertEqual(bindings[0]['snapshot'],work['snapshot'])
        restored=restore_snapshot(before,bindings[0]['snapshot'])
        self.assertEqual(before,self.snapshot['state'])
        self.assertEqual(restored['workspaces'][:-1],before['workspaces'])
        self.assertEqual(restored['draft'],before['draft'])
        self.assertEqual(metadata['raw']['prompt_studio']['generation']['queue_revision'],work['sha256'])

    def test_sqlite_claim_is_atomic_sort_remove_and_reopen_do_not_requeue_success(self):
        work=self.capture()
        path=self.root/'desktop.db'
        first=sqlite3.connect(path);second=sqlite3.connect(path)
        self.addCleanup(first.close);self.addCleanup(second.close)
        a,b=WorkQueue(first),WorkQueue(second)
        one=a.add(work,work['capture']);two=a.add(work,work['capture']);three=a.add(work,work['capture'])
        a.edit_pending(three['id'],'next')
        claimed=a.claim(work['scope']['server'])
        self.assertEqual(claimed['id'],three['id'])
        self.assertIsNone(b.claim(work['scope']['server']))
        with self.assertRaises(ValueError):b.edit_pending(three['id'],'remove')
        b.edit_pending(two['id'],'remove')
        a.update(three['id'],state='complete')
        self.assertEqual(b.claim(work['scope']['server'])['id'],one['id'])
        self.assertEqual(WorkQueue(second).read(three['id'])['state'],'complete')

    def test_replay_marker_and_changed_snapshot_rejected(self):
        work=self.capture()
        changed=copy.deepcopy(work);changed['graph']['5']['inputs']['width']=1234
        with self.assertRaises(ValueError):validate(changed)
        result=self.run_submit(work)
        replay=copy.deepcopy(self.sent[0])
        with self.assertRaises(ValueError):self.service.prepare_prompt(replay)
        self.assertEqual(replay['prompt'],{})

    def test_retry_keeps_frozen_work_and_attempt_identity_without_old_results(self):
        work=self.capture()
        db=sqlite3.connect(':memory:');self.addCleanup(db.close)
        queue=WorkQueue(db);item=queue.add(work,work['capture'])
        first=queue.claim(work['scope']['server'])
        queue.update(item['id'],state='failed',prompt_id='old-prompt',outputs={'9':{'images':['old.png']}})
        queue.retry(item['id'])
        waiting=queue.read(item['id'])
        self.assertNotIn('outputs',waiting);self.assertNotIn('prompt_id',waiting)
        second=queue.claim(work['scope']['server'])
        self.assertNotEqual(first['attempt'],second['attempt'])
        self.assertEqual(second['work'],work)
        self.assertEqual(len(second['attempts']),2)

    def test_legacy_occupancy_remembers_verified_completion_and_blocks_unknown(self):
        work=self.capture()
        operation=self.queue.read(work['capture'])
        operation.update(id='legacy',action='queue',state='queued',prompt_id='legacy-prompt')
        self.queue.save(operation)
        self.assertTrue(self.queue.occupied([],[],lambda _:{}))
        prompt=[0,'legacy-prompt',operation['graph'],{}]
        history={'legacy-prompt':dict(prompt=prompt,status=dict(completed=True))}
        self.assertFalse(self.queue.occupied([],[],lambda _:history))
        self.assertFalse(self.queue.occupied([],[],lambda _:{}))
        external=[0,'manual',{},{}]
        self.assertFalse(self.queue.occupied([external],[],lambda _:{}))
        pcs=[0,'pcs',{},dict(extra_pnginfo=dict(prompt_studio=dict(schema_version=1)))]
        self.assertTrue(self.queue.occupied([pcs],[],lambda _:{}))

    def test_pure_image_capture_and_three_variants_without_clip_or_web(self):
        self.upgrade();self.service.work_queue.input_root=self.root
        png(self.root/'source.png',{})
        profile=dict(id='image-only',name='純圖片',mode='img2img',multi_text=True,image='20',values={},
            graph={'20':dict(class_type='LoadImage',inputs=dict(image='source.png')),
                   '21':dict(class_type='ImageScale',inputs=dict(image=['20',0],width=1024,height=1024,upscale_method='bicubic',crop='disabled')),
                   '22':dict(class_type='SaveImage',inputs=dict(images=['21',0],filename_prefix='pure'))},
            origin=dict(server='http://127.0.0.1:8188',path='folder/A.json'),frontend_id='native-A')
        self.snapshot['state']['generation']['profiles'].append(profile)
        self.live['capture_protocol']=1;self.queue.poll(self.live)
        request=dict(id='pure',snapshot=self.snapshot,workflow='image-only')
        self.queue.start(request,'http://127.0.0.1:8188','capture')
        command=self.queue.poll(self.live)['commands'][0]
        self.assertEqual(command['texts'],[])
        visual=dict(id='native-A',nodes=[dict(id=int(k),type=v['class_type'],widgets_values=list(v['inputs'].values())) for k,v in profile['graph'].items()])
        self.queue.prepare(dict(command,session='tab',client_id='real-web-sid',output=profile['graph'],workflow=visual))
        base=self.queue.status('pure')['work'];self.queue.sessions.clear()
        hashes=[]
        for index in range(3):
            name=f'input-{index}.png';png(self.root/name,dict(parameters=str(index)))
            variant=self.service.work_queue.variant(dict(id='variant-'+str(index),base='pure',sha256=base['sha256'],node='20',image=name),'http://127.0.0.1:8188')['work']
            self.assertEqual(variant['texts'],[])
            self.assertEqual(variant['graph']['21'],base['graph']['21'])
            hashes.append(variant['assets'][0]['sha256'])
            result=self.run_submit(variant,'image-attempt-'+str(index))
            self.assertEqual(result['state'],'queued')
            images=[]
            # A loader can return multiple frames; retain the complete result set.
            for frame in range(2 if index==2 else 1):
                output=f'out-{index}-{frame}.png';png(self.root/output,result['payload']['extra_data']['extra_pnginfo'])
                images.append(dict(filename=output,type='output',subfolder=''))
            entry=dict(prompt=[0,result['prompt_id'],variant['graph'],{}],outputs={'22':dict(images=images)},status=dict(completed=True))
            self.assertEqual(self.service.work_queue.reconcile(result['id'],set(),set(),{result['prompt_id']:entry})['state'],'complete')
        self.assertEqual(len(set(hashes)),3)

    def test_explicit_metadata_mapping_missing_value_stops_and_empty_fixed_is_valid(self):
        from prompt_calculus_studio.image_iteration import plan_inputs
        png(self.root/'has.png',dict(prompt_studio=dict(schema_version=1,texts=[dict(node='6',field='text',text='from first')],bindings=[])))
        png(self.root/'missing.png',{})
        sources=[dict(relative='has.png'),dict(relative='missing.png')]
        with self.assertRaisesRegex(ValueError,'缺少'):
            plan_inputs(self.root,sources,'metadata',('6','text'),('text','6','text'))
        fixed=plan_inputs(self.root,sources,'fixed',('6','text'),fixed='')
        self.assertEqual([e['text']['value'] for e in fixed],['',''])
        self.assertEqual(len(plan_inputs(self.root,sources)),2)


if __name__=='__main__':unittest.main()
