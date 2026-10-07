"""Additional bounded Canvas gestures, window modes and saved workspace views."""
import copy
import sys
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF,QRectF
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from stage_fixture import StageFixture,APP
from prompt_calculus_studio import workspace_scene
from prompt_calculus_studio.window import Window


class CanvasRegressionTests(StageFixture):
    def setUp(self):
        self.errors=[]
        hook=patch.object(sys,'excepthook',lambda kind,value,tb:self.errors.append(repr(value)))
        hook.start();self.addCleanup(hook.stop)
        for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
        super().setUp();self.w.state['settings']['reduce_motion']=True

    def tearDown(self):
        APP.clipboard().clear();super().tearDown();self.assertFalse(self.errors,self.errors)

    def drag_rect(self,rect):
        view=self.c.view;view.centerOn(rect.center());QTest.qWait(10)
        a=view.mapFromScene(rect.topLeft());b=view.mapFromScene(rect.bottomRight())
        self.assertTrue(view.viewport().rect().contains(a));self.assertTrue(view.viewport().rect().contains(b))
        QTest.mousePress(view.viewport(),Qt.MouseButton.LeftButton,pos=a)
        QTest.mouseMove(view.viewport(),b,10)
        QTest.mouseRelease(view.viewport(),Qt.MouseButton.LeftButton,pos=b);QTest.qWait(20)

    def test_rubber_selects_function_cards_and_container_scope_selects_members(self):
        stage=next(iter(self.c.data()['stages']))
        old_position=QPointF(self.c.flow_cards[stage].pos())
        self.c.move_selection({self.clip:[5000,5000],stage:[5500,5000]})
        self.assertEqual(self.c.flow_cards[stage].pos(),QPointF(5500,5000))
        self.c.undo();self.assertEqual(self.c.flow_cards[stage].pos(),old_position)
        self.c.redo();self.assertEqual(self.c.flow_cards[stage].pos(),QPointF(5500,5000))
        self.c.view.resetTransform();self.c.view.scale(.6,.6)
        self.drag_rect(QRectF(4960,4960,1010,360))
        self.assertEqual(set(self.c.selection_keys()),{self.clip,stage})
        container=self.c.containers[self.cid]
        self.c.move_selection({'root':[container.x()+50,container.y()+160]})
        root=self.c.cards['root'];self.c.view.scene().clearSelection()
        self.drag_rect(root.sceneBoundingRect().adjusted(-10,-10,10,10))
        self.assertEqual(set(self.c.selection_keys()),{'root'})
        self.assertFalse(self.executor.submissions)

    def test_bulk_drag_carries_container_members_once_and_undoes_atomically(self):
        container=self.c.containers[self.cid]
        self.c.move_selection({self.cid:[4500,4500],'root':[4550,4660],self.clip:[5400,4500]})
        before=copy.deepcopy(self.w.state['text_positions']);canvas_before=list(self.c.data()['canvases'][self.cid]['position'])
        connections=copy.deepcopy(self.c.data()['connections']);stack=len(self.c.undo_stack)
        self.c.view.resetTransform();self.c.view.scale(.7,.7);self.c.view.centerOn(5000,4730);QTest.qWait(10)
        self.c.view.scene().clearSelection();container.setSelected(True);self.c.clips[self.clip].setSelected(True)
        point=self.c.view.mapFromScene(container.mapToScene(QPointF(100,20)));end=point+QPoint(84,49)
        QTest.mousePress(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=point)
        QTest.mouseMove(self.c.view.viewport(),end,10)
        QTest.mouseRelease(self.c.view.viewport(),Qt.MouseButton.LeftButton,pos=end);QTest.qWait(20)
        self.assertEqual(len(self.c.undo_stack),stack+1)
        for key in ('root',self.clip):
            self.assertAlmostEqual(self.w.state['text_positions'][key][0]-before[key][0],120,places=4)
            self.assertAlmostEqual(self.w.state['text_positions'][key][1]-before[key][1],70,places=4)
        self.assertEqual(self.c.data()['canvases'][self.cid]['position'],[canvas_before[0]+120,canvas_before[1]+70])
        self.assertEqual(self.c.data()['connections'],connections)
        self.c.undo();self.assertEqual(self.w.state['text_positions'],before)
        self.assertEqual(self.c.data()['canvases'][self.cid]['position'],canvas_before)
        self.assertFalse(self.executor.submissions)

    def test_fullscreen_and_maximized_survive_editor_navigation(self):
        for show,predicate in ((self.w.showMaximized,self.w.isMaximized),(self.w.showFullScreen,self.w.isFullScreen)):
            show();QTest.qWait(15);self.assertTrue(predicate())
            self.c.view.resetTransform();self.c.view.scale(.73,.73);self.c.view.centerOn(1350,630)
            origin=self.c.view.mapToScene(0,0)
            self.w.set_interface_mode('list');QTest.qWait(15)
            self.assertIs(self.w.surface_stack.currentWidget(),self.w.prompt_editor);self.assertTrue(predicate())
            self.w.enter_canvas();QTest.qWait(15);self.assertTrue(predicate())
            self.assertAlmostEqual(self.c.view.transform().m11(),.73,places=6)
            self.assertLess((self.c.view.mapToScene(0,0)-origin).manhattanLength(),4)

    def test_workspace_switch_and_database_reopen_keep_both_views_and_pending_edit(self):
        original=self.w.state['workspace'];workspace_scene.upgrade(self.w.state)
        other=workspace_scene.create(self.w.state,'other')
        self.c.view.resetTransform();self.c.view.scale(.61,.61);self.c.view.centerOn(780,890)
        self.w.remember_canvas_view();outer=list(self.w.state['canvas_view'])
        self.c.open_editor(self.cid);QTest.qWait(20);editor=self.c.editor_page
        editor.view.resetTransform();editor.view.scale(1.19,1.19);editor.view.centerOn(310,390)
        editor.edit_text('root');editor.prompt_editor.prompt.setPlainText('pending before switching')
        editor.save_view();inside=list(editor.data()['edit_view'])
        self.w.change_workspace(other);QTest.qWait(20)
        self.assertEqual(self.w.state['workspace'],other);self.assertIsNone(self.c.editor_page)
        self.w.change_workspace(original);QTest.qWait(20)
        self.assertEqual(self.w.state['uses']['root']['prompt'],'pending before switching')
        self.assertAlmostEqual(self.c.view.transform().m11(),outer[0],places=6)
        self.assertLess((self.c.view.mapToScene(self.c.view.viewport().rect().center())-QPointF(*outer[1:])).manhattanLength()*outer[0],3)
        self.assertEqual(self.c.data()['canvases'][self.cid]['edit_view'],inside)
        self.w.close();APP.processEvents()
        self.w=Window(self.tmp.name);self.c=self.w.canvas;self.w.show();QTest.qWait(25)
        self.assertEqual(self.w.state['workspace'],original)
        self.assertEqual(self.w.state['uses']['root']['prompt'],'pending before switching')
        self.c.open_editor(self.cid);QTest.qWait(25)
        self.assertAlmostEqual(self.c.editor_page.view.transform().m11(),inside[0],places=6)
        center=self.c.editor_page.view.mapToScene(self.c.editor_page.view.viewport().rect().center())
        self.assertLess((center-QPointF(*inside[1:])).manhattanLength(),4)
