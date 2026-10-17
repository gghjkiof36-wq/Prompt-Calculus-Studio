"""A shared sidebar edge, including deliberate drag-to-collapse."""
from PySide6.QtCore import QObject, QEvent, QPoint, QTimer
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid
from .sidebar_resize import SidebarResizeHandle


MIN_WIDTH = 220
MAX_WIDTH = 480
COLLAPSE_OVERSHOOT = 36


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
        available = max(MIN_WIDTH, self.window.width()-self.window.rail.width()-380)
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
