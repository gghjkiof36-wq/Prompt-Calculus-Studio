"""P3 file commands through real Qt pages, with isolated files and fault seams."""
import copy
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QInputMethodEvent, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog
from shiboken6 import isValid

from prompt_calculus_studio import materials, drafts
from prompt_calculus_studio.local_documents import io, assets, schema, category, workflow
from prompt_calculus_studio.workspace_home import WorkspaceHome
from prompt_calculus_studio.workspace_file_dialogs import SaveAsDialog, RenameDialog, MoveDialog
from prompt_calculus_studio.workspace_file_io import rename_exclusive
from prompt_calculus_studio.workspace_locations import path_key
import test_workspace_shell as fixture


class WorkspaceFileOperationsTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events

    def open(self, name='Notes.md', new=False):
        tab = self.w.open_path(self.root/name, True, new_tab=new)
        self.events()
        self.assertIsNotNone(tab, self.errors)
        return tab

    def context(self, tab=None, path=None, origin='tab'):
        if path:
            return self.w.commands.context('tree', self.w.path_items[path_key(path)])
        return self.w.commands.context(origin, tab=tab)

    def execute(self, action, tab=None, path=None, **args):
        return self.w.commands.files.run(action, self.context(tab, path), **args)

    def dialog(self, trigger, check, cls):
        failures = []
        def interact():
            dialog = self.app.activeModalWidget()
            try:
                self.assertIsInstance(dialog, cls)
                check(dialog)
            except BaseException as error:
                failures.append(error)
                if dialog:
                    dialog.reject()
        QTimer.singleShot(25, interact)
        result = trigger()
        QTest.qWait(30)
        self.events()
        if failures:
            raise failures[0]
        return result

    def test_navigation_reuses_current_slot_and_history_restores_same_editor(self):
        for position in ('side', 'top'):
            with self.subTest(position=position):
                self.w.set_navigation_position(position)
                self.w.sidebar_preference = True
                self.w.sync_sidebar()
                home = self.w.new_tab()
                before, at = self.w.tab_strip.bar.count(), self.w.index_of(home.key)
                self.w.select_path(self.root/'Notes.md')
                item = self.w.tree.currentItem()
                QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.LeftButton,
                                 pos=self.w.tree.visualItemRect(item).center())
                self.events()
                a = self.w.tabs_by_key[self.w.active_key]
                self.assertEqual(self.w.tab_strip.bar.count(), before)
                self.assertEqual(self.w.index_of(a.key), at)
                QTest.keyClicks(a.widget, 'kept')
                text = a.widget.toPlainText()
                b = self.open('Street.pcs-workflow')
                self.assertEqual(self.w.index_of(b.key), at)
                self.assertFalse(a.dirty)
                QTest.mouseClick(self.w.back, Qt.MouseButton.LeftButton)
                self.events()
                self.assertIs(self.w.pages.currentWidget(), a.widget)
                self.assertEqual(a.widget.toPlainText(), text)
                self.assertTrue(a.widget.document().isUndoAvailable())
                a.widget.undo()
                self.assertNotEqual(a.widget.toPlainText(), text)
                a.widget.redo()
                QTest.mouseClick(self.w.forward, Qt.MouseButton.LeftButton)
                self.events()
                self.assertIs(self.w.pages.currentWidget(), b.widget)
                self.assertEqual(self.w.tab_strip.bar.count(), before)
                self.w.close_tab(at, decision='discard')

    def test_home_recent_open_dialog_new_document_and_explicit_new_tab(self):
        home = self.w.pages.currentWidget()
        home.set_recent([str(self.root/'Notes.md')])
        QTest.mouseClick(home.listing.viewport(), Qt.MouseButton.LeftButton,
                         pos=home.listing.visualItemRect(home.listing.item(0)).center())
        self.events()
        self.assertEqual(self.w.tab_strip.bar.count(), 1)
        self.w.new_tab()
        home = self.w.pages.currentWidget()
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(self.root/'Empty.md'), '')):
            QTest.mouseClick(home.open_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.w.tab_strip.bar.count(), 2)
        active = self.w.tabs_by_key[self.w.active_key]
        for kind in ('workflow', 'category', 'markdown'):
            tab = self.w.new_document(kind, 'Created '+kind)
            self.assertEqual(self.w.tab_strip.bar.count(), 2)
            self.assertEqual(self.w.index_of(tab.key), 1)
        context = self.context(path=self.root/'Action.pcs-category')
        menu = self.w.commands.menu_for(context)
        next(a for a in menu.actions() if a.text() == '在新分頁開啟').trigger()
        self.assertEqual(self.w.tab_strip.bar.count(), 3)
        self.assertEqual(self.w.tabs_by_key[self.w.active_key].path, self.root/'Action.pcs-category')

    def test_failed_navigation_and_cancel_leave_current_page_and_order(self):
        tab = self.open()
        QTest.keyClicks(tab.widget, 'local')
        history = list(self.w.history)
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            self.assertIsNone(self.w.open_path(self.root/'Street.pcs-workflow'))
        self.assertEqual(self.w.active_key, tab.key)
        self.assertEqual(self.w.history, history)
        self.assertEqual(self.w.tab_strip.bar.count(), 1)
        self.assertTrue(tab.dirty)
        with patch.object(QFileDialog, 'getOpenFileName', return_value=('', '')):
            self.w.choose_file()
        self.assertEqual(self.w.active_key, tab.key)

    def test_save_as_waits_without_saving_source_other_page_continues_and_cancel_resumes(self):
        a = self.open()
        b = self.open('Empty.md', True)
        original = a.path.read_bytes()
        self.w.autosave.delay_ms = 70
        QTest.keyClicks(a.widget, 'A')
        QTest.keyClicks(b.widget, 'B')
        def inspect(dialog):
            QTest.qWait(170)
            self.assertEqual(a.path.read_bytes(), original)
            self.assertEqual(b.path.read_text(), 'B')
            self.assertFalse(self.w.save_tab(a))
            self.assertFalse(self.w.close_tab(self.w.index_of(a.key)))
            self.assertFalse(self.w.close())
            self.assertIsNone(self.execute('rename', a, name='blocked'))
            dialog.reject()
        self.dialog(lambda: self.execute('save_as', a), inspect, SaveAsDialog)
        QTest.qWait(170)
        self.assertTrue(a.path.read_bytes().startswith(b'\xef\xbb\xbfA'))
        self.assertFalse(self.w.commands.files.pending)

    def test_save_as_markdown_keeps_native_undo_encoding_and_only_new_path_writes(self):
        a = self.open()
        source, before, model = a.path, a.path.read_bytes(), a.widget.document()
        QTest.keyClicks(a.widget, 'local ')
        count = self.w.tab_strip.bar.count()
        self.assertEqual(self.execute('save_as', a, name='備份 多.點'), self.root/'備份 多.點.md')
        self.assertEqual(self.w.tab_strip.bar.count(), count)
        self.assertIs(a.widget.document(), model)
        self.assertFalse(a.dirty)
        self.assertTrue(a.path.read_bytes().startswith(b'\xef\xbb\xbflocal '))
        self.assertIn(b'\r\n', a.path.read_bytes())
        a.widget.undo()
        self.assertTrue(a.dirty)
        self.assertTrue(self.w.save_tab(a))
        a.widget.redo()
        self.assertTrue(self.w.close_tab(self.w.index_of(a.key)))
        self.assertEqual(source.read_bytes(), before)
        self.assertIn(b'local ', a.path.read_bytes())
        reopened = self.open(a.path.name)
        self.assertIn('local ', reopened.widget.toPlainText())
        self.assertIsNot(self.open(source.name, True).widget.document(), model)

    def test_save_as_category_and_workflow_keep_live_history_stable_owned_ids_and_assets(self):
        from local_document_fixtures import p0
        value, ids = p0.fixture()
        runtime = self.root/'asset-source'
        (runtime/'assets').mkdir(parents=True)
        (runtime/'assets/p0-swatch.png').write_bytes(p0.png())
        for name, doc in (('Complex.pcs-category', category.from_state(value, ids['category_a'])),
                          ('Complex.pcs-workflow', workflow.from_state(value, ids['workspace_a']))):
            doc = assets.prepare(doc, runtime, self.root)
            self.assertEqual(io.create(self.root, name, doc).status, 'saved')
            tab = self.open(name, True)
            original, before, host = tab.path, tab.path.read_bytes(), tab.document.host
            old_key = tab.key
            ident = host.state['items'][0]['id']
            host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='changed')))
            if tab.document.kind == 'workflow':
                output = next(iter(host.state['multi_output']['outputs']))
                host.canvas.commit(lambda s: drafts.edit(s, '', output))
            undo = copy.deepcopy(host.canvas.undo_stack)
            external = self.root.parent/('external-'+tab.document.kind)
            external.mkdir()
            self.assertIsNotNone(self.execute('save_as', tab, name='copy', directory=external))
            self.assertNotEqual(tab.key, old_key)
            self.assertIs(tab.document.host, host)
            self.assertEqual(host.canvas.undo_stack, undo)
            saved = io.load(external, tab.path.name)
            self.assertEqual(saved.issues, ())
            owned = copy.deepcopy(saved.document['payload'])
            self.assertNotEqual(saved.document['document_id'], io.load(original.parent, original.name).document['document_id'])
            self.assertTrue(self.w.save_tab(tab))
            self.assertEqual(io.load(external, tab.path.name).document['payload'], owned)
            host.canvas.undo()
            self.assertTrue(self.w.save_tab(tab))
            host.canvas.redo()
            self.assertTrue(self.w.save_tab(tab))
            self.assertEqual(io.load(external, tab.path.name).document['payload'], owned)
            self.assertEqual(original.read_bytes(), before)
            self.assertFalse(self.w.locations.root_for(tab.path))
            self.w.close_tab(self.w.index_of(tab.key))
            reopened = self.w.open_path(external/saved.receipt.path.split(os.sep)[-1], True, new_tab=True)
            self.assertIsNotNone(reopened)
            self.assertEqual(reopened.document.issues, ())

    def test_conflict_cancel_stays_stopped_and_save_as_rescues_without_touching_external(self):
        a = self.open()
        QTest.keyClicks(a.widget, 'local')
        a.path.write_bytes(b'external')
        self.assertFalse(self.w.save_tab(a))
        self.dialog(lambda: self.execute('save_as', a), lambda d: d.reject(), SaveAsDialog)
        self.assertTrue(a.widget.error)
        self.w.autosave.delay_ms = 20
        QTest.qWait(70)
        self.assertEqual(a.path.read_bytes(), b'external')
        source = a.path
        self.assertIsNotNone(self.execute('save_as', a, name='Rescued'))
        self.assertEqual(a.widget.error, '')
        QTest.keyClicks(a.widget, 'new')
        QTest.qWait(80)
        self.assertEqual(source.read_bytes(), b'external')
        self.assertIn(b'local', a.path.read_bytes())

    def test_unopened_and_background_save_as_use_clicked_object(self):
        active = self.open()
        background = self.open('Empty.md', True)
        self.w.tab_strip.bar.setCurrentIndex(self.w.index_of(active.key))
        original_key, count = active.key, self.w.tab_strip.bar.count()
        self.assertIsNotNone(self.execute('save_as', background, name='background'))
        self.assertEqual(self.w.active_key, original_key)
        self.assertEqual(self.w.tab_strip.bar.count(), count)
        QTest.keyClicks(active.widget, 'do not save me')
        before = active.path.read_bytes()
        self.assertIsNotNone(self.execute('save_as', path=self.root/'Action.pcs-category', name='unopened'))
        self.assertEqual(self.w.tab_strip.bar.count(), count+1)
        self.assertEqual(active.path.read_bytes(), before)
        self.assertTrue(active.dirty)

    def test_collisions_alias_and_failed_write_keep_source_model(self):
        tab = self.open()
        QTest.keyClicks(tab.widget, 'draft')
        source, raw = tab.path, tab.path.read_bytes()
        for name in ('Notes', 'Empty', 'bad/name', 'CON', 'tail.', 'tail '):
            self.assertIsNone(self.execute('save_as', tab, name=name))
            self.assertEqual(tab.path, source)
        alias = self.root/'Alias.md'
        os.link(source, alias)
        self.assertIsNone(self.execute('save_as', tab, name='Alias'))
        with patch('prompt_calculus_studio.workspace_file_operations.create_markdown', side_effect=OSError('disk full')):
            self.assertIsNone(self.execute('save_as', tab, name='Failed'))
        self.assertFalse((self.root/'Failed.md').exists())
        self.assertTrue(tab.dirty)
        self.assertEqual(source.read_bytes(), raw)

    def test_f2_inline_ime_invalid_escape_focus_loss_and_case_only_rename(self):
        tab = self.open()
        QTest.keyClicks(tab.widget, 'local')
        self.w.select_path(tab.path)
        self.w.tree.setFocus()
        self.events()
        QTest.keyClick(self.w.tree, Qt.Key.Key_F2)
        editor = self.w.commands.files.inline
        self.assertIsNotNone(editor)
        editor.name.setText('中文 名稱')
        self.app.sendEvent(editor.name, QInputMethodEvent('組字', []))
        QTest.keyClick(editor.name, Qt.Key.Key_Return)
        self.assertEqual(tab.path.name, 'Notes.md')
        self.app.sendEvent(editor.name, QInputMethodEvent('', []))
        editor.name.setText('CON')
        QTest.keyClick(editor.name, Qt.Key.Key_Return)
        self.assertTrue(editor.error.isVisible())
        QTest.keyClick(editor.name, Qt.Key.Key_Escape)
        self.events()
        self.assertFalse(self.w.commands.files.pending)
        self.w.tree.setFocus()
        QTest.keyClick(self.w.tree, Qt.Key.Key_F2)
        editor = self.w.commands.files.inline
        editor.name.setText('cancelled')
        tab.widget.setFocus()
        self.events()
        self.assertIsNone(self.w.commands.files.inline)
        self.assertEqual(tab.path.name, 'Notes.md')
        QTest.keyClick(tab.widget, Qt.Key.Key_F2)
        self.assertIsNone(self.w.commands.files.inline)
        self.assertIsNotNone(self.execute('rename', tab, name='notes'))
        self.assertEqual(tab.path.name, 'notes.md')
        self.assertIn('notes.md', [p.name for p in self.root.iterdir()])
        self.assertTrue(tab.widget.document().isUndoAvailable())

    def test_dirty_rename_move_parent_updates_all_writers_and_history(self):
        folder = self.root/'Folder'
        (folder/'One.md').write_text('one')
        (folder/'Two.md').write_text('two')
        a = self.open('Folder/One.md')
        b = self.open('Folder/Two.md', True)
        QTest.keyClicks(a.widget, 'A')
        QTest.keyClicks(b.widget, 'B')
        self.w.refresh_tree()
        active = self.w.active_key
        self.assertEqual(self.execute('rename', path=folder, name='Renamed'), self.root/'Renamed')
        self.assertEqual(self.w.active_key, active)
        self.assertFalse(folder.exists())
        self.assertTrue(a.dirty and b.dirty)
        destination = self.root/'Destination'
        destination.mkdir()
        self.assertEqual(self.execute('move', path=self.root/'Renamed', directory=destination), destination/'Renamed')
        self.assertEqual(a.path, destination/'Renamed/One.md')
        self.assertEqual(b.path, destination/'Renamed/Two.md')
        self.assertTrue(self.w.save_tab(a))
        self.assertTrue(self.w.save_tab(b))
        self.assertEqual(a.path.read_text(), 'Aone')
        self.assertEqual(b.path.read_text(), 'Btwo')
        self.assertFalse(folder.exists())
        a.widget.undo()
        self.assertTrue(self.w.save_tab(a))
        self.assertEqual(a.path.read_text(), 'one')

    def test_move_rejects_cross_root_self_child_collision_and_broken_markdown_reference(self):
        tab = self.open()
        outside = self.root.parent/'Outside'
        outside.mkdir()
        self.assertIsNone(self.execute('move', tab, directory=outside))
        self.assertEqual(self.execute('move', tab, directory=self.root), tab.path)
        (self.root/'Folder/Notes.md').write_text('other')
        self.assertIsNone(self.execute('move', tab, directory=self.root/'Folder'))
        self.w.refresh_tree()
        self.assertIsNone(self.execute('move', path=self.root/'Folder', directory=self.root/'Folder'))
        (self.root/'picture.png').write_bytes(b'image')
        tab.widget.setPlainText('![local](picture.png)')
        tab.widget.document().setModified(True)
        destination = self.root/'Destination'
        destination.mkdir()
        self.assertIsNone(self.execute('move', tab, directory=destination))
        self.assertIsNone(self.execute('save_as', tab, name='Copy', directory=destination))
        self.assertEqual(tab.path, self.root/'Notes.md')
        self.assertTrue(tab.dirty)
        self.assertFalse((destination/'Copy.md').exists())

    def test_move_dialog_new_folder_cancel_and_submit(self):
        tab = self.open()
        def inspect(dialog):
            self.assertFalse(dialog.move_button.isEnabled())
            created = dialog.create_destination('Created', self.root)
            dialog.populate(created)
            self.assertTrue(dialog.move_button.isEnabled())
            dialog.reject()
        self.dialog(lambda: self.execute('move', tab), inspect, MoveDialog)
        self.assertTrue((self.root/'Created').is_dir())
        self.assertEqual(tab.path, self.root/'Notes.md')
        def submit(dialog):
            dialog.populate(self.root/'Created')
            QTest.mouseClick(dialog.move_button, Qt.MouseButton.LeftButton)
        self.dialog(lambda: self.execute('move', tab), submit, MoveDialog)
        self.assertEqual(tab.path, self.root/'Created/Notes.md')

    def test_move_failure_rolls_back_and_partial_failure_stops_writes(self):
        tab = self.open()
        QTest.keyClicks(tab.widget, 'draft')
        source, before = tab.path, tab.path.read_bytes()
        ops = self.w.commands.files
        with patch.object(ops, 'apply_location', side_effect=ValueError('view failed')):
            self.assertIsNone(self.execute('move', tab, directory=self.root/'Folder'))
        self.assertEqual(tab.path, source)
        self.assertEqual(source.read_bytes(), before)
        self.assertFalse((self.root/'Folder/Notes.md').exists())
        calls = []
        def first_only(a, b):
            calls.append((a,b))
            if len(calls) > 1:
                raise OSError('locked rollback')
            rename_exclusive(a,b)
        with patch.object(ops, 'apply_location', side_effect=ValueError('view failed')), patch(
                'prompt_calculus_studio.workspace_file_operations.rename_exclusive', side_effect=first_only):
            self.assertIsNone(self.execute('move', tab, directory=self.root/'Folder'))
        self.assertTrue(tab.widget.operation_error)
        self.assertFalse(self.w.save_tab(tab))
        self.assertFalse(source.exists())
        self.assertEqual((self.root/'Folder/Notes.md').read_bytes(), before)

    def test_source_external_change_during_dialog_and_destination_race_are_not_overwritten(self):
        tab = self.open()
        def conflict(dialog):
            tab.path.write_bytes(b'external')
            dialog.name.setText('Changed')
            dialog.submit()
            self.assertTrue(dialog.error.isVisible())
            dialog.reject()
        self.dialog(lambda: self.execute('rename', tab), conflict, RenameDialog)
        self.assertEqual(tab.path.name, 'Notes.md')
        self.assertEqual(tab.path.read_bytes(), b'external')
        def collision(dialog):
            dialog.name.setText('Competitor')
            (self.root/'Competitor.md').write_bytes(b'competing')
            dialog.submit()
            self.assertTrue(dialog.error.isVisible())
            dialog.reject()
        self.dialog(lambda: self.execute('save_as', tab), collision, SaveAsDialog)
        self.assertEqual((self.root/'Competitor.md').read_bytes(), b'competing')

    def test_complete_chain_new_save_as_rename_move_save_reopen_keeps_original(self):
        for kind in ('markdown', 'category', 'workflow'):
            tab = self.w.new_document(kind, 'Original '+kind)
            original, before = tab.path, tab.path.read_bytes()
            if kind == 'markdown':
                QTest.keyClicks(tab.widget, 'new text')
            else:
                tab.document.host.canvas.commit(lambda s: s['workspaces'][0].update(notes='confirmed edit'))
            self.assertIsNotNone(self.execute('save_as', tab, name='Copy '+kind))
            self.assertIsNotNone(self.execute('rename', tab, name='Renamed '+kind))
            self.assertIsNotNone(self.execute('move', tab, directory=self.root/'Folder'))
            target = tab.path
            self.assertTrue(self.w.save_tab(tab))
            self.w.close_tab(self.w.index_of(tab.key))
            reopened = self.w.open_path(target, True)
            self.assertIsNotNone(reopened)
            self.assertEqual(original.read_bytes(), before)
            if kind == 'markdown':
                self.assertEqual(reopened.widget.toPlainText(), 'new text')
            else:
                self.assertEqual(reopened.document.issues, ())

    def test_save_as_flushes_pending_canvas_text_without_saving_original(self):
        from prompt_calculus_studio.prompt_editor import MaterialEditorForm
        tab = self.open('Street.pcs-workflow')
        host = tab.document.host
        original, before = tab.path, tab.path.read_bytes()
        use = next(iter(host.state['uses']))
        editor = MaterialEditorForm(host, compact=True)
        try:
            editor.edit_canvas(use)
            editor.prompt.setPlainText('pending canvas text')
            editor.timer.stop()
            self.assertTrue(editor.dirty)
            self.assertNotEqual(materials.canvas_node(host.state, use)['prompt'], 'pending canvas text')
            self.assertIsNotNone(self.execute('save_as', tab, name='Pending canvas copy'))
            copied = io.load(tab.path.parent, tab.path.name).document['payload']['state']
            copied_use = tab.document.identity_maps['scene'][use]
            self.assertEqual(materials.canvas_node(copied, copied_use)['prompt'], 'pending canvas text')
            self.assertEqual(original.read_bytes(), before)
            host.canvas.undo()
            self.assertTrue(self.w.save_tab(tab))
            self.assertEqual(original.read_bytes(), before)
        finally:
            editor.timer.stop()
            editor.close()
            editor.deleteLater()
            self.events()

    def test_unopened_save_as_rechecks_source_replaced_while_form_is_open(self):
        active = self.open('Notes.md')
        for filename in ('Empty.md', 'Scene.pcs-category'):
            source = self.root/filename
            count = self.w.tab_strip.bar.count()
            def change(dialog):
                source.write_bytes(b'changed externally')
                dialog.name.setText('Must not copy')
                dialog.submit()
                self.assertTrue(dialog.error.isVisible())
                dialog.reject()
            self.dialog(lambda: self.execute('save_as', path=source), change, SaveAsDialog)
            self.assertFalse((source.parent/('Must not copy'+source.suffix)).exists())
            self.assertEqual(self.w.tab_strip.bar.count(), count)
            self.assertEqual(self.w.active_key, active.key)

    def test_inflight_save_finishes_before_save_as_without_flushing_next_pending_edit(self):
        tab = self.open()
        source = tab.path
        QTest.keyClicks(tab.widget, 'first ')
        save = tab.widget.save
        def during_write():
            self.assertIsNone(self.execute('save_as', tab, name='After write'))
            return save()
        with patch.object(tab.widget, 'save', side_effect=during_write):
            self.assertTrue(self.w.save_tab(tab))
        first = source.read_bytes()
        QTest.keyClicks(tab.widget, 'pending ')
        self.events()
        self.assertEqual(tab.path.name, 'After write.md')
        self.assertEqual(source.read_bytes(), first)
        self.assertIn(b'pending', tab.path.read_bytes())

    def test_retarget_failure_retains_published_copy_and_blocks_source_autosave(self):
        tab = self.open()
        source, before = tab.path, tab.path.read_bytes()
        QTest.keyClicks(tab.widget, 'draft')
        with patch.object(self.w, 'remember_recent', side_effect=OSError('UI update failed')):
            self.assertIsNone(self.execute('save_as', tab, name='Saved copy'))
        self.assertTrue((self.root/'Saved copy.md').exists())
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(tab.widget.operation_error)
        self.assertFalse(self.w.close_tab(self.w.index_of(tab.key)))
        QTest.keyClicks(tab.widget, 'later')
        self.w.autosave.flush(tab)
        self.assertEqual(source.read_bytes(), before)
        self.assertIn(str(self.root/'Saved copy.md'), tab.widget.recovery_paths)

    def test_markdown_published_copy_cleanup_failure_stops_source_writer(self):
        tab = self.open()
        source, before = tab.path, tab.path.read_bytes()
        QTest.keyClicks(tab.widget, 'draft')
        original = Path.unlink
        def fail_cleanup(path, *args, **kwargs):
            if path.name.startswith('.pcs-new-'):
                raise PermissionError('temporary file locked')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_cleanup):
            self.assertIsNone(self.execute('save_as', tab, name='Published'))
        self.assertTrue((self.root/'Published.md').exists())
        self.assertEqual(source.read_bytes(), before)
        self.assertTrue(tab.widget.operation_error)
        self.w.autosave.flush(tab)
        self.assertEqual(source.read_bytes(), before)
        self.assertIn(str(self.root/'Published.md'), tab.widget.recovery_paths)

    def test_native_shortcuts_and_file_page_menu_target_only_the_focused_document(self):
        tab = self.open('Scene.pcs-category')
        self.w.tab_strip.bar.setFocus()
        self.events()
        def rename(dialog):
            dialog.name.setText('From F2')
            dialog.submit()
        self.dialog(lambda: QTest.keyClick(self.w.tab_strip.bar, Qt.Key.Key_F2), rename, RenameDialog)
        self.assertEqual(tab.path.name, 'From F2.pcs-category')
        self.w.activateWindow()
        self.w.tab_strip.bar.setFocus()
        self.events()
        def save(dialog):
            dialog.name.setText('From shortcut')
            dialog.submit()
        self.dialog(lambda: QTest.keyClick(self.w.tab_strip.bar, Qt.Key.Key_S,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier), save, SaveAsDialog)
        self.assertEqual(tab.path.name, 'From shortcut.pcs-category')
        tab.document.host.prompt_editor.library_menu()
        menu = self.app.activePopupWidget()
        self.assertIn('另存新檔…', [a.text() for a in menu.actions()])
        source = tab.path.read_bytes()
        with patch.object(tab.document, 'save', side_effect=AssertionError('paused writer')):
            self.w.autosave.paused.add(id(tab))
            next(a for a in menu.actions() if a.text() == '保存文件').trigger()
            self.w.autosave.paused.discard(id(tab))
        self.assertEqual(tab.path.read_bytes(), source)
        menu.close()
        # The existing material editor still owns F2 inside its listing.
        editor = tab.document.host.prompt_editor
        self.w.activateWindow()
        editor.listing.setFocus()
        editor.preview_id = tab.document.host.state['items'][0]['id']
        self.events()
        with patch.object(editor, 'edit_material') as edit:
            QTest.keyClick(editor.listing, Qt.Key.Key_F2)
            self.events()
            edit.assert_called_once()
        self.assertFalse(self.w.commands.files.pending)
        self.w.open_route('settings')
        self.w.tab_strip.bar.setFocus()
        QTest.keyClick(self.w.tab_strip.bar, Qt.Key.Key_F2)
        self.assertIsNone(self.app.activeModalWidget())
        self.assertFalse(self.w.commands.files.pending)

    def test_cached_navigation_page_paths_follow_move_and_close_releases_history(self):
        a = self.open()
        QTest.keyClicks(a.widget, 'kept')
        b = self.open('Empty.md')
        self.assertIn(a.key, self.w.saved_pages)
        # A hidden history page is still a writer/identity owner for relocation.
        self.assertIsNotNone(self.execute('move', path=a.path, directory=self.root/'Folder'))
        self.w.go(-1)
        self.assertIs(self.w.pages.currentWidget(), a.widget)
        self.assertEqual(a.path, self.root/'Folder/Notes.md')
        a.widget.undo()
        self.assertTrue(self.w.save_tab(a))
        self.assertFalse((self.root/'Notes.md').exists())
        self.w.close_tab(self.w.index_of(a.key))
        self.events()
        self.assertNotIn(a.key, self.w.saved_pages)
        self.assertNotIn(b.key, self.w.saved_pages)
        self.assertFalse(isValid(a.widget))

    def test_assets_move_with_dirty_workflow_and_survive_original_directory_removal(self):
        from local_document_fixtures import p0
        value, ids = p0.fixture()
        source = self.root/'Portable'
        (source/'assets').mkdir(parents=True)
        (source/'assets/p0-swatch.png').write_bytes(p0.png())
        document = assets.prepare(workflow.from_state(value, ids['workspace_a']), source, source)
        self.assertEqual(io.create(source, 'Graph.pcs-workflow', document).status, 'saved')
        tab = self.open('Portable/Graph.pcs-workflow')
        host = tab.document.host
        ident = next(iter(host.state['multi_output']['outputs']))
        host.canvas.commit(lambda s: drafts.edit(s, '', ident))
        before = copy.deepcopy(host.canvas.undo_stack)
        key = tab.key
        self.assertIsNotNone(self.execute('move', tab, directory=self.root/'Folder'))
        self.assertEqual(tab.key, key)
        self.assertEqual(host.canvas.undo_stack, before)
        self.assertEqual(io.load(tab.path.parent, tab.path.name).issues, ())
        self.assertTrue(self.w.save_tab(tab))
        for entry in io.load(tab.path.parent, tab.path.name).document['assets']:
            assets.resolve(tab.document.document, tab.path.parent, entry['reference'])
        self.assertTrue((source/'assets').is_dir())  # Shared original assets are preserved.

    def test_folder_move_preserves_internal_markdown_attachment_and_rejects_self_link_rename(self):
        folder = self.root/'Folder'
        (folder/'image.png').write_bytes(b'synthetic')
        (folder/'Photo.md').write_text('![photo](image.png)', encoding='utf-8')
        self.w.refresh_tree()
        target = self.root/'Target'
        target.mkdir()
        self.assertEqual(self.execute('move', path=folder, directory=target), target/'Folder')
        self.assertEqual((target/'Folder/image.png').read_bytes(), b'synthetic')
        tab = self.open()
        tab.widget.setPlainText('[self](Notes.md)')
        tab.widget.document().setModified(True)
        self.assertIsNone(self.execute('rename', tab, name='Broken'))
        self.assertEqual(tab.path.name, 'Notes.md')
