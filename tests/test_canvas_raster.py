"""Raster reuse must retain live paint states and physical-pixel sharpness."""
import math
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.canvas_raster import LAYERS, SHADOWS, RasterCache, paint_cached, paint_scope
from prompt_calculus_studio.canvas_items import CanvasView, NodeCard, canvas_colors
from prompt_calculus_studio.composition import node
from prompt_calculus_studio import module_chrome

APP = QApplication.instance() or QApplication([])


class CanvasRasterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')

    def setUp(self):
        LAYERS.clear()
        SHADOWS.clear()
        self.font = QFont('Microsoft JhengHei UI', 10)
        self.value = node('角色設定', '午後街道，暖色陽光。 standing, soft light')
        self.value['collapsed'] = False
        self.canvas = SimpleNamespace(window=SimpleNamespace(state={
            'settings': {'visual_palette': 'graphite'}, 'workspace': 'raster-test'}),
            font=lambda: QFont(self.font), cards={}, entries={'root': ('root', self.value, self.value)},
            affected_nodes={}, active_keys=set())
        self.card = NodeCard(self.canvas, 'root', self.value, '')

    def render(self, draw, zoom=.9, dpr=1.25, offset=(25.35, 25.75)):
        image = QImage(760, 620, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr)
        image.fill(QColor('#15191a'))
        painter = QPainter(image)
        try:
            painter.setFont(self.font)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(*offset)
            painter.scale(zoom, zoom)
            draw(painter)
        finally:
            painter.end()
        return image

    def assert_pixels_close(self, actual, expected):
        # Premultiplied SourceOver through an intermediate transparent image
        # can round a channel differently; geometry/text shifts exceed this.
        delta = max(abs(a-b) for a, b in zip(bytes(actual.constBits()), bytes(expected.constBits())))
        self.assertLessEqual(delta, 3)

    def direct_card(self, painter):
        with patch.object(module_chrome, 'paint_shadow', module_chrome._draw_shadow):
            colors=canvas_colors(self.canvas)
            self.card._paint_surface(painter, colors, self.canvas.affected_nodes.get('root', {}))
            self.card._paint_content(painter, colors)

    def test_text_shadow_and_edges_match_at_zoom_dpr_and_fractional_pan(self):
        for zoom, dpr in ((.3, 1), (.77, 1.25), (.9, 1.25), (1, 1), (1.5, 1.25), (1, 2)):
            for offset in ((25.35, 25.75), (26.1, 27.25)):
                with self.subTest(zoom=zoom, dpr=dpr, offset=offset):
                    expected = self.render(self.direct_card, zoom, dpr, offset)
                    for _ in range(3):
                        actual = self.render(lambda p: self.card.paint(p, None), zoom, dpr, offset)
                        self.assert_pixels_close(actual, expected)

    def test_live_visual_states_invalidate_the_card(self):
        previous = self.render(lambda p: self.card.paint(p, None))
        def selected(): self.card.setSelected(True)
        def hover(): self.card.hot = 'plus'
        def pressed(): self.card.pressed = 'plus'
        def weight(): self.value['weight'] = -25
        def running(): self.canvas.active_keys.add('root')
        def conflict(): self.canvas.affected_nodes['root'] = {'all': True}
        def editor(): self.canvas.window._material_editor_session = {'active': ('canvas', 'root', 'raster-test')}
        def resized(): self.card.requested_size = [450, 300]; self.card.layout_card()
        def content(): self.value['name'] = '更新名稱'; self.card.own = '更新後的文字'; self.card.layout_card()
        def palette(): self.canvas.window.state['settings']['visual_palette'] = 'paper'
        def font(): self.font.setPointSize(13); self.card.layout_card()
        def disabled(): self.card.muted = True
        def drop(): self.card.drop_size = (320, 140); self.card.layout_card()
        for change in (selected, hover, pressed, weight, conflict, running, editor, resized, content,
                       palette, font, disabled, drop):
            with self.subTest(state=change.__name__):
                change()
                self.render(lambda p: self.card.paint(p, None))
                actual = self.render(lambda p: self.card.paint(p, None))
                self.assertNotEqual(actual, previous)
                self.assert_pixels_close(actual, self.render(self.direct_card))
                previous = actual

    def test_unchanged_card_reuses_raster_after_integer_device_translation(self):
        with patch.object(self.card, '_paint_surface', wraps=self.card._paint_surface) as draw:
            self.render(lambda p: self.card.paint(p, None), offset=(24, 24))
            self.render(lambda p: self.card.paint(p, None), offset=(28, 28))
            self.render(lambda p: self.card.paint(p, None), offset=(32, 32))
            self.assertEqual(draw.call_count, 2)
            self.render(lambda p: self.card.paint(p, None), zoom=1.0, offset=(28, 28))
            self.assertEqual(draw.call_count, 3)

    def test_shadow_reuse_keeps_nested_and_outer_outlines_distinct(self):
        outline = QPainterPath(); outline.addRoundedRect(QRectF(0, 0, 320, 180), 16, 16)
        for nested in (False, True):
            self.render(lambda p: module_chrome.paint_shadow(p, outline, nested))
            actual = self.render(lambda p: module_chrome.paint_shadow(p, outline, nested))
            expected = self.render(lambda p: module_chrome._draw_shadow(p, outline, nested))
            self.assert_pixels_close(actual, expected)
        with patch.object(module_chrome, '_draw_shadow', wraps=module_chrome._draw_shadow) as draw:
            self.render(lambda p: module_chrome.paint_shadow(p, outline, True))
            self.assertEqual(draw.call_count, 0)

    def test_cache_budget_eviction_and_large_surface_fallback(self):
        cache = RasterCache(max_bytes=10000, max_entry_bytes=5000)
        small = QImage(25, 25, QImage.Format.Format_ARGB32_Premultiplied)
        for i in range(6): cache.put(i, small)
        self.assertLessEqual(cache.byte_count, 10000)
        self.assertIsNone(cache.get(0)); self.assertIsNotNone(cache.get(5))
        cache.get(2); cache.put(6, small)
        self.assertIsNotNone(cache.get(2)); self.assertIsNone(cache.get(3))
        def draw(p): p.fillRect(QRectF(0, 0, 320, 180), QColor('red'))
        with patch.object(cache, 'put', wraps=cache.put) as put:
            actual = self.render(lambda p: paint_cached(p, 'large', QRectF(0, 0, 320, 180), draw, cache))
            self.assertEqual(actual, self.render(draw))
            put.assert_not_called()

    def test_large_shadow_does_not_evict_card_layers(self):
        self.render(lambda p: self.card.paint(p, None))
        self.render(lambda p: self.card.paint(p, None))
        count, size = len(LAYERS.entries), LAYERS.byte_count
        large = QPainterPath(); large.addRoundedRect(QRectF(0, 0, 1400, 1000), 16, 16)
        for n in range(16):
            self.render(lambda p: module_chrome.paint_shadow(p, large), offset=(25+n*.25,25+n*.25))
        self.assertEqual((len(LAYERS.entries),LAYERS.byte_count),(count,size))
        with patch.object(self.card, '_paint_surface', wraps=self.card._paint_surface) as draw:
            self.render(lambda p: self.card.paint(p, None))
            draw.assert_not_called()

    def test_one_time_zoom_levels_do_not_allocate_disposable_card_images(self):
        with patch.object(LAYERS, 'put', wraps=LAYERS.put) as put:
            for zoom in (.71, .77, .83, .91, 1.03, 1.11):
                self.render(lambda p: self.card.paint(p, None),zoom)
            put.assert_not_called()
        cache=RasterCache(max_entries=4)
        for i in range(10): cache.recurring(i)
        self.assertEqual(len(cache.pending),4)

    def test_zoom_paint_scope_avoids_cache_allocation_and_restores_reuse(self):
        with patch.object(LAYERS, 'put', wraps=LAYERS.put) as put:
            with paint_scope(False):
                for _ in range(3): self.render(lambda p:self.card.paint(p,None))
            put.assert_not_called()
            for _ in range(3): self.render(lambda p:self.card.paint(p,None))
            self.assertEqual(put.call_count,1)

    def test_batch_grid_matches_individual_points_at_negative_positions(self):
        view = SimpleNamespace(canvas=self.canvas)
        rect = QRectF(-275, -141, 1300, 900)
        for zoom in (.3, .599, .6, .9, 1.5):
            view.transform = lambda: SimpleNamespace(m11=lambda: zoom)
            def direct(p):
                colors=canvas_colors(self.canvas); p.fillRect(rect, QColor(colors['base']))
                p.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                dot=QColor(colors['line']); dot.setAlpha(90)
                pen=QPen(dot, 1); pen.setCosmetic(True); p.setPen(pen)
                spacing=28 if zoom >= .6 else 112
                for x in range(math.floor(rect.left()/spacing)*spacing, math.ceil(rect.right()), spacing):
                    for y in range(math.floor(rect.top()/spacing)*spacing, math.ceil(rect.bottom()), spacing):
                        p.drawPoint(QPointF(x,y))
            with self.subTest(zoom=zoom):
                self.assertEqual(self.render(lambda p: CanvasView.drawBackground(view,p,rect),zoom),
                                 self.render(direct,zoom))
                points=view._grid_points
                self.render(lambda p: CanvasView.drawBackground(view,p,rect),zoom)
                self.assertIs(view._grid_points,points)


if __name__ == '__main__': unittest.main()
