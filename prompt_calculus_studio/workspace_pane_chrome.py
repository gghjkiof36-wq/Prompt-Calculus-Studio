"""Pane chrome follows the content geometry without adding a second frame."""
from PySide6.QtCore import Qt, QEvent, QPoint, QTimer, QSize, QLineF
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import QWidget, QSplitter, QSplitterHandle


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

    def __init__(self):
        super().__init__(Qt.Orientation.Horizontal)
        self.setHandleWidth(1)
        self.setChildrenCollapsible(False)

    def createHandle(self):
        return PaneDivider(self.orientation(), self)


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
        boundary = self.mapFromGlobal(self.w.panes[1].container.mapToGlobal(QPoint())).x()
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
            QTimer.singleShot(0, self.deferred_sync)
        return False

    def deferred_sync(self):
        self.pending = False
        self.sync()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.sync()

