"""Searchable long enum lists; filtering never edits the selected parameter."""
from PySide6.QtCore import Qt, QEvent, QPoint, QPersistentModelIndex
from PySide6.QtWidgets import QFrame, QVBoxLayout, QLineEdit, QListWidget, QListWidgetItem, QLabel
from shiboken6 import isValid
from .widgets import ComboBox, rounded_mask


class _ChoicesPopup(QFrame):
    def __init__(self, owner):
        super().__init__(owner, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.owner = owner
        self.setObjectName('StageParameterChoicesPopup')
        self.setFont(owner.font())
        # This popup is a separate native window. An alpha-backed QFrame can
        # omit its stylesheet surface, exposing the editor through list gaps.
        # Use the same opaque, rounded-mask surface as the ordinary ComboBox.
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground)
        self.setStyleSheet('''
            #StageParameterChoicesPopup {background:#222324;border:1px solid #414342;border-radius:10px;}
            #StageParameterChoicesPopup QLineEdit {background:#171819;color:#edeae5;border:1px solid #414342;border-radius:7px;padding:0 10px;min-height:36px;}
            #StageParameterChoicesPopup QListWidget {background:#222324;color:#edeae5;border:0;outline:0;}
            #StageParameterChoicesPopup QListWidget::item {padding:8px 10px;border-radius:6px;}
            #StageParameterChoicesPopup QListWidget::item:selected {background:#393c39;}
            #StageParameterChoicesPopup QListWidget::item:hover {background:#303332;}
            #StageParameterChoicesPopup QLabel {color:#aaa9a4;border:0;background:transparent;}
        ''')
        body = QVBoxLayout(self)
        body.setContentsMargins(10, 10, 10, 10)
        body.setSpacing(8)
        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋選項 / Search options')
        self.search.setAccessibleName('搜尋選項 / Search options')
        self.items = QListWidget()
        self.items.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.items.setTextElideMode(Qt.TextElideMode.ElideMiddle)
        self.empty = QLabel('沒有符合的選項 / No matching options')
        body.addWidget(self.search)
        body.addWidget(self.items, 1)
        body.addWidget(self.empty)
        self.indices = [QPersistentModelIndex(owner.model().index(row, owner.modelColumn(), owner.rootModelIndex()))
                        for row in range(owner.count())]
        self.selected = QPersistentModelIndex(owner.model().index(owner.currentIndex(), owner.modelColumn(), owner.rootModelIndex()))
        self.search.textChanged.connect(self.filter_items)
        self.items.itemClicked.connect(self.choose)
        for widget in (self.search, self.items):
            widget.installEventFilter(self)
        self.filter_items('')

    def filter_items(self, text):
        query = text.casefold()
        self.items.clear()
        for index in self.indices:
            if not index.isValid():
                continue
            title = str(index.data(Qt.ItemDataRole.DisplayRole) or '')
            if query not in title.casefold():
                continue
            item = QListWidgetItem(title)
            item.setToolTip(title)
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setFlags(index.flags() & (Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable))
            self.items.addItem(item)
            if index == self.selected:
                self.items.setCurrentItem(item)
        self.empty.setVisible(self.items.count() == 0)
        if self.items.currentItem():
            self.items.scrollToItem(self.items.currentItem())

    def open(self):
        owner = self.owner
        bounds = owner.screen().availableGeometry().adjusted(8, 8, -8, -8)
        self.resize(min(max(owner.width(), 340), bounds.width()), min(360, bounds.height()))
        below = owner.mapToGlobal(QPoint(0, owner.height()))
        above = owner.mapToGlobal(QPoint(0, 0)).y() - self.height()
        y = below.y() if below.y() + self.height() <= bounds.bottom() + 1 else above
        self.move(max(bounds.left(), min(below.x(), bounds.right() - self.width() + 1)),
                  max(bounds.top(), min(y, bounds.bottom() - self.height() + 1)))
        self.show()
        self.search.setFocus(Qt.FocusReason.PopupFocusReason)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rounded_mask(self, 10)

    def choose(self, item):
        if item is None:
            return
        index = item.data(Qt.ItemDataRole.UserRole)
        owner = self.owner
        valid = (index.isValid() and index.model() is owner.model()
                 and bool(index.flags() & Qt.ItemFlag.ItemIsEnabled)
                 and bool(index.flags() & Qt.ItemFlag.ItemIsSelectable))
        row = index.row() if valid else -1
        self.hide()
        if row < 0:
            return
        owner.setCurrentIndex(row)
        if isValid(owner):
            owner.activated.emit(row)
            if isValid(owner):
                owner.textActivated.emit(owner.currentText())

    def eventFilter(self, watched, event):
        keys = (Qt.Key.Key_Escape, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Down, Qt.Key.Key_Up)
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in keys:
            event.accept()
            return True
        if event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key.Key_Escape:
                self.hide()
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self.choose(self.items.currentItem())
                return True
            if key in (Qt.Key.Key_Down, Qt.Key.Key_Up):
                step = 1 if key == Qt.Key.Key_Down else -1
                row = self.items.currentRow()
                row = row + step if row >= 0 else (0 if step > 0 else self.items.count() - 1)
                while 0 <= row < self.items.count():
                    item = self.items.item(row)
                    if item.flags() & Qt.ItemFlag.ItemIsEnabled and item.flags() & Qt.ItemFlag.ItemIsSelectable:
                        self.items.setCurrentRow(row)
                        self.items.scrollToItem(item)
                        break
                    row += step
                return True
        return super().eventFilter(watched, event)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.owner._choices_popup is self:
            self.owner._choices_popup = None
            self.owner.hidePopup()
            self.owner.setFocus(Qt.FocusReason.PopupFocusReason)
        self.deleteLater()


class ParameterChoices(ComboBox):
    SEARCH_THRESHOLD = 20

    def __init__(self, *args):
        super().__init__(*args)
        self._choices_popup = None

    def find_value(self, value):
        """QComboBox.findData coerces text and numbers; parameter values cannot."""
        return next((i for i in range(self.count())
                     if type(self.itemData(i)) is type(value) and self.itemData(i) == value), -1)

    def showPopup(self):
        if self.count() < self.SEARCH_THRESHOLD:
            super().showPopup()
        elif self._choices_popup is None:
            self._choices_popup = _ChoicesPopup(self)
            self._choices_popup.open()

    def hidePopup(self):
        if self._choices_popup is not None:
            self._choices_popup.hide()
        super().hidePopup()

    def keyPressEvent(self, event):
        if self.count() >= self.SEARCH_THRESHOLD:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_F4) or (
                    event.key() == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier):
                self.showPopup()
                event.accept()
                return
            if event.text().isprintable() and not event.modifiers() & (
                    Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
                self.showPopup()
                self._choices_popup.search.setText(event.text())
                event.accept()
                return
        super().keyPressEvent(event)
