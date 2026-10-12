"""Canvas picker presentation and a short, location-scoped close guard."""
from PySide6.QtCore import Qt, QSize, QRectF, QObject, QEvent, QTimer
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPen, QTextLayout, QTextOption
from PySide6.QtWidgets import QApplication, QStyledItemDelegate, QStyle
from .views import widget_colors
from .ui_icons import icon
from .color_roles import mix


class PickerCardDelegate(QStyledItemDelegate):
    @staticmethod
    def lines(text, font, width):
        text = ' '.join(text.split())
        if not text: return []
        layout = QTextLayout(text, font)
        option = QTextOption(); option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        layout.setTextOption(option); layout.beginLayout()
        first = layout.createLine(); first.setLineWidth(max(20, width))
        end = first.textStart() + first.textLength(); layout.endLayout()
        encoded = text.encode('utf-16-le')
        result = [encoded[:end * 2].decode('utf-16-le').rstrip()]
        remaining = encoded[end * 2:].decode('utf-16-le').lstrip()
        if remaining:
            result.append(QFontMetrics(font).elidedText(remaining, Qt.TextElideMode.ElideRight, max(20, width)))
        return result

    @staticmethod
    def text_width(width):
        return max(20, width - 58)

    def sizeHint(self, option, index):
        line = QFontMetrics(option.font).height()
        text = str(index.data() or '').partition('\n')[2]
        count = len(self.lines(text, option.font, self.text_width(self.parent().viewport().width())))
        return QSize(120, 36 + (count + 1) * line)

    def paint(self, painter, option, index):
        colors = self.parent().motion_colors() if hasattr(self.parent(),'motion_colors') else widget_colors(self.parent())
        depth = getattr(self.parent(),'stack_depth',lambda _:0)(index)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hover = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(1, 3, -2, -3)
        painter.save(); painter.setRenderHint(painter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors['accent' if selected else 'line']), 1))
        fill=colors['selected' if selected else 'hover' if hover else 'surface']
        painter.setBrush(QColor(mix(fill,colors['base'],min(.55,depth*.3))))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setOpacity(max(0,1-depth/.4))
        left = rect.left() + 14
        width = self.text_width(int(rect.width()))
        name, _, prompt = str(index.data() or '').partition('\n')
        font = QFont(option.font); font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font); metrics = QFontMetrics(font); height = metrics.height()
        painter.setPen(QColor(colors['text']))
        painter.drawText(QRectF(left, rect.top() + 10, width, height + 2), Qt.AlignmentFlag.AlignVCenter,
                         metrics.elidedText(name, Qt.TextElideMode.ElideRight, width))
        font.setWeight(QFont.Weight.Normal); painter.setFont(font)
        painter.setPen(QColor(colors['secondary']))
        for i, part in enumerate(self.lines(prompt, font, width)):
            painter.drawText(QRectF(left, rect.top() + 16 + height * (i + 1), width, height + 2),
                             Qt.AlignmentFlag.AlignVCenter, part)
        icon('plus', color=colors['secondary'], size=18).paint(
            painter, QRectF(rect.right() - 31, rect.center().y() - 9, 18, 18).toRect())
        painter.restore()


class ClosingClickGuard(QObject):
    """Consume only a closing click's continuation at its original location.

    The modal can disappear between the two clicks of a native double click.
    Keep its second press/release from reaching the underlying Canvas viewport.
    Keyboard events and clicks elsewhere are never intercepted.
    """
    def __init__(self, viewport, global_position):
        super().__init__(viewport)
        self.viewport = viewport; self.position = global_position
        viewport.installEventFilter(self)
        QTimer.singleShot(QApplication.doubleClickInterval(), self.deleteLater)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                            QEvent.Type.MouseButtonDblClick):
            if (event.globalPosition().toPoint() - self.position).manhattanLength() <= QApplication.startDragDistance():
                event.accept(); return True
        return False
