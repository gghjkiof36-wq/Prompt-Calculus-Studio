"""Shared navigation, material preview and readable numeric controls.

Synthetic local data and an inert transport keep these tests independent of
ComfyUI, downloads and native desktop composition.
"""
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionSpinBox

from prompt_studio.theme import visual_tokens
from prompt_studio.window import Window


APP = QApplication.instance() or QApplication([])
if APP.platformName() == 'offscreen':
    for font in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
        QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + font)


class RevisionTwoShellTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {'PROMPT_STUDIO_V08': '1', 'PROMPT_STUDIO_V081': '1'})
        self.environment.start()
        self.transport = patch('prompt_studio.comfy_client.ComfyClient.request', return_value=None)
        self.transport.start()
        self.w = Window(self.temp.name)
        self.w.state['settings'].update(online=False, material='solid', reduce_motion=True)
        self.w.set_interface_mode('canvas')
        self.w.resize(1440, 920)
        self.w.apply_theme()
        self.w.show()
        self.settle()

    def tearDown(self):
        self.w.close()
        APP.processEvents()
        self.transport.stop()
        self.environment.stop()
        self.temp.cleanup()

    def settle(self):
        QTest.qWait(60)

    def test_five_destinations_share_one_content_top_edge(self):
        for width, height in ((1440, 920), (800, 640)):
            self.w.resize(width, height)
            for destination in ('canvas', 'media', 'explore', 'export', 'settings'):
                with self.subTest(width=width, destination=destination):
                    self.w.studio_navigation.entries[destination][0].click()
                    self.settle()
                    page = self.w.surface_stack.currentWidget()
                    self.assertTrue(page.isVisible())
                    self.assertEqual(page.mapTo(self.w, QPoint(0, 0)).y(),
                                     self.w.studio_navigation.mapTo(self.w, QPoint(0, 0)).y()
                                     + self.w.studio_navigation.height())
                    self.assertFalse(self.w.settings_page.compact_navigation.isVisible())

    def test_top_five_destinations_only_show_hints_when_icon_only(self):
        for width,size in ((1440,11),(640,18),(1440,11)):
            self.w.state['settings']['ui_size']=size;self.w.resize(width,800)
            self.w.apply_theme();self.settle()
            for entry,title in self.w.studio_navigation.entries.values():
                self.assertEqual(entry.toolTip(),'' if entry.text() else title)
                self.assertEqual(entry.accessibleName(),title)

    def test_canvas_uses_inset_workspace_capsule_and_child_recent_overlay(self):
        nav = self.w.studio_navigation
        capsule = self.w.canvas_workspace_bar
        self.assertIsNone(self.w.context_sidebar_target())
        self.assertFalse(self.w.context_rail.isVisible());self.assertFalse(nav.sidebar.isEnabled())
        self.assertTrue(capsule.isVisible());self.assertTrue(capsule.isAncestorOf(self.w.canvas_workspace))
        self.assertFalse(nav.isAncestorOf(self.w.canvas_workspace))
        self.assertGreaterEqual(capsule.mapTo(self.w.canvas_shell,QPoint()).x(),12)
        self.assertGreaterEqual(capsule.mapTo(self.w.canvas_shell,QPoint()).y(),12)
        self.assertLess(capsule.width(),self.w.canvas_shell.width()*.65)
        self.assertTrue(capsule.history.isVisible());self.assertTrue(capsule.list_mode.isVisible())
        geometry=self.w.canvas_content.geometry()
        capsule.history.click()
        self.settle()
        sheet=self.w.recent_sheet
        self.assertIs(sheet.parentWidget(),self.w.surface_stack);self.assertFalse(sheet.isWindow())
        self.assertTrue(sheet.surface.isAncestorOf(self.w.recent))
        self.assertEqual(self.w.canvas_content.geometry(),geometry)
        self.w.close_recent_sheet();self.settle()
        self.assertIsNone(self.w.recent_sheet);self.assertGreaterEqual(self.w.tabs.indexOf(self.w.recent),0)

    def test_connection_status_tracks_mock_connection_including_compact_mode(self):
        nav = self.w.studio_navigation
        colors = visual_tokens(self.w.state['settings'])
        for connected, description, token in ((False, '未連線', 'control'), (True, '已連線', 'connected_indicator')):
            self.w.comfy.connected = connected
            nav.update_connection()
            self.assertEqual(nav.connection.accessibleName(), 'ComfyUI · ' + description)
            self.assertTrue(nav.brand.text().endswith(description))
            self.assertIn(colors[token], nav.connection_dot.styleSheet())
            self.w.resize(800, 640)
            self.settle()
            self.assertTrue(nav.connection_dot.isVisible())
            self.assertTrue(nav.brand.isVisible())
            self.assertEqual(nav.brand.text(),description)
            self.assertEqual(nav.connection.toolTip().splitlines()[0], 'ComfyUI · ' + description)

    def test_opacity_drag_does_not_reapply_theme_and_saves_final_value(self):
        self.w.settings('appearance')
        self.settle()
        page = self.w.settings_page
        prefs = page.preferences
        with patch('prompt_studio.window.apply_backdrop', return_value=True):
            prefs.material.setCurrentIndex(prefs.material.findData('mica'))
            page.flush()
            self.settle()
            with patch.object(self.w, 'apply_theme', wraps=self.w.apply_theme) as theme:
                for value in range(20, 61):
                    prefs.transparency.setValue(value)
                    APP.processEvents()
                self.assertEqual(prefs.transparency_value.value(), 60)
                page.flush()
                theme.assert_not_called()
            self.assertEqual(self.w.state['settings']['mica_transparency'], 60)
            self.assertEqual(self.w.store.load()['settings']['mica_transparency'], 60)

    def test_numeric_edit_rect_keeps_entire_value_after_font_changes(self):
        self.w.settings('appearance')
        self.settle()
        prefs = self.w.settings_page.preferences
        prefs.material.setCurrentIndex(prefs.material.findData('mica'))
        prefs.transparency.setValue(100)
        for width, size in ((1440, 11), (800, 18), (1440, 14), (800, 11)):
            self.w.resize(width, 920)
            self.w.state['settings']['ui_size'] = size
            self.w.apply_theme(preserve_layout=True)
            self.settle()
            for control in (prefs.ui_size, prefs.prompt_size, prefs.transparency_value):
                with self.subTest(width=width, font=size, value=control.text()):
                    option = QStyleOptionSpinBox()
                    control.initStyleOption(option)
                    rect = control.style().subControlRect(QStyle.ComplexControl.CC_SpinBox, option,
                                                         QStyle.SubControl.SC_SpinBoxEditField, control)
                    self.assertGreaterEqual(rect.width(), control.fontMetrics().horizontalAdvance(control.text()))
                    self.assertGreaterEqual(rect.height(), control.fontMetrics().height())

    def test_narrow_pin_stays_overlay_across_sidebar_destinations_and_resize(self):
        self.w.resize(800,640); self.settle()
        self.w.show_page(self.w.gallery);self.settle()
        self.w.studio_navigation.sidebar.click(); self.settle()
        self.assertTrue(self.w.sidebar_pinned_preference)
        for destination in ('media','explore','export','settings'):
            with self.subTest(destination=destination):
                self.w.studio_navigation.entries[destination][0].click(); self.settle()
                peek=self.w.sidebar_peek; target=self.w.context_sidebar_target()
                self.assertTrue(peek.is_pinned_overlay); self.assertIs(peek.target,target)
                self.assertTrue(target.isVisible()); self.assertTrue(target.property('pcsSidebarBorrowed'))
                self.assertEqual(target.parentWidget(),peek.overlay); self.assertTrue(self.w.studio_navigation.sidebar.isChecked())
                self.w.resize(780,640); self.settle()
                self.assertTrue(peek.is_pinned_overlay); self.assertTrue(target.isVisible())
                self.assertEqual(peek.overlay.width(),240)
                self.assertEqual(peek.overlay.y(),self.w.studio_navigation.height())
                self.assertEqual(peek.overlay.x(),self.w.context_rail.width())
                if destination=='media':
                    content=self.w.gallery.image_panel; self.assertTrue(content.isVisible())
                elif destination=='export':content=self.w.clean_export.layout().itemAt(0).widget()
                else:content=self.w.settings_page.reading
                self.assertEqual(content.mapTo(self.w,QPoint()).x(),self.w.context_rail.width())
                self.w.resize(800,640); self.settle()
        self.w.studio_navigation.entries['canvas'][0].click();self.settle()
        self.assertIsNone(self.w.context_sidebar_target());self.assertFalse(self.w.sidebar_peek.is_open)
        self.assertFalse(self.w.context_rail.isVisible());self.assertFalse(self.w.studio_navigation.sidebar.isEnabled())
        self.assertTrue(self.w.sidebar_pinned_preference)
        self.w.show_page(self.w.gallery);self.settle()
        self.assertTrue(self.w.sidebar_peek.is_pinned_overlay)

    def test_pin_expands_into_wide_layout_then_global_collapse_survives_page_changes(self):
        self.w.resize(800,640); self.w.show_page(self.w.gallery);self.settle()
        self.w.studio_navigation.sidebar.click(); self.settle()
        self.w.resize(1440,920); self.settle()
        self.assertFalse(self.w.sidebar_peek.is_open); self.assertTrue(self.w.gallery.folder_panel.isVisible())
        self.assertFalse(self.w.gallery.folder_panel.property('pcsSidebarBorrowed'))
        self.assertEqual(self.w.gallery.media_workspace.mapTo(self.w,QPoint()).x(),240)
        self.w.studio_navigation.sidebar.click(); self.settle()
        self.assertFalse(self.w.sidebar_pinned_preference)
        for destination in ('media','explore','export','settings','canvas'):
            self.w.studio_navigation.entries[destination][0].click(); self.settle()
            target=self.w.context_sidebar_target()
            if destination=='canvas':
                self.assertIsNone(target);self.assertFalse(self.w.context_rail.isVisible())
                self.assertFalse(self.w.studio_navigation.sidebar.isEnabled())
            else:self.assertTrue(target.isHidden(),destination)
            self.assertFalse(self.w.sidebar_peek.is_open)
            self.assertFalse(self.w.studio_navigation.sidebar.isChecked())

    def test_temporary_hover_does_not_change_global_pin_preference(self):
        self.w.sidebar_pinned_preference=False; self.w.settings('appearance'); self.settle()
        self.w.sidebar_peek.open_timer.setInterval(30)
        original=self.w.settings_page.reading.geometry()
        QTest.mouseMove(self.w,QPoint(600,300)); QTest.mouseMove(self.w,QPoint(2,100)); QTest.qWait(75)
        self.assertTrue(self.w.sidebar_peek.is_open); self.assertFalse(self.w.sidebar_peek.is_pinned_overlay)
        self.assertFalse(self.w.sidebar_pinned_preference); self.assertFalse(self.w.studio_navigation.sidebar.isChecked())
        self.assertEqual(self.w.settings_page.reading.geometry(),original)
        self.w.escape_page(); self.settle()
        self.assertFalse(self.w.sidebar_peek.is_open); self.assertFalse(self.w.sidebar_pinned_preference)
        self.assertTrue(self.w.settings_page.navigation_area.isHidden())

    def test_outer_workspace_rounding_contains_sidebar_and_content_on_all_pages(self):
        self.w.display_recovery.stop()
        stack = self.w.surface_stack
        for destination in ('canvas', 'media', 'explore', 'export', 'settings'):
            self.w.studio_navigation.entries[destination][0].click()
            self.w.sidebar_pinned_preference = True
            self.w.sync_context_sidebar(); self.settle()
            if destination == 'canvas':
                # The optional onboarding card has its own rounded corners;
                # inspect the workspace beneath it, not that content card.
                self.w.canvas.onboarding.hide(); APP.processEvents()
            sidebar = self.w.context_sidebar_target()
            if destination == 'canvas':
                self.assertIsNone(sidebar);self.assertFalse(self.w.context_rail.isVisible())
                self.assertEqual(self.w.canvas_shell.mapTo(stack,QPoint()),QPoint())
                self.assertFalse(stack.mask().contains(QPoint()))
                self.assertTrue(stack.mask().contains(QPoint(stack.width()//2,0)))
                before=self.w.canvas_content.geometry()
                self.w.sidebar_pinned_preference=False;self.w.sync_context_sidebar();self.settle()
                self.assertEqual(self.w.canvas_content.geometry(),before)
                self.assertFalse(self.w.sidebar_resize.handle.isVisible())
                continue
            if destination == 'media': content = self.w.gallery.media_workspace
            elif destination == 'export': content = self.w.clean_export.layout().itemAt(1).widget()
            else: content = self.w.settings_page.reading
            with self.subTest(destination=destination, pinned=True):
                self.assertEqual(sidebar.mapTo(stack, QPoint()), QPoint())
                self.assertEqual(content.mapTo(stack, QPoint()), QPoint(sidebar.width(), 0))
                self.assertFalse(stack.mask().contains(QPoint()))
                self.assertTrue(stack.mask().contains(QPoint(stack.width() // 2, 0)))
                self.assertTrue(stack.mask().contains(QPoint(sidebar.width(), 0)))
                # A former 18 px rounded content corner exposed the brighter
                # sidebar at this junction. Both pixels now belong to content.
                image = stack.grab().toImage()
                boundary = sidebar.width()
                self.assertEqual(image.pixelColor(boundary + 2, 2),
                                 image.pixelColor(boundary + 24, 2))
            self.w.sidebar_pinned_preference = False
            self.w.sync_context_sidebar(); self.settle()
            with self.subTest(destination=destination, pinned=False):
                self.assertTrue(sidebar.isHidden())
                self.assertEqual(content.mapTo(stack, QPoint()), QPoint())
                self.assertFalse(stack.mask().contains(QPoint()))
                self.assertTrue(stack.mask().contains(QPoint(stack.width() // 2, 0)))

    def test_sidebar_resize_hint_is_invisible_at_rest(self):
        self.w.display_recovery.stop()
        self.w.settings('appearance')
        self.w.sidebar_pinned_preference = True
        self.w.sync_context_sidebar(); self.settle()
        handle = self.w.sidebar_resize.handle
        QTest.mouseMove(self.w, QPoint(700, 400)); self.settle()
        handle.hovered = False; handle.clearFocus()
        handle.hide(); APP.processEvents()
        area = handle.geometry()
        without_handle = self.w.grab().toImage().copy(area)
        handle.show(); handle.raise_(); APP.processEvents()
        at_rest = self.w.grab().toImage().copy(area)
        self.assertEqual(at_rest, without_handle)
        handle.hovered = True; handle.update(); APP.processEvents()
        hovering = self.w.grab().toImage().copy(area)
        self.assertNotEqual(hovering, at_rest)
        handle.hovered = False; handle.update()


if __name__ == '__main__':
    unittest.main()
