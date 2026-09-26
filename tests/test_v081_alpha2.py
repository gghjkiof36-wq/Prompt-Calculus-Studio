"""User-visible Alpha 2 regressions. No live services, clipboard, or desktop input."""
import copy,hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QByteArray,QBuffer,QIODevice
from PySide6.QtGui import QImage,QColor
from PySide6.QtWidgets import QApplication,QListWidgetItem,QPushButton
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio.core import Storage
from prompt_studio.civitai import API_BASE,filter_content,visible_images,preview_url,site_url
from prompt_studio.civitai_gallery import Preview,read_image
APP=QApplication.instance() or QApplication([])

def record(ident=1):
    return dict(id=ident,name='Landscape studio '+str(ident),type='LORA',creator={'username':'artist'},modelVersions=[dict(id=ident+100,modelId=ident,name='v1',baseModel='Illustrious',_details_loaded=True,trainedWords=['warm light'],images=[],files=[])])

class ContentTests(unittest.TestCase):
    def test_red_defaults_and_minor_filter_do_not_treat_all_mature_models_as_minors(self):
        self.assertEqual(API_BASE,'https://civitai.red/api/v1')
        records=[record(1),dict(record(2),minor=True),dict(record(3),name='Child portrait'),dict(record(4),nsfw=True)]
        original=copy.deepcopy(records); result=filter_content({'items':records},{})
        self.assertEqual([r['id'] for r in result['items']],[1,4]); self.assertEqual(records,original)
        self.assertEqual(len(filter_content({'items':records},{'civitai_filter_minor':False})['items']),4)
        self.assertEqual(site_url('https://civitai.com/models/1?modelVersionId=101'),'https://civitai.red/models/1?modelVersionId=101')
    def test_preview_policy_and_encoded_urls(self):
        images=[{'url':'a','nsfwLevel':1},{'url':'b','nsfwLevel':8},{'url':'c','minor':True},{'url':'d','type':'video'}]
        self.assertEqual([r['url'] for r in visible_images(images,{'civitai_mature':False})],['a'])
        url=preview_url('https://image.civitai.com/account/id/original=true/a b.jpeg',320)
        self.assertIn('a%20b.jpeg',url); self.assertIn('width=320',url); self.assertNotIn('original=true',url)
    def test_preview_contains_all_edges_and_image_decode_is_bounded(self):
        image=QImage(1600,800,QImage.Format.Format_RGB32); image.fill(QColor('red'))
        image.setPixelColor(1599,400,QColor('blue'))
        raw=QByteArray(); buf=QBuffer(raw); buf.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buf,'PNG')
        decoded=read_image(bytes(raw)); self.assertLessEqual(decoded.width(),800)
        preview=Preview(); preview.resize(400,300); preview.set_image(image); preview.show(); APP.processEvents()
        rendered=preview.grab().toImage(); self.assertEqual(rendered.pixelColor(1,100).name(),'#ff0000')
        self.assertEqual(preview.height(),200); preview.close()
        with self.assertRaises(ValueError):read_image(b'<html>not an image</html>')

class SettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.w=Window(self.tmp.name); self.w.state['settings'].update(online=False,material='solid')
        self.w.error=lambda message:self.fail(message); self.w.resize(1280,860); self.w.show(); self.p=self.w.settings_page; self.c=self.p.civitai
    def tearDown(self):self.w.close(); APP.processEvents(); self.tmp.cleanup()
    def wait(self):
        for _ in range(1200):
            QTest.qWait(5)
            if not self.c.browse_jobs.active and not self.c.pending_search:return
        self.fail('browse worker did not settle')
    def test_dictionary_visible_and_autosaves_without_footer(self):
        self.w.settings('dictionary'); APP.processEvents(); edit=self.p.preferences.dictionary
        self.assertTrue(edit.isVisible()); self.assertGreater(edit.height(),250)
        edit.setPlainText('晨光 = morning light'); QTest.qWait(450)
        self.assertEqual(self.w.state['dictionary'],{'晨光':'morning light'})
        self.assertFalse(any(b.isVisible() and b.text() in ('儲存設定','取消變更') for b in self.p.findChildren(QPushButton)))
        with _StorageRead(self.tmp.name) as store:
            self.assertEqual(store.load()['dictionary'],{'晨光':'morning light'})
    def test_incomplete_dictionary_preserves_last_valid_and_edit_buffer_on_reopen(self):
        self.w.settings('dictionary'); edit=self.p.preferences.dictionary
        edit.setPlainText('有效 = valid'); self.p.flush(); edit.setPlainText('未寫完'); self.p.flush()
        self.assertEqual(self.w.state['dictionary'],{'有效':'valid'}); self.assertIn('暫存',self.p.preferences.dictionary_feedback.text())
        self.p.clear_preferences(); self.w.settings('dictionary'); self.assertEqual(self.p.preferences.dictionary.toPlainText(),'未寫完')
        self.p.preferences.dictionary.setPlainText('完成 = finished'); self.p.flush(); self.assertNotIn('dictionary_buffer',self.w.state['settings'])
    def test_imported_state_cannot_be_overwritten_by_old_autosave_editors(self):
        self.w.settings('dictionary'); self.p.preferences.dictionary.setPlainText('old = old')
        imported=copy.deepcopy(self.w.state); imported['dictionary']={'new':'imported'}; imported['settings']['ui_size']=15
        path=Path(self.tmp.name)/'import.json'; path.write_text(json.dumps(imported),encoding='utf-8')
        with patch('prompt_studio.window.QFileDialog.getOpenFileName',return_value=(str(path),'')),patch('prompt_studio.window.ask',return_value=True):self.w.import_json()
        self.p.flush()
        self.assertEqual(self.w.state['dictionary'],{'new':'imported'}); self.assertEqual(self.w.state['settings']['ui_size'],15)
    def test_sidebars_and_detail_visibility_preserve_selected_version(self):
        self.w.settings('civitai'); APP.processEvents(); self.assertTrue(self.c.detail_pane.isHidden())
        self.p.toggle_navigation(); self.assertTrue(self.p.navigation.isHidden()); self.p.toggle_navigation()
        item=QListWidgetItem('Landscape'); item.setData(Qt.ItemDataRole.UserRole,record()); self.c.list.addItem(item); self.c.list.setCurrentItem(item); APP.processEvents()
        self.assertTrue(self.c.detail_pane.isVisible()); self.assertNotIn('Hash',self.c.fields)
        self.assertEqual(self.c.trigger.text(),'warm light'); self.c.hide_details(); self.assertTrue(self.c.detail_pane.isHidden())
        self.c.toggle_details(); self.assertEqual(self.c.version.currentData()['id'],101)
        self.c.list.clear(); self.assertTrue(self.c.detail_pane.isHidden())
    def test_empty_search_browses_once_and_cache_avoids_duplicate_request(self):
        self.w.state['settings']['online']=True
        with patch('prompt_studio.civitai_ui.CivitAIClient') as client:
            client.return_value.search_models.return_value={'items':[record()]}
            self.w.settings('civitai'); self.wait(); self.c.search(); self.wait()
            self.assertEqual(client.return_value.search_models.call_count,1)
            args=client.return_value.search_models.call_args.kwargs
            self.assertEqual(args['query'],''); self.assertEqual(args['sort'],'Highest Rated')
            self.assertTrue(self.c.filter_minor.isChecked()); self.assertFalse(self.c.nsfw.isVisible())
    def test_typing_search_debounces_and_supplies_candidates(self):
        self.w.settings('civitai'); APP.processEvents(); self.w.state['settings']['online']=True
        with patch('prompt_studio.civitai_ui.CivitAIClient') as client:
            client.return_value.search_models.return_value={'items':[record()]}
            QTest.keyClicks(self.c.query,'land'); QTest.qWait(520); self.wait()
            self.assertEqual(client.return_value.search_models.call_count,1)
            self.assertEqual(client.return_value.search_models.call_args.kwargs['query'],'land')
            self.assertEqual(self.c.suggestions.stringList(),['Landscape studio 1'])
    def test_image_cache_hit_does_not_use_network_and_corruption_refetches(self):
        image=QImage(40,60,QImage.Format.Format_RGB32); image.fill(0xff123456); data=QByteArray(); buffer=QBuffer(data); buffer.open(QIODevice.OpenModeFlag.WriteOnly); image.save(buffer,'PNG')
        url='https://image.civitai.com/test.png'; cache=self.c.images.directory/(hashlib.sha256(url.encode()).hexdigest()+'.image'); cache.write_bytes(bytes(data))
        self.w.state['settings']['online']=True; loaded=[]
        with patch.object(self.c.images.manager,'get',side_effect=AssertionError('unexpected network')):
            self.c.images.fetch(('thumb',1),url,loaded.append,self.fail)
        self.assertEqual(loaded[0].size(),image.size())
        cache.write_bytes(b'broken')
        with patch.object(self.c.images.manager,'get',side_effect=RuntimeError('refetch attempted')):
            with self.assertRaisesRegex(RuntimeError,'refetch'):self.c.images.fetch(('thumb',2),url,loaded.append,self.fail)
        self.assertFalse(cache.exists())
    def test_recent_and_canvas_header_have_no_duplicate_run_or_connection_actions(self):
        self.assertFalse(hasattr(self.w.recent,'run_controls')); self.w.set_interface_mode('canvas'); APP.processEvents()
        visible=[b.text() for b in self.w.canvas_header.findChildren(QPushButton) if b.isVisible()]
        self.assertNotIn('連線',visible); self.assertNotIn('資料',visible); self.assertIn('設定',visible)
    def test_small_gallery_large_font_does_not_force_horizontal_scrolling(self):
        self.w.state['settings']['ui_size']=16; self.w.apply_theme(); self.w.resize(1040,740); self.w.settings('civitai')
        for ident in range(8):
            item=QListWidgetItem('Landscape'); item.setData(Qt.ItemDataRole.UserRole,record(ident)); self.c.list.addItem(item)
        self.c.list.setCurrentRow(0); APP.processEvents()
        self.assertFalse(self.c.list.horizontalScrollBar().isVisible()); self.assertGreater(self.c.query.width(),200)
        self.assertLessEqual(self.c.list.gridSize().width(),self.c.list.viewport().width())

class _StorageRead:
    def __init__(self,path):self.store=Storage(path)
    def __enter__(self):return self.store
    def __exit__(self,*args):self.store.close()

if __name__=='__main__':unittest.main()
