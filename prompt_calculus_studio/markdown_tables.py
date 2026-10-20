"""Source-backed Markdown tables. A dialog edits a draft, never a rendered document."""
import csv
import io
import re
from dataclasses import dataclass
from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtWidgets import QApplication, QHBoxLayout, QTableWidget, QTableWidgetItem, QPushButton, QDialogButtonBox, QStyledItemDelegate, QPlainTextEdit, QAbstractItemDelegate, QLabel
from .widgets import RoundMenu as QMenu, StudioDialog
from .markdown_source import SourceEdit, literal_ranges
from .markdown_syntax import visible_text
from .ui_icons import icon
from .theme import visual_tokens,window_appearance
from .markdown_table_cells import transform_pipes, decode_cell, encode_cell


def split_row(line):
    """Keep raw slices; GFM's table layer consumes one slash before any pipe."""
    cuts=[];previous=''
    for pos,char in enumerate(line):
        if char == '|' and previous != '\\':cuts.append(pos)
        previous=char
    if not cuts:return None
    parts=[];previous=0
    for cut in cuts:parts.append((previous,cut,line[previous:cut]));previous=cut+1
    parts.append((previous,len(line),line[previous:]))
    if not parts[0][2].strip():parts.pop(0)
    if parts and not parts[-1][2].strip():parts.pop()
    return parts


@dataclass
class Table:
    start:int
    end:int
    rows:list
    align:list
    source:str=''
    newline:bool=True

    def serialize(self):
        lines=['| '+' | '.join(self.rows[0])+' |']
        lines.append('| '+' | '.join({'left':':---','right':'---:','center':':---:','':'---'}[a] for a in self.align)+' |')
        lines.extend('| '+' | '.join(row)+' |' for row in self.rows[1:])
        return '\n'.join(lines)+ ('\n' if self.newline else '')


def tables(text):
    lines=text.splitlines(keepends=True);offsets=[];total=0
    for line in lines:offsets.append(total);total+=len(line)
    protected=literal_ranges(text);result=[];i=0
    while i+1<len(lines):
        header=split_row(lines[i].rstrip('\n'));separator=split_row(lines[i+1].rstrip('\n'))
        if (not header or not separator or len(header)!=len(separator)
                or any(not re.fullmatch(r'\s*:?-{3,}:?\s*',p[2]) for p in separator)
                or any(a<=offsets[i]<b for a,b in protected)
                or lines[i].lstrip().startswith(('>','    ','\t'))):i+=1;continue
        rows=[[p[2].strip() for p in header]];j=i+2
        while j<len(lines):
            parts=split_row(lines[j].rstrip('\n'))
            if not parts or len(parts)!=len(header):break
            rows.append([p[2].strip() for p in parts]);j+=1
        align=[]
        for _,_,raw in separator:
            raw=raw.strip();align.append('center' if raw.startswith(':') and raw.endswith(':') else 'left' if raw.startswith(':') else 'right' if raw.endswith(':') else '')
        end=offsets[j] if j<len(lines) else len(text)
        result.append(Table(offsets[i],end,rows,align,text[offsets[i]:end],text[:end].endswith('\n')));i=j
    return result


class CellDelegate(QStyledItemDelegate):
    def createEditor(self,parent,option,index):
        editor=QPlainTextEdit(parent);editor.setTabChangesFocus(True)
        editor.setAccessibleName('表格儲存格；Shift+Enter 換行');return editor

    def setEditorData(self,editor,index):editor.setPlainText(index.data() or '');editor.selectAll()
    def setModelData(self,editor,model,index):model.setData(index,editor.toPlainText())

    def eventFilter(self,editor,event):
        if event.type()==QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter) and not event.modifiers()&Qt.KeyboardModifier.ShiftModifier:
                self.commitData.emit(editor);self.closeEditor.emit(editor,QAbstractItemDelegate.EndEditHint.NoHint);return True
            if event.key() in (Qt.Key.Key_Tab,Qt.Key.Key_Backtab):
                self.commitData.emit(editor)
                hint=QAbstractItemDelegate.EndEditHint.EditPreviousItem if event.key()==Qt.Key.Key_Backtab or event.modifiers()&Qt.KeyboardModifier.ShiftModifier else QAbstractItemDelegate.EndEditHint.EditNextItem
                self.closeEditor.emit(editor,hint);return True
        return super().eventFilter(editor,event)


