"""Native composition and recovery keep the existing scene and viewport controls."""
from types import SimpleNamespace
import copy
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QEventLoop, QTimer, Qt
from PySide6.QtGui import QColor, QSurface
from PySide6.QtWidgets import (QApplication, QGraphicsScene, QGraphicsView, QLabel,
                               QLineEdit, QRubberBand, QVBoxLayout, QWidget)
from shiboken6 import isValid

from prompt_calculus_studio import canvas_acceleration as acceleration
from prompt_calculus_studio.canvas_pan_cache import PanRasterCache


APP = QApplication.instance() or QApplication([])


class CountingView(QGraphicsView):
    paint_count = paint_depth = max_paint_depth = 0

    def paintEvent(self, event):
        self.paint_count += 1
        self.paint_depth += 1
        self.max_paint_depth = max(self.max_paint_depth, self.paint_depth)
        try:
            super().paintEvent(event)
        finally:
            self.paint_depth -= 1


class AnchoredControl(QLabel):
    def __init__(self, parent):
        super().__init__('Fixed control', parent)
        self.resize(100, 24)
        self.resize_events = 0
        parent.installEventFilter(self)

    def eventFilter(self, watched, event):
        if watched is self.parentWidget() and event.type() == QEvent.Type.Resize:
            self.resize_events += 1
            self.move(watched.width()-self.width(), watched.height()-self.height())
        return False


