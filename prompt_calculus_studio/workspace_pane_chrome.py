"""Pane chrome follows the content geometry without adding a second frame."""
from PySide6.QtCore import Qt, QEvent, QPoint, QTimer, QSize, QLineF
from PySide6.QtGui import QColor, QPainter, QPen, QMouseEvent
from PySide6.QtWidgets import QApplication, QWidget, QSplitter, QSplitterHandle


class PaneDivider(QSplitterHandle):
    def __init__(self, orientation, parent):
        super().__init__(orientation, parent)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setMouseTracking(True)
        self.setAccessibleName('調整左右頁面寬度')

    def paintEvent(self, event):
        # QSplitter expands the hit area of a one-pixel handle over the panes.
        # Only the seam is painted, never that wider invisible target.
        color = QColor(self.splitter().seam_color)
        color.setAlpha(36 if self.underMouse() else 14)
        painter = QPainter(self)
        pen = QPen(color, 1)
        pen.setCosmetic(True)
        painter.setPen(pen)
        x = self.width() / 2
        painter.drawLine(QLineF(x, 0, x, self.height()))

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()


class PaneSplitter(QSplitter):
    seam_color = QColor('#e6e6e6')

    def __init__(self,workspace=None):
        super().__init__(Qt.Orientation.Horizontal)
        self.workspace = workspace
        self.setHandleWidth(1)
        self.setChildrenCollapsible(False)

    def restore_ratio(self):
        if self.workspace is not None and self.count()==2:
            total=max(0,self.width()-self.handleWidth())
            left=round(total*self.workspace.split_ratio)
            self.setSizes([left,total-left])

    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.restore_ratio()

    def showEvent(self,event):
        super().showEvent(event)
        self.restore_ratio()

    def createHandle(self):
        return PaneDivider(self.orientation(), self)


class PaneResizeGrip(QWidget):
    """Keep the native divider's full hit area above the stable editor layer."""
    def __init__(self, workspace):
        super().__init__(workspace.workspace_stack)
        self.w = workspace
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setCursor(Qt.CursorShape.SplitHCursor)
        self.setAccessibleName('調整左右頁面寬度')
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.sync)
        workspace.pane_split.installEventFilter(self)
        workspace.pane_split.splitterMoved.connect(self.request_sync)
        self.hide()

    def request_sync(self, *_):
        if not self.timer.isActive():
            self.timer.start(0)

    def sync(self, *_):
        split = self.w.pane_split
        if len(self.w.panes) != 2 or split.count() != 2 or not split.isVisible():
            self.hide()
            return
        handle = split.handle(1)
        self.setGeometry(handle.mapTo(self.parentWidget(), QPoint()).x(),
                         handle.mapTo(self.parentWidget(), QPoint()).y(), handle.width(), handle.height())
        self.show()
        self.raise_()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Move, QEvent.Type.Resize, QEvent.Type.Show, QEvent.Type.Hide):
            self.request_sync()
        return False

    def forward_mouse(self, event):
        handle = self.w.pane_split.handle(1)
        forwarded = QMouseEvent(event.type(), handle.mapFromGlobal(event.globalPosition().toPoint()),
                                event.globalPosition(), event.button(), event.buttons(), event.modifiers())
        QApplication.sendEvent(handle, forwarded)
        event.accept()

    mousePressEvent = forward_mouse
    mouseMoveEvent = forward_mouse
    mouseReleaseEvent = forward_mouse


class PaneTabRow(QWidget):
    """Two strips on the established row, with the same boundary as the views."""
    def __init__(self, workspace):
        super().__init__()
        self.w = workspace
        self.pending = False
        workspace.pane_split.installEventFilter(self)
        workspace.home_split.installEventFilter(self)
        workspace.pane_split.splitterMoved.connect(self.sync)

    def sizeHint(self):
        return QSize(440, self.w.panes[0].strip.bar.tab_height)

    def minimumSizeHint(self):
        return QSize(280, self.w.panes[0].strip.bar.tab_height)

    def sync(self, *_):
        if len(self.w.panes) != 2 or self.isHidden():
            return
        if self.w.active_route=='home' and self.w.pane_split.isVisible():
            boundary = self.mapFromGlobal(self.w.panes[1].container.mapToGlobal(QPoint())).x()
        else:
            # Hidden pane geometries are stale while a utility page resizes.
            boundary = round(self.width()*self.w.split_ratio)
        # At the desktop minimum width the tab list remains reachable even if
        # the fixed caption controls leave less room than the content below.
        boundary = max(138, min(boundary, self.width()-138))
        self.w.panes[0].strip.setGeometry(0, 0, boundary, self.height())
        self.w.panes[1].strip.setGeometry(boundary, 0, self.width()-boundary, self.height())
        self.update()
        if self.parentWidget():self.parentWidget().update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Move, QEvent.Type.LayoutRequest) and not self.pending:
            self.pending = True
            QTimer.singleShot(0, self, self.deferred_sync)
        return False

    def deferred_sync(self):
        self.pending = False
        self.sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync()