class TableGrid(QTableWidget):
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_V and event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            self.paste_text(QApplication.clipboard().text());return
        if event.key()==Qt.Key.Key_C and event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            QApplication.clipboard().setText(self.copy_text());return
        super().keyPressEvent(event)

    def copy_text(self):
        ranges=self.selectedRanges()
        if not ranges:return ''
        region=ranges[0];out=io.StringIO();writer=csv.writer(out,delimiter='\t',lineterminator='\n')
        for row in range(region.topRow(),region.bottomRow()+1):
            writer.writerow([self.item(row,col).text() for col in range(region.leftColumn(),region.rightColumn()+1)])
        return out.getvalue()

    def paste_text(self,text):
        data=list(csv.reader(io.StringIO(text),delimiter='\t'))
        if not data:return
        row,col=max(0,self.currentRow()),max(0,self.currentColumn())
        needed_rows=row+len(data);needed_cols=col+max(map(len,data))
        if needed_rows>10000 or needed_cols>256:raise ValueError('貼上的表格過大，請分段貼上。')
        self.setRowCount(max(self.rowCount(),needed_rows));self.setColumnCount(max(self.columnCount(),needed_cols))
        for r in range(self.rowCount()):
            for c in range(self.columnCount()):
                if not self.item(r,c):self.setItem(r,c,QTableWidgetItem(''))
        for r,values in enumerate(data,row):
            for c,value in enumerate(values,col):self.item(r,c).setText(value)


