"""Actual Qt picker events; isolated data, held lookup transport, no service.

The fixed-destination API is owned by Window/TextCanvas integration. This suite
adapts that API to the existing real commit/material copy transaction on the
isolated picker branch; integrated three-entry checks use the final API itself.
"""
import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from PySide6.QtCore import Qt, QPoint, QPointF, QSize, QEvent
from PySide6.QtGui import QTextCursor, QInputMethodEvent, QFontDatabase, QMouseEvent
from PySide6.QtWidgets import QApplication
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
        self.network = patch('prompt_calculus_studio.comfy_client.ComfyClient.request'); self.network.start()
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
        self.asset_api = Mock(side_effect=self.copy_to_canvas)
        self.tag_api = Mock(side_effect=self.tag_to_canvas)
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
        self.assertEqual(self.errors, [], 'Qt callback exceptions were captured')

    def copy_to_canvas(self, asset, position, destination):
        point = [position.x(),position.y()] if position is not None else None
        return self.c.commit(lambda state: materials.add_to_canvas(state,asset['id'],destination,point))

    def tag_to_canvas(self, text, position, destination):
        from prompt_calculus_studio import composition, multi_output
        key = uid(); value = composition.node(text.strip(),text)
        def add(state):
            state.setdefault('uses',{})[key] = value; state.setdefault('output_order',[]).append(key)
            multi_output.assign(state,key,destination)
            if position is not None: state.setdefault('text_positions',{})[key] = [position.x(),position.y()]
        return self.c.commit(add)

    def open(self, **kwargs):
        if self.p: self.p.reject()
        args = dict(destination_id=self.a); args.update(kwargs)
        self.p = CanvasPalette(self.c,QPointF(450,250),**args)
        self.p.show(); self.p.activateWindow(); QTest.qWait(15)
        return self.p

    def search(self,text):
        self.p.query.setFocus(); self.p.query.setPlainText(text)
        self.p.query.moveCursor(QTextCursor.MoveOperation.End); self.w.completion.timer.stop(); APP.processEvents()

    def select(self,ident):
        self.p.restore_selection(self.p.modules,ident); APP.processEvents()

    def card(self):
        self.select(self.cat); return self.p.assets.item(0)

    def click_card(self):
        item = self.card(); self.p.assets.scrollToItem(item)
        QTest.mouseClick(self.p.assets.viewport(),Qt.MouseButton.LeftButton,pos=self.p.assets.visualItemRect(item).center())
        APP.processEvents()

    def test_browse_category_alias_search_empty_and_uncategorized_do_not_mutate(self):
        before = self.c.history_state()
        self.select(self.cat); self.search('rev9唯一別名')
        self.assertEqual(self.p.assets.count(),1); self.assertIn('服裝',self.p.scope.text())
        self.select(self.empty_cat)
        self.assertTrue(self.p.empty.isVisible()); self.assertTrue(self.p.search_all.isVisible())
        QTest.mouseClick(self.p.search_all,Qt.MouseButton.LeftButton)
        self.assertEqual(self.p.assets.count(),1); self.assertIn('全部分類',self.p.scope.text())
        self.search(''); self.select(materials.item(self.w.state,self.unclassified)['module'])
        self.assertEqual(self.p.selected_id(self.p.assets),None)
        self.assertIn(self.unclassified,[self.p.assets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.p.assets.count())])
        self.assertNotIn('@functions',[self.p.modules.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.p.modules.count())])
        self.assertEqual(self.c.history_state(),before); self.assertEqual(self.asset_api.call_count,0)

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
        self.assertEqual({k:v for k,v in materials.item(self.w.state,self.ident).items() if k!='last_used'},source)
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
        source['preview']='thumbnails/picker-must-not-read.png'
        before=copy.deepcopy(source)
        with patch('prompt_calculus_studio.widgets.preview_path',side_effect=AssertionError('Picker must not read images')) as read:
            self.p.refresh(); APP.processEvents(); self.p.assets.viewport().grab()
            read.assert_not_called()
        item=self.p.assets.item(0)
        self.assertTrue(item.icon().isNull())
        self.assertEqual(self.p.assets.visualItemRect(item).height(),before_height)
        self.assertEqual(materials.item(self.w.state,self.ident),before)
        self.assertIn(before['name'],item.text()); self.assertIn(before['prompt'],item.text())

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

    def test_completion_choice_fills_query_then_explicit_submit_and_ime_priority(self):
        self.search('red'); self.p.toggle_lookup(); self.w.completion.timer.stop()
        rows=[dict(value='red_dress',source='Danbooru',category=0,count=1)]
        self.p.query.display(self.p.query.context(),rows)
        self.p.suggestions.setCurrentRow(0); self.p.suggestions.setFocus()
        QTest.keyClick(self.p.suggestions,Qt.Key.Key_Return)
        self.assertEqual(self.tag_api.call_count,0); self.assertTrue(self.p.isVisible())
        self.assertIn('red dress',self.p.query.toPlainText()); self.assertTrue(self.p.query.hasFocus())
        APP.sendEvent(self.p.query,QInputMethodEvent('輸入',[]))
        for key in (Qt.Key.Key_Return,Qt.Key.Key_Down,Qt.Key.Key_Escape,Qt.Key.Key_Tab): QTest.keyClick(self.p.query,key)
        self.assertEqual(self.tag_api.call_count,0); self.assertTrue(self.p.isVisible())
        APP.sendEvent(self.p.query,QInputMethodEvent('',[])); self.w.completion.timer.stop()
        QTest.keyClick(self.p.query,Qt.Key.Key_Return)
        self.assertEqual(self.tag_api.call_count,1); self.assertFalse(self.p.isVisible())

    def test_lookup_collapse_cancels_request_preserves_cursor_and_rejects_late_result(self):
        self.search('custom'); self.p.toggle_lookup(); self.w.completion.timer.stop()
        self.w.state['settings']['online']=True; service=self.w.completion
        service.next_request=0; service.schedule(self.p.query); service.timer.stop(); service.lookup()
        self.assertEqual(len(self.pending),1); task=self.pending[-1]
        self.assertIn('正在查詢',self.p.lookup_status.text())
        cursor=self.p.query.textCursor(); cursor.setPosition(2); cursor.setPosition(4,QTextCursor.MoveMode.KeepAnchor)
        self.p.query.setTextCursor(cursor); service.timer.stop(); text=self.p.query.toPlainText()
        self.p.toggle_lookup(); service.finished(task.serial,[dict(value='wrong',source='offline fixture',category=0,count=1)],'')
        self.assertEqual(self.p.query.toPlainText(),text)
        self.assertEqual((self.p.query.textCursor().anchor(),self.p.query.textCursor().position()),(2,4))
        self.assertEqual(self.p.suggestions.count(),0); self.assertTrue(self.p.lookup_pane.isHidden())
        self.assertFalse(service.timer.isActive()); self.assertEqual(self.tag_api.call_count,0)

    def test_lookup_failure_empty_and_closed_late_callback_are_visible_or_ignored(self):
        self.search('lookup test'); self.p.toggle_lookup(); self.w.completion.timer.stop()
        service=self.w.completion; self.w.state['settings']['online']=True
        service.next_request=0; service.schedule(self.p.query); service.timer.stop(); service.lookup()
        task=self.pending[-1]; service.finished(task.serial,[],'合成查詢失敗，請重試。')
        self.assertIn('失敗',self.p.lookup_status.text())
        self.p.query.display(self.p.query.context(),[])
        self.assertIn('沒有候選',self.p.lookup_status.text()); self.assertTrue(self.p.suggestions.isHidden())
        service.next_request=0; service.schedule(self.p.query); service.timer.stop(); service.lookup()
        task=self.pending[-1]; self.p.reject(); before=self.p.lookup_status.text()
        service.finished(task.serial,[dict(value='late',source='offline',category=0,count=1)],'')
        self.assertEqual(self.p.lookup_status.text(),before); self.assertEqual(self.tag_api.call_count,0)

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

    def test_blank_cancel_stays_and_reusable_destination_uses_new_entry(self):
        QTest.mouseClick(self.p.new_button,Qt.MouseButton.LeftButton)
        self.c.add_new_with_destination.assert_called_once_with(QPointF(450,250),self.a)
        self.assertTrue(self.p.isVisible()); self.assertFalse(self.p.inserted)
        QTest.mouseClick(self.p.material_button,Qt.MouseButton.LeftButton)
        self.w.open_new_material.assert_called_once_with(self.a)
        self.assertFalse(self.p.isVisible()); self.assertEqual(self.asset_api.call_count,0)

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

    def test_layout_legacy_settings_and_narrow_large_text_controls_reachable(self):
        self.p.sheet_size=QSize(980,680); self.p.split.setSizes([210,600]); self.p.save_layout()
        saved=copy.deepcopy(self.w.state['settings']['canvas_palette']); self.assertEqual(len(saved['columns']),3)
        self.open(); self.assertEqual(self.p.sheet_size,QSize(*saved['size']))
        self.w.state['settings']['ui_size']=18; self.w.apply_theme(preserve_layout=True)
        self.w.resize(640,480); self.p.fit_window(); APP.processEvents()
        self.assertTrue(self.p.rect().contains(self.p.surface.geometry())); self.assertTrue(self.p.narrow_categories.isVisible())
        self.assertTrue(self.p.category_pane.isHidden())
        for widget in (self.p.title,self.p.destination_label,self.p.close_button):
            self.assertTrue(self.p.surface.rect().contains(widget.mapTo(self.p.surface,widget.rect().bottomRight())))
        for widget in (self.p.category,self.p.new_button,self.p.material_button):
            self.p.scroll.ensureWidgetVisible(widget); APP.processEvents()
            point=widget.mapTo(self.p.scroll.viewport(),widget.rect().center())
            self.assertTrue(self.p.scroll.viewport().rect().contains(point))
            self.assertGreaterEqual(widget.height(),widget.fontMetrics().height()+16)
        output=os.environ.get('PCS_PICKER_SHOTS')
        if output:
            from pathlib import Path
            Path(output).mkdir(parents=True,exist_ok=True)
            self.p.scroll.verticalScrollBar().setValue(0); APP.processEvents()
            self.p.grab().save(str(Path(output)/'picker-640-480-18pt.png'))


if __name__=='__main__': unittest.main()
