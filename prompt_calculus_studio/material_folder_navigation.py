"""Compact folder/category rows, with shared menus and transactional drag intent."""
from PySide6.QtCore import Qt, QRect, QSize, Signal
from PySide6.QtGui import QColor, QPainter, QPen, QKeySequence
from PySide6.QtWidgets import QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem, QStyle
from . import material_navigation as nav
from .prompt_editor import ReorderableList
from .views import widget_colors
from .ui_icons import icon

ROW = Qt.ItemDataRole.UserRole + 1


class FolderDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(180, option.fontMetrics.height()+20+(16 if index.data(Qt.ItemDataRole.UserRole) is None else 0))

    def paint(self, painter, option, index):
        entry = QStyleOptionViewItem(option); self.initStyleOption(entry, index)
        data = index.data(ROW); rect = QRect(option.rect)
        if data['id'] is None: rect.adjust(0, 0, 0, -16)
        entry.rect = rect; entry.text = ''
        self.parent().style().drawControl(QStyle.ControlElement.CE_ItemViewItem, entry, painter, self.parent())
        colors = self.parent().colors(); painter.save(); painter.setFont(option.font)
        arrow, name, count, more = self.parent().regions(rect, data)
        if data['kind'] == 'folder':
            icon('chevron-right' if data['collapsed'] else 'chevron-down', colors['secondary']).paint(painter, arrow.adjusted(6, 6, -6, -6))
            icon('folder', colors['secondary']).paint(painter, QRect(name.left()-24, rect.center().y()-9, 18, 18))
        painter.setPen(QColor(colors['text']))
        painter.drawText(name, Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(data['name'], Qt.TextElideMode.ElideRight, name.width()))
        painter.setPen(QColor(colors['secondary']))
        painter.drawText(count, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, str(data['count']))
        if self.parent().manage and data['kind'] != 'shortcut':
            icon('more-horizontal', colors['secondary']).paint(painter, more.adjusted(6, 6, -6, -6))
        if data['id'] is None:
            painter.setPen(QPen(QColor(colors.get('divider', colors['line'])), 1))
            painter.drawLine(option.rect.left()+10, option.rect.bottom()-8, option.rect.right()-10, option.rect.bottom()-8)
        painter.restore()


