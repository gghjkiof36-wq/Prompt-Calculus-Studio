"""PCS acquisition against a loopback manifest server and real Stage owner.

The executor replaces ComfyUI/GPU. Lifecycle receipts, the native manifest
observer, HTTP result transport, worker files and document transactions are real.
"""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import threading
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import unittest
from _result_files import change_result_file
from unittest.mock import patch
from _comfy_history import normalized_prompt

from PySide6.QtCore import QEventLoop, QTimer
import test_event_acceptance_atomic as acceptance
from test_comfy_integration import png
from prompt_calculus_studio import comfy_result_fetch as fetch
from prompt_calculus_studio.comfy_event_protocol import encode
from prompt_calculus_studio.workspace_runtime import unfinished


class ManifestServer:
    def __init__(self, peer):
        self.peer=peer;self.calls=[];self.fail_assets=False;self.corrupt=False;self.mutate=None
        self.reconcile_on_bind=True
        outer=self
        class Handler(BaseHTTPRequestHandler):
            disable_nagle_algorithm=True
            def log_message(self,*args):pass
            def do_GET(self):self.dispatch()
            def do_POST(self):self.dispatch()
            def dispatch(self):
                path=urlsplit(self.path);q={k:v[0] for k,v in parse_qs(path.query,keep_blank_values=True).items()}
                route=path.path.removeprefix('/prompt_studio/results/')
                outer.calls.append((route,threading.get_ident()))
                try:
                    if self.headers.get('X-Prompt-Studio')!='fixture':raise ValueError('token')
                    store=outer.peer.service.result_manifests
                    owner=json.loads(q['owner'])
                    scope=json.loads(q['scope'])
                    if any(scope[k]!=owner[k] for k in ('library_id','workspace_id')):raise ValueError('scope')
                    log=outer.peer.service.events
                    if (q['server_instance'],q['epoch'])!=(log.server_instance,log.epoch):raise ValueError('epoch')
                    if route=='manifests/bind':
                        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                        store.bind(body)
                        queue=SimpleNamespace(get_history=lambda prompt_id:copy.deepcopy({prompt_id:outer.peer.history[prompt_id]}) if prompt_id in outer.peer.history else {})
                        if outer.reconcile_on_bind:store.reconcile_native(body['prompt_id'],queue)
                        with outer.peer.service.connect() as db:value=store._header(db,fetch.digest(body))
                    else:
                        ref=(q['manifest_id'],int(q['revision']),q['checksum'],owner)
                        if route=='manifest':value=store.page(*ref,after=int(q['after']))
                        elif route=='asset':
                            if outer.fail_assets:raise OSError('copy unavailable')
                            value=store.asset(*ref,ordinal=int(q['ordinal']),asset_sha256=q['asset_sha256'],
                                              expires=int(q['expires']),token=q['asset_token'])
                            if outer.corrupt:value=value[:-1]+b'!'
                        else:raise ValueError('unexpected route')
                    if outer.mutate and route=='manifest':value=outer.mutate(copy.deepcopy(value))
                    content=value if isinstance(value,bytes) else encode(value)
                    self.send_response(200);self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
                except Exception as error:
                    content=encode(dict(error=str(error)));self.send_response(500)
                    self.send_header('Content-Length',str(len(content)));self.end_headers();self.wfile.write(content)
        self.http=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.http.serve_forever,daemon=True);self.thread.start()
        self.url='http://127.0.0.1:'+str(self.http.server_port)

    def close(self):
        self.http.shutdown();self.http.server_close();self.thread.join(2)


