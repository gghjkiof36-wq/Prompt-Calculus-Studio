"""A note property draft: names and values first, type actions on demand."""
from PySide6.QtCore import Qt, QTimer, QEvent
from PySide6.QtWidgets import (QHBoxLayout, QVBoxLayout, QScrollArea, QLayout,
    QLineEdit, QPlainTextEdit, QCheckBox, QPushButton, QLabel, QDialogButtonBox, QWidget, QStackedLayout)
from .markdown_properties import frontmatter, property_edits
from .markdown_property_widgets import PropertyType, TagValues
from .ui_icons import icon
from .widgets import StudioDialog
from .theme import visual_tokens, window_appearance

KINDS = [('文字','text'),('清單','list'),('數字','number'),('核取方塊','checkbox'),('日期','date'),('日期與時間','datetime'),('標籤','tags')]


class PropertyValue(QWidget):
    def __init__(self, kind):
        super().__init__();self.kind=kind;self.previous='text';self.setObjectName('PropertyValue')
        self.stack=QStackedLayout(self);self.stack.setContentsMargins(0,0,0,0)
        self.single=QLineEdit();self.single.setAccessibleName('屬性內容');self.stack.addWidget(self.single)
        self.tags=TagValues();self.stack.addWidget(self.tags)
        self.check=QCheckBox();self.check.setAccessibleName('屬性內容');self.stack.addWidget(self.check)
        raw=QWidget();layout=QVBoxLayout(raw);layout.setContentsMargins(0,0,0,0);layout.setSpacing(4)
        self.raw_toggle=QPushButton('原文');self.raw_toggle.setObjectName('PropertyRaw');self.raw_toggle.setIcon(icon('chevron-right'))
        self.raw_toggle.setFixedHeight(32);layout.addWidget(self.raw_toggle,0,Qt.AlignmentFlag.AlignLeft)
        self.text=QPlainTextEdit();self.text.setAccessibleName('屬性原文');self.text.document().setDocumentMargin(0)
        self.text.setFixedHeight(86);layout.addWidget(self.text);self.text.hide();self.stack.addWidget(raw)
        self.raw_toggle.clicked.connect(self.toggle_raw);kind.currentIndexChanged.connect(self.sync)
        self.tags.layoutChanged.connect(self.update_height);self.sync()

    def sync(self):
        value=self.toPlainText();key=self.kind.currentData();self.previous=key
        self.stack.setCurrentIndex(2 if key=='checkbox' else 1 if key in ('list','tags') else 3 if key=='complex' else 0)
        self.setPlainText(value);self.update_height()

    def update_height(self):
        height=self.tags.heightForWidth(max(96,self.width())) if self.previous in ('list','tags') else 122 if self.previous=='complex' and not self.text.isHidden() else 32
        if self.height()!=height:self.setFixedHeight(height)

    def resizeEvent(self, event):super().resizeEvent(event);self.update_height()

    def event(self, event):
        handled=super().event(event)
        if event.type()==QEvent.Type.LayoutRequest and hasattr(self,'previous'):self.update_height()
        return handled

    def toggle_raw(self):
        self.text.setVisible(self.text.isHidden());self.raw_toggle.setIcon(icon('chevron-down' if not self.text.isHidden() else 'chevron-right'));self.update_height()

    def setPlainText(self, text):
        self.single.setText(text);self.tags.setPlainText(text);self.text.setPlainText(text);self.check.setChecked(text.lower()=='true')

    def toPlainText(self):
        if self.previous=='checkbox':return 'true' if self.check.isChecked() else 'false'
        if self.previous in ('list','tags'):return self.tags.toPlainText()
        return self.text.toPlainText() if self.previous=='complex' else self.single.text()

    def setReadOnly(self, value):self.text.setReadOnly(value);self.single.setReadOnly(value);self.check.setEnabled(not value)


