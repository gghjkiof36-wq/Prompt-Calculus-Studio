"""Camera-only backing reuse stays opaque, sharp, bounded and live."""
import math
import os
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen, QTransform, QWheelEvent, QMouseEvent
from PySide6.QtWidgets import (QApplication, QGraphicsRectItem, QGraphicsScene,
                               QGraphicsView, QLabel, QLineEdit, QVBoxLayout, QWidget)
from PySide6.QtTest import QTest

from prompt_calculus_studio.canvas_pan_cache import PanRasterCache


APP = QApplication.instance() or QApplication([])


class PaintedItem(QGraphicsRectItem):
    paints = 0

    def paint(self, *args):
        self.paints += 1
        super().paint(*args)


class GridView(QGraphicsView):
    def drawBackground(self, painter, rect):
        painter.fillRect(rect, QColor('#242829'))
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        pen = QPen(QColor('#58645b'), 1); pen.setCosmetic(True)
        painter.setPen(pen)
        for x in range(math.floor(rect.left()/28)*28, math.ceil(rect.right()), 28):
            for y in range(math.floor(rect.top()/28)*28, math.ceil(rect.bottom()), 28):
                painter.drawPoint(QPointF(x, y))
        painter.restore()


class CanvasPanCacheTests(unittest.TestCase):
    def setUp(self):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        self.view = GridView()
        self.scene = QGraphicsScene(self.view)
        self.view.setScene(self.scene)
        self.scene.setSceneRect(-2000, -2000, 4000, 4000)
        self.view.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.view.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.item = PaintedItem(QRectF(10, 25, 125, 65))
        self.item.setBrush(QColor('#82969c')); self.scene.addItem(self.item)
        self.edit = QLineEdit('Live result 123')
        self.edit.setFont(QFont('Microsoft JhengHei UI', 12))
        self.edit.resize(185, 35)
        self.proxy = self.scene.addWidget(self.edit); self.proxy.setPos(-110, -45)
        self.overlay = QLabel('VIEWPORT ONLY', self.view.viewport())
        self.overlay.setStyleSheet('background:#ff00ff;color:white')
        self.overlay.setGeometry(8, 8, 135, 22); self.overlay.show()
        self.view.resize(440, 320); self.view.show(); self.view.centerOn(0, 0)
        self.events()
        self.cache = PanRasterCache(self.view)
        self.cache.begin()

    def tearDown(self):
        self.cache.end(); self.view.close(); self.view.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()

    def events(self):
        APP.processEvents(); APP.processEvents()

    def frame(self, *, cached=True, dpr=1.25, appearance=None, cache=None):
        rect = self.view.viewport().rect()
        image = QImage(math.ceil(rect.width()*dpr), math.ceil(rect.height()*dpr),
                       QImage.Format.Format_RGB32)
        image.setDevicePixelRatio(dpr); image.fill(QColor('#ff00ff'))
        painter = QPainter(image)
        painter.setRenderHints(self.view.renderHints())
        used = (cache or self.cache).paint(painter, appearance) if cached else False
        if not used:
            self.view.render(painter, QRectF(rect), rect, Qt.AspectRatioMode.IgnoreAspectRatio)
        painter.end()
        return image, used

    def assert_pixels(self, actual, direct, *, roundoff=False):
        self.assertEqual(actual.size(), direct.size())
        first, second = bytes(actual.constBits()), bytes(direct.constBits())
        if first == second:
            return
        delta = [abs(a-b) for a,b in zip(first, second)]
        changed = sum(a != b for a,b in zip(first, second))
        # An extended render target changes Qt's floating-point operation order.
        # At scale 1.04/DPR 2 the native edit's corner had one pixel with two
        # channels differing by one. Allow only that tiny AA rounding envelope;
        # integer translations and the no-interpolation checks stay exact.
        if roundoff and max(delta) <= 1 and changed <= 32:
            return
        if os.environ.get('PCS_PAN_CACHE_QA'):
            path = Path(os.environ['PCS_PAN_CACHE_QA'])
            actual.save(str(path/(self._testMethodName+'-actual.png')))
            direct.save(str(path/(self._testMethodName+'-direct.png')))
        self.fail(f'Raster differs: max channel delta={max(delta)}, changed channels={changed}')

    def test_integer_device_pan_reuses_scene_and_keeps_opaque_pixels(self):
        initial, used = self.frame()
        self.assertTrue(used); self.assertFalse(self.cache.image.hasAlphaChannel())
        count = self.item.paints
        bar = self.view.horizontalScrollBar()
        for _ in range(8):
            bar.setValue(bar.value()+4)  # Five physical pixels at 125%.
            actual, used = self.frame()
            self.assertTrue(used)
            self.assertEqual(self.item.paints, count)
        self.assertEqual(self.cache.captures, 1)
        self.assertEqual(self.cache.hits, 8)
        self.assert_pixels(actual, self.frame(cached=False)[0])

    def test_one_logical_pixel_pan_at_125_percent_never_snaps_to_four(self):
        original, _ = self.frame()
        bar = self.view.horizontalScrollBar(); start = bar.value()
        offsets = []
        for step in range(1, 9):
            bar.setValue(start+step)
            actual, used = self.frame()
            self.assertTrue(used)
            dx, dy = self.cache.last_offset
            self.assertEqual(dy, 0)
            self.assertLessEqual(abs(dx+step*1.25), .5)
            offsets.append(dx)
            # Reused interior bytes are a pure integer translation, not an
            # interpolated image: glyph/edge pixels remain bit-for-bit sharp.
            old, new = bytes(original.constBits()), bytes(actual.constBits())
            stride = actual.bytesPerLine()
            for y in range(20, actual.height()-20):
                begin, end = 20, actual.width()-20
                self.assertEqual(new[y*stride+begin*4:y*stride+end*4],
                                 old[y*stride+(begin-dx)*4:y*stride+(end-dx)*4])
        self.assertEqual(len(set(offsets)), 8)
        self.assertEqual(self.cache.captures, 1)
        self.assertEqual(bar.value(), start+8)
        self.cache.end()
        exact, used = self.frame()
        self.assertFalse(used); self.assertEqual(self.cache.byte_count, 0)
        self.assert_pixels(exact, self.frame(cached=False)[0])

    def test_live_proxy_and_scene_changes_rebuild_before_the_next_frame(self):
        original, _ = self.frame()
        self.edit.setText('UPDATED live result')
        self.events()
        updated, used = self.frame()
        self.assertTrue(used); self.assertNotEqual(updated, original)
        self.assertEqual(self.cache.captures, 2)
        self.assert_pixels(updated, self.frame(cached=False)[0])
        self.item.setBrush(QColor('#d2b066')); self.events()
        changed, _ = self.frame()
        self.assertEqual(self.cache.captures, 3)
        self.assert_pixels(changed, self.frame(cached=False)[0])
        self.scene.removeItem(self.item); self.events()
        removed, _ = self.frame()
        self.assertNotEqual(removed, changed)
        self.assert_pixels(removed, self.frame(cached=False)[0])

    def test_theme_scale_dpr_resize_and_scene_replacement_cannot_reuse_old_pixels(self):
        self.frame(appearance=('dark',))
        for action, dpr, appearance in (
            (lambda: None, 1.25, ('paper',)),
            (lambda: self.edit.setStyleSheet('color:#ffcc66;background:#343434'), 1.25, ('paper',)),
            (lambda: self.view.setTransform(QTransform.fromScale(1.04, 1.04)), 1.25, ('paper',)),
            (lambda: None, 2., ('paper',)),
            (lambda: self.view.resize(475, 335), 2., ('paper',)),
        ):
            count = self.cache.captures
            action(); self.events()
            actual, used = self.frame(dpr=dpr, appearance=appearance)
            self.assertTrue(used); self.assertEqual(self.cache.captures, count+1)
            self.assert_pixels(actual, self.frame(cached=False, dpr=dpr)[0], roundoff=True)
        new_scene = QGraphicsScene(self.view); new_scene.setSceneRect(-2000, -2000, 4000, 4000)
        new_scene.addRect(-80, -30, 90, 70, QPen(Qt.PenStyle.NoPen), QColor('#d4b080'))
        self.view.setScene(new_scene); self.events()
        actual, _ = self.frame()
        self.assertIs(self.cache._scene, new_scene)
        self.assert_pixels(actual, self.frame(cached=False)[0])

    def test_viewport_controls_are_not_part_of_the_scene_snapshot(self):
        actual, _ = self.frame(dpr=1.)
        self.assertNotEqual(actual.pixelColor(12, 12), QColor('#ff00ff'))
        # The true viewport child remains independent and visible.
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.view.viewport().grab().toImage().pixelColor(12, 12), QColor('#ff00ff'))

    def test_overscan_exit_rebuilds_and_memory_limit_falls_back_without_partial_frame(self):
        self.frame(); count = self.cache.captures
        bar = self.view.horizontalScrollBar(); bar.setValue(bar.value()+260)
        actual, used = self.frame()
        self.assertTrue(used); self.assertEqual(self.cache.captures, count+1)
        self.assert_pixels(actual, self.frame(cached=False)[0])
        self.cache.end()
        rect = self.view.viewport().rect(); full_bytes = rect.width()*rect.height()*4
        limited = PanRasterCache(self.view, max_bytes=full_bytes+8192)
        limited.begin()
        _, used = self.frame(dpr=1., cache=limited)
        self.assertTrue(used); self.assertLessEqual(limited.byte_count, full_bytes+8192)
        self.assertLess(limited._pad, 128)
        limited.end()
        too_small = PanRasterCache(self.view, max_bytes=full_bytes-1)
        too_small.begin()
        actual, used = self.frame(dpr=1., cache=too_small)
        self.assertFalse(used); self.assertEqual(too_small.byte_count, 0)
        self.assert_pixels(actual, self.frame(cached=False, dpr=1.)[0])
        too_small.end()

    def test_large_diagonal_pans_extend_edges_without_repainting_reused_content(self):
        for dpr in (1.,1.25,1.5,2.):
            with self.subTest(dpr=dpr):
                self.cache.end();self.view.centerOn(0,0);self.events()
                h,v=self.view.horizontalScrollBar(),self.view.verticalScrollBar()
                start=h.value(),v.value();positions=((160,144),(4,4),(-160,156),(8,0))
                expected=[]
                # Direct reference renders send widget Paint events. Build
                # them before the gesture so they cannot invalidate its cache.
                for dx,dy in positions:
                    h.setValue(start[0]+dx);v.setValue(start[1]+dy)
                    expected.append(self.frame(cached=False,dpr=dpr)[0])
                h.setValue(start[0]);v.setValue(start[1]);self.events();self.cache.begin();self.frame(dpr=dpr)
                for (dx,dy),reference in zip(positions,expected):
                    h.setValue(start[0]+dx);v.setValue(start[1]+dy)
                    before=self.cache.extensions;paints=self.item.paints
                    actual,used=self.frame(dpr=dpr)
                    self.assertTrue(used);self.assertEqual(self.cache.extensions,before+1)
                    self.assertEqual(self.item.paints,paints)
                    self.assert_pixels(actual,reference,roundoff=True)
                    self.assertLessEqual(self.cache.byte_count,self.cache.max_bytes)
                self.edit.setText('Updated after large pan '+str(dpr));self.events()
                actual,_=self.frame(dpr=dpr)
                self.assert_pixels(actual,self.frame(cached=False,dpr=dpr)[0],roundoff=True)

    def test_fractional_pan_extension_keeps_the_same_raster_phase(self):
        initial,_=self.frame(dpr=1.25)
        h,v=self.view.horizontalScrollBar(),self.view.verticalScrollBar()
        # A 129-pixel move crosses the backing with a quarter physical pixel
        # remainder. Returning must retain the original sharp pixels.
        start=h.value(),v.value()
        for dx,dy in ((129,133),(261,267),(129,133),(0,0)):
            h.setValue(start[0]+dx);v.setValue(start[1]+dy)
            actual,used=self.frame(dpr=1.25)
            self.assertTrue(used)
        self.assert_pixels(actual,initial,roundoff=True)

    def test_hidden_or_released_view_releases_buffer_and_rotated_view_uses_direct_paint(self):
        self.frame(); self.assertGreater(self.cache.byte_count, 0)
        self.view.hide()
        self.assertFalse(self.cache.active); self.assertEqual(self.cache.byte_count, 0)
        self.view.show(); self.events(); self.cache.begin()
        self.view.rotate(5)
        actual, used = self.frame()
        self.assertFalse(used); self.assertEqual(self.cache.byte_count, 0)
        self.assert_pixels(actual, self.frame(cached=False)[0])

    def test_canvas_view_press_pan_release_and_live_change_use_the_real_hook(self):
        from prompt_calculus_studio.canvas_items import CanvasView
        canvas = QWidget()
        canvas.window = SimpleNamespace(state={'workspace': 'qa'}, store=None)
        canvas._paint_colors = dict(base='#242829', canvas_background='#242829', line='#525757')
        layout = QVBoxLayout(canvas)
        view = CanvasView(canvas); layout.addWidget(view)
        view.scene().setSceneRect(-1000, -1000, 3000, 3000)
        item = PaintedItem(QRectF(-50, -50, 100, 100))
        item.setBrush(QColor('#9bbaa8')); view.scene().addItem(item)
        canvas.resize(470, 350); canvas.show(); view.centerOn(0, 0)
        try:
            self.events()
            QTest.mousePress(view.viewport(), Qt.MouseButton.MiddleButton,
                             Qt.KeyboardModifier.NoModifier, QPoint(100, 100))
            view.viewport().update(); self.events()
            self.assertTrue(view.pan_raster.active)
            self.assertIsNotNone(view.pan_raster.image)
            count = item.paints
            from prompt_calculus_studio import canvas_proxy_cache
            with patch.object(canvas_proxy_cache,'configure',wraps=canvas_proxy_cache.configure) as configure:
                for x in (110, 120, 130):
                    QTest.mouseMove(view.viewport(), QPoint(x, 100)); self.events()
                self.assertEqual(configure.call_count,0)
            self.assertEqual(item.paints, count)
            self.assertGreaterEqual(view.pan_raster.hits, 2)
            count = view.pan_raster.captures
            with patch.object(canvas_proxy_cache,'configure',wraps=canvas_proxy_cache.configure) as configure:
                item.setBrush(QColor('#e3a563')); self.events()
                self.assertGreater(configure.call_count,0)
            self.assertGreater(view.pan_raster.captures, count)
            paints = item.paints
            QTest.mouseRelease(view.viewport(), Qt.MouseButton.MiddleButton,
                               Qt.KeyboardModifier.NoModifier, QPoint(130, 100))
            self.events()
            self.assertIsNone(view.pan)
            self.assertFalse(view.pan_raster.active)
            self.assertEqual(view.pan_raster.byte_count, 0)
            self.assertGreater(item.paints, paints)
        finally:
            canvas.close(); canvas.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_high_rate_fractional_mouse_motion_is_not_discarded(self):
        from prompt_calculus_studio.canvas_items import CanvasView
        canvas=QWidget();canvas.window=SimpleNamespace(state={'workspace':'qa'},store=None)
        canvas._paint_colors=dict(base='#242829',canvas_background='#242829',line='#525757')
        layout=QVBoxLayout(canvas);view=CanvasView(canvas);layout.addWidget(view)
        view.scene().setSceneRect(-1000,-1000,3000,3000)
        canvas.resize(470,350);canvas.show();view.centerOn(0,0);self.events()
        def move(x,y):
            point=QPointF(x,y)
            APP.sendEvent(view.viewport(),QMouseEvent(QEvent.Type.MouseMove,point,point,
                Qt.MouseButton.NoButton,Qt.MouseButton.MiddleButton,Qt.KeyboardModifier.NoModifier))
        try:
            QTest.mousePress(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(100,100))
            before=(view.horizontalScrollBar().value(),view.verticalScrollBar().value())
            for i in range(1,101):move(100+i*.2,100+i*.125)
            self.assertEqual(view.horizontalScrollBar().value(),before[0]-20)
            self.assertLessEqual(abs(view.verticalScrollBar().value()-(before[1]-12.5)),.5)
            for i in range(99,-1,-1):move(100+i*.2,100+i*.125)
            self.assertEqual((view.horizontalScrollBar().value(),view.verticalScrollBar().value()),before)
            QTest.mouseRelease(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(100,100))
            QTest.mousePress(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(100,100))
            move(100.2,100.2)
            self.assertEqual((view.horizontalScrollBar().value(),view.verticalScrollBar().value()),before)
            QTest.mouseRelease(view.viewport(),Qt.MouseButton.MiddleButton,pos=QPoint(100,100))
        finally:
            canvas.close();canvas.deleteLater();APP.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def assert_full_frame(self, image):
        # frame() starts magenta. No pixel may be left uncovered by the camera
        # snapshot; the magenta viewport child is deliberately not in it.
        self.assertNotIn(0xffff00ff, memoryview(image.constBits()).cast('I'))

    def test_wheel_snapshot_reuses_scaled_camera_pixels_without_repainting_items(self):
        self.cache.end(); self.cache.begin_zoom()
        self.frame()
        image = self.cache.image
        key, paints, captures = image.cacheKey(), self.item.paints, self.cache.captures
        self.view.scale(1.15, 1.15)
        actual, used = self.frame()
        self.assertTrue(used); self.assertEqual(self.cache.hits, 1)
        self.assertEqual(self.cache.captures, captures)
        self.assertEqual(self.cache.image.cacheKey(), key)
        self.assertEqual(self.item.paints, paints)
        self.assertLessEqual(self.cache.byte_count, self.cache.max_bytes)
        self.assert_full_frame(actual)

    def test_zoom_ratio_or_camera_coverage_miss_recaptures_within_budget(self):
        self.cache.end(); self.cache.begin_zoom()
        self.frame()
        for name, action in (
            ('ratio', lambda: self.view.scale(1.6, 1.6)),
            ('uncovered_edge', lambda: self.view.horizontalScrollBar().setValue(
                self.view.horizontalScrollBar().value()+1400)),
        ):
            with self.subTest(reason=name):
                captures = self.cache.captures
                action()
                actual, used = self.frame()
                self.assertTrue(used)
                self.assertEqual(self.cache.captures, captures+1)
                self.assertLessEqual(self.cache.image.sizeInBytes(), self.cache.max_bytes)
                self.assertLessEqual(self.cache.byte_count, self.cache.max_zoom_bytes)
                self.assert_full_frame(actual)
                self.assert_pixels(actual, self.frame(cached=False)[0], roundoff=True)
        self.cache.end()
        rect = self.view.viewport().rect()
        limited = PanRasterCache(self.view, max_bytes=rect.width()*rect.height()*4-1)
        try:
            limited.begin_zoom()
            actual, used = self.frame(dpr=1., cache=limited)
            self.assertFalse(used); self.assertEqual(limited.byte_count, 0)
            self.assert_full_frame(actual)
            self.assert_pixels(actual, self.frame(cached=False, dpr=1.)[0])
        finally:
            limited.end()

    def test_live_scene_and_proxy_updates_replace_a_scaled_wheel_snapshot(self):
        self.cache.end(); self.cache.begin_zoom()
        self.frame(); self.view.scale(1.1, 1.1)
        self.frame()
        for name, action in (
            ('scene', lambda: self.item.setBrush(QColor('#efc861'))),
            ('proxy', lambda: self.edit.setText('Changed during wheel zoom')),
        ):
            with self.subTest(source=name):
                captures = self.cache.captures
                action(); self.events()
                self.assertIsNone(self.cache.image)
                actual, used = self.frame()
                self.assertTrue(used)
                self.assertEqual(self.cache.captures, captures+1)
                self.assert_full_frame(actual)
                self.assert_pixels(actual, self.frame(cached=False)[0], roundoff=True)

    def test_viewport_layout_keeps_wheel_pixels_but_proxy_layout_recaptures(self):
        self.cache.end(); self.cache.begin_zoom()
        self.frame()
        image, captures = self.cache.image, self.cache.captures
        APP.sendEvent(self.view.viewport(), QEvent(QEvent.Type.LayoutRequest))
        self.assertIs(self.cache.image, image)
        self.frame()
        self.assertEqual(self.cache.captures, captures)
        APP.sendEvent(self.edit, QEvent(QEvent.Type.LayoutRequest))
        self.assertIsNone(self.cache.image)
        actual, used = self.frame()
        self.assertTrue(used); self.assertEqual(self.cache.captures, captures+1)
        self.assert_pixels(actual, self.frame(cached=False)[0], roundoff=True)

    @contextmanager
    def real_canvas(self):
        from prompt_calculus_studio.canvas_items import CanvasView
        canvas = QWidget()
        canvas.window = SimpleNamespace(state={'workspace': 'qa'}, store=None)
        canvas._paint_colors = dict(base='#242829', canvas_background='#242829', line='#525757')
        layout = QVBoxLayout(canvas)
        view = CanvasView(canvas); layout.addWidget(view)
        view.scene().setSceneRect(-1000, -1000, 3000, 3000)
        item = PaintedItem(QRectF(-50, -50, 100, 100))
        item.setBrush(QColor('#9bbaa8')); view.scene().addItem(item)
        canvas.resize(470, 350); canvas.show(); view.centerOn(0, 0)
        self.events()
        try:
            yield view, item
        finally:
            canvas.close(); canvas.deleteLater()
            APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def wheel(self, view, delta=120):
        point = QPoint(20, 20)
        event = QWheelEvent(QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                            QPoint(), QPoint(0, delta), Qt.MouseButton.NoButton,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.ScrollUpdate, False)
        APP.sendEvent(view.viewport(), event)
        self.events()

    def test_actual_wheel_hook_settles_to_exact_paint_and_releases_after_120ms(self):
        with self.real_canvas() as (view, item):
            self.wheel(view)
            cache = view.pan_raster
            self.assertTrue(cache.active); self.assertTrue(cache.allow_zoom)
            self.assertEqual(cache.zoom_timer.interval(), 120)
            self.assertTrue(cache.zoom_timer.isActive()); self.assertIsNotNone(cache.image)
            captures, paints = cache.captures, item.paints
            self.wheel(view, 40)
            self.assertEqual(cache.captures, captures)
            self.assertEqual(item.paints, paints)
            self.assertGreater(cache.hits, 0)
            QTest.qWait(170); self.events()
            self.assertFalse(cache.active); self.assertFalse(cache.allow_zoom)
            self.assertFalse(cache.zoom_timer.isActive()); self.assertEqual(cache.byte_count, 0)
            self.assertGreater(item.paints, paints)  # Timeout requested actual scene paint.
            actual = view.viewport().grab().toImage()
            rect = view.viewport().rect()
            expected = QImage(actual.size(), QImage.Format.Format_RGB32)
            expected.setDevicePixelRatio(actual.devicePixelRatioF())
            expected.fill(QColor('#ff00ff'))
            painter = QPainter(expected); painter.setRenderHints(view.renderHints())
            try:
                view.render(painter, QRectF(rect), rect, Qt.AspectRatioMode.IgnoreAspectRatio)
            finally:
                painter.end()
            self.assert_pixels(actual.convertToFormat(QImage.Format.Format_RGB32), expected, roundoff=True)

    def test_programmatic_camera_transforms_cancel_active_wheel_reuse(self):
        with self.real_canvas() as (view, item):
            for name, action in (
                ('scale', lambda: view.scale(1.05, 1.05)),
                ('reset', view.resetTransform),
                ('set', lambda: view.setTransform(QTransform.fromScale(.85, .85))),
            ):
                with self.subTest(transform=name):
                    self.wheel(view)
                    self.assertTrue(view.pan_raster.active)
                    paints = item.paints
                    action()
                    self.assertFalse(view.pan_raster.active)
                    self.assertFalse(view.pan_raster.allow_zoom)
                    self.assertFalse(view.pan_raster.zoom_timer.isActive())
                    self.assertEqual(view.pan_raster.byte_count, 0)
                    self.events()
                    self.assertGreater(item.paints, paints)
