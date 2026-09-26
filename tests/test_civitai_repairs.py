import copy,hashlib,os,tempfile,threading,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtWidgets import QApplication
from prompt_studio.civitai import identify_models,normalize_version,download_version,CivitAIError,download_preview,CivitAIClient
from prompt_studio.core import Storage
from prompt_studio.media import Catalog,scan_models
from prompt_studio.window import Window
from test_civitai import version,parent,Response

APP=QApplication.instance() or QApplication([])

class SourceIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); (self.root/'loras').mkdir()
        self.file=self.root/'loras/a.safetensors'; self.file.write_bytes(b'old')
        self.store=Storage(self.root/'data'); self.catalog=Catalog(self.store)
        self.catalog.merge_models(scan_models(self.root),self.root); self.row=self.catalog.rows('model',str(self.root),limit=10)[0]
        digest=hashlib.sha256(b'old').hexdigest().upper(); source=normalize_version(version(digest),digest,parent())
        self.row.update(name='personal',notes='notes',trigger='manual trigger',url='https://example.org/my-notes',
                        sha256=digest,hash_size=3,hash_mtime=self.file.stat().st_mtime_ns,civitai=source,civitai_status='matched',
                        civitai_version_id=22,civitai_model_id=11,base_model='old base',creator='old maker')
        self.catalog.put('model',self.row,str(self.root))
    def tearDown(self): self.store.close(); self.tmp.cleanup()
    def lookup(self,found):
        client=SimpleNamespace(versions_by_hash=lambda *a:found,models_by_ids=lambda *a:[parent()])
        return identify_models([copy.deepcopy(self.row)],client)[0]
    def test_no_result_clears_previous_identity_but_keeps_personal_fields(self):
        result=self.lookup([]); self.catalog.update_identified_models([result]); saved=self.catalog.get(self.row['id'])
        self.assertEqual(saved['civitai_status'],'not_found')
        for key in ('civitai','civitai_model_id','civitai_version_id','base_model','creator'): self.assertNotIn(key,saved)
        for key in ('name','notes','trigger','url'): self.assertEqual(saved[key],self.row[key])
    def test_new_hash_does_not_inherit_old_source_and_scan_invalidates_cache(self):
        self.file.write_bytes(b'new file bytes'); self.catalog.merge_models(scan_models(self.root),self.root)
        saved=self.catalog.get(self.row['id']); self.assertEqual(saved['civitai_status'],'stale')
        self.assertNotIn('civitai',saved); self.assertNotIn('sha256',saved)
        self.row=saved; result=self.lookup([]); self.assertEqual(result['civitai_status'],'not_found')
        self.assertEqual(result['sha256'],hashlib.sha256(b'new file bytes').hexdigest().upper())
    def test_late_identification_preserves_newer_personal_edits(self):
        result=self.lookup([version(self.row['sha256'])]); latest=self.catalog.get(self.row['id'])
        latest.update(name='new name',notes='new notes',trigger='new manual',url='my URL'); self.catalog.put('model',latest,str(self.root))
        self.catalog.update_identified_models([result]); saved=self.catalog.get(self.row['id'])
        for key in ('name','notes','trigger','url'): self.assertEqual(saved[key],latest[key])
        self.assertEqual(saved['civitai_version_id'],22)
    def test_changed_file_after_lookup_is_not_marked_matched(self):
        result=self.lookup([version(self.row['sha256'])]); self.file.write_bytes(b'changed during lookup')
        self.catalog.update_identified_models([result]); saved=self.catalog.get(self.row['id'])
        self.assertEqual(saved['civitai_status'],'stale'); self.assertNotIn('civitai_version_id',saved)
    def test_cached_hash_still_checks_current_file_stat(self):
        self.file.write_bytes(b'changed but not rescanned')
        with self.assertRaisesRegex(ValueError,'重新掃描'): self.lookup([])
    def test_deleted_record_is_not_resurrected_by_a_late_result(self):
        result=self.lookup([]); self.catalog.delete(self.row['id']); self.catalog.update_identified_models([result])
        self.assertIsNone(self.catalog.get(self.row['id']))

