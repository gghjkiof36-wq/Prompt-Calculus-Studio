"""Optional Windows Canvas composition, isolated from the workspace surface."""
import time
import weakref

from PySide6.QtCore import QCoreApplication, QEvent, QObject, QPoint, QTimer, Qt
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid


_controllers = weakref.WeakSet()
_failed = False
_native_api = None


def create_viewport(view):
    from PySide6.QtGui import QPaintEvent
    from PySide6.QtOpenGLWidgets import QOpenGLWidget

    class CanvasViewport(QOpenGLWidget):
        def paintGL(self):
            # QWidget.grab() bypasses viewport Paint events and invokes paintGL.
            # The default implementation clears our scene FBO. Use the same
            # full scene paint for these snapshots; normal view paints remain
            # dispatched by QGraphicsView's viewport event filter.
            view.paintEvent(QPaintEvent(self.rect()))

    return CanvasViewport(view)


def requested(canvas):
    owner = getattr(canvas.window, 'workspace_owner', None)
    if owner is not None:
        return bool(getattr(owner, 'canvas_hardware_acceleration_active', True))
    settings = getattr(canvas.window, 'state', {}).get('settings', {})
    return settings.get('canvas_hardware_acceleration', True) is not False


def report_fallback(canvas):
    owner = getattr(canvas.window, 'workspace_owner', None)
    if owner is not None:
        callback = getattr(owner, 'report_canvas_acceleration_fallback', None)
        if callable(callback):
            callback()
        else:
            owner.canvas_hardware_acceleration_fallback = True


