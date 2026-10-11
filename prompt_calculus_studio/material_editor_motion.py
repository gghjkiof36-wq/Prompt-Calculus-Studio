"""Editor-only wheel easing and bottom card stack with matching hit geometry."""
import math
from PySide6.QtCore import Qt, QRect, QPoint, QModelIndex, QPropertyAnimation, QEasingCurve, QAbstractAnimation
from PySide6.QtGui import QPainter, QColor, QCursor
from PySide6.QtWidgets import QApplication, QStyleOptionViewItem, QStyle
from .prompt_editor import MaterialList
from .views import widget_colors


class EditorMaterialList(MaterialList):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self._drag_active = False
        self.setVerticalScrollMode(self.ScrollMode.ScrollPerPixel)
        self.scroll_motion = QPropertyAnimation(self.verticalScrollBar(), b'value', self)
        self.scroll_motion.setDuration(160)
        self.scroll_motion.setEasingCurve(QEasingCurve.Type.OutCubic)
        bar = self.verticalScrollBar()
        bar.sliderPressed.connect(self.stop_scroll)
        bar.rangeChanged.connect(self.stop_scroll)
        bar.valueChanged.connect(self.viewport().update)

    def reduced_motion(self):
        return self.owner.window.state['settings'].get('reduce_motion', False)

    def stop_scroll(self, *_):
        self.scroll_motion.stop()

    def wheelEvent(self, event):
        bar = self.verticalScrollBar()
        if (self.reduced_motion() or not event.pixelDelta().isNull() or
                not event.angleDelta().y() or event.modifiers() or self._drag_active):
            self.stop_scroll(); return super().wheelEvent(event)
        active = self.scroll_motion.state() == QAbstractAnimation.State.Running
        origin = self.scroll_motion.endValue() if active else bar.value()
        distance = event.angleDelta().y()/120 * max(1, QApplication.wheelScrollLines()) * bar.singleStep()
        target = max(bar.minimum(), min(bar.maximum(), round(origin-distance)))
        if target == bar.value():
            self.stop_scroll(); event.accept(); return
        self.stop_scroll()
        self.scroll_motion.setStartValue(bar.value()); self.scroll_motion.setEndValue(target)
        self.scroll_motion.start(); event.accept()

    def _stack(self):
        if getattr(self, '_computing_stack', False): return None
        self._computing_stack = True
        try: return self._calculate_stack()
        finally: self._computing_stack = False

    def _calculate_stack(self):
        bar = self.verticalScrollBar()
        if (self._drag_active or self.reduced_motion() or not self.count() or
                bar.maximum() <= 0 or bar.value() >= bar.maximum()): return None
        height = self.viewport().height()
        band = min(180, round(height*.28))
        start = height-band
        first = super().indexAt(QPoint(self.viewport().width()//2, start))
        if not first.isValid(): return None
        strength = min(1., (bar.maximum()-bar.value())/max(1, band))
        layers = []
        for row in range(first.row(), min(self.count(), first.row()+4)):
            index = self.model().index(row, 0)
            rect = super().visualRect(index)
            depth = max(0., (rect.top()-start)/max(1, rect.height()))
            scale = 1-min(.10, depth*.045)*strength
            compressed = start+rect.height()*(1-math.exp(-depth*.65))
            top = rect.top() if depth == 0 else round(rect.top()+(compressed-rect.top())*strength)
            inset = round(rect.width()*(1-scale)/2)
            layers.append((index, QRect(rect.left()+inset, top, rect.width()-inset*2,
                                        round(rect.height()*scale)), depth*strength))
        return start, layers

    def visualRect(self, index):
        stack = self._stack()
        if stack:
            for entry, rect, _ in stack[1]:
                if entry == index: return rect
        return super().visualRect(index)

    def visualItemRect(self, item):
        return self.visualRect(self.indexFromItem(item))

    def scrollTo(self, index, hint=MaterialList.ScrollHint.EnsureVisible):
        if hint == self.ScrollHint.EnsureVisible:
            stack = self._stack()
            if stack and any(entry == index and rect.intersects(self.viewport().rect())
                             for entry, rect, _ in stack[1]): return
        super().scrollTo(index, hint)

    def indexAt(self, point):
        stack = self._stack()
        if stack and point.y() >= stack[0]:
            # Frontmost card wins; gaps never select an invisible row beneath it.
            for index, rect, _ in stack[1]:
                if rect.adjusted(1, 6, -1, -6).contains(point): return index
            return QModelIndex()
        return super().indexAt(point)

    def stack_depth(self, index):
        stack = self._stack()
        if stack:
            for entry, _, depth in stack[1]:
                if entry == index: return depth
        return 0.

    def paintEvent(self, event):
        super().paintEvent(event)
        stack = self._stack()
        if not stack: return
        start, layers = stack
        painter = QPainter(self.viewport())
        painter.setClipRect(QRect(0, start, self.viewport().width(), self.viewport().height()-start))
        painter.fillRect(self.viewport().rect(), QColor(widget_colors(self)['base']))
        hovered = self.indexAt(self.viewport().mapFromGlobal(QCursor.pos()))
        for index, rect, _ in reversed(layers):
            option = QStyleOptionViewItem(); self.initViewItemOption(option); option.rect = rect
            if self.selectionModel().isSelected(index): option.state |= QStyle.StateFlag.State_Selected
            if index == hovered: option.state |= QStyle.StateFlag.State_MouseOver
            if self.hasFocus() and index == self.currentIndex(): option.state |= QStyle.StateFlag.State_HasFocus
            self.itemDelegate().paint(painter, option, index)
        painter.end()

    def mousePressEvent(self, event):
        self.stop_scroll(); super().mousePressEvent(event)

    def keyPressEvent(self, event):
        self.stop_scroll(); super().keyPressEvent(event)

    def hideEvent(self, event):
        self.stop_scroll(); super().hideEvent(event)

    def startDrag(self, actions):
        self.stop_scroll(); self._drag_active = True; self.viewport().update()
        try: super().startDrag(actions)
        finally: self._drag_active = False; self.viewport().update()

    def dragEnterEvent(self, event):
        self.stop_scroll(); super().dragEnterEvent(event)
