"""Compact two-line catalog rows with persistent current-item feedback."""
from PySide6.QtCore import Qt, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from ..theme import visual_tokens, window_appearance


class CatalogDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(220, max(64, option.fontMetrics.height() * 2 + 20))

    def paint(self, painter, option, index):
        tokens = visual_tokens(window_appearance(self.parent()))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(0, 2, -2, -2)
        painter.save()
        painter.setClipRect(option.rect)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens['selected' if selected else 'hover']))
            painter.drawRoundedRect(rect, 8, 8)
        text_rect = rect.adjusted(12, 10, -12, -10)
        decoration = index.data(Qt.ItemDataRole.DecorationRole)
        if decoration is not None:
            decoration.paint(painter, int(text_rect.x()), int(text_rect.center().y()-10), 20, 20)
            text_rect.adjust(32, 0, 0, 0)
        lines = (index.data(Qt.ItemDataRole.DisplayRole) or '').split('\n', 1)
        painter.setFont(option.font)
        painter.setPen(QColor(tokens['text']))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                         option.fontMetrics.elidedText(lines[0], Qt.TextElideMode.ElideRight, int(text_rect.width())))
        if len(lines) > 1:
            font = QFont(option.font)
            if font.pixelSize() > 0:
                font.setPixelSize(max(12, font.pixelSize()-1))
            else:
                font.setPointSizeF(max(10, font.pointSizeF()-1))
            painter.setFont(font); painter.setPen(QColor(tokens['secondary']))
            painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignBottom,
                             QFontMetrics(font).elidedText(lines[1], Qt.TextElideMode.ElideRight, int(text_rect.width())))
        painter.restore()
