"""Passive Qt UI redraw FPS and GUI wake-up latency, not display refresh Hz."""
from collections import deque
import time

from PySide6.QtCore import QEvent, QPoint, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QRegion
from PySide6.QtWidgets import QWidget

from .application_events import listen, unlisten
from .theme import visual_tokens


class PerformanceSamples:
    """A bounded, one-second measurement window using monotonic timestamps."""
    def __init__(self, now):
        self.reset(now)

    def reset(self, now):
        self.started = now
        self.paints = deque(maxlen=4096)
        self.latencies = deque(maxlen=128)

    def paint(self, now):
        self.paints.append(now)

    def wake(self, now, expected):
        self.latencies.append((now, max(0., now - expected) * 1000.))

    def values(self, now):
        cutoff = now - 1.
        while self.paints and self.paints[0] <= cutoff:
            self.paints.popleft()
        while self.latencies and self.latencies[0][0] <= cutoff:
            self.latencies.popleft()
        span = min(1., max(.001, now - self.started))
        return len(self.paints) / span, max((v for _, v in self.latencies), default=0.)


class WorkspacePerformance(QWidget):
    """One workspace-owned observer; its own paints never feed the counter."""
    INTERVAL_MS = 50

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.anchor = workspace.routes['settings']
        self.setObjectName('WorkspacePerformance')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        description = ('FPS：最近一秒的介面重繪幀率，靜止時可為 0；不是螢幕更新率（Hz）。\n'
                       'Lag：最近一秒介面事件處理的最大延遲。')
        self.setToolTip(description)
        self.setAccessibleDescription(description)
        self.rate_text, self.delay_text = '0', '0ms'
        self.lines = ('FPS', self.rate_text, 'Lag', self.delay_text)
        self.running = False
        self.closed = False
        self.batch_painted = False
        self.samples = PerformanceSamples(time.perf_counter())
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(self.INTERVAL_MS)
        self.timer.timeout.connect(self.tick)
        self.visibility_sync = QTimer(self)
        self.visibility_sync.setSingleShot(True)
        self.visibility_sync.timeout.connect(self.sync)
        workspace.installEventFilter(self)
        workspace.rail.installEventFilter(self)
        self.anchor.installEventFilter(self)
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
        self.setAccessibleName(f'效能監測：介面 FPS {self.rate_text}，延遲 {self.delay_text}')
        width = (max(1, min(44, self.workspace.rail.width() - 8)) if side else
                 max(metrics.horizontalAdvance(text) for text in ('FPS 4096', 'Lag 9999+s')) + 12)
        self.setFixedSize(width, metrics.height() * len(self.lines) + 8)
        if side:
            point = self.anchor.mapTo(self.workspace, QPoint(0, 0))
            rail = self.workspace.rail
            rail_left = rail.mapTo(self.workspace, QPoint(0, 0)).x()
            x, bottom = rail_left + (rail.width() - self.width()) // 2, point.y() - 6
        else:
            # Keep the document's word count / save-status footer readable.
            x, bottom = 8, self.workspace.height() - max(48, 2 * QFontMetrics(self.paint_font).height())
        self.move(min(x, max(0, self.workspace.width() - self.width())),
                  max(0, bottom - self.height()))
        self.raise_()

    def sync(self):
        active = (not self.closed and self.workspace.performance_monitor_enabled
                  and self.workspace.isVisible() and not self.workspace.isMinimized()
                  and self.workspace.isActiveWindow())
        if active != self.running:
            self.running = active
            self.batch_painted = False
            if active:
                now = time.perf_counter()
                self.samples.reset(now)
                self.expected = now + self.INTERVAL_MS / 1000.
                self.last_display = now
                self.rate_text, self.delay_text = '0', '0ms'
                listen(self, QEvent.Type.Paint, QEvent.Type.UpdateRequest)
                self.timer.start()
            else:
                self.timer.stop()
                unlisten(self)
        self.setVisible(active)
        if active:
            self.place()

    def shutdown(self):
        self.closed = True
        self.visibility_sync.stop()
        self.sync()

    def tick(self):
        if not self.running:
            return
        now = time.perf_counter()
        self.samples.wake(now, self.expected)
        # Qt merges overdue timer events. Start a fresh deadline after a stall;
        # do not count missed timer ticks as either frames or repeated stalls.
        self.expected = now + self.INTERVAL_MS / 1000.
        if now - self.last_display < 1.:
            return
        self.last_display = now
        rate, latency = self.samples.values(now)
        delay = f'{latency:.0f}ms' if latency < 1000 else f'{latency / 1000:.1f}s'
        if latency >= 9999900:
            delay = '9999+s'
        values = f'{rate:.0f}', delay
        if values != (self.rate_text, self.delay_text):
            self.rate_text, self.delay_text = values
            self.place()
            self.update()

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
        if not self.running:
            return False
        if watched is self.workspace and kind == QEvent.Type.UpdateRequest:
            self.batch_painted = False
        elif (kind == QEvent.Type.Paint and not self.batch_painted
              and isinstance(watched, QWidget) and watched is not self
              and watched.window() is self.workspace):
            # A root paint restricted to this overlay is still self-generated.
            if watched is self.workspace and event.region().subtracted(QRegion(self.geometry())).isEmpty():
                return False
            self.batch_painted = True
            self.samples.paint(time.perf_counter())
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
