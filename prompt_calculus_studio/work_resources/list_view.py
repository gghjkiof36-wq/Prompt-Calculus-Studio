"""Native file-list selection with a quiet checkbox gutter."""
from PySide6.QtCore import Qt, QRect, QEvent, Signal
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QStyle, QStyledItemDelegate, QStyleOptionViewItem, QTreeWidget

from ..theme import visual_tokens, window_appearance
from ..ui_icons import icon


class ResourceDelegate(QStyledItemDelegate):
    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        tree = self.parent()
        hovered = tree.hover_row == index.row()
        if hovered:
            opt.state |= QStyle.StateFlag.State_MouseOver
        opt.features &= ~QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        style = opt.widget.style() if opt.widget else QApplication.style()
        style.drawControl(QStyle.ControlElement.CE_ItemViewItem, opt, painter, opt.widget)
        checkable = index.column() == 0 and bool(index.flags() & Qt.ItemFlag.ItemIsUserCheckable)
        checked = index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value)
        keyboard = tree.hasFocus() and tree.currentIndex().row() == index.row()
        if not checkable or not (checked or hovered or keyboard):
            return
        rect = tree.check_rect(index)
        colors = tree.colors
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(colors['accent_outline'] if checked else colors['control']), 1))
        painter.setBrush(QColor(colors['accent'] if checked else colors['field']))
        painter.drawRoundedRect(rect, 4, 4)
        if checked:
            tree.check_icon.paint(painter, rect.adjusted(1, 1, -1, -1))
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if index.column() == 0 and index.flags() & Qt.ItemFlag.ItemIsUserCheckable:
            if event.type() == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                if self.parent().check_rect(index).adjusted(-5, -7, 5, 7).contains(event.position().toPoint()):
                    value = Qt.CheckState.Unchecked if index.data(Qt.ItemDataRole.CheckStateRole) in (Qt.CheckState.Checked, Qt.CheckState.Checked.value) else Qt.CheckState.Checked
                    return model.setData(index, value.value, Qt.ItemDataRole.CheckStateRole)
            return False
        return super().editorEvent(event, model, option, index)


class ResourceList(QTreeWidget):
    toggleSelection = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.hover_row = -1
        self.setMouseTracking(True)
        self.setItemDelegate(ResourceDelegate(self))
        self.refresh_colors()

    def refresh_colors(self):
        self.colors = visual_tokens(window_appearance(self))
        self.check_icon = icon('check', color=self.colors['on_accent'])
        header = self.header()
        header.setAutoFillBackground(False)
        header.viewport().setAutoFillBackground(False)
        header.setStyleSheet('QHeaderView { background:transparent; } QHeaderView::section {'
                            'background:transparent;border:0;padding:8px;color:' + self.colors['secondary'] + ';}')

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.PaletteChange, QEvent.Type.StyleChange):
            self.refresh_colors()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Space and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            self.toggleSelection.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def check_rect(self, index):
        rect = self.visualRect(self.model().index(index.row(), 0))
        return QRect(rect.center().x() - 9, rect.center().y() - 9, 18, 18)

    def mouseMoveEvent(self, event):
        self.set_hover(self.indexAt(event.position().toPoint()).row())
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.set_hover(-1)
        super().leaveEvent(event)

    def set_hover(self, row):
        if row == self.hover_row:
            return
        previous, self.hover_row = self.hover_row, row
        for index in (previous, row):
            if index >= 0:
                rect = self.visualRect(self.model().index(index, 0))
                rect.setWidth(self.viewport().width())
                self.viewport().update(rect)
