"""Timed file writes, failed-close retention and non-layout notifications."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox, QStatusBar

from prompt_calculus_studio import drafts, materials
from prompt_calculus_studio.local_documents import io, schema
import test_workspace_shell as fixture


class WorkspaceAutosaveTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events
    open = fixture.WorkspaceShellTests.open
    focus = fixture.WorkspaceShellTests.focus
    editor = fixture.WorkspaceShellTests.editor

    def setUp(self):
        fixture.WorkspaceShellTests.setUp(self)
        self.w.autosave.delay_ms = 100

    def settled(self):
        QTest.qWait(220)
        self.events()

    def test_markdown_coalesces_edits_preserves_encoding_undo_and_closes_latest_text(self):
        tab = self.open('Notes', 'md', True)
        before = tab.path.read_bytes()
        page = tab.widget
        page.moveCursor(page.textCursor().MoveOperation.End)
        with patch.object(page, 'save', wraps=page.save) as writes:
            for text in ('first', ' second', ' third'):
                QTest.keyClicks(page, text)
                QTest.qWait(25)
            self.assertEqual(tab.path.read_bytes(), before)
            self.settled()
            self.assertEqual(writes.call_count, 1)
        self.assertEqual(tab.path.read_bytes(), before+b'first second third')
        self.assertFalse(tab.dirty)
        self.assertTrue(page.document().isUndoAvailable())
        QTest.keyClick(page, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.settled()
        self.assertNotEqual(tab.path.read_bytes(), before+b'first second third')
        QTest.keyClick(page, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(page, ' final')
        with patch.object(QMessageBox, 'exec', side_effect=AssertionError('modal save prompt')):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertEqual(tab.path.read_bytes(), before+b'first second third final')
        reopened = self.open('Notes', 'md', True)
        self.assertTrue(reopened.widget.toPlainText().endswith('third final'))

    def test_workflow_empty_manual_draft_restore_and_undo_survive_autosave(self):
        tab = self.open('Street', 'pcs-workflow', True)
        host = tab.document.host
        key = next(iter(host.state['multi_output']['outputs']))
        other_file = self.root/'Night.pcs-workflow'
        other_bytes = other_file.read_bytes()
        before = copy.deepcopy(host.state['multi_output']['outputs'][key])
        panel = host.canvas.outputs[key].panel
        host.canvas.commit(lambda state: drafts.edit(state, '', key))
        self.settled()
        output = io.load(self.root, tab.path.name).document['payload']['state']['multi_output']['outputs'][key]
        self.assertEqual(output['draft'], '')
        self.assertEqual(panel.draft_status.text(), 'Prompt 預覽')
        self.assertEqual(panel.clear.text(), '還原')
        self.assertTrue(panel.clear.isVisible())
        self.assertFalse(tab.dirty)
        panel.clear.click()
        self.settled()
        self.assertIsNone(host.state['multi_output']['outputs'][key]['draft'])
        self.assertFalse(panel.clear.isVisible())
        host.canvas.undo()
        self.settled()
        self.assertEqual(host.state['multi_output']['outputs'][key]['draft'], '')
        host.canvas.redo()
        self.settled()
        self.assertIsNone(host.state['multi_output']['outputs'][key]['draft'])
        self.assertEqual(other_file.read_bytes(), other_bytes)
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        reopened = self.open('Street', 'pcs-workflow', True)
        actual = reopened.document.host.state['multi_output']['outputs'][key]
        self.assertIsNone(actual['draft'])
        self.assertEqual(actual['canvas'], before['canvas'])

    def test_material_dialog_cancel_is_not_accepted_by_background_autosave(self):
        tab = self.open(retained=True)
        before = tab.path.read_bytes()
        ident, dialog = self.editor(tab)
        QTest.keyClicks(dialog.prompt, ' cancelled')
        self.settled()
        self.assertEqual(tab.path.read_bytes(), before)
        self.assertIs(tab.document.host.prompt_editor.active_dialog, dialog)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.settled()
        self.assertEqual(tab.path.read_bytes(), before)
        ident, dialog = self.editor(tab)
        QTest.keyClicks(dialog.prompt, ' applied')
        expected = dialog.prompt.toPlainText()
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.settled()
        self.assertFalse(tab.dirty)
        self.assertEqual(io.load(self.root, tab.path.name).document['payload']['items'][0]['prompt'], expected)
        tab.document.host.canvas.undo()
        self.settled()
        self.assertNotEqual(io.load(self.root, tab.path.name).document['payload']['items'][0]['prompt'], expected)

    def test_background_tabs_save_to_their_own_paths(self):
        a = self.open('Notes', 'md', True)
        b = self.open('Empty', 'md', True)
        QTest.keyClicks(a.widget, 'A')
        QTest.keyClicks(b.widget, 'B')
        self.w.new_tab()
        self.settled()
        self.assertTrue(a.path.read_bytes().startswith(b'\xef\xbb\xbfA'))
        self.assertEqual(b.path.read_text(), 'B')
        self.assertFalse(a.dirty or b.dirty)

    def test_failed_write_keeps_recovery_and_only_shows_an_expiring_notice(self):
        tab = self.open('Notes', 'md', True)
        before = tab.path.read_bytes()
        geometry = self.w.centralWidget().geometry()
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            QTest.keyClicks(tab.widget, 'unsaved')
            self.settled()
            self.assertTrue(tab.dirty)
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertNotIn(tab.key, self.w.tabs_by_key)
        self.assertEqual(tab.path.read_bytes(), before)
        self.assertIn('恢復副本', self.w.notification.text())
        self.assertTrue(self.w.notification.isVisible())
        self.assertIsNone(self.app.activeModalWidget())
        self.assertEqual(self.w.centralWidget().geometry(), geometry)
        self.w.notification.expire.start(10)
        self.events()
        self.assertFalse(self.w.notification.isVisible())
        self.assertEqual(self.w.centralWidget().geometry(), geometry)
        tab=self.open('Notes','md',True)
        self.assertTrue(self.w.save_current())
        self.assertFalse(tab.dirty)
        self.assertTrue(tab.path.read_bytes().startswith(b'\xef\xbb\xbfunsaved'))

    def test_external_change_stops_retries_and_retains_local_draft_on_close(self):
        tab = self.open('Street', 'pcs-workflow', True)
        key = next(iter(tab.document.host.state['multi_output']['outputs']))
        external = io.load(self.root, tab.path.name)
        external.document['payload']['state']['multi_output']['outputs'][key]['name'] = 'External'
        self.assertEqual(io.save(self.root, tab.path.name, external.document, external.receipt).status, 'saved')
        original = tab.path.read_bytes()
        tab.document.host.canvas.commit(lambda state: drafts.edit(state, 'local text', key))
        self.settled()
        self.assertTrue(tab.dirty)
        self.assertEqual(tab.path.read_bytes(), original)
        retained = tuple(io.recovery_files(self.root, tab.path.name))
        self.settled()
        self.assertEqual(tuple(io.recovery_files(self.root, tab.path.name)), retained)
        with patch.object(QMessageBox, 'exec', side_effect=AssertionError('modal save prompt')):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertNotIn(tab.key, self.w.tabs_by_key)
        candidates = [Path(p) for p in tab.document.last_result.recovery_paths if p.endswith('.candidate')]
        self.assertTrue(candidates)
        self.assertEqual(schema.decode(candidates[0].read_bytes())['payload']['state']['multi_output']['outputs'][key]['draft'], 'local text')
        self.assertIn('恢復副本', self.w.notification.text())

    def test_window_close_flushes_all_files_before_removing_tabs(self):
        a = self.open('Notes', 'md', True)
        b = self.open('Empty', 'md', True)
        QTest.keyClicks(a.widget, 'A')
        QTest.keyClicks(b.widget, 'B')
        self.assertTrue(self.w.close())
        self.assertTrue(a.path.read_bytes().startswith(b'\xef\xbb\xbfA'))
        self.assertEqual(b.path.read_text(), 'B')

    def test_notifications_leave_content_flush_with_bottom_on_all_routes_and_positions(self):
        tab = self.open('Street', 'pcs-workflow', True)
        tab.document.host.notice('Canvas notice')
        self.events()
        for position in ('side', 'top'):
            self.w.set_navigation_position(position)
            for route in ('home', 'gallery', 'explore', 'export', 'settings'):
                with self.subTest(position=position, route=route):
                    self.w.open_route(route)
                    self.events()
                    before = self.w.centralWidget().geometry()
                    self.w.show_notice('Transient workspace notification')
                    self.events()
                    self.assertEqual(self.w.findChildren(QStatusBar), [])
                    self.assertEqual(self.w.centralWidget().geometry(), before)
                    bottom = self.w.workspace_stack.mapTo(self.w, QPoint(0, self.w.workspace_stack.height())).y()
                    self.assertEqual(bottom, self.w.height())
                    self.w.notification.expire.start(10)
                    self.events()
                    self.assertFalse(self.w.notification.isVisible())
                    self.assertEqual(self.w.centralWidget().geometry(), before)


if __name__ == '__main__':
    unittest.main()
