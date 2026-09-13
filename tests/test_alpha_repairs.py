import copy,json,os,sys,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]; sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import Qt,QEvent,QPoint
from PySide6.QtGui import QImage,QImageReader
from PySide6.QtWidgets import QApplication,QFrame,QScrollArea,QPushButton
from PySide6.QtTest import QTest
from shiboken6 import isValid
from prompt_studio.core import Storage
from prompt_studio.media import Catalog,import_image
from prompt_studio.media_paths import recover_owned_files,original_path,preview_file
from prompt_studio.widgets import RoundMenu,record_icon
from prompt_studio.image_preview import SourcePreview
from prompt_studio.error_dialog import ImportErrorToast,error_message,import_error
from prompt_studio.window import Window
APP=QApplication.instance() or QApplication([])


class MediaRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.temp.name)
        self.old=self.root/'old'; self.new=self.root/'new'; self.old.mkdir(); self.new.mkdir()
        self.image=self.old/'originals/albums/one/image.png'; self.image.parent.mkdir(parents=True)
        picture=QImage(64,48,QImage.Format.Format_RGB32); picture.fill(Qt.GlobalColor.blue); picture.save(str(self.image))
        self.thumb=self.old/'thumbnails/saved.jpg'; self.thumb.parent.mkdir(); picture.save(str(self.thumb))
        self.record=dict(id='one',name='image.png',owned=True,path=str(self.image),original_relative='originals/albums/one/image.png',thumb='thumbnails/saved.jpg')
    def tearDown(self): self.temp.cleanup()
    def test_exact_index_falls_back_to_old_source_and_thumbnail_without_writing(self):
        store=Storage(self.new)
        try:
            catalog=Catalog(store); catalog.put('image',self.record)
            record=catalog.get('one'); self.assertEqual(record['path'],str(self.image))
            self.assertEqual(preview_file(self.new,record),self.thumb)
            self.assertFalse(record_icon(store,record,96).isNull())
            self.assertEqual(json.loads(store.db.execute('SELECT body FROM resources').fetchone()[0]),self.record)
            self.assertFalse((self.new/'originals').exists())
        finally: store.close()
    def test_repair_is_byte_exact_idempotent_and_never_replaces_new_files(self):
        report=recover_owned_files(self.new,[self.record]); self.assertEqual(len(report),2)
        for entry in report: self.assertEqual(Path(entry['source']).read_bytes(),Path(entry['destination']).read_bytes())
        current=self.new/self.record['original_relative']; current.write_bytes(b'new content')
        self.assertEqual(recover_owned_files(self.new,[self.record]),[]); self.assertEqual(current.read_bytes(),b'new content')
        self.assertEqual(original_path(self.new,self.record),current)
    def test_lost_original_keeps_thumbnail_and_does_not_invent_an_original(self):
        self.image.unlink(); report=recover_owned_files(self.new,[self.record])
        self.assertEqual([r['kind'] for r in report],['thumbnails'])
        self.assertFalse(original_path(self.new,self.record).is_file()); self.assertTrue(preview_file(self.new,self.record).is_file())
    def test_missing_thumbnail_uses_original_and_moved_folder_prefers_local_copy(self):
        recover_owned_files(self.new,[self.record]); (self.new/self.record['thumb']).unlink(); self.thumb.unlink(); self.image.unlink()
        self.assertEqual(preview_file(self.new,self.record),self.new/self.record['original_relative'])
    def test_wrong_filename_and_path_traversal_are_not_recovered(self):
        for change in ({'original_relative':'../image.png'},{'original_relative':'originals/albums/one/other.png'},{'original_relative':str(self.image)},{'owned':False}):
            row={**self.record,**change}; self.assertEqual(recover_owned_files(self.new,[row]),[])


class InterfaceRepairTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'qa'); self.root=Path(self.temp.name)
        self.window=Window(self.root/'data'); self.window.state['settings'].update(online=False,material='solid'); self.window.apply_theme()
        self.window.resize(1440,900); self.window.show(); self.window.set_interface_mode('canvas'); QTest.qWait(30)
    def tearDown(self):
        for toast in self.window.findChildren(ImportErrorToast):
            if isValid(toast): toast.dispose()
        self.window.close(); APP.processEvents(); self.temp.cleanup()
    def test_import_error_slides_from_bottom_keeps_focus_and_expires(self):
        w=self.window; w.settings('workflows'); before=copy.deepcopy(w.state['generation'])
        editor=w.settings_page.workflows.address; editor.setFocus(); APP.processEvents(); focused=APP.focusWidget()
        path=self.root/'invalid.json'; path.write_text('{invalid',encoding='utf-8')
        w.generation_panel.import_workflow(path); error=w.findChild(ImportErrorToast)
        self.assertIsNotNone(error); self.assertTrue(error.motion.state()==error.motion.State.Running)
        self.assertGreaterEqual(error.y(),w.height()-2); self.assertIs(APP.focusWidget(),focused); self.assertIsNone(APP.activeModalWidget())
        QTest.qWait(210); self.assertEqual(error.geometry().bottom(),w.height()-25)
        self.assertEqual(len(error.findChildren(QPushButton)),0); self.assertFalse(error.isWindow())
        QTest.keyClicks(editor,'x'); self.assertTrue(editor.text().endswith('x'))
        self.assertTrue(w.settings_page.workflows.feedback.isHidden()); self.assertEqual(w.state['generation'],before)
        QTest.qWait(2100); self.assertFalse(isValid(error)); self.assertTrue(w.isVisible())
    def test_repeated_error_reuses_notice_and_tracks_resized_window(self):
        toast=import_error(self.window,'first'); QTest.qWait(220)
        self.assertIs(import_error(self.window,'second'),toast); self.assertEqual(len(self.window.findChildren(ImportErrorToast)),1)
        self.window.resize(1200,820); QTest.qWait(220)
        self.assertEqual(toast.geometry().bottom(),self.window.height()-25); self.assertEqual(toast.message.text(),'second')
        self.window.hide(); APP.sendPostedEvents(None,QEvent.Type.DeferredDelete); self.assertFalse(isValid(toast))
    def test_media_import_failure_uses_one_dialog_instead_of_small_status_text(self):
        gallery=self.window.gallery; gallery.queue=[]; gallery.import_errors=['missing.png：找不到檔案。']; gallery.import_done=0
        gallery.next_image(); self.assertEqual(len(self.window.findChildren(ImportErrorToast)),1)
        self.assertEqual(self.window.status.text(),''); self.assertFalse(gallery.importing)
        self.assertEqual(error_message(FileNotFoundError('very long path')),'找不到檔案，請確認檔案位置。')
    def test_function_submenu_can_hide_and_reopen_until_root_closes(self):
        root=RoundMenu(self.window); root.addAction('加入 Tag 與素材')
        sub=self.window.canvas.functions.menu(root,QPoint(0,0)); action=root.addMenu(sub); root.addAction('新增模組')
        root.open_at(self.window.mapToGlobal(QPoint(50,70)))
        for _ in range(3):
            sub.popup(root.pos()+QPoint(root.width(),20)); QTest.qWait(10); sub.hide()
            APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            self.assertTrue(isValid(sub)); self.assertIn(action,root.actions()); self.assertEqual(len(sub.actions()),3)
        root.close(); APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(root)); self.assertFalse(isValid(sub))
    def test_source_keeps_pixels_through_resize_zoom_and_repeated_refresh(self):
        path=self.root/'detail.png'; image=QImage(832,1216,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.blue); image.save(str(path))
        c=self.window.canvas
        with patch('prompt_studio.image_preview.QImageReader',wraps=QImageReader) as reader:
            key=c.functions.add_image(path=path); card=c.functions.cards[key]; source=card.panel.preview
            self.assertEqual(source.image.size(),image.size()); self.assertGreater(source.image.width(),source.width())
            for index in range(30):
                card.requested_size=[320+index,350+index]; card.layout_card(); card.panel.refresh()
            c.view.scale(1.5,1.5); APP.processEvents(); self.assertEqual(reader.call_count,1)
        self.assertLessEqual(source.image.sizeInBytes(),2048*2048*4)
        source.load_path(None); self.assertTrue(source.image.isNull()); self.assertIn('尚未選擇',source.text())
        source.load_path(self.root/'missing.png'); self.assertTrue(source.image.isNull()); self.assertIn('無法讀取',source.text())
    def test_large_source_is_bounded_and_file_change_invalidates_cache(self):
        path=self.root/'large.png'; image=QImage(3000,2200,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.blue); image.save(str(path))
        preview=SourcePreview(self.window.store); preview.load_path(path)
        self.assertEqual(preview.image.width(),2048); self.assertLessEqual(preview.image.sizeInBytes(),16*1024*1024)
        image=QImage(8,8,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.red); image.save(str(path)); preview.load_path(path)
        self.assertEqual(preview.image.size(),image.size()); self.assertEqual(preview.image.pixelColor(0,0),Qt.GlobalColor.red); preview.deleteLater()
    def test_settings_reading_area_is_bounded_but_model_tab_uses_full_width(self):
        w=self.window; w.resize(2000,1050); w.settings('appearance'); QTest.qWait(30)
        area=w.settings_page.pages.currentWidget(); self.assertIsInstance(area,QScrollArea)
        self.assertLessEqual(w.settings_page.preferences.material.width(),1440)
        self.assertGreater(w.settings_page.reading.width(),1400)
        reading=w.settings_page.reading; page=w.settings_page
        self.assertLessEqual(reading.mapTo(page,reading.rect().bottomRight()).y(),page.height()-1)
        self.assertFalse(hasattr(page,'footer'))
        for material in ('solid','mica','acrylic'):
            w.settings_page.preferences.material.setCurrentIndex(w.settings_page.preferences.material.findData(material)); APP.processEvents()
            self.assertTrue(reading.isVisible())
            self.assertTrue(page.rect().contains(reading.mapTo(page,reading.rect().bottomRight())))
        w.settings('models'); QTest.qWait(30); self.assertGreater(w.models.width(),1200)


if __name__=='__main__': unittest.main()
