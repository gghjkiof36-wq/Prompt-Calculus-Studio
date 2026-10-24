"""Captured submenu, drag/drop, confirmation and disclosure interactions."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QTimer, QMimeData, QEvent
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QDrag, QInputMethodEvent, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialogButtonBox, QDialog, QWidget
from prompt_calculus_studio.workspace_locations import path_key
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_file_drag import MIME
import test_workspace_file_operations as helpers


class Enter(QDragEnterEvent):
    def source(self):
        return self.origin


class Move(QDragMoveEvent):
    def source(self):
        return self.origin


class Drop(QDropEvent):
    def source(self):
        return self.origin


class WorkspaceFileInteractionsTests(unittest.TestCase):
    setUpClass = classmethod(helpers.WorkspaceFileOperationsTests.setUpClass.__func__)
    setUp = helpers.WorkspaceFileOperationsTests.setUp
    events = helpers.WorkspaceFileOperationsTests.events
    open = helpers.WorkspaceFileOperationsTests.open
    context = helpers.WorkspaceFileOperationsTests.context
    dialog = helpers.WorkspaceFileOperationsTests.dialog

    def tearDown(self):
        self.w.tree.file_drag.finish()
        if self.w.commands.files.inline:
            self.w.commands.files.inline.cancel()
        for operation in list(self.w.commands.files.pending):
            self.w.commands.files.end(operation)
        helpers.WorkspaceFileOperationsTests.tearDown(self)

    def menu(self, path):
        menu = self.w.commands.menu_for(self.context(path=path))
        submenu = next(a.menu() for a in menu.actions() if a.text() == '移動到')
        menu.open_at(self.w.tree.mapToGlobal(QPoint(30, 70)))
        submenu.popup(menu.pos()+QPoint(menu.width(), 0))
        self.events()
        return menu, submenu

    def event(self, cls, position, mime=None, origin=None):
        point = QPointF(position) if cls is Drop else position
        data = mime or self.w.tree.file_drag.mime()
        event = cls(point, Qt.DropAction.MoveAction, data,
                    Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        event.owned_mime = data  # Qt events borrow their MIME data.
        event.origin = self.w.tree if origin is None else origin
        QApplication.sendEvent(self.w.tree.viewport(), event)
        return event

    def drag_to(self, path, drop=True):
        item = self.w.path_items[path_key(path)]
        self.w.tree.scrollToItem(item)
        self.app.processEvents()
        position = self.w.tree.visualItemRect(item).center()
        self.event(Enter, position)
        event = self.event(Move, position)
        if drop:
            event = self.event(Drop, position)
        return event

    def test_submenu_captures_target_and_hiding_child_keeps_pause_until_cancel(self):
        a = self.open()
        b = self.open('Empty.md', new=True)
        a.widget.insertPlainText('pending')
        menu, sub = self.menu(a.path)
        self.assertIn(id(a), self.w.autosave.paused)
        self.assertNotIn(id(b), self.w.autosave.paused)
        sub.hide()
        self.events()
        self.assertIn(id(a), self.w.autosave.paused)
        menu.close()
        self.events()
        self.assertFalse(self.w.commands.files.pending)
        self.assertNotIn(id(a), self.w.autosave.paused)
        self.assertTrue(a.dirty)
        self.assertEqual(a.path, self.root/'Notes.md')

    def test_submenu_parent_leaf_root_and_source_descendants(self):
        nested = self.root/'Folder'/'Nested'
        nested.mkdir()
        self.w.refresh_tree()
        a = self.open()
        menu, sub = self.menu(a.path)
        same = next(x for x in sub.actions() if x.data() == str(self.root))
        self.assertFalse(same.isEnabled())
        folder = next(x.menu() for x in sub.actions() if x.data() == str(self.root/'Folder'))
        folder.aboutToShow.emit()
        parent = next(x for x in folder.actions() if x.text() == '移至此資料夾')
        parent.trigger()
        menu.close()
        self.events()
        self.assertEqual(a.path, self.root/'Folder/Notes.md')
        menu, sub = self.menu(self.root/'Folder')
        invalid = next(x for x in sub.actions() if x.data() == str(self.root/'Folder'))
        self.assertFalse(invalid.isEnabled())
        menu.close()
        self.events()
        menu, sub = self.menu(a.path)
        next(x for x in sub.actions() if x.data() == str(self.root)).trigger()
        menu.close()
        self.events()
        self.assertEqual(a.path, self.root/'Notes.md')

    def test_drag_event_chain_moves_file_without_navigation_and_keeps_undo(self):
        for name in ('Notes.md', 'Scene.pcs-category', 'Street.pcs-workflow'):
            active = self.w.active_key
            path = self.root/name
            self.w.select_path(path)
            before = path.read_bytes()
            drag = self.w.tree.file_drag
            def execute(*args):
                self.assertTrue(self.drag_to(self.root/'Folder').isAccepted())
                return Qt.DropAction.MoveAction
            with patch.object(QDrag, 'exec', side_effect=execute):
                self.w.tree.startDrag(Qt.DropAction.MoveAction)
            self.assertEqual(self.w.active_key, active)
            self.assertEqual((self.root/'Folder'/name).read_bytes(), before)
            self.assertFalse(path.exists())
            self.assertFalse(drag.op)
        tab = self.open('Folder/Notes.md')
        tab.widget.insertPlainText('draft')
        text = tab.widget.toPlainText()
        self.w.select_path(tab.path)
        drag = self.w.tree.file_drag
        self.assertTrue(drag.begin())
        self.assertTrue(self.drag_to(self.root).isAccepted())
        drag.finish()
        self.assertEqual(tab.path, self.root/'Notes.md')
        self.assertEqual(tab.widget.toPlainText(), text)
        tab.widget.undo()
        self.assertNotEqual(tab.widget.toPlainText(), text)
        tab.widget.redo()
        self.assertTrue(self.w.save_tab(tab))
        self.assertIn(b'draft', tab.path.read_bytes())

    def test_pointer_press_drag_release_does_not_open_the_dragged_file(self):
        active = self.open('Empty.md')
        active.widget.insertPlainText('keep active draft')
        self.w.select_path(self.root/'Notes.md')
        view = self.w.tree.viewport()
        start = self.w.tree.visualItemRect(self.w.tree.currentItem()).center()
        end = self.w.tree.visualItemRect(self.w.path_items[path_key(self.root/'Folder')]).center()
        def execute(*args):
            self.assertTrue(self.drag_to(self.root/'Folder').isAccepted())
            return Qt.DropAction.MoveAction
        with patch.object(QDrag, 'exec', side_effect=execute) as native_drag:
            QTest.mousePress(view, Qt.MouseButton.LeftButton, pos=start)
            point = QPointF(start+QPoint(QApplication.startDragDistance()+10, 0))
            move = QMouseEvent(QEvent.Type.MouseMove, point, QPointF(view.mapToGlobal(point.toPoint())),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QApplication.sendEvent(view, move)
            QTest.mouseRelease(view, Qt.MouseButton.LeftButton, pos=end)
            native_drag.assert_called_once()
        self.events()
        self.assertEqual(self.w.active_key, active.key)
        self.assertTrue(active.dirty)
        self.assertTrue((self.root/'Folder/Notes.md').exists())

    def test_folder_drag_retargets_cached_descendants_and_rejects_invalid_places(self):
        source = self.root/'Source'
        (source/'Child').mkdir(parents=True)
        (source/'Child'/'Nested.md').write_text('original', encoding='utf-8')
        self.w.refresh_tree()
        a = self.open('Source/Child/Nested.md')
        a.widget.insertPlainText('edited ')
        b = self.open()
        self.assertIn(a.key, self.w.saved_pages)
        self.w.select_path(source)
        drag = self.w.tree.file_drag
        self.assertTrue(drag.begin())
        for target in (source, source/'Child', self.root, b.path):
            self.assertFalse(self.drag_to(target, drop=False).isAccepted())
        self.assertTrue(self.drag_to(self.root/'Folder').isAccepted())
        drag.finish()
        self.assertEqual(a.path, self.root/'Folder/Source/Child/Nested.md')
        self.assertEqual(self.w.active_key, b.key)
        self.assertTrue(a.widget.document().isUndoAvailable())
        self.assertEqual(a.path.read_text(encoding='utf-8'), 'edited original')

    def test_drag_cancel_collision_external_payload_and_external_change_preserve_source(self):
        tab = self.open()
        tab.widget.insertPlainText('kept ')
        source, raw = tab.path, tab.path.read_bytes()
        self.w.select_path(source)
        drag = self.w.tree.file_drag
        self.assertTrue(drag.begin())
        mime = QMimeData()
        mime.setData(MIME, b'foreign')
        self.assertFalse(self.event(Enter, QPoint(20, 20), mime).isAccepted())
        drag.finish()  # Same finally path as Esc/cancel from QDrag.exec.
        self.assertEqual(source.read_bytes(), raw)
        self.assertTrue(tab.dirty)
        self.w.select_path(source)
        self.assertTrue(drag.begin())
        collision = self.root/'Folder'/source.name
        collision.write_text('do not overwrite', encoding='utf-8')
        self.assertTrue(self.drag_to(self.root/'Folder').isAccepted())
        drag.finish()
        self.assertEqual(collision.read_text(encoding='utf-8'), 'do not overwrite')
        self.assertEqual(tab.path, collision.with_name('Notes(1).md'))
        self.assertFalse(source.exists())
        # Move the same note back before testing a changed-source rejection.
        self.w.commands.files.run('move', self.w.commands.context('tab', tab=tab), directory=self.root)
        source = tab.path
        self.w.select_path(source)
        self.assertTrue(drag.begin())
        source.write_bytes(b'external changed')
        collision.unlink()
        self.assertFalse(self.drag_to(self.root/'Folder').isAccepted())
        drag.finish()
        self.assertEqual(tab.path, source)
        self.assertEqual(source.read_bytes(), b'external changed')
        self.assertIn('kept', tab.widget.toPlainText())
        self.assertFalse((self.root/'Folder'/source.name).exists())

    def test_drag_dwell_expands_and_edge_scrolls_without_opening_a_document(self):
        for n in range(65):
            (self.root/'Folder'/f'child {n:02}').mkdir()
        self.w.refresh_tree()
        tab = self.open()
        active = self.w.active_key
        self.w.select_path(tab.path)
        drag = self.w.tree.file_drag
        self.assertTrue(drag.begin())
        self.assertTrue(self.drag_to(self.root/'Folder', drop=False).isAccepted())
        QTest.qWait(700)
        self.assertTrue(self.w.path_items[path_key(self.root/'Folder')].isExpanded())
        drag.position = QPoint(100, self.w.tree.viewport().height()-4)
        before = self.w.tree.verticalScrollBar().value()
        QTest.qWait(180)
        self.assertGreater(self.w.tree.verticalScrollBar().value(), before)
        self.assertEqual(self.w.active_key, active)
        drag.finish()

    def test_delete_ignores_legacy_confirmation_preference_and_has_no_modal(self):
        self.w.save_ui_preferences()
        config = self.w.data/'workspace-ui.json'
        saved = json.loads(config.read_text(encoding='utf-8'))
        saved['confirm_trash'] = True
        config.write_text(json.dumps(saved), encoding='utf-8')
        self.w.load_ui_preferences()
        path = self.root/'Notes.md'
        with patch.object(QDialog, 'exec', side_effect=AssertionError('no modal')), \
                patch.object(self.w.commands, 'move_to_trash', side_effect=OSError('locked')):
            self.assertFalse(self.w.commands.trash(self.context(path=path)))
        self.assertIn('locked', self.w.commands.delete_notice.message.text())
        self.w.open_route('settings')
        self.events()
        self.assertFalse(hasattr(self.w.utility.settings_page.preferences, 'confirm_trash'))
        self.w.save_ui_preferences()
        self.assertNotIn('confirm_trash', json.loads(config.read_text(encoding='utf-8')))

    def test_disclosure_refresh_restart_selection_scroll_and_coalesced_save(self):
        for n in range(45):
            (self.root/'Folder'/f'Branch {n:02}'/'Child').mkdir(parents=True)
        self.w.refresh_tree()
        tree = self.w.tree
        tree.expandAll()
        selected = self.root/'Folder/Branch 30/Child'
        self.w.select_path(selected)
        branch = self.w.path_items[path_key(self.root/'Folder/Branch 30')]
        parent = self.w.path_items[path_key(self.root/'Folder')]
        root = tree.topLevelItem(0)
        with patch.object(self.w.locations, 'inventory', side_effect=AssertionError('no scan on toggle')):
            for item in (root, parent, branch):
                for i in range(40):
                    item.setExpanded(i % 2 == 1)
            branch.setExpanded(False)
            self.assertTrue(self.w.tree_state.timer.isActive())
        before = tree.verticalScrollBar().value()
        self.w.refresh_tree()
        self.events()
        self.assertEqual(tree.currentItem().data(0, Qt.ItemDataRole.UserRole+1), str(selected))
        self.assertFalse(self.w.path_items[path_key(self.root/'Folder/Branch 30')].isExpanded())
        self.assertEqual(tree.verticalScrollBar().value(), before)
        QTest.qWait(420)
        self.assertFalse(self.w.tree_state.dirty)
        self.w.close()
        other = WorkspaceShell(self.root, self.w.data)
        try:
            other.show()
            self.events()
            self.assertTrue(other.tree.topLevelItem(0).isExpanded())
            self.assertTrue(other.path_items[path_key(self.root/'Folder')].isExpanded())
            self.assertFalse(other.path_items[path_key(self.root/'Folder/Branch 30')].isExpanded())
            self.assertEqual(other.tree.currentItem().data(0, Qt.ItemDataRole.UserRole+1), str(selected))
            self.assertEqual(other.tree.verticalScrollBar().value(), before)
        finally:
            other.close()
            other.deleteLater()

    def test_creation_labels_and_rename_preserve_standard_markdown_format(self):
        menu = self.w.commands.menu_for(self.context(path=self.root/'Folder'))
        creation = menu.actions()[0].menu()
        expected = ['新增prompt管理頁', '新增工作流', '新增文字文件']
        self.assertEqual([a.text() for a in creation.actions()][:3], expected)
        home = self.w.pages.currentWidget()
        self.assertEqual([b.text() for b in home.create_buttons.values()], expected)
        rename = next(a for a in menu.actions() if a.text() == '重新命名')
        self.assertTrue(rename.shortcut().isEmpty())
        trash = next(a for a in menu.actions() if a.text().startswith('移至資源回收筒'))
        self.assertFalse(trash.icon().isNull())
        path = self.w.commands.create_at('markdown', '文字文件', self.root)
        self.assertEqual(path.suffix, '.md')
        menu.deleteLater()

    def test_inline_creation_all_kinds_ime_focus_cancel_and_current_slot(self):
        for position in ('side', 'top'):
            self.w.set_navigation_position(position)
            for kind, suffix in (('folder', ''), ('markdown', '.md'),
                                 ('category', '.pcs-category'), ('workflow', '.pcs-workflow')):
                self.w.select_path(self.root/'Folder')
                before = self.w.tab_strip.bar.count()
                active = self.w.active_key
                editor = self.w.commands.new(kind, self.w.commands.context('tree', self.w.path_items[path_key(self.root/'Folder')]))
                self.assertIsNone(self.app.activeModalWidget())
                self.assertEqual(editor.name.text(), '未命名')
                QTest.keyClick(editor.name, Qt.Key.Key_Escape)
                self.assertFalse(list((self.root/'Folder').glob('未命名*')))
                self.w.select_path(self.root/'Folder')
                editor = self.w.commands.new(kind, self.w.commands.context('tree', self.w.path_items[path_key(self.root/'Folder')]))
                editor.name.setText('IME-'+position+'-'+kind)
                preedit = QInputMethodEvent('組字', [])
                QApplication.sendEvent(editor.name, preedit)
                QTest.keyClick(editor.name, Qt.Key.Key_Return)
                self.assertIs(self.w.commands.files.inline, editor)
                QApplication.sendEvent(editor.name, QInputMethodEvent())
                QTest.keyClick(editor.name, Qt.Key.Key_Return)
                self.assertIsNone(self.w.commands.files.inline)
                path = self.root/'Folder'/('IME-'+position+'-'+kind+suffix)
                self.assertTrue(path.exists())
                self.assertEqual(self.w.tab_strip.bar.count(), before)
                if kind == 'folder':
                    self.assertEqual(self.w.active_key, active)
                else:
                    self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, path)
        editor = self.w.new_document('markdown', context=self.w.commands.selected_context())
        original = set(self.root.rglob('*'))
        self.w.tree_fold.setFocus()
        self.events()
        self.assertIsNone(self.w.commands.files.inline)
        self.assertEqual(set(self.root.rglob('*')), original)

    def test_inline_creation_collision_avoids_hidden_selection_and_no_root_does_not_guess(self):
        (self.root/'未命名.md').write_text('existing', encoding='utf-8')
        self.w.select_path(self.root/'Folder')
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        editor = self.w.new_document('markdown', context=self.w.commands.selected_context())
        self.assertTrue(self.w.tree.isVisible())
        self.assertEqual(editor.name.text(), '未命名 2')
        editor.name.setText('未命名')
        editor.submit()
        self.assertTrue(editor.error.isVisible())
        self.assertEqual((self.root/'未命名.md').read_text(encoding='utf-8'), 'existing')
        self.assertEqual(editor.name.text(), '未命名')
        self.assertIsNone(self.w.tree.currentItem().data(0, Qt.ItemDataRole.UserRole))
        editor.cancel()
        self.w.remove_root(self.root)
        with patch.object(self.w, 'choose_root') as choose:
            self.assertIsNone(self.w.new_document('markdown', context=self.w.commands.selected_context()))
            choose.assert_called_once()

    def test_fold_control_and_trailing_menu_actions_are_reachable_in_both_positions(self):
        (self.root/'Folder'/'Child').mkdir()
        self.w.refresh_tree()
        for position in ('side', 'top'):
            self.w.set_navigation_position(position)
            self.w.sidebar_resize.resize_to(220)
            self.events()
            for control in (self.w.tree_new, self.w.tree_folder, self.w.tree_fold, self.w.tree_more):
                self.assertTrue(control.isVisible())
                self.assertLessEqual(control.mapTo(self.w.tree_heading, control.rect().bottomRight()).x(),
                                     self.w.tree_heading.width())
            with patch.object(self.w.locations, 'inventory', side_effect=AssertionError('toggle must not scan')):
                self.w.tree.collapseAll()
                self.assertEqual(self.w.tree_fold.toolTip(), '全部展開')
                QTest.mouseClick(self.w.tree_fold, Qt.MouseButton.LeftButton)
                self.assertFalse(self.w.tree_state.closed_branches)
                self.assertEqual(self.w.tree_fold.toolTip(), '全部收合')
                QTest.mouseClick(self.w.tree_fold, Qt.MouseButton.LeftButton)
                self.assertTrue(self.w.tree_state.closed_branches)
            self.w.root_menu()
            menu = self.app.activePopupWidget()
            labels = [a.text() for a in menu.actions()]
            self.assertEqual(labels, ['重新整理', '加入本機資料夾…', '顯示空資料夾', '開啟根資料夾位置'])
            empty = next(a for a in menu.actions() if a.text() == '顯示空資料夾')
            before = self.w.show_empty_folders
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(empty).center())
            self.events()
            self.assertIs(self.w.show_empty_folders, not before)
            self.w.root_menu()
            menu = self.app.activePopupWidget()
            refresh = next(a for a in menu.actions() if a.text() == '重新整理')
            self.assertIsNotNone(refresh.defaultWidget())
            (self.root/('New '+position+'.md')).write_text('refresh', encoding='utf-8')
            QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(refresh).center())
            self.events()
            self.assertIn(path_key(self.root/('New '+position+'.md')), self.w.path_items)

    def test_mouse_side_buttons_traverse_once_keep_undo_and_never_pan_canvas(self):
        for position in ('side', 'top'):
            self.w.set_navigation_position(position)
            note = self.open()
            note.widget.insertPlainText(' side button draft')
            text = note.widget.toPlainText()
            canvas = self.open('Street.pcs-workflow')
            view = canvas.document.host.canvas.view
            camera = (view.transform(), view.horizontalScrollBar().value(), view.verticalScrollBar().value())
            before = self.w.history_at
            QTest.mousePress(view.viewport(), Qt.MouseButton.BackButton)
            self.assertEqual(self.w.history_at, before)
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.BackButton)
            self.events()
            self.assertEqual(self.w.history_at, before-1)
            self.assertIs(self.w.pages.currentWidget(), note.widget)
            self.assertEqual(note.widget.toPlainText(), text)
            note.widget.undo()
            self.assertNotEqual(note.widget.toPlainText(), text)
            note.widget.redo()
            QTest.mouseClick(note.widget.viewport(), Qt.MouseButton.ForwardButton)
            self.events()
            self.assertIs(self.w.pages.currentWidget(), canvas.widget)
            self.assertEqual(self.w.history_at, before)
            self.assertEqual((view.transform(), view.horizontalScrollBar().value(), view.verticalScrollBar().value()), camera)
            # Canvas form widgets live in QGraphicsProxyWidget, not the shell's
            # QWidget parent tree. Side buttons must work over them as well.
            panel = next(iter(canvas.document.host.canvas.outputs.values())).panel
            QTest.mouseClick(panel.editor.viewport(), Qt.MouseButton.BackButton)
            self.events()
            self.assertIs(self.w.pages.currentWidget(), note.widget)
            QTest.mouseClick(note.widget.viewport(), Qt.MouseButton.ForwardButton)
            self.events()
            self.assertIs(self.w.pages.currentWidget(), canvas.widget)
            QTest.mouseClick(view.viewport(), Qt.MouseButton.ForwardButton)
            self.assertEqual(self.w.history_at, before)  # End of history.
            self.w.open_route('gallery')
            self.events()
            QTest.mouseClick(self.w.workspace_stack, Qt.MouseButton.BackButton)
            self.events()
            self.assertEqual(self.w.active_route, 'home')
            self.assertIs(self.w.pages.currentWidget(), canvas.widget)
            self.assertEqual(self.w.tab_strip.bar.count(), 1)

    def test_mouse_side_buttons_do_not_navigate_behind_popups_dialogs_or_other_windows(self):
        self.open()
        self.open('Empty.md')
        at = self.w.history_at
        dialog = QDialog(self.w)
        dialog.setModal(True)
        dialog.show()
        self.events()
        try:
            QTest.mouseClick(dialog, Qt.MouseButton.BackButton)
            QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.BackButton)
            self.assertEqual(self.w.history_at, at)
        finally:
            dialog.reject()
            dialog.deleteLater()
        self.w.root_menu()
        menu = self.app.activePopupWidget()
        QTest.mouseClick(menu, Qt.MouseButton.BackButton)
        self.assertEqual(self.w.history_at, at)
        menu.close()
        other = QWidget()
        other.show()
        self.events()
        try:
            QTest.mouseClick(other, Qt.MouseButton.BackButton)
            self.assertEqual(self.w.history_at, at)
        finally:
            other.close()
            other.deleteLater()
        QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.BackButton)
        self.assertEqual(self.w.history_at, at-1)

    def test_real_qfile_recycle_closes_active_and_cached_pages_and_allows_window_close(self):
        import os
        if os.name != 'nt':
            self.skipTest('Native Windows recycle contract')
        workflow = self.open('Street.pcs-workflow')
        note = self.open('Notes.md')
        for tab in (workflow, note):
            path = tab.path
            self.assertTrue(path.is_file())
            self.assertTrue(self.w.commands.trash(self.context(path=path)))
            self.assertFalse(path.exists())
            self.assertNotIn(tab, self.w.all_pages())
            self.assertNotIn(tab.key, self.w.autosave.timers)
            self.assertNotIn(id(tab), self.w.autosave.paused)
        self.assertTrue(self.w.close())

    def test_missing_file_retains_live_edits_and_save_as_recovers_then_closes(self):
        from prompt_calculus_studio.local_documents import io
        tab = self.open('Street.pcs-workflow')
        original = tab.path
        original.unlink()  # Simulated external deletion of this synthetic file.
        self.assertFalse(self.w.save_tab(tab))
        self.assertIn('Ctrl+Shift+S', tab.document.error)
        self.assertTrue(tab.dirty)
        self.assertFalse(self.w.close())
        before = set(self.root.glob(original.name+'.recovery-*'))
        self.assertFalse(self.w.save_tab(tab))
        self.assertEqual(set(self.root.glob(original.name+'.recovery-*')), before)
        target = self.w.commands.files.run('save_as', self.context(tab=tab),
                                          name='Recovered', directory=self.root)
        self.assertEqual(target, self.root/'Recovered.pcs-workflow')
        self.assertFalse(original.exists())
        self.assertEqual(io.load(self.root, target.name).document['document_id'], tab.document.key)
        self.assertFalse(tab.dirty)
        self.assertTrue(self.w.close())


if __name__ == '__main__':
    unittest.main()