class TableDialog(StudioDialog):
    def __init__(self,surface,table,snapshot):
        super().__init__(surface);self.surface=surface;self.table=table;self.snapshot=snapshot
        self.setWindowTitle('編輯表格');self.resize(880,560);self.setMinimumSize(480,360)
        from .module_chrome import dialog_colors
        colors=dialog_colors(visual_tokens(window_appearance(surface)))
        self.setObjectName('MarkdownTableEditor')
        self.setStyleSheet(self.styleSheet()+f'''
            QTableWidget#MarkdownTableGrid {{background:{colors['base']};gridline-color:{colors['line']};border:1px solid {colors['line']};border-radius:0;selection-background-color:{colors['selected']};selection-color:{colors['text']};}}
            QTableWidget#MarkdownTableGrid::item {{padding:8px 10px;border:0;}}
            QTableWidget#MarkdownTableGrid QPlainTextEdit {{padding:6px 8px;border:1px solid {colors['accent']};border-radius:3px;}}
            QHeaderView::section {{background:{colors['surface']};color:{colors['secondary']};border:0;padding:6px;}}
            QPushButton#TableMenu {{padding:6px 26px 6px 10px;min-height:22px;}}
            QPushButton#TableMenu::menu-indicator {{subcontrol-origin:padding;subcontrol-position:right center;right:8px;}}
            QLabel#TableHint {{color:{colors['secondary']};}}''')
        layout=self.body;tools=QHBoxLayout();tools.setSpacing(8);layout.addLayout(tools)
        self.grid=TableGrid(len(table.rows),len(table.align),self)
        self.grid.setObjectName('MarkdownTableGrid')
        self.grid.setAccessibleName('表格內容');self.grid.setItemDelegate(CellDelegate(self.grid))
        self.grid.setWordWrap(True);self.grid.horizontalHeader().setStretchLastSection(True)
        self.grid.setAlternatingRowColors(False)
        self.align=list(table.align)
        for r,row in enumerate(table.rows):
            for c,value in enumerate(row):
                item=QTableWidgetItem(decode_cell(value));item.setData(Qt.ItemDataRole.UserRole,(decode_cell(value),value));self.grid.setItem(r,c,item)
        self.grid.setCurrentCell(0,0)
        self.grid.resizeColumnsToContents()
        for col in range(self.grid.columnCount()):self.grid.setColumnWidth(col,max(120,min(320,self.grid.columnWidth(col))))
        self.grid.resizeRowsToContents()
        self.grid.horizontalHeader().sectionResized.connect(lambda *args:QTimer.singleShot(0,self.grid,self.grid.resizeRowsToContents))
        self.grid.itemChanged.connect(lambda *args:self.grid.resizeRowsToContents())
        for title,symbol,choices in [
            ('列','list',[('在下方新增列',lambda:self.add_row()),('刪除此列',self.remove_row),('此列上移',lambda:self.move_row(-1)),('此列下移',lambda:self.move_row(1)),('依此欄升冪排序',lambda:self.sort_rows(False)),('依此欄降冪排序',lambda:self.sort_rows(True))]),
            ('欄','grid',[('在右方新增欄',self.add_column),('刪除此欄',self.remove_column),('此欄左移',lambda:self.move_column(-1)),('此欄右移',lambda:self.move_column(1)),('依欄名排序',self.sort_columns)]),
            ('欄對齊','align',[(label,lambda value=value:self.set_alignment(value)) for label,value in [('預設',''),('靠左','left'),('置中','center'),('靠右','right')]])]:
            button=QPushButton(title,self);button.setObjectName('TableMenu');button.setIcon(icon(symbol,colors['text']));button.setAutoDefault(False)
            button.setAccessibleName(title);menu=QMenu(button,persistent=True)
            for label,callback in choices:menu.addAction(label,callback)
            button.setMenu(menu);tools.addWidget(button)
        tools.addStretch();layout.addWidget(self.grid)
        self.status=QLabel('第一列為欄名 · Tab 換格 · Shift+Enter 格內換行',self);self.status.setObjectName('TableHint');self.status.setWordWrap(True);layout.addWidget(self.status)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Save|QDialogButtonBox.StandardButton.Cancel,self)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText('套用');buttons.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        buttons.button(QDialogButtonBox.StandardButton.Save).setObjectName('Primary')
        buttons.accepted.connect(self.commit);buttons.rejected.connect(self.reject);layout.addWidget(buttons)

    def empty_row(self,row):
        for c in range(self.grid.columnCount()):self.grid.setItem(row,c,QTableWidgetItem(''))

    def add_row(self):
        row=max(1,self.grid.currentRow()+1);self.grid.insertRow(row);self.empty_row(row);self.grid.setCurrentCell(row,0)

    def remove_row(self):
        row=self.grid.currentRow()
        if row>0:self.grid.removeRow(row)

    def move_row(self,direction):
        row=self.grid.currentRow();other=row+direction
        if row<1 or not 1<=other<self.grid.rowCount():return
        for c in range(self.grid.columnCount()):
            a=self.grid.takeItem(row,c);b=self.grid.takeItem(other,c);self.grid.setItem(row,c,b);self.grid.setItem(other,c,a)
        self.grid.setCurrentCell(other,max(0,self.grid.currentColumn()))

    def ensure_alignment(self):
        self.align.extend(['']*(self.grid.columnCount()-len(self.align)))

    def add_column(self):
        self.ensure_alignment();col=max(0,self.grid.currentColumn()+1);self.grid.insertColumn(col);self.align.insert(col,'')
        for row in range(self.grid.rowCount()):self.grid.setItem(row,col,QTableWidgetItem(''))
        self.grid.setCurrentCell(0,col)

    def remove_column(self):
        self.ensure_alignment();col=self.grid.currentColumn()
        if col>=0 and self.grid.columnCount()>1:self.grid.removeColumn(col);self.align.pop(col)

    def move_column(self,direction):
        self.ensure_alignment();col=self.grid.currentColumn();other=col+direction
        if not 0<=other<self.grid.columnCount():return
        for row in range(self.grid.rowCount()):
            a=self.grid.takeItem(row,col);b=self.grid.takeItem(row,other);self.grid.setItem(row,col,b);self.grid.setItem(row,other,a)
        self.align[col],self.align[other]=self.align[other],self.align[col];self.grid.setCurrentCell(0,other)

    @staticmethod
    def sort_key(text):
        return [(1,float(x)) if re.fullmatch(r'\d+(?:\.\d+)?',x) else (0,x.casefold()) for x in re.split(r'(\d+(?:\.\d+)?)',visible_text(text))]

    def sort_rows(self,reverse):
        col=max(0,self.grid.currentColumn());rows=[]
        for r in range(1,self.grid.rowCount()):rows.append([self.grid.takeItem(r,c) for c in range(self.grid.columnCount())])
        rows.sort(key=lambda row:self.sort_key(row[col].text()),reverse=reverse)
        for r,row in enumerate(rows,1):
            for c,item in enumerate(row):self.grid.setItem(r,c,item)

    def sort_columns(self):
        self.ensure_alignment();order=sorted(range(self.grid.columnCount()),key=lambda c:self.sort_key(self.grid.item(0,c).text()))
        cells=[[self.grid.takeItem(r,c) for c in range(self.grid.columnCount())] for r in range(self.grid.rowCount())]
        for r,row in enumerate(cells):
            for c,old in enumerate(order):self.grid.setItem(r,c,row[old])
        self.align=[self.align[old] for old in order]

    def set_alignment(self,value):
        self.ensure_alignment();col=max(0,self.grid.currentColumn());self.align[col]=value
        self.status.setText('目前欄對齊：'+{'':'預設','left':'靠左','center':'置中','right':'靠右'}[value])

    def draft(self):
        self.ensure_alignment();rows=[]
        for r in range(self.grid.rowCount()):
            row=[]
            for c in range(self.grid.columnCount()):
                item=self.grid.item(r,c);old=item.data(Qt.ItemDataRole.UserRole)
                row.append(old[1] if old and item.text()==old[0] else encode_cell(item.text()))
            rows.append(row)
        return Table(self.table.start,self.table.end,rows,list(self.align),newline=self.table.newline)

    def commit(self):
        editor=QApplication.focusWidget()
        if isinstance(editor,QPlainTextEdit) and self.grid.isAncestorOf(editor):
            self.grid.itemDelegate().commitData.emit(editor)
            self.grid.itemDelegate().closeEditor.emit(editor,QAbstractItemDelegate.EndEditHint.NoHint)
        self.grid.clearFocus();draft=self.draft()
        if self.table.source and draft.rows==self.table.rows and draft.align==self.table.align:self.accept();return
        replacement=draft.serialize()
        if not self.table.source:
            before=self.snapshot.text[:self.table.start]
            replacement=('\n\n' if before and not before.endswith('\n\n') else '')+replacement+'\n'
        try:
            if self.surface.mode=='read':raise ValueError('閱讀模式不能修改表格。')
            self.surface.page.buffer.apply(self.snapshot,[SourceEdit(self.table.start,self.table.end,replacement)])
        except ValueError as error:self.status.setText(str(error));return
        self.surface.refresh();self.accept()


def edit_table(surface):
    if surface.mode=='read' or surface.page.buffer.composing:return
    snapshot,start,end=surface.selection()
    table=next((t for t in tables(snapshot.text) if t.start<=start<t.end),None)
    if table is None:table=Table(start,end,[['欄位一','欄位二'],['',''],['','']],['',''])
    dialog=TableDialog(surface,table,snapshot);dialog.exec();surface.focus_document()
