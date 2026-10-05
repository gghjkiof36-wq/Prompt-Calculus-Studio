"""Destination names remain readable inside the native caption safe area."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'vendor'),str(ROOT)]
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PySide6.QtGui import QFontDatabase
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.window import Window

APP=QApplication.instance() or QApplication([])
for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
    QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


class StudioNavigationLayoutTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'qa')
        self.transport=patch('prompt_calculus_studio.comfy_client.ComfyClient.request',return_value=None)
        self.transport.start()
        self.w=Window(Path(self.tmp.name)/'data')
        self.w.state['settings'].update(online=False,material='solid')
        self.w.show();self.nav=self.w.studio_navigation;APP.processEvents()

    def tearDown(self):
        self.w.close();APP.processEvents();self.transport.stop();self.tmp.cleanup()

    def settle(self):
        for _ in range(3):APP.processEvents()

    def test_navigation_stays_inside_caption_area_at_supported_font_sizes(self):
        for font in (11,18,22):
            self.w.state['settings']['ui_size']=font;self.w.apply_theme()
            for width,inset in ((1440,144),(800,0),(800,144),(640,144),(640,0)):
                self.nav.set_caption_inset(inset);self.w.resize(width,480);self.settle()
                for key,(entry,title) in self.nav.entries.items():
                    with self.subTest(font=font,width=width,inset=inset,current=key):
                        self.nav.select(key);self.settle()
                        self.assertEqual(self.w.width(),width)
                        self.assertEqual(entry.text(),title)
                        self.assertEqual(entry.accessibleName(),title)
                        self.assertEqual(entry.toolTip(),'')
                        for navigation_button,full_title in self.nav.entries.values():
                            self.assertEqual(navigation_button.toolTip(),'' if navigation_button.text() else full_title)
                        controls=[self.nav.sidebar,*self.nav.history_buttons,
                                  *(item[0] for item in self.nav.entries.values()),self.nav.connection]
                        for left,right in zip(controls,controls[1:]):
                            self.assertLess(left.geometry().right(),right.geometry().left())
                        for control in controls:
                            self.assertGreaterEqual(control.x(),0)
                            self.assertLess(control.geometry().right(),width-inset)
                            self.assertGreaterEqual(control.y(),0)
                            self.assertLessEqual(control.geometry().bottom(),self.nav.height())
                        self.assertEqual(len({item[0].y() for item in self.nav.entries.values()}),1)
                        self.assertEqual(self.nav.height(),entry.height()+12)
                        self.assertGreaterEqual(self.nav.connection.width(),self.nav.connection.height())
                        if width==1440 or width==800 and font==11:
                            self.assertTrue(all(button.text()==name for button,name in self.nav.entries.values()))

    def test_current_name_updates_on_click_and_status_uses_available_space(self):
        self.w.state['settings']['ui_size']=18;self.w.apply_theme()
        self.nav.set_caption_inset(144);self.w.resize(640,480);self.settle()
        self.nav.entries['media'][0].click()
        self.assertEqual(self.nav.entries['media'][0].text(),'媒體庫')
        self.assertTrue(any(not entry.text() for entry,_ in self.nav.entries.values()))
        self.settle();self.assertFalse(self.nav.brand.isVisible())
        self.w.state['settings']['ui_size']=11;self.w.apply_theme();self.w.resize(800,640);self.settle()
        self.assertTrue(all(entry.text()==title for entry,title in self.nav.entries.values()))
        self.assertTrue(self.nav.brand.isVisible());self.assertEqual(self.nav.brand.text(),'未連線')
        self.w.comfy.connected=True;self.nav.update_connection();self.settle()
        self.assertEqual(self.nav.brand.text(),'已連線')
        self.assertEqual(self.nav.connection.accessibleName(),'ComfyUI · 已連線')
        self.w.resize(1440,900);self.settle()
        self.assertEqual(self.nav.brand.text(),'ComfyUI · 已連線')

    def test_mouse_click_has_no_focus_ring_and_keyboard_navigation_keeps_focus(self):
        explore=self.nav.entries['explore'][0]
        media=self.nav.entries['media'][0]
        QTest.mouseClick(explore,Qt.MouseButton.LeftButton);self.settle()
        self.assertTrue(explore.isChecked())
        self.assertFalse(explore.hasFocus())
        media.setFocus(Qt.FocusReason.TabFocusReason);self.settle()
        self.assertTrue(media.hasFocus())
        QTest.keyClick(media,Qt.Key.Key_Space);self.settle()
        self.assertTrue(media.isChecked())
        self.assertTrue(media.hasFocus())
        QTest.mouseClick(explore,Qt.MouseButton.LeftButton);self.settle()
        self.assertTrue(explore.isChecked())
        self.assertFalse(any(entry.hasFocus() for entry in self.nav._focus_buttons))


if __name__=='__main__':unittest.main()
