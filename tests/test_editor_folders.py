"""Reachable Editor folder flows, Qt menus, drag, drafts and transaction isolation."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import test_editor_reorder as fixture
from prompt_calculus_studio import material_navigation as nav, materials
from prompt_calculus_studio.core import build_prompt
from prompt_calculus_studio.prompt_editor import MaterialEditorForm
from prompt_calculus_studio.widgets import RoundMenu


class EditorFolderTests(unittest.TestCase):
    setUp = fixture.EditorReorderTests.setUp
    tearDown = fixture.EditorReorderTests.tearDown
    internal_event = fixture.EditorReorderTests.internal_event
    drag = fixture.EditorReorderTests.drag

    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])

    def row(self, ident):
        return next(self.page.categories.item(i) for i in range(self.page.categories.count())
                    if self.page.categories.item(i).data(Qt.ItemDataRole.UserRole) == ident)

    def create(self, name='人物'):
        with patch('prompt_calculus_studio.material_editor_browser.InputDialog.getText', return_value=(name, True)):
            QTest.mouseClick(self.page.category_add, Qt.MouseButton.LeftButton)
            menu = QApplication.activePopupWidget(); self.assertIsInstance(menu, RoundMenu)
            self.assertEqual([v.text() for v in menu.actions()], ['新增資料夾', '新增分類'])
            menu.actions()[0].trigger(); menu.close(); self.app.processEvents()
        return self.page.get_browser_category()

    def move_menu(self, category, destination):
        listing = self.page.categories; item = self.row(category); listing.scrollToItem(item); self.app.processEvents()
        from prompt_calculus_studio.material_folder_navigation import ROW
        more = listing.regions(listing.visualItemRect(item), item.data(ROW))[3]
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=more.center())
        menu = QApplication.activePopupWidget(); self.assertIsInstance(menu, RoundMenu)
        submenu = menu.actions()[0].menu(); self.assertIsInstance(submenu, RoundMenu)
        text = nav.folder(self.w.state, destination)['name'] if destination else '移回外層'
        next(v for v in submenu.actions() if v.text() == text).trigger()
        menu.close(); self.app.processEvents()

    def protected(self):
        return copy.deepcopy({k: self.w.state.get(k) for k in
            ('modules', 'items', 'uses', 'multi_output', 'draft', 'draft_base', 'output_order', 'content_history')})

    def test_folder_full_chain_aggregate_move_rename_remove_undo_reopen(self):
        protected = self.protected(); prompt = build_prompt(self.w.state)
        fid = self.create(); self.assertEqual(self.page.listing.count(), 0)
        self.move_menu(self.cat, fid); self.move_menu(self.other_cat, fid)
        self.page.set_browser_category(fid)
        self.assertEqual(set(self.page.listing.order_ids()), set(self.ids))
        self.page.search.setText('Bravo'); self.assertEqual(self.page.listing.order_ids(), [self.ids[0]])
        self.page.search.clear(); self.page.set_browser_category(self.cat); self.assertEqual(self.page.listing.count(), 3)
        self.move_menu(self.cat, None); self.assertEqual(self.page.get_browser_category(), self.cat)
        with patch('prompt_calculus_studio.material_editor_browser.InputDialog.getText', return_value=('收納', True)):
            self.page.rename_folder(fid)
        self.assertEqual(nav.folder(self.w.state, fid)['name'], '收納')
        self.page.set_browser_category(fid)
        with patch('prompt_calculus_studio.material_editor_browser.ask', return_value=True): self.page.remove_folder(fid)
        self.assertIsNone(self.page.get_browser_category()); self.assertIsNone(nav.parent(self.w.state, self.other_cat))
        self.page.categories.setFocus(); QTest.keyClick(self.page.categories, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents(); self.assertEqual(nav.parent(self.w.state, self.other_cat), fid)
        QTest.keyClick(self.page.categories, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.app.processEvents(); self.assertIsNone(nav.folder(self.w.state, fid))
        self.assertEqual(self.protected(), protected); self.assertEqual(build_prompt(self.w.state), prompt)
        self.w.canvas.undo(); self.page.toggle_folder(fid); self.w.persist()
        loaded = self.w.store.load(); self.assertEqual(loaded[nav.FIELD], self.w.state[nav.FIELD])
        self.assertIn(fid, loaded['settings']['material_folder_collapsed'])
        self.w.state = loaded; self.page.refresh(); self.assertNotIn(self.other_cat, self.page.categories.order_ids())
        self.assertEqual(self.protected(), protected)

    def test_folder_disclosure_keeps_category_preview_and_dirty_document(self):
        fid = self.create(); self.move_menu(self.cat, fid); self.page.set_browser_category(self.cat)
        self.page.preview_material(self.ids[0]); listing_before = self.page.listing.order_ids()
        form = MaterialEditorForm(self.w, compact=True); form.open_material(self.ids[0])
        form.prompt.moveCursor(QTextCursor.MoveOperation.End); form.prompt.insertPlainText(' 未保存')
        form._publish(force=True); document = form.prompt.document(); session = copy.deepcopy(self.w._material_editor_session)
        try:
            listing = self.page.categories; item = self.row(fid)
            from prompt_calculus_studio.material_folder_navigation import ROW
            arrow = listing.regions(listing.visualItemRect(item), item.data(ROW))[0]
            undo = len(self.w.canvas.undo_stack)
            QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=arrow.center())
            self.assertEqual(self.page.get_browser_category(), self.cat); self.assertEqual(self.page.preview_id, self.ids[0])
            self.assertEqual(self.page.listing.order_ids(), listing_before); self.assertEqual(len(self.w.canvas.undo_stack), undo)
            self.page.toggle_folder(fid); self.move_menu(self.cat, None)
            self.w.canvas.undo(); self.w.canvas.redo()
            self.assertIs(form.prompt.document(), document); self.assertEqual(self.w._material_editor_session, session)
            self.assertTrue(form.prompt.toPlainText().endswith(' 未保存'))
        finally: form.dirty = False; form.close(); form.deleteLater()

    def test_folder_drag_drop_into_collapsed_folder_out_to_root_and_reorder(self):
        fid = self.create(); self.page.toggle_folder(fid)
        listing = self.page.categories; source = self.row(self.cat); target = self.row(fid)
        position = listing.visualItemRect(target).center(); mime = listing.drag_mime(source)
        for event in ('enter', 'move', 'drop'): self.assertTrue(self.internal_event(listing, event, position, mime).isAccepted())
        self.assertEqual(nav.parent(self.w.state, self.cat), fid)
        self.page.toggle_folder(fid)
        self.move_menu(self.other_cat, fid)
        self.drag(listing, self.other_cat, self.cat)
        self.assertEqual(nav.folder(self.w.state, fid)['categories'], [self.other_cat, self.cat])
        self.drag(listing, self.cat, None)
        self.assertIsNone(nav.parent(self.w.state, self.cat)); self.assertEqual(nav.view(self.w.state)['order'][0], self.cat)
        self.w.canvas.undo(); self.assertEqual(nav.parent(self.w.state, self.cat), fid)

    def test_folder_new_category_and_material_use_real_categories_cancel_is_read_only(self):
        fid = self.create(); before = self.protected(); undo = len(self.w.canvas.undo_stack)
        with patch('prompt_calculus_studio.material_editor_browser.InputDialog.getText', return_value=('取消', False)):
            self.page.new_folder(); self.page.new_category(); self.page.rename_folder(fid)
        self.assertEqual(self.protected(), before); self.assertEqual(len(self.w.canvas.undo_stack), undo)
        with patch('prompt_calculus_studio.material_editor_browser.InputDialog.getText', return_value=('新分類', True)):
            self.page.new_category()
        category = self.page.get_browser_category(); self.assertEqual(nav.parent(self.w.state, category), fid)
        self.page.set_browser_category(fid); dialog = self.page.new_material()
        self.assertTrue(dialog.require_category); self.assertIsNone(dialog.form.edit_category.currentData())
        dialog.name.setText('新素材'); dialog.prompt.setPlainText('prompt')
        self.assertFalse(dialog.save()); self.assertNotIn('新素材', [v['name'] for v in self.w.state['items']])
        dialog.form.edit_category.setCurrentIndex(dialog.form.edit_category.findData(category)); self.assertTrue(dialog.save())
        saved = materials.item(self.w.state, dialog.target_id); self.assertEqual(saved['module'], category)
        self.w.canvas.undo(); self.assertNotIn(saved['id'], [v['id'] for v in self.w.state['items']])
        self.w.canvas.redo(); self.assertEqual(materials.item(self.w.state, saved['id'])['module'], category)

    def test_folder_popup_escape_narrow_access_and_no_output_side_effects(self):
        with patch.object(self.w.comfy.input_flow.chain, 'route_changed') as route, patch.object(self.w.canvas, 'refresh') as canvas_refresh:
            fid = self.create(); self.move_menu(self.cat, fid); self.w.canvas.undo(); self.w.canvas.redo()
            route.assert_not_called(); canvas_refresh.assert_not_called()
        self.w.resize(760, 700); self.app.processEvents(); self.page.set_browser_category(fid)
        self.assertTrue(self.page.category_more.isVisible()); self.assertTrue(self.page.category_more.isEnabled())
        QTest.mouseClick(self.page.category_more, Qt.MouseButton.LeftButton)
        menu = QApplication.activePopupWidget(); self.assertIsInstance(menu, RoundMenu)
        self.assertEqual([a.text() for a in menu.actions()], ['重新命名', '移除資料夾'])
        self.assertTrue(menu.screen().availableGeometry().contains(menu.geometry()))
        QTest.keyClick(menu, Qt.Key.Key_Escape); self.app.processEvents()
        self.assertIsNone(QApplication.activePopupWidget())

    def test_folder_close_reopen_retains_membership_order_and_collapse(self):
        fid = self.create(); self.move_menu(self.cat, fid); self.page.toggle_folder(fid)
        saved = copy.deepcopy(self.w.state[nav.FIELD]); records = copy.deepcopy(self.w.state['items'])
        old = self.w; old.persist(); self.assertTrue(old.close()); old.deleteLater(); self.app.processEvents()
        self.w = fixture.Window(self.folder.name); self.w.resize(1440, 900); self.w.show(); self.w.leave_canvas()
        self.page = self.w.prompt_editor; self.app.processEvents()
        self.assertEqual(self.w.state[nav.FIELD], saved); self.assertEqual(self.w.state['items'], records)
        self.assertNotIn(self.cat, self.page.categories.order_ids()); self.page.set_browser_category(fid)
        self.assertEqual(set(self.page.listing.order_ids()), {self.ids[0], self.ids[2], self.ids[4]})

    def test_folder_noop_cancel_and_outside_popup_do_not_create_transactions(self):
        fid = self.create(); self.move_menu(self.cat, fid)
        before = self.protected(); undo = len(self.w.canvas.undo_stack)
        self.assertFalse(self.page.move_category(self.cat, fid))
        with patch('prompt_calculus_studio.material_editor_browser.InputDialog.getText', return_value=('人物', True)):
            self.page.rename_folder(fid)
        with patch('prompt_calculus_studio.material_editor_browser.ask', return_value=False): self.page.remove_folder(fid)
        menu = self.page.category_add_menu()
        QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=QPoint(-12, -12)); self.app.processEvents()
        self.assertIsNone(QApplication.activePopupWidget())
        self.assertEqual(self.protected(), before); self.assertEqual(len(self.w.canvas.undo_stack), undo)


if __name__ == '__main__': unittest.main()
