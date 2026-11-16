"""Bounded cold history, retention and on-use repair through existing widgets."""
import copy
import hashlib
import json
from pathlib import Path
import statistics
import threading
import time
import unittest
from _result_files import change_result_file
from _nested_cleanup import cleanup_nested
from unittest.mock import patch

import test_partial_node_results as fixtures
from prompt_calculus_studio import comfy_result_fetch as fetch
from prompt_calculus_studio import result_candidate_store as retention


class CandidateLifecycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.PartialNodeResultsTests.setUpClass()

    def setUp(self):
        self.t=fixtures.PartialNodeResultsTests('runTest');self.t.setUp();self.addCleanup(cleanup_nested,self.t)

    def complete(self, count=1):
        t=self.t;t.terminal({'9':dict(images=t.images(count))});t.assert_complete(count)
        t.until(lambda:not t.manager.derived and not t.manager.work)

    def seed(self):
        t=self.t;db=t.client.window.store.db
        original_ids={table:[row[0] for row in db.execute('SELECT id FROM '+table)]
                      for table in ('stage_header','generation_jobs')}
        def remove_header_only_fixtures():
            # These retention-only rows intentionally have no journal body.
            # Remove them after assertions, before real UI shutdown reads it.
            with db:
                for table,ids in original_ids.items():
                    db.execute('DELETE FROM '+table+' WHERE id NOT IN ('+','.join('?' for _ in ids)+')',ids)
        self.addCleanup(remove_header_only_fixtures)
        receipt=json.loads(t.candidates('history')[0][2]);owner=t.manager.identity(t.client.events.job(t.ident))['owner']
        candidates=[];jobs=[];attempts=[];events=[]
        for prefix,count,state in (('history',5000,'complete'),('failed',500,'failed'),('orphan',50,'failed')):
            for number in range(count):
                ident=f'{prefix}-{number}'
                candidates.append((ident,'history' if prefix=='history' else 'candidates',ident,json.dumps(owner),ident,0,'ready','{}',json.dumps(receipt),0))
                jobs.append((ident,json.dumps(dict(id=ident,state=state,created=0,chain='retained',server=t.client.url,native_operation=True)),0))
                if prefix!='orphan':
                    body=json.dumps(dict(id=ident,kind='attempt',status=state,owner='historical-run',workspace=t.client.window.state['workspace']))
                    attempts.append((ident,'attempt',t.client.window.state['workspace'],'historical-run',number,body,'{}','[]'))
                if state=='complete':events.append(('local','stage-continuation-v1',ident+':continue','unused','attempt:'+ident,0,'local_continuation',0))
        with db:
            db.executemany('INSERT INTO channel_result_candidates VALUES (?,?,?,?,?,?,?,?,?,?)',candidates)
            db.executemany('INSERT INTO generation_jobs VALUES (?,?,?)',jobs)
            db.executemany('INSERT INTO stage_header VALUES (?,?,?,?,?,?,?,?)',attempts)
            db.executemany('INSERT INTO channel_applied_events VALUES (?,?,?,?,?,?,?,?)',events)

    def rebind(self):
        t=self.t;t.client.events.enabled=False
        t.client.events.bind(dict(t.h.identity,result_manifest_v1=True))

    def test_manifest_metadata_rollback_and_worker_serialization(self):
        import sqlite3
        t=self.t;before=copy.deepcopy(t.client.events.job(t.ident));threads=[]
        original=fetch.prepare_packet
        def prepared(packet):threads.append(threading.get_ident());return original(packet)
        t.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('intent rollback')) if name=='before_candidate_intent_commit' else None
        with patch.object(fetch,'prepare_packet',prepared):
            images=t.images(4);t.executed('9',images)
            t.until(lambda:any(row.get('error')=='intent rollback' for row in t.manager.pending.values()))
            self.assertEqual(t.candidates(),[])
            self.assertEqual(t.client.generation.record(t.ident).get('result_manifest'),before.get('result_manifest'))
            t.client.events.store.fault=None;t.refresh();t.ready_candidates(4)
        self.assertTrue(threads);self.assertNotIn(t.gui,threads)
        after=t.client.events.job(t.ident)
        self.assertEqual(after['payload'],before['payload'])
        self.assertEqual(t.client.generation.record(t.ident)['result_manifest'],after['result_manifest'])
        # The digest cache still rejects mutation of the frozen graph itself.
        probe=copy.deepcopy(after);first=t.manager.identity(probe)
        probe['payload']['prompt']['9']['class_type']='different'
        self.assertNotEqual(first,t.manager.identity(probe))
        self.assertEqual(t.manager.identity(after),first)
        t.terminal({'9':dict(images=images)});t.assert_complete(4)

    def test_final_projection_worker_and_stale_continuation_retry(self):
        from prompt_calculus_studio import comfy_result_projection as projection
        t=self.t;entered=threading.Event();release=threading.Event();threads=[]
        original=projection.prepare
        def prepare(*args):
            threads.append(threading.get_ident())
            value=original(*args)
            if len(threads)==1:
                entered.set();release.wait(5)
            return value
        with patch.object(projection,'prepare',prepare):
            t.terminal({'9':dict(images=t.images(3))})
            t.until(entered.is_set)
            attempt=t.runner.store.read(t.ident)
            t.runner.pause(attempt['owner'],'changed while preparing',reason='manual')
            release.set()
            t.until(lambda:any('continuation changed' in row.get('error','') for row in t.manager.pending.values()))
            self.assertIsNone(t.runner.store.read(t.ident).get('result'))
            self.assertEqual(t.continuations(),0)
            t.refresh();t.assert_complete(3)
        self.assertGreaterEqual(len(threads),2);self.assertNotIn(t.gui,threads)
        self.assertEqual(len(t.peer.submissions),1)
        job=t.client.generation.record(t.ident)
        self.assertNotIn('result_prepared',job);self.assertNotIn('result_assets',job)

    def test_final_transaction_rechecks_ready_after_projection(self):
        from prompt_calculus_studio import comfy_result_projection as projection
        t=self.t;entered=threading.Event();release=threading.Event()
        original=projection.prepare
        def prepare(*args):
            value=original(*args);entered.set();release.wait(5);return value
        with patch.object(projection,'prepare',prepare):
            t.terminal({'9':dict(images=t.images(2))});t.until(entered.is_set)
            with t.client.window.store.db:
                t.client.window.store.db.execute("UPDATE channel_result_candidates SET state='pending' WHERE source='history'")
            release.set()
            t.until(lambda:any('not ready' in row.get('error','') for row in t.manager.pending.values()))
            self.assertIsNone(t.runner.store.read(t.ident).get('result'))
            self.assertEqual(t.continuations(),0)
        # Restore the committed receipt as a crash/retry would, without submit.
        with t.client.window.store.db:
            t.client.window.store.db.execute("UPDATE channel_result_candidates SET state='ready' WHERE source='history'")
        t.refresh();t.assert_complete(2)

    def test_dense_notices_coalesce_but_success_is_immediate(self):
        t=self.t;images=t.images(2);t.executed('9',images);t.ready_candidates(2)
        row=t.manager.pending[t.ident];row['next_manifest']=time.monotonic()+3600
        header=t.client.events.job(t.ident)['result_manifest'];done=[]
        before=sum(route=='manifests/bind' for route,_ in t.server.calls)
        for number in range(20):
            event=dict(t.h.terminal(t.prompt),kind='result.manifest',seq=900+number,
                entity='result_manifest:'+header['manifest_id'],scope=header['identity']['owner'],
                payload={key:header[key] for key in ('manifest_id','revision','checksum')})
            t.manager.ensure(t.client.events.job(t.ident),event=event,done=lambda:done.append(True),force=True)
        t.manager.tick();t.h.drain()
        self.assertEqual(done,[])
        self.assertEqual(sum(route=='manifests/bind' for route,_ in t.server.calls),before)
        self.assertEqual(t.continuations(),0)
        t.terminal({'9':dict(images=images)});t.assert_complete(2)
        self.assertEqual(len(done),20)
        self.assertLessEqual(sum(route=='manifests/bind' for route,_ in t.server.calls)-before,2)

    def test_5550_history_bind_twice_no_image_io_hash_or_failed_http(self):
        self.complete();t=self.t
        def measured():
            timings=[]
            for _ in range(2):
                start=time.perf_counter();self.rebind();timings.append((time.perf_counter()-start)*1000)
                t.manager.health(dict(state='offline',needs_reconcile=True));t.manager.heartbeat()
                t.manager.tick();t.h.drain()
            return timings
        baseline=measured();self.seed();before=len(t.server.calls)
        reads=[];hashes=[];original_read=Path.read_bytes;original_hash=hashlib.sha256
        def read(path):
            if path.parent.name=='generation':reads.append(str(path))
            return original_read(path)
        def hashed(value=b'',*args,**kwargs):
            if isinstance(value,bytes) and value.startswith(b'\x89PNG'):hashes.append(threading.get_ident())
            return original_hash(value,*args,**kwargs)
        with patch.object(Path,'read_bytes',read),patch.object(hashlib,'sha256',hashed):
            history=measured()
        self.assertEqual(reads,[]);self.assertEqual(hashes,[]);self.assertEqual(len(t.server.calls),before)
        delta=statistics.median(history)-statistics.median(baseline)
        self.assertLessEqual(delta,5)
        plan=list(t.client.window.store.db.execute('EXPLAIN QUERY PLAN SELECT receipt FROM channel_result_candidates WHERE derived_pending=1'))
        self.assertIn('channel_candidate_deriving',str(plan))
        print('HISTORY_BIND '+json.dumps(dict(baseline_ms=baseline,history_ms=history,median_delta_ms=delta,
            complete=5000,failed=500,orphans=50,original_reads=len(reads),image_hashes=len(hashes),http=len(t.server.calls)-before,query_plan=plan)),flush=True)

    def test_bind_only_resumes_unfinished_derived_receipt(self):
        self.complete(2);t=self.t;db=t.client.window.store.db
        row=db.execute("SELECT manifest,source,semantic,receipt FROM channel_result_candidates WHERE operation=? AND source='history' ORDER BY ordinal LIMIT 1",(t.ident,)).fetchone()
        receipt=json.loads(row[3]);receipt['derived']=None
        with db:db.execute('UPDATE channel_result_candidates SET receipt=?,derived_pending=1 WHERE manifest=? AND source=? AND semantic=?',(json.dumps(receipt),*row[:3]))
        calls=[];original=fetch.derivatives
        def build(*args):calls.append((args[1]['asset_key'],threading.get_ident()));return original(*args)
        with patch.object(fetch,'derivatives',build):
            self.rebind();t.until(lambda:not t.manager.work and not t.manager.derived)
        self.assertEqual(calls,[(receipt['asset_key'],calls[0][1])]);self.assertNotEqual(calls[0][1],t.gui)
        self.assertEqual(db.execute('SELECT count(*) FROM channel_result_candidates WHERE derived_pending=1').fetchone()[0],0)

    def test_final_candidate_delete_rolls_back_with_frame_and_keeps_reused_asset(self):
        import sqlite3
        t=self.t;images=t.images(2);t.executed('9',images);t.ready_candidates(2)
        files=[t.client.window.store.directory/json.loads(row[2])['source']['relative'] for row in t.candidates()]
        t.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('held frame')) if name=='before_frame_commit' else None
        t.terminal({'9':dict(images=images)})
        t.until(lambda:any(row.get('error')=='held frame' for row in t.manager.pending.values()))
        self.assertEqual(len(t.candidates()),2);self.assertEqual(t.continuations(),0)
        t.client.events.store.fault=None;t.refresh();t.assert_complete(2)
        self.assertEqual(t.candidates(),[]);self.assertTrue(all(path.is_file() for path in files))

    def test_retention_clears_removed_history_failed_and_orphans_but_protects_unfinished(self):
        self.complete();self.seed();t=self.t;db=t.client.window.store.db
        template=list(db.execute("SELECT * FROM channel_result_candidates WHERE operation='failed-0'").fetchone())
        with db:
            db.execute("DELETE FROM generation_jobs WHERE id LIKE 'history-%' OR id LIKE 'failed-%'")
            for ident,state,status in (('pending','results_pending','results_pending'),('unfinished','unconfirmed','submitted'),('before-frame','complete','complete'),('failed-frame','failed','failed')):
                row=template[:];row[0]=row[2]=row[4]=ident;db.execute('INSERT INTO channel_result_candidates VALUES (?,?,?,?,?,?,?,?,?,?)',row)
                db.execute('INSERT INTO generation_jobs VALUES (?,?,?)',(ident,json.dumps(dict(id=ident,state=state,created=0,chain='keep')),0))
                db.execute('INSERT INTO stage_header VALUES (?,?,?,?,?,?,?,?)',(ident,'attempt','scope','run',0,json.dumps(dict(id=ident,status=status)),'{}','[]'))
            db.execute('INSERT INTO stage_header VALUES (?,?,?,?,?,?,?,?)',('run','run','scope','',0,json.dumps(dict(stack=[dict(job='failed-frame')])),'{}','[]'))
            db.execute("DELETE FROM generation_jobs WHERE id='failed-frame'")
            row=template[:];row[0]=row[2]=row[4]='index-only';db.execute('INSERT INTO channel_result_candidates VALUES (?,?,?,?,?,?,?,?,?,?)',row)
        t.client.events.queue_active.add('index-only')
        # Exercise the original generation retention point. Each save sweeps
        # at most 128 rows, and an entire pass eventually reaches every orphan.
        with db:db.execute('DELETE FROM channel_result_retention')
        current=t.client.events.job(t.ident);passes=0
        while True:
            with db:t.client.generation._write_job(current)
            passes+=1
            if not retention.cursor(db):break
            self.assertLess(passes,60)
        remaining={row[0] for row in db.execute('SELECT operation FROM channel_result_candidates')}
        self.assertEqual(remaining,{t.ident,'pending','unfinished','before-frame','failed-frame','index-only'})
        t.client.events.queue_active.discard('index-only')
        # Existing non-chain job limit removes its candidate in that same save.
        with db:
            for number in range(201):
                ident=f'retired-{number}'
                db.execute('INSERT INTO generation_jobs VALUES (?,?,?)',(ident,json.dumps(dict(id=ident,state='failed',created=number)),number))
            row=template[:];row[0]=row[2]=row[4]='retired-0';db.execute('INSERT INTO channel_result_candidates VALUES (?,?,?,?,?,?,?,?,?,?)',row)
            t.client.generation._write_job(current)
        self.assertIsNone(db.execute("SELECT 1 FROM channel_result_candidates WHERE operation='retired-0'").fetchone())

    def test_display_missing_corrupt_and_valid_wrong_thumbnail_rebuilds_once(self):
        self.complete();t=self.t
        from prompt_calculus_studio.widgets import thumb_icon
        receipt=json.loads(t.candidates('history')[0][2]);path=t.client.window.store.directory/receipt['thumb']
        original=fetch.derivatives
        notices=[];t.manager.repaired.connect(notices.append)
        thumb_icon(t.client.window.store,receipt['thumb'],72)
        t.until(lambda:not t.manager.derived and not t.manager.work)
        self.assertEqual(notices,[],'unchanged thumbnail must not cause another GUI decode')
        for damage in ('missing','corrupt','wrong-png'):
            with self.subTest(damage=damage):
                if damage=='missing':change_result_file(path)
                elif damage=='corrupt':change_result_file(path,b'broken')
                else:change_result_file(path,(t.peer.root/t.images(1,suffix='other')[0]['filename']).read_bytes())
                calls=[]
                def build(*args):calls.append(threading.get_ident());return original(*args)
                with patch.object(fetch,'derivatives',build):
                    for _ in range(3):thumb_icon(t.client.window.store,receipt['thumb'],72)
                    t.until(lambda:not t.manager.derived and not t.manager.work)
                self.assertEqual(len(calls),1);self.assertNotEqual(calls[0],t.gui)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),receipt['derived']['thumb'])
        self.assertEqual(t.continuations(),1);self.assertEqual(len(t.peer.submissions),1)

    def test_successful_parallel_chunks_reuse_originals_after_sibling_failure(self):
        t=self.t;counts={};original=fetch.ManifestReader.request;first=[True]
        def request(reader,route,**kwargs):
            if route=='asset':
                ordinal=kwargs['ordinal']
                if ordinal==0 and first[0]:first[0]=False;raise OSError('one chunk failed')
                counts[ordinal]=counts.get(ordinal,0)+1
            return original(reader,route,**kwargs)
        with patch.object(fetch.ManifestReader,'request',request):
            t.executed('9',t.images(8))
            t.until(lambda:any(row.get('error')=='one chunk failed' for row in t.manager.pending.values()))
            t.refresh();t.ready_candidates(8)
        self.assertTrue(all(count==1 for count in counts.values()),counts)


