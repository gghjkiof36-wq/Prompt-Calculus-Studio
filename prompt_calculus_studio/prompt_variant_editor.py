"""Named text variants, with a separate native text document for each draft."""
import copy
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLineEdit, QPlainTextEdit, QSpinBox, QCheckBox
from .widgets import row, button, ComboBox, label, RoundMenu
from .prompt_group_editor import page, field, PageStack, mode_options
from . import prompt_groups as groups


class VariantEditor(QWidget):
    changed = Signal()

    def __init__(self, sequence, parent=None):
        super().__init__(parent); self.sequence=copy.deepcopy(sequence);self.loading=False;self.editors={}
        layout=QVBoxLayout(self);layout.setContentsMargins(0,0,0,0);layout.setSpacing(12)
        self.mode=ComboBox();mode_options(self.mode);self.mode.setAccessibleName('文字變體變量')
        self.random_repeats=QCheckBox('允許重複');self.random_repeats.setChecked(sequence.get('random_repeats',True))
        layout.addLayout(row(field('變量',self.mode),self.random_repeats,None))
        self.choose=ComboBox();self.choose.setAccessibleName('選擇文字變體')
        self.actions=button('更多',self.menu,'Quiet')
        layout.addLayout(row(self.choose,button('新增變體',self.add,'Quiet'),self.actions))
        self.name=QLineEdit();self.name.setAccessibleName('變體名稱')
        self.repeat=QSpinBox();self.repeat.setRange(1,groups.MAX_REPEAT);self.repeat.setSuffix(' 次');self.repeat.setMaximumWidth(112)
        self.repeat.setAccessibleName('變體重複次數')
        layout.addLayout(row(field('變體名稱',self.name),field('重複次數',self.repeat)))
        self.stack=PageStack();layout.addWidget(self.stack,1)
        self.mode.setCurrentIndex(self.mode.findData(sequence['mode']))
        self.mode.currentIndexChanged.connect(self.configure);self.random_repeats.toggled.connect(self.configure)
        self.choose.currentIndexChanged.connect(self.select);self.name.textEdited.connect(self.edit_metadata)
        self.repeat.valueChanged.connect(self.edit_metadata)
        self.refresh(sequence['selected']);self.configure()

    def refresh(self, selected):
        self.loading=True;self.choose.clear()
        for group in self.sequence['groups']:self.choose.addItem(group['name'],group['id'])
        self.choose.setCurrentIndex(self.choose.findData(selected));self.loading=False;self.select()

    def select(self):
        if self.loading or not self.choose.currentData():return
        ident=self.choose.currentData();self.sequence['selected']=ident;group=groups.selected(self.sequence)
        self.loading=True;self.name.setText(group['name']);self.repeat.setValue(group['repeat'])
        if ident not in self.editors:
            editor=QPlainTextEdit();editor.setMinimumHeight(240);editor.setPlainText(group['text'])
            editor.setAccessibleName('變體 Prompt');self.stack.addWidget(editor);self.editors[ident]=editor
            editor.textChanged.connect(lambda g=group,e=editor:self.edit_text(g,e))
        self.stack.setCurrentWidget(self.editors[ident]);self.loading=False

    def edit_text(self, group, editor):
        if self.loading:return
        group['text']=editor.toPlainText();self.changed.emit()

    def edit_metadata(self,*_):
        if self.loading:return
        group=groups.selected(self.sequence);group.update(name=self.name.text(),repeat=self.repeat.value())
        self.choose.setItemText(self.choose.currentIndex(),group['name']);self.changed.emit()

    def configure(self,*_):
        if self.loading:return
        self.sequence.update(mode=self.mode.currentData(),random_repeats=self.random_repeats.isChecked())
        self.random_repeats.setVisible(self.sequence['mode']=='random');self.changed.emit()

    def add(self):
        previous=groups.selected(self.sequence)
        group=groups.new_group(f'變體 {len(self.sequence["groups"])+1}',content_kind=previous.get('content_kind','tags'))
        self.sequence['groups'].append(group);self.refresh(group['id']);self.name.setFocus();self.name.selectAll();self.changed.emit()

    def move(self,delta):
        group=groups.selected(self.sequence);rows=self.sequence['groups'];at=rows.index(group)
        if not 0<=at+delta<len(rows):return
        rows[at],rows[at+delta]=rows[at+delta],rows[at];self.refresh(group['id']);self.changed.emit()

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
        menu.addAction('設為起始變體',self.set_start)
        menu.addAction('刪除變體',self.remove).setEnabled(len(self.sequence['groups'])>1)
        menu.exec(self.actions.mapToGlobal(self.actions.rect().bottomLeft()))

    def set_start(self):
        self.sequence['start']=self.choose.currentData();self.changed.emit()

    def value(self, content_kind):
        result=copy.deepcopy(self.sequence)
        for group in result['groups']:group['content_kind']=content_kind
        groups.validate(result);return result
