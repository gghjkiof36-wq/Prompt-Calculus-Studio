"""Reachable navigation without hidden page construction or intermediate paint."""
from PySide6.QtCore import Qt, QPoint, QObject, QEvent
from PySide6.QtTest import QTest
from unittest.mock import patch
import unittest

from prompt_calculus_studio.canvas_items import CanvasView
from prompt_calculus_studio.deferred_page import page_ready
from prompt_calculus_studio.workspace_home import WorkspaceHome
from prompt_calculus_studio.workspace_locations import path_key
import test_workspace_shell as fixture


class CanvasPaints(QObject):
    def __init__(self, app):
        super().__init__()
        self.frames = []
        app.installEventFilter(self)

    def eventFilter(self, widget, event):
        if event.type() == QEvent.Type.Paint and isinstance(widget.parent(), CanvasView):
            self.frames.append(len(widget.parent().scene().items()))
        return False


class WorkspaceNavigationLoadingTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspaceShellTests.setUpClass.__func__)
    setUp = fixture.WorkspaceShellTests.setUp
    tearDown = fixture.WorkspaceShellTests.tearDown
    events = fixture.WorkspaceShellTests.events

    def test_folder_whole_row_and_arrow_toggle_once_without_opening_file(self):
        path = self.root/'Folder'
        (path/'Inside.md').write_text('inside', encoding='utf-8')
        self.w.refresh_tree()
        item = self.w.path_items[path_key(path)]
        root = self.w.tree.topLevelItem(0)
        original = self.w.active_key
        for target in (item, root):
            for area in ('icon', 'label', 'empty', 'arrow'):
                with self.subTest(target=target.text(0), area=area):
                    target.setExpanded(False)
                    self.events()
                    rect = self.w.tree.visualItemRect(target)
                    x = {'icon': rect.left()+8, 'label': rect.left()+42,
                         'empty': rect.right()-8, 'arrow': rect.left()-self.w.tree.indentation()//2}[area]
                    point = QPoint(x, rect.center().y())
                    with patch.object(self.w.locations, 'inventory', side_effect=AssertionError('no scan on click')):
                        for expected in (True, False):
                            QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.LeftButton, pos=point)
                            self.events()
                            self.assertEqual(target.isExpanded(), expected)
                    self.assertEqual(self.w.active_key, original)
            target.setExpanded(True)
        # Right-click preserves disclosure and the existing keyboard controls.
        self.w.tree.setCurrentItem(item)
        item.setExpanded(False)
        QTest.mouseClick(self.w.tree.viewport(), Qt.MouseButton.RightButton,
                         pos=self.w.tree.visualItemRect(item).center())
        self.assertFalse(item.isExpanded())
        popup = self.app.activePopupWidget()
        if popup:
            popup.close()
        self.w.tree.setFocus()
        QTest.keyClick(self.w.tree, Qt.Key.Key_Right)
        self.assertTrue(item.isExpanded())
        QTest.keyClick(self.w.tree, Qt.Key.Key_Left)
        self.assertFalse(item.isExpanded())

    def test_sidebar_click_finishes_immediately_and_retains_canvas(self):
        tab = self.w.open_path(self.root/'Street.pcs-workflow')
        self.events()
        self.w.sidebar_preference = True
        self.w.sync_sidebar()
        for visible in (False, True, False):
            QTest.mouseClick(self.w.collapse, Qt.MouseButton.LeftButton)
            self.assertEqual(self.w.sidebar.isVisible(), visible)
            self.assertIsNone(self.w.sidebar_resize.motion_target)
            self.events()
            self.assertIs(self.w.pages.currentWidget(), tab.widget)
            self.assertTrue(tab.host.canvas.isVisible())

    def test_first_open_and_reopen_after_close_use_shell_palette_and_populated_first_frame(self):
        path = self.root/'Street.pcs-workflow'
        self.w.settings['visual_palette'] = 'paper'
        self.w.style_shell()
        self.events()
        palette = self.app.palette()
        changes = []
        callback = lambda value: changes.append(value)
        self.app.paletteChanged.connect(callback)
        paint = CanvasPaints(self.app)
        try:
            for _ in range(2):
                self.assertIsInstance(self.w.tabs_by_key[self.w.active_key].widget, WorkspaceHome)
                self.w.sidebar_preference = False
                self.w.sync_sidebar()
                tab = self.w.open_path(path)
                self.events()
                self.assertTrue(tab.host.canvas._has_presented_view)
                self.assertTrue(tab.host.canvas.containers)
                self.assertEqual(self.app.palette(), palette)
                self.assertFalse(self.w.sidebar.isVisible())
                self.assertTrue(self.w.close_pane_tabs(self.w.active_pane))
                self.events()
                self.assertEqual(self.w.tab_strip.bar.count(), 1)
            self.assertTrue(paint.frames)
            self.assertTrue(all(paint.frames), paint.frames)
            self.assertEqual(changes, [])
        finally:
            self.app.removeEventFilter(paint)
            self.app.paletteChanged.disconnect(callback)

    def test_tools_only_construct_the_requested_pages_and_reuse_them(self):
        note = self.w.open_path(self.root/'Notes.md')
        note.widget.insertPlainText('pending ')
        self.w.open_route('gallery')
        self.events()
        host = self.w.utility
        self.assertTrue(page_ready(host.gallery))
        self.assertFalse(page_ready(host.settings_page))
        self.assertFalse(page_ready(host.clean_export))
        self.assertFalse(hasattr(host, 'prompt_editor'))
        self.w.open_route('explore')
        self.events()
        settings = host.settings_page
        self.assertTrue(page_ready(settings))
        self.assertFalse(page_ready(settings.civitai))
        self.assertFalse(page_ready(settings.comfy_content))
        self.w.open_route('export')
        self.events()
        self.assertTrue(page_ready(host.clean_export))
        self.w.open_route('home')
        self.events()
        self.assertIs(self.w.tabs_by_key[self.w.active_key], note)
        self.assertIn('pending ', note.widget.toPlainText())
        self.assertTrue(note.widget.document().isUndoAvailable())
        self.w.open_route('gallery')
        self.assertIs(self.w.utility, host)
        self.assertFalse(page_ready(settings.comfy_content))
        self.w.open_route('settings', 'workflows')
        self.events()
        self.assertTrue(page_ready(settings.comfy_content))
        self.assertTrue(settings.workflow_manager.isVisible())
        self.assertFalse(page_ready(settings.civitai))


