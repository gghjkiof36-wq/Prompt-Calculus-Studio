"""Viewport notices remain readable independently of the draggable run bar."""
import os,tempfile,unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtCore import QPoint,QPointF,Qt,QEvent
from PySide6.QtGui import QFontDatabase,QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class CanvasStatusLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name});self.env.start()
        self.transport=patch('prompt_studio.comfy_client.ComfyClient.request',return_value=None);self.transport.start()
        self.w=Window(self.tmp.name);self.w.display_recovery.stop();self.w.comfy.timer.stop()
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.show();self.w.enter_canvas()
        self.canvas=self.w.canvas;self.bar=self.canvas.execution_bar;self.view=self.canvas.view

    def tearDown(self):
        self.w.close();APP.processEvents();self.transport.stop();self.env.stop();self.tmp.cleanup()

    def configure(self,width,size):
        self.w.resize(width,800 if width>1000 else 640)
        self.w.state['settings'].update(ui_size=size,execution_bar_position=[.5,1.])
        self.w.apply_theme(preserve_layout=True);self.bar.update_progress();QTest.qWait(60)

    def assert_layout(self):
        notices=[label for label in (self.w.canvas_status,self.bar.status) if label.isVisible()]
        for label in notices:
            self.assertIs(label.parentWidget(),self.view.viewport())
            self.assertEqual(label.x(),12)
            self.assertTrue(label.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            self.assertTrue(self.view.viewport().rect().contains(label.geometry()),
                            (self.view.viewport().size(),label.geometry(),label.text()))
            for obstacle in (self.bar,self.canvas.zoom_controls):
                if obstacle.isVisible():self.assertFalse(label.geometry().intersects(obstacle.geometry()),
                                                         (label.geometry(),obstacle.geometry()))
        if len(notices)==2:self.assertFalse(notices[0].geometry().intersects(notices[1].geometry()))

    def test_pending_clip_and_notice_fit_wide_and_compact_at_both_sizes(self):
        for width,size in ((1440,11),(640,11),(1440,18),(640,18)):
            with self.subTest(width=width,size=size):
                self.w.notice('');self.configure(width,size)
                self.assertEqual(self.bar.status.text(),'CLIP 待綁定')
                self.assertTrue(self.bar.status.isVisible());self.assert_layout()
                self.w.notice('已儲存工作區');QTest.qWait(10)
                self.assertTrue(self.w.canvas_status.isVisible());self.assert_layout()

    def test_dragged_execution_bar_does_not_carry_notices(self):
        for width in (1440,640):
            with self.subTest(width=width):
                self.configure(width,18);self.w.notice('已儲存工作區')
                start=self.bar.pos();handle=self.bar.handle;local=handle.rect().center()
                global_start=handle.mapToGlobal(local);delta=QPoint(-300,-110)
                for event_type,button,buttons,global_pos in (
                    (QEvent.Type.MouseButtonPress,Qt.MouseButton.LeftButton,Qt.MouseButton.LeftButton,global_start),
                    (QEvent.Type.MouseMove,Qt.MouseButton.NoButton,Qt.MouseButton.LeftButton,global_start+delta),
                    (QEvent.Type.MouseButtonRelease,Qt.MouseButton.LeftButton,Qt.MouseButton.NoButton,global_start+delta)):
                    event=QMouseEvent(event_type,QPointF(handle.mapFromGlobal(global_pos)),QPointF(global_pos),button,buttons,Qt.KeyboardModifier.NoModifier)
                    APP.sendEvent(handle,event)
                QTest.qWait(10);self.assertNotEqual(self.bar.pos(),start);self.assert_layout()
                self.assertEqual(self.bar.status.x(),12);self.assertEqual(self.w.canvas_status.x(),12)

    def test_canvas_list_switch_hides_and_restores_execution_hint(self):
        self.configure(640,18);self.w.notice('已儲存工作區');self.assert_layout()
        self.w.set_interface_mode('list');QTest.qWait(20)
        self.assertFalse(self.bar.status.isVisible());self.assertFalse(self.w.canvas_status.isVisible())
        self.w.set_interface_mode('canvas');QTest.qWait(20)
        self.assertTrue(self.bar.status.isVisible());self.assertTrue(self.w.canvas_status.isVisible())
        self.assertEqual(self.bar.status.text(),'CLIP 待綁定');self.assert_layout()

    def test_long_notices_remain_within_viewport(self):
        for width,size in ((1440,11),(640,18)):
            with self.subTest(width=width,size=size):
                self.configure(width,size)
                text='此工作區尚未選擇工作流，請先前往連線與工作流確認設定，完成後再返回畫布繼續。'
                self.w.notice(text*3)
                self.bar._status_text='CLIP 待綁定，請確認目前工作流中的文字輸入節點。';self.bar.present_status()
                QTest.qWait(10);self.assert_layout()

    def test_oversized_notice_is_visually_elided_without_losing_source_text(self):
        self.configure(640,18)
        text='無法讀取工作流，請確認所選資料夾與連線設定。'*20
        self.w.notice(text);QTest.qWait(10);self.assert_layout()
        notice=self.w.canvas_status
        self.assertEqual(notice.text(),text);self.assertEqual(notice.accessibleName(),text)
        self.assertTrue(notice._canvas_status_elided)
        self.assertLessEqual(notice.height(),notice.fontMetrics().lineSpacing()*3+12)
        self.assertTrue(self.bar.status.isVisible())
        self.bar.move(12,12);QTest.qWait(10);self.assert_layout()
        self.assertGreater(notice.y(),self.bar.geometry().bottom())
        self.w.notice('已儲存工作區');QTest.qWait(10);self.assert_layout()
        self.assertFalse(notice._canvas_status_elided);self.assertEqual(notice.text(),'已儲存工作區')


if __name__=='__main__':unittest.main()
