"""Actual Qt gestures and transaction failures with synthetic/offline data."""
import copy
import sys
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from stage_fixture import StageFixture, APP
from prompt_calculus_studio.canvas_operations import MIME
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.canvas_exclusions import affected_nodes
from prompt_calculus_studio import materials


class CanvasInteractionTests(StageFixture):
    def setUp(self):
        self.errors=[]
        hook=patch.object(sys,'excepthook',lambda *args:self.errors.append(str(args[1])))
        hook.start();self.addCleanup(hook.stop)
        super().setUp()
        self.c.view.resetTransform();self.c.view.scale(.8,.8)

    def tearDown(self):
        # The offscreen platform has no OS clipboard owner after QApplication
        # teardown. Release its synthetic MIME object while Qt is still alive.
        QApplication.clipboard().clear()
        super().tearDown()
        self.assertFalse(self.errors,self.errors)

    def point(self,card,x=70,y=18):
        self.c.view.centerOn(card);QTest.qWait(5)
        return self.c.view.mapFromScene(card.mapToScene(QPointF(x,y)))

    def test_focused_function_clipboard_and_single_undo(self):
        key=next(iter(self.c.data()['stages']));card=self.c.flow_cards[key]
        point=self.point(card)
        QTest.mouseClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        self.assertTrue(card.isSelected())
        QTest.keyClick(self.c.view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(QApplication.clipboard().mimeData().hasFormat(MIME))
        before=set(self.c.data()['stages']);self.c.view.pointer_scene=QPointF(8000,1200)
        QTest.keyClick(self.c.view,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier);QTest.qWait(15)
        self.assertEqual(len(set(self.c.data()['stages'])-before),1,self.notices)
        QTest.keyClick(self.c.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        self.assertEqual(set(self.c.data()['stages']),before,self.notices)
        self.assertFalse(self.executor.submissions)

    def test_ctrl_toggle_and_plain_single_then_atomic_delete(self):
        root=self.c.cards['root'];clip=self.c.clips[self.clip]
        QTest.mouseClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point(root))
        QTest.mouseClick(self.c.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,self.point(clip))
        self.assertEqual(set(self.c.selection_keys()),{'root',self.clip})
        QTest.keyClick(self.c.view,Qt.Key.Key_Delete);QTest.qWait(10)
        self.assertNotIn('root',self.w.state['uses']);self.assertNotIn(self.clip,self.c.data()['clip_inputs'])
        self.c.undo();QTest.qWait(5)
        self.assertIn('root',self.w.state['uses']);self.assertIn(self.clip,self.c.data()['clip_inputs'])
        self.assertFalse(self.executor.submissions)

    def test_embedded_editor_copy_delete_undo_remain_text_operations(self):
        panel=self.c.outputs[self.out].panel;editor=panel.editor
        self.c.view.centerOn(self.c.outputs[self.out]);editor.setFocus();editor.setPlainText('private draft')
        editor.selectAll();QTest.keyClick(editor,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(QApplication.clipboard().text(),'private draft')
        QTest.keyClick(editor,Qt.Key.Key_Backspace)
        self.assertIn(self.out,self.c.data()['outputs']);self.assertEqual(self.c.data()['outputs'][self.out]['draft'],'')

    def test_failed_history_validation_does_not_partially_commit(self):
        before=copy.deepcopy(self.w.state);stack=list(self.c.undo_stack)
        with patch('prompt_calculus_studio.content_history.record',side_effect=ValueError('journal rejected')):
            self.assertFalse(self.c.commit(lambda state:state['uses']['root'].update(prompt='must not stick')))
        self.assertEqual(self.w.state,before);self.assertEqual(self.c.undo_stack,stack)

    def test_exclusions_all_partial_child_and_reversible_originals(self):
        def setup(state):
            state['uses']['root'].update(prompt='red, blue',children=[node('child','red')])
            state['uses']['block']=node('block','',excludes=['red'])
            state['multi_output']['canvases'][self.cid]['members'].append('block')
        self.c.commit(setup);status=affected_nodes(self.w.state)
        self.assertFalse(status['root']['all'])
        child=self.w.state['uses']['root']['children'][0]
        self.assertTrue(status['root:'+child['id']]['all'])
        self.c.commit(lambda state:state['uses']['block']['excludes'].append('blue'))
        self.assertTrue(affected_nodes(self.w.state)['root']['all'])
        self.assertEqual(self.w.state['uses']['root']['prompt'],'red, blue')
        self.c.undo();self.assertFalse(affected_nodes(self.w.state)['root']['all'])

    def test_drop_material_creates_independent_collapsed_copy_with_provenance(self):
        asset=dict(name='asset',prompt='smile',module=self.w.state['modules'][0]['id'])
        ids=[];self.c.commit(lambda state:ids.append(materials.save(state,asset)))
        original=copy.deepcopy(materials.item(self.w.state,ids[0]));before=set(self.w.state['uses'])
        self.assertTrue(self.w.add_material_to_canvas(ids[0],canvas_id=self.cid))
        key=(set(self.w.state['uses'])-before).pop();value=self.w.state['uses'][key]
        self.assertTrue(value['collapsed']);self.assertTrue(value['source_revision'])
        self.c.commit(lambda state:materials.edit_canvas(state,key,dict(prompt='new copy')))
        self.assertEqual(materials.item(self.w.state,ids[0])['prompt'],original['prompt'])
        self.c.undo();self.assertEqual(self.w.state['uses'][key]['prompt'],'smile')

    def test_editor_transition_preserves_canvas_position_and_maximized_state(self):
        self.c.view.resetTransform();self.c.view.scale(.75,.75);self.c.view.centerOn(450,280)
        self.w.showMaximized();QTest.qWait(10)
        self.w.remember_canvas_view();saved=list(self.w.state['canvas_view'])
        origin=self.c.view.mapToScene(QPoint(0,0))
        self.w.set_interface_mode('list');QTest.qWait(5)
        self.assertIs(self.w.surface_stack.currentWidget(),self.w.prompt_editor)
        self.assertEqual(self.w.state['selection_view'],'canvas')
        self.assertTrue(self.w.isMaximized())
        self.w.enter_canvas();QTest.qWait(10);self.assertTrue(self.w.isMaximized())
        self.assertAlmostEqual(self.c.view.transform().m11(),saved[0],places=4)
        point=self.c.view.mapToScene(QPoint(0,0))
        self.assertLess((point-origin).manhattanLength(),5)

    def test_wheel_anchors_current_cursor_even_after_different_context_point(self):
        view=self.c.view;view.centerOn(5000,5000);QTest.qWait(5)
        view.pointer_scene=QPointF(-999,-999);point=QPoint(100,110);before=view.mapToScene(point)
        event=QWheelEvent(QPointF(point),QPointF(view.viewport().mapToGlobal(point)),QPoint(),QPoint(0,120),
                          Qt.MouseButton.NoButton,Qt.KeyboardModifier.NoModifier,Qt.ScrollPhase.NoScrollPhase,False)
        QApplication.sendEvent(view.viewport(),event)
        after=view.mapToScene(point)
        self.assertLess((after-before).manhattanLength(),4)
        self.assertFalse(self.executor.submissions)

    def test_binding_outside_closes_without_applying_and_inside_preserves_dialog(self):
        from prompt_calculus_studio.clip_widgets import ClipBindingDialog
        before=copy.deepcopy(self.c.data()['bindings'])
        dialog=ClipBindingDialog(self.c,self.clip);dialog.show();QTest.qWait(10)
        QTest.mouseClick(dialog.target,Qt.MouseButton.LeftButton)
        QTest.qWait(5);self.assertTrue(dialog.isVisible())
        dialog.target.hidePopup()
        point=QPoint(3,3)
        self.assertFalse(dialog.frameGeometry().contains(self.w.mapToGlobal(point)))
        QTest.mouseClick(self.w,Qt.MouseButton.LeftButton,pos=point);QTest.qWait(5)
        self.assertFalse(dialog.isVisible());self.assertTrue(dialog.closed)
        self.assertEqual(self.c.data()['bindings'],before)
        self.assertFalse(self.executor.submissions)
        dialog.deleteLater()

    def test_hidden_wires_refresh_immediately_and_persist_without_graph_changes(self):
        from prompt_calculus_studio.dialogs import SettingsDialog
        before=copy.deepcopy(self.c.data());self.assertTrue(self.c.lines)
        dialog=SettingsDialog(self.w)
        dialog.hide_connections.setChecked(True);self.assertTrue(dialog.save(accept=False))
        self.assertTrue(all(not line.isVisible() for line in self.c.lines.values()))
        self.assertEqual(self.c.data(),before)
        self.w.store.save(self.w.state)
        self.assertTrue(self.w.store.load()['settings']['hide_connections'])
        dialog.hide_connections.setChecked(False);self.assertTrue(dialog.save(accept=False))
        self.assertTrue(all(line.isVisible() for line in self.c.lines.values()))
        self.assertEqual(self.c.data(),before);self.assertFalse(self.executor.submissions)
        dialog.reject();dialog.deleteLater()

    def test_shadow_is_paint_only_not_a_click_target(self):
        for card in (self.c.cards['root'],self.c.outputs[self.out],self.c.flow_cards[next(iter(self.c.data()['stages']))]):
            shadow=QPointF(card.width+2,card.height/2)
            self.assertTrue(card.boundingRect().contains(shadow))
            self.assertFalse(card.shape().contains(shadow))
