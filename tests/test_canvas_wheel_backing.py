"""Bounded multi-scale wheel pixels remain current and release on handoff."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QColor, QImage, QTransform
from PySide6.QtWidgets import QGraphicsScene, QGraphicsView
from shiboken6 import delete

import test_canvas_pan_cache as fixture
from prompt_calculus_studio import canvas_pan_cache as product


class CanvasWheelBackingTests(unittest.TestCase):
    def setUp(self):
        self.f = fixture.CanvasPanCacheTests(methodName='runTest')
        self.f.setUp()
        self.cache = self.f.cache
        self.cache.end(); self.cache.begin_zoom(); self.cache.zoom_timer.stop()

    def tearDown(self):
        self.f.tearDown()

    def frame(self, scale, **kw):
        self.f.view.setTransform(QTransform.fromScale(scale, scale))
        self.f.view.centerOn(0, 0)
        image, used = self.f.frame(**kw)
        self.assertTrue(used)
        return image

    def warm(self):
        for scale in (1., 2., .5):
            self.frame(scale)
        self.assertEqual(len(self.cache._zoom_backings), 3)

    def test_archive_alias_is_reused_and_real_content_changes_clear_every_scale(self):
        self.warm()
        keys = {s.image.cacheKey() for s in self.cache._zoom_backings}
        count = self.cache.captures
        old = self.frame(1.)
        self.assertEqual(self.cache.captures, count)
        self.assertGreater(self.cache.archive_hits, 0)
        self.assertIs(self.cache.image, self.cache._zoom_backings[0].image)
        self.f.edit.setText('new result must replace every old scale')
        self.f.events()
        self.assertEqual(self.cache.byte_count, 0)
        self.assertIsNone(self.cache.image)
        updated = self.frame(1.)
        self.assertNotEqual(updated, old)
        self.assertNotIn(self.cache.image.cacheKey(), keys)
        self.f.item.setBrush(QColor('#a73152')); self.f.events()
        self.assertEqual(self.cache.byte_count, 0)
        self.assertEqual(self.cache._zoom_backings, [])

    def test_content_change_during_capture_rejects_new_frame_and_old_archives(self):
        self.warm()
        original = self.f.view.render
        def during(*args):
            original(*args)
            self.f.scene.changed.emit([QRectF(0, 0, 100, 100)])
        self.f.view.setTransform(QTransform.fromScale(4., 4.))
        with patch.object(self.f.view, 'render', during):
            _, used = self.f.frame()
        self.assertFalse(used)
        self.assertEqual(self.cache.byte_count, 0)
        self.assertEqual(self.cache._zoom_backings, [])
        self.assertIsNone(self.cache.image)

    def test_reentrant_paint_preserves_archives_without_drawing_them(self):
        self.warm()
        keys = [s.image.cacheKey() for s in self.cache._zoom_backings]
        self.cache._capturing = True
        try:
            _, used = self.f.frame()
        finally:
            self.cache._capturing = False
        self.assertFalse(used)
        self.assertEqual([s.image.cacheKey() for s in self.cache._zoom_backings], keys)

    def test_coverage_dpr_appearance_geometry_scene_and_end_do_not_keep_stale_pixels(self):
        self.warm()
        captures = self.cache.captures
        bar = self.f.view.horizontalScrollBar(); bar.setValue(bar.value()+2000)
        image, used = self.f.frame()
        self.assertTrue(used); self.assertGreater(self.cache.captures, captures)
        self.f.assert_full_frame(image)
        self.frame(1., appearance=('changed-theme',))
        self.assertEqual(len(self.cache._zoom_backings), 1)
        self.frame(1., dpr=2., appearance=('changed-theme',))
        self.assertEqual(len(self.cache._zoom_backings), 1)
        self.f.view.resize(475, 335); self.f.events()
        self.assertEqual(self.cache.byte_count, 0)
        self.frame(1.)
        scene = QGraphicsScene(self.f.view)
        scene.setSceneRect(-2000, -2000, 4000, 4000)
        scene.addRect(-80, -30, 90, 70).setBrush(QColor('#4a88cc'))
        self.f.view.setScene(scene); self.f.events()
        image = self.frame(1.)
        self.assertIs(self.cache._scene, scene)
        self.assertEqual(len(self.cache._zoom_backings), 1)
        self.f.assert_pixels(image, self.f.frame(cached=False)[0])
        self.cache.end()
        self.assertEqual(self.cache._zoom_backings, [])
        self.assertEqual(self.cache.byte_count, 0)

    def test_full_size_allocations_and_eviction_never_hold_a_fourth_frame(self):
        self.f.view.resize(1686, 958); self.f.events()
        allocations = []
        def allocate(width, height, format):
            before = self.cache.byte_count
            self.assertLessEqual(before+width*height*4, self.cache.max_zoom_bytes)
            allocations.append(before+width*height*4)
            return QImage(width, height, format)
        allocate.Format = QImage.Format
        with patch.object(product, 'QImage', allocate):
            for scale in (1., 2., .5, 4., .25):
                self.frame(scale)
                images = {id(s.image): s.image for s in self.cache._zoom_backings}
                self.assertIs(self.cache.image, self.cache._zoom_backings[0].image)
                self.assertLessEqual(len(images), 3)
                self.assertEqual(self.cache.byte_count, sum(i.sizeInBytes() for i in images.values()))
                self.assertLessEqual(self.cache.byte_count, 96*1024*1024)
        self.assertGreater(max(allocations), 90*1024*1024)
        self.assertLessEqual(max(allocations), 96*1024*1024)

    def test_new_wheel_view_releases_previous_wheel_but_does_not_end_its_pan(self):
        self.warm()
        other = QGraphicsView(); other.setScene(QGraphicsScene(other))
        cache = product.PanRasterCache(other)
        try:
            cache.begin_zoom(); cache.zoom_timer.stop()
            self.assertTrue(self.f.view.isInteractive())
            self.assertFalse(self.cache.allow_zoom)
            self.assertEqual(self.cache.byte_count, 0)
            # An old wheel owner can be reused for panning. Another view's
            # later wheel must not cancel that separate gesture.
            self.f.view.pan = QPointF(10, 10)
            self.cache.begin()
            cache.end(); cache.begin_zoom()
            self.assertTrue(self.cache.active)
            self.assertEqual(self.f.view.pan, QPointF(10, 10))
        finally:
            self.f.view.pan = None
            cache.end(); delete(other)

    def test_deleted_cpp_wheel_owner_does_not_keep_pixels_or_block_new_owner(self):
        other = QGraphicsView(); other.setScene(QGraphicsScene(other))
        cache = product.PanRasterCache(other)
        cache.begin_zoom()
        delete(other)
        self.cache.begin_zoom(); self.cache.zoom_timer.stop()
        self.assertTrue(self.cache.allow_zoom)
        self.assertFalse(self.f.view.isInteractive())
        self.assertEqual(cache.byte_count, 0)


if __name__ == '__main__':
    unittest.main()
