"""A single native tab strip with an adjacent new-tab button and overflow."""
from PySide6.QtCore import Qt, QSize, Signal, QRectF
from PySide6.QtGui import QFont, QFontMetrics, QPalette, QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QWidget, QTabBar, QHBoxLayout, QSizePolicy, QStylePainter, QStyleOptionTab, QStyle, QAbstractButton
from .widgets import button, RoundMenu
from .ui_icons import icon


class TabCloseButton(QAbstractButton):
    def __init__(self, bar):
        super().__init__(bar)
        self.bar = bar
        self.setFixedSize(22, 22)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setToolTip('關閉分頁')
        self.clicked.connect(self.close_tab)

    def close_tab(self):
        for i in range(self.bar.count()):
            if self.bar.tabButton(i, QTabBar.ButtonPosition.RightSide) is self:
                self.bar.tabCloseRequested.emit(i)
                break

    def enterEvent(self, event):
        super().enterEvent(event)
        self.update()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        if self.underMouse() or self.isDown():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(self.bar.close_fill)
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(1, 1, -1, -1), 7, 7)
        painter.setPen(QPen(self.bar.active_ink if self.underMouse() else self.bar.idle_ink, 1.2))
        middle = self.width()/2
        from PySide6.QtCore import QLineF
        painter.drawLine(QLineF(middle-3.5, middle-3.5, middle+3.5, middle+3.5))
        painter.drawLine(QLineF(middle+3.5, middle-3.5, middle-3.5, middle+3.5))


class DocumentTabBar(QTabBar):
    def __init__(self):
        super().__init__()
        self.tab_width = 228
        self.tab_height = 40
        self.body_inset = 3
        self.hover_radius = 10
        self._workspace_active = True
        self.join_sidebar = False
        self.sidebar_fill = QColor('#151819')
        self.active_fill = QColor('#151819')
        self.hover_fill = QColor('#2c3235')
        self.close_fill = QColor('#3a4246')
        self.active_ink = QColor('#f1f0ea')
        self.idle_ink = QColor('#aab1b3')
        self.preview_keys = set()
        self.setObjectName('DocumentTabBar')
        self.setExpanding(False)
        self.setMovable(True)
        self.setTabsClosable(True)
        self.setUsesScrollButtons(True)
        self.setElideMode(Qt.TextElideMode.ElideRight)
        self.setDocumentMode(True)
        self.setDrawBase(False)
        self.setAccessibleName('文件分頁')
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)

    @property
    def workspace_active(self):
        return self._workspace_active

    @workspace_active.setter
    def workspace_active(self, active):
        self._workspace_active = active
        self.update()
        if self.parentWidget() is not None:
            self.parentWidget().update()

    def active_path(self, index):
        rect = QRectF(self.tabRect(index))
        left, right = rect.left()+self.body_inset, rect.right()-self.body_inset
        top, bottom = rect.top(), rect.bottom()
        # The adjacent hover corner and this concave foot are concentric.
        # Their radius difference is the same as the horizontal/vertical seam.
        foot, shoulder = self.hover_radius+2*self.body_inset, 14
        path = QPainterPath()
        path.moveTo(left-foot, bottom)
        path.arcTo(QRectF(left-2*foot, bottom-2*foot, 2*foot, 2*foot), 270, 90)
        path.lineTo(left, top+shoulder)
        path.quadTo(left, top, left+shoulder, top)
        path.lineTo(right-shoulder, top)
        path.quadTo(right, top, right, top+shoulder)
        path.lineTo(right, bottom-foot)
        path.arcTo(QRectF(right, bottom-2*foot, 2*foot, 2*foot), 180, 90)
        path.closeSubpath()
        return path

    def tabSizeHint(self, index):
        size = super().tabSizeHint(index)
        size.setWidth(self.tab_width)
        size.setHeight(self.tab_height)
        return size

    def align_close_buttons(self):
        # Qt places the native close controls again after tabLayoutChange.
        # Use the final tab rectangles, including their scroll offset, so both
        # the icon and its hit target stay inside the curved tab body.
        for index in range(self.count()):
            close=self.tabButton(index,QTabBar.ButtonPosition.RightSide)
            if close is not None:
                rect=self.tabRect(index)
                x=rect.right()-close.width()-13
                y=rect.top()+(rect.height()-close.height())//2
                if close.x()!=x or close.y()!=y:
                    close.move(x,y)

    def paintEvent(self, event):
        self.align_close_buttons()
        painter = QStylePainter(self)
        order=[i for i in range(self.count()) if i != self.currentIndex()]+[self.currentIndex()]
        for index in order:
            if index < 0:
                continue
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            if not self.workspace_active:
                option.state &= ~QStyle.StateFlag.State_Selected
            if self.workspace_active and index == self.currentIndex():
                # Curved feet join the page below without a bottom border.
                rect=option.rect
                if self.join_sidebar and index == 0 and rect.left() == 0:
                    # The first tab's curved shoulder meets the sidebar surface,
                    # rather than revealing a sliver of the outer window frame.
                    painter.fillRect(QRectF(rect.left(), rect.top(), self.body_inset+14, rect.height()), self.sidebar_fill)
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.active_fill)
                painter.drawPath(self.active_path(index))
                painter.restore()
            elif option.state & QStyle.StateFlag.State_MouseOver:
                # Inactive hover is a compact pill, not the active tab's feet.
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.hover_fill)
                painter.drawRoundedRect(QRectF(option.rect).adjusted(
                    self.body_inset, 0, -self.body_inset, -2*self.body_inset),
                    self.hover_radius, self.hover_radius)
                painter.restore()
            font = QFont(self.font())
            font.setItalic(self.tabData(index) in self.preview_keys)
            painter.setFont(font)
            option.fontMetrics = QFontMetrics(font)
            # Native tab labels are centered even when a tab has spare width.
            # Keep a stable left inset and reserve the actual close control.
            text_rect = option.rect.adjusted(22, 0, -34, 0)
            close = self.tabButton(index, QTabBar.ButtonPosition.RightSide)
            if close is not None:
                text_rect.setRight(min(text_rect.right(), close.x()-8))
            title = option.fontMetrics.elidedText(self.tabText(index), Qt.TextElideMode.ElideRight, max(0,text_rect.width()))
            option.palette.setColor(QPalette.ColorRole.WindowText,
                self.active_ink if self.workspace_active and index == self.currentIndex() else self.idle_ink)
            painter.drawItemText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 option.palette, self.isTabEnabled(index), title, QPalette.ColorRole.WindowText)


