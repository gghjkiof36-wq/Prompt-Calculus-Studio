"""Camera backing quiets hover only while wheel or pan owns scene input."""
import unittest

from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QInputMethodEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QLineEdit, QPlainTextEdit
from PySide6.QtTest import QTest

import test_canvas_pan_cache as pan_fixture

APP = pan_fixture.APP


class HoverItem(QGraphicsRectItem):
    def __init__(self):
        super().__init__(-50, -50, 100, 100)
        self.setAcceptHoverEvents(True)
        self.hover_events = 0

    def hoverMoveEvent(self, event):
        self.hover_events += 1
        self.update()
        super().hoverMoveEvent(event)


class CanvasWheelInteractionTests(unittest.TestCase):
    events = pan_fixture.CanvasPanCacheTests.events
    real_canvas = pan_fixture.CanvasPanCacheTests.real_canvas

    def mouse(self, view, kind, point, button=Qt.MouseButton.NoButton,
              buttons=Qt.MouseButton.NoButton):
        event = QMouseEvent(kind, QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                            button, buttons, Qt.KeyboardModifier.NoModifier)
        APP.sendEvent(view.viewport(), event)
        self.events()

    def wheel(self, view, point=None, delta=120, buttons=Qt.MouseButton.NoButton):
        point = point or view.viewport().rect().center()
        event = QWheelEvent(QPointF(point), QPointF(view.viewport().mapToGlobal(point)),
                            QPoint(), QPoint(0, delta), buttons,
                            Qt.KeyboardModifier.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
        APP.sendEvent(view.viewport(), event)
        self.events()

    def test_wheel_hover_is_quiet_but_scene_updates_and_idle_hover_stay_live(self):
        with self.real_canvas() as (view, old):
            view.scene().removeItem(old)
            item = HoverItem(); view.scene().addItem(item)
            self.events()
            point = view.mapFromScene(QPointF(0, 0))
            self.mouse(view, QEvent.Type.MouseMove, point)
            self.mouse(view, QEvent.Type.MouseMove, point+QPoint(1, 0))
            self.assertGreater(item.hover_events, 0)
            self.wheel(view, point)
            cache = view.pan_raster
            self.assertFalse(view.isInteractive())
            before_hover, captures, hits = item.hover_events, cache.captures, cache.hits
            for delta in (-120, 120, -120, 120):
                self.mouse(view, QEvent.Type.MouseMove, point)
                self.wheel(view, point, delta)
            self.assertEqual(item.hover_events, before_hover)
            self.assertEqual(cache.captures, captures)
            self.assertGreater(cache.hits, hits)
            item.setBrush(QColor('#c05038')); self.events()
            self.assertGreater(cache.captures, captures)
            self.assertFalse(view.isInteractive())
            QTest.qWait(170); self.events()
            self.assertTrue(view.isInteractive())
            self.assertFalse(cache.active)
            self.mouse(view, QEvent.Type.MouseMove, point+QPoint(2, 0))
            self.assertGreater(item.hover_events, before_hover)

    def test_proxy_focus_typing_undo_and_ime_are_restored_before_dispatch(self):
        with self.real_canvas() as (view, _):
            edit = QLineEdit('draft')
            proxy = view.scene().addWidget(edit)
            proxy.setPos(-80, -15); edit.resize(160, 35)
            view.setFocus(); edit.setFocus(); edit.setCursorPosition(len(edit.text()))
            self.events()
            focus = view.scene().focusItem()
            self.assertIsNotNone(focus)
            self.wheel(view, QPoint(20, 20))
            self.assertIs(view.scene().focusItem(), focus)
            self.assertFalse(view.isInteractive())
            QTest.keyClicks(view, 'x'); self.events()
            self.assertEqual(edit.text(), 'draftx')
            self.assertTrue(view.isInteractive()); self.assertFalse(view.pan_raster.active)
            self.wheel(view, QPoint(20, 20))
            QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
            self.events()
            self.assertEqual(edit.text(), 'draft')
            self.assertTrue(view.isInteractive())
            self.wheel(view, QPoint(20, 20))
            commit = QInputMethodEvent(); commit.setCommitString('文')
            APP.sendEvent(view, commit); self.events()
            self.assertEqual(edit.text(), 'draft文')
            self.assertTrue(view.isInteractive())

    def test_press_restores_scene_drag_and_wheel_during_grab_does_not_suspend_it(self):
        with self.real_canvas() as (view, item):
            item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                          QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.wheel(view)
            point = view.mapFromScene(item.sceneBoundingRect().center())
            self.mouse(view, QEvent.Type.MouseButtonPress, point,
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
            self.assertTrue(view.isInteractive())
            self.assertFalse(view.pan_raster.active)
            self.assertIs(view.scene().mouseGrabberItem(), item)
            # Even a buttonless wheel cannot start a backing while a scene
            # item owns the grab (for example, a driver reporting no buttons).
            self.wheel(view, point)
            self.assertTrue(view.isInteractive()); self.assertFalse(view.pan_raster.active)
            old = item.pos()
            self.mouse(view, QEvent.Type.MouseMove, point+QPoint(35, 10),
                       buttons=Qt.MouseButton.LeftButton)
            self.mouse(view, QEvent.Type.MouseButtonRelease, point+QPoint(35, 10),
                       Qt.MouseButton.LeftButton)
            self.assertNotEqual(item.pos(), old)
            self.assertIsNone(view.scene().mouseGrabberItem())

    def test_wheel_to_middle_pan_preserves_pan_ownership(self):
        with self.real_canvas() as (view, _):
            self.wheel(view)
            point = QPoint(12, 12)
            self.mouse(view, QEvent.Type.MouseButtonPress, point,
                       Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
            self.assertFalse(view.isInteractive())
            self.assertIsNotNone(view.pan)
            self.assertTrue(view.pan_raster.active); self.assertFalse(view.pan_raster.allow_zoom)
            self.wheel(view, point, buttons=Qt.MouseButton.MiddleButton)
            self.assertFalse(view.isInteractive()); self.assertFalse(view.pan_raster.allow_zoom)
            before = view.horizontalScrollBar().value()
            self.mouse(view, QEvent.Type.MouseMove, point+QPoint(30, 0),
                       buttons=Qt.MouseButton.MiddleButton)
            self.assertNotEqual(view.horizontalScrollBar().value(), before)
            self.mouse(view, QEvent.Type.MouseButtonRelease, point+QPoint(30, 0),
                       Qt.MouseButton.MiddleButton)
            self.assertIsNone(view.pan); self.assertFalse(view.pan_raster.active)
            self.assertTrue(view.isInteractive())

    def test_pan_after_hover_keeps_backing_live_without_replaying_hover(self):
        for button in (Qt.MouseButton.MiddleButton, Qt.MouseButton.LeftButton):
            with self.subTest(button=button), self.real_canvas() as (view, old):
                view.scene().removeItem(old)
                item = HoverItem(); view.scene().addItem(item); self.events()
                center = view.mapFromScene(QPointF(0, 0))
                self.mouse(view, QEvent.Type.MouseMove, center)
                self.mouse(view, QEvent.Type.MouseMove, center+QPoint(1, 0))
                self.assertGreater(item.hover_events, 0)
                # Reproduce the wheel -> pan route that left a stale,
                # buttonless scene mouse position for Qt to replay.
                self.wheel(view, center)
                self.wheel(view, center, -120)
                view.pan_raster.finish_zoom(); self.events()
                center = view.mapFromScene(QPointF(0, 0))
                self.mouse(view, QEvent.Type.MouseMove, center)
                point = center if button == Qt.MouseButton.MiddleButton else QPoint(12, 12)
                self.mouse(view, QEvent.Type.MouseButtonPress, point, button, button)
                cache = view.pan_raster
                # Complete the first pan frame and its queued scene updates
                # before measuring steady reuse. No later capture is allowed.
                self.mouse(view, QEvent.Type.MouseMove, point+QPoint(2, 0), buttons=button)
                self.events()
                hover, captures, hits = item.hover_events, cache.captures, cache.hits
                for step in range(2, 9):
                    self.mouse(view, QEvent.Type.MouseMove, point+QPoint(2*step, 0), buttons=button)
                self.assertEqual(item.hover_events, hover)
                self.assertEqual(cache.captures, captures)
                self.assertGreater(cache.hits, hits)
                self.assertFalse(view.isInteractive())
                image = cache.image.cacheKey()
                item.setBrush(QColor('#c05038')); self.events()
                self.assertGreater(cache.captures, captures)
                self.assertNotEqual(cache.image.cacheKey(), image)
                self.assertFalse(view.isInteractive())
                self.mouse(view, QEvent.Type.MouseButtonRelease, point+QPoint(16, 0), button)
                self.assertTrue(view.isInteractive()); self.assertFalse(cache.active)
                self.mouse(view, QEvent.Type.MouseMove, view.mapFromScene(QPointF(2, 0)))
                self.assertGreater(item.hover_events, hover)

    def test_pan_release_restores_proxy_editing_and_scene_drag(self):
        with self.real_canvas() as (view, item):
            edit = QLineEdit('draft')
            proxy = view.scene().addWidget(edit)
            proxy.setPos(-80, -15); edit.resize(160, 35)
            item.setPos(0, 90)
            item.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable |
                          QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
            self.events()
            point = QPoint(12, 12)
            self.mouse(view, QEvent.Type.MouseButtonPress, point,
                       Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
            cache = view.pan_raster; captures = cache.captures
            edit.setText('updated'); self.events()
            self.assertGreater(cache.captures, captures)
            self.assertFalse(view.isInteractive())
            self.mouse(view, QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.MiddleButton)
            edit_point = view.mapFromScene(proxy.mapToScene(QPointF(30, 15)))
            self.mouse(view, QEvent.Type.MouseButtonPress, edit_point,
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
            self.mouse(view, QEvent.Type.MouseButtonRelease, edit_point, Qt.MouseButton.LeftButton)
            QTest.keyClick(view, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
            QTest.keyClicks(view, 'typed'); self.events()
            self.assertEqual(edit.text(), 'typed')
            QTest.keyClick(view, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier); self.events()
            self.assertEqual(edit.text(), 'updated')
            drag_point = view.mapFromScene(item.sceneBoundingRect().center())
            self.mouse(view, QEvent.Type.MouseButtonPress, drag_point,
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton)
            self.assertIs(view.scene().mouseGrabberItem(), item)
            old = item.pos()
            self.mouse(view, QEvent.Type.MouseMove, drag_point+QPoint(25, 5),
                       buttons=Qt.MouseButton.LeftButton)
            self.mouse(view, QEvent.Type.MouseButtonRelease, drag_point+QPoint(25, 5),
                       Qt.MouseButton.LeftButton)
            self.assertNotEqual(item.pos(), old)
            self.assertIsNone(view.scene().mouseGrabberItem())

    def test_pan_cleanup_and_transform_preserve_original_interaction_policy(self):
        for interactive in (False, True):
            for action in ('release', 'hide', 'close', 'end', 'transform'):
                with self.subTest(interactive=interactive, action=action), self.real_canvas() as (view, _):
                    view.setInteractive(interactive)
                    point = QPoint(12, 12)
                    self.mouse(view, QEvent.Type.MouseButtonPress, point,
                               Qt.MouseButton.MiddleButton, Qt.MouseButton.MiddleButton)
                    view.pan_raster.begin()  # Repeated begin must retain the original policy.
                    self.assertFalse(view.isInteractive())
                    if action in ('release', 'transform'):
                        if action == 'transform':
                            view.scale(1.02, 1.02); self.events()
                            self.assertIsNotNone(view.pan)
                            self.assertFalse(view.isInteractive())
                        self.mouse(view, QEvent.Type.MouseButtonRelease, point, Qt.MouseButton.MiddleButton)
                    elif action == 'hide':
                        view.hide(); self.events()
                    elif action == 'close':
                        view.close(); self.events()
                    else:
                        view.pan_raster.end()  # Renderer fallback also uses this path.
                    self.assertEqual(view.isInteractive(), interactive)
                    self.assertFalse(view.pan_raster.active)
                    self.assertEqual(view.pan_raster.byte_count, 0)

    def test_direct_wheel_to_pan_transfer_restores_original_policy(self):
        for interactive in (False, True):
            with self.subTest(interactive=interactive), self.real_canvas() as (view, _):
                view.setInteractive(interactive)
                cache = view.pan_raster
                cache.begin_zoom()
                view.pan = QPointF(12, 12)
                cache.begin()
                self.assertFalse(view.isInteractive())
                self.assertTrue(cache.active); self.assertFalse(cache.allow_zoom)
                self.assertFalse(cache.zoom_timer.isActive())
                cache.finish_zoom()  # A late wheel completion cannot release the pan.
                self.assertFalse(view.isInteractive()); self.assertTrue(cache.active)
                view.pan = None; cache.end()
                self.assertEqual(view.isInteractive(), interactive)

    def test_scrollable_editor_wheel_ends_camera_gesture_and_keeps_zoom(self):
        with self.real_canvas() as (view, _):
            edit = QPlainTextEdit('\n'.join('line '+str(i) for i in range(100)))
            proxy = view.scene().addWidget(edit)
            proxy.setPos(-90, -50); edit.resize(180, 100)
            self.events(); self.assertGreater(edit.verticalScrollBar().maximum(), 0)
            self.wheel(view, QPoint(12, 12))
            self.assertFalse(view.isInteractive())
            point = view.mapFromScene(proxy.mapToScene(QPointF(60, 45)))
            zoom = view.transform().m11()
            self.wheel(view, point, -120)
            self.assertEqual(view.transform().m11(), zoom)
            self.assertGreater(edit.verticalScrollBar().value(), 0)
            self.assertTrue(view.isInteractive()); self.assertFalse(view.pan_raster.active)

    def test_original_noninteractive_policy_and_hide_programmatic_end_are_preserved(self):
        with self.real_canvas() as (view, _):
            for interactive in (False, True):
                for action in ('timeout', 'hide', 'transform', 'end'):
                    with self.subTest(interactive=interactive, action=action):
                        view.show(); view.setInteractive(interactive); self.events()
                        self.wheel(view)
                        self.assertFalse(view.isInteractive())
                        if action == 'timeout':
                            QTest.qWait(170); self.events()
                        elif action == 'hide':
                            view.hide(); self.events()
                        elif action == 'transform':
                            view.scale(1.02, 1.02)
                        else:
                            view.pan_raster.end()  # Also used by renderer fallback.
                        self.assertEqual(view.isInteractive(), interactive)
                        self.assertFalse(view.pan_raster.allow_zoom)


if __name__ == '__main__':
    unittest.main()
