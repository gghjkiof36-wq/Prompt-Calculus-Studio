"""Compact extension settings, with inert metadata and no installation."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QProcess, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QBoxLayout, QPushButton

from prompt_calculus_studio.core import DEFAULT_SETTINGS
from prompt_calculus_studio.extension_page import ExtensionPage
from prompt_calculus_studio.management_reading import ReadingText
from prompt_calculus_studio.theme import stylesheet, widget_palette
from prompt_calculus_studio.widgets import scrolling
from test_manager_page import Window


class CompactExtensionPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)

    def setUp(self):
        self.original_palette = self.app.palette()
        self.temp = tempfile.TemporaryDirectory(prefix='pcs-extension-ui-')
        self.root = Path(self.temp.name)
        self.window = Window(self.root)
        self.window.resize(1200, 900)
        with patch('prompt_calculus_studio.extension_page.available_package',
                   return_value=(self.root, self.root / 'missing-bundle')), \
                patch.object(ExtensionPage, 'startup'):
            self.page = ExtensionPage(self.window)
        self.launch = patch.object(self.page.process, 'start',
                                   side_effect=AssertionError('UI test must never launch an installer'))
        self.start = self.launch.start()
        self.scroll = scrolling(self.page)
        self.appearance(740, 760, 11)

    def tearDown(self):
        self.page.shutdown()
        self.scroll.close(); self.window.close()
        self.scroll.deleteLater(); self.window.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.start.assert_not_called()
        self.launch.stop()
        self.app.setPalette(self.original_palette)
        self.temp.cleanup()

    def appearance(self, width, height, points):
        settings = dict(DEFAULT_SETTINGS, ui_size=points, material='solid', reduce_motion=True)
        self.window.state['settings'] = settings
        self.app.setPalette(widget_palette(settings))
        self.scroll.setStyleSheet(stylesheet(settings))
        self.scroll.setPalette(widget_palette(settings))
        self.scroll.resize(width, height)
        self.scroll.show()
        self.app.processEvents(); QTest.qWait(15); self.app.processEvents()

    def screenshot(self, name):
        evidence = os.environ.get('PCS_EXTENSION_EVIDENCE')
        if evidence:
            self.scroll.grab().save(str(Path(evidence) / (name + '.png')))

    def test_missing_bundle_has_one_short_status_without_unknown_version_rows(self):
        self.assertEqual(self.page.status.text(), '未附擴充包')
        self.assertFalse(self.page.version.isVisible())
        self.assertFalse(self.page.choose.isEnabled())
        self.assertFalse(self.page.update.isEnabled())
        self.assertFalse(self.page.update.isVisible())
        self.assertEqual(self.page.check.text(), '')
        self.assertEqual(self.page.check.accessibleName(), '重新檢查 PCS 擴充')
        texts = [text.text() for text in self.page.findChildren(ReadingText) if text.isVisible()]
        self.assertEqual(texts, ['PCS 擴充', '未附擴充包'])
        self.assertLess(self.page.card.height(), 330)
        self.screenshot('missing-bundle-740')

    def test_known_version_is_one_line_and_restart_or_failure_messages_remain(self):
        self.page.candidate = dict(version='8.9.10 pre-alpha', git_head='a', release='fixture')
        self.page.preferences.value['root'] = str(self.root / 'ComfyUI')
        previous = self.root / 'ComfyUI/custom_nodes/PCS'
        with patch.object(self.page, 'selection', return_value=(self.root, previous, self.page.candidate)), \
                patch('prompt_calculus_studio.extension_page.update_state', return_value='current'):
            self.page.render(); self.app.processEvents()
            self.assertEqual(self.page.version.text(), '版本 8.9.10 pre-alpha')
            self.assertIn('重啟 ComfyUI', self.page.status.text())
            self.assertEqual(self.page.choose.text(), '更改資料夾並安裝')
            self.assertFalse(self.page.update.isEnabled())
            self.page.message = '安裝未完成，請查看安裝紀錄；不會自動重試。'
            self.page.render()
            self.assertEqual(self.page.status.text(), self.page.message)
            self.page.message = ''
            self.window.comfy.connected = True
            self.window.comfy.extension_info = dict(root=str(previous), git_head='a')
            self.page.render(); self.app.processEvents()
            self.assertEqual(self.page.status.text(), '已安裝並載入')
            self.screenshot('installed-740')

    def test_folder_action_still_installs_and_cancellation_has_no_side_effect(self):
        self.page.candidate = dict(version='fixture')
        self.page.render()
        self.app.processEvents()
        self.screenshot('bundle-ready-740')
        with patch('prompt_calculus_studio.extension_page.QFileDialog.getExistingDirectory', return_value=''), \
                patch.object(self.page, 'install') as install, patch.object(self.page.preferences, 'save') as save:
            self.page.choose.click()
            install.assert_not_called(); save.assert_not_called()
        with patch('prompt_calculus_studio.extension_page.QFileDialog.getExistingDirectory', return_value=str(self.root)), \
                patch('prompt_calculus_studio.extension_page.comfy_root', return_value=self.root), \
                patch('prompt_calculus_studio.extension_page.installed', return_value=None), \
                patch.object(self.page, 'install') as install, patch.object(self.page.preferences, 'save') as save:
            self.page.choose.click()
            install.assert_called_once_with(); save.assert_called_once_with()
            self.assertEqual(self.page.preferences.value['root'], str(self.root))
            self.assertEqual(self.page.choose.text(), '更改資料夾並安裝')

    def test_check_icon_does_not_install_and_automatic_toggle_keeps_existing_preference(self):
        with patch.object(self.page, 'install') as install:
            self.page.check.click()
            install.assert_not_called()
        with patch.object(self.page.preferences, 'save') as save:
            before = self.page.automatic.isChecked()
            self.page.automatic.setChecked(not before)
            self.assertEqual(self.page.preferences.value['automatic'], not before)
            save.assert_called_once_with()

    def test_narrow_large_font_keeps_all_controls_and_long_error_readable(self):
        self.page.candidate = dict(version='8.9.10 pre-alpha')
        self.page.preferences.value['root'] = 'D:/合成位置/' + 'long-name/' * 8 + 'ComfyUI'
        self.page.message = '安裝未完成；請查看安裝紀錄。保留原始資料。' * 2
        self.page.render()
        for width, points in ((740, 18), (380, 18)):
            with self.subTest(width=width, points=points):
                self.appearance(width, 720, points)
                self.assertEqual(self.scroll.horizontalScrollBar().maximum(), 0)
                self.assertEqual(self.page.location.verticalScrollBar().maximum(), 0)
                for text in self.page.findChildren(ReadingText):
                    if text.isVisible():
                        self.assertGreaterEqual(text.height(), text.heightForWidth(text.width()), text.text())
                for control in self.page.findChildren(QPushButton):
                    if control.isVisible():
                        self.assertGreaterEqual(control.width(), control.minimumSizeHint().width(), control.text())
                        self.assertGreaterEqual(control.height(), control.fontMetrics().height() + 16, control.text())
                if width == 380:
                    self.assertEqual(self.page.actions.direction(), QBoxLayout.Direction.TopToBottom)
                    self.assertEqual(self.page.options.direction(), QBoxLayout.Direction.TopToBottom)
                self.screenshot(f'large-font-{width}')


if __name__ == '__main__':
    unittest.main()
