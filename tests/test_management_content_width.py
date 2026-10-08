"""Management panels retain one content column across window sizes."""
import sys
import tempfile
import traceback
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QPoint
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from prompt_calculus_studio.management_reading import PREFERRED_CONTENT_WIDTH, ReadingPanel, ReadingText
from prompt_calculus_studio.window import Window


class ManagementContentWidthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.errors = []
        for target, replacement in (
            ('sys.excepthook', lambda kind, value, tb: self.errors.append(''.join(traceback.format_exception(kind, value, tb)))),
            ('prompt_calculus_studio.extension_page.ExtensionPage.startup', lambda _: None),
            ('prompt_calculus_studio.manager_client.ManagerClient.request', lambda *a, **k: self.fail('Unexpected Manager request')),
            ('prompt_calculus_studio.comfy_client.ComfyClient.request', lambda *a, **k: self.fail('Unexpected ComfyUI request')),
        ):
            mock = patch(target, replacement)
            mock.start()
            self.addCleanup(mock.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.window = Window(self.temp.name)
        self.window.comfy.timer.stop()
        self.window.comfy.enabled = False
        self.window.state['settings'].update(online=False, reduce_motion=True, material='solid')
        self.page = self.window.settings_page.manager
        self.page.client.set_server('', False)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.assertFalse(self.errors, self.errors)

    def show(self, width, height, key, points=11):
        self.window.state['settings']['ui_size'] = points
        self.window.resize(width, height)
        self.window.apply_theme()
        self.window.show()
        self.window.settings('nodes')
        self.page.show_view(key)
        QTest.qWait(30)
        scroll = self.page.pages.currentWidget()
        return scroll, scroll.widget().findChild(ReadingPanel)

    def test_normal_and_wide_panels_share_fixed_bounds(self):
        recorded = {}
        for width, height in ((1360, 900), (2048, 1152)):
            bounds = []
            for key in ('extension', 'tools', 'automatic'):
                with self.subTest(width=width, page=key):
                    scroll, panel = self.show(width, height, key)
                    bounds.append((panel.mapTo(self.window, QPoint()).x(), panel.width()))
                    self.assertEqual(panel.width(), PREFERRED_CONTENT_WIDTH)
                    self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
            self.assertEqual(len(set(bounds)), 1, bounds)
            recorded[width] = bounds[0]
        self.assertEqual(recorded[1360], recorded[2048])

    def test_narrow_large_text_keeps_panels_and_controls_reachable(self):
        bounds = []
        for key in ('extension', 'tools', 'automatic'):
            with self.subTest(page=key):
                scroll, panel = self.show(640, 480, key, points=18)
                bounds.append((panel.mapTo(self.window, QPoint()).x(), panel.width()))
                self.assertLess(panel.width(), PREFERRED_CONTENT_WIDTH)
                self.assertLessEqual(panel.width(), scroll.viewport().width())
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                for text in panel.findChildren(ReadingText):
                    self.assertGreaterEqual(text.height(), text.heightForWidth(text.width()), text.text())
                for control in panel.findChildren(QPushButton):
                    self.assertGreaterEqual(control.height(), control.fontMetrics().height() + 16, control.text())
        self.assertEqual(len(set(bounds)), 1, bounds)

    def test_automatic_update_links_keep_their_destinations(self):
        _, panel = self.show(1360, 900, 'automatic')
        buttons = {control.text(): control for control in panel.findChildren(QPushButton)}
        buttons['管理套件'].click()
        self.assertEqual(self.page.view, 'packages')
        self.page.show_view('automatic')
        buttons['PCS 配套擴充的自動更新'].click()
        self.assertEqual(self.page.view, 'extension')
