"""View-local find highlights; never format or modify the source document."""
from PySide6.QtCore import QObject
from PySide6.QtGui import QColor, QTextCursor
from PySide6.QtWidgets import QTextEdit
from .markdown_source import utf16_position, python_position


class SearchHighlight(QObject):
    def __init__(self,surface):
        super().__init__(surface);self.surface=surface
        surface.editor.selectionChanged.connect(self.update)
        surface.reader.selectionChanged.connect(self.update)

    def state(self,view,active):
        if bool(view.property('searchHit'))==active:return
        view.setProperty('searchHit',active);view.style().unpolish(view);view.style().polish(view)
        view.viewport().update()

    def clear(self):
        for view in (self.surface.editor,self.surface.reader):
            view.setExtraSelections([]);self.state(view,False)

    def update(self):
        s=self.surface
        if not s.find_bar.isVisible() or not s.query.text():self.clear();return
        view=s.reader if s.mode=='read' else s.editor
        other=s.editor if view is s.reader else s.reader
        other.setExtraSelections([]);self.state(other,False)
        if s.mode=='read' and s.reader.advanced:return
        matches=s.matches();text=view.toPlainText();current=view.textCursor()
        pair=(python_position(text,current.selectionStart()),python_position(text,current.selectionEnd()))
        selections=[]
        for start,end in matches:
            selection=QTextEdit.ExtraSelection();selection.cursor=QTextCursor(view.document())
            selection.cursor.setPosition(utf16_position(text,start))
            selection.cursor.setPosition(utf16_position(text,end),QTextCursor.MoveMode.KeepAnchor)
            selection.format.setBackground(QColor('#fff16a' if (start,end)==pair else '#e7d65b'))
            selection.format.setForeground(QColor('#272410'));selections.append(selection)
        view.setExtraSelections(selections);self.state(view,pair in matches)
