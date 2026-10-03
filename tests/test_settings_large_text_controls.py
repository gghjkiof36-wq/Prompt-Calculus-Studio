"""Large setting fonts retain readable selected values and reachable actions."""
import os,tempfile,unittest
from unittest.mock import patch
from PySide6.QtCore import QPoint,QRect
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QStyle,QStyleOptionComboBox
from prompt_studio.window import Window

APP=QApplication.instance() or QApplication([])
if APP.platformName()=='offscreen':
    for font in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+font)


def offline_request(client,route,data=None,done=None,**kwargs):
    if done and route.startswith('/userdata?'):
        done(['folder/workflow.json','a very long synthetic folder name / deeper path / workflow.json'])


class SettingsLargeTextControlsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.env=patch.dict(os.environ,{'PROMPT_STUDIO_V08':'1','PROMPT_STUDIO_DATA':self.tmp.name});self.env.start()
        self.transport=patch('prompt_studio.comfy_client.ComfyClient.request',offline_request);self.transport.start()
        self.w=Window(self.tmp.name);self.w.display_recovery.stop();self.w.first_models=False
        self.w.state['settings'].update(online=False,material='solid',reduce_motion=True)
        self.w.show();self.w.settings('appearance')

    def tearDown(self):
        self.w.close();APP.processEvents();self.transport.stop();self.env.stop();self.tmp.cleanup()

    def configure(self,width,size,route):
        self.w.resize(width,640)
        control=self.w.settings_page.preferences.ui_size
        control.blockSignals(True);control.setValue(size);control.blockSignals(False)
        self.w.state['settings']['ui_size']=size;self.w.apply_theme(preserve_layout=True)
        self.w.settings(route);QTest.qWait(100)

    def field(self,combo):
        option=QStyleOptionComboBox();combo.initStyleOption(option)
        return combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,combo)

    def test_font_family_is_readable_at_large_type_and_preserves_selection(self):
        combo=self.w.settings_page.preferences.family;family=combo.currentFont().family()
        for width,size in ((1440,18),(640,18),(640,22),(1440,22)):
            with self.subTest(width=width,size=size):
                self.configure(width,size,'appearance')
                self.assertGreaterEqual(self.field(combo).width(),combo.fontMetrics().horizontalAdvance(combo.currentText()))
                self.assertEqual(combo.currentFont().family(),family)
                self.assertEqual(combo.toolTip(),combo.currentText())
                self.assertEqual(self.w.width(),width)
                page=self.w.settings_page.pages.widget(self.w.settings_page.index['appearance'])
                rect=QRect(combo.mapTo(page.content,QPoint()),combo.size())
                self.assertGreaterEqual(rect.left(),0);self.assertLess(rect.right(),page.content.width())
                self.assertEqual(page.horizontalScrollBar().maximum(),0)

    def test_folder_value_and_actions_fit_at_maximum_type_size(self):
        self.configure(640,22,'workflows');manager=self.w.settings_page.workflow_manager
        combo=manager.folder
        self.assertEqual(combo.currentText(),'全部資料夾')
        self.assertGreaterEqual(self.field(combo).width(),combo.fontMetrics().horizontalAdvance(combo.currentText()))
        controls=(combo,manager.reload_button,manager.import_button)
        rectangles=[QRect(control.mapTo(manager,QPoint()),control.size()) for control in controls]
        self.assertFalse(any(left.intersects(right) for index,left in enumerate(rectangles) for right in rectangles[index+1:]))
        self.assertGreater(rectangles[1].top(),rectangles[0].bottom())
        for rect in rectangles:
            self.assertGreaterEqual(rect.left(),0);self.assertLess(rect.right(),manager.width())
        self.assertEqual(self.w.width(),640)

    def test_long_folder_has_complete_tooltip_and_keeps_selected_filter_after_resize(self):
        self.configure(640,22,'workflows');manager=self.w.settings_page.workflow_manager
        combo=manager.folder;index=next(i for i in range(combo.count()) if combo.itemText(i).startswith('a very long'))
        combo.setCurrentIndex(index);QTest.qWait(30)
        text=combo.currentText();selected=combo.currentData()
        self.assertEqual(combo.toolTip(),text)
        self.assertGreater(combo.fontMetrics().horizontalAdvance(text),self.field(combo).width())
        self.assertEqual(manager.list.count(),1)
        self.w.resize(1440,900);QTest.qWait(60)
        self.assertEqual(combo.currentData(),selected);self.assertEqual(combo.toolTip(),text)
        self.assertEqual(manager.list.count(),1)


if __name__=='__main__':unittest.main()
