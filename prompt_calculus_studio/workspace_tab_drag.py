"""One captured gesture for tab ordering and cross-pane moves."""
from PySide6.QtCore import QObject, Qt, QEvent, QPoint, QRect, QRectF, QTimer, QEasingCurve
from PySide6.QtGui import QColor, QPainter, QPen, QMouseEvent
from PySide6.QtWidgets import QWidget, QApplication

from .dialog_presentation import reduced_motion
from .theme import visual_tokens
from .ui_icons import icon


class DropPreview(QWidget):
    def __init__(self, workspace):
        super().__init__(workspace.centralWidget())
        self.w = workspace
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.amount = 1.0
        from .elapsed_animation import ElapsedAnimation
        self.motion = ElapsedAnimation(self)
        self.motion.setDuration(120)
        self.motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.motion.setStartValue(.6)
        self.motion.setEndValue(1.0)
        self.motion.valueChanged.connect(self.fade)
        self.hide()

    def fade(self, value):
        self.amount = float(value)
        self.update()

    def present(self, rect):
        entering = self.isHidden()
        self.setGeometry(rect)
        if entering:
            self.show()
            self.raise_()
            self.motion.stop()
            if reduced_motion(self.w) or rect.width() <= 4:
                self.fade(1.0)
            else:
                self.fade(.6)
                self.motion.start()

    def clear(self):
        self.motion.stop()
        self.hide()

    def paintEvent(self, event):
        painter = QPainter(self)
        ink = QColor(visual_tokens(self.w.settings)['accent'])
        if self.width() <= 4:
            painter.fillRect(self.rect(), ink)
            return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        fill = QColor(ink)
        fill.setAlpha(round(78*self.amount))
        ink.setAlpha(round(210*self.amount))
        painter.setPen(QPen(ink, 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(3.5, 3.5, -3.5, -3.5), 8, 8)


class DragLabel(QWidget):
    """A small page label follows the pointer, without capturing input."""
    def __init__(self, workspace):
        super().__init__(workspace.centralWidget())
        self.w = workspace
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    def begin(self, tab):
        self.title = self.fontMetrics().elidedText(tab.title + (' *' if tab.dirty else ''),
                                                  Qt.TextElideMode.ElideRight, 180)
        suffix = tab.path.suffix if tab.path else ''
        self.symbol = 'workflow' if suffix == '.pcs-workflow' else 'list' if suffix == '.pcs-category' else 'message-square' if suffix == '.pcs-chat' else 'file-text'
        self.resize(self.fontMetrics().horizontalAdvance(self.title)+48, 34)

    def follow(self, global_point):
        point = self.parentWidget().mapFromGlobal(global_point)+QPoint(16, 18)
        area = self.parentWidget().rect()
        self.move(max(0, min(point.x(), area.width()-self.width())),
                  max(0, min(point.y(), area.height()-self.height())))
        self.show()
        self.raise_()

    def paintEvent(self, event):
        t = visual_tokens(self.w.settings)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(t['line']), 1))
        painter.setBrush(QColor(t['raised']))
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(.5, .5, -.5, -.5), 6, 6)
        icon(self.symbol, t['text']).paint(painter, QRect(10, 9, 16, 16))
        painter.setPen(QColor(t['text']))
        painter.drawText(self.rect().adjusted(33, 0, -10, 0), Qt.AlignmentFlag.AlignVCenter, self.title)


