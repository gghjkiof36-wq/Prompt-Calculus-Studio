"""P3-A: real creation controls, explicit locations and protected editors."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from shiboken6 import isValid

from PySide6.QtCore import Qt, QTimer, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QMessageBox, QFileDialog, QStyle, QStyleOptionViewItem

from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_commands import NewEntryDialog, PATH_ROLE
from prompt_calculus_studio.workspace_locations import WorkspaceLocations, path_key
from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.local_documents import io


class WorkspaceFileCommandsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-p3-a-')
        self.base = Path(self.area.name)
        self.root = self.base/'content'
        self.folder = self.root/'參考'
        self.folder.mkdir(parents=True)
        self.external = self.base/'external'
        self.external.mkdir()
        self.note = self.root/'Draft.md'
        self.note.write_bytes(b'\xef\xbb\xbf# draft\r\n')
        (self.folder/'Nested.md').write_text('nested', encoding='utf-8')
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.errors.append(str(value))
        self.w = WorkspaceShell(self.root, self.base/'data')
        self.w.report_error = self.errors.append
        self.w.show()
        self.events()

    def events(self):
        self.app.processEvents()
        QTest.qWait(20)
        self.app.processEvents()

    def tearDown(self):
        popup = self.app.activePopupWidget()
        if popup:
            popup.close()
        while self.w.tab_strip.bar.count():
            self.assertTrue(self.w.close_tab(0, decision='discard', make_home=False))
        self.w.close()
        self.w.deleteLater()
        self.events()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def item(self, path):
        return self.w.path_items[path_key(path)]

    def dialog_action(self, action, edit):
        failures = []
        def interact():
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsInstance(dialog, NewEntryDialog)
                edit(dialog)
            except BaseException as error:
                failures.append(error)
                if dialog:
                    dialog.reject()
        QTimer.singleShot(15, interact)
        result = action()
        if failures:
            raise failures[0]
        self.events()
        return result

    def test_all_kinds_and_captured_destinations_preserve_dirty_editor(self):
        tab = self.w.open_path(self.note, True)
        QTest.keyClicks(tab.widget, 'unsaved ')
        original = self.note.read_bytes()
        old_editor = tab.widget
        contexts = [self.w.commands.context('tree', self.item(self.root)),
                    self.w.commands.context('tree', self.item(self.folder)),
                    self.w.commands.context('toolbar', self.item(self.folder/'Nested.md')),
                    self.w.commands.context('home')]
        for number, context in enumerate(contexts):
            expected = self.folder if number in (1, 2) else self.root
            self.assertEqual(context.destination, expected)
            for kind in ('category', 'workflow', 'markdown', 'folder'):
                with self.subTest(context=number, kind=kind):
                    result = self.w.commands.new(kind, context, f'第 {number} 類 {kind}.v1')
                    self.assertIsNotNone(result)
                    path = result if kind == 'folder' else result.path
                    self.assertEqual(path.parent, expected)
                    self.assertTrue(path.exists())
                    if kind == 'folder':
                        self.assertEqual(self.w.tree.currentItem().data(0, PATH_ROLE), str(path))
                        self.assertTrue(self.item(path).isExpanded())
                    else:
                        self.assertTrue(result.retained)
        self.assertIs(tab.widget, old_editor)
        self.assertTrue(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        self.assertEqual(self.note.read_bytes(), original)

    def test_toolbar_folder_menu_and_home_use_one_real_form(self):
        self.w.tree.setCurrentItem(self.item(self.folder))
        self.events()
        QTest.mouseClick(self.w.tree_new, Qt.MouseButton.LeftButton)
        menu = self.app.activePopupWidget()
        self.assertEqual([a.text() for a in menu.actions()], ['分類…', 'Canvas 工作流…', 'Markdown 筆記…'])
        action = menu.actions()[2]
        self.w.tree.setCurrentItem(self.item(self.root))  # Context already captured.
        def create_nested(dialog):
            self.assertEqual(dialog.directory.text(), str(self.folder))
            dialog.name.setText('工具列.md')
            QTest.mouseClick(dialog.submit_button, Qt.MouseButton.LeftButton)
        self.dialog_action(action.trigger, create_nested)
        if isValid(menu):
            menu.close()
        self.assertTrue((self.folder/'工具列.md').exists())
        before = set(self.w.tabs_by_key)
        target = self.item(self.folder)
        self.w.tree_menu(self.w.tree.visualItemRect(target).center())
        self.assertEqual(set(self.w.tabs_by_key), before)  # Right-click never opens.
        menu = self.app.activePopupWidget()
        actions = menu.actions()[0].menu().actions()
        self.assertEqual(len(actions), 4)
        def create_folder(dialog):
            self.assertEqual(dialog.directory.text(), str(self.folder))
            dialog.name.setText('新資料夾')
            dialog.submit()
        self.dialog_action(actions[3].trigger, create_folder)
        if isValid(menu):
            menu.close()
        self.assertEqual(set(self.w.tabs_by_key), before)
        home = self.w.new_tab().widget
        self.w.tree.setCurrentItem(self.item(self.folder))
        def create_home(dialog):
            self.assertEqual(dialog.directory.text(), str(self.root))
            dialog.directory.setText(str(self.external))
            dialog.name.setText('外部.v2')
            dialog.submit()
        self.dialog_action(lambda: QTest.mouseClick(home.create_buttons['category'], Qt.MouseButton.LeftButton), create_home)
        self.assertTrue((self.external/'外部.v2.pcs-category').is_file())
        self.assertNotIn(path_key(self.external), self.w.path_items)

    def test_cancel_invalid_input_and_directory_picker_cancel_leave_no_files(self):
        before = set(self.root.rglob('*')), set(self.w.tabs_by_key)
        def reject(dialog):
            dialog.name.setText('未提交')
            with patch.object(QFileDialog, 'getExistingDirectory', return_value=''):
                dialog.choose_directory()
            self.assertEqual(dialog.directory.text(), str(self.root))
            QTest.keyClick(dialog, Qt.Key.Key_Escape)
        self.assertIsNone(self.dialog_action(lambda: self.w.new_document('markdown'), reject))
        def fix_error(dialog):
            dialog.name.setText('CON')
            dialog.submit()
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.error.text())
            self.assertEqual(dialog.name.text(), 'CON')
            dialog.name.setText('保留輸入')
            dialog.directory.setText('relative/path')
            dialog.submit()
            self.assertTrue(dialog.isVisible())
            dialog.reject()
        self.dialog_action(lambda: self.w.new_document('workflow'), fix_error)
        self.assertEqual((set(self.root.rglob('*')), set(self.w.tabs_by_key)), before)

    def test_io_name_validation_and_case_collision_for_all_four_kinds(self):
        for kind in ('category', 'workflow', 'markdown', 'folder'):
            for name in ('', ' ', 'trailing ', 'trailing.', 'CON', 'NUL.txt', 'LPT1', 'bad:name', 'bad/name', 'bad\\name', '.obsidian', '.pcs', 'assets'):
                with self.subTest(kind=kind, name=name), self.assertRaises(ValueError):
                    create_file(self.root, kind, name)
            path = create_file(self.root, kind, '中文 有空白.' + kind)
            before = path.read_bytes() if path.is_file() else None
            with self.assertRaises((ValueError, OSError)):
                create_file(self.root, kind, path.name.upper())
            if before is not None:
                self.assertEqual(path.read_bytes(), before)
            self.assertNotIn(path.name + path.suffix, [p.name for p in self.root.iterdir()])
        for kind, name in (('markdown', '.md'), ('workflow', '.pcs-workflow'), ('category', '.pcs-category')):
            with self.assertRaises(ValueError):
                create_file(self.root, kind, name)

    def test_external_three_kinds_save_reopen_same_name_and_recent_migration(self):
        local = self.w.new_document('markdown', '同名')
        for kind in ('category', 'workflow', 'markdown'):
            tab = self.w.new_document(kind, '同名', directory=self.external)
            self.assertIsNotNone(tab)
            self.assertEqual(tab.path.parent, self.external)
            self.assertIn(str(self.external), self.w.tab_strip.bar.tabToolTip(self.w.index_of(tab.key)))
            if tab.document:
                host = tab.document.host
                section = 'modules' if kind == 'category' else 'workspaces'
                self.assertTrue(host.canvas.commit(lambda state: state[section][0].update(name='目前編輯')))
                self.assertTrue(tab.dirty)
            else:
                QTest.keyClicks(tab.widget, 'external note')
            self.assertTrue(self.w.save_tab(tab))
            self.assertFalse(tab.dirty)
            path, raw = tab.path, tab.path.read_bytes()
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), 'save'))
            reopened = self.w.open_path(path, True)
            self.assertIsNotNone(reopened)
            self.assertEqual(reopened.path.read_bytes(), raw)
            self.assertNotIn(path_key(path), self.w.path_items)
        self.assertEqual(local.widget.toPlainText(), '')
        self.w.save_recent()
        records = json.loads((self.w.data/'recent-files.json').read_text(encoding='utf-8'))
        self.assertIn(str(self.external/'同名.md'), records)
        page = self.w.new_tab().widget
        entry = next(page.listing.item(i) for i in range(page.listing.count())
                     if page.listing.item(i).data(Qt.ItemDataRole.UserRole) == str(self.external/'同名.md'))
        page.listing.itemActivated.emit(entry)
        self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, self.external/'同名.md')
        restored = self.w.locations.recent(['Draft.md', '../bad', str(self.external/'同名.md'), 'missing.md'])
        self.assertEqual(restored, [str(self.note), str(self.external/'同名.md')])

    def test_explicit_root_links_persist_and_do_not_reset_dirty_tabs(self):
        tab = self.w.open_path(self.note, True)
        QTest.keyClicks(tab.widget, 'retained ')
        order = list(self.w.tabs_by_key)
        (self.external/'Other.md').write_text('other', encoding='utf-8')
        self.assertNotIn(path_key(self.external/'Other.md'), self.w.path_items)
        self.assertTrue(self.w.add_root(self.external))
        self.assertIn(path_key(self.external/'Other.md'), self.w.path_items)
        self.assertEqual(self.w.commands.context('home').destination, self.external)
        self.assertTrue(self.w.remove_root(self.root))
        self.assertTrue(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        self.assertEqual(list(self.w.tabs_by_key), order)
        self.assertTrue(self.note.exists())
        saved = self.w.read_navigation_json('linked-folders.json')
        restored = WorkspaceLocations(self.root, self.w.data, saved)
        self.assertEqual(restored.roots, [self.external])
        self.assertEqual(restored.current, self.external)
        self.assertTrue(self.w.remove_root(self.external))
        self.assertEqual(self.w.tree.topLevelItemCount(), 0)
        self.assertIsNone(self.w.commands.context('home').destination)
        self.assertEqual(WorkspaceLocations(self.root, self.w.data, self.w.read_navigation_json('linked-folders.json')).roots, [])

    def test_external_folder_success_reports_real_path_and_offers_link(self):
        result = []
        def accept_link():
            prompt = self.app.activeModalWidget()
            try:
                self.assertIsInstance(prompt, QMessageBox)
                self.assertIn(str(self.external/'新資料夾'), prompt.text())
                control = next(b for b in prompt.buttons() if b.text() == '加入側欄並定位')
                control.click()
            except BaseException as error:
                result.append(error)
                if prompt:
                    prompt.reject()
        QTimer.singleShot(30, accept_link)
        path = self.w.new_document('folder', '新資料夾', directory=self.external)
        if result:
            raise result[0]
        self.assertEqual(path, self.external/'新資料夾')
        self.assertIn(path, self.w.locations.roots)
        self.assertEqual(self.w.tree.currentItem().data(0, PATH_ROLE), str(path))

    def test_inventory_excludes_private_and_internal_subtrees_before_traversal(self):
        for name in ('.obsidian', '.pcs', 'assets'):
            folder = self.root/name
            folder.mkdir()
            (folder/'Sentinel.md').write_text('private', encoding='utf-8')
        internal = self.root/'runtime'
        internal.mkdir()
        (internal/'Private.md').write_text('private', encoding='utf-8')
        locations = WorkspaceLocations(self.root, internal)
        original = Path.iterdir
        traversed = []
        def spy(path):
            traversed.append(path)
            return original(path)
        with patch.object(Path, 'iterdir', spy):
            found = list(locations.inventory(self.root))
        self.assertNotIn(internal, traversed)
        for name in ('.obsidian', '.pcs', 'assets'):
            self.assertNotIn(self.root/name, traversed)
            with self.assertRaises(ValueError):
                locations.directory(self.root/name)
        self.assertNotIn(internal/'Private.md', [p for p, kind in found])
        with self.assertRaises(ValueError):
            locations.file(internal/'Private.md')

    def test_write_failure_keeps_original_dirty_tab_and_has_no_ghost(self):
        tab = self.w.open_path(self.note, True)
        QTest.keyClicks(tab.widget, 'unsaved ')
        before = self.note.read_bytes(), list(self.w.tabs_by_key), set(self.w.path_items)
        def fail(stage, *args):
            if stage == 'before-create':
                raise PermissionError('injected destination lock')
        with patch.object(io, '_checkpoint', fail):
            self.assertIsNone(self.w.new_document('workflow', 'Failure'))
        self.assertIn('injected destination lock', self.errors.pop())
        self.assertFalse((self.root/'Failure.pcs-workflow').exists())
        self.assertEqual((self.note.read_bytes(), list(self.w.tabs_by_key), set(self.w.path_items)), before)
        self.assertTrue(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())

    def test_racing_destination_is_never_overwritten(self):
        target = self.root/'Race.pcs-category'
        def race(stage, *args):
            if stage == 'before-create':
                target.write_bytes(b'other writer')
        before = list(self.w.tabs_by_key)
        with patch.object(io, '_checkpoint', race):
            self.assertIsNone(self.w.new_document('category', 'Race'))
        self.assertTrue(self.errors.pop())
        self.assertEqual(target.read_bytes(), b'other writer')
        self.assertEqual(list(self.w.tabs_by_key), before)
        self.assertNotIn(path_key(target), self.w.path_items)

    def test_markdown_and_folder_permission_failures_keep_form_and_original(self):
        tab = self.w.open_path(self.note, True)
        QTest.keyClicks(tab.widget, 'keep draft')
        original, tabs = self.note.read_bytes(), list(self.w.tabs_by_key)
        for kind, method in (('markdown', 'open'), ('folder', 'mkdir')):
            action = getattr(Path, method)
            def deny(path, *args, **kwargs):
                if path.name.startswith('Denied'):
                    raise PermissionError('destination is read-only')
                return action(path, *args, **kwargs)
            def submit(dialog):
                dialog.name.setText('Denied')
                with patch.object(Path, method, deny):
                    dialog.submit()
                self.assertTrue(dialog.isVisible())
                self.assertIn('read-only', dialog.error.text())
                self.assertEqual(dialog.name.text(), 'Denied')
                dialog.reject()
            self.dialog_action(lambda: self.w.new_document(kind), submit)
        self.assertEqual(self.note.read_bytes(), original)
        self.assertEqual(list(self.w.tabs_by_key), tabs)
        self.assertTrue(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        self.assertFalse(list(self.root.glob('Denied*')))

    def test_root_link_persistence_failure_rolls_back_navigation_only(self):
        before = list(self.w.locations.roots), self.w.locations.current, set(self.w.path_items)
        with patch.object(self.w, 'save_locations', side_effect=PermissionError('link storage unavailable')):
            self.assertFalse(self.w.add_root(self.external))
            self.assertEqual(self.errors.pop(), 'link storage unavailable')
            self.assertFalse(self.w.remove_root(self.root))
            self.assertEqual(self.errors.pop(), 'link storage unavailable')
        self.assertEqual((list(self.w.locations.roots), self.w.locations.current, set(self.w.path_items)), before)

    def test_toolbar_folder_shortcut_is_keyboard_reachable_at_all_widths(self):
        for width in (220, 264, 480):
            self.w.sidebar_resize.resize_to(width)
            self.events()
            controls = [self.w.tree_new, self.w.tree_folder, self.w.tree_more]
            for control in controls:
                self.assertTrue(control.isVisible())
                self.assertTrue(control.accessibleName())
                self.assertNotEqual(control.focusPolicy(), Qt.FocusPolicy.NoFocus)
                point = control.mapTo(self.w.sidebar, control.rect().bottomRight())
                self.assertLess(point.x(), width)
            self.assertLess(controls[0].geometry().right(), controls[1].x())
        self.w.tree.setCurrentItem(self.item(self.folder))
        self.w.tree_folder.setFocus()
        def create(dialog):
            self.assertEqual(dialog.directory.text(), str(self.folder))
            dialog.name.setText('鍵盤')
            dialog.submit()
        self.dialog_action(lambda: QTest.keyClick(self.w.tree_folder, Qt.Key.Key_Space), create)
        self.assertTrue((self.folder/'鍵盤').is_dir())

    def test_top_header_uses_space_beside_tabs_and_nested_names_align(self):
        self.w.set_navigation_position('top')
        self.events()
        for width in (220, 264, 480):
            self.w.sidebar_resize.resize_to(width)
            self.events()
            header = self.w.tree_heading
            self.assertIs(header.parentWidget(), self.w.tab_row)
            self.assertEqual(header.width(), self.w.sidebar.width())
            self.assertEqual(header.mapTo(self.w, QPoint()).y(), self.w.tab_strip.mapTo(self.w, QPoint()).y()-2)
            self.assertEqual(self.w.tab_strip.mapTo(self.w, QPoint()).x(), width)
        self.w.toggle_sidebar()
        self.events()
        self.assertFalse(self.w.tree_heading.isVisible())
        self.assertEqual(self.w.tab_strip.mapTo(self.w, QPoint()).x(), 16)
        self.w.toggle_sidebar()
        self.events()
        self.assertTrue(self.w.tree_heading.isVisible())
        self.w.set_navigation_position('side')
        self.events()
        self.assertIs(self.w.tree_heading.parentWidget(), self.w.sidebar)
        self.w.tree.expandAll()
        def text_x(path):
            index = self.w.tree.indexFromItem(self.item(path))
            option = QStyleOptionViewItem()
            option.initFrom(self.w.tree)
            option.widget = self.w.tree
            option.decorationSize = self.w.tree.iconSize()
            option.rect = self.w.tree.visualRect(index)
            self.w.tree.itemDelegate().initStyleOption(option, index)
            return self.w.tree.style().subElementRect(QStyle.SubElement.SE_ItemViewItemText, option, self.w.tree).x()
        self.assertEqual(text_x(self.folder/'Nested.md'), text_x(self.folder))
        self.assertEqual(text_x(self.note), text_x(self.folder))


if __name__ == '__main__':
    unittest.main()
