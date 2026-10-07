"""Independent Canvas review regressions using synthetic/offscreen Qt only."""
import copy
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF
from PySide6.QtTest import QTest

from stage_fixture import StageFixture,APP
from prompt_calculus_studio.composition import node
from prompt_calculus_studio.flow_data import add_scheduler


class CanvasReviewTests(StageFixture):
    def tearDown(self):
        APP.clipboard().clear()
        super().tearDown()

    def test_container_and_nested_selection_must_move_only_once(self):
        def setup(s):
            s['uses']['root'].update(collapsed=False,children=[node('nested','detail')])
        self.c.commit(setup)
        self.c.move_selection({self.cid:[4500,4500],'root':[4550,4660]})
        container=self.c.containers[self.cid]
        child=next(card for key,card in self.c.cards.items() if key.startswith('root:'))
        self.c.view.resetTransform();self.c.view.scale(.7,.7);self.c.view.centerOn(4900,4730);QTest.qWait(10)
        self.c.view.scene().clearSelection();container.setSelected(True);child.setSelected(True)
        point=self.c.view.mapFromScene(container.mapToScene(QPointF(100,20)));child_key=child.key
        before=QPointF(child.scenePos());relative=QPointF(child.pos());stack=len(self.c.undo_stack)
        QTest.mousePress(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        group=self.c.view.interaction.group
        self.assertIsNotNone(group)
        keys=set(group[1])
        self.assertNotIn(child.key,keys,'child remains in moving set alongside the auto-added root: '+repr(keys))
        QTest.mouseMove(self.c.view.viewport(),point+QPoint(70,35),10)
        QTest.mouseRelease(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point+QPoint(70,35));QTest.qWait(10)
        moved=self.c.cards[child_key]
        self.assertEqual(moved.scenePos(),before+QPointF(100,50))
        self.assertEqual(moved.pos(),relative);self.assertEqual(len(self.c.undo_stack),stack+1)
        self.c.undo();self.assertEqual(self.c.cards[child_key].scenePos(),before)

    def test_failed_group_drag_restores_visible_position(self):
        stage=next(iter(self.c.data()['stages']))
        self.c.move_selection({self.clip:[4500,4500],stage:[5000,4500]})
        self.c.view.resetTransform();self.c.view.scale(.7,.7);self.c.view.centerOn(4950,4700);QTest.qWait(10)
        self.c.view.scene().clearSelection();self.c.clips[self.clip].setSelected(True);self.c.flow_cards[stage].setSelected(True)
        old_state=copy.deepcopy(self.w.state);before=QPointF(self.c.clips[self.clip].pos())
        point=self.c.view.mapFromScene(self.c.clips[self.clip].mapToScene(QPointF(90,20)))
        with patch('prompt_calculus_studio.content_history.record',side_effect=ValueError('synthetic rejection')):
            QTest.mousePress(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
            QTest.mouseMove(self.c.view.viewport(),point+QPoint(70,35),10)
            QTest.mouseRelease(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point+QPoint(70,35));QTest.qWait(10)
        self.assertEqual(self.w.state,old_state)
        self.assertEqual(self.c.clips[self.clip].pos(),before,'data stayed intact but card retained failed drag position')

    def test_shadow_only_rubber_rect_does_not_select_function_card(self):
        stage=next(iter(self.c.data()['stages']))
        self.c.move_selection({stage:[5000,5000]})
        card=self.c.flow_cards[stage];view=self.c.view
        view.resetTransform();view.scale(2,2);view.centerOn(card);QTest.qWait(10)
        self.c.view.scene().clearSelection()
        a=view.mapFromScene(card.mapToScene(QPointF(40,card.height+2)))
        b=view.mapFromScene(card.mapToScene(QPointF(80,card.height+6)))
        self.assertIsNone(view.itemAt(a))
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a)
        QTest.mouseMove(view.viewport(),b,10)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b)
        self.assertFalse(card.isSelected(),'a rectangle wholly below the card body selected its shadow margin')

    def test_failed_delete_does_not_detach_scheduler(self):
        ids=[];self.c.commit(lambda s:ids.append(add_scheduler(s)))
        self.c.view.scene().clearSelection();self.c.flow_cards[ids[0]].setSelected(True)
        before=copy.deepcopy(self.w.state)
        with patch.object(self.w.comfy.input_flow,'detach') as detach,patch('prompt_calculus_studio.content_history.record',side_effect=ValueError('synthetic rejection')):
            self.c.delete_selected()
        self.assertEqual(self.w.state,before)
        detach.assert_not_called()

    def test_successful_delete_detaches_only_after_document_commit(self):
        ids=[];self.c.commit(lambda s:ids.append(add_scheduler(s)))
        key=ids[0];self.c.view.scene().clearSelection();self.c.flow_cards[key].setSelected(True)
        seen=[]
        with patch.object(self.w.comfy.input_flow,'detach',side_effect=lambda owner:seen.append((owner,owner in self.c.data()['schedulers']))):
            self.c.delete_selected()
        self.assertEqual(seen,[(key,False)])
        self.assertNotIn(key,self.c.data()['schedulers'])
        self.c.undo();self.assertIn(key,self.c.data()['schedulers'])
        self.assertFalse(self.executor.submissions)

    def test_failed_nested_drag_restores_parent_child_geometry_and_state(self):
        self.c.commit(lambda s:s['uses']['root'].update(collapsed=False,children=[node('nested','detail')]))
        self.c.move_selection({self.cid:[4500,4500],'root':[4550,4660]})
        container=self.c.containers[self.cid];child=next(c for k,c in self.c.cards.items() if k.startswith('root:'))
        self.c.view.resetTransform();self.c.view.scale(.7,.7);self.c.view.centerOn(4900,4730);QTest.qWait(10)
        self.c.view.scene().clearSelection();container.setSelected(True);child.setSelected(True)
        before=copy.deepcopy(self.w.state)
        geometry={k:(QPointF(c.scenePos()),QPointF(c.pos()),c.width,c.height) for k,c in self.c.cards.items()}
        container_pos=QPointF(container.pos());point=self.c.view.mapFromScene(container.mapToScene(QPointF(100,20)))
        with patch('prompt_calculus_studio.content_history.record',side_effect=ValueError('synthetic rejection')):
            QTest.mousePress(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
            QTest.mouseMove(self.c.view.viewport(),point+QPoint(70,35),10)
            QTest.mouseRelease(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point+QPoint(70,35));QTest.qWait(10)
        self.assertEqual(self.w.state,before);self.assertEqual(container.pos(),container_pos)
        for k,c in self.c.cards.items():self.assertEqual((c.scenePos(),c.pos(),c.width,c.height),geometry[k])

    def test_clean_visible_editor_follows_undo_redo(self):
        self.w.edit_canvas_material('root');QTest.qWait(10);editor=self.w.material_sidebar
        before=self.w.state['uses']['root']['prompt']
        self.c.commit(lambda s:s['uses']['root'].update(prompt='edited through document'))
        self.assertEqual(editor.prompt.toPlainText(),'edited through document')
        self.c.undo();self.assertEqual(editor.prompt.toPlainText(),before)
        self.c.redo();self.assertEqual(editor.prompt.toPlainText(),'edited through document')
        self.assertFalse(editor.dirty);self.assertFalse(self.executor.submissions)


if __name__=='__main__':unittest.main(verbosity=2)
