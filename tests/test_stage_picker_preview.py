"""Keyboard previews use an empty journal and never alter the real document."""
import copy
import unittest
from contextlib import contextmanager
from unittest.mock import patch
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.function_picker import FunctionPicker
import test_editor_reorder as fixture


class StagePickerPreviewTests(unittest.TestCase):
    setUp=fixture.EditorReorderTests.setUp
    tearDown=fixture.EditorReorderTests.tearDown

    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    @contextmanager
    def picker(self):
        self.w.enter_canvas();QTest.qWait(400);self.w.persist()
        dialog=FunctionPicker(self.w.canvas);dialog.show();QTest.qWait(30)
        try:yield dialog
        finally:
            dialog.reject();dialog.deleteLater();self.app.processEvents()

    def select_stage(self,dialog):
        at=next(i for i in range(dialog.list.count())
                if dialog.list.item(i).data(Qt.ItemDataRole.UserRole)=='stage')
        dialog.list.setFocus();QTest.keyClick(dialog.list,Qt.Key.Key_Home)
        for _ in range(at):QTest.keyClick(dialog.list,Qt.Key.Key_Down)
        QTest.qWait(20)
        self.assertEqual(dialog.current_id(),'stage')
        self.assertIsNotNone(dialog.preview.card)
        self.assertEqual(self.errors,[],'Qt callback exceptions must fail this test')

    def test_keyboard_browse_and_escape_keep_document_and_journal_untouched(self):
        with self.picker() as dialog:
            before=copy.deepcopy(self.w.state);saved=self.w.store.load()
            history=self.w.canvas.history_projection(self.w.state)
            runner=self.w.comfy.input_flow.chain
            journal=list(self.w.store.db.execute('SELECT * FROM stage_journal'))
            with patch.object(runner.store,'latest_stage_status',side_effect=AssertionError('No real journal reads')):
                for _ in range(2):
                    self.select_stage(dialog)
                    self.assertFalse(dialog.preview.card.panel.status.isVisible())
                    self.assertIsNot(dialog.preview.context.window.comfy.input_flow.chain.store,runner.store)
                    QTest.keyClick(dialog.list,Qt.Key.Key_Home);QTest.qWait(10)
                self.select_stage(dialog)
                QTest.keyClick(dialog.list,Qt.Key.Key_Escape);QTest.qWait(20)
            self.assertFalse(dialog.isVisible())
            self.assertEqual(self.w.state,before);self.assertEqual(self.w.store.load(),saved)
            self.assertEqual(self.w.canvas.history_projection(self.w.state),history)
            self.assertEqual(list(self.w.store.db.execute('SELECT * FROM stage_journal')),journal)

    def test_button_adds_one_real_stage_whose_status_still_uses_its_journal(self):
        with self.picker() as dialog:
            before=set(self.w.state['multi_output']['stages'])
            self.select_stage(dialog)
            QTest.mouseClick(dialog.add_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
            added=set(self.w.state['multi_output']['stages'])-before
            self.assertEqual(len(added),1);self.assertFalse(dialog.isVisible())
            key=added.pop();panel=self.w.canvas.flow_cards[key].panel
            store=self.w.comfy.input_flow.chain.store
            attempt=store.add('attempt',self.w.state['workspace'],'synthetic-preview-check',
                              stage=key,status='failed',error='synthetic failure')
            self.w.comfy.stateChanged.emit()
            self.assertEqual(panel.status.text(),'synthetic failure');self.assertTrue(panel.status.isVisible())
            store.update(attempt['id'],status='complete',error='');self.w.comfy.stateChanged.emit()
            self.assertEqual(panel.status.text(),'');self.assertFalse(panel.status.isVisible())
            self.assertEqual(self.errors,[])

    def test_keyboard_enter_adds_stage_only_once(self):
        with self.picker() as dialog:
            before=len(self.w.state['multi_output']['stages'])
            self.select_stage(dialog)
            QTest.keyClick(dialog.list,Qt.Key.Key_Return);QTest.qWait(30)
            self.assertFalse(dialog.isVisible())
            self.assertEqual(len(self.w.state['multi_output']['stages']),before+1)
            self.assertEqual(self.errors,[])


if __name__=='__main__':unittest.main()
