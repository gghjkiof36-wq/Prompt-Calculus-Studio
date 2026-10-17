"""A single native tab strip with an adjacent new-tab button and overflow."""
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFont, QFontMetrics, QPalette, QColor, QPainter, QPainterPath
from PySide6.QtWidgets import QWidget, QTabBar, QHBoxLayout, QSizePolicy, QStylePainter, QStyleOptionTab, QStyle
from .widgets import button, RoundMenu
from .ui_icons import icon


class DocumentTabBar(QTabBar):
    def __init__(self):
        super().__init__()
        self.tab_width = 228
        self.tab_height = 40
        self.workspace_active = True
        self.active_fill = QColor('#151819')
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
                foot,shoulder=8,14
                left,right=rect.left()+foot,rect.right()-foot
                top,bottom=rect.top(),rect.bottom()+1
                path=QPainterPath()
                path.moveTo(rect.left(),bottom)
                path.quadTo(left,bottom,left,bottom-foot)
                path.lineTo(left,top+shoulder)
                path.quadTo(left,top,left+shoulder,top)
                path.lineTo(right-shoulder,top)
                path.quadTo(right,top,right,top+shoulder)
                path.lineTo(right,bottom-foot)
                path.quadTo(right,bottom,rect.right()+1,bottom)
                path.closeSubpath()
                painter.save()
                painter.setRenderHint(QPainter.RenderHint.Antialiasing)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(self.active_fill)
                painter.drawPath(path)
                painter.restore()
            else:
                painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)
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
        self.line.setSpacing(3)
        self.line.addWidget(self.bar)
        self.line.addWidget(self.plus)
        self.line.addWidget(self.overflow)
        self.line.addStretch(1)
        self.bar.tabMoved.connect(lambda *_: self.fit())

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
        self.bar.setTabToolTip(index, path + ('\n未保存' if dirty else '\n已保留' if retained else '\n瀏覽分頁；雙擊保留'))
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
