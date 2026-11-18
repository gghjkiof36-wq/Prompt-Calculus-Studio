"""Replay exact successful history followed by loss of temp and history.

Only synthetic PNGs and the loopback result peer are used. Canvas, journals,
native submission, the extension worker/store and desktop acquisition are real.
"""
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from _comfy_history import normalized_prompt
from _nested_cleanup import cleanup_nested
from _qt_wait import wait_ms, wait_until
from prompt_calculus_studio import comfy_result_fetch as fetch, multi_output
from prompt_calculus_studio.flow_data import add_scheduler
import test_partial_node_results as desktop
import test_result_manifest_native as native
import test_work_queue as frozen


class DeliveryStoreTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.f=native.NativeFixture(temp.name,bind=False)
        self.header=self.f.store.bind(self.f.identity,retain=True)
        self.ident=self.header['manifest_id']

    def test_capture_survives_before_seal_but_success_alone_never_seals(self):
        f=self.f
        images=f.images(4)
        contents=[]
        for image in images:
            source=f.root/'output'/image['filename']
            contents.append(source.read_bytes())
            source.rename(f.root/'temp'/source.name)
            image['type']='temp'
        f.store.observe_native('executed',dict(prompt_id='prompt',node='9',output=dict(images=images)),
                               f.service.events.epoch)
        f.store.observe_native('execution_success',dict(prompt_id='prompt'),f.service.events.epoch)
        f.store.reconcile_native('prompt',f.queue)
        self.assertEqual(f.latest()['item_count'],4)
        self.assertFalse(f.latest()['sealed'])
        self.assertIsNone(f.store.deliveries.recover(self.ident))
        f.completed({'9':dict(images=images)})
        with patch.object(f.service.events,'append',side_effect=sqlite3.OperationalError('publication interrupted')):
            with self.assertRaises(sqlite3.OperationalError):f.store.seal_history(self.ident,f.history)
        self.assertFalse(f.latest()['sealed'])
        for image in images:(f.root/'temp'/image['filename']).unlink()
        f.history.clear()
        f.service=native.Service(f.root/'integration',f.root/'output',f.root/'temp')
        f.store=f.service.result_manifests
        f.store.reconcile_native('prompt',f.queue)
        self.assertTrue(f.latest()['sealed'])
        self.assertEqual([f.asset(ordinal=i) for i in range(4)],contents)

    def test_missing_wrong_bytes_ack_owner_and_persistent_output(self):
        f=self.f
        images=f.images(2)
        (f.root/'output'/images[0]['filename']).rename(f.root/'temp'/images[0]['filename'])
        images[0]['type']='temp'
        persistent=f.root/'output'/images[1]['filename']
        expected=persistent.read_bytes()
        f.completed({'9':dict(images=images)})
        missing=copy.deepcopy(f.history)
        missing['prompt']['outputs']['9']['images'][1]['filename']='missing.png'
        with self.assertRaises(OSError):f.store.seal_history(self.ident,missing)
        self.assertFalse(f.latest()['sealed'])
        self.assertIsNone(f.store.deliveries.recover(self.ident))
        missing['prompt']['outputs']['9']['images'][1]={}
        self.assertTrue(f.store.seal_history(self.ident,missing))
        self.assertFalse(f.latest()['sealed'])
        f.store.seal_history(self.ident,f.history)
        header=f.latest()
        ref=(self.ident,header['revision'],header['checksum'])
        receipt=dict(item_count=2,final_digest=header['final_digest'])
        spool=f.store.deliveries.path(self.ident)
        with sqlite3.connect(spool) as db:
            stored=db.execute('SELECT ordinal,length(content) FROM originals ORDER BY ordinal').fetchall()
            self.assertIsNone(stored[1][1])
            # Even a same-sized, valid PNG from the other ordinal is rejected.
            db.execute('UPDATE originals SET content=? WHERE ordinal=0',(expected,))
        db.close()
        with self.assertRaises(ValueError):f.asset(ordinal=0)
        self.assertEqual(f.asset(ordinal=1),expected)
        with self.assertRaises(ValueError):f.store.acknowledge(*ref,dict(f.owner,document_id='other'),receipt)
        with self.assertRaises(ValueError):f.store.acknowledge(*ref,f.owner,dict(receipt,item_count=1))
        with self.assertRaises(ValueError):f.store.acknowledge(*ref,f.owner,dict(receipt,final_digest='0'*64))
        self.assertTrue(spool.exists())
        for _ in range(2):f.store.acknowledge(*ref,f.owner,receipt)
        self.assertFalse(spool.exists())
        self.assertEqual(persistent.read_bytes(),expected)
        self.assertTrue((f.root/'temp'/images[0]['filename']).exists())
        self.assertEqual(f.asset(ordinal=1),expected)

    def test_frozen_submission_before_bind_releases_only_its_completed_lane(self):
        case=frozen.QueueTests('runTest')
        case.setUp()
        self.addCleanup(case.doCleanups)
        case.upgrade()
        case.snapshot['library_id']='library'
        work=case.capture()
        queue=case.service.work_queue
        request=dict(case.request_for(work),result_document='doc')
        posts=[]
        async def post(payload):
            posts.append(payload['prompt_id'])
            case.service.prepare_prompt(payload)
            return 200,dict(prompt_id=payload['prompt_id'])
        address='http://127.0.0.1:8188'
        result=asyncio.run(queue.submit(request,address,post))
        prompt=result['prompt_id']
        second=dict(case.request_for(work,'next'),result_document='doc')
        with self.assertRaises(ValueError):asyncio.run(queue.submit(second,address,post))
        history=case.completed_history(result,work,count=9)
        history[prompt]['prompt'].append(['9'])
        snapshot=work['snapshot']
        owner=dict(library_id=snapshot['library_id'],workspace_id=snapshot['state']['workspace'],document_id='doc')
        store=case.service.result_manifests
        ident=store.headers(owner)['headers'][0]['manifest_id']
        self.assertTrue(store.seal_history(ident,history))
        history=case.completed_history(result,work)
        history[prompt]['prompt'].append(['9'])
        store.seal_history(ident,history)
        self.assertTrue(store.headers(owner)['headers'][0]['sealed'])
        result2=asyncio.run(queue.submit(second,address,post))
        self.assertEqual(queue.reconcile(result['id'],[],[],{})['state'],'complete')
        self.assertEqual(asyncio.run(queue.submit(request,address,post))['prompt_id'],prompt)
        self.assertEqual(posts,[prompt,result2['prompt_id']])

    def test_receipt_route_replays_only_from_original_authenticated_owner(self):
        from test_result_manifest import RouteClient,route_app,ResultRouteTests
        f=self.f
        f.completed({'9':dict(images=f.images(2))})
        f.store.seal_history(self.ident,f.history)
        query=ResultRouteTests.query(SimpleNamespace(f=f))
        header=f.latest()
        receipt=dict(item_count=2,final_digest=header['final_digest'])
        client=RouteClient(route_app(f.service))
        auth={'X-Prompt-Studio':'fixture-token'}
        async def run():
            route='/prompt_studio/results/received'
            for changed,headers,status in (({}, {},403),(dict(epoch='old'),auth,400),
                    (dict(checksum='0'*64),auth,400),(dict(owner=json.dumps(dict(f.owner,document_id='foreign'))),auth,400)):
                res=await client.request('POST',route,params=dict(query,**changed),headers=headers,data=receipt)
                self.assertEqual(res.status,status)
                self.assertTrue(f.store.deliveries.path(self.ident).exists())
            for _ in range(2):
                res=await client.request('POST',route,params=query,headers=auth,data=receipt)
                self.assertEqual(res.status,200)
                self.assertEqual((await res.json())['manifest_id'],self.ident)
        asyncio.run(run())
        self.assertFalse(f.store.deliveries.path(self.ident).exists())


class ReadyOrderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):desktop.acceptance.AcceptanceTests.setUpClass()

    def test_two_parked_documents_remain_in_click_order_with_opposite_ready_order(self):
        h=desktop.acceptance.AcceptanceTests('runTest')
        h.setUp()
        self.addCleanup(cleanup_nested,h)
        a,b=h.f.a,h.f.b
        for tab in (a,b):tab.host.comfy.events.health(dict(state='detected',needs_reconcile=True))
        for tab in (b,a):
            h.f.w.focus_tab(tab)
            controls=tab.host.canvas.execution_bar.controls
            controls.refresh()
            QTest.mouseClick(controls.run_button,Qt.MouseButton.LeftButton)
        a.host.comfy.events.health(dict(state='receiving'))
        wait_ms(80)
        self.assertEqual(h.peer.submissions,[])
        b.host.comfy.events.health(dict(state='receiving'))
        self.assertTrue(wait_until(lambda:len(h.peer.submissions)==1,5000))
        self.assertEqual(h.peer.submissions[0]['prompt']['6']['inputs']['text'],'beta')
        h.drain()
        h.finish_all()
        self.assertEqual([p['prompt']['6']['inputs']['text'] for p in h.peer.submissions],['beta','alpha'])


class DeliveryFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):desktop.PartialNodeResultsTests.setUpClass()

    def open_delivery(self, *, lose_start=False):
        self.lose_start=lose_start
        self.t=desktop.PartialNodeResultsTests('runTest')
        with patch.object(desktop.acceptance.AcceptanceTests,'start',self.start_batch):
            self.t.setUp()
        self.addCleanup(cleanup_nested,self.t)

    def start_batch(self):
        t=self.t
        t.h.identity.update(result_manifest_v1=True,result_delivery_v1=True)
        t.bind()
        tab=t.f.a
        canvas=tab.host.canvas
        def add_batch(state):
            stage=next(iter(state['multi_output']['stages']))
            scheduler=add_scheduler(state)
            multi_output.connect(state,stage,scheduler+'::flow','flow')
        self.assertTrue(canvas.commit(add_batch))
        tab.document.save()
        t.f.w.focus_tab(tab)
        controls=canvas.execution_bar.controls
        controls.count.setValue(2)
        controls.refresh()
        events=t.client.events
        events.health(dict(state='detected',needs_reconcile=True))
        self.assertFalse(events.ready)
        if self.lose_start:
            t.h.lost_submit=True
            self.original_transport=t.f.transport_request
            def interrupted(client,route,*args,**kwargs):
                if route=='workflow/native/recovery':return
                return self.original_transport(client,route,*args,**kwargs)
            t.f.transport_request=interrupted
        QTest.mouseClick(controls.run_button,Qt.MouseButton.LeftButton)
        wait_ms(60)
        runs=t.runner.runs(True)
        self.assertEqual(len(runs),1)
        self.run_id=runs[0]['id']
        self.assertEqual(len(t.peer.submissions),0)
        self.assertEqual(t.runner.store.batch_heads(runs[0]['workspace'])[0]['expanded'],0)
        t.client.connected=False
        t.client.input_flow.disconnected()
        self.assertEqual(t.runner.store.read(self.run_id)['status'],'running')
        t.client.connected=True
        # No second click, poll, explicit pump or resume.
        for _ in range(2):events.health(dict(state='receiving'))
        self.assertTrue(wait_until(lambda:len(t.peer.submissions)==1,3500))
        if self.lose_start:
            job=next(iter(t.client.generation.jobs.values()))
            self.assertFalse(job.get('prompt_id'))
            self.assertFalse(job.get('payload',{}).get('prompt'))
            return job['id'],t.peer.submissions[0]['prompt_id']
        t.h.drain()
        job=next(iter(t.client.generation.jobs.values()))
        self.assertEqual(t.runner.store.read(job['id'])['owner'],self.run_id)
        self.assertEqual(t.f.b.host.comfy.input_flow.chain.runs(),[])
        return job['id'],job['prompt_id']

    def finish_offline(self):
        t=self.t
        t.stop_workers()
        images=t.images(4)
        payload=t.peer.pending.pop(t.prompt)
        t.peer.history[t.prompt]=dict(prompt=[0,t.prompt,normalized_prompt(payload['prompt']),payload['extra_data'],['9']],
            outputs={'9':dict(images=images)},status=dict(completed=True,status_str='success',messages=[]))
        store=t.peer.service.result_manifests
        queue=SimpleNamespace(get_history=lambda prompt_id:{prompt_id:t.peer.history[prompt_id]})
        buffer=native.m.ResultManifestBuffer(t.peer.service,queue=queue)
        buffer.observe('executed',dict(prompt_id=t.prompt,node='9',output=dict(images=images)))
        buffer.observe('execution_success',dict(prompt_id=t.prompt))
        buffer.drain()
        expected=[hashlib.sha256((t.peer.root/im['filename']).read_bytes()).hexdigest() for im in images]
        owner=dict(library_id=t.client.window.store.library_id,workspace_id=t.client.window.state['workspace'],
                   document_id=t.manager.document_id)
        header=store.headers(owner)['headers'][0]
        self.assertEqual(header['identity']['operation']['id'],t.ident)
        self.assertEqual(header['identity']['prompt_id'],t.prompt)
        self.assertTrue(header['sealed'])
        self.assertEqual(header['expected_count'],4)
        self.spool=store.deliveries.path(header['manifest_id'])
        for image in images:(t.peer.root/image['filename']).unlink()
        t.peer.history.clear()
        service=t.peer.service
        service.close()
        service=type(service)(service.directory,t.peer.root,t.peer.root)
        for peer in t.f.executors.values():peer.service,peer.native=service,service.native_queue
        t.h.identity['epoch']=service.events.epoch
        t.h.since='0'
        return expected

    def check_collection(self, half, *, lose_start=False):
        self.open_delivery(lose_start=lose_start)
        t=self.t
        expected=self.finish_offline()
        if lose_start:
            self.assertFalse(t.client.generation.record(t.ident).get('prompt_id'))
            t.f.transport_request=self.original_transport
            t.h.lost_submit=False
        if half:
            def after_two():
                return sum(row[1]=='ready' for row in t.candidates('history'))==2
            original=fetch.asset
            def limited(reader,packet,item,*args,**kwargs):
                if item['ordinal']>=2:raise OSError('isolated PCS offline')
                return original(reader,packet,item,*args,**kwargs)
            with patch.object(fetch,'FINAL_BATCH',2),patch.object(fetch,'asset',limited):
                t.bind()
                self.assertTrue(wait_until(after_two,5000))
                self.assertEqual(t.continuations(),0)
                t.stop_workers()
        t.server.lose_receipt=True
        t.reopen()
        complete=wait_until(lambda:t.client.generation.record(t.ident)['state']=='complete',8000)
        if not complete:
            job=t.client.generation.record(t.ident)
            self.fail(dict(job_state=job['state'],has_prompt=bool(job.get('prompt_id')),has_payload=bool(job.get('payload')),
                           result_pending=list(t.manager.pending),reconcile={k:{n:r.get(n) for n in ('reason','error','busy','tries')}
                           for k,r in t.client.events.reconcile.pending.items()},recent_calls=t.h.calls[-8:],
                           diagnostics=t.client.events.diagnostics[-3:],route=t.f.notices[-2:]))
        attempt=t.runner.store.read(t.ident)
        final=attempt['result']['images']
        hashes=[hashlib.sha256((t.client.window.store.directory/image['relative']).read_bytes()).hexdigest() for image in final]
        self.assertEqual(hashes,expected)
        self.assertEqual([(im['reference']['node'],im['reference']['index']) for im in final],[('9',i) for i in range(4)])
        self.assertEqual(t.continuations(),1)
        submitted=wait_until(lambda:len(t.peer.submissions)==2,5000)
        run=t.runner.store.read(self.run_id)
        self.assertTrue(submitted,(run['status'],run.get('pause_reason'),run['message'],
                                   t.client.connected,t.client.events.ready,t.f.notices))
        self.assertNotEqual(t.peer.submissions[1]['prompt_id'],t.prompt)
        self.assertEqual(len(t.runner.store.rows('attempt',owner=self.run_id)),2)
        self.assertEqual({row['workspace'] for row in t.runner.store.rows('attempt',owner=self.run_id)},
                         {run['workspace']})
        self.assertTrue(wait_until(lambda:not self.spool.exists(),5000))
        db=t.client.window.store.db
        self.assertTrue(wait_until(lambda:not db.execute('SELECT 1 FROM generation_result_acks').fetchone(),6500))
        self.assertGreaterEqual(sum(route=='received' for route,_ in t.server.calls),2)
        self.assertEqual(t.continuations(),1)
        self.assertEqual(len(t.peer.submissions),2)
        print(json.dumps(dict(interrupted_after=2 if half else 0,original_reply_lost=lose_start,hashes=hashes,continuations=1,
                              submissions=2,acknowledgements=sum(route=='received' for route,_ in t.server.calls))))

    def test_empty_local_after_backend_restart(self):self.check_collection(False)
    def test_partial_local_after_backend_restart(self):self.check_collection(True)
    def test_lost_initial_reply_and_empty_local_after_restart(self):self.check_collection(False,lose_start=True)

    def test_manual_pause_survives_result_recovery(self):
        self.open_delivery()
        t=self.t
        t.runner.pause(self.run_id)
        self.finish_offline()
        t.reopen()
        t.until(lambda:t.client.generation.record(t.ident)['state']=='complete')
        for _ in range(2):t.client.events.health(dict(state='receiving'))
        wait_ms(80)
        run=t.runner.store.read(self.run_id)
        self.assertEqual((run['status'],run['pause_reason']),('paused','manual'))
        self.assertEqual(len(t.peer.submissions),1)
