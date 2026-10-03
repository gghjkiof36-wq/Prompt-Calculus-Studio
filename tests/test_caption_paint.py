"""Caption workaround preserves native interaction and readable hover paint."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from prompt_studio.window_chrome import CaptionPaintSurface, _WindowsCaptionApi, install_expanded_chrome
from prompt_studio.theme import visual_tokens


APP = QApplication.instance() or QApplication([])


class CaptionPaintTests(unittest.TestCase):
    def test_graphite_hover_retains_dark_surface_and_visible_glyph(self):
        window = QMainWindow()
        window.state = {'settings': {'visual_palette': 'graphite'}}
        surface = CaptionPaintSurface(window)
        surface.resize(138, 31)
        surface.hovered = 1
        image = QImage(138, 31, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor('#202527'))
        surface.render(image)
        self.assertLess(max(image.pixelColor(50, 4).getRgb()[:3]), 100)
        glyph_pixels = [image.pixelColor(x, y).lightness() for x in range(63, 76) for y in range(9, 23)]
        self.assertGreater(max(glyph_pixels), 180)
        self.assertTrue(surface.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(surface.focusPolicy(), Qt.FocusPolicy.NoFocus)
        window.close()

    def test_light_hover_and_close_glyph_remain_readable(self):
        window = QMainWindow()
        window.state = {'settings': {'visual_palette': 'paper'}}
        surface = CaptionPaintSurface(window)
        surface.resize(138, 31)
        tokens=visual_tokens(window.state['settings'])
        for hovered in (1, 2):
            surface.hovered = hovered
            image = QImage(138, 31, QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(QColor('#e2ded5'))
            surface.render(image)
            pixel = image.pixelColor(50 if hovered == 1 else 96, 4)
            self.assertEqual(pixel.name(), tokens['selected'] if hovered == 1 else '#c42c1e')
            glyph_x=range(63,76) if hovered==1 else range(109,122)
            values=[image.pixelColor(x,y).lightness() for x in glyph_x for y in range(9,23)]
            self.assertLess(min(values),90) if hovered==1 else self.assertGreater(max(values),180)
        window.close()

    def test_native_commands_and_hit_tests_are_never_consumed(self):
        window = QMainWindow()
        window.setCentralWidget(QWidget())
        with patch('prompt_studio.window_chrome.supported', return_value=True):
            controller = install_expanded_chrome(window)
        controller.caption_replaced = True
        controller.hwnd = 123
        controller.native_api = Mock()
        controller.caption_surface = Mock()
        for message in (0x84, 0xA1, 0xA2, 0x112, 0x201, 0x202, 0x02E0):
            self.assertEqual(controller.native_notification(123, message), (False, 0))
        controller.native_api.activate_without_repaint.assert_not_called()
        self.assertEqual(controller.native_notification(999, 0x86), (False, 0))
        controller.native_api.activate_without_repaint.return_value = 1
        self.assertEqual(controller.native_notification(123, 0x86, 0, 456), (True, 1))
        controller.native_api.activate_without_repaint.assert_called_once_with(123, 0, 456)
        window.close()

    def test_activation_suppresses_only_non_minimized_repaint(self):
        api = object.__new__(_WindowsCaptionApi)
        api.user = SimpleNamespace(IsIconic=Mock(return_value=False), DefWindowProcW=Mock(return_value=1))
        self.assertEqual(api.activate_without_repaint(123, 0, 456), 1)
        api.user.DefWindowProcW.assert_called_with(123, 0x86, 0, -1)
        api.user.IsIconic.return_value = True
        api.activate_without_repaint(123, 1, 456)
        api.user.DefWindowProcW.assert_called_with(123, 0x86, 1, 456)

    def test_missing_qt_decoration_falls_back_without_duplicate_controls(self):
        window = QMainWindow()
        window.setCentralWidget(QWidget())
        with patch('prompt_studio.window_chrome.supported', return_value=True):
            controller = install_expanded_chrome(window)
        surface = CaptionPaintSurface(window)
        controller.caption_surface = surface
        controller.native_api = SimpleNamespace(hide_qt_caption=Mock(return_value=False))
        controller._sync_caption_surface()
        self.assertTrue(surface.isHidden())
        self.assertFalse(controller.caption_replaced)
        self.assertEqual(controller.native_notification(controller.hwnd, 0x86), (False, 0))
        window.close()


if __name__ == '__main__':
    unittest.main()
