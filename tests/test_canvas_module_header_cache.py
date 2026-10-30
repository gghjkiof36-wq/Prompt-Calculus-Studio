"""Native module headings reuse raster work without stale text or controls."""
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtGui import QColor, QFont, QFontDatabase, QImage, QPainter
from PySide6.QtWidgets import QApplication, QTextEdit, QWidget

from prompt_calculus_studio import module_chrome
from prompt_calculus_studio.canvas_items import TextCard
from prompt_calculus_studio.canvas_raster import LAYERS, SHADOWS, paint_scope


APP = QApplication.instance() or QApplication([])


class ModuleHeaderCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')

    def setUp(self):
        LAYERS.clear(); SHADOWS.clear()
        self.font = QFont('Microsoft JhengHei UI', 11)
        self.panel = QWidget(); self.editor = QTextEdit()
        self.value = {}
        self.state = {'settings': {'visual_palette': 'graphite'},
                      'canvas_functions': {'text_sources': {'__text_output__': self.value}}}
        self.canvas = SimpleNamespace(
            window=SimpleNamespace(state=self.state, builder_panel=self.panel, final=self.editor),
            font=lambda: QFont(self.font), active_keys=set(),
            functions=SimpleNamespace(outline=lambda card, outline: outline))
        self.card = TextCard(self.canvas)
        self.card.width = 360; self.card.height = 180
        self.card.title = '文字輸出 · 場景設定'

    def tearDown(self):
        self.card.deleteLater(); self.panel.deleteLater(); self.editor.deleteLater()
        APP.processEvents()

    def render(self, *, direct=False, zoom=1.04, dpr=1.25, offset=(24, 16)):
        image = QImage(740, 130, QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(dpr); image.fill(QColor('#15191a'))
        painter = QPainter(image)
        try:
            painter.setFont(self.font); painter.translate(*offset); painter.scale(zoom, zoom)
            with paint_scope(not direct):
                self.card.paint(painter, None)
        finally:
            painter.end()
        return image

    def assert_same_pixels(self, actual, direct):
        # Same physical-pixel raster, allowing only transparent SourceOver rounding.
        self.assertEqual(actual.size(), direct.size())
        delta = max(abs(a-b) for a, b in zip(bytes(actual.constBits()), bytes(direct.constBits())))
        self.assertLessEqual(delta, 3)

    def test_pan_reuses_header_without_replaying_layout_or_icon_work(self):
        with patch.object(module_chrome, 'paint_header', wraps=module_chrome.paint_header) as draw:
            for offset in ((24, 16), (28, 20), (32, 24), (36, 28)):
                self.render(offset=offset)
            self.assertEqual(draw.call_count, 2)  # First direct draw, then admission.

    def test_title_font_palette_fold_icon_and_hover_change_without_stale_pixels(self):
        def title(): self.card.title = '更新後的文字標題'
        def font(): self.font.setPointSize(14)
        def palette(): self.state['settings']['visual_palette'] = 'paper'
        def fold(): self.value['collapsed'] = True
        def icon(): self.card.module_icon = 'file-text'
        def hover(): self.card.hot = 'collapse'
        def leave(): self.card.hot = None
        previous = self.render()
        self.render(); self.render()
        for change in (title, font, palette, fold, icon, hover, leave):
            with self.subTest(state=change.__name__):
                change()
                expected = self.render(direct=True)
                self.assertNotEqual(expected, previous)
                for _ in range(3):
                    self.assert_same_pixels(self.render(), expected)
                previous = expected

    def test_header_retains_pixels_across_zoom_dpr_and_pan_phase(self):
        for zoom, dpr, offset in ((.5, 1, (24, 16)), (1.04, 1.25, (24.35, 16.75)),
                                  (1.5, 1.25, (25.1, 17.25)), (1, 2, (24, 16))):
            with self.subTest(zoom=zoom, dpr=dpr, offset=offset):
                args = dict(zoom=zoom, dpr=dpr, offset=offset)
                expected = self.render(direct=True, **args)
                for _ in range(3):
                    self.assert_same_pixels(self.render(**args), expected)


if __name__ == '__main__':
    unittest.main()
