"""Passive update counting, event-loop latency and workspace preference lifetime."""
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QFontMetrics
from PySide6.QtTest import QTest

import test_workspace_shell as fixture
from prompt_calculus_studio.workspace_performance import PerformanceSamples
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class PerformanceSamplesTests(unittest.TestCase):
    def test_wakes_are_not_frames_and_lag_expires(self):
        samples = PerformanceSamples(0.)
        for index in range(1, 21):
            samples.wake(index * .05, index * .05)
        self.assertEqual(samples.values(1.), (0., 0.))
        samples.wake(1.2, 1.05)
        self.assertAlmostEqual(samples.values(1.2)[1], 150.)
        samples.paint(1.4)
        samples.paint(1.5)
        self.assertEqual(samples.values(2.)[0], 2.)
        self.assertAlmostEqual(samples.values(2.)[1], 150.)
        self.assertEqual(samples.values(2.6), (0., 0.))

    def test_windows_are_bounded_and_resume_discards_old_samples(self):
        samples = PerformanceSamples(0.)
        for index in range(5000):
            samples.paint(index / 10000.)
            samples.wake(index / 10000., 0.)
        self.assertLessEqual(len(samples.paints), 4096)
        self.assertLessEqual(len(samples.latencies), 128)
        samples.reset(10.)
        self.assertEqual(samples.values(10.), (0., 0.))


class WorkspacePerformanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.WorkspaceShellTests.setUpClass.__func__(cls)
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            QFontDatabase.addApplicationFont('C:/Windows/Fonts/' + name)
    events = fixture.WorkspaceShellTests.events
    open = fixture.WorkspaceShellTests.open
    tearDown = fixture.WorkspaceShellTests.tearDown

    def setUp(self):
        fixture.WorkspaceShellTests.setUp(self)
        self.w.sync_display_settings(dict(self.w.settings, font_family='Microsoft JhengHei UI'))
        self.w.activateWindow()
        self.events()
        self.monitor = self.w.performance_monitor
        self.assertTrue(self.monitor.running)

    def test_actual_child_paints_count_once_and_monitor_does_not_drive_idle(self):
        # A content update may repaint several widgets, but is one window batch.
        self.monitor.timer.stop()
        self.events()
        self.monitor.samples.reset(time.perf_counter() - 1.)
        for child in (self.w.rail, self.w.routes['home'], self.w.routes['settings']):
            child.update()
        self.app.processEvents()
        self.assertEqual(len(self.monitor.samples.paints), 1)
        self.monitor.samples.reset(time.perf_counter() - 1.)
        self.monitor.rate_text, self.monitor.delay_text = '123', '456ms'
        self.monitor.place()
        self.monitor.update()
        self.app.processEvents()
        self.assertEqual(len(self.monitor.samples.paints), 0)
        # No periodic redraw is requested just to sample the GUI wake-up time.
        self.monitor.expected = time.perf_counter() + .05
        self.monitor.last_display = time.perf_counter()
        self.monitor.timer.start()
        QTest.qWait(2250)
        self.assertEqual(self.monitor.samples.values(time.perf_counter())[0], 0.)
        self.assertEqual(self.monitor.lines[:2], ('FPS', '0'))

    def test_fps_readout_counts_real_batches_and_explains_its_meaning(self):
        monitor = self.monitor
        monitor.timer.stop()
        self.events()
        monitor.samples.reset(time.perf_counter() - 1.)
        for _ in range(3):
            self.w.routes['home'].update()
            self.app.processEvents()
        self.assertEqual(len(monitor.samples.paints), 3)
        monitor.last_display = time.perf_counter() - 1.1
        monitor.expected = time.perf_counter()
        monitor.tick()
        self.assertEqual(monitor.lines[:2], ('FPS', '3'))
        self.assertIn('FPS 3', monitor.accessibleName())
        self.assertEqual(monitor.accessibleDescription(), monitor.toolTip())
        for explanation in ('介面重繪幀率', '靜止時可為 0', '不是螢幕更新率', 'Hz'):
            self.assertIn(explanation, monitor.toolTip())

    def test_a_gui_stall_records_latency_without_fabricating_frames(self):
        QTest.qWait(80)
        self.monitor.samples.reset(time.perf_counter() - 1.)
        time.sleep(.18)  # This is the deliberate, bounded GUI-thread stall.
        self.app.processEvents()
        self.assertGreater(self.monitor.samples.values(time.perf_counter())[1], 100.)
        self.assertLessEqual(len(self.monitor.samples.paints), 1)

    def test_checkbox_persists_without_editing_documents(self):
        tab = self.open('Notes', 'md')
        before = tab.path.read_bytes()
        self.w.open_route('settings')
        self.events()
        control = self.w.utility.settings_page.preferences.performance_monitor
        self.assertTrue(control.isChecked())
        self.assertEqual(control.toolTip(), self.monitor.toolTip())
        self.assertFalse(control.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        control.setChecked(False)
        self.events()
        self.assertFalse(self.monitor.timer.isActive())
        self.assertFalse(self.monitor.isVisible())
        saved = json.loads((self.w.data / 'workspace-ui.json').read_text(encoding='utf-8'))
        self.assertIs(saved['performance_monitor_enabled'], False)
        self.assertEqual(tab.path.read_bytes(), before)
        self.assertTrue(self.w.close())
        self.w.deleteLater()
        self.app.processEvents()
        self.w = WorkspaceShell(self.root, Path(self.area.name) / 'data')
        self.w.show()
        self.w.activateWindow()
        self.app.setActiveWindow(self.w)  # Offscreen has no native activation on re-show.
        self.events()
        self.assertFalse(self.w.performance_monitor_enabled)
        self.assertFalse(self.w.performance_monitor.timer.isActive())
        self.w.set_performance_monitor(True)
        self.assertTrue(self.w.performance_monitor.timer.isActive())

    def test_position_mouse_pass_through_and_large_font_narrow_window(self):
        self.open('Notes', 'md')
        monitor = self.monitor
        original = monitor.pos()
        self.w.toggle_sidebar()
        self.events()
        self.assertEqual(monitor.pos(), original)
        self.assertTrue(monitor.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(monitor.focusPolicy(), Qt.FocusPolicy.NoFocus)
        self.assertIsNot(self.w.childAt(monitor.geometry().center()), monitor)
        for width, font in ((1440, 12), (740, 18)):
            self.w.resize(width, 900)
            self.w.sync_display_settings(dict(self.w.settings, ui_size=font))
            self.events()
            self.assertTrue(self.w.rect().contains(monitor.geometry()))
            anchor_y = self.w.routes['settings'].mapTo(self.w, QPoint(0, 0)).y()
            self.assertLess(monitor.geometry().bottom(), anchor_y)
            rail_rect = QRect(self.w.rail.mapTo(self.w, QPoint()), self.w.rail.size())
            self.assertTrue(rail_rect.contains(monitor.geometry()))
            self.assertLessEqual(monitor.width(), 44)
            self.assertEqual(monitor.paint_font.pixelSize(), 9)
            self.assertEqual(len(monitor.lines), 4)
            metrics = QFontMetrics(monitor.paint_font)
            self.assertTrue(all(metrics.horizontalAdvance(line) + 4 <= monitor.width()
                                for line in ('FPS', '4096', 'Lag', '9999+s')))
            if folder := os.environ.get('PCS_PERFORMANCE_EVIDENCE'):
                target = Path(folder)
                target.mkdir(parents=True, exist_ok=True)
                self.assertTrue(self.w.grab().save(str(target / f'side-{width}-{font}.png')))
        self.w.set_navigation_position('top')
        self.events()
        self.assertTrue(self.w.rect().contains(monitor.geometry()))
        self.assertGreater(monitor.y(), self.w.height() // 2)
        self.assertLessEqual(monitor.geometry().bottom(), self.w.height() - 48)
        self.assertEqual(len(monitor.lines), 2)
        self.assertFalse(monitor.geometry().intersects(self.w.rail.geometry()))
        if folder := os.environ.get('PCS_PERFORMANCE_EVIDENCE'):
            self.assertTrue(self.w.grab().save(str(Path(folder) / 'top-740-18.png')))

    def test_close_veto_keeps_the_live_monitor(self):
        with patch.object(self.w, '_close_workspace_event', side_effect=lambda event: event.ignore()):
            self.assertFalse(self.w.close())
        self.assertFalse(self.monitor.closed)
        self.assertTrue(self.monitor.timer.isActive())
        self.assertTrue(self.monitor.isVisible())

    def test_hidden_background_and_close_stop_observation(self):
        monitor = self.monitor
        self.w.hide()
        self.events()
        self.assertFalse(monitor.timer.isActive())
        self.w.show()
        self.w.activateWindow()
        self.app.setActiveWindow(self.w)
        self.events()
        self.assertTrue(monitor.timer.isActive())
        with patch.object(self.w, 'isActiveWindow', return_value=False):
            monitor.sync()
            self.assertFalse(monitor.running)
        monitor.sync()
        self.assertTrue(monitor.running)
        self.assertEqual(len(monitor.samples.paints), 0)
        self.w.showMinimized()
        self.events()
        self.assertFalse(monitor.timer.isActive())
        self.w.showNormal()
        self.w.activateWindow()
        self.app.setActiveWindow(self.w)
        self.events()
        self.assertTrue(monitor.timer.isActive())
        self.assertEqual(len([timer for timer in monitor.findChildren(QTimer) if not timer.isSingleShot()]), 1)
        self.assertTrue(self.w.close())
        self.assertTrue(monitor.closed)
        self.assertFalse(monitor.timer.isActive())
        router = self.app._pcs_application_events
        self.assertFalse(any(ref() is monitor for entries in router.listeners.values() for ref in entries))
