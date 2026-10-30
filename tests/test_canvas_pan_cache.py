"""Camera-only backing reuse stays opaque, sharp, bounded and live."""
import math
import os
from pathlib import Path
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPen, QTransform
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
            for x in (110, 120, 130):
                QTest.mouseMove(view.viewport(), QPoint(x, 100)); self.events()
            self.assertEqual(item.paints, count)
            self.assertGreaterEqual(view.pan_raster.hits, 2)
            count = view.pan_raster.captures
            item.setBrush(QColor('#e3a563')); self.events()
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
