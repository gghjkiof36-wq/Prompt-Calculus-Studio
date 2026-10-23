"""Per-view source presentation with one authoritative document and history."""
import re
from bisect import bisect_right
from PySide6.QtCore import Qt, QTimer, QByteArray, QRectF, QPoint, QEvent, Slot
from PySide6.QtGui import (QColor, QFont, QPainter, QTextCursor, QTextCharFormat,
    QKeySequence, QImage, QPen, QTextBlockFormat, QTextLayout)
from PySide6.QtWidgets import QTextEdit, QPlainTextEdit, QWidget, QTextBrowser
from .widgets import RoundMenu as QMenu
from .markdown_layout import SourceLayout
from .markdown_source import source_text, changed_source, python_position, utf16_position, SourceEdit
from .markdown_syntax import spans, line_info, reading_html, display_style, escaped_markers
from .markdown_commands import continuation, paragraph_edits
from .theme import visual_tokens, window_appearance, font_pixels


def char_style(values, code_color='#a8b7bd'):
    result = QTextCharFormat()
    if 'bold' in values:result.setFontWeight(QFont.Weight.Bold if values['bold'] else QFont.Weight.Normal)
    if 'italic' in values:result.setFontItalic(values['italic'])
    if 'underline' in values:result.setFontUnderline(values['underline'])
    if 'strike' in values:result.setFontStrikeOut(values['strike'])
    if values.get('family'):result.setFontFamilies([values['family']])
    if values.get('size'):result.setFontPointSize(values['size'])
    if values.get('color'):result.setForeground(QColor(values['color']))
    if values.get('background'):result.setBackground(QColor(values['background']))
    if values.get('code'):result.setFontFamilies(['Consolas']);result.setForeground(QColor(code_color))
    if values.get('vertical'):
        result.setVerticalAlignment(QTextCharFormat.VerticalAlignment.AlignSubScript if values['vertical'] == 'sub' else QTextCharFormat.VerticalAlignment.AlignSuperScript)
    return result


class Syntax:
    def __init__(self, editor):
        self.editor=editor

    def currentBlock(self):return self._block

    def format(self,position):
        return QTextCharFormat(self._formats[position]) if self._formats and self._formats[position] else QTextCharFormat()

    def setFormat(self,start,length,format_):
        if not length:return
        if self._formats is None:self._formats=[None]*(self._block.length()-1)
        self._formats[start:start+length]=[format_]*length

    def mergeFormat(self,start,length,format_):
        if not length:return
        if self._formats is None:self._formats=[None]*(self._block.length()-1)
        stop=start+length
        while start<stop:
            previous=self._formats[start];end=start+1
            while end<stop and self._formats[end] is previous:end+=1
            merged=QTextCharFormat(previous) if previous is not None else QTextCharFormat()
            merged.merge(format_);self._formats[start:end]=[merged]*(end-start);start=end

    def resolve_contrast(self):
        start=0
        while self._formats and start<len(self._formats):
            original=self._formats[start];end=start+1
            while end<len(self._formats) and self._formats[end] is original:end+=1
            if original is not None and original.background().style()!=Qt.BrushStyle.NoBrush and original.foreground().style()==Qt.BrushStyle.NoBrush:
                adjusted=QTextCharFormat(original)
                adjusted.setForeground(QColor(display_style({'background':original.background().color().name()})['color']))
                self._formats[start:end]=[adjusted]*(end-start)
            start=end

    def rehighlightBlock(self,block):
        if not block.isValid():return
        self._block=block;self._formats=None;self.highlightBlock(block.text())
        ranges=[];formats=self._formats or []
        start=0
        while start<len(formats):
            fmt=formats[start];end=start+1
            while end<len(formats) and (formats[end] is fmt or formats[end]==fmt):end+=1
            if fmt is not None:
                item=QTextLayout.FormatRange();item.start=start;item.length=end-start;item.format=fmt;ranges.append(item)
            start=end
        layout=block.layout()
        if layout.formats()!=ranges:
            layout.setFormats(ranges)
            was_syncing=self.editor.syncing;self.editor.syncing=True
            try:self.editor.document().markContentsDirty(block.position(),block.length())
            finally:self.editor.syncing=was_syncing
        self._block=None;self._formats=None

    def rehighlight(self):
        first,last=self.editor._format_window if self.editor.lazy_presentation else (0,len(self.editor.lines))
        for number in range(first,last):self.rehighlightBlock(self.editor.document().findBlockByNumber(number))

    def highlightBlock(self, text):
        editor = self.editor
        number = self.currentBlock().blockNumber()
        if getattr(editor,'_suspend_highlight',False):return
        if number >= len(editor.lines):return
        item = editor.lines[number]
        current = editor.textCursor()
        selected_a = editor.document().findBlock(current.selectionStart()).blockNumber()
        selected_b = editor.document().findBlock(current.selectionEnd()).blockNumber()
        reveal = editor.mode == 'source' or selected_a <= number <= selected_b
        def apply(a, b, values, replace=False):
            begin, end = utf16_position(text, a), utf16_position(text, b)
            values = dict(values)
            if values.get('size'):values['size'] *= editor.surface.zoom_percent/100
            format_ = char_style(values, editor.tokens()['info'])
            if replace:self.setFormat(begin,end-begin,format_);return
            self.mergeFormat(begin,end-begin,format_)
        def syntax(a, b):
            format_ = QTextCharFormat();format_.setForeground(QColor(editor.tokens()['secondary']))
            if not reveal:
                # The source is still present; only inactive-line glyph metrics
                # collapse. Entering that line reveals the exact original syntax.
                font = QFont(editor.font());font.setPointSizeF(.1);font.setStretch(1)
                font.setHintingPreference(QFont.HintingPreference.PreferNoHinting)
                format_.setFont(font);format_.setForeground(QColor(0, 0, 0, 0))
            self.setFormat(utf16_position(text, a), utf16_position(text, b)-utf16_position(text, a), format_)
        if item['protected']:
            apply(0, len(text), {'code': True},True);return
        if item['heading']:
            heading = item['heading'];level = len(heading.group(2))
            if editor.mode == 'write':apply(heading.start(3), heading.end(3), {'bold': True, 'size': (25, 21, 18, 16, 14, 13)[level-1]*editor.surface.preferences['reading_size']/12},True)
            syntax(0, heading.start(3))
        if item['listing']:
            syntax(len(item['listing'].group(1)), item['listing'].start(4))
        elif text.lstrip().startswith('>'):
            syntax(0, re.match(r'^\s*>\s?', text).end())
        elif re.fullmatch(r'\s{0,3}([-*_])(?:\s*\1){2,}\s*', text):syntax(0, len(text))
        text_spans=spans(text)
        for span in text_spans:
            if editor.mode == 'write':apply(span.first, span.last, span.style)
            syntax(span.start, span.first);syntax(span.last, span.end)
        if editor.mode == 'write' and any(span.style.get('background') for span in text_spans):
            # Resolve nested foreground/background styles together. An explicit
            # author-selected foreground always wins over automatic ink.
            self.resolve_contrast()
        if '[' in text and '](' in text:
            from .markdown_links import links
            for link in links(text):
                if link.wiki:continue
                first=link.start+(2 if link.embed else 1)
                last=first+len(link.label)
                # Collapse a complete inline link as a pair. Keep reference
                # definitions, escaped syntax and code as their original text.
                if text[last:last+2]!='](':continue
                suffix=text[link.target_end:link.end]
                if text[link.target_start-1:link.target_start]=='<':suffix=suffix[1:]
                if not re.fullmatch(r'''(?:\s+(["'])(?:\\.|(?!\1)[^\n])*\1)?\s*\)''',suffix):continue
                opening=link.start
                if link.embed and (opening-len(text[:opening].rstrip('\\')))%2:
                    opening+=1  # An escaped ! is prose, not an image marker.
                syntax(opening,first);syntax(last,link.end)
        if '\\' in text:
            for offset in escaped_markers(text,text_spans):syntax(offset,offset+1)


