"""Expanded chrome preserves system controls and the window title."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QMargins, Qt
from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from prompt_calculus_studio.window_chrome import caption_inset, caption_hit_test, install_expanded_chrome
from prompt_calculus_studio.theme import apply_backdrop


APP = QApplication.instance() or QApplication([])


class WindowChromeTests(unittest.TestCase):
    def test_install_keeps_system_controls_without_title_overlap(self):
        window = QMainWindow()
        central = QWidget()
        window.setCentralWidget(central)
        window.setWindowTitle('Prompt Calculus Studio')
        window.setGeometry(40, 50, 900, 600)
        original = window.windowFlags()
        with patch('prompt_calculus_studio.window_chrome.supported', return_value=True):
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
        with patch('prompt_calculus_studio.window_chrome.supported',return_value=True):
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
        with patch('prompt_calculus_studio.window_chrome.caption_inset',return_value=224):
            window.windowHandle().safeAreaMarginsChanged.emit(QMargins(0,48,0,0))
        with patch('prompt_calculus_studio.window_chrome.caption_inset',return_value=188):
            window.windowHandle().screenChanged.emit(window.screen())
        self.assertEqual(insets,[224,188])
        window.close()

    def test_solid_material_does_not_reset_the_expanded_frame(self):
        margins=[];kinds=[]
        def extend(_hwnd,pointer):
            value=pointer._obj
            margins.append((value.left,value.right,value.top,value.bottom))
            return 0
        def attribute(_hwnd,key,pointer,_size):
            if key==38:kinds.append(pointer._obj.value)
            return 0
        dwm=SimpleNamespace(DwmSetWindowAttribute=attribute,
                            DwmExtendFrameIntoClientArea=extend)
        window=QMainWindow();window.winId()
        with patch('PySide6.QtGui.QGuiApplication.platformName',return_value='windows'), \
                patch('prompt_calculus_studio.theme.ctypes.windll',SimpleNamespace(dwmapi=dwm),create=True), \
                patch('prompt_calculus_studio.theme.sys.platform','win32'), \
                patch('prompt_calculus_studio.theme.sys.getwindowsversion',return_value=SimpleNamespace(build=22621),create=True):
            self.assertTrue(apply_backdrop(window,'solid',{}))
            self.assertEqual(margins[-1],(0,0,0,0))
            with patch('prompt_calculus_studio.window_chrome.supported',return_value=True):
                install_expanded_chrome(window)
            for material in ('solid','mica','acrylic','clear','solid'):
                self.assertTrue(apply_backdrop(window,material,{}))
                self.assertEqual(margins[-1],(-1,-1,-1,-1))
            self.assertEqual(kinds,[1,1,2,3,1,1])
            dwm.DwmExtendFrameIntoClientArea=lambda *_:-1
            self.assertFalse(apply_backdrop(window,'clear',{}))
        window.close()

    def test_unsupported_platform_does_not_create_native_window(self):
        window = QMainWindow()
        with patch('prompt_calculus_studio.window_chrome.supported', return_value=False):
            self.assertIsNone(install_expanded_chrome(window))
        self.assertIsNone(window.windowHandle())
        window.close()

    def test_visible_window_is_not_reconfigured(self):
        window = QMainWindow()
        window.show()
        flags = window.windowHandle().flags()
        with patch('prompt_calculus_studio.window_chrome.supported', return_value=True):
            with self.assertRaises(RuntimeError):
                install_expanded_chrome(window)
        self.assertEqual(window.windowHandle().flags(), flags)
        window.close()

    def test_caption_controls_are_reserved_for_reported_safe_area(self):
        self.assertGreaterEqual(caption_inset(QMargins(0, 25, 0, 0)), 3 * 25 * 1.5)
        self.assertGreaterEqual(caption_inset(QMargins(0, 40, 210, 0)), 210)

    def test_caption_hit_regions_keep_controls_content_edges_and_fullscreen_distinct(self):
        flags = Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint
        def hit(x, y, border=8, fullscreen=False):
            return caption_hit_test(x, y, 1000, 700, border, 38, 57, flags, fullscreen)
        self.assertEqual(hit(850, 20), 8)
        self.assertEqual(hit(920, 20), 9)
        self.assertEqual(hit(970, 20), 20)
        self.assertEqual(hit(970, 60), 1)
        self.assertEqual(hit(200, 20), 1)  # Tab strip receives ordinary Qt input.
        self.assertEqual(hit(2, 2), 13)
        self.assertEqual(hit(998, 2), 14)
        self.assertEqual(hit(998, 698), 17)
        self.assertEqual(hit(998, 20, border=0), 20)
        self.assertEqual(hit(970, 20, fullscreen=True), 1)

    def test_hit_testing_and_cancelled_caption_gestures_cannot_close_or_open_a_menu(self):
        window = QMainWindow()
        window.setCentralWidget(QWidget())
        with patch('prompt_calculus_studio.window_chrome.supported', return_value=True):
            control = install_expanded_chrome(window)
        window.show()
        APP.processEvents()
        native_api, hwnd, replaced = control.native_api, control.hwnd, control.caption_replaced
        control.native_api = SimpleNamespace(hit_test=lambda *args:20)
        control.hwnd, control.caption_replaced = 123, True
        try:
            with patch.object(window, 'close') as close:
                for _ in range(5):
                    self.assertEqual(control.native_notification(123, 0x84, 0, 100), (True, 20))
                close.assert_not_called()
                control.native_notification(123, 0xA1, 20)
                control.native_notification(123, 0x202)
                control.native_notification(123, 0xA2, 20)
                APP.processEvents()
                close.assert_not_called()
                control.native_notification(123, 0xA1, 20)
                control.native_notification(123, 0xA2, 20)
                APP.processEvents()
                close.assert_called_once()
                self.assertEqual(control.native_notification(123, 0x112, 0xF100), (False, 0))
        finally:
            control.native_api, control.hwnd, control.caption_replaced = native_api, hwnd, replaced
            window.close()


if __name__ == '__main__':
    unittest.main()
