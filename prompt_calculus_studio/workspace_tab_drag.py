"""Captured internal tab gestures, isolated from native file-transfer drags."""
from PySide6.QtCore import QObject, Qt, QEvent, QPoint
from PySide6.QtGui import QPainter, QPen, QMouseEvent
from PySide6.QtWidgets import QWidget, QApplication


class DropPreview(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        ink = self.palette().highlight().color()
        ink.setAlpha(48)
        painter.fillRect(self.rect(), ink)
        ink.setAlpha(190)
        painter.setPen(QPen(ink, 2))
        painter.drawRect(self.rect().adjusted(1, 1, -1, -1))


class TabDrag(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.w = workspace
        self.press = None
        self.dragging = None
        self.target = None
        self.source = None
        self.cancelled = False
        # A QWidget parented directly to QSplitter becomes a third split child.
        # Paint above the workspace instead; previews never take layout space.
        self.preview = DropPreview(workspace.centralWidget())
        QApplication.instance().installEventFilter(self)

    def register(self, pane):pass

    def destination(self, global_point):
        for pane in self.w.panes:
            bar = pane.strip.bar
            point = bar.mapFromGlobal(global_point)
            if bar.rect().contains(point):
                at = bar.tabAt(point)
                if at < 0:at = bar.count()
                elif point.x() > bar.tabRect(at).center().x():at += 1
                origin = bar.mapTo(self.preview.parentWidget(), QPoint(0, 0))
                x = bar.tabRect(at).left() if at < bar.count() else bar.tabRect(bar.count()-1).right()
                self.preview.setGeometry(origin.x()+x-1, origin.y(), 3, bar.height())
                return pane, at
            point = pane.pages.mapFromGlobal(global_point)
            if len(self.w.panes) == 1 and pane.pages.rect().contains(point) and point.x() >= pane.pages.width()-max(50, int(pane.pages.width()*.18)):
                rect = self.w.pane_split.rect()
                rect.setLeft(rect.width()//2)
                rect.translate(self.w.pane_split.mapTo(self.preview.parentWidget(), QPoint(0, 0)))
                self.preview.setGeometry(rect)
                return 'split', None

    def update_target(self, point):
        self.target = self.destination(point)
        self.preview.setVisible(self.target is not None)
        self.preview.raise_()

    def finish(self, global_point, commit):
        source, tab = self.source, self.dragging
        target = self.destination(global_point) if commit else None
        self.dragging = self.source = self.target = self.press = None
        self.preview.hide()
        source.releaseMouse()
        source.setMovable(True)
        # Complete the original QTabBar press only after releasing capture.
        local = source.mapFromGlobal(global_point)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, local, global_point,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(source, release)
        if target and tab.key in self.w.tabs_by_key:
            pane, at = target
            self.w.move_view(tab, None if pane == 'split' else pane, at)

    def eventFilter(self, obj, event):
        kind = event.type()
        if self.dragging is not None:
            if kind == QEvent.Type.MouseMove:
                self.update_target(event.globalPosition().toPoint())
                return True
            if kind == QEvent.Type.MouseButtonRelease and event.button() == Qt.MouseButton.LeftButton:
                self.finish(event.globalPosition().toPoint(), True)
                return True
            if kind == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                from PySide6.QtGui import QCursor
                self.finish(QCursor.pos(), False)
                self.cancelled = True
                return True
            if kind == QEvent.Type.ApplicationDeactivate:
                from PySide6.QtGui import QCursor
                self.finish(QCursor.pos(), False)
        bars = [p.strip.bar for p in self.w.panes]
        if kind == QEvent.Type.MouseButtonRelease:
            self.press = None
            if self.cancelled:
                self.cancelled = False
                return True
        if obj not in bars:return False
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.cancelled = False
            at = obj.tabAt(event.position().toPoint())
            self.press = (obj, event.position().toPoint(), obj.tabData(at)) if at >= 0 else None
        elif kind == QEvent.Type.MouseMove and self.press and event.buttons() & Qt.MouseButton.LeftButton:
            bar, start, key = self.press
            point = event.position().toPoint()
            if bar is obj and not obj.rect().contains(point) and (point-start).manhattanLength() >= QApplication.startDragDistance():
                tab = self.w.tabs_by_key.get(key)
                self.press = None
                if tab is None or not tab.path:return False
                self.dragging, self.source = tab, obj
                obj.setMovable(False)
                obj.grabMouse()
                self.update_target(event.globalPosition().toPoint())
                return True
        return False
