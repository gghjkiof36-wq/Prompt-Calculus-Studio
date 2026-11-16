"""Cancellation, parallel worker fencing and repair of durable result receipts."""
import hashlib
import gc
import json
from pathlib import Path
import sqlite3
import ssl
import threading
import time
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_partial_node_results as fixtures
from _result_files import change_result_file
from prompt_calculus_studio import comfy_result_fetch as fetch


class ResultFetchRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):fixtures.PartialNodeResultsTests.setUpClass()

    def setUp(self):
        gc.collect()
        self.t=fixtures.PartialNodeResultsTests('runTest');self.t.setUp();self.addCleanup(self.t.doCleanups)

    def test_http_reader_does_not_initialize_tls(self):
        t=self.t
        with patch.object(ssl,'_create_default_https_context',side_effect=AssertionError('HTTP loaded TLS')):
            reader=fetch.ManifestReader(t.server.url,t.client.token,t.client.events.identity,
                t.manager.identity(t.client.events.job(t.ident))['owner'],fetch.Cancellation())
            packet=reader.manifest(t.manager.identity(t.client.events.job(t.ident)))
        self.assertFalse(packet['header']['sealed'])

    def cancel(self):
        t=self.t;t.runner.cancel_current();t.client.events.reconcile.tick();t.h.drain()
        t.until(lambda:t.client.generation.record(t.ident)['state']=='failed')
        t.until(lambda:not t.manager.work)

    def quiet_hour(self):
        t=self.t;before=len(t.server.calls);clock=time.monotonic()
        with patch.object(fetch,'time',SimpleNamespace(monotonic=lambda:clock)):
            for _ in range(3600):clock+=1;t.manager.tick()
        self.assertNotIn(t.ident,t.manager.pending)
        self.assertEqual(len(t.server.calls),before)
        self.assertEqual(t.continuations(),0);self.assertEqual(len(t.peer.submissions),1)

    def reopen(self):
        t=self.t;t.stop_workers();token=t.client.window.runtime_token
        with patch.object(t.f,'tick',lambda:None):t.f.reopen(restore_tabs=False)
        # Finished owners are deliberately absent from the unfinished index.
        # Open retained history via its normal owner API, without restoring UI.
        value=next(value for value in t.f.w.runtimes.rows() if value['token']==token)
        execution=t.f.w.runtimes.history_owner(value)
        t.h.restored_client=execution.client
        t.client.connected=t.client.native_supported=t.client.flow_supported=True
        t.client.timer.stop();t.bind()
        t.client.events.reconcile.clock=t.h.clock
        gc.collect()

    def test_cancel_hour_new_event_once_and_two_reopens(self):
        t=self.t;t.executed('9',t.images(2));t.ready_candidates(2);self.cancel();self.quiet_hour()
        for _ in range(2):
            self.reopen();self.quiet_hour()
            header=t.client.events.job(t.ident)['result_manifest']
            event=dict(t.h.terminal(t.prompt),kind='result.manifest',entity='result_manifest:'+header['manifest_id'],
                scope=header['identity']['owner'],payload={k:header[k] for k in ('manifest_id','revision','checksum')})
            before=sum(route=='manifests/bind' for route,_ in t.server.calls);done=[]
            t.client.events.receive(event,lambda:done.append(True))
            t.until(lambda:done and not t.manager.work)
            self.assertEqual(sum(route=='manifests/bind' for route,_ in t.server.calls),before+1)
            t.client.events.receive(event,lambda:done.append(True));self.assertEqual(len(done),2)
            self.quiet_hour();self.assertEqual(len(t.candidates()),2)

    def blocked_workers(self, action):
        t=self.t;original=fetch.asset;started=set();lock=threading.Lock();cancelled=[]
        def blocked(reader,*args,**kwargs):
            with lock:started.add(threading.get_ident())
            self.assertTrue(reader.cancel.wait(3),'owner cancellation did not reach worker')
            cancelled.append(True)
            raise ValueError('cancelled fixture')
        with patch.object(fetch,'asset',blocked):
            t.executed('9',t.images(32))
            t.until(lambda:len(started)==fetch.PARALLEL)
            at=time.monotonic();action();t.until(lambda:not t.manager.work,timeout=2)
            self.assertLess(time.monotonic()-at,2)
        self.assertTrue(cancelled);self.assertLessEqual(len(started),max(1,(fetch.os.cpu_count() or 2)-1))
        self.assertEqual(t.continuations(),0);self.assertEqual(len(t.peer.submissions),1)
        self.assertTrue(all(row[1]=='pending' for row in t.candidates()))

    def test_parallel_cancel_fences_all_ready_callbacks(self):self.blocked_workers(self.cancel)

    def test_parallel_close_fences_all_ready_callbacks(self):self.blocked_workers(self.t.manager.close)

    def test_parallel_owner_change_fences_old_work(self):
        t=self.t
        self.blocked_workers(lambda:t.client.events.bind(dict(t.h.identity,result_manifest_v1=False)))

    def blocked_writer(self, close=False):
        from prompt_calculus_studio import comfy_result_writer as writer
        t=self.t;entered=threading.Event();release=threading.Event();threads=[]
        original=writer.write
        def write(path,phase,packet,identity,ident,values,cancel,current):
            count=0
            def checked():
                nonlocal count
                count+=1
                if phase=='ready' and count==len(values)+2:
                    threads.append(threading.get_ident());entered.set();release.wait(5)
                return current() and not cancel.is_set()
            return original(path,phase,packet,identity,ident,values,cancel,checked)
        with patch.object(writer,'write',write):
            try:
                t.executed('9',t.images(2));t.until(entered.is_set)
                self.assertEqual(t.client.window.store.db.execute('PRAGMA journal_mode').fetchone()[0],'wal')
                self.assertLessEqual(t.client.window.store.db.execute('PRAGMA busy_timeout').fetchone()[0],8)
                # A reader stays usable while the worker holds its WAL write.
                self.assertTrue(all(row[1]=='pending' for row in t.candidates()))
                if close:t.manager.close()
                else:t.manager.stop_operation(t.ident)
            finally:release.set()
            t.until(lambda:not t.manager.work)
        self.assertNotIn(t.gui,threads);self.assertEqual(t.continuations(),0)
        self.assertTrue(all(row[1]=='pending' for row in t.candidates()))
        self.assertEqual(len(t.peer.submissions),1)

    def test_candidate_writer_close_before_commit(self):self.blocked_writer(close=True)

    def test_candidate_writer_cancel_before_commit(self):self.blocked_writer()

    def test_candidate_writer_failure_retries_original_assets(self):
        from prompt_calculus_studio import comfy_result_writer as writer
        t=self.t;original=writer.write;failed=[]
        def write(*args):
            if args[1]=='ready' and not failed:
                failed.append(True);raise sqlite3.OperationalError('writer failed')
            return original(*args)
        with patch.object(writer,'write',write):
            t.terminal({'9':dict(images=t.images(2))})
            t.until(lambda:any(row.get('error')=='writer failed' for row in t.manager.pending.values()))
            before=sum(route=='asset' for route,_ in t.server.calls)
            t.refresh();t.assert_complete(2)
            self.assertEqual(sum(route=='asset' for route,_ in t.server.calls),before)

    def test_candidate_writer_committed_before_crash_reopens_twice(self):
        from prompt_calculus_studio import comfy_result_writer as writer
        t=self.t;original=writer.write;failed=[]
        def write(*args):
            value=original(*args)
            if args[1]=='ready' and not failed:
                failed.append(True);raise OSError('writer summary lost')
            return value
        with patch.object(writer,'write',write):
            t.terminal({'9':dict(images=t.images(2))})
            t.until(lambda:any(row.get('error')=='writer summary lost' for row in t.manager.pending.values()))
        self.assertTrue(all(row[1]=='ready' for row in t.candidates('history')))
        self.assertEqual(t.continuations(),0)
        for _ in range(2):
            self.reopen();t.assert_complete(2)
        self.assertEqual(len(t.peer.submissions),1)

    def test_sharing_timeout_uses_existing_asset_retry_without_resubmit(self):
        self.asset_failure_retry(32)

    def test_target_access_denial_uses_existing_asset_retry_without_resubmit(self):
        # Windows MoveFileEx can report 5 when the destination denies sharing.
        # Do not treat every permission denial as a transient scanner lock.
        self.asset_failure_retry(5)

    def asset_failure_retry(self, code):
        t=self.t
        with patch.object(fetch.os,'replace',side_effect=sharing_error(code)):
            t.terminal({'9':dict(images=t.images(2))})
            t.until(lambda:any(r.get('error') for r in t.manager.pending.values()))
            self.assertTrue(all(r[1]=='pending' for r in t.candidates('history')))
            self.assertEqual(t.continuations(),0)
        t.refresh();t.assert_complete(2)
        self.assertEqual(len(t.peer.submissions),1)

    def test_sharing_timeout_keeps_derivative_repair_queued(self):
        t=self.t;replace=fetch.os.replace
        def blocked(source,target):
            if 'thumbnails' in Path(target).parts:raise sharing_error()
            return replace(source,target)
        with patch.object(fetch.os,'replace',blocked):
            t.terminal({'9':dict(images=t.images(2))});t.assert_complete(2)
            t.until(lambda:not t.manager.work)
            self.assertTrue(t.manager.derived)
            self.assertTrue(all(json.loads(r[2]).get('derived') is None for r in t.candidates('history')))
        t.manager.derived_at=0;t.manager.tick()
        t.until(lambda:not t.manager.derived and not t.manager.work)
        self.assertTrue(all(json.loads(r[2]).get('derived') for r in t.candidates('history')))
        self.assertEqual(t.continuations(),1);self.assertEqual(len(t.peer.submissions),1)

    def test_deferred_derivatives_repair_missing_and_corrupt_on_display(self):
        t=self.t;images=t.images(2)
        original=fetch.derivatives
        with patch.object(fetch,'derivatives',side_effect=OSError('derived storage unavailable')):
            t.terminal({'9':dict(images=images)});t.assert_complete(2)
            t.until(lambda:not t.manager.work)
            self.assertTrue(all(json.loads(row[2]).get('derived') is None for row in t.candidates('history')))
        t.manager.derived_at=0;t.manager.tick();t.until(lambda:not t.manager.derived and not t.manager.work)
        for corrupt in (False,True):
            for _,_,raw in t.candidates('history'):
                receipt=json.loads(raw)
                for key in ('thumb','metadata'):
                    path=t.client.window.store.directory/receipt[key]
                    change_result_file(path,b'corrupt' if corrupt else None)
            self.reopen()
            self.assertFalse(t.manager.derived)
            from prompt_calculus_studio.widgets import thumb_icon
            for _,_,raw in t.candidates('history'):thumb_icon(t.client.window.store,json.loads(raw)['thumb'],72)
            t.until(lambda:not t.manager.derived and not t.manager.work)
            for _,_,raw in t.candidates('history'):
                receipt=json.loads(raw)
                for key in ('thumb','metadata'):
                    content=(t.client.window.store.directory/receipt[key]).read_bytes()
                    self.assertEqual(hashlib.sha256(content).hexdigest(),receipt['derived'][key])
            self.assertEqual(t.continuations(),1);self.assertEqual(len(t.peer.submissions),1)

    def test_missing_or_corrupt_original_ready_receipt_reacquires_after_reopen(self):
        t=self.t;images=t.images(2)
        t.client.events.store.fault=lambda name:(_ for _ in ()).throw(sqlite3.OperationalError('frame hold')) if name=='before_frame_commit' else None
        t.terminal({'9':dict(images=images)})
        t.until(lambda:any(row.get('error')=='frame hold' for row in t.manager.pending.values()))
        t.stop_workers()
        receipts=[json.loads(row[2]) for row in t.candidates('history')]
        root=t.client.window.store.directory
        change_result_file(root/receipts[0]['source']['relative'])
        change_result_file(root/receipts[1]['source']['relative'],b'corrupt original')
        before=sum(route=='asset' for route,_ in t.server.calls)
        t.server.fail_assets=True;t.reopen()
        t.until(lambda:any(row.get('error') for row in t.manager.pending.values()))
        self.assertEqual(t.continuations(),0)
        t.server.fail_assets=False;t.refresh();t.assert_complete(2)
        self.assertGreaterEqual(sum(route=='asset' for route,_ in t.server.calls),before+2)
        for receipt in receipts:
            self.assertEqual(hashlib.sha256((root/receipt['source']['relative']).read_bytes()).hexdigest(),receipt['source']['sha256'])

    def test_completed_original_reacquires_without_second_frame_after_two_reopens(self):
        t=self.t;t.terminal({'9':dict(images=t.images(2))});t.assert_complete(2)
        t.until(lambda:not t.manager.derived and not t.manager.work)
        for corrupt in (False,True):
            before=sum(route=='asset' for route,_ in t.server.calls)
            for _,_,raw in t.candidates('history'):
                receipt=json.loads(raw);path=t.client.window.store.directory/receipt['source']['relative']
                change_result_file(path,b'corrupt final original' if corrupt else None)
            self.reopen();t.until(lambda:not t.manager.derived and not t.manager.work)
            self.assertEqual(sum(route=='asset' for route,_ in t.server.calls),before)
            for _,_,raw in t.candidates('history'):
                t.manager.request_repair(json.loads(raw)['source']['relative'],failed=True)
            t.until(lambda:not t.manager.derived and not t.manager.work)
            self.assertEqual(sum(route=='asset' for route,_ in t.server.calls),before+2)
            for _,_,raw in t.candidates('history'):
                receipt=json.loads(raw)
                self.assertEqual(hashlib.sha256((t.client.window.store.directory/receipt['source']['relative']).read_bytes()).hexdigest(),receipt['source']['sha256'])
            self.assertEqual(t.continuations(),1);self.assertEqual(len(t.peer.submissions),1)


