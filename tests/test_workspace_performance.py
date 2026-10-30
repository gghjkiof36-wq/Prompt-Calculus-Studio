"""Real GUI callback cadence, responsiveness and workspace preference lifetime."""
import json
import os
from pathlib import Path
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEventLoop, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QFontDatabase, QFontMetrics, QPainter, QScreen
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

import test_workspace_shell as fixture
from prompt_calculus_studio.workspace_performance import PerformanceSamples
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class ContentPaintProbe(QWidget):
    """A real opaque content surface, independent of the readout region."""
    def __init__(self, owner):
        super().__init__(owner)
        self.window = owner  # Some PCS content widgets use this owner attribute.
        self.paints = 0
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setGeometry(300, 180, 120, 80)

    def paintEvent(self, event):
        self.paints += 1
        painter = QPainter(self)
        painter.fillRect(event.rect(), self.palette().window())


class PerformanceSamplesTests(unittest.TestCase):
    def test_fractional_deadlines_do_not_drift_at_60_144_and_5994_hz(self):
        for rate in (60., 144., 59.94):
            with self.subTest(rate=rate):
                start = now = 123.456
                samples = PerformanceSamples(start, rate)
                waits = set()
                for index in range(1, 901):
                    delay = samples.wait_ms(now)
                    self.assertGreaterEqual(delay, 1)
                    waits.add(delay)
                    now += delay / 1000.
                    self.assertTrue(samples.pulse(now))
                    # Compare with the independent absolute ideal, not with
                    # a deadline computed by another copy of the algorithm.
                    ideal = start + index / rate
                    self.assertGreaterEqual(now + 1e-9, ideal)
                    self.assertLessEqual(now - ideal, .001001)
                    self.assertAlmostEqual(samples.deadline, start + (index + 1) / rate, places=8)
                    self.assertGreater(samples.deadline, now)
                self.assertGreater(len(waits), 1, 'Fractional intervals need alternating integer waits')
                self.assertAlmostEqual(samples.values(now)[0], rate, delta=1.)

    def test_early_callbacks_do_not_count_or_postpone_the_existing_deadline(self):
        samples = PerformanceSamples(0.)
        deadline = samples.deadline
        for now in (.001, .004, .009, .015):
            self.assertFalse(samples.pulse(now))
            self.assertEqual(samples.deadline, deadline)
            self.assertEqual(len(samples.frames), 0)
        self.assertTrue(samples.pulse(.017))
        self.assertEqual(len(samples.frames), 1)
        self.assertFalse(samples.pulse(.017))
        self.assertLessEqual(samples.wait_ms(.017), 17)

    def test_long_stall_counts_once_skips_missed_slots_and_still_resumes(self):
        samples = PerformanceSamples(0.)
        self.assertTrue(samples.pulse(.017))
        self.assertTrue(samples.pulse(1.37))
        self.assertEqual(len(samples.frames), 2)
        self.assertGreater(samples.deadline, 1.37)
        self.assertLessEqual(samples.deadline - 1.37, 1. / 60. + 1e-9)
        self.assertGreater(samples.frames[-1][1], 1300.)
        for _ in range(10):
            self.assertFalse(samples.pulse(1.37))
        self.assertEqual(len(samples.frames), 2, 'A missed slot is not an observed frame')
        next_callback = 1.37 + samples.wait_ms(1.37) / 1000.
        self.assertTrue(samples.pulse(next_callback))
        self.assertEqual(len(samples.frames), 3)

    def test_one_second_window_keeps_the_missing_time_and_expires_lag(self):
        samples = PerformanceSamples(0., 10.)
        for now in (.101, .201, .301):
            self.assertTrue(samples.pulse(now))
        self.assertTrue(samples.pulse(.901))
        rate, lag = samples.values(1.)
        self.assertEqual(rate, 4., 'Do not divide by only the short retained timestamp span')
        self.assertAlmostEqual(lag, 501.)
        self.assertEqual(samples.values(1.902), (0., 0.))

    def test_samples_are_bounded_and_reset_discards_old_rate_and_stall(self):
        samples = PerformanceSamples(0., 1000.)
        for _ in range(5000):
            samples.pulse(samples.deadline + .000001)
        self.assertLessEqual(len(samples.frames), 4096)
        samples.reset(10., 144.)
        self.assertEqual(samples.values(10.), (0., 0.))
        self.assertEqual(samples.refresh_rate, 144.)
        self.assertAlmostEqual(samples.deadline, 10. + 1. / 144.)

    def test_invalid_rates_fall_back_without_invalid_timer_intervals(self):
        for rate in (None, True, False, '144', 0, -1, .5, 1001, float('inf'), float('-inf'), float('nan')):
            with self.subTest(rate=rate):
                samples = PerformanceSamples(100., rate)
                self.assertEqual(samples.refresh_rate, 60.)
                self.assertEqual(samples.wait_ms(100.), 17)
                self.assertEqual(samples.wait_ms(101.), 1)
        for rate in (1., 59.94, 144., 1000.):
            self.assertEqual(PerformanceSamples.valid_rate(rate), rate)


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
        refresh = patch.object(QScreen, 'refreshRate', return_value=60.)
        refresh.start()
        self.addCleanup(refresh.stop)
        for method in ('get', 'post', 'put', 'sendCustomRequest'):
            network = patch.object(QNetworkAccessManager, method, side_effect=AssertionError('No network'))
            network.start()
            self.addCleanup(network.stop)
        fixture.WorkspaceShellTests.setUp(self)
        self.w.sync_display_settings(dict(self.w.settings, font_family='Microsoft JhengHei UI'))
        self.w.activateWindow()
        self.events()
        self.monitor = self.w.performance_monitor
        self.assertTrue(self.monitor.running)

    def wait(self, milliseconds):
        # qWait processes events in coarse steps, distorting high refresh rates.
        loop = QEventLoop()
        QTimer.singleShot(milliseconds, loop.quit)
        loop.exec()

    def observe_callbacks(self):
        timestamps = []
        self.monitor.timer.timeout.connect(lambda: timestamps.append(time.perf_counter()))
        return timestamps

    def evidence(self, name, value):
        if folder := os.environ.get('PCS_PERFORMANCE_EVIDENCE'):
            target = Path(folder)
            target.mkdir(parents=True, exist_ok=True)
            (target / (name + '.json')).write_text(json.dumps(value, indent=2), encoding='utf-8')

    def test_idle_fps_is_measured_from_real_callbacks_not_requested_refresh_rate(self):
        observed = self.observe_callbacks()
        self.wait(1300)
        now = time.perf_counter()
        rate, latency = self.monitor.samples.values(now)
        actual = sum(now - 1. < stamp <= now for stamp in observed)
        self.assertGreater(actual, 35, observed)
        self.assertLessEqual(actual, 65)
        self.assertAlmostEqual(rate, actual, delta=1.)
        self.assertGreater(float(self.monitor.rate_text), 35.)
        self.assertLess(latency, 150.)
        self.assertTrue(self.monitor.timer.isSingleShot())
        self.assertEqual(self.monitor.timer.timerType(), Qt.TimerType.PreciseTimer)
        self.evidence('idle-callbacks', dict(measured_fps=rate, observed_callbacks_last_second=actual,
                                          requested_refresh_rate=60., max_lag_ms=latency))

    def test_readout_explains_gui_cadence_and_latency_without_claiming_display_frames(self):
        self.wait(400)
        monitor = self.monitor
        self.assertGreater(float(monitor.rate_text), 0.)
        self.assertIn(monitor.rate_text, monitor.accessibleName())
        self.assertEqual(monitor.accessibleDescription(), monitor.toolTip())
        self.assertNotIn('暫無介面重繪', monitor.accessibleName())
        self.assertNotIn('介面重繪幀率', monitor.toolTip())
        for explanation in ('依實際回應計算', '靜止時仍持續量測', '卡頓時下降', '不是 GPU 呈現幀率', '延遲估計值'):
            self.assertIn(explanation, monitor.toolTip())

    def test_real_gui_stall_lowers_fps_raises_lag_and_recovers_without_catchup(self):
        observed = self.observe_callbacks()
        self.wait(1300)
        baseline, _ = self.monitor.samples.values(time.perf_counter())
        self.assertGreater(baseline, 35.)
        before = len(observed)
        time.sleep(.32)  # Deliberately block this GUI thread once, within bounds.
        self.app.processEvents()
        now = time.perf_counter()
        stalled, latency = self.monitor.samples.values(now)
        self.assertLess(stalled, baseline * .85)
        self.assertGreater(latency, 200.)
        self.assertLessEqual(len(observed) - before, 2, 'Missed slots must not be replayed in a burst')
        self.assertAlmostEqual(stalled, sum(now - 1. < stamp <= now for stamp in observed), delta=1.)
        self.wait(1300)
        recovered, recovered_latency = self.monitor.samples.values(time.perf_counter())
        self.assertGreater(recovered, baseline * .8)
        self.assertLess(recovered_latency, 150., 'The old stall must expire from the rolling window')
        self.evidence('stall-recovery', dict(baseline_fps=baseline, stalled_fps=stalled,
                                            stall_max_lag_ms=latency, stall_seconds=.32,
                                            recovered_fps=recovered, recovered_max_lag_ms=recovered_latency))

    def test_sampling_and_readout_do_not_continuously_repaint_content(self):
        child = ContentPaintProbe(self.w)
        child.show()
        self.wait(350)
        try:
            before = child.paints
            child.repaint()
            self.assertGreater(child.paints, before, 'The probe must observe a real synchronous paint')
            self.w.set_performance_monitor(False)
            self.wait(100)
            child.paints = 0
            self.wait(300)
            self.assertEqual(child.paints, 0)
            self.w.set_performance_monitor(True)
            self.wait(100)
            child.paints = 0
            callbacks = self.observe_callbacks()
            self.wait(1250)
            self.assertGreater(len(callbacks), 35)
            self.assertEqual(child.paints, 0, 'Neither sampling nor readout refresh may drive content painting')
            self.evidence('content-paints', dict(callbacks=len(callbacks), observed_seconds=1.25,
                                                content_paints=child.paints, monitor_enabled=True))
        finally:
            child.hide(); child.deleteLater(); self.events()

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
        for width, font in ((1440, 12), (740, 18)):
            self.w.resize(width, 900)
            self.w.sync_display_settings(dict(self.w.settings, ui_size=font))
            self.events()
            self.assertIs(monitor.parentWidget(), self.w.rail)
            self.assertTrue(monitor.isVisible())
            self.assertFalse(monitor.visibleRegion().isEmpty())
            self.assertTrue(self.w.rail.rect().contains(monitor.geometry()))
            self.assertEqual(self.w.rail.body.indexOf(monitor),
                             self.w.rail.body.indexOf(self.w.routes['settings']) + 1)
            self.assertEqual(len(monitor.lines), 2)
            self.assertEqual(monitor.paint_font.pixelSize(), 9)
            for control in self.w.routes.values():
                self.assertFalse(monitor.geometry().intersects(control.geometry()))
                self.assertTrue(self.w.rail.rect().contains(control.geometry()))
            global_rect = QRect(monitor.mapTo(self.w, QPoint()), monitor.size())
            self.assertTrue(self.w.top.geometry().contains(global_rect))
            connection_rect = QRect(self.w.connection.mapTo(self.w, QPoint()), self.w.connection.size())
            self.assertFalse(global_rect.intersects(connection_rect))
            metrics = QFontMetrics(monitor.paint_font)
            self.assertTrue(all(metrics.horizontalAdvance(line) + 12 <= monitor.width()
                                for line in ('FPS 4096', 'Lag 9999+s')))
            if folder := os.environ.get('PCS_PERFORMANCE_EVIDENCE'):
                self.assertTrue(self.w.grab().save(str(Path(folder) / f'top-{width}-{font}.png')))

    def test_top_readout_slot_survives_navigation_switches_and_respects_disabled_hidden(self):
        monitor = self.monitor
        for _ in range(2):
            self.w.set_navigation_position('top'); self.events()
            self.assertIs(monitor.parentWidget(), self.w.rail)
            self.assertTrue(monitor.isVisible()); self.assertTrue(monitor.timer.isActive())
            self.w.set_performance_monitor(False); self.events()
            self.assertFalse(monitor.isVisible()); self.assertFalse(monitor.timer.isActive())
            self.w.set_performance_monitor(True); self.events()
            self.assertTrue(monitor.isVisible()); self.assertTrue(monitor.timer.isActive())
            self.w.hide(); self.events()
            self.assertFalse(monitor.isVisible()); self.assertFalse(monitor.timer.isActive())
            self.w.show(); self.w.activateWindow(); self.app.setActiveWindow(self.w); self.events()
            self.assertTrue(monitor.isVisible()); self.assertTrue(monitor.timer.isActive())
            self.w.set_navigation_position('side'); self.events()
            self.assertIs(monitor.parentWidget(), self.w)
            self.assertEqual(self.w.rail.body.indexOf(monitor), -1)
            self.assertTrue(monitor.isVisible())
            rail_rect = QRect(self.w.rail.mapTo(self.w, QPoint()), self.w.rail.size())
            self.assertTrue(rail_rect.contains(monitor.geometry()))

    def test_close_veto_keeps_the_live_monitor(self):
        with patch.object(self.w, '_close_workspace_event', side_effect=lambda event: event.ignore()):
            self.assertFalse(self.w.close())
        self.assertFalse(self.monitor.closed)
        self.assertTrue(self.monitor.timer.isActive())
        self.assertTrue(self.monitor.isVisible())

    def test_hidden_background_and_close_stop_observation(self):
        monitor = self.monitor
        callbacks = self.observe_callbacks()
        self.w.hide()
        self.events()
        self.assertFalse(monitor.timer.isActive())
        before = len(callbacks)
        self.wait(100)
        self.assertEqual(len(callbacks), before)
        self.w.show()
        self.w.activateWindow()
        self.app.setActiveWindow(self.w)
        self.events()
        self.assertTrue(monitor.timer.isActive())
        with patch.object(self.w, 'isActiveWindow', return_value=False):
            monitor.sync()
            self.assertFalse(monitor.running)
            before = len(callbacks)
            self.wait(100)
            self.assertEqual(len(callbacks), before)
        monitor.sync()
        self.assertTrue(monitor.running)
        self.assertEqual(len(monitor.samples.frames), 0)
        self.assertEqual(monitor.lines[:2], ('FPS', '—'))
        self.assertIn('正在取得', monitor.accessibleName())
        self.w.showMinimized()
        self.events()
        self.assertFalse(monitor.timer.isActive())
        before = len(callbacks)
        self.wait(100)
        self.assertEqual(len(callbacks), before)
        self.w.showNormal()
        self.w.activateWindow()
        self.app.setActiveWindow(self.w)
        self.events()
        self.assertTrue(monitor.timer.isActive())
        timers = monitor.findChildren(QTimer)
        self.assertTrue(all(timer.isSingleShot() for timer in timers))
        self.assertEqual([timer for timer in timers if timer.isActive()], [monitor.timer])
        self.assertTrue(self.w.close())
        self.assertTrue(monitor.closed)
        self.assertFalse(monitor.timer.isActive())
        before = len(callbacks)
        self.wait(100)
        self.assertEqual(len(callbacks), before)