class CanvasAccelerationTests(unittest.TestCase):
    def setUp(self):
        self.old_failed = acceleration._failed
        acceleration._failed = False
        self.root = QWidget()
        self.root.setWindowTitle('PCS isolated Canvas context check')
        self.root.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.root.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        self.root.resize(640, 420)
        self.owner = SimpleNamespace(canvas_hardware_acceleration_active=True,
                                     canvas_hardware_acceleration=True,
                                     canvas_hardware_acceleration_fallback=False)
        self.canvas = QWidget(self.root)
        self.canvas.window = SimpleNamespace(workspace_owner=self.owner, state={})
        QVBoxLayout(self.root).addWidget(self.canvas)
        self.view = CountingView(self.canvas)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.control = acceleration.CanvasAcceleration(self.view, self.canvas)
        QVBoxLayout(self.canvas).addWidget(self.view)
        self.scene = QGraphicsScene(self.view)
        self.scene.setSceneRect(-1000, -1000, 2000, 2000)
        self.view.setScene(self.scene)
        self.edit = QLineEdit('Original draft remains editable')
        self.proxy = self.scene.addWidget(self.edit)
        self.fixed = AnchoredControl(self.view.viewport())
        self.rubber = QRubberBand(QRubberBand.Shape.Rectangle, self.view.viewport())
        self.rubber.hide()
        self.view.pan_raster = PanRasterCache(self.view)
        self.control.finalize()

    def tearDown(self):
        self.root.close();self.root.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        acceleration._failed = self.old_failed

    def settle(self, predicate, timeout=4500):
        if predicate():return
        loop=QEventLoop();poll=QTimer();limit=QTimer()
        poll.setInterval(20);limit.setSingleShot(True)
        poll.timeout.connect(lambda:loop.quit() if predicate() else None)
        limit.timeout.connect(loop.quit);poll.start();limit.start(timeout)
        try:loop.exec()
        finally:poll.stop();limit.stop()
        self.assertTrue(predicate(), self.control.error)

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows GL context')
    def test_real_context_isolated_from_root_and_no_sibling_promotion(self):
        self.root.show()
        self.settle(lambda:self.control.state == 'active')
        self.assertEqual(self.root.windowHandle().surfaceType(), QSurface.SurfaceType.RasterSurface)
        self.assertEqual(self.view.windowHandle().surfaceType(), QSurface.SurfaceType.OpenGLSurface)
        native=[w for w in [self.root,*self.root.findChildren(QWidget)] if w.windowHandle() is not None]
        self.assertEqual(set(native), {self.root,self.view})
        self.assertIs(self.rubber.parentWidget(), self.view.viewport())
        self.assertFalse(self.rubber.isVisible())
        self.assertIs(self.view.scene(), self.scene)

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows GL context')
    def test_failed_context_returns_to_raster_preserving_scene_controls_and_camera(self):
        self.root.show()
        self.settle(lambda:self.control.state == 'active')
        old=self.view.viewport()
        self.view.scale(1.04,1.04)
        self.view.horizontalScrollBar().setValue(123)
        self.view.verticalScrollBar().setValue(-87)
        self.edit.setText('Unsaved draft, emoji 🌿')
        self.edit.setCursorPosition(7);self.edit.setSelection(2,5)
        selection=(self.edit.cursorPosition(),self.edit.selectionStart(),self.edit.selectedText())
        before=(self.view.transform(),self.view.horizontalScrollBar().value(),self.view.verticalScrollBar().value())
        self.control.fail('Injected startup failure')
        APP.processEvents()
        self.assertEqual(self.control.state, 'raster')
        self.assertFalse(isValid(old))
        self.assertTrue(self.owner.canvas_hardware_acceleration_fallback)
        self.assertTrue(self.owner.canvas_hardware_acceleration)
        self.assertTrue(self.owner.canvas_hardware_acceleration_active)
        self.assertIs(self.view.scene(), self.scene)
        self.assertIs(self.proxy.widget(), self.edit)
        self.assertEqual(self.edit.text(), 'Unsaved draft, emoji 🌿')
        self.assertEqual((self.edit.cursorPosition(),self.edit.selectionStart(),self.edit.selectedText()),selection)
        self.assertEqual((self.view.transform(),self.view.horizontalScrollBar().value(),self.view.verticalScrollBar().value()),before)
        self.assertIs(self.fixed.parentWidget(), self.view.viewport())
        self.assertIs(self.rubber.parentWidget(), self.view.viewport())
        self.assertTrue(self.rubber.isHidden())
        count=self.fixed.resize_events
        self.root.resize(770,530)
        self.settle(lambda:self.fixed.resize_events>count)
        self.assertEqual(self.fixed.x()+self.fixed.width(),self.view.viewport().width())
        self.assertEqual(self.fixed.y()+self.fixed.height(),self.view.viewport().height())
        self.assertEqual(self.root.windowHandle().surfaceType(), QSurface.SurfaceType.RasterSurface)
        handle=self.view.windowHandle()
        if handle is not None:
            self.assertEqual(handle.surfaceType(),QSurface.SurfaceType.RasterSurface)
        second=QGraphicsView(self.canvas)
        try:
            disabled=acceleration.CanvasAcceleration(second,self.canvas)
            self.assertEqual(disabled.state,'raster')
        finally:second.deleteLater()

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows GL context')
    def test_software_GL_and_timeout_use_the_same_recovery(self):
        self.root.show()
        self.settle(lambda:self.control.state=='active')
        self.control.state='pending'
        functions=SimpleNamespace(glGetString=lambda token:'Microsoft Corporation' if token==0x1F00 else 'GDI Generic')
        context=SimpleNamespace(isValid=lambda:True,functions=lambda:functions)
        with patch.object(self.control.viewport,'context',return_value=context):
            self.control.check_context()
        self.assertEqual(self.control.state,'raster')
        self.assertEqual(self.control.error,'OpenGL hardware renderer is unavailable')
        self.assertTrue(self.owner.canvas_hardware_acceleration_fallback)

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows GL context')
    def test_timeout_does_not_mark_a_workspace_with_acceleration_disabled(self):
        self.root.show()
        self.settle(lambda:self.control.state=='active')
        other=QWidget(self.root)
        owner=SimpleNamespace(canvas_hardware_acceleration_active=False,canvas_hardware_acceleration_fallback=False)
        other.window=SimpleNamespace(workspace_owner=owner,state={})
        view=QGraphicsView(other)
        inactive=acceleration.CanvasAcceleration(view,other)
        self.control.state='pending';self.control.deadline=time.monotonic()-1
        with patch.object(self.control.viewport,'isValid',return_value=False):
            self.control.check_context()
        self.assertEqual(self.control.state,'raster')
        self.assertEqual(inactive.state,'raster')
        self.assertFalse(owner.canvas_hardware_acceleration_fallback)

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows hit-test coordinates')
    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows hit testing')
    def test_hit_test_only_yields_covered_alien_points_without_pan_capture(self):
        import ctypes
        from ctypes import wintypes
        self.root.show();self.settle(lambda:self.control.state=='active')
        local=self.view.rect().center()
        overlay=QLabel('Alien overlay',self.root)
        pos=self.view.mapTo(self.root,local)
        overlay.setGeometry(pos.x()-40,pos.y()-20,80,40)
        overlay.show();overlay.raise_();APP.processEvents()
        self.assertIs(self.root.childAt(pos),overlay)
        handle=int(self.view.effectiveWinId())
        physical=wintypes.POINT(round(local.x()*self.view.devicePixelRatioF()),round(local.y()*self.view.devicePixelRatioF()))
        user=ctypes.WinDLL('user32')
        user.ClientToScreen.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.POINT)]
        self.assertTrue(user.ClientToScreen(handle,ctypes.byref(physical)))
        msg=wintypes.MSG();msg.hWnd=handle;msg.message=0x0084
        msg.lParam=(physical.x&0xffff)|((physical.y&0xffff)<<16)
        call=lambda:self.control.native_event(b'windows_generic_MSG',ctypes.addressof(msg))
        self.assertEqual(call(),(True,-1))
        with patch.object(acceleration._native_api,'GetCapture',return_value=handle):
            self.assertEqual(call(),(False,0))
        self.view.pan=local
        self.assertEqual(call(),(False,0))
        self.view.pan=None
        msg.hWnd=int(self.root.effectiveWinId())
        self.assertEqual(call(),(False,0))
        msg.hWnd=handle;overlay.hide()
        self.assertEqual(call(),(False,0))
        self.control.fail('Injected fallback before overlay routing')
        APP.processEvents();overlay.show();overlay.raise_()
        own=self.view.windowHandle()
        if own is not None:
            msg.hWnd=int(own.winId())
            physical=wintypes.POINT(round(local.x()*self.view.devicePixelRatioF()),round(local.y()*self.view.devicePixelRatioF()))
            user.ClientToScreen(msg.hWnd,ctypes.byref(physical))
            msg.lParam=(physical.x&0xffff)|((physical.y&0xffff)<<16)
            self.assertEqual(call(),(True,-1),dict(overlay_native=overlay.windowHandle() is not None,
                target=type(self.root.childAt(pos)).__name__,view_geometry=self.view.geometry().getRect(),
                view_native=self.view.testAttribute(Qt.WidgetAttribute.WA_NativeWindow),overlay_visible=overlay.isVisible()))
        with patch.object(self.view,'windowHandle',return_value=None):
            self.assertEqual(call(),(False,0))

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows GL context')
    def test_native_boundary_failure_falls_back_before_show(self):
        from prompt_calculus_studio import webengine_host
        self.control.native=False
        with patch.object(webengine_host,'prepare_native_host',side_effect=RuntimeError('native setup failed')):
            self.control.finalize()
        self.assertEqual(self.control.state,'raster')
        self.assertTrue(self.owner.canvas_hardware_acceleration_fallback)
        self.root.show();APP.processEvents()
        self.assertEqual(self.root.windowHandle().surfaceType(),QSurface.SurfaceType.RasterSurface)

    @unittest.skipUnless(APP.platformName() == 'windows', 'Requires native Windows GL context')
    def test_owning_widget_snapshot_preserves_scene_framebuffer(self):
        self.root.show();self.settle(lambda:self.control.state=='active')
        center=self.view.mapToScene(self.view.viewport().rect().center())
        rect=self.scene.addRect(center.x()-70,center.y()-60,140,120,
                                brush=QColor('#d73151'))
        rect.setZValue(1000)
        self.view.viewport().repaint();APP.processEvents()
        count=self.view.paint_count
        self.view.viewport().repaint()
        self.assertEqual(self.view.paint_count-count,1)
        local=self.view.viewport().rect().center()
        root_point=self.view.viewport().mapTo(self.root,local)
        # PopupSurface samples its owning widget, which calls Qt's GL grab path.
        count=self.view.paint_count
        image=self.root.grab().toImage()
        self.assertEqual(self.view.paint_count-count,1)
        self.assertEqual(self.view.max_paint_depth,1)
        ratio=image.devicePixelRatio()
        self.assertEqual(image.pixelColor(round(root_point.x()*ratio),round(root_point.y()*ratio)).name(),'#d73151')
        frame=self.view.viewport().grabFramebuffer()
        self.assertEqual(frame.pixelColor(round(local.x()*frame.devicePixelRatio()),round(local.y()*frame.devicePixelRatio())).name(),'#d73151')

    def test_startup_policy_is_frozen_not_current_requested_preference(self):
        self.owner.canvas_hardware_acceleration=False
        self.assertTrue(acceleration.requested(self.canvas))
        self.owner.canvas_hardware_acceleration_active=False
        self.owner.canvas_hardware_acceleration=True
        self.assertFalse(acceleration.requested(self.canvas))
        if APP.platformName() != 'windows':
            self.assertEqual(self.control.state,'raster')
            self.assertIsNone(self.view.windowHandle())


