import copy,hashlib,json,os,tempfile,threading,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_studio.core import Storage,validate_state
from prompt_studio.media import Catalog,scan_models
from prompt_studio.civitai import normalize_version,download_version,CivitAIError
from prompt_studio.civitai_assets import (make_plan,DownloadReceipts,perform_download,check_download,register_download,installed_version,categories,apply_categories,suggested_target)
from prompt_studio.civitai_controls import details,DownloadDialog,FilterRow
from prompt_studio.window import Window
from test_civitai import version,parent,Response

APP=QApplication.instance() or QApplication([])

class InstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.root=Path(self.tmp.name); self.target=self.root/'models/loras'; self.target.mkdir(parents=True)
        self.store=Storage(self.root/'data'); self.catalog=Catalog(self.store); self.receipts=DownloadReceipts(self.store.directory)
        self.payload=b'test-only model bytes'; self.digest=hashlib.sha256(self.payload).hexdigest().upper()
        self.version=version(self.digest); self.version['images']=[]; self.parent={**parent(),'type':'LORA'}
        self.plan=make_plan(self.parent,self.version,self.version['files'][0],self.target,'hero.safetensors','角色',self.target.parent)
    def tearDown(self):self.store.close(); self.tmp.cleanup()
    def downloaded(self):
        with patch('prompt_studio.civitai._open_download',return_value=Response(self.payload,{'Content-Length':str(len(self.payload))})):
            return perform_download(self.receipts,self.plan,'',threading.Event())
    def test_exact_file_installs_once_preserving_personal_fields(self):
        record=self.downloaded(); check_download(record); row=register_download(self.catalog,record)
        self.assertEqual((row['kind'],row['base_model'],row['category']),('LoRA','Illustrious','角色'))
        self.assertEqual(row['trigger'],'hero, blue eyes'); self.assertEqual(self.catalog.count('model'),1)
        row.update(name='mine',notes='notes',trigger='manual',thumb='custom.jpg'); self.catalog.put('model',row,row['root'])
        again=register_download(self.catalog,record,'source.jpg')
        self.assertEqual([again[k] for k in ('name','notes','trigger','thumb')],['mine','notes','manual','custom.jpg'])
        self.assertEqual(self.catalog.count('model'),1)
        self.assertIsNotNone(installed_version(self.catalog,22,33)); self.assertIsNone(installed_version(self.catalog,22,99))
    def test_changed_or_missing_file_is_not_installed_and_cannot_reregister(self):
        record=self.downloaded(); row=register_download(self.catalog,record); Path(row['path']).write_bytes(b'changed')
        self.assertIsNone(installed_version(self.catalog,22)); self.assertRaises(ValueError,check_download,record)
        Path(row['path']).unlink(); self.assertIsNone(installed_version(self.catalog,22))
    def test_completed_download_survives_registration_failure_and_restart(self):
        record=self.downloaded(); self.assertEqual(self.receipts.get(record['id'])['state'],'downloaded')
        with patch.object(self.catalog,'get',side_effect=RuntimeError('database busy')):self.assertRaises(RuntimeError,register_download,self.catalog,record)
        self.receipts.recover(); record=self.receipts.get(record['id']); self.assertEqual(record['state'],'downloaded')
        with patch('prompt_studio.civitai._open_download') as network:
            register_download(self.catalog,check_download(record)); network.assert_not_called()
    def test_restart_does_not_resubmit_interrupted_work(self):
        self.receipts.save(self.plan); self.receipts.recover(); self.assertEqual(self.receipts.get(self.plan['id'])['state'],'interrupted')
    def test_missing_official_hash_is_not_verified(self):
        self.plan['version']['files'][0]['hashes']={}; record=self.downloaded(); row=register_download(self.catalog,record)
        self.assertEqual(row['verification'],'unavailable'); self.assertEqual(row['civitai_status'],'unverified')
    def test_cancel_or_bad_hash_never_registers_partial_file(self):
        self.plan['version']['files'][0]['hashes']={'SHA256':'0'*64}
        self.assertRaises(CivitAIError,self.downloaded); self.assertEqual(list(self.target.iterdir()),[])
        self.assertEqual(self.receipts.get(self.plan['id'])['state'],'failed')
    def test_same_name_rejected_and_unknown_type_has_no_suggested_directory(self):
        self.downloaded(); self.assertRaises(ValueError,make_plan,self.parent,self.version,self.version['files'][0],self.target,'hero.safetensors','',self.target.parent)
        self.assertEqual(suggested_target(str(self.target.parent),'Unknown'),('其他',''))
        self.assertEqual(suggested_target(str(self.target.parent),'LORA')[1],str(self.target))
    def test_select_non_primary_file_uses_its_hash_name_and_download_url(self):
        alternate={**self.version['files'][0],'id':34,'name':'alternate.safetensors','primary':False,'downloadUrl':'https://civitai.com/api/download/models/22?fileId=34'}
        self.version['files'].append(alternate)
        with patch('prompt_studio.civitai._open_download',return_value=Response(self.payload)) as request:
            result=download_version(self.version,self.target,file_id=34)
        self.assertIn('fileId=34',request.call_args.args[0].full_url); self.assertEqual(result['source']['file_id'],34)
        self.assertEqual(Path(result['path']).name,'alternate.safetensors')
    def test_html_response_is_not_a_model(self):
        with patch('prompt_studio.civitai._open_download',return_value=Response(b'<html/>',{'Content-Type':'text/html'})):
            self.assertRaises(CivitAIError,download_version,self.version,self.target)
        self.assertEqual(list(self.target.iterdir()),[])
    def test_source_details_do_not_invent_missing_values(self):
        source=normalize_version(self.version,parent=self.parent); fields=details(source)
        self.assertEqual(fields['Usage Tips'],''); self.assertEqual(fields['Reviews'],''); self.assertEqual(fields['Published'],'')
        self.assertIn('所選版本',fields['Stats']); self.assertNotIn('99',fields['Stats'])
    def test_scoped_categories_preserve_legacy_and_deleted_becomes_unclassified(self):
        record=self.downloaded(); row=register_download(self.catalog,record)
        settings={'model_categories':['角色','舊分類'],'model_category_types':{}}
        self.assertIn('舊分類',categories(settings,'CKPT'))
        apply_categories(self.catalog,settings,['角色','舊分類'],{'角色':['LoRA']},{})
        self.assertNotIn('角色',categories(settings,'CKPT')); self.assertIn('角色',categories(settings,'LoRA'))
        apply_categories(self.catalog,settings,['舊分類'],{}, {'角色':'未分類'})
        self.assertEqual(self.catalog.get(row['id'])['category'],'未分類'); self.assertTrue(Path(row['path']).is_file())
    def test_backup_contains_receipts_not_token_or_downloaded_models(self):
        from prompt_studio.backup import archive_data
        record=self.downloaded(); register_download(self.catalog,record)
        archive=self.root/'backup.zip'; archive_data(self.store.directory,self.store.backup(),archive)
        with zipfile.ZipFile(archive) as bundle:
            self.assertIn('data/civitai/downloads/'+record['id']+'.json',bundle.namelist())
            self.assertFalse(any(n.endswith('.safetensors') or 'credentials' in n for n in bundle.namelist()))
    def test_new_local_types_roundtrip_through_json_resource_validation(self):
        from prompt_studio.validation import validate_resources
        record=self.downloaded(); record['kind']='Embedding'; row=register_download(self.catalog,record)
        validate_resources([dict(id=row['id'],name=row['name'],parent=row['root'],kind='model',body=row)])
    def test_visible_thumbnail_url_never_requests_original(self):
        from prompt_studio.civitai import preview_url
        small=preview_url('https://image.civitai.com/account/id/original=true/name.jpeg',96)
        self.assertIn('width=96',small); self.assertNotIn('original=true',small)

class UiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.w=Window(self.tmp.name); self.w.state['settings']['online']=False
        self.errors=[]; self.w.error=self.errors.append; self.p=self.w.settings_page.civitai
        self.target=Path(self.tmp.name)/'models/loras'; self.target.mkdir(parents=True); self.w.models.root.setText(str(self.target.parent))
        self.payload=b'abc'; self.v=version(hashlib.sha256(self.payload).hexdigest().upper()); self.v['images']=[]; self.v['_details_loaded']=True
        self.parent={**parent(),'type':'LORA','modelVersions':[self.v]}
    def tearDown(self):self.w.close(); APP.processEvents(); self.tmp.cleanup()
    def wait(self):
        for _ in range(1000):
            if not self.w.jobs.active and not self.p.browse_jobs.active and not self.p.pending_search:return
            QTest.qWait(10)
        self.fail('worker did not finish')
    def test_download_dialog_freezes_selected_file_type_category_and_location(self):
        dialog=DownloadDialog(self.w,self.parent,self.v); dialog.category.setCurrentText('角色'); dialog.confirm()
        self.assertIsNotNone(dialog.plan); self.assertEqual(dialog.plan['kind'],'LoRA'); self.assertEqual(dialog.plan['category'],'角色')
        self.assertEqual(dialog.plan['target'],str(self.target)); self.assertFalse((self.target/'Illustrious').exists())
        dialog.deleteLater()
    def test_download_registration_failure_retry_stays_on_search_and_avoids_network(self):
        self.w.settings('civitai'); plan=make_plan(self.parent,self.v,self.v['files'][0],self.target,'hero.safetensors','角色',self.target.parent)
        self.p.receipts.save(plan); self.p.pending_download=plan; self.w.state['settings']['online']=True
        with patch('prompt_studio.civitai._open_download',return_value=Response(self.payload)),patch('prompt_studio.civitai_ui.register_download',side_effect=RuntimeError('busy')):
            self.p.start_download(); self.wait()
        record=self.p.receipts.get(plan['id']); self.assertEqual(record['state'],'registration_failed',self.p.install_state.text()); self.assertTrue(Path(record['result']['path']).is_file())
        self.w.state['settings']['online']=False
        with patch('prompt_studio.civitai._open_download') as request:
            self.p.retry_register(); self.wait(); request.assert_not_called()
        self.assertEqual(self.p.receipts.get(plan['id'])['state'],'completed'); self.assertEqual(self.p.tabs.currentIndex(),0)
        self.assertEqual(self.w.catalog.count('model'),1); self.assertEqual(self.errors,[])
        asset=self.w.catalog.rows('model',limit=1)[0]; original=copy.deepcopy(asset); self.p.show_asset(asset)
        self.assertEqual(self.w.models.record['id'],asset['id'])
        self.assertEqual(self.w.catalog.get(asset['id']),original)
    def test_personal_preview_replacement_and_clear_survive_source_refresh(self):
        file=self.target/'a.safetensors'; file.write_bytes(b'a'); self.w.catalog.merge_models(scan_models(self.target.parent),self.target.parent)
        models=self.w.models; models.refresh(); models.list.setCurrentRow(0)
        img=QImage(16,16,QImage.Format.Format_RGB32); img.fill(0xff55aa44); path=Path(self.tmp.name)/'preview.png'; img.save(str(path))
        models.receive_image(str(path)); self.assertTrue(self.w.catalog.get(models.record['id']).get('thumb'))
        models.clear_preview(); self.assertEqual(self.w.catalog.get(models.record['id'])['thumb'],'')
    def test_base_model_filter_and_small_window_keep_search_readable(self):
        self.w.resize(1040,740); self.w.show(); self.w.settings('models'); APP.processEvents()
        self.assertGreater(self.w.models.search.width(),160)
        self.assertEqual(self.w.settings_page.comfy_tabs.count(),3)
        self.assertLess(self.w.settings_page.index['civitai'],self.w.settings_page.index['comfy'])
    def test_cursor_search_loads_more_without_duplicates_and_keeps_selection(self):
        self.w.state['settings']['online']=True
        other={**copy.deepcopy(self.parent),'id':12,'name':'Second'}
        with patch('prompt_studio.civitai_ui.CivitAIClient') as client:
            client.return_value.search_models.side_effect=[{'items':[self.parent],'metadata':{'nextCursor':'next'}},{'items':[self.parent,other],'metadata':{}}]
            self.p.query.setText('light'); self.p.search(); self.wait(); self.p.list.setCurrentRow(0)
            self.p.load_more(); self.wait()
            self.assertEqual(self.p.list.count(),2); self.assertEqual(self.p.current['id'],11)
            self.assertEqual(client.return_value.search_models.call_args.kwargs['cursor'],'next'); self.assertFalse(self.p.more.isEnabled())
    def test_selected_version_details_replace_search_snapshot_and_missing_tips(self):
        self.w.state['settings']['online']=True; self.v.pop('_details_loaded',None)
        self.p.current=self.parent; full={**self.v,'air':'urn:air:test','publishedAt':'2026-09-01T00:00:00Z','stats':{'thumbsUpCount':7}}
        with patch('prompt_studio.civitai_ui.CivitAIClient') as client:
            client.return_value.version.return_value=full
            self.p.version.addItem('v2',self.v); self.wait()
        self.assertEqual(self.p.fields['AIR'].text(),'urn:air:test'); self.assertEqual(self.p.fields['Stats'].text(),'未提供')
        self.assertIn('7',self.p.fields['Reviews'].text()); self.assertEqual(self.p.fields['Usage Tips'].text(),'未提供')

if __name__=='__main__':unittest.main()
