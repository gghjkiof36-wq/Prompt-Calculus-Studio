"""One resize affordance and width preference for every context sidebar."""
from PySide6.QtCore import QObject, QEvent, QPoint, QPointF, QTimer, Qt
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QWidget
from shiboken6 import isValid

from .theme import visual_tokens


MIN_WIDTH = 220
MAX_WIDTH = 480
DEFAULT_WIDTH = 240


class SidebarResizeHandle(QWidget):
    def __init__(self, controller):
        super().__init__(controller.window)
        self.controller = controller
        self.hovered = False
        self.setObjectName('SidebarResizeHandle')
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setMouseTracking(True)
        self.setToolTip('拖曳調整側欄寬度')
        self.setAccessibleName('調整側欄寬度')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.hide()

    def paintEvent(self, event):
        if self.hovered or self.controller.dragging:
            colors = visual_tokens(self.controller.window.state['settings'])
            painter = QPainter(self)
            painter.fillRect(self.width() // 2 - 1, 0, 2, self.height(), QColor(colors['accent']))

    def enterEvent(self, event):
        self.hovered = True; self.update(); super().enterEvent(event)

    def leaveEvent(self, event):
        self.hovered = False; self.update(); super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.controller.begin(event.globalPosition().toPoint()); event.accept()
        else: super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.controller.dragging:
            self.controller.move(event.globalPosition().toPoint()); event.accept()
        else: super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.controller.dragging:
            self.controller.move(event.globalPosition().toPoint())
            self.controller.finish(); event.accept()
        else: super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            self.controller.resize_to(self.controller.current_width() + (10 if event.key() == Qt.Key.Key_Right else -10))
            self.controller.finish(); event.accept()
        else: super().keyPressEvent(event)


class SidebarResizeController(QObject):
    def __init__(self, window, target_provider, peek):
        super().__init__(window)
        self.window = window; self.target_provider = target_provider; self.peek = peek
        self.target = None; self.dragging = False; self._drag_target = None
        self._drag_anchor = None
        self._pending_width = None; self._syncing = False; self._stopped = False
        self.handle = SidebarResizeHandle(self)
        self.timer = QTimer(self); self.timer.setSingleShot(True); self.timer.timeout.connect(self.sync)
        QApplication.instance().installEventFilter(self)

    def preferred_width(self):
        value = self.window.state['settings'].get('context_sidebar_width', DEFAULT_WIDTH)
        return value if type(value) is int and MIN_WIDTH <= value <= MAX_WIDTH else DEFAULT_WIDTH

    def limit(self, width, overlay=None):
        overlay = self.peek.is_open if overlay is None else overlay
        available = self.window.width() - (64 if overlay else 520)
        return max(MIN_WIDTH, min(MAX_WIDTH, max(MIN_WIDTH, available), round(width)))

    def current_width(self):
        if self.peek.is_open and self.peek.target is self.target: return self.peek.overlay.width()
        return self.target.width() if self.target is not None else self.preferred_width()

    def sync(self):
        if self._stopped or self._syncing: return
        self._syncing = True
        try:
            target = self.target_provider()
            if target is None or not isValid(target):
                self.target = None; self.handle.hide(); return
            self.target = target; target.setProperty('pcsSidebarManaged', True)
            width = self.limit(self._pending_width if self._pending_width is not None else self.preferred_width())
            borrowed = self.peek.is_open and self.peek.target is target
            if borrowed:
                self.peek.set_width(width)
                surface = self.peek.overlay
            else:
                self._set_layout_width(target, width)
                surface = target
            if not surface.isVisible() or not self.window.isVisible():
                self.handle.hide(); return
            position = surface.mapTo(self.window, QPoint())
            inset = 10 if borrowed else 0
            self.handle.setGeometry(position.x() + surface.width() - 4,
                                    position.y() + inset, 8, max(1, surface.height() - 2 * inset))
            self.handle.show(); self.handle.raise_()
        finally: self._syncing = False

    def _set_layout_width(self, target, width):
        if target.minimumWidth() == width and target.maximumWidth() == width: return
        anchor = self._drag_anchor or self._canvas_anchor(target)
        target.setFixedWidth(width)
        parent = target.parentWidget()
        if parent is not None and parent.layout() is not None: parent.layout().activate()
        if anchor is not None: anchor[0].centerOn(anchor[1])
        gallery=getattr(self.window,'gallery',None)
        if gallery is not None and target is gallery.folder_panel:gallery.adapt_panels()
        export=getattr(self.window,'clean_export',None)
        if export is not None and target is export.sidebar:export.adapt_layout()

    def _canvas_anchor(self,target):
        view=getattr(getattr(self.window,'canvas',None),'view',None)
        if view is None or not view.isVisible() or target is not getattr(self.window,'recent_sidebar',None):return None
        center=view.viewportTransform().inverted()[0].map(QPointF(view.viewport().width()/2,view.viewport().height()/2))
        return view,center

    def begin(self, global_position):
        self.sync()
        if self.target is None: return
        self.dragging = True; self._drag_target = self.target
        self._drag_anchor = self._canvas_anchor(self.target)
        self._origin_x = global_position.x(); self._origin_width = self.current_width()
        self.peek.close_timer.stop(); self.handle.update()

    def move(self, global_position):
        if not self.dragging: return
        if self.target_provider() is not self._drag_target:
            self.finish(); return
        self.resize_to(self._origin_width + global_position.x() - self._origin_x)

    def resize_to(self, width):
        self._pending_width = self.limit(width)
        self.sync()

    def finish(self):
        value = self._pending_width
        self.dragging = False; self._drag_target = None; self._pending_width = None; self._drag_anchor = None
        if value is not None and value != self.preferred_width():
            self.window.state['settings']['context_sidebar_width'] = value
            self.window.changed(scope='settings', refresh=False)
        self.handle.update(); self.schedule()

    def schedule(self):
        if not self._stopped and not self.timer.isActive(): self.timer.start(0)

    def shutdown(self):
        if self._stopped: return
        self.finish(); self._stopped = True; self.timer.stop(); self.handle.hide()
        QApplication.instance().removeEventFilter(self)

    def eventFilter(self, watched, event):
        if self._stopped or self._syncing or watched is self.handle: return False
        kind = event.type()
        if watched in (self.window, self.target, self.peek.overlay) and kind in (
                QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.Show, QEvent.Type.Hide,
                QEvent.Type.ParentChange, QEvent.Type.LayoutRequest):
            self.schedule()
        return False
