import copy
import os
import sys
import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QModelIndex,QEvent
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton,QLineEdit
from prompt_studio.window import Window
from prompt_studio import clean_metadata
from prompt_studio.media import import_image
from prompt_studio.widgets import ComboBox
APP=QApplication.instance() or QApplication([])

class ExportUiTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.tmp.name)
        self.w=Window(self.root/'data'); self.errors=[]; self.w.error=self.errors.append
        self.w.state['settings']['material']='solid'; self.w.apply_theme(); self.w.show(); APP.processEvents()

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.tmp.cleanup()

    def wait_job(self):
        end=time.monotonic()+10
        while self.w.jobs.active and time.monotonic()<end: QTest.qWait(10)
        self.assertIsNone(self.w.jobs.active); self.assertEqual(self.errors,[])

    def test_async_preview_export_audit_and_no_gallery_copies(self):
        source=self.root/'master.png'; target=self.root/'share'; target.mkdir()
        image=QImage(24,16,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.red); image.setText('prompt','private prompt'); image.save(str(source))
        before=source.read_bytes(); count=self.w.catalog.count('image'); p=self.w.clean_export
        self.w.open_export([str(source)]); self.wait_job(); self.assertEqual(len(p.rows),1)
        p.directory.setText(str(target)); p.preview(); self.wait_job(); self.assertTrue(p.export_button.isEnabled())
        p.quality.setValue(75); self.assertFalse(p.export_button.isEnabled())
        p.preview(); self.wait_job(); p.export(); self.wait_job()
        self.assertEqual(p.result['results'][0]['status'],'Passed'); self.assertIn('Passed 1',p.summary.text())
        self.assertEqual(source.read_bytes(),before); self.assertEqual(clean_metadata.inspect(target/'master.png')['text'],{})
        self.assertEqual(self.w.catalog.count('image'),count); self.assertEqual(len(p.records.history()),1)
        self.assertFalse(p.export_button.isEnabled())

    def test_destination_warning_only_after_failed_run_and_clears_on_selection(self):
        recent=self.w.recent; self.assertTrue(recent.destination_warning.isHidden())
        self.w.state['draft']='red wall'; self.w.refresh_builder(); self.w.comfy.ready=True; self.w.comfy.connected=True; self.w.comfy_changed()
        with patch.object(self.w.comfy,'run') as run:
            self.w.copy_final(); self.assertFalse(recent.destination_warning.isHidden()); run.assert_not_called()
        self.assertEqual(recent.destination_warning.toolTip(),'請先選擇收藏位置。')
        self.w.catalog.put('album',dict(id='test-album',name='測試收藏')); recent.refresh_destinations()
        recent.destination_picker.setCurrentIndex(recent.destination_picker.findData('album:test-album'))
        self.assertTrue(recent.destination_warning.isHidden())

    def test_connection_warning_after_control_loss_and_recovers(self):
        client=self.w.comfy; client.enabled=True; payload={'ready':False,'reason':'尚未啟用'}
        def request(route,data=None,done=None,failed=None):
            if route=='config': done({'token':'test'})
            elif route=='desktop/status': done(payload)
        client.request=request; client.poll(); self.assertFalse(client.control_interrupted)
        payload.update(ready=True,lease='one',target='text'); client.poll(); self.assertFalse(client.control_interrupted)
        payload.update(ready=False,reason='已停止桌面控制，保留目前文字。'); client.poll()
        self.assertTrue(client.control_interrupted); self.assertIn('#e4ba59',self.w.comfy_status.styleSheet())
        payload.update(ready=True); client.poll(); self.assertEqual(self.w.comfy_status.styleSheet(),'')
        client.disconnect(); self.assertFalse(client.control_interrupted)

    def test_library_move_rows_keeps_output_and_hidden_items(self):
        w=self.w; first=w.state['items'][0]; second=copy.deepcopy(first); second.update(id='second-item',name='Second')
        w.state['items'].insert(2,second); w.current_module=first['module']; w.state['selections'][first['module']]=[first['id']]
        w.refresh_library(); w.refresh_builder(); before=w.final.toPlainText(); selections=copy.deepcopy(w.state['selections'])
        hidden=[r['id'] for r in w.state['items'] if r['module']!=first['module']]
        ids=[w.library.item(i).data(Qt.ItemDataRole.UserRole) for i in range(w.library.count())]
        self.assertTrue(w.library.model().moveRows(QModelIndex(),0,1,QModelIndex(),w.library.count()))
        self.assertEqual([r['id'] for r in w.state['items'] if r['module']==first['module']],ids[1:]+ids[:1])
        self.assertEqual([r['id'] for r in w.state['items'] if r['module']!=first['module']],hidden)
        self.assertEqual(w.state['selections'],selections); self.assertEqual(w.final.toPlainText(),before)
        w.persist(); self.assertEqual(w.store.load()['items'],w.state['items'])

    def test_album_context_exposes_actions_and_preserves_image_multiselection(self):
        w=self.w; w.catalog.put('album',dict(id='a',name='資料夾')); w.gallery.refresh_albums(); self.w.tabs.setCurrentWidget(w.gallery); APP.processEvents()
        with patch('prompt_studio.pages.QMenu') as menu:
            w.gallery.album_context(w.gallery.albums.visualItemRect(w.gallery.albums.item(0)).center())
            names=[c.args[0] for c in menu.return_value.addAction.call_args_list]
            self.assertIn('移除資料夾…',names); self.assertIn('重新命名',names); self.assertIn('匯出資料夾圖片…',names)

    def fixture_image(self):
        source=self.root/'fixture.png'
        image=QImage(32,24,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.blue); image.save(str(source))
        return source

    def test_recent_export_survives_list_refresh_while_menu_open(self):
        source=self.fixture_image(); record=import_image(source,self.w.store.directory,'')
        record['id']='recent-test'; self.w.catalog.put('recent',record)
        p=self.w.recent; p.refresh(); self.w.tabs.setCurrentWidget(p); APP.processEvents()
        self.assertIsInstance(p.images.item(0).data(Qt.ItemDataRole.UserRole),str)
        with patch('prompt_studio.recent.RoundMenu') as menu:
            p.context(p.images.visualItemRect(p.images.item(0)).center())
            callback=next(c.args[1] for c in menu.return_value.addAction.call_args_list if c.args[0]=='匯出圖片…')
            p.refresh(); callback(); self.wait_job()
        self.assertIs(self.w.tabs.currentWidget(),self.w.clean_export)
        self.assertEqual(self.w.clean_export.rows[0]['source'],str(source))

    def test_media_folder_source_uses_registered_images_only(self):
        source=self.fixture_image(); self.w.catalog.put('album',dict(id='test',name='測試'))
        record=import_image(source,self.w.store.directory,'test'); self.w.catalog.put('image',record,'test')
        p=self.w.clean_export
        with patch('prompt_studio.export_page.InputDialog.getItem',return_value=('測試 (1)',True)):
            p.choose_album(); self.wait_job()
        self.assertEqual(p.inputs,[str(source)]); self.assertEqual(len(p.rows),1)
        self.assertEqual(self.w.catalog.count('image'),1)

    def test_export_source_is_retained_until_worker_idle(self):
        source=self.fixture_image(); gate=threading.Event()
        self.w.jobs.start('test',lambda cancel:gate.wait(2),lambda result:None)
        self.w.open_export([str(source)]); self.assertEqual(self.w.clean_export.pending_sources,[str(source)])
        gate.set(); self.wait_job()
        self.assertEqual(self.w.clean_export.rows[0]['source'],str(source)); self.assertIsNone(self.w.clean_export.pending_sources)

    def test_preview_selects_destination_and_validation_stays_visible(self):
        p=self.w.clean_export; self.w.tabs.setCurrentWidget(p)
        p.preview(); self.assertEqual(p.feedback.text(),'請先選擇圖片或資料夾。')
        self.w.open_export([str(self.fixture_image())]); self.wait_job()
        with patch('prompt_studio.export_page.QFileDialog.getExistingDirectory',return_value=''):
            p.preview(); self.assertEqual(p.feedback.text(),'請選擇輸出資料夾。')
        target=self.root/'share'; target.mkdir()
        with patch('prompt_studio.export_page.QFileDialog.getExistingDirectory',return_value=str(target)):
            p.preview(); self.wait_job()
        self.assertIsNotNone(p.plan); self.assertTrue(p.feedback.isHidden())
        item=p.table.item(0,2); self.assertEqual(item.text(),'待匯出'); self.assertEqual(item.foreground().color().name(),'#e4ba59')
        self.assertFalse(item.flags() & Qt.ItemFlag.ItemIsEditable)
        p.export(); self.wait_job(); self.assertEqual(p.table.item(0,2).text(),'成功')
        self.assertEqual(p.table.item(0,2).foreground().color().name(),'#75cd98')

    def test_gallery_location_menu_uses_resolved_original(self):
        source=self.fixture_image(); self.w.catalog.put('album',dict(id='test',name='測試'))
        record=import_image(source,self.w.store.directory,'test'); self.w.catalog.put('image',record,'test')
        p=self.w.gallery; p.refresh_albums(); self.w.tabs.setCurrentWidget(p); APP.processEvents()
        with patch('prompt_studio.pages.QMenu') as menu, patch('prompt_studio.pages.reveal_file') as reveal:
            p.context(p.images.visualItemRect(p.images.item(0)).center())
            callback=next(c.args[1] for c in menu.return_value.addAction.call_args_list if c.args[0]=='顯示檔案位置')
            callback(); reveal.assert_called_once_with(p,str(source))
        self.assertIn(str(source.parent),p.path.text())

    def test_shell_cursor_resets_on_content_and_leaves_editor_cursor(self):
        shell=self.w.centralWidget(); editor=QLineEdit(shell); editor.setCursor(Qt.CursorShape.IBeamCursor)
        shell.setCursor(Qt.CursorShape.SizeVerCursor)
        APP.sendEvent(editor,QEvent(QEvent.Type.Enter))
        self.assertFalse(shell.testAttribute(Qt.WidgetAttribute.WA_SetCursor))
        self.assertEqual(editor.cursor().shape(),Qt.CursorShape.IBeamCursor)
        for page in (self.w.recent,self.w.models,self.w.gallery,self.w.clean_export):
            shell.setCursor(Qt.CursorShape.SizeVerCursor)
            APP.sendEvent(page,QEvent(QEvent.Type.Enter))
            self.assertFalse(shell.testAttribute(Qt.WidgetAttribute.WA_SetCursor))
        editor.deleteLater()

    def test_combo_has_independent_popup_and_model_footer_is_aligned(self):
        combo=self.w.workspace; self.assertIsInstance(combo,ComboBox)
        self.assertIsNot(combo._popup,self.w); self.assertIs(combo._popup,combo.view().window())
        self.assertFalse(APP.isEffectEnabled(Qt.UIEffect.UI_AnimateCombo))
        combo.showPopup(); APP.processEvents(); self.assertFalse(combo._popup.mask().isEmpty()); combo.hidePopup()
        self.w.tabs.setCurrentWidget(self.w.models); APP.processEvents()
        actions={b.text():b for b in self.w.models.findChildren(QPushButton)}
        self.assertEqual(actions['上一頁'].y(),actions['下一頁'].y())
        self.assertEqual(actions['匯入檔案…'].y(),actions['管理分類'].y())
        self.assertEqual(actions['辨識來源'].y(),actions['管理分類'].y())

if __name__=='__main__': unittest.main()