class DocumentTabs(QWidget):
    newRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bar = DocumentTabBar()
        self.plus = button('', self.newRequested.emit, 'Quiet')
        self.plus.setIcon(icon('plus','navigation'))
        self.plus.setAccessibleName('開啟新分頁')
        self.plus.setToolTip('開啟新分頁 (Ctrl+T)')
        self.plus.setFixedSize(32, 32)
        self.overflow = button('', self.show_list, 'Quiet')
        self.overflow.setIcon(icon('chevron-down','navigation'))
        self.overflow.setAccessibleName('所有分頁')
        self.overflow.setToolTip('所有分頁')
        self.overflow.setFixedSize(28, 32)
        self.line = QHBoxLayout(self)
        self.line.setContentsMargins(0, 0, 0, 0)
        self.line.setSpacing(1)
        self.line.addWidget(self.bar)
        self.line.addWidget(self.plus)
        self.line.addWidget(self.overflow)
        self.line.addStretch(1)
        self.bar.tabMoved.connect(lambda *_: self.fit())
        self.bar.currentChanged.connect(self.update)

    def paintEvent(self, event):
        super().paintEvent(event)
        if not self.bar.workspace_active or self.bar.currentIndex() < 0:
            return
        if self.overflow.isVisible() or self.bar.tabRect(self.bar.currentIndex()).right()+1 != self.bar.width():
            return
        # Keep the last tab's foot intact beside + without expanding its hit box
        # or moving that button away. The tab bar paints the part inside itself.
        painter = QPainter(self)
        painter.setClipRegion(QRegion(self.rect()).subtracted(QRegion(self.bar.geometry())))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bar.active_fill)
        painter.translate(self.bar.pos())
        painter.drawPath(self.bar.active_path(self.bar.currentIndex()))

    def set_tab_height(self, height):
        if self.bar.tab_height == height:
            return
        self.bar.tab_height = height
        self.bar.setExpanding(True)
        self.bar.setExpanding(False)
        self.bar.updateGeometry()
        self.updateGeometry()

    def fit(self):
        count = max(1, self.bar.count())
        budget = max(80, self.width()-42)
        width = max(124, min(228, budget//count))
        overflow = count*width > budget
        if overflow:
            budget = max(80, self.width()-74)
        if self.bar.tab_width != width:
            self.bar.tab_width = width
            # Re-layout the native tabs without recreating them or their views.
            self.bar.setExpanding(True)
            self.bar.setExpanding(False)
        desired = count*width
        self.bar.setFixedWidth(min(desired, budget))
        self.overflow.setVisible(overflow)
        self.bar.updateGeometry()
        self.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit()

    def decorate(self, index, title, retained, dirty, path=''):
        self.bar.setTabText(index, title + (' *' if dirty else ''))
        key = self.bar.tabData(index)
        if retained:
            self.bar.preview_keys.discard(key)
        else:
            self.bar.preview_keys.add(key)
        close = self.bar.tabButton(index, QTabBar.ButtonPosition.RightSide)
        if not isinstance(close, TabCloseButton):
            close = TabCloseButton(self.bar)
            self.bar.setTabButton(index, QTabBar.ButtonPosition.RightSide, close)
        close.setAccessibleName('關閉「'+title+'」')
        lines = [path] if path else []
        if dirty:
            lines.append('未保存')
        elif path and not retained:
            lines.append('預覽分頁；雙擊保留')
        self.bar.setTabToolTip(index, '\n'.join(lines))
        self.fit()

    def show_list(self):
        menu = RoundMenu(self)
        for i in range(self.bar.count()):
            action = menu.addAction(self.bar.tabText(i), lambda checked=False, n=i: self.select_from_list(n))
            action.setCheckable(True)
            action.setChecked(i == self.bar.currentIndex())
        menu.open_at(self.overflow.mapToGlobal(self.overflow.rect().bottomLeft()))

    def select_from_list(self, index):
        self.bar.setCurrentIndex(index)
        # Selecting the already-current file must also leave a utility route.
        self.bar.tabBarClicked.emit(index)