class DownloadVerificationTests(unittest.TestCase):
    def test_cancelled_worker_cannot_deliver_a_successful_late_result(self):
        from prompt_studio.jobs import Job
        seen=[]; job=Job(lambda cancel:(cancel.set(),{'would_write':True})[1])
        job.signals.finished.connect(lambda result,error:seen.append((result,error)))
        job.run(); self.assertEqual(len(seen),1); self.assertIsNone(seen[0][0]); self.assertIn('已取消',seen[0][1])
    def test_verified_missing_and_failed_official_hash_are_distinct(self):
        content=b'tiny mock model'; digest=hashlib.sha256(content).hexdigest().upper()
        for expected,status in [(digest,'verified'),('','unavailable'),('0'*64,'failed')]:
            value=version(expected)
            with tempfile.TemporaryDirectory() as directory,patch('prompt_studio.civitai._open_download',return_value=Response(content)):
                if status=='failed':
                    with self.assertRaises(CivitAIError) as error: download_version(value,directory)
                    self.assertEqual(error.exception.kind,'checksum'); self.assertEqual(list(Path(directory).iterdir()),[])
                else:
                    result=download_version(value,directory); self.assertEqual(result['verification'],status)
                    self.assertEqual(result['official_sha256'],expected); self.assertEqual(result['sha256'],digest)
    def test_cancelled_network_operations_never_open_a_connection(self):
        cancel=threading.Event(); cancel.set()
        with tempfile.TemporaryDirectory() as directory,patch('prompt_studio.civitai._open_api') as request,patch('prompt_studio.civitai._open_download') as download:
            for action in (lambda:download_version(version(),directory,cancel=cancel),lambda:download_preview('https://image.civitai.com/p.jpg',directory,cancel),lambda:CivitAIClient(cancel=cancel).test_connection()):
                with self.assertRaisesRegex(ValueError,'已取消'): action()
            request.assert_not_called(); download.assert_not_called()

class DeferredUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.w=Window(self.tmp.name); self.w.state['settings']['online']=True
        self.page=self.w.settings_page.civitai; self.pending=[]; self.started=[]
        def start(worker,label,task,done,failed=None):
            if worker.active: return False
            job=SimpleNamespace(cancel=threading.Event(),worker=worker); worker.active=job
            self.pending.append((task,done,failed,job)); self.started.append(label); return True
        self.patches=[patch.object(worker,'start',side_effect=lambda *args,worker=worker:start(worker,*args)) for worker in (self.w.jobs,self.page.browse_jobs)]
        for p in self.patches:p.start()
        self.image_requests=[]
        self.image_patch=patch.object(self.page.images,'fetch',side_effect=lambda *args,**kwargs:self.image_requests.append(args)); self.image_patch.start()
    def tearDown(self):
        self.w.jobs.active=self.page.browse_jobs.active=None
        for p in self.patches:p.stop()
        self.image_patch.stop(); self.w.close(); APP.processEvents(); self.tmp.cleanup()
    def finish(self,result=None,error=None,run=False):
        task,done,failed,job=self.pending.pop(0); job.worker.active=None
        if run: result=task(job.cancel)
        if job.cancel.is_set(): error='已取消操作。'
        if error:
            if failed: failed(error)
        else: done(result)
        if job.worker.active is None: job.worker.became_idle.emit()
    def select_preview(self,name):
        self.page.current={'id':11,'name':name}; self.page.version.blockSignals(True); self.page.version.clear()
        self.page.version.addItem(name,dict(id=22,modelId=11,_details_loaded=True,images=[dict(url='https://image.civitai.com/'+name+'.jpg')],files=[]))
        self.page.version.blockSignals(False); self.page.refresh_version()
    def test_token_change_and_clear_invalidate_late_save(self):
        for clear in (False,True):
            self.page.token.setText('old-token'); self.page.test_connection()
            with patch('prompt_studio.civitai_ui.save_token') as save,patch('prompt_studio.civitai_ui.clear_token'):
                if clear:self.page.clear_token()
                else:self.page.token.setText('new-token')
                self.finish(dict(authenticated=True)); save.assert_not_called()
    def test_search_parameters_are_frozen_and_worker_never_reads_widgets(self):
        self.page.nsfw.blockSignals(True); self.page.nsfw.setChecked(False); self.page.nsfw.blockSignals(False)
        self.page.token.setText('before'); self.page.query.setText('first'); self.page.search()
        self.page.token.setText('after'); self.page.query.setText('second'); self.page.nsfw.setChecked(True)
        task,done,failed,job=self.pending.pop(0); results=[]
        with patch('prompt_studio.civitai_ui.CivitAIClient') as client,patch.object(self.page.sort,'currentText',side_effect=AssertionError('worker read UI')):
            client.return_value.search_models.return_value={'items':[]}
            thread=threading.Thread(target=lambda:results.append(task(job.cancel))); thread.start(); thread.join(5)
            self.assertFalse(thread.is_alive()); self.assertEqual(len(results),1)
            self.assertEqual(client.call_args.args,('before',)); self.assertEqual(client.return_value.search_models.call_args.kwargs['query'],'first')
            self.assertFalse(client.return_value.search_models.call_args.kwargs['nsfw'])
        self.page.browse_jobs.active=None
    def test_busy_file_worker_does_not_delay_preview(self):
        self.w.jobs.active=SimpleNamespace(cancel=threading.Event()); self.select_preview('a'); self.select_preview('b')
        self.assertEqual(self.pending,[]); self.assertEqual(len(self.image_requests),2)
        from PySide6.QtGui import QImage
        image=QImage(20,30,QImage.Format.Format_RGB32); image.fill(0xff224466)
        self.image_requests[-1][2](image); self.assertEqual(self.page.preview.image.size(),image.size())
    def test_running_preview_cancel_then_failure_does_not_leave_spinner(self):
        self.select_preview('a'); self.select_preview('b'); self.select_preview('c')
        from PySide6.QtGui import QImage
        self.image_requests[0][2](QImage(10,10,QImage.Format.Format_RGB32)); self.assertTrue(self.page.preview.image.isNull())
        self.image_requests[-1][3]('預覽載入失敗'); self.assertEqual(self.page.preview.text(),'預覽載入失敗')
        self.assertIsNone(self.page.pending_preview)
    def test_all_network_entrypoints_respect_offline_and_cancel_inflight(self):
        self.page.token.setText('token'); self.page.test_connection(); job=self.page.browse_jobs.active
        self.w.state['settings']['online']=False; self.w.changed(); self.assertTrue(job.cancel.is_set())
        with patch('prompt_studio.civitai_ui.save_token') as save: self.finish(dict(authenticated=True)); save.assert_not_called()
        count=len(self.started)
        models=Path(self.tmp.name)/'models'; models.mkdir(); self.w.models.root.setText(str(models))
        self.page.test_connection(); self.page.search(); self.page.download_selected(); self.select_preview('offline'); self.w.models.identify_civitai()
        self.assertEqual(len(self.started),count); self.assertEqual(self.page.preview.text(),'聯網已關閉')
    def test_busy_file_worker_does_not_block_search(self):
        self.w.jobs.active=SimpleNamespace(cancel=threading.Event()); self.page.search()
        self.assertIsNotNone(self.page.browse_jobs.active); self.finish({'items':[]}); self.assertEqual(self.page.counter.text(),'沒有相符結果')
    def test_identification_callback_keeps_unsaved_personal_ui_edits(self):
        root=Path(self.tmp.name)/'models'; (root/'loras').mkdir(parents=True); file=root/'loras/x.safetensors'; file.write_bytes(b'x')
        models=self.w.models; models.root.setText(str(root)); self.w.catalog.merge_models(scan_models(root),root); models.refresh()
        models.list.setCurrentRow(0); row=self.w.catalog.get(models.record['id']); models.identify_civitai()
        digest=hashlib.sha256(b'x').hexdigest().upper(); source=normalize_version(version(digest),digest,parent())
        result={**row,'sha256':digest,'hash_size':1,'hash_mtime':file.stat().st_mtime_ns,'civitai':source,'civitai_status':'matched',
                'civitai_version_id':22,'civitai_model_id':11}
        models.name.setText('edited while waiting'); models.notes.setPlainText('new note'); models.trigger.setPlainText('my words')
        self.finish([result]); saved=self.w.catalog.get(row['id'])
        self.assertEqual((saved['name'],saved['notes'],saved['trigger']),('edited while waiting','new note','my words'))
        self.assertEqual(saved['civitai_version_id'],22)

if __name__=='__main__': unittest.main()
