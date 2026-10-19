"""Split managers adapt their preview without clipping or trapping the divider."""
import copy
import sys
import unittest

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest

import test_workspace_shell as fixture


class WorkspacePaneMinimumTests(unittest.TestCase):
    events = fixture.WorkspaceShellTests.events

    @classmethod
    def setUpClass(cls):
        fixture.WorkspaceShellTests.setUpClass.__func__(cls)
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)
        cls.app.setFont(QFont('Microsoft JhengHei UI', 12))

    def setUp(self):
        fixture.WorkspaceShellTests.setUp(self)
        self.w.set_navigation_position('top')
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        self.w.resize(1500, 900)
        self.left = self.w.open_path(self.root/'Street.pcs-workflow', True)
        self.right = self.w.ensure_split()
        self.tab = self.w.open_path(self.root/'Scene.pcs-category', True, pane=self.right)
        self.page = self.tab.host.prompt_editor
        self.events()

    def tearDown(self):
        for pane in list(self.w.panes):
            while pane in self.w.panes and pane.strip.bar.count():
                self.w.close_tab(0, decision='discard', make_home=False, pane=pane)
        self.w.close()
        self.w.deleteLater()
        self.events()
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def preview(self, page=None):
        page = page or self.page
        ident = page.window.state['items'][0]['id']
        page.preview_material(ident)
        self.events()

    def assert_visible_columns(self, tab=None):
        tab = tab or self.tab
        page = tab.host.prompt_editor
        pane = self.w.pane_for(tab)
        self.assertGreaterEqual(pane.container.width(), page.workspace_minimum_width())
        self.assertEqual(tab.widget.width(), pane.pages.width())
        viewport = page.scroll.viewport()
        self.assertFalse(page.scroll.horizontalScrollBar().isVisible())
        self.assertEqual(page.scroll.horizontalScrollBar().maximum(), 0)
        for widget in (page.listing, page.preview_card, page.edit_button, page.add_canvas_button):
            self.assertTrue(widget.isVisible())
            rect = QRect(widget.mapTo(viewport, QPoint()), widget.size())
            self.assertGreaterEqual(rect.left(), 0)
            self.assertLess(rect.right(), viewport.width())
        if page.preview_card.x() == page.browser.x():
            self.assertGreater(page.preview_card.y(), page.browser.geometry().bottom())
        else:
            self.assertGreater(page.preview_card.x(), page.browser.geometry().right())

    def test_divider_stops_before_preview_and_does_not_change_content(self):
        state = copy.deepcopy(self.tab.host.state)
        self.preview()
        self.w.pane_split.setSizes([1500, 1])
        self.events()
        self.assert_visible_columns()
        self.assertEqual(self.page.preview_card.x(), self.page.browser.x())
        self.assertLess(self.right.container.width(), 650)
        self.assertEqual(self.tab.host.state, state)
        # Closing/reopening a preview does not shift the divider.
        sizes = self.w.pane_split.sizes()
        self.page.close_preview()
        self.events()
        self.preview()
        self.assertEqual(self.w.pane_split.sizes(), sizes)
        QTest.mouseClick(self.page.edit_button, Qt.MouseButton.LeftButton)
        self.events()
        self.assertIsNotNone(self.page.active_dialog)
        self.page.active_dialog.reject()

    def test_hidden_manager_does_not_limit_home_and_return_restores_minimum(self):
        self.preview()
        home = self.w.new_tab(pane=self.right)
        self.events()
        self.w.pane_split.setSizes([1500, 1])
        self.events()
        self.assertEqual(self.right.pages.minimumWidth(), 250)
        self.assertLess(self.right.container.width(), self.page.workspace_minimum_width())
        self.w.focus_tab(self.tab)
        self.events()
        self.assert_visible_columns()
        self.assertIn(home.key, self.w.tabs_by_key)

    def test_resize_restores_preferred_split_and_wide_preview(self):
        self.preview()
        self.w.pane_split.setSizes([700, 800]); self.w.remember_ratio(); self.events()
        preferred = self.w.split_ratio
        for width in (900, 1500, 1000, 1800):
            self.w.resize(width, 900); self.events()
            self.assert_visible_columns()
            self.assertAlmostEqual(self.w.split_ratio, preferred, places=3)
        sizes = self.w.pane_split.sizes()
        self.assertAlmostEqual(sizes[0]/sum(sizes), preferred, places=2)
        self.assertGreater(self.page.preview_card.x(), self.page.browser.geometry().right())

    def test_utility_resize_keeps_both_tab_controls_separate(self):
        from PySide6.QtWidgets import QToolButton
        self.preview()
        self.w.open_route('settings', 'appearance'); self.events()
        for width in (1500, 900, 1800, 1000):
            self.w.resize(width, 760); self.events()
            strips = [p.strip for p in self.w.panes]
            self.assertEqual(strips[0].geometry().right()+1, strips[1].x())
            for strip in strips:
                for control in strip.findChildren(QToolButton):
                    if not control.isVisible(): continue
                    rect = QRect(control.mapTo(strip, QPoint()), control.size())
                    self.assertTrue(strip.rect().contains(rect), (width, rect, strip.rect()))
                self.assertGreater(strip.bar.width(), 100)
        self.assertAlmostEqual(strips[0].width()/self.w.pane_tab_row.width(), .5, places=2)

    def test_one_tab_click_restores_both_pages_from_every_utility_route(self):
        self.preview()
        original = copy.deepcopy(self.tab.host.state)
        for route in ('settings', 'gallery', 'explore', 'export'):
            for chosen in self.w.panes:
                self.w.open_route(route); self.events()
                self.assertFalse(self.left.widget.isVisible())
                self.assertFalse(self.tab.widget.isVisible())
                QTest.mouseClick(chosen.strip.bar, Qt.MouseButton.LeftButton,
                                 pos=chosen.strip.bar.tabRect(chosen.strip.bar.currentIndex()).center())
                self.events()
                self.assertEqual(self.w.active_route, 'home')
                self.assertTrue(self.left.widget.isVisible(), (route, chosen.key, 'left'))
                self.assertTrue(self.tab.widget.isVisible(), (route, chosen.key, 'right'))
                self.assert_visible_columns()
        self.assertEqual(self.tab.host.state, original)

    def test_two_managers_and_window_minimum_keep_both_previews(self):
        other = self.w.open_path(self.root/'Action.pcs-category', True, pane=self.w.panes[0])
        self.preview(other.host.prompt_editor)
        self.preview()
        for sizes in ([1, 1500], [1500, 1]):
            self.w.pane_split.setSizes(sizes)
            self.w.resize(720, 600)
            self.events()
            self.assert_visible_columns(other)
            self.assert_visible_columns()
            self.assertLessEqual(self.right.container.mapTo(self.w, self.right.container.rect().topRight()).x(), self.w.width())

    def test_sidebar_and_side_navigation_reserve_only_their_visible_width(self):
        self.preview()
        for navigation in ('side', 'top'):
            self.w.set_navigation_position(navigation)
            for sidebar in (True, False):
                self.w.sidebar_preference = sidebar
                self.w.sync_sidebar()
                self.events()
                self.w.resize(720, 650)
                self.events()
                self.assert_visible_columns()
                self.assertLess(self.w.width(), 1600)
                self.assertLessEqual(self.right.container.mapTo(self.w, self.right.container.rect().topRight()).x(), self.w.width())


if __name__ == '__main__':
    unittest.main()
