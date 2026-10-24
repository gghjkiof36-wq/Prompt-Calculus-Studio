"""P4 merge/close/restart flows preserve files and live view ownership."""
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QSaveFile
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import test_workspace_panes as fixture
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.workspace_layout import MissingDocument
from prompt_calculus_studio.workspace_home import WorkspaceHome


class WorkspaceLayoutTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def ordered(self, pane):
        return [self.w.tabs_by_key[pane.strip.bar.tabData(i)] for i in range(pane.strip.bar.count())]

    def restart(self):
        data = self.w.data
        self.assertTrue(self.w.close())
        self.w.deleteLater()
        self.events()
        self.w = WorkspaceShell(self.root, data)
        self.w.report_error = self.errors.append
        self.w.autosave.delay_ms = 60000
        self.w.show()
        self.events()

    def test_restart_applies_saved_ratio_to_visible_splitter(self):
        self.open('Street.pcs-workflow')
        self.w.open_path(self.root/'Notes.md', pane=self.w.ensure_split())
        self.events()
        self.w.pane_split.setSizes([750, 250])
        self.w.remember_ratio()
        expected = self.w.split_ratio
        self.restart()
        sizes = self.w.pane_split.sizes()
        self.assertAlmostEqual(sizes[0]/sum(sizes), expected, delta=.01)

    def test_merge_left_then_right_keeps_duplicate_view_drafts_cursor_and_undo(self):
        note = self.open('Notes.md')
        self.open('Scene.pcs-category', new_tab=True)
        self.open('Street.pcs-workflow', new_tab=True)
        duplicate = self.w.duplicate_view(note, True)
        active = self.open('Night.pcs-workflow', new_tab=True)
        expected = [*self.ordered(self.w.panes[0]), *self.ordered(self.w.panes[1])]
        self.assertEqual(len(expected), 5)
        note.widget.moveCursor(QTextCursor.MoveOperation.End)
        note.widget.insertPlainText('共同內容')
        duplicate.widget.moveCursor(QTextCursor.MoveOperation.Start)
        cursors = note.widget.textCursor().position(), duplicate.widget.textCursor().position()
        before = {t.path: t.path.read_bytes() for t in expected}
        original_widgets = [t.widget for t in expected]
        with patch.object(self.w, 'save_tab', wraps=self.w.save_tab) as save:
            self.assertTrue(self.w.panes[1].strip.overflow.isVisible())
            QTest.mouseClick(self.w.panes[1].strip.overflow, Qt.MouseButton.LeftButton)
            menu = self.app.activePopupWidget()
            self.assertIsNotNone(menu)
            next(action for action in menu.actions() if action.text() == '合回單區').trigger()
            menu.close()
            self.events()
            save.assert_not_called()
        self.assertEqual(len(self.w.panes), 1)
        self.assertEqual(self.ordered(self.w.panes[0]), expected)
        self.assertEqual([t.widget for t in self.ordered(self.w.panes[0])], original_widgets)
        self.assertEqual(self.w.active_key, active.key)
        self.assertEqual((note.widget.textCursor().position(), duplicate.widget.textCursor().position()), cursors)
        self.assertTrue(note.dirty)
        self.assertEqual({p: p.read_bytes() for p in before}, before)
        self.w.focus_tab(duplicate)
        QTest.keyClick(duplicate.widget, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertNotIn('共同內容', note.widget.toPlainText())
        self.w.move_view(duplicate)
        self.assertEqual(len(self.w.panes), 2)
        self.assertIs(note.widget.document(), duplicate.widget.document())

    def test_close_side_saves_each_file_once_and_keeps_conflicting_views(self):
        import base64
        from prompt_calculus_studio.draft_cache import DraftCache
        from prompt_calculus_studio.workspace_recovery import key
        note = self.open('Notes.md')
        duplicate = self.w.duplicate_view(note, True)
        third = self.w.duplicate_view(duplicate)
        clean = self.open('Empty.md', new_tab=True)
        clean.widget.insertPlainText('save this file')
        note.widget.insertPlainText('unsaved local text')
        expected = note.widget.encoded()
        note.path.write_text('external edit', encoding='utf-8')
        left, right = self.w.panes
        left_keys = [t.key for t in self.ordered(left)]
        # Retain the conflicting views when neither the source nor a fresh
        # durable recovery record can be written. Memory alone is insufficient.
        with patch.object(self.w, 'save_tab', wraps=self.w.save_tab) as save, \
             patch.object(self.w.close_recovery.cache, 'write', side_effect=OSError('recovery unavailable')):
            self.assertFalse(self.w.close_pane_tabs(right))
        self.assertEqual([c.args[0].path.name for c in save.call_args_list], ['Notes.md', 'Empty.md'])
        self.assertEqual(self.ordered(right), [duplicate, third])
        self.assertEqual([t.key for t in self.ordered(left)], left_keys)
        self.assertEqual(self.w.active_key, duplicate.key)
        self.assertTrue(duplicate.dirty)
        self.assertEqual(note.path.read_text(encoding='utf-8'), 'external edit')
        self.assertEqual(clean.path.read_text(encoding='utf-8'), 'save this file')
        self.assertIsNone(DraftCache(self.w.data).read(key(note.path)))
        # An existing source with a durable recovery record can still close.
        self.assertTrue(self.w.close_pane_tabs(right))
        self.assertEqual(len(self.w.panes), 1)
        self.assertEqual(note.widget.encoded(), expected)
        cached = DraftCache(self.w.data).read(key(note.path))
        self.assertEqual(base64.b64decode(cached['raw']), expected)
        self.assertEqual(note.path.read_text(encoding='utf-8'), 'external edit')

    def test_last_view_close_removes_the_split_and_empty_home_can_be_closed(self):
        note = self.open('Notes.md')
        right = self.w.duplicate_view(note, True)
        note.widget.insertPlainText('保留左側')
        self.w.focus_tab(right)
        close = self.w.panes[1].strip.bar.tabButton(0, self.w.tab_strip.bar.ButtonPosition.RightSide)
        QTest.mouseClick(close, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(len(self.w.panes), 1)
        self.assertEqual(self.w.active_key, note.key)
        self.assertIn('保留左側', note.widget.toPlainText())
        self.assertTrue(note.widget.document().isUndoAvailable())
        pane = self.w.ensure_split()
        placeholder = self.ordered(pane)[0]
        self.assertIsInstance(placeholder.widget, WorkspaceHome)
        self.assertTrue(self.w.close_tab(0, pane=pane))
        self.assertEqual(len(self.w.panes), 1)
        self.w.duplicate_view(note, True)
        menu_pane = self.w.panes[1]
        QTest.mouseClick(menu_pane.strip.overflow, Qt.MouseButton.LeftButton)
        menu = self.app.activePopupWidget()
        next(action for action in menu.actions() if action.text() == '關閉此分區').trigger()
        menu.close()
        self.events()
        self.assertEqual(len(self.w.panes), 1)
        self.assertIn(note.key, self.w.tabs_by_key)

    def test_restart_restores_order_focus_shared_model_and_view_positions(self):
        note = self.open('Notes.md')
        note.widget.setPlainText('\n'.join(f'private editor text {i}' for i in range(80)))
        flow = self.open('Street.pcs-workflow', new_tab=True)
        duplicate = self.w.duplicate_view(note, True)
        self.open('Scene.pcs-category', new_tab=True)
        note.widget.moveCursor(QTextCursor.MoveOperation.End)
        duplicate.widget.moveCursor(QTextCursor.MoveOperation.Start)
        cursor = duplicate.widget.textCursor()
        cursor.setPosition(7, QTextCursor.MoveMode.KeepAnchor)
        duplicate.widget.setTextCursor(cursor)
        flow.host.canvas.view.resetTransform()
        flow.host.canvas.view.scale(.8, .8)
        self.w.pane_split.setSizes([400, 700])
        self.w.remember_ratio()
        ratio = self.w.split_ratio
        self.w.focus_tab(note)
        self.w.focus_tab(duplicate)
        expected = [[t.path for t in self.ordered(p)] for p in self.w.panes]
        self.restart()
        self.assertTrue(self.w.layout_restored)
        self.assertEqual([[t.path for t in self.ordered(p)] for p in self.w.panes], expected)
        a, b = self.ordered(self.w.panes[0])[0], self.ordered(self.w.panes[1])[0]
        self.assertIs(a.widget.document(), b.widget.document())
        self.assertEqual(a.widget.textCursor().position(), len(a.widget.toPlainText()))
        self.assertEqual((b.widget.textCursor().position(), b.widget.textCursor().anchor()), (7, 0))
        self.assertIs(self.w.active_pane, self.w.panes[1])
        self.assertEqual(self.w.active_key, b.key)
        self.assertAlmostEqual(self.w.split_ratio, ratio, places=3)
        saved = (self.w.data/'workspace-layout.json').read_text(encoding='utf-8')
        self.assertNotIn('private editor text', saved)
        flow = self.ordered(self.w.panes[0])[1]
        self.w.focus_tab(flow)
        self.events()
        self.assertAlmostEqual(flow.host.canvas.view.transform().m11(), .8, places=3)
        self.assertFalse(a.widget.document().isUndoAvailable())

    def test_missing_file_keeps_slot_and_retry_recovers_without_creating_bytes(self):
        note = self.open('Notes.md')
        self.w.move_view(note)
        self.open('Street.pcs-workflow', pane=self.w.panes[0])
        data, path = self.w.data, note.path
        self.assertTrue(self.w.close())
        self.w.deleteLater()
        self.events()
        moved = path.with_name('Away.md')
        path.rename(moved)
        before = moved.read_bytes()
        self.w = WorkspaceShell(self.root, data)
        self.w.report_error = self.errors.append
        self.w.show()
        self.events()
        missing = self.ordered(self.w.panes[1])[0]
        self.assertIsInstance(missing.widget, MissingDocument)
        self.assertIsNotNone(self.ordered(self.w.panes[0])[0].document)
        QTest.mouseClick(missing.widget.retry_button, Qt.MouseButton.LeftButton)
        self.assertFalse(path.exists())
        self.assertIn(missing.key, self.w.tabs_by_key)
        moved.rename(path)
        QTest.mouseClick(missing.widget.retry_button, Qt.MouseButton.LeftButton)
        self.events()
        restored = self.ordered(self.w.panes[1])[0]
        self.assertNotIsInstance(restored.widget, MissingDocument)
        self.assertEqual(restored.path, path)
        self.assertEqual(path.read_bytes(), before)

    def test_layout_atomic_failure_keeps_previous_record_and_corruption_keeps_files(self):
        tab = self.open('Notes.md')
        self.assertTrue(self.w.layout_store.save())
        path = self.w.layout_store.path
        before = path.read_bytes()
        self.w.new_tab()
        with patch.object(QSaveFile, 'commit', return_value=False):
            self.assertFalse(self.w.layout_store.save())
        self.assertEqual(path.read_bytes(), before)
        original = tab.path.read_bytes()
        self.assertTrue(self.w.close())
        data = self.w.data
        self.w.deleteLater()
        self.events()
        path.write_text('{broken', encoding='utf-8')
        self.w = WorkspaceShell(self.root, data)
        self.w.report_error = self.errors.append
        self.w.show()
        self.events()
        self.assertFalse(self.w.layout_restored)
        self.assertEqual(len(self.w.panes), 1)
        self.assertIsInstance(self.ordered(self.w.panes[0])[0].widget, WorkspaceHome)
        self.assertEqual(tab.path.read_bytes(), original)

    def test_disconnected_primary_folder_is_a_placeholder_and_never_recreated(self):
        self.open('Notes.md')
        data = self.w.data
        self.assertTrue(self.w.close())
        self.w.deleteLater()
        self.events()
        moved = self.root.with_name('disconnected-files')
        self.assertEqual(moved.parent.resolve(), Path(self.area.name).resolve())
        self.assertEqual(self.root.parent.resolve(), Path(self.area.name).resolve())
        self.root.rename(moved)
        try:
            self.w = WorkspaceShell(self.root, data)
            self.w.report_error = self.errors.append
            self.w.show()
            self.events()
            tab = self.ordered(self.w.panes[0])[0]
            self.assertIsInstance(tab.widget, MissingDocument)
            self.assertFalse(self.root.exists())
            moved.rename(self.root)
            tab.widget.retry_button.click()
            self.events()
            self.assertNotIsInstance(self.ordered(self.w.panes[0])[0].widget, MissingDocument)
        finally:
            if moved.exists():moved.rename(self.root)


if __name__ == '__main__':unittest.main()
