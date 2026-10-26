"""Automatic checkpoints retain an exact owner/receipt while newer input lives."""
import copy
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.local_ai import checkpoint
from prompt_calculus_studio.local_documents import io, identity, schema
from prompt_calculus_studio.workspace_autosave import WorkspaceAutosave
import test_local_chat_branch as branch_tests


class ChatCheckpointContracts(unittest.TestCase):
    setUp = branch_tests.BranchOwnerContracts.setUp

    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def tearDown(self):
        self.assertTrue(self.source.finish_checkpoint())
        self.assertTrue(checkpoint.pool().waitForDone(5000))
        self.app.processEvents()
        branch_tests.BranchOwnerContracts.tearDown(self)

    def delay(self):
        original=io.save; entered,release=threading.Event(),threading.Event(); threads=[]
        def delayed(*args):
            threads.append(threading.get_ident());entered.set();release.wait(3)
            return original(*args)
        return patch.object(io,'save',side_effect=delayed),entered,release,threads

    def test_one_background_writer_coalesces_and_does_not_replace_newer_draft(self):
        self.source.set_draft('保存中的快照');context,entered,release,threads=self.delay()
        with context as writer:
            self.assertTrue(self.source.request_checkpoint());self.assertTrue(entered.wait(2))
            job=self.source._checkpoint_job
            self.source.set_draft('保存期間的新草稿')
            self.assertTrue(self.source.request_checkpoint());self.assertIs(self.source._checkpoint_job,job)
            release.set();self.assertTrue(self.source.finish_checkpoint())
            self.assertEqual(writer.call_count,1)
        self.assertNotEqual(threads[0],threading.get_ident())
        self.assertEqual(self.source.baseline['draft'],'保存中的快照')
        self.assertEqual(self.source.payload['draft'],'保存期間的新草稿');self.assertTrue(self.source.dirty)
        self.assertTrue(self.source.save())
        self.assertEqual(io.load(self.root,self.source_path.name).document['payload']['draft'],'保存期間的新草稿')

    def test_barrier_timeout_is_not_failure_or_permission_to_move_and_later_result_is_accepted(self):
        self.source.set_draft('稍後保存');context,entered,release,_=self.delay()
        with context:
            self.source.request_checkpoint();self.assertTrue(entered.wait(2))
            job=self.source._checkpoint_job
            self.assertFalse(self.source.finish_checkpoint(.01));self.assertIs(self.source._checkpoint_job,job)
            self.assertIn('仍在保存',self.source.notice);self.assertFalse(self.source.error)
            release.set();self.assertTrue(self.source.finish_checkpoint());self.assertIsNone(self.source._checkpoint_job)
        self.assertFalse(self.source.dirty);self.assertFalse(self.source.notice)

    def test_stream_delta_survives_checkpoint_and_stop_cancels_before_waiting_for_save(self):
        self.assertTrue(self.source.send('串流問題'));request=self.service.submitted[-1]
        self.service.delta.emit(request,'先前片段');context,entered,release,_=self.delay()
        with context:
            self.source.request_checkpoint();self.assertTrue(entered.wait(2))
            self.service.delta.emit(request,'新片段')
            cancel_before_release=[]
            def unblock():
                limit=time.monotonic()+2
                while request.request_id not in self.service.cancelled and time.monotonic()<limit:time.sleep(.005)
                cancel_before_release.append(request.request_id in self.service.cancelled);release.set()
            helper=threading.Thread(target=unblock);helper.start()
            self.assertTrue(self.source.stop());helper.join(3)
        self.assertEqual(cancel_before_release,[True])
        saved=io.load(self.root,self.source_path.name).document
        last=saved['payload']['turns'][-1]['attempts'][-1]
        self.assertEqual((last['status'],last['text']),('stopped','先前片段新片段'))
        self.service.delta.emit(request,'晚到');self.service.complete(request,'晚到')
        self.assertEqual(self.source.payload,saved['payload'])

    def test_failed_checkpoint_retains_recovery_and_does_not_overwrite_live_candidate(self):
        self.source.set_draft('保留內容')
        result=io.SaveResult('conflict',error='外部變更',recovery_paths=('fixture-recovery',))
        with patch.object(io,'save',return_value=result):
            self.assertTrue(self.source.request_checkpoint());self.assertTrue(self.source.finish_checkpoint())
        self.assertEqual(self.source.error,'外部變更');self.assertTrue(self.source.dirty)
        self.assertEqual(self.source.payload['draft'],'保留內容')
        self.assertEqual(self.source.recovery_paths,['fixture-recovery'])
        self.assertFalse(self.source.request_checkpoint())

    def test_retarget_drains_original_file_and_late_signal_cannot_change_new_owner(self):
        self.source.set_draft('原文件的快照');context,entered,release,_=self.delay()
        with context:
            self.source.request_checkpoint();self.assertTrue(entered.wait(2))
            old_job=self.source._checkpoint_job
            candidate=identity.duplicate(self.source.project()).document
            path=self.root/'new.pcs-chat';path.write_bytes(schema.encode(candidate));loaded=io.load(self.root,path.name)
            helper=threading.Timer(.02,release.set);helper.start()
            self.assertTrue(self.source.retarget(io.SaveResult('saved',loaded.document,loaded.receipt),path));helper.join()
        before=copy.deepcopy(self.source.payload);receipt=self.source.receipt
        self.source._checkpoint_done(old_job.token);self.app.processEvents()
        self.assertEqual(self.source.payload,before);self.assertEqual(self.source.receipt,receipt)
        self.assertEqual(io.load(self.root,'source.pcs-chat').document['payload']['draft'],'原文件的快照')

    def test_dispose_waits_for_accepted_write_and_detaches_late_callback(self):
        self.source.set_draft('關閉前快照');context,entered,release,_=self.delay()
        with context:
            self.source.request_checkpoint();self.assertTrue(entered.wait(2));job=self.source._checkpoint_job
            helper=threading.Timer(.02,release.set);helper.start()
            self.assertTrue(self.source.dispose());helper.join()
        self.assertTrue(job.finished.is_set());self.assertIsNone(self.source._checkpoint_job)
        before=copy.deepcopy(self.source.payload);self.source._checkpoint_done(job.token)
        self.assertEqual(self.source.payload,before)

    def test_checkpoint_has_immutable_snapshot_not_live_nested_objects(self):
        self.source.set_draft('複製');context,entered,release,_=self.delay()
        original=self.source.payload['turns'][0]['attempts'][0]['parameters']['seed']
        with context:
            self.source.request_checkpoint();self.assertTrue(entered.wait(2))
            self.source.payload['turns'][0]['attempts'][0]['parameters']['seed']=123
            release.set();self.source.finish_checkpoint()
        self.assertEqual(self.source.baseline['turns'][0]['attempts'][0]['parameters']['seed'],original)
        self.assertTrue(self.source.dirty)

    def test_workspace_timer_routes_only_chat_to_background_checkpoint(self):
        calls=[];doc=SimpleNamespace(kind='chat',error='',request_checkpoint=lambda:calls.append('chat'))
        tab=SimpleNamespace(document=doc,dirty=True)
        workspace=SimpleNamespace(all_pages=lambda:[tab],save_tab=lambda t:calls.append('sync'))
        autosave=SimpleNamespace(workspace=workspace,paused=set(),stop=lambda t:None,editing_dialog=lambda t:False)
        WorkspaceAutosave.flush(autosave,tab);self.assertEqual(calls,['chat'])
        doc.kind='category';WorkspaceAutosave.flush(autosave,tab);self.assertEqual(calls,['chat','sync'])
