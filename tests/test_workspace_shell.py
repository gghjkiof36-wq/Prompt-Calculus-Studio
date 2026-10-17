"""Real Qt document lifetimes, write targets and user-visible tab operations."""
import copy
import os
from pathlib import Path
from unittest.mock import patch
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
        self.w.autosave.delay_ms = 60000  # Autosave timing has its own integration cases.
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
        self.w.activateWindow()
        self.w.setFocus()
        self.events()
        QTest.keyClick(self.w,Qt.Key.Key_S,Qt.KeyboardModifier.ControlModifier)
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

    def test_close_saves_dirty_page_without_confirmation(self):
        tab = self.open()
        ident, dialog = self.editor(tab)
        QTest.keyClicks(dialog.prompt, ' keep')
        self.assertTrue(dialog.save())
        self.events()
        expected = materials.item(tab.document.host.state, ident)['prompt']
        with patch.object(QMessageBox, 'exec', side_effect=AssertionError('unexpected save confirmation')):
            self.assertTrue(self.w.close_tab(self.w.index_of(tab.key)))
        self.assertNotIn(tab.key, self.w.tabs_by_key)
        self.assertEqual(io.load(self.root, tab.path.name).document['payload']['items'][0]['prompt'], expected)

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
        self.assertIn(tab.document.error, self.w.notification.text())
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

    def test_failed_window_close_keeps_all_tabs_and_their_history(self):
        a = self.open(retained=True)
        b = self.open('Action', retained=True)
        ident = b.document.host.state['items'][0]['id']
        self.assertTrue(b.document.host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='pending'))))
        pages = {key: tab.widget for key, tab in self.w.tabs_by_key.items()}
        with patch.object(b.document, 'save', return_value=False):
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
            self.assertFalse(tab.widget.isReadOnly())
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
        self.w.toggle_sidebar()  # Narrow layout starts collapsed; explicitly open it.
        self.events()
        self.assertFalse(self.w.sidebar.isHidden())
        self.w.toggle_sidebar()
        self.events()
        self.assertTrue(self.w.sidebar.isHidden())

    def test_tool_navigation_keeps_home_tabs_and_dirty_document_without_duplicate_sidebar(self):
        a = self.open(retained=True)
        host = a.document.host
        ident = host.state['items'][0]['id']
        self.assertTrue(host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='keep while browsing tools'))))
        original_tabs = dict(self.w.tabs_by_key)
        for route in ('gallery', 'explore', 'export', 'settings'):
            self.w.open_route(route)
            self.events()
            self.assertEqual(self.w.active_key, 'utility:'+route)
            self.assertEqual(self.w.tabs_by_key, original_tabs)
            self.assertTrue(self.w.tab_strip.isVisible())
            self.assertFalse(self.w.tab_strip.bar.workspace_active)
            self.assertFalse(self.w.sidebar.isVisible())
            self.assertFalse(self.w.utility.studio_navigation.isVisible())
            self.assertFalse(self.w.utility.canvas_header.isVisible())
            self.assertTrue(self.w.nav_controls.isVisible())
            self.assertEqual(self.w.nav_controls.parentWidget(), self.w.top)
            self.assertTrue(self.w.back.isVisible())
            if route in ('explore', 'settings'):
                self.assertFalse(self.w.utility.settings_page.return_button.isVisible())
        self.assertFalse(self.w.close_current())  # Ctrl+W in a tool cannot close a hidden draft.
        QTest.mouseClick(self.w.tab_strip.bar,Qt.MouseButton.LeftButton,
                         pos=self.w.tab_strip.bar.tabRect(self.w.index_of(a.key)).center())
        self.events()
        self.assertEqual(self.w.active_key, a.key)
        self.assertTrue(self.w.tab_strip.isVisible())
        self.assertTrue(self.w.tab_strip.bar.workspace_active)
        self.assertTrue(self.w.sidebar.isVisible())
        self.assertTrue(a.document.dirty)
        self.assertEqual(materials.item(host.state, ident)['prompt'], 'keep while browsing tools')
        self.w.open_route('gallery')
        self.w.tab_strip.show_list()
        menu = QApplication.activePopupWidget()
        self.assertIsNotNone(menu)
        menu.actions()[self.w.index_of(a.key)].trigger()
        menu.close()
        self.events()
        self.assertEqual(self.w.active_key, a.key)
        self.assertTrue(self.w.sidebar.isVisible())
        self.assertTrue(a.document.dirty)
        host.canvas.undo()
        self.assertNotEqual(materials.item(host.state, ident)['prompt'], 'keep while browsing tools')
        self.assertTrue(a.document.host.prompt_editor.isVisible())
        self.assertFalse(a.document.host.canvas_header.isVisible())
        self.assertFalse(a.document.host.prompt_editor.workspace_header.isVisible())

    def test_full_width_tabs_and_horizontal_history_restore_document_and_tool(self):
        a = self.open(retained=True)
        self.w.open_route('gallery')
        self.events()
        self.w.open_route('export')
        self.events()
        QTest.mouseClick(self.w.back, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(self.w.active_route, 'gallery')
        QTest.keyClick(self.w, Qt.Key.Key_Left, Qt.KeyboardModifier.AltModifier)
        self.events()
        self.assertEqual(self.w.active_key, a.key)
        QTest.keyClick(self.w, Qt.Key.Key_Right, Qt.KeyboardModifier.AltModifier)
        self.events()
        self.assertEqual(self.w.active_route, 'gallery')
        self.w.open_route('home')
        self.events()
        self.assertEqual(self.w.top.width(), self.w.centralWidget().width())
        self.assertEqual(self.w.top.x(), 0)
        self.assertEqual(self.w.back.y(), self.w.forward.y())
        self.assertEqual(self.w.nav_controls.parentWidget(), self.w.top)
        from PySide6.QtCore import QPoint
        self.assertEqual(self.w.rail.mapTo(self.w, QPoint()).y(), self.w.top.height())
        self.assertEqual(self.w.collapse.mapTo(self.w,self.w.collapse.rect().center()).x(),
                         self.w.routes['home'].mapTo(self.w,self.w.routes['home'].rect().center()).x())

    def test_sidebar_edge_resizes_clamps_collapses_and_reopens_on_each_route(self):
        from PySide6.QtCore import QPoint
        def drag(dx):
            self.w.sidebar_resize.sync()
            handle = self.w.sidebar_resize.handle
            self.assertTrue(handle.isVisible())
            origin = handle.mapToGlobal(handle.rect().center())
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=handle.rect().center())
            destination = origin+QPoint(dx, 0)
            QTest.mouseMove(handle, handle.mapFromGlobal(destination))
            self.events()
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=handle.mapFromGlobal(destination))
            self.events()
        for route in ('home', 'gallery', 'export', 'explore', 'settings'):
            self.w.open_route(route)
            self.w.sidebar_preference = True
            self.w.sidebar_width = 264
            self.w.sync_sidebar()
            self.events()
            drag(60)
            self.assertEqual(self.w.sidebar_target().width(), 324, route)
            drag(-116)
            self.assertEqual(self.w.sidebar_target().width(), 220, route)
            self.assertTrue(self.w.sidebar_target().isVisible())
            drag(-60)
            self.assertFalse(self.w.sidebar_target().isVisible(), route)
            QTest.mouseClick(self.w.collapse, Qt.MouseButton.LeftButton)
            self.events()
            self.assertTrue(self.w.sidebar_target().isVisible(), route)
            self.assertEqual(self.w.sidebar_target().width(), 220, route)

    def test_display_controls_and_sidebar_preferences_survive_restart_without_changing_document(self):
        tab = self.open(retained=True)
        before = tab.path.read_bytes()
        document_settings = copy.deepcopy(tab.document.host.state['settings'])
        old_font = self.w.tree.font().pixelSize()
        self.w.open_route('settings', 'appearance')
        self.events()
        controls = self.w.utility.settings_page.preferences
        requested_size = self.w.settings['ui_size'] + 1
        controls.ui_size.setValue(requested_size)
        controls.material.setCurrentIndex(controls.material.findData('acrylic'))
        controls.visual_palette.setCurrentIndex(controls.visual_palette.findData('paper'))
        controls.accent.setCurrentIndex(controls.accent.findData('green'))
        controls.density.setCurrentIndex(controls.density.findData('compact'))
        self.w.utility.settings_page.flush()
        self.events()
        self.w.open_route('home')
        self.events()
        self.assertGreater(self.w.tree.font().pixelSize(), old_font)
        self.assertEqual(self.w.settings['material'], 'acrylic')
        self.assertEqual(self.w.settings['visual_palette'], 'paper')
        self.assertEqual(self.w.settings['accent'], 'green')
        self.assertEqual(self.w.settings['density'], 'compact')
        from prompt_calculus_studio.canvas_items import canvas_colors
        from prompt_calculus_studio.views import widget_colors
        from PySide6.QtGui import QPalette, QColor
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window),QColor('#f5f3ee'))
        self.assertEqual(canvas_colors(tab.document.host.canvas)['palette'],'paper')
        self.assertEqual(widget_colors(tab.document.host.prompt_editor.listing)['palette'],'paper')
        self.assertEqual(tab.path.read_bytes(), before)
        self.assertEqual(tab.document.host.state['settings'], document_settings)
        self.assertFalse(tab.document.dirty)
        self.w.sidebar_resize.resize_to(314)
        self.w.toggle_sidebar()
        self.events()
        while self.w.tab_strip.bar.count():
            self.w.close_tab(0, decision='discard', make_home=False)
        self.w.close()
        self.w.deleteLater()
        self.events()
        self.w = WorkspaceShell(self.root, Path(self.area.name)/'data')
        self.w.report_error = self.errors.append
        self.w.show()
        self.events()
        self.assertEqual(self.w.settings['ui_size'], requested_size)
        self.assertEqual(self.w.settings['material'], 'acrylic')
        self.assertEqual(self.w.settings['visual_palette'], 'paper')
        self.assertEqual(self.w.settings['accent'], 'green')
        self.assertEqual(self.w.settings['density'], 'compact')
        self.assertEqual(self.w.sidebar_width, 314)
        self.assertFalse(self.w.sidebar.isVisible())
        QTest.mouseClick(self.w.collapse, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(self.w.sidebar.width(), 314)
        self.w.open_route('settings', 'appearance')
        self.events()
        self.assertEqual(self.w.utility.settings_page.preferences.ui_size.value(), requested_size)
        self.assertEqual(self.w.utility.settings_page.preferences.material.currentData(), 'acrylic')
        self.assertEqual(self.w.utility.settings_page.preferences.visual_palette.currentData(), 'paper')
        self.assertNotIn('interface',[entry[0] for entry in self.w.utility.settings_page.nav_routes])
        reopened=self.open(retained=True)
        self.events()
        self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window),QColor('#f5f3ee'))
        self.assertEqual(canvas_colors(reopened.document.host.canvas)['palette'],'paper')

    def test_navigation_position_keeps_tabs_sidebars_and_drafts_and_survives_restart(self):
        from PySide6.QtCore import QPoint
        tab = self.open(retained=True)
        before = tab.path.read_bytes()
        host = tab.document.host
        ident = host.state['items'][0]['id']
        self.assertTrue(host.canvas.commit(lambda s: materials.save(s, dict(id=ident, prompt='kept draft'))))
        self.assertTrue(tab.dirty)
        count = self.w.tab_strip.bar.count()
        self.w.sidebar_preference = True
        self.w.sidebar_width = 314
        self.assertEqual(self.w.settings['ui_size'], 12)
        self.w.settings['ui_size'] = 13
        self.w.open_route('settings', 'appearance')
        self.events()
        page = self.w.utility.settings_page
        position = page.navigation_position
        self.assertEqual(position.currentData(), 'side')
        position.setCurrentIndex(position.findData('top'))
        self.events()
        self.assertEqual(self.w.active_route, 'settings')
        self.assertEqual(self.w.navigation_position, 'top')
        self.assertTrue(self.w.tab_row.isVisible())
        self.assertLess(self.w.top.geometry().bottom(), self.w.tab_row.geometry().top())
        self.assertIs(self.w.rail.parentWidget(), self.w.top)
        self.assertTrue(self.w.sidebar_target().isVisible())
        self.assertEqual(self.w.sidebar_target().width(), 314)
        self.assertEqual(self.w.tab_strip.bar.mapTo(self.w, QPoint()).x(), 314)
        QTest.mouseClick(self.w.collapse, Qt.MouseButton.LeftButton)
        self.events()
        self.assertFalse(self.w.sidebar_target().isVisible())
        self.assertEqual(self.w.tab_strip.bar.mapTo(self.w, QPoint()).x(), 16)
        position.setCurrentIndex(position.findData('side'))
        self.events()
        self.assertFalse(self.w.tab_row.isVisible())
        self.assertFalse(self.w.sidebar_target().isVisible())
        position.setCurrentIndex(position.findData('top'))
        self.events()
        QTest.mouseClick(self.w.collapse, Qt.MouseButton.LeftButton)
        self.events()
        self.assertEqual(self.w.sidebar_target().width(), 314)
        for route in ('gallery', 'explore', 'export', 'settings'):
            QTest.mouseClick(self.w.routes[route], Qt.MouseButton.LeftButton)
            self.events()
            self.assertEqual(self.w.active_route, route)
            self.assertEqual(self.w.tab_strip.bar.count(), count)
            self.assertTrue(self.w.sidebar_target().isVisible())
            self.assertEqual(self.w.tab_strip.bar.mapTo(self.w, QPoint()).x(), self.w.sidebar_target().width())
        self.w.resize(720, 560)
        self.events()
        self.assertEqual(self.w.width(), 720)
        for entry in self.w.routes.values():
            right = entry.mapTo(self.w, entry.rect().topRight()).x()
            self.assertLess(right, self.w.connection.mapTo(self.w,QPoint()).x())
            self.assertTrue(entry.isVisible())
        QTest.mouseClick(self.w.tab_strip.bar, Qt.MouseButton.LeftButton,
                        pos=self.w.tab_strip.bar.tabRect(self.w.index_of(tab.key)).center())
        self.events()
        self.assertEqual(self.w.active_route, 'home')
        self.assertEqual(self.w.active_key, tab.key)
        self.assertEqual(materials.item(host.state,ident)['prompt'], 'kept draft')
        self.assertTrue(tab.dirty)
        self.assertEqual(tab.path.read_bytes(), before)
        while self.w.tab_strip.bar.count():
            self.w.close_tab(0, decision='discard', make_home=False)
        self.w.close(); self.w.deleteLater(); self.events()
        self.w = WorkspaceShell(self.root, Path(self.area.name)/'data')
        self.w.report_error = self.errors.append
        self.w.show(); self.events()
        self.assertEqual(self.w.navigation_position, 'top')
        self.assertTrue(self.w.tab_row.isVisible())
        self.w.open_route('settings', 'appearance'); self.events()
        self.assertEqual(self.w.utility.settings_page.navigation_position.currentData(), 'top')
        self.w.utility.settings_page.reset_appearance()
        self.events()
        self.assertEqual(self.w.navigation_position, 'side')
        self.assertEqual(self.w.settings['ui_size'], 12)
        self.assertEqual(self.w.utility.settings_page.preferences.ui_size.value(), 12)
        self.w.utility.settings_page.reset_appearance(restore=True)
        self.events()
        self.assertEqual(self.w.navigation_position, 'top')
        self.assertEqual(self.w.settings['ui_size'], 13)
        self.assertEqual((self.root/'Scene.pcs-category').read_bytes(), before)

    def test_palette_round_trip_refreshes_existing_preview_and_content_surface(self):
        from PySide6.QtGui import QColor, QPalette
        from prompt_calculus_studio.theme import visual_tokens
        from prompt_calculus_studio.workspace_style import workspace_colors
        tab = self.open(retained=True)
        page = tab.document.host.prompt_editor
        before = tab.path.read_bytes()
        document_settings = copy.deepcopy(tab.document.host.state['settings'])
        entry = page.listing.item(0)
        QTest.mouseClick(page.listing.viewport(), Qt.MouseButton.LeftButton,
                        pos=page.listing.visualItemRect(entry).center())
        QTest.qWait(QApplication.doubleClickInterval()+20)
        self.events()
        self.assertIsNotNone(page.preview_id)
        preview_text = page.preview_text.toPlainText()
        self.assertTrue(preview_text)
        for palette in ('paper', 'mist', 'graphite'):
            with self.subTest(palette=palette):
                self.w.open_route('settings', 'appearance')
                self.events()
                controls = self.w.utility.settings_page.preferences
                controls.visual_palette.setCurrentIndex(controls.visual_palette.findData(palette))
                self.w.utility.settings_page.flush()
                self.focus(tab)
                self.w.open_route('home')
                self.events()
                appearance = self.w.display_settings()
                self.assertEqual(page.preview_text.palette().color(QPalette.ColorRole.Text),
                                 QColor(visual_tokens(appearance)['text']))
                self.assertEqual(page.preview_text.toPlainText(), preview_text)
                # Sample the rendered empty gutter, not a token or a CSS string.
                image = page.shell.grab().toImage()
                self.assertEqual(image.pixelColor(3, image.height()//2),
                                 QColor(workspace_colors(appearance)['content']))
                self.assertEqual(tab.path.read_bytes(), before)
                self.assertEqual(tab.document.host.state['settings'], document_settings)
                self.assertFalse(tab.document.dirty)

    def test_utility_routes_keep_window_bounds_and_contour_after_material_and_resize(self):
        from PySide6.QtCore import QPoint
        self.w.resize(1100,760)
        self.events()
        for route in ('gallery','export','explore','settings'):
            self.w.open_route(route)
            self.events()
            self.assertEqual((self.w.width(),self.w.height()),(1100,760))
            page = self.w.workspace_stack.currentWidget()
            self.assertFalse(page.mask().contains(QPoint(0,0)))
            self.assertTrue(page.mask().contains(page.rect().center()))
        self.w.open_route('settings','appearance')
        self.events()
        preferences = self.w.utility.settings_page.preferences
        preferences.material.setCurrentIndex(preferences.material.findData('acrylic'))
        self.w.utility.settings_page.flush()
        self.w.resize(800,560)
        self.events()
        page = self.w.workspace_stack.currentWidget()
        self.assertEqual((self.w.width(),self.w.height()),(800,560))
        self.assertFalse(page.mask().isEmpty())
        self.assertFalse(page.mask().contains(QPoint(0,0)))
        self.assertTrue(page.mask().contains(QPoint(page.width()-2,page.height()-2)))

    def test_start_page_creation_recent_files_and_collision_preserve_existing_content(self):
        self.assertEqual(self.w.pages.currentWidget().listing.count(), 0)
        for kind, name in (('workflow', 'New canvas'), ('category', 'New category'), ('markdown', 'New note')):
            tab = self.w.new_document(kind, name)
            self.events()
            self.assertIsNotNone(tab, self.errors)
            self.assertTrue(tab.path.is_file())
            before = tab.path.read_bytes()
            self.assertIsNone(self.w.new_document(kind, name))
            self.assertEqual(tab.path.read_bytes(), before)
            self.assertTrue(self.errors.pop())
        page = self.w.new_tab().widget
        self.events()
        self.assertEqual(page.listing.count(), 3)
        self.assertEqual(page.listing.item(0).text(), 'New note')
        self.assertEqual(set(page.create_buttons), {'workflow', 'category', 'markdown'})
        self.assertLessEqual(page.content.width(), 620)
        self.assertAlmostEqual(page.content.geometry().center().x(), page.rect().center().x(), delta=2)
        self.assertEqual(self.w.tree.columnCount(), 1)
        self.w.save_recent()
        import json
        self.assertEqual(json.loads((self.w.data/'recent-files.json').read_text(encoding='utf-8'))[0], str(self.root/'New note.md'))

    def test_markdown_edit_save_cancel_reopen_and_conflict_preserve_bytes(self):
        tab = self.w.open_path(self.root/'Notes.md', True)
        editor = tab.widget
        editor.moveCursor(editor.textCursor().MoveOperation.End)
        QTest.keyClicks(editor, 'kept')
        self.assertTrue(tab.dirty)
        self.assertFalse(self.w.close_tab(self.w.index_of(tab.key), decision='cancel'))
        self.assertTrue(self.w.save_current())
        self.assertEqual(tab.path.read_bytes(), b'\xef\xbb\xbf# Notes\r\nkept')
        editor.undo()
        self.assertTrue(tab.dirty)
        editor.redo()
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key), decision='save'))
        reopened = self.w.open_path(self.root/'Notes.md', True)
        self.assertTrue(reopened.widget.toPlainText().endswith('kept'))
        QTest.keyClicks(reopened.widget, 'local')
        reopened.path.write_bytes(b'external')
        self.assertFalse(self.w.close_tab(self.w.index_of(reopened.key), decision='save'))
        self.assertIn('外部修改', self.w.notification.text())
        self.assertEqual(reopened.path.read_bytes(), b'external')
        self.assertTrue(reopened.dirty)

    def test_tabs_start_wide_shrink_then_overflow_with_titles_still_available(self):
        bar = self.w.tab_strip.bar
        self.events()
        self.assertGreaterEqual(bar.tabRect(0).width(), 210)
        for _ in range(7):
            self.w.new_tab()
        self.events()
        self.assertGreaterEqual(bar.tabRect(0).width(), 124)
        self.assertLess(bar.tabRect(0).width(), 210)
        for _ in range(8):
            self.w.new_tab()
        self.events()
        self.assertTrue(self.w.tab_strip.overflow.isVisible())
        self.assertEqual(bar.tabToolTip(0), '')  # An unnamed home has no status to explain.

    def test_markdown_alias_from_tool_returns_to_same_unsaved_editor(self):
        tab = self.w.open_path(self.root/'Notes.md', True)
        QTest.keyClicks(tab.widget, 'keep local')
        alias = self.root/'Alias.md'
        os.link(tab.path, alias)
        self.w.open_route('gallery')
        opened = self.w.open_path(alias, True)
        self.assertIs(opened, tab)
        self.assertEqual(self.w.active_route, 'home')
        self.assertTrue(tab.dirty)
        self.assertIn('keep local', tab.widget.toPlainText())

    def test_document_does_not_construct_unused_settings_and_short_home_scrolls(self):
        tab = self.open(retained=True)
        self.assertFalse(hasattr(tab.document.host, 'settings_page'))
        from prompt_calculus_studio.deferred_page import page_ready
        host = tab.document.host
        for page in (host.models, host.gallery, host.clean_export):
            self.assertFalse(page_ready(page))
        # Existing callers can assign state before visiting a deferred tool.
        host.gallery.queue = ['pending']
        self.assertEqual(host.gallery.load().queue, ['pending'])
        host.gallery.queue = []
        host.show_page(host.gallery)
        self.events()
        self.assertTrue(host.gallery.isVisible())
        host.return_to_prompt()
        self.assertFalse(tab.dirty)
        page = self.w.new_tab().widget
        self.w.resize(720,480)
        self.events()
        page.ensureWidgetVisible(page.open_button)
        self.events()
        self.assertLessEqual(page.open_button.mapTo(page.viewport(),page.open_button.rect().bottomRight()).y(), page.viewport().height())

    def test_modal_center_is_workspace_center_with_either_sidebar_and_nested_sheet(self):
        from unittest.mock import patch
        from PySide6.QtCore import QPoint, QRect
        from PySide6.QtGui import QScreen
        from prompt_calculus_studio.widgets import StudioDialog
        tab = self.open(retained=True)
        host = tab.document.host
        source = tab.path.read_bytes()
        with patch.object(QScreen, 'availableGeometry', return_value=QRect(0, 0, 2400, 1600)):
            self.w.move(120, 100)
            self.w.resize(1440, 900)
            for position, expanded in (('side', True), ('top', True), ('top', False)):
                self.w.set_navigation_position(position)
                self.w.sidebar_preference = expanded
                self.w.sync_sidebar(); self.events()
                dialog = host.prompt_editor._open_dialog(None)
                self.events()
                expected = self.w.mapToGlobal(self.w.rect().center())
                self.assertIs(dialog.parentWidget(), self.w)
                actual = dialog.mapToGlobal(dialog.panels.main.mapTo(dialog, dialog.panels.main.rect().center()))
                self.assertLessEqual((actual-expected).manhattanLength(), 2)
                nested = StudioDialog(host)
                nested.resize(320, 200); nested.show(); self.events()
                self.assertIs(nested.parentWidget(), dialog)
                nested.reject(); nested.deleteLater(); self.events()
                dialog.reject(); self.events()
        self.assertEqual(tab.path.read_bytes(), source)
        self.assertFalse(tab.dirty)


if __name__ == '__main__':
    unittest.main()