def sharing_error(code=32):
    error=PermissionError('file is in use');error.winerror=code
    return error


class FileSharingTests(unittest.TestCase):
    def clock(self):
        now=[0.];waits=[]
        def sleep(delay):waits.append(delay);now[0]+=delay
        return SimpleNamespace(monotonic=lambda:now[0],sleep=sleep),now,waits

    def test_file_helpers_bound_only_sharing_violation(self):
        import _result_files
        for product in (False,True):
            for code in (32,5):
                with self.subTest(product=product,winerror=code):
                    clock,now,waits=self.clock();calls=[]
                    def fail():calls.append(True);raise sharing_error(code)
                    module=fetch if product else _result_files
                    with patch.object(module,'time',clock),self.assertRaises(PermissionError):
                        if product:fetch.sharing_retry(fail)
                        else:change_result_file(SimpleNamespace(unlink=fail))
                    self.assertLessEqual(now[0],.5)
                    if code==32:
                        self.assertAlmostEqual(now[0],.5);self.assertGreater(len(calls),1)
                    else:self.assertEqual(waits,[]);self.assertEqual(len(calls),1)

    def test_file_mutations_retry_delete_and_overwrite(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'result.png'
            for content in (b'changed',None):
                change_result_file(path,b'original');calls=[]
                method='unlink' if content is None else 'write_bytes';original=getattr(Path,method)
                def mutate(target,*args):
                    calls.append(True)
                    if len(calls)==1:raise sharing_error()
                    return original(target,*args)
                with patch.object(Path,method,mutate):change_result_file(path,content)
                self.assertEqual(len(calls),2)
                if content is None:self.assertFalse(path.exists())
                else:self.assertEqual(path.read_bytes(),content)

    def test_atomic_failure_preserves_old_file_and_removes_partial(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'image.png';target.write_bytes(b'old')
            clock,now,_=self.clock()
            with patch.object(fetch,'time',clock),patch.object(fetch.os,'replace',side_effect=sharing_error()):
                with self.assertRaises(PermissionError):fetch.atomic_file(root,target.name,b'new',repair=True)
            self.assertEqual(now[0],.5)
            self.assertEqual(target.read_bytes(),b'old');self.assertEqual(list(root.glob('*.partial')),[])

    def test_atomic_partial_cleanup_retries_sharing_violation(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'image.png';calls=[];unlink=Path.unlink
            def competing_writer(source,destination):
                destination.write_bytes(b'new');raise FileExistsError('same bytes published')
            def cleanup(path,*args,**kwargs):
                calls.append(True)
                if len(calls)==1:raise sharing_error()
                return unlink(path,*args,**kwargs)
            with patch.object(fetch.os,'rename',competing_writer),patch.object(Path,'unlink',cleanup):
                fetch.atomic_file(root,target.name,b'new')
            self.assertEqual(target.read_bytes(),b'new');self.assertEqual(len(calls),2)
            self.assertEqual(list(root.glob('*.partial')),[])

    def test_cancel_interrupts_sharing_wait_and_preserves_original(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'image.png';target.write_bytes(b'old')
            cancel=fetch.Cancellation();calls=[]
            def blocked(*args):calls.append(True);cancel.set();raise sharing_error()
            at=time.monotonic()
            with patch.object(fetch.os,'replace',blocked),self.assertRaisesRegex(ValueError,'owner closed'):
                fetch.atomic_file(root,target.name,b'new',repair=True,cancel=cancel)
            self.assertLess(time.monotonic()-at,.5);self.assertEqual(len(calls),1)
            self.assertEqual(target.read_bytes(),b'old');self.assertEqual(list(root.glob('*.partial')),[])

    @unittest.skipUnless(fetch.os.name=='nt','Windows file sharing contract')
    def test_original_fsync_precedes_replace_under_real_windows_lock(self):
        import ctypes
        from ctypes import wintypes as w
        kernel=ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.CreateFileW.argtypes=[w.LPCWSTR,w.DWORD,w.DWORD,ctypes.c_void_p,w.DWORD,w.DWORD,w.HANDLE]
        kernel.CreateFileW.restype=w.HANDLE
        kernel.CloseHandle.argtypes=[w.HANDLE];kernel.CloseHandle.restype=w.BOOL
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);target=root/'image.png';target.write_bytes(b'old')
            # A scanner can open the flushed partial before MoveFileEx. Deny
            # delete on that source: Windows reports ERROR_SHARING_VIOLATION.
            handles=[];blocked=threading.Event();released=threading.Event();order=[]
            def release():
                blocked.wait(3);time.sleep(.03)
                for handle in handles:kernel.CloseHandle(handle)
                released.set()
            thread=threading.Thread(target=release);thread.start()
            replace=fetch.os.replace;fsync=fetch.os.fsync
            def sync(fd):order.append('fsync');return fsync(fd)
            def move(*args):
                order.append('replace')
                if not handles:
                    handle=kernel.CreateFileW(str(args[0]),0x80000000,1,None,3,0,None)
                    self.assertNotEqual(handle,ctypes.c_void_p(-1).value);handles.append(handle)
                try:return replace(*args)
                except OSError as exc:
                    self.assertEqual(exc.winerror,32);blocked.set();raise
            try:
                with patch.object(fetch.os,'replace',move),patch.object(fetch.os,'fsync',sync):
                    fetch.atomic_file(root,target.name,b'new',repair=True)
            finally:blocked.set();thread.join(4)
            self.assertTrue(released.is_set());self.assertEqual(order[0],'fsync')
            self.assertEqual(order.count('fsync'),1);self.assertGreaterEqual(order.count('replace'),2)
            self.assertEqual(target.read_bytes(),b'new');self.assertEqual(list(root.glob('*.partial')),[])
            # A locked *destination* instead reports ACCESS_DENIED (5). It
            # propagates to the ordinary owner retry, preserving old bytes.
            handle=kernel.CreateFileW(str(target),0x80000000,1,None,3,0,None)
            self.assertNotEqual(handle,ctypes.c_void_p(-1).value)
            try:
                with self.assertRaises(PermissionError) as error:
                    fetch.atomic_file(root,target.name,b'later',repair=True)
                self.assertEqual(error.exception.winerror,5)
                self.assertEqual(target.read_bytes(),b'new');self.assertEqual(list(root.glob('*.partial')),[])
            finally:kernel.CloseHandle(handle)
            fetch.atomic_file(root,target.name,b'later',repair=True)
            self.assertEqual(target.read_bytes(),b'later')


if __name__=='__main__':unittest.main()
