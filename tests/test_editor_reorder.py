"""Library order transactions: real Qt drag events, isolated local storage."""
import copy
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QPointF, QCoreApplication, QEvent
from PySide6.QtGui import QDragEnterEvent, QDragMoveEvent, QDropEvent, QMouseEvent, QTextCursor
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio import materials
from prompt_calculus_studio.core import DEFAULT_SETTINGS, build_prompt, output_groups
from prompt_calculus_studio.extension_page import ExtensionPage
from prompt_calculus_studio.prompt_editor import MaterialEditorForm, MaterialList
from prompt_calculus_studio.window import Window


class EditorReorderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.patches = []
        self.errors = []
        def use(context):
            self.patches.append(context); return context.start()
        use(patch.dict(os.environ, PROMPT_STUDIO_V08='1', PROMPT_STUDIO_V081='1', PROMPT_STUDIO_DATA=self.folder.name))
        use(patch.dict(DEFAULT_SETTINGS, online=False, comfy_enabled=False, material='solid'))
        use(patch.object(ExtensionPage, 'startup'))
        use(patch.object(sys, 'excepthook', lambda *error: self.errors.append(error)))
        use(patch('prompt_calculus_studio.comfy_client.ComfyClient.request', side_effect=AssertionError('No service calls')))
        use(patch('prompt_calculus_studio.extension_page.ExtensionPage.check_now', side_effect=AssertionError('No installation probes')))
        for method in ('get', 'post', 'put', 'sendCustomRequest'):
            use(patch.object(QNetworkAccessManager, method, side_effect=AssertionError('No network')))
        self.w = Window(self.folder.name); self.w.resize(1440, 900); self.w.show(); self.w.enter_canvas()
        self.app.processEvents()
        self.cat = materials.add_category(self.w.state, 'Order A')
        self.other_cat = materials.add_category(self.w.state, 'Order B')
        self.ids = []
        for name, category, used in [('Bravo', self.cat, 2), ('Hidden one', self.other_cat, 0),
                                     ('Alpha', self.cat, 1), ('Hidden two', self.other_cat, 0),
                                     ('Charlie', self.cat, 3)]:
            self.ids.append(materials.save(self.w.state, dict(name=name, prompt='shared '+name,
                module=category, last_used=used, future={'preserve': name})))
        self.w.leave_canvas(); self.page = self.w.prompt_editor
        self.page.set_browser_category(self.cat); self.app.processEvents()

    def tearDown(self):
        if self.page.active_dialog: self.page.active_dialog.reject()
        for saved in getattr(self.w, '_material_editor_session', {}).get('drafts', {}).values(): saved['dirty'] = False
        self.w.completion.timer.stop()
        with patch('prompt_calculus_studio.prompt_editor.ask', return_value=True): self.w.close()
        self.w.deleteLater(); self.app.processEvents()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        for context in reversed(self.patches): context.stop()
        self.folder.cleanup()
        self.assertEqual(self.errors, [], 'No Qt callback exceptions')

    def drag(self, listing, source_id, target_id, after=False):
        """Only replace the native OS drag loop; deliver real Qt viewport events."""
        source = next(listing.item(i) for i in range(listing.count()) if listing.item(i).data(Qt.ItemDataRole.UserRole) == source_id)
        target = next(listing.item(i) for i in range(listing.count()) if listing.item(i).data(Qt.ItemDataRole.UserRole) == target_id)
        listing.scrollToItem(source); self.app.processEvents()
        start = listing.visualItemRect(source).center()
        received = []
        def execute(drag, actions, default):
            listing.scrollToItem(target); self.app.processEvents()
            rect = listing.visualItemRect(target)
            position = QPoint(rect.center().x(), rect.bottom()-1 if after else rect.top()+1)
            mime = drag.mimeData()
            class Enter(QDragEnterEvent):
                def source(self): return listing
            class Move(QDragMoveEvent):
                def source(self): return listing
            class Drop(QDropEvent):
                def source(self): return listing
            enter = Enter(position, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(listing.viewport(), enter)
            move = Move(position, actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(listing.viewport(), move)
            self.assertTrue(enter.isAccepted()); self.assertTrue(move.isAccepted())
            self.assertIsNotNone(listing._drop_row)
            drop = Drop(QPointF(position), actions, mime, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(listing.viewport(), drop)
            received.append((actions, default, bytes(mime.data(materials.MIME_TYPE)), drop.isAccepted()))
            return drop.dropAction()
        with patch('prompt_calculus_studio.prompt_editor.QDrag.exec', execute):
            QTest.mousePress(listing.viewport(), Qt.MouseButton.LeftButton, pos=start)
            position = start + QPoint(0, self.app.startDragDistance()+8)
            move = QMouseEvent(QEvent.Type.MouseMove, QPointF(position), QPointF(listing.viewport().mapToGlobal(position)),
                               Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
            QCoreApplication.sendEvent(listing.viewport(), move)
            QTest.mouseRelease(listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
        self.assertEqual(len(received), 1, 'The mouse gesture must reach QDrag.startDrag')
        self.assertTrue(received[0][3]); self.app.processEvents()
        return received[0]

    def assert_slots(self, field, previous, visible, ordered):
        expected = iter(ordered)
        self.assertEqual([value['id'] for value in self.w.state[field]],
                         [next(expected) if value['id'] in visible else value['id'] for value in previous])
        self.assertEqual({value['id']: value for value in self.w.state[field]},
                         {value['id']: value for value in previous})

    def test_name_drag_becomes_manual_persists_and_preserves_hidden_records(self):
        previous = copy.deepcopy(self.w.state['items'])
        before = self.page.listing.order_ids(); self.assertEqual(before, [self.ids[2], self.ids[0], self.ids[4]])
        actions, default, mime, accepted = self.drag(self.page.listing, self.ids[4], self.ids[2])
        ordered = [self.ids[4], self.ids[2], self.ids[0]]
        self.assertEqual(self.page.listing.order_ids(), ordered); self.assertEqual(self.page.order.currentData(), 'manual')
        self.assert_slots('items', previous, set(before), ordered)
        self.assertEqual(default, Qt.DropAction.CopyAction); self.assertTrue(actions & Qt.DropAction.MoveAction)
        self.assertEqual(mime.decode(), self.ids[4]); self.assertEqual(self.page.selected_id(), self.ids[4])
        self.w.persist()
        loaded = self.w.store.load()
        self.assertEqual(loaded['settings']['material_library_order'], 'manual')
        self.assertEqual([v['id'] for v in loaded['items']], [v['id'] for v in self.w.state['items']])
        self.page.refresh(); self.assertEqual(self.page.listing.order_ids(), ordered)

    def test_recent_search_and_pin_filters_reorder_only_visible_slots(self):
        for ident in (self.ids[0], self.ids[4]): materials.item(self.w.state, ident)['pinned'] = True
        self.page.order.setCurrentIndex(self.page.order.findData('recent'))
        self.page.search.setText('shared'); self.page.pins.setChecked(True)
        before = self.page.listing.order_ids(); self.assertEqual(before, [self.ids[4], self.ids[0]])
        previous = copy.deepcopy(self.w.state['items'])
        self.drag(self.page.listing, before[1], before[0])
        self.assert_slots('items', previous, set(before), list(reversed(before)))
        self.assertEqual(self.page.order.currentData(), 'manual')
        self.assertEqual(self.page.search.text(), 'shared'); self.assertTrue(self.page.pins.isChecked())

    def test_categories_keep_shortcuts_fixed_and_preserve_canvas_prompt_with_undo(self):
        canvas = next(iter(self.w.state['multi_output']['canvases']))
        self.w.canvas.commit(lambda s: [materials.add_to_canvas(s, ident, canvas) for ident in (self.ids[0], self.ids[4])])
        previous = copy.deepcopy(self.w.state['modules'])
        protected = copy.deepcopy({key: self.w.state.get(key) for key in ('uses','output_order','selections','draft','draft_base','multi_output')})
        prompt = build_prompt(self.w.state)
        self.drag(self.page.categories, self.other_cat, None)
        order = [self.other_cat]+[v['id'] for v in previous if v['id'] != self.other_cat]
        self.assertEqual(self.page.categories.order_ids()[0], None)
        self.assertFalse(self.page.categories.item(0).flags() & Qt.ItemFlag.ItemIsDragEnabled)
        self.assertEqual([v['id'] for v in self.w.state['modules']], order)
        self.assertEqual([self.page.category.itemData(i) for i in range(1, self.page.category.count()) if self.page.category.itemData(i) != '@uncategorized'], order)
        self.assertEqual({key: self.w.state.get(key) for key in protected}, protected)
        self.assertEqual(build_prompt(self.w.state), prompt)
        self.w.canvas.undo(); self.app.processEvents()
        self.assertEqual(self.w.state['modules'], previous)
        self.assertEqual([i for i in self.page.categories.order_ids() if i not in (None,'@uncategorized')], [v['id'] for v in previous])
        self.w.canvas.redo(); self.app.processEvents()
        self.assertEqual([v['id'] for v in self.w.state['modules']], order)

    def test_failed_commit_and_foreign_drop_leave_state_and_sort_mode_unchanged(self):
        # Window geometry is saved asynchronously and is not a material edit.
        fields = ('items', 'modules', 'uses', 'settings', 'output_order', 'selections', 'draft', 'draft_base', 'content_history')
        project = lambda: {key: self.w.state.get(key) for key in fields}
        state = copy.deepcopy(project()); before = self.page.listing.order_ids()
        with patch.object(self.w.canvas, 'commit', return_value=False): self.drag(self.page.listing, before[-1], before[0])
        self.assertEqual(project(), state); self.assertEqual(self.page.listing.order_ids(), before)
        self.assertEqual(self.page.order.currentData(), 'name')
        foreign = MaterialList(); foreign.addItem('foreign'); foreign.item(0).setData(Qt.ItemDataRole.UserRole, before[0])
        event = QDragEnterEvent(QPoint(20,20), Qt.DropAction.CopyAction, foreign.drag_mime(foreign.item(0)),
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QCoreApplication.sendEvent(self.page.listing.viewport(), event)
        self.assertFalse(event.isAccepted()); self.assertEqual(project(), state); foreign.deleteLater()

    def test_category_reorder_freezes_missing_legacy_output_order(self):
        self.w.state.pop('output_order', None)
        self.w.state['selections'][self.cat] = [self.ids[0]]
        self.w.state['selections'][self.other_cat] = [self.ids[1]]
        expected = output_groups(self.w.state, include_hidden=True)
        self.drag(self.page.categories, self.other_cat, None)
        self.assertEqual(output_groups(self.w.state, include_hidden=True), expected)
        self.assertEqual(self.w.state['output_order'], expected)

    def test_reorder_keeps_native_draft_document_and_f2_target(self):
        ident = self.ids[0]
        prior = MaterialEditorForm(self.w, compact=True); prior.open_material(ident)
        document = prior.prompt.document(); original = prior.prompt.toPlainText()
        prior.prompt.moveCursor(QTextCursor.MoveOperation.End); prior.prompt.insertPlainText(' draft')
        cursor = prior.prompt.textCursor(); cursor.setPosition(1); cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
        prior.prompt.setTextCursor(cursor); prior._publish(force=True)
        session = copy.deepcopy(self.w._material_editor_session)
        try:
            self.drag(self.page.listing, ident, self.ids[4], after=True)
            self.assertIs(prior.prompt.document(), document); self.assertEqual(self.w._material_editor_session, session)
            self.assertEqual((prior.prompt.textCursor().anchor(), prior.prompt.textCursor().position()), (1,4))
            prior.prompt.undo(); self.assertEqual(prior.prompt.toPlainText(), original)
            prior.prompt.redo(); self.assertEqual(prior.prompt.toPlainText(), original+' draft')
            self.page.listing.setFocus(); QTest.keyClick(self.page.listing, Qt.Key.Key_F2); self.app.processEvents()
            self.assertIsNotNone(self.page.active_dialog); self.assertEqual(self.page.active_dialog.target_id, ident)
            self.assertEqual(self.page.active_dialog.prompt.toPlainText(), original+' draft')
            self.page.active_dialog.reject()
        finally:
            prior.dirty = False; prior.close(); prior.deleteLater()


if __name__ == '__main__': unittest.main()
