"""Expanded chrome preserves system controls and the window title."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QMargins, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from prompt_studio.window_chrome import caption_inset, install_expanded_chrome
from prompt_studio.theme import apply_backdrop


APP = QApplication.instance() or QApplication([])


class WindowChromeTests(unittest.TestCase):
    def test_install_keeps_system_controls_without_title_overlap(self):
        window = QMainWindow()
        central = QWidget()
        window.setCentralWidget(central)
        window.setWindowTitle('Prompt Calculus Studio')
        window.setGeometry(40, 50, 900, 600)
        original = window.windowFlags()
        with patch('prompt_studio.window_chrome.supported', return_value=True):
            controller = install_expanded_chrome(window)
            self.assertIs(install_expanded_chrome(window), controller)
        flags = window.windowHandle().flags()
        for flag in (Qt.WindowType.WindowSystemMenuHint, Qt.WindowType.WindowMinimizeButtonHint,
                     Qt.WindowType.WindowMaximizeButtonHint, Qt.WindowType.WindowCloseButtonHint):
            self.assertEqual(bool(flags & flag), bool(original & flag))
        self.assertFalse(flags & Qt.WindowType.WindowTitleHint)
        self.assertEqual(window.windowFlags(),flags)
        self.assertFalse(flags & Qt.WindowType.FramelessWindowHint)
        self.assertTrue(flags & Qt.WindowType.ExpandedClientAreaHint)
        self.assertTrue(flags & Qt.WindowType.NoTitleBarBackgroundHint)
        self.assertEqual(window.windowTitle(), 'Prompt Calculus Studio')
        APP.processEvents()
        self.assertEqual(window.size().toTuple(), (900, 600))
        self.assertEqual(window.windowHandle().geometry(), window.geometry())
        self.assertFalse(window.testAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea))
        self.assertFalse(central.testAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea))
        window.close()

    def test_recreated_native_window_keeps_expanded_decoration_flags(self):
        window=QMainWindow();window.setCentralWidget(QWidget())
        window.setGeometry(40,50,900,600)
        with patch('prompt_studio.window_chrome.supported',return_value=True):
            controller=install_expanded_chrome(window)
        expected=window.windowFlags()
        expected_geometry=window.geometry()
        old_handle=window.windowHandle()
        window.destroy()
        self.assertIsNone(controller.handle)
        controller._update_inset()
        window.winId()
        APP.processEvents()
        self.assertIsNot(window.windowHandle(),old_handle)
        self.assertIs(controller.handle,window.windowHandle())
        self.assertEqual(window.geometry(),expected_geometry)
        self.assertEqual(window.windowHandle().flags(),expected)
        self.assertFalse(expected & Qt.WindowType.WindowTitleHint)
        self.assertTrue(expected & Qt.WindowType.WindowSystemMenuHint)
        self.assertTrue(expected & Qt.WindowType.WindowMinMaxButtonsHint)
        insets=[]
        controller.insetChanged.connect(insets.append)
        with patch('prompt_studio.window_chrome.caption_inset',return_value=224):
            window.windowHandle().safeAreaMarginsChanged.emit(QMargins(0,48,0,0))
        with patch('prompt_studio.window_chrome.caption_inset',return_value=188):
            window.windowHandle().screenChanged.emit(window.screen())
        self.assertEqual(insets,[224,188])
        window.close()

    def test_solid_material_does_not_reset_the_expanded_frame(self):
        margins=[]
        def extend(_hwnd,pointer):
            value=pointer._obj
            margins.append((value.left,value.right,value.top,value.bottom))
        dwm=SimpleNamespace(DwmSetWindowAttribute=lambda *args:0,
                            DwmExtendFrameIntoClientArea=extend)
        window=QMainWindow();window.winId()
        with patch('PySide6.QtGui.QGuiApplication.platformName',return_value='windows'), \
                patch('prompt_studio.theme.ctypes.windll',SimpleNamespace(dwmapi=dwm),create=True), \
                patch('prompt_studio.theme.sys.platform','win32'), \
                patch('prompt_studio.theme.sys.getwindowsversion',return_value=SimpleNamespace(build=22621),create=True):
            self.assertTrue(apply_backdrop(window,'solid',{}))
            self.assertEqual(margins[-1],(0,0,0,0))
            with patch('prompt_studio.window_chrome.supported',return_value=True):
                install_expanded_chrome(window)
            for material in ('solid','mica','acrylic','solid'):
                self.assertTrue(apply_backdrop(window,material,{}))
                self.assertEqual(margins[-1],(-1,-1,-1,-1))
        window.close()

    def test_unsupported_platform_does_not_create_native_window(self):
        window = QMainWindow()
        with patch('prompt_studio.window_chrome.supported', return_value=False):
            self.assertIsNone(install_expanded_chrome(window))
        self.assertIsNone(window.windowHandle())
        window.close()

    def test_visible_window_is_not_reconfigured(self):
        window = QMainWindow()
        window.show()
        flags = window.windowHandle().flags()
        with patch('prompt_studio.window_chrome.supported', return_value=True):
            with self.assertRaises(RuntimeError):
                install_expanded_chrome(window)
        self.assertEqual(window.windowHandle().flags(), flags)
        window.close()

    def test_caption_controls_are_reserved_for_reported_safe_area(self):
        self.assertGreaterEqual(caption_inset(QMargins(0, 25, 0, 0)), 3 * 25 * 1.5)
        self.assertGreaterEqual(caption_inset(QMargins(0, 40, 210, 0)), 210)


if __name__ == '__main__':
    unittest.main()
