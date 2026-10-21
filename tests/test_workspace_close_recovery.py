"""Closing must not become an endless save-required loop."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint
from PySide6.QtTest import QTest
from prompt_calculus_studio.local_documents import io
from prompt_calculus_studio.workspace_shell import WorkspaceShell
import test_workspace_shell as fixture


class CloseRecoveryTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    open = fixture.WorkspaceShellTests.open
    events = fixture.WorkspaceShellTests.events

    def restart(self):
        data=self.w.data
        self.assertTrue(self.w.close());self.w.deleteLater();self.events()
        self.w=WorkspaceShell(self.root,data)
        self.w.autosave.delay_ms=60000
        self.w.report_error=self.errors.append
        self.w.show();self.events()

    def test_inspect_dismiss_does_not_edit_or_block_close(self):
        tab = self.open(retained=True)
        page = tab.document.host.prompt_editor
        before = tab.path.read_bytes()
        state = copy.deepcopy(tab.document.state)
        ident = tab.document.state['items'][0]['id']
        d = page.open_material(ident)
        self.events()
        self.assertEqual(d.windowModality(), Qt.WindowModality.NonModal)
        d.reject();self.events()
        self.assertFalse(tab.dirty)
        self.assertEqual(tab.document.state['items'], state['items'])
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertEqual(tab.path.read_bytes(), before)

    def test_library_preferences_are_local_and_save_does_not_fail(self):
        tab = self.open(retained=True)
        page = tab.document.host.prompt_editor
        before = tab.path.read_bytes()
        page.order.setCurrentIndex(page.order.findData('manual'))
        tab.document.state['settings']['material_folder_collapsed'] = []
        tab.document.update_dirty()
        self.assertFalse(tab.dirty)
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertEqual(tab.path.read_bytes(), before)

    def test_outside_dismiss_preserves_draft_and_explicit_discard_drops_it(self):
        tab = self.open(retained=True)
        page = tab.document.host.prompt_editor
        ident = tab.document.state['items'][0]['id']
        original = copy.deepcopy(tab.document.state['items'])
        d = page.open_material(ident)
        d.prompt.insertPlainText('draft preserved ')
        expected = d.prompt.toPlainText()
        self.events()
        QTest.mouseClick(self.w.centralWidget(), Qt.MouseButton.LeftButton, pos=QPoint(2, 2))
        self.events()
        self.assertIsNone(page.active_dialog)
        self.assertEqual(tab.document.state['items'], original)
        d = page.open_material(ident)
        self.assertEqual(d.prompt.toPlainText(), expected)
        d.discard();self.events()
        d = page.open_material(ident)
        self.assertNotEqual(d.prompt.toPlainText(), expected)
        d.reject()

    def test_failed_markdown_write_can_close_and_reopen_exact_bytes(self):
        tab = self.open('Notes', 'md', True)
        before = tab.path.read_bytes()
        tab.widget.insertPlainText('recover me ')
        expected = tab.widget.encoded()
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit', return_value=False):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertEqual(tab.path.read_bytes(), before)
        reopened = self.open('Notes', 'md', True)
        self.assertEqual(reopened.widget.encoded(), expected)
        self.assertTrue(reopened.dirty)
        self.assertTrue(self.w.save_tab(reopened))
        self.assertEqual(tab.path.read_bytes(), expected)

    def test_app_close_keeps_open_material_draft_without_prompt(self):
        tab = self.open(retained=True)
        page = tab.document.host.prompt_editor
        ident = tab.document.state['items'][0]['id']
        d = page.open_material(ident)
        d.prompt.insertPlainText('close draft ')
        expected = d.prompt.toPlainText()
        self.assertTrue(self.w.close())
        from prompt_calculus_studio.draft_cache import DraftCache
        value = DraftCache(tab.document.data).read('material:'+ident)
        self.assertEqual(value['form']['fields']['prompt'], expected)

    def test_unsupported_cached_state_keeps_original_and_recovery_record(self):
        from prompt_calculus_studio.workspace_recovery import key
        tab = self.open(retained=True)
        path, original = tab.path, tab.path.read_bytes()
        self.w.close_recovery.preserve(tab)
        value = self.w.close_recovery.cache.read(key(path))
        value['state']['version'] = 999
        self.w.close_recovery.cache.write(key(path), value)
        self.w.close_recovery.memory.clear()
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='discard'))
        reopened = self.open(retained=True)
        self.assertNotEqual(reopened.document.state['version'], 999)
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(self.w.close_recovery.cache.read(key(path))['state']['version'], 999)

    def test_repeated_close_of_external_conflict_never_overwrites_original(self):
        tab=self.open('Notes','md',True)
        tab.widget.insertPlainText('local draft ')
        expected=tab.widget.encoded()
        external=b'# External edit\n'
        tab.path.write_bytes(external)
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        for _ in range(3):
            tab=self.open('Notes','md',True)
            self.assertEqual(tab.widget.encoded(),expected)
            self.assertTrue(tab.widget.operation_error)
            self.assertFalse(self.w.save_tab(tab))
            self.assertEqual(tab.path.read_bytes(),external)
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))

    def test_failed_category_save_restores_committed_edit_after_restart(self):
        tab=self.open(retained=True)
        before=tab.path.read_bytes()
        dialog=tab.document.host.prompt_editor.open_material(tab.document.state['items'][0]['id'])
        dialog.prompt.setPlainText('recover category text')
        self.assertTrue(dialog.save(False))
        with patch('prompt_calculus_studio.document_host.io.save',side_effect=OSError('fixture write failure')):
            self.assertTrue(self.w.close())
        self.assertEqual(tab.path.read_bytes(),before)
        self.restart()
        tab=self.w.open_path(tab.path,True)
        self.assertEqual(tab.document.state['items'][0]['prompt'],'recover category text')
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(io.load(self.root,tab.path.name).document['payload']['items'][0]['prompt'],
                         'recover category text')

    def test_restart_restores_material_draft_and_saves_it_once(self):
        tab=self.open(retained=True)
        ident=tab.document.state['items'][0]['id']
        dialog=tab.document.host.prompt_editor.open_material(ident)
        dialog.prompt.setPlainText('recover after restart')
        self.restart()
        tab=self.w.open_path(tab.path,True)
        dialog=tab.document.host.prompt_editor.open_material(ident)
        self.assertEqual(dialog.prompt.toPlainText(),'recover after restart')
        self.assertTrue(dialog.save(False));self.events()
        self.assertTrue(self.w.save_tab(tab))
        self.restart()
        tab=self.w.open_path(tab.path,True)
        dialog=tab.document.host.prompt_editor.open_material(ident)
        self.assertEqual(dialog.prompt.toPlainText(),'recover after restart')
        self.assertIsNone(dialog.restored_draft)
        dialog.reject()

    def test_write_failure_on_app_close_restores_text_and_layout_after_restart(self):
        tab=self.open('Notes','md',True)
        tab.widget.insertPlainText('cached on shutdown ')
        expected=tab.widget.encoded()
        with patch('prompt_calculus_studio.workspace_files.QSaveFile.commit',return_value=False):
            self.assertTrue(self.w.close())
        data=self.w.data
        self.w.deleteLater();self.events()
        self.w=WorkspaceShell(self.root,data);self.w.show();self.events()
        reopened=next(t for t in self.w.all_pages() if t.path==tab.path)
        self.assertEqual(reopened.widget.encoded(),expected)
        self.assertTrue(self.w.save_tab(reopened))

    def test_group_and_empty_variant_drafts_survive_dismissal(self):
        tab=self.open(retained=True)
        page=tab.document.host.prompt_editor
        dialog=page.new_material()
        picker=dialog.pages.type_picker
        picker.setCurrentIndex(picker.findData('flow'))
        editor=dialog.pages.group_editors['flow']
        editor.add_group();editor.sequence['groups'][0]['text']='unsaved group'
        dialog.form.edited();expected=copy.deepcopy(editor.sequence)
        dialog.reject();self.events()
        dialog=page.new_material()
        self.assertEqual(dialog.pages.material_type,'flow')
        self.assertEqual(dialog.pages.group_editors['flow'].sequence,expected)
        dialog.discard();self.events()
        dialog=page.new_material()
        dialog.pages.enable_variants()
        variant=dialog.pages.variant_editor
        variant.stack.currentWidget().setPlainText('')
        dialog.form.edited();expected=copy.deepcopy(variant.sequence)
        dialog.reject();self.events()
        dialog=page.new_material()
        self.assertEqual(dialog.pages.variant_editor.sequence,expected)
        dialog.discard()


if __name__ == '__main__':unittest.main()
