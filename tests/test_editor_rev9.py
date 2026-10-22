"""Rev9 modal editing boundaries, real Qt events, isolated in-memory project."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QCoreApplication, QEvent, QPoint, QObject, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QWidget, QLineEdit, QDialogButtonBox, QApplication, QPushButton
from PySide6.QtTest import QTest
import test_material_editor as fixture_module
from prompt_calculus_studio.prompt_editor import PromptEditor
from prompt_calculus_studio import materials
from prompt_calculus_studio.settings_page import SETTINGS_READING_WIDTH


class WindowShowTrace(QObject):
    def __init__(self, app):
        super().__init__(app)
        self.shows = []
        app.installEventFilter(self)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Show and isinstance(watched, QWidget) and watched.isWindow():
            self.shows.append((type(watched).__name__, watched.windowTitle(),
                               watched.text() if hasattr(watched, 'text') else ''))
        return False


class EditorRev9Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): fixture_module.MaterialEditorTests.setUpClass(); cls.app = fixture_module.MaterialEditorTests.app

    def setUp(self):
        self.fixture = fixture_module.MaterialEditorTests(); self.fixture.setUp()
        self.w = self.fixture.w; self.w.resize(1440, 900)
        self.w.enter_canvas = lambda: None; self.w.leave_canvas = lambda: None
        self.page = PromptEditor(self.w); self.page.resize(1440, 900); self.page.show()
        self.app.processEvents(); self.app.processEvents()
        self.ident = self.w.state['items'][0]['id']

    def tearDown(self):
        if self.page.active_dialog: self.page.active_dialog.reject()
        self.page.close(); self.page.deleteLater(); self.fixture.tearDown()

    def edit(self):
        dialog = self.page.edit_material(self.ident); self.app.processEvents(); return dialog

    def test_category_plus_menu_opens_creation_and_cancel_preserves_state(self):
        for width in (1440, 640):
            self.page.resize(width, 800); self.app.processEvents(); QTest.qWait(35)
            self.assertTrue(self.page.category_add.isVisible())
            if not self.page.narrow:
                self.assertGreater(self.page.category_add.x(), self.page.category_heading.geometry().right())
            self.assertFalse([button for button in self.page.category_panel.findChildren(QPushButton)
                              if button.text() == '管理分類'])
            for accepted in (False, True):
                before = copy.deepcopy(self.w.state); seen = []; errors = []
                name = 'Created category '+str(width)
                def operate():
                    dialog = QApplication.activeModalWidget()
                    try:
                        self.assertIsNotNone(dialog); self.assertEqual(dialog.windowTitle(), '新增分類')
                        editor = dialog.findChild(QLineEdit)
                        self.assertIsNotNone(editor); QTest.keyClicks(editor, name)
                        buttons = dialog.findChild(QDialogButtonBox)
                        action = buttons.button(QDialogButtonBox.StandardButton.Save if accepted else QDialogButtonBox.StandardButton.Cancel)
                        seen.append(dialog.windowTitle()); QTest.mouseClick(action, Qt.MouseButton.LeftButton)
                    except BaseException as exc:
                        errors.append(exc)
                        if dialog is not None: dialog.reject()
                if accepted:
                    self.page.category_add.setFocus(); QTest.keyClick(self.page.category_add, Qt.Key.Key_Space)
                else: QTest.mouseClick(self.page.category_add, Qt.MouseButton.LeftButton)
                menu = QApplication.activePopupWidget(); self.assertIsNotNone(menu)
                category_action = next(v for v in menu.actions() if v.text() == '新增分類')
                QTimer.singleShot(40, operate)
                QTest.mouseClick(menu, Qt.MouseButton.LeftButton, pos=menu.actionGeometry(category_action).center())
                self.app.processEvents(); self.assertFalse(errors, errors); self.assertEqual(seen, ['新增分類'])
                if accepted:
                    added = [value for value in self.w.state['modules'] if value['name'] == name]
                    self.assertEqual(len(added), 1); self.assertEqual(self.page.get_browser_category(), added[0]['id'])
                    self.assertEqual(self.w.state['items'], before['items'])
                    self.assertEqual(self.w.state['modules'][:-1], before['modules'])
                else: self.assertEqual(self.w.state, before)

    def test_new_button_mouse_and_keyboard_open_one_persistent_dialog(self):
        before = copy.deepcopy(self.w.state)
        trace = WindowShowTrace(self.app)
        self.addCleanup(self.app.removeEventFilter, trace)
        for keyboard in (False, True):
            with self.subTest(keyboard=keyboard):
                if keyboard:
                    self.page.new_button.setFocus(Qt.FocusReason.TabFocusReason)
                    QTest.keyClick(self.page.new_button, Qt.Key.Key_Space)
                else:
                    QTest.mouseClick(self.page.new_button, Qt.MouseButton.LeftButton)
                dialog = self.page.active_dialog
                self.assertIsNotNone(dialog)
                for delay in (30, 170, self.app.doubleClickInterval()+60):
                    QTest.qWait(delay)
                    self.assertIs(self.page.active_dialog, dialog)
                    self.assertTrue(dialog.isVisible())
                    self.assertIsNone(dialog.target_id)
                QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
                self.app.processEvents()
                self.assertIsNone(self.page.active_dialog)
                self.assertEqual(self.w.state, before)
        self.assertFalse([entry for entry in trace.shows if entry[0] == 'QPushButton'], trace.shows)

    def test_complete_double_click_keeps_editor_visible_after_second_release(self):
        from prompt_calculus_studio.material_editor_browser import MaterialEditDialog
        before = copy.deepcopy(self.w.state)
        trace = WindowShowTrace(self.app)
        self.addCleanup(self.app.removeEventFilter, trace)
        for width, preview in ((1440, False), (1360, True), (1024, False), (640, True)):
            with self.subTest(width=width, preview=preview):
                self.page.resize(width, 900)
                self.page.set_browser_category(None)
                if preview: self.page.preview_material(self.ident)
                else: self.page.close_preview()
                self.app.processEvents(); QTest.qWait(40)
                entry = next(self.page.listing.item(i) for i in range(self.page.listing.count())
                             if self.page.listing.item(i).data(Qt.ItemDataRole.UserRole) == self.ident)
                self.page.listing.scrollToItem(entry)
                self.app.processEvents()
                position = self.page.listing.visualItemRect(entry).center()
                QTest.mouseClick(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
                QTest.qWait(35)
                self.assertIsNone(self.page.active_dialog)
                QTest.mouseDClick(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
                QTest.mouseRelease(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
                dialog = self.page.active_dialog
                self.assertIsNotNone(dialog)
                for delay in (30, 170, self.app.doubleClickInterval()+60):
                    QTest.qWait(delay)
                    self.assertIs(self.page.active_dialog, dialog)
                    self.assertTrue(dialog.isVisible())
                    self.assertEqual(dialog.target_id, self.ident)
                    visible = [v for v in self.app.topLevelWidgets()
                               if isinstance(v, MaterialEditDialog) and v.isVisible()]
                    self.assertEqual(visible, [dialog])
                dialog.reject(); self.app.processEvents()
                self.assertEqual(self.w.state, before)
        self.assertFalse([entry for entry in trace.shows if entry[0] == 'QPushButton'], trace.shows)

    def test_mouse_preview_is_readonly_then_f2_opens_dialog(self):
        self.page.set_browser_category(self.w.state['items'][0]['module'])
        entry = next(self.page.listing.item(i) for i in range(self.page.listing.count()) if self.page.listing.item(i).data(Qt.ItemDataRole.UserRole) == self.ident)
        before = copy.deepcopy(self.w.state)
        self.app.processEvents(); QTest.qWait(30)
        QTest.mouseClick(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=self.page.listing.visualItemRect(entry).center())
        self.assertIsNone(self.page.active_dialog)
        QTest.qWait(self.app.doubleClickInterval()+60)
        self.assertEqual(self.page.preview_id, self.ident); self.assertTrue(self.page.preview_text.isReadOnly())
        self.assertEqual(self.w.state, before)
        self.page.listing.setFocus(); QTest.keyClick(self.page.listing, Qt.Key.Key_F2)
        self.assertEqual(self.page.active_dialog.target_id, self.ident)

    def test_list_uses_remaining_height_without_stretching_rows(self):
        materials.save(self.w.state, dict(name='Eighth material', prompt='eight', module=None))
        self.page.refresh(); self.page.set_browser_category(None)
        for width, height in ((2048, 1152), (1440, 1000), (1024, 1000), (640, 900)):
            for preview in (False, True):
                with self.subTest(width=width, height=height, preview=preview):
                    self.page.resize(width, height)
                    if preview: self.page.preview_material(self.ident)
                    else: self.page.close_preview()
                    self.app.processEvents(); QTest.qWait(40)
                    viewport = self.page.scroll.viewport()
                    bottom = self.page.listing.mapTo(viewport, QPoint(0, self.page.listing.height())).y()
                    self.assertAlmostEqual(viewport.height()-bottom, 24, delta=2)
                    self.assertEqual(self.page.listing.verticalScrollBar().maximum(), 0)
                    last = self.page.listing.item(self.page.listing.count()-1)
                    self.assertTrue(self.page.listing.viewport().rect().contains(self.page.listing.visualItemRect(last)))
                    first_height = self.page.listing.visualItemRect(self.page.listing.item(0)).height()
                    self.assertLess(first_height, 110)

    def test_overflow_stays_inside_list_and_last_row_remains_reachable(self):
        for i in range(30):
            materials.save(self.w.state, dict(name=f'Overflow {i:02}', prompt='more', module=None))
        self.page.refresh(); self.page.set_browser_category(None)
        self.page.resize(1024, 640); self.page.preview_material(self.ident)
        self.app.processEvents(); QTest.qWait(40)
        self.assertGreater(self.page.listing.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.page.scroll.verticalScrollBar().maximum(), 0)
        self.page.listing.scrollToBottom(); self.app.processEvents()
        last = self.page.listing.item(self.page.listing.count()-1)
        self.assertTrue(self.page.listing.viewport().rect().contains(self.page.listing.visualItemRect(last)))

    def test_bottom_stack_hit_targets_reduced_motion_and_scroll_interrupt(self):
        from PySide6.QtGui import QWheelEvent
        from PySide6.QtCore import QPointF, QAbstractAnimation
        for i in range(30):
            materials.save(self.w.state, dict(name=f'Motion {i:02}', prompt='sample', module=None))
        self.page.refresh(); self.page.resize(1024, 640); self.page.preview_material(self.ident)
        self.w.state['settings']['reduce_motion'] = False
        listing = self.page.listing; bar = listing.verticalScrollBar()
        self.app.processEvents(); QTest.qWait(40)
        start, layers = listing._stack()
        targets = {}
        for y in range(start, listing.viewport().height()):
            point = QPoint(listing.viewport().width()//2, y)
            index = listing.indexAt(point)
            if index.isValid(): targets.setdefault(index.row(), point)
        self.assertGreaterEqual(len(targets), 2)
        row, point = list(targets.items())[-1]
        ident = listing.item(row).data(Qt.ItemDataRole.UserRole)
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.page.selected_id(), ident)
        self.assertEqual(self.page.preview_id, ident)
        QTest.mouseDClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=point)
        QTest.mouseRelease(listing.viewport(), Qt.MouseButton.LeftButton, pos=point)
        self.assertEqual(self.page.active_dialog.target_id, ident)
        self.page.active_dialog.reject(); self.app.processEvents()

        point = QPointF(80, 80)
        def wheel(delta=-120):
            event = QWheelEvent(point, point, QPoint(), QPoint(0, delta),
                                Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
                                Qt.ScrollPhase.NoScrollPhase, False)
            QApplication.sendEvent(listing.viewport(), event)
        before = bar.value(); wheel()
        self.assertEqual(listing.scroll_motion.state(), QAbstractAnimation.State.Running)
        QTest.qWait(70); self.assertGreater(bar.value(), before)
        first_target = listing.scroll_motion.endValue(); wheel()
        self.assertGreater(listing.scroll_motion.endValue(), first_target)
        # Reverse from the visible position, not an outstanding far target.
        current = bar.value(); wheel(120)
        self.assertLess(listing.scroll_motion.endValue(),current)
        QTest.keyClick(listing, Qt.Key.Key_Home)
        self.assertEqual(listing.scroll_motion.state(), QAbstractAnimation.State.Stopped)
        self.w.state['settings']['reduce_motion'] = True
        self.assertIsNone(listing._stack())
        before = bar.value(); wheel()
        self.assertEqual(listing.scroll_motion.state(), QAbstractAnimation.State.Stopped)
        self.assertGreater(bar.value(), before)

    def test_switch_open_preview_updates_on_click_and_preserves_double_click_target(self):
        before = copy.deepcopy(self.w.state)
        for width in (1440, 1024, 640):
            self.page.resize(width, 900); self.page.set_browser_category(None)
            first, second = self.page.listing.item(0), self.page.listing.item(1)
            self.page.preview_material(first.data(Qt.ItemDataRole.UserRole))
            self.app.processEvents(); QTest.qWait(35)
            self.page.listing.scrollToItem(second); self.app.processEvents()
            position = self.page.listing.visualItemRect(second).center()
            ident = second.data(Qt.ItemDataRole.UserRole)
            QTest.mouseClick(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
            self.assertEqual(self.page.preview_id, ident)
            self.assertEqual(self.page.preview_text.toPlainText(), materials.item(self.w.state, ident)['prompt'])
            self.app.processEvents()
            self.assertIs(self.page.listing.itemAt(position), second)
            QTest.mouseDClick(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
            QTest.mouseRelease(self.page.listing.viewport(), Qt.MouseButton.LeftButton, pos=position)
            self.assertEqual(self.page.active_dialog.target_id, ident)
            self.page.active_dialog.reject(); self.app.processEvents()
        self.assertEqual(self.w.state, before)

    def test_cancel_escape_keeps_existing_draft_document_undo_selection(self):
        prior = self.fixture.editor; prior.open_material(self.ident)
        document = prior.prompt.document(); original = prior.prompt.toPlainText()
        prior.prompt.moveCursor(QTextCursor.MoveOperation.End); prior.prompt.insertPlainText(' prior draft')
        cursor = prior.prompt.textCursor(); cursor.setPosition(2); cursor.setPosition(6, QTextCursor.MoveMode.KeepAnchor); prior.prompt.setTextCursor(cursor)
        prior._publish(force=True)
        saved = copy.deepcopy(self.w._material_editor_session)
        dialog = self.edit(); self.assertEqual(dialog.prompt.toPlainText(), original+' prior draft')
        dialog.prompt.moveCursor(QTextCursor.MoveOperation.End); QTest.keyClicks(dialog.prompt, ' cancel me')
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Escape); self.app.processEvents()
        self.assertEqual(self.w._material_editor_session, saved)
        self.assertIs(prior.prompt.document(), document)
        self.assertEqual((prior.prompt.textCursor().anchor(), prior.prompt.textCursor().position()), (2,6))
        again = self.edit(); self.assertEqual(again.prompt.toPlainText(), original+' prior draft')
        self.assertEqual((again.prompt.textCursor().anchor(), again.prompt.textCursor().position()), (2,6))
        again.reject()
        prior.prompt.undo(); self.assertEqual(prior.prompt.toPlainText(), original)
        prior.prompt.redo(); self.assertEqual(prior.prompt.toPlainText(), original+' prior draft')

    def test_new_cancel_creates_no_item_and_keeps_older_new_draft(self):
        prior = self.fixture.editor; prior.new_material(text='old new draft'); prior.name.setText('old name'); prior._publish(force=True)
        snapshot = copy.deepcopy(self.w._material_editor_session); before = copy.deepcopy(self.w.state)
        dialog = self.page.new_material(); dialog.name.clear(); dialog.prompt.insertPlainText('discard')
        QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.w.state, before); self.assertEqual(self.w._material_editor_session, snapshot)

    def test_save_updates_original_only_preserves_unknown_and_native_history(self):
        self.w.state['items'][0]['future'] = {'keep': 1}
        key = materials.add_to_canvas(self.w.state, self.ident, self.fixture.canvas)
        prior = self.fixture.editor; prior.open_material(self.ident); document = prior.prompt.document()
        old_text = prior.prompt.toPlainText(); old_copy = copy.deepcopy(self.w.state['uses'][key])
        dialog = self.edit(); dialog.prompt.moveCursor(QTextCursor.MoveOperation.End); QTest.keyClicks(dialog.prompt, ' changed')
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(dialog.prompt.toPlainText(), old_text)
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(dialog.save())
        self.assertEqual(materials.item(self.w.state,self.ident)['prompt'], old_text+' changed')
        self.assertEqual(self.w.state['uses'][key], old_copy)
        self.assertEqual(materials.item(self.w.state,self.ident)['future'], {'keep':1})
        self.assertIs(self.w._material_editor_documents[('source',self.ident)], document)
        document.undo(); self.assertEqual(document.toPlainText(), old_text)
        document.redo(); self.assertEqual(document.toPlainText(), old_text+' changed')

    def test_saving_original_does_not_remove_unrelated_new_draft(self):
        prior=self.fixture.editor;prior.new_material(text='keep new');prior.name.setText('keep');prior._publish(force=True)
        saved=copy.deepcopy(self.w._material_editor_session['drafts'][('new','')])
        dialog=self.edit();dialog.prompt.insertPlainText('edited ');self.assertTrue(dialog.save())
        self.assertEqual(self.w._material_editor_session['drafts'][('new','')],saved)

    def saved_text(self, text):
        dialog = self.edit(); dialog.prompt.selectAll(); QTest.keyClicks(dialog.prompt, text)
        self.assertTrue(dialog.save()); self.app.processEvents()

    def undo_key(self, dialog):
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)

    def redo_key(self, dialog):
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)

    def test_reopen_native_undo_redo_keyboard_and_standard_menu(self):
        original = materials.item(self.w.state, self.ident)['prompt']
        self.saved_text('saved first'); self.saved_text('saved second')
        canonical = copy.deepcopy(self.w.state); dialog = self.edit()
        self.undo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), 'saved first')
        self.undo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), original)
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(dialog.prompt.toPlainText(), 'saved first')
        menu = dialog.prompt.createStandardContextMenu()
        actions = {action.objectName(): action for action in menu.actions()}
        self.assertTrue(actions['edit-undo'].isEnabled()); self.assertTrue(actions['edit-redo'].isEnabled())
        actions['edit-redo'].trigger(); self.assertEqual(dialog.prompt.toPlainText(), 'saved second')
        actions['edit-undo'].trigger(); self.assertEqual(dialog.prompt.toPlainText(), 'saved first')
        menu.deleteLater(); self.assertEqual(self.w.state, canonical)
        self.assertTrue(dialog.save())
        self.assertEqual(materials.item(self.w.state, self.ident)['prompt'], 'saved first')
        again = self.edit(); self.redo_key(again); self.assertEqual(again.prompt.toPlainText(), 'saved second')

    def test_cancel_after_inherited_undo_and_typing_restores_prior_redo_and_view(self):
        original = materials.item(self.w.state, self.ident)['prompt']
        self.saved_text('one'); self.saved_text('two')
        dialog = self.edit(); self.undo_key(dialog); self.assertTrue(dialog.save())
        prior = self.fixture.editor; prior.open_material(self.ident)
        source = prior.prompt.document(); self.assertTrue(source.isRedoAvailable())
        cursor = prior.prompt.textCursor(); cursor.setPosition(1); cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        prior.prompt.setTextCursor(cursor); prior._publish(force=True)
        saved_session = copy.deepcopy(self.w._material_editor_session); state = copy.deepcopy(self.w.state)
        for escape in (False, True):
            dialog = self.edit(); self.undo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), original)
            dialog.prompt.moveCursor(QTextCursor.MoveOperation.End); QTest.keyClicks(dialog.prompt, ' local branch')
            # Even a hidden form claiming and publishing cannot touch this source.
            prior.prompt.claim_history(); prior._publish(force=True)
            self.assertEqual(self.w._material_editor_session, saved_session); self.assertEqual(self.w.state, state)
            if escape: QTest.keyClick(dialog.prompt, Qt.Key.Key_Escape)
            else: QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
            self.assertIs(prior.prompt.document(), source); self.assertEqual(source.toPlainText(), 'one')
            self.assertEqual((prior.prompt.textCursor().anchor(), prior.prompt.textCursor().position()), (1,3))
            self.assertTrue(source.isRedoAvailable()); self.assertEqual(self.w._material_editor_session, saved_session)
            again = self.edit(); self.redo_key(again); self.assertEqual(again.prompt.toPlainText(), 'two'); again.reject()
            self.assertEqual(source.toPlainText(), 'one'); self.assertTrue(source.isRedoAvailable())

    def test_local_and_inherited_native_chains_traverse_in_order_then_save_branch(self):
        self.saved_text('one'); self.saved_text('two')
        dialog = self.edit(); dialog.prompt.moveCursor(QTextCursor.MoveOperation.End)
        QTest.keyClicks(dialog.prompt, ' local')
        self.undo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), 'two')
        self.undo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), 'one')
        self.redo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), 'two')
        self.redo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), 'two local')
        self.undo_key(dialog); self.undo_key(dialog)
        dialog.prompt.moveCursor(QTextCursor.MoveOperation.End); QTest.keyClicks(dialog.prompt, ' branch')
        self.assertTrue(dialog.save())
        again = self.edit(); self.undo_key(again); self.assertEqual(again.prompt.toPlainText(), 'one')
        self.redo_key(again); self.assertEqual(again.prompt.toPlainText(), 'one branch')
        self.redo_key(again); self.assertEqual(again.prompt.toPlainText(), 'one branch')

    def test_partial_add_save_is_new_checkpoint_even_when_followup_cancelled(self):
        self.saved_text('before'); self.page.set_destination(self.fixture.canvas)
        dialog = self.edit(); dialog.prompt.selectAll(); QTest.keyClicks(dialog.prompt, 'saved before retry')
        original_commit = self.w.document_operations.commit
        with patch.object(self.w.document_operations, 'commit', lambda op,label='Edit',**kw: False if label=='加入文字素材' else original_commit(op,label,**kw)):
            self.assertFalse(dialog.save(True))
        saved_state = copy.deepcopy(self.w.state); saved_session = copy.deepcopy(self.w._material_editor_session)
        self.undo_key(dialog); self.assertEqual(dialog.prompt.toPlainText(), 'before')
        QTest.keyClicks(dialog.prompt, ' discarded'); dialog.reject()
        self.assertEqual(self.w.state, saved_state); self.assertEqual(self.w._material_editor_session, saved_session)
        again = self.edit(); self.assertEqual(again.prompt.toPlainText(), 'saved before retry')
        self.undo_key(again); self.assertEqual(again.prompt.toPlainText(), 'before')
        self.redo_key(again); self.assertEqual(again.prompt.toPlainText(), 'saved before retry')

    def test_cancel_native_navigation_preserves_long_draft_scroll_and_redo(self):
        prior = self.fixture.editor; prior.open_material(self.ident)
        long_text = '\n'.join('原有長稿 %d: αβ文字😀' % i for i in range(90))
        prior.prompt.selectAll(); prior.prompt.insertPlainText(long_text)
        prior.prompt.moveCursor(QTextCursor.MoveOperation.End); prior.prompt.insertPlainText(' pending redo')
        prior.prompt.undo(); self.assertTrue(prior.prompt.document().isRedoAvailable())
        cursor = prior.prompt.textCursor(); cursor.setPosition(190); cursor.setPosition(210, QTextCursor.MoveMode.KeepAnchor)
        prior.prompt.setTextCursor(cursor); prior.prompt.verticalScrollBar().setValue(160)
        prior._publish(force=True); source = prior.prompt.document()
        position = (prior.prompt.textCursor().position(), prior.prompt.textCursor().anchor(), prior.prompt.verticalScrollBar().value())
        session = copy.deepcopy(self.w._material_editor_session); state = copy.deepcopy(self.w.state)
        dialog = self.edit(); self.redo_key(dialog); self.assertTrue(dialog.prompt.toPlainText().endswith(' pending redo'))
        dialog.prompt.insertPlainText('\n新行😀'); self.undo_key(dialog); self.undo_key(dialog)
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Escape)
        self.assertIs(prior.prompt.document(), source); self.assertEqual(source.toPlainText(), long_text)
        self.assertEqual((prior.prompt.textCursor().position(), prior.prompt.textCursor().anchor(), prior.prompt.verticalScrollBar().value()), position)
        self.assertEqual(self.w._material_editor_session, session); self.assertEqual(self.w.state, state)
        self.assertTrue(source.isRedoAvailable()); prior.prompt.redo()
        self.assertEqual(prior.prompt.toPlainText(), long_text+' pending redo')

    def test_modal_history_lifecycle_duplicate_cancel_and_saved_source_retained(self):
        from shiboken6 import isValid
        self.saved_text('source alive')
        source = self.w._material_editor_documents[('source', self.ident)]
        for _ in range(3):
            dialog = self.page.duplicate_material(self.ident)
            dialog = self.page.active_dialog
            scope = dialog.form.document_scope
            private = set(v for key,v in self.w._material_editor_documents.items() if key[:2] == ('dialog',scope))
            self.assertGreater(len(private), 1); dialog.reject()
            self.app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.app.processEvents(); QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            self.assertTrue(all(not isValid(document) for document in private))
            self.assertTrue(isValid(source)); self.assertFalse(self.w._material_native_transactions)
        self.assertEqual(source.toPlainText(), 'source alive')
        again = self.edit(); self.undo_key(again); self.assertNotEqual(again.prompt.toPlainText(), 'source alive')

    def test_virtual_uncategorized_is_readonly_and_maps_to_real_id(self):
        before=copy.deepcopy(self.w.state);self.page.set_browser_category('@uncategorized')
        self.assertEqual(self.page.get_browser_category(),'@uncategorized');self.assertEqual(self.page.listing.count(),0)
        self.assertEqual(self.w.state,before)
        category=materials.add_category(self.w.state,'未分類');self.page.refresh()
        self.assertEqual(self.page.get_browser_category(),category)
        self.page.set_browser_category('@uncategorized');self.assertEqual(self.page.get_browser_category(),category)

    def test_save_failure_stays_in_dialog(self):
        self.w.resize(640, 480); self.page.resize(640, 480)
        before = copy.deepcopy(self.w.state); dialog = self.edit(); dialog.name.clear()
        dialog.advanced_toggle.setChecked(True); dialog.info_toggle.setChecked(True)
        dialog.relation_toggle.setChecked(True); self.app.processEvents()
        dialog.form_scroll.verticalScrollBar().setValue(dialog.form_scroll.verticalScrollBar().maximum())
        self.assertFalse(dialog.save()); self.assertTrue(dialog.isVisible()); self.assertTrue(dialog.error.text())
        self.app.processEvents()
        self.assertFalse(dialog.form_scroll.isAncestorOf(dialog.error))
        self.assertTrue(dialog.error.isVisible())
        self.assertTrue(dialog.rect().contains(dialog.error.mapTo(dialog, dialog.error.rect().topLeft())))
        self.assertTrue(dialog.rect().contains(dialog.error.mapTo(dialog, dialog.error.rect().bottomRight())))
        self.assertTrue(dialog.rect().contains(dialog.save_button.mapTo(dialog, dialog.save_button.rect().center())))
        self.assertEqual(self.w.state,before)

    def test_enter_saves_name_and_reopens_with_prompt_undo_redo(self):
        for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            original = materials.item(self.w.state, self.ident)['prompt']
            dialog = self.edit()
            dialog.prompt.moveCursor(QTextCursor.MoveOperation.End)
            QTest.keyClicks(dialog.prompt, ' keyboard saved')
            dialog.name.setText('Keyboard save '+str(key.value)); dialog.name.setFocus()
            QTest.keyClick(dialog.name, key); self.app.processEvents()
            self.assertIsNone(self.page.active_dialog)
            self.assertEqual(materials.item(self.w.state, self.ident)['name'], 'Keyboard save '+str(key.value))
            self.assertEqual(materials.item(self.w.state, self.ident)['prompt'], original+' keyboard saved')
            again = self.edit()
            self.assertEqual(again.prompt.toPlainText(), original+' keyboard saved')
            self.undo_key(again); self.assertEqual(again.prompt.toPlainText(), original)
            self.redo_key(again); self.assertEqual(again.prompt.toPlainText(), original+' keyboard saved')
            again.reject(); self.app.processEvents()

    def test_enter_keeps_multiline_and_failed_save_draft(self):
        before = copy.deepcopy(self.w.state); dialog = self.edit()
        dialog.advanced_toggle.setChecked(True); dialog.info_toggle.setChecked(True)
        dialog.relation_toggle.setChecked(True)
        for editor in (dialog.prompt, dialog.form.notes, dialog.form.excludes):
            dialog.form_scroll.ensureWidgetVisible(editor); editor.setFocus()
            editor.moveCursor(QTextCursor.MoveOperation.End); original = editor.toPlainText()
            for key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                QTest.keyClicks(editor, ' next line'); original += ' next line'
                QTest.keyClick(editor, key, Qt.KeyboardModifier.KeypadModifier if key == Qt.Key.Key_Enter else Qt.KeyboardModifier.NoModifier); original += '\n'
                self.assertEqual(editor.toPlainText(), original)
                self.assertTrue(dialog.isVisible()); self.assertEqual(self.w.state, before)
        dialog.name.clear(); dialog.name.setFocus(); QTest.keyClick(dialog.name, Qt.Key.Key_Return)
        self.assertTrue(dialog.isVisible()); self.assertTrue(dialog.error.text())
        self.assertEqual(self.w.state, before)
        QTest.keyClick(dialog.name, Qt.Key.Key_Escape); self.app.processEvents()
        self.assertIsNone(self.page.active_dialog); self.assertEqual(self.w.state, before)

    def test_enter_completion_and_ime_commit_do_not_save(self):
        from PySide6.QtGui import QInputMethodEvent
        before = copy.deepcopy(self.w.state); dialog = self.edit()
        dialog.prompt.selectAll(); QTest.keyClicks(dialog.prompt, 'smi')
        token = dialog.prompt.context()
        dialog.prompt.display(token, [dict(value='smile', source='local', category=0)])
        self.assertTrue(dialog.prompt.completer.popup().isVisible())
        QTest.keyClick(dialog.prompt, Qt.Key.Key_Return)
        self.assertIn('smile', dialog.prompt.toPlainText())
        self.assertTrue(dialog.isVisible()); self.assertEqual(self.w.state, before)
        preedit = QInputMethodEvent('文字', [])
        QApplication.sendEvent(dialog.prompt, preedit)
        self.assertTrue(dialog.prompt.composing)
        commit = QInputMethodEvent(); commit.setCommitString('文字')
        QApplication.sendEvent(dialog.prompt, commit)
        self.assertIn('文字', dialog.prompt.toPlainText())
        self.assertTrue(dialog.isVisible()); self.assertEqual(self.w.state, before)

    def test_initial_preview_is_immediate_and_double_click_survives_reflow(self):
        from PySide6.QtGui import QMouseEvent
        from PySide6.QtCore import QPointF
        before = copy.deepcopy(self.w.state)
        for width in (1440, 1024, 640):
            for edge in (False, True):
                self.page.resize(width, 900); self.page.close_preview(); self.app.processEvents()
                listing = self.page.listing; entry = listing.item(2)
                ident = entry.data(Qt.ItemDataRole.UserRole); rect = listing.visualItemRect(entry)
                point = QPoint(rect.right()-15, rect.center().y()) if edge else rect.center()
                global_point = listing.viewport().mapToGlobal(point)
                QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton, pos=point)
                self.assertTrue(self.page.preview_card.isVisible())
                self.assertEqual(self.page.preview_id, ident)
                self.app.processEvents()
                # Deliver the second physical click to the widget now under
                # the original desktop position, including the new preview.
                receiver = QApplication.widgetAt(global_point) or self.page
                local = receiver.mapFromGlobal(global_point)
                event = QMouseEvent(QEvent.Type.MouseButtonPress, QPointF(local), QPointF(global_point),
                                   Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(receiver, event)
                self.assertEqual(self.page.active_dialog.target_id, ident)
                release = QMouseEvent(QEvent.Type.MouseButtonRelease, QPointF(), QPointF(global_point),
                                     Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
                QApplication.sendEvent(self.page.active_dialog, release)
                self.assertTrue(self.page.active_dialog.isVisible())
                self.page.active_dialog.reject(); self.app.processEvents()
        self.assertEqual(self.w.state, before)

    def test_hover_actions_target_row_without_selecting_and_menu_edits_same_item(self):
        listing = self.page.listing; actions = listing.row_actions
        self.page.preview_material(self.ident); self.app.processEvents()
        entry = next(listing.item(i) for i in range(listing.count())
                     if listing.item(i).data(Qt.ItemDataRole.UserRole) != self.ident)
        target = entry.data(Qt.ItemDataRole.UserRole)
        pinned = materials.item(self.w.state, target).get('pinned', False)
        QTest.mouseMove(listing.viewport(), listing.visualItemRect(entry).center()); self.app.processEvents()
        self.assertTrue(actions.isVisible()); self.assertEqual(actions.ident, target)
        QTest.mouseMove(actions.pin, actions.pin.rect().center()); self.app.processEvents()
        self.assertTrue(actions.pin.isVisible())
        QTest.mouseClick(actions.pin, Qt.MouseButton.LeftButton); self.app.processEvents()
        self.assertEqual(materials.item(self.w.state, target)['pinned'], not pinned)
        self.assertEqual(self.page.selected_id(), self.ident); self.assertEqual(self.page.preview_id, self.ident)
        entry = next(listing.item(i) for i in range(listing.count()) if listing.item(i).data(Qt.ItemDataRole.UserRole) == target)
        QTest.mouseMove(listing.viewport(), listing.visualItemRect(entry).center()); self.app.processEvents()
        QTest.mouseClick(actions.more, Qt.MouseButton.LeftButton); self.app.processEvents()
        menu = QApplication.activePopupWidget(); self.assertIsNotNone(menu)
        self.assertEqual([a.text() for a in menu.actions()], ['編輯素材', '取消釘選' if not pinned else '釘選素材', '另存為新素材', '內容歷史', '刪除'])
        menu.hide(); menu.actions()[0].trigger(); self.app.processEvents()
        self.assertEqual(self.page.active_dialog.target_id, target)
        self.page.active_dialog.reject(); self.app.processEvents()
        QTest.mouseMove(self.page.search, self.page.search.rect().center()); self.app.processEvents()
        self.assertFalse(actions.isVisible())

    def test_hover_actions_hide_on_scroll_and_filtered_unpin(self):
        for i in range(25): materials.save(self.w.state, dict(name=f'Hover {i}', prompt='scroll', module=None, pinned=True))
        self.page.pins.setChecked(True); self.page.resize(1024, 640); self.page.refresh(); self.app.processEvents()
        listing = self.page.listing; actions = listing.row_actions
        entry = listing.item(0); target = entry.data(Qt.ItemDataRole.UserRole)
        QTest.mouseMove(listing.viewport(), listing.visualItemRect(entry).center()); self.app.processEvents()
        self.assertTrue(actions.isVisible())
        QTest.mouseClick(actions.pin, Qt.MouseButton.LeftButton); self.app.processEvents()
        self.assertFalse(materials.item(self.w.state, target)['pinned'])
        self.assertNotIn(target, listing.order_ids())
        QTest.mouseMove(listing.viewport(), listing.visualItemRect(listing.item(1)).center()); self.app.processEvents()
        self.assertTrue(actions.isVisible())
        listing.verticalScrollBar().setValue(100)
        self.assertFalse(actions.isVisible())

    def test_advanced_tabs_keep_drafts_and_footer_reachable(self):
        from PySide6.QtWidgets import QScrollArea
        for size in ((1440, 900), (640, 480)):
            with self.subTest(size=size):
                self.w.resize(*size); self.page.resize(*size)
                before = copy.deepcopy(self.w.state); dialog = self.edit()
                def reveal(control):
                    # Management fields now live on the separate side card,
                    # whose scroll area is independent of the prompt editor.
                    scroll = dialog.panels.scroll if dialog.panels.side.isAncestorOf(control) else dialog.form_scroll
                    viewport = scroll.viewport()
                    scroll.ensureWidgetVisible(control, 8, 8)
                    self.app.processEvents(); QTest.qWait(10)
                    # Qt's ensureWidgetVisible follows a text edit's cursor,
                    # which can leave its bottom clipped. Exercise the outer
                    # scrollbar to reveal the complete active field as well.
                    center=control.mapTo(scroll.widget(),control.rect().center())
                    scroll.ensureVisible(center.x(),center.y(),8,control.height()//2+8)
                    self.app.processEvents()
                    position = control.mapTo(viewport, control.rect().center())
                    self.assertTrue(viewport.rect().contains(position), type(control).__name__)
                    return control
                QTest.mouseClick(reveal(dialog.advanced_toggle), Qt.MouseButton.LeftButton)
                QTest.qWait(260)
                tabs=dialog.pages.details
                self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ['資訊', '排除 Tag', '預覽圖'])
                for index,controls in enumerate(((dialog.form.edit_category,dialog.form.kind,dialog.form.aliases,dialog.form.notes,dialog.form.lookup),
                                                (dialog.form.excludes,), (dialog.form.preview,))):
                    tabs.setCurrentIndex(index); self.app.processEvents()
                    for control in controls:
                        self.assertTrue(control.isVisible()); reveal(control)
                self.assertEqual(dialog.form_scroll.findChildren(QScrollArea), [])
                for control in (dialog.save_button, dialog.cancel_button):
                    self.assertFalse(dialog.form_scroll.isAncestorOf(control))
                    self.assertTrue(dialog.rect().contains(control.mapTo(dialog, control.rect().center())))
                self.assertFalse(dialog.form_scroll.isAncestorOf(dialog.error))
                reveal(dialog.history_button)
                tabs.setCurrentIndex(0); self.app.processEvents()
                QTest.mouseClick(reveal(dialog.form.aliases), Qt.MouseButton.LeftButton)
                dialog.form.aliases.selectAll(); QTest.keyClicks(dialog.form.aliases, 'advanced aliases')
                QTest.mouseClick(reveal(dialog.form.notes), Qt.MouseButton.LeftButton)
                dialog.form.notes.selectAll(); QTest.keyClicks(dialog.form.notes, 'advanced notes')
                tabs.setCurrentIndex(1); self.app.processEvents()
                QTest.mouseClick(reveal(dialog.form.excludes), Qt.MouseButton.LeftButton)
                dialog.form.excludes.selectAll(); QTest.keyClicks(dialog.form.excludes, 'excluded_tag')
                self.assertEqual(dialog._value()['aliases'], ['advanced aliases'])
                self.assertEqual(dialog._value()['notes'], 'advanced notes')
                self.assertEqual(dialog._value()['excludes'], ['excluded_tag'])
                self.assertEqual(self.w.state, before)
                QTest.mouseClick(dialog.cancel_button, Qt.MouseButton.LeftButton)
                self.assertEqual(self.w.state, before)

    def test_long_text_and_management_keep_narrow_dialog_footer_within_host(self):
        self.w.resize(640, 480); self.page.resize(640, 480)
        dialog = self.edit()
        dialog.prompt.setPlainText('\n'.join('long draft line' for _ in range(100)))
        for opened in (True, False):
            dialog.pages.details_toggle.setChecked(opened); QTest.qWait(260)
            self.assertLessEqual(dialog.height(), self.w.height()-24)
            self.assertTrue(dialog.rect().contains(dialog.save_button.mapTo(dialog, dialog.save_button.rect().center())))
            self.assertGreater(dialog.prompt.verticalScrollBar().maximum(), 0)

    def test_save_add_partial_failure_retry_is_only_add_and_idempotent(self):
        self.page.set_destination(self.fixture.canvas); dialog = self.page.new_material()
        dialog.name.setText('retry source'); dialog.prompt.setPlainText('retry text')
        original_commit = self.w.document_operations.commit; calls=[]
        def commit(operation, label='Edit', **kwargs):
            calls.append(label)
            if label == '加入文字素材' and calls.count(label) == 1: return False
            return original_commit(operation,label,**kwargs)
        with patch.object(self.w.document_operations,'commit',commit):
            self.assertFalse(dialog.save(True)); ident=dialog.target_id
            self.assertEqual(len([v for v in self.w.state['items'] if v['id']==ident]),1)
            self.assertIn('已保存',dialog.error.text())
            self.assertTrue(dialog.save(True)); self.assertTrue(dialog.save(True))
        self.assertEqual(calls.count('保存文字素材'),1); self.assertEqual(calls.count('加入文字素材'),2)
        self.assertEqual(len([v for v in self.w.state['uses'].values() if v.get('source_id')==ident]),1)

    def test_partial_retry_changed_content_requires_new_save(self):
        self.page.set_destination(self.fixture.canvas); dialog = self.page.new_material()
        dialog.name.setText('changed'); dialog.prompt.setPlainText('one')
        original_commit=self.w.document_operations.commit; calls=[]
        def commit(operation,label='Edit',**kwargs):
            calls.append(label)
            if label=='加入文字素材' and calls.count(label)==1:return False
            return original_commit(operation,label,**kwargs)
        with patch.object(self.w.document_operations,'commit',commit):
            self.assertFalse(dialog.save(True));dialog.prompt.selectAll();QTest.keyClicks(dialog.prompt,'two')
            self.assertTrue(dialog.save(True))
        self.assertEqual(calls.count('保存文字素材'),2)
        copies=[v for v in self.w.state['uses'].values() if v.get('source_id')==dialog.target_id]
        self.assertEqual(len(copies),1);self.assertEqual(copies[0]['prompt'],'two')

    def test_retry_destination_change_never_uses_old_target(self):
        self.page.set_destination(self.fixture.canvas); dialog=self.page.new_material()
        dialog.name.setText('destination');dialog.prompt.setPlainText('text')
        original_commit=self.w.document_operations.commit
        with patch.object(self.w.document_operations,'commit',lambda op,label='Edit',**kw: False if label=='加入文字素材' else original_commit(op,label,**kw)):
            self.assertFalse(dialog.save(True))
        ident=dialog.target_id;self.page.set_destination(None)
        self.assertFalse(dialog.save(True));self.assertIn('選擇',dialog.error.text())
        self.assertFalse(any(v.get('source_id')==ident for v in self.w.state['uses'].values()))
        self.page.set_destination(self.fixture.canvas);self.assertTrue(dialog.save(True))
        self.assertEqual(sum(v.get('source_id')==ident for v in self.w.state['uses'].values()),1)

    def test_category_search_preview_layout_and_empty_new_inherits_category(self):
        category = materials.add_category(self.w.state,'Empty category');self.page.refresh();self.page.set_browser_category(category)
        self.assertEqual(self.page.listing.count(),0)
        self.assertFalse(self.page.listing.isVisible());self.assertTrue(self.page.empty_results.isVisible())
        dialog=self.page.new_material();self.assertEqual(dialog.form.edit_category.currentData(),category);dialog.reject()
        self.page.set_browser_category(None)
        for width,height in ((1360,900),(2048,1152)):
            self.page.close_preview();self.page.resize(width,height);self.app.processEvents();self.app.processEvents()
            before=self.page.browser.geometry();self.page.preview_material(self.ident);self.app.processEvents()
            self.assertEqual(before.width(),SETTINGS_READING_WIDTH)
            workspace_left=self.page.category_panel.geometry().right()+1
            left_gap=before.x()-workspace_left
            right_gap=self.page.scroll.viewport().width()-(before.x()+before.width())
            self.assertLessEqual(abs(left_gap-right_gap),1,('Unbalanced workspace gutters',left_gap,right_gap))
            self.assertGreaterEqual(left_gap,24)
            self.assertLessEqual(self.page.browser.width(),SETTINGS_READING_WIDTH)
            self.assertEqual(self.page.title.mapTo(self.page.browser,QPoint()).x(),1)
            self.assertEqual(self.page.search.mapTo(self.page.browser,QPoint()).x(),1)
            self.assertEqual(self.page.listing.x(),0)
            order_right=self.page.order.mapTo(self.page.browser,QPoint()).x()+self.page.order.width()
            more_right=self.page.library_more.mapTo(self.page.browser,QPoint()).x()+self.page.library_more.width()
            self.assertEqual(order_right,self.page.listing.viewport().width()-1)
            self.assertEqual(more_right,order_right)
            self.assertIs(self.page.category_panel.parentWidget(),self.page.scroll.viewport())
            self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(),0)
            self.assertTrue(260 <= self.page.preview_card.width() <= 380)
            self.assertEqual(self.page.preview_card.x(),self.page.browser.geometry().right()+25)
            self.assertEqual(self.page.preview_card.y(),self.page.search.mapTo(self.page.shell,QPoint()).y())
            group_left=self.page.browser.x()-workspace_left
            group_right=self.page.scroll.viewport().width()-self.page.preview_card.geometry().right()-1
            self.assertLessEqual(abs(group_left-group_right),1)
            self.page.close_preview();self.assertEqual(self.page.browser.geometry(),before)
        for i in range(self.page.listing.count()): self.assertTrue(self.page.listing.item(i).icon().isNull())
        for width in (1100,1024,800,640):
            self.page.close_preview();self.page.resize(width,640);self.app.processEvents();self.app.processEvents()
            self.page.preview_material(self.ident);self.app.processEvents();self.app.processEvents()
            self.assertLessEqual(self.page.browser.width(),SETTINGS_READING_WIDTH)
            self.assertLessEqual(self.page.browser.geometry().right(),self.page.scroll.viewport().width())
            workspace_left=0 if self.page.narrow else self.page.category_panel.geometry().right()+1
            left_gap=self.page.browser.x()-workspace_left
            right_gap=self.page.scroll.viewport().width()-(self.page.preview_card.x()+self.page.preview_card.width())
            self.assertLessEqual(abs(left_gap-right_gap),1)
            self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(),0)
            self.assertEqual(self.page.preview_card.x(),self.page.browser.geometry().right()+25)
            self.assertEqual(self.page.preview_card.y(),self.page.search.mapTo(self.page.shell,QPoint()).y())

    def test_preview_stays_right_while_list_and_controls_adapt_during_resize(self):
        self.page.set_browser_category(None); self.page.preview_material(self.ident)
        original=copy.deepcopy(self.w.state['items'])
        for width in (2048,1440,1360,1184,1152,1100,1024,1000,960,900,880,800,640,1024,1360,2048):
            self.page.resize(width,900); self.app.processEvents(); self.app.processEvents()
            main=self.page.browser.geometry(); detail=self.page.preview_card.geometry()
            self.assertEqual(self.page.preview_id,self.ident)
            self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(),0)
            self.assertLessEqual(detail.right(),self.page.scroll.viewport().width())
            self.assertEqual(detail.x(),main.right()+25)
            self.assertEqual(detail.y(),self.page.search.mapTo(self.page.shell,QPoint()).y())
            self.assertGreaterEqual(main.width(),280)
            self.assertGreaterEqual(detail.width(),260)
            if width==1024:
                self.assertLess(main.width(),520)
                self.assertLess(detail.width(),340)
            controls=(self.page.search,self.page.pins,self.page.order,self.page.new_button,self.page.library_more)
            for control in controls:
                location=control.mapTo(self.page.browser,QPoint())
                self.assertTrue(self.page.browser.rect().contains(location),control)
                self.assertLessEqual(location.x()+control.width(),main.width(),control)
            card_right=self.page.listing.viewport().mapTo(self.page.browser,QPoint()).x()+self.page.listing.viewport().width()-1
            for control in (self.page.order,self.page.library_more):
                self.assertEqual(control.mapTo(self.page.browser,QPoint()).x()+control.width(),card_right)
            self.assertIs(self.page.category_add.parentWidget(),
                          self.page.category_controls if self.page.narrow else self.page.category_title)
        self.assertEqual(self.w.state['items'],original)

    def test_narrow_preview_filters_close_reopen_and_edit_keep_the_same_material(self):
        self.page.resize(1024,900); self.app.processEvents();self.app.processEvents()
        asset=materials.item(self.w.state,self.ident)
        before=copy.deepcopy(self.w.state['items'])
        for width in (1024,640):
            self.page.resize(width,900);self.app.processEvents();self.app.processEvents()
            self.page.search.setText(asset['prompt']);self.app.processEvents()
            self.page.preview_material(self.ident);self.app.processEvents()
            self.assertEqual(self.page.preview_text.toPlainText(),asset['prompt'])
            self.page.order.setCurrentIndex(self.page.order.findData('manual'));self.app.processEvents()
            self.assertEqual(self.page.preview_id,self.ident)
            QTest.mouseClick(self.page.edit_button,Qt.MouseButton.LeftButton);self.app.processEvents()
            self.assertIsNotNone(self.page.active_dialog)
            self.assertEqual(self.page.active_dialog.target_id,self.ident)
            self.page.active_dialog.reject();self.app.processEvents()
            self.page.close_preview();self.app.processEvents()
            main_width=self.page.browser.width()
            self.page.preview_material(self.ident);self.app.processEvents();self.app.processEvents()
            self.assertLess(self.page.browser.width(),main_width)
            self.assertEqual(self.page.preview_card.y(),self.page.search.mapTo(self.page.shell,QPoint()).y())
            self.assertGreater(self.page.preview_card.x(),self.page.browser.geometry().right())
        self.assertEqual(self.w.state['items'],before)

if __name__=='__main__': unittest.main()
