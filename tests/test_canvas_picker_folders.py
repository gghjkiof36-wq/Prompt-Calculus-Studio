"""Canvas picker shares library folders without moving or editing originals."""
import copy
import unittest
from PySide6.QtCore import Qt, QPoint, QSize
from PySide6.QtTest import QTest
from prompt_calculus_studio import material_navigation as nav, materials, multi_output
from prompt_calculus_studio.material_folder_navigation import ROW
from prompt_calculus_studio.core import Storage
import test_canvas_picker_rev9 as fixture

APP = fixture.APP


class PickerFolderTests(unittest.TestCase):
    setUp = fixture.PickerRev9Tests.setUp
    tearDown = fixture.PickerRev9Tests.tearDown
    open = fixture.PickerRev9Tests.open
    select = fixture.PickerRev9Tests.select

    def folders(self):
        state = self.w.state
        self.fid = nav.add_folder(state, '人物')
        self.empty_folder = nav.add_folder(state, '模型與風格')
        self.other_cat = next(m['id'] for m in state['modules'] if m['name'] == '動作')
        nav.move(state, self.cat, self.fid)
        nav.move(state, self.other_cat, self.fid)
        self.p.populate_categories()
        return self.fid

    def row(self, ident):
        return next(self.p.modules.item(i) for i in range(self.p.modules.count())
                    if self.p.modules.item(i).data(Qt.ItemDataRole.UserRole) == ident)

    def ids(self):
        return [self.p.assets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.p.assets.count())]

    def click_row(self, ident):
        row = self.row(ident); self.p.modules.scrollToItem(row); APP.processEvents()
        rect = self.p.modules.regions(self.p.modules.visualItemRect(row), row.data(ROW))[1]
        QTest.mouseClick(self.p.modules.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        APP.processEvents()

    def fold(self, ident):
        row = self.row(ident)
        arrow = self.p.modules.regions(self.p.modules.visualItemRect(row), row.data(ROW))[0]
        QTest.mouseClick(self.p.modules.viewport(), Qt.MouseButton.LeftButton, pos=arrow.center())
        APP.processEvents()

    def test_editor_order_folder_aggregate_search_empty_and_read_only(self):
        self.folders(); self.p.reject(); self.w.leave_canvas()
        from prompt_calculus_studio.material_editor_browser import TYPE_SHORTCUTS
        # Type shortcuts belong to the full library. The picker keeps its
        # all-material view and the shared editable category/folder ordering.
        expected = [key for key in self.w.prompt_editor.categories.order_ids()
                    if key is None or key not in TYPE_SHORTCUTS]
        self.w.enter_canvas(); self.open(); before = self.c.history_state()
        self.assertEqual(self.p.modules.order_ids(), expected)
        self.click_row(self.fid)
        expected = {v['id'] for v in self.w.state['items'] if v['module'] in (self.cat, self.other_cat)}
        self.assertEqual(set(self.ids()), expected)
        self.assertEqual(self.row(self.fid).data(ROW)['count'], len(expected))
        self.assertEqual(self.p.assets_heading.text(), f'人物 · {len(expected)} 個素材')
        self.p.search.setText('rev9唯一別名'); self.assertEqual(self.ids(), [self.ident])
        self.p.search.clear(); self.click_row(self.empty_folder)
        self.assertEqual(self.p.empty.text(), '此資料夾尚無素材')
        self.assertTrue(self.p.new_button.isEnabled())
        self.assertFalse(self.p.modules.dragEnabled()); self.assertFalse(self.p.modules.acceptDrops())
        self.assertFalse(self.p.modules.manage)
        self.assertEqual(self.c.history_state(), before)
        self.asset_api.assert_not_called()

    def test_disclosure_keeps_child_filter_selection_and_persists_without_undo(self):
        self.folders(); self.click_row(self.cat); self.p.assets.setCurrentRow(0)
        before = self.c.history_state(); undo = len(self.c.undo_stack)
        self.fold(self.fid)
        self.assertNotIn(self.cat, self.p.modules.order_ids())
        self.assertEqual(self.p.get_browser_category(), self.cat)
        self.assertEqual(self.ids(), [self.ident])
        self.assertEqual(self.p.selected_id(self.p.assets), self.ident)
        self.assertEqual(self.p.destination_id, self.a)
        self.assertEqual(len(self.c.undo_stack), undo); self.assertEqual(self.c.history_state(), before)
        self.w.persist(); self.p.reject(); self.w.state = self.w.store.load(); self.open()
        self.assertIn(self.fid, self.w.state['settings']['material_folder_collapsed'])
        self.assertNotIn(self.cat, self.p.modules.order_ids())
        self.assertEqual(self.p.get_browser_category(), self.cat); self.assertEqual(self.ids(), [self.ident])
        self.click_row(self.fid); self.p.modules.setFocus()
        QTest.keyClick(self.p.modules, Qt.Key.Key_Right)
        self.assertIn(self.cat, self.p.modules.order_ids()); self.assertTrue(self.p.modules.hasFocus())
        QTest.keyClick(self.p.modules, Qt.Key.Key_Left)
        self.assertNotIn(self.cat, self.p.modules.order_ids()); self.assertEqual(self.p.get_browser_category(), self.fid)

    def test_narrow_combo_keeps_folder_path_and_collapsed_children_reachable(self):
        self.folders(); self.fold(self.fid)
        self.w.resize(640, 480); self.p.sheet_size = QSize(900, 660); self.p.fit_window(); APP.processEvents()
        self.assertTrue(self.p.narrow_categories.isVisible()); self.assertFalse(self.p.category_pane.isVisible())
        index = self.p.category.findData(self.cat)
        self.assertIn('人物 / 服裝', self.p.category.itemText(index))
        self.p.category.setCurrentIndex(index)
        self.assertEqual(self.p.get_browser_category(), self.cat); self.assertEqual(self.ids(), [self.ident])
        self.p.category.setCurrentIndex(self.p.category.findData(self.fid))
        self.assertIn(self.ident, self.ids()); self.assertEqual(self.p.get_browser_category(), self.fid)
        for widget in (self.p.category, self.p.search, self.p.new_button):
            self.assertTrue(self.p.surface.rect().contains(widget.mapTo(self.p.surface, widget.rect().bottomRight())))
        self.assertEqual(self.p.modules.horizontalScrollBar().maximum(), 0)
        self.asset_api.assert_not_called()

    def test_insert_from_folder_keeps_destination_original_and_undo_save_reopen(self):
        self.folders(); self.click_row(self.fid); self.p.search.setText('rev9唯一別名')
        source = copy.deepcopy(materials.item(self.w.state, self.ident)); before = self.c.history_state()
        self.c.insertion_canvas = self.b
        item = self.p.assets.item(0)
        QTest.mouseClick(self.p.assets.viewport(), Qt.MouseButton.LeftButton, pos=self.p.assets.visualItemRect(item).center())
        APP.processEvents(); self.assertTrue(self.p.inserted); self.assertEqual(self.asset_api.call_count, 1)
        after = self.c.history_state(); key = (set(self.w.state['uses'])-set(before.get('uses', {}))).pop()
        self.assertEqual(multi_output.owner(self.w.state, key), self.a)
        self.assertEqual(self.w.state['uses'][key]['source_id'], self.ident)
        self.assertEqual({k:v for k,v in materials.item(self.w.state, self.ident).items() if k != 'last_used'},
                         {k:v for k,v in source.items() if k != 'last_used'})
        self.c.undo(); self.assertEqual(self.c.history_state(), before)
        self.c.redo(); self.assertEqual(self.c.history_state(), after)
        self.w.persist(); store = Storage(self.temp.name)
        try:
            loaded = store.load(); self.assertEqual(multi_output.owner(loaded, key), self.a)
            self.assertEqual(loaded[nav.FIELD], self.w.state[nav.FIELD])
        finally: store.close()

    def test_editor_folder_selection_and_changes_are_reflected_on_reopen(self):
        self.folders(); self.p.reject(); self.w.leave_canvas()
        page = self.w.prompt_editor; page.set_browser_category(self.fid); self.w.enter_canvas()
        self.open(); self.assertEqual(self.p.get_browser_category(), self.fid)
        self.assertIn(self.ident, self.ids())
        self.p.reject(); nav.rename_folder(self.w.state, self.fid, '人物素材'); self.open()
        self.assertIn('人物素材', self.p.assets_heading.text())
        self.p.reject(); nav.remove_folder(self.w.state, self.fid); self.open()
        self.assertIsNone(self.p.get_browser_category())
        self.assertIn(self.cat, self.p.modules.order_ids()); self.assertIn(self.ident, self.ids())


if __name__ == '__main__': unittest.main()