class PartialNodeResultsTests(unittest.TestCase):
    setUpClass=classmethod(lambda cls:acceptance.AcceptanceTests.setUpClass())

    def setUp(self):
        self.h=acceptance.AcceptanceTests('runTest');self.h.setUp()
        self.addCleanup(self.h.doCleanups)
        self.f=self.h.f;self.peer=self.h.peer
        self.server=ManifestServer(self.peer);self.addCleanup(self.server.close)
        self.gui=threading.get_ident();self.asset_threads=[]
        original=fetch.asset
        def record(*args,**kwargs):
            self.asset_threads.append(threading.get_ident())
            return original(*args,**kwargs)
        self.asset_patch=patch.object(fetch,'asset',record);self.asset_patch.start();self.addCleanup(self.asset_patch.stop)
        self.ident,self.prompt=self.h.start()
        self.bind()
        self.addCleanup(self.stop_workers)

    @property
    def client(self):return self.h.client
    @property
    def runner(self):return self.client.input_flow.chain
    @property
    def manager(self):return self.client.events.results

    def bind(self):
        server=self.server
        class Reader(fetch.ManifestReader):
            def __init__(self,url,*args):super().__init__(server.url,*args)
        self.client.token='fixture'
        self.manager.reader_type=Reader
        self.client.events.bind(dict(self.h.identity,result_manifest_v1=True))
        self.client.events.ready=self.client.events.snapshot_ready=True

    def stop_workers(self):
        manager=self.manager;manager.close()
        self.until(lambda:not manager.work,timeout=10)

    def until(self, predicate, *, timeout=12):
        end=time.monotonic()+timeout
        loop=QEventLoop();timer=QTimer();timer.setInterval(10)
        def tick():
            if predicate() or time.monotonic()>=end:loop.quit()
        timer.timeout.connect(tick);timer.start()
        if not predicate():loop.exec()
        timer.stop()
        self.assertTrue(predicate(),dict(errors=self.f.errors,pending={k:{n:v for n,v in r.items() if n in ('phase','error','busy','tries')} for k,r in self.manager.pending.items()}))

    def images(self, count=1, node='9', *, suffix='', same=False):
        payload=self.peer.pending.get(self.prompt) or self.peer.submissions[0]
        values=[]
        for i in range(count):
            filename=f'{self.prompt}-{node.replace(":","_")}-{suffix}-{i}.png'
            png(self.peer.root/filename,dict(prompt=payload['prompt'],**payload['extra_data']['extra_pnginfo'],
                position=0 if same else i))
            values.append(dict(filename=filename,subfolder='',type='temp'))
        return values

    def executed(self, node, images, path=None):
        data=dict(prompt_id=self.prompt,node=node,output=dict(images=images))
        if path:data['node_path']=path
        self.peer.service.result_manifests.observe_native('executed',data,self.h.identity['epoch'],capture_id=str(time.monotonic_ns()))
        self.refresh()

    def refresh(self):
        self.manager.ensure(self.client.events.job(self.ident),force=True)

    def terminal(self, outputs, *, error=False, interrupted=False, deliver=True, requested=None):
        payload=self.peer.pending.pop(self.prompt,None) or self.peer.submissions[0]
        self.peer.history[self.prompt]=dict(prompt=[0,self.prompt,normalized_prompt(payload['prompt']),payload['extra_data'],requested if requested is not None else ['9']],
            outputs=outputs,status=dict(completed=True,status_str='error' if error or interrupted else 'success',
                messages=[['execution_interrupted',{}]] if interrupted else [['execution_error',{}]] if error else []))
        event='execution_interrupted' if interrupted else 'execution_error' if error else 'execution_success'
        self.peer.service.result_manifests.observe_native(event,dict(prompt_id=self.prompt),self.h.identity['epoch'])
        if deliver:self.client.events.receive(self.h.terminal(self.prompt),lambda:None)
        else:self.client.events.reconcile.require(self.ident,'recovered_connection',force=True)
        self.client.events.reconcile.tick();self.refresh()

    def candidates(self, source='candidates'):
        return list(self.client.window.store.db.execute('SELECT body,state,receipt FROM channel_result_candidates WHERE operation=? AND source=? ORDER BY ordinal',(self.ident,source)))

    def ready_candidates(self, count):
        # The worker commit precedes its queued owner notification. Observe both.
        self.until(lambda:len([r for r in self.candidates() if r[1]=='ready'])==count and
                   self.manager.pending.get(self.ident,{}).get('phase')!='ready_write')
        self.assertEqual(len(self.candidates()),count)
        self.assertNotIn('result',self.runner.store.read(self.ident))
        self.assertEqual(self.continuations(),0)
        self.assertEqual(len(self.peer.submissions),1)

    def continuations(self):
        return self.client.window.store.db.execute("SELECT count(*) FROM channel_applied_events WHERE disposition='local_continuation' AND entity=?",('attempt:'+self.ident,)).fetchone()[0]

    def assert_complete(self,count):
        self.until(lambda:self.client.generation.record(self.ident)['state']=='complete')
        attempt=self.runner.store.read(self.ident)
        self.assertEqual(len(attempt['result']['images']),count)
        self.assertEqual(self.continuations(),1)
        self.assertEqual(len(self.peer.submissions),1)
        self.assertEqual(len(self.candidates('history')),count)
        self.assertEqual(len({json.loads(r[0])['ordinal'] for r in self.candidates('history')}),count)
        self.assertNotIn(self.gui,self.asset_threads)
        self.assertFalse(self.f.errors,self.f.errors)
        return attempt

    def test_01_137_burst_candidates_never_advance_final_once(self):
        images=self.images(137);self.executed('9',images);self.ready_candidates(137)
        self.assertEqual(self.runner.results,{})
        before=len([r for r in self.server.calls if r[0]=='asset'])
        self.terminal({'9':dict(images=images)});attempt=self.assert_complete(137)
        self.assertEqual(len([r for r in self.server.calls if r[0]=='asset']),before,'final reuses exact candidate assets')
        self.client.events.receive(self.h.terminal(self.prompt),lambda:None)
        self.h.drain()
        self.assertEqual(self.runner.store.read(self.ident)['result'],attempt['result'])
        self.assertEqual(self.continuations(),1);self.assertEqual(len(self.candidates()),0)

    def test_02_partial_cache_final_adds_unobserved_node(self):
        early=self.images(2);cached=self.images(3,'nested:cache')
        self.executed('9',early);self.ready_candidates(2)
        self.terminal({'9':dict(images=early),'nested:cache':dict(images=cached)})
        self.assert_complete(5);self.assertEqual(len(self.candidates()),0)

    def test_03_all_cache(self):
        images=self.images(3);self.terminal({'9':dict(images=images)})
        self.assert_complete(3);self.assertEqual(self.candidates(),[])

    def test_04_zero_history_and_unsealed_empty(self):
        self.until(lambda:self.client.events.job(self.ident).get('result_manifest'))
        self.assertEqual(self.continuations(),0)
        # A text-only prompt explicitly has no requested image-output nodes.
        self.terminal({'6':dict(text=['text'])},requested=['6'])
        self.assert_complete(0);self.assertEqual(self.candidates(),[])

    def test_05_partial_error_keeps_candidates(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        self.terminal({'9':dict(images=images)},error=True)
        self.until(lambda:self.client.generation.record(self.ident)['state']=='failed')
        self.assertEqual(len(self.candidates()),2);self.assertEqual(self.continuations(),0)
        self.assertEqual(len(self.peer.submissions),1);self.assertNotIn('result',self.runner.store.read(self.ident))

    def test_06_interrupted_keeps_candidates(self):
        images=self.images(1);self.executed('9',images);self.ready_candidates(1)
        self.terminal({'9':dict(images=images)},interrupted=True)
        self.until(lambda:self.client.generation.record(self.ident)['state']=='failed')
        self.assertEqual(len(self.candidates()),1);self.assertEqual(self.continuations(),0);self.assertEqual(len(self.peer.submissions),1)

    def test_07_same_node_twice_final_is_history_only(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        self.executed('9',images);self.ready_candidates(4)
        self.assertEqual([json.loads(r[0])['occurrence'] for r in self.candidates()],['0','0','1','1'])
        self.terminal({'9':dict(images=images)});self.assert_complete(2);self.assertEqual(len(self.candidates()),0)

    def test_08_group_path_is_display_only(self):
        early=self.images(2);final=self.images(1,'flattened:7')
        self.executed('9',early,['group','inner','9']);self.ready_candidates(2)
        self.terminal({'flattened:7':dict(images=final)},requested=['group'])
        attempt=self.assert_complete(1)
        self.assertEqual(attempt['result']['images'][0]['reference']['node'],'flattened:7')
        self.assertEqual(len(self.candidates()),0)

    def test_09_lost_success_recovers_original_operation(self):
        self.terminal({'9':dict(images=self.images(2))},deliver=False)
        self.assert_complete(2);self.assertEqual(self.candidates(),[])

    def test_10_copy_failure_then_replay_and_db_failure(self):
        self.server.fail_assets=True;images=self.images(2)
        self.terminal({'9':dict(images=images)})
        self.until(lambda:self.client.generation.record(self.ident)['state']=='results_pending')
        self.until(lambda:bool(self.candidates('history')))
        self.assertTrue(unfinished(self.client.window.store.db));self.assertEqual(self.continuations(),0)
        self.server.fail_assets=False
        self.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('ready rollback')) if name=='before_candidate_ready_commit' else None
        self.refresh();self.until(lambda:any(r.get('error')=='ready rollback' for r in self.manager.pending.values()))
        self.assertTrue(all(r[1]=='pending' for r in self.candidates('history')))
        self.client.events.store.fault=None;self.refresh();self.assert_complete(2)

    def test_11_wrong_scope_and_corrupt_bytes_never_ready(self):
        self.server.corrupt=True;images=self.images(2);self.executed('9',images)
        self.until(lambda:any('mismatch' in r.get('error','') for r in self.manager.pending.values()))
        self.assertEqual(len(self.candidates()),2);self.assertTrue(all(r[1]=='pending' for r in self.candidates()))
        notice=dict(self.h.terminal(self.prompt),kind='result.manifest',scope=dict(library_id='foreign',workspace_id='foreign'),
                    payload=dict(manifest_id=next(iter(self.manager.manifests))))
        done=[];self.client.events.receive(notice,lambda:done.append(True));self.assertEqual(done,[True])
        self.server.corrupt=False;self.refresh();self.ready_candidates(2)
        self.terminal({'9':dict(images=images)});self.assert_complete(2)

    def reopen(self):
        self.stop_workers()
        token=self.client.window.runtime_token
        with patch.object(self.f,'tick',lambda:None):self.f.reopen(restore_tabs=False)
        self.h.restored_client=self.f.w.scheduler.clients[token]
        self.bind()
        self.client.events.reconcile.clock=self.h.clock

    def test_12_history_missing_then_complete_never_seals_false_zero(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        self.terminal({})
        self.until(lambda:self.client.generation.record(self.ident)['state']=='results_pending')
        self.assertEqual(self.continuations(),0)
        self.assertFalse(self.client.events.job(self.ident)['result_manifest']['sealed'])
        self.peer.history[self.prompt]['outputs']={'9':dict(images=images)}
        self.refresh();self.assert_complete(2)

    def test_13_crash_after_files_before_ready_reopen_twice(self):
        images=self.images(2)
        self.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('ready crash')) if name=='before_candidate_ready_commit' else None
        self.terminal({'9':dict(images=images)})
        self.until(lambda:any(r.get('error')=='ready crash' for r in self.manager.pending.values()))
        self.assertEqual(self.continuations(),0)
        self.assertTrue(all(r[1]=='pending' for r in self.candidates('history')))
        self.assertEqual(len(list((self.client.window.store.directory/'originals/generation').glob('*.png'))),2)
        self.server.fail_assets=True
        for _ in range(2):
            self.reopen()
            self.assertTrue(unfinished(self.client.window.store.db));self.assertEqual(self.continuations(),0)
        self.server.fail_assets=False;self.refresh();self.assert_complete(2)

    def test_14_final_transaction_rollback_retries_only_collection(self):
        images=self.images(2)
        self.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('frame crash')) if name=='before_frame_commit' else None
        self.terminal({'9':dict(images=images)})
        self.until(lambda:any(r.get('error')=='frame crash' for r in self.manager.pending.values()))
        self.assertEqual(self.continuations(),0);self.assertNotIn('result',self.runner.store.read(self.ident))
        self.assertTrue(all(r[1]=='ready' for r in self.candidates('history')))
        self.client.events.store.fault=None;self.refresh();self.assert_complete(2)

    def test_15_reordered_replayed_notices_wrong_identity(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        header=self.client.events.job(self.ident)['result_manifest']
        notice=dict(self.h.terminal(self.prompt),kind='result.manifest',entity='result_manifest:'+header['manifest_id'],
                    scope=header['identity']['owner'],payload={k:header[k] for k in ('manifest_id','revision','checksum')})
        for key in ('server_instance','epoch'):
            done=[];self.client.events.receive(dict(notice,**{key:'foreign'}),lambda:done.append(True))
            self.assertEqual(done,[True]);self.assertFalse(self.client.events.store.contains(dict(notice,**{key:'foreign'})))
        for field in ('prompt_id','graph_digest'):
            original=self.manager.identity(self.client.events.job(self.ident))
            bad=dict(original,**{field:'wrong'})
            reader=self.manager.reader_type(self.client.url,'fixture',self.client.events.identity,original['owner'],threading.Event())
            with self.assertRaises(Exception):reader.manifest(bad)
        done=[]
        for _ in range(2):self.client.events.receive(notice,lambda:done.append(True))
        self.until(lambda:len(done)==2)
        self.assertTrue(self.client.events.store.contains(notice));self.assertEqual(len(self.candidates()),2)
        self.terminal({'9':dict(images=images)});self.assert_complete(2)
        self.client.events.receive(notice,lambda:None)
        self.assertEqual(self.continuations(),1);self.assertEqual(len(self.candidates()),0)

    def test_16_scheme_c_prefetch_never_previews_or_enters_execution(self):
        from prompt_calculus_studio import multi_output
        host=self.f.a.host;key=self.runner.store.read(self.ident)['stage']
        host.canvas.commit(lambda state:multi_output.connect(state,key,multi_output.PREVIEW,'image'))
        before=copy.deepcopy(host.state)
        card=host.canvas.results
        images=self.images(3)
        with patch.object(fetch,'thumbnail',side_effect=AssertionError('candidate worker preview')), \
                patch('prompt_calculus_studio.canvas_results.QImageReader',side_effect=AssertionError('candidate GUI decode')), \
                patch.object(self.manager,'notify',wraps=self.manager.notify) as notify:
            self.executed('9',images);self.ready_candidates(3)
            self.assertIsNone(self.manager.preview(key))
            self.assertTrue(card.refresh_stage_input())
            self.assertEqual(len(card.preview.pictures),0)
            self.assertFalse(self.manager.notice.isActive())
            notify.assert_not_called()
        self.assertEqual(host.state,before)
        self.assertFalse(self.runner.results)
        self.assertNotIn('_stage_results',host.state)
        self.terminal({})
        self.until(lambda:card.feedback.text()=='結果待確認')
        self.assertEqual(self.continuations(),0)
        self.terminal({'9':dict(images=images)});self.assert_complete(3)
        self.assertEqual([row['name'] for row in card.input_records],[image['filename'] for image in images])
        self.assertEqual(len(card.preview.pictures),3)

    def test_20_scheme_c_final_display_keeps_history_content_and_order(self):
        from prompt_calculus_studio import multi_output
        host=self.f.a.host;key=self.runner.store.read(self.ident)['stage']
        host.canvas.commit(lambda state:multi_output.connect(state,key,multi_output.PREVIEW,'image'))
        early=self.images(2);cached=self.images(2,'nested:cache')
        self.executed('9',early);self.ready_candidates(2)
        self.assertIsNone(self.manager.preview(key))
        self.assertEqual(host.canvas.results.input_records,[])
        # History controls order, including cache and reversed candidate order.
        ordered=cached+list(reversed(early))
        self.terminal({'nested:cache':dict(images=cached),'9':dict(images=list(reversed(early)))})
        attempt=self.assert_complete(4)
        expected=[image['filename'] for image in ordered]
        self.assertEqual([image['name'] for image in attempt['result']['images']],expected)
        self.assertEqual([image['name'] for image in host.canvas.results.input_records],expected)
        self.assertEqual([image['reference']['node'] for image in attempt['result']['images']],['nested:cache']*2+['9']*2)
        self.assertEqual(len(host.canvas.results.preview.pictures),4)

    def test_17_confirmed_cancel_keeps_early_assets_and_no_frame(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        self.runner.cancel_current()
        self.client.events.reconcile.tick();self.h.drain()
        self.until(lambda:self.client.generation.record(self.ident)['state']=='failed')
        self.assertEqual(self.continuations(),0);self.assertEqual(len(self.candidates()),2)
        self.assertNotIn('result',self.runner.store.read(self.ident));self.assertEqual(len(self.peer.submissions),1)

    def recover_old_numeric_manifest(self, cancel=False):
        from test_result_manifest_native import events, m
        images=self.images(4);self.executed('9',images);self.ready_candidates(4)
        store=self.peer.service.result_manifests
        # Preserve an old unsealed manifest with real candidates and terminal
        # observations. This gate models the old digest-only rejection.
        original_seal=store.seal_history
        def old_seal(ident,history):
            entry=history.get(self.prompt)
            if entry and m.digest(entry['prompt'][2])!=self.manager.identity(self.client.events.job(self.ident))['graph_digest']:
                return True
            return original_seal(ident,history)
        with patch.object(store,'seal_history',old_seal):
            self.terminal({'9':dict(images=images)})
            self.until(lambda:self.client.generation.record(self.ident)['state']=='results_pending')
            header=self.client.events.job(self.ident)['result_manifest']
            self.assertFalse(header['sealed'])
            self.assertNotEqual(m.digest(self.peer.history[self.prompt]['prompt'][2]),header['identity']['graph_digest'])
            if cancel:
                self.runner.cancel_current()
                self.client.events.reconcile.tick();self.h.drain()
                self.assertTrue(self.runner.store.read(self.ident)['cancel_requested'])
            self.stop_workers()
        originals={p:p.read_bytes() for p in (self.client.window.store.directory/'originals/generation').glob('*.png')}
        self.assertEqual(len(originals),4)
        service=self.peer.service
        service.close()
        self.peer.service=type(service)(service.directory, self.peer.root, self.peer.root)
        self.peer.native=self.peer.service.native_queue
        restarted=self.peer.service.result_manifests
        self.assertIn(self.prompt,restarted.pending_native())
        # No client bind or synthetic reconcile may seal this manifest: only
        # the production worker's automatic startup scan does so.
        self.server.reconcile_on_bind=False
        queue=SimpleNamespace(get_current_queue_volatile=lambda:([],[]),
            get_history=lambda prompt_id:copy.deepcopy({prompt_id:self.peer.history[prompt_id]}))
        publisher=events.NativeEventBuffer(self.peer.service,queue)
        self.addCleanup(publisher.stop)
        publisher.start()
        def latest():
            with self.peer.service.connect() as db:return restarted._header(db,header['manifest_id'])
        self.until(lambda:latest()['sealed'])
        self.assertEqual(latest()['identity'],header['identity'])
        self.assertEqual(len(self.peer.submissions),1)
        # Normal reconnect/event delivery, no user refresh/retry/resubmit.
        log=self.peer.service.events
        self.h.identity=dict(server_instance=log.server_instance,epoch=log.epoch)
        self.h.since='0'  # Event sequence restarts with the extension epoch.
        self.bind();self.h.drain()
        self.until(lambda:self.client.generation.record(self.ident)['state']=='complete')
        final=self.client.generation.record(self.ident)
        self.assertTrue(final['result_manifest']['sealed'])
        self.assertEqual(len(final['results']),4)
        attempt=self.runner.store.read(self.ident)
        self.assertEqual(attempt['status'],'complete')
        self.assertEqual(len(attempt['result']['images']),4)
        self.assertFalse(attempt.get('cancel_requested'))
        # This fixture has a single Stage: settling its last frame completes
        # the run even after cancel-current paused further dispatch.
        self.assertEqual(self.runner.store.read(attempt['owner'])['status'],'complete')
        self.assertEqual(len(self.peer.submissions),1)
        self.assertTrue(all(p.read_bytes()==data for p,data in originals.items()))
        self.assertFalse(self.f.errors,self.f.errors)
        print(json.dumps(dict(t2_restart=True, cancelled_before_restart=cancel,
            job=final['state'], attempt=attempt['status'], sealed=final['result_manifest']['sealed'],
            images=len(final['results']), cancel_requested=attempt.get('cancel_requested'),
            submissions=len(self.peer.submissions), originals_preserved=len(originals))))

    def test_23_old_numeric_manifest_restart_completes_without_resubmit(self):
        self.recover_old_numeric_manifest()

    def test_24_cancelled_results_pending_finishes_after_extension_restart(self):
        self.recover_old_numeric_manifest(cancel=True)

    def test_18_file_failure_recovers_without_resubmit(self):
        with patch.object(fetch,'atomic_file',side_effect=OSError('disk unavailable')):
            self.terminal({'9':dict(images=self.images(2))})
            self.until(lambda:any('disk unavailable' in r.get('error','') for r in self.manager.pending.values()))
            self.assertEqual(self.continuations(),0)
            self.assertTrue(all(r[1]=='pending' for r in self.candidates('history')))
        self.refresh();self.assert_complete(2)

    def test_19_missing_local_candidate_is_downloaded_again_for_final(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        source=json.loads(self.candidates()[0][2])['source']
        path=self.client.window.store.directory/source['relative'];change_result_file(path)
        before=len([r for r in self.server.calls if r[0]=='asset'])
        self.terminal({'9':dict(images=images)});self.assert_complete(2)
        self.assertTrue(path.is_file())
        self.assertEqual(len([r for r in self.server.calls if r[0]=='asset']),before+1)

    def test_21_same_size_corrupt_candidate_is_rehashed_before_final(self):
        images=self.images(2);self.executed('9',images);self.ready_candidates(2)
        source=json.loads(self.candidates()[0][2])['source']
        path=self.client.window.store.directory/source['relative'];original=path.read_bytes()
        changed=original[:-1]+bytes([original[-1]^1])
        change_result_file(path,changed)
        before=len([r for r in self.server.calls if r[0]=='asset'])
        self.terminal({'9':dict(images=images)});self.assert_complete(2)
        self.assertEqual(path.read_bytes(),original)
        self.assertEqual(len([r for r in self.server.calls if r[0]=='asset']),before+1)


class ResultDeliveryTests(unittest.TestCase):
    def test_manifest_survives_disposable_executed_filter_and_replay(self):
        from prompt_calculus_studio.comfy_event_transport import DeliveryFilter
        delivery=DeliveryFilter();delivery.accepted('owner',['prompt'])
        notice=dict(kind='result.manifest',prompt_id='prompt',payload=dict(manifest_id='manifest'))
        page=dict(owner='owner',events=[dict(kind='native.executed',prompt_id='prompt'),
            dict(kind='native.progress',prompt_id='prompt'),notice])
        for _ in range(2):
            self.assertEqual(delivery.select(page)['events'],[notice])
        self.assertEqual(len(page['events']),3)


if __name__=='__main__':unittest.main()
