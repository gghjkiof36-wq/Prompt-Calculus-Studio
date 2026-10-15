"""Type shortcuts are library views, not stored categories or picker restrictions."""
import copy
import unittest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtWidgets import QApplication
import test_editor_reorder as fixture
from prompt_calculus_studio import materials, material_navigation as nav, prompt_materials as pm
from prompt_calculus_studio.material_editor_browser import TYPE_SHORTCUTS
from prompt_calculus_studio.material_folder_navigation import ROW


class MaterialTypeNavigationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.EditorReorderTests.setUpClass.__func__)
    tearDown = fixture.EditorReorderTests.tearDown

    def setUp(self):
        fixture.EditorReorderTests.setUp(self)
        self.combination_category = materials.add_category(self.w.state, '組合')
        self.flow_category = materials.add_category(self.w.state, '流程')
        self.combination = self.save('combination', 'Synthetic combination', self.combination_category)
        self.flow = self.save('flow', 'Synthetic flow', self.flow_category)
        self.named_flow_text = self.save('text', '名字叫流程的文字', self.cat)
        self.page.refresh(); self.w.persist()

    def save(self, kind, name, category):
        root = pm.new(kind, name)
        if kind == 'text': root['prompt'] = 'synthetic text'
        return materials.save(self.w.state, dict(name=name, prompt='', composition=root, module=category))

    def entries(self):
        return [self.page.categories.item(i).data(ROW) for i in range(self.page.categories.count())]

    def test_fixed_types_use_actual_kind_and_counts_without_data_mutation(self):
        before = copy.deepcopy(self.w.state)
        self.assertEqual([row['name'] for row in self.entries()[:3]], ['文字模塊', '組合', '流程'])
        for index, (shortcut, kind) in enumerate(TYPE_SHORTCUTS.items()):
            with self.subTest(kind=kind):
                self.page.set_browser_category(shortcut)
                expected = {v['id'] for v in self.w.state['items'] if pm.kind(v) == kind}
                self.assertEqual(set(self.page.listing.order_ids()), expected)
                self.assertEqual(self.entries()[index]['count'], len(expected))
                self.assertEqual(self.page.title.text(), pm.TYPES[kind])
                self.assertTrue(self.page.type_filter.isHidden())
        self.assertEqual(self.w.state, before)

    def test_hide_only_dedicated_same_name_categories_and_map_old_selection(self):
        before = copy.deepcopy(self.w.state)
        self.assertNotIn(self.flow_category, [r['id'] for r in self.entries()])
        self.assertNotIn(self.combination_category, [r['id'] for r in self.entries()])
        self.page.set_browser_category(self.flow_category)
        self.assertEqual(self.page.get_browser_category(), '@material-type:flow')
        self.assertEqual(self.page.listing.order_ids(), [self.flow])
        self.assertEqual(self.w.state, before)
        extra = self.save('text', '分類保留的文字', self.flow_category)
        self.page.refresh()
        self.assertIn(self.flow_category, [r['id'] for r in self.entries()])
        self.page.set_browser_category(self.flow_category)
        self.assertEqual(set(self.page.listing.order_ids()), {self.flow, extra})
        self.assertFalse(self.page.type_filter.isHidden())

    def test_empty_type_entry_names_the_selected_type(self):
        self.w.state['items'] = []; self.page.refresh()
        for shortcut, kind in TYPE_SHORTCUTS.items():
            self.page.set_browser_category(shortcut)
            self.assertEqual(self.page.empty_results.text(), '還沒有'+pm.TYPES[kind]+'，可按「新增」開始。')
            self.assertFalse(self.page.empty_results.isHidden())
        self.page.set_browser_category(self.cat)
        self.assertEqual(self.page.empty_results.text(), '這個分類還沒有素材，可按「新增」開始。')

    def test_shortcut_search_pin_and_category_filter_reset(self):
        self.page.set_browser_category(self.cat)
        self.page.type_filter.setCurrentIndex(self.page.type_filter.findData('flow'))
        self.page.set_browser_category(None)
        self.assertIsNone(self.page.type_filter.currentData())
        self.page.search.setText('名字叫流程')
        self.assertEqual(self.page.listing.order_ids(), [self.named_flow_text])
        self.page.pins.setChecked(True); self.assertEqual(self.page.listing.count(), 0)
        materials.item(self.w.state, self.named_flow_text)['pinned'] = True
        self.page.refresh(); self.assertEqual(self.page.listing.order_ids(), [self.named_flow_text])
        self.page.search.clear(); self.page.pins.setChecked(False)
        self.page.set_browser_category('@material-type:flow'); self.page.refresh()
        self.assertEqual(self.page.get_browser_category(), '@material-type:flow')
        self.assertEqual(self.page.listing.order_ids(), [self.flow])
        self.page.set_browser_category(self.cat)
        self.assertIsNone(self.page.type_filter.currentData())
        self.assertFalse(self.page.type_filter.isHidden())

    def test_fixed_types_reject_category_operations_and_hidden_slots_survive_reorder(self):
        listing = self.page.categories; before = copy.deepcopy(self.w.state)
        for shortcut in TYPE_SHORTCUTS:
            self.page.set_browser_category(shortcut); self.app.processEvents()
            row = next(listing.item(i) for i in range(listing.count()) if listing.item(i).data(ROW)['id'] == shortcut)
            listing.scrollToItem(row); self.app.processEvents()
            self.assertFalse(row.flags() & Qt.ItemFlag.ItemIsDragEnabled)
            self.assertIsNone(listing.destination(listing.visualItemRect(row).center()))
            self.assertFalse(self.page.category_more.isEnabled())
            self.assertIsNone(self.page.category_row_menu(shortcut, QPoint()))
        self.assertEqual(self.w.state, before)
        original = listing.order_ids(); reordered = list(original)
        a, b = reordered.index(self.cat), reordered.index(self.other_cat)
        reordered[a], reordered[b] = reordered[b], reordered[a]
        root = nav.view(self.w.state)['order']; hidden_positions = {k: root.index(k) for k in (self.flow_category, self.combination_category)}
        self.page._reorder_categories(original, reordered)
        result = nav.view(self.w.state)['order']
        self.assertEqual(result.index(self.cat), root.index(self.other_cat))
        for key, position in hidden_positions.items(): self.assertEqual(result.index(key), position)
        self.assertEqual(self.w.state['items'], before['items'])
        self.assertEqual(self.w.state['modules'], before['modules'])

    def test_create_from_type_entry_presets_type_and_cancel_preserves_originals(self):
        before = self.w.canvas.history_projection(self.w.state)
        for shortcut, kind in TYPE_SHORTCUTS.items():
            with self.subTest(kind=kind):
                self.page.set_browser_category(shortcut)
                dialog = self.page.new_material(); self.app.processEvents()
                self.assertEqual(dialog.pages.material_type, kind)
                self.assertNotIn(dialog.form.edit_category.currentData(), [key for key in TYPE_SHORTCUTS if key is not None])
                dialog.reject(); self.app.processEvents()
        self.assertEqual(self.w.canvas.history_projection(self.w.state), before)

    def test_saving_supplied_prompt_remains_text_regardless_of_browser_entry(self):
        self.page.set_browser_category('@material-type:flow')
        dialog = self.page.new_material(text='output prompt')
        self.assertEqual(dialog.pages.material_type, 'text')
        self.assertEqual(dialog.prompt.toPlainText(), 'output prompt')
        dialog.reject(); self.app.processEvents()

    def test_canvas_picker_retains_all_types_and_ordinary_category_rows(self):
        from prompt_calculus_studio.canvas_palette import CanvasPalette
        self.page.set_browser_category('@material-type:flow')
        self.w.enter_canvas(); self.app.processEvents()
        picker = CanvasPalette(self.w.canvas, QPoint())
        try:
            picker.set_browser_category(None)
            self.assertEqual(set(picker.assets.item(i).data(Qt.ItemDataRole.UserRole) for i in range(picker.assets.count())),
                             {v['id'] for v in self.w.state['items']})
            keys = [picker.modules.item(i).data(ROW)['id'] for i in range(picker.modules.count())]
            self.assertIn(self.flow_category, keys); self.assertIn(self.combination_category, keys)
            self.assertNotIn('@material-type:flow', keys)
        finally: picker.reject(); picker.deleteLater(); self.app.processEvents()

    def test_type_create_save_reopen_undo_redo_uses_real_or_uncategorized_membership(self):
        # The combination has a dedicated category; the flow intentionally has none.
        materials.remove_category(self.w.state, self.flow_category); self.page.refresh()
        for kind in ('combination', 'flow'):
            with self.subTest(kind=kind):
                shortcut = '@material-type:'+kind; self.page.set_browser_category(shortcut)
                dialog = self.page.new_material(); dialog.name.setText('Saved '+kind)
                editor = dialog.pages.group_editor
                if kind == 'flow': editor.edit_group(editor.sequence['groups'][0]['id'])
                editor.input_tabs.setCurrentIndex(1); editor.direct_text.setPlainText('synthetic '+kind)
                if kind == 'flow': editor.back()
                ident = dialog.form.base['id']
                self.assertTrue(dialog.save(), dialog.error.text()); self.app.processEvents()
                saved = copy.deepcopy(materials.item(self.w.state, ident))
                self.assertEqual(pm.kind(saved), kind)
                expected_category = self.combination_category if kind == 'combination' else next(v['id'] for v in self.w.state['modules'] if v['name'] == '未分類')
                self.assertEqual(saved['module'], expected_category)
                self.assertEqual(self.page.get_browser_category(), shortcut)
                self.assertIn(ident, self.page.listing.order_ids())
                reopened = self.page.edit_material(ident); self.app.processEvents()
                self.assertEqual(reopened.pages.material_type, kind)
                self.assertEqual(reopened._value()['prompt'], 'synthetic '+kind)
                reopened.reject(); self.app.processEvents()
                self.w.canvas.undo(); self.app.processEvents()
                self.assertNotIn(ident, [v['id'] for v in self.w.state['items']])
                self.w.canvas.redo(); self.app.processEvents()
                self.assertEqual(materials.item(self.w.state, ident), saved)
                self.assertIn(ident, self.page.listing.order_ids())


if __name__ == '__main__': unittest.main()
