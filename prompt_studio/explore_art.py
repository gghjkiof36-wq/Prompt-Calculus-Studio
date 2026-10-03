"""Small, theme-aware illustrations for local navigation and empty states."""
from PySide6.QtCore import Qt, QRectF, QPointF
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QLinearGradient
from PySide6.QtWidgets import QWidget, QSizePolicy

from .theme import visual_tokens


class ExploreArtwork(QWidget):
    """Decorative vector artwork; no downloaded images or interactive controls."""
    def __init__(self, window, kind='workflow', height=112):
        super().__init__()
        self.owner = window
        self.kind = kind
        self.setFixedHeight(height)
        self.setMinimumWidth(0)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

    def paintEvent(self, event):
        settings = dict(self.owner.state['settings'])
        settings.update(getattr(self.owner, 'appearance_preview', {}))
        t = visual_tokens(settings)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        bounds = QRectF(self.rect()).adjusted(.5, .5, -.5, -.5)
        clip = QPainterPath(); clip.addRoundedRect(bounds, 12, 12)
        painter.setClipPath(clip)
        painter.fillPath(clip, QColor(t['field']))
        gradient = QLinearGradient(0, 0, self.width(), self.height())
        start = QColor(t['info']); start.setAlpha(40)
        end = QColor(t['success']); end.setAlpha(18)
        gradient.setColorAt(0, start); gradient.setColorAt(1, end)
        painter.fillPath(clip, gradient)
        # An intentionally quiet motif occupies the former empty middle area.
        painter.translate((self.width()-min(self.width(), 340))/2, 0)
        painter.scale(min(self.width(), 340)/340, self.height()/112)
        line = QColor(t['info']); line.setAlpha(155)
        painter.setPen(QPen(line, 1.5))
        if self.kind == 'workflow':
            for points in ((89, 63, 145, 40), (195, 40, 251, 69)):
                x1, y1, x2, y2 = points
                path = QPainterPath(QPointF(x1, y1))
                path.cubicTo(x1+30, y1, x2-30, y2, x2, y2)
                painter.drawPath(path)
            for x, y, width, height in ((33, 43, 56, 40), (145, 20, 50, 40), (251, 49, 56, 40)):
                painter.setBrush(QColor(t['surface']))
                painter.drawRoundedRect(QRectF(x, y, width, height), 7, 7)
                painter.drawLine(QPointF(x+11, y+15), QPointF(x+width-11, y+15))
                quiet = QColor(t['success']); quiet.setAlpha(145)
                painter.setPen(QPen(quiet, 2.5))
                painter.drawLine(QPointF(x+11, y+25), QPointF(x+width-23, y+25))
                painter.setPen(QPen(line, 1.5))
        else:
            for x, y in ((69, 30), (130, 19), (191, 30)):
                painter.setBrush(QColor(t['surface']))
                painter.drawRoundedRect(QRectF(x, y, 80, 68), 7, 7)
                mountain = QPainterPath(QPointF(x+10, y+50))
                mountain.lineTo(x+31, y+26); mountain.lineTo(x+48, y+43)
                mountain.lineTo(x+59, y+34); mountain.lineTo(x+70, y+50)
                painter.drawPath(mountain)
                painter.drawEllipse(QPointF(x+57, y+17), 4, 4)
                painter.drawLine(QPointF(x+12, y+58), QPointF(x+49, y+58))
        painter.end()
