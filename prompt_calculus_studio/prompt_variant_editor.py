"""Text variant drafts with a compact editor and a separate management card."""
import copy
from PySide6.QtCore import Qt, Signal, QEvent, QSize, QRect
from PySide6.QtGui import QIcon, QPalette
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QPlainTextEdit, QLineEdit, QSpinBox, QCheckBox,
    QSizePolicy, QListWidgetItem, QStyledItemDelegate, QStyleOptionViewItem, QStyle)
from .widgets import row, button, ComboBox, RoundMenu, label
from .prompt_group_editor import page, field, PageStack, mode_options, tool
from .prompt_editor import ReorderableList
from .ui_icons import icon
from . import prompt_groups as groups


REPEAT_ROLE=Qt.ItemDataRole.UserRole+1
START_ROLE=Qt.ItemDataRole.UserRole+2


class VariantDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        return QSize(240,max(42,option.fontMetrics.height()+20))

    @staticmethod
    def regions(rect,metrics,repeat):
        content=rect.adjusted(12,0,-12,0)
        value_width=max(44,metrics.horizontalAdvance(f'{repeat} 次')+8)
        marker=QRect(content.left(),content.center().y()-10,20,20)
        value=QRect(content.right()-value_width+1,content.top(),value_width,content.height())
        name=QRect(marker.right()+9,content.top(),max(1,value.left()-marker.right()-21),content.height())
        return dict(marker=marker,name=name,repeat=value)

    def paint(self,painter,option,index):
        native=QStyleOptionViewItem(option);self.initStyleOption(native,index)
        name=native.text;native.text='';native.icon=QIcon()
        self.parent().style().drawControl(QStyle.ControlElement.CE_ItemViewItem,native,painter,self.parent())
        repeat=index.data(REPEAT_ROLE) or 1
        regions=self.regions(option.rect,option.fontMetrics,repeat)
        painter.save();painter.setFont(option.font)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        color=option.palette.color(QPalette.ColorRole.HighlightedText if selected else QPalette.ColorRole.Text)
        painter.setPen(color)
        icon('pin' if index.data(START_ROLE) else 'grip-vertical',color).paint(painter,regions['marker'])
        painter.drawText(regions['name'],Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                         option.fontMetrics.elidedText(name,Qt.TextElideMode.ElideRight,regions['name'].width()))
        painter.drawText(regions['repeat'],Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,f'{repeat} 次')
        painter.restore()


class VariantList(ReorderableList):
    def __init__(self):
        super().__init__();self.setItemDelegate(VariantDelegate(self))

    def fit_rows(self):
        row_height=self.sizeHintForRow(0) if self.count() else max(42,self.fontMetrics().height()+20)
        self.setFixedHeight(max(3,min(6,self.count()))*max(1,row_height)+self.frameWidth()*2+4)

    def row_regions(self,item):
        return VariantDelegate.regions(self.visualItemRect(item),self.fontMetrics(),item.data(REPEAT_ROLE) or 1)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self.fit_rows()


