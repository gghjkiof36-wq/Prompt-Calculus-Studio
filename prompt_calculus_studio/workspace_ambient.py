"""Sparse, opt-in decoration. Never receives input or changes document state."""
from dataclasses import dataclass
import math
import random
import sys
import time

from PySide6.QtCore import QEvent, QPointF, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QApplication, QWidget


EFFECTS = (('關閉', 'off'), ('櫻花', 'sakura'), ('下雨', 'rain'), ('雪花', 'snow'))


def system_motion_enabled():
    if sys.platform == 'win32':
        import ctypes
        value = ctypes.c_int(1)
        if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(value), 0):
            return bool(value.value)
    return True


@dataclass
class Particle:
    x: float
    y: float
    size: float
    speed: float
    phase: float
    angle: float


class WorkspaceAmbient(QWidget):
    def __init__(self, workspace):
        super().__init__(workspace.workspace_stack)
        self.workspace = workspace
        self.effect = 'off'
        self.reduced = False
        self.system_motion = system_motion_enabled()
        self.particles = []
        self.random = random.Random()
        self.last_frame = time.monotonic()
        self.color = QColor('#e8b7c9')
        self.setObjectName('WorkspaceAmbient')
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setStyleSheet('#WorkspaceAmbient { background: transparent; border: none; }')
        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)
        workspace.installEventFilter(self)
        self.parentWidget().installEventFilter(self)
        self.parentWidget().currentChanged.connect(self.sync)
        QApplication.instance().applicationStateChanged.connect(self.sync)
        self.hide()

    def configure(self):
        from .theme import visual_tokens
        appearance = self.workspace.display_settings()
        effect = self.workspace.ambient_effect
        if effect != self.effect:
            self.effect = effect
            self.particles.clear()
        self.reduced = bool(appearance['reduce_motion'])
        light = visual_tokens(appearance)['scheme'] == 'light'
        colors = {'sakura': '#b46683' if light else '#e8b7c9',
                  'rain': '#587889' if light else '#b8cdd9',
                  'snow': '#6a8293' if light else '#e7eff3'}
        self.color = QColor(colors.get(effect, '#e8b7c9'))
        self.sync()

    def sync(self, *_):
        self.setGeometry(self.parentWidget().rect())
        active = (self.effect != 'off' and not self.reduced and self.system_motion
                  and self.workspace.isVisible() and not self.workspace.isMinimized()
                  and self.workspace.isActiveWindow())
        self.setVisible(active)
        if active:
            self.raise_()
            self.workspace.notification.raise_()
            if not self.timer.isActive():
                self.last_frame = time.monotonic()
                self.timer.start()
        else:
            self.timer.stop()
        if self.effect == 'off':
            self.particles.clear()

    def eventFilter(self, watched, event):
        kind = event.type()
        if watched is self.workspace and kind == QEvent.Type.ActivationChange:
            self.system_motion = system_motion_enabled()
        if kind in (QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide,
                    QEvent.Type.WindowStateChange, QEvent.Type.ActivationChange):
            self.sync()
        return False

    def spawn(self, *, scattered=False):
        r = self.random
        size = r.uniform(3.0, 5.5) if self.effect == 'sakura' else r.uniform(1.2, 2.4)
        speed = r.uniform(18, 30) if self.effect == 'sakura' else r.uniform(14, 25)
        if self.effect == 'rain':
            size, speed = r.uniform(10, 20), r.uniform(130, 200)
        return Particle(r.uniform(0, max(1, self.width())),
                        r.uniform(0, max(1, self.height())) if scattered else -25,
                        size, speed, r.uniform(0, math.tau), r.uniform(0, 360))

    def bounds(self, particle):
        radius = math.ceil(particle.size+4)
        return QRect(int(particle.x)-radius, int(particle.y)-radius, radius*2+1, radius*2+1)

    def advance(self):
        now = time.monotonic()
        elapsed = min(.05, max(0, now-self.last_frame))
        self.last_frame = now
        count = min(36, max(6, self.width()*self.height()//42000))
        while len(self.particles) < count:
            self.particles.append(self.spawn(scattered=True))
        dirty = QRegion()
        for particle in self.particles:
            dirty |= QRegion(self.bounds(particle))
        del self.particles[count:]
        for i, particle in enumerate(self.particles):
            particle.phase += elapsed*.7
            particle.x += elapsed*(9*math.sin(particle.phase) if self.effect != 'rain' else -28)
            particle.y += elapsed*particle.speed
            particle.angle += elapsed*22
            if particle.y > self.height()+25 or particle.x < -25 or particle.x > self.width()+25:
                self.particles[i] = particle = self.spawn()
            dirty |= QRegion(self.bounds(particle))
        self.update(dirty)

    def paintEvent(self, event):
        if self.effect == 'off':
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.color)
        color.setAlphaF(.19 if self.effect == 'rain' else .32)
        painter.setPen(QPen(color, 1.0, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(color)
        for particle in self.particles:
            if not event.region().intersects(self.bounds(particle)):
                continue
            p, s = particle, particle.size
            if self.effect == 'rain':
                painter.drawLine(QPointF(p.x, p.y), QPointF(p.x+3, p.y-s))
            elif self.effect == 'snow':
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(p.x, p.y), s, s)
            else:
                painter.save()
                painter.setPen(Qt.PenStyle.NoPen)
                painter.translate(p.x, p.y)
                painter.rotate(p.angle)
                petal = QPainterPath(QPointF(0, s))
                petal.cubicTo(-s, 0, -s*.7, -s, -.9, -s*.75)
                petal.lineTo(0, -s*.35)
                petal.lineTo(.9, -s*.75)
                petal.cubicTo(s*.7, -s, s, 0, 0, s)
                painter.drawPath(petal)
                painter.restore()
        painter.end()
