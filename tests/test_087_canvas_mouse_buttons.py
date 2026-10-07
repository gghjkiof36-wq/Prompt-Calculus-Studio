"""Actual viewport events for left-pan/right-select; no native desktop or service."""
import copy
import sys
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF,QRectF
from PySide6.QtGui import QContextMenuEvent,QFontDatabase
from PySide6.QtTest import QTest
from stage_fixture import StageFixture,APP


class CanvasMouseButtonTests(StageFixture):
    def setUp(self):
        self.errors=[]
        hook=patch.object(sys,'excepthook',lambda kind,value,tb:self.errors.append(repr(value)))
        hook.start();self.addCleanup(hook.stop)
        for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
        super().setUp();self.w.state['settings']['reduce_motion']=True
        self.view=self.c.view;self.view.resetTransform();self.view.scale(.7,.7)

    def tearDown(self):
        self.assertFalse(self.executor.submissions)
        APP.clipboard().clear();super().tearDown();self.assertFalse(self.errors,self.errors)

    def drag(self,a,b,button,modifiers=Qt.KeyboardModifier.NoModifier):
        for point in (a,b):self.assertTrue(self.view.viewport().rect().contains(point),point)
        QTest.mousePress(self.view.viewport(),button,modifiers,a)
        QTest.mouseMove(self.view.viewport(),b,10)
        QTest.mouseRelease(self.view.viewport(),button,modifiers,b);QTest.qWait(15)

    def context(self,point,reason=QContextMenuEvent.Reason.Mouse):
        event=QContextMenuEvent(reason,point,self.view.viewport().mapToGlobal(point))
        APP.sendEvent(self.view.viewport(),event);APP.processEvents()

    def point(self,card,local=QPointF(70,20)):
        self.view.centerOn(card);QTest.qWait(5)
        return self.view.mapFromScene(card.mapToScene(local))

    def box(self,rect,modifiers=Qt.KeyboardModifier.NoModifier):
        self.view.centerOn(rect.center());QTest.qWait(5)
        a=self.view.mapFromScene(rect.topLeft());b=self.view.mapFromScene(rect.bottomRight())
        self.drag(a,b,Qt.MouseButton.RightButton,modifiers);return b

    def test_left_blank_drag_pans_without_layout_or_undo_changes(self):
        self.view.centerOn(8000,8000);QTest.qWait(5)
        a=self.view.viewport().rect().center();b=a+QPoint(70,42)
        self.assertIsNone(self.view.itemAt(a))
        before=self.view.mapToScene(a);positions=copy.deepcopy(self.w.state['text_positions']);history=len(self.c.undo_stack)
        self.drag(a,b,Qt.MouseButton.LeftButton)
        self.assertLess((self.view.mapToScene(b)-before).manhattanLength(),3)
        self.assertEqual(self.w.state['text_positions'],positions);self.assertEqual(len(self.c.undo_stack),history)
        self.assertIsNone(self.view.interaction.box);self.assertFalse(self.view.rubber.isVisible());self.assertIsNone(self.view.pan)
        self.c.clips[self.clip].setSelected(True)
        self.drag(a,b,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(set(self.c.selection_keys()),{self.clip});self.assertIsNone(self.view.box_start)

    def test_container_empty_body_pans_even_with_selected_group(self):
        container=self.c.containers[self.cid]
        point=self.point(container,QPointF(container.width-70,container.height-90))
        self.assertIs(self.view.itemAt(point),container)
        container.setSelected(True);self.c.clips[self.clip].setSelected(True)
        positions=copy.deepcopy(self.w.state['text_positions']);data=copy.deepcopy(self.c.data());history=len(self.c.undo_stack)
        before=self.view.mapToScene(point);end=point+QPoint(-63,-35)
        self.drag(point,end,Qt.MouseButton.LeftButton)
        self.assertLess((self.view.mapToScene(end)-before).manhattanLength(),3)
        self.assertEqual(self.c.data(),data);self.assertEqual(self.w.state['text_positions'],positions)
        self.assertEqual(len(self.c.undo_stack),history);self.assertIsNone(self.view.interaction.group)

    def test_right_drag_selects_and_ctrl_adds_without_opening_release_menu(self):
        stage=next(iter(self.c.data()['stages']))
        self.c.move_selection({self.clip:[5000,5000],stage:[5800,5000]});history=len(self.c.undo_stack)
        with patch.object(self.c,'tools_menu') as tools:
            end=self.box(QRectF(4960,4960,480,320))
            self.assertEqual(set(self.c.selection_keys()),{self.clip})
            self.context(end);tools.assert_not_called()
            end=self.box(QRectF(5760,4960,500,380),Qt.KeyboardModifier.ControlModifier)
            self.assertEqual(set(self.c.selection_keys()),{self.clip,stage})
            self.context(end);tools.assert_not_called()
            QTest.mouseClick(self.view.viewport(),Qt.MouseButton.RightButton,pos=end)
            self.context(end);tools.assert_called_once()
        self.assertEqual(len(self.c.undo_stack),history)

    def test_right_click_card_selects_and_preserves_its_context_menu(self):
        card=self.c.cards['root'];point=self.point(card)
        self.c.clips[self.clip].setSelected(True)
        with patch.object(self.c,'context') as menu:
            QTest.mouseClick(self.view.viewport(),Qt.MouseButton.RightButton,pos=point)
            self.context(point)
            menu.assert_called_once();self.assertEqual(menu.call_args.args[0],'root')
        self.assertEqual(set(self.c.selection_keys()),{'root'})

    def test_keyboard_context_menu_is_not_suppressed_after_right_drag(self):
        self.view.centerOn(8000,8000);QTest.qWait(5)
        a=self.view.viewport().rect().center();b=a+QPoint(60,40)
        self.drag(a,b,Qt.MouseButton.RightButton)
        with patch.object(self.c,'tools_menu') as menu:
            self.context(b,QContextMenuEvent.Reason.Keyboard);menu.assert_called_once()

    def test_ctrl_click_card_control_changes_weight_not_selection_or_group(self):
        card=self.c.cards['root'];point=self.point(card,card.actions()['plus'].center())
        card.setSelected(True);self.c.clips[self.clip].setSelected(True)
        before=self.w.state['uses']['root']['weight'];position=QPointF(card.pos())
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point);QTest.qWait(10)
        self.assertEqual(self.w.state['uses']['root']['weight'],before+1)
        self.assertEqual(self.c.cards['root'].pos(),position);self.assertIsNone(self.view.interaction.group)
        self.c.undo();self.assertEqual(self.w.state['uses']['root']['weight'],before)

    def test_left_card_drag_moves_and_undo_redo_restore_exact_positions(self):
        card=self.c.clips[self.clip];point=self.point(card);end=point+QPoint(70,35)
        before=QPointF(card.pos());history=len(self.c.undo_stack)
        self.drag(point,end,Qt.MouseButton.LeftButton)
        expected=before+QPointF(100,50)
        self.assertLess((self.c.clips[self.clip].pos()-expected).manhattanLength(),.01)
        self.assertEqual(len(self.c.undo_stack),history+1)
        self.c.undo();self.assertEqual(self.c.clips[self.clip].pos(),before)
        self.c.redo();self.assertLess((self.c.clips[self.clip].pos()-expected).manhattanLength(),.01)

    def test_embedded_text_and_button_own_viewport_clicks(self):
        card=self.c.outputs[self.out];panel=card.panel;editor=panel.editor
        self.view.centerOn(card);QTest.qWait(5)
        def point(widget):
            local=widget.mapTo(panel,widget.rect().center())
            return self.view.mapFromScene(card.proxy.mapToScene(QPointF(local)))
        edit_point=point(editor.viewport());before=QPointF(card.pos())
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,edit_point)
        self.assertTrue(editor.hasFocus());self.assertIsNone(self.view.box_start);self.assertIsNone(self.view.interaction.box)
        QTest.keyClick(editor,Qt.Key.Key_A,Qt.KeyboardModifier.ControlModifier);QTest.keyClicks(editor,'manual text')
        self.assertEqual(self.c.data()['outputs'][self.out]['draft'],'manual text')
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=point(panel.preview_toggle));QTest.qWait(5)
        self.assertTrue(self.c.data()['outputs'][self.out]['preview_collapsed'])
        self.assertEqual(card.pos(),before);self.assertIsNone(self.view.pan)

    def test_ports_keep_connection_and_context_operations_with_selected_cards(self):
        port=next(p for p in self.c.ports.values() if p.key==self.clip and p.output)
        self.view.centerOn(port.scenePos());QTest.qWait(5);point=self.view.mapFromScene(port.scenePos())
        self.c.clips[self.clip].setSelected(True);self.c.cards['root'].setSelected(True)
        self.assertIsNone(self.view.interaction.card_at(point))
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point)
        self.assertIs(self.c.connection_gesture.anchor,port);self.assertIsNone(self.view.interaction.group)
        self.c.connection_gesture.cancel()
        with patch.object(self.c.connection_gesture,'menu') as menu:
            QTest.mouseClick(self.view.viewport(),Qt.MouseButton.RightButton,pos=point)
            self.context(point);menu.assert_called_once();self.assertIs(menu.call_args.args[1],port)

    def test_double_click_blank_container_and_card_retain_original_actions(self):
        self.view.centerOn(8000,8000);QTest.qWait(5);point=self.view.viewport().rect().center()
        with patch.object(self.c,'function_palette') as palette:
            QTest.mouseDClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(5)
            palette.assert_called_once()
        container=self.c.containers[self.cid];point=self.point(container,QPointF(container.width-70,container.height-90))
        with patch.object(self.c,'quick_text') as quick:
            QTest.mouseDClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(5)
            quick.assert_called_once();self.assertEqual(quick.call_args.args[0],self.cid)
        point=self.point(self.c.cards['root'])
        with patch.object(self.c,'edit') as edit:
            QTest.mouseDClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=point);QTest.qWait(5)
            edit.assert_called_once_with('root')
