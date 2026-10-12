"""Real Qt popup dismissal, nested focus, stable placement and form depth; offline."""
import copy
from unittest.mock import patch
from PySide6.QtCore import Qt,QPoint,QPointF,QRect,QTimer,QEvent
from PySide6.QtGui import QScreen,QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFrame,QWidget,QPushButton,QDialog
from stage_fixture import StageFixture,APP
from prompt_calculus_studio.canvas_palette import CanvasPalette
from prompt_calculus_studio.function_picker import FunctionPicker
from prompt_calculus_studio.text_canvas import NodeDialog
from prompt_calculus_studio.widgets import RoundMenu
from prompt_calculus_studio.clip_widgets import ClipBindingDialog


class CanvasPopupLayersTests(StageFixture):
    def setUp(self):
        super().setUp()
        self.w.state['settings'].update(visual_palette='graphite',reduce_motion=False)
        self.w.resize(1400,960);self.w.apply_theme(preserve_layout=True)
        QTest.qWait(400);self.w.persist()

    def test_function_sheet_opens_in_place_with_backdrop_and_outside_click_without_canvas_edit(self):
        before=copy.deepcopy(self.c.data());undo=len(self.c.undo_stack)
        with patch.object(QScreen,'availableGeometry',return_value=QRect(0,0,1920,1080)):
            dialog=FunctionPicker(self.c);dialog.show()
            try:
                self.assertEqual(dialog.geometry(),QRect(self.w.mapToGlobal(QPoint()),self.w.size()))
                size=dialog.surface.size();start=dialog.surface.pos()
                self.assertEqual(dialog.grab().toImage().pixelColor(4,4).alpha(),120)
                for _ in range(6):
                    QTest.qWait(40)
                    self.assertEqual(dialog.surface.pos(),start)
                    self.assertEqual(dialog.surface.size(),size)
                    self.assertEqual(dialog.grab().toImage().pixelColor(4,4).alpha(),120)
                self.w.resize(1200,780);QTest.qWait(20)
                self.assertEqual(dialog.size(),self.w.size())
                self.assertTrue(dialog.rect().contains(dialog.surface.geometry()))
                QTest.mouseClick(dialog,Qt.MouseButton.LeftButton,pos=QPoint(4,4));QTest.qWait(10)
                self.assertFalse(dialog.isVisible());self.assertEqual(dialog.result(),QDialog.DialogCode.Rejected)
                self.assertEqual(self.c.data(),before);self.assertEqual(len(self.c.undo_stack),undo)
            finally:dialog.reject();dialog.deleteLater()

    def test_reduced_motion_has_full_scrim_immediately_and_escape_returns_to_canvas(self):
        self.w.state['settings']['reduce_motion']=True
        dialog=FunctionPicker(self.c);dialog.show();QTest.qWait(10)
        try:
            self.assertEqual(dialog.grab().toImage().pixelColor(4,4).alpha(),120)
            QTest.keyClick(dialog.search,Qt.Key.Key_Escape);QTest.qWait(10)
            self.assertFalse(dialog.isVisible())
        finally:dialog.reject();dialog.deleteLater()

    def test_blank_module_dims_visible_picker_and_cancel_restores_parent_and_data(self):
        palette=CanvasPalette(self.c,QPointF(400,400),destination_id=self.cid)
        palette.show();QTest.qWait(25)
        before=copy.deepcopy(self.w.state);undo=len(self.c.undo_stack)
        sample=palette.surface.pos()+QPoint(18,18)
        initial=palette.grab().toImage().pixelColor(sample)
        seen=[];errors=[]
        def inspect():
            dialog=APP.activeModalWidget()
            try:
                self.assertIsInstance(dialog,NodeDialog)
                self.assertIs(dialog.parentWidget(),palette)
                self.assertIsNotNone(dialog._backdrop)
                self.assertTrue(dialog._backdrop.isVisible())
                dimmed=palette.grab().toImage().pixelColor(sample)
                self.assertLess(dimmed.lightness(),initial.lightness())
                editor=dialog.findChild(QFrame,'DialogEditor')
                self.assertNotEqual(editor.palette().window().color(),dialog.prompt.palette().base().color())
                dialog.name.setText('取消後不能新增');dialog.prompt.setPlainText('unsaved')
                seen.append(True)
            except Exception as error:errors.append(error)
            finally:dialog.reject()
        QTimer.singleShot(30,inspect)
        try:
            QTest.mouseClick(palette.new_button,Qt.MouseButton.LeftButton);QTest.qWait(30)
            self.assertFalse(errors,errors);self.assertEqual(seen,[True])
            self.assertTrue(palette.isVisible());self.assertIs(APP.activeModalWidget(),palette)
            self.assertEqual(palette.grab().toImage().pixelColor(sample),initial)
            self.assertEqual(self.w.state,before);self.assertEqual(len(self.c.undo_stack),undo)
        finally:palette.reject();palette.deleteLater()

    def test_binding_fields_have_editor_contrast_and_shade_is_removed_on_cancel(self):
        before=copy.deepcopy(self.c.data())
        dialog=ClipBindingDialog(self.c,self.clip);dialog.show();QTest.qWait(25)
        try:
            editor=dialog.findChild(QFrame,'DialogEditor')
            shell=dialog.findChild(QFrame,'DialogSurface')
            self.assertNotEqual(editor.palette().window().color(),shell.palette().window().color())
            self.assertNotEqual(editor.palette().window().color(),dialog.workflow.palette().base().color())
            self.assertTrue(dialog._backdrop.isVisible())
            dialog.reject();QTest.qWait(5)
            self.assertIsNone(dialog._backdrop)
            self.assertEqual(self.c.data(),before)
        finally:dialog.reject();dialog.deleteLater()

    def test_menu_dismiss_press_is_not_replayed_to_trigger_and_next_click_can_reopen(self):
        trigger=QPushButton('選單',self.w);trigger.setGeometry(30,70,100,40);trigger.show();trigger.raise_()
        opened=[]
        def open_menu():
            menu=RoundMenu(self.w);menu.addAction('新增模組');menu.open_for(trigger);opened.append(menu)
        trigger.clicked.connect(open_menu)
        try:
            QTest.mouseClick(trigger,Qt.MouseButton.LeftButton);QTest.qWait(15)
            menu=opened[-1];point=menu.mapFromGlobal(trigger.mapToGlobal(trigger.rect().center()))
            self.assertFalse(menu.rect().contains(point))
            # Route the actual outside press to the grab owner. Offscreen does
            # not provide OS click replay; WA_NoMouseReplay is Qt's contract.
            QTest.mousePress(menu,Qt.MouseButton.LeftButton,pos=point)
            self.assertTrue(menu.testAttribute(Qt.WidgetAttribute.WA_NoMouseReplay))
            self.assertFalse(menu.isVisible());self.assertEqual(len(opened),1)
            QTest.mouseRelease(trigger,Qt.MouseButton.LeftButton);QTest.qWait(15)
            self.assertIsNone(APP.activePopupWidget())
            QTest.mouseClick(trigger,Qt.MouseButton.LeftButton);QTest.qWait(15)
            self.assertEqual(len(opened),2);self.assertTrue(opened[-1].isVisible())
            QTest.keyClick(opened[-1],Qt.Key.Key_Escape);QTest.qWait(10)
            self.assertIsNone(APP.activePopupWidget())
        finally:
            if APP.activePopupWidget():APP.activePopupWidget().close()
            trigger.deleteLater()

    def test_scheduler_pause_and_resume_match_save_material_action(self):
        from prompt_calculus_studio.flow_data import add_scheduler
        keys=[];self.c.commit(lambda state:keys.append(add_scheduler(state)))
        panel=self.c.flow_cards[keys[0]].panel
        save=self.c.outputs[self.out].panel.save_material
        self.assertEqual(panel.pause_button.styleSheet(),save.styleSheet())
        QTest.mouseClick(panel.pause_button,Qt.MouseButton.LeftButton);QTest.qWait(10)
        self.assertEqual(panel.pause_button.text(),'繼續')
        self.assertTrue(panel.runner.store.scheduler_paused(self.w.state['workspace'],keys[0]))
        self.assertEqual(panel.pause_button.styleSheet(),save.styleSheet())
        QTest.mouseClick(panel.pause_button,Qt.MouseButton.LeftButton);QTest.qWait(10)
        self.assertEqual(panel.pause_button.text(),'暫停')
        self.assertFalse(panel.runner.store.scheduler_paused(self.w.state['workspace'],keys[0]))
        from prompt_calculus_studio.canvas_items import canvas_colors
        for palette in ('paper','graphite'):
            self.w.state['settings']['visual_palette']=palette;self.w.apply_theme(preserve_layout=True)
            self.assertEqual(panel.pause_button.styleSheet(),save.styleSheet())
            self.assertEqual(save.palette().button().color(),QColor(canvas_colors(self.c)['surface']))

    def test_editor_placeholder_contrast_and_disposal_leave_no_orphan_shade(self):
        from PySide6.QtGui import QPalette
        from prompt_calculus_studio.color_roles import contrast
        from shiboken6 import isValid
        for palette in ('graphite','paper','mist'):
            self.w.state['settings']['visual_palette']=palette;self.w.apply_theme(preserve_layout=True)
            dialog=NodeDialog(self.w);dialog.show();QTest.qWait(10)
            colors=dialog.prompt.palette()
            self.assertGreaterEqual(contrast(colors.color(QPalette.ColorRole.PlaceholderText).name(),colors.base().color().name()),4.5)
            shade=dialog._backdrop
            dialog.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
            APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)
            self.assertFalse(isValid(shade))