class PropertyEditor(StudioDialog):
    def __init__(self, surface):
        super().__init__(surface);self.surface=surface;self.rows=[];self.row_widgets=[];self.row_frames=[];self.removed=[]
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle('筆記屬性');self.resize(660,490);self.setMinimumSize(560,340)
        self.body.setSpacing(12)
        t=visual_tokens(window_appearance(surface))
        self.fields=QWidget();self.fields.setObjectName('PropertyFields')
        self.fields.setStyleSheet(f'''QWidget#PropertyFields {{background:transparent;}}
            QWidget#PropertyRow {{border:0;border-bottom:1px solid {t['line']};background:transparent;}}
            QWidget#PropertyFields QLineEdit {{background:transparent;border:1px solid transparent;padding:3px 6px;border-radius:4px;min-height:0;}}
            QWidget#PropertyFields QLineEdit:hover {{background:{t['hover']};}}
            QWidget#PropertyFields QLineEdit:focus {{background:{t['surface']};border-color:{t['accent']};}}
            QWidget#PropertyFields QLineEdit#PropertyName {{color:{t['secondary']};}}
            QWidget#PropertyFields QCheckBox {{padding:0 6px;}}
            QWidget#PropertyFields QToolButton {{background:transparent;border:1px solid transparent;padding:0;border-radius:4px;}}
            QWidget#PropertyFields QToolButton:hover {{background:{t['hover']};}}
            QWidget#PropertyFields QToolButton:focus {{border-color:{t['accent']};}}
            QWidget#PropertyChip {{background:{t['hover']};border:0;border-radius:4px;}}
            QWidget#PropertyChip QToolButton {{padding:0;border:0;background:transparent;}}
            QWidget#PropertyChip QToolButton:focus {{background:{t['selected']};}}
            QWidget#PropertyFields QPlainTextEdit {{padding:6px 8px;border:0;background:{t['surface']};border-radius:4px;}}
            QWidget#PropertyFields QPushButton#PropertyRaw {{background:transparent;border:0;text-align:left;padding:0 6px;}}
            QWidget#PropertyFields QPushButton#PropertyRaw:hover {{background:{t['hover']};}}
        ''')
        self.list_layout=QVBoxLayout(self.fields);self.list_layout.setContentsMargins(0,0,4,0);self.list_layout.setSpacing(0)
        self.list_layout.setSizeConstraint(QLayout.SizeConstraint.SetMinAndMaxSize);self.list_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setWidget(self.fields)
        self.scroll.setFrameShape(QScrollArea.Shape.NoFrame);self.scroll.setStyleSheet('QScrollArea {background:transparent;border:0;}')
        self.body.addWidget(self.scroll,1)
        self.message=QLabel();self.message.setWordWrap(True);self.message.hide();self.body.addWidget(self.message)
        footer=QHBoxLayout();footer.setSpacing(8)
        add=QPushButton('新增屬性');add.setIcon(icon('plus'));add.setStyleSheet('QPushButton {background:transparent;border:0;padding:6px 4px;}')
        add.clicked.connect(lambda:self.add_row(focus=True));footer.addWidget(add);footer.addStretch()
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('套用');self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('Primary')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.submit);self.buttons.rejected.connect(self.reject);footer.addWidget(self.buttons);self.body.addLayout(footer)
        self.reload()

    def reload(self):
        self.snapshot=self.surface.page.buffer.snapshot();_,properties,error=frontmatter(self.snapshot.text)
        for frame in self.row_frames:self.list_layout.removeWidget(frame);frame.deleteLater()
        self.rows=[];self.row_widgets=[];self.row_frames=[];self.removed=[]
        for prop in properties:self.add_row(prop)
        self.show_error(error or '');self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not error)

    def show_error(self, text):self.message.setText(text);self.message.setVisible(bool(text))

    def focus_field(self, control):
        control.setFocus()
        QTimer.singleShot(0,self,lambda:self.scroll.ensureWidgetVisible(control,0,12))

    def add_row(self, prop=None, *, focus=False):
        frame=QWidget();frame.setObjectName('PropertyRow');layout=QHBoxLayout(frame);layout.setContentsMargins(0,6,0,6);layout.setSpacing(6)
        name=QLineEdit(prop.name if prop else '');name.setObjectName('PropertyName');name.setAccessibleName('屬性名稱');name.setFixedSize(154,32)
        kind=PropertyType(lambda:self.remove_row(name))
        for label,key in KINDS:kind.addItem(label,key)
        kind.setCurrentIndex(0);value=PropertyValue(kind)
        if prop:
            if not prop.editable:kind.addItem('原文','complex')
            kind.setCurrentIndex(max(0,kind.findData(prop.kind)))
            value.setPlainText('\n'.join(prop.value) if isinstance(prop.value,list) else str(prop.value).lower() if isinstance(prop.value,bool) else str(prop.value if prop.value is not None else ''))
            if not prop.editable:name.setReadOnly(True);kind.setEnabled(False);value.setReadOnly(True);value.setPlainText(prop.raw)
        for control in (kind,name):layout.addWidget(control,0,Qt.AlignmentFlag.AlignTop)
        layout.addWidget(value,1,Qt.AlignmentFlag.AlignTop)
        self.rows.append((prop,name,kind,value));self.row_widgets.append((name,kind,value));self.row_frames.append(frame)
        self.list_layout.addWidget(frame);frame.show()
        if focus:QTimer.singleShot(0,self,lambda:self.focus_field(name))

    def remove_row(self, name):
        index=next((i for i,row in enumerate(self.rows) if row[1] is name),None)
        if index is None:return
        prop=self.rows[index][0]
        if prop and not prop.editable:return
        if prop:self.removed.append(prop.name)
        self.rows.pop(index);self.row_widgets.pop(index);frame=self.row_frames.pop(index)
        self.list_layout.removeWidget(frame);frame.hide();frame.deleteLater()

    def submit(self):
        invalid=None
        try:
            updates=[(name,name,'text',None) for name in self.removed]
            for prop,name,kind,control in self.rows:
                if prop and not prop.editable:continue
                invalid=name;value=control.toPlainText();key=kind.currentData();title=name.text().strip()
                if not title:raise ValueError('屬性名稱不能空白。')
                if key in ('list','tags'):value=[v.strip() for v in value.splitlines() if v.strip()]
                elif key=='checkbox':value=value.lower()=='true'
                elif key=='number':
                    from .markdown_properties import scalar
                    actual,value=scalar(value)
                    if actual!='number':invalid=control.single;raise ValueError(title+'必須是數字。')
                if prop and prop.value is None and value=='':value=prop.value
                if prop and (title,key,value)==(prop.name,prop.kind,prop.value):continue
                updates.append((prop.name if prop else None,title,key,value))
            invalid=None;edits=property_edits(self.snapshot.text,updates)
            self.surface.page.buffer.apply(self.snapshot,edits);self.surface.refresh();self.accept()
        except (ValueError,OSError) as error:
            self.show_error(str(error))
            if invalid:
                QTimer.singleShot(0,self,lambda c=invalid:self.focus_field(c))
