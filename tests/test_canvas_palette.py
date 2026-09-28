"""Rev 6 insertion behavior, focus paths, and asynchronous result ownership.

All service requests are held locally; no public service or daily data is used.
"""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QSize
from PySide6.QtGui import QTextCursor, QInputMethodEvent, QFontDatabase
from PySide6.QtWidgets import QApplication, QPushButton
from PySide6.QtTest import QTest
from prompt_studio.window import Window
from prompt_studio.canvas_palette import CanvasPalette
from prompt_studio.core import uid

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_V081':'1'}); self.env.start()
        self.transport=patch('prompt_studio.comfy_client.ComfyClient.request'); self.transport.start()
        self.w=Window(self.temp.name); self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1280,860); self.w.apply_theme(); self.w.show(); self.w.set_interface_mode('canvas'); QTest.qWait(30)
        self.c=self.w.canvas; self.service=self.w.completion
        self.pending=[]; self.pool=patch.object(self.service.pool,'start',self.pending.append); self.pool.start()
        asset=self.w.state['items'][0]
        self.items=[]
        for i in range(6):
            a=copy.deepcopy(asset); a.update(id=uid(),name=f'sm local {i}',prompt=f'smile {i}',aliases=[])
            self.items.append(a)
        self.w.state['items'].extend(self.items)
        self.p=CanvasPalette(self.c,QPointF(840,460)); self.p.show(); self.p.activateWindow(); QTest.qWait(20)
        self.search('sm'); self.suggest()

    def tearDown(self):
        self.p.reject(); self.service.timer.stop(); self.service.tasks.clear(); self.service.active=None
        self.pool.stop(); self.w.close(); APP.processEvents(); self.transport.stop(); self.env.stop(); self.temp.cleanup()

    def search(self,text):
        self.p.query.setFocus(); self.p.query.setPlainText(text)
        self.p.query.moveCursor(QTextCursor.MoveOperation.End); self.service.timer.stop(); APP.processEvents()

    def suggest(self):
        self.rows=[dict(value=f'smile_{i}',source='Danbooru',category=0,count=10-i) for i in range(8)]
        self.p.query.display(self.p.query.context(),self.rows); self.service.timer.stop(); APP.processEvents()

    def key(self,key,widget=None,modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(widget or APP.focusWidget(),key,modifiers); APP.processEvents()

    def test_function_category_adds_at_invocation_point_and_undoes(self):
        self.search(''); self.p.restore_selection(self.p.modules,'@functions')
        names=[self.p.assets.item(i).text() for i in range(self.p.assets.count())]
        self.assertEqual(names,['畫布','Prompt 控制','CLIP 輸入','圖片來源','載入圖片（單張／工作流輸出）','圖片輸入','預排程','預覽圖片'])
        before=copy.deepcopy(self.c.data()); self.p.restore_selection(self.p.assets,'CLIP 輸入'); self.p.insert()
        added=set(self.c.data()['clip_inputs'])-set(before['clip_inputs'])
        self.assertEqual(len(added),1); key=added.pop()
        self.assertEqual(self.w.state['text_positions'][key],[840,460]); self.assertFalse(self.p.isVisible())
        self.c.undo(); self.assertEqual(self.c.data(),before)

    def test_search_right_fifth_left_restores_search_cursor(self):
        cursor=self.p.query.textCursor().position()
        self.key(Qt.Key.Key_Right)
        self.assertTrue(self.p.suggestions.hasFocus()); self.assertEqual(self.p.suggestions.currentRow(),0)
        for _ in range(4): self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Left)
        self.assertTrue(self.p.query.hasFocus()); self.assertEqual(self.p.query.textCursor().position(),cursor)
        self.key(Qt.Key.Key_Right); self.assertEqual(self.p.suggestions.currentRow(),0)

    def test_local_second_to_fifth_back_then_restore_fifth(self):
        self.key(Qt.Key.Key_Down); self.key(Qt.Key.Key_Down)
        self.assertEqual(self.p.assets.currentRow(),1)
        self.key(Qt.Key.Key_Right)
        for _ in range(4): self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Left)
        self.assertTrue(self.p.assets.hasFocus()); self.assertEqual(self.p.assets.currentRow(),1)
        self.key(Qt.Key.Key_Right); self.assertEqual(self.p.suggestions.currentRow(),4)
        self.key(Qt.Key.Key_Left); self.key(Qt.Key.Key_Up)
        self.assertTrue(self.p.assets.hasFocus()); self.assertEqual(self.p.assets.currentRow(),0)
        self.key(Qt.Key.Key_Up); self.assertTrue(self.p.query.hasFocus())

    def test_tab_cycle_and_shift_tab_restore_selection(self):
        self.key(Qt.Key.Key_Tab); self.assertTrue(self.p.assets.hasFocus())
        self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Tab); self.assertTrue(self.p.suggestions.hasFocus())
        self.key(Qt.Key.Key_Tab); self.assertTrue(self.p.query.hasFocus())
        self.key(Qt.Key.Key_Backtab); self.assertTrue(self.p.suggestions.hasFocus())
        self.key(Qt.Key.Key_Backtab); self.assertTrue(self.p.assets.hasFocus()); self.assertEqual(self.p.assets.currentRow(),1)
        self.key(Qt.Key.Key_Backtab); self.assertTrue(self.p.query.hasFocus())

    def test_text_cursor_selection_and_ime_protect_editing(self):
        self.key(Qt.Key.Key_Home); self.key(Qt.Key.Key_Right)
        self.assertTrue(self.p.query.hasFocus()); self.assertEqual(self.p.query.textCursor().position(),1)
        self.p.query.selectAll(); self.key(Qt.Key.Key_Right)
        self.assertTrue(self.p.query.hasFocus())
        self.p.query.selectAll(); saved=(self.p.query.textCursor().anchor(),self.p.query.textCursor().position())
        self.key(Qt.Key.Key_Tab); self.key(Qt.Key.Key_Backtab)
        self.assertEqual((self.p.query.textCursor().anchor(),self.p.query.textCursor().position()),saved)
        before=self.c.history_state()
        APP.sendEvent(self.p.query,QInputMethodEvent('輸入',[]))
        for key in (Qt.Key.Key_Return,Qt.Key.Key_Down,Qt.Key.Key_Right,Qt.Key.Key_Escape): self.key(key,self.p.query)
        self.assertTrue(self.p.query.composing); self.assertTrue(self.p.isVisible()); self.assertEqual(self.c.history_state(),before)
        APP.sendEvent(self.p.query,QInputMethodEvent('',[]))

    def test_empty_and_replaced_results_keep_valid_focus(self):
        self.key(Qt.Key.Key_Right)
        self.p.query.display(self.p.query.context(),[])
        self.assertTrue(self.p.query.hasFocus()); self.assertEqual(self.p.suggestions.currentRow(),-1)
        self.search('does_not_match_any_local_asset')
        self.key(Qt.Key.Key_Down); self.key(Qt.Key.Key_Right); self.key(Qt.Key.Key_Tab)
        self.assertTrue(self.p.query.hasFocus()); self.assertEqual(self.p.assets.currentRow(),-1)
        self.search('sm'); self.suggest(); self.key(Qt.Key.Key_Down); self.key(Qt.Key.Key_Down)
        self.key(Qt.Key.Key_Right)
        removed=self.p.selected_id(self.p.assets)
        self.w.state['items']=[a for a in self.w.state['items'] if a['id']!=removed]
        self.p.refresh(); self.key(Qt.Key.Key_Left)
        self.assertTrue(self.p.query.hasFocus())

    def test_late_result_does_not_steal_focus_or_cover_local_list(self):
        self.key(Qt.Key.Key_Down); self.key(Qt.Key.Key_Down)
        selected=self.p.selected_id(self.p.assets)
        self.p.query.display(self.p.query.context(),self.rows)
        self.assertTrue(self.p.assets.hasFocus()); self.assertEqual(self.p.selected_id(self.p.assets),selected)
        self.assertFalse(self.p.query.completer.popup().isVisible())
        self.assertFalse(hasattr(self.p,'feedback'))
        left=self.p.assets.mapTo(self.p.surface,QPoint()); right=self.p.suggestions.mapTo(self.p.surface,QPoint())
        self.assertLess(left.x()+self.p.assets.width(),right.x())

    def test_single_typed_insert_closes_and_preserves_position_draft_and_undo(self):
        self.w.final.setPlainText('manual stays')
        self.c.view.scale(1.65,1.65); self.c.view.centerOn(QPointF(-800,200))
        before=self.c.history_state(); depth=len(self.c.undo_stack)
        self.search('user tag'); self.key(Qt.Key.Key_Return)
        self.assertFalse(self.p.isVisible()); self.assertTrue(self.p.inserted)
        added=set(self.w.state['uses'])-set(before.get('uses',{})); self.assertEqual(len(added),1)
        ident=added.pop(); self.assertEqual(self.w.state['text_positions'][ident],[840.,460.])
        self.assertEqual(self.w.final.toPlainText(),'manual stays'); self.assertEqual(len(self.c.undo_stack),depth+1)
        after=self.c.history_state(); self.c.undo(); self.assertEqual(self.c.history_state(),before)
        self.c.redo(); self.assertEqual(self.c.history_state(),after)
        self.w.persist(); self.assertEqual(self.w.store.load()['text_positions'][ident],[840.,460.])

    def test_single_suggestion_and_local_insert_use_invocation_point(self):
        before=set(self.w.state.get('uses',{}))
        self.key(Qt.Key.Key_Right); self.key(Qt.Key.Key_Down); self.key(Qt.Key.Key_Return)
        ident=(set(self.w.state['uses'])-before).pop()
        self.assertEqual(self.w.state['uses'][ident]['prompt'],'smile 1')
        self.assertEqual(self.w.state['text_positions'][ident],[840.,460.]); self.assertFalse(self.p.isVisible())
        self.p=CanvasPalette(self.c,QPointF(640,350)); self.p.show(); self.p.activateWindow(); QTest.qWait(20)
        self.search('sm local 2'); item=self.p.assets.item(0)
        QTest.mouseClick(self.p.assets.viewport(),Qt.MouseButton.LeftButton,pos=self.p.assets.visualItemRect(item).center())
        added=next(k for k,v in self.w.state['uses'].items() if v.get('source_id')==self.items[2]['id'])
        self.assertEqual(self.w.state['text_positions'][added],[640.,350.]); self.assertFalse(self.p.isVisible())

    def test_backdrop_click_and_escape_close_without_canvas_changes(self):
        before=self.c.history_state()
        QTest.mouseClick(self.p,Qt.MouseButton.LeftButton,pos=QPoint(2,2)); APP.processEvents()
        self.assertFalse(self.p.isVisible()); self.assertEqual(self.c.history_state(),before)
        self.p=CanvasPalette(self.c,QPointF(0,0)); self.p.show(); self.p.activateWindow(); QTest.qWait(20)
        self.key(Qt.Key.Key_Escape,self.p.query)
        self.assertFalse(self.p.isVisible()); self.assertEqual(self.c.history_state(),before)

    def test_resize_center_bounds_large_font_and_no_close_buttons(self):
        for width,height,font in ((1440,920,11),(960,620,18),(1100,800,14)):
            self.w.state['settings']['ui_size']=font; self.w.apply_theme(preserve_layout=True)
            self.w.resize(width,height); self.p.sheet_size=QSize(2400,1500); self.p.place_sheet(); APP.processEvents()
            self.assertTrue(self.p.rect().contains(self.p.surface.geometry()))
            self.assertLessEqual((self.p.surface.geometry().center()-self.p.rect().center()).manhattanLength(),2)
            for listing in (self.p.modules,self.p.assets,self.p.suggestions):
                self.assertGreater(listing.width(),70); self.assertGreater(listing.height(),40)
                self.assertTrue(self.p.surface.rect().contains(listing.mapTo(self.p.surface,listing.rect().bottomRight())))
            self.assertGreaterEqual(self.p.query.viewport().height(),self.p.query.fontMetrics().height())
            actions={b.text():b for b in self.p.findChildren(QPushButton)}
            category=actions['新增分類']; blank=actions['新增空白模組']
            self.assertEqual(category.mapTo(self.p,QPoint()).y(),blank.mapTo(self.p,QPoint()).y())
        before=self.p.surface.size(); grip=self.p.grip
        QTest.mousePress(grip,Qt.MouseButton.LeftButton,pos=QPoint(9,9))
        QTest.mouseMove(grip,QPoint(-70,-40)); QTest.mouseRelease(grip,Qt.MouseButton.LeftButton,pos=QPoint(9,9))
        self.assertLess(self.p.surface.width(),before.width())
        self.assertLessEqual((self.p.surface.geometry().center()-self.p.rect().center()).manhattanLength(),2)
        self.assertFalse(self.p.query.verticalScrollBar().isVisible())
        self.assertFalse({'×','關閉'} & {b.text() for b in self.p.findChildren(QPushButton)})

    def start_request(self):
        self.w.state['settings']['online']=True; self.service.next_request=0
        self.service.schedule(self.p.query); self.service.timer.stop(); self.service.lookup()
        self.assertTrue(self.pending); return self.pending[-1]

    def test_resize_and_column_widths_survive_reopening(self):
        self.p.sheet_size=QSize(1100,740); self.p.place_sheet(); self.p.split.setSizes([210,450,400]); self.p.save_layout()
        saved=copy.deepcopy(self.w.state['settings']['canvas_palette']); self.p.reject(); self.w.persist()
        self.p=CanvasPalette(self.c,None); self.p.show(); QTest.qWait(10)
        self.assertEqual(self.p.sheet_size,QSize(*saved['size']))
        self.assertEqual(self.p.split.sizes(),saved['columns'])
        from prompt_studio.core import Storage
        store=Storage(self.temp.name)
        try: self.assertEqual(store.load()['settings']['canvas_palette'],saved)
        finally: store.close()

    def test_cache_clear_disable_reenable_reject_old_callbacks(self):
        for action in ('clear','disable','reenable'):
            with self.subTest(action=action):
                self.search('sm'+action); request=self.start_request(); key=self.service.active[3]
                if action=='clear': self.service.clear_cache()
                else:
                    self.service.configure_cache(False)
                    if action=='reenable': self.service.configure_cache(True)
                self.service.finished(request.serial,self.rows,'')
                self.service.configure_cache(True); self.assertIsNone(self.w.store.cached(key))
        self.search('smfresh'); request=self.start_request(); key=self.service.active[3]
        self.service.finished(request.serial,self.rows,''); self.assertEqual(self.w.store.cached(key),self.rows)

    def test_stale_cache_is_shown_while_refresh_is_pending(self):
        import json,time
        key=json.dumps(['dictionary','sm',False],ensure_ascii=False)
        old=[dict(value='sm_old',source='Danbooru',category=0,count=1)]
        self.w.store.cache(key,old)
        with self.w.store.db:self.w.store.db.execute('UPDATE cache SET saved=? WHERE key=?',(time.time()-90000,key))
        request=self.start_request()
        self.assertIn('sm old',self.p.suggestions.item(0).text())
        self.service.finished(request.serial,self.rows,'')
        self.assertEqual(self.w.store.cached(key),self.rows); self.assertEqual(self.p.suggestions.count(),8)

    def test_pending_query_finishes_while_focus_in_local_column(self):
        request=self.start_request(); self.key(Qt.Key.Key_Down); self.key(Qt.Key.Key_Down)
        self.service.finished(request.serial,self.rows,'')
        self.assertTrue(self.p.assets.hasFocus()); self.assertEqual(self.p.assets.currentRow(),1)
        self.assertEqual(self.p.suggestions.count(),8)

    def test_delayed_lookup_does_not_cross_workspace_before_request_starts(self):
        self.w.state['settings']['online']=True
        self.service.schedule(self.p.query); self.service.timer.stop()
        workspace=self.w.state['workspace']; self.w.state['workspace']='changed-before-request'
        self.service.lookup(); self.assertFalse(self.pending)
        self.w.state['workspace']=workspace

    def test_canvas_ownership_and_saved_reopen_after_palette_insert(self):
        from prompt_studio import multi_output as model
        self.p.reject()
        cid=self.c.add_canvas(QPointF(1500,800))
        point=self.c.containers[cid].pos()+QPointF(60,150)
        self.p=CanvasPalette(self.c,point); self.p.show(); self.p.activateWindow(); QTest.qWait(20)
        before=self.c.history_state(); self.search('owned tag'); self.key(Qt.Key.Key_Return)
        ident=(set(self.w.state['uses'])-set(before.get('uses',{}))).pop()
        self.assertEqual(model.owner(self.w.state,ident),cid)
        self.assertTrue(self.c.containers[cid].sceneBoundingRect().contains(self.c.cards[ident].sceneBoundingRect()))
        after=self.c.history_state(); self.c.undo(); self.assertEqual(self.c.history_state(),before)
        self.c.redo(); self.assertEqual(self.c.history_state(),after)
        self.w.persist()
        from prompt_studio.core import Storage
        reopened=Storage(self.temp.name)
        try:
            state=reopened.load(); self.assertEqual(model.owner(state,ident),cid)
            self.assertEqual(state['text_positions'][ident],self.w.state['text_positions'][ident])
        finally: reopened.close()

    def test_old_query_offline_close_workspace_and_store_reject_late_results(self):
        for change in ('query','offline','close','workspace','store'):
            with self.subTest(change=change):
                self.search('sm'+change); request=self.start_request(); key=self.service.active[3]
                workspace=self.w.state['workspace']; store=self.w.store
                if change=='query': self.search('different query')
                elif change=='offline': self.w.state['settings']['online']=False
                elif change=='close': self.p.reject()
                elif change=='workspace': self.w.state['workspace']='different-workspace'
                elif change=='store': self.w.store=object()
                self.service.finished(request.serial,self.rows,'')
                self.w.store=store; self.w.state['workspace']=workspace
                self.assertIsNone(store.cached(key))
                if change=='close':
                    self.p=CanvasPalette(self.c,None); self.p.show(); self.p.activateWindow(); QTest.qWait(20)


if __name__=='__main__': unittest.main()
