"""Queued Qt drops stay with their document, workspace and live source panel."""
import copy
import json
import sys
import traceback
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import QCoreApplication, QEvent
from shiboken6 import isValid
from stage_fixture import StageFixture, APP
import test_output_source_tree as fixture
from prompt_calculus_studio import workspace_scene
from prompt_calculus_studio.multi_output import disconnect
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.flow_data import text_sources


class SourceLifecycleTests(StageFixture):
    prepare = fixture.SourceTreeTests.prepare
    group = fixture.SourceTreeTests.group
    drop = fixture.SourceTreeTests.drop

    def setUp(self):
        self.callback_errors = []; self.closed = False
        hook = patch.object(sys, 'excepthook', lambda *error:
                            self.callback_errors.append(''.join(traceback.format_exception(*error))))
        hook.start(); self.addCleanup(hook.stop)
        super().setUp()

    def tearDown(self):
        if not self.closed: super().tearDown()
        APP.processEvents()
        self.assertEqual(self.callback_errors, [])
        self.assertFalse(self.executor.submissions)

    def queue(self, tree, kind='member'):
        group = self.group(tree, self.cid)
        item, target = ((group.child(0), group.child(1)) if kind == 'member'
                        else (group, self.group(tree, self.other)))
        self.assertTrue(self.drop(tree, item, target, process=False))

    def protected(self):
        return copy.deepcopy({key: self.w.state.get(key) for key in
                              ('uses', 'multi_output', 'draft', 'draft_base', 'canvas_functions')})

    def assert_unchanged_after_callbacks(self):
        before = self.protected(); history = copy.deepcopy((self.c.undo_stack, self.c.redo_stack))
        APP.processEvents()
        self.assertEqual(self.protected(), before)
        self.assertEqual((self.c.undo_stack, self.c.redo_stack), history)

    def test_workspace_switch_rejects_member_and_source_drops_with_matching_ids(self):
        tree = self.prepare(); original = self.w.state['workspace']
        other = workspace_scene.create(self.w.state, 'Copied workspace', duplicate=True)
        for kind in ('member', 'source'):
            with self.subTest(kind=kind):
                self.queue(tree, kind); self.w.change_workspace(other)
                self.assert_unchanged_after_callbacks()
                self.w.change_workspace(original); APP.processEvents()
                self.assertEqual(self.c.data()['canvases'][self.cid]['members'], ['root', 'pose'])
                self.assertEqual(text_sources(self.c.data()['outputs'][self.out]), [self.cid, self.other])

    def test_workspace_round_trip_does_not_revive_queued_drop(self):
        tree = self.prepare(); original = self.w.state['workspace']
        other = workspace_scene.create(self.w.state, 'Copied workspace', duplicate=True)
        self.queue(tree); self.w.change_workspace(other); self.w.change_workspace(original)
        self.assert_unchanged_after_callbacks()

    def test_same_id_json_import_rejects_old_member_and_source_drops(self):
        tree = self.prepare()
        for kind in ('member', 'source'):
            with self.subTest(kind=kind):
                incoming = copy.deepcopy(self.w.state)
                incoming['uses']['root']['prompt'] = 'imported text ' + kind
                path = Path(self.tmp.name) / (kind + '.json')
                path.write_text(json.dumps(incoming, ensure_ascii=False), encoding='utf-8')
                self.queue(tree, kind)
                with patch('prompt_calculus_studio.window.QFileDialog.getOpenFileName', return_value=(str(path), 'JSON')), \
                        patch('prompt_calculus_studio.window.ask', return_value=True), \
                        patch('prompt_calculus_studio.prompt_editor.ask', return_value=True):
                    self.w.import_json()
                self.assertEqual(self.w.state['uses']['root']['prompt'], 'imported text ' + kind)
                self.assertEqual(self.c.undo_stack, []); self.assertEqual(self.c.redo_stack, [])
                self.assert_unchanged_after_callbacks()
                self.w.persist(); saved = self.w.store.load()
                self.assertEqual(saved['multi_output']['canvases'][self.cid]['members'], ['root', 'pose'])
                self.assertEqual(text_sources(saved['multi_output']['outputs'][self.out]), [self.cid, self.other])
                tree = self.c.outputs[self.out].panel.order

    def test_replaced_store_rejects_queued_drop(self):
        tree = self.prepare(); self.queue(tree)
        original = self.w.store; replacement = Storage(Path(self.tmp.name) / 'other-data')
        replacement.save(self.w.state); self.w.store = replacement
        try: self.assert_unchanged_after_callbacks()
        finally: self.w.store = original; replacement.close()

    def test_removed_output_rejects_drop_before_qt_deletion(self):
        tree = self.prepare(); self.queue(tree); self.c.remove_output(self.out)
        self.assert_unchanged_after_callbacks()
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'], ['root', 'pose'])

    def test_removed_canvas_rejects_queued_drop(self):
        tree = self.prepare(); self.queue(tree); self.c.remove_container(self.cid)
        self.assert_unchanged_after_callbacks()
        self.assertNotIn(self.cid, self.c.data()['canvases'])

    def test_disconnected_source_rejects_pending_member_drop(self):
        tree = self.prepare(); self.queue(tree)
        connection = next(value['id'] for value in self.c.data()['connections']
                          if value['source'] == self.cid and value['destination'] == self.out)
        self.c.commit(lambda state: disconnect(state, connection))
        self.assert_unchanged_after_callbacks()
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'], ['root', 'pose'])
        self.assertIn('排序來源已移除，請重新選擇。', self.notices)

    def test_deleted_source_panel_cancels_callback(self):
        from prompt_calculus_studio.output_sources import OutputSources
        tree = self.prepare(); self.queue(tree)
        # Replace the parent's live control before deleting the old context;
        # display/font recovery must not be left with a dangling panel.order.
        panel = self.c.outputs[self.out].panel
        panel.order = OutputSources(self.c, self.out)
        self.assertIsNotNone(tree.parentWidget().layout().replaceWidget(tree, panel.order))
        tree.hide(); tree.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(isValid(tree))
        self.w.display_recovery.refresh()
        self.assert_unchanged_after_callbacks()

    def test_window_close_cancels_callback_before_closed_store_use(self):
        tree = self.prepare(); self.queue(tree)
        self.closed = self.w.close(); self.assertTrue(self.closed)
        self.assert_unchanged_after_callbacks()

    def test_unrelated_transaction_keeps_valid_drop_and_undo_redo(self):
        tree = self.prepare(); self.queue(tree)
        self.c.commit(lambda state: state['uses']['scene'].update(prompt='evening garden'))
        APP.processEvents()
        self.assertEqual(self.c.data()['canvases'][self.cid]['members'], ['pose', 'root'])
        self.assertEqual(self.w.state['uses']['scene']['prompt'], 'evening garden')
        self.c.undo(); self.assertEqual(self.c.data()['canvases'][self.cid]['members'], ['root', 'pose'])
        self.assertEqual(self.w.state['uses']['scene']['prompt'], 'evening garden')
        self.c.redo(); self.w.persist()
        self.assertEqual(self.w.store.load()['multi_output']['canvases'][self.cid]['members'], ['pose', 'root'])
