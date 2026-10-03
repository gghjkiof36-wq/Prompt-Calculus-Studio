"""Opt-in Qt expanded client area for the Windows desktop shell.

Install once, before the first show. Changing these flags while maximized
resets the native maximize state, so the same chrome is used in both states.
"""
import math
import ctypes

from PySide6.QtCore import QAbstractNativeEventFilter, QEvent, QObject, QPointF, QRectF, Qt, Signal, qVersion
from PySide6.QtGui import QColor, QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget


class _WindowsCaptionApi:
    """Only touches the current window and Qt's decoration child."""
    def __init__(self):
        from ctypes import wintypes
        self.user = ctypes.windll.user32
        self.gdi = ctypes.windll.gdi32
        self.dwm = ctypes.windll.dwmapi
        self.user.FindWindowExW.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p]
        self.user.FindWindowExW.restype = ctypes.c_void_p
        self.user.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.user.GetDpiForWindow.argtypes = [ctypes.c_void_p]
        self.user.GetDpiForWindow.restype = ctypes.c_uint
        self.user.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
        self.user.IsIconic.argtypes = [ctypes.c_void_p]
        self.user.IsZoomed.argtypes = [ctypes.c_void_p]
        self.user.SetWindowPos.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        self.user.DefWindowProcW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t]
        self.user.DefWindowProcW.restype = ctypes.c_ssize_t
        self.user.GetWindowRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        self.user.GetClientRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT)]
        self.user.ClientToScreen.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.POINT)]
        self.user.GetWindowDC.argtypes = [ctypes.c_void_p]
        self.user.GetWindowDC.restype = ctypes.c_void_p
        self.user.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        self.user.FillRect.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.RECT), ctypes.c_void_p]
        self.gdi.SaveDC.argtypes = [ctypes.c_void_p]
        self.gdi.RestoreDC.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.gdi.ExcludeClipRect.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        self.gdi.GetStockObject.argtypes = [ctypes.c_int]
        self.gdi.GetStockObject.restype = ctypes.c_void_p
        self.dwm.DwmIsCompositionEnabled.argtypes = [ctypes.POINTER(wintypes.BOOL)]

    def refresh_frame(self, hwnd):
        # Recalculate the client region without moving, activating or resizing.
        self.user.SetWindowPos(hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0001 | 0x0002 | 0x0004 | 0x0010)

    def calculate_client_rect(self, hwnd, address, fullscreen=False):
        """Give Qt ownership of the full restored surface, including resize edges.

        Qt 6.11.2 leaves an eight-pixel NC strip on three sides of an expanded
        client window. DefWindowProc can redraw that strip white during resize,
        after the previous workaround cleared it. Remove the strip at its
        geometry boundary instead of painting over it. Native hit tests and
        WS_THICKFRAME stay in place. Maximized windows retain their invisible
        resize margins so their contents fit the monitor work area.
        """
        if not address or self.user.IsIconic(hwnd):
            return False
        from ctypes import wintypes
        # rgrc[0] is the first member of NCCALCSIZE_PARAMS.
        rect = wintypes.RECT.from_address(address)
        if self.user.IsZoomed(hwnd) and not fullscreen:
            dpi = self.user.GetDpiForWindow(hwnd) or 96
            x = sum(self.user.GetSystemMetricsForDpi(metric, dpi) for metric in (32, 92))
            y = sum(self.user.GetSystemMetricsForDpi(metric, dpi) for metric in (33, 92))
            rect.left += x; rect.right -= x
            rect.top += y; rect.bottom -= y
        return True

    def hide_qt_caption(self, hwnd):
        child = self.user.FindWindowExW(hwnd, None, '_q_titlebar', None)
        if not child:
            return False
        self.user.ShowWindow(child, 0)  # SW_HIDE; never alter its input handling.
        return True

    def metrics(self, hwnd):
        dpi = self.user.GetDpiForWindow(hwnd) or 96
        # Match QWindowsWindow::getTitleBarHeight_sys and titleButtonWidth.
        height = sum(self.user.GetSystemMetricsForDpi(metric, dpi) for metric in (32, 92, 4))
        return max(1, height), max(1, int(height * 1.5))

    def activate_without_repaint(self, hwnd, active, previous):
        # Minimized windows still need normal icon-title activation handling.
        parameter = previous if self.user.IsIconic(hwnd) else -1
        return self.user.DefWindowProcW(hwnd, 0x0086, active, parameter)

    def clear_nonclient_background(self, hwnd):
        """Clear only DWM's normally hidden frame pixels before its NC paint.

        Under composition, the default NC painter can leave its backing pixels
        white. Resizing/restoring briefly exposes those pixels outside Qt's
        client area. Black GDI pixels supply zero alpha for the extended frame;
        DWM still paints its border/shadow after this method returns.
        """
        from ctypes import wintypes
        if self.user.IsIconic(hwnd):
            return False
        composed = wintypes.BOOL()
        if self.dwm.DwmIsCompositionEnabled(ctypes.byref(composed)) != 0 or not composed.value:
            return False
        window_rect, client_rect = wintypes.RECT(), wintypes.RECT()
        origin = wintypes.POINT()
        if not (self.user.GetWindowRect(hwnd, ctypes.byref(window_rect))
                and self.user.GetClientRect(hwnd, ctypes.byref(client_rect))
                and self.user.ClientToScreen(hwnd, ctypes.byref(origin))):
            return False
        width, height = window_rect.right - window_rect.left, window_rect.bottom - window_rect.top
        if width <= 0 or height <= 0:
            return False
        offset_x, offset_y = origin.x - window_rect.left, origin.y - window_rect.top
        client_rect = wintypes.RECT(client_rect.left + offset_x, client_rect.top + offset_y,
                                   client_rect.right + offset_x, client_rect.bottom + offset_y)
        bounds = wintypes.RECT(0, 0, width, height)
        dc = self.user.GetWindowDC(hwnd)
        if not dc:
            return False
        try:
            return self._clear_nonclient_dc(dc, bounds, client_rect)
        finally:
            self.user.ReleaseDC(hwnd, dc)

    def _clear_nonclient_dc(self, dc, bounds, client_rect):
        saved = self.gdi.SaveDC(dc)
        if not saved:
            return False
        try:
            if not self.gdi.ExcludeClipRect(dc, client_rect.left, client_rect.top,
                                           client_rect.right, client_rect.bottom):
                return False
            # BLACK_BRUSH is a system-owned stock object; do not delete it.
            brush = self.gdi.GetStockObject(4)
            return bool(brush and self.user.FillRect(dc, ctypes.byref(bounds), brush))
        finally:
            self.gdi.RestoreDC(dc, saved)


