"""Native media navigation preserves catalog state across layout changes."""
import copy
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt, QPoint, QPointF, QRect
from PySide6.QtGui import QFontDatabase, QHelpEvent, QImage, QPainter, QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QListWidget, QPushButton, QStyleOptionViewItem

from prompt_calculus_studio.window import Window

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf','consola.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class MediaLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.environment=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'}); self.environment.start()
        self.transport=patch('prompt_calculus_studio.comfy_client.ComfyClient.request',side_effect=AssertionError('No service in media UI tests')); self.transport.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1440,900); self.w.apply_theme(); self.w.show(); self.w.show_page(self.w.gallery)
        self.g=self.w.gallery; self.settle()

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.transport.stop(); self.environment.stop(); self.temp.cleanup()

    def settle(self):QTest.qWait(40)

    def add_images(self,count=4):
        source=Path(self.temp.name)/'fixture.png'; image=QImage(96,144,QImage.Format.Format_RGB32); image.fill(Qt.GlobalColor.darkGreen); image.save(str(source))
        self.w.catalog.put('album',dict(id='album',name='視覺練習'))
        for i in range(count):
            record=dict(id=f'image-{i:03}',album='album',name=f'Image {i:03}.png',path=str(source),thumb='',owned=False,
                        metadata={'source':'none','raw':{}},manual=None,notes=f'Original note {i}')
            self.w.catalog.put('image',record,'album')
        self.g.refresh_albums(); self.settle()

    def test_empty_library_and_album_show_relevant_next_action(self):
        self.assertTrue(self.g.empty_start.isVisible()); self.assertEqual(self.g.empty_action.text(),'新增資料夾')
        self.assertGreater(self.g.album_title.width(),self.g.album_title.fontMetrics().horizontalAdvance('媒體庫'))
        self.assertFalse(self.g.detail_scroll.isVisible()); self.assertFalse(self.g.import_button.isEnabled())
        self.w.catalog.put('album',dict(id='album',name='空資料夾')); self.g.refresh_albums(); self.settle()
        self.assertEqual(self.g.empty_action.text(),'匯入圖片'); self.assertTrue(self.g.import_button.isEnabled())
        self.g.search.setText('missing'); self.settle()
        self.assertEqual(self.g.empty_action.text(),'清除搜尋'); self.assertEqual(self.g.counter.text(),'0 張')
        self.assertFalse(self.g.previous.isVisible()); self.assertFalse(self.g.next.isVisible())

    def test_folder_removal_confirms_all_records_and_preserves_original_files(self):
        self.add_images(65)
        source=Path(self.temp.name)/'fixture.png'; original_bytes=source.read_bytes()
        owned=Path(self.temp.name)/'owned.png'; owned.write_bytes(original_bytes)
        thumb=Path(self.temp.name)/'preview.png'; thumb.write_bytes(original_bytes)
        record=self.w.catalog.get('image-000'); record.update(path=str(owned),owned=True,thumb=str(thumb))
        self.w.catalog.put('image',record,'album')
        other=dict(record,id='other-image',album='other',name='Other.png',owned=False,path=str(source))
        self.w.catalog.put('album',dict(id='other',name='另一資料夾')); self.w.catalog.put('image',other,'other')
        recent=dict(other,id='recent-entry',album=''); self.w.catalog.put('recent',recent)
        self.w.state['settings']['recent_destination']='album:album'
        self.g.refresh_albums(); self.g.search.setText('Image'); self.g.turn(1); self.g.images.setCurrentRow(1); self.settle()
        current=copy.deepcopy(self.g.record)
        selected={item.data(Qt.ItemDataRole.UserRole)['id'] for item in self.g.images.selectedItems()}
        before=list(self.w.catalog.db.execute('SELECT * FROM resources ORDER BY id'))
        with patch('prompt_calculus_studio.pages.ask',return_value=False) as confirmation:
            self.g.delete_album()
            self.assertIn('65 張圖片紀錄',confirmation.call_args.args[2])
            self.assertIn('磁碟原圖、應用內副本與縮圖都會保留',confirmation.call_args.args[2])
        self.assertEqual(list(self.w.catalog.db.execute('SELECT * FROM resources ORDER BY id')),before)
        self.assertEqual(self.g.record,current); self.assertTrue(self.g.detail_scroll.isVisible())
        self.assertEqual({item.data(Qt.ItemDataRole.UserRole)['id'] for item in self.g.images.selectedItems()},selected)
        with patch('prompt_calculus_studio.pages.ask',return_value=True):self.g.delete_album()
        self.settle()
        self.assertIsNone(self.w.catalog.get('album')); self.assertEqual(self.w.catalog.count('image','album'),0)
        self.assertEqual(self.w.catalog.get('other-image'),other); self.assertEqual(self.w.catalog.get('recent-entry'),recent)
        self.assertEqual(self.g.album,'other'); self.assertEqual(self.g.albums.count(),1)
        self.assertEqual(self.g.images.count(),1); self.assertEqual(self.g.page,0); self.assertFalse(self.g.search.text())
        self.assertIsNone(self.g.record); self.assertIsNone(self.g.images.currentItem())
        self.assertFalse(self.g.detail_scroll.isVisible()); self.assertFalse(self.g.detail_name.text())
        self.assertTrue(self.g.preview.pixmap().isNull()); self.assertFalse(self.g.export_selection.isEnabled())
        self.assertEqual(self.w.state['settings']['recent_destination'],'')
        self.assertEqual(self.w.recent.destination_picker.findData('album:album'),-1)
        # Removing the remaining populated folder reaches the real empty state.
        self.g.images.setCurrentRow(0); self.settle()
        with patch('prompt_calculus_studio.pages.ask',return_value=True):self.g.delete_album()
        self.settle()
        self.assertEqual(self.w.catalog.count('album'),0); self.assertEqual(self.w.catalog.count('image'),0)
        self.assertEqual(self.g.albums.count(),0); self.assertEqual(self.g.images.count(),0)
        self.assertIsNone(self.g.album); self.assertIsNone(self.g.record)
        self.assertFalse(self.g.detail_scroll.isVisible()); self.assertTrue(self.g.preview.pixmap().isNull())
        self.assertTrue(self.g.empty_start.isVisible()); self.assertEqual(self.g.empty_action.text(),'新增資料夾')
        self.assertEqual(self.g.counter.text(),'0 張')
        self.assertEqual(self.w.catalog.get('recent-entry'),recent)
        for path in (source,owned,thumb):self.assertEqual(path.read_bytes(),original_bytes)

    def test_collapsed_folder_actions_create_and_open_menu_at_visible_rail(self):
        self.w.sidebar_pinned_preference=False; self.w.sync_context_sidebar(); self.settle()
        rail=self.w.context_rail
        self.assertTrue(rail.isVisible()); self.assertIs(rail.peek_trigger,rail.buttons['sidebar'])
        for key,title in (('add-folder','新增資料夾'),('folder-more','資料夾操作')):
            self.assertTrue(rail.buttons[key].isVisible()); self.assertEqual(rail.buttons[key].accessibleName(),title)
            self.assertFalse(rail.buttons[key].isCheckable())
        self.assertEqual(rail.buttons['folder-more'].property('iconName'),'more-horizontal')
        with patch('prompt_calculus_studio.pages.QInputDialog.getText',return_value=('新資料夾',True)):
            rail.buttons['add-folder'].click()
        self.settle()
        self.assertEqual(self.w.catalog.count('album'),1); self.assertEqual(self.g.albums.currentItem().text(),'新資料夾')
        self.assertFalse(self.g.sidebar_expanded)
        menus=[]
        with patch('prompt_calculus_studio.pages.QMenu.open_at',autospec=True,side_effect=lambda menu,position:menus.append((menu,position))):
            rail.buttons['folder-more'].click()
        menu,position=menus[0]; trigger=rail.buttons['folder-more']
        self.assertEqual(position,trigger.mapToGlobal(trigger.rect().bottomLeft()))
        removal=next(action for action in menu.actions() if action.text()=='移除資料夾…')
        with patch('prompt_calculus_studio.pages.ask',return_value=True):removal.trigger()
        self.settle(); self.assertEqual(self.w.catalog.count('album'),0)
        self.assertEqual(self.g.empty_action.text(),'新增資料夾')

    def held_collection(self):
        record=dict(self.w.catalog.get('image-000'),id='recent-held',album='',collected={},
                    source={'prompt_id':'offline-fixture','image':{'filename':'fixture.png','type':'output','subfolder':''}})
        self.w.catalog.put('recent',record)
        self.w.state['settings']['recent_destination']='album:album'; self.w.recent.refresh_destinations()
        request=Mock()
        with patch.object(self.w.comfy,'connected',True),patch.object(self.w.comfy,'request',request):
            self.w.recent.collect(record)
        destination=Path(request.call_args.args[1]['destination'])/'collected.png'
        destination.write_bytes(Path(record['path']).read_bytes())
        result=dict(path=str(destination),has_generation=False,has_snapshot=False)
        return record,request,result

    def test_album_removal_waits_for_network_queued_active_and_confirmation_started_collection(self):
        import threading
        self.add_images(1); record,request,result=self.held_collection(); recent=self.w.recent
        jobs=Mock(active=None); jobs.start.return_value=True
        with patch.object(self.w,'jobs',jobs),patch('prompt_calculus_studio.recent.QTimer.singleShot'):
            for phase in ('network','queued','active'):
                if phase=='queued':
                    jobs.active=object(); request.call_args.args[2](result)
                    self.assertTrue(recent.saves); self.assertFalse(recent.processing)
                elif phase=='active':
                    jobs.active=None; recent.pump()
                    self.assertFalse(recent.saves); self.assertTrue(recent.processing)
                with self.subTest(phase=phase),patch('prompt_calculus_studio.pages.ask') as confirm,patch.object(self.w,'notice') as notice:
                    self.g.delete_album(); confirm.assert_not_called()
                    self.assertIsNotNone(self.w.catalog.get('album')); self.assertIn('等待圖片收藏',notice.call_args.args[0])
            work,done=jobs.start.call_args.args[1:3]; done(work(threading.Event()))
            self.assertFalse(recent.collecting); self.assertFalse(recent.processing)
            self.assertTrue(self.w.catalog.get(record['id'])['collected'])
        before=list(self.w.catalog.db.execute('SELECT * FROM resources ORDER BY id'))
        def start_during_confirmation(*_):
            recent.collect(record); return True
        request=Mock()
        try:
            with patch.object(self.w.comfy,'connected',True),patch.object(self.w.comfy,'request',request),patch('prompt_calculus_studio.pages.ask',side_effect=start_during_confirmation):self.g.delete_album()
            request.assert_called_once(); self.assertIn(record['id'],recent.collecting)
            self.assertEqual(list(self.w.catalog.db.execute('SELECT * FROM resources ORDER BY id')),before)
        finally:
            if request.called:request.call_args.args[3]('fixture cancellation')

    def test_late_collection_result_cannot_recreate_removed_album_or_report_success(self):
        import threading
        for removed_at in ('network','active'):
            with self.subTest(removed_at=removed_at):
                self.add_images(1); record,request,result=self.held_collection(); recent=self.w.recent
                downloaded=Path(result['path']); original_bytes=downloaded.read_bytes()
                jobs=Mock(active=None); jobs.start.return_value=True
                with patch.object(self.w,'jobs',jobs),patch('prompt_calculus_studio.recent.QTimer.singleShot'),patch.object(self.w,'notice') as notice:
                    if removed_at=='active':
                        request.call_args.args[2](result)
                        value=jobs.start.call_args.args[1](threading.Event())
                        self.assertTrue(recent.processing)
                    # Simulate an externally removed target to exercise the
                    # callback guard independently of the UI's busy guard.
                    with self.w.catalog.db:
                        self.w.catalog.db.execute("DELETE FROM resources WHERE id='album' OR (kind='image' AND parent='album')")
                    if removed_at=='network':
                        request.call_args.args[2](result)
                        value=jobs.start.call_args.args[1](threading.Event())
                    jobs.start.call_args.args[2](value)
                    self.assertFalse(recent.collecting); self.assertFalse(recent.processing); self.assertFalse(recent.saves)
                    self.assertIsNone(self.w.catalog.get('album')); self.assertEqual(self.w.catalog.count('image','album'),0)
                    self.assertFalse(self.w.catalog.get(record['id']).get('collected'))
                    self.assertTrue(any('請重新選擇' in call.args[0] for call in notice.call_args_list))
                    self.assertFalse(any(call.args[0].startswith('已收藏') for call in notice.call_args_list))
                    self.assertTrue(recent.destination_warning.isVisibleTo(recent)); self.assertNotIn('已收藏',recent.save_button.text())
                    self.assertEqual(self.w.state['settings']['recent_destination'],'')
                    self.assertEqual(downloaded.read_bytes(),original_bytes)
                    self.assertEqual(Path(record['path']).read_bytes(),original_bytes)

    def test_cancelled_collection_worker_releases_album_removal_guard(self):
        self.add_images(1); record,request,result=self.held_collection(); recent=self.w.recent
        downloaded=Path(result['path']); original_bytes=downloaded.read_bytes()
        jobs=Mock(active=None); jobs.start.return_value=True
        with patch.object(self.w,'jobs',jobs),patch('prompt_calculus_studio.recent.QTimer.singleShot'):
            request.call_args.args[2](result)
            self.assertTrue(recent.processing); self.assertIn(record['id'],recent.collecting)
            jobs.start.call_args.args[3]('已取消操作。')
            self.assertFalse(recent.processing); self.assertFalse(recent.collecting)
            self.assertFalse(self.w.catalog.get(record['id']).get('collected'))
            with patch('prompt_calculus_studio.pages.ask',return_value=True) as confirm:self.g.delete_album()
            confirm.assert_called_once(); self.assertIsNone(self.w.catalog.get('album'))
            self.assertEqual(downloaded.read_bytes(),original_bytes)

    def test_presentation_and_refresh_keep_selection_metadata_and_search(self):
        self.add_images(65); self.g.search.setText('Image'); self.g.turn(1); self.settle()
        self.g.images.setCurrentRow(2); self.g.images.item(0).setSelected(True); self.settle()
        selected={i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()}
        current=self.g.record['id']; before=copy.deepcopy(self.w.catalog.get(current)); items=[self.g.images.item(i) for i in range(self.g.images.count())]
        for mode in ('list','images'):
            self.g.set_view_mode(mode); self.settle()
            self.assertEqual(self.g.images.viewMode(),QListWidget.ViewMode.ListMode if mode=='list' else QListWidget.ViewMode.IconMode)
            self.assertEqual([self.g.images.item(i) for i in range(self.g.images.count())],items)
            self.assertEqual({i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()},selected)
            self.assertEqual((self.g.page,self.g.search.text(),self.g.record['id']),(1,'Image',current))
        self.g.refresh(); self.settle()
        self.assertEqual({i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()},selected)
        self.assertEqual(self.g.record['id'],current); self.assertEqual(self.w.catalog.get(current),before)
        self.assertEqual(self.g.counter.text(),'65 張'); self.assertEqual(self.g.page_counter.text(),'2 / 2 頁')

    def test_narrow_folder_selection_returns_to_images_and_details_are_reversible(self):
        self.add_images(); self.w.resize(800,640); self.settle()
        self.assertFalse(self.g.sidebar_expanded)
        self.g.set_sidebar_expanded(True); self.settle()
        self.assertTrue(self.g.folder_panel.isVisible()); self.assertFalse(self.g.image_panel.isVisible())
        self.g.refresh_albums(); self.settle(); self.assertTrue(self.g.sidebar_expanded)
        self.g.choose_album(self.g.albums.currentItem()); self.settle()
        self.assertFalse(self.g.sidebar_expanded); self.assertTrue(self.g.image_panel.isVisible())
        self.g.images.setCurrentRow(1); self.settle(); selected=self.g.record['id']
        self.assertTrue(self.g.detail_scroll.isVisible()); self.assertFalse(self.g.image_panel.isVisible())
        self.g.hide_details(); self.settle()
        self.assertTrue(self.g.image_panel.isVisible()); self.assertEqual(self.g.record['id'],selected)
        self.g.images.itemClicked.emit(self.g.images.currentItem()); self.settle()
        self.assertTrue(self.g.detail_scroll.isVisible()); self.assertEqual(self.g.record['id'],selected)

    def test_model_editor_only_opens_for_a_selected_record(self):
        self.w.settings('models'); self.settle(); models=self.w.models; models.refresh(); self.settle()
        self.assertFalse(models.detail_scroll.isVisible()); self.assertTrue(models.library_empty.isVisible())
        root=Path(self.temp.name)/'models'; root.mkdir(); source=root/'example.safetensors'; source.write_bytes(b'fixture')
        record=dict(id='model',name='Example',path=str(source),root=str(root.resolve()),kind='LoRA',size=7,mtime=source.stat().st_mtime_ns,thumb='',trigger='kept trigger',notes='kept notes',url='')
        self.w.catalog.put('model',record,str(root.resolve())); models.root.setText(str(root)); models.refresh(); models.list.setCurrentRow(0); self.settle()
        self.assertTrue(models.detail_scroll.isVisible()); self.assertEqual(models.notes.toPlainText(),'kept notes')
        models.notes.setPlainText('edited notes'); models.search.setText('missing'); self.settle()
        self.assertFalse(models.detail_scroll.isVisible()); self.assertEqual(self.w.catalog.get('model')['notes'],'edited notes')
        self.assertEqual(models.empty_title.text(),'沒有符合的模型')

    def test_thumbnails_are_rounded_without_changing_source_ratio_or_cached_icon(self):
        self.add_images()
        for mode in ('images','list'):
            self.g.set_view_mode(mode); self.settle()
            item=self.g.images.item(0); rect=self.g.images.visualItemRect(item)
            rendered=self.g.images.viewport().grab().toImage().copy(rect)
            pixels=[(x,y) for y in range(rendered.height()) for x in range(rendered.width()) if rendered.pixelColor(x,y).name()=='#008000']
            if mode=='list':
                self.assertFalse(pixels); self.assertEqual(item.text(),item.data(Qt.ItemDataRole.UserRole)['name']); continue
            self.assertTrue(pixels)
            left=min(x for x,y in pixels); right=max(x for x,y in pixels)
            top=min(y for x,y in pixels); bottom=max(y for x,y in pixels)
            self.assertNotEqual(rendered.pixelColor(left,top).name(),'#008000')
            self.assertAlmostEqual((right-left+1)/(bottom-top+1),96/144,delta=.04)
            cached=item.icon().pixmap(208,208).toImage()
            self.assertEqual(cached.pixelColor(0,0).name(),'#008000')

    def test_single_file_list_has_unclipped_name_and_no_thumbnail(self):
        self.add_images(1); self.w.resize(2560,1440); self.settle()
        self.g.set_view_mode('list'); self.settle()
        item=self.g.images.item(0); rect=self.g.images.visualItemRect(item)
        self.assertGreaterEqual(rect.top(),0)
        self.assertTrue(self.g.images.viewport().rect().contains(rect))
        self.assertGreaterEqual(rect.height(),self.g.images.fontMetrics().lineSpacing()+18)
        self.assertFalse(self.g.thumbnail_control.isVisible())
        self.assertEqual(item.text(),'Image 000.png')
        rendered=self.g.images.viewport().grab().toImage().copy(rect)
        # The filename's light text has space above and below, and no image pixels remain.
        text_rows=[y for y in range(rendered.height()) if any(rendered.pixelColor(x,y).lightness()>150 for x in range(rendered.width()))]
        self.assertTrue(text_rows); self.assertGreater(min(text_rows),3); self.assertLess(max(text_rows),rect.height()-4)

    def test_fullscreen_thumbnail_bounds_and_narrow_window_reduce_columns(self):
        self.add_images(60)
        screen=Mock(); screen.availableGeometry.return_value=QRect(0,0,2560,1440)
        def columns():
            first=self.g.images.visualItemRect(self.g.images.item(0)).top()
            return sum(self.g.images.visualItemRect(self.g.images.item(i)).top()==first for i in range(self.g.images.count()))
        with patch.object(self.w,'screen',return_value=screen):
            self.w.resize(2560,1440); self.settle()
            for value,expected in ((0,20),(8,12),(12,8)):
                self.g.thumbnail_size.setValue(value); self.settle()
                self.assertEqual(columns(),expected)
            large_size=self.g.images.iconSize()
            self.w.resize(800,640); self.settle()
            self.assertLess(columns(),8); self.assertEqual(self.g.images.iconSize(),large_size)
            self.assertEqual(self.w.size().width(),800); self.assertTrue(self.g.thumbnail_control.isVisible())
            self.g.thumbnail_size.setValue(6); self.settle()
            view=self.g.images.viewport(); point=QPointF(view.rect().center())
            event=QWheelEvent(point,QPointF(view.mapToGlobal(point.toPoint())),QPoint(),QPoint(0,120),Qt.MouseButton.NoButton,Qt.KeyboardModifier.ControlModifier,Qt.ScrollPhase.NoScrollPhase,False)
            APP.sendEvent(view,event); self.settle()
            self.assertEqual(self.g.thumbnail_size.value(),7); self.assertEqual(self.w.state['settings']['gallery_columns'],13)
            for steps,limit in ((100,12),(-100,0)):
                self.g.images.zoomRequested.emit(steps); self.settle(); self.assertEqual(self.g.thumbnail_size.value(),limit)

    def test_image_details_change_columns_without_resizing_thumbnails(self):
        self.add_images(60)
        screen=Mock(); screen.availableGeometry.return_value=QRect(0,0,2560,1440)
        def columns():
            first=self.g.images.visualItemRect(self.g.images.item(0)).top()
            return sum(self.g.images.visualItemRect(self.g.images.item(i)).top()==first for i in range(self.g.images.count()))
        self.g.images.blockSignals(True); self.g.images.setCurrentRow(0); self.g.images.item(1).setSelected(True); self.g.images.blockSignals(False)
        item=self.g.images.currentItem(); self.g.select(item,open_detail=False)
        selected={i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()}
        record=copy.deepcopy(self.g.record)
        with patch.object(self.w,'screen',return_value=screen):
            self.w.resize(2560,1440); self.settle()
            for expected in (8,12,20):
                with self.subTest(fullscreen_columns=expected):
                    self.g.hide_details(); self.g.thumbnail_size.setValue(20-expected); self.settle()
                    size=self.g.images.iconSize(); grid=self.g.images.gridSize()
                    self.assertEqual(columns(),expected); self.assertEqual(grid.width(),grid.height())
                    self.g.select(item); self.settle()
                    self.assertEqual(self.g.images.iconSize(),size); self.assertEqual(self.g.images.gridSize(),grid)
                    self.assertGreater(columns(),0); self.assertLess(columns(),expected)
                    self.g.split.moveSplitter(self.g.split.width()-430-self.g.split.handleWidth(),1); self.settle()
                    self.assertEqual(self.g.images.iconSize(),size); self.assertEqual(self.g.images.gridSize(),grid)
                    self.g.hide_details(); self.settle()
                    self.assertEqual(columns(),expected); self.assertEqual(self.g.images.iconSize(),size)
            self.w.resize(800,640); self.settle()
            self.assertEqual(self.g.images.iconSize(),size)
            self.assertEqual(self.g.images.horizontalScrollBar().maximum(),0)
            # On an unusually large screen, a narrow window may fit less than
            # the chosen full-screen tile. Only this single-column case shrinks.
            screen.availableGeometry.return_value=QRect(0,0,7680,4320)
            self.g.thumbnail_size.setValue(12); self.settle()
            self.assertEqual(columns(),1)
            rect=self.g.images.visualItemRect(item)
            self.assertGreaterEqual(rect.left(),0); self.assertLess(rect.right(),self.g.images.viewport().width())
            self.assertEqual(self.g.images.horizontalScrollBar().maximum(),0)
        self.assertEqual({i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()},selected)
        self.assertIs(self.g.images.currentItem(),item)
        self.assertEqual(self.g.record,record); self.assertEqual(self.w.catalog.get(record['id']),record)

    def test_shared_header_and_adaptive_columns_keep_scrollbar_space(self):
        self.add_images(60); self.g.images.setCurrentRow(0); self.g.images.item(2).setSelected(True)
        self.g.set_view_mode('list'); self.settle()
        record=copy.deepcopy(self.g.record)
        for width in (1280,1440,1920):
            self.w.resize(width,900); self.w.apply_theme(); self.w.show_page(self.g)
            self.g.hide_details(); self.g.select(self.g.images.item(0)); self.settle()
            workspace=self.g.media_workspace
            self.assertEqual(workspace.x(),self.g.folder_panel.width())
            group=self.g.media_group.geometry()
            self.assertEqual(group.width(),workspace.width())
            self.assertEqual(group.x(),0)
            self.assertEqual(self.g.split.width(),group.width())
            self.assertGreaterEqual(self.g.search.width(),560)
            start=self.g.search.mapTo(workspace,QPoint())
            viewport=self.g.images.viewport()
            viewport_start=viewport.mapTo(workspace,QPoint())
            self.assertEqual(start.x(),24)
            self.assertLessEqual(abs(start.x()-viewport_start.x()),1)
            self.assertLessEqual(abs(self.g.search.width()-viewport.width()),1)
            bar=self.g.images.verticalScrollBar()
            bar_start=bar.mapTo(workspace,QPoint())
            self.assertTrue(bar.isVisible())
            self.assertGreaterEqual(bar_start.x()-viewport_start.x()-viewport.width(),12)
            card_start=self.g.detail_card.mapTo(workspace,QPoint())
            self.assertLess(card_start.x()-viewport_start.x()-viewport.width(),80)
            self.assertEqual(card_start.y(),viewport_start.y())
            self.assertLess(self.g.image_mode.mapTo(workspace,QPoint()).y(),start.y())
            self.assertEqual(self.g.counter.mapTo(workspace,QPoint()).x(),start.x())
        # A hidden scrollbar keeps its gutter, so a short result list does not
        # grow wider than the search box or shift the selected row border.
        self.g.search.setText('Image 000'); self.settle()
        self.assertEqual(self.g.images.verticalScrollBar().maximum(),0)
        self.assertEqual(self.g.images.viewport().width(),self.g.search.width())
        self.g.search.clear(); self.settle()
        self.g.select(self.g.images.item(0)); self.settle()
        before=self.g.detail_scroll.width()
        self.g.split.moveSplitter(self.g.split.width()-430-self.g.split.handleWidth(),1); self.settle()
        self.assertNotEqual(self.g.detail_scroll.width(),before)
        moved=self.g.detail_scroll.width(); self.w.resize(1440,900); self.settle()
        self.assertEqual(self.g.detail_scroll.width(),moved)
        self.g.set_view_mode('images'); self.settle()
        self.assertEqual(self.g.split.width(),self.g.media_workspace.width())
        self.assertEqual(self.w.catalog.get(record['id']),record)

    def test_reading_group_survives_sidebar_drawer_resize_and_return(self):
        self.add_images(60); self.g.images.setCurrentRow(0); self.g.images.item(2).setSelected(True)
        self.g.set_view_mode('list'); self.w.resize(1920,1080)
        self.w.sidebar_pinned_preference=False; self.w.sync_context_sidebar(); self.settle()
        parent=self.g.folder_panel.parentWidget(); geometry=self.g.media_group.geometry()
        selected={item.data(Qt.ItemDataRole.UserRole)['id'] for item in self.g.images.selectedItems()}
        record=copy.deepcopy(self.g.record)
        self.assertTrue(self.w.sidebar_peek._borrow(self.g.folder_panel)); self.settle()
        self.w.sidebar_resize.sync(); self.w.sidebar_resize.resize_to(350); self.w.sidebar_resize.finish(); self.settle()
        self.assertEqual(self.g.media_group.geometry(),geometry)
        self.assertTrue(self.w.sidebar_peek.is_open)
        self.w.sidebar_peek.close(); self.settle()
        self.assertIs(self.g.folder_panel.parentWidget(),parent)
        self.assertEqual(parent.indexOf(self.g.folder_panel),0)
        self.assertEqual(self.g.media_group.geometry(),geometry)
        self.w.sidebar_pinned_preference=True; self.w.sync_context_sidebar(); self.settle()
        self.assertEqual(self.g.folder_panel.width(),350)
        self.assertEqual(self.g.media_workspace.x(),350)
        self.assertEqual(self.g.media_group.width(),self.g.media_workspace.width())
        self.assertLess(abs(self.g.media_group.geometry().center().x()-self.g.media_workspace.rect().center().x()),3)
        self.assertEqual({item.data(Qt.ItemDataRole.UserRole)['id'] for item in self.g.images.selectedItems()},selected)
        self.assertEqual(self.w.catalog.get(record['id']),record)

    def test_context_rail_and_pinned_drawer_keep_column_limits_and_thumbnail_size(self):
        self.add_images(60)
        screen=Mock(); screen.availableGeometry.return_value=QRect(0,0,2560,1440)
        def columns():
            first=self.g.images.visualItemRect(self.g.images.item(0)).top()
            return sum(self.g.images.visualItemRect(self.g.images.item(i)).top()==first for i in range(self.g.images.count()))
        with patch.object(self.w,'screen',return_value=screen):
            for pinned in (False,True):
                self.w.sidebar_pinned_preference=pinned
                for expected in (8,12,20):
                    with self.subTest(pinned=pinned,columns=expected):
                        self.w.resize(2560,1440); self.w.sync_context_sidebar()
                        self.g.thumbnail_size.setValue(20-expected); self.settle()
                        self.assertEqual(columns(),expected)
                        self.assertEqual(self.w.context_rail.isVisible(),not pinned)
                        size=self.g.images.iconSize()
                        self.w.resize(800,640); self.settle()
                        self.assertEqual(self.g.images.iconSize(),size)
                        self.assertGreater(columns(),0); self.assertLess(columns(),expected)
                        self.assertTrue(self.w.context_rail.isVisible())
                        self.assertEqual(self.w.sidebar_peek.is_pinned_overlay,pinned)
                        self.assertTrue(self.g.image_panel.isVisible())

    def test_image_detail_actions_fit_300px_and_remain_reachable(self):
        self.add_images(1); self.g.images.setCurrentRow(0); self.settle()
        for size in (11,18):
            with self.subTest(font_size=size):
                self.w.state['settings']['ui_size']=size; self.w.apply_theme()
                self.g.split.moveSplitter(self.g.split.width()-336-self.g.split.handleWidth(),1); self.settle()
                scroll=self.g.detail_scroll
                self.assertGreaterEqual(self.g.detail_card.width(),300)
                self.assertLessEqual(scroll.width(),340)
                self.assertEqual(self.g.detail_card.geometry().top(),self.g.search.height()+12)
                self.assertEqual(scroll.viewport().width()-self.g.detail_card.geometry().right()-1,24)
                self.assertEqual(scroll.horizontalScrollBar().maximum(),0)
                actions=scroll.widget().findChildren(QPushButton)
                for action in actions:self.assertGreaterEqual(action.width(),action.minimumSizeHint().width(),action.text())
                scroll.ensureWidgetVisible(self.g.detail_more); self.settle()
                bounds=QRect(self.g.detail_more.mapTo(scroll.viewport(),QPoint()),self.g.detail_more.size())
                self.assertTrue(scroll.viewport().rect().contains(bounds))
                menus=[]
                with patch('prompt_calculus_studio.pages.QMenu.open_at',lambda menu,position:menus.append(menu)):
                    self.g.detail_more.click()
                choices={action.text():action for action in menus[0].actions() if not action.isSeparator()}
                self.assertEqual(set(choices),{'恢復模組組合（無快照）','重新連結原圖','附上目前工作區資料','編輯備註','移至其它資料夾','移除圖片紀錄…'})
                self.assertFalse(choices['恢復模組組合（無快照）'].isEnabled())
                self.assertIn('未保存',choices['恢復模組組合（無快照）'].toolTip())
                with patch('prompt_calculus_studio.pages.ask',return_value=False) as confirmation, patch.object(self.w.catalog,'delete') as deleted:
                    choices['移除圖片紀錄…'].trigger()
                    confirmation.assert_called_once(); deleted.assert_not_called()

    def test_viewed_image_is_distinct_from_multi_selection_and_action_scope_is_preserved(self):
        from prompt_calculus_studio.theme import visual_tokens
        self.add_images(3); self.g.images.setCurrentRow(0); self.g.images.item(2).setSelected(True); self.settle()
        viewed=self.g.images.item(0); other=self.g.images.item(2)
        selected=[i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()]
        self.assertEqual(self.g.selection_counter.text(),'· 已選 2 張')
        self.assertEqual(self.g.detail_name.text(),viewed.text())
        self.assertEqual(self.g.export_selection.text(),'匯出 2 張…')
        accent=visual_tokens(self.w.state['settings'])['accent']
        for mode in ('list','images'):
            self.g.set_view_mode(mode); self.g.images.clearFocus(); self.settle()
            rendered=self.g.images.viewport().grab().toImage()
            def accent_pixels(item):
                image=rendered.copy(self.g.images.visualItemRect(item))
                return sum(image.pixelColor(x,y).name()==accent for y in range(image.height()) for x in range(image.width()))
            self.assertGreater(accent_pixels(viewed),0); self.assertEqual(accent_pixels(other),0)
        with patch('prompt_calculus_studio.pages.open_file') as opened, patch.object(self.w,'open_export') as exported:
            self.g.open_original(); opened.assert_called_once_with(self.g,viewed.data(Qt.ItemDataRole.UserRole)['path'])
            self.g.export_selected(); self.assertEqual(len(exported.call_args.args[0]),2)
        # The existing context menu can inspect an already selected item without
        # moving Qt's current index. The visible marker follows the actual detail record.
        self.g.select(other); self.settle()
        self.assertIs(self.g.images.currentItem(),viewed)
        self.assertEqual(self.g.detail_name.text(),other.text())
        self.assertEqual(self.g.record['id'],other.data(Qt.ItemDataRole.UserRole)['id'])
        self.assertEqual([i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()],selected)

    def test_detail_card_follows_content_and_short_window_actions_remain_reachable(self):
        self.add_images(1); item=self.g.images.item(0); self.g.images.setCurrentRow(0)
        record=self.w.catalog.get(item.data(Qt.ItemDataRole.UserRole)['id'])
        record['notes']=''
        for size in (11,18):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme()
            for text in ('','Seed: 42','Long prompt and settings\n'*800):
                with self.subTest(font_size=size,long=len(text)>100):
                    record['metadata']={'source':'none','raw':{'parameters':text}} if text else {'source':'none','raw':{}}
                    self.w.catalog.put('image',record,'album'); before=copy.deepcopy(record)
                    self.w.resize(1440,900)
                    self.g.select(item); self.g.split.moveSplitter(self.g.split.width()-336-self.g.split.handleWidth(),1); self.settle()
                    self.assertEqual(self.g.path.text(),'連結原圖')
                    self.assertEqual(self.g.metadata_hint.text(),'含內嵌資料' if text else '無內嵌資料')
                    self.assertLessEqual(self.g.detail_card.height()-self.g.metadata_hint.mapTo(self.g.detail_card,QPoint(0,self.g.metadata_hint.height())).y(),24)
                    if size==11:
                        self.assertLess(self.g.detail_card.height(),self.g.detail_scroll.viewport().height()-60)
                        # The transparent area belongs to the surrounding workspace,
                        # rather than stretching the bordered detail card.
                        from prompt_calculus_studio.theme import visual_tokens
                        point=self.g.detail_scroll.viewport().mapTo(self.w,QPoint(60,self.g.detail_card.geometry().bottom()+30))
                        pixel=self.w.grab().toImage().pixelColor(point)
                        self.assertEqual(pixel.name(),visual_tokens(self.w.state['settings'])['base'])
                    self.assertEqual(self.g.detail_scroll.horizontalScrollBar().maximum(),0)
                    self.w.resize(800,480); self.settle()
                    self.assertEqual(self.g.detail_card.geometry().top(),12+self.g.detail_batch.height())
                    self.assertEqual(self.g.detail_card.geometry().left(),12)
                    self.assertGreaterEqual(self.g.detail_card.width(),300)
                    self.g.detail_scroll.ensureWidgetVisible(self.g.detail_export_selection); self.settle()
                    bounds=QRect(self.g.detail_export_selection.mapTo(self.g.detail_scroll.viewport(),QPoint()),self.g.detail_export_selection.size())
                    self.assertTrue(self.g.detail_scroll.viewport().rect().contains(bounds))
                    self.assertEqual(self.g.detail_scroll.horizontalScrollBar().maximum(),0)
                    self.assertFalse(self.g.restore_button.isEnabled())
                    self.assertEqual(self.w.catalog.get(record['id']),before)

    def test_image_data_dialog_preserves_notes_manual_and_complete_raw_data(self):
        from prompt_calculus_studio.media_gallery import ImageMetadataDialog
        self.add_images(1); item=self.g.images.item(0); self.g.images.setCurrentRow(0)
        record=self.w.catalog.get(item.data(Qt.ItemDataRole.UserRole)['id'])
        record['metadata']={'source':'none','raw':{'parameters':'Long prompt\n'*1400+'LAST-PARAMETER'}}
        record['notes']='保留個人備註\n第二行'
        record['manual']={'source':'manual_recommendation','workspace':'手動工作區','prompt':'',
                          'parameters':{'seed':18446744073709551615},'note':'保留原始手動說明','extra':['additional saved value']}
        self.w.catalog.put('image',record,'album'); before=copy.deepcopy(record)
        self.g.select(item); self.settle()
        self.assertIn('備註',self.g.metadata_hint.text()); self.assertIn('手動附註',self.g.metadata_hint.text())
        for size in (11,18):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme(); self.w.resize(640,480); self.settle()
            dialog=ImageMetadataDialog(self.g,self.g.record); dialog.show(); self.settle()
            try:
                self.assertEqual(dialog.tabs.count(),2)
                summary=dialog.summary.toPlainText()
                for value in ('保留個人備註\n第二行','手動工作區','18446744073709551615','保留原始手動說明','additional saved value','提示詞：'):
                    self.assertIn(value,summary)
                self.assertTrue(dialog.summary.isReadOnly()); self.assertTrue(dialog.raw.isReadOnly())
                self.assertEqual(json.loads(dialog.raw.toPlainText()),record['metadata']['raw'])
                self.assertGreater(dialog.summary.verticalScrollBar().maximum(),0)
                for editor in (dialog.summary,dialog.raw):
                    dialog.tabs.setCurrentWidget(editor); self.settle(); dialog.copy_button.click()
                    self.assertEqual(APP.clipboard().text(),editor.toPlainText())
                    editor.selectAll(); self.assertEqual(editor.textCursor().selectedText().replace('\u2029','\n'),editor.toPlainText())
                    self.assertEqual(editor.horizontalScrollBar().maximum(),0)
                self.assertLessEqual(dialog.width(),640); self.assertLessEqual(dialog.height(),480)
                self.assertGreaterEqual(dialog.summary.viewport().height(),200)
                self.assertTrue(dialog.rect().contains(QRect(dialog.copy_button.mapTo(dialog,QPoint()),dialog.copy_button.size())))
            finally:dialog.reject(); dialog.deleteLater(); APP.processEvents()
        self.assertEqual(self.w.catalog.get(record['id']),before)
        with patch('prompt_calculus_studio.media_gallery.ImageMetadataDialog.exec',return_value=0) as opened:
            self.g.metadata_button.click(); opened.assert_called_once()

    def test_image_data_typography_keeps_plain_content_and_inherited_size(self):
        from PySide6.QtGui import QFont
        from prompt_calculus_studio.media_gallery import ImageMetadataDialog
        from prompt_calculus_studio.metadata_view import readable_metadata
        from prompt_calculus_studio.theme import visual_tokens
        self.add_images(1)
        record=self.w.catalog.get(self.g.images.item(0).data(Qt.ItemDataRole.UserRole)['id'])
        record['metadata']={'source':'none','nodes':[
            {'type':'CheckpointLoaderSimple','node':'4','values':{'ckpt_name':'example.safetensors'}},
            {'type':'CLIPTextEncode','node':'6','values':{'text':'保留完整提示詞'}},
            {'type':'KSampler','node':'3','values':{'seed':42}}],
            'raw':{'parameters':'保留原始內容\nSeed: 42'}}
        record['manual']={'workspace':'保留工作區'}
        before=copy.deepcopy(record)
        for size in (11,18):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme(); self.w.resize(800,480); self.settle()
            dialog=ImageMetadataDialog(self.g,record); dialog.show(); self.settle()
            try:
                self.assertEqual(dialog.summary.toPlainText(),readable_metadata(record).rstrip()+'\n\n手動附上的工作區資料\n工作區：保留工作區')
                headings=('載入模型 · 節點 4','提示詞 · 節點 6','採樣器 · 節點 3','圖片備註','手動附上的工作區資料')
                blocks={}; block=dialog.summary.document().firstBlock()
                while block.isValid():
                    blocks[block.text()]=block; block=block.next()
                for heading in headings:
                    formats=blocks[heading].layout().formats()
                    self.assertTrue(any(span.start==0 and span.format.fontWeight()==QFont.Weight.Bold for span in formats),heading)
                    self.assertFalse(blocks[heading].previous().text())
                label=blocks['Seed：42'].layout().formats()
                self.assertEqual(label[0].format.foreground().color().name(),visual_tokens(self.w.state['settings'])['secondary'])
                self.assertEqual(label[0].length,len('Seed：'))
                dialog.tabs.setCurrentWidget(dialog.raw); self.settle()
                self.assertEqual(json.loads(dialog.raw.toPlainText()),record['metadata']['raw'])
                self.assertEqual(dialog.raw.font().pixelSize(),dialog.summary.font().pixelSize())
                self.assertEqual(dialog.raw.font().pointSizeF(),dialog.summary.font().pointSizeF())
                self.assertIn('Consolas',dialog.raw.font().families())
                metrics=dialog.raw.fontMetrics()
                self.assertAlmostEqual(metrics.horizontalAdvance('iiii'),metrics.horizontalAdvance('WWWW'),delta=1)
                self.assertEqual(dialog.raw.horizontalScrollBar().maximum(),0)
            finally:dialog.reject(); dialog.deleteLater(); APP.processEvents()
        self.assertEqual(record,before)

    def test_image_tiles_hide_captions_but_keep_names_and_viewing_badges(self):
        from PySide6.QtCore import QEvent
        from prompt_calculus_studio.theme import visual_tokens
        self.add_images(1); self.g.images.setCurrentRow(0); self.settle()
        item=self.g.images.item(0)
        delegate=self.g.images.itemDelegate(); index=self.g.images.model().index(0,0)
        current=self.g.record; colors=visual_tokens(self.w.state['settings'])
        def paint(width,viewing):
            self.g.record=current if viewing else None
            result=QImage(width,180,QImage.Format.Format_RGB32); result.fill(Qt.GlobalColor.black)
            option=QStyleOptionViewItem(); option.initFrom(self.g.images); option.rect=QRect(0,0,width,180)
            painter=QPainter(result); delegate.paint(painter,option,index); painter.end()
            return result
        self.g.set_view_mode('images'); self.settle()
        for width in (150,64):
            active=paint(width,True); normal=paint(width,False)
            # Changing only the caption cannot change any grid pixels. The
            # record name remains available to assistive readers and tooltips.
            item.setText('A completely different filename.png')
            self.assertEqual(paint(width,True),active); self.assertEqual(paint(width,False),normal)
            item.setText(current['name'])
            self.assertEqual(item.data(Qt.ItemDataRole.AccessibleTextRole),current['name'])
            self.assertEqual(item.toolTip(),current['name'])
            self.assertEqual(self.g.detail_name.text(),current['name'])
            self.assertTrue(any(active.pixelColor(x,y).name()==colors['accent'] for y in range(8,120) for x in range(8,width-8)))
        self.g.record=current
        event=QHelpEvent(QEvent.Type.ToolTip,QPoint(15,15),QPoint(15,15))
        with patch('prompt_calculus_studio.media_gallery.QToolTip.showText') as tooltip:
            self.assertTrue(delegate.helpEvent(event,self.g.images,QStyleOptionViewItem(),index))
            self.assertIn(current['name'],tooltip.call_args.args[1])
        self.g.set_view_mode('list'); self.settle()
        active=paint(300,True); normal=paint(300,False)
        self.assertNotEqual(active,normal)
        item.setText('A completely different filename.png'); self.assertNotEqual(paint(300,False),normal)
        item.setText(current['name'])
        self.g.record=current

    def test_detail_card_switches_image_and_actions_without_overlap_or_lost_controls(self):
        from PySide6.QtWidgets import QBoxLayout
        self.add_images(1); self.g.images.setCurrentRow(0); self.settle()
        for size in (11,18):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme()
            for width in (640,800,1440):
                with self.subTest(size=size,width=width):
                    self.w.resize(width,640); self.settle()
                    self.assertEqual(self.w.width(),width)
                    card=self.g.detail_card; preview=self.g.preview; actions=self.g.detail_actions
                    expected_horizontal=width==800 and size==11
                    self.assertEqual(self.g.detail_body.direction()==QBoxLayout.Direction.LeftToRight,expected_horizontal)
                    if expected_horizontal:self.assertEqual(actions.x()-preview.geometry().right()-1,24)
                    else:self.assertGreater(actions.y(),preview.geometry().bottom())
                    self.assertFalse(actions.geometry().intersects(preview.geometry()))
                    self.assertTrue(card.rect().contains(preview.geometry())); self.assertTrue(card.rect().contains(actions.geometry()))
                    self.assertEqual(self.g.detail_scroll.horizontalScrollBar().maximum(),0)
                    self.assertGreater(self.g.path.width(),0)
                    self.assertGreater(self.g.metadata_hint.y(),self.g.metadata_button.geometry().bottom())
                    self.assertLessEqual(self.g.metadata_hint.y()-self.g.metadata_button.geometry().bottom(),6)
                    for action in (self.g.img2img_button,self.g.open_original_button,self.g.detail_more,self.g.metadata_button):
                        self.g.detail_scroll.ensureWidgetVisible(action); self.settle()
                        bounds=QRect(action.mapTo(self.g.detail_scroll.viewport(),QPoint()),action.size())
                        self.assertTrue(self.g.detail_scroll.viewport().rect().contains(bounds),action.text())

    def test_filename_columns_are_row_major_and_keyboard_follows_visible_rows(self):
        self.add_images(60); self.g.set_view_mode('list'); self.g.images.setCurrentRow(0)
        original=[self.g.images.item(i).data(Qt.ItemDataRole.UserRole)['id'] for i in range(60)]
        for size,expected_columns in ((11,(1,2,3)),(18,(1,1,2))):
            self.w.state['settings']['ui_size']=size; self.w.apply_theme()
            for width,columns in zip((1280,1440,1920),expected_columns):
                with self.subTest(size=size,width=width):
                    self.w.resize(width,640); self.settle(); self.g.images.setCurrentRow(0); self.settle()
                    rectangles=[self.g.images.visualItemRect(self.g.images.item(i)) for i in range(60)]
                    first=rectangles[0]
                    self.assertEqual(sum(rect.top()==first.top() for rect in rectangles),columns)
                    self.assertEqual(self.g.list_columns,columns)
                    self.assertEqual(first.height(),38 if size==11 else 48)
                    for i,rect in enumerate(rectangles):
                        self.assertEqual(rect.top(),first.top()+(i//columns)*first.height())
                        self.assertGreaterEqual(rect.left(),0); self.assertLessEqual(rect.right(),self.g.images.viewport().width())
                    self.g.images.setFocus()
                    for key,expected in ((Qt.Key.Key_Right,1 if columns>1 else 0),(Qt.Key.Key_Down,columns+1 if columns>1 else 1),(Qt.Key.Key_Left,columns if columns>1 else 1),(Qt.Key.Key_Up,0)):
                        QTest.keyClick(self.g.images,key); self.assertEqual(self.g.images.currentRow(),expected)
                    self.assertEqual([self.g.images.item(i).data(Qt.ItemDataRole.UserRole)['id'] for i in range(60)],original)

    def test_column_changes_keep_ctrl_selection_current_and_scrolled_item(self):
        self.add_images(60); self.g.set_view_mode('list'); self.g.images.setCurrentRow(0)
        self.w.resize(1440,640); self.settle()
        for index,modifier in ((0,Qt.KeyboardModifier.NoModifier),(5,Qt.KeyboardModifier.ControlModifier)):
            rect=self.g.images.visualItemRect(self.g.images.item(index))
            QTest.mouseClick(self.g.images.viewport(),Qt.MouseButton.LeftButton,modifier,rect.center()); self.settle()
        self.assertEqual({self.g.images.row(item) for item in self.g.images.selectedItems()},{0,5})
        current=self.g.images.currentItem(); record=copy.deepcopy(self.g.record)
        self.g.images.verticalScrollBar().setValue(240); self.settle()
        for width in (1920,1280,1440):
            anchor=self.g.gallery_anchor(); self.w.resize(width,640); self.settle()
            anchored=next(self.g.images.item(i) for i in range(60) if self.g.images.item(i).data(Qt.ItemDataRole.UserRole)['id']==anchor[0])
            self.assertEqual(self.g.images.visualItemRect(anchored).top(),anchor[1])
            self.assertIs(self.g.images.currentItem(),current)
            self.assertEqual({self.g.images.row(item) for item in self.g.images.selectedItems()},{0,5})
            self.assertEqual(self.g.record,record); self.assertEqual(self.w.catalog.get(record['id']),record)
        self.w.resize(800,480); self.settle()
        self.assertTrue(self.g.detail_export_selection.isVisible())
        with patch.object(self.w,'open_export') as exported:
            self.g.detail_export_selection.click(); self.assertEqual(len(exported.call_args.args[0]),2)

    def test_preview_resizes_cached_image_and_keeps_missing_source_hint(self):
        from prompt_calculus_studio.widgets import set_record_preview
        self.add_images(1); item=self.g.images.item(0); self.g.images.setCurrentRow(0); self.settle()
        pixmap=self.g.preview.pixmap()
        self.assertAlmostEqual(pixmap.width()/pixmap.height(),96/144,delta=.01)
        with patch('prompt_calculus_studio.widgets.record_pixmap',side_effect=AssertionError('Resizing must use cached pixels')):
            for width in (1280,1920,800):self.w.resize(width,640); self.settle()
        missing=dict(self.g.record,path=str(Path(self.temp.name)/'missing.png'),thumb='')
        set_record_preview(self.g.preview,self.w.store,missing,512)
        self.assertIn('無法讀取',self.g.preview.text())
        for width in (1440,1280,800):
            self.w.resize(width,640); self.settle(); self.assertIn('無法讀取',self.g.preview.text())
        set_record_preview(self.g.preview,self.w.store,self.g.record,512)
        self.assertFalse(self.g.preview.text()); self.assertFalse(self.g.preview.pixmap().isNull())

    def test_zoom_and_mode_switch_keep_selection_and_visible_anchor(self):
        self.add_images(60); self.w.resize(1100,640); self.settle()
        self.g.thumbnail_size.setValue(12); self.settle()
        self.g.images.blockSignals(True); self.g.images.setCurrentRow(24); self.g.images.item(25).setSelected(True); self.g.images.blockSignals(False)
        self.g.select(self.g.images.currentItem(),open_detail=False); self.settle()
        selected=[i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()]
        before=copy.deepcopy(self.g.record); self.g.images.verticalScrollBar().setValue(150); self.settle()
        anchor=self.g.gallery_anchor(); self.g.set_view_mode('list'); self.settle()
        self.g.set_view_mode('images'); self.settle()
        self.assertEqual(self.g.gallery_anchor(),anchor)
        self.g.thumbnail_size.setValue(11); self.settle()
        anchored=next(self.g.images.item(i) for i in range(self.g.images.count()) if self.g.images.item(i).data(Qt.ItemDataRole.UserRole)['id']==anchor[0])
        self.assertEqual(self.g.images.visualItemRect(anchored).top(),anchor[1])
        self.assertEqual([i.data(Qt.ItemDataRole.UserRole)['id'] for i in self.g.images.selectedItems()],selected)
        self.assertEqual(self.g.record,before); self.assertEqual(self.w.catalog.get(before['id']),before)


if __name__=='__main__':unittest.main()