class FolderNavigation(ReorderableList):
    toggleRequested = Signal(str)
    menuRequested = Signal(object, object)
    moveRequested = Signal(str, object, object)
    undoRequested = Signal()
    redoRequested = Signal()

    def __init__(self, parent=None, *, manage=True, colors=None):
        super().__init__(parent)
        self.manage = manage; self.colors = colors or (lambda: widget_colors(self))
        self.setItemDelegate(FolderDelegate(self)); self.setMouseTracking(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._pressed_control = None; self._folder_drop = None; self._drag_ident = None
        self.has_folders = False
        if manage: self.enable_reordering()
        else:
            self.setDragEnabled(False); self.setAcceptDrops(False)

    def regions(self, rect, data):
        button = 28; center = rect.center().y()
        arrow = QRect(rect.left()+2, center-button//2, button, button)
        more = QRect(rect.right()-button-4, center-button//2, button, button)
        right = more.left()-6 if self.manage and data['kind'] != 'shortcut' else rect.right()-10
        count_width = self.fontMetrics().horizontalAdvance(str(data['count']))+4
        count = QRect(right-count_width, rect.top(), count_width, rect.height())
        left = rect.left() + (54 if data['kind'] == 'folder' else 32 if data['parent'] else 12)
        name = QRect(left, rect.top(), max(1, count.left()-left-10), rect.height())
        return arrow, name, count, more

    def add_row(self, ident, name, count, kind='category', parent=None, collapsed=False):
        entry = QListWidgetItem(name); entry.setData(Qt.ItemDataRole.UserRole, ident)
        entry.setData(ROW, dict(id=ident, name=name, count=count, kind=kind, parent=parent, collapsed=collapsed))
        entry.setToolTip(f'{name} · {count} 個模組')
        entry.setData(Qt.ItemDataRole.AccessibleTextRole, f'{name}，{count} 個模組')
        if kind == 'shortcut' or not self.manage: entry.setFlags(entry.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
        self.addItem(entry)

    def mousePressEvent(self, event):
        self._pressed_control = None
        item = self.itemAt(event.position().toPoint())
        if item:
            data = item.data(ROW); arrow, _, _, more = self.regions(self.visualItemRect(item), data)
            if event.button() == Qt.MouseButton.LeftButton:
                action = ('toggle' if data['kind'] == 'folder' and arrow.contains(event.position().toPoint())
                          else 'menu' if self.manage and data['kind'] != 'shortcut' and more.contains(event.position().toPoint()) else None)
                if action:
                    self._pressed_control = (item, action); event.accept(); return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        if self._pressed_control:
            item, action = self._pressed_control; self._pressed_control = None
            if item is self.itemAt(event.position().toPoint()):
                if action == 'toggle': self.toggleRequested.emit(item.data(Qt.ItemDataRole.UserRole))
                else: self.open_menu(item)
            event.accept(); return
        super().mouseReleaseEvent(event)

    def open_menu(self, item):
        if not self.manage: return
        data = item.data(ROW)
        if data['kind'] == 'shortcut': return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        more = self.regions(self.visualItemRect(item), data)[3]
        self.menuRequested.emit(data['id'], self.viewport().mapToGlobal(more.bottomLeft()))

    def contextMenuEvent(self, event):
        item = self.itemAt(event.pos()) or self.currentItem()
        if item: self.open_menu(item)
        event.accept()

    def keyPressEvent(self, event):
        if self.manage and event.matches(QKeySequence.StandardKey.Undo): self.undoRequested.emit(); event.accept(); return
        if self.manage and event.matches(QKeySequence.StandardKey.Redo): self.redoRequested.emit(); event.accept(); return
        item = self.currentItem(); data = item.data(ROW) if item else None
        if data and data['kind'] == 'folder' and event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            if data['collapsed'] == (event.key() == Qt.Key.Key_Right): self.toggleRequested.emit(data['id'])
            event.accept(); return
        if item and (event.key() == Qt.Key.Key_Menu or (event.key() == Qt.Key.Key_F10 and event.modifiers() & Qt.KeyboardModifier.ShiftModifier)):
            self.open_menu(item); event.accept(); return
        super().keyPressEvent(event)

    def _placement(self, position):
        if not self.viewport().rect().contains(position): return None
        item = self.itemAt(position)
        if item is None:
            row = self._insertion_row(position)
            item = self.item(row) if row is not None and row < self.count() else None
        if item is None: return (None, None, None)
        data = item.data(ROW); rect = self.visualItemRect(item)
        if data['kind'] == 'shortcut':
            first = next((self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())
                          if self.item(i).data(ROW)['kind'] != 'shortcut' and not self.item(i).data(ROW)['parent']), None)
            return (None, first if data['id'] is None else None, None)
        after = position.y() > rect.center().y()
        if (data['kind'] == 'folder' and not nav.is_folder(self._drag_ident)
                and rect.top()+rect.height()//4 <= position.y() <= rect.bottom()-rect.height()//4):
            return (data['id'], None, data['id'])
        destination = data['parent']
        target = data['id']
        if nav.is_folder(self._drag_ident) or (destination and position.x() < 24):
            target = destination or target; destination = None
        siblings = [self.item(i).data(ROW)['id'] for i in range(self.count())
                    if self.item(i).data(ROW)['kind'] != 'shortcut' and self.item(i).data(ROW)['parent'] == destination]
        if after:
            index = siblings.index(target)+1
            target = siblings[index] if index < len(siblings) else None
        return (destination, target, None)

    def dragMoveEvent(self, event):
        if not self.has_folders: return super().dragMoveEvent(event)
        payload = self._own_drag(event)
        if not payload: self._clear_drop(); event.ignore(); return
        self._drag_ident = payload['ident']; self._drag_position = event.position().toPoint()
        self._folder_drop = self._placement(self._drag_position)
        if self._folder_drop is None: self._clear_drop(); event.ignore(); return
        self._drop_row = self._placement_row(self._folder_drop)
        self._scroll_timer.start(); self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction); event.accept()

    def _scroll_drag(self):
        super()._scroll_drag()
        if self.has_folders and self._drag_position is not None:
            self._folder_drop = self._placement(self._drag_position)
            if self._folder_drop is not None: self._drop_row = self._placement_row(self._folder_drop)

    def _placement_row(self, placement):
        destination, before, hover = placement
        if before or hover:
            return next(i for i in range(self.count()) if self.item(i).data(Qt.ItemDataRole.UserRole) == (hover or before))
        if destination:
            members = [i for i in range(self.count()) if self.item(i).data(ROW)['parent'] == destination
                       or self.item(i).data(Qt.ItemDataRole.UserRole) == destination]
            return members[-1]+1
        return self.count()-1  # Before the fixed uncategorized shortcut.

    def _clear_drop(self):
        self._folder_drop = None; self._drag_ident = None
        super()._clear_drop()

    def dropEvent(self, event):
        if not self.has_folders: return super().dropEvent(event)
        payload = self._own_drag(event)
        if payload: self._drag_ident = payload['ident']
        placement = self._placement(event.position().toPoint()) if payload else None
        self._clear_drop()
        if placement is None: event.ignore(); return
        event.setDropAction(Qt.DropAction.MoveAction); event.accept()
        self.moveRequested.emit(payload['ident'], placement[0], placement[1])

    def paintEvent(self, event):
        placement = self._folder_drop
        if not placement or not placement[2]: return super().paintEvent(event)
        row = self._drop_row; self._drop_row = None
        super().paintEvent(event); self._drop_row = row
        target = next((self.item(i) for i in range(self.count()) if self.item(i).data(Qt.ItemDataRole.UserRole) == placement[2]), None)
        if target:
            painter = QPainter(self.viewport()); painter.setPen(QPen(QColor(widget_colors(self)['accent']), 2))
            painter.drawRoundedRect(self.visualItemRect(target).adjusted(2, 2, -2, -2), 7, 7)
