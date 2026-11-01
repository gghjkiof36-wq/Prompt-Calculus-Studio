"""Text-only cards and compact rows for execution-group navigation."""
from PySide6.QtCore import Qt, QSize, QRect, QEvent
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QListWidget, QStyledItemDelegate, QStyle
from .module_chrome import dialog_colors, dialog_context


DETAILS = int(Qt.ItemDataRole.UserRole) + 1


class ScheduleGroupDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return self.parent().gridSize()

    def paint(self, painter, option, index):
        details = index.data(DETAILS)
        if not details:
            return
        name, count, status, stamp, group = details
        colors = dialog_colors(dialog_context(self.parent())[0])
        grid = self.parent().grid_mode
        rect = option.rect.adjusted(0, 0, -12, -12 if grid else -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        if grid or selected or hovered:
            painter.setBrush(QColor(colors['selected' if selected else 'hover' if hovered else 'surface']))
            painter.drawRoundedRect(rect, 8, 8)
        if not grid:
            painter.setPen(QPen(QColor(colors['line']), 1))
            painter.drawLine(rect.left()+14, rect.bottom(), rect.right()-14, rect.bottom())
        if focused:
            painter.setPen(QPen(QColor(colors['accent']), 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 8, 8)
        painter.setFont(option.font)
        line = max(22, option.fontMetrics.height())
        text_rect = QRect(rect.left()+14, rect.top()+14, rect.width()-28, line)
        count_width = max(84, option.fontMetrics.horizontalAdvance(f'{count:,} 項')+12)
        status_width = max(110, option.fontMetrics.horizontalAdvance('提交未確認')+12)
        name_rect = text_rect if grid else text_rect.adjusted(0, 0, -count_width-status_width-16, 0)
        metrics_rect = text_rect.translated(0, line+6) if grid else text_rect
        count_rect = QRect(metrics_rect.left() if grid else metrics_rect.right()-status_width-count_width,
                           metrics_rect.top(), count_width, line)
        status_rect = QRect(metrics_rect.right()-status_width, metrics_rect.top(), status_width, line)
        stamp_rect = text_rect.translated(0, (line+6)*2 if grid else line+4)
        for area, text, role, alignment in (
            (name_rect, name, 'text', Qt.AlignmentFlag.AlignLeft),
            (count_rect, f'{count:,} 項', 'secondary', Qt.AlignmentFlag.AlignLeft),
            (status_rect, status, 'secondary', Qt.AlignmentFlag.AlignRight),
            (stamp_rect, f'{group} · {stamp}', 'secondary', Qt.AlignmentFlag.AlignLeft)):
            painter.setPen(QColor(colors[role]))
            painter.drawText(area, alignment|Qt.AlignmentFlag.AlignVCenter,
                             option.fontMetrics.elidedText(text, Qt.TextElideMode.ElideRight, max(1,area.width())))
        painter.restore()


class ScheduleGroupView(QListWidget):
    def __init__(self):
        super().__init__()
        self.grid_mode = True
        self.setObjectName('ScheduleGroupsList')
        self.setViewMode(self.ViewMode.IconMode)
        self.setResizeMode(self.ResizeMode.Adjust)
        self.setMovement(self.Movement.Static)
        self.setSelectionMode(self.SelectionMode.SingleSelection)
        self.setUniformItemSizes(True)
        self.setSpacing(0)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.setMouseTracking(True)
        self.setItemDelegate(ScheduleGroupDelegate(self))
        self.setAccessibleName('預排程分組，點選查看組內序列')
        self.reflow()

    def set_mode(self, mode):
        self.grid_mode = mode == 'cards'
        self.setViewMode(self.ViewMode.IconMode if self.grid_mode else self.ViewMode.ListMode)
        self.reflow()
        self.doItemsLayout()
        if self.currentItem():
            self.scrollToItem(self.currentItem())
        self.viewport().update()

    def reflow(self):
        width = max(1, self.viewport().width()-max(24, self.verticalScrollBar().sizeHint().width()+2))
        minimum = max(240, self.fontMetrics().horizontalAdvance('1,000 項 · 提交未確認')+40)
        columns = max(1, width//minimum) if self.grid_mode else 1
        line = max(22, self.fontMetrics().height())
        size = QSize(width//columns, line*3+52 if self.grid_mode else line*2+32)
        if size != self.gridSize():
            self.setGridSize(size)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.FontChange:
            self.reflow()
