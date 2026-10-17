"""Two live views across delayed drafts, file relocation and recycling."""
import copy
from pathlib import Path
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio import materials, drafts, clip_flow, generation, composition, multi_output
from prompt_calculus_studio.local_documents import io
from prompt_calculus_studio.prompt_editor import MaterialEditorForm
from prompt_calculus_studio.workspace_file_dialogs import SaveAsDialog
import test_workspace_panes as fixture
import test_workspace_file_operations as commands


class WorkspaceMultiviewFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])
    setUp = fixture.WorkspacePaneTests.setUp
    tearDown = fixture.WorkspacePaneTests.tearDown
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open
    context = commands.WorkspaceFileOperationsTests.context
    execute = commands.WorkspaceFileOperationsTests.execute
    dialog = commands.WorkspaceFileOperationsTests.dialog

    @staticmethod
    def add_copy(state, cid):
        from prompt_calculus_studio.core import uid
        key = uid()
        state['uses'][key] = composition.clone_node(next(iter(state['uses'].values())))
        state['output_order'].append(key)
        multi_output.assign(state, key, cid)
        return key

    def test_two_pending_canvas_editors_save_as_retargets_every_timer(self):
        left = self.open('Street.pcs-workflow')
        right = self.w.duplicate_view(left, True)
        canvas = left.host.canvas
        cid = next(iter(canvas.data()['canvases']))
        canvas.commit(lambda s: self.add_copy(s, cid))
        self.events()
        keys = list(left.host.state['uses'])
        original, before = left.path, left.path.read_bytes()
        editors = [MaterialEditorForm(t.host, compact=True) for t in (left, right)]
        try:
            for editor, key, text in zip(editors, keys, ('左側待套用', '右側待套用')):
                editor.edit_canvas(key)
                editor.prompt.setPlainText(text)
                editor.timer.stop()
                self.assertTrue(editor.dirty)
            target = self.execute('save_as', right, name='Pending views', directory=self.root)
            self.assertIsNotNone(target)
            self.assertEqual(left.path, right.path)
            saved = io.load(target.parent, target.name).document['payload']['state']
            for key, text in zip(keys, ('左側待套用', '右側待套用')):
                self.assertEqual(materials.canvas_node(saved, left.document.identity_maps['scene'][key])['prompt'], text)
            self.assertEqual(original.read_bytes(), before)
            self.w.autosave.delay_ms = 50
            editors[1].prompt.setPlainText('另存後繼續編輯')
            editors[1].flush_canvas()
            QTest.qWait(180)
            right.host.canvas.undo()
            self.events()
            self.assertTrue(self.w.close_tab(self.w.index_of(right.key), pane=self.w.pane_for(right)))
            self.w.autosave.flush(left)
            self.assertEqual(original.read_bytes(), before)
            reopened = self.w.open_path(original, new_tab=True)
            self.assertIsNot(reopened.document, left.document)
            self.assertNotEqual(reopened.document.key, left.document.key)
        finally:
            for editor in editors:
                editor.timer.stop()
                editor.close()
                editor.deleteLater()
            self.events()

    def test_save_as_cancel_and_failed_write_keep_shared_content_and_history(self):
        left = self.open('Notes.md')
        right = self.w.duplicate_view(left, True)
        left.widget.insertPlainText('共同草稿')
        before, source = left.path.read_bytes(), left.path
        original_model = left.widget.document()
        def cancel(dialog):
            self.assertEqual(len(self.w.commands.files.pending[0].pages), 2)
            self.w.autosave.flush(left)
            self.w.autosave.flush(right)
            self.assertEqual(source.read_bytes(), before)
            dialog.reject()
        self.dialog(lambda: self.execute('save_as', right), cancel, SaveAsDialog)
        with patch('prompt_calculus_studio.workspace_file_operations.create_markdown', side_effect=OSError('locked target')):
            self.assertIsNone(self.execute('save_as', left, name='Cannot write', directory=self.root))
        self.assertEqual(left.path, source)
        self.assertEqual(right.path, source)
        self.assertIs(right.widget.document(), original_model)
        self.assertTrue(original_model.isUndoAvailable())
        self.assertEqual(source.read_bytes(), before)
        self.w.autosave.flush(right)
        self.assertIn('共同草稿', source.read_text(encoding='utf-8-sig'))
        right.widget.undo()
        self.assertNotIn('共同草稿', left.widget.toPlainText())

    def test_folder_move_and_recycle_close_all_views_without_old_path_resurrection(self):
        folder = self.root/'Folder'
        (folder/'A.md').write_text('A', encoding='utf-8')
        (folder/'B.md').write_text('B', encoding='utf-8')
        left = self.open('Folder/A.md')
        right = self.w.duplicate_view(left, True)
        other = self.open('Folder/B.md', new_tab=True)
        left.widget.insertPlainText('shared ')
        other.widget.insertPlainText('other ')
        self.w.refresh_tree()
        target = self.execute('rename', path=folder, name='Renamed')
        destination = self.root/'Destination'
        destination.mkdir()
        moved = self.execute('move', path=target, directory=destination)
        self.assertEqual(left.path, moved/'A.md')
        self.assertEqual(left.path, right.path)
        self.assertEqual(other.path, moved/'B.md')
        self.w.autosave.flush(left)
        self.w.autosave.flush(other)
        self.assertFalse(folder.exists())
        self.assertTrue(left.widget.document().isUndoAvailable())
        path, before = left.path, left.path.read_bytes()
        recycled = self.root.parent/'recycled-A.md'
        self.w.confirm_trash = False
        self.w.autosave.timers[self.w.autosave.key(left)].start(25)
        def recycle(path):
            Path(path).rename(recycled)
            return True
        with patch('prompt_calculus_studio.workspace_commands.QFile.moveToTrash', side_effect=recycle):
            self.assertTrue(self.w.commands.trash(self.context(left)))
        QTest.qWait(140)
        self.events()
        self.assertFalse(path.exists())
        self.assertEqual(recycled.read_bytes(), before)
        self.assertNotIn(left.key, self.w.tabs_by_key)
        self.assertNotIn(right.key, self.w.tabs_by_key)
        self.assertIn(other.key, self.w.tabs_by_key)
        self.assertEqual(other.path.read_text(encoding='utf-8'), 'other B')

    def test_different_workflows_keep_empty_draft_order_and_bindings_independent(self):
        from test_generation import workflow
        a = self.open('Street.pcs-workflow')
        self.w.move_view(a)
        b = self.open('Night.pcs-workflow', pane=self.w.panes[0])
        for tab, node, text in ((a, '2', ''), (b, '3', 'B manual')):
            def change(state):
                cid = next(iter(state['multi_output']['canvases']))
                self.add_copy(state, cid)
                if tab is b:state['output_order'].reverse()
                profile = workflow('txt2img')
                generation.store_profile(state, profile)
                clip = next(iter(state['multi_output']['clip_inputs']))
                clip_flow.set_binding(state, profile['id'], clip, (node, 'text'))
                drafts.edit(state, text, next(iter(state['multi_output']['outputs'])))
            self.assertTrue(tab.host.canvas.commit(change))
        self.events()
        before_b = copy.deepcopy(b.host.state)
        order = copy.deepcopy(a.host.state['output_order'])
        a.host.canvas.undo()
        self.assertEqual(b.host.state, before_b)
        a.host.canvas.redo()
        self.assertEqual(a.host.state['output_order'], order)
        for tab, node, text in ((a, '2', ''), (b, '3', 'B manual')):
            self.w.focus_tab(tab)
            self.assertTrue(self.w.save_tab(tab))
            saved = io.load(tab.path.parent, tab.path.name).document['payload']['state']
            self.assertEqual(next(iter(saved['multi_output']['outputs'].values()))['draft'], text)
            self.assertEqual(saved['multi_output']['bindings'][0]['node'], node)
            self.assertEqual(len(saved['output_order']), len(tab.host.state['output_order']))


if __name__ == '__main__':unittest.main()
