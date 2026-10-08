"""Rev9 modal editing boundaries, real Qt events, isolated in-memory project."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QCoreApplication, QEvent
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
import test_material_editor as fixture_module
from prompt_calculus_studio.prompt_editor import PromptEditor
from prompt_calculus_studio import materials
from prompt_calculus_studio.settings_page import SETTINGS_READING_WIDTH


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
        original_commit = self.w.canvas.commit
        with patch.object(self.w.canvas, 'commit', lambda op,label='Edit',**kw: False if label=='加入文字素材' else original_commit(op,label,**kw)):
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
        before = copy.deepcopy(self.w.state); dialog = self.edit(); dialog.name.clear()
        self.assertFalse(dialog.save()); self.assertTrue(dialog.isVisible()); self.assertTrue(dialog.error.text())
        self.assertEqual(self.w.state,before)

    def test_save_add_partial_failure_retry_is_only_add_and_idempotent(self):
        self.page.set_destination(self.fixture.canvas); dialog = self.page.new_material()
        dialog.name.setText('retry source'); dialog.prompt.setPlainText('retry text')
        original_commit = self.w.canvas.commit; calls=[]
        def commit(operation, label='Edit', **kwargs):
            calls.append(label)
            if label == '加入文字素材' and calls.count(label) == 1: return False
            return original_commit(operation,label,**kwargs)
        with patch.object(self.w.canvas,'commit',commit):
            self.assertFalse(dialog.save(True)); ident=dialog.target_id
            self.assertEqual(len([v for v in self.w.state['items'] if v['id']==ident]),1)
            self.assertIn('已保存',dialog.error.text())
            self.assertTrue(dialog.save(True)); self.assertTrue(dialog.save(True))
        self.assertEqual(calls.count('保存文字素材'),1); self.assertEqual(calls.count('加入文字素材'),2)
        self.assertEqual(len([v for v in self.w.state['uses'].values() if v.get('source_id')==ident]),1)

    def test_partial_retry_changed_content_requires_new_save(self):
        self.page.set_destination(self.fixture.canvas); dialog = self.page.new_material()
        dialog.name.setText('changed'); dialog.prompt.setPlainText('one')
        original_commit=self.w.canvas.commit; calls=[]
        def commit(operation,label='Edit',**kwargs):
            calls.append(label)
            if label=='加入文字素材' and calls.count(label)==1:return False
            return original_commit(operation,label,**kwargs)
        with patch.object(self.w.canvas,'commit',commit):
            self.assertFalse(dialog.save(True));dialog.prompt.selectAll();QTest.keyClicks(dialog.prompt,'two')
            self.assertTrue(dialog.save(True))
        self.assertEqual(calls.count('保存文字素材'),2)
        copies=[v for v in self.w.state['uses'].values() if v.get('source_id')==dialog.target_id]
        self.assertEqual(len(copies),1);self.assertEqual(copies[0]['prompt'],'two')

    def test_retry_destination_change_never_uses_old_target(self):
        self.page.set_destination(self.fixture.canvas); dialog=self.page.new_material()
        dialog.name.setText('destination');dialog.prompt.setPlainText('text')
        original_commit=self.w.canvas.commit
        with patch.object(self.w.canvas,'commit',lambda op,label='Edit',**kw: False if label=='加入文字素材' else original_commit(op,label,**kw)):
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
            self.assertEqual(self.page.browser.geometry(),before)
            self.assertEqual(self.page.title.x(),0)
            self.assertEqual(self.page.search.x(),0)
            self.assertEqual(self.page.listing.x(),0)
            self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(),0)
            if width==2048:
                self.assertTrue(340 <= self.page.preview_card.width() <= 380)
                self.assertLess(self.page.browser.geometry().right(),self.page.preview_card.x())
            else:
                self.assertGreater(self.page.preview_card.y(),self.page.browser.geometry().bottom())
            self.page.close_preview();self.assertEqual(self.page.browser.geometry(),before)
        for i in range(self.page.listing.count()): self.assertTrue(self.page.listing.item(i).icon().isNull())
        for width in (1100,640,360):
            self.page.close_preview();self.page.resize(width,640);self.app.processEvents();self.app.processEvents()
            self.page.preview_material(self.ident);self.app.processEvents();self.app.processEvents()
            self.assertLessEqual(self.page.browser.width(),SETTINGS_READING_WIDTH)
            self.assertLessEqual(self.page.browser.geometry().right(),self.page.scroll.viewport().width())
            workspace_left=0 if self.page.narrow else self.page.category_panel.geometry().right()+1
            left_gap=self.page.browser.x()-workspace_left
            right_gap=self.page.scroll.viewport().width()-(self.page.browser.x()+self.page.browser.width())
            self.assertLessEqual(abs(left_gap-right_gap),1)
            self.assertEqual(self.page.scroll.horizontalScrollBar().maximum(),0)
            self.assertGreater(self.page.preview_card.y(),self.page.browser.geometry().bottom())

if __name__=='__main__': unittest.main()
