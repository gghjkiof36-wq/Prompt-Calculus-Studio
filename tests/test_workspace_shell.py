"""Real Qt document lifetimes, write targets and user-visible tab operations."""
import copy
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QMessageBox, QDialog, QListWidget, QPlainTextEdit

from prompt_calculus_studio import materials
from prompt_calculus_studio.local_documents import category, workflow, io, schema, assets
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from test_material_editor import state


class WorkspaceShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-p2-')
        self.root = Path(self.area.name)/'files'
        self.root.mkdir()
        value = state()
        owner = value['items'][0]['module']
        canvas = next(iter(value['multi_output']['canvases']))
        materials.add_to_canvas(value, value['items'][0]['id'], canvas)
        for name in ('Scene', 'Action'):
            doc = category.from_state(value, owner)
            self.assertEqual(io.create(self.root, name+'.pcs-category', doc).status, 'saved')
        for name in ('Street', 'Night'):
            doc = workflow.from_state(value)
            self.assertEqual(io.create(self.root, name+'.pcs-workflow', doc).status, 'saved')
        (self.root/'Notes.md').write_bytes(b'\xef\xbb\xbf# Notes\r\n')
        (self.root/'Empty.md').write_bytes(b'')
        (self.root/'Folder').mkdir()
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, value, trace: self.errors.append(str(value))
        self.w = WorkspaceShell(self.root, Path(self.area.name)/'data')
        self.w.report_error = self.errors.append
        self.w.show()
        self.events()

    def events(self):
        self.app.processEvents()
        QTest.qWait(15)
        self.app.processEvents()

    def tearDown(self):
        while self.w.tab_strip.bar.count():
            self.assertTrue(self.w.close_tab(0, decision='discard', make_home=False))
        self.w.close()
        self.w.deleteLater()
        self.events()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def open(self, name='Scene', suffix='pcs-category', retained=False):
        tab = self.w.open_path(self.root/(name+'.'+suffix), retained)
        self.events()
        self.assertIsNotNone(tab, self.errors)
        return tab

    def focus(self, tab):
        self.w.tab_strip.bar.setCurrentIndex(self.w.index_of(tab.key))
        self.events()

    def editor(self, tab):
        page = tab.document.host.prompt_editor
        ident = tab.document.host.state['items'][0]['id']
        page.search.setText(materials.item(tab.document.host.state, ident)['name'])
        self.events()
        entry = next(page.listing.item(i) for i in range(page.listing.count())
                     if page.listing.item(i).data(Qt.ItemDataRole.UserRole) == ident)
        pos = page.listing.visualItemRect(entry).center()
        QTest.mouseClick(page.listing.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        QTest.qWait(QApplication.doubleClickInterval()+20)
        self.assertEqual(page.preview_id, ident)
        QTest.mouseDClick(page.listing.viewport(), Qt.MouseButton.LeftButton, pos=pos)
        self.events()
        self.assertIsNotNone(page.active_dialog)
        return ident, page.active_dialog

    def test_preview_replacement_retention_dedup_and_explicit_new(self):
        a = self.open()
        self.assertFalse(a.retained)
        a_key = a.key
        b = self.open('Action')
        self.assertNotIn(a_key, self.w.tabs_by_key)
        self.assertEqual(self.w.tab_strip.bar.count(), 2)  # Home plus one preview
        self.assertIs(self.open('Action'), b)
        self.w.retain(b)
        a = self.open()
        self.assertIn(b.key, self.w.tabs_by_key)
        self.assertIs(self.open(retained=True), a)
        self.assertTrue(a.retained)
        before = self.w.tab_strip.bar.count()
        QTest.mouseClick(self.w.tab_strip.plus, Qt.MouseButton.LeftButton)
        self.assertEqual(self.w.tab_strip.bar.count(), before+1)

    def test_search_preview_edit_cancel_save_switch_and_real_undo(self):
        a = self.open()
        before = a.path.read_bytes()
        ident, dialog = self.editor(a)
        original = materials.item(a.document.host.state, ident)['prompt']
        QTest.keyClicks(dialog.prompt, ' cancelled')
        self.assertTrue(a.retained)
        self.assertTrue(a.document.draft_dirty)
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertFalse(a.document.dirty)
        self.assertEqual(a.path.read_bytes(), before)
        self.assertEqual(materials.item(a.document.host.state, ident)['prompt'], original)
        ident, dialog = self.editor(a)
        dialog.prompt.moveCursor(dialog.prompt.textCursor().MoveOperation.End)
        QTest.keyClicks(dialog.prompt, ' saved')
        native = dialog.prompt.document()
        self.assertTrue(native.isUndoAvailable())
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        expected = dialog.prompt.toPlainText()
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertTrue(a.document.dirty)
        self.assertEqual(a.path.read_bytes(), before)
        QTest.mouseClick(a.save_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertFalse(a.document.dirty, a.document.error)
        self.assertEqual(io.load(self.root, a.path.name).document['payload']['items'][0]['prompt'], expected)
        other_before = (self.root/'Action.pcs-category').read_bytes()
        native_cache = dict(a.document.host._material_editor_documents)
        b = self.open('Action', retained=True)
        self.focus(a)
        self.assertEqual(dict(a.document.host._material_editor_documents), native_cache)
        a.document.host.canvas.undo()
        self.events()
        self.assertEqual(materials.item(a.document.host.state, ident)['prompt'], original)
        self.assertTrue(a.document.dirty)
        a.document.host.canvas.redo()
        self.events()
        self.assertEqual(materials.item(a.document.host.state, ident)['prompt'], expected)
        self.assertEqual(b.path.read_bytes(), other_before)
        self.assertTrue(a.document.save(), a.document.error)
        self.assertTrue(self.w.close_tab(self.w.index_of(a.key)))
        reopened = self.open()
        self.assertEqual(materials.item(reopened.document.host.state, ident)['prompt'], expected)

    def test_dirty_notes_close_cancel_and_save_active_dialog(self):
        tab = self.open()
        ident, dialog = self.editor(tab)
        dialog.form.notes.setPlainText('draft notes, still in the modal editor')
        self.assertTrue(tab.document.draft_dirty)
        self.assertFalse(self.w.close_tab(self.w.index_of(tab.key), decision='cancel'))
        self.assertIs(tab.document.host.prompt_editor.active_dialog, dialog)
        self.assertEqual(dialog.form.notes.toPlainText(), 'draft notes, still in the modal editor')
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='save'))
        self.assertEqual(io.load(self.root, tab.path.name).document['payload']['items'][0]['notes'],
                         'draft notes, still in the modal editor')

    def test_close_question_cancel_keeps_dirty_page_and_native_documents(self):
        tab = self.open()
        ident, dialog = self.editor(tab)
        QTest.keyClicks(dialog.prompt, ' keep')
        self.assertTrue(dialog.save())
        self.events()
        host = tab.document.host
        cache = dict(host._material_editor_documents)
        def cancel():
            box = self.app.activeModalWidget()
            self.assertIsInstance(box, QMessageBox)
            QTest.mouseClick(next(b for b in box.buttons() if b.text() == '取消'), Qt.MouseButton.LeftButton)
        QTimer.singleShot(0, cancel)
        self.assertFalse(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertIs(tab.document.host, host)
        self.assertTrue(tab.document.dirty)
        self.assertEqual(dict(host._material_editor_documents), cache)

    def test_external_save_conflict_keeps_both_versions_and_failed_close(self):
        tab = self.open()
        host = tab.document.host
        ident = host.state['items'][0]['id']
        self.assertTrue(host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='local edit'))))
        external = io.load(self.root, tab.path.name)
        external.document['payload']['items'][0]['prompt'] = 'external edit'
        self.assertEqual(io.save(self.root, tab.path.name, external.document, external.receipt).status, 'saved')
        external_bytes = tab.path.read_bytes()
        self.assertFalse(self.w.close_tab(self.w.index_of(tab.key), decision='save'))
        self.assertTrue(tab.document.dirty)
        self.assertEqual(tab.path.read_bytes(), external_bytes)
        self.assertEqual(materials.item(host.state, ident)['prompt'], 'local edit')
        candidates = [Path(p) for p in tab.document.last_result.recovery_paths if p.endswith('.candidate')]
        self.assertTrue(candidates)
        self.assertEqual(schema.decode(candidates[0].read_bytes())['payload']['items'][0]['prompt'], 'local edit')
        captured = []
        def inspect():
            dialog = self.app.activeModalWidget()
            listing = dialog.findChild(QListWidget)
            viewer = dialog.findChild(QPlainTextEdit)
            for i in range(listing.count()):
                listing.setCurrentRow(i)
                captured.append(viewer.toPlainText())
            dialog.reject()
        QTimer.singleShot(0, inspect)
        self.w.show_recovery(tab.document)
        self.assertTrue(any('external edit' in s for s in captured))
        self.assertTrue(any('local edit' in s for s in captured))

    def test_cancelling_window_close_keeps_all_tabs_and_their_history(self):
        a = self.open(retained=True)
        b = self.open('Action', retained=True)
        ident = b.document.host.state['items'][0]['id']
        self.assertTrue(b.document.host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='pending'))))
        pages = {key: tab.widget for key, tab in self.w.tabs_by_key.items()}
        QTimer.singleShot(0, lambda: self.app.activeModalWidget().reject())
        self.assertFalse(self.w.close())
        self.assertEqual({key: tab.widget for key, tab in self.w.tabs_by_key.items()}, pages)
        self.assertTrue(b.document.dirty)
        b.document.host.canvas.undo()
        self.assertNotEqual(materials.item(b.document.host.state, ident)['prompt'], 'pending')

    def test_workflow_real_canvas_switch_preserves_camera_selection_history_and_other_file(self):
        a = self.open('Street', 'pcs-workflow', True)
        b_path = self.root/'Night.pcs-workflow'
        untouched = b_path.read_bytes()
        host = a.document.host
        canvas = host.canvas
        self.assertEqual(type(canvas).__name__, 'MultiCanvas')
        self.assertTrue(canvas.view.scene().items())
        key = next(iter(host.state['uses']))
        original = copy.deepcopy(host.state['uses'][key])
        self.assertTrue(canvas.commit(lambda s: s['uses'][key].update(prompt='workflow local edit')))
        self.events()
        canvas.zoom_to(.7)
        selected = next(iter(canvas.cards.values()))
        selected.setSelected(True)
        selection = [i.key for i in canvas.view.scene().selectedItems()]
        transform = canvas.view.transform()
        scene = canvas.view.scene()
        b = self.open('Night', 'pcs-workflow', True)
        self.focus(a)
        self.assertIs(canvas.view.scene(), scene)
        self.assertEqual(canvas.view.transform(), transform)
        self.assertEqual([i.key for i in canvas.view.scene().selectedItems()], selection)
        canvas.undo()
        self.assertEqual(host.state['uses'][key]['prompt'], original['prompt'])
        canvas.redo()
        self.assertEqual(host.state['uses'][key]['prompt'], 'workflow local edit')
        self.assertTrue(a.document.save(), a.document.error)
        self.assertEqual(io.load(self.root, a.path.name).document['payload']['state']['uses'][key]['prompt'], 'workflow local edit')
        self.assertEqual(b_path.read_bytes(), untouched)
        self.assertFalse(b.document.dirty)

    def test_same_file_alias_focuses_and_duplicate_identity_cannot_make_second_writer(self):
        tab = self.open(retained=True)
        link = self.root/'Alias.pcs-category'
        os.link(tab.path, link)
        self.assertIs(self.w.open_path(link), tab)
        duplicate = self.root/'Duplicate.pcs-category'
        shutil.copyfile(tab.path, duplicate)
        self.assertIsNone(self.w.open_path(duplicate))
        self.assertIn('同身分', self.errors.pop())
        self.assertEqual(sum(t.document is not None for t in self.w.tabs_by_key.values()), 1)

    def test_edit_file_with_attachment_and_remove_preview_preserves_original_asset(self):
        from PySide6.QtGui import QImage
        source = Path(self.area.name)/'asset-source'
        (source/'previews').mkdir(parents=True)
        image = QImage(4, 4, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.blue)
        self.assertTrue(image.save(str(source/'previews/swatch.png')))
        doc = io.load(self.root, 'Scene.pcs-category').document
        doc['payload']['items'][0]['preview'] = 'previews/swatch.png'
        doc = assets.prepare(doc, source, self.root)
        self.assertEqual(io.create(self.root, 'Assets.pcs-category', doc).status, 'saved')
        tab = self.open('Assets')
        ident = tab.document.host.state['items'][0]['id']
        original_asset = assets.resolve(doc, self.root, 'previews/swatch.png')
        original_bytes = original_asset.read_bytes()
        self.assertTrue(tab.document.host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='edit with asset'))))
        self.assertTrue(tab.document.save(), tab.document.error)
        loaded = io.load(self.root, tab.path.name)
        self.assertEqual(loaded.issues, ())
        self.assertTrue(tab.document.host.canvas.commit(lambda s: materials.save(s, dict(id=ident, preview=''))))
        self.assertTrue(tab.document.save(), tab.document.error)
        self.assertEqual(io.load(self.root, tab.path.name).document['assets'], [])
        self.assertEqual(original_asset.read_bytes(), original_bytes)

    def test_md_bytes_mixed_tree_and_narrow_overflow_keep_plus_accessible(self):
        paths = [self.root/'Notes.md', self.root/'Empty.md']
        before = [p.read_bytes() for p in paths]
        for p in paths:
            tab = self.w.open_path(p, True)
            self.assertTrue(tab.widget.isReadOnly())
        self.assertEqual([p.read_bytes() for p in paths], before)
        self.assertEqual(len(self.w.entries), 6)
        self.assertEqual(self.w.tree.topLevelItem(0).childCount(), 7)
        for _ in range(7):
            self.w.new_tab()
        self.w.resize(720, 600)
        self.events()
        strip = self.w.tab_strip
        self.assertTrue(strip.overflow.isVisible())
        self.assertLessEqual(strip.plus.geometry().right(), strip.width())
        self.assertLessEqual(strip.overflow.geometry().right(), strip.width())
        self.assertLessEqual(strip.plus.x()-strip.bar.geometry().right(), 5)
        before_key = strip.bar.tabData(0)
        strip.bar.moveTab(0, 3)
        self.assertEqual(strip.bar.tabData(3), before_key)
        self.w.toggle_sidebar()
        self.events()
        self.assertTrue(self.w.sidebar.isHidden())

    def test_original_tool_pages_remain_reachable_without_duplicate_navigation(self):
        a = self.open(retained=True)
        for route in ('gallery', 'explore', 'export', 'settings'):
            self.w.open_route(route)
            self.events()
            self.assertEqual(self.w.active_key, 'utility:'+route)
            self.assertFalse(self.w.utility.studio_navigation.isVisible())
            self.assertFalse(self.w.utility.canvas_header.isVisible())
        self.focus(a)
        self.assertTrue(a.document.host.prompt_editor.isVisible())
        self.assertFalse(a.document.host.canvas_header.isVisible())
        self.assertFalse(a.document.host.prompt_editor.workspace_header.isVisible())


if __name__ == '__main__':
    unittest.main()