class TabDrag(QObject):
    def __init__(self, workspace):
        super().__init__(workspace)
        self.w = workspace
        self.press = self.dragging = self.target = self.source = None
        self.last_point = QPoint()
        self.cancelled = False
        # Overlays are siblings of the layout, never children of QSplitter.
        self.preview = DropPreview(workspace)
        self.label = DragLabel(workspace)
        self.scroll_control = None
        self.scroll_timer = QTimer(self)
        self.scroll_timer.setInterval(240)
        self.scroll_timer.timeout.connect(self.scroll)
        from .application_events import listen
        listen(self, QEvent.Type.MouseMove, QEvent.Type.MouseButtonRelease,
               QEvent.Type.MouseButtonPress, QEvent.Type.KeyPress,
               QEvent.Type.ApplicationDeactivate, QEvent.Type.Hide, QEvent.Type.UngrabMouse)

    def register(self, pane):
        pass

    def pane_rect(self, pane):
        parent = self.preview.parentWidget()
        rect = QRect(pane.container.mapTo(parent, QPoint()), pane.container.size())
        rect.setTop(pane.strip.mapTo(parent, QPoint()).y())
        return rect

    def destination(self, global_point, *, opening_file=False):
        self.target_rect = None
        self.scroll_control = None
        if self.w.active_route != 'home':
            return None
        for pane in self.w.panes:
            bar = pane.strip.bar
            if not pane.container.isVisible():
                continue
            point = bar.mapFromGlobal(global_point)
            strip_point = pane.strip.mapFromGlobal(global_point)
            if pane.strip.rect().contains(strip_point):
                for control in bar.scroll_controls:
                    if control.isVisible() and control.rect().contains(control.mapFromGlobal(global_point)):
                        if control.isEnabled():
                            self.scroll_control = control
                        return None
                if any(control.isVisible() and control.rect().contains(control.mapFromGlobal(global_point))
                       for control in (pane.strip.plus, pane.strip.overflow)):
                    return None
                at = next((i for i in range(bar.count()) if point.x() < bar.tabRect(i).center().x()), bar.count())
                x = bar.tabRect(at).left() if at < bar.count() else bar.tabRect(bar.count()-1).right()+1
                x = max(1, min(x, bar.width()-2))
                origin = bar.mapTo(self.preview.parentWidget(), QPoint(x-1, 3))
                self.target_rect = QRect(origin, bar.size()).adjusted(0, 0, 3-bar.width(), -6)
                return pane, at
            point = pane.pages.mapFromGlobal(global_point)
            if not pane.pages.rect().contains(point):
                continue
            if len(self.w.panes) == 1 and point.x() >= pane.pages.width()*2//3:
                rect = self.pane_rect(pane)
                rect.setLeft(rect.left()+round((rect.width()-1)*self.w.split_ratio)+1)
                self.target_rect = rect
                return 'split', None
            if opening_file or (len(self.w.panes) == 2 and self.dragging is not None and pane is not self.w.pane_for(self.dragging)):
                self.target_rect = self.pane_rect(pane)
                return pane, None

    def update_target(self, point):
        self.last_point = point
        self.target = self.destination(point)
        if self.target is None:
            self.preview.clear()
        else:
            self.preview.present(self.target_rect)
        self.label.follow(point)
        if self.scroll_control is not None:
            if not self.scroll_timer.isActive():
                self.scroll_timer.start()
        else:
            self.scroll_timer.stop()

    def scroll(self):
        if self.scroll_control is not None and self.dragging is not None:
            self.scroll_control.click()
            self.update_target(self.last_point)

    def clear_press(self):
        if self.press is not None:
            bar, _, _, movable = self.press
            self.press = None
            bar.setMovable(movable)

    def finish(self, global_point, commit):
        source, tab = self.source, self.dragging
        target = self.destination(global_point) if commit else None
        self.dragging = self.source = self.target = None
        self.clear_press()
        self.preview.clear()
        self.label.hide()
        self.scroll_timer.stop()
        self.scroll_control = None
        source.releaseMouse()
        source.setCursor(self.previous_cursor)
        # Finish the native click; native tab sorting never owned this drag.
        local = source.mapFromGlobal(global_point)
        release = QMouseEvent(QEvent.Type.MouseButtonRelease, local, global_point,
                             Qt.MouseButton.LeftButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(source, release)
        if target and tab.key in self.w.tabs_by_key:
            pane, at = target
            if pane is self.w.pane_for(tab) and at is not None:
                at -= self.w.index_of(tab.key) < at
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
                self.finish(self.last_point, False)
                self.cancelled = True
                return True
            if kind == QEvent.Type.ApplicationDeactivate or (obj is self.source and kind in (QEvent.Type.Hide, QEvent.Type.UngrabMouse)):
                self.finish(self.last_point, False)
                self.cancelled = True
        if kind == QEvent.Type.MouseButtonRelease:
            self.clear_press()
            if self.cancelled:
                self.cancelled = False
                return True
        if kind == QEvent.Type.MouseMove and self.press and event.buttons() & Qt.MouseButton.LeftButton:
            bar, start, key, _ = self.press
            point = event.globalPosition().toPoint()
            if (point-start).manhattanLength() >= QApplication.startDragDistance():
                tab = self.w.tabs_by_key.get(key)
                if tab is None:
                    self.clear_press()
                    return False
                self.dragging, self.source = tab, bar
                self.previous_cursor = bar.cursor()
                bar.setCursor(Qt.CursorShape.ClosedHandCursor)
                bar.grabMouse()
                self.label.begin(tab)
                self.update_target(point)
                return True
        if kind == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
            self.cancelled = False
            self.clear_press()
            bars = [pane.strip.bar for pane in self.w.panes]
            if obj in bars and QApplication.activeModalWidget() is None:
                at = obj.tabAt(event.position().toPoint())
                if at >= 0:
                    self.press = (obj, event.globalPosition().toPoint(), obj.tabData(at), obj.isMovable())
                    # Prevent QTabBar's separate reorder drag from stealing capture.
                    obj.setMovable(False)
        return False