class CanvasAcceleration(QObject):
    """One viewport owner; a failed context disables retries for this process."""
    def __init__(self, view, canvas):
        super().__init__(view)
        self.view, self.canvas = view, canvas
        self.state = 'raster'
        self.error = None
        self.renderer = None
        self.deadline = None
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.check_context)
        self.viewport = None
        self.native = False
        self._switching = False
        _controllers.add(self)
        if QApplication.platformName() != 'windows' or not requested(canvas):
            return
        if _failed:
            report_fallback(canvas)
            return
        try:
            self.viewport = create_viewport(view)
            view.setViewport(self.viewport)
            self.state = 'pending'
            view.installEventFilter(self)
            self.viewport.installEventFilter(self)
        except (ImportError, RuntimeError) as error:
            self.fail(str(error))

    def finalize(self):
        """The GL child and Canvas layout must exist before native creation."""
        if self.state != 'pending' or self.native:
            return
        from .webengine_host import prepare_native_host
        try:
            prepare_native_host(self.view)
            self.native = True
        except RuntimeError as error:
            self.native = self.view.testAttribute(Qt.WidgetAttribute.WA_NativeWindow)
            self.fail(str(error))
        # Do not call winId here: show must create the platform tree after its
        # real parents/layout are attached, with the GL requirement already set.

    def eventFilter(self, watched, event):
        if self.state == 'pending' and event.type() in (QEvent.Type.Show, QEvent.Type.Resize):
            self.timer.start(0)
        elif event.type() == QEvent.Type.Hide and watched is self.view:
            self.timer.stop()
            self.deadline = None
        return False

    def native_event(self, event_type, message):
        """Let Windows deliver covered points to the same-thread alien overlay."""
        if bytes(event_type) != b'windows_generic_MSG':
            return False, 0
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message != 0x0084:
            return False, 0
        # A fallback can retain its own native raster surface. It still needs
        # overlay routing; an alien view must never use its ancestor's HWND.
        handle = self.view.windowHandle()
        if handle is None or msg.hWnd != int(handle.winId()):
            return False, 0
        grab = QWidget.mouseGrabber()
        scene = self.view.scene()
        if ((grab is not None and (grab is self.view or self.view.isAncestorOf(grab)))
                or getattr(self.view, 'pan', None) is not None
                or (scene is not None and scene.mouseGrabberItem() is not None)):
            return False, 0
        global _native_api
        if _native_api is None:
            _native_api = ctypes.WinDLL('user32', use_last_error=True)
            _native_api.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
            _native_api.ScreenToClient.restype = wintypes.BOOL
            _native_api.GetCapture.argtypes = []
            _native_api.GetCapture.restype = wintypes.HWND
        # Qt's implicit Windows mouse capture is not QWidget.mouseGrabber().
        # Keep native delivery while a viewport control is being dragged.
        if _native_api.GetCapture() == msg.hWnd:
            return False, 0
        # WM_NCHITTEST supplies signed physical screen coordinates. Convert to
        # this native client's local pixels before applying its current DPR;
        # dividing global coordinates would break non-primary monitor origins.
        point = wintypes.POINT(ctypes.c_short(msg.lParam & 0xffff).value,
                              ctypes.c_short((msg.lParam >> 16) & 0xffff).value)
        if not _native_api.ScreenToClient(msg.hWnd, ctypes.byref(point)):
            return False, 0
        ratio = self.view.devicePixelRatioF()
        local = QPoint(round(point.x / ratio), round(point.y / ratio))
        if not self.view.rect().contains(local):
            return False, 0
        root = self.view.window()
        target = root.childAt(self.view.mapTo(root, local))
        if (target is None or target is self.view or self.view.isAncestorOf(target)
                or target.isAncestorOf(self.view)):
            return False, 0
        # Only a visible alien overlay above this Canvas. Other native widgets
        # retain their own Windows hit-test routing; the workspace is unchanged.
        ancestor = target
        while ancestor is not None and ancestor is not root:
            if not ancestor.isVisible() or ancestor.windowHandle() is not None:
                return False, 0
            ancestor = ancestor.parentWidget()
        return (True, -1) if ancestor is root else (False, 0)  # HTTRANSPARENT

    def check_context(self):
        if self.state != 'pending' or not self.view.isVisible():
            return
        handle = self.view.windowHandle()
        if handle is None or not handle.isExposed():
            self.timer.start(50)
            return
        viewport = self.viewport
        if viewport.isValid() and viewport.context() and viewport.context().isValid():
            try:
                viewport.makeCurrent()
                functions = viewport.context().functions()
                vendor, renderer = functions.glGetString(0x1F00), functions.glGetString(0x1F01)
                self.renderer = {'vendor': str(vendor), 'renderer': str(renderer)}
                name = (str(vendor) + ' ' + str(renderer)).lower()
                hardware = bool(vendor and renderer) and not any(part in name for part in
                    ('software', 'llvmpipe', 'softpipe', 'swiftshader', 'gdi generic', 'basic render', 'warp'))
            except RuntimeError:
                hardware = False
            finally:
                viewport.doneCurrent()
            if not hardware:
                self.fail('OpenGL hardware renderer is unavailable')
                return
            self.state = 'active'
            self.deadline = None
            return
        now = time.monotonic()
        if self.deadline is None:
            self.deadline = now + 3.
        if now >= self.deadline:
            self.fail('OpenGL context could not initialize')
        else:
            self.timer.start(50)

    def fail(self, reason):
        global _failed
        _failed = True
        self.error = str(reason)
        # This also handles an earlier canvas whose context worked: one failed
        # driver initialization must not repeatedly retry in subsequent tabs.
        for controller in tuple(_controllers):
            if (isValid(controller) and isValid(controller.view)
                    and (controller is self or controller.state in ('pending', 'active'))):
                report_fallback(controller.canvas)
                controller.to_raster()

    def to_raster(self):
        if self.state == 'raster' or self._switching:
            return
        self._switching = True
        self.timer.stop()
        view = self.view
        old = view.viewport()
        hidden, geometry = view.isHidden(), view.geometry()
        focus = QApplication.focusWidget()
        focused = focus is not None and (focus is view or view.isAncestorOf(focus))
        bars = view.horizontalScrollBar(), view.verticalScrollBar()
        scroll = tuple(bar.value() for bar in bars)
        children = [(child, child.isHidden(), child.geometry()) for child in
                    old.findChildren(QWidget, options=Qt.FindChildOption.FindDirectChildrenOnly)]
        cache = getattr(view, 'pan_raster', None)
        attribute = Qt.ApplicationAttribute.AA_DontCreateNativeWidgetSiblings
        previous = QCoreApplication.testAttribute(attribute)
        QCoreApplication.setAttribute(attribute, True)
        try:
            view.hide()
            if cache is not None:
                cache.end()
                old.removeEventFilter(cache)
            for child, _, _ in children:
                # Fixed viewport controls watch its Resize/Show events. Moving
                # their QObject parent alone does not transfer that filter.
                old.removeEventFilter(child)
                child.setParent(view)
            replacement = QWidget(view)
            replacement.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, True)
            replacement.setAcceptDrops(old.acceptDrops())
            replacement.setMouseTracking(old.hasMouseTracking())
            view.setViewport(replacement)
            self.viewport = None
            if self.native:
                view.setAttribute(Qt.WidgetAttribute.WA_NativeWindow, False)
                view.destroy(True, False)
                view.setAttribute(Qt.WidgetAttribute.WA_DontCreateNativeAncestors, False)
                self.native = False
            for child, was_hidden, child_geometry in children:
                child.setParent(replacement)
                child.setGeometry(child_geometry)
                child.setVisible(not was_hidden)
                replacement.installEventFilter(child)
            if cache is not None:
                replacement.installEventFilter(cache)
            view.setGeometry(geometry)
            self.state = 'raster'
            if not hidden:
                view.show()
            for bar, value in zip(bars, scroll):
                bar.setValue(value)
            if focused and isValid(focus) and view.window().isActiveWindow():
                focus.setFocus(Qt.FocusReason.OtherFocusReason)
            replacement.update()
        finally:
            QCoreApplication.setAttribute(attribute, previous)
            self._switching = False
