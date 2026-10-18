"""Optional native editing affordances, all source edits share one undo stack."""
import re
from PySide6.QtCore import QObject,QTimer,Qt
from PySide6.QtGui import QTextCursor,QTextCharFormat,QColor,QKeySequence
from PySide6.QtWidgets import QTextEdit,QCheckBox,QFormLayout
from .markdown_source import SourceEdit,python_position,utf16_position as qt_position


class AdvancedEditing(QObject):
    def __init__(self,surface):
        super().__init__(surface); self.s=surface; self.e=surface.editor; self.cursors=[]; self.rectangle=None
        self.vim_mode='insert'; self.pending=''; self.register=''; self.count=''; self.spell=None; self.misspellings=[]; self.vim_origin=0
        self.spell_timer=QTimer(self); self.spell_timer.setSingleShot(True); self.spell_timer.setInterval(450)
        self.spell_timer.timeout.connect(self.check_spelling)
        self.s.page.buffer.text.contentsChanged.connect(self.changed); self.e.verticalScrollBar().valueChanged.connect(self.changed)
        self.applying=False
    def configure(self):
        if self.s.preferences.get('vim',False):self.vim_mode='normal'
        else:self.vim_mode='insert'
        self.count='';self.pending='';self.changed()
    def changed(self,*_):
        if self.s.preferences.get('spellcheck',False):self.spell_timer.start()
        else:self.misspellings=[];self.paint()
    def clear(self):self.cursors=[];self.paint()
    def extras(self):
        result=[]
        light=QColor(self.e.tokens()['base']).lightnessF()>.5
        for cursor in self.cursors:
            item=QTextEdit.ExtraSelection(); item.cursor=QTextCursor(cursor)
            item.format.setBackground(QColor('#ead79a' if light else '#6a6542'))
            item.format.setForeground(QColor('#292b29' if light else '#f3f1e8'))
            if not item.cursor.hasSelection():item.cursor.movePosition(QTextCursor.MoveOperation.NextCharacter,QTextCursor.MoveMode.KeepAnchor)
            result.append(item)
        for start,end,_ in self.misspellings:
            item=QTextEdit.ExtraSelection(); item.cursor=QTextCursor(self.e.document())
            item.cursor.setPosition(start);item.cursor.setPosition(end,QTextCursor.MoveMode.KeepAnchor)
            item.format.setUnderlineColor(QColor('#b84337' if light else '#d98978'));item.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.SpellCheckUnderline)
            result.append(item)
        return result
    def paint(self):
        QTextEdit.setExtraSelections(self.e,list(getattr(self.e,'base_selections',[]))+self.extras())
    def next_occurrence(self):
        cursor=self.e.textCursor()
        if not cursor.hasSelection():cursor.select(QTextCursor.SelectionType.WordUnderCursor);self.e.setTextCursor(cursor);return
        text=self.s.page.buffer.snapshot().text; word=cursor.selectedText().replace('\u2029','\n')
        used={(c.selectionStart(),c.selectionEnd()) for c in [cursor,*self.cursors]}
        after=python_position(text,max(c.selectionEnd() for c in [cursor,*self.cursors]))
        matches=list(re.finditer(re.escape(word),text))
        matches.sort(key=lambda m:(m.start()<after,m.start()))
        for match in matches:
            a,b=qt_position(text,match.start()),qt_position(text,match.end())
            if (a,b) in used:continue
            extra=QTextCursor(self.e.document());extra.setPosition(a);extra.setPosition(b,QTextCursor.MoveMode.KeepAnchor)
            self.cursors.append(extra);self.paint();return
    def add_vertical(self,delta):
        cursor=QTextCursor(self.cursors[-1] if self.cursors else self.e.textCursor())
        cursor.movePosition(QTextCursor.MoveOperation.Up if delta<0 else QTextCursor.MoveOperation.Down)
        if cursor.position() not in [c.position() for c in [self.e.textCursor(),*self.cursors]]:self.cursors.append(cursor);self.paint()
    def multi_insert(self,text,backspace=False,delete=False):
        snapshot=self.s.page.buffer.snapshot(); primary=self.e.textCursor(); edits=[]
        for cursor in [primary,*self.cursors]:
            start=python_position(snapshot.text,cursor.selectionStart());end=python_position(snapshot.text,cursor.selectionEnd())
            if start==end:
                if backspace:start=max(0,start-1)
                if delete:end=min(len(snapshot.text),end+1)
            if (start,end) not in [(e.start,e.end) for e in edits]:edits.append(SourceEdit(start,end,text))
        edits.sort(key=lambda e:e.start)
        if any(a.end>b.start for a,b in zip(edits,edits[1:])):self.clear();return
        primary_start=python_position(snapshot.text,primary.selectionStart());positions=[];shift=0
        for edit in edits:positions.append(edit.start+shift+len(text));shift+=len(text)-(edit.end-edit.start)
        primary_index=next((i for i,e in enumerate(edits) if e.start<=primary_start<=e.end),0)
        self.cursors=[];self.applying=True
        try:self.s.apply(edits,positions[primary_index])
        finally:self.applying=False
        updated=self.s.page.buffer.snapshot().text
        for index,position in enumerate(positions):
            if index==primary_index:continue
            cursor=QTextCursor(self.e.document());cursor.setPosition(qt_position(updated,position));self.cursors.append(cursor)
        self.paint()
    def mouse_press(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or self.s.page.buffer.composing:return False
        mods=event.modifiers()
        if mods & Qt.KeyboardModifier.AltModifier:
            cursor=self.e.cursorForPosition(event.position().toPoint())
            if mods & Qt.KeyboardModifier.ShiftModifier:self.rectangle=cursor;self.cursors=[]
            elif cursor.position()!=self.e.textCursor().position():self.cursors.append(cursor)
            self.paint();return True
        self.clear();return False
    def mouse_move(self,event):
        if self.rectangle is None:return False
        end=self.e.cursorForPosition(event.position().toPoint());a,b=sorted((self.rectangle.blockNumber(),end.blockNumber()))
        left,right=sorted((self.rectangle.positionInBlock(),end.positionInBlock()));cursors=[]
        for number in range(a,b+1):
            block=self.e.document().findBlockByNumber(number);cursor=QTextCursor(block)
            cursor.setPosition(block.position()+min(left,block.length()-1))
            cursor.setPosition(block.position()+min(right,block.length()-1),QTextCursor.MoveMode.KeepAnchor);cursors.append(cursor)
        if cursors:self.e.setTextCursor(cursors[0]);self.cursors=cursors[1:];self.paint()
        return True
    def key(self,event):
        if self.s.page.buffer.composing or self.s.mode=='read':return False
        key=event.key();mods=event.modifiers();ctrl=Qt.KeyboardModifier.ControlModifier;alt=Qt.KeyboardModifier.AltModifier
        if mods==ctrl and key==Qt.Key.Key_D:self.next_occurrence();return True
        if mods==(ctrl|alt) and key in (Qt.Key.Key_Up,Qt.Key.Key_Down):self.add_vertical(-1 if key==Qt.Key.Key_Up else 1);return True
        if key==Qt.Key.Key_Escape:
            self.clear()
            if self.s.preferences.get('vim'):self.vim_mode='normal';self.pending='';self.count='';return True
        if self.cursors:
            if key in (Qt.Key.Key_Backspace,Qt.Key.Key_Delete):self.multi_insert('',key==Qt.Key.Key_Backspace,key==Qt.Key.Key_Delete);return True
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter):self.multi_insert('\n');return True
            if event.matches(QKeySequence.StandardKey.Paste):
                from PySide6.QtWidgets import QApplication
                self.multi_insert(QApplication.clipboard().text());return True
            if event.text() and not mods&(ctrl|alt):self.multi_insert(event.text());return True
            if key in (Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down):self.clear()
        if self.s.preferences.get('vim') and self.vim_mode!='insert':return self.vim(event)
        cursor=self.e.textCursor()
        if event.text()=='/' and not cursor.block().text().strip() and self.s.preferences.get('slash_commands',True):
            self.s.open_commands();return True
        return False
    def vim(self,event):
        from PySide6.QtWidgets import QApplication
        key=event.text();cursor=self.e.textCursor();keep=QTextCursor.MoveMode.KeepAnchor if self.vim_mode=='visual' else QTextCursor.MoveMode.MoveAnchor
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            if event.key()==Qt.Key.Key_R:self.s.page.redo();return True
            return False
        if key.isdigit() and (key!='0' or self.count):self.count+=key;return True
        count=min(int(self.count or 1),1000);self.count=''
        motions={'h':QTextCursor.MoveOperation.Left,'j':QTextCursor.MoveOperation.Down,'k':QTextCursor.MoveOperation.Up,
                 'l':QTextCursor.MoveOperation.Right,'w':QTextCursor.MoveOperation.NextWord,'b':QTextCursor.MoveOperation.PreviousWord,
                 '0':QTextCursor.MoveOperation.StartOfLine,'$':QTextCursor.MoveOperation.EndOfLine,'G':QTextCursor.MoveOperation.End}
        if self.pending:
            pending=self.pending;self.pending=''
            if pending=='g' and key=='g':cursor.movePosition(QTextCursor.MoveOperation.Start);self.e.setTextCursor(cursor);return True
            if pending in ('d','y') and (key in motions or key==pending):
                if key==pending:
                    cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock);cursor.movePosition(QTextCursor.MoveOperation.NextBlock,QTextCursor.MoveMode.KeepAnchor,count)
                    if not cursor.hasSelection():cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock,QTextCursor.MoveMode.KeepAnchor)
                else:cursor.movePosition(motions[key],QTextCursor.MoveMode.KeepAnchor,count)
                self.register=cursor.selectedText().replace('\u2029','\n')
                if pending=='d':
                    self.vim_origin=cursor.selectionStart();self.e.setTextCursor(cursor);self.s.apply([SourceEdit(*self.s.selection()[1:],'')])
                return True
        if key in motions:cursor.movePosition(motions[key],keep,count);self.e.setTextCursor(cursor);return True
        if key in ('d','y') and self.vim_mode=='visual':
            self.register=cursor.selectedText().replace('\u2029','\n')
            if key=='d':self.s.apply([SourceEdit(*self.s.selection()[1:],'')])
            self.vim_mode='normal';return True
        if key in ('d','y','g'):self.pending=key;return True
        if key in ('i','a','I','A','o','O'):
            if key=='a':cursor.movePosition(QTextCursor.MoveOperation.Right)
            if key in ('I','O'):cursor.movePosition(QTextCursor.MoveOperation.StartOfBlock)
            if key in ('A','o'):cursor.movePosition(QTextCursor.MoveOperation.EndOfBlock)
            self.e.setTextCursor(cursor)
            if key in ('o','O'):
                _,start,end=self.s.selection();self.s.apply([SourceEdit(start,end,'\n')],start+(key=='o'))
            self.vim_mode='insert';return True
        if key=='v':self.vim_mode='visual' if self.vim_mode!='visual' else 'normal';return True
        if key=='x':
            cursor.movePosition(QTextCursor.MoveOperation.Right,QTextCursor.MoveMode.KeepAnchor,count);self.e.setTextCursor(cursor)
            self.s.apply([SourceEdit(*self.s.selection()[1:],'')]);return True
        if key=='u':
            self.s.page.undo();cursor=self.e.textCursor();cursor.setPosition(min(self.vim_origin,self.e.document().characterCount()-1));self.e.setTextCursor(cursor);return True
        if key=='p':
            _,start,end=self.s.selection();self.s.apply([SourceEdit(start,end,self.register or QApplication.clipboard().text())]);return True
        if key=='/':self.s.open_find();return True
        return True
    def check_spelling(self):
        if not self.s.preferences.get('spellcheck') or self.s.page.buffer.composing:return
        if self.spell is None:
            try:
                from spellchecker import SpellChecker
                self.spell=SpellChecker(language='en',distance=1)
            except ImportError:self.s.report('英文拼字字典未安裝。');return
            workspace=self.s.page.file_title.workspace
            self.custom=set(workspace.notebook.store.get('spelling:en',[]) if workspace and hasattr(workspace,'notebook') else [])
            self.spell.word_frequency.load_words(self.custom)
        from .markdown_links import prose_mask
        text=self.s.page.buffer.snapshot().text;start=python_position(text,self.e.firstVisibleBlock().position())
        # Spell only the visible neighborhood; scrolling schedules a fresh pass.
        segment=text[start:start+12000];masked=prose_mask(segment);self.misspellings=[]
        for match in re.finditer(r"\b[A-Za-z][A-Za-z']{2,29}\b",masked):
            word=match.group()
            if word.isupper() or self.spell.known([word]):continue
            self.misspellings.append((qt_position(text,start+match.start()),qt_position(text,start+match.end()),word))
        self.paint()
    def spelling_menu(self,menu,position):
        if not self.spell:return
        hit=next((v for v in self.misspellings if v[0]<=position<=v[1]),None)
        if not hit:return
        start,end,word=hit
        def replace(value):
            text=self.s.page.buffer.snapshot().text
            if text[python_position(text,start):python_position(text,end)]==word:
                self.s.apply([SourceEdit(python_position(text,start),python_position(text,end),value)])
        for suggestion in sorted(self.spell.candidates(word) or (),key=lambda w:(-self.spell.word_usage_frequency(w),w))[:5]:
            menu.addAction(suggestion,lambda checked=False,value=suggestion:replace(value))
        def remember():
            self.custom.add(word.lower());self.spell.word_frequency.load_words([word]);workspace=self.s.page.file_title.workspace
            if workspace and hasattr(workspace,'notebook'):workspace.notebook.store.put('spelling:en',sorted(self.custom))
            self.check_spelling()
        menu.addAction('加入字典',remember);menu.addSeparator()


from .notebook_dialogs import Sheet
class AdvancedDialog(Sheet):
    def __init__(self,surface):
        super().__init__(surface,'進階編輯',460,280);form=QFormLayout();self.fields={}
        for key,title,default in [('vim','Vim 鍵位',False),('spellcheck','英文拼字檢查',False),('slash_commands','斜線命令',True)]:
            field=QCheckBox();field.setChecked(surface.preferences.get(key,default));field.setAccessibleName(title)
            form.addRow(title,field);self.fields[key]=field
        self.body.addLayout(form)
        def save():
            surface.preferences.update({key:field.isChecked() for key,field in self.fields.items()});surface.save_preferences();surface.advanced.configure()
        self.footer(save)
