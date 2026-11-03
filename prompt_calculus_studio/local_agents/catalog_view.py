"""Compact two-line catalog rows with persistent current-item feedback."""
from PySide6.QtCore import Qt, QEvent, QRectF, QSize
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyledItemDelegate, QStyle
from ..theme import visual_tokens, window_appearance
from ..ui_icons import icon


class CatalogDelegate(QStyledItemDelegate):
    def __init__(self, parent):
        super().__init__(parent)
        self._check_color, self._check_icon = None, None

    @staticmethod
    def checkable(index):
        return bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable) and index.data(Qt.ItemDataRole.CheckStateRole) is not None

    def sizeHint(self, option, index):
        return QSize(220, max(68, option.fontMetrics.height() * 2 + 26))

    def paint(self, painter, option, index):
        tokens = visual_tokens(window_appearance(self.parent()))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect = QRectF(option.rect).adjusted(0, 2, -2, -2)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(option.rect)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(tokens['selected' if selected else 'hover']))
            painter.drawRoundedRect(rect, 8, 8)
        text_rect = rect.adjusted(12, 12, -12, -12)
        if self.checkable(index):
            checked = index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            check = QRectF(text_rect.x(), rect.center().y()-9, 18, 18)
            painter.setPen(QPen(QColor(tokens['accent_outline'] if checked else tokens['control']), 1))
            painter.setBrush(QColor(tokens['accent'] if checked else tokens['field']))
            painter.drawRoundedRect(check, 4, 4)
            if checked:
                if self._check_color != tokens['on_accent']:
                    self._check_color = tokens['on_accent']
                    self._check_icon = icon('check', color=self._check_color)
                self._check_icon.paint(painter, check.toRect().adjusted(1, 1, -1, -1))
            text_rect.adjust(32, 0, 0, 0)
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
        if option.state & QStyle.StateFlag.State_HasFocus and self.parent().hasFocus():
            painter.setPen(QPen(QColor(tokens['accent_outline']), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if not self.checkable(index) or not index.flags() & Qt.ItemFlag.ItemIsEnabled:
            return super().editorEvent(event, model, option, index)
        if event.type() == QEvent.Type.MouseButtonRelease:
            toggle = event.button() == Qt.MouseButton.LeftButton and option.rect.contains(event.position().toPoint())
        elif event.type() == QEvent.Type.KeyPress:
            toggle = event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.NoModifier
        else:
            return False
        if toggle:
            checked = index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
            value = Qt.CheckState.Unchecked if checked else Qt.CheckState.Checked
            return model.setData(index, value.value, Qt.ItemDataRole.CheckStateRole)
        return False
