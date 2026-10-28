"""Workspace-owned Canvas controls stay reachable without changing the graph."""
import copy
import os
import tempfile
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

import test_workspace_shell as fixture


class CanvasRecentExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        isolated = tempfile.TemporaryDirectory(prefix='pcs-canvas-controls-')
        self.addCleanup(isolated.cleanup)
        patches = (
            patch.dict(os.environ, {'PROMPT_STUDIO_V08': '1', 'PROMPT_STUDIO_DATA': isolated.name}),
            patch('prompt_calculus_studio.extension_page.ExtensionPage.startup', lambda _: None),
            patch('prompt_calculus_studio.extension_page.ExtensionPage.install',
                  side_effect=AssertionError('No installation in Canvas tests')),
            patch('prompt_calculus_studio.manager_client.ManagerClient.request', return_value=None),
            patch('prompt_calculus_studio.comfy_client.ComfyClient.request', return_value=None),
            patch('prompt_calculus_studio.local_ai.runtime.LlamaRuntime.start',
                  side_effect=AssertionError('No model runtime in Canvas tests')),
        )
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        fixture.WorkspaceShellTests.setUp(self)
        self.w.sidebar_auto_hide = False
        self.w.sidebar_preference = True
        self.w.sync_sidebar()
        self.w.resize(1600, 1000)
        self.tab = self.w.open_path(self.root/'Street.pcs-workflow')
        self.assertIsNotNone(self.tab, self.errors)
        self.canvas = self.tab.host.canvas
        self.canvas.window.state['settings'].pop('execution_bar_position', None)
        self.events()

    events = fixture.WorkspaceShellTests.events

    def tearDown(self):
        for pane in self.w.panes:
            while pane.strip.bar.count():
                self.assertTrue(self.w.close_tab(0, decision='discard', make_home=False, pane=pane))
        fixture.WorkspaceShellTests.tearDown(self)

    def assert_controls_reachable(self, canvas=None):
        canvas = canvas or self.canvas
        self.events()
        canvas.execution_bar.place()
        self.events()
        viewport = canvas.view.viewport()
        self.assertTrue(canvas.execution_bar.isVisible())
        self.assertTrue(canvas.recent_control.isVisible())
        self.assertEqual(canvas.recent_control.geometry().right(), viewport.rect().right()-12)
        controls = [control for control in (canvas.execution_bar, canvas.recent_control,
                                             canvas.zoom_controls) if control.isVisible()]
        for index, control in enumerate(controls):
            self.assertTrue(viewport.rect().contains(control.geometry()), control.geometry())
            for other in controls[index+1:]:
                self.assertFalse(control.geometry().intersects(other.geometry()),
                                 (control.geometry(), other.geometry()))
        return viewport

    def test_single_pane_default_centers_on_application_with_or_without_sidebar(self):
        graph = copy.deepcopy(self.canvas.data())
        bar = self.canvas.execution_bar
        for sidebar in (True, False):
            with self.subTest(sidebar=sidebar):
                self.w.sidebar_preference = sidebar
                self.w.sync_sidebar()
                viewport = self.assert_controls_reachable()
                app_center = self.w.mapToGlobal(self.w.rect().center()).x()
                bar_center = bar.mapToGlobal(bar.rect().center()).x()
                self.assertLessEqual(abs(app_center-bar_center), 2)
                self.assertLessEqual(abs(bar.geometry().bottom()-(viewport.height()-13)), 1)
        self.assertEqual(self.canvas.data(), graph)

    def test_saved_drag_position_survives_relayout_and_narrow_window(self):
        settings = self.canvas.window.state['settings']
        settings['execution_bar_position'] = [.1, .65]
        self.w.sidebar_preference = False
        self.w.sync_sidebar()
        for width, height in ((1600, 1000), (700, 600)):
            with self.subTest(width=width):
                self.w.resize(width, height)
                viewport = self.assert_controls_reachable()
                bar = self.canvas.execution_bar
                area = viewport.rect().adjusted(12, 12, -12, -12)
                expected = QPoint(area.left()+round((area.width()-bar.width())*.1),
                                  area.top()+round((area.height()-bar.height())*.65))
                self.assertEqual(bar.pos(), expected)
                self.assertEqual(settings['execution_bar_position'], [.1, .65])
                if width == 700:
                    self.assertTrue(bar.compact)
                    self.assertTrue(self.canvas.zoom_controls.isHidden())

    def test_split_canvases_center_controls_in_their_own_panes(self):
        right = self.w.duplicate_view(self.tab, True)
        self.events()
        self.assertEqual(len(self.w.panes), 2)
        for tab in (self.tab, right):
            with self.subTest(pane=tab.pane):
                canvas = tab.host.canvas
                viewport = self.assert_controls_reachable(canvas)
                self.assertLessEqual(abs(canvas.execution_bar.geometry().center().x()
                                         -viewport.rect().center().x()), 2)

    def test_recent_icon_opens_owner_overlay_and_closes_without_mutating_scene(self):
        canvas = self.canvas
        host = canvas.window
        graph = copy.deepcopy(canvas.data())
        camera = canvas.view.transform()
        tab_count = host.tabs.count()
        self.assertEqual(canvas.recent_control.action.text(), '')
        self.assertFalse(canvas.recent_control.action.icon().isNull())
        for close_with in ('escape', 'blank'):
            with self.subTest(close_with=close_with):
                QTest.mouseClick(canvas.recent_control.action, Qt.MouseButton.LeftButton)
                self.events()
                sheet = host.recent_sheet
                self.assertIsNotNone(sheet)
                self.assertTrue(sheet.isVisible())
                self.assertIs(sheet.page, host.recent)
                if close_with == 'escape':
                    QTest.keyClick(sheet, Qt.Key.Key_Escape)
                else:
                    blank = QPoint(1, 1)
                    self.assertFalse(sheet.surface.geometry().contains(blank))
                    QTest.mouseClick(sheet, Qt.MouseButton.LeftButton, pos=blank)
                self.events()
                self.assertIsNone(host.recent_sheet)
                self.assertEqual(host.tabs.count(), tab_count)
                self.assertGreaterEqual(host.tabs.indexOf(host.recent), 0)
                self.assertEqual(canvas.data(), graph)
                self.assertEqual(canvas.view.transform(), camera)
