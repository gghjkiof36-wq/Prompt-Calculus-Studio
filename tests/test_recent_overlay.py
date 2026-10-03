"""Offline interaction checks for the temporary canvas image browser."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch,Mock
from PySide6.QtCore import QPoint,Qt
from PySide6.QtGui import QFontDatabase,QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton
from prompt_studio.window import Window
from prompt_studio.media import import_image

APP=QApplication.instance() or QApplication([])
for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class RecentOverlayTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1'});self.env.start()
        self.network=patch('prompt_studio.comfy_client.ComfyClient.request',side_effect=AssertionError('No network'));self.network.start()
        self.w=Window(self.temp.name);self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1440,900);self.w.apply_theme();self.w.show();self.w.enter_canvas();QTest.qWait(100)
        self.w.display_recovery.stop();self.r=self.w.recent
        image=QImage(180,260,QImage.Format.Format_RGB32);image.fill(Qt.GlobalColor.darkGreen)
        path=Path(self.temp.name)/'生成圖片.png';image.save(str(path))
        base=import_image(path,self.w.store.directory,'')
        for i in range(35):
            record=dict(base,id=f'recent-{i:02}',name=f'{i:02} 生成圖片.png',source={'prompt_id':str(i),'image':{'filename':path.name}},collected={})
            self.w.catalog.put('recent',record)

    def tearDown(self):
        self.w.close();APP.processEvents();self.network.stop();self.env.stop();self.temp.cleanup()

    def test_overlay_outside_escape_preserve_canvas_and_data(self):
        canvas=self.w.canvas
        from prompt_studio.drafts import edit
        key=next(iter(canvas.data()['outputs']));canvas.commit(lambda state:edit(state,'',key));QTest.qWait(500)
        canvas.view.setFocus();canvas.view.resetTransform();canvas.view.scale(.9,.9);canvas.view.centerOn(800,300)
        self.w.persist();self.w.save_timer.stop()
        geometry=canvas.geometry();transform=canvas.view.transform();center=canvas.view.mapToScene(canvas.view.viewport().rect().center())
        state=copy.deepcopy(self.w.state);undo=copy.deepcopy(canvas.undo_stack)
        for close in ('outside','escape','hide'):
            self.w.show_recent_sheet();QTest.qWait(50);sheet=self.w.recent_sheet
            self.assertTrue(sheet.isAncestorOf(self.r));self.assertFalse(self.w.context_rail.isVisible());self.assertIsNone(self.w.context_sidebar_target())
            self.assertEqual(sheet.geometry(),self.w.surface_stack.rect());self.assertGreater(sheet.surface.x(),0)
            self.assertFalse(self.r.detail_scroll.isVisible());self.assertFalse(self.r.images.item(0).icon().isNull())
            self.assertEqual(self.r.images.gridSize().width(),self.r.images.gridSize().height())
            if close=='outside':QTest.mouseClick(sheet,Qt.MouseButton.LeftButton,pos=QPoint(3,3))
            elif close=='escape':QTest.keyClick(self.r.images,Qt.Key.Key_Escape)
            else:sheet.hide()
            QTest.qWait(30);self.assertIsNone(self.w.recent_sheet)
            self.assertEqual(canvas.geometry(),geometry);self.assertEqual(canvas.view.transform(),transform)
            self.assertEqual(canvas.view.mapToScene(canvas.view.viewport().rect().center()),center)
            self.assertEqual(self.w.state,state);self.assertEqual(canvas.undo_stack,undo)
            self.assertGreaterEqual(self.w.tabs.indexOf(self.r),0)
            self.assertIs(APP.focusWidget(),canvas.view)

    def test_missing_thumbnail_and_resize_preserve_record_and_readonly_preview(self):
        record=self.w.catalog.get('recent-01');record.update(path=str(Path(self.temp.name)/'missing.png'),thumb='');self.w.catalog.put('recent',record)
        self.w.open_recent_details(record['id']);QTest.qWait(60)
        self.assertTrue(self.r.images.currentItem().icon().isNull());self.assertTrue(self.r.preview.text())
        self.assertEqual(self.r.images.currentItem().data(Qt.ItemDataRole.AccessibleTextRole),record['name'])
        before=self.w.catalog.get(record['id'])
        with patch.object(self.r.preview,'receive') as receive:
            QTest.keyClick(self.r.preview,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier)
            self.w.resize(800,640);QTest.qWait(60)
            receive.assert_not_called();self.assertTrue(self.r.preview.text());self.assertEqual(self.w.catalog.get(record['id']),before)

    def test_header_icons_share_hit_area_and_keep_refresh_and_close_actions(self):
        for font in (11,18):
            self.w.state['settings']['ui_size']=font;self.w.apply_theme()
            self.w.show_recent_sheet();APP.processEvents()
            refresh,close=self.r.refresh_button,self.r.close_button
            self.assertEqual(refresh.size(),close.size());self.assertEqual(refresh.iconSize(),close.iconSize())
            self.assertFalse(refresh.icon().isNull());self.assertFalse(close.icon().isNull())
            self.assertEqual(close.text(),'')
            with patch.object(self.w.comfy,'connected',True),patch.object(self.w.comfy,'request') as request:
                QTest.mouseClick(refresh,Qt.MouseButton.LeftButton,pos=QPoint(4,4))
                request.assert_called_once()
            QTest.mouseClick(close,Qt.MouseButton.LeftButton,pos=QPoint(close.width()-5,close.height()-5))
            APP.processEvents();self.assertIsNone(self.w.recent_sheet)

    def test_selection_grid_sizes_and_small_detail_actions_remain_reachable(self):
        self.w.show_recent_sheet();QTest.qWait(50);size=self.r.images.gridSize()
        item=self.r.images.item(2);QTest.mouseClick(self.r.images.viewport(),Qt.MouseButton.LeftButton,pos=self.r.images.visualItemRect(item).center());QTest.qWait(50)
        record=copy.deepcopy(self.r.record)
        self.assertTrue(self.r.detail_scroll.isVisible());self.assertEqual(self.r.images.gridSize(),size)
        self.assertEqual(self.r.detail_name.text(),record['name'])
        self.assertEqual(self.r.images.currentItem().toolTip(),record['name'])
        for width,height,font in ((800,640,11),(640,480,18)):
            self.w.resize(width,height);self.w.state['settings']['ui_size']=font;self.w.apply_theme();QTest.qWait(100)
            self.assertTrue(self.r.detail_scroll.isVisible());self.assertFalse(self.r.grid_panel.isVisible())
            self.assertTrue(self.r.back_button.isVisible());self.assertEqual(self.r.detail_scroll.horizontalScrollBar().maximum(),0)
            image_rect=self.r.preview.geometry();actions_rect=self.r.operations.geometry()
            self.assertFalse(image_rect.intersects(actions_rect))
            if height==480:
                self.r.detail_scroll.verticalScrollBar().setValue(0);QTest.qWait(20)
                action_top=self.r.save_button.mapTo(self.r.detail_scroll.viewport(),QPoint()).y()
                self.assertGreaterEqual(action_top,0);self.assertLessEqual(action_top+self.r.save_button.height(),self.r.detail_scroll.viewport().height())
            self.r.detail_scroll.ensureWidgetVisible(self.r.save_button);QTest.qWait(30)
            pos=self.r.save_button.mapTo(self.r.detail_scroll.viewport(),QPoint())
            self.assertGreaterEqual(pos.y(),0);self.assertLessEqual(pos.y()+self.r.save_button.height(),self.r.detail_scroll.viewport().height())
            self.assertEqual(self.r.record,record)
        self.r.hide_details();QTest.qWait(30);self.assertTrue(self.r.grid_panel.isVisible());self.assertFalse(self.r.detail_scroll.isVisible())

    def test_save_is_primary_and_keeps_destination_connection_and_duplicate_guards(self):
        self.w.open_recent_details('recent-02');QTest.qWait(50)
        self.assertEqual(self.r.save_button.objectName(),'Primary')
        self.assertEqual(self.r.save_button.text(),'儲存圖片')
        self.assertFalse(self.r.save_button.isEnabled())
        self.assertFalse(any('圖生圖' in button.text() for button in self.r.findChildren(QPushButton)))
        menus=[]
        with patch('prompt_studio.recent.RoundMenu.open_at',autospec=True,side_effect=lambda menu,_:menus.append(menu)):
            self.r.context(self.r.images.visualItemRect(self.r.images.currentItem()).center())
        self.assertTrue(menus)
        self.assertFalse(any('圖生圖' in action.text() for action in menus[0].actions()))
        self.assertEqual(menus[0].actions()[0].text(),'儲存圖片')
        self.w.catalog.put('album',{'id':'save-album','name':'儲存測試'})
        self.w.state['settings']['recent_destination']='album:save-album';self.r.refresh_destinations();self.r.update_save_button()
        self.assertFalse(self.r.save_button.isEnabled())
        record=copy.deepcopy(self.r.record);request=Mock()
        with patch.object(self.w.comfy,'connected',True),patch.object(self.w.comfy,'request',request),patch.object(self.w,'notice'):
            self.r.update_save_button();self.assertTrue(self.r.save_button.isEnabled())
            destination,album=self.r.destination();self.assertEqual(album,'save-album')
            self.r.save_button.click()
            self.assertEqual(request.call_args.args[:2],('desktop/collect',dict(image=record['source']['image'],prompt_id=record['source']['prompt_id'],destination=destination)))
            self.assertEqual(self.r.save_button.text(),'正在儲存…');self.assertFalse(self.r.save_button.isEnabled())
            self.r.collect();request.assert_called_once()
            request.call_args.args[3]('isolated fixture cancellation')
            self.assertFalse(self.r.collecting);self.assertTrue(self.r.save_button.isEnabled())
            self.assertEqual(self.w.catalog.get(record['id']),record)
            saved=dict(path=record['path'],has_generation=False,has_snapshot=False)
            self.r.record=dict(record,collected={destination:saved});self.r.update_save_button()
            self.assertEqual(self.r.save_button.text(),'✓ 已儲存');self.assertFalse(self.r.save_button.isEnabled())
        self.assertTrue(hasattr(self.w.gallery,'img2img_button'))

    def test_canvas_capsule_and_bidirectional_mode_switch_preserve_drafts(self):
        self.assertTrue(self.w.canvas_workspace_bar.isVisible());self.assertFalse(self.w.context_rail.isVisible())
        self.w.resize(640,480);QTest.qWait(30);self.assertEqual(self.w.canvas_workspace_bar.list_mode.text(),'')
        self.w.resize(1440,900);QTest.qWait(30);self.assertEqual(self.w.canvas_workspace_bar.list_mode.text(),'切換清單')
        state=self.w.state
        from prompt_studio.drafts import edit
        key=next(iter(self.w.canvas.data()['outputs']));self.w.canvas.commit(lambda current:edit(current,'',key));QTest.qWait(40)
        expected=copy.deepcopy(self.w.canvas.data())
        self.w.canvas_workspace_bar.list_mode.click();QTest.qWait(40)
        self.assertFalse(self.w.canvas_mode);self.assertEqual(self.w.state['settings']['interface_mode'],'list')
        self.w.set_interface_mode('canvas');QTest.qWait(50)
        self.assertTrue(self.w.canvas_mode);self.assertEqual(self.w.canvas.data(),expected)
        self.assertEqual(self.w.canvas.data()['outputs'][key]['draft'],'')


if __name__=='__main__':unittest.main()
