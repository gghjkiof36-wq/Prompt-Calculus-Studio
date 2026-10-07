"""Expanded text canvas gestures with isolated state and synthetic ComfyUI."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF
from PySide6.QtTest import QTest
from stage_fixture import StageFixture,APP
from prompt_calculus_studio import content_history


class CompositionEditorTests(StageFixture):
    def editor(self):
        self.w.state['settings']['reduce_motion']=True
        self.c.open_editor(self.cid);QTest.qWait(30)
        editor=self.c.editor_page
        self.assertIs(self.w.surface_stack.currentWidget(),editor)
        return editor

    def test_double_click_blank_creates_local_text_at_editor_coordinates(self):
        editor=self.editor();before=set(self.w.state['uses'])
        point=editor.view.viewport().rect().bottomRight()-QPoint(25,25)
        position=editor.view.mapToScene(point)
        with patch('prompt_calculus_studio.text_canvas.InputDialog.getMultiLineText',return_value=('local\ntext',True)):
            QTest.mouseDClick(editor.view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(35)
        added=(set(self.w.state['uses'])-before).pop()
        self.assertIn(added,editor.data()['members'])
        self.assertEqual(self.w.state['uses'][added]['prompt'],'local\ntext')
        self.assertEqual(editor.data()['edit_positions'][added],[position.x(),position.y()])
        self.assertNotEqual(self.w.state['text_positions'][added],editor.data()['edit_positions'][added])
        editor.undo();self.assertNotIn(added,self.w.state['uses'])
        editor.redo();self.assertEqual(self.w.state['uses'][added]['prompt'],'local\ntext')
        self.assertFalse(self.executor.submissions)

    def test_card_double_click_uses_shared_editor_in_expanded_page(self):
        editor=self.editor();card=editor.text_canvas.cards['root'];editor.view.centerOn(card)
        position=editor.view.mapFromScene(card.mapToScene(QPointF(75,15)))
        QTest.mouseDClick(editor.view.viewport(),Qt.MouseButton.LeftButton,pos=position);QTest.qWait(30)
        self.assertIsNotNone(editor.prompt_editor)
        self.assertEqual(editor.prompt_editor.target,('canvas','root'))
        self.assertIs(self.w.surface_stack.currentWidget(),editor)
        self.assertIn(editor.data()['name'],editor.prompt_editor.heading.text())
        editor.prompt_editor.prompt.setPlainText('edited local copy');QTest.qWait(410)
        self.assertEqual(self.w.state['uses']['root']['prompt'],'edited local copy')
        self.assertTrue(content_history.entries(self.w.state,target=self.cid,content_only=True))
        self.assertFalse(self.executor.submissions)

    def test_expanded_view_restores_zoom_center_and_outer_view(self):
        self.c.view.resetTransform();self.c.view.scale(.63,.63);self.c.view.centerOn(280,320)
        outer_center=self.c.view.mapToScene(self.c.view.viewport().rect().center());editor=self.editor()
        editor.view.resetTransform();editor.view.scale(1.31,1.31);editor.view.centerOn(320,460)
        center=editor.view.mapToScene(editor.view.viewport().rect().center());stack=len(self.c.undo_stack)
        editor.close_editor();QTest.qWait(20)
        self.assertAlmostEqual(self.c.view.transform().m11(),.63,places=6)
        self.assertLess((self.c.view.mapToScene(self.c.view.viewport().rect().center())-outer_center).manhattanLength(),4)
        self.assertEqual(len(self.c.undo_stack),stack)
        editor=self.editor()
        self.assertAlmostEqual(editor.view.transform().m11(),1.31,places=6)
        self.assertLess((editor.view.mapToScene(editor.view.viewport().rect().center())-center).manhattanLength(),4)

    def test_text_shortcuts_and_close_flush_do_not_delete_canvas_items(self):
        editor=self.editor();editor.edit_text('root');QTest.qWait(15)
        card=editor.text_canvas.cards['root'];card.setSelected(True)
        prompt=editor.prompt_editor.prompt;prompt.setFocus();stack=len(self.c.undo_stack)
        QTest.keyClick(prompt,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(prompt,Qt.Key.Key_Backspace)
        QTest.keyClick(prompt,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(prompt.toPlainText(),'white shirt');self.assertIn('root',self.w.state['uses'])
        self.assertEqual(len(self.c.undo_stack),stack)
        prompt.setPlainText('close without waiting');self.assertTrue(editor.can_leave())
        self.assertEqual(self.w.state['uses']['root']['prompt'],'close without waiting')
        editor.view.setFocus();editor.view.scene().clearFocus()
        QTest.keyClick(editor.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.w.state['uses']['root']['prompt'],'white shirt')
        self.assertFalse(self.executor.submissions)

    def test_history_filters_current_canvas_and_adapter_accepts_edit_metadata(self):
        editor=self.editor();before=copy.deepcopy(self.w.state['uses']['root'])
        editor.text_canvas.commit(lambda state:state['uses']['root'].update(prompt='adapter'),label='展開頁文字',merge_key='expanded:root')
        entries=content_history.entries(self.w.state,target=self.cid,content_only=True)
        self.assertEqual(entries[0]['label'],'展開頁文字')
        with patch('prompt_calculus_studio.prompt_editor.show_history') as show:
            QTest.mouseClick(editor.history_button,Qt.MouseButton.LeftButton)
        show.assert_called_once_with(self.w,self.cid)
        editor.undo();self.assertEqual(self.w.state['uses']['root'],before)


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(CompositionEditorTests(name) for name in CompositionEditorTests.__dict__ if name.startswith('test_'))
