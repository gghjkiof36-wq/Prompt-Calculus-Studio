"""A single native tab strip with an adjacent new-tab button and overflow."""
from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QWidget, QTabBar, QHBoxLayout, QSizePolicy, QStylePainter, QStyleOptionTab, QStyle
from .widgets import button, RoundMenu
from .ui_icons import icon


class DocumentTabBar(QTabBar):
    def __init__(self):
        super().__init__()
        self.cap = 192
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
        size.setWidth(min(self.cap, max(112, size.width())))
        size.setHeight(38)
        return size

    def paintEvent(self, event):
        painter = QStylePainter(self)
        for index in range(self.count()):
            option = QStyleOptionTab()
            self.initStyleOption(option, index)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabShape, option)
            font = QFont(self.font())
            font.setItalic(self.tabData(index) in self.preview_keys)
            painter.setFont(font)
            option.fontMetrics = QFontMetrics(font)
            painter.drawControl(QStyle.ControlElement.CE_TabBarTabLabel, option)


class DocumentTabs(QWidget):
    newRequested = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.bar = DocumentTabBar()
        self.plus = button('', self.newRequested.emit, 'Quiet')
        self.plus.setIcon(icon('plus'))
        self.plus.setAccessibleName('開啟新分頁')
        self.plus.setToolTip('開啟新分頁 (Ctrl+T)')
        self.plus.setFixedSize(32, 32)
        self.overflow = button('', self.show_list, 'Quiet')
        self.overflow.setIcon(icon('chevron-down'))
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

    def fit(self):
        desired = sum(self.bar.tabSizeHint(i).width() for i in range(self.bar.count()))
        budget = max(80, self.width()-70)
        self.bar.setFixedWidth(min(desired, budget))
        self.overflow.setVisible(desired > budget)
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
            action = menu.addAction(self.bar.tabText(i), lambda checked=False, n=i: self.bar.setCurrentIndex(n))
            action.setCheckable(True)
            action.setChecked(i == self.bar.currentIndex())
        menu.open_at(self.overflow.mapToGlobal(self.overflow.rect().bottomLeft()))
