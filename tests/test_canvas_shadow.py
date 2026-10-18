"""Zoom shadow reuse preserves vector edges without caching live content."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QTransform
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio import canvas_shadow, module_chrome

APP = QApplication.instance() or QApplication([])


class ShadowTileTests(unittest.TestCase):
    def setUp(self):
        canvas_shadow.TILES.clear()

    def outline(self, width=330, height=270, radius=16):
        path = QPainterPath()
        path.addRoundedRect(QRectF(0, 0, width, height), radius, radius)
        return path

    def render(self, outline, *, zoom=.77, dpr=1.25, offset=(25.35, 25.75),
               nested=False, covered=False, strength=1., direct=False,
               opacity=1., rotate=0, composition=None, clip=False, smooth=False):
        image = QImage(1400, 1100, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor('#d4d4d4'))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, smooth)
        painter.translate(*offset)
        painter.scale(zoom, zoom)
        painter.rotate(rotate)
        painter.setOpacity(opacity)
        if composition is not None:
            painter.setCompositionMode(composition)
        if clip:
            painter.setClipRect(QRectF(30, 20, 140, 180))
        before = (painter.worldTransform(), painter.renderHints(), painter.opacity(),
                  painter.compositionMode(), painter.clipPath())
        try:
            (module_chrome._draw_shadow if direct else module_chrome.paint_shadow)(
                painter, outline, nested, strength, covered)
            self.assertEqual(before, (painter.worldTransform(), painter.renderHints(), painter.opacity(),
                                      painter.compositionMode(), painter.clipPath()))
            if covered:
                painter.fillPath(outline, QColor('#242829'))
        finally:
            painter.end()
        return image

    def assert_pixels(self, actual, expected):
        left, right = bytes(actual.constBits()), bytes(expected.constBits())
        self.assertEqual(len(left), len(right))
        maximum = 0
        # Compare unchanged chunks in C; inspect differing pixels without
        # turning a raster check into millions of Python-loop operations.
        for at in range(0, len(left), 4096):
            a, b = left[at:at + 4096], right[at:at + 4096]
            if a != b:
                maximum = max(maximum, max(abs(x-y) for x, y in zip(a, b)))
        self.assertLessEqual(maximum, 3)

    def test_corners_and_straight_edges_match_vectors_through_zoom_and_fractional_pan(self):
        for width, height, radius in ((25, 30, 12), (330, 270, 16), (840, 640, 12)):
            path = self.outline(width, height, radius)
            for zoom, dpr in ((.12, 1.25), (.3, 1.), (.77, 1.25), (1., 1.5), (1.5, 1.25), (2.5, 1.5)):
                for nested, covered in ((False, False), (False, True), (True, True)):
                    with self.subTest(size=(width, height), zoom=zoom, dpr=dpr, nested=nested, covered=covered):
                        options = dict(zoom=zoom, dpr=dpr, nested=nested, covered=covered)
                        self.assert_pixels(self.render(path, **options), self.render(path, direct=True, **options))
                        options['offset'] = (26.1, 27.25)
                        self.assert_pixels(self.render(path, **options), self.render(path, direct=True, **options))

    def test_strength_translucent_card_and_clipped_painter_stay_live(self):
        path = self.outline()
        previous = None
        for strength in (0., .4, 1., 1.7):
            options = dict(strength=strength, covered=False, clip=True, smooth=True)
            actual = self.render(path, **options)
            self.assert_pixels(actual, self.render(path, direct=True, **options))
            if previous is not None:
                self.assertNotEqual(actual, previous)
            previous = actual

    def test_arbitrary_outline_rotation_and_blending_use_original_drawing(self):
        irregular = QPainterPath()
        irregular.addEllipse(QRectF(0, 0, 320, 180))
        for path, options in ((irregular, {}), (self.outline(), {'rotate': 8}),
                              (self.outline(), {'opacity': .5}),
                              (self.outline(), {'composition': QPainter.CompositionMode.CompositionMode_Multiply})):
            with self.subTest(options=options):
                self.assert_pixels(self.render(path, **options), self.render(path, direct=True, **options))
        self.assertFalse(canvas_shadow.TILES.entries)

    def test_unchanged_phase_reuses_tiles_and_dpr_resize_update_them(self):
        path = self.outline()
        with patch.object(module_chrome, '_draw_shadow', wraps=module_chrome._draw_shadow) as draw:
            self.render(path, zoom=.8, dpr=1.25, offset=(24, 24))
            self.render(path, zoom=.8, dpr=1.25, offset=(28, 28))
            self.assertEqual(draw.call_count, 1)
            self.render(path, zoom=.8, dpr=1.5, offset=(28, 28))
            self.assertEqual(draw.call_count, 2)
            self.render(self.outline(331, 271), zoom=.77, offset=(28, 28))
            self.assertEqual(draw.call_count, 3)

    def test_large_cards_and_continuous_zoom_have_a_bounded_small_tile_cache(self):
        path = self.outline(6000, 4000)
        with patch.object(module_chrome, '_draw_shadow', wraps=module_chrome._draw_shadow) as draw:
            for step in range(140):
                self.render(path, zoom=.2 + step * .007, covered=True)
            self.assertTrue(draw.called)
            for call in draw.call_args_list:
                bounds = call.args[1].boundingRect()
                self.assertLess(bounds.width(), 70)
                self.assertLess(bounds.height(), 70)
        self.assertLessEqual(canvas_shadow.TILES.byte_count, canvas_shadow.TILES.max_bytes)
        self.assertLessEqual(len(canvas_shadow.TILES.entries), canvas_shadow.TILES.max_entries)


if __name__ == '__main__':
    unittest.main()
