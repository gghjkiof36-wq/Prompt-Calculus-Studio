"""P4 user-visible view ownership, shared content and file lifetimes."""
import unittest
import copy
from unittest.mock import patch, PropertyMock
from pathlib import Path
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QTextCursor, QInputMethodEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
import test_workspace_shell as fixture


class WorkspacePaneTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])
    setUp = fixture.WorkspaceShellTests.setUp
    events = fixture.WorkspaceShellTests.events

    def tearDown(self):
        for pane in self.w.panes:
            while pane.strip.bar.count():
                self.assertTrue(self.w.close_tab(0, decision='discard', make_home=False, pane=pane),
                                (self.errors, pane.strip.bar.tabData(0), list(self.w.tabs_by_key)))
        fixture.WorkspaceShellTests.tearDown(self)

    def open(self, name, **kwargs):
        tab = self.w.open_path(self.root/name, **kwargs)
        self.events()
        self.assertIsNotNone(tab, self.errors)
        return tab

    def test_same_markdown_shared_text_undo_independent_cursor_and_save_as(self):
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        self.events()
        self.assertEqual(len(self.w.panes), 2)
        self.assertIs(left.widget.document(), right.widget.document())
        right.widget.moveCursor(QTextCursor.MoveOperation.Start)
        left.widget.moveCursor(QTextCursor.MoveOperation.End)
        preedit = QInputMethodEvent('中文', [])
        self.app.sendEvent(left.widget, preedit)
        self.assertNotIn('中文', right.widget.toPlainText())
        committed = QInputMethodEvent()
        committed.setCommitString('中文 A')
        self.app.sendEvent(left.widget, committed)
        self.assertEqual(left.widget.toPlainText(), right.widget.toPlainText())
        self.assertEqual(right.widget.textCursor().position(), 0)
        right.widget.undo()
        self.assertNotIn('中文 A', left.widget.toPlainText())
        left.widget.redo()
        before = (self.root/'Notes.md').read_bytes()
        context = self.w.commands.context('tab', tab=right)
        copied = self.w.commands.files.run('save_as', context, name='Copy', directory=self.root)
        self.assertEqual(copied, self.root/'Copy.md')
        self.assertEqual(left.path, right.path)
        right.widget.insertPlainText(' B')
        self.assertTrue(self.w.save_tab(left))
        self.assertEqual((self.root/'Notes.md').read_bytes(), before)
        self.assertIn(' B', copied.read_text(encoding='utf-8-sig'))

    def test_canvas_shared_model_history_and_separate_camera(self):
        left = self.open('Street.pcs-workflow')
        right = self.w.duplicate_view(left, True)
        self.events()
        a, b = left.host.canvas, right.host.canvas
        b.view.scale(.7, .7)
        transform = b.view.transform()
        self.assertIs(left.host.state, right.host.state)
        key = next(iter(a.data()['canvases']))
        self.assertTrue(a.commit(lambda s: s['multi_output']['canvases'][key].update(name='共同畫布')))
        self.events()
        self.assertEqual(b.data()['canvases'][key]['name'], '共同畫布')
        self.assertIs(a.undo_stack, b.undo_stack)
        self.assertEqual(b.view.transform(), transform)
        b.undo()
        self.events()
        self.assertNotEqual(a.data()['canvases'][key]['name'], '共同畫布')
        a.redo()
        self.events()
        self.assertEqual(b.data()['canvases'][key]['name'], '共同畫布')
        self.assertIs(left.host.comfy, right.host.comfy)

    def test_navigation_uses_active_pane_and_explicit_new_always_adds(self):
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        self.events()
        original = self.w.panes[0].strip.bar.count()
        replacement = self.open('Empty.md')
        self.assertEqual(replacement.pane, right.pane)
        self.assertEqual(self.w.panes[0].strip.bar.count(), original)
        same_file = self.open('Notes.md')
        self.assertIs(same_file.widget.document(), left.widget.document())
        duplicate = self.open('Notes.md', new_tab=True)
        self.assertIsNot(duplicate, same_file)
        self.assertEqual(self.w.tab_strip.bar.count(), 2)
        self.assertIs(self.open('Notes.md'), same_file)

    def test_move_keeps_view_and_captured_creation_target(self):
        tab = self.open('Notes.md')
        tab.widget.insertPlainText('keep')
        widget = tab.widget
        context = self.w.commands.context('home')
        self.w.move_view(tab)
        self.events()
        self.assertIs(tab.widget, widget)
        self.assertEqual(self.w.pane_for(tab), self.w.panes[1])
        self.assertEqual(self.w.panes[0].strip.bar.count(), 1)
        self.assertTrue(tab.widget.document().isUndoAvailable())
        self.w.new_tab(pane=self.w.panes[0])
        created = self.w.commands.new('markdown', context, name='Created')
        self.assertEqual(created.pane, tab.pane)

    def test_multiview_workflow_save_as_and_rename_update_every_writer(self):
        left = self.open('Street.pcs-workflow')
        right = self.w.duplicate_view(left, True)
        self.events()
        before = left.path.read_bytes()
        old_id = left.document.key
        key = next(iter(left.host.canvas.data()['canvases']))
        left.host.canvas.commit(lambda s: s['multi_output']['canvases'][key].update(name='另存內容'))
        context = self.w.commands.context('tab', tab=right)
        target = self.w.commands.files.run('save_as', context, name='Copied', directory=self.root)
        self.assertEqual(target, self.root/'Copied.pcs-workflow', self.errors)
        self.assertIs(left.document, right.document)
        self.assertNotEqual(old_id, left.document.key)
        self.assertEqual(left.path, right.path)
        right.host.canvas.undo()
        self.assertTrue(self.w.save_tab(right))
        self.assertEqual((self.root/'Street.pcs-workflow').read_bytes(), before)
        renamed = self.w.commands.files.run('rename', self.w.commands.context('tab', tab=left), name='Renamed')
        self.assertEqual(left.path, renamed)
        self.assertEqual(right.path, renamed)

    def test_execution_controls_and_delayed_results_keep_the_frozen_document(self):
        from prompt_calculus_studio.document_host import LocalEditingClient
        a = self.open('Street.pcs-workflow')
        self.w.move_view(a)
        b = self.open('Night.pcs-workflow', pane=self.w.panes[0])
        same = self.w.duplicate_view(a)
        self.events()
        self.assertIs(a.host.comfy, same.host.comfy)
        calls, stopped = [], []
        def submit(client, count=1):
            calls.append((client, client.window.document_id, copy.deepcopy(client.window.state), count))
            client.connected = True
            client.run_id = 'synthetic-'+str(len(calls))
            client.input_flow.has_work = lambda: True
            client.input_flow.last_status = dict(queued_ids=[client.run_id], running_ids=[])
            client.stateChanged.emit()
        def stop(client, clear_pending=False):stopped.append(client.window.document_id)
        with patch.object(LocalEditingClient, 'can_run', new_callable=PropertyMock, return_value=True), \
                patch.object(LocalEditingClient, 'run', submit), patch.object(LocalEditingClient, 'interrupt', stop):
            for tab, count in ((a, 2), (b, 3)):
                self.w.focus_tab(tab)
                tab.host.comfy.stateChanged.emit()
                tab.host.run_controls.count.setValue(count)
                QTest.mouseClick(tab.host.run_controls.run_button, Qt.MouseButton.LeftButton)
            self.assertEqual([(c[1], c[3]) for c in calls], [(a.document.key, 2), (b.document.key, 3)])
            QTest.mouseClick(b.host.run_controls.stop, Qt.MouseButton.LeftButton)
            self.assertEqual(stopped, [b.document.key])
        execution = a.document.execution
        before = a.path.read_bytes()
        self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=a), name='TaskCopy', directory=self.root)
        self.assertIsNot(a.document.execution, execution)
        self.assertIsNone(execution.document)
        self.assertEqual(execution.document_id, calls[0][1])
        current = copy.deepcopy(a.host.state)
        execution.client.resultsReceived.emit([dict(id='late-result', owner=calls[0][1])])
        self.assertEqual(execution.received[-1]['id'], 'late-result')
        self.assertEqual(a.host.state, current)
        self.assertEqual((self.root/'Street.pcs-workflow').read_bytes(), before)
        self.w.merge_panes()
        self.assertEqual(len(self.w.panes), 1)
        for tab in (a, same):self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab))
        execution.client.resultsReceived.emit([dict(id='after-close', owner=calls[0][1])])
        self.assertEqual(execution.received[-1]['id'], 'after-close')
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':unittest.main()
