"""Per-view source presentation with one authoritative document and history."""
import re
from PySide6.QtCore import Qt, QTimer, QByteArray, QRectF, QPoint, QPointF
from PySide6.QtGui import (QColor, QFont, QPainter, QTextCursor, QTextCharFormat,
    QSyntaxHighlighter, QKeySequence, QImage, QPen, QTextBlockFormat)
from PySide6.QtWidgets import QTextEdit, QWidget, QTextBrowser, QMenu
from .markdown_source import source_text, python_position, utf16_position, SourceEdit
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


class Syntax(QSyntaxHighlighter):
    def __init__(self, editor):
        super().__init__(editor.document());self.editor = editor

    def highlightBlock(self, text):
        editor = self.editor
        number = self.currentBlock().blockNumber()
        if number >= len(editor.lines):return
        item = editor.lines[number]
        current = editor.textCursor()
        selected_a = editor.document().findBlock(current.selectionStart()).blockNumber()
        selected_b = editor.document().findBlock(current.selectionEnd()).blockNumber()
        reveal = editor.mode == 'source' or selected_a <= number <= selected_b
        def apply(a, b, values):
            begin, end = utf16_position(text, a), utf16_position(text, b)
            format_ = char_style(values, editor.tokens()['info'])
            for offset in range(begin, end):
                combined = self.format(offset);combined.merge(format_);self.setFormat(offset, 1, combined)
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
            apply(0, len(text), {'code': True});return
        if item['heading']:
            heading = item['heading'];level = len(heading.group(2))
            if editor.mode == 'write':apply(heading.start(3), heading.end(3), {'bold': True, 'size': (25, 21, 18, 16, 14, 13)[level-1]})
            syntax(0, heading.start(3))
        if item['listing']:
            syntax(len(item['listing'].group(1)), item['listing'].start(4))
        elif text.lstrip().startswith('>'):
            syntax(0, re.match(r'^\s*>\s?', text).end())
        elif re.fullmatch(r'\s{0,3}([-*_])(?:\s*\1){2,}\s*', text):syntax(0, len(text))
        for span in spans(text):
            if editor.mode == 'write':apply(span.first, span.last, span.style)
            syntax(span.start, span.first);syntax(span.last, span.end)
        if editor.mode == 'write':
            # Resolve nested foreground/background styles together. An explicit
            # author-selected foreground always wins over automatic ink.
            for offset in range(utf16_position(text,len(text))):
                current_format=self.format(offset)
                if current_format.background().style() != Qt.BrushStyle.NoBrush and current_format.foreground().style() == Qt.BrushStyle.NoBrush:
                    color=display_style({'background':current_format.background().color().name()})['color']
                    current_format.setForeground(QColor(color));self.setFormat(offset,1,current_format)
        for match in re.finditer(r'\]\([^)]*\)', text):syntax(match.start(), match.end())
        for offset in escaped_markers(text):syntax(offset,offset+1)


class Gutter(QWidget):
    def __init__(self, editor):super().__init__(editor);self.editor = editor
    def paintEvent(self, event):self.editor.paint_gutter(event)
    def mousePressEvent(self, event):
        for target, number in getattr(self.editor, 'task_targets', []):
            if target.contains(event.position()+self.pos()):self.editor.toggle_task(number);return
        block = self.editor.firstVisibleBlock()
        while block.isValid():
            top = self.editor.blockBoundingGeometry(block).translated(self.editor.contentOffset()).top()
            height = self.editor.blockBoundingRect(block).height()
            if top <= event.position().y() < top+height:
                self.editor.toggle_fold(block.blockNumber());return
            block = block.next()


class Markers(QWidget):
    def __init__(self, editor):
        super().__init__(editor);self.editor=editor
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)

    def paintEvent(self, event):
        painter=QPainter(self)
        self.editor.paint_markers(painter)


