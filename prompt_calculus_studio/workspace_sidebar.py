"""A shared sidebar edge, including deliberate drag-to-collapse."""
from collections import OrderedDict
from PySide6.QtCore import QObject, QEvent, QPoint, QTimer, QRect, QRectF, Qt, QPersistentModelIndex, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QPainterPath, QPixmap
from PySide6.QtWidgets import QApplication, QTreeWidget, QStyle, QStyleOptionViewItem, QStyledItemDelegate
from shiboken6 import isValid
from .sidebar_resize import SidebarResizeHandle


MIN_WIDTH = 220
MAX_WIDTH = 480
COLLAPSE_OVERSHOOT = 36


class WorkspaceTreeDelegate(QStyledItemDelegate):
    def __init__(self, tree):
        super().__init__(tree)
        self.rows = OrderedDict()
        tree.model().dataChanged.connect(lambda *_: self.rows.clear())
        tree.model().modelReset.connect(lambda *_: self.rows.clear())
        tree.model().rowsRemoved.connect(lambda *_: self.rows.clear())

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(Qt.ItemDataRole.UserRole) and option.icon.isNull():
            # Reserve the folder icon column without drawing a document icon.
            option.features |= QStyleOptionViewItem.ViewItemFeature.HasDecoration
            option.decorationSize = self.parent().iconSize()
            if index.parent().parent().isValid():
                # Inside a child folder, names begin under that folder's label.
                # Root-level files still align with sibling folder labels.
                option.rect.adjust(-self.parent().indentation(), 0, 0, 0)

    def paint(self, painter, option, index):
        clean = QStyleOptionViewItem(option)
        clean.state &= ~(QStyle.StateFlag.State_Selected | QStyle.StateFlag.State_MouseOver | QStyle.StateFlag.State_HasFocus)
        # The native QSS delegate is expensive on rapid disclosure repaints.
        # Cache its unchanged label/icon only; hover, focus and drop paint live.
        dpr = self.parent().devicePixelRatioF()
        key = (QPersistentModelIndex(index), clean.rect.size().toTuple(), clean.font.toString(),
               clean.palette.cacheKey(), clean.state.value, clean.direction.value, dpr)
        pixmap = self.rows.get(key)
        if pixmap is None:
            pixmap = QPixmap(round(clean.rect.width()*dpr), round(clean.rect.height()*dpr))
            pixmap.setDevicePixelRatio(dpr)
            pixmap.fill(Qt.GlobalColor.transparent)
            cached = QPainter(pixmap)
            local = QStyleOptionViewItem(clean)
            local.rect.moveTopLeft(QPoint())
            super().paint(cached, local, index)
            cached.end()
            self.rows[key] = pixmap
            if len(self.rows) > 128:
                self.rows.popitem(last=False)
        else:
            self.rows.move_to_end(key)
        painter.drawPixmap(clean.rect.topLeft(), pixmap)


