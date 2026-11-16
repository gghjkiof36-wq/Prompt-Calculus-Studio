"""Current category picker; see FAST_SUITE.md for retired lookup test inventory."""
from _qt_wait import wait_ms
import copy
import os
import tempfile
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QSize
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.text_canvas import NodeDialog
from prompt_calculus_studio.core import uid, Storage
from prompt_calculus_studio import materials, multi_output as model
from _dialog_driver import drive_dialog
from _qt_cleanup import close_window

APP=QApplication.instance() or QApplication([])


class PaletteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        for context in (patch.dict(os.environ, PROMPT_STUDIO_V08='1'),
                        patch('prompt_calculus_studio.comfy_client.ComfyClient.request')):
            context.start();self.addCleanup(context.stop)
        self.w=Window(self.temp.name)
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.resize(1280,860);self.w.show();self.w.enter_canvas();wait_ms(20)
        self.c=self.w.canvas;self.cid=next(iter(self.c.data()['canvases']))
        self.category=materials.add_category(self.w.state,'Palette fixture')
        self.ids=[materials.save(self.w.state,dict(id=uid(),module=self.category,
                  name=f'local {i}',prompt=f'smile {i}',aliases=[f'alias-{i}'])) for i in range(6)]
        self.position=self.c.containers[self.cid].pos()+QPointF(60,150)
        self.p=None;self.open()

    def open(self):
        if self.p:self.p.reject()
        self.p=CanvasPalette(self.c,self.position,destination_id=self.cid)
        self.p.show();self.p.activateWindow();self.p.set_browser_category(self.category);wait_ms(10)

    def tearDown(self):
        if self.p:self.p.reject()
        close_window(self.w)

    def ids_shown(self):
        return [self.p.assets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.p.assets.count())]

    def insert(self,ident):
        self.assertTrue(self.p.restore_selection(self.p.assets,ident))
        item=self.p.assets.currentItem();self.p.assets.scrollToItem(item)
        QTest.mouseClick(self.p.assets.viewport(),Qt.MouseButton.LeftButton,pos=self.p.assets.visualItemRect(item).center())

    def test_category_and_alias_search_are_local_and_read_only(self):
        before=self.c.history_state()
        with patch.object(self.w.completion,'schedule') as schedule:
            self.assertEqual(self.ids_shown(),self.ids)
            self.p.search.setText('alias-2');self.assertEqual(self.ids_shown(),[self.ids[2]])
            self.p.search.clear();self.assertEqual(self.ids_shown(),self.ids)
            schedule.assert_not_called()
        self.assertEqual(self.c.history_state(),before)

    def test_empty_search_then_clear_restores_category(self):
        self.p.search.setText('absent-fixture-value')
        self.assertEqual(self.ids_shown(),[]);self.assertTrue(self.p.empty.isVisible())
        self.p.search.clear();self.assertEqual(self.ids_shown(),self.ids)

    def test_keyboard_search_list_and_category_focus(self):
        self.p.search.setText('local');self.p.search.setFocus()
        QTest.keyClick(self.p.search,Qt.Key.Key_Home);QTest.keyClick(self.p.search,Qt.Key.Key_Right)
        self.assertTrue(self.p.search.hasFocus());self.assertEqual(self.p.search.cursorPosition(),1)
        self.p.search.selectAll();QTest.keyClick(self.p.search,Qt.Key.Key_Right)
        self.assertTrue(self.p.search.hasFocus());self.assertEqual(self.p.search.cursorPosition(),5)
        self.p.search.clear()
        self.p.search.setFocus();QTest.keyClick(self.p.search,Qt.Key.Key_Down)
        self.assertTrue(self.p.assets.hasFocus());self.assertEqual(self.p.assets.currentRow(),0)
        QTest.keyClick(self.p.assets,Qt.Key.Key_Down);self.assertEqual(self.p.assets.currentRow(),1)
        QTest.keyClick(self.p.assets,Qt.Key.Key_Left);self.assertTrue(self.p.modules.hasFocus())
        QTest.keyClick(self.p.modules,Qt.Key.Key_Right);self.assertTrue(self.p.assets.hasFocus())

    def test_search_return_selects_but_does_not_create_free_text(self):
        before=self.c.history_state();self.p.search.setText('local 2')
        QTest.keyClick(self.p.search,Qt.Key.Key_Return)
        self.assertTrue(self.p.assets.hasFocus());self.assertTrue(self.p.isVisible())
        self.assertEqual(self.c.history_state(),before)
        QTest.keyClick(self.p.assets,Qt.Key.Key_Return)
        self.assertTrue(self.p.inserted);self.assertFalse(self.p.isVisible())

    def test_copy_uses_invocation_point_preserves_source_draft_and_undo(self):
        self.w.final.setPlainText('manual stays');originals=copy.deepcopy(self.w.state['items'])
        self.c.view.scale(1.65,1.65);self.c.view.centerOn(QPointF(-800,200))
        before=self.c.history_state();depth=len(self.c.undo_stack)
        self.insert(self.ids[2]);added=set(self.w.state['uses'])-set(before.get('uses',{}))
        self.assertEqual(len(added),1);ident=added.pop()
        self.assertEqual(self.w.state['uses'][ident]['prompt'],'smile 2')
        self.assertEqual(self.w.state['text_positions'][ident],[self.position.x(),self.position.y()])
        self.assertEqual(model.owner(self.w.state,ident),self.cid)
        self.assertTrue(self.c.containers[self.cid].sceneBoundingRect().contains(self.c.cards[ident].sceneBoundingRect()))
        self.assertEqual(self.w.state['items'],originals);self.assertEqual(self.w.final.toPlainText(),'manual stays')
        self.assertEqual(len(self.c.undo_stack),depth+1)
        after=self.c.history_state();self.c.undo();self.assertEqual(self.c.history_state(),before)
        self.c.redo();self.assertEqual(self.c.history_state(),after)

    def test_inserted_copy_and_owner_survive_database_reopen(self):
        before=set(self.w.state['uses']);self.insert(self.ids[1])
        ident=(set(self.w.state['uses'])-before).pop();self.w.persist()
        store=Storage(self.temp.name)
        try:
            state=store.load();self.assertEqual(model.owner(state,ident),self.cid)
            self.assertEqual(state['uses'][ident],self.w.state['uses'][ident])
            self.assertEqual(state['text_positions'][ident],[self.position.x(),self.position.y()])
        finally:store.close()

    def test_backdrop_and_escape_cancel_without_changes(self):
        before=self.c.history_state()
        QTest.mouseClick(self.p,Qt.MouseButton.LeftButton,pos=QPoint(2,2))
        self.assertFalse(self.p.isVisible());self.assertEqual(self.c.history_state(),before)
        self.open();QTest.keyClick(self.p.search,Qt.Key.Key_Escape)
        self.assertFalse(self.p.isVisible());self.assertEqual(self.c.history_state(),before)

    def test_plus_opens_choice_menu_before_blank_dialog(self):
        before=self.c.history_state();QTest.mouseClick(self.p.new_button,Qt.MouseButton.LeftButton)
        self.assertTrue(self.p.import_menu.isVisible());self.assertIsNone(APP.activeModalWidget())
        self.assertIn('新增臨時文字模塊',[a.text() for a in self.p.import_menu.actions()])
        self.p.import_menu.close();self.assertEqual(self.c.history_state(),before)

    def test_blank_dialog_cancel_preserves_picker_and_destination(self):
        before=self.c.history_state()
        def cancel(dialog):
            dialog.prompt.setPlainText('discard');dialog.reject()
        drive_dialog(self.p.new_module,NodeDialog,cancel)
        self.assertTrue(self.p.isVisible());self.assertFalse(self.p.inserted)
        self.assertEqual(self.p.destination_id,self.cid);self.assertEqual(self.c.history_state(),before)

    def test_blank_dialog_save_inserts_once_at_invocation(self):
        before=set(self.w.state['uses']);depth=len(self.c.undo_stack)
        def save(dialog):
            dialog.name.setText('temporary');dialog.prompt.setPlainText('new text');dialog.accept()
        drive_dialog(self.p.new_module,NodeDialog,save)
        added=set(self.w.state['uses'])-before;self.assertEqual(len(added),1)
        ident=added.pop();self.assertEqual(self.w.state['uses'][ident]['prompt'],'new text')
        self.assertEqual(self.w.state['text_positions'][ident],[self.position.x(),self.position.y()])
        self.assertEqual(len(self.c.undo_stack),depth+1);self.assertTrue(self.p.inserted)

    def test_resize_bounds_and_saved_columns_survive_reopening(self):
        self.p.sheet_size=QSize(1100,740);self.p.place_sheet();self.p.split.setSizes([240,760]);self.p.save_layout()
        saved=copy.deepcopy(self.w.state['settings']['canvas_palette']);self.w.persist();self.open()
        self.assertEqual(self.p.sheet_size,QSize(*saved['size']))
        self.assertEqual(self.p.split.sizes(),saved['columns'][:2])
        store=Storage(self.temp.name)
        try:self.assertEqual(store.load()['settings']['canvas_palette'],saved)
        finally:store.close()
        for width,height,font in ((1440,920,11),(960,620,18),(1100,800,14)):
            self.w.state['settings']['ui_size']=font;self.w.apply_theme(preserve_layout=True)
            self.w.resize(width,height);self.p.sheet_size=QSize(2400,1500);self.p.place_sheet();APP.processEvents()
            self.assertTrue(self.p.rect().contains(self.p.surface.geometry()))
            self.assertLessEqual((self.p.surface.geometry().center()-self.p.rect().center()).manhattanLength(),2)
            for listing in (self.p.modules,self.p.assets):
                if listing.isVisible():
                    self.assertGreater(listing.width(),70);self.assertGreater(listing.height(),40)
                    self.assertTrue(self.p.surface.rect().contains(listing.mapTo(self.p.surface,listing.rect().bottomRight())))
            self.assertGreaterEqual(self.p.search.height(),self.p.search.fontMetrics().height())
        before=self.p.surface.size();grip=self.p.grip
        QTest.mousePress(grip,Qt.MouseButton.LeftButton,pos=QPoint(9,9))
        QTest.mouseMove(grip,QPoint(-70,-40));QTest.mouseRelease(grip,Qt.MouseButton.LeftButton,pos=QPoint(9,9))
        self.assertLess(self.p.surface.width(),before.width())

    def test_workspace_change_rejects_stale_picker_insert(self):
        before=self.c.history_state();workspace=self.w.state['workspace']
        self.p.assets.setCurrentRow(0)
        try:
            self.w.state['workspace']='different-workspace';self.p.insert()
            self.assertFalse(self.p.inserted);self.assertIn('工作區',self.p.status.text())
        finally:self.w.state['workspace']=workspace
        self.assertEqual(self.c.history_state(),before)


if __name__=='__main__':unittest.main()