class CaptionPaintSurface(QWidget):
    """Paint caption glyphs in the same backing store as the navigation.

    Qt 6.11's separate layered titlebar sends straight-alpha pixels to a
    premultiplied-alpha Windows API. A white hover consequently turns opaque.
    This surface is input-transparent: Qt's original native caption hit tests,
    system menu and resize/move handling remain responsible for interaction.
    """
    def __init__(self, window):
        super().__init__(window)
        self.setObjectName('CaptionPaintSurface')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.button_width = 46.0
        self.title_height = 31.0
        self.hovered = None

    def update_hover(self):
        pos = self.mapFromGlobal(QCursor.pos())
        hovered = None
        if self.rect().contains(pos):
            hovered = min(2, max(0, int((pos.x() - self.width() + 3 * self.button_width) / self.button_width)))
        if hovered != self.hovered:
            self.hovered = hovered
            self.update()

    def paintEvent(self, event):
        from .theme import visual_tokens
        window = self.window()
        settings = dict(getattr(window, 'state', {}).get('settings', {}))
        settings.update(getattr(window, 'appearance_preview', {}))
        tokens = visual_tokens(settings)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        for index in range(3):
            rect = QRectF(self.width() - (3 - index) * self.button_width, 0,
                          self.button_width, self.title_height)
            hovered = self.hovered == index
            if hovered:
                painter.fillRect(rect, QColor(tokens['caption_close_hover'] if index == 2 else tokens['selected']))
            foreground = tokens['on_caption_close'] if hovered and index == 2 else tokens['text']
            painter.setPen(QPen(QColor(foreground), 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            center = rect.center()
            x, y = center.x() - 4.5, center.y() - 4.5
            if index == 0:
                painter.drawLine(QPointF(x, center.y()), QPointF(x + 9, center.y()))
            elif index == 1 and window.isMaximized():
                painter.drawPolyline([QPointF(x + 2, y + 2), QPointF(x + 2, y),
                                      QPointF(x + 9, y), QPointF(x + 9, y + 7), QPointF(x + 7, y + 7)])
                painter.drawRect(QRectF(x, y + 2, 7, 7))
            elif index == 1:
                painter.drawRect(QRectF(x, y, 9, 9))
            else:
                painter.drawLine(QPointF(x, y), QPointF(x + 9, y + 9))
                painter.drawLine(QPointF(x + 9, y), QPointF(x, y + 9))


class _CaptionNativeEvents(QAbstractNativeEventFilter):
    def __init__(self, controller):
        super().__init__()
        self.controller = controller

    def nativeEventFilter(self, event_type, message):
        if bytes(event_type) != b'windows_generic_MSG':
            return False, 0
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        return self.controller.native_notification(msg.hWnd, msg.message, msg.wParam, msg.lParam)


def supported():
    """Limit the feature to the Windows Qt version used by this application."""
    app = QApplication.instance()
    version = tuple(int(part) for part in qVersion().split('.')[:2])
    return bool(app and app.platformName() == 'windows' and version >= (6, 11))


def expanded_flags(flags):
    """Keep resize and system buttons, suppress only Qt's title/icon drawing."""
    if flags & Qt.WindowType.FramelessWindowHint:
        raise ValueError('Expanded chrome requires a native window frame')
    value = int(flags)
    value |= int(Qt.WindowType.ExpandedClientAreaHint)
    value |= int(Qt.WindowType.NoTitleBarBackgroundHint)
    value |= int(Qt.WindowType.CustomizeWindowHint)
    # QWidget.setWindowFlags adds this hint back with system buttons. Install
    # these exact flags with overrideWindowFlags so a recreated native window
    # also receives the same decoration contract.
    value &= ~int(Qt.WindowType.WindowTitleHint)
    return Qt.WindowType(value)


def caption_inset(margins):
    """Reserve the caption controls at the right edge, in logical pixels.

    Qt Windows reports a top safe area, not the caption buttons' right inset.
    Its three caption buttons each use 1.5 times the title-bar height. The
    minimum also covers the ordinary Windows caption control width.
    """
    return max(144, margins.right(), math.ceil(margins.top() * 4.5) + 8)


class ExpandedWindowChrome(QObject):
    insetChanged = Signal(int)

    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.handle = None
        self.caption_right_inset = 144
        self.hwnd = 0
        self.native_api = None
        self.native_filter = None
        self.caption_surface = None
        self.caption_replaced = False
        # Keep this workaround local to the supported Windows backend. Tests
        # may exercise the flag/geometry contract on the offscreen backend.
        if QApplication.instance().platformName() == 'windows':
            try:
                self.native_api = _WindowsCaptionApi()
            except (OSError, AttributeError):
                pass
            if self.native_api is not None:
                self.caption_surface = CaptionPaintSurface(window)
                self.caption_surface.hide()
        window.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        central = window.centralWidget() if hasattr(window, 'centralWidget') else None
        if central is not None:
            central.setAttribute(Qt.WidgetAttribute.WA_ContentsMarginsRespectsSafeArea, False)
        geometry = window.geometry()
        flags = expanded_flags(window.windowFlags())
        window.overrideWindowFlags(flags)
        window.winId()
        handle = window.windowHandle()
        if handle.flags() != flags:
            handle.setFlags(flags)
        # Flag changes can include frame-size rounding at fractional DPI. Keep
        # the requested client geometry before Qt records the restore bounds.
        # QWidget may still cache the requested rectangle while QWindow has
        # already adjusted it, so update both instead of hitting its no-op path.
        handle.setGeometry(geometry)
        window.setGeometry(geometry)
        self._bind_window_handle()
        window.installEventFilter(self)
        if self.native_api:
            self.native_filter = _CaptionNativeEvents(self)
            QApplication.instance().installNativeEventFilter(self.native_filter)
            window.destroyed.connect(self._dispose)
            self._refresh_native_frame(geometry)

    def eventFilter(self, watched, event):
        if (event.type() == QEvent.Type.WinIdChange
                and watched is getattr(self, 'window', None)):
            # QWidget.destroy() deletes its QWindow. A later winId()/show()
            # creates a new one, with new safe-area and screen signals.
            self._bind_window_handle()
        elif watched is getattr(self, 'window', None) and event.type() in (
                QEvent.Type.Show, QEvent.Type.Resize, QEvent.Type.WindowStateChange,
                QEvent.Type.PaletteChange, QEvent.Type.DevicePixelRatioChange):
            self._sync_caption_surface()
        return super().eventFilter(watched, event)

    def _dispose(self, *_args):
        self.hwnd = 0
        self.caption_replaced = False
        if self.native_filter is not None:
            QApplication.instance().removeNativeEventFilter(self.native_filter)
            self.native_filter = None

    def _refresh_native_frame(self, geometry):
        self.native_api.refresh_frame(self.hwnd)
        # SWP_FRAMECHANGED keeps the outer rectangle. Removing its old NC
        # margins would otherwise enlarge/move the client on every HWND rebuild.
        self.handle.setGeometry(geometry)
        self.window.setGeometry(geometry)

    def native_notification(self, hwnd, message, parameter=0, previous=0):
        if self.native_api and self.hwnd and hwnd == self.hwnd and message == 0x0083 and parameter:
            if self.native_api.calculate_client_rect(hwnd, previous, self.window.isFullScreen()):
                return True, 0
        if not self.caption_replaced or not self.hwnd or hwnd != self.hwnd:
            return False, 0
        if message == 0x0086:  # WM_NCACTIVATE: retain activation, omit NC repaint.
            self.caption_surface.update()
            return True, self.native_api.activate_without_repaint(hwnd, parameter, previous)
        if message == 0x0085:  # DWM composes the border; no default GDI frame repaint.
            self.native_api.clear_nonclient_background(hwnd)
            return True, 0
        if message in (0x0200, 0x00A0, 0x02A3, 0x02A2, 0x0084):
            # Mouse/client/non-client movement and hit testing. Merely repaint;
            # do not consume hit tests, clicks, system commands or snap events.
            self.caption_surface.update_hover()
        return False, 0

    def _sync_caption_surface(self):
        if self.native_api is None or self.handle is None or not self.hwnd:
            return
        self.caption_replaced = self.native_api.hide_qt_caption(self.hwnd)
        surface = self.caption_surface
        if not self.caption_replaced:
            # A future Qt backend may replace the private decoration window.
            # Fall back to its complete chrome, never draw two sets of buttons.
            surface.hide()
            return
        height, button_width = self.native_api.metrics(self.hwnd)
        ratio = max(1.0, self.window.devicePixelRatioF())
        surface.title_height = height / ratio
        surface.button_width = button_width / ratio
        width = math.ceil(3 * surface.button_width)
        surface.setGeometry(self.window.width() - width, 0, width, math.ceil(surface.title_height))
        surface.setVisible(not self.window.isFullScreen())
        surface.raise_()
        surface.update_hover()
        surface.update()

    def _bind_window_handle(self):
        geometry = self.window.geometry()
        handle = self.window.windowHandle()
        if handle is self.handle:
            return
        if self.handle is not None:
            self.handle.safeAreaMarginsChanged.disconnect(self._update_inset)
            self.handle.screenChanged.disconnect(self._update_inset)
            self.handle.destroyed.disconnect(self._handle_destroyed)
        self.handle = handle
        if handle is not None:
            self.hwnd = int(handle.winId())
            handle.safeAreaMarginsChanged.connect(self._update_inset)
            handle.screenChanged.connect(self._update_inset)
            handle.destroyed.connect(self._handle_destroyed)
            self._update_inset()
            if self.native_api and self.native_filter:
                self._refresh_native_frame(geometry)

    def _handle_destroyed(self, *_args):
        self.handle = None
        self.hwnd = 0
        self.caption_replaced = False

    def _update_inset(self, *_args):
        if self.handle is None:
            return
        inset = caption_inset(self.handle.safeAreaMargins())
        if inset != self.caption_right_inset:
            self.caption_right_inset = inset
            self.insetChanged.emit(inset)
        self._sync_caption_surface()


def install_expanded_chrome(window):
    """Return the chrome controller, or None on unsupported platforms.

    Call after native window flags and the central widget are configured, and
    before restoring saved geometry or showing the window. Keep the result on
    the window; reserve ``caption_right_inset`` in the global header's right
    margin and follow ``insetChanged`` for screen changes. This keeps the
    existing title for the taskbar and does not enable FramelessWindowHint.
    """
    if not supported():
        return None
    existing = getattr(window, '_expanded_window_chrome', None)
    if existing is not None:
        return existing
    if window.isVisible():
        raise RuntimeError('Expanded chrome must be installed before showing the window')
    controller = ExpandedWindowChrome(window)
    window._expanded_window_chrome = controller
    return controller
