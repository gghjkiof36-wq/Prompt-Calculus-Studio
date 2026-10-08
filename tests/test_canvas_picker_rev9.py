"""Real Qt local-module picker events with isolated data and no services."""
import copy
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QPoint, QPointF, QSize, QEvent, QTimer
from PySide6.QtGui import QFontDatabase, QMouseEvent
from PySide6.QtWidgets import QApplication, QPlainTextEdit, QLineEdit, QPushButton, QScrollArea
from PySide6.QtTest import QTest
from prompt_calculus_studio import materials
from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.core import uid, Storage
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])
if APP.platformName() == 'offscreen':
    for font in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + font)


class PickerRev9Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.errors = []
        self.hook = patch.object(sys, 'excepthook', lambda *error: self.errors.append(error)); self.hook.start()
        self.env = patch.dict(os.environ, {'PROMPT_STUDIO_V08': '1', 'PROMPT_STUDIO_V081': '1'}); self.env.start()
        self.network = patch('prompt_calculus_studio.comfy_client.ComfyClient.request',
                             side_effect=AssertionError('Picker tests must not request ComfyUI'))
        self.requests = self.network.start()
        self.w = Window(self.temp.name)
        self.w.state['settings'].update(online=False, material='solid', reduce_motion=True)
        self.w.resize(1280,860); self.w.apply_theme(); self.w.show(); self.w.enter_canvas(); APP.processEvents()
        self.c = self.w.canvas
        self.a = self.c.add_canvas(QPointF(400,100)); self.b = self.c.add_canvas(QPointF(1500,100))
        self.w.state['multi_output']['canvases'][self.a]['name'] = '目的文字畫布 A'
        self.cat = materials.add_category(self.w.state, '服裝')
        self.empty_cat = materials.add_category(self.w.state, '空分類')
        self.ident = materials.save(self.w.state, dict(id=uid(), name='紅色洋裝女孩', prompt='1girl, red dress',
                                                      aliases=['rev9唯一別名'], module=self.cat))
        self.unclassified = materials.save(self.w.state, dict(id=uid(), name='未分類片段', prompt='plain text', module=None))
        self.asset_api = Mock(wraps=self.c.insert_asset_with_destination)
        self.tag_api = Mock(wraps=self.c.add_tag_with_destination)
        self.actual_new = self.c.add_new_with_destination
        self.c.insert_asset_with_destination = self.asset_api
        self.c.add_tag_with_destination = self.tag_api
        self.c.add_new_with_destination = Mock(return_value=False)
        self.w.open_new_material = Mock()
        self.pending = []
        self.pool = patch.object(self.w.completion.pool,'start',self.pending.append); self.pool.start()
        self.p = None
        self.open()

    def tearDown(self):
        if self.p: self.p.reject()
        self.w.completion.timer.stop(); self.w.completion.tasks.clear(); self.w.completion.active = None
        self.pool.stop(); self.w.close(); APP.processEvents(); APP.clipboard().clear()
        self.network.stop(); self.env.stop(); self.hook.stop(); self.temp.cleanup()
        self.requests.assert_not_called()
        self.assertEqual(self.errors, [], 'Qt callback exceptions were captured')

    def open(self, **kwargs):
        if self.p: self.p.reject()
        args = dict(destination_id=self.a); args.update(kwargs)
        self.p = CanvasPalette(self.c,QPointF(450,250),**args)
        self.p.show(); self.p.activateWindow(); QTest.qWait(15)
        return self.p

    def select(self,ident):
        self.p.restore_selection(self.p.modules,ident); APP.processEvents()

    def card(self):
        self.select(self.cat); return self.p.assets.item(0)

    def click_card(self):
        item = self.card(); self.p.assets.scrollToItem(item)
        QTest.mouseClick(self.p.assets.viewport(),Qt.MouseButton.LeftButton,pos=self.p.assets.visualItemRect(item).center())
        APP.processEvents()

    def test_browse_category_counts_empty_and_uncategorized_do_not_mutate(self):
        before = self.c.history_state()
        self.select(self.cat)
        self.assertEqual(self.p.assets.count(),1); self.assertIn('服裝 · 1 個模組',self.p.assets_heading.text())
        self.select(self.empty_cat)
        self.assertTrue(self.p.empty.isVisible()); self.assertEqual(self.p.empty.text(),'此分類尚無文字模組')
        self.assertTrue(self.p.assets.isHidden()); self.assertTrue(self.p.new_button.isVisible())
        self.select(materials.item(self.w.state,self.unclassified)['module'])
        self.assertIn(self.unclassified,[self.p.assets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.p.assets.count())])
        self.assertNotIn('@functions',[self.p.modules.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.p.modules.count())])
        self.assertEqual(self.c.history_state(),before); self.assertEqual(self.asset_api.call_count,0)

    def test_only_category_text_choices_and_blank_action_exist(self):
        self.assertEqual(self.p.findChildren(QPlainTextEdit),[])
        self.assertEqual(self.p.findChildren(QLineEdit),[])
        self.assertEqual(self.p.findChildren(QScrollArea),[])
        self.assertEqual(set(self.p.findChildren(QPushButton)),{self.p.close_button,self.p.new_button})
        for removed in ('query','scope','lookup_toggle','lookup_pane','lookup_status','suggestions',
                        'submit_button','search_all','category_manage','material_button'):
            self.assertFalse(hasattr(self.p,removed),removed)
        self.assertEqual(self.p.modules.contextMenuPolicy(),Qt.ContextMenuPolicy.DefaultContextMenu)
        self.assertEqual(self.p.assets.contextMenuPolicy(),Qt.ContextMenuPolicy.DefaultContextMenu)
        self.assertIs(self.p.new_button.parent(),self.p.surface)
        self.assertIsNot(self.p.content,self.p.new_button.parent())

    def test_open_browse_focus_and_close_never_schedule_completion(self):
        self.w.state['settings']['online']=True
        with patch.object(self.w.completion,'schedule') as schedule, \
             patch.object(self.w.completion,'lookup') as lookup, \
             patch.object(self.w.completion,'cancel') as cancel:
            self.open(); self.select(self.cat)
            QTest.keyClick(self.p.modules,Qt.Key.Key_Right)
            QTest.keyClick(self.p.assets,Qt.Key.Key_Tab)
            self.select(self.empty_cat); self.p.reject(); QTest.qWait(30)
            schedule.assert_not_called(); lookup.assert_not_called(); cancel.assert_not_called()
        self.assertEqual(self.pending,[]); self.tag_api.assert_not_called()

    def test_keyboard_focus_moves_between_visible_tasks_without_inserting(self):
        self.select(self.cat); self.p.modules.setFocus()
        QTest.keyClick(self.p.modules,Qt.Key.Key_Right)
        self.assertTrue(self.p.assets.hasFocus()); self.assertEqual(self.p.assets.currentRow(),0)
        QTest.keyClick(self.p.assets,Qt.Key.Key_Left); self.assertTrue(self.p.modules.hasFocus())
        QTest.keyClick(self.p.modules,Qt.Key.Key_Tab); self.assertTrue(self.p.assets.hasFocus())
        QTest.keyClick(self.p.assets,Qt.Key.Key_Tab); self.assertTrue(self.p.new_button.hasFocus())
        QTest.keyClick(self.p.new_button,Qt.Key.Key_Tab); self.assertTrue(self.p.close_button.hasFocus())
        QTest.keyClick(self.p.close_button,Qt.Key.Key_Tab); self.assertTrue(self.p.modules.hasFocus())
        QTest.keyClick(self.p.modules,Qt.Key.Key_Backtab); self.assertTrue(self.p.close_button.hasFocus())
        self.select(self.empty_cat); self.p.modules.setFocus()
        QTest.keyClick(self.p.modules,Qt.Key.Key_Tab); self.assertTrue(self.p.new_button.hasFocus())
        self.assertEqual(self.asset_api.call_count,0); self.c.add_new_with_destination.assert_not_called()

    def test_click_copies_once_preserves_source_other_destination_and_undo_reopen(self):
        source = copy.deepcopy(materials.item(self.w.state,self.ident)); before=self.c.history_state()
        self.c.insertion_canvas=self.b  # Invocation destination remains A.
        self.click_card()
        self.assertEqual(self.asset_api.call_count,1); self.assertTrue(self.p.inserted); self.assertFalse(self.p.isVisible())
        after=self.c.history_state(); added=set(self.w.state['uses'])-set(before.get('uses',{}))
        self.assertEqual(len(added),1); key=added.pop()
        from prompt_calculus_studio import multi_output
        self.assertEqual(multi_output.owner(self.w.state,key),self.a)
        self.assertEqual(self.w.state['text_positions'][key],[450.,250.])
        self.assertEqual({k:v for k,v in materials.item(self.w.state,self.ident).items() if k!='last_used'},
                         {k:v for k,v in source.items() if k!='last_used'})
        self.assertEqual(self.w.state['uses'][key]['source_id'],source['id'])
        self.assertEqual(self.w.state['uses'][key]['source_revision'],materials.fingerprint(source))
        self.assertEqual(self.w.state['multi_output']['canvases'][self.b],before['multi_output']['canvases'][self.b])
        self.c.undo(); self.assertEqual(self.c.history_state(),before)
        self.c.redo(); self.assertEqual(self.c.history_state(),after)
        self.w.persist(); store=Storage(self.temp.name)
        try: self.assertEqual(multi_output.owner(store.load(),key),self.a)
        finally: store.close()

    def test_enter_selected_card_does_not_double_activate(self):
        self.card(); self.p.assets.setCurrentRow(0); self.p.assets.setFocus()
        QTest.keyClick(self.p.assets,Qt.Key.Key_Return)
        self.p.assets.itemActivated.emit(self.p.assets.item(0)); self.p.insert()
        self.assertEqual(self.asset_api.call_count,1); self.assertTrue(self.p.inserted)

    def test_text_only_picker_does_not_read_preview_or_reserve_image_space(self):
        self.card(); APP.processEvents()
        before_height=self.p.assets.visualItemRect(self.p.assets.item(0)).height()
        source=materials.item(self.w.state,self.ident)
        image=Path(self.temp.name)/'picker-must-not-read.png'; image.write_bytes(b'preserved-image-bytes')
        source['preview']=str(image)
        before=copy.deepcopy(source)
        with patch('prompt_calculus_studio.widgets.preview_path',side_effect=AssertionError('Picker must not read images')) as read:
            self.p.refresh(); APP.processEvents(); self.p.assets.viewport().grab()
            read.assert_not_called()
        item=self.p.assets.item(0)
        self.assertTrue(item.icon().isNull())
        self.assertEqual(self.p.assets.visualItemRect(item).height(),before_height)
        self.assertEqual(materials.item(self.w.state,self.ident),before)
        self.assertIn(before['name'],item.text()); self.assertIn(before['prompt'],item.text())
        self.click_card()
        self.assertEqual(materials.item(self.w.state,self.ident)['preview'],str(image))
        self.assertEqual(image.read_bytes(),b'preserved-image-bytes')

    def test_failed_insert_retains_selection_then_explicit_retry_succeeds(self):
        self.asset_api.side_effect=[False,True]
        self.click_card()
        self.assertTrue(self.p.isVisible()); self.assertFalse(self.p.inserted)
        self.assertEqual(self.p.selected_id(self.p.assets),self.ident); self.assertTrue(self.p.status.isVisible())
        self.p.assets.itemActivated.emit(self.p.assets.item(0))
        self.assertEqual(self.asset_api.call_count,1)
        QTest.keyClick(self.p.assets,Qt.Key.Key_Return)
        self.assertEqual(self.asset_api.call_count,2); self.assertFalse(self.p.isVisible())

    def test_unselected_deleted_and_absent_destination_cannot_insert(self):
        self.open(destination_id=None,choose_destination=True)
        self.assertTrue(self.p.destination.isVisible()); self.assertIsNone(self.p.destination_id)
        self.p.insert(); self.assertEqual(self.asset_api.call_count,0)
        self.p.destination.setCurrentIndex(self.p.destination.findData(self.b))
        self.assertEqual(self.p.destination_id,self.b); self.assertTrue(self.p.assets.isEnabled())
        remaining={key:value for key,value in self.p.canvases().items() if key!=self.b}
        with patch.object(self.p,'canvases',return_value=remaining):
            self.p.insert(); self.assertEqual(self.asset_api.call_count,0); self.assertTrue(self.p.isVisible())
        with patch.object(CanvasPalette,'canvases',return_value={}):
            self.open(destination_id=None,choose_destination=True)
            self.assertIn('尚無文字畫布',self.p.destination_label.text()); self.assertFalse(self.p.new_button.isEnabled())

    def test_shared_category_and_workspace_identity_protect_invocation(self):
        self.select(self.cat); self.assertEqual(self.w._material_browser_category,self.cat)
        self.open(); self.assertEqual(self.p.get_browser_category(),self.cat)
        original_modules=copy.deepcopy(self.w.state['modules']); original_items=copy.deepcopy(self.w.state['items'])
        try:
            unclassified={m['id'] for m in original_modules if m['name']=='未分類'}
            self.w.state['modules']=[m for m in original_modules if m['id'] not in unclassified]
            self.w.state['items']=[item for item in original_items if item['module'] not in unclassified]
            before=copy.deepcopy((self.w.state['modules'],self.w.state['items']))
            self.p.populate_categories(); self.p.set_browser_category('@uncategorized')
            self.assertEqual(self.p.get_browser_category(),'@uncategorized')
            self.assertEqual(self.w._material_browser_category,'@uncategorized')
            self.assertEqual(self.p.assets.count(),0)
            self.assertEqual((self.w.state['modules'],self.w.state['items']),before)
            actual=materials.add_category(self.w.state,'未分類')
            self.p.populate_categories()
            self.assertEqual(self.p.get_browser_category(),actual)
            self.assertEqual(self.w._material_browser_category,actual)
            self.p.set_browser_category(None); self.assertIsNone(self.p.get_browser_category())
            self.p.set_browser_category('@uncategorized'); self.assertEqual(self.p.get_browser_category(),actual)
        finally:
            self.w.state['modules']=original_modules; self.w.state['items']=original_items
            self.p.populate_categories(); self.p.set_browser_category(self.cat)
        before=self.w.state['workspace']
        try:
            self.w.state['workspace']='changed'; self.p.insert()
            self.assertEqual(self.asset_api.call_count,0); self.assertIn('工作區',self.p.status.text())
        finally: self.w.state['workspace']=before

    def test_blank_dialog_cancel_stays_and_preserves_data_and_destination(self):
        self.c.add_new_with_destination=self.actual_new
        before=copy.deepcopy(self.w.state); shown=[]
        def cancel_blank():
            dialog=self.c.active_node_dialog
            shown.append(dialog.windowTitle())
            dialog.name.setText('取消不得新增'); dialog.prompt.setPlainText('temporary edits')
            dialog.reject()
        QTimer.singleShot(30,cancel_blank)
        QTest.mouseClick(self.p.new_button,Qt.MouseButton.LeftButton)
        self.assertEqual(shown,['新增模組'])
        self.assertTrue(self.p.isVisible()); self.assertFalse(self.p.inserted)
        self.assertEqual(self.p.destination_id,self.a)
        self.assertEqual(self.w.state,before); self.assertEqual(self.asset_api.call_count,0)

    def test_backdrop_close_blocks_remaining_double_click_at_same_canvas_location(self):
        viewport=self.c.view.viewport(); global_pos=self.p.mapToGlobal(QPoint(2,2)); local=viewport.mapFromGlobal(global_pos)
        before=self.c.history_state()
        QTest.mouseClick(self.p,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.assertFalse(self.p.isVisible())
        with patch.object(self.c,'function_palette') as opened:
            event=QMouseEvent(QEvent.Type.MouseButtonDblClick,QPointF(local),QPointF(global_pos),
                              Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            APP.sendEvent(viewport,event); APP.processEvents()
            opened.assert_not_called()
        self.assertEqual(self.c.history_state(),before)

    def test_layout_legacy_settings_and_narrow_footer_stays_visible(self):
        self.p.sheet_size=QSize(980,680); self.p.split.setSizes([210,600]); self.p.save_layout()
        saved=copy.deepcopy(self.w.state['settings']['canvas_palette']); self.assertEqual(len(saved['columns']),3)
        self.open(); self.assertEqual(self.p.sheet_size,QSize(*saved['size']))
        for i in range(40): materials.add_category(self.w.state,'分類 '+str(i))
        for i in range(60):
            materials.save(self.w.state,dict(id=uid(),name='文字模組 '+str(i),prompt='long summary '*8,module=self.cat))
        self.p.populate_categories(); self.select(self.cat)
        self.w.state['settings']['ui_size']=18; self.w.apply_theme(preserve_layout=True)
        self.w.resize(640,480); self.p.fit_window(); APP.processEvents()
        self.assertTrue(self.p.rect().contains(self.p.surface.geometry())); self.assertTrue(self.p.narrow_categories.isVisible())
        self.assertTrue(self.p.category_pane.isHidden())
        for widget in (self.p.title,self.p.destination_label,self.p.close_button,self.p.category,self.p.new_button):
            for corner in (widget.rect().topLeft(),widget.rect().bottomRight()):
                self.assertTrue(self.p.surface.rect().contains(widget.mapTo(self.p.surface,corner)),widget.objectName())
        before=self.p.new_button.mapTo(self.p.surface,QPoint())
        self.assertGreater(self.p.assets.verticalScrollBar().maximum(),0)
        self.p.assets.verticalScrollBar().setValue(self.p.assets.verticalScrollBar().maximum()); APP.processEvents()
        self.assertEqual(self.p.new_button.mapTo(self.p.surface,QPoint()),before)
        self.assertTrue(self.p.new_button.isVisible())
        self.p.category.setFocus(); QTest.keyClick(self.p.category,Qt.Key.Key_Tab)
        self.assertTrue(self.p.assets.hasFocus())
        QTest.keyClick(self.p.assets,Qt.Key.Key_Tab); self.assertTrue(self.p.new_button.hasFocus())
        for widget in (self.p.category,self.p.new_button):
            self.assertGreaterEqual(widget.height(),widget.fontMetrics().height()+16)
        self.p.new_button.setFocus(); output=os.environ.get('PCS_PICKER_SHOTS')
        if output:
            Path(output).mkdir(parents=True,exist_ok=True)
            self.p.grab().save(str(Path(output)/'picker-640-480-18pt.png'))
        self.open(destination_id=None,choose_destination=True)
        self.assertTrue(self.p.destination.isVisible()); self.assertFalse(self.p.new_button.isEnabled())
        for widget in (self.p.destination,self.p.category,self.p.new_button):
            self.assertTrue(self.p.surface.rect().contains(widget.mapTo(self.p.surface,widget.rect().bottomRight())))
        self.p.destination.setFocus(); QTest.keyClick(self.p.destination,Qt.Key.Key_Tab)
        self.assertTrue(self.p.category.hasFocus())
        self.p.destination.setCurrentIndex(self.p.destination.findData(self.a))
        self.assertTrue(self.p.assets.isEnabled()); self.assertTrue(self.p.new_button.isEnabled())


if __name__=='__main__': unittest.main()