class SourceView(QTextEdit):
    """View-local layout/highlighting, but edits and undo belong to the buffer."""
    def __init__(self, surface):
        super().__init__(surface)
        self.surface, self.page = surface, surface.page
        self.mode = 'write';self.syncing = False;self.forwarding = False
        self.lines = line_info(self.page.buffer.snapshot().text)
        self.folded = set();self.last_block = 0
        self.setPlainText(self.page.buffer.snapshot().text)
        self.setUndoRedoEnabled(False)
        self.setAcceptRichText(False)
        self.setFrameShape(QTextEdit.Shape.NoFrame)
        self.setAccessibleName('Markdown 編寫區')
        self.setPlaceholderText('開始撰寫…')
        self.setStyleSheet('QTextEdit {border:0;padding:0;background:transparent;}')
        self.gutter = Gutter(self)
        self.markers = Markers(self);self.markers.raise_()
        self.highlighter = Syntax(self)
        self.document().contentsChange.connect(self.forward_change)
        self.page.buffer.text.contentsChange.connect(self.receive_change)
        self.cursorPositionChanged.connect(self.cursor_changed)
        self.document().documentLayout().update.connect(lambda rect: self.update_markers())
        self.verticalScrollBar().valueChanged.connect(self.update_markers)
        self.horizontalScrollBar().valueChanged.connect(self.update_markers)
        self.update_metadata()

    def forward_change(self, position, removed, added):
        if self.syncing:return
        previous = self.page.buffer.snapshot().text
        latest = source_text(self.document())
        if previous == latest:return  # Highlighting changes layout, not source.
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
        if self.forwarding:return
        latest = self.page.buffer.snapshot().text
        previous = source_text(self.document())
        if latest == previous:return
        self.syncing = True
        try:
            a = python_position(latest, position)
            b = python_position(latest, position+added)
            cursor = QTextCursor(self.document())
            cursor.setPosition(min(position, self.document().characterCount()-1))
            cursor.setPosition(min(position+removed, self.document().characterCount()-1), QTextCursor.MoveMode.KeepAnchor)
            cursor.insertText(latest[a:b])
            # Whole-document reload notifications may include Qt's final marker.
            if source_text(self.document()) != latest:
                saved = self.textCursor().position()
                self.setPlainText(latest)
                cursor = self.textCursor();cursor.setPosition(min(saved, self.document().characterCount()-1));self.setTextCursor(cursor)
        finally:self.syncing = False
        self.update_metadata()

    def update_metadata(self):
        self.lines = line_info(source_text(self.document()))
        if hasattr(self, 'highlighter'):self.highlighter.rehighlight()
        self.update_measure()
        self.update_paragraphs()
        self.folded.intersection_update(range(len(self.lines)))
        self.apply_folds()
        self.gutter.update()

    def cursor_changed(self):
        number = self.textCursor().blockNumber()
        for index in {number, self.last_block}:
            block = self.document().findBlockByNumber(index)
            if block.isValid():self.highlighter.rehighlightBlock(block)
        self.last_block = number
        if hasattr(self.surface, 'statistics'):self.surface.update_statistics()
        if hasattr(self.surface, 'format_actions'):self.surface.update_controls()

    def tokens(self):return visual_tokens(window_appearance(self))

    def update_markers(self, *args):self.gutter.update();self.markers.update()

    def firstVisibleBlock(self):return self.cursorForPosition(QPoint(0,0)).block()

    def blockBoundingGeometry(self, block):return self.document().documentLayout().blockBoundingRect(block)

    def blockBoundingRect(self, block):return self.blockBoundingGeometry(block)

    def contentOffset(self):return QPointF(-self.horizontalScrollBar().value(),-self.verticalScrollBar().value())

    def update_measure(self):
        preferences = self.surface.preferences
        margin = max(24, (self.width()-preferences.get('reading_width',880))//2) if self.mode == 'write' else 16
        gutter = 60 if preferences.get('line_numbers') else 22
        self.setViewportMargins(margin+gutter, 22, margin, 16)
        self.gutter.setGeometry(margin, self.viewport().y(), gutter, self.viewport().height())
        self.markers.setGeometry(self.rect());self.markers.raise_();self.markers.update()

    def update_paragraphs(self):
        block = self.document().begin()
        while block.isValid():
            number = block.blockNumber()
            if number >= len(self.lines):break
            item = self.lines[number];fmt = block.blockFormat();original = QTextBlockFormat(fmt)
            fmt.setLeftMargin(22 if self.mode == 'write' and (item['listing'] or item['text'].lstrip().startswith('>')) else 0)
            fmt.setAlignment(Qt.AlignmentFlag.AlignLeft)
            fmt.setTopMargin(0);fmt.setBottomMargin(0)
            if self.mode == 'write':
                if item['heading']:
                    fmt.setTopMargin(6 if number else 2);fmt.setBottomMargin(6)
                for span in spans(item['text']):
                    if span.start == 0 and span.end == len(item['text']):
                        if span.style.get('align'):fmt.setAlignment({'left':Qt.AlignmentFlag.AlignLeft,'center':Qt.AlignmentFlag.AlignHCenter,'right':Qt.AlignmentFlag.AlignRight,'justify':Qt.AlignmentFlag.AlignJustify}[span.style['align']])
                        if span.style.get('indent'):fmt.setLeftMargin(span.style['indent']*self.fontMetrics().horizontalAdvance('M'))
            height = self.surface.preferences.get('line_height',150) if self.mode == 'write' else 130
            if self.mode == 'write':
                if not item['text'].strip():height = 65
                elif item['heading']:height = 120
            fmt.setLineHeight(height, QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)
            if fmt != original:
                cursor=QTextCursor(block);cursor.setBlockFormat(fmt)
            block = block.next()

    def paintEvent(self, event):
        super().paintEvent(event)
        self.markers.update()

    def paint_markers(self, painter):
        self.task_targets = []
        if self.mode != 'write':return
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setClipRect(self.viewport().geometry().adjusted(-24,0,0,0))
        t=self.tokens();painter.setPen(QPen(QColor(t['secondary']),1.3))
        current=self.textCursor();first=self.document().findBlock(current.selectionStart()).blockNumber();last=self.document().findBlock(current.selectionEnd()).blockNumber()
        block=self.firstVisibleBlock()
        while block.isValid():
            number=block.blockNumber()
            top=self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            if top > self.viewport().height():break
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
            block=block.next()

    def toggle_task(self, number):
        if self.surface.mode == 'read' or self.page.buffer.composing:return
        item=self.lines[number];listing=item['listing']
        if listing and listing.group(3) is not None:
            start=item['start']+listing.start(3)
            self.surface.apply([SourceEdit(start,start+1,' ' if listing.group(3).lower()=='x' else 'x')])

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for target,number in getattr(self,'task_targets',[]):
                if target.contains(event.position()+self.viewport().pos()):self.toggle_task(number);event.accept();return
        super().mousePressEvent(event)

    def fold_end(self, number):
        item = self.lines[number]
        if item['heading']:
            level = len(item['heading'].group(2))
            return next((entry['number'] for entry in self.lines[number+1:] if entry['heading'] and len(entry['heading'].group(2)) <= level), len(self.lines))
        if item['listing']:
            indent = len(item['listing'].group(1))
            end = next((entry['number'] for entry in self.lines[number+1:] if entry['text'].strip() and (not entry['listing'] or len(entry['listing'].group(1)) <= indent)), len(self.lines))
            return end if any(entry['listing'] for entry in self.lines[number+1:end]) else number+1
        return number+1

    def toggle_fold(self, number):
        if self.fold_end(number) <= number+1:return
        self.folded.symmetric_difference_update({number});self.apply_folds()

    def fold_all(self, folded):
        self.folded = {i for i in range(len(self.lines)) if self.fold_end(i) > i+1} if folded else set()
        self.apply_folds()

    def apply_folds(self):
        hidden = set()
        for number in self.folded:hidden.update(range(number+1, self.fold_end(number)))
        block = self.document().begin()
        while block.isValid():
            shown = block.blockNumber() not in hidden
            block.setVisible(shown);block.setLineCount(1 if shown else 0)
            block = block.next()
        self.document().markContentsDirty(0, self.document().characterCount())
        self.viewport().update();self.gutter.update();self.markers.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'gutter'):self.update_measure()

    def paint_gutter(self, event):
        painter = QPainter(self.gutter);painter.setPen(QColor(self.tokens()['secondary']))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        while block.isValid():
            top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
            if top > self.viewport().height():break
            if block.isVisible():
                number = block.blockNumber()
                if self.surface.preferences.get('line_numbers'):painter.drawText(3, int(top)+self.fontMetrics().ascent(), str(number+1))
                if number < len(self.lines) and self.fold_end(number) > number+1:
                    line=block.layout().lineAt(0) if block.layout().lineCount() else None
                    x = self.gutter.width()-12;y = int(top+(line.height()/2 if line else 9))
                    if number in self.folded:painter.drawLine(x, y-3, x+3, y);painter.drawLine(x+3, y, x, y+3)
                    else:painter.drawLine(x-2, y-2, x+1, y+1);painter.drawLine(x+1, y+1, x+4, y-2)
            block = block.next()

    def keyPressEvent(self, event):
        if self.surface.mode == 'read':event.ignore();return
        if event.matches(QKeySequence.StandardKey.Undo):self.page.undo();return
        if event.matches(QKeySequence.StandardKey.Redo):self.page.redo();return
        if event.matches(QKeySequence.StandardKey.Find):self.surface.open_find();return
        if event.key() == Qt.Key.Key_Escape and self.surface.find_bar.isVisible():self.surface.find_bar.hide();return
        if self.page.buffer.composing:return super().keyPressEvent(event)
        if event.modifiers() == Qt.KeyboardModifier.ControlModifier:
            command = {Qt.Key.Key_B: {'bold': True}, Qt.Key.Key_I: {'italic': True}, Qt.Key.Key_U: {'underline': True}}.get(event.key())
            if command:self.surface.toggle_style(next(iter(command)));return
            if event.key() == Qt.Key.Key_H:self.surface.open_find(True);return
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
        if self.surface.mode == 'read':event.ignore();return
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

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        menu.addAction('復原', self.page.undo).setEnabled(self.page.document().isUndoAvailable())
        menu.addAction('重做', self.page.redo).setEnabled(self.page.document().isRedoAvailable())
        menu.addSeparator()
        menu.addAction('剪下', self.cut).setEnabled(self.textCursor().hasSelection())
        menu.addAction('複製', self.copy).setEnabled(self.textCursor().hasSelection())
        menu.addAction('貼上', self.paste)
        menu.addAction('全選', self.selectAll)
        menu.addSeparator();menu.addAction('清除格式', self.surface.clear_format)
        menu.exec(event.globalPos())


