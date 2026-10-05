"""Virtualized package covers and compact rows for the Manager browser."""
from PySide6.QtCore import Qt, QSize, QRect, Signal, QEvent, QTimer
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPalette
from PySide6.QtWidgets import (QListWidget, QListView, QStyledItemDelegate, QStyle,
                             QStyleOptionButton, QAbstractItemView, QApplication)

from .ui_icons import icon


DATA_ROLE = Qt.ItemDataRole.UserRole


def package_status(pack, pinned=False):
    """Runtime enablement is independent from available updates and pins."""
    state = '已啟用' if pack.get('enabled') else '已停用'
    markers = []
    if pack.get('update_available') is True:
        markers.append('有更新')
    if pinned:
        markers.append('已固定')
    return state, markers


class PackageDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return self.parent().gridSize()

    def paint(self, painter, option, index):
        data = index.data(DATA_ROLE) or {}
        pack = data.get('pack', {})
        gallery = self.parent()
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        focused = bool(option.state & QStyle.StateFlag.State_HasFocus)
        active = selected or hovered or focused
        palette = QApplication.palette()
        text = palette.color(QPalette.ColorRole.Text)
        muted = palette.color(QPalette.ColorRole.PlaceholderText)
        surface = palette.color(QPalette.ColorRole.Button)
        highlight = palette.color(QPalette.ColorRole.Highlight)
        rect = option.rect.adjusted(4, 4, -8, -8)
        line_height = gallery.line_height()
        painter.save()
        painter.setClipRect(rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(highlight if active else surface if gallery.grid_mode else Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, 12, 12)
        if selected:
            line = palette.color(QPalette.ColorRole.HighlightedText)
            line.setAlpha(110)
            painter.setPen(QPen(line, 1))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 11, 11)
        if gallery.grid_mode:
            cover = QRect(rect.left() + 1, rect.top() + 1, rect.width() - 2, gallery.cover_height)
            cover_color = QColor(highlight)
            cover_color.setAlpha(150 if active else 110)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(cover_color)
            painter.drawRoundedRect(cover, 11, 11)
            icon_size = 24
            icon('package', muted).paint(painter, cover.center().x() - icon_size // 2,
                                  cover.center().y() - icon_size // 2, icon_size, icon_size)
            name_rect = QRect(rect.left() + 14, cover.bottom() + 12, rect.width() - 28, line_height)
            metadata_rect = name_rect.translated(0, line_height + 3)
            status_rect = name_rect.translated(0, line_height * 2 + 5)
        else:
            status_width = min(gallery.status_width(), max(0, (rect.width() - 78) // 2))
            status_rect = QRect(rect.right() - 28 - status_width, rect.top() + 10, status_width, line_height)
            name_rect = QRect(rect.left() + 50, rect.top() + 10, max(0, status_rect.left() - rect.left() - 68), line_height)
            metadata_rect = name_rect.translated(0, line_height + 3)
            if not active and not gallery.batch_mode:
                icon('package').paint(painter, rect.left() + 15, rect.top() + 22, 20, 20)
        font = QFont(gallery.font())
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(text)
        painter.drawText(name_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(pack.get('name', ''), Qt.TextElideMode.ElideRight, name_rect.width()))
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(muted)
        source = pack.get('author') or ('Git' if pack.get('aux_id') else 'Comfy Registry' if pack.get('cnr_id') else '本機套件')
        metadata = ' · '.join(value for value in (pack.get('version', ''), source) if value)
        painter.drawText(metadata_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(metadata, Qt.TextElideMode.ElideRight, metadata_rect.width()))
        status, markers = package_status(pack, data.get('pinned'))
        painter.setPen(text if pack.get('enabled') else muted)
        painter.drawText(status_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                         painter.fontMetrics().elidedText(status, Qt.TextElideMode.ElideRight, status_rect.width()))
        marker_x = status_rect.left() + painter.fontMetrics().horizontalAdvance(status) + 8 if gallery.grid_mode else status_rect.left()
        marker_height = max(22, painter.fontMetrics().height() + 2)
        marker_y = status_rect.top() + (max(0, (line_height - marker_height) // 2) if gallery.grid_mode else line_height + 3)
        for marker in markers:
            available = status_rect.right() + 1 - marker_x
            if available <= 12:
                break
            width = min(painter.fontMetrics().horizontalAdvance(marker) + 12, available)
            marker_rect = QRect(marker_x, marker_y, width, marker_height)
            update_marker = marker == '有更新'
            marker_color = text if update_marker else muted
            border = QColor(marker_color)
            border.setAlpha(100 if update_marker else 80)
            painter.setPen(QPen(border, 1))
            fill = QColor(marker_color)
            fill.setAlpha(14)
            painter.setBrush(fill if update_marker else Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(marker_rect, 6, 6)
            painter.setPen(marker_color)
            painter.drawText(marker_rect, Qt.AlignmentFlag.AlignCenter,
                             painter.fontMetrics().elidedText(marker, Qt.TextElideMode.ElideRight, max(0, width - 12)))
            marker_x += width + 4
        checked = index.data(Qt.ItemDataRole.CheckStateRole) == Qt.CheckState.Checked.value
        if active or gallery.batch_mode or checked:
            if data.get('selectable'):
                checkbox = QStyleOptionButton()
                checkbox.rect = gallery.check_rect(option.rect)
                checkbox.state = QStyle.StateFlag.State_Enabled | (QStyle.StateFlag.State_On if checked else QStyle.StateFlag.State_Off)
                checkbox.palette = palette
                gallery.style().drawControl(QStyle.ControlElement.CE_CheckBox, checkbox, painter, gallery)
            if active:
                x = rect.right() - 28
                y = rect.top() + 16 if gallery.grid_mode else rect.top() + 25
                icon('forward').paint(painter, x, y, 16, 16)
        painter.restore()

    def editorEvent(self, event, model, option, index):
        # The view handles the check target separately from opening a card.
        return False


class PackageGallery(QListWidget):
    openRequested = Signal(str)
    checkRequested = Signal(str, bool)

    def __init__(self):
        super().__init__()
        self.grid_mode = True
        self.compact = False
        self.cover_height = 56
        self.batch_mode = False
        self.check_press = None
        self.relayout = QTimer(self)
        self.relayout.setSingleShot(True)
        self.relayout.timeout.connect(self.settle_layout)
        self.setObjectName('ManagerGallery')
        self.setStyleSheet('QListWidget#ManagerGallery::item {padding:0; margin:0; border:0;}')
        self.setFrameShape(QListWidget.Shape.NoFrame)
        self.setMouseTracking(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setMovement(QListView.Movement.Static)
        self.setItemDelegate(PackageDelegate(self))
        self.itemClicked.connect(self.open_item)
        self.itemActivated.connect(self.open_item)
        self.itemChanged.connect(self.check_item)
        self.viewport().installEventFilter(self)
        self.set_mode(True)

    def eventFilter(self, watched, event):
        if watched is self.viewport() and event.type() == QEvent.Type.Resize:
            self.relayout.start(0)
        return super().eventFilter(watched, event)

    def settle_layout(self):
        self.reflow()
        self.doItemsLayout()

    def set_mode(self, grid):
        self.grid_mode = bool(grid)
        self.setViewMode(QListView.ViewMode.IconMode if grid else QListView.ViewMode.ListMode)
        self.setFlow(QListView.Flow.LeftToRight if grid else QListView.Flow.TopToBottom)
        self.setWrapping(bool(grid))
        self.reflow()

    def set_compact(self, compact):
        if self.compact != compact:
            self.compact = compact
            self.reflow()
            self.viewport().update()

    def line_height(self):
        return max(24, self.fontMetrics().height())

    def status_width(self):
        return max(116, sum(self.fontMetrics().horizontalAdvance(text) + 12 for text in ('有更新', '已固定')) + 4)

    def check_rect(self, item_rect):
        return QRect(item_rect.left() + 16, item_rect.top() + (16 if self.grid_mode else 25), 20, 20)

    def open_item(self, item):
        if item:
            self.openRequested.emit(item.data(DATA_ROLE)['pack']['id'])

    def check_item(self, item):
        self.checkRequested.emit(item.data(DATA_ROLE)['pack']['id'], item.checkState() == Qt.CheckState.Checked)

    def mousePressEvent(self, event):
        item = self.itemAt(event.position().toPoint())
        if (event.button() == Qt.MouseButton.LeftButton and item and
                self.check_rect(self.visualItemRect(item)).contains(event.position().toPoint())):
            self.check_press = item.data(DATA_ROLE)['pack']['id']
            self.setCurrentItem(item)
            self.setFocus(Qt.FocusReason.MouseFocusReason)
            event.accept()
            return
        self.check_press = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        ident, self.check_press = self.check_press, None
        if ident is not None:
            item = self.itemAt(event.position().toPoint())
            if (item and item.data(DATA_ROLE)['pack']['id'] == ident and
                    self.check_rect(self.visualItemRect(item)).contains(event.position().toPoint()) and
                    item.data(DATA_ROLE).get('selectable')):
                item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        item = self.currentItem()
        if event.key() == Qt.Key.Key_Space and item:
            if item.data(DATA_ROLE).get('selectable'):
                item.setCheckState(Qt.CheckState.Unchecked if item.checkState() == Qt.CheckState.Checked else Qt.CheckState.Checked)
            event.accept()
        elif event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Menu) and item:
            self.open_item(item)
            event.accept()
        else:
            super().keyPressEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reflow()

    def reflow(self):
        # Keep the native style/scrollbar gutter available during icon layout;
        # otherwise Qt can wrap the final column before showing a scrollbar.
        width = max(1, self.viewport().width() - max(24, self.verticalScrollBar().sizeHint().width() + 2))
        minimum_card = max(220, self.fontMetrics().horizontalAdvance('已停用') + 8 + self.status_width() + 40)
        columns = max(1, width // minimum_card) if self.grid_mode else 1
        cell_width = width // columns
        size = QSize(cell_width, self.cover_height + self.line_height() * 3 + 46 if self.grid_mode else self.line_height() * 2 + 30)
        if size != self.gridSize():
            self.setGridSize(size)
            self.relayout.start(0)
