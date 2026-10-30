"""Compressed module surfaces retain native corners, headers and blending."""
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio import canvas_items, canvas_surface, module_chrome
from prompt_calculus_studio.canvas_raster import LAYERS, SHADOWS
from prompt_calculus_studio.theme import visual_tokens


APP = QApplication.instance() or QApplication([])


class SurfaceTileTests(unittest.TestCase):
    def setUp(self):
        canvas_surface.TILES.clear(); LAYERS.clear(); SHADOWS.clear()
        self.failure_number = 0

    def outline(self, width=360, height=240):
        path = QPainterPath(); path.addRoundedRect(QRectF(0, 0, width, height), 16, 16)
        return path

    def render(self, path, *, zoom=1.04, dpr=1.25, offset=(25.35, 25.75),
               palette='graphite', alpha=255, header=44, width=1, cosmetic=False,
               direct=False, clip=False, rotate=0, opacity=1, composition=None, dashed=False):
        colors = module_chrome.matte_colors(visual_tokens({'visual_palette': palette}))
        colors['canvas_alpha'] = alpha
        image = QImage(1280, 1050, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr); image.fill(QColor('#b5c1ca'))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.translate(*offset); painter.scale(zoom, zoom); painter.rotate(rotate)
        pen = QPen(QColor(colors['accent'] if width > 1 else colors['canvas_line']), width)
        pen.setCosmetic(cosmetic)
        if dashed: pen.setStyle(Qt.PenStyle.DashLine)
        painter.setPen(pen); painter.setOpacity(opacity)
        if composition is not None: painter.setCompositionMode(composition)
        if clip: painter.setClipRect(QRectF(30.25, 17.7, 170.6, 157.2))
        state = painter.worldTransform(), painter.renderHints(), painter.opacity(), painter.compositionMode(), painter.clipPath()
        try:
            if direct:
                with patch.object(module_chrome, 'paint_shadow', module_chrome._draw_shadow):
                    canvas_items._paint_module_surface(painter, path, colors, header)
            else:
                canvas_items.paint_module_surface(painter, path, colors, header)
            self.assertEqual(state, (painter.worldTransform(), painter.renderHints(), painter.opacity(),
                                     painter.compositionMode(), painter.clipPath()))
        finally:
            painter.end()
        return image

    def assert_pixels(self, actual, expected):
        left, right = bytes(actual.constBits()), bytes(expected.constBits())
        maximum = 0
        for at in range(0, len(left), 4096):
            a, b = left[at:at+4096], right[at:at+4096]
            if a != b: maximum = max(maximum, max(abs(x-y) for x, y in zip(a, b)))
        if maximum > 3 and os.environ.get('PCS_SURFACE_QA'):
            self.failure_number += 1
            folder = Path(os.environ['PCS_SURFACE_QA']); folder.mkdir(parents=True, exist_ok=True)
            stem = self.id().split('.')[-1] + f'-{self.failure_number}'
            actual.save(str(folder / (stem+'-actual.png')))
            expected.save(str(folder / (stem+'-direct.png')))
        self.assertLessEqual(maximum, 3)

    # Exercise tile pixels with bounded image fixtures at every scale; the
    # real 2 MiB admission boundary is checked separately below.
    @patch.object(LAYERS, 'max_entry_bytes', 0)
    def test_physical_corner_header_body_pixels_match_zoom_dpr_and_phase(self):
        path = self.outline()
        for zoom in (.3, .77, 1.04, 1.5):
            for dpr in (1, 1.25, 2):
                with self.subTest(zoom=zoom, dpr=dpr):
                    args = dict(zoom=zoom, dpr=dpr)
                    self.assert_pixels(self.render(path, **args), self.render(path, direct=True, **args))
                    args['offset'] = (26.1, 27.25)
                    self.assert_pixels(self.render(path, **args), self.render(path, direct=True, **args))
        path = self.outline(800, 700)
        self.assert_pixels(self.render(path), self.render(path, direct=True))

    @patch.object(LAYERS, 'max_entry_bytes', 0)
    def test_palette_alpha_header_and_selection_pen_remain_live(self):
        path = self.outline()
        previous = None
        for args in (dict(palette='paper'), dict(palette='mist', width=1.5),
                     dict(palette='graphite', width=2.5, cosmetic=True),
                     dict(alpha=110), dict(alpha=0), dict(header=68)):
            with self.subTest(args=args):
                actual = self.render(path, **args)
                self.assert_pixels(actual, self.render(path, direct=True, **args))
                if previous is not None: self.assertNotEqual(actual, previous)
                previous = actual

    def test_clip_keeps_partial_target_and_painter_state(self):
        for alpha in (255, 100):
            self.assert_pixels(self.render(self.outline(), alpha=alpha, clip=True),
                               self.render(self.outline(), alpha=alpha, clip=True, direct=True))
        self.assertFalse(canvas_surface.TILES.entries)

    @patch.object(LAYERS, 'max_entry_bytes', 0)
    def test_narrow_center_keeps_border_pixels_instead_of_forcing_body_fill(self):
        for width in (1, 8, 25):
            with self.subTest(width=width):
                path = self.outline(width, 180)
                self.assert_pixels(self.render(path, width=4), self.render(path, width=4, direct=True))

    def test_ported_rotated_blended_and_dashed_shapes_keep_original_path(self):
        ported = self.outline()
        cut = QPainterPath(); cut.addEllipse(QRectF(-8, 70, 20, 20))
        ported = ported.subtracted(cut)
        for path, args in ((ported, {}), (self.outline(), dict(rotate=7)),
                           (self.outline(), dict(opacity=.5)), (self.outline(), dict(dashed=True)),
                           (self.outline(), dict(composition=QPainter.CompositionMode.CompositionMode_Multiply))):
            with self.subTest(args=args):
                self.assert_pixels(self.render(path, **args), self.render(path, direct=True, **args))
        self.assertFalse(canvas_surface.TILES.entries)

    def test_large_body_uses_small_tiles_and_reuses_same_phase(self):
        with patch.object(canvas_items, '_paint_module_surface', wraps=canvas_items._paint_module_surface) as draw:
            for offset in ((24, 24), (28, 28), (32, 32)):
                self.render(self.outline(), offset=offset)
            self.assertEqual(draw.call_count, 2)
            self.assertFalse(canvas_surface.TILES.entries)
            draw.reset_mock()
            path = self.outline(800, 700)
            for offset in ((24, 24), (28, 28), (32, 32)):
                self.render(path, offset=offset)
            self.assertEqual(draw.call_count, 1)
            for step in range(24):
                self.render(self.outline(6000, 4000), zoom=.3+step*.035)
            for call in draw.call_args_list:
                bounds = call.args[1].boundingRect()
                self.assertLess(bounds.width(), 70)
                self.assertLess(bounds.height(), 96)
        self.assertLessEqual(canvas_surface.TILES.byte_count, 4*1024*1024)
        self.assertLessEqual(max(image.sizeInBytes() for image in canvas_surface.TILES.entries.values()), 512*1024)


if __name__ == '__main__': unittest.main()