class VariantEditor(QWidget):
    changed = Signal()
    layoutChanged = Signal()

    def __init__(self, sequence, parent=None):
        super().__init__(parent)
        self.sequence=copy.deepcopy(sequence);self.loading=False;self.editors={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        # The host places this beside the material name. Keep detached controls
        # hidden until the host installs them, so they cannot overlay the text.
        self.choose=ComboBox(self);self.choose.setAccessibleName('選擇文字變體')
        self.choose.setMinimumContentsLength(3);self.choose.setMinimumWidth(96)
        self.choose.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed);self.choose.hide()
        self.mode=ComboBox();mode_options(self.mode);self.mode.setAccessibleName('文字變體變量')
        self.mode.setMaximumWidth(190)
        self.random_repeats=QCheckBox('允許重複');self.random_repeats.setChecked(sequence.get('random_repeats',True))
        layout.addLayout(row(field('變量',self.mode),self.random_repeats,None))
        self.stack=PageStack();layout.addWidget(self.stack,1)
        self.management,management=page();self.management.setParent(self);self.management.hide()
        self.repeat=QSpinBox();self.repeat.setRange(1,groups.MAX_REPEAT);self.repeat.setSuffix(' 次')
        self.repeat.setMaximumWidth(112);self.repeat.setAccessibleName('變體重複次數')
        management.addWidget(label('拖動排序；雙擊編輯名稱與次數','Subtle',True))
        self.listing=VariantList();self.listing.enable_reordering()
        self.listing.setAccessibleName('變體順序')
        self.listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.listing.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.listing.setToolTip('單擊切換變體；雙擊編輯名稱與重複次數。')
        management.addWidget(self.listing)
        self.details,details=page();management.addWidget(self.details);self.details.hide()
        self.name=QLineEdit();self.name.setAccessibleName('變體名稱');self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        details.addLayout(row(field('變體名稱',self.name),field('重複次數',self.repeat)))
        self.collapse_details=button('收合編輯',self.close_details,'Quiet');self.collapse_details.setIcon(icon('chevron-up'))
        details.addLayout(row(None,self.collapse_details))
        self.pin=tool('pin','設為起始變體',self.set_start)
        self.actions=tool('more-horizontal','更多變體操作',self.menu)
        management.addLayout(row(self.pin,None,self.actions))
        management.addStretch(1)
        self.mode.setCurrentIndex(self.mode.findData(sequence['mode']))
        self.mode.currentIndexChanged.connect(self.configure);self.random_repeats.toggled.connect(self.configure)
        self.choose.currentIndexChanged.connect(self.select)
        self.repeat.valueChanged.connect(self.edit_metadata)
        self.name.textEdited.connect(self.edit_metadata)
        self.listing.currentItemChanged.connect(self.select_from_list)
        self.listing.itemDoubleClicked.connect(self.open_details)
        self.listing.itemActivated.connect(self.open_details)
        self.listing.reorderRequested.connect(self.reorder)
        self.refresh(sequence['selected']);self.configure()

    def refresh(self, selected):
        self.loading=True;self.choose.clear();self.listing.clear()
        for group in self.sequence['groups']:
            self.choose.addItem(group['name'],group['id'])
            item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,group['id']);self.listing.addItem(item)
            self.update_row(group,item)
        self.listing.fit_rows()
        self.choose.setCurrentIndex(self.choose.findData(selected));self.loading=False;self.select()

    def update_row(self,group,item):
        start=group['id']==self.sequence['start']
        item.setText(group['name']);item.setData(REPEAT_ROLE,group['repeat']);item.setData(START_ROLE,start)
        item.setIcon(icon('pin' if start else 'grip-vertical'))
        item.setData(Qt.ItemDataRole.AccessibleTextRole,f'{group["name"]}，{group["repeat"]} 次')
        item.setData(Qt.ItemDataRole.AccessibleDescriptionRole,'起始變體' if start else '')
        item.setToolTip(group['name']+('\n起始變體' if start else '')+'\n'+group['text'])

    def select_from_list(self,current,*_):
        if not self.loading and current:
            self.choose.setCurrentIndex(self.choose.findData(current.data(Qt.ItemDataRole.UserRole)))

    def select(self):
        if self.loading or not self.choose.currentData():return
        ident=self.choose.currentData();self.sequence['selected']=ident;group=groups.selected(self.sequence)
        self.loading=True;self.repeat.setValue(group['repeat']);self.name.setText(group['name'])
        if ident not in self.editors:
            editor=QPlainTextEdit();editor.setMinimumHeight(240);editor.setPlainText(group['text'])
            editor.setPlaceholderText('輸入這個變體的 Prompt');editor.setAccessibleName('變體 Prompt')
            self.stack.addWidget(editor);self.editors[ident]=editor
            editor.textChanged.connect(lambda g=group,e=editor:self.edit_text(g,e))
        self.stack.setCurrentWidget(self.editors[ident]);self.listing.setCurrentRow(self.choose.currentIndex())
        self.loading=False;self.update_controls();self.close_details()

    def open_details(self,*_):
        self.details.show();self.name.setFocus();self.name.selectAll();self.layoutChanged.emit()

    def close_details(self):
        if not self.details.isHidden():self.details.hide();self.layoutChanged.emit()

    def update_controls(self):
        at=self.choose.currentIndex();count=self.choose.count();current=groups.selected(self.sequence)
        start=next(group['name'] for group in self.sequence['groups'] if group['id']==self.sequence['start'])
        self.choose.setToolTip(f'{current["name"]} · 第 {at+1} / {count} 個變體\n起始變體：{start}')
        self.choose.setAccessibleDescription(f'第 {at+1} / {count} 個變體；起始變體：{start}')
        pinned=current['id']==self.sequence['start']
        self.pin.setToolTip('目前是起始變體' if pinned else '設為起始變體');self.pin.setEnabled(not pinned)

    def navigate(self,delta):
        index=self.choose.currentIndex()+delta
        if 0<=index<self.choose.count():self.choose.setCurrentIndex(index)

    def edit_text(self, group, editor):
        if self.loading:return
        group['text']=editor.toPlainText()
        for index in range(self.listing.count()):
            item=self.listing.item(index)
            if item.data(Qt.ItemDataRole.UserRole)==group['id']:
                self.update_row(group,item);break
        self.changed.emit()

    def edit_metadata(self,*_):
        if self.loading:return
        group=groups.selected(self.sequence);group.update(name=self.name.text(),repeat=self.repeat.value())
        self.choose.setItemText(self.choose.currentIndex(),group['name'])
        self.update_row(group,self.listing.currentItem());self.update_controls();self.changed.emit()

    def configure(self,*_):
        if self.loading:return
        self.sequence.update(mode=self.mode.currentData(),random_repeats=self.random_repeats.isChecked())
        self.random_repeats.setVisible(self.sequence['mode']=='random');self.layoutChanged.emit();self.changed.emit()

    def add(self):
        if len(self.sequence['groups'])>=groups.MAX_GROUPS:return
        previous=groups.selected(self.sequence);names={group['name'] for group in self.sequence['groups']}
        number=len(names)+1
        while f'變體 {number}' in names:number+=1
        group=groups.new_group(f'變體 {number}',content_kind=previous.get('content_kind','tags'))
        self.sequence['groups'].append(group);self.refresh(group['id']);self.stack.currentWidget().setFocus();self.changed.emit()

    def reorder(self,before,after):
        rows=self.sequence['groups']
        if [group['id'] for group in rows]!=before or len(after)!=len(before) or set(before)!=set(after):return
        mapping={group['id']:group for group in rows};rows[:]=[mapping[ident] for ident in after]
        self.refresh(self.choose.currentData());self.changed.emit()

    def move(self,delta):
        group=groups.selected(self.sequence);before=[value['id'] for value in self.sequence['groups']];at=before.index(group['id'])
        if not 0<=at+delta<len(before):return
        after=list(before);after[at],after[at+delta]=after[at+delta],after[at];self.reorder(before,after)

    def remove(self):
        rows=self.sequence['groups'];ident=self.choose.currentData()
        if len(rows)<2:return
        at=next(i for i,v in enumerate(rows) if v['id']==ident)
        replacement=rows[(at+1)%len(rows)]['id'];groups.remove(self.sequence,ident,replacement)
        if ident in self.editors:
            editor=self.editors.pop(ident);self.stack.removeWidget(editor);editor.deleteLater()
        self.refresh(replacement);self.changed.emit()

    def menu(self):
        menu=RoundMenu(self);at=self.choose.currentIndex()
        menu.addAction('上移',lambda:self.move(-1)).setEnabled(at>0)
        menu.addAction('下移',lambda:self.move(1)).setEnabled(at+1<len(self.sequence['groups']))
        menu.addAction('刪除變體',self.remove).setEnabled(len(self.sequence['groups'])>1)
        menu.exec(self.actions.mapToGlobal(self.actions.rect().bottomLeft()))

    def set_start(self):
        self.sequence['start']=self.choose.currentData();self.refresh(self.choose.currentData());self.changed.emit()

    def value(self, content_kind):
        result=copy.deepcopy(self.sequence)
        for group in result['groups']:group['content_kind']=content_kind
        groups.validate(result);return result
