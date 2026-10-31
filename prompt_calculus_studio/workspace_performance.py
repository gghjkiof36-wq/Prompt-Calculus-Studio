"""Measure GUI update cadence without continuously repainting the workspace."""
from collections import deque
import math
import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter
from PySide6.QtWidgets import QWidget

from .theme import visual_tokens


class PerformanceSamples:
    """Real GUI callbacks in a one-second window; missed slots are not frames."""
    def __init__(self, now, refresh_rate=60.):
        self.reset(now, refresh_rate)

    @staticmethod
    def valid_rate(rate):
        return float(rate) if type(rate) in (int, float) and math.isfinite(rate) and 1 <= rate <= 1000 else 60.

    def reset(self, now, refresh_rate=60.):
        self.started = now
        self.refresh_rate = self.valid_rate(refresh_rate)
        self.period = 1. / self.refresh_rate
        self.deadline = now + self.period
        self.frames = deque(maxlen=4096)

    def pulse(self, now):
        if now < self.deadline:
            return False
        late = now - self.deadline
        self.frames.append((now, late * 1000.))
        # Keep the fractional cadence (e.g. 144 Hz), but never replay callbacks
        # missed during a stall or let a delayed timer drive a catch-up loop.
        self.deadline += (math.floor(late / self.period) + 1) * self.period
        if self.deadline <= now:
            self.deadline += self.period
        return True

    def wait_ms(self, now):
        return max(1, math.ceil((self.deadline - now) * 1000.))

    def values(self, now):
        cutoff = now - 1.
        while self.frames and self.frames[0][0] <= cutoff:
            self.frames.popleft()
        span = min(1., max(.001, now - self.started))
        return len(self.frames) / span, max((lag for _, lag in self.frames), default=0.)