class ReadingView(QTextBrowser):
    def __init__(self, surface):
        super().__init__(surface);self.surface = surface
        self.setOpenLinks(False);self.setOpenExternalLinks(False)
        self.setFrameShape(QTextBrowser.Shape.NoFrame)
        self.setAccessibleName('Markdown 閱讀區')
        self.setStyleSheet('QTextBrowser {border:0;padding:0;background:transparent;}')
        self.selectionChanged.connect(surface.update_statistics)

    def apply_preferences(self):
        p=self.surface.preferences;t=visual_tokens(window_appearance(self))
        self.document().setDefaultFont(QFont(p['reading_family'],p['reading_size']))
        self.document().setDefaultStyleSheet(f'''p {{margin-top:8px;margin-bottom:8px;line-height:{p['line_height']}%;}}
            h1,h2,h3,h4,h5,h6 {{margin-top:22px;margin-bottom:10px;font-weight:600;}}
            h1 {{font-size:25pt;}} h2 {{font-size:21pt;}} h3 {{font-size:18pt;}}
            h4 {{font-size:16pt;}} h5 {{font-size:14pt;}} h6 {{font-size:13pt;}}
            pre,code {{font-family:Consolas;}} blockquote {{margin-left:22px;color:{t['secondary']};}}''')
        self.update_measure()

    def update_measure(self):
        margin=max(24,(self.width()-self.surface.preferences.get('reading_width',880))//2)
        self.setViewportMargins(margin+22,26,margin,16)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self,'surface'):self.update_measure()

    def loadResource(self, kind, url):
        if url.toString() not in ('pcs-task:checked','pcs-task:open'):return QByteArray()
        image=QImage(28,28,QImage.Format.Format_ARGB32_Premultiplied);image.fill(Qt.GlobalColor.transparent);image.setDevicePixelRatio(2)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        t=visual_tokens(window_appearance(self));painter.setPen(QPen(QColor(t['secondary']),1.3));painter.drawRoundedRect(QRectF(1,1,12,12),1.5,1.5)
        if url.toString().endswith('checked'):
            painter.drawLine(3,7,6,10);painter.drawLine(6,10,11,3)
        painter.end();return image

    def refresh(self):
        scroll = self.verticalScrollBar().value()
        previous=self.textCursor();anchor,position=previous.anchor(),previous.position()
        self.setHtml(reading_html(self.surface.page.buffer.snapshot().text, self.surface.preferences.get('hard_breaks', False)))
        cursor=self.textCursor();maximum=self.document().characterCount()-1
        cursor.setPosition(min(anchor,maximum));cursor.setPosition(min(position,maximum),QTextCursor.MoveMode.KeepAnchor);self.setTextCursor(cursor)
        self.verticalScrollBar().setValue(scroll)
