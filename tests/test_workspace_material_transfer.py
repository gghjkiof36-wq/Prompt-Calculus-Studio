"""Cross-document placements retain originals, writer identity and shared Undo."""
import copy
from pathlib import Path
from unittest.mock import patch
import unittest

from PySide6.QtCore import QPointF, Qt, QMimeData
from PySide6.QtGui import QDrag, QDragEnterEvent, QDragMoveEvent, QDropEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio import materials, multi_output
from prompt_calculus_studio.local_documents import io
from prompt_calculus_studio.workspace_material_menu import open_menu
from prompt_calculus_studio.workspace_material_drag import MaterialCopyDrag, MIME
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
        if self.transfer.drag:
            self.transfer.drag.finish()
            self.transfer.drag = None
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

    def begin_drag(self):
        drag = self.transfer.drag = MaterialCopyDrag(self.transfer, self.page, self.ident)
        mime = QMimeData()
        mime.setData(MIME, drag.token)
        return drag, mime

    def drag_event(self, widget, mime, point, kind=QDragMoveEvent):
        position = QPointF(point) if kind is QDropEvent else point
        event = kind(position, Qt.DropAction.CopyAction, mime,
                     Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(widget, event)
        return event

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
        menu.setActiveAction(child.menuAction())
        QTest.keyClick(menu, Qt.Key.Key_Right)
        self.events()
        self.assertTrue(child.isVisible())
        QTest.keyClick(child, Qt.Key.Key_Home)
        self.assertEqual(child.activeAction().text(), 'Canvas空白處')
        QTest.keyClick(child, Qt.Key.Key_Return)
        self.events()
        self.assertEqual(self.w.active_key, self.source_tab.key)
        self.assertEqual(len(self.w.documents), before+1)

    def test_actual_list_drag_into_other_pane_uses_scene_coordinates_and_no_camera_jump(self):
        tab = self.target_tab()
        self.w.move_view(tab)
        self.w.focus_tab(self.source_tab)
        self.events()
        canvas, view = tab.host.canvas, tab.host.canvas.view
        view.scale(.65, .65)
        view.centerOn(4300, 3300)
        self.events()
        point = view.viewport().rect().center()
        scene_point = view.mapToScene(point)
        transform, center = view.transform(), view.mapToScene(view.viewport().rect().center())
        old_keys = set(tab.host.state['uses'])
        original = copy.deepcopy(self.source_tab.host.state['items'])
        self.page.search.setFocus()
        focus = self.app.focusWidget()
        listing = self.page.listing
        current = next(listing.item(i) for i in range(listing.count())
                       if listing.item(i).data(Qt.ItemDataRole.UserRole) == self.ident)
        listing.setCurrentItem(current)
        def execute(native, *args):
            mime = native.mimeData()
            self.assertTrue(mime.hasFormat(MIME))
            self.assertTrue(self.drag_event(view.viewport(), mime, point, QDragEnterEvent).isAccepted())
            with patch.object(canvas, 'refresh', wraps=canvas.refresh) as refresh:
                for _ in range(12):self.assertTrue(self.drag_event(view.viewport(), mime, point).isAccepted())
                refresh.assert_not_called()
            overlay = self.transfer.drag.overlays[view]
            self.assertEqual(overlay.kind, 'outline')
            self.assertTrue(overlay.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.assertTrue(self.drag_event(view.viewport(), mime, point, QDropEvent).isAccepted())
            self.assertFalse(overlay.isVisible())
            return Qt.DropAction.CopyAction
        with patch.object(QDrag, 'exec', execute):listing.startDrag(Qt.DropAction.CopyAction)
        self.events()
        added = set(tab.host.state['uses'])-old_keys
        self.assertEqual(len(added), 1)
        key = added.pop()
        self.assertIsNone(multi_output.owner(tab.host.state, key))
        self.assertEqual(len(canvas.data()['canvases']), 1)
        actual = QPointF(*tab.host.state['text_positions'][key])
        self.assertLess((actual-scene_point).manhattanLength(), 2)
        self.assertEqual(view.transform(), transform)
        self.assertLess((view.mapToScene(view.viewport().rect().center())-center).manhattanLength(), 2)
        self.assertIs(self.app.focusWidget(), focus)
        self.assertEqual(self.source_tab.host.state['items'], original)
        self.assertIsNone(self.transfer.drag)
        self.assertIsNone(self.transfer.notification)

    def test_drop_over_existing_canvas_text_adds_member_without_moving_or_covering_old_text(self):
        tab = self.target_tab()
        canvas, view = tab.host.canvas, tab.host.canvas.view
        cid = next(iter(canvas.data()['canvases']))
        old_members = list(canvas.data()['canvases'][cid]['members'])
        old_positions = {key:list(tab.host.state['text_positions'][key]) for key in old_members}
        card = canvas.cards[old_members[0]]
        scene_point = card.sceneBoundingRect().center()
        view.centerOn(scene_point)
        point = view.mapFromScene(scene_point)
        drag, mime = self.begin_drag()
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDragEnterEvent).isAccepted())
        self.assertTrue(self.drag_event(view.viewport(), mime, point).isAccepted())
        self.assertEqual(drag.overlays[view].kind, 'plus')
        self.assertEqual(drag.overlays[view].box.width(), 24)
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDropEvent).isAccepted())
        drag.finish(); self.transfer.drag = None
        self.events()
        members = canvas.data()['canvases'][cid]['members']
        self.assertEqual(members[:-1], old_members)
        key = members[-1]
        for member, pos in old_positions.items():
            self.assertEqual(tab.host.state['text_positions'][member], pos)
            self.assertFalse(canvas.cards[key].sceneBoundingRect().intersects(canvas.cards[member].sceneBoundingRect()))
        self.assertEqual(multi_output.owner(tab.host.state, key), cid)

    def test_hover_reveals_only_destination_pane_and_escape_restores_without_writes(self):
        target = self.target_tab()
        self.w.focus_tab(self.source_tab)
        self.events()
        pane = self.w.active_pane
        current = pane.pages.currentWidget()
        original = target.path.read_bytes()
        drag, mime = self.begin_drag()
        bar = pane.strip.bar
        point = bar.tabRect(self.w.index_of(target.key)).center()
        self.assertTrue(self.drag_event(bar, mime, point, QDragEnterEvent).isAccepted())
        QTest.qWait(250)
        self.assertIs(pane.pages.currentWidget(), current)
        QTest.qWait(320)
        self.assertIs(pane.pages.currentWidget(), target.widget)
        self.assertEqual(self.w.active_key, self.source_tab.key)
        QTest.keyClick(bar, Qt.Key.Key_Escape)
        self.assertTrue(drag.cancelled)
        drag.finish(); self.transfer.drag = None
        self.assertIs(pane.pages.currentWidget(), current)
        self.assertEqual(target.path.read_bytes(), original)
        self.assertFalse(target.dirty)
        self.assertIsNone(self.transfer.notification)

    def test_hover_then_drop_retains_target_and_rejects_invalid_node_or_token(self):
        tab = self.target_tab()
        self.w.focus_tab(self.source_tab)
        self.events()
        bar = self.w.active_pane.strip.bar
        drag, mime = self.begin_drag()
        point = bar.tabRect(self.w.index_of(tab.key)).center()
        self.drag_event(bar, mime, point, QDragEnterEvent)
        QTest.qWait(550)
        view = tab.host.canvas.view
        output = next(iter(tab.host.canvas.outputs.values()))
        view.centerOn(output.sceneBoundingRect().center())
        invalid = view.mapFromScene(output.sceneBoundingRect().center())
        self.assertTrue(self.drag_event(view.viewport(), mime, invalid, QDragEnterEvent).isAccepted())
        self.assertFalse(self.drag_event(view.viewport(), mime, invalid).isAccepted())
        self.assertIsNone(self.transfer.notification)
        view.centerOn(4700, 3600)
        point = view.viewport().rect().center()
        # Recover within the SAME gesture; no second DragEnter is sent.
        self.assertTrue(self.drag_event(view.viewport(), mime, point).isAccepted())
        self.assertEqual(drag.overlays[view].kind, 'outline')
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDropEvent).isAccepted())
        drag.finish(); self.transfer.drag = None
        drag, mime = self.begin_drag()
        wrong = QMimeData(); wrong.setData(MIME, b'unknown')
        self.assertFalse(self.drag_event(view.viewport(), wrong, point, QDragEnterEvent).isAccepted())
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDragEnterEvent).isAccepted())
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDropEvent).isAccepted())
        drag.finish(); self.transfer.drag = None
        self.assertIs(self.w.active_pane.pages.currentWidget(), tab.widget)
        self.assertEqual(self.w.active_key, tab.key)
        self.assertTrue(tab.dirty)

    def test_captured_destination_follows_rename_move_but_rejects_save_as(self):
        tab = self.target_tab()
        target = self.transfer.target(tab.path)
        rename = self.w.commands.files.run('rename', self.w.commands.context('tab', tab=tab), name='Renamed')
        self.assertEqual(rename, self.root/'Renamed.pcs-workflow')
        moved = self.w.commands.files.run('move', self.w.commands.context('tab', tab=tab), directory=self.root/'Folder')
        self.assertEqual(moved, self.root/'Folder/Renamed.pcs-workflow')
        key = self.transfer.add(self.capture(), target)
        self.assertTrue(key)
        self.assertTrue(self.w.save_tab(tab))
        before = moved.read_bytes()
        stale = self.transfer.target(tab.path)
        copied = self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=tab), name='Copied', directory=self.root)
        self.assertEqual(copied, self.root/'Copied.pcs-workflow')
        self.assertFalse(self.transfer.add(self.capture(), stale))
        self.assertEqual(moved.read_bytes(), before)

    def test_initial_functional_node_entry_recovers_into_canvas_in_same_drag(self):
        tab = self.target_tab()
        canvas, view = tab.host.canvas, tab.host.canvas.view
        cid = next(iter(canvas.data()['canvases']))
        old_members = list(canvas.data()['canvases'][cid]['members'])
        output = next(iter(canvas.outputs.values()))
        original = copy.deepcopy(self.source_tab.host.state)
        drag, mime = self.begin_drag()
        view.centerOn(output.sceneBoundingRect().center()); self.events()
        point = view.mapFromScene(output.sceneBoundingRect().center())
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDragEnterEvent).isAccepted())
        self.assertFalse(self.drag_event(view.viewport(), mime, point).isAccepted())
        target = canvas.cards[old_members[0]].sceneBoundingRect().center()
        view.centerOn(target); self.events(); point = view.mapFromScene(target)
        self.assertTrue(self.drag_event(view.viewport(), mime, point).isAccepted())
        self.assertTrue(self.drag_event(view.viewport(), mime, point, QDropEvent).isAccepted())
        drag.finish(); self.transfer.drag = None; self.events()
        members = canvas.data()['canvases'][cid]['members']
        self.assertEqual(members[:-1], old_members)
        self.assertEqual(self.source_tab.host.state, original)
        canvas.undo(); self.events()
        self.assertEqual(canvas.data()['canvases'][cid]['members'], old_members)

    def test_real_unconfirmed_edit_is_preserved_then_confirmed_value_can_autosave(self):
        ident, dialog = fixture.WorkspaceShellTests.editor(self, self.source_tab)
        QTest.keyClicks(dialog.prompt, ' unconfirmed')
        original = self.source_tab.path.read_bytes()
        with self.assertRaises(ValueError):self.capture()
        self.assertTrue(dialog.isVisible())
        self.assertEqual(self.source_tab.path.read_bytes(), original)
        QTest.mouseClick(dialog.save_button, Qt.MouseButton.LeftButton)
        self.events()
        confirmed = materials.item(self.source_tab.host.state, ident)['prompt']
        target = self.transfer.target(self.root/'Street.pcs-workflow')
        key = self.transfer.add(self.capture(), target)
        tab = next(t for t in self.w.all_pages() if t.path == target.path)
        self.assertEqual(tab.host.state['uses'][key]['prompt'], confirmed)
        self.w.autosave.flush(tab)
        self.assertEqual(io.load(self.root, target.path.name).document['payload']['state']['uses'][key]['prompt'], confirmed)

    def test_variants_settings_required_assets_and_use_copy_survive_reopen_independently(self):
        from prompt_calculus_studio import prompt_materials as pm, prompt_groups, composition
        root = pm.new('text', 'All variants')
        root['variants'] = pm.variants(root)
        root['variants']['groups'][0]['text'] = 'first'
        second = prompt_groups.new_group('second', 'second content', content_kind='text')
        second['repeat'] = 3
        root['variants']['groups'].append(second)
        root['variants'].update(mode='increment', selected=second['id'], random_repeats=False)
        root.update(weight=15, excludes=['excluded'], disabled_tags=['disabled'], preview='previews/needed.png')
        image = self.source_tab.document.data/'previews/needed.png'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b'needed-preview')
        (image.parent/'unrelated.png').write_bytes(b'unrelated-private')
        self.assertTrue(self.source_tab.host.canvas.commit(
            lambda s: materials.item(s, self.ident).update(composition=root)))
        source = self.capture()
        before = copy.deepcopy(source.document.state['items'])
        source_bytes = self.source_tab.path.read_bytes()
        target = self.transfer.target(self.root/'Street.pcs-workflow')
        key = self.transfer.add(source, target)
        self.assertTrue(key)
        tab = next(t for t in self.w.all_pages() if t.path == target.path)
        placed = tab.host.state['uses'][key]
        expected = composition.clone_node(root)
        pm.reset(expected)
        self.assertEqual(placed['variants'], expected['variants'])
        self.assertEqual(placed['weight'], 15)
        self.assertEqual(placed['excludes'], ['excluded'])
        self.assertEqual(placed['disabled_tags'], ['disabled'])
        self.assertNotEqual(placed['id'], root['id'])
        self.w.autosave.flush(tab)
        saved = io.load(self.root, target.path.name).document
        self.assertEqual(len(saved['assets']), 1)
        self.assertEqual(saved['assets'][0]['reference'], placed['preview'])
        self.assertNotIn(str(self.source_tab.document.data), target.path.read_text(encoding='utf-8'))
        self.assertNotIn(b'unrelated-private', target.path.read_bytes())
        self.w.close_tab(self.w.index_of(tab.key))
        tab = self.w.open_path(target.path, True, new_tab=True)
        self.events()
        reopened = tab.host.state['uses'][key]
        self.assertEqual(reopened['variants'], expected['variants'])
        self.assertEqual((tab.document.data/reopened['preview']).read_bytes(), b'needed-preview')
        self.assertTrue(tab.host.canvas.commit(
            lambda s:s['uses'][key]['variants']['groups'][0].update(text='copy-only edit')))
        self.assertEqual(source.document.state['items'], before)
        self.assertEqual(self.source_tab.path.read_bytes(), source_bytes)

    def test_menu_deduplicates_views_distinguishes_names_and_empty_canvas_needs_no_container(self):
        from prompt_calculus_studio.local_documents import workflow
        tab = self.target_tab()
        self.w.duplicate_view(tab)
        self.events()
        self.assertEqual(self.transfer.candidates().count(tab.path), 1)
        other = self.root/'Folder/Street.pcs-workflow'
        self.assertEqual(io.create(other.parent, other.name, workflow.from_state(tab.host.state)).status, 'saved')
        cid = next(iter(tab.host.canvas.data()['canvases']))
        tab.host.canvas.remove_container(cid)
        self.events()
        menu = open_menu(self.page, self.ident, self.page.mapToGlobal(self.page.rect().center()))
        self.events()
        entries = [a for a in menu.actions() if a.data() in (str(tab.path), str(other))]
        self.assertEqual(len(entries), 2)
        self.assertNotEqual(entries[0].text(), entries[1].text())
        child = next(a.menu() for a in entries if a.data() == str(tab.path))
        child.aboutToShow.emit()
        self.assertEqual([a.text() for a in child.actions()], ['Canvas空白處'])
        before = len(tab.host.state['uses'])
        child.actions()[0].trigger()
        self.events()
        self.assertEqual(len(tab.host.state['uses']), before+1)
        self.assertEqual(tab.host.canvas.data()['canvases'], {})
        self.assertEqual(io.load(other.parent, other.name).document['payload']['state']['uses'].keys(),
                         (tab.host.state['uses'].keys()-{tab.host.state['output_order'][-1]}))

    def test_deleted_canvas_and_commit_failure_are_atomic_and_notification_is_single_nonmodal(self):
        tab = self.target_tab()
        cid = next(iter(tab.host.canvas.data()['canvases']))
        target = self.transfer.target(tab.path)
        tab.host.canvas.remove_container(cid)
        self.events()
        before = copy.deepcopy(tab.host.state['uses'])
        self.page.search.setFocus()
        focus = self.app.focusWidget()
        self.assertFalse(self.transfer.add(self.capture(), target, cid))
        notice = self.transfer.notification
        self.assertTrue(notice.isVisible())
        self.assertLessEqual(notice.width(), 352)
        self.assertGreaterEqual(notice.x(), self.w.workspace_stack.width()-notice.width()-24)
        self.assertEqual(notice.expire.interval(), 6000)
        self.assertIs(self.app.focusWidget(), focus)
        image = self.source_tab.document.data/'previews/rollback.png'
        image.parent.mkdir(parents=True, exist_ok=True)
        image.write_bytes(b'rollback-preview')
        materials.item(self.source_tab.host.state, self.ident)['preview'] = 'previews/rollback.png'
        with patch.object(tab.host.canvas, 'commit', return_value=False):
            self.assertFalse(self.transfer.add(self.capture(), target))
        self.assertEqual(tab.host.state['uses'], before)
        self.assertEqual(list((tab.document.data/'transfers').glob('*')), [])
        self.transfer.error('another failure')
        self.assertIs(self.transfer.notification, notice)
        QTest.mouseClick(notice.close_button, Qt.MouseButton.LeftButton)
        self.assertFalse(notice.isVisible())

    def test_background_target_avoids_nontext_nodes_before_its_first_show(self):
        target = self.transfer.target(self.root/'Street.pcs-workflow')
        tab = self.w.open_path(target.path, True, new_tab=True, background=True)
        canvas = tab.host.canvas
        self.assertFalse(canvas.isVisible())
        output_key = next(iter(canvas.data()['outputs']))
        point = QPointF(*tab.host.state.get('text_positions', {}).get(output_key, [0, -350]))
        active = self.w.active_key
        key = self.transfer.add(self.capture(), target, point=point)
        self.assertTrue(key)
        self.assertEqual(self.w.active_key, active)
        self.w.focus_tab(tab)
        self.events()
        self.assertFalse(canvas.cards[key].sceneBoundingRect().intersects(canvas.outputs[output_key].sceneBoundingRect()))

    def test_many_file_choices_scroll_and_remain_keyboard_reachable_without_opening_hosts(self):
        paths = [self.root/f'Canvas-{i:02}.pcs-workflow' for i in range(64)]
        before = len(self.w.documents)
        with patch.object(self.transfer, 'candidates', return_value=paths):
            menu = open_menu(self.page, self.ident, self.w.mapToGlobal(self.w.rect().topLeft()))
            self.events()
            for _ in range(65):QTest.keyClick(menu, Qt.Key.Key_Down)
            self.assertEqual(menu.activeAction().text(), '選擇其他Canvas…')
            self.assertTrue(menu.rect().contains(menu.actionGeometry(menu.activeAction())))
            for _ in range(64):QTest.keyClick(menu, Qt.Key.Key_Up)
            self.assertEqual(menu.activeAction().data(), str(paths[0]))
            self.assertTrue(menu.rect().contains(menu.actionGeometry(menu.activeAction())))
            self.assertEqual(len(self.w.documents), before)
            menu.close()


if __name__ == '__main__':unittest.main()
