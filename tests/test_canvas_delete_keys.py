"""Canvas deletion uses real mouse/key events without contacting ComfyUI."""
import copy
import unittest
from PySide6.QtCore import Qt,QPointF
from PySide6.QtTest import QTest
import stage_fixture as fixture
from prompt_studio import multi_output as model,stage_model
from prompt_studio.flow_data import add_image_input,add_scheduler
from prompt_studio.result_data import add_text_reader


class CanvasDeleteKeysTests(unittest.TestCase):
    setUp=fixture.StageFixture.setUp
    tearDown=fixture.StageFixture.tearDown

    def press(self,key,modifiers=Qt.KeyboardModifier.NoModifier):
        QTest.keyClick(self.c.view,key,modifiers);QTest.qWait(15)

    def select_header(self,card):
        # Isolate the target visually, then exercise the ordinary header click.
        self.header_count=getattr(self,'header_count',0)+1
        at=(4500+(self.header_count%4)*1200,1000+(self.header_count//4)*1200)
        if card.key in self.c.data()['canvases']:
            self.c.commit(lambda s:s['multi_output']['canvases'][card.key].update(position=list(at)))
        else:self.c.commit(lambda s:s.setdefault('text_positions',{}).update({card.key:list(at)}))
        self.c.refresh()
        # Text composition cards are rebuilt during layout changes.
        card=self.c.cards.get(card.key,card)
        view=self.c.view;view.resetTransform();view.scale(.7,.7)
        view.centerOn(card.sceneBoundingRect().center());QTest.qWait(10)
        pos=view.mapFromScene(card.mapToScene(QPointF(80,24)))
        QTest.mouseClick(view.viewport(),Qt.MouseButton.LeftButton,pos=pos);QTest.qWait(10)
        self.assertTrue(card.isSelected(),(card.key,pos,view.viewport().rect(),type(view.itemAt(pos)).__name__,card.scenePos()))

    def test_preview_delete_and_backspace_restore_its_connection(self):
        self.c.functions.add_image(enhanced=False)
        source=next(iter(self.c.functions.cards))
        self.c.commit(lambda s:model.connect(s,source,'__result_preview__','image'))
        for key in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            with self.subTest(key=key):
                before=copy.deepcopy(self.c.data()['connections'])
                self.select_header(self.c.preview_card);self.press(key)
                self.assertFalse(self.w.state['canvas_functions']['preview'])
                self.assertFalse(self.c.preview_card.isVisible())
                self.assertFalse(any(c['destination']=='__result_preview__' for c in self.c.data()['connections']))
                self.press(Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
                self.assertTrue(self.c.preview_card.isVisible())
                self.assertEqual(self.c.data()['connections'],before)
                self.press(Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
                self.assertFalse(self.c.preview_card.isVisible())
                self.press(Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)

    def test_header_selection_after_editor_focus_all_module_kinds(self):
        extra={}
        def setup(s):
            extra['image_input']=add_image_input(s)
            extra['scheduler']=add_scheduler(s)
            extra['stage']=stage_model.add(s)
            extra['reader']=add_text_reader(s,(1500,800))
            model.connect(s,self.clip,extra['stage'],'control')
        self.c.commit(setup)
        self.c.functions.add_image(enhanced=False)
        source=next(k for k,v in self.w.state['canvas_functions']['images'].items() if not v.get('reader'))
        self.c.functions.add_image(enhanced=True)
        collection=next(k for k,v in self.w.state['canvas_functions']['images'].items() if v.get('enhanced'))
        targets=[('preview','__result_preview__'),('clip',self.clip),('output',self.out),('source',source),
                 ('image_source',collection),
                 ('reader',extra['reader']),('image_input',extra['image_input']),('scheduler',extra['scheduler']),
                 ('stage',extra['stage']),('canvas',self.cid),('text','root')]
        for kind,key in targets:
          for delete_key in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            with self.subTest(kind=kind,key=delete_key):
                editor=self.c.outputs[self.out].panel.editor
                QTest.mouseClick(editor.viewport(),Qt.MouseButton.LeftButton)
                editor.setFocus();QTest.qWait(5)
                self.assertIsNotNone(self.c.view.scene().focusItem())
                card=next(c for c in [self.c.preview_card,*self.c.clips.values(),*self.c.outputs.values(),
                                     *self.c.functions.cards.values(),*self.c.flow_cards.values(),
                                     *self.c.containers.values(),*self.c.cards.values()] if c.key==key)
                self.select_header(card)
                before=self.c.history_state()
                self.assertIsNone(self.c.view.scene().focusItem(),kind)
                self.press(delete_key)
                self.assertEqual(self.c.undo_stack[-1][0],before,kind)
                self.assertNotEqual(self.c.history_state(),before,kind)
                self.press(Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(self.c.history_state(),before,kind)
                self.press(Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier)
                self.assertNotEqual(self.c.history_state(),before,kind)
                self.press(Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
                self.assertEqual(self.c.history_state(),before,kind)
                if kind=='text':self.assertEqual(model.owner(self.w.state,key),self.cid)
        self.assertFalse(self.executor.submissions)

    def test_text_editing_keeps_delete_backspace_and_undo_inside_editor(self):
        editor=self.c.outputs[self.out].panel.editor
        self.select_header(self.c.outputs[self.out])
        QTest.mouseClick(editor.viewport(),Qt.MouseButton.LeftButton)
        editor.setFocus();QTest.keyClick(editor,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier)
        QTest.keyClicks(editor,'abc');QTest.keyClick(editor,Qt.Key.Key_Backspace)
        self.assertEqual(editor.toPlainText(),'ab')
        QTest.keyClick(editor,Qt.Key.Key_Home);QTest.keyClick(editor,Qt.Key.Key_Delete)
        self.assertEqual(editor.toPlainText(),'b')
        QTest.keyClick(editor,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(editor.toPlainText(),'ab')
        self.assertIn(self.out,self.c.data()['outputs'])
        self.assertFalse(self.executor.submissions)


if __name__=='__main__':unittest.main()
