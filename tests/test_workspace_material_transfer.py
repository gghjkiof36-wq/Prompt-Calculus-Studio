"""Cross-document placements retain originals, writer identity and shared Undo."""
import copy
from pathlib import Path
from unittest.mock import patch
import unittest

from PySide6.QtCore import QPointF, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio import materials, multi_output
from prompt_calculus_studio.local_documents import io
from prompt_calculus_studio.workspace_material_menu import open_menu
import test_workspace_shell as fixture


class WorkspaceMaterialTransferTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    events = fixture.WorkspaceShellTests.events

    def setUp(self):
        fixture.WorkspaceShellTests.setUp(self)
        self.source_tab = self.w.open_path(self.root/'Scene.pcs-category', True)
        self.events()
        self.page = self.source_tab.host.prompt_editor
        self.ident = self.source_tab.host.state['items'][0]['id']
        self.transfer = self.w.material_transfer

    def tearDown(self):
        for popup in QApplication.topLevelWidgets():
            if popup.windowType() == Qt.WindowType.Popup:popup.close()
        for pane in list(self.w.panes):
            while pane in self.w.panes and pane.strip.bar.count():
                self.w.close_tab(0, decision='discard', make_home=False, pane=pane)
        self.w.close()
        self.w.deleteLater()
        self.events()
        import sys
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def capture(self):
        return self.transfer.capture(self.page, self.ident)

    def target_tab(self):
        tab = self.w.open_path(self.root/'Street.pcs-workflow', True, new_tab=True)
        self.events()
        return tab

    def test_blank_background_placement_preserves_source_focus_and_saves_without_manual_step(self):
        source = self.capture()
        before = copy.deepcopy(source.document.state['items'])
        original = (self.root/'Scene.pcs-category').read_bytes()
        active, widget = self.w.active_key, self.w.pages.currentWidget()
        self.page.search.setFocus()
        focus = self.app.focusWidget()
        self.w.autosave.delay_ms = 60
        key = self.transfer.add(source, self.transfer.target(self.root/'Street.pcs-workflow'))
        self.assertTrue(key)
        self.assertEqual(self.w.active_key, active)
        self.assertIs(self.w.pages.currentWidget(), widget)
        self.assertIs(self.app.focusWidget(), focus)
        tab = next(t for t in self.w.all_pages() if t.path == self.root/'Street.pcs-workflow')
        self.assertIsNone(multi_output.owner(tab.host.state, key))
        self.assertEqual(len(tab.host.state['multi_output']['canvases']), 1)
        self.assertEqual(source.document.state['items'], before)
        self.assertEqual((self.root/'Scene.pcs-category').read_bytes(), original)
        self.assertIsNone(self.transfer.notification)
        QTest.qWait(250)
        self.assertFalse(tab.dirty)
        saved = io.load(self.root, 'Street.pcs-workflow').document['payload']['state']
        self.assertEqual(saved['uses'][key]['source_document'], source.document_id)
        self.w.close_tab(self.w.index_of(tab.key))
        tab = self.w.open_path(self.root/'Street.pcs-workflow', True, new_tab=True)
        self.events()
        self.assertIn(key, tab.host.state['uses'])

    def test_canvas_order_and_shared_undo_redo_do_not_modify_other_documents(self):
        tab = self.target_tab()
        duplicate = self.w.open_path(tab.path, True, new_tab=True)
        self.events()
        other = self.w.open_path(self.root/'Night.pcs-workflow', True, new_tab=True)
        self.events()
        other_before = copy.deepcopy(other.host.state['uses'])
        canvas_id = next(iter(tab.host.state['multi_output']['canvases']))
        order = list(tab.host.state['output_order'])
        members = list(tab.host.state['multi_output']['canvases'][canvas_id]['members'])
        key = self.transfer.add(self.capture(), self.transfer.target(tab.path), canvas_id, host=tab.host)
        self.assertTrue(key)
        self.events()
        self.assertEqual(tab.host.state['output_order'], order+[key])
        self.assertEqual(tab.host.state['multi_output']['canvases'][canvas_id]['members'], members+[key])
        self.assertIn(key, duplicate.host.state['uses'])
        duplicate.host.canvas.undo()
        self.events()
        self.assertNotIn(key, tab.host.state['uses'])
        self.assertEqual(tab.host.state['output_order'], order)
        duplicate.host.canvas.redo()
        self.events()
        self.assertIn(key, tab.host.state['uses'])
        self.assertEqual(other.host.state['uses'], other_before)

    def test_required_attachment_is_copied_independently_and_missing_one_is_atomic(self):
        asset = self.source_tab.document.data/'previews/test.png'
        asset.parent.mkdir(parents=True, exist_ok=True)
        asset.write_bytes(b'synthetic-preview')
        materials.item(self.source_tab.host.state, self.ident)['preview'] = 'previews/test.png'
        target = self.transfer.target(self.root/'Street.pcs-workflow')
        key = self.transfer.add(self.capture(), target)
        self.assertTrue(key)
        tab = next(t for t in self.w.all_pages() if t.path == target.path)
        ref = tab.host.state['uses'][key]['preview']
        self.assertTrue(ref.startswith('transfers/'))
        self.assertEqual((tab.document.data/ref).read_bytes(), b'synthetic-preview')
        before = copy.deepcopy(tab.host.state['uses'])
        asset.unlink()
        self.assertFalse(self.transfer.add(self.capture(), self.transfer.target(tab.path)))
        self.assertEqual(tab.host.state['uses'], before)
        self.assertTrue(self.transfer.notification.isVisible())

    def test_stale_source_target_and_unconfirmed_form_do_not_change_content(self):
        source = self.capture()
        target = self.transfer.target(self.root/'Street.pcs-workflow')
        original = target.path.read_bytes()
        materials.item(self.source_tab.host.state, self.ident)['prompt'] += ' changed'
        self.assertFalse(self.transfer.add(source, target))
        self.assertEqual(target.path.read_bytes(), original)
        self.page.active_dialog = object()
        try:
            with self.assertRaises(ValueError):self.capture()
        finally:self.page.active_dialog = None
        source = self.capture()
        target.path.write_bytes((self.root/'Night.pcs-workflow').read_bytes())
        replaced = target.path.read_bytes()
        self.assertFalse(self.transfer.add(source, target))
        self.assertEqual(target.path.read_bytes(), replaced)

    def test_failed_autosave_retains_one_copy_and_retry_only_writes(self):
        tab = self.target_tab()
        key = self.transfer.add(self.capture(), self.transfer.target(tab.path))
        uses = copy.deepcopy(tab.host.state['uses'])
        with patch.object(io, 'save', return_value=io.SaveResult('conflict', error='synthetic write conflict')):
            self.w.autosave.flush(tab)
        self.assertTrue(tab.dirty)
        self.assertIn('已加入，尚未自動儲存', self.transfer.notification.message.text())
        tab.document.error = ''
        self.assertTrue(self.w.save_tab(tab))
        self.assertEqual(tab.host.state['uses'], uses)
        tab.host.canvas.undo()
        self.events()
        self.assertNotIn(key, tab.host.state['uses'])

    def test_hierarchical_menu_is_lazy_and_reaches_background_document(self):
        self.page.preview_material(self.ident)
        self.events()
        self.assertTrue(self.page.add_canvas_button.isVisible())
        before = len(self.w.documents)
        menu = open_menu(self.page, self.ident, self.page.mapToGlobal(self.page.rect().center()))
        self.events()
        entry = next(a for a in menu.actions() if a.menu() and a.data() == str(self.root/'Street.pcs-workflow'))
        child = entry.menu()
        child.aboutToShow.emit()
        self.assertEqual(len(self.w.documents), before)
        choices = [a.text() for a in child.actions() if not a.isSeparator()]
        self.assertEqual(choices[0], 'Canvas空白處')
        self.assertGreater(len(choices), 1)
        menu.close()
        self.events()
        self.assertEqual(len(self.w.documents), before)
        menu = open_menu(self.page, self.ident, self.page.mapToGlobal(self.page.rect().center()))
        self.events()
        child = next(a.menu() for a in menu.actions() if a.data() == str(self.root/'Street.pcs-workflow'))
        child.aboutToShow.emit()
        child.actions()[0].trigger()
        self.events()
        self.assertEqual(self.w.active_key, self.source_tab.key)
        self.assertEqual(len(self.w.documents), before+1)


if __name__ == '__main__':unittest.main()
