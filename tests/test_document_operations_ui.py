"""Real Manager/Canvas adapters retain shared Undo, file writes and flow hooks."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from prompt_calculus_studio import materials, material_navigation, multi_output, drafts
from prompt_calculus_studio.local_documents import io
import test_workspace_panes as fixture
import test_workspace_shell as shell_fixture


class DocumentOperationsUITests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open
    editor = shell_fixture.WorkspaceShellTests.editor

    def test_manager_save_uses_document_interface_shared_undo_and_autosave_reopen(self):
        left = self.open('Scene.pcs-category')
        host = left.host
        original = left.path.read_bytes()
        ident, dialog = self.editor(left)
        before = copy.deepcopy(materials.item(host.state, ident))
        text = 'Updated by the Manager\nwith a second line'
        dialog.prompt.setPlainText(text)
        history_count = len(host.canvas.undo_stack)
        self.w.autosave.delay_ms = 50
        with patch.object(host.canvas, 'commit', side_effect=AssertionError('Manager must not call Canvas.commit')):
            QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertIsNone(host.prompt_editor.active_dialog)
        self.assertEqual(materials.item(host.state, ident)['prompt'], text)
        self.assertIs(host.document_operations.history, left.document.history)
        self.assertEqual(len(host.canvas.undo_stack), history_count+1)
        QTest.qWait(180)
        self.events()
        self.assertFalse(left.dirty)
        self.assertNotEqual(left.path.read_bytes(), original)
        self.assertEqual(io.load(self.root, left.path.name).document['payload']['items'][0]['prompt'], text)
        right = self.w.duplicate_view(left, True)
        self.events()
        self.assertIs(right.host.document_operations.history, host.document_operations.history)
        right.host.canvas.undo()
        self.events()
        self.assertEqual(materials.item(host.state, ident), before)
        host.canvas.redo()
        self.events()
        self.assertEqual(materials.item(right.host.state, ident)['prompt'], text)
        self.assertTrue(self.w.save_tab(right))
        path = left.path
        for tab in (right, left):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), pane=self.w.pane_for(tab), make_home=False))
        reopened = self.w.open_path(path, new_tab=True)
        self.events()
        self.assertEqual(materials.item(reopened.host.state, ident)['prompt'], text)
        # Reopen establishes a fresh editor; its new transaction still has one
        # Undo. Persisted restart Undo is not a contract of this document type.
        reopened.host.prompt_editor.pin_material(ident)
        self.assertTrue(materials.item(reopened.host.state, ident)['pinned'])
        reopened.host.canvas.undo()
        self.assertFalse(materials.item(reopened.host.state, ident).get('pinned', False))
        reopened.host.canvas.redo()
        self.assertTrue(materials.item(reopened.host.state, ident)['pinned'])

    def test_manager_navigation_does_not_project_stage_or_change_manual_draft(self):
        tab = self.open('Street.pcs-workflow')
        host = tab.host
        host.leave_canvas()
        self.events()
        output = host.state['multi_output']['current_output']
        self.assertTrue(host.document_operations.commit(lambda state: drafts.edit(state, '', output)))
        before = copy.deepcopy(host.state)
        count = len(host.canvas.undo_stack)
        folder = []
        with patch.object(host.canvas, 'commit', side_effect=AssertionError('Canvas transaction coupling')), \
             patch('prompt_calculus_studio.stage_context.sync_result_view', side_effect=AssertionError('navigation projected Stage')), \
             patch.object(host.comfy.input_flow.chain, 'route_changed', side_effect=AssertionError('navigation changed route')):
            self.assertTrue(host.prompt_editor._commit_navigation(
                lambda state: folder.append(material_navigation.add_folder(state, 'Collection')), '新增資料夾'))
        self.assertEqual(len(host.canvas.undo_stack), count+1)
        for key in ('multi_output', 'uses', 'output_order', 'draft', 'draft_base'):
            self.assertEqual(host.state.get(key), before.get(key), key)
        host.canvas.undo()
        self.assertEqual(host.state.get(material_navigation.FIELD), before.get(material_navigation.FIELD))
        host.canvas.redo()
        self.assertEqual(material_navigation.view(host.state)['folders'][0]['id'], folder[0])
        self.assertEqual(host.state['multi_output']['outputs'][output]['draft'], '')

    def test_rejected_manager_save_keeps_original_and_draft_without_history_or_route(self):
        tab = self.open('Scene.pcs-category')
        host = tab.host
        ident, dialog = self.editor(tab)
        before = copy.deepcopy(host.state)
        history = copy.deepcopy(tab.document.history)
        original = tab.path.read_bytes()
        dialog.prompt.setPlainText('Unpublished draft')
        with patch('prompt_calculus_studio.content_history.record', side_effect=ValueError('journal rejected')), \
             patch.object(host.comfy.input_flow.chain, 'route_changed', side_effect=AssertionError('rejected edit changed route')):
            QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertIs(host.prompt_editor.active_dialog, dialog)
        self.assertEqual(dialog.prompt.toPlainText(), 'Unpublished draft')
        self.assertEqual(host.state, before)
        self.assertEqual(tab.document.history, history)
        self.assertEqual(tab.path.read_bytes(), original)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(materials.item(host.state, ident), materials.item(before, ident))

    def test_canvas_stage_projection_route_detach_and_final_history_keep_order(self):
        tab = self.open('Street.pcs-workflow')
        host = tab.host
        canvas = host.canvas
        from prompt_calculus_studio.flow_data import add_scheduler
        added = []
        self.assertTrue(canvas.commit(lambda state: added.append(add_scheduler(state))))
        scheduler = added[0]
        output = host.state['multi_output']['current_output']
        self.assertTrue(canvas.commit(lambda state: multi_output.connect(state, output, scheduler+'::clip1', 'clip')))
        connection = next(c['id'] for c in host.state['multi_output']['connections'] if c['destination'] == scheduler+'::clip1')
        events = []
        from prompt_calculus_studio.stage_context import sync_result_view
        real_sync = canvas.sync
        def project(state, directory, results):
            events.append('project')
            return sync_result_view(state, directory, results)
        def sync(scope):
            events.append('sync')
            real_sync(scope)
        def route():
            events.append('route')
            # The established route callback may refresh derived edit state.
            host.state['text_positions']['runtime-marker'] = [10, 20]
        def detach(owner):
            events.append(('detach', owner))
        with patch('prompt_calculus_studio.stage_context.sync_result_view', side_effect=project), \
             patch.object(canvas, 'sync', side_effect=sync), \
             patch.object(host.comfy.input_flow.chain, 'route_changed', side_effect=route), \
             patch.object(host.comfy.input_flow, 'local_controls', return_value=[('owner', {'route': {'scheduler': scheduler}})]), \
             patch.object(host.comfy.input_flow, 'detach', side_effect=detach):
            self.assertTrue(canvas.commit(lambda state: multi_output.disconnect(state, connection)))
        self.assertEqual(events, ['project', 'sync', 'route', ('detach', 'owner')])
        self.assertEqual(canvas.undo_stack[-1][1], canvas.history_state())
        self.assertEqual(canvas.last_state, canvas.history_state())
        canvas.undo()
        self.assertIn(connection, [c['id'] for c in host.state['multi_output']['connections']])
        canvas.redo()
        self.assertNotIn(connection, [c['id'] for c in host.state['multi_output']['connections']])


if __name__ == '__main__':
    unittest.main()
