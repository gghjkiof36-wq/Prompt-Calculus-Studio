"""Captured tab gestures and visible drop destinations, without desktop input."""
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_workspace_panes import WorkspacePaneTests


class WorkspaceTabDragTests(unittest.TestCase):
    setUpClass = WorkspacePaneTests.__dict__['setUpClass']
    setUp = WorkspacePaneTests.setUp
    tearDown = WorkspacePaneTests.tearDown
    events = WorkspacePaneTests.events
    open = WorkspacePaneTests.open

    def press(self, tab):
        bar = self.w.pane_for(tab).strip.bar
        QTest.mousePress(bar, Qt.MouseButton.LeftButton,
                        pos=bar.tabRect(self.w.index_of(tab.key)).center())
        return bar

    def pointer(self, bar, position, release=False):
        event = QMouseEvent(QEvent.Type.MouseButtonRelease if release else QEvent.Type.MouseMove,
                            QPointF(bar.mapFromGlobal(position)), QPointF(position),
                            Qt.MouseButton.LeftButton if release else Qt.MouseButton.NoButton,
                            Qt.MouseButton.NoButton if release else Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(bar, event)
        self.events()

    def right_point(self, pane):
        return pane.pages.mapToGlobal(QPoint(pane.pages.width()*3//4, pane.pages.height()//2))

    def test_new_tab_splits_and_recent_open_follows_its_new_pane(self):
        original = self.open('Notes.md')
        tab = self.w.new_tab()
        self.events()
        left = self.w.panes[0]
        bar = self.press(tab)
        point = self.right_point(left)
        self.pointer(bar, point)
        self.assertIs(self.w.tab_drag.dragging, tab)
        self.assertTrue(self.w.tab_drag.preview.isVisible())
        self.assertEqual(self.w.tab_drag.target, ('split', None))
        self.pointer(bar, point, release=True)
        self.assertEqual(len(self.w.panes), 2)
        right = self.w.panes[1]
        self.assertIs(self.w.pane_for(tab), right)
        self.assertIs(self.w.pane_for(original), left)
        self.assertFalse(self.w.tab_drag.preview.isVisible())
        listing = tab.widget.listing
        self.assertGreater(listing.count(), 0)
        QTest.mouseClick(listing.viewport(), Qt.MouseButton.LeftButton,
                         pos=listing.visualItemRect(listing.item(0)).center())
        self.events()
        self.assertNotIn(tab.key, self.w.tabs_by_key)
        self.assertEqual(right.strip.bar.count(), 1)
        self.assertEqual(left.strip.bar.count(), 1)
        self.assertEqual(self.w.tabs_by_key[right.strip.bar.tabData(0)].path, original.path)

    def test_existing_pane_body_accepts_dirty_view_without_recreating_it(self):
        tab = self.open('Notes.md')
        tab.widget.insertPlainText('draft ')
        widget, before = tab.widget, tab.path.read_bytes()
        right = self.w.ensure_split()
        self.w.focus_tab(tab)
        self.events()
        bar = self.press(tab)
        point = right.pages.mapToGlobal(right.pages.rect().center())
        self.pointer(bar, point)
        self.assertTrue(self.w.tab_drag.preview.isVisible())
        self.assertIs(self.w.tab_drag.target[0], right)
        self.pointer(bar, point, release=True)
        self.assertIs(self.w.pane_for(tab), right)
        self.assertIs(tab.widget, widget)
        self.assertEqual(tab.path.read_bytes(), before)
        self.assertTrue(widget.document().isUndoAvailable())
        widget.undo()
        self.assertNotIn('draft ', widget.toPlainText())
        widget.redo()
        self.assertIn('draft ', widget.toPlainText())

    def order(self, pane):
        return [pane.strip.bar.tabData(i) for i in range(pane.strip.bar.count())]

    def test_horizontal_drag_starts_in_the_row_and_uses_insertion_boundary(self):
        a = self.open('Notes.md')
        b = self.open('Empty.md', new_tab=True)
        c = self.w.new_tab()
        self.events()
        pane = self.w.panes[0]
        bar = self.press(a)
        point = bar.mapToGlobal(bar.tabRect(1).center()+QPoint(15, 0))
        self.pointer(bar, point)
        self.assertIs(self.w.tab_drag.dragging, a)
        self.assertTrue(self.w.tab_drag.preview.isVisible())
        self.assertEqual(self.w.tab_drag.preview.width(), 3)
        self.assertEqual(self.order(pane), [a.key, b.key, c.key])
        self.pointer(bar, point, release=True)
        self.assertEqual(self.order(pane), [b.key, a.key, c.key])
        bar = self.press(a)
        point = bar.mapToGlobal(bar.tabRect(1).center()+QPoint(18, 0))
        self.pointer(bar, point)
        self.pointer(bar, point, release=True)
        self.assertEqual(self.order(pane), [b.key, a.key, c.key])
        self.assertTrue(bar.isMovable())

    def test_escape_invalid_drop_and_deactivation_leave_views_unchanged(self):
        tab = self.open('Notes.md')
        initial = self.order(self.w.panes[0])
        bar = self.press(tab)
        point = self.right_point(self.w.panes[0])
        self.pointer(bar, point)
        self.assertTrue(self.w.tab_drag.preview.isVisible())
        QTest.keyClick(bar, Qt.Key.Key_Escape)
        self.pointer(bar, point, release=True)
        self.assertIsNone(self.w.tab_drag.dragging)
        self.assertFalse(self.w.tab_drag.preview.isVisible())
        self.assertFalse(self.w.tab_drag.label.isVisible())
        bar = self.press(tab)
        outside = self.w.mapToGlobal(QPoint(-20, -20))
        self.pointer(bar, point)
        self.pointer(bar, outside)
        self.assertIsNone(self.w.tab_drag.target)
        self.assertFalse(self.w.tab_drag.preview.isVisible())
        self.pointer(bar, outside, release=True)
        self.assertEqual(self.order(self.w.panes[0]), initial)
        bar = self.press(tab)
        self.pointer(bar, point)
        QApplication.sendEvent(self.app, QEvent(QEvent.Type.ApplicationDeactivate))
        self.pointer(bar, point, release=True)
        self.assertEqual(len(self.w.panes), 1)
        self.assertEqual(self.order(self.w.panes[0]), initial)
        bar = self.press(tab)
        self.pointer(bar, point)
        QApplication.sendEvent(bar, QEvent(QEvent.Type.UngrabMouse))
        self.assertIsNone(self.w.tab_drag.dragging)
        # A new click after lost capture must not lose its release event.
        QTest.mouseClick(self.w.panes[0].strip.plus, Qt.MouseButton.LeftButton)
        self.assertEqual(self.w.panes[0].strip.bar.count(), len(initial)+1)

    def test_canvas_moves_preserve_contents_and_do_not_refresh_unchanged_nodes(self):
        tab = self.open('Street.pcs-workflow')
        self.open('Notes.md', new_tab=True)
        self.w.focus_tab(tab)
        self.events()
        canvas = tab.host.canvas
        parent = tab.widget.parentWidget()
        ports = tuple(id(port) for port in canvas.ports)
        with patch.object(canvas, 'refresh', wraps=canvas.refresh) as refresh:
            for i in range(3):
                source = self.w.pane_for(tab)
                bar = self.press(tab)
                target = next((p for p in self.w.panes if p is not source), None)
                point = target.pages.mapToGlobal(target.pages.rect().center()) if target else self.right_point(source)
                self.pointer(bar, point)
                self.assertTrue(self.w.tab_drag.preview.isVisible())
                self.pointer(bar, point, release=True)
                self.assertIsNot(self.w.pane_for(tab), source)
                self.assertIs(tab.widget.parentWidget(), parent)
                self.assertEqual(tuple(id(port) for port in canvas.ports), ports)
            self.assertEqual(refresh.call_count, 0)

    def test_body_focus_and_visibility_follow_the_pane_after_move_and_merge(self):
        a = self.open('Notes.md')
        b = self.open('Empty.md', new_tab=True)
        self.w.move_view(a)
        self.events()
        for tab in (b, a):
            QTest.mouseClick(tab.widget.viewport(), Qt.MouseButton.LeftButton, pos=QPoint(30, 20))
            self.events()
            self.assertIs(self.w.active_pane, self.w.pane_for(tab))
        self.w.open_route('settings')
        self.events()
        self.assertFalse(a.widget.isVisible())
        self.assertFalse(b.widget.isVisible())
        self.w.focus_tab(a)
        self.events()
        self.assertTrue(a.widget.isVisible())
        self.assertTrue(b.widget.isVisible())
        self.w.merge_panes()
        self.events()
        self.assertTrue(a.widget.isVisible())
        self.assertFalse(b.widget.isVisible())
        self.w.focus_tab(b)
        self.events()
        self.assertTrue(b.widget.isVisible())
        self.assertFalse(a.widget.isVisible())

    def test_reduced_motion_keeps_highlight_and_normal_clicks_work(self):
        a = self.open('Notes.md')
        b = self.open('Empty.md', new_tab=True)
        self.w.settings['reduce_motion'] = True
        bar = self.press(a)
        point = self.right_point(self.w.panes[0])
        self.pointer(bar, point)
        self.assertTrue(self.w.tab_drag.preview.isVisible())
        self.assertEqual(self.w.tab_drag.preview.amount, 1.0)
        QTest.keyClick(bar, Qt.Key.Key_Escape)
        self.pointer(bar, point, release=True)
        QTest.mouseClick(bar, Qt.MouseButton.LeftButton, pos=bar.tabRect(self.w.index_of(b.key)).center())
        self.assertEqual(self.w.active_key, b.key)
        self.assertIsNone(self.w.tab_drag.dragging)
        self.assertTrue(bar.isMovable())

    def test_overflow_scroll_arrows_remain_available_during_drag(self):
        for _ in range(9):
            tab = self.w.new_tab()
        self.events()
        bar = self.w.panes[0].strip.bar
        control = next(c for c in bar.scroll_controls if c.isVisible() and c.isEnabled())
        before = bar.tabRect(0).x()
        self.press(tab)
        point = control.mapToGlobal(control.rect().center())
        self.pointer(bar, point)
        self.assertIs(self.w.tab_drag.scroll_control, control)
        QTest.qWait(280)
        self.events()
        self.assertNotEqual(bar.tabRect(0).x(), before)
        QTest.keyClick(bar, Qt.Key.Key_Escape)
        self.pointer(bar, point, release=True)
        self.assertFalse(self.w.tab_drag.scroll_timer.isActive())
        self.assertEqual(len(self.w.panes), 1)

    def test_split_resize_hit_area_stays_above_both_editors(self):
        tab = self.open('Notes.md')
        self.w.move_view(tab)
        self.events()
        grip = self.w.pane_grip
        center = grip.mapTo(self.w, grip.rect().center())
        for dx in (-2, -1, 0, 1, 2):
            self.assertIs(self.w.childAt(center+QPoint(dx, 0)), grip)
        before = self.w.pane_split.sizes()
        QTest.mousePress(grip, Qt.MouseButton.LeftButton, pos=grip.rect().center())
        target = grip.mapToGlobal(grip.rect().center())+QPoint(55, 0)
        self.pointer(grip, target)
        self.pointer(grip, target, release=True)
        after = self.w.pane_split.sizes()
        self.assertGreater(after[0], before[0]+40)
        self.assertLess(after[1], before[1]-40)
        for pane in self.w.panes:
            page = pane.pages.currentWidget()
            self.assertEqual(page.geometry().size(), pane.pages.size())


if __name__ == '__main__':
    unittest.main()
