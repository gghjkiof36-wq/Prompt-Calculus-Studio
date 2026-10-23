"""Workspace-wide Markdown mode changes preserve document-owned content/history."""
import json
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from PySide6.QtGui import QInputMethodEvent, QTextCursor

from prompt_calculus_studio.workspace_layout import RestoreView, view_state
from prompt_calculus_studio.markdown_relations import open_link
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from run_workspace import open_initial_documents
import test_workspace_panes as fixture


class GlobalMarkdownModeTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def choose(self, tab, label):
        self.w.focus_tab(tab)
        tab.widget.md_surface.mode_menu()
        menu = self.app.activePopupWidget()
        self.assertIsNotNone(menu)
        next(action for action in menu.actions() if action.text() == label).trigger()
        menu.close()
        self.events()

    def modes(self, expected):
        pages = [tab.widget for tab in self.w.all_pages() if hasattr(tab.widget, 'md_surface')]
        self.assertTrue(pages)
        self.assertEqual({page.md_surface.mode for page in pages}, {expected})
        self.assertEqual(self.w.markdown_mode, expected)

    def test_menu_choice_applies_to_split_existing_cached_and_new_documents(self):
        left = self.open('Notes.md', retained=True)
        right = self.open('Empty.md', retained=True, new_tab=True)
        self.w.move_view(right)
        original = {tab.path: tab.path.read_bytes() for tab in (left, right)}
        for label, mode in (('原文', 'source'), ('閱讀', 'read'), ('編寫', 'write')):
            with self.subTest(mode=mode):
                self.choose(left, label)
                self.modes(mode)
                self.assertEqual(self.w.active_key, left.key)
                self.w.focus_tab(right); self.events(); self.modes(mode)
                self.assertFalse(left.dirty); self.assertFalse(right.dirty)
                self.assertFalse(left.widget.document().isUndoAvailable())
                self.assertEqual({path: path.read_bytes() for path in original}, original)
                path = self.root / (mode + '.md'); path.write_text('# New\n', encoding='utf-8')
                new = self.w.open_path(path)
                self.events()
                self.assertEqual(new.widget.md_surface.mode, mode)
                self.w.open_path(self.root / 'Notes.md')
                self.events(); self.modes(mode)
        self.assertNotIn('markdown_mode', view_state(left))

    def test_unsaved_empty_drafts_undo_redo_save_as_and_original_are_isolated(self):
        left = self.open('Empty.md', retained=True)
        duplicate = self.w.duplicate_view(left, True)
        other = self.open('Notes.md', retained=True, new_tab=True)
        left.widget.insertPlainText('draft A')
        other.widget.moveCursor(QTextCursor.MoveOperation.End)
        other.widget.insertPlainText('draft B')
        draft_a, draft_b = left.widget.encoded(), other.widget.encoded()
        source_a, source_b = left.path.read_bytes(), other.path.read_bytes()
        self.choose(other, '閱讀'); self.modes('read')
        self.assertEqual(left.widget.encoded(), draft_a)
        self.assertEqual(other.widget.encoded(), draft_b)
        self.assertTrue(left.dirty); self.assertTrue(other.dirty)
        self.assertEqual(left.path.read_bytes(), source_a)
        self.assertEqual(other.path.read_bytes(), source_b)
        self.choose(duplicate, '原文')
        duplicate.widget.undo()
        self.assertEqual(left.widget.toPlainText(), '')
        self.assertEqual(other.widget.encoded(), draft_b)
        duplicate.widget.redo()
        self.assertEqual(left.widget.encoded(), draft_a)
        context = self.w.commands.context('tab', tab=left)
        target = self.w.commands.files.run('save_as', context, name='Saved draft', directory=self.root)
        self.assertEqual(target.read_bytes(), draft_a)
        self.assertEqual(left.path, duplicate.path)
        self.assertEqual((self.root / 'Empty.md').read_bytes(), source_a)
        reopened = self.w.open_path(self.root / 'Empty.md', retained=True, new_tab=True)
        self.events()
        self.assertEqual(reopened.widget.md_surface.mode, 'source')
        self.assertEqual(reopened.widget.toPlainText(), '')
        self.assertIsNot(reopened.widget.buffer, left.widget.buffer)
        duplicate.widget.undo()
        self.assertEqual(left.widget.toPlainText(), '')
        self.assertEqual(reopened.widget.toPlainText(), '')
        self.assertEqual(other.widget.encoded(), draft_b)
        duplicate.widget.redo()
        self.assertEqual(left.widget.encoded(), draft_a)

    def test_late_per_page_restore_does_not_publish_an_old_mode(self):
        left = self.open('Notes.md', retained=True)
        right = self.open('Empty.md', retained=True, new_tab=True)
        stale = RestoreView(left, dict(markdown_mode='read', cursor=[2, 2], scroll=[0, 0]))
        self.choose(right, '原文')
        stale.finish(); stale.apply()
        self.modes('source')
        self.assertEqual(left.widget.textCursor().position(), 2)
        self.choose(left, '閱讀')
        RestoreView(right, dict(markdown_mode='write')).finish()
        self.modes('read')
        self.assertFalse(left.dirty); self.assertFalse(right.dirty)

    def test_restart_global_preference_wins_over_old_mixed_page_states(self):
        left = self.open('Notes.md', retained=True)
        self.open('Empty.md', retained=True, new_tab=True)
        self.choose(left, '原文')
        data = self.w.data
        self.assertTrue(self.w.close()); self.w.deleteLater(); self.events()
        path = data / 'workspace-layout.json'
        layout = json.loads(path.read_text(encoding='utf-8'))
        for pane in layout['panes']:
            for view in pane['views']:
                view['state']['markdown_mode'] = 'read' if view['path'].endswith('Notes.md') else 'write'
        path.write_text(json.dumps(layout), encoding='utf-8')
        self.w = WorkspaceShell(self.root, data)
        self.w.report_error = self.errors.append; self.w.autosave.delay_ms = 60000
        self.w.show(); self.events()
        self.modes('source')
        self.assertEqual(json.loads((data / 'workspace-ui.json').read_text())['markdown_mode'], 'source')
        for tab in self.w.tabs_by_key.values():
            if tab.path and tab.path.suffix == '.md':
                self.w.focus_tab(tab); self.events(); self.modes('source')

    def test_background_large_file_uses_latest_mode_after_loading_without_read_render(self):
        first = self.open('Notes.md', retained=True)
        path = self.root / 'Large.md'
        original = ('line\n' * 20002).encode(); path.write_bytes(original)
        large = self.w.open_path(path, retained=True, new_tab=True, background=True)
        page = large.widget; loading = page.buffer.source_loading
        loading.timer.stop()
        self.assertTrue(page.buffer.opening)
        with patch.object(page.md_surface.reader, 'refresh', side_effect=AssertionError('hidden reader rendered')):
            first.widget.md_surface.set_mode('read')
            self.assertEqual(page.md_surface.pending_mode, 'read')
            first.widget.md_surface.set_mode('source')
            # An older source-ready expectation must not overwrite the owner.
            page.md_surface.pending_mode = 'read'
            loading.timer.start()
            deadline = time.monotonic() + 15
            while page.buffer.opening and time.monotonic() < deadline:
                self.app.processEvents(); time.sleep(.005)
            self.assertFalse(page.buffer.opening)
            self.assertEqual(page.md_surface.mode, 'source')
            self.assertIsNone(page.md_surface.reader.rich)
        self.w.focus_tab(large); self.events(); self.modes('source')
        self.assertEqual(page.encoded(), original)
        self.assertFalse(page.dirty)
        self.assertFalse(page.document().isUndoAvailable())

    def test_other_pane_composition_blocks_global_change_until_commit(self):
        left = self.open('Notes.md', retained=True)
        right = self.open('Empty.md', retained=True, new_tab=True)
        self.w.move_view(right)
        self.app.sendEvent(left.widget.md_surface.editor, QInputMethodEvent('中文', []))
        self.assertTrue(left.widget.buffer.composing)
        self.assertFalse(right.widget.md_surface.set_mode('read'))
        self.modes('write')
        commit = QInputMethodEvent(); commit.setCommitString('中文')
        self.app.sendEvent(left.widget.md_surface.editor, commit)
        self.assertTrue(right.widget.md_surface.set_mode('read'))
        self.modes('read')
        self.assertIn('中文', left.widget.toPlainText())
        self.assertEqual(right.widget.toPlainText(), '')

    def test_source_links_to_new_and_existing_notes_keep_the_global_mode(self):
        first = self.open('Notes.md', retained=True)
        self.choose(first, '原文')
        target = open_link(first.widget.md_surface, 'Empty.md')
        self.events(); self.modes('source')
        self.assertEqual(target.path, self.root / 'Empty.md')
        target.widget.insertPlainText('unsaved linked note')
        reopened = open_link(first.widget.md_surface, 'Empty.md')
        self.assertIs(reopened, target)
        self.modes('source')
        self.assertEqual(target.widget.toPlainText(), 'unsaved linked note')
        self.assertEqual(target.path.read_bytes(), b'')

    def test_visible_inactive_pane_source_ready_keeps_focus_and_navigation_local(self):
        first = self.open('Notes.md', retained=True)
        other = self.open('Empty.md', retained=True, new_tab=True)
        other.widget.insertPlainText('other content')
        self.w.move_view(other)
        self.choose(first, '原文')
        first.widget.md_surface.editor.setFocus(); self.events()
        focus = self.app.focusWidget()
        self.assertTrue(other.widget.md_surface.isVisible())
        other.widget.md_surface.pending_mode = 'read'
        other.widget.md_surface.pending_position = 3
        other.widget.md_surface.source_ready()
        self.events()
        self.assertEqual(self.w.active_key, first.key)
        self.assertIs(self.app.focusWidget(), focus)
        self.assertEqual(other.widget.textCursor().position(), 3)
        self.modes('source')

    def test_global_read_does_not_render_hidden_pages_with_queued_refresh(self):
        first = self.open('Notes.md', retained=True)
        hidden = self.w.open_path(self.root / 'Empty.md', retained=True, new_tab=True, background=True)
        hidden.widget.insertPlainText('draft still belongs to hidden note')
        with patch.object(hidden.widget.md_surface.reader, 'refresh') as rendered:
            first.widget.md_surface.set_mode('read')
            deadline = time.monotonic() + .12
            while time.monotonic() < deadline:
                self.app.processEvents(); time.sleep(.005)
            rendered.assert_not_called()
        self.assertTrue(hidden.dirty)
        self.assertEqual(hidden.path.read_bytes(), b'')
        self.w.focus_tab(hidden); self.events()
        self.assertIn('draft still belongs to hidden note', hidden.widget.md_surface.reader.toPlainText())

    def test_welcome_entry_respects_an_already_chosen_mode(self):
        self.w.markdown_mode = 'source'; self.w._markdown_mode_chosen = True
        self.w.save_ui_preferences()
        path = self.root / '歡迎使用.md'; path.write_text('# Welcome\n', encoding='utf-8')
        tab = open_initial_documents(self.w)
        self.events()
        self.assertEqual(tab.path, path)
        self.modes('source')
        self.assertFalse(tab.dirty)


if __name__ == '__main__':
    unittest.main()
