"""Settings routes share geometry while existing controls remain reachable."""
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QPushButton, QLineEdit, QComboBox, QPlainTextEdit, QWidget

from prompt_studio.settings_page import ReadingPage, SETTINGS_READING_WIDTH
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)

ROUTES=('interface','appearance','completion','dictionary','data','workflows')


class SettingsReadingLayoutTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.temp.name}); self.env.start()
        self.requests=patch('prompt_studio.comfy_client.ComfyClient.request'); self.requests.start()
        self.w=Window(self.temp.name); self.w.display_recovery.stop(); self.w.first_models=False
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.apply_theme(); self.w.show()

    def tearDown(self):
        self.w.close(); APP.processEvents(); self.requests.stop(); self.env.stop(); self.temp.cleanup()

    def page(self,route):
        self.w.settings(route); QTest.qWait(70)
        settings=self.w.settings_page
        page=settings.comfy_tabs.widget(0) if route=='workflows' else settings.pages.widget(settings.index[route])
        self.assertIsInstance(page,ReadingPage)
        page.verticalScrollBar().setValue(0); QTest.qWait(20)
        return page

    def configure(self,width,height,size,palette):
        self.w.resize(width,height)
        self.w.settings_page.ensure_preferences()
        control=self.w.settings_page.preferences.ui_size
        control.blockSignals(True); control.setValue(size); control.blockSignals(False)
        self.w.state['settings'].update(ui_size=size,visual_palette=palette)
        self.w.apply_theme(preserve_layout=True); QTest.qWait(80)

    def test_every_form_uses_the_same_column_and_heading_origin(self):
        for width,height,size,palette in ((1680,1000,11,'graphite'),(800,640,18,'paper'),(640,480,18,'mist')):
            self.configure(width,height,size,palette)
            geometry=[]
            for route in ROUTES:
                with self.subTest(width=width,size=size,route=route):
                    page=self.page(route); content=page.content
                    title=next(child for child in content.findChildren(QLabel) if child.objectName()=='DialogTitle')
                    origin=content.mapTo(self.w,QPoint())
                    header=title.mapTo(self.w,QPoint())
                    geometry.append((origin.x(),origin.y(),content.width(),header.x(),header.y()))
                    self.assertLessEqual(content.width(),SETTINGS_READING_WIDTH)
                    self.assertLessEqual(page.container.width(),page.viewport().width())
                    self.assertEqual(self.w.width(),width)
                    self.assertEqual(page.horizontalScrollBar().maximum(),0)
            for item in geometry[1:]:
                for actual,expected in zip(item,geometry[0]):
                    self.assertLessEqual(abs(actual-expected),1,(width,size,geometry))

    def test_large_type_controls_are_reachable_without_widening_the_window(self):
        self.configure(640,480,18,'paper')
        for route in ROUTES:
            page=self.page(route); content=page.content
            controls=[child for child in content.findChildren(QWidget) if isinstance(child,(QPushButton,QLineEdit,QComboBox,QPlainTextEdit))]
            for control in controls:
                if not control.isVisibleTo(content):continue
                with self.subTest(route=route,control=control.objectName() or type(control).__name__,text=getattr(control,'text',lambda:'')()):
                    rect=QRect(control.mapTo(content,QPoint()),control.size())
                    self.assertGreaterEqual(rect.left(),0)
                    self.assertLessEqual(rect.right(),content.width()-1)
                    self.assertGreater(control.width(),0)
                    page.ensureWidgetVisible(control,0,0); QTest.qWait(5)
                    shown=QRect(control.mapTo(page.viewport(),QPoint()),control.size())
                    self.assertTrue(shown.intersects(page.viewport().rect()))
        self.assertEqual(self.w.width(),640)


if __name__=='__main__':unittest.main()
