"""A per-note property draft; only explicitly edited scalar ranges are saved."""
from PySide6.QtCore import Qt,QEvent
from PySide6.QtWidgets import (QDialog,QVBoxLayout,QHBoxLayout,QTableWidget,QHeaderView,
    QLineEdit,QComboBox,QPlainTextEdit,QCheckBox,QPushButton,QLabel,QDialogButtonBox,QWidget,QStackedLayout)
from .markdown_properties import frontmatter,property_edits
from .widgets import StudioDialog

KINDS=[('文字','text'),('清單','list'),('數字','number'),('核取方塊','checkbox'),('日期','date'),('日期與時間','datetime'),('標籤','tags')]


class PropertyValue(QWidget):
    def __init__(self,kind):
        super().__init__();self.kind=kind;self.stack=QStackedLayout(self);self.stack.setContentsMargins(0,0,0,0)
        self.text=QPlainTextEdit();self.text.setAccessibleName('屬性內容');self.stack.addWidget(self.text)
        self.text.setStyleSheet('QPlainTextEdit {padding:5px 7px;}');self.text.document().setDocumentMargin(0)
        self.check=QCheckBox('已勾選');self.check.setAccessibleName('核取方塊屬性內容');self.stack.addWidget(self.check)
        kind.currentIndexChanged.connect(self.sync);self.sync()
    def sync(self):
        value=self.kind.currentData();self.stack.setCurrentIndex(1 if value=='checkbox' else 0)
        self.text.setPlaceholderText('每行一個項目' if value in ('list','tags') else '2026-10-18' if value=='date' else '2026-10-18T20:00' if value=='datetime' else '')
    def setPlainText(self,text):self.text.setPlainText(text);self.check.setChecked(text.lower()=='true')
    def toPlainText(self):return ('true' if self.check.isChecked() else 'false') if self.kind.currentData()=='checkbox' else self.text.toPlainText()
    def setReadOnly(self,value):self.text.setReadOnly(value);self.check.setEnabled(not value)


class PropertyEditor(StudioDialog):
    def __init__(self,surface):
        super().__init__(surface);self.surface=surface;self.rows=[];self.removed=[]
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setWindowTitle('筆記屬性');self.resize(720,570)
        layout=self.body;self.table=QTableWidget(0,3);self.table.setAccessibleName('筆記屬性')
        self.table.setShowGrid(False);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setStyleSheet('QTableWidget {border:0;background:transparent;} QHeaderView::section {padding:8px;border:0;}')
        self.table.setHorizontalHeaderLabels(['名稱','型別','內容']);self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.Stretch)
        self.table.setColumnWidth(0,150);self.table.setColumnWidth(1,115);layout.addWidget(self.table)
        row=QHBoxLayout();add=QPushButton('新增屬性');add.clicked.connect(lambda:self.add_row());row.addWidget(add)
        remove=QPushButton('移除選取屬性');remove.clicked.connect(self.remove_row);row.addWidget(remove);row.addStretch();layout.addLayout(row)
        self.message=QLabel();self.message.setWordWrap(True);layout.addWidget(self.message)
        self.buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setText('套用')
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('Primary')
        self.buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        self.buttons.accepted.connect(self.submit);self.buttons.rejected.connect(self.reject);layout.addWidget(self.buttons)
        self.reload()
    def reload(self):
        self.snapshot=self.surface.page.buffer.snapshot();_,properties,error=frontmatter(self.snapshot.text)
        self.table.setRowCount(0);self.rows=[];self.removed=[]
        for prop in properties:self.add_row(prop)
        self.message.setText(error or ('複合屬性保留原文；可從原文模式修改。' if any(not p.editable for p in properties) else ''))
        self.buttons.button(QDialogButtonBox.StandardButton.Save).setEnabled(not error)
    def add_row(self,prop=None):
        index=self.table.rowCount();self.table.insertRow(index)
        name=QLineEdit(prop.name if prop else '');name.setAccessibleName('屬性名稱')
        kind=QComboBox();kind.setAccessibleName('屬性型別')
        for label,key in KINDS:kind.addItem(label,key)
        value=PropertyValue(kind);value.setMaximumHeight(74)
        if prop:
            kind.setCurrentIndex(max(0,kind.findData(prop.kind)))
            value.setPlainText('\n'.join(prop.value) if isinstance(prop.value,list) else str(prop.value).lower() if isinstance(prop.value,bool) else str(prop.value if prop.value is not None else ''))
            if not prop.editable:
                kind.addItem('原文保留','complex');kind.setCurrentIndex(kind.count()-1)
                name.setReadOnly(True);kind.setEnabled(False);value.setReadOnly(True);value.setPlainText(prop.raw)
        for column,control in enumerate((name,kind,value)):
            cell=QWidget();inset=QVBoxLayout(cell);inset.setContentsMargins(3,3,3,3)
            inset.addWidget(control)
            self.table.setCellWidget(index,column,cell)
            control.installEventFilter(self)
            for child in control.findChildren(QWidget):child.installEventFilter(self)
        self.table.setRowHeight(index,80 if kind.currentData() in ('list','tags','complex') else 48)
        self.rows.append((prop,name,kind,value))
        kind.currentIndexChanged.connect(lambda:self.resize_rows())
    def resize_rows(self):
        for index,(_,_,kind,_) in enumerate(self.rows):
            self.table.setRowHeight(index,80 if kind.currentData() in ('list','tags','complex') else 48)
    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.FocusIn:
            for index,(_,name,kind,value) in enumerate(self.rows):
                if any(watched is control or control.isAncestorOf(watched) for control in (name,kind,value)):
                    self.table.setCurrentCell(index,0);break
        return super().eventFilter(watched,event)
    def remove_row(self):
        index=self.table.currentRow()
        if index<0:return
        prop,*_=self.rows[index]
        if prop and not prop.editable:self.message.setText('這個複合屬性請在原文中修改。');return
        if prop:self.removed.append(prop.name)
        self.rows.pop(index);self.table.removeRow(index)
    def submit(self):
        try:
            updates=[(name,name,'text',None) for name in self.removed]
            for prop,name,kind,control in self.rows:
                if prop and not prop.editable:continue
                value=control.toPlainText();key=kind.currentData();title=name.text().strip()
                if not title:raise ValueError('請填入屬性名稱，或移除空白列。')
                if key in ('list','tags'):value=[v.strip() for v in value.splitlines() if v.strip()]
                elif key=='checkbox':
                    if value.lower() not in ('true','false'):raise ValueError('核取方塊請填入 true 或 false。')
                    value=value.lower()=='true'
                elif key=='number':
                    from .markdown_properties import scalar
                    actual,value=scalar(value)
                    if actual!='number':raise ValueError('請填入數字。')
                if prop and prop.value is None and value=='':value=prop.value
                if prop and (title,key,value)==(prop.name,prop.kind,prop.value):continue
                updates.append((prop.name if prop else None,title,key,value))
            edits=property_edits(self.snapshot.text,updates)
            self.surface.page.buffer.apply(self.snapshot,edits);self.surface.refresh();self.accept()
        except (ValueError,OSError) as error:self.message.setText(str(error)+' 關閉後重新開啟可取得最新內容。')
