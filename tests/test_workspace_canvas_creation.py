"""Nonblocking material selection and immediate creation at the content root."""
import copy
import unittest
from pathlib import Path
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QDropEvent
from PySide6.QtGui import QInputMethodEvent
from PySide6.QtTest import QTest
from prompt_calculus_studio.workspace_locations import path_key
from prompt_calculus_studio.workspace_commands import PATH_ROLE
import test_workspace_panes as fixture


class CanvasCreationTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    def setUp(self):
        fixture.WorkspacePaneTests.setUp(self)
        self.w.activateWindow()
        self.events()
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def picker(self):
        tab = self.open('Street.pcs-workflow')
        canvas = tab.host.canvas
        key = next(iter(canvas.data()['canvases']))
        picker = canvas.quick_text(key, QPointF(480, 260))
        self.events()
        self.assertTrue(picker.isVisible())
        self.assertIsNone(self.app.activeModalWidget())
        return tab, picker

    def test_picker_sidebar_click_closes_and_navigates_once(self):
        self.w.sidebar_preference = True
        self.w.sync_sidebar()
        tab, picker = self.picker()
        state = copy.deepcopy(tab.host.state)
        count = self.w.tab_strip.bar.count()
        item = self.w.path_items[path_key(self.root/'Notes.md')]
        QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.LeftButton,
                        pos=self.w.tree.visualItemRect(item).center())
        self.events()
        self.assertIsNone(tab.host.active_material_picker)
        self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, self.root/'Notes.md')
        self.assertEqual(self.w.tab_strip.bar.count(), count)
        self.assertEqual(tab.host.state, state)

    def test_picker_blank_and_escape_do_not_insert_or_reopen(self):
        tab, picker = self.picker()
        state = copy.deepcopy(tab.host.state)
        QTest.mouseClick(picker, Qt.MouseButton.LeftButton, pos=QPoint(3, 3))
        self.events()
        self.assertIsNone(tab.host.active_material_picker)
        self.assertEqual(tab.host.state, state)
        tab, picker = self.picker()
        QTest.keyClick(picker.search, Qt.Key.Key_Escape)
        self.events()
        self.assertIsNone(tab.host.active_material_picker)

    def test_picker_keeps_hover_sidebar_available(self):
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        self.w.set_sidebar_hover(True, 100, 150)
        tab, picker = self.picker()
        QTest.mouseMove(self.w, QPoint(self.w.width()-50, 250))
        self.events()
        QTest.mouseMove(self.w, QPoint(4, 200))
        for _ in range(70):
            QTest.qWait(10)
            if self.w.sidebar_peek.is_open:
                break
        self.assertTrue(self.w.sidebar_peek.is_open)
        self.assertTrue(picker.isVisible())
        item = self.w.path_items[path_key(self.root/'Notes.md')]
        QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.LeftButton,
                        pos=self.w.tree.visualItemRect(item).center())
        self.events()
        self.assertIsNone(tab.host.active_material_picker)
        self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, self.root/'Notes.md')

    def test_material_insert_keeps_owner_undo_and_save_reopen(self):
        tab, picker = self.picker()
        canvas = tab.host.canvas
        picker.import_category(self.root/'Scene.pcs-category')
        before = canvas.history_state()
        ident = tab.host.state['items'][0]['id']
        category = tab.host.state['items'][0]['module']
        picker.set_browser_category(category)
        self.assertTrue(picker.restore_selection(picker.assets, ident))
        picker.insert()
        self.events()
        self.assertIsNone(tab.host.active_material_picker)
        after = canvas.history_state()
        self.assertNotEqual(before, after)
        canvas.undo()
        self.assertEqual(canvas.history_state(), before)
        canvas.redo()
        self.assertEqual(canvas.history_state(), after)
        self.assertTrue(self.w.save_tab(tab))
        path = tab.path
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='discard'))
        reopened = self.w.open_path(path)
        self.events()
        self.assertEqual(reopened.host.canvas.history_state(), after)

    def test_initial_root_is_implicit_and_external_roots_remain_named(self):
        self.assertNotIn(path_key(self.root), self.w.path_items)
        item = self.w.path_items[path_key(self.root/'Notes.md')]
        self.assertIsNone(item.parent())
        self.assertEqual(item.data(0, PATH_ROLE), str(self.root/'Notes.md'))
        external = Path(self.area.name)/'external'
        external.mkdir()
        self.assertTrue(self.w.add_root(external))
        self.assertEqual(self.w.path_items[path_key(external)].text(0), 'external')
        self.w.tree.setCurrentItem(None)
        self.assertEqual(self.w.commands.selected_context().destination, self.root)

    def test_new_entries_exist_before_optional_name_and_number_collisions(self):
        self.w.show_empty_folders = False
        for kind, suffix in [('folder', ''), ('markdown', '.md'), ('category', '.pcs-category'), ('workflow', '.pcs-workflow')]:
            with self.subTest(kind=kind):
                for number in range(3):
                    self.w.tree.setCurrentItem(None)
                    editor = self.w.commands.new(kind, self.w.commands.selected_context())
                    name = '未命名'+(f'({number})' if number else '')
                    target = self.root/(name+suffix)
                    self.assertTrue(target.exists(), target)
                    self.assertEqual(editor.name.text(), name)
                    if number == 1:
                        editor.name.clear()
                        editor.submit()
                    else:
                        QTest.keyClick(editor.name, Qt.Key.Key_Escape)
                    self.events()
                    self.assertTrue(target.exists())
                    self.assertIsNone(self.w.commands.files.inline)
                self.assertIsNone(self.w.path_items[path_key(target)].parent())

    def test_creation_optional_rename_and_focus_loss_preserve_file(self):
        self.w.tree.setCurrentItem(None)
        editor = self.w.commands.new('markdown', self.w.commands.selected_context())
        editor.name.setText('Named')
        editor.submit()
        self.events()
        self.assertTrue((self.root/'Named.md').is_file())
        self.assertFalse((self.root/'未命名.md').exists())
        self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, self.root/'Named.md')
        self.w.tree.setCurrentItem(None)
        editor = self.w.commands.new('folder', self.w.commands.selected_context())
        self.w.tree.setFocus()
        self.events()
        self.assertTrue((self.root/'未命名').is_dir())
        self.assertIsNone(self.w.commands.files.inline)

    def test_optional_rename_preserves_original_on_collision_and_waits_for_ime(self):
        (self.root/'Existing.md').write_text('original', encoding='utf-8')
        self.w.tree.setCurrentItem(None)
        editor = self.w.commands.new('markdown', self.w.commands.selected_context())
        editor.name.setText('Existing')
        editor.submit()
        self.assertTrue(editor.error.isVisible())
        self.assertEqual((self.root/'Existing.md').read_text('utf-8'), 'original')
        self.assertTrue((self.root/'未命名.md').exists())
        editor.name.setText('Changed')
        self.app.sendEvent(editor.name, QInputMethodEvent('組字', []))
        QTest.keyClick(editor.name, Qt.Key.Key_Return)
        self.assertFalse((self.root/'Changed.md').exists())
        self.app.sendEvent(editor.name, QInputMethodEvent())
        QTest.keyClick(editor.name, Qt.Key.Key_Return)
        self.events()
        self.assertTrue((self.root/'Changed.md').exists())
        self.assertIsNone(self.w.commands.files.inline)

    def test_drag_folder_to_blank_area_moves_back_to_content_root(self):
        nested = self.root/'Folder'/'Child'
        nested.mkdir()
        (nested/'note.md').write_text('kept', encoding='utf-8')
        self.w.refresh_tree()
        self.w.select_path(nested)
        drag = self.w.tree.file_drag
        self.assertTrue(drag.begin())
        try:
            point = QPoint(60, self.w.tree.viewport().height()-10)
            self.assertIsNone(self.w.tree.itemAt(point))
            self.assertEqual(drag.destination(point), self.root)
            tree = self.w.tree
            class Drop(QDropEvent):
                def source(self):
                    return tree
            mime = drag.mime()
            event = Drop(QPointF(point), Qt.DropAction.MoveAction, mime,
                               Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            drag.drop(event)
            self.assertTrue(event.isAccepted())
        finally:
            drag.finish()
        self.events()
        self.assertFalse(nested.exists())
        self.assertEqual((self.root/'Child'/'note.md').read_text('utf-8'), 'kept')
        self.assertIsNone(self.w.path_items[path_key(self.root/'Child')].parent())


if __name__ == '__main__':
    unittest.main()
