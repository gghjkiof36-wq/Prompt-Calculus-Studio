"""NC prepaint clears only frame storage and never takes over native behavior."""
import ctypes
import sys
import unittest
from ctypes import wintypes
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QMainWindow, QWidget

from prompt_studio.window_chrome import _WindowsCaptionApi, install_expanded_chrome


APP = QApplication.instance() or QApplication([])


def write_struct(pointer, kind, values):
    value = ctypes.cast(pointer, ctypes.POINTER(kind)).contents
    for key, item in values.items():
        setattr(value, key, item)
    return 1


class NativeFramePaintTests(unittest.TestCase):
    def api(self):
        api = object.__new__(_WindowsCaptionApi)
        api.user = SimpleNamespace(
            IsIconic=Mock(return_value=False),
            GetWindowRect=Mock(side_effect=lambda _h, p: write_struct(
                p, wintypes.RECT, dict(left=-100, top=200, right=500, bottom=600))),
            GetClientRect=Mock(side_effect=lambda _h, p: write_struct(
                p, wintypes.RECT, dict(left=0, top=0, right=584, bottom=392))),
            ClientToScreen=Mock(side_effect=lambda _h, p: write_struct(
                p, wintypes.POINT, dict(x=-92, y=200))),
            GetWindowDC=Mock(return_value=91), ReleaseDC=Mock(), FillRect=Mock(return_value=1))
        api.dwm = SimpleNamespace(DwmIsCompositionEnabled=Mock(
            side_effect=lambda p: write_struct(p, wintypes.BOOL, dict(value=1)) - 1))
        api.gdi = SimpleNamespace(SaveDC=Mock(return_value=7), RestoreDC=Mock(),
                                  ExcludeClipRect=Mock(return_value=2), GetStockObject=Mock(return_value=14))
        return api

    def test_native_client_origin_is_excluded_and_dc_is_restored(self):
        api = self.api()
        self.assertTrue(api.clear_nonclient_background(123))
        api.gdi.ExcludeClipRect.assert_called_once_with(91, 8, 0, 592, 392)
        api.gdi.GetStockObject.assert_called_once_with(4)
        api.gdi.RestoreDC.assert_called_once_with(91, 7)
        api.user.ReleaseDC.assert_called_once_with(123, 91)

    def test_iconic_and_non_composited_windows_do_not_acquire_a_dc(self):
        api = self.api()
        api.user.IsIconic.return_value = True
        self.assertFalse(api.clear_nonclient_background(123))
        api.dwm.DwmIsCompositionEnabled.assert_not_called()
        api.user.GetWindowDC.assert_not_called()
        api.user.IsIconic.return_value = False
        api.dwm.DwmIsCompositionEnabled.side_effect = lambda _p: -1
        self.assertFalse(api.clear_nonclient_background(123))
        api.user.GetWindowDC.assert_not_called()

    def test_dc_is_released_on_clip_failure_and_paint_exception(self):
        api = self.api()
        api.gdi.ExcludeClipRect.return_value = 0
        self.assertFalse(api.clear_nonclient_background(123))
        api.user.FillRect.assert_not_called()
        api.gdi.RestoreDC.assert_called_once_with(91, 7)
        api.user.ReleaseDC.assert_called_once_with(123, 91)
        api = self.api()
        api.user.FillRect.side_effect = RuntimeError('test paint failure')
        with self.assertRaisesRegex(RuntimeError, 'test paint failure'):
            api.clear_nonclient_background(123)
        api.gdi.RestoreDC.assert_called_once_with(91, 7)
        api.user.ReleaseDC.assert_called_once_with(123, 91)

    def test_ncpaint_does_not_repaint_over_custom_frame_and_other_windows_are_untouched(self):
        window = QMainWindow()
        window.setCentralWidget(QWidget())
        with patch('prompt_studio.window_chrome.supported', return_value=True):
            controller = install_expanded_chrome(window)
        controller.hwnd = 123
        controller.caption_replaced = True
        controller.native_api = Mock()
        controller.caption_surface = Mock()
        for message in (0x0005, 0x0084, 0x0083, 0x0112, 0x02E0):
            self.assertEqual(controller.native_notification(123, message), (False, 0))
        self.assertEqual(controller.native_notification(999, 0x0085), (False, 0))
        controller.native_api.clear_nonclient_background.assert_not_called()
        self.assertEqual(controller.native_notification(123, 0x0085, 1, 0), (True, 0))
        controller.native_api.clear_nonclient_background.assert_called_once_with(123)
        controller.caption_replaced = False
        self.assertEqual(controller.native_notification(123, 0x0085), (False, 0))
        self.assertEqual(controller.native_api.clear_nonclient_background.call_count, 1)
        window.close()

    def test_restored_client_owns_every_edge_and_maximized_client_respects_dpi(self):
        api=self.api()
        api.user.IsZoomed=Mock(return_value=False)
        api.user.GetDpiForWindow=Mock(return_value=144)
        api.user.GetSystemMetricsForDpi=Mock(side_effect=lambda metric,dpi: {32:6,33:6,92:6}[metric])
        rect=wintypes.RECT(-1200,50,-200,750)
        self.assertTrue(api.calculate_client_rect(123,ctypes.addressof(rect)))
        self.assertEqual((rect.left,rect.top,rect.right,rect.bottom),(-1200,50,-200,750))
        api.user.IsZoomed.return_value=True
        self.assertTrue(api.calculate_client_rect(123,ctypes.addressof(rect)))
        self.assertEqual((rect.left,rect.top,rect.right,rect.bottom),(-1188,62,-212,738))
        api.user.GetSystemMetricsForDpi.assert_any_call(32,144)
        rect=wintypes.RECT(0,0,1920,1080)
        self.assertTrue(api.calculate_client_rect(123,ctypes.addressof(rect),fullscreen=True))
        self.assertEqual((rect.left,rect.top,rect.right,rect.bottom),(0,0,1920,1080))
        api.user.IsIconic.return_value=True
        self.assertFalse(api.calculate_client_rect(123,ctypes.addressof(rect)))

    def test_only_matching_window_full_nccalcsize_is_handled(self):
        window=QMainWindow();window.setCentralWidget(QWidget())
        with patch('prompt_studio.window_chrome.supported',return_value=True):
            controller=install_expanded_chrome(window)
        controller.hwnd=123;controller.native_api=Mock()
        controller.native_api.calculate_client_rect.return_value=True
        self.assertEqual(controller.native_notification(999,0x83,1,42),(False,0))
        self.assertEqual(controller.native_notification(123,0x83,0,42),(False,0))
        controller.native_api.calculate_client_rect.assert_not_called()
        self.assertEqual(controller.native_notification(123,0x83,1,42),(True,0))
        controller.native_api.calculate_client_rect.assert_called_once_with(123,42,False)
        window.close()

    @unittest.skipUnless(sys.platform == 'win32', 'GDI memory bitmap needs Windows')
    def test_real_gdi_pixels_preserve_client_and_restore_clipping(self):
        # Memory-only bitmap, never a desktop/screen capture or a shown window.
        api = _WindowsCaptionApi()
        gdi = api.gdi
        class Header(ctypes.Structure):
            _fields_ = [('size', wintypes.DWORD), ('width', wintypes.LONG), ('height', wintypes.LONG),
                        ('planes', wintypes.WORD), ('bits', wintypes.WORD), ('compression', wintypes.DWORD),
                        ('image_size', wintypes.DWORD), ('xppm', wintypes.LONG), ('yppm', wintypes.LONG),
                        ('used', wintypes.DWORD), ('important', wintypes.DWORD)]
        gdi.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
        gdi.CreateCompatibleDC.restype = ctypes.c_void_p
        gdi.CreateDIBSection.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT,
                                       ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, wintypes.DWORD]
        gdi.CreateDIBSection.restype = ctypes.c_void_p
        gdi.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        gdi.SelectObject.restype = ctypes.c_void_p
        gdi.DeleteObject.argtypes = [ctypes.c_void_p]
        gdi.DeleteDC.argtypes = [ctypes.c_void_p]
        width, height = 30, 20
        header = Header(ctypes.sizeof(Header), width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        bits = ctypes.c_void_p()
        dc = gdi.CreateCompatibleDC(None)
        bitmap = gdi.CreateDIBSection(dc, ctypes.byref(header), 0, ctypes.byref(bits), None, 0)
        self.assertTrue(dc and bitmap and bits.value)
        previous = gdi.SelectObject(dc, bitmap)
        try:
            pixels = (ctypes.c_uint32 * (width * height)).from_address(bits.value)
            bounds = wintypes.RECT(0, 0, width, height)
            for client in (wintypes.RECT(3, 0, 27, 17), wintypes.RECT(3, 3, 27, 17)):
                for i in range(len(pixels)):
                    pixels[i] = 0xFFAEBECD
                self.assertTrue(api._clear_nonclient_dc(dc, bounds, client))
                gdi.GdiFlush()
                for y in range(height):
                    for x in range(width):
                        inside = client.left <= x < client.right and client.top <= y < client.bottom
                        self.assertEqual(pixels[y * width + x], 0xFFAEBECD if inside else 0,
                                         f'pixel {(x, y)} with client {(client.left, client.top)}')
                self.assertTrue(api.user.FillRect(dc, ctypes.byref(bounds), gdi.GetStockObject(4)))
                gdi.GdiFlush()
                self.assertTrue(all(pixel == 0 for pixel in pixels), 'Saved client clipping leaked into later drawing')
        finally:
            gdi.SelectObject(dc, previous)
            gdi.DeleteObject(bitmap)
            gdi.DeleteDC(dc)


if __name__ == '__main__':
    unittest.main()