import test_workspace_shell as workspace_fixture
from prompt_calculus_studio.workspace_shell import WorkspaceShell


@unittest.skipUnless(APP.platformName()=='windows','Requires actual product Windows Canvas')
class CanvasAccelerationWorkspaceTests(unittest.TestCase):
    setUpClass=classmethod(workspace_fixture.WorkspaceShellTests.setUpClass.__func__)
    events=workspace_fixture.WorkspaceShellTests.events
    settle=CanvasAccelerationTests.settle

    def setUp(self):
        self.old_failed=acceleration._failed;acceleration._failed=False
        original=WorkspaceShell.__init__
        def nonactivating(shell,*args,**kwargs):
            original(shell,*args,**kwargs)
            shell.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
        with patch.object(WorkspaceShell,'__init__',nonactivating):
            workspace_fixture.WorkspaceShellTests.setUp(self)

    def tearDown(self):
        try:workspace_fixture.WorkspaceShellTests.tearDown(self)
        finally:acceleration._failed=self.old_failed

    def test_product_viewport_fallback_keeps_document_and_controls_live(self):
        tab=self.w.open_path(self.root/'Street.pcs-workflow')
        canvas=tab.host.canvas;self.view=canvas.view;self.control=self.view.acceleration
        self.events();self.settle(lambda:self.control.state=='active')
        before=copy.deepcopy(tab.host.state);original=tab.path.read_bytes();scene=self.view.scene()
        canvas.zoom_to(1.04);self.events()
        self.control.fail('Injected product-path failure')
        self.events()
        self.assertEqual(self.control.state,'raster')
        self.assertEqual(self.w.windowHandle().surfaceType(),QSurface.SurfaceType.RasterSurface)
        self.assertIs(self.view.scene(),scene)
        # Camera state is intentionally a UI setting; compare content before/after fallback.
        self.assertEqual(tab.path.read_bytes(),original)
        self.assertEqual(tab.host.state['uses'],before['uses'])
        for widget in (canvas.execution_bar,canvas.zoom_controls,canvas.recent_control,self.view.rubber):
            self.assertIs(widget.parentWidget(),self.view.viewport())
        with (patch.object(canvas.zoom_controls,'place',wraps=canvas.zoom_controls.place) as zoom_place,
              patch.object(canvas.recent_control,'place',wraps=canvas.recent_control.place) as recent_place):
            self.w.resize(self.w.width()+130,self.w.height()+80)
            self.events();self.events()
            self.assertGreater(zoom_place.call_count,0);self.assertGreater(recent_place.call_count,0)
        self.assertTrue(self.view.viewport().rect().contains(canvas.recent_control.geometry()))
        self.assertTrue(self.view.viewport().rect().contains(canvas.zoom_controls.geometry()))
        self.view.viewport().repaint();self.events()
        self.assertFalse(self.view.viewport().grab().isNull())
        self.assertEqual(self.errors,[])


if __name__ == '__main__':unittest.main()
