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
        self.w.autosave.delay_ms = 60000  # These cases exercise explicit file commands.
        self.w.report_error = self.errors.append
        self.w.show()
        self.events()

    def events(self):
        self.app.processEvents()
        QTest.qWait(20)
        if hasattr(self, 'w') and self.w.sidebar_resize.motion_target is not None:
            self.w.sidebar_resize.motion.setCurrentTime(self.w.sidebar_resize.motion.duration())
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

    def test_all_kinds_and_captured_destinations_save_current_and_preserve_editor_history(self):
        tab = self.w.open_path(self.note, True)
        QTest.keyClicks(tab.widget, 'unsaved ')
        original = self.note.read_bytes()
        old_editor = tab.widget
        contexts = [self.w.commands.context('tree', None),
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
        self.assertFalse(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        self.assertNotEqual(self.note.read_bytes(), original)

    def inline_action(self, action, edit):
        popup = self.app.activePopupWidget()
        if popup:
            popup.close()
        action()
        editor = self.w.commands.files.inline
        self.assertIsNotNone(editor)
        self.assertIsNone(self.app.activeModalWidget())
        edit(editor)
        self.events()

    def test_toolbar_and_folder_use_inline_home_creates_unique_document(self):
        self.w.select_path(self.folder)
        QTest.mouseClick(self.w.tree_new, Qt.MouseButton.LeftButton)
        menu = self.app.activePopupWidget()
        self.assertEqual([a.text() for a in menu.actions()],
                         ['新增prompt管理頁', '新增工作流', '新增文字文件', '新增對話'])
        action = next(a for a in menu.actions() if a.text() == '新增文字文件')
        self.w.select_path(self.root)
        self.inline_action(action.trigger, lambda e: (e.name.setText('工具列.md'), QTest.keyClick(e.name, Qt.Key.Key_Return)))
        self.assertTrue((self.folder/'工具列.md').exists())
        before = set(self.w.tabs_by_key)
        menu = self.w.commands.menu_for(self.w.commands.context('tree', self.item(self.folder)))
        create_folder = next(a for a in menu.actions()[0].menu().actions() if a.text() == '新增資料夾…')
        self.inline_action(create_folder.trigger,
                           lambda e: (e.name.setText('新資料夾'), e.submit()))
        self.assertTrue((self.folder/'新資料夾').is_dir())
        self.assertEqual(set(self.w.tabs_by_key), before)
        menu.deleteLater()
        home = self.w.new_tab().widget
        self.w.select_path(self.folder)
        QTest.mouseClick(home.create_buttons['category'], Qt.MouseButton.LeftButton)
        self.events()
        self.assertIsNone(self.w.commands.files.inline)
        self.assertTrue((self.folder/'未命名.pcs-category').is_file())

    def test_cancel_and_invalid_inline_name_keep_created_unnamed_files(self):
        before = set(self.root.rglob('*')), set(self.w.tabs_by_key)
        def cancel(editor):
            editor.name.setText('未提交')
            QTest.keyClick(editor.name, Qt.Key.Key_Escape)
        self.inline_action(lambda: self.w.commands.new('markdown', self.w.commands.context('toolbar')), cancel)
        def fix_error(editor):
            editor.name.setText('CON')
            editor.submit()
            self.assertTrue(editor.isVisible())
            self.assertTrue(editor.error.text())
            self.assertEqual(editor.name.text(), 'CON')
            editor.cancel()
        self.inline_action(lambda: self.w.commands.new('workflow', self.w.commands.context('toolbar')), fix_error)
        self.assertTrue((self.root/'未命名.md').is_file())
        self.assertTrue((self.root/'未命名.pcs-workflow').is_file())
        self.assertFalse((self.root/'未提交.md').exists())
        self.assertFalse((self.root/'CON.pcs-workflow').exists())

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

    def test_markdown_and_folder_permission_failures_preserve_original_and_no_ghost(self):
        tab = self.w.open_path(self.note, True)
        QTest.keyClicks(tab.widget, 'keep draft')
        original, tabs = self.note.read_bytes(), list(self.w.tabs_by_key)
        for kind in ('markdown', 'folder'):
            with patch.object(self.w.commands, 'create_at', side_effect=PermissionError('destination is read-only')), patch.object(self.w, 'show_notice') as notice:
                self.assertIsNone(self.w.commands.new(kind, self.w.commands.context('toolbar')))
                self.assertIn('read-only',notice.call_args.args[0])
            self.assertIsNone(self.w.commands.files.inline)
        self.assertEqual(self.note.read_bytes(), original)
        self.assertEqual(list(self.w.tabs_by_key), tabs)
        self.assertTrue(tab.dirty)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        self.assertFalse(list(self.root.glob('未命名*')))

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
            controls = [self.w.tree_new, self.w.tree_folder, self.w.tree_fold, self.w.tree_more]
            for control in controls:
                self.assertTrue(control.isVisible())
                self.assertTrue(control.accessibleName())
                self.assertNotEqual(control.focusPolicy(), Qt.FocusPolicy.NoFocus)
                point = self.w.sidebar.mapFromGlobal(control.mapToGlobal(control.rect().bottomRight()))
                self.assertLess(point.x(), width)
            self.assertLess(controls[0].geometry().right(), controls[1].x())
        self.w.tree.setCurrentItem(self.item(self.folder))
        self.w.tree_folder.setFocus()
        def create(dialog):
            self.assertIsNone(self.app.activeModalWidget())
            dialog.name.setText('鍵盤')
            dialog.submit()
        self.inline_action(lambda: QTest.keyClick(self.w.tree_folder, Qt.Key.Key_Space), create)
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
            self.assertEqual(header.mapTo(self.w, QPoint()).y(), self.w.tab_strip.mapTo(self.w, QPoint()).y())
            self.assertEqual(self.w.tab_strip.mapTo(self.w, QPoint()).x(), width)
        self.w.toggle_sidebar()
        self.events()
        self.assertFalse(self.w.tree_heading.isVisible())
        self.assertEqual(self.w.tab_strip.mapTo(self.w, QPoint()).x(), 0)
        self.w.toggle_sidebar()
        self.events()
        self.assertTrue(self.w.tree_heading.isVisible())
        self.w.set_navigation_position('side')
        self.events()
        self.assertIs(self.w.tree_heading.parentWidget(), self.w.tab_row)
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

    def test_file_menu_operations_target_clicked_file_and_root_only_unlinks(self):
        context = self.w.commands.context('tree', self.item(self.folder/'Nested.md'))
        menu = self.w.commands.menu_for(context)
        actions = {action.text(): action for action in menu.actions()}
        self.assertIn('移至資源回收筒', actions)
        child = actions['另存新檔…']
        self.dialog_action(child.trigger, lambda d: (self.assertEqual(d.directory.text(), str(self.folder)), d.reject()))
        self.w.add_root(self.external)
        root_menu = self.w.commands.menu_for(self.w.commands.context('tree', self.item(self.external)))
        labels = [action.text() for action in root_menu.actions()]
        self.assertNotIn('移至資源回收筒', labels)
        self.assertIn('移除側欄連結（保留原檔）', labels)

    def trash_action(self, path, confirm=True):
        return self.w.commands.trash(self.w.commands.context('tree', self.item(path)))

    def test_trash_failure_keeps_file_tab_recent_and_dirty_descendants(self):
        tab = self.w.open_path(self.note, True)
        before = self.note.read_bytes(), list(self.w.recent_files)
        with patch.object(self.w.commands, 'move_to_trash', side_effect=OSError('access denied')):
            self.assertFalse(self.trash_action(self.note))
        self.assertIn('access denied', self.w.commands.delete_notice.message.text())
        self.assertIn(tab.key, self.w.tabs_by_key)
        self.assertEqual((self.note.read_bytes(), self.w.recent_files), before)
        nested = self.w.open_path(self.folder/'Nested.md', True, new_tab=True)
        QTest.keyClicks(nested.widget, 'unsaved ')
        recycled = self.external/'folder'
        with patch.object(self.w.commands, 'move_to_trash', side_effect=lambda p:p.rename(recycled)):
            self.assertTrue(self.trash_action(self.folder))
        self.assertNotIn(nested.key, self.w.tabs_by_key)
        self.assertEqual((recycled/'Nested.md').read_text(encoding='utf-8'), 'nested')

    def test_trash_success_closes_only_affected_tabs_and_keeps_attachments(self):
        tab = self.w.open_path(self.note, True)
        other = self.w.open_path(self.folder/'Nested.md', True, new_tab=True)
        attachment = self.root/'assets'/'keep.png'
        attachment.parent.mkdir()
        attachment.write_bytes(b'untouched attachment')
        recycled = self.external/'recycled.md'
        def move(path):
            Path(path).rename(recycled)
            return True, str(recycled)
        with patch('prompt_calculus_studio.workspace_commands.WorkspaceCommands.move_to_trash', side_effect=move):
            self.assertTrue(self.trash_action(self.note, True))
        self.assertEqual(recycled.read_bytes(), b'\xef\xbb\xbf# draft\r\n')
        self.assertNotIn(tab.key, self.w.tabs_by_key)
        self.assertIn(other.key, self.w.tabs_by_key)
        self.assertNotIn(str(self.note), self.w.recent_files)
        self.assertNotIn(path_key(self.note), self.w.path_items)
        self.assertEqual(attachment.read_bytes(), b'untouched attachment')

    def test_all_sidebar_headers_dock_collapse_and_restore(self):
        self.w.set_navigation_position('top')
        self.w.open_route('settings')
        self.events()
        self.assertFalse(self.w.utility.gallery.is_loaded)
        self.assertFalse(self.w.utility.clean_export.is_loaded)
        self.assertEqual(self.w.utility.styleSheet(), '')
        for route in ('home', 'gallery', 'explore', 'export', 'settings'):
            self.w.open_route(route)
            self.events()
            target = self.w.sidebar_target()
            header = next(h for side, h, *_ in self.w.sidebar_headers.entries if side is target)
            self.assertIs(header.parentWidget(), self.w.tab_row, route)
            self.assertEqual(header.height(), self.w.tab_strip.bar.height(), route)
            self.assertEqual(header.mapTo(self.w, QPoint()).y(), self.w.tab_strip.bar.mapTo(self.w, QPoint()).y(), route)
            if route in ('explore', 'settings'):
                navigation = self.w.utility.settings_page.navigation
                self.assertLessEqual(navigation.visualItemRect(navigation.item(0)).height(), 34, route)
            self.w.toggle_sidebar()
            self.events()
            self.assertFalse(header.isVisible(), route)
            self.assertEqual(self.w.tab_strip.x(), 0 if route == 'home' else 16, route)
            self.w.toggle_sidebar()
            self.w.set_navigation_position('side')
            self.events()
            self.assertIs(header.parentWidget(), self.w.tab_row, route)
            self.assertTrue(header.isVisible(), route)
            self.w.set_navigation_position('top')
            self.events()

    def test_connection_shortcut_preserves_page_and_navigation(self):
        host = self.w.ensure_utility()
        current = self.w.workspace_stack.currentWidget()
        history = list(self.w.history)
        with patch.object(host.comfy, 'recheck_connection') as recheck:
            self.w.connection.click()
            recheck.assert_called_once_with()
        self.assertIs(self.w.workspace_stack.currentWidget(), current)
        self.assertEqual(self.w.history, history)
        self.assertEqual(self.w.active_route, 'home')
        self.assertEqual(self.w.connection.brand.objectName(), 'Subtle')
        self.assertEqual(self.w.connection.dot.size().width(), 10)
        self.assertFalse(self.w.connection.brand.isHidden())
        self.assertIs(self.w.connection.parentWidget(), self.w.top)
        self.w.set_navigation_position('top')
        self.assertEqual(self.w.connection.dot.size().width(), 10)
        self.assertFalse(self.w.connection.brand.isHidden())
        self.assertIs(self.w.connection.parentWidget(), self.w.top)

    def test_canvas_proxy_style_matches_legacy_and_survives_appearance_changes(self):
        from prompt_calculus_studio.theme import stylesheet, window_appearance
        path = self.root/'Canvas.pcs-workflow'
        create_file(path.parent, 'workflow', 'Canvas')
        tab = self.w.open_path(path, True)
        self.events()
        host = tab.document.host
        panel = next(iter(host.canvas.outputs.values())).panel
        self.assertEqual(host.styleSheet(), '')
        for size in (12, 14):
            self.w.settings['ui_size'] = size
            self.w.style_shell()
            self.events()
            appearance = window_appearance(host)
            appearance['material'] = 'solid'
            self.assertEqual(panel.styleSheet(), stylesheet(appearance))
            self.assertIn('Cascadia Mono', panel.editor.styleSheet() + panel.styleSheet())
        self.assertNotEqual(host.state['settings']['ui_size'], 14)

    def test_function_catalog_matches_embedded_canvas_without_changing_document_settings(self):
        import copy
        from prompt_calculus_studio.module_preview import ModulePreview
        from PySide6.QtWidgets import QWidget
        from prompt_calculus_studio.function_picker import FUNCTIONS
        from prompt_calculus_studio.canvas_items import canvas_colors
        from prompt_calculus_studio.theme import canvas_stylesheet, window_appearance
        path = self.root/'Catalog.pcs-workflow'
        create_file(path.parent, 'workflow', 'Catalog')
        tab = self.w.open_path(path, True)
        self.events()
        canvas = tab.document.host.canvas
        saved = copy.deepcopy(tab.document.state['settings'])
        preview = ModulePreview(canvas)
        self.addCleanup(preview.deleteLater)
        for palette, size in (('graphite', 12), ('mist', 14), ('paper', 12)):
            self.w.settings.update(visual_palette=palette, ui_size=size)
            self.w.style_shell()
            self.events()
            for kind, *_ in FUNCTIONS:
                with self.subTest(palette=palette, kind=kind):
                    preview.show_kind(kind)
                    self.events()
                    self.assertEqual(canvas_colors(preview.context), canvas_colors(canvas))
                    self.assertEqual(window_appearance(preview.context.window), window_appearance(canvas.window))
                    panel = getattr(preview.card, 'panel', None)
                    if isinstance(panel, QWidget):
                        self.assertTrue(panel.styleSheet().startswith(canvas_stylesheet(canvas.window)))
        self.assertEqual(tab.document.state['settings'], saved)


if __name__ == '__main__':
    unittest.main()
