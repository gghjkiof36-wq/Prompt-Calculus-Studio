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

    def box(self,rect,modifiers=Qt.KeyboardModifier.NoModifier,button=Qt.MouseButton.RightButton):
        self.view.centerOn(rect.center());QTest.qWait(5)
        a=self.view.mapFromScene(rect.topLeft());b=self.view.mapFromScene(rect.bottomRight())
        self.drag(a,b,button,modifiers);return b

    def test_left_blank_drag_pans_without_layout_or_undo_changes(self):
        self.view.centerOn(8000,8000);QTest.qWait(5)
        a=self.view.viewport().rect().center();b=a+QPoint(70,42)
        self.assertIsNone(self.view.itemAt(a))
        before=self.view.mapToScene(a);positions=copy.deepcopy(self.w.state['text_positions']);history=len(self.c.undo_stack)
        self.drag(a,b,Qt.MouseButton.LeftButton)
        self.assertLess((self.view.mapToScene(b)-before).manhattanLength(),3)
        self.assertEqual(self.w.state['text_positions'],positions);self.assertEqual(len(self.c.undo_stack),history)
        self.assertIsNone(self.view.interaction.box);self.assertFalse(self.view.rubber.isVisible());self.assertIsNone(self.view.pan)

    def test_ctrl_left_blank_waits_for_threshold_then_selects_without_pan(self):
        self.view.centerOn(8000,8000);QTest.qWait(5)
        point=self.view.viewport().rect().center();before=self.view.mapToScene(point)
        self.c.clips[self.clip].setSelected(True);history=len(self.c.undo_stack)
        QTest.mousePress(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point)
        self.assertIsNone(self.view.pan)
        self.assertEqual(set(self.c.selection_keys()),{self.clip});self.assertFalse(self.view.rubber.isVisible())
        QTest.mouseMove(self.view.viewport(),point+QPoint(1,1),5)
        QTest.mouseRelease(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point+QPoint(1,1))
        self.assertEqual(set(self.c.selection_keys()),{self.clip});self.assertEqual(self.view.mapToScene(point),before)
        self.drag(point,point+QPoint(65,40),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
        self.assertEqual(self.c.selection_keys(),[]);self.assertEqual(self.view.mapToScene(point),before)
        self.assertEqual(len(self.c.undo_stack),history);self.assertFalse(self.view.rubber.isVisible())

    def test_ctrl_left_matches_right_selection_replaces_and_scopes_to_container(self):
        stage=next(iter(self.c.data()['stages']))
        self.c.move_selection({self.clip:[5000,5000],stage:[5800,5000]})
        rect=QRectF(4960,4960,480,320)
        self.box(rect);expected=set(self.c.selection_keys());self.assertEqual(expected,{self.clip})
        self.view.scene().clearSelection();self.c.flow_cards[stage].setSelected(True)
        with patch.object(self.c,'tools_menu') as menu:
            self.box(rect,Qt.KeyboardModifier.ControlModifier,Qt.MouseButton.LeftButton)
            self.assertEqual(set(self.c.selection_keys()),expected);menu.assert_not_called()
        self.assertFalse(self.view.interaction.suppress_context_menu)
        container=self.c.containers[self.cid]
        self.c.move_selection({'root':[container.x()+50,container.y()+160]})
        root=self.c.cards['root'];rect=root.sceneBoundingRect().adjusted(-10,-10,10,10)
        self.c.clips[self.clip].setSelected(True)
        self.box(rect,Qt.KeyboardModifier.ControlModifier,Qt.MouseButton.LeftButton)
        self.assertEqual(set(self.c.selection_keys()),{'root'});self.assertFalse(container.isSelected())

    def test_ctrl_left_from_outer_space_does_not_select_container_children(self):
        self.c.move_selection({self.cid:[4500,4500],'root':[4550,4660]})
        container=self.c.containers[self.cid]
        self.view.resetTransform();self.view.scale(.4,.4)
        rect=container.sceneBoundingRect().adjusted(-30,-30,30,30)
        self.box(rect,Qt.KeyboardModifier.ControlModifier,Qt.MouseButton.LeftButton)
        self.assertEqual(set(self.c.selection_keys()),{self.cid})

    def test_new_marquee_selection_moves_copies_pastes_and_undoes_atomically(self):
        from prompt_calculus_studio.canvas_operations import MIME
        stage=next(iter(self.c.data()['stages']))
        self.c.move_selection({self.clip:[5000,5000],stage:[5500,5000]})
        self.box(QRectF(4960,4960,1010,380),Qt.KeyboardModifier.ControlModifier,Qt.MouseButton.LeftButton)
        self.assertEqual(set(self.c.selection_keys()),{self.clip,stage})
        before=copy.deepcopy(self.w.state['text_positions']);history=len(self.c.undo_stack)
        point=self.point(self.c.clips[self.clip])
        self.drag(point,point+QPoint(70,35),Qt.MouseButton.LeftButton)
        for key in (self.clip,stage):
            self.assertEqual(self.w.state['text_positions'][key],[before[key][0]+100,before[key][1]+50])
        self.assertEqual(len(self.c.undo_stack),history+1)
        QTest.keyClick(self.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        for key in (self.clip,stage):self.assertEqual(self.w.state['text_positions'][key],before[key])
        QTest.keyClick(self.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        QTest.keyClick(self.view,Qt.Key.Key_C,Qt.KeyboardModifier.ControlModifier)
        self.assertTrue(APP.clipboard().mimeData().hasFormat(MIME))
        clips=set(self.c.data()['clip_inputs']);stages=set(self.c.data()['stages']);history=len(self.c.undo_stack)
        self.view.pointer_scene=QPointF(8000,8000)
        QTest.keyClick(self.view,Qt.Key.Key_V,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        self.assertEqual(len(set(self.c.data()['clip_inputs'])-clips),1)
        self.assertEqual(len(set(self.c.data()['stages'])-stages),1)
        self.assertEqual(len(self.c.undo_stack),history+1)
        QTest.keyClick(self.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        self.assertEqual(set(self.c.data()['clip_inputs']),clips);self.assertEqual(set(self.c.data()['stages']),stages)
        QTest.keyClick(self.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        self.assertEqual(len(set(self.c.data()['stages'])-stages),1)

    def test_expanded_text_marquee_matches_right_and_group_move_keeps_outer_positions(self):
        from prompt_calculus_studio.composition import node
        def setup(state):
            state['uses']['second']=node('Second','more')
            canvas=state['multi_output']['canvases'][self.cid]
            canvas['members'].append('second');canvas['edit_positions']={'root':[0,0],'second':[600,0]}
        self.c.commit(setup);outer=copy.deepcopy(self.w.state['text_positions'])
        self.c.clips[self.clip].setSelected(True)
        self.c.open_editor(self.cid);QTest.qWait(15)
        page=self.c.editor_page;self.view=page.view;self.view.resetTransform();self.view.scale(.5,.5)
        def selected():return {i.key for i in self.view.scene().selectedItems() if hasattr(i,'key')}
        rect=page.text_canvas.cards['root'].sceneBoundingRect().adjusted(-15,-15,15,15)
        self.box(rect);self.assertEqual(selected(),{'root'})
        self.view.scene().clearSelection()
        self.box(rect,Qt.KeyboardModifier.ControlModifier,Qt.MouseButton.LeftButton)
        self.assertEqual(selected(),{'root'})
        self.assertIsNone(self.view.pan);self.assertEqual(set(self.c.selection_keys()),{self.clip})
        rect=page.text_canvas.cards['second'].sceneBoundingRect().adjusted(-15,-15,15,15)
        self.box(rect,Qt.KeyboardModifier.ControlModifier);self.assertEqual(selected(),{'root','second'})
        before=copy.deepcopy(page.data()['edit_positions']);history=len(self.c.undo_stack)
        point=self.point(page.text_canvas.cards['root'])
        self.drag(point,point+QPoint(40,25),Qt.MouseButton.LeftButton)
        self.assertEqual(len(self.c.undo_stack),history+1)
        for key in ('root','second'):
            self.assertEqual(page.data()['edit_positions'][key],[before[key][0]+80,before[key][1]+50])
        self.assertEqual(self.w.state['text_positions'],outer)
        QTest.keyClick(self.view,Qt.Key.Key_Z,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        self.assertEqual(page.data()['edit_positions'],before)
        QTest.keyClick(self.view,Qt.Key.Key_Y,Qt.KeyboardModifier.ControlModifier);QTest.qWait(5)
        self.assertEqual(page.data()['edit_positions']['root'],[80,50])
        page.close_editor()

    def test_expanded_short_right_click_menu_survives_both_marquee_entries(self):
        self.c.open_editor(self.cid);QTest.qWait(15);page=self.c.editor_page;self.view=page.view
        self.view.centerOn(8000,8000);QTest.qWait(5)
        point=self.view.viewport().rect().center();end=point+QPoint(60,40)
        with patch('prompt_calculus_studio.composition_editor.RoundMenu.open_at') as menu:
            self.drag(point,end,Qt.MouseButton.RightButton);self.context(end);menu.assert_not_called()
            self.drag(point,end,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
            self.assertFalse(self.view.interaction.suppress_context_menu)
            QTest.mouseClick(self.view.viewport(),Qt.MouseButton.RightButton,pos=end);self.context(end)
            menu.assert_called_once()
        page.close_editor()

    def test_ctrl_resize_and_image_drawing_keep_their_own_gestures(self):
        self.c.commit(lambda state:state['uses']['root'].update(collapsed=False))
        card=self.c.cards['root'];point=self.point(card,QPointF(card.width-8,card.height-8))
        self.assertIs(self.view.interaction.card_at(point),card)
        width=card.width;height=card.height
        self.drag(point,point+QPoint(35,28),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
        self.assertGreater(self.c.cards['root'].width,width);self.assertGreater(self.c.cards['root'].height,height)
        self.assertIsNone(self.view.interaction.box)
        from prompt_calculus_studio.composition_image import document
        self.c.open_editor(self.cid);QTest.qWait(15);page=self.c.editor_page
        page.commit_image(document(600,600));page.show_text.setChecked(False);page.tool='rect'
        self.view=page.view;self.view.resetTransform();self.view.scale(.6,.6);self.view.centerOn(300,300);QTest.qWait(5)
        a=self.view.mapFromScene(QPointF(50,50));b=self.view.mapFromScene(QPointF(200,200))
        self.drag(a,b,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier)
        self.assertIsNone(self.view.interaction.box);self.assertEqual(len(page.image()['layers']),1)
        self.assertEqual(page.image()['layers'][0]['type'],'rect');page.close_editor()

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

    def test_ctrl_click_card_still_adds_and_removes_from_selection(self):
        root=self.c.cards['root'];clip=self.c.clips[self.clip]
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,pos=self.point(root))
        point=self.point(clip)
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point)
        self.assertEqual(set(self.c.selection_keys()),{'root',self.clip})
        QTest.mouseClick(self.view.viewport(),Qt.MouseButton.LeftButton,Qt.KeyboardModifier.ControlModifier,point)
        self.assertEqual(set(self.c.selection_keys()),{'root'});self.assertIsNone(self.view.interaction.box)

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