class RetentionCursorTests(unittest.TestCase):
    def test_bounded_sweep_cursor_survives_two_reopens_and_rolls_back(self):
        import tempfile
        from prompt_calculus_studio.core import Storage
        with tempfile.TemporaryDirectory() as area:
            store=Storage(area)
            with store.db:
                store.db.execute('CREATE TABLE generation_jobs(id TEXT PRIMARY KEY,body TEXT,created REAL)')
                store.db.executemany('INSERT INTO channel_result_candidates VALUES (?,?,?,?,?,?,?,?,?,?)',
                    [(str(i),'candidates',str(i),'owner',str(i),0,'pending','{}','{}',0) for i in range(500)])
            with store.db:retention.retain(store.db)
            self.assertEqual(retention.cursor(store.db),128);store.close()
            for expected in (256,384):
                store=Storage(area)
                try:
                    prior=retention.cursor(store.db)
                    with self.assertRaises(OSError),store.db:
                        retention.retain(store.db);raise OSError('rollback retention')
                    self.assertEqual(retention.cursor(store.db),prior)
                    with store.db:retention.retain(store.db)
                    self.assertEqual(retention.cursor(store.db),expected)
                finally:store.close()
            store=Storage(area)
            try:
                with store.db:retention.retain(store.db)
                self.assertEqual(retention.cursor(store.db),0)
                self.assertEqual(store.db.execute('SELECT count(*) FROM channel_result_candidates').fetchone()[0],0)
            finally:store.close()


if __name__=='__main__':unittest.main()
