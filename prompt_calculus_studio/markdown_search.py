"""View-local find highlights; never format or modify the source document."""
import re
import time
from bisect import bisect_left, bisect_right
from PySide6.QtCore import QObject, QTimer, QPoint
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QTextEdit
from .markdown_source import utf16_position


class SearchHighlight(QObject):
    def __init__(self,surface):
        super().__init__(surface);self.surface=surface
        surface.editor.selectionChanged.connect(self.update)
        surface.reader.selectionChanged.connect(self.update)
        self.key=None;self.paint_key=None;self.painted=False;self.spans=[];self.qt_spans=[]
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.update)
        self.scan_timer=QTimer(self);self.scan_timer.setInterval(0);self.scan_timer.timeout.connect(self.scan)
        self.searching=False
        for view in (surface.editor,surface.reader):
            view.verticalScrollBar().valueChanged.connect(lambda _:self.timer.start(0))

    def matches(self):
        s=self.surface;query=s.query.text()
        if not query:
            self.scan_timer.stop();self.searching=False
            self.key=None;self.spans=[];self.qt_spans=[]
            return self.spans
        if s.mode=='read':
            view=s.reader;identity=(id(view.document()),view.document().revision())
            snapshot=None
        else:
            snapshot=s.page.buffer.snapshot();identity=(snapshot.document_id,snapshot.revision)
        key=(s.mode,identity,query,s.case_sensitive.isChecked())
        if key!=self.key:
            self.text=snapshot.text if snapshot is not None else view.toPlainText()
            self.scan_timer.stop();self.searching=False
            if len(self.text)>256_000:
                self.key=key;self.paint_key=None;self.spans=[];self.qt_spans=[]
                self.iterator=re.finditer(re.escape(query),self.text,0 if s.case_sensitive.isChecked() else re.I)
                self.scan_spans=[];self.scan_qt=[];self.scan_previous=0;self.scan_position=0
                self.searching=True;self.scan_timer.start()
                return self.spans
            self.spans=[m.span() for m in re.finditer(re.escape(query),self.text,0 if s.case_sensitive.isChecked() else re.I)]
            self.qt_spans=[];previous=0;position=0
            # Convert each source slice once, including non-BMP characters.
            # Prefix-encoding for every result was quadratic in document size.
            for start,end in self.spans:
                position+=utf16_position(self.text[previous:start],start-previous);a=position
                position+=utf16_position(self.text[start:end],end-start)
                self.qt_spans.append((a,position));previous=end
            self.key=key;self.paint_key=None
        return self.spans

    def scan(self):
        deadline=time.perf_counter()+.004
        while time.perf_counter()<deadline:
            match=next(self.iterator,None)
            if match is None:
                self.scan_timer.stop();self.searching=False
                self.spans,self.qt_spans=self.scan_spans,self.scan_qt
                self.surface.search_changed()
                return
            start,end=match.span()
            self.scan_position+=len(self.text[self.scan_previous:start].encode('utf-16-le'))//2
            a=self.scan_position
            self.scan_position+=len(self.text[start:end].encode('utf-16-le'))//2
            self.scan_spans.append((start,end));self.scan_qt.append((a,self.scan_position))
            self.scan_previous=end

    def current_index(self,view):
        self.matches();cursor=view.textCursor();pair=(cursor.selectionStart(),cursor.selectionEnd())
        index=bisect_left(self.qt_spans,pair)
        return index if index<len(self.qt_spans) and self.qt_spans[index]==pair else -1

    def next_span(self,cursor,direction):
        self.matches()
        if not self.qt_spans:return None
        if direction<0:
            at=cursor.selectionStart();index=bisect_left(self.qt_spans,(at,))-1
            if index>=0 and self.qt_spans[index][1]>at:index-=1
        else:index=bisect_left(self.qt_spans,(cursor.selectionEnd(),))
        return self.qt_spans[index%len(self.qt_spans)]

    def state(self,view,active):
        if bool(view.property('searchHit'))==active:return
        view.setProperty('searchHit',active);view.style().unpolish(view);view.style().polish(view)
        view.viewport().update()

    def clear(self):
        if not self.painted and not any(view.property('searchHit') for view in (self.surface.editor,self.surface.reader)):return
        for view in (self.surface.editor,self.surface.reader):
            view.setExtraSelections([]);self.state(view,False)
        self.paint_key=None;self.painted=False

    def update(self):
        s=self.surface
        if s.page.buffer.opening:return
        if not s.find_bar.isVisible() or not s.query.text():self.clear();return
        view=s.reader if s.mode=='read' else s.editor
        other=s.editor if view is s.reader else s.reader
        other.setExtraSelections([]);self.state(other,False)
        if s.mode=='read' and s.reader.advanced:return
        self.matches();current=view.textCursor();pair=(current.selectionStart(),current.selectionEnd())
        indices=range(len(self.qt_spans))
        if len(self.qt_spans)>64:
            first=max(0,view.cursorForPosition(QPoint(0,0)).position()-2*len(s.query.text()))
            last=view.cursorForPosition(QPoint(view.viewport().width(),view.viewport().height())).position()+2*len(s.query.text())
            indices=range(max(0,bisect_left(self.qt_spans,(first,))-1),bisect_right(self.qt_spans,(last,float('inf'))))
        paint_key=(id(view),self.key,pair,indices.start,indices.stop)
        if paint_key==self.paint_key:return
        selections=[]
        for index in indices:
            start,end=self.qt_spans[index]
            selection=QTextEdit.ExtraSelection();selection.cursor=QTextCursor(view.document())
            selection.cursor.setPosition(start)
            selection.cursor.setPosition(end,QTextCursor.MoveMode.KeepAnchor)
            selection.format.setBackground(QColor('#fff16a' if (start,end)==pair else '#e7d65b'))
            selection.format.setForeground(QColor('#272410'));selections.append(selection)
        view.setExtraSelections(selections);self.state(view,self.current_index(view)>=0);self.paint_key=paint_key;self.painted=bool(selections)
