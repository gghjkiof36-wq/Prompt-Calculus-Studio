"""Category menus preserve library content and history across native Qt actions."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QTimer, QCoreApplication, QEvent
from PySide6.QtGui import QColor, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLineEdit, QPushButton
import test_editor_folders as fixture
from prompt_calculus_studio import materials, material_navigation as nav
from prompt_calculus_studio.material_folder_navigation import ROW
from prompt_calculus_studio.core import validate_state, initial_state, Storage
from prompt_calculus_studio.multi_output import capture_current
from prompt_calculus_studio.theme import visual_tokens
from prompt_calculus_studio.color_roles import contrast


class CategoryActionsTests(unittest.TestCase):
    setUp = fixture.EditorFolderTests.setUp
    tearDown = fixture.EditorFolderTests.tearDown
    row = fixture.EditorFolderTests.row
    create = fixture.EditorFolderTests.create
    move_menu = fixture.EditorFolderTests.move_menu

    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')

    def menu(self, ident):
        listing = self.page.categories; item = self.row(ident)
        listing.scrollToItem(item); self.app.processEvents()
        more = listing.regions(listing.visualItemRect(item), item.data(ROW))[3]
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=more.center())
        menu = QApplication.activePopupWidget(); self.assertIsNotNone(menu)
        return menu

    def choose(self, ident, text, *, name=None, accept=True, keyboard=False):
        menu = self.menu(ident); action = next(a for a in menu.actions() if a.text() == text)
        answered = []
        def reply():
            dialog = QApplication.activeModalWidget()
            if dialog is None: return
            try:
                if name is not None:
                    editor = dialog.findChild(QLineEdit); self.assertIsNotNone(editor)
                    editor.selectAll(); editor.insert(name)
                target = next(b for b in dialog.findChildren(QPushButton) if b.text() == ('確定' if accept else '取消'))
                answered.append(dialog.windowTitle()); QTest.mouseClick(target, Qt.MouseButton.LeftButton)
            except BaseException:
                dialog.reject(); raise
        QTimer.singleShot(30, reply)
        if keyboard:
            menu.setActiveAction(action); QTest.keyClick(menu, Qt.Key.Key_Return)
        else:
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(action).center())
        QTest.qWait(40); self.assertEqual(len(answered), 1)

    def test_rename_from_menu_keeps_id_membership_and_can_undo_redo(self):
        fid = self.create(); self.move_menu(self.cat, fid); self.page.set_browser_category(self.cat)
        original = copy.deepcopy(self.w.state['items']); order = [v['id'] for v in self.w.state['modules']]
        with patch.object(self.w.comfy.input_flow.chain, 'route_changed') as route:
            self.choose(self.cat, '重新命名', name='新角色分類')
            self.assertEqual(materials.category(self.w.state, self.cat)['name'], '新角色分類')
            self.assertEqual(self.page.title.text(), '新角色分類')
            self.w.canvas.undo(); self.assertEqual(materials.category(self.w.state, self.cat)['name'], 'Order A')
            self.w.canvas.redo(); self.assertEqual(materials.category(self.w.state, self.cat)['name'], '新角色分類')
            route.assert_not_called()
        self.assertEqual(nav.parent(self.w.state, self.cat), fid)
        self.assertEqual([v['id'] for v in self.w.state['modules']], order)
        self.assertEqual(self.w.state['items'], original)
        self.w.persist(); self.assertEqual(self.w.store.load()['modules'], self.w.state['modules'])

    def test_delete_category_cancel_accept_undo_redo_and_reopen_preserves_content(self):
        fid = self.create(); self.move_menu(self.cat, fid); self.page.set_browser_category(self.cat)
        self.w.state.update(draft='', draft_base='manual base')
        # Seed the active output projection as well as the root draft before
        # comparing a library-only transaction against its original state.
        capture_current(self.w.state)
        before = copy.deepcopy(self.w.state); undo = len(self.w.canvas.undo_stack)
        self.choose(self.cat, '刪除分類', accept=False)
        self.assertEqual(self.w.state['modules'], before['modules']); self.assertEqual(len(self.w.canvas.undo_stack), undo)
        with patch.object(self.w.comfy.input_flow.chain, 'route_changed') as route:
            self.choose(self.cat, '刪除分類', keyboard=True)
            self.assertNotIn(self.cat, [v['id'] for v in self.w.state['modules']])
            destination = next(v['id'] for v in self.w.state['modules'] if v['name'] == '未分類')
            expected = copy.deepcopy(before['items'])
            for value in expected:
                if value['module'] == self.cat: value['module'] = destination
            self.assertEqual(self.w.state['items'], expected)
            self.assertEqual(self.page.get_browser_category(), destination)
            self.assertEqual(set(self.page.listing.order_ids()), {self.ids[0], self.ids[2], self.ids[4]})
            self.assertEqual(nav.folder(self.w.state, fid)['categories'], [])
            self.w.canvas.undo()
            self.assertEqual(self.w.state['modules'], before['modules']); self.assertEqual(self.w.state['items'], before['items'])
            self.assertEqual(nav.parent(self.w.state, self.cat), fid)
            self.w.canvas.redo(); route.assert_not_called()
        for field in ('uses', 'draft', 'draft_base', 'multi_output'):
            self.assertEqual(self.w.state.get(field), before.get(field))
        self.w.persist(); validate_state(self.w.store.load())
        old = self.w; old.close(); old.deleteLater(); self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.w = fixture.fixture.Window(self.folder.name); self.w.show(); self.w.leave_canvas()
        self.page = self.w.prompt_editor; self.app.processEvents()
        self.assertEqual(self.w.state['items'], expected); self.assertEqual(self.w.state['draft'], '')

    def test_folder_delete_menu_retains_children_and_undo(self):
        fid = self.create(); self.move_menu(self.cat, fid)
        before = copy.deepcopy(self.w.state['items'])
        self.choose(fid, '刪除資料夾', accept=False); self.assertIsNotNone(nav.folder(self.w.state, fid))
        self.choose(fid, '刪除資料夾'); self.assertIsNone(nav.folder(self.w.state, fid))
        self.assertIsNone(nav.parent(self.w.state, self.cat)); self.assertEqual(self.w.state['items'], before)
        self.w.canvas.undo(); self.assertEqual(nav.parent(self.w.state, self.cat), fid)
        self.w.canvas.redo(); self.assertIsNone(nav.folder(self.w.state, fid))

    def test_red_labels_idle_and_active_in_all_themes_and_narrow_menu(self):
        for palette in ('graphite', 'mist', 'paper'):
            self.w.state['settings']['visual_palette'] = palette
            self.w.apply_theme(preserve_layout=True); self.page.refresh()
            menu = self.menu(self.cat)
            action = next(a for a in menu.actions() if a.text() == '刪除分類')
            caption = action.defaultWidget(); tokens = visual_tokens(self.w.state['settings'])
            from PySide6.QtGui import QPalette
            self.assertEqual(menu._surface.surface_palette().color(QPalette.ColorRole.ToolTipBase).name(), tokens['raised'])
            self.assertGreaterEqual(contrast(tokens['destructive_text'], tokens['raised']), 4.5)
            self.assertGreaterEqual(contrast(tokens['destructive_text'], tokens['popup_active']), 4.5)
            for active in (False, True):
                menu.setActiveAction(action if active else menu.actions()[0]); self.app.processEvents()
                image = caption.grab().toImage(); target = QColor(tokens['destructive_text'])
                colors = {image.pixelColor(x, y).name() for x in range(image.width()) for y in range(image.height())}
                self.assertTrue(any(sum(abs(a-b) for a,b in zip(QColor(c).getRgb()[:3], target.getRgb()[:3])) < 24 for c in colors), (palette, active))
            QTest.keyClick(menu, Qt.Key.Key_Escape)
        self.w.resize(760, 700); self.page.set_browser_category(self.cat); self.app.processEvents()
        QTest.mouseClick(self.page.category_more, Qt.MouseButton.LeftButton)
        menu = QApplication.activePopupWidget()
        self.assertEqual([a.text() for a in menu.actions() if not a.isSeparator()], ['重新命名', '移至資料夾', '刪除分類'])
        QTest.keyClick(menu, Qt.Key.Key_Escape)

    def test_deletion_undo_restores_old_workspace_selection_references(self):
        from prompt_calculus_studio.workspace_scene import capture
        state = self.w.state
        state['selections'] = {self.cat: [self.ids[0]]}; state['temporary_before'] = self.cat
        workspace = state['workspaces'][0]
        workspace['picks'] = {self.cat: [self.ids[0]]}; workspace['fixed'] = [self.cat]
        from prompt_calculus_studio.composition import prune_instances
        from prompt_calculus_studio.multi_output import capture_current
        prune_instances(state); capture_current(state)
        capture(state); validate_state(state)
        before = copy.deepcopy(state)
        self.choose(self.cat, '刪除分類')
        validate_state(self.w.state)
        self.w.canvas.undo(); validate_state(self.w.state)
        for field in ('modules', 'items', 'workspaces', 'workspace_scenes', 'multi_output', 'temporary_before', 'selections'):
            self.assertEqual(self.w.state.get(field), before.get(field), field)
        self.w.canvas.redo(); self.w.persist(); validate_state(self.w.store.load())


class CategoryModelTests(unittest.TestCase):
    def test_protected_category_duplicate_and_blank_name_do_not_change_state(self):
        state = initial_state(); ident = state['modules'][0]['id']; destination = materials.add_category(state, '未分類')
        before = copy.deepcopy(state)
        for name in ('', ' ', '未分類', state['modules'][1]['name']):
            with self.assertRaises(ValueError): materials.rename_category(state, ident, name)
            self.assertEqual(state, before)
        with self.assertRaises(ValueError): materials.rename_category(state, destination, 'Other')
        with self.assertRaises(ValueError): materials.remove_category(state, destination)
        self.assertEqual(state, before)

    def test_deleted_category_references_in_saved_workspaces_survive_storage(self):
        import tempfile
        from prompt_calculus_studio import workspace_scene
        state = initial_state(); ident = state['modules'][2]['id']
        chosen = [v['id'] for v in state['items'] if v['module'] == ident][:1]
        state['selections'] = {ident: chosen}; state['temporary_before'] = ident; state['output_order'] = [ident]
        workspace = state['workspaces'][0]; workspace['fixed'] = [ident]; workspace['picks'] = {ident: chosen}
        workspace['canvas_owners'] = {ident: None}
        workspace_scene.upgrade(state); before = copy.deepcopy(state); validate_state(state)
        destination = materials.remove_category(state, ident); validate_state(state)
        self.assertEqual(state['selections'][destination], chosen)
        self.assertEqual(workspace['picks'][destination], chosen); self.assertEqual(workspace['fixed'], [destination])
        saved = state['workspace_scenes']['items'][workspace['id']]
        self.assertEqual(saved['selections'][destination], chosen); self.assertEqual(saved['temporary_before'], destination)
        self.assertEqual(len(state['items']), len(before['items']))
        with tempfile.TemporaryDirectory() as directory:
            store = Storage(directory); store.save(state)
            self.assertEqual(store.load(), state); store.db.close()


if __name__ == '__main__': unittest.main()