class WorkspaceTree(QTreeWidget):
    """One rounded row highlight, including the tree's indentation gutter."""
    def __init__(self, workspace):
        super().__init__()
        self.workspace = workspace
        from .theme import visual_tokens
        from .workspace_style import workspace_colors
        self.row_colors = dict(visual_tokens(workspace.display_settings()), **workspace_colors(workspace.display_settings()))
        self.hovered = QPersistentModelIndex()
        self.file_drag = None
        self.setMouseTracking(True)
        self.setItemDelegate(WorkspaceTreeDelegate(self))

    def changeEvent(self, event):
        if event.type() in (QEvent.Type.StyleChange, QEvent.Type.FontChange, QEvent.Type.PaletteChange):
            delegate = self.itemDelegate()
            if isinstance(delegate, WorkspaceTreeDelegate):
                delegate.rows.clear()
        super().changeEvent(event)

    def drawRow(self, painter, option, index):
        selected = self.selectionModel().isSelected(index)
        hovered = self.hovered == index
        dropping = self.file_drag is not None and self.file_drag.target == index
        focused = (self.hasFocus() and self.testAttribute(Qt.WidgetAttribute.WA_KeyboardFocusChange)
                   and self.currentIndex() == index)
        colors = self.row_colors
        bounds = QRectF(2, option.rect.y()+1, self.viewport().width()-4, option.rect.height()-2)
        if selected or hovered or dropping:
            painter.save()
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(colors['selected' if selected or dropping else 'hover']))
            painter.drawRoundedRect(bounds, 6, 6)
            painter.restore()
        # Do not ask the native style to draw PE_PanelItemViewRow: Windows adds
        # its own gutter marker and hover block independently of item QSS.
        content = QStyleOptionViewItem(option)
        content.rect = self.visualRect(index)
        content.decorationSize = self.iconSize()
        self.drawBranches(painter, QRect(0, content.rect.y(), content.rect.x(), content.rect.height()), index)
        self.itemDelegateForIndex(index).paint(painter, content, index)
        if focused or dropping:
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

    def startDrag(self, actions):
        if self.file_drag is not None:
            self.file_drag.start()

    def dragEnterEvent(self, event):
        if self.file_drag is not None:
            self.file_drag.enter(event)

    def dragMoveEvent(self, event):
        if self.file_drag is not None:
            self.file_drag.move(event)

    def dragLeaveEvent(self, event):
        if self.file_drag is not None:
            self.file_drag.clear_target()
        event.accept()

    def dropEvent(self, event):
        if self.file_drag is not None:
            self.file_drag.drop(event)

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
        self.motion_target = None
        self.motion_width = 0
        self.motion = QVariantAnimation(self)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.valueChanged.connect(self.motion_frame)
        self.motion.finished.connect(self.finish_motion)
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
            width = self.motion_width if self.target is self.motion_target else self.limit(self.window.sidebar_width)
            if self.target.minimumWidth()!=width or self.target.maximumWidth()!=width:
                self.target.setFixedWidth(width)
                parent = self.target.parentWidget()
                if self.target is self.window.sidebar:
                    self.window.home_split.setSizes([width, max(0, self.window.home_split.width()-width)])
                elif parent.layout():
                    parent.layout().activate()
                utility = self.window.utility
                from .deferred_page import page_ready
                if utility and page_ready(utility.clean_export) and self.target is utility.clean_export.sidebar:
                    utility.clean_export.adapt_layout()
                elif utility and page_ready(utility.gallery) and self.target is utility.gallery.folder_panel:
                    utility.gallery.adapt_panels()
            self.window.sync_tab_inset()
            if self.motion_target is not None:
                self.handle.hide()
                return
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
        self.finish_motion()
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
        self.finish_motion()
        self.window.sidebar_width = self.limit(width)
        self.sync()

    def animate_visibility(self, expanded):
        """One shared edge carries the heading, tabs and page during a toggle."""
        from .dialog_presentation import reduced_motion
        target = self.window.sidebar_target()
        if target is None or not self.window.isVisible() or reduced_motion(self.window):
            self.finish_motion()
            return False
        # Respect Windows' accessibility preference as well as the app setting.
        if __import__('sys').platform == 'win32':
            import ctypes
            enabled = ctypes.c_int(1)
            if ctypes.windll.user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(enabled), 0) and not enabled.value:
                self.finish_motion()
                return False
        start = self.motion_width if self.motion_target is target else target.width() if target.isVisible() else 0
        self.motion.stop()
        if self.motion_target is not None and self.motion_target is not target:
            self.finish_motion()
        self.motion_target = target
        self.motion_width = start
        end = self.limit(self.window.sidebar_width) if expanded else 0
        target.setFixedWidth(start)
        target.show()
        self.motion.setDuration(240 if expanded else 180)
        self.motion.setStartValue(start)
        self.motion.setEndValue(end)
        self.motion.start()
        self.motion_frame(start)
        return True

    def motion_frame(self, value):
        if self.motion_target is None:
            return
        self.motion_width = max(0, round(value))
        self.sync()
        self.window.workspace_stack.sync_surface()

    def finish_motion(self):
        if self.motion_target is None:
            return
        self.motion.stop()
        target = self.motion_target
        self.motion_target = None
        if isValid(target):
            target.setVisible(bool(self.window.sidebar_preference))
            target.setFixedWidth(self.limit(self.window.sidebar_width))
        self.window.sync_tab_inset()
        self.window.workspace_stack.sync_surface()
        self.schedule()

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
        self.finish_motion()
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
