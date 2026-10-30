"""Device caches stay bounded without freezing native Canvas controls."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QRectF
from PySide6.QtGui import QFont, QFontDatabase, QPixmapCache, QTransform
from PySide6.QtWidgets import QApplication, QGraphicsItem, QGraphicsView, QLineEdit, QTextEdit, QVBoxLayout, QWidget

from prompt_calculus_studio.canvas_items import CanvasView, ModuleProxyWidget
from prompt_calculus_studio import canvas_proxy_cache as cache
from test_canvas_navigation_rendering import assert_device_cache_pixels


APP = QApplication.instance() or QApplication([])
DIRECT = QGraphicsItem.CacheMode.NoCache
CACHED = QGraphicsItem.CacheMode.DeviceCoordinateCache


class MeasuredProxy(ModuleProxyWidget):
    def paint(self, *args):
        self.paints += 1
        return super().paint(*args)


class ProxyCacheBudgetTests(unittest.TestCase):
    def setUp(self):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/msjh.ttc')
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/segoeui.ttf')
        self.windows = []
        self.proxies = []
        self.limit = QPixmapCache.cacheLimit()
        self.view = self.new_view()

    def new_view(self):
        canvas = QWidget()
        canvas.window = SimpleNamespace(state={'workspace': 'qa'}, store=None)
        canvas._paint_colors = dict(base='#242829', canvas_background='#242829', line='#525757')
        layout = QVBoxLayout(canvas)
        view = CanvasView(canvas)
        layout.addWidget(view)
        view.scene().setSceneRect(-1000, -1000, 5000, 4000)
        canvas.resize(2048, 1152)
        self.windows.append(canvas)
        canvas.show()
        view.setTransform(QTransform.fromScale(1.04, 1.04))
        view.centerOn(1050, 500)
        self.events()
        return view

    def add_proxy(self, width, height, x=100, view=None):
        view = view or self.view
        panel = QWidget()
        panel.setFont(QFont('Microsoft JhengHei UI', 12))
        panel.setStyleSheet('QWidget {background:#242829; color:#eeeeee;}')
        layout = QVBoxLayout(panel)
        edit = QLineEdit('Stable original text')
        layout.addWidget(edit)
        layout.addWidget(QTextEdit('Synthetic content\n'*20))
        proxy = MeasuredProxy()
        proxy.paints = 0
        proxy.edit = edit
        proxy.setWidget(panel)
        view.scene().addItem(proxy)
        proxy.setGeometry(QRectF(x, 100, width, height))
        self.proxies.append(proxy)
        self.events()
        return proxy

    def tearDown(self):
        for canvas in self.windows:
            canvas.close()
            canvas.deleteLater()
        APP.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        cache.configure()
        self.assertEqual(QPixmapCache.cacheLimit(), self.limit)

    def events(self):
        APP.processEvents()
        APP.processEvents()

    def assert_budget(self):
        selected = [p for p in self.proxies if p.cacheMode() == CACHED]
        cost = sum(cache.cache_bytes(p, {}) for p in selected)
        self.assertLessEqual(cost, int(self.limit*1024*cache.CACHE_SHARE))
        self.assertEqual(QPixmapCache.cacheLimit(), self.limit)
        return selected

    def assert_direct_pixels(self, image):
        with patch.object(cache, 'configure'):
            for proxy in self.proxies:
                proxy.setCacheMode(DIRECT)
            self.events()
            direct = self.view.viewport().grab().toImage()
        scale=self.view.transform().m11()
        assert_device_cache_pixels(self, image, direct, fractional_sampling=abs(scale-round(scale))>1e-6)

    def test_over_limit_controls_do_not_thrash_and_match_direct_pixels(self):
        for index in range(3):
            self.add_proxy(900, 900, 100+560*index)
        self.assertGreater(sum(cache.cache_bytes(p, {}) for p in self.proxies), self.limit*1024)
        selected = self.assert_budget()
        self.assertTrue(selected)
        self.assertLess(len(selected), 3)
        for proxy in self.proxies:
            proxy.paints = 0
        for index in range(20):
            bar = self.view.horizontalScrollBar()
            bar.setValue(bar.value()+(1 if index < 10 else -1))
            self.events()
        for proxy in self.proxies:
            if proxy in selected:
                self.assertLess(proxy.paints, 3)
            else:
                self.assertGreaterEqual(proxy.paints, 15)
        self.assertEqual(self.assert_budget(), selected)
        self.assert_direct_pixels(self.view.viewport().grab().toImage())

    def test_text_update_and_zoom_invalidate_admitted_pixels(self):
        proxy = self.add_proxy(320, 250)
        self.assertEqual(proxy.cacheMode(), CACHED)
        before = self.view.viewport().grab().toImage()
        proxy.paints = 0
        proxy.edit.setText('Updated control text')
        self.events()
        after = self.view.viewport().grab().toImage()
        self.assertGreater(proxy.paints, 0)
        self.assertNotEqual(before, after)
        proxy.paints = 0
        self.view.setTransform(QTransform.fromScale(1.5, 1.5))
        self.view.centerOn(proxy)
        self.events()
        image = self.view.viewport().grab().toImage()
        self.assertGreater(proxy.paints, 0)
        self.assertEqual(proxy.cacheMode(), CACHED)
        self.assert_direct_pixels(image)

    def test_hidden_removed_and_hidden_view_release_caches(self):
        proxy = self.add_proxy(320, 250)
        self.assertEqual(proxy.cacheMode(), CACHED)
        proxy.hide()
        self.assertEqual(proxy.cacheMode(), DIRECT)
        proxy.show()
        self.events()
        self.assertEqual(proxy.cacheMode(), CACHED)
        self.view.scene().removeItem(proxy)
        self.assertEqual(proxy.cacheMode(), DIRECT)
        self.view.scene().addItem(proxy)
        self.events()
        self.assertEqual(proxy.cacheMode(), CACHED)
        self.windows[0].showMinimized()
        self.events()
        self.assertEqual(proxy.cacheMode(), DIRECT)
        self.windows[0].showNormal()
        # Offscreen restoration need not deliver an expose event. Admission
        # occurs at the next real paint, not just the window-state change.
        self.view.viewport().repaint()
        self.events()
        self.assertEqual(proxy.cacheMode(), CACHED)
        self.view.hide()
        self.assertEqual(proxy.cacheMode(), DIRECT)

    def test_budget_is_shared_between_canvases_and_admits_small_controls_first(self):
        self.add_proxy(900, 900)
        second = self.new_view()
        self.add_proxy(900, 900, view=second)
        small = self.add_proxy(320, 250, 1100, view=second)
        self.view.viewport().update()
        self.events()
        selected = self.assert_budget()
        self.assertIn(small, selected)
        self.assertLess(len(selected), 3)

    def test_multiple_views_and_offscreen_items_use_direct_rendering(self):
        proxy = self.add_proxy(320, 250)
        other = QGraphicsView(self.view.scene())
        try:
            other.show()
            self.view.viewport().update()
            self.events()
            self.assertEqual(proxy.cacheMode(), DIRECT)
            other.setScene(None)
            self.view.viewport().update()
            self.events()
            self.assertEqual(proxy.cacheMode(), CACHED)
            proxy.setPos(10000, 10000)
            self.events()
            self.assertEqual(proxy.cacheMode(), DIRECT)
        finally:
            other.close()
            other.deleteLater()


if __name__ == '__main__':
    unittest.main()
