"""Offline Qt checks for management-only text rhythm and large-font reflow."""
import sys,tempfile,unittest,traceback
from pathlib import Path
from unittest.mock import patch
from PySide6.QtCore import Qt,QCoreApplication,QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication,QPushButton,QScrollArea
from prompt_calculus_studio.window import Window
from prompt_calculus_studio.management_reading import ReadingText,ReadingPanel,text_layout
from prompt_calculus_studio.theme import font_pixels

APP=QApplication.instance() or QApplication([])


class ManagementReadingTests(unittest.TestCase):
    def setUp(self):
        self.errors=[]
        hook=patch.object(sys,'excepthook',lambda kind,value,tb:self.errors.append(''.join(traceback.format_exception(kind,value,tb))))
        hook.start();self.addCleanup(hook.stop)
        startup=patch('prompt_calculus_studio.extension_page.ExtensionPage.startup')
        startup.start();self.addCleanup(startup.stop)
        for name in ('msjh.ttc','msjhbd.ttc','segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.w=Window(self.tmp.name);self.w.comfy.timer.stop();self.w.comfy.enabled=False
        self.w.error=self.errors.append
        self.page=self.w.settings_page.manager;self.page.client.set_server('',False)
        self.appearance(1440,900,11)

    def tearDown(self):
        APP.clipboard().clear();self.w.close();APP.processEvents();self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);APP.processEvents()
        self.assertFalse(self.errors,self.errors)

    def appearance(self,width,height,points):
        self.w.state['settings'].update(ui_size=points,online=False,material='solid',reduce_motion=True)
        self.w.resize(width,height);self.w.apply_theme();self.w.show();self.w.settings('nodes');QTest.qWait(20)

    def content(self,key):
        self.page.show_view(key);QTest.qWait(20)
        return self.page.extension if key=='extension' else self.page.tools_page.widget() if key=='tools' else self.page.auto_page.widget()

    def assert_readable(self,content):
        for text in content.findChildren(ReadingText):
            self.assertGreaterEqual(text.height(),text.heightForWidth(text.width()),text.text())
            offset={'panel':3,'group':1,'body':0}[text.level]
            self.assertEqual(text.font().pixelSize(),font_pixels(self.w.state['settings']['ui_size']+offset))
        for control in content.findChildren(QPushButton):
            self.assertGreaterEqual(control.height(),max(36,control.fontMetrics().height()+16),control.text())

    def test_three_pages_use_local_roles_natural_heights_and_group_gaps(self):
        for key in ('extension','tools','automatic'):
            content=self.content(key);self.assert_readable(content)
            self.assertEqual(self.page.heading.font().pixelSize(),font_pixels(16))
            panel=content.findChild(ReadingPanel);self.assertEqual(panel.body.contentsMargins().left(),24)
        extension=self.page.extension;body=extension.findChild(ReadingPanel).body
        self.assertEqual(body.spacing(),14)
        self.assertEqual(extension.automatic.text(),'自動更新')
        self.assertEqual(extension.check.text(),'')

    def test_narrow_large_text_reflows_long_paths_and_status_without_internal_scroll(self):
        path='D:/合成安裝位置/中文長路徑/'+('very-long-directory-name-'*10)+'/ComfyUI'
        extension=self.page.extension;extension.preferences.value['root']=path
        extension.message='合成的安裝狀態說明。這個狀態使用正常字級，長中文內容應自然換行並完整保留。'*3
        extension.candidate=dict(version='0.8.7 Alpha 1 · synthetic')
        self.appearance(640,480,18);extension.render()
        for key in ('extension','tools','automatic'):
            content=self.content(key);self.assert_readable(content)
            scroll=self.page.pages.currentWidget();self.assertIsInstance(scroll,QScrollArea)
            self.assertEqual(scroll.horizontalScrollBar().maximum(),0)
            self.assertLessEqual(content.width(),scroll.viewport().width())
            scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum());QTest.qWait(5)
        self.assertEqual(extension.location.text(),path);self.assertTrue(extension.location.isReadOnly())
        self.assertEqual(extension.location.verticalScrollBar().maximum(),0)
        extension.location.selectAll();extension.location.copy();self.assertEqual(APP.clipboard().text(),path)
        self.assertEqual(extension.status.text(),extension.message)
        self.assertGreater(extension.automatic.height(),36)

    def test_page_heading_reverts_on_other_pages_and_updates_with_font_preferences(self):
        self.content('tools');self.assertEqual(self.page.heading.font().pixelSize(),font_pixels(16))
        self.appearance(1440,900,18);self.assertEqual(self.page.heading.font().pixelSize(),font_pixels(23))
        for key in ('packages','history'):
            self.page.show_view(key);QTest.qWait(5)
            self.assertEqual(self.page.heading.styleSheet(),'')
            self.assertEqual(self.page.heading.font().pixelSize(),font_pixels(22))

    def test_auto_page_links_and_extension_controls_keep_existing_semantics(self):
        content=self.content('automatic')
        buttons={b.text():b for b in content.findChildren(QPushButton)}
        buttons['管理套件'].click();self.assertEqual(self.page.view,'packages')
        self.content('automatic');buttons['PCS 配套擴充的自動更新'].click();self.assertEqual(self.page.view,'extension')
        extension=self.page.extension
        with patch('prompt_calculus_studio.extension_page.QFileDialog.getExistingDirectory',return_value='') as chooser,patch.object(extension.process,'start') as start:
            extension.candidate=dict(version='synthetic');extension.render();extension.choose.click()
            chooser.assert_called_once();start.assert_not_called()
        original=extension.automatic.isChecked()
        QTest.mouseClick(extension.automatic,Qt.MouseButton.LeftButton)
        self.assertEqual(extension.preferences.value['automatic'],not original)
        self.assertFalse(extension.busy);self.assertFalse(extension.retry_timer.isActive())

    def test_reading_lines_measure_font_metrics_and_preserve_newline_content(self):
        content=self.content('extension');text=self.page.extension.version
        text.setText('隨附版本：0.8.7\n已安裝：尚未確認')
        layout,height=text_layout(text.text(),text.font(),800)
        self.assertEqual(layout.lineCount(),2)
        self.assertAlmostEqual(layout.lineAt(1).position().y()-layout.lineAt(0).position().y(),layout.lineAt(0).height()*1.6,delta=1/64)
        self.assertGreaterEqual(height,layout.lineAt(1).position().y()+layout.lineAt(1).height())
