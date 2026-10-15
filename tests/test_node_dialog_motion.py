"""Centered disclosures animate a child surface, never the native window."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt,QRect,QPoint,QEvent,QObject,QTimer
from PySide6.QtGui import QScreen
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

import test_prompt_group_ui as prior
from prompt_calculus_studio import composition as comp,prompt_groups as groups,materials
from prompt_calculus_studio.text_canvas import NodeDialog


class NodeDialogMotionTests(unittest.TestCase):
    setUpClass=classmethod(prior.GroupUITests.setUpClass.__func__)

    def setUp(self):
        self.screen=patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1200))
        self.screen.start();prior.GroupUITests.setUp(self)
        self.w.resize(1440,1000);self.w.state['settings']['reduce_motion']=False

    def tearDown(self):
        try:prior.GroupUITests.tearDown(self)
        finally:self.screen.stop()

    def flow(self):
        first=groups.new_group('庭園','garden, smile');second=groups.new_group('海灘','beach, daylight')
        root=comp.node('流程 A');root['sequence']=dict(version=1,mode='increment',selected=first['id'],start=first['id'],groups=[first,second])
        return root

    def settle(self,dialog):
        for _ in range(30):
            if dialog.presentation.motion.state()==dialog.presentation.motion.State.Stopped:
                self.app.processEvents();return
            QTest.qWait(10)
        self.fail('disclosure motion did not finish')

    def assert_centered(self,dialog):
        sheet=dialog.presentation.surface
        center=sheet.mapToGlobal(sheet.rect().center())
        self.assertLessEqual((center-dialog.presentation.bounds().center()).manhattanLength(),2)
        self.assertTrue(dialog.rect().contains(sheet.geometry()))

    def test_new_text_and_flow_first_paint_is_final_centered_without_reveal(self):
        for value,title in ((None,'新增畫布模組'),(comp.node('文字','smile'),'編輯文字模組'),(self.flow(),'編輯當輪')):
            dialog=NodeDialog(self.w,value);frames=[]
            class Probe(QObject):
                def eventFilter(probe,widget,event):
                    if event.type()==QEvent.Type.Paint:
                        frames.append((QRect(dialog.geometry()),QRect(dialog.presentation.surface.geometry())))
                    return False
            probe=Probe(dialog);dialog.installEventFilter(probe)
            dialog.show();QTest.qWait(60)
            self.assertEqual(dialog.windowTitle(),title);self.assertTrue(frames)
            self.assertEqual(frames[0],(dialog.geometry(),dialog.presentation.surface.geometry()))
            self.assert_centered(dialog);self.assertEqual(dialog.windowOpacity(),1.)
            self.assertEqual(dialog.reveal.state(),dialog.reveal.State.Stopped)
            self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0)
            dialog.reject()

    def test_expand_and_collapse_keep_native_window_fixed_and_restore_surface(self):
        for root in (comp.node('文字','smile'),self.flow()):
            dialog=NodeDialog(self.w,root);dialog.show();QTest.qWait(30)
            sheet=dialog.presentation.surface;closed=QRect(sheet.geometry());native=QRect(dialog.geometry())
            events=[];frames=[]
            class Probe(QObject):
                def eventFilter(probe,widget,event):
                    if event.type() in (QEvent.Type.Move,QEvent.Type.Resize):events.append(event.type())
                    if event.type()==QEvent.Type.Paint:frames.append(sheet.isVisible())
                    return False
            probe=Probe(dialog);dialog.installEventFilter(probe)
            positions=[];dialog.presentation.motion.valueChanged.connect(lambda _:positions.append(QRect(sheet.geometry())))
            for _ in range(3):
                QTest.mouseClick(dialog.excludes_toggle,Qt.MouseButton.LeftButton)
                self.assertEqual(sheet.geometry(),closed)
                self.settle(dialog);self.assertGreater(sheet.height(),closed.height())
                self.assertLess(sheet.y(),closed.y());self.assert_centered(dialog)
                self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0)
                QTest.mouseClick(dialog.excludes_toggle,Qt.MouseButton.LeftButton);self.settle(dialog)
                self.assertEqual(sheet.geometry(),closed);self.assertEqual(dialog.geometry(),native)
            self.assertEqual(events,[]);self.assertTrue(frames);self.assertTrue(all(frames));self.assertTrue(positions)
            self.assertTrue(all(abs(rect.center().y()-closed.center().y())<=1 for rect in positions))
            dialog.reject()

    def test_rapid_reversal_continues_from_current_geometry_and_last_toggle_wins(self):
        dialog=NodeDialog(self.w,self.flow());dialog.show();QTest.qWait(30)
        sheet=dialog.presentation.surface;closed=QRect(sheet.geometry());native=QRect(dialog.geometry())
        cursor=dialog.prompt.textCursor();cursor.setPosition(2);cursor.setPosition(8,cursor.MoveMode.KeepAnchor)
        dialog.prompt.setTextCursor(cursor);selection=(cursor.position(),cursor.anchor(),cursor.selectedText())
        for opened,delay in ((True,45),(False,30),(True,25),(False,10),(True,40)):
            before=QRect(sheet.geometry());dialog.excludes_toggle.setChecked(opened)
            self.assertEqual(sheet.geometry(),before);QTest.qWait(delay)
            current=dialog.prompt.textCursor()
            self.assertEqual((current.position(),current.anchor(),current.selectedText()),selection)
        self.settle(dialog);self.assertTrue(dialog.excludes_panel.isVisible());self.assert_centered(dialog)
        dialog.excludes_toggle.setChecked(False);self.settle(dialog)
        self.assertEqual(sheet.geometry(),closed);self.assertEqual(dialog.geometry(),native);dialog.reject()

    def test_first_disclosure_reveals_only_with_sheet_without_scroll_or_content_jump(self):
        for value in (comp.node('文字','smile'),self.flow()):
            dialog=NodeDialog(self.w,value);dialog.show();QTest.qWait(20)
            closed=QRect(dialog.presentation.surface.geometry());native=QRect(dialog.geometry())
            panel=dialog.excludes_panel;prompt_height=dialog.prompt.height();frames=[]
            dialog.presentation.motion.valueChanged.connect(lambda _,d=dialog,p=panel:frames.append(
                (p.height(),d.form_scroll.verticalScrollBar().maximum(),d.prompt.height())))
            dialog.excludes_toggle.setChecked(True)
            self.assertEqual(panel.height(),0,'The first paint must not contain a full-height panel in a collapsed sheet')
            self.assertEqual(dialog.presentation.surface.geometry(),closed)
            self.settle(dialog)
            self.assertTrue(frames);self.assertEqual([f[0] for f in frames],sorted(f[0] for f in frames))
            self.assertTrue(all(f[1]==0 for f in frames),frames)
            self.assertTrue(all(abs(f[2]-prompt_height)<=2 for f in frames),frames)
            self.assertEqual(dialog.geometry(),native)
            dialog.excludes_toggle.setChecked(False);self.settle(dialog)
            self.assertEqual(dialog.presentation.surface.geometry(),closed);dialog.reject()

    def test_small_parent_resize_reduced_motion_and_error_keep_footer_reachable(self):
        dialog=NodeDialog(self.w,self.flow());dialog.show();QTest.qWait(30)
        dialog.excludes_toggle.setChecked(True);QTest.qWait(40)
        self.w.resize(980,560);QTest.qWait(30)
        self.assertEqual(dialog.presentation.motion.state(),dialog.presentation.motion.State.Stopped)
        self.assert_centered(dialog);self.assertTrue(dialog.presentation.bounds().contains(dialog.geometry()))
        self.assertGreater(dialog.form_scroll.verticalScrollBar().maximum(),0)
        save=next(button for button in dialog.findChildren(QPushButton) if button.text()=='套用')
        self.assertTrue(dialog.presentation.surface.rect().contains(save.mapTo(dialog.presentation.surface,save.rect().center())))
        dialog.name.clear();dialog.save();self.assertTrue(dialog.error.isVisible());self.assertTrue(dialog.isVisible())
        self.w.state['settings']['reduce_motion']=True
        for opened in (False,True,False):
            dialog.excludes_toggle.setChecked(opened)
            self.assertEqual(dialog.presentation.motion.state(),dialog.presentation.motion.State.Stopped)
            self.assertEqual(dialog.excludes_panel.isVisible(),opened);self.assert_centered(dialog)
        self.w.resize(1440,1000);QTest.qWait(20)
        self.assertEqual(dialog.form_scroll.verticalScrollBar().maximum(),0);self.assert_centered(dialog);dialog.reject()

    def test_close_during_motion_stops_callbacks_and_reopen_preserves_text_undo(self):
        original=comp.node('文字','smile');saved=copy.deepcopy(original)
        dialog=NodeDialog(self.w,original);dialog.show();QTest.qWait(30)
        dialog.prompt.insertPlainText('bright, ');dialog.excludes_toggle.setChecked(True)
        dialog.excludes.setPlainText('shadow');QTest.qWait(40)
        dialog.reject();geometry=QRect(dialog.presentation.surface.geometry());QTest.qWait(220)
        self.assertEqual(dialog.presentation.motion.state(),dialog.presentation.motion.State.Stopped)
        self.assertEqual(dialog.presentation.surface.geometry(),geometry);self.assertEqual(original,saved)
        dialog.show();QTest.qWait(30);self.assert_centered(dialog)
        self.assertEqual(dialog.excludes.toPlainText(),'shadow');dialog.prompt.undo()
        self.assertEqual(dialog.prompt.toPlainText(),'smile');dialog.prompt.redo()
        self.assertEqual(dialog.prompt.toPlainText(),'bright, smile');dialog.reject()

    def test_canvas_save_reopen_undo_and_cancel_preserve_draft_ownership(self):
        canvas=self.w.canvas;target=next(iter(self.w.state['multi_output']['canvases']));ids=[]
        canvas.commit(lambda state:ids.append(materials.add_to_canvas(state,self.ids[0],target)))
        ident=ids[0];key=next(key for key,value in canvas.entries.items() if value[0]==ident)
        original=copy.deepcopy(self.w.state['uses'][ident])
        def save():
            dialog=canvas.active_node_dialog;dialog.prompt.setPlainText('edited prompt')
            dialog.excludes_toggle.setChecked(True);dialog.excludes.setPlainText('shadow');dialog.save()
        QTimer.singleShot(0,save);self.assertTrue(canvas.edit(key));edited=copy.deepcopy(self.w.state['uses'][ident])
        self.assertEqual(edited['prompt'],'edited prompt');self.assertEqual(edited['excludes'],['shadow'])
        def cancel():
            dialog=canvas.active_node_dialog;self.assertEqual(dialog.prompt.toPlainText(),'edited prompt')
            dialog.prompt.setPlainText('cancelled');dialog.excludes_toggle.setChecked(True);dialog.reject()
        QTimer.singleShot(0,cancel);self.assertFalse(canvas.edit(key));self.assertEqual(self.w.state['uses'][ident],edited)
        canvas.undo();self.assertEqual(self.w.state['uses'][ident],original)
        canvas.redo();self.assertEqual(self.w.state['uses'][ident],edited)


if __name__=='__main__':unittest.main()
