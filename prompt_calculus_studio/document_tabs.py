"""A single native tab strip with an adjacent new-tab button and overflow."""
from PySide6.QtCore import Qt, QSize, Signal, QRectF, QRect, QPoint, QEvent
from PySide6.QtGui import QFont, QFontMetrics, QPalette, QColor, QPainter, QPainterPath, QPen, QRegion
from PySide6.QtWidgets import QWidget, QTabBar, QHBoxLayout, QSizePolicy, QStylePainter, QStyleOptionTab, QStyleOptionToolButton, QStyle, QAbstractButton, QToolButton
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
        self.pane_active = True
        self.join_sidebar = False
        self.join_outer_edge = False
        self.sidebar_fill = QColor('#151819')
        self.active_fill = QColor('#151819')
        self.hover_fill = QColor('#2c3235')
        self.close_fill = QColor('#3a4246')
        self.scroll_fill = QColor('#202527')
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
        self.scroll_controls = self.findChildren(QToolButton)
        for control in self.scroll_controls:
            control.installEventFilter(self)
            control.clicked.connect(self.update_scroll_controls)
        self.currentChanged.connect(self.update_scroll_controls)
        self.tabMoved.connect(self.update_scroll_controls)

    def update_scroll_controls(self):
        for control in self.scroll_controls:
            control.update()

    def eventFilter(self, control, event):
        if control in self.scroll_controls and event.type() == QEvent.Type.Paint:
            painter = QStylePainter(control)
            option = QStyleOptionToolButton()
            control.initStyleOption(option)
            painter.fillRect(control.rect(), self.hover_fill if control.underMouse() else self.scroll_fill)
            if self.workspace_active and self.currentIndex() >= 0:
                tab = QRectF(self.tabRect(self.currentIndex()))
                button = QRectF(control.geometry())
                # Only the outward foot may reach beneath an arrow. A tab body
                # scrolled out of view remains hidden by the button surface.
                if tab.right()-self.body_inset <= button.left() or tab.left()+self.body_inset >= button.right():
                    painter.save()
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(self.active_fill)
                    painter.translate(-control.pos())
                    painter.drawPath(self.active_path(self.currentIndex()))
                    painter.restore()
            painter.drawControl(QStyle.ControlElement.CE_ToolButtonLabel, option)
            return True
        return super().eventFilter(control, event)

    @property
    def workspace_active(self):
        return self._workspace_active

    @workspace_active.setter
    def workspace_active(self, active):
        self._workspace_active = active
        self.update()
        self.update_scroll_controls()
        if self.parentWidget() is not None:
            self.parentWidget().update_outline()

    def active_path(self, index):
        rect = QRectF(self.tabRect(index))
        left, right = rect.left()+self.body_inset, rect.right()-self.body_inset
        top, bottom = rect.top(), rect.bottom()
        # The adjacent hover corner and this concave foot are concentric.
        # Their radius difference is the same as the horizontal/vertical seam.
        foot, shoulder = self.hover_radius+2*self.body_inset, 14
        path = QPainterPath()
        if self.join_outer_edge and index == 0 and rect.left() == 0:
            # The first tab and the page share the outside edge. An outward
            # foot here would intrude into the rail and create a second corner.
            left = rect.left()
            path.moveTo(left, bottom)
        else:
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
                self.active_ink if self.workspace_active and self.pane_active and index == self.currentIndex() else self.idle_ink)
            painter.drawItemText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                                 option.palette, self.isTabEnabled(index), title, QPalette.ColorRole.WindowText)


class DocumentTabs(QWidget):
    newRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bar = DocumentTabBar()
        self.has_overflow = False
        self.pane_menu_always = False
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
        self.bar.currentChanged.connect(self.update_outline)

    def update_outline(self):
        self.update()
        if self.parentWidget() is not None:
            self.parentWidget().update()
            if self.parentWidget().parentWidget() is not None:
                self.parentWidget().parentWidget().update()

    def paint_outer_feet(self, surface):
        if not self.bar.workspace_active or self.bar.currentIndex() < 0:
            return
        if self.has_overflow:
            return
        # The row owns both outer feet, including the one in the existing gap
        # beside history navigation. No tab or button hit target is moved.
        origin = self.bar.mapTo(surface, QPoint(0, 0))
        clip = QRegion(surface.rect()).subtracted(QRegion(QRect(origin, self.bar.size())))
        if self.bar.join_sidebar:
            clip &= QRegion(QRect(origin.x(), 0, surface.width()-origin.x(), surface.height()))
        painter = QPainter(surface)
        painter.setClipRegion(clip)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.bar.active_fill)
        painter.translate(origin)
        painter.drawPath(self.bar.active_path(self.bar.currentIndex()))

    def set_tab_height(self, height):
        if self.bar.tab_height == height:
            return
        self.bar.tab_height = height
        self.bar.setExpanding(True)
        self.bar.setExpanding(False)
        self.bar.updateGeometry()
        self.updateGeometry()

    def set_leading_foot(self, enabled):
        # Keep the whole concave foot inside its pane, tangent to the divider.
        # The reserve is derived from the curve rather than a visual offset.
        inset = self.bar.hover_radius+self.bar.body_inset if enabled else 0
        if self.line.contentsMargins().left() != inset:
            self.line.setContentsMargins(inset, 0, 0, 0)
            self.fit()

    def fit(self):
        count = max(1, self.bar.count())
        available = self.width()-self.line.contentsMargins().left()
        budget = max(80, available-(74 if self.pane_menu_always else 42))
        width = max(124, min(228, budget//count))
        overflow = count*width > budget
        if overflow:
            budget = max(80, available-74)
        self.has_overflow = overflow
        if self.bar.tab_width != width:
            self.bar.tab_width = width
            # Re-layout the native tabs without recreating them or their views.
            self.bar.setExpanding(True)
            self.bar.setExpanding(False)
        desired = count*width
        self.bar.setFixedWidth(min(desired, budget))
        self.overflow.setVisible(overflow or self.pane_menu_always)
        self.bar.updateGeometry()
        self.update_outline()

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
        if hasattr(self, 'pane_menu'):self.pane_menu(menu)
        menu.open_at(self.overflow.mapToGlobal(self.overflow.rect().bottomLeft()))

    def select_from_list(self, index):
        self.bar.setCurrentIndex(index)
        # Selecting the already-current file must also leave a utility route.
        self.bar.tabBarClicked.emit(index)