class WorkspacePerformance(QWidget):
    """One paced GUI heartbeat; the displayed number is not a GPU present rate."""
    DISPLAY_INTERVAL = .25

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.anchor = workspace.routes['settings']
        self.setObjectName('WorkspacePerformance')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        description = ('FPS：最近一秒的介面更新率估計。以所在螢幕刷新率安排採樣，'
                       '依實際回應計算；靜止時仍持續量測，卡頓時下降。'
                       '不是 GPU 呈現幀率；— 表示正在取得樣本。\n'
                       'Lag：最近一秒介面回應的最大延遲估計值。')
        self.setToolTip(description)
        self.setAccessibleDescription(description)
        self.rate_text, self.delay_text = '—', '0ms'
        self.lines = ('FPS', self.rate_text, 'Lag', self.delay_text)
        self.running = False
        self.closed = False
        self.samples = PerformanceSamples(time.perf_counter())
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.tick)
        self.visibility_sync = QTimer(self)
        self.visibility_sync.setSingleShot(True)
        self.visibility_sync.timeout.connect(self.sync)
        workspace.installEventFilter(self)
        workspace.rail.installEventFilter(self)
        self.anchor.installEventFilter(self)
        workspace.rail.performance_monitor = self
        self.configure()

    def configure(self):
        appearance = self.workspace.display_settings()
        self.colors = visual_tokens(appearance)
        self.paint_font = QFont(appearance['font_family'])
        # This readout lives inside the fixed icon rail, independently of the
        # document text size. The physical display still applies normal DPI.
        self.paint_font.setPixelSize(9)
        self.place()
        self.sync()
        self.update()

    def place(self):
        side = self.workspace.navigation_position == 'side'
        metrics = QFontMetrics(self.paint_font)
        self.lines = (('FPS', self.rate_text, 'Lag', self.delay_text) if side else
                      (f'FPS {self.rate_text}', f'Lag {self.delay_text}'))
        activity = '正在取得介面更新樣本' if self.rate_text == '—' else f'介面 FPS 估計 {self.rate_text}'
        self.setAccessibleName(f'效能監測：{activity}，延遲估計 {self.delay_text}')
        width = (max(1, min(44, self.workspace.rail.width() - 8)) if side else
                 max(metrics.horizontalAdvance(text) for text in ('FPS 4096', 'Lag 9999+s')) + 12)
        self.setFixedSize(width, metrics.height() * len(self.lines) + 8)
        if side:
            # Side navigation retains the compact readout above Settings.
            # Top navigation owns a real layout slot rather than covering a
            # document/native browser surface near the bottom of the window.
            if self.parentWidget() is not self.workspace:
                self.workspace.rail.body.removeWidget(self)
                self.setParent(self.workspace)
                self.setVisible(self.running)
            point = self.anchor.mapTo(self.workspace, QPoint(0, 0))
            rail = self.workspace.rail
            rail_left = rail.mapTo(self.workspace, QPoint(0, 0)).x()
            x, bottom = rail_left + (rail.width() - self.width()) // 2, point.y() - 6
        else:
            rail = self.workspace.rail
            if self.parentWidget() is not rail:
                self.setParent(rail)
                self.setVisible(self.running)
            index = rail.body.indexOf(self.anchor) + 1
            if rail.body.indexOf(self) != index:
                rail.body.removeWidget(self)
                rail.body.insertWidget(index, self, 0, Qt.AlignmentFlag.AlignVCenter)
            return
        self.move(min(x, max(0, self.workspace.width() - self.width())),
                  max(0, bottom - self.height()))
        self.raise_()

    def sync(self):
        active = (not self.closed and self.workspace.performance_monitor_enabled
                  and not self.workspace._startup_loading
                  and self.workspace.isVisible() and not self.workspace.isMinimized()
                  and self.workspace.isActiveWindow())
        if active != self.running:
            self.running = active
            if active:
                now = time.perf_counter()
                self.samples.reset(now, self.screen_rate())
                self.last_display = now
                self.rate_text, self.delay_text = '—', '0ms'
                self.timer.start(self.samples.wait_ms(now))
            else:
                self.timer.stop()
        self.setVisible(active)
        if active:
            self.place()

    def shutdown(self):
        self.closed = True
        self.visibility_sync.stop()
        self.sync()

    def screen_rate(self):
        screen = QWidget.screen(self.workspace)
        return PerformanceSamples.valid_rate(screen.refreshRate() if screen else None)

    def tick(self):
        if not self.running:
            return
        now = time.perf_counter()
        self.samples.pulse(now)
        if now - self.last_display >= self.DISPLAY_INTERVAL:
            self.last_display = now
            refresh_rate = self.screen_rate()
            if abs(refresh_rate - self.samples.refresh_rate) > .01:
                # Moving to another display or changing its refresh rate starts
                # a fresh window; old-display samples cannot skew the new rate.
                self.samples.reset(now, refresh_rate)
                values = '—', '0ms'
            else:
                rate, latency = self.samples.values(now)
                delay = f'{latency:.0f}ms' if latency < 1000 else f'{latency / 1000:.1f}s'
                if latency >= 9999900:
                    delay = '9999+s'
                values = f'{rate:.0f}' if self.samples.frames else '—', delay
            if values != (self.rate_text, self.delay_text):
                self.rate_text, self.delay_text = values
                self.place()
                self.update()
        self.timer.start(self.samples.wait_ms(time.perf_counter()))

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.workspace and kind in (
                QEvent.Type.Show, QEvent.Type.Hide, QEvent.Type.WindowStateChange,
                QEvent.Type.ActivationChange):
            self.sync()
            # Show/state events precede the final visible/active state on some
            # platforms. Coalesce one follow-up; no timer runs while hidden.
            if kind in (QEvent.Type.Show, QEvent.Type.WindowStateChange) and not self.closed:
                self.visibility_sync.start(0)
            elif kind == QEvent.Type.Hide:
                self.visibility_sync.stop()
        if watched in (self.workspace, self.workspace.rail, self.anchor) and kind in (
                QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(event.rect(), QColor(self.colors['chrome']))
        painter.setPen(QColor(self.colors['secondary']))
        painter.setFont(self.paint_font)
        metrics = QFontMetrics(self.paint_font)
        side = self.workspace.navigation_position == 'side'
        for index, line in enumerate(self.lines):
            area = QRect(2 if side else 6, 4 + index * metrics.height(),
                         self.width() - (4 if side else 12), metrics.height())
            painter.drawText(area, Qt.AlignmentFlag.AlignVCenter |
                             (Qt.AlignmentFlag.AlignHCenter if side else Qt.AlignmentFlag.AlignLeft), line)
