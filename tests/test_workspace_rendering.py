"""Retained scene rendering keeps live edits, history and surface joins correct."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget, QPushButton, QComboBox

import test_workspace_shell as fixtures
from prompt_calculus_studio.module_preview import ModulePreview
from prompt_calculus_studio.function_picker import FUNCTIONS


class WorkspaceRenderingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.WorkspaceShellTests.setUpClass.__func__(cls)
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/'+name)

    setUp = fixtures.WorkspaceShellTests.setUp
    tearDown = fixtures.WorkspaceShellTests.tearDown
    events = fixtures.WorkspaceShellTests.events
    open = fixtures.WorkspaceShellTests.open

    def test_first_tab_and_content_have_one_continuous_outside_edge(self):
        self.open('Street', 'pcs-workflow')
        self.w.sidebar_preference = False
        for position in ('side', 'top'):
            self.w.set_navigation_position(position)
            self.w.sync_sidebar(); self.events()
            image = self.w.grab().toImage()
            origin = self.w.workspace_stack.mapTo(self.w, QPoint())
            dpr = self.w.devicePixelRatioF()
            x, join = round(origin.x()*dpr), round(origin.y()*dpr)
            # Check the actual raster on both sides of the tab/body join,
            # including the first pixel where a separate stroke used to start.
            for dx in range(3):
                above = image.pixelColor(x+dx, join-3)
                for dy in range(4):
                    self.assertEqual(image.pixelColor(x+dx, join+dy), above, (position, dx, dy))

    def test_home_document_widgets_follow_the_bottom_left_outer_contour(self):
        self.w.sidebar_preference = False
        for navigation in ('side', 'top'):
            self.w.set_navigation_position(navigation)
            self.w.sync_sidebar()
            for kind in ('home', 'workflow', 'category', 'markdown'):
                with self.subTest(navigation=navigation, kind=kind):
                    tab = self.w.new_tab() if kind == 'home' else self.w.open_path(self.root/{
                        'workflow':'Street.pcs-workflow', 'category':'Scene.pcs-category', 'markdown':'Notes.md'}[kind])
                    self.events()
                    page = tab.widget
                    self.assertFalse(page.mask().contains(QPoint(0, page.height()-1)))
                    self.assertTrue(page.mask().contains(page.rect().center()))
                    self.assertTrue(page.mask().contains(QPoint(page.width()-2, page.height()-2)))
                    image = self.w.grab().toImage()
                    point = page.mapTo(self.w, QPoint(0, page.height()-2))
                    dpr = self.w.devicePixelRatioF()
                    x, y = round(point.x()*dpr), round(point.y()*dpr)
                    if x > 3:
                        self.assertEqual(image.pixelColor(x+1,y), image.pixelColor(x-3,y))

    def test_unchanged_tab_reuses_scene_and_keeps_camera_selection_and_undo(self):
        tab = self.open('Street', 'pcs-workflow')
        canvas = tab.host.canvas
        cid = next(iter(canvas.containers))
        canvas.commit(lambda s: s['multi_output']['canvases'][cid].update(name='Retained'))
        self.events()
        canvas.containers[cid].setSelected(True)
        canvas.view.scale(.8, .8)
        canvas.view.centerOn(QPointF(70, 90))
        camera = canvas.view.mapToScene(QPoint())
        ports = dict(canvas.ports)
        history = list(canvas.undo_stack)
        note = self.open('Notes', 'md', True)
        with patch.object(canvas, 'refresh', wraps=canvas.refresh) as refresh:
            self.w.focus_tab(tab); self.events()
            refresh.assert_not_called()
        self.assertEqual(canvas.ports, ports)
        self.assertEqual(canvas.undo_stack, history)
        self.assertTrue(canvas.containers[cid].isSelected())
        self.assertLess((canvas.view.mapToScene(QPoint())-camera).manhattanLength(), 3)
        canvas.undo(); self.events()
        self.assertNotEqual(canvas.data()['canvases'][cid]['name'], 'Retained')
        canvas.redo(); self.events()
        self.assertEqual(canvas.data()['canvases'][cid]['name'], 'Retained')

    def test_hidden_shared_view_refreshes_on_return(self):
        tab = self.open('Street', 'pcs-workflow')
        other = self.w.duplicate_view(tab)
        self.events()
        first, second = tab.host.canvas, other.host.canvas
        self.assertFalse(first.isVisible())
        cid = next(iter(second.containers))
        previous = dict(first.ports)
        second.commit(lambda s: s['multi_output']['canvases'][cid].update(name='Changed elsewhere'))
        self.events()
        self.w.focus_tab(tab); self.events()
        self.assertNotEqual(first.ports, previous)
        self.assertEqual(first.data()['canvases'][cid]['name'], 'Changed elsewhere')
        first.undo(); self.events()
        self.assertNotEqual(second.data()['canvases'][cid]['name'], 'Changed elsewhere')

    def test_catalog_field_weights_are_regular_across_all_modules_and_palettes(self):
        tab = self.open('Street', 'pcs-workflow')
        preview = ModulePreview(tab.host.canvas)
        self.addCleanup(preview.deleteLater)
        checked = set()
        for palette in ('graphite', 'mist', 'paper'):
            self.w.settings['visual_palette'] = palette
            self.w.style_shell(); self.events()
            for kind, *_ in FUNCTIONS:
                preview.show_kind(kind); self.events()
                panel = getattr(preview.card, 'panel', None)
                if not isinstance(panel, QWidget):
                    continue
                controls = panel.findChildren(QPushButton) + panel.findChildren(QComboBox)
                for control in controls:
                    if control.property('modulePending') is not None or isinstance(control, QComboBox):
                        control.ensurePolished()
                        self.assertEqual(control.font().weight(), QFont.Weight.Normal,
                                         (palette, kind, control.objectName()))
                        checked.add(kind)
        self.assertTrue({'stage', 'image-input', 'reader', 'clip', 'source'} <= checked)


if __name__ == '__main__':
    unittest.main()
