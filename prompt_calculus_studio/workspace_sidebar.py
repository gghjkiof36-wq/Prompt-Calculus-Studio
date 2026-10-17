"""A shared sidebar edge, including deliberate drag-to-collapse."""
from PySide6.QtCore import QObject, QEvent, QPoint, QTimer, QRect, QRectF, Qt, QPersistentModelIndex
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath
from PySide6.QtWidgets import QApplication, QTreeWidget, QStyle, QStyleOptionViewItem, QStyledItemDelegate
from shiboken6 import isValid
from .sidebar_resize import SidebarResizeHandle


MIN_WIDTH = 220
MAX_WIDTH = 480
COLLAPSE_OVERSHOOT = 36


class WorkspaceTreeDelegate(QStyledItemDelegate):
    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(Qt.ItemDataRole.UserRole) and option.icon.isNull():
            # Reserve the folder icon column without drawing a document icon.
            option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
            option.decorationSize = self.parent().iconSize()

    def paint(self, painter, option, index):
        clean = QStyleOptionViewItem(option)
        clean.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus)
        super().paint(painter, clean, index)


class WorkspaceTree(QTreeWidget):
    """One rounded row highlight, including the tree's indentation gutter."""
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        from .theme import visual_tokens
        from .workspace_style import workspace_colors
        self.row_colors = dict(visual_tokens(workspace.display_settings()), **workspace_colors(workspace.display_settings()))
        self.hovered = QPersistentModelIndex()
        self.setMouseTracking(True)
        self.setItemDelegate(WorkspaceTreeDelegate(self))

    def drawRow(self, painter, option, index):
        selected = self.selectionModel().isSelected(index)
        hovered = self.hovered == index
        focused = (self.hasFocus() and self.testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)
                   and self.currentIndex() == index)
        colors = self.row_colors
        bounds = QRectF(2, option.rect.y()+1, self.viewport().width()-4, option.rect.height()-2)
        if selected or hovered:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors['selected' if selected else 'hover']))
            painter.drawRoundedRect(bounds, 6, 6)
            painter.restore()
        # Do not ask the native style to draw PE_PanelItemViewRow: Windows adds
        # its own gutter marker and hover block independently of item QSS.
        content = QStyleOptionViewItem(option)
        content.rect = self.visualRect(index)
        content.decorationSize = self.iconSize()
        self.drawBranches(painter, QRect(0, content.rect.y(), content.rect.x(), content.rect.height()), index)
        self.itemDelegateForIndex(index).paint(painter, content, index)
        if focused:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor(colors['accent']), 1))
            painter.drawRoundedRect(bounds.adjusted(.5, .5, -.5, -.5), 6, 6)
            painter.restore()

    def drawBranches(self, painter, rect, index):
        # The Windows tree style paints a separate selected marker in every
        # indentation cell. Only a folder disclosure belongs in this gutter.
        if not self.model().hasChildren(index):
            return
        x = rect.right()-self.indentation()/2+1
        y = rect.center().y()+.5
        path = QPainterPath()
        if self.isExpanded(index):
            path.moveTo(x-3, y-1.5); path.lineTo(x, y+1.5); path.lineTo(x+3, y-1.5)
        else:
            path.moveTo(x-1.5, y-3); path.lineTo(x+1.5, y); path.lineTo(x-1.5, y+3)
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(self.row_colors['secondary']), 1.2))
        painter.drawPath(path)
        painter.restore()

    def mouseMoveEvent(self, event):
        index = QPersistentModelIndex(self.indexAt(event.position().toPoint()))
        if index != self.hovered:
            self.hovered = index
            self.viewport().update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        self.hovered = QPersistentModelIndex()
        self.viewport().update()
        super().leaveEvent(event)

    def scrollContentsBy(self, dx, dy):
        self.hovered = QPersistentModelIndex()
        super().scrollContentsBy(dx, dy)


class WorkspaceSidebarResize(QObject):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.target = None
        self.dragging = False
        self.collapsed = False
        self.stopped = False
        self.syncing = False
        self.handle = SidebarResizeHandle(self)
        self.handle.setToolTip('拖曳調整側欄；縮至最小後繼續向左拖曳可收合')
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.sync)
        QApplication.instance().installEventFilter(self)

    def limit(self, width):
        rail_width = self.window.rail.width() if self.window.navigation_position == 'side' else 0
        available = max(MIN_WIDTH, self.window.width()-rail_width-380)
        return max(MIN_WIDTH, min(MAX_WIDTH, available, round(width)))

    def current_width(self):
        return self.target.width() if self.target is not None else self.window.sidebar_width

    def sync(self):
        if self.stopped or self.syncing:
            return
        self.syncing = True
        try:
            self.target = self.window.sidebar_target()
            if self.target is None or not isValid(self.target) or not self.window.isVisible():
                self.handle.hide()
                return
            width = self.limit(self.window.sidebar_width)
            if self.target.minimumWidth()!=width or self.target.maximumWidth()!=width:
                self.target.setFixedWidth(width)
                parent = self.target.parentWidget()
                if parent.layout():
                    parent.layout().activate()
                utility = self.window.utility
                if utility and self.target is utility.clean_export.sidebar:
                    utility.clean_export.adapt_layout()
                elif utility and self.target is utility.gallery.folder_panel:
                    utility.gallery.adapt_panels()
            self.window.sync_tab_inset()
            if not self.target.isVisible() and not self.dragging:
                self.handle.hide()
                return
            position = self.target.mapTo(self.window, QPoint())
            edge = position.x() + (0 if self.collapsed and self.dragging else self.target.width())
            self.handle.setGeometry(edge-4, position.y(), 8, self.target.height())
            self.handle.show()
            self.handle.raise_()
        finally:
            self.syncing = False

    def begin(self, global_position):
        self.sync()
        if self.target is None:
            return
        self.dragging = True
        self.collapsed = False
        self.origin_x = global_position.x()
        self.origin_width = self.current_width()
        self.restore_width = self.window.sidebar_width
        self.drag_target = self.target
        self.handle.update()

    def move(self, global_position):
        if not self.dragging:
            return
        if self.window.sidebar_target() is not self.drag_target:
            self.finish()
            return
        width = self.origin_width+global_position.x()-self.origin_x
        self.collapsed = width < MIN_WIDTH-COLLAPSE_OVERSHOOT
        self.window.sidebar_preference = not self.collapsed
        self.window.sidebar_width = self.restore_width if self.collapsed else self.limit(width)
        self.window.sync_sidebar()
        self.sync()

    def resize_to(self, width):
        # Arrow keys resize without unexpectedly hiding the focused control.
        self.window.sidebar_width = self.limit(width)
        self.sync()

    def finish(self):
        self.dragging = False
        self.collapsed = False
        self.window.save_ui_preferences()
        self.handle.update()
        self.schedule()

    def schedule(self):
        if not self.stopped and not self.timer.isActive():
            self.timer.start(0)

    def shutdown(self):
        self.stopped = True
        self.dragging = False
        self.timer.stop()
        self.handle.hide()
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event):
        if not self.stopped and not self.syncing and watched in (self.window, self.target):
            if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show,
                                QEvent.Type.Hide, QEvent.Type.LayoutRequest):
                self.schedule()
        return False
