"""Quiet property type controls and editable, wrapping lists of values."""
from PySide6.QtCore import Qt, Signal, QSize, QEvent
from PySide6.QtWidgets import QToolButton, QMenu, QWidget, QHBoxLayout, QLineEdit, QSizePolicy
from .ui_icons import icon

TYPE_ICONS = dict(text='type',list='list',number='list-ordered',checkbox='task',date='clock',datetime='clock',tags='tag',complex='source-code')


class PropertyType(QToolButton):
    currentIndexChanged = Signal(int)
    def __init__(self, remove, parent=None):
        super().__init__(parent);self.items=[];self.index=0;self.remove=remove
        self.setObjectName('PropertyType');self.setFixedSize(28,32);self.setIconSize(QSize(16,16))
        self.clicked.connect(self.open_menu)

    def addItem(self, label, key):self.items.append((label,key))
    def count(self):return len(self.items)
    def findData(self, key):return next((i for i,item in enumerate(self.items) if item[1]==key),-1)
    def currentData(self):return self.items[self.index][1]
    def setCurrentIndex(self, index):
        changed=self.index!=index;self.index=index;label,key=self.items[index]
        self.setIcon(icon(TYPE_ICONS[key]));self.setToolTip(label);self.setAccessibleName('屬性型別：'+label)
        if changed:self.currentIndexChanged.emit(index)

    def open_menu(self):
        self.menu=QMenu(self)
        for index,(label,key) in enumerate(self.items):
            action=self.menu.addAction(icon(TYPE_ICONS[key]),label);action.setCheckable(True);action.setChecked(index==self.index)
            action.triggered.connect(lambda checked=False,i=index:self.setCurrentIndex(i))
        self.menu.addSeparator();self.menu.addAction(icon('trash'),'刪除屬性',self.remove)
        self.menu.popup(self.mapToGlobal(self.rect().bottomLeft()))


class TagValues(QWidget):
    layoutChanged=Signal()
    def __init__(self):
        super().__init__();self.values=[];self.editing=None;self.chips=[]
        self.setObjectName('PropertyTags');self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.input=QLineEdit(self);self.input.setObjectName('PropertyTagInput');self.input.setFixedSize(72,28)
        self.input.setAccessibleName('新增或修改項目');self.input.installEventFilter(self)
        self.input.returnPressed.connect(self.commit);self.input.textEdited.connect(self.resize_input);self.rebuild()
        self.setFocusProxy(self.input)

    def resize_input(self):
        self.input.setFixedWidth(max(72,min(220,self.input.fontMetrics().horizontalAdvance(self.input.text())+20)))
        self.arrange(self.width(),True);self.updateGeometry();self.layoutChanged.emit()

    def sizeHint(self):return QSize(260,self.heightForWidth(260))
    def minimumSizeHint(self):return QSize(96,32)
    def heightForWidth(self,width):return self.arrange(width,False)
    def resizeEvent(self,event):super().resizeEvent(event);self.arrange(self.width(),True)
    def arrange(self,width,apply):
        x=4;y=2
        for control in self.chips+[self.input]:
            size=control.sizeHint().boundedTo(QSize(max(40,width-8),28))
            if control is self.input:size=QSize(control.width(),28)
            if x>4 and x+size.width()>width-4:x=4;y+=32
            if apply:control.setGeometry(x,y,size.width(),28)
            x+=size.width()+4
        return y+30

    def setPlainText(self, text):
        self.values=[line.strip() for line in text.splitlines() if line.strip()];self.editing=None;self.input.clear();self.rebuild()

    def toPlainText(self):
        values=list(self.values);draft=self.input.text().strip()
        if draft:
            if self.editing is None:values.append(draft)
            else:values[self.editing]=draft
        return '\n'.join(values)

    def commit(self):
        text=self.input.text().strip()
        if not text:return
        additions=[line.strip() for line in text.splitlines() if line.strip()]
        if self.editing is None:self.values.extend(additions)
        else:self.values[self.editing:self.editing+1]=additions
        self.editing=None;self.input.clear();self.rebuild()

    def edit(self, index):
        self.commit();self.editing=index;self.input.setText(self.values[index]);self.resize_input();self.input.setFocus();self.input.selectAll()

    def remove(self, index):
        self.commit();self.values.pop(index);self.editing=None;self.rebuild();self.input.setFocus()

    def rebuild(self):
        for chip in self.chips:chip.hide();chip.deleteLater()
        self.chips=[]
        for index,value in enumerate(self.values):
            chip=QWidget(self);chip.setObjectName('PropertyChip');layout=QHBoxLayout(chip);layout.setContentsMargins(6,0,3,0);layout.setSpacing(2)
            label=QToolButton();label.setText(value);label.setToolTip(value);label.setMaximumWidth(220);label.setFixedHeight(28)
            label.setAccessibleName('修改 '+value);label.clicked.connect(lambda checked=False,i=index:self.edit(i));layout.addWidget(label)
            close=QToolButton();close.setIcon(icon('close'));close.setIconSize(QSize(11,11));close.setFixedSize(20,24)
            close.setAccessibleName('移除 '+value);close.setToolTip('移除');close.clicked.connect(lambda checked=False,i=index:self.remove(i));layout.addWidget(close)
            self.chips.append(chip);chip.show()
        self.input.show();self.resize_input();self.updateGeometry()

    def eventFilter(self, watched, event):
        if event.type()==QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):self.commit();return True
            if event.key()==Qt.Key.Key_Escape and (self.input.text() or self.editing is not None):
                self.input.clear();self.editing=None;self.resize_input();return True
            if event.key()==Qt.Key.Key_Backspace and not self.input.text() and self.values:self.edit(len(self.values)-1);return True
        if event.type()==QEvent.Type.FocusOut:self.commit()
        return super().eventFilter(watched,event)
