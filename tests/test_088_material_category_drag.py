"""Cross-category Qt viewport drag transactions; OS drag transport is isolated."""
import copy
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF,QEvent,QCoreApplication
from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent,QDropEvent,QDragLeaveEvent,QMouseEvent,QFontDatabase
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
import test_editor_reorder as fixture
from prompt_calculus_studio import materials,material_navigation as nav,multi_output
from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.material_folder_navigation import ROW
from prompt_calculus_studio.prompt_editor import MaterialEditorForm


class MaterialCategoryDragTests(unittest.TestCase):
    tearDown=fixture.EditorReorderTests.tearDown

    def setUp(self):
        fixture.EditorReorderTests.setUp(self)
        QTest.qWait(350);self.w.persist()

    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])
        for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            path=Path('C:/Windows/Fonts')/font
            if path.is_file():QFontDatabase.addApplicationFont(str(path))

    def row(self,ident):
        return next(self.page.categories.item(i) for i in range(self.page.categories.count())
                    if self.page.categories.item(i).data(Qt.ItemDataRole.UserRole)==ident)

    def capture(self,widget,name):
        if folder:=os.environ.get('PCS_QA_OUTPUT'):
            path=Path(folder);path.mkdir(parents=True,exist_ok=True);self.assertTrue(widget.grab().save(str(path/name)))

    def drag(self,ident,destination,*,cancel=False,during=None,source_override=None):
        listing=self.page.listing; target=self.page.categories
        source=next(listing.item(i) for i in range(listing.count()) if listing.item(i).data(Qt.ItemDataRole.UserRole)==ident)
        listing.scrollToItem(source);self.app.processEvents();start=listing.visualItemRect(source).center();received=[]
        def execute(drag,actions,default):
            item=self.row(destination);target.scrollToItem(item);self.app.processEvents();point=target.visualItemRect(item).center()
            mime=drag.mimeData()
            class Enter(QDragEnterEvent):
                def source(self):return source_override or listing
            class Move(QDragMoveEvent):
                def source(self):return source_override or listing
            class Drop(QDropEvent):
                def source(self):return source_override or listing
            enter=Enter(point,actions,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(target.viewport(),enter)
            move=Move(point,actions,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(target.viewport(),move)
            if during:during()
            if cancel:
                self.app.sendEvent(target.viewport(),QDragLeaveEvent());received.append(False);return Qt.DropAction.IgnoreAction
            if move.isAccepted():self.capture(self.w,'material-drag-category-target.png')
            drop=Drop(QPointF(point),actions,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(target.viewport(),drop);received.append(drop.isAccepted());return drop.dropAction()
        with patch('prompt_calculus_studio.prompt_editor.QDrag.exec',execute):
            QTest.mousePress(listing.viewport(),Qt.MouseButton.LeftButton,pos=start)
            point=start+QPoint(0,self.app.startDragDistance()+8)
            event=QMouseEvent(QEvent.Type.MouseMove,QPointF(point),QPointF(listing.viewport().mapToGlobal(point)),
                Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
            self.app.sendEvent(listing.viewport(),event)
            QTest.mouseRelease(listing.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertEqual(len(received),1,'The pointer gesture must start the real QDrag path')
        self.assertIsNone(listing.category_drag);self.assertIsNone(target._material_hover)
        self.app.processEvents();return received[0]

    def test_move_preserves_copies_drafts_ids_and_undo_redo_save_reopen(self):
        c=self.w.canvas;canvas=next(iter(c.data()['canvases']))
        c.commit(lambda state:materials.add_to_canvas(state,self.ids[0],canvas))
        self.w.state.update(draft='',draft_base='manual base');multi_output.capture_current(self.w.state)
        form=MaterialEditorForm(self.w,compact=True);form.open_material(self.ids[2]);form.prompt.insertPlainText('local unsaved ')
        form._publish(force=True);draft=copy.deepcopy(self.w._material_editor_session)
        before=c.history_state();undo=len(c.undo_stack);record=copy.deepcopy(materials.item(self.w.state,self.ids[0]))
        try:
            with patch.object(self.w.comfy.input_flow.chain,'route_changed') as route:
                self.assertTrue(self.drag(self.ids[0],self.other_cat));self.assertEqual(len(c.undo_stack),undo+1)
                expected=dict(record,module=self.other_cat);self.assertEqual(materials.item(self.w.state,self.ids[0]),expected)
                for field in ('uses','draft','draft_base','multi_output'):
                    self.assertEqual(c.history_state().get(field),before.get(field))
                self.assertEqual(self.w._material_editor_session,draft)
                self.page.categories.setFocus();QTest.keyClick(self.page.categories,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(c.history_state(),before)
                QTest.keyClick(self.page.categories,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(materials.item(self.w.state,self.ids[0]),expected);route.assert_not_called()
            self.w.persist();self.assertEqual(materials.item(self.w.store.load(),self.ids[0]),expected)
        finally:form.dirty=False;form.close();form.deleteLater()
        for saved in self.w._material_editor_session['drafts'].values():saved['dirty']=False
        old=self.w;old.close();old.deleteLater();self.app.processEvents();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.w=fixture.Window(self.folder.name);self.w.show();self.w.leave_canvas();self.page=self.w.prompt_editor
        self.assertEqual(materials.item(self.w.state,self.ids[0]),expected);self.assertEqual(self.w.state['draft'],'')
        self.assertEqual(self.w.state['uses'],before['uses'])

    def test_folder_child_counts_picker_disclosure_and_editor_stay_in_sync(self):
        folder=nav.add_folder(self.w.state,'人物');nav.move(self.w.state,self.other_cat,folder);self.page.refresh()
        self.assertTrue(self.drag(self.ids[0],self.other_cat))
        self.assertEqual(self.row(folder).data(ROW)['count'],3)
        self.page.set_browser_category(folder);self.capture(self.w,'editor-category-moved.png')
        self.w.enter_canvas();c=self.w.canvas;picker=CanvasPalette(c,QPointF(800,500),destination_id=next(iter(c.data()['canvases'])))
        picker.show();self.app.processEvents();self.assertEqual(picker.get_browser_category(),folder)
        self.assertIn(self.ids[0],[picker.assets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(picker.assets.count())])
        row=next(picker.modules.item(i) for i in range(picker.modules.count()) if picker.modules.item(i).data(Qt.ItemDataRole.UserRole)==folder)
        self.assertEqual(row.data(ROW)['count'],3)
        arrow=picker.modules.regions(picker.modules.visualItemRect(row),row.data(ROW))[0]
        QTest.mouseClick(picker.modules.viewport(),Qt.MouseButton.LeftButton,pos=arrow.center())
        self.assertNotIn(self.other_cat,picker.modules.order_ids());self.capture(picker,'canvas-picker-category-moved.png');picker.reject()
        self.w.leave_canvas();self.assertNotIn(self.other_cat,self.page.categories.order_ids())
        self.page.toggle_folder(folder);self.w.enter_canvas();picker=CanvasPalette(c,QPointF(800,500));picker.show();self.app.processEvents()
        self.assertIn(self.other_cat,picker.modules.order_ids());picker.reject();self.w.leave_canvas()

    def test_cancel_same_category_folder_and_foreign_source_are_noops(self):
        folder=nav.add_folder(self.w.state,'空資料夾');self.page.refresh();before=self.w.canvas.history_state();undo=len(self.w.canvas.undo_stack)
        for target,kwargs in ((self.other_cat,{'cancel':True}),(self.cat,{}),(folder,{}),(None,{}),(self.other_cat,{'source_override':self.page.categories})):
            self.assertFalse(self.drag(self.ids[0],target,**kwargs));self.assertEqual(self.w.canvas.history_state(),before)
            self.assertEqual(len(self.w.canvas.undo_stack),undo)

    def test_changed_context_material_and_failed_commit_reject_drop(self):
        before=self.w.canvas.history_state();undo=len(self.w.canvas.undo_stack)
        self.assertFalse(self.drag(self.ids[0],self.other_cat,during=lambda:setattr(self.w,'canvas_edit_context',object())))
        self.assertEqual(self.w.canvas.history_state(),before)
        def edit():materials.item(self.w.state,self.ids[0])['notes']='changed during drag'
        self.assertFalse(self.drag(self.ids[0],self.other_cat,during=edit))
        self.assertEqual(materials.item(self.w.state,self.ids[0])['module'],self.cat)
        with patch('prompt_calculus_studio.materials.move_category',side_effect=ValueError('isolated failure')):
            self.assertFalse(self.drag(self.ids[0],self.other_cat))
        self.assertEqual(materials.item(self.w.state,self.ids[0])['module'],self.cat);self.assertEqual(len(self.w.canvas.undo_stack),undo)

    def test_uncategorized_target_accepts_material_without_changing_content(self):
        destination=next((m['id'] for m in self.w.state['modules'] if m['name']=='未分類'),'@uncategorized')
        original=copy.deepcopy(materials.item(self.w.state,self.ids[0]))
        self.assertTrue(self.drag(self.ids[0],destination))
        moved=materials.item(self.w.state,self.ids[0]);destination=next(m['id'] for m in self.w.state['modules'] if m['name']=='未分類')
        self.assertEqual(moved,dict(original,module=destination))
