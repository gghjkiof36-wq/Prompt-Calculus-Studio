"""Retained material previews stay beside the list after real page navigation."""
import copy
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import Qt, QPoint, QCoreApplication, QEvent
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton
from prompt_calculus_studio.core import Storage
from prompt_calculus_studio.window import Window
from test_material_editor import state


class EditorPreviewNavigationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {
            'PROMPT_STUDIO_DATA': self.tmp.name, 'PROMPT_STUDIO_V08': '1'})
        self.environment.start()
        self.transport = patch('prompt_calculus_studio.comfy_client.ComfyClient.request', return_value=None)
        self.transport.start()
        store = Storage(Path(self.tmp.name))
        store.save(state()); store.close()
        self.w = Window(Path(self.tmp.name))
        self.w.resize(2048, 1152); self.w.show()
        self.w.set_interface_mode('list'); self.settle()
        self.page = self.w.prompt_editor
        self.page.set_browser_category(self.w.state['items'][0]['module']); self.settle()

    def tearDown(self):
        self.w.close(); self.w.deleteLater(); self.settle()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.transport.stop(); self.environment.stop(); self.tmp.cleanup()

    def settle(self):
        for _ in range(6):
            self.app.processEvents()

    def open_preview(self):
        entry = self.page.listing.item(0)
        self.ident = entry.data(Qt.ItemDataRole.UserRole)
        QTest.mouseClick(self.page.listing.viewport(), Qt.MouseButton.LeftButton,
                        pos=self.page.listing.visualItemRect(entry).center())
        self.settle()
        self.assert_preview()

    def navigate(self, key):
        QTest.mouseClick(self.w.studio_navigation.entries[key][0], Qt.MouseButton.LeftButton)
        self.settle()

    def assert_preview(self):
        self.assertTrue(self.page.isVisible())
        self.assertTrue(self.page.preview_card.isVisible())
        self.assertEqual(self.page.preview_id, self.ident)
        self.assertEqual(self.page.selected_id(), self.ident)
        main, detail = self.page.browser.geometry(), self.page.preview_card.geometry()
        self.assertFalse(main.intersects(detail), (main, detail))
        self.assertEqual(detail.left() - main.right() - 1, 24)
        self.assertEqual(detail.y(), self.page.search.mapTo(self.page.shell, QPoint()).y())

    def test_preview_survives_page_round_trips_without_moving_the_columns(self):
        self.open_preview()
        columns = (self.page.browser.geometry(), self.page.preview_card.geometry())
        text = self.page.preview_text.toPlainText()
        original = copy.deepcopy(self.w.state['items'])
        # Settings and other top-level pages hide the entire editor while its
        # refresh runs on return, which differs from a standalone widget test.
        for key in ('media', 'settings', 'export', 'explore', 'media'):
            with self.subTest(page=key):
                self.navigate(key)
                self.assertFalse(self.page.isVisible())
                self.navigate('canvas')
                self.assert_preview()
                self.assertEqual((self.page.browser.geometry(), self.page.preview_card.geometry()), columns)
                self.assertEqual(self.page.preview_text.toPlainText(), text)
        QTest.mouseClick(self.page.workspace_switch.canvas_button, Qt.MouseButton.LeftButton)
        self.settle()
        QTest.mouseClick(self.w.canvas_workspace_bar.workspace_switch.editor_button, Qt.MouseButton.LeftButton)
        self.settle(); self.assert_preview()
        self.assertEqual(self.w.state['items'], original)

    def test_retained_preview_reflows_after_resizing_on_another_page(self):
        self.open_preview()
        for width, height in ((1360, 900), (800, 640), (640, 480), (2048, 1152)):
            with self.subTest(size=(width, height)):
                self.navigate('settings')
                self.w.resize(width, height); self.settle()
                self.navigate('canvas'); self.assert_preview()
                self.assertLessEqual(self.page.preview_card.geometry().right(), self.page.shell.width())

    def test_closed_preview_stays_closed_after_leaving_and_returning(self):
        self.open_preview()
        close = next(button for button in self.page.preview_card.findChildren(QPushButton)
                     if button.text() == '×')
        QTest.mouseClick(close, Qt.MouseButton.LeftButton); self.settle()
        column = self.page.browser.geometry()
        self.assertIsNone(self.page.preview_id)
        for key in ('media', 'settings'):
            self.navigate(key); self.navigate('canvas')
            self.assertTrue(self.page.preview_card.isHidden())
            self.assertIsNone(self.page.preview_id)
            self.assertEqual(self.page.browser.geometry(), column)


if __name__ == '__main__':
    unittest.main()