class Gutter(QWidget):
    def __init__(self, editor):
        super().__init__(editor);self.editor = editor;self.setMouseTracking(True)
        self.setAccessibleName('文件折疊控制')
    def paintEvent(self, event):self.editor.paint_gutter(event)
    def mouseMoveEvent(self,event):
        number=self.editor.fold_number_at(event.position())
        self.setCursor(Qt.CursorShape.PointingHandCursor if number is not None else Qt.CursorShape.ArrowCursor)
        self.setToolTip(('展開內容' if number in self.editor.folded else '收合內容') if number is not None else '')
        super().mouseMoveEvent(event)
    def mousePressEvent(self, event):
        if event.button()!=Qt.MouseButton.LeftButton:return super().mousePressEvent(event)
        for target, number in getattr(self.editor, 'task_targets', []):
            if target.contains(event.position()+self.pos()):self.editor.toggle_task(number);return
        number=self.editor.fold_number_at(event.position())
        if number is not None:self.editor.toggle_fold(number);event.accept();return
        super().mousePressEvent(event)

class Markers(QWidget):
    def __init__(self, editor):
        super().__init__(editor);self.editor=editor
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event):
        painter=QPainter(self)
        self.editor.paint_markers(painter)


class SourceView(QPlainTextEdit):
    def insertPlainText(self,text):
        if self.page.buffer.opening:return
        super().insertPlainText(text)
    def mouseReleaseEvent(self,event):
        if hasattr(self.surface,'advanced') and self.surface.advanced.rectangle is not None:
            self.surface.advanced.rectangle=None;event.accept();return
        if event.button()==Qt.MouseButton.LeftButton and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            cursor=self.cursorForPosition(event.position().toPoint())
            self.surface.open_current_link(python_position(self.surface.page.buffer.snapshot().text,cursor.position()))
            event.accept();return
        super().mouseReleaseEvent(event)
    """View-local layout/highlighting, but edits and undo belong to the buffer."""
    def __init__(self, surface):
        super().__init__(surface)
        self.surface, self.page = surface, surface.page
        self.mode = 'write';self.syncing = False;self.forwarding = False
        self._metadata_snapshot=self.page.buffer.snapshot()
        self.lines = line_info('' if self.page.buffer.opening else self._metadata_snapshot.text)
        self._line_starts=[item['start'] for item in self.lines];self._source_change=None
        self._format_window=(0,160);self._presented=set();self._updating_presentation=False
        self.folded = set();self.last_block = 0
        self.source_layout=SourceLayout(self.document(),self)
        self.document().setDocumentLayout(self.source_layout)
        # The surface installs its final font, mode and margins before enabling
        # layout. Do not lay out a long document several times at startup.
        self.document().setLayoutEnabled(False)
        self.setPlainText('' if self.page.buffer.opening else self.page.buffer.snapshot().text)
        self.setReadOnly(self.page.buffer.opening)
        self.setUndoRedoEnabled(False)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setAccessibleName('Markdown 編寫區')
        self.setPlaceholderText('')
        self.setStyleSheet('QPlainTextEdit {border:0;padding:0;background:transparent;}')
        self.gutter = Gutter(self)
        self.markers = Markers(self);self.markers.raise_()
        self.highlighter = Syntax(self)
        self.presentation_timer=QTimer(self);self.presentation_timer.setSingleShot(True)
        self.presentation_timer.timeout.connect(self.present_visible)
        self.document().contentsChange.connect(self.forward_change)
        self.page.buffer.text.contentsChange.connect(self.receive_change)
        self.cursorPositionChanged.connect(self.cursor_changed)
        self.selectionChanged.connect(self.update_markers)
        self.document().documentLayout().update.connect(lambda rect: self.update_markers())
        self.verticalScrollBar().valueChanged.connect(self.update_markers)
        self.verticalScrollBar().valueChanged.connect(self.schedule_presentation)
        self.horizontalScrollBar().valueChanged.connect(self.update_markers)
        self.update_metadata()

    def forward_change(self, position, removed, added):
        if self.syncing or self.page.buffer.opening:return
        previous = self.page.buffer.snapshot().text
        latest = changed_source(self.document(),previous,position,removed,added)
        if previous == latest:return  # Highlighting changes layout, not source.
        self._source_change=(position,removed,added)
        a = python_position(previous, position);b = python_position(previous, position+removed)
        finish = python_position(latest, position+added)
        inserted = latest[a:finish]
        # IME may report the whole paragraph, including Qt's final sentinel,
        # although only the commit changed. Trim within that authoritative
        # range so unchanged source slices and mixed line endings stay intact.
        if self.page.buffer.composing:
            while a < b and inserted and previous[a] == inserted[0]:
                a += 1;inserted = inserted[1:]
            while a < b and inserted and previous[b-1] == inserted[-1]:
                b -= 1;inserted = inserted[:-1]
            self.ime_inserted_range=(a,a+len(inserted))
        if previous[:a]+inserted+previous[b:] != latest:
            self.surface.report('這次輸入的位置無法核對，請保留目前文字並切換原文檢查。')
            return
        self.forwarding = True
        try:
            cursor = QTextCursor(self.page.buffer.text)
            cursor.setPosition(utf16_position(previous, a))
            cursor.setPosition(utf16_position(previous, b), QTextCursor.MoveMode.KeepAnchor)
            if self.page.buffer.composing:cursor.beginEditBlock()
            try:cursor.insertText(inserted)
            finally:
                if self.page.buffer.composing:cursor.endEditBlock()
        finally:self.forwarding = False
        self.update_metadata()

    def receive_change(self, position, removed, added):
        if self.forwarding or self.page.buffer.opening:return
        latest = self.page.buffer.snapshot().text
        previous = self._metadata_snapshot.text
        if latest == previous:return
        self._source_change=(position,removed,added)
        self.syncing = True
        try:
            a = python_position(latest, position)
            b = python_position(latest, position+added)
            cursor = QTextCursor(self.document())
            cursor.setPosition(min(position, self.document().characterCount()-1))
            cursor.setPosition(min(position+removed, self.document().characterCount()-1), QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(latest[a:b])
            # Whole-document reload notifications may include Qt's final marker.
            if ((len(latest)<256_000 and source_text(self.document()) != latest)
                    or self.document().characterCount()-1 != utf16_position(latest,len(latest))):
                saved = self.textCursor().position()
                self.setPlainText(latest)
                cursor = self.textCursor();cursor.setPosition(min(saved, self.document().characterCount()-1));self.setTextCursor(cursor)
        finally:self.syncing = False
        self.update_metadata()

    def update_metadata(self):
        if self.page.buffer.opening:return
        was_syncing=self.syncing;self.syncing=True
        transaction=QTextCursor(self.document());transaction.beginEditBlock()
        try:
            previous=self.lines
            snapshot=self.page.buffer.snapshot()
            unchanged=self._metadata_snapshot is snapshot
            before=self._metadata_snapshot.text
            self._metadata_snapshot=snapshot
            changed_line=None
            if not unchanged:
                prepared = self.page.buffer.cached_syntax(snapshot.text)
                change=self._source_change;self._source_change=None
                if change and prepared is None:
                    position,removed,added=change
                    at=python_position(before,position)
                    n=max(0,bisect_right(self._line_starts,at)-1);old=previous[n]
                    delta=len(snapshot.text)-len(before);end=old['end']+delta
                    line=snapshot.text[old['start']:end]
                    if (python_position(before,position+removed)<=old['end'] and '\n' not in line
                            and not old['protected'] and not old['listing'] and not old['heading']
                            and not re.search(r'[`~#>*+\-$]|^\s*\d+[.)]|^\s*<|^\s*\|',line)
                            and not line.startswith(('    ','\t'))):
                        item=line_info(line)[0];item.update(number=n,start=old['start'],end=end)
                        self.lines=previous[:n]+[item]+[dict(x,start=x['start']+delta,end=x['end']+delta) for x in previous[n+1:]]
                        changed_line=n
                        if getattr(self,'_fold_lines',None) is previous:self._fold_lines=self.lines
                if prepared is not None:self.lines=prepared
                elif changed_line is None:self.lines=self.line_metadata(snapshot.text,previous)
                self.page.buffer.remember_syntax(before,previous)
                self.page.buffer.remember_syntax(snapshot.text,self.lines)
                self._line_starts=[item['start'] for item in self.lines]
            key=(self.mode,self.surface.zoom_percent,tuple(sorted(self.surface.preferences.items())),str(self.tokens()))
            if unchanged and getattr(self,'_presentation_key',None)==key:
                self.update_measure();return
            first=0;last=len(self.lines)
            same_presentation=getattr(self,'_presentation_key',None)==key
            if same_presentation and not self.lazy_paragraphs:
                equal=lambda a,b:(a['text'],a['protected'],a['display_marker'])==(b['text'],b['protected'],b['display_marker'])
                while first<min(len(previous),last) and equal(previous[first],self.lines[first]):first+=1
                suffix=0
                while suffix<min(len(previous),last)-first and equal(previous[-1-suffix],self.lines[-1-suffix]):suffix+=1
                last-=suffix
            paragraph_first,paragraph_last=first,last
            self._presentation_key=key
            lazy=self.lazy_presentation
            if lazy:
                if same_presentation and changed_line is not None:self._presented.discard(changed_line)
                else:self._presented.clear()
                first,last=self.presentation_range();self._format_window=(first,last)
            self.update_measure()
            self._suspend_highlight=True
            try:
                if self.lazy_paragraphs:self.update_paragraphs(first,last)
                else:self.update_paragraphs(paragraph_first,paragraph_last)
            finally:self._suspend_highlight=False
            if hasattr(self, 'highlighter'):
                if not lazy and (not getattr(self,'_highlight_initialized',False) or (first==0 and last==len(self.lines))):self.highlighter.rehighlight()
                else:
                    for number in range(first,last):self.highlighter.rehighlightBlock(self.document().findBlockByNumber(number))
                self._highlight_initialized=True
            if lazy:self._presented.update(range(first,last))
            if self.folded:self.folded.intersection_update(range(len(self.lines)))
            self.apply_folds()
            self.gutter.update()
        finally:
            transaction.endEditBlock()
            self.syncing=was_syncing

    @property
    def lazy_presentation(self):
        # Dense tables/lists can contain thousands of blocks in less than
        # 128 KiB. Bound syntax highlighting to the visible window too.
        return len(self.lines)>1000

    @property
    def lazy_paragraphs(self):return len(self.lines)>10000

    def presentation_range(self):
        if not self.document().isLayoutEnabled():return 0,min(160,len(self.lines))
        first=self.firstVisibleBlock().blockNumber()
        last=self.cursorForPosition(QPoint(0,self.viewport().height())).blockNumber()
        # While Qt is relaying out after a font/width change, a hit test can
        # transiently return the final block. Bound work to a viewport, never
        # turn that provisional result into formatting the whole manuscript.
        last=min(last,first+max(100,self.viewport().height()//4+60))
        return max(0,first-30),min(len(self.lines),max(first+100,last+30))

    def schedule_presentation(self,*args):
        if self.lazy_presentation and not self.syncing and not self._updating_presentation:self.presentation_timer.start(0)

    def present_visible(self):
        if self.surface.mode=='read' or self.surface.page.buffer.opening or not self.isVisible():return
        if not self.lazy_presentation or self.syncing or self._updating_presentation:return
        first,last=self.presentation_range();self._format_window=(first,last)
        numbers=set(range(first,last));numbers.update(block.blockNumber() for block in self.visible_blocks())
        pending=sorted(numbers-self._presented)
        if not pending:return
        cursor=self.textCursor()
        keep_cursor=(self.lazy_paragraphs and cursor.block().isVisible()
                     and self.viewport().rect().intersects(self.cursorRect(cursor)))
        transaction=QTextCursor(self.document());self._updating_presentation=True;self.syncing=True
        transaction.beginEditBlock()
        try:
            if self.lazy_paragraphs:
                self._suspend_highlight=True
                try:
                    start=previous=pending[0]
                    for number in pending[1:]:
                        if number!=previous+1:self.update_paragraphs(start,previous+1);start=number
                        previous=number
                    self.update_paragraphs(start,previous+1)
                finally:self._suspend_highlight=False
            for number in pending:self.highlighter.rehighlightBlock(self.document().findBlockByNumber(number))
            self._presented.update(pending)
        finally:
            transaction.endEditBlock();self.syncing=False;self._updating_presentation=False
        if self.lazy_paragraphs:
            # SourceLayout changes paragraph heights without changing the
            # native line count. Refresh the scrollbar limit once, otherwise
            # ensureCursorVisible can be clamped above the newly laid-out tail.
            layout=self.document().documentLayout()
            layout.documentSizeChanged.emit(layout.documentSize())
        # Lazy line heights can push an already-visible caret below the view.
        # Preserve that navigation intent, but do not pull a manually scrolled
        # viewport back to a caret the user has deliberately left off screen.
        if keep_cursor and self.textCursor()==cursor and cursor.block().isVisible():
            self.ensureCursorVisible()
        self.update_markers()

    @staticmethod
    def line_metadata(text,previous):
        raw=text.split('\n')
        if len(raw)==len(previous):
            changed=[i for i,line in enumerate(raw) if line!=previous[i]['text']]
            if not changed:return previous
            if len(changed)==1:
                n=changed[0];old=previous[n];line=raw[n]
                # Inline prose edits cannot change the fence/list state of the
                # following lines. Structural edits still use the full parser.
                if (not old['protected'] and not old['listing'] and not old['heading']
                        and not re.match(r'^[ \t>]*(?:[#`~$<]|[-+*]\s|\d+[.)]\s|---)',line)
                        and not line.startswith(('    ','\t'))):
                    item=line_info(line)[0];shift=len(line)-len(old['text'])
                    item.update(number=n,start=old['start'],end=old['start']+len(line))
                    result=previous[:n]+[item]
                    result.extend(dict(entry,start=entry['start']+shift,end=entry['end']+shift) for entry in previous[n+1:])
                    return result
        return line_info(text)

    def cursor_changed(self):
        if self.page.buffer.opening:return
        if self.syncing:return
        number = self.textCursor().blockNumber()
        for index in {number, self.last_block}:
            block = self.document().findBlockByNumber(index)
            if block.isValid():self.highlighter.rehighlightBlock(block)
        self.last_block = number
        self.update_markers()
        if hasattr(self.surface, 'statistics'):self.surface.update_statistics()
        if hasattr(self.surface,'outline'):self.surface.outline.sync_current()
        if hasattr(self.surface, 'format_actions'):self.surface.update_controls()

    def tokens(self):
        if not hasattr(self,'_tokens_cache'):self._tokens_cache=visual_tokens(window_appearance(self))
        return self._tokens_cache

    def update_markers(self, *args):self.gutter.update();self.markers.update()

    def firstVisibleBlock(self):return super().firstVisibleBlock()

    def blockBoundingGeometry(self, block):return super().blockBoundingGeometry(block)

    def blockBoundingRect(self, block):return super().blockBoundingRect(block)

    def contentOffset(self):return super().contentOffset()

    def paint_selections(self,selections):super().setExtraSelections(selections)

    def update_measure(self):
        preferences = self.surface.preferences
        margin = max(24, (self.width()-preferences.get('reading_width',880))//2)
        gutter = 60 if preferences.get('line_numbers') else 22
        self.setViewportMargins(margin+gutter, 22, margin, 16)
        self.gutter.setGeometry(margin, self.viewport().y(), gutter, self.viewport().height())
        self.markers.setGeometry(self.rect());self.markers.raise_();self.markers.update()

    def update_paragraphs(self,first=0,last=None):
        block = self.document().findBlockByNumber(first)
        transaction=QTextCursor(self.document());transaction.beginEditBlock()
        writing=self.mode=='write'
        normal_height=self.surface.preferences.get('line_height',150) if writing else 130
        formats={}
        while block.isValid() and (last is None or block.blockNumber()<last):
            number=block.blockNumber()
            if number>=len(self.lines):break
            item=self.lines[number]
            left=22 if writing and (item['listing'] or item['text'].lstrip().startswith('>')) else 0
            align=Qt.AlignmentFlag.AlignLeft;top=bottom=0;height=normal_height
            if writing:
                if item['heading']:top=6 if number else 2;bottom=6;height=120
                if item['text'].startswith('<'):
                    for span in spans(item['text']):
                        if span.start==0 and span.end==len(item['text']):
                            if span.style.get('align'):align={'left':Qt.AlignmentFlag.AlignLeft,'center':Qt.AlignmentFlag.AlignHCenter,'right':Qt.AlignmentFlag.AlignRight,'justify':Qt.AlignmentFlag.AlignJustify}[span.style['align']]
                            if span.style.get('indent'):left=span.style['indent']*self.fontMetrics().horizontalAdvance('M')
            # QTextDocument interns formats. Reuse a computed result only for
            # the same original format and desired geometry in this update.
            # Every paragraph is still applied before returning; no lazy height.
            key=(block.blockFormatIndex(),left,align,top,bottom,height)
            if key not in formats:
                original=block.blockFormat();fmt=QTextBlockFormat(original)
                fmt.setLeftMargin(left);fmt.setAlignment(align)
                fmt.setTopMargin(top);fmt.setBottomMargin(bottom)
                fmt.setLineHeight(height,QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
                formats[key]=fmt if fmt!=original else None
            fmt=formats[key]
            if fmt is not None:QTextCursor(block).setBlockFormat(fmt)
            block=block.next()
        transaction.endEditBlock()

    def visible_blocks(self):
        for block, top in self.visible_block_positions():yield block

    def visible_block_positions(self):
        if self.surface.mode=='read' or not self.isVisible() or self.surface.page.buffer.opening:return
        block=self.firstVisibleBlock();remaining=max(100,self.viewport().height()//4+60)
        if not block.isValid():return
        # blockBoundingGeometry walks from the first visible paragraph each
        # time. Accumulate once so gutter/markers cost O(visible paragraphs),
        # rather than repeating that walk for every row on every arrow key.
        top=self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        while block.isValid() and remaining:
            if top>self.viewport().height():break
            remaining-=1
            if block.isVisible():
                yield block,top
                top+=self.blockBoundingRect(block).height()
            number=block.blockNumber()
            block=self.document().findBlockByNumber(self.fold_end(number)) if number in self.folded else block.next()

    def paint_markers(self, painter):
        self.task_targets = []
        if self.mode != 'write':return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(self.viewport().geometry().adjusted(-24,0,0,0))
        t=self.tokens();painter.setPen(QPen(QColor(t['secondary']),1.3))
        current=self.textCursor();first=self.document().findBlock(current.selectionStart()).blockNumber();last=self.document().findBlock(current.selectionEnd()).blockNumber()
        for block,top in self.visible_block_positions():
            number=block.blockNumber()
            if block.isVisible() and number < len(self.lines) and not first <= number <= last:
                item=self.lines[number];listing=item['listing']
                if listing:
                    local=QTextCursor(block);local.setPosition(block.position()+utf16_position(item['text'],listing.start(4)))
                    rect=self.cursorRect(local).translated(self.viewport().pos());x=rect.left()-20;y=rect.top()+max(2,(rect.height()-14)/2)
                    target=QRectF(x,y,14,14)
                    if listing.group(3) is not None:
                        painter.drawRoundedRect(target,1.5,1.5)
                        if listing.group(3).lower()=='x':
                            painter.drawLine(int(x+3),int(y+7),int(x+6),int(y+10));painter.drawLine(int(x+6),int(y+10),int(x+12),int(y+3))
                        self.task_targets.append((target.adjusted(-3,-3,3,3),number))
                    elif listing.group(2) in '-+*':
                        painter.setBrush(QColor(t['secondary']));painter.drawEllipse(QRectF(x+5,y+5,4,4));painter.setBrush(Qt.BrushStyle.NoBrush)
                    else:painter.drawText(target.adjusted(-10,-4,5,5), Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,item['display_marker'])
                elif item['text'].lstrip().startswith('>'):
                    x=self.viewport().x()-12;y=self.viewport().y()+top
                    painter.drawLine(x,int(y+2),x,int(y+self.blockBoundingRect(block).height()-2))
                elif not item['protected'] and re.fullmatch(r'\s{0,3}([-*_])(?:\s*\1){2,}\s*',item['text']):
                    y=int(self.viewport().y()+top+self.blockBoundingRect(block).height()/2);painter.drawLine(self.viewport().x()+4,y,self.viewport().geometry().right()-6,y)

    def toggle_task(self, number):
        if self.surface.mode == 'read' or self.page.buffer.composing:return
        item=self.lines[number];listing=item['listing']
        if listing and listing.group(3) is not None:
            start=item['start']+listing.start(3)
            self.surface.apply([SourceEdit(start,start+1,' ' if listing.group(3).lower()=='x' else 'x')])

    def mousePressEvent(self, event):
        if hasattr(self.surface,'advanced') and self.surface.advanced.mouse_press(event):event.accept();return
        if event.button() == Qt.MouseButton.LeftButton:
            for target,number in getattr(self,'task_targets',[]):
                if target.contains(event.position()+self.viewport().pos()):self.toggle_task(number);event.accept();return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if hasattr(self.surface,'advanced') and self.surface.advanced.mouse_move(event):event.accept();return
        super().mouseMoveEvent(event)

    def setExtraSelections(self,selections):
        self.base_selections=list(selections)
        advanced=getattr(self.surface,'advanced',None)
        super().setExtraSelections(self.base_selections+(advanced.extras() if advanced else []))

    def fold_end(self, number):
        if getattr(self,'_fold_lines',None) is not self.lines:
            self._fold_lines=self.lines;self._fold_ends={}
            headings=[];lists=[]
            for index,item in enumerate(self.lines):
                if item['heading']:
                    level=len(item['heading'].group(2))
                    while headings and headings[-1][1]>=level:
                        start,_=headings.pop();self._fold_ends[start]=index
                    headings.append((index,level))
                if not item['text'].strip():continue
                indent=len(item['listing'].group(1)) if item['listing'] else -1
                while lists and lists[-1][1]>=indent:
                    start,_,children=lists.pop()
                    if children:self._fold_ends[start]=index
                if item['listing']:
                    if lists:lists[-1][2]=True
                    lists.append([index,indent,False])
            for start,_ in headings:self._fold_ends[start]=len(self.lines)
            for start,_,children in lists:
                if children:self._fold_ends[start]=len(self.lines)
        return self._fold_ends.get(number,number+1)

    def toggle_fold(self, number):
        if self.fold_end(number) <= number+1:return
        self.folded.symmetric_difference_update({number});self.apply_folds()

    def fold_all(self, folded):
        self.folded = {i for i in range(len(self.lines)) if self.fold_end(i) > i+1} if folded else set()
        self.apply_folds()

    def apply_folds(self):
        if not self.folded and not getattr(self,'_had_folds',False):return
        self._had_folds=bool(self.folded)
        hidden = set()
        for number in self.folded:hidden.update(range(number+1, self.fold_end(number)))
        block = self.document().begin()
        while block.isValid():
            shown = block.blockNumber() not in hidden
            block.setVisible(shown);block.setLineCount(1 if shown else 0)
            block = block.next()
        self.document().markContentsDirty(0, self.document().characterCount())
        self.viewport().update();self.gutter.update();self.markers.update()
        self.schedule_presentation()

    def resizeEvent(self, event):
        if hasattr(self, 'gutter'):self.update_measure()
        super().resizeEvent(event)
        # Align within this resize, before the first paint can expose the
        # title's previous (often far-left) position.
        if hasattr(self.surface,'editor'):
            self.surface.update_title_layout()
            self.surface.title_layout.activate()
        if hasattr(self,'presentation_timer'):self.schedule_presentation()

    def fold_control_rect(self,block):
        top=self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        line=block.layout().lineAt(0) if block.layout().lineCount() else None
        center=top+(line.y()+line.height()/2 if line else self.fontMetrics().height()/2)
        return QRectF(self.gutter.width()-20,center-9,18,18)

    def fold_number_at(self,point):
        for block in self.visible_blocks():
            number=block.blockNumber()
            if block.isVisible() and number<len(self.lines) and self.fold_end(number)>number+1:
                if self.fold_control_rect(block).adjusted(-2,-3,2,3).contains(point):return number
        return None

    def paint_gutter(self, event):
        from .ui_icons import icon
        painter = QPainter(self.gutter);painter.setPen(QColor(self.tokens()['secondary']))
        painter.setFont(self.font())
        for block,top in self.visible_block_positions():
            if block.isVisible():
                number = block.blockNumber()
                if self.surface.preferences.get('line_numbers'):painter.drawText(3, int(top)+self.fontMetrics().ascent(), str(number+1))
                if number < len(self.lines) and self.fold_end(number) > number+1:
                    icon('chevron-right' if number in self.folded else 'chevron-down',self.tokens()['secondary']).paint(painter,self.fold_control_rect(block).toRect())

    def keyPressEvent(self, event):
        if self.surface.mode == 'read' or self.page.buffer.opening:event.ignore();return
        if event.key()==Qt.Key.Key_V and event.modifiers()==(Qt.KeyboardModifier.ControlModifier|Qt.KeyboardModifier.ShiftModifier):
            self.paste_plain();event.accept();return
        if hasattr(self.surface,'advanced') and self.surface.advanced.key(event):event.accept();return
        if event.matches(QKeySequence.StandardKey.Undo):self.page.undo();return
        if event.matches(QKeySequence.StandardKey.Redo):self.page.redo();return
        if event.matches(QKeySequence.StandardKey.Find):
            shortcut=self.surface.command_shortcuts.get('Ctrl+F')
            if shortcut and shortcut.key()==QKeySequence('Ctrl+F'):self.surface.open_find();return
        if event.key() == Qt.Key.Key_Escape and self.surface.find_bar.isVisible():self.surface.close_panel(self.surface.find_bar);return
        if self.page.buffer.composing:return super().keyPressEvent(event)
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            command = {Qt.Key.Key_B: {'bold': True}, Qt.Key.Key_I: {'italic': True}, Qt.Key.Key_U: {'underline': True}}.get(event.key())
            if command:self.surface.toggle_style(next(iter(command)));return
            if event.key() == Qt.Key.Key_H:
                shortcut=self.surface.command_shortcuts.get('Ctrl+H')
                if shortcut and shortcut.key()==QKeySequence('Ctrl+H'):self.surface.open_find(True);return
            if event.key() == Qt.Key.Key_Space:self.toggle_task(self.textCursor().blockNumber());return
        cursor = self.textCursor()
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not cursor.hasSelection() and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            snapshot = self.page.buffer.snapshot();at = python_position(snapshot.text, cursor.position())
            if not self.lines[cursor.blockNumber()]['protected']:
                edit = continuation(snapshot.text, at)
                self.surface.apply([edit], edit.start+len(edit.replacement));return
        if event.key() in (Qt.Key.Key_Tab, Qt.Key.Key_Backtab):
            command = 'outdent' if event.key() == Qt.Key.Key_Backtab or event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 'indent'
            self.surface.paragraph(command, self.surface.preferences.get('tab_width', 4));return
        if event.text() and not (event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier)):
            if self.surface.typing_style or self.surface.clear_next:
                self.surface.insert_styled(event.text());return
            if self.surface.preferences.get('auto_pairs') and event.text() in '([{"\'':
                pair = {'(': ')', '[': ']', '{': '}', '"': '"', "'": "'"}[event.text()]
                snapshot, start, end = self.surface.selection()
                selected = snapshot.text[start:end]
                self.surface.apply([SourceEdit(start, end, event.text()+selected+pair)], start+1+len(selected));return
        super().keyPressEvent(event)

    def inputMethodEvent(self, event):
        if self.surface.mode == 'read' or self.page.buffer.opening:event.ignore();return
        if hasattr(self.surface,'advanced'):self.surface.advanced.clear()
        token = id(self.page);self.page.buffer.composing.add(token)
        self.ime_inserted_range=None
        try:super().inputMethodEvent(event)
        finally:
            if not event.preeditString():self.page.buffer.composing.discard(token)
            self.page.buffer.compositionChanged.emit()
        committed=getattr(self,'ime_inserted_range',None)
        if event.commitString() and committed and self.surface.clear_next and not self.page.buffer.composing:
            self.surface.clear_next=False
            self.surface.clear_range(*committed,join_previous=True)
        elif event.commitString() and committed and self.surface.typing_style and not self.page.buffer.composing:
            text=source_text(self.document());start,end=(utf16_position(text,value) for value in committed)
            cursor = self.textCursor();cursor.setPosition(start);cursor.setPosition(end,QTextCursor.MoveMode.KeepAnchor)
            self.setTextCursor(cursor)

            self.surface.format_selection(self.surface.typing_style, join_previous=True)
            self.surface.typing_style = {}
            cursor = self.textCursor()
            cursor.setPosition(max(start, cursor.selectionEnd()-len('</span>')))
            self.setTextCursor(cursor)

    def undo(self):self.page.undo()
    def redo(self):self.page.redo()

    def paste_plain(self):
        if self.isReadOnly() or self.surface.mode=='read' or self.page.buffer.composing:return
        from PySide6.QtWidgets import QApplication
        mime=QApplication.clipboard().mimeData()
        if mime is None or not mime.hasText():return
        text=mime.text().replace('\r\n','\n').replace('\r','\n')
        if not text:return
        advanced=getattr(self.surface,'advanced',None)
        if advanced and advanced.cursors:
            advanced.multi_insert(text);return
        _,start,end=self.surface.selection()
        self.surface.apply([SourceEdit(start,end,text)],start+len(text))

    def insertFromMimeData(self,mime):
        if self.isReadOnly() or self.surface.mode=='read' or self.page.buffer.composing:return
        from .markdown_paste import clipboard_text, MARKDOWN_MIMES
        from .markdown_attachments import insert
        has_markdown=any(mime.hasFormat(kind) for kind in MARKDOWN_MIMES)
        # Copy Image may contain an HTML <img> wrapper as well as the actual
        # bitmap. Preserve that local bitmap instead of a remote/blob URL.
        bitmap_first=mime.hasImage() and not mime.text().strip() and not has_markdown
        if (has_markdown or mime.hasHtml()) and not bitmap_first:
            if mime.hasHtml() and not has_markdown:
                from .markdown_paste_job import PreparedPaste, LARGE_HTML
                html=mime.html()
                if len(html)>LARGE_HTML:
                    if not hasattr(self,'prepared_paste'):self.prepared_paste=PreparedPaste(self)
                    self.prepared_paste.start(html)
                    return
            try:
                text=clipboard_text(mime)
                if text:
                    _,start,end=self.surface.selection()
                    self.surface.apply([SourceEdit(start,end,text)],start+len(text))
                return
            except (ValueError, UnicodeError) as error:
                self.surface.report(str(error)+' 可使用 Ctrl+Shift+V 貼上純文字。')
                return
        if mime.hasUrls():
            files=[url.toLocalFile() for url in mime.urls() if url.isLocalFile()]
            if files:insert(self.surface,files=files);return
        if mime.hasImage():
            image=mime.imageData()
            if not isinstance(image,QImage):image=QImage(image)
            insert(self.surface,image=image);return
        text=clipboard_text(mime)
        if text:
            _,start,end=self.surface.selection()
            self.surface.apply([SourceEdit(start,end,text)],start+len(text))

    def canInsertFromMimeData(self,mime):
        from .markdown_paste import MARKDOWN_MIMES
        return any(mime.hasFormat(kind) for kind in MARKDOWN_MIMES) or mime.hasImage() or mime.hasUrls() or mime.hasHtml() or super().canInsertFromMimeData(mime)

    def dropEvent(self,event):
        if event.mimeData().hasImage() or any(url.isLocalFile() for url in event.mimeData().urls()):
            if self.page.buffer.composing or self.surface.mode=='read':event.ignore();return
            self.setTextCursor(self.cursorForPosition(event.position().toPoint()))
            self.insertFromMimeData(event.mimeData());event.acceptProposedAction();return
        super().dropEvent(event)

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if hasattr(self.surface,'advanced'):self.surface.advanced.spelling_menu(menu,self.cursorForPosition(event.pos()).position())
        menu.addAction('復原', self.page.undo).setEnabled(self.page.document().isUndoAvailable())
        menu.addAction('重做', self.page.redo).setEnabled(self.page.document().isRedoAvailable())
        menu.addSeparator()
        menu.addAction('剪下', self.cut).setEnabled(self.textCursor().hasSelection())
        menu.addAction('複製', self.copy).setEnabled(self.textCursor().hasSelection())
        menu.addAction('貼上', self.paste)
        menu.addAction('貼上純文字\tCtrl+Shift+V',self.paste_plain)
        menu.addAction('全選', self.selectAll)
        menu.addSeparator();menu.addAction('清除格式', self.surface.clear_format)
        menu.open_at(event.globalPos())


class ReadingView(QTextBrowser):
    def __init__(self, surface):
        super().__init__(surface);self.surface = surface
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setAccessibleName('Markdown 閱讀區')
        self.rich=None;self.advanced=False
        self.setStyleSheet('QTextBrowser {border:0;padding:0;background:transparent;}')
        self.selectionChanged.connect(surface.update_statistics)

    def event(self,event):
        handled=super().event(event)
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show) and getattr(self,'rich',None):
            self.fit_rich()
        return handled

    def apply_preferences(self):
        p=self.surface.preferences;t=visual_tokens(window_appearance(self))
        scale = self.surface.zoom_percent/100
        font = QFont(p['reading_family']);font.setPointSizeF(p['reading_size']*scale);self.document().setDefaultFont(font)
        heading = [round(n*p['reading_size']/12*scale,2) for n in (25,21,18,16,14,13)]
        self.document().setDefaultStyleSheet(f'''p {{margin-top:8px;margin-bottom:8px;line-height:{p['line_height']}%;}}
            h1,h2,h3,h4,h5,h6 {{margin-top:22px;margin-bottom:10px;font-weight:600;}}
            h1 {{font-size:{heading[0]}pt;}} h2 {{font-size:{heading[1]}pt;}} h3 {{font-size:{heading[2]}pt;}}
            h4 {{font-size:{heading[3]}pt;}} h5 {{font-size:{heading[4]}pt;}} h6 {{font-size:{heading[5]}pt;}}
            pre,code {{font-family:Consolas;}} blockquote {{margin-left:22px;color:{t['secondary']};}}''')
        self.update_measure()

    def update_measure(self):
        margin=max(24,(self.width()-self.surface.preferences.get('reading_width',880))//2)
        self.setViewportMargins(margin+22,26,margin,16)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self,'surface'):self.update_measure()
        if getattr(self,'rich',None):QTimer.singleShot(0,self,self.fit_rich)

    def setFocus(self,*args):
        if self.advanced and self.rich:self.rich.setFocus(*args)
        else:super().setFocus(*args)

    def selected_text(self):
        return self.rich.selectedText() if self.advanced and self.rich else self.textCursor().selectedText()

    def scrollToAnchor(self, name):
        if self.advanced and self.rich:
            import json
            self.rich.page().runJavaScript('document.getElementById('+json.dumps(name)+')?.scrollIntoView()')
        else:super().scrollToAnchor(name)

    def hideEvent(self,event):
        if self.rich:self.rich.pause()
        super().hideEvent(event)

    def showEvent(self,event):
        super().showEvent(event)
        QTimer.singleShot(0,self,self._refresh_on_show)

    def _refresh_on_show(self):
        if self.isVisible() and self.surface.mode=='read':self.refresh_cached()
        self.fit_rich()

    def fit_rich(self):
        if self.rich:
            self.rich.setGeometry(self.rect());self.rich.raise_();self.rich.sync_title_alignment()

    def loadResource(self, kind, url):
        if url.toString() not in ('pcs-task:checked','pcs-task:open'):return QByteArray()
        image=QImage(28,28,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(2)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t=visual_tokens(window_appearance(self));painter.setPen(QPen(QColor(t['secondary']),1.3));painter.drawRoundedRect(QRectF(1,1,12,12),1.5,1.5)
        if url.toString().endswith('checked'):
            painter.drawLine(3,7,6,10);painter.drawLine(6,10,11,3)
        painter.end();return image

    def render_key(self):
        from .markdown_relations import reading_root
        snapshot=self.surface.page.buffer.snapshot()
        return (snapshot.revision,snapshot.document_id,str(self.surface.page.path),str(reading_root(self.surface)),tuple(sorted(self.surface.preferences.items())),self.surface.zoom_percent,str(visual_tokens(window_appearance(self))))

    def _invalidate_cache(self):
        # Dependency changes invalidate this view, never the source buffer.
        self._render_key=None

    def root_changed(self):
        self._invalidate_cache()
        if self.rich:
            self.rich.discard_request()
            # Synchronous projections have no request guard for late browser
            # completions. Retire their version as well as their resource scope.
            if self.rich.read_request is None:self.rich.version+=1
            self.rich.gate.allowed.clear();self.rich.image_sources.clear()
            self.rich.render_state='stale'
            self.rich.relations.timer.start()
        elif self.isVisible() and self.surface.mode=='read':self.refresh_cached()

    def refresh_cached(self):
        if getattr(self,'_render_key',None)!=self.render_key():self.refresh()

    def replace_html(self, html):
        document=self.document();enabled=document.isLayoutEnabled()
        document.setLayoutEnabled(False)
        try:self.setHtml(html)
        finally:document.setLayoutEnabled(enabled)

    def refresh(self):
        scroll = self.verticalScrollBar().value()
        previous=self.textCursor();anchor,position=previous.anchor(),previous.position()
        source=self.surface.page.buffer.snapshot().text
        from .markdown_render import advanced
        # The native rich-text importer becomes quadratic with many blocks.
        # Large manuscripts use the same local renderer as tables and media;
        # never build an invisible second rich-text layout for those documents.
        # Source formatting can be lazy before the native reader needs to be
        # replaced. Keep ordinary medium notes on their existing reading path.
        large=len(source)>128_000 or self.surface.editor.lazy_paragraphs
        complex_content=advanced(source)
        self.advanced=large or complex_content
        self.viewport().setVisible(not self.advanced)
        policy=Qt.ScrollBarPolicy.ScrollBarAlwaysOff if self.advanced else Qt.ScrollBarPolicy.ScrollBarAsNeeded
        self.setVerticalScrollBarPolicy(policy);self.setHorizontalScrollBarPolicy(policy)
        if self.advanced:
            if self.rich is None:
                # Keep the reader in the widget stack. Promoting this scroll
                # area to a native window during a mode change can hide it
                # and promote its siblings/ancestors on Windows. WebEngine's
                # texture child must share the shell's normal composition.
                from .markdown_rich_view import RichView
                self.rich=RichView(self);self.rich.setGeometry(self.rect());self.rich.setZoomFactor(self.surface.zoom_percent/100)
            if large and not complex_content:
                editor=self.surface.editor
                lines=editor.lines if editor._metadata_snapshot is self.surface.page.buffer.snapshot() else None
                if not hasattr(self,'plain_projection'):
                    from .markdown_read_projection import PlainReadingProjection
                    self.plain_projection=PlainReadingProjection(self)
                    self.plain_projection.ready.connect(self.finish_plain_projection)
                # Invalidate an older WebEngine completion before the new
                # projection is ready to be sent to it.
                self.rich.version+=1;self.rich.render_state='loading'
                self.plain_projection.request(self.render_key(),source,self.surface.preferences.get('hard_breaks',False),lines)
            else:self.rich.refresh(source)
            # The WebEngine projection is the only visible document. Importing
            # the same HTML into a hidden QTextDocument adds layout work and
            # synchronous resource decoding to every ordinary note opening.
            self.clear()
            self.rich.show();self.rich.raise_()
            QTimer.singleShot(0,self,self.fit_rich)
        else:
            if self.rich:self.rich.pause();self.rich.hide()
            self.replace_html(reading_html(source, self.surface.preferences.get('hard_breaks', False)))
        cursor=self.textCursor();maximum=self.document().characterCount()-1
        cursor.setPosition(min(anchor,maximum));cursor.setPosition(min(position,maximum),QTextCursor.MoveMode.KeepAnchor);self.setTextCursor(cursor)
        self.verticalScrollBar().setValue(scroll)
        self._render_key=self.render_key()

    @Slot(object,str,str)
    def finish_plain_projection(self,key,body,error):
        if key!=self.render_key():return
        if self.surface.mode!='read':
            self._render_key=None;return
        if error:
            self._render_key=None;self.rich.render_state='error'
            self.surface.report('內容預覽未完成；原文仍可編輯。');return
        from .markdown_render import Projection
        self.rich.refresh(self.surface.page.buffer.snapshot().text,projection=Projection(body=body))
