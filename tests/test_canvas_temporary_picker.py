"""Canvas-local creation from the picker, with real Qt forms and file saves."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QTimer, Qt
from PySide6.QtTest import QTest

from prompt_calculus_studio import multi_output
from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.local_documents import io, workflow
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from test_material_editor import state
from test_workspace_shell import WorkspaceShellTests


class CanvasTemporaryPickerTests(unittest.TestCase):
    setUpClass = classmethod(WorkspaceShellTests.setUpClass.__func__)
    events = WorkspaceShellTests.events

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-local-picker-')
        self.root = Path(self.area.name) / 'files'
        self.root.mkdir()
        self.path = self.root / 'Canvas.pcs-workflow'
        created = io.create(self.root, self.path.name, workflow.from_state(state()))
        self.assertEqual(created.status, 'saved', created.error)
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.errors.append(str(value))
        self.network = patch('prompt_calculus_studio.comfy_client.ComfyClient.request',
                             side_effect=AssertionError('No ComfyUI requests in Canvas picker tests'))
        self.requests = self.network.start()
        self.w = WorkspaceShell(self.root, Path(self.area.name) / 'data')
        self.w.autosave.delay_ms = 60000
        self.w.report_error = self.errors.append
        self.w.resize(1400, 950)
        self.w.show()
        self.tab = self.w.open_path(self.path, True, new_tab=True)
        self.events()
        self.assertIsNotNone(self.tab, self.errors)
        self.canvas = self.tab.host.canvas
        self.destination = next(iter(self.tab.host.state['multi_output']['canvases']))
        self.picker = CanvasPalette(self.canvas, QPointF(540, 330), self.destination)
        self.picker.show()
        self.events()

    def tearDown(self):
        if not self.picker.closed:
            self.picker.reject()
        try:
            WorkspaceShellTests.tearDown(self)
            self.requests.assert_not_called()
        finally:
            self.network.stop()

    def local_action(self):
        QTest.mouseClick(self.picker.new_button, Qt.MouseButton.LeftButton)
        self.events()
        actions = [a for a in self.picker.import_menu.actions() if not a.isSeparator()]
        self.assertEqual([a.text() for a in actions], ['新增臨時文字模塊', '選擇其他管理頁…'])
        self.assertEqual(self.w.material_transfer.candidates('category'), [])
        evidence = os.environ.get('PCS_PICKER_EVIDENCE')
        if evidence:
            self.picker.import_menu.grab().save(str(Path(evidence) / 'local-create-menu.png'))
        return actions[0]

    def run_form(self, action, operation):
        failures = []
        completed = []
        def interact():
            dialog = getattr(self.canvas, 'active_node_dialog', None)
            try:
                self.assertIsNotNone(dialog)
                self.assertTrue(self.picker.busy)
                operation(dialog)
                completed.append(True)
            except BaseException as error:
                failures.append(error)
            finally:
                if dialog is not None and dialog.isVisible():
                    dialog.reject()
        QTimer.singleShot(30, interact)
        QTest.mouseClick(self.picker.import_menu, Qt.MouseButton.LeftButton,
                        pos=self.picker.import_menu.actionGeometry(action).center())
        self.events()
        if failures:
            raise failures[0]
        self.assertEqual(completed, [True])

    def test_local_module_without_manager_is_one_undo_and_survives_save_reopen(self):
        before = copy.deepcopy(self.tab.host.state)
        undo_count = len(self.canvas.undo_stack)
        action = self.local_action()
        def complete(dialog):
            dialog.name.setText('臨時構圖')
            dialog.prompt.setPlainText('wide shot, soft light')
            # The form belongs to the Window, not the nonmodal picker. Its click
            # must not dismiss/delete the picker while the operation is running.
            QTest.mouseClick(dialog.prompt.viewport(), Qt.MouseButton.LeftButton)
            self.assertFalse(self.picker.closed)
            evidence = os.environ.get('PCS_PICKER_EVIDENCE')
            if evidence:
                dialog.grab().save(str(Path(evidence) / 'local-create-form.png'))
            dialog.save()
        self.run_form(action, complete)
        self.assertTrue(self.picker.inserted)
        self.assertTrue(self.picker.closed)
        new_keys = set(self.tab.host.state['uses']) - set(before['uses'])
        self.assertEqual(len(new_keys), 1)
        key = new_keys.pop()
        created = copy.deepcopy(self.tab.host.state['uses'][key])
        self.assertEqual(created['name'], '臨時構圖')
        self.assertEqual(created['prompt'], 'wide shot, soft light')
        self.assertNotIn('source_id', created)
        self.assertEqual(multi_output.owner(self.tab.host.state, key), self.destination)
        self.assertEqual(self.tab.host.state['items'], before['items'])
        self.assertEqual(self.tab.host.state['draft'], before['draft'])
        self.assertEqual(len(self.canvas.undo_stack), undo_count + 1)
        self.canvas.undo()
        self.assertNotIn(key, self.tab.host.state['uses'])
        self.canvas.redo()
        self.assertEqual(self.tab.host.state['uses'][key], created)
        self.w.autosave.flush(self.tab)
        self.assertFalse(self.tab.dirty, self.tab.document.error)
        self.assertEqual(io.load(self.root, self.path.name).document['payload']['state']['uses'][key], created)
        self.assertTrue(self.w.close_tab(self.w.index_of(self.tab.key), make_home=False))
        self.tab = self.w.open_path(self.path, True, new_tab=True)
        self.events()
        self.assertEqual(self.tab.host.state['uses'][key], created)
        self.assertEqual(list(self.root.glob('*.pcs-category')), [])

    def test_cancel_keeps_picker_and_original_content_then_outside_click_dismisses(self):
        before = copy.deepcopy(self.tab.host.state)
        undo_count = len(self.canvas.undo_stack)
        self.run_form(self.local_action(), lambda dialog: dialog.reject())
        self.assertEqual(self.tab.host.state, before)
        self.assertEqual(len(self.canvas.undo_stack), undo_count)
        self.assertFalse(self.picker.closed)
        self.assertTrue(self.picker.isVisible())
        self.assertFalse(self.picker.busy)
        QTest.mouseClick(self.canvas.view.viewport(), Qt.MouseButton.LeftButton)
        self.events()
        self.assertTrue(self.picker.closed)

    def test_name_validation_keeps_form_open_without_early_mutation(self):
        before = copy.deepcopy(self.tab.host.state)
        def invalid_then_cancel(dialog):
            dialog.name.clear()
            dialog.prompt.setPlainText('unsaved')
            dialog.save()
            self.assertTrue(dialog.isVisible())
            self.assertTrue(dialog.error.isVisible())
            self.assertEqual(self.tab.host.state, before)
        self.run_form(self.local_action(), invalid_then_cancel)
        self.assertEqual(self.tab.host.state, before)
        self.assertFalse(self.picker.closed)

    def test_stale_menu_does_not_open_form_after_destination_is_removed(self):
        action = self.local_action()
        self.picker.import_menu.hide()
        del self.tab.host.state['multi_output']['canvases'][self.destination]
        before = copy.deepcopy(self.tab.host.state)
        with patch.object(self.canvas, 'add_new_with_destination',
                          side_effect=AssertionError('Stale destination must be rejected')) as add:
            action.trigger()
            add.assert_not_called()
        self.assertEqual(self.tab.host.state, before)
        self.assertIn('目的畫布', self.picker.status.text())


if __name__ == '__main__':
    unittest.main()
