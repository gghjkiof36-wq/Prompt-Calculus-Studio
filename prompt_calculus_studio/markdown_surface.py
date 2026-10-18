"""Document-local Markdown controls; the workspace shell remains unchanged."""
import html
import json
import re
from PySide6.QtCore import Qt, QTimer, QSaveFile, QIODevice, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor, QColor, QKeySequence, QShortcut, QPixmap, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QSplitter, QToolButton, QComboBox, QSpinBox, QLineEdit, QLabel, QCheckBox,
    QListWidget, QListWidgetItem, QMenu, QColorDialog, QFontDialog, QGraphicsDropShadowEffect)
from .ui_icons import icon
from .markdown_source import SourceEdit, python_position, utf16_position, source_text
from .markdown_commands import format_edits, clear_edits, paragraph_edits, heading_sections, move_section_edits, selection_lines
from .markdown_syntax import COLORS, text_colors, style_css, visible_text, spans
from .markdown_view import SourceView, ReadingView
from .markdown_toolbar import make_toolbar
from .theme import visual_tokens, window_appearance
from .widgets import ElidedLabel


class Outline(QListWidget):
    def __init__(self, surface):
        super().__init__(surface);self.surface = surface
        self.setAccessibleName('文件大綱')
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.itemClicked.connect(self.navigate)
        self.setMinimumWidth(150)

    def navigate(self, item):
        self.surface.navigate(item.data(Qt.ItemDataRole.UserRole)[0])

    def dropEvent(self, event):
        source, target = self.currentItem(), self.itemAt(event.position().toPoint())
        if not source or not target or source is target:return event.ignore()
        start, end = source.data(Qt.ItemDataRole.UserRole)[:2]
        destination = target.data(Qt.ItemDataRole.UserRole)[0]
        if start <= destination < end:return event.ignore()
        snapshot = self.surface.page.buffer.snapshot()
        self.surface.apply(move_section_edits(snapshot, start, end, destination),
                           destination if destination < start else destination-(end-start))
        event.accept()


class MarkdownSurface(QWidget):
    def __init__(self, page):
        super().__init__(page)
        self.page = page;self.mode = 'write';self.typing_style = {};self.clear_next = False;self.message = ''
        self.preferences = dict(line_numbers=False, auto_pairs=True, wrap=True, tab_width=4,
                                hard_breaks=False, reading_width=880, reading_size=12,
                                reading_family='Microsoft JhengHei UI', line_height=150)
        self.preferences_loaded = False
        self.setObjectName('MarkdownSurface')
        body = QVBoxLayout(self);body.setContentsMargins(0, 0, 0, 0);body.setSpacing(0)
        top = QWidget(self);top.setObjectName('MarkdownToolbar')
        heading = QHBoxLayout(top);heading.setContentsMargins(20, 9, 18, 6);heading.setSpacing(5)
        self.breadcrumb = ElidedLabel('工作區 /', top);self.breadcrumb.setObjectName('MarkdownBreadcrumb')
        self.breadcrumb.setMinimumWidth(0);heading.addWidget(self.breadcrumb)
        page.file_title.setParent(top);page.file_title.setObjectName('MarkdownFileTitle')
        heading.addWidget(page.file_title, 1)
        self.mode_box = self.tool('編寫 · 切換文件視圖', self.mode_menu, 'pencil')
        self.mode_box.setObjectName('MarkdownMode')
        heading.addWidget(self.mode_box)
        self.view_tools = self.tool('搜尋、大綱與編輯偏好', self.view_menu, 'more-horizontal')
        self.view_tools.setObjectName('MarkdownViewTools')
        heading.addWidget(self.view_tools)
        body.addWidget(top)
        self.toolbar_host = QWidget(self)
        tools_layout = QHBoxLayout(self.toolbar_host);tools_layout.setContentsMargins(12, 2, 12, 7)
        self.toolbar = make_toolbar(self);tools_layout.addWidget(self.toolbar)
        self.toolbar_shadow = QGraphicsDropShadowEffect(self.toolbar)
        self.toolbar_shadow.setOffset(0, 3);self.toolbar_shadow.setBlurRadius(12)
        self.toolbar.setGraphicsEffect(self.toolbar_shadow)
        body.addWidget(self.toolbar_host)
        self.find_bar = QWidget(self);self.find_bar.setObjectName('MarkdownFind')
        find = QVBoxLayout(self.find_bar);find.setContentsMargins(20, 2, 20, 10);find.setSpacing(5)
        query_row = QHBoxLayout();self.query = QLineEdit();self.query.setPlaceholderText('搜尋此文件');self.query.setAccessibleName('搜尋此文件')
        query_row.setSpacing(4);self.query.setMinimumWidth(40)
        query_row.addWidget(self.query, 1)
        self.match_count = QLabel();query_row.addWidget(self.match_count)
        self.case_sensitive = QCheckBox('Aa');self.case_sensitive.setAccessibleName('區分大小寫');self.case_sensitive.setToolTip('區分大小寫');query_row.addWidget(self.case_sensitive)
        query_row.addWidget(self.tool('上一筆', lambda:self.find_next(-1), 'chevron-up'))
        query_row.addWidget(self.tool('下一筆', self.find_next, 'chevron-down'))
        query_row.addWidget(self.tool('關閉搜尋', lambda:self.close_panel(self.find_bar), 'close'));find.addLayout(query_row)
        self.replace_row = QWidget();replace_layout = QHBoxLayout(self.replace_row);replace_layout.setContentsMargins(0, 0, 0, 0)
        self.replacement = QLineEdit();self.replacement.setPlaceholderText('取代為');self.replacement.setAccessibleName('取代為');replace_layout.addWidget(self.replacement, 1)
        replace_layout.addWidget(self.tool('取代這一筆', self.replace_one, text='取代'))
        replace_layout.addWidget(self.tool('全部取代', self.replace_all, text='全部取代'))
        find.addWidget(self.replace_row);body.addWidget(self.find_bar);self.find_bar.hide();self.replace_row.hide()
        self.query.textChanged.connect(self.search_changed);self.case_sensitive.toggled.connect(self.search_changed)
        self.query.returnPressed.connect(self.find_next)
        self.command_box = QWidget();self.command_box.setObjectName('MarkdownCommands')
        commands = QVBoxLayout(self.command_box);commands.setContentsMargins(20, 2, 20, 8)
        self.command_query = QLineEdit();self.command_query.setPlaceholderText('搜尋命令');self.command_query.setAccessibleName('搜尋 Markdown 命令')
        self.command_list = QListWidget();self.command_list.setMaximumHeight(220)
        command_header = QHBoxLayout();command_header.addWidget(self.command_query, 1)
        command_header.addWidget(self.tool('關閉命令', lambda:self.close_panel(self.command_box), 'close'))
        commands.addLayout(command_header);commands.addWidget(self.command_list);body.addWidget(self.command_box);self.command_box.hide()
        self.command_query.textChanged.connect(self.refresh_commands)
        self.command_query.returnPressed.connect(self.run_selected_command)
        self.command_list.itemActivated.connect(self.run_selected_command)
        self.splitter = QSplitter();self.outline = Outline(self);self.outline.setObjectName('MarkdownOutline');self.outline.hide()
        self.splitter.addWidget(self.outline)
        self.stack = QStackedWidget();self.editor = SourceView(self);self.reader = ReadingView(self)
        self.stack.addWidget(self.editor);self.stack.addWidget(self.reader);self.splitter.addWidget(self.stack)
        self.splitter.setStretchFactor(1, 1);body.addWidget(self.splitter, 1)
        self.statistics = QLabel();self.statistics.setObjectName('MarkdownStatus');self.statistics.setMinimumHeight(28)
        self.statistics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);body.addWidget(self.statistics)
        self.refresh_timer = QTimer(self);self.refresh_timer.setSingleShot(True);self.refresh_timer.timeout.connect(self.refresh)
        page.buffer.text.contentsChanged.connect(lambda:self.refresh_timer.start(40))
        page.buffer.text.modificationChanged.connect(self.update_statistics)
        page.buffer.text.undoAvailable.connect(self.update_controls)
        page.buffer.text.redoAvailable.connect(self.update_controls)
        page.buffer.compositionChanged.connect(self.update_controls)
        self.commands = self.command_entries()
        for sequence, callback in [('Ctrl+F', self.open_find), ('Ctrl+H', lambda:self.open_find(True)), ('Ctrl+Shift+P', self.open_commands), ('Alt+F10', self.focus_toolbar)]:
            shortcut = QShortcut(QKeySequence(sequence), self);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);shortcut.activated.connect(callback)
        for panel in (self.command_box, self.find_bar, self.toolbar):
            shortcut = QShortcut(QKeySequence('Esc'), panel);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.focus_document if panel is self.toolbar else lambda p=panel:self.close_panel(p))
        self.apply_appearance();self.refresh()
        QTimer.singleShot(0, self.load_preferences)

    def tool(self, label, callback, symbol=None, text=None):
        control = QToolButton();control.setAccessibleName(label);control.setToolTip(label)
        control.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if symbol:control.setIcon(icon(symbol));control.setFixedSize(28,28)
        else:control.setText(text or label)
        control.clicked.connect(lambda checked=False:callback())
        return control

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'breadcrumb'):self.breadcrumb.setMaximumWidth(max(0, self.width()//3))

    def apply_appearance(self):
        t = visual_tokens(window_appearance(self))
        appearance_key = tuple(sorted(t.items()))
        if getattr(self, '_appearance_key', None) == appearance_key:return
        self._appearance_key = appearance_key
        light = QColor(t['base']).lightnessF() > .5
        fill = QColor(t['surface']);edge = QColor(t['text']);top_edge = QColor(t['text'])
        fill.setAlpha(230);edge.setAlpha(30 if light else 23);top_edge.setAlpha(20 if light else 36)
        rgba = lambda c:f'rgba({c.red()},{c.green()},{c.blue()},{c.alpha()})'
        self.toolbar_shadow.setColor(QColor(0,0,0,24 if light else 65))
        self.setStyleSheet(f'''QWidget#MarkdownSurface {{background:transparent;}}
            QWidget#MarkdownToolbar QToolButton {{padding:1px;min-width:0;min-height:0;border:1px solid transparent;border-radius:4px;background:transparent;}}
            QWidget#MarkdownToolbar QToolButton:hover {{background:{t['hover']};}}
            QWidget#MarkdownToolbar QToolButton#MarkdownViewTools, QWidget#MarkdownToolbar QToolButton#MarkdownMode {{min-width:24px;max-width:24px;}}
            QWidget#MarkdownToolbar QToolButton:focus {{border-color:{t['accent']};}}
            QWidget#MarkdownToolbar QComboBox {{background:transparent;border:0;padding:3px 23px 3px 6px;min-height:26px;}}
            QWidget#MarkdownToolbar QComboBox::drop-down {{width:20px;border:0;}}
            QLabel#MarkdownBreadcrumb {{color:{t['secondary']};font-weight:400;font-size:14px;}}
            QLabel#MarkdownFileTitle {{font-weight:400;font-size:14px;}}
            QToolBar#MarkdownRibbon {{background:{rgba(fill)};border:1px solid {rgba(edge)};border-top-color:{rgba(top_edge)};border-radius:7px;padding:2px;spacing:2px;}}
            QToolBar#MarkdownRibbon QToolButton, QFrame#MarkdownOverflow QToolButton {{background:transparent;border:1px solid transparent;border-radius:4px;padding:2px;min-width:24px;min-height:24px;}}
            QToolBar#MarkdownRibbon QToolButton:hover {{background:{t['hover']};}}
            QToolBar#MarkdownRibbon QToolButton:checked {{background:{t['selected']};}}
            QToolBar#MarkdownRibbon QToolButton:pressed {{background:{t['selected']};}}
            QToolBar#MarkdownRibbon QToolButton:focus {{border-color:{t['accent']};}}
            QToolBar#MarkdownRibbon QToolButton#qt_toolbar_ext_button {{min-width:27px;max-width:27px;padding:2px;border-radius:4px;}}
            QToolBar#MarkdownRibbon::separator {{width:6px;background:transparent;}}
            QFrame#MarkdownOverflow {{background:{t['surface']};border:1px solid {rgba(top_edge)};border-radius:7px;}}
            QFrame#MarkdownOverflow QToolButton:hover {{background:{t['hover']};}}
            QFrame#MarkdownOverflow QToolButton:checked {{background:{t['selected']};}}
            QFrame#MarkdownOverflow QToolButton:focus {{border-color:{t['accent']};}}
            QWidget#MarkdownFind QToolButton, QWidget#MarkdownCommands QToolButton {{padding:2px 6px;min-width:0;min-height:0;border-radius:4px;}}
            QWidget#MarkdownFind QToolButton:focus, QWidget#MarkdownCommands QToolButton:focus {{border-color:{t['accent']};}}
            QListWidget#MarkdownOutline {{border:0;background:transparent;padding:8px;}}
            QLabel#MarkdownStatus {{padding:0 20px;color:{t['secondary']};font-size:12px;}}
        ''')
        if hasattr(self, 'editor'):self.editor.update_metadata();self.reader.apply_preferences()

    def focus_document(self):
        (self.reader if self.mode == 'read' else self.editor).setFocus()

    def close_panel(self, panel):panel.hide();self.focus_document()

    def focus_toolbar(self):
        if self.mode == 'read':self.view_tools.setFocus();return
        for action in self.toolbar.actions():
            button=self.toolbar.widgetForAction(action)
            if button and button.isVisible() and button.isEnabled():button.setFocus();return

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, 'reader'):
            QTimer.singleShot(0, self, self.apply_appearance)

    def view_menu(self):
        menu = QMenu(self)
        menu.addAction(icon('search'), '搜尋此文件 · Ctrl+F', self.open_find)
        if self.mode != 'read':menu.addAction('取代此文件 · Ctrl+H', lambda:self.open_find(True))
        menu.addAction(icon('list'), '顯示／隱藏大綱', self.toggle_outline)
        menu.addAction('命令 · Ctrl+Shift+P', self.open_commands)
        menu.addSeparator();menu.addAction(icon('settings'), '編輯與閱讀偏好…', self.preferences_menu)
        menu.exec(self.view_tools.mapToGlobal(self.view_tools.rect().bottomLeft()))

    def load_preferences(self):
        workspace = self.page.file_title.workspace
        if workspace is None:return
        path = workspace.data/'md-preferences.json'
        try:
            saved = json.loads(path.read_text(encoding='utf-8'))
            for key, default in self.preferences.items():
                if type(saved.get(key)) is type(default):self.preferences[key] = saved[key]
        except (OSError, ValueError, TypeError):pass
        self.preferences['tab_width'] = 2 if self.preferences['tab_width'] == 2 else 4
        self.preferences['reading_width'] = max(480, min(1200, self.preferences['reading_width']))
        self.preferences['reading_size'] = max(9, min(32, self.preferences['reading_size']))
        self.preferences['line_height'] = max(120, min(200, self.preferences['line_height']))
        self.preferences_loaded = True;self.apply_preferences()

    def save_preferences(self):
        workspace = self.page.file_title.workspace
        if workspace:
            output = QSaveFile(str(workspace.data/'md-preferences.json'));output.setDirectWriteFallback(False)
            if output.open(QIODevice.OpenModeFlag.WriteOnly):
                raw = json.dumps(self.preferences).encode()
                if output.write(raw) != len(raw) or not output.commit():self.report('編輯偏好尚未保存，請稍後重試。')
        self.apply_preferences()

    def apply_preferences(self):
        self.editor.setLineWrapMode(self.editor.LineWrapMode.WidgetWidth if self.preferences['wrap'] else self.editor.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(' ')*self.preferences['tab_width'])
        self.editor.update_metadata();self.reader.apply_preferences();self.reader.refresh()
        self.update_breadcrumb()

    def preferences_menu(self):
        menu = QMenu(self)
        for key, label in [('line_numbers', '顯示行號'), ('auto_pairs', '自動配對括號'), ('wrap', '自動折行'), ('hard_breaks', '每個原文換行都顯示換行')]:
            action = menu.addAction(label);action.setCheckable(True);action.setChecked(self.preferences[key])
            action.toggled.connect(lambda value, k=key:self.set_preference(k, value))
        submenu = menu.addMenu('Tab 縮排')
        for number in (2, 4):submenu.addAction(str(number)+' 個空白', lambda value=number:self.set_preference('tab_width', value))
        submenu = menu.addMenu('閱讀行寬')
        for name, width in [('窄',640),('舒適',880),('寬',1120)]:
            entry=submenu.addAction(name, lambda value=width:self.set_preference('reading_width', value));entry.setCheckable(True);entry.setChecked(self.preferences['reading_width']==width)
        submenu = menu.addMenu('閱讀字級')
        for number in (10, 12, 14, 16, 18, 22):submenu.addAction(str(number)+' pt', lambda value=number:self.set_preference('reading_size', value))
        submenu = menu.addMenu('閱讀行距')
        for name, number in [('緊密',130),('標準',150),('寬鬆',180)]:submenu.addAction(name, lambda value=number:self.set_preference('line_height', value))
        def reading_font():
            initial=QFont(self.preferences['reading_family'], self.preferences['reading_size'])
            font, accepted=QFontDialog.getFont(initial, self, '個人閱讀字體')
            if accepted:self.preferences.update(reading_family=font.family(),reading_size=font.pointSize());self.save_preferences()
        menu.addAction('閱讀字體…', reading_font)
        menu.exec(self.view_tools.mapToGlobal(self.view_tools.rect().bottomLeft()))

    def set_preference(self, key, value):self.preferences[key] = value;self.save_preferences()

    def mode_menu(self):
        menu=QMenu(self)
        for key,label,symbol in [('write','編寫','pencil'),('source','原文','source-code'),('read','閱讀','book-open')]:
            action=menu.addAction(icon(symbol),label,lambda value=key:self.set_mode(value))
            action.setCheckable(True);action.setChecked(self.mode==key)
        menu.exec(self.mode_box.mapToGlobal(self.mode_box.rect().bottomLeft()))

    def set_mode(self, mode):
        if self.page.buffer.composing:self.report('請先完成目前的中文組字。');return
        if mode not in ('write', 'source', 'read'):return
        self.mode = mode
        label,symbol={'write':('編寫','pencil'),'source':('原文','source-code'),'read':('閱讀','book-open')}[mode]
        self.mode_box.setIcon(icon(symbol));self.mode_box.setToolTip(label+' · 切換文件視圖');self.mode_box.setAccessibleName(label+' · 切換文件視圖')
        self.toolbar_host.setVisible(mode != 'read');self.replace_row.setVisible(False)
        self.outline.setDragEnabled(mode != 'read')
        if mode == 'read':self.reader.refresh();self.stack.setCurrentWidget(self.reader);self.reader.setFocus()
        else:
            self.editor.mode = mode;self.editor.update_metadata();self.stack.setCurrentWidget(self.editor);self.editor.setFocus()
        self.search_changed();self.update_statistics();self.update_controls()

    def selection(self):
        snapshot = self.page.buffer.snapshot();cursor = self.editor.textCursor()
        return snapshot, python_position(snapshot.text, cursor.selectionStart()), python_position(snapshot.text, cursor.selectionEnd())

    def apply(self, edits, caret=None, *, join_previous=False):
        if self.mode == 'read':self.report('目前是閱讀模式，請切換至編寫或原文。');return False
        try:
            changed = self.page.buffer.apply(self.page.buffer.snapshot(), edits, join_previous=join_previous)
            if caret is not None:
                text = self.page.buffer.snapshot().text;cursor = self.editor.textCursor()
                cursor.setPosition(utf16_position(text, min(caret, len(text))));self.editor.setTextCursor(cursor)
            self.message = '';self.editor.setFocus();self.refresh();return changed
        except (ValueError, OSError) as error:self.report(str(error));return False

    def format_selection(self, style, *, join_previous=False):
        if self.mode == 'read':return self.report('閱讀模式不能修改格式。')
        if self.page.buffer.composing:return self.report('請先完成目前的中文組字。')
        snapshot, start, end = self.selection()
        if start == end:
            self.clear_next=False;self.typing_style.update(style);self.message = '接下來輸入的文字套用此樣式';self.update_statistics();self.editor.setFocus();return
        try:
            edits = format_edits(snapshot, start, end, style)
            # Keep selection around the complete styled range for further tools.
            growth = sum(len(e.replacement)-(e.end-e.start) for e in edits)
            if self.apply(edits, join_previous=join_previous):
                text = self.page.buffer.snapshot().text;cursor = self.editor.textCursor();cursor.setPosition(utf16_position(text, start));cursor.setPosition(utf16_position(text, end+growth), QTextCursor.MoveMode.KeepAnchor);self.editor.setTextCursor(cursor)
        except ValueError as error:self.report(str(error))

    def insert_styled(self, text):
        snapshot, start, end = self.selection();style = self.typing_style;self.typing_style = {}
        if self.clear_next:
            self.clear_next=False
            value=html.escape(text,quote=False)
            if self.apply([SourceEdit(start,end,value)],start+len(value)):
                self.clear_range(start,start+len(value),join_previous=True)
            return
        opening = '<span style="'+html.escape(style_css(style), quote=True)+'">'
        self.apply([SourceEdit(start, end, opening+html.escape(text, quote=False)+'</span>')], start+len(opening)+len(html.escape(text, quote=False)))

    def clear_format(self):
        if self.mode == 'read' or self.page.buffer.composing:return
        snapshot, start, end = self.selection()
        if start == end:
            self.typing_style = {};self.clear_next=True;self.message = '接下來輸入的文字不帶字元格式';self.update_statistics();self.focus_document();return
        self.apply(clear_edits(snapshot, start, end))

    def clear_range(self, start, end, *, join_previous=False):
        edits=clear_edits(self.page.buffer.snapshot(),start,end)
        caret=end+sum(len(edit.replacement)-(edit.end-edit.start) for edit in edits
                      if edit.end < end or edit.end == end and edit.start < end)
        self.apply(edits,caret,join_previous=join_previous)

    def paragraph(self, command, value=None):
        snapshot, start, end = self.selection();self.apply(paragraph_edits(snapshot, start, end, command, value))

    def indent_paragraph(self, direction):
        snapshot,start,end=self.selection();edits=[]
        for item in selection_lines(snapshot.text,start,end):
            if item['protected']:continue
            if item['listing']:
                command='indent' if direction > 0 else 'outdent';value=self.preferences['tab_width']
            else:
                existing=next((span.style.get('indent',0) for span in spans(item['text']) if span.start == 0 and span.end == len(item['text'])),0)
                command='paragraph_indent';value=max(0,min(12,existing+direction*2))
            edits.extend(paragraph_edits(snapshot,item['start'],item['end'],command,value))
        self.apply(edits)

    def font_dialog(self):
        values = self.active_style()
        initial = QFont(values.get('family', self.editor.font().family()))
        initial.setPointSizeF(values.get('size', max(12, self.editor.font().pointSizeF())))
        font, accepted = QFontDialog.getFont(initial, self, '文字字體與字號')
        if accepted:self.format_selection({'family': font.family(), 'size': font.pointSizeF()})

    def active_style(self):
        # History signals may precede another view's text synchronization.
        # Toolbar state must use that view's own text/cursor, never cross maps.
        text = source_text(self.editor.document())
        offset = self.editor.textCursor().selectionStart()
        try:start = python_position(text, offset)
        except UnicodeError:return dict(self.typing_style)
        line = text[:start].rfind('\n')+1
        stop = text.find('\n', start)
        values = {}
        for item in spans(text[line:stop if stop >= 0 else len(text)]):
            if item.first <= start-line <= item.last:values.update(item.style)
        values.update(self.typing_style)
        return values

    def toggle_style(self, key):
        snapshot, start, end = self.selection()
        if self.active_style().get(key):
            if start == end:
                self.typing_style[key] = False;self.update_controls();self.editor.setFocus();return
            self.apply(clear_edits(snapshot, start, end, keys={key}))
        else:self.format_selection({key:True})

    def update_controls(self, *args):
        if not hasattr(self, 'editor'):return
        ready = self.mode != 'read' and not self.page.buffer.composing
        self.toolbar.setEnabled(ready);self.mode_box.setEnabled(not self.page.buffer.composing)
        self.undo_action.setEnabled(ready and self.page.document().isUndoAvailable())
        self.redo_action.setEnabled(ready and self.page.document().isRedoAvailable())
        values = self.active_style()
        for key in ('bold','italic','underline','strike'):
            action=self.format_actions[key];action.blockSignals(True);action.setChecked(bool(values.get(key)));action.blockSignals(False)
        self.format_actions['font'].setToolTip('字體與字號：'+str(values.get('family',self.editor.font().family()))+' · '+str(values.get('size',12))+' pt')

    def update_breadcrumb(self):
        workspace = self.page.file_title.workspace
        try:parent = self.page.path.relative_to(workspace.root).parent if workspace else self.page.path.parent
        except ValueError:parent = self.page.path.parent
        parts = parent.parts if workspace else ()
        label = '工作區'+(' / '+' / '.join(parts) if parts else '')+' /'
        self.breadcrumb.setText(label);self.breadcrumb.setToolTip(label)

    def color_menu(self, background):
        menu = QMenu(self)
        colors = COLORS if background else text_colors(visual_tokens(window_appearance(self))['base'])
        for index, color in enumerate(colors):
            swatch=QPixmap(14,14);swatch.fill(QColor(color))
            menu.addAction(QIcon(swatch), ('黃','橙','紅','紫','藍','綠')[index], lambda value=color:self.format_selection({'background' if background else 'color': value}))
        menu.addSeparator()
        def custom():
            value = QColorDialog.getColor(QColor(COLORS[0] if background else '#ededed'), self, '文字底色' if background else '文字顏色')
            if value.isValid():self.format_selection({'background' if background else 'color': value.name()})
        menu.addAction('自訂顏色…', custom)
        button=self.toolbar.widgetForAction(self.format_actions['background' if background else 'color'])
        panel=self.toolbar.panel
        popup_button=next((b for b in panel.buttons if b.defaultAction() is self.format_actions['background' if background else 'color']),None) if panel and panel.isVisible() else None
        anchor=popup_button or (button if button and button.isVisible() else self.view_tools)
        menu.exec(anchor.mapToGlobal(anchor.rect().bottomLeft()))

    def format_menu(self):
        menu = QMenu(self)
        menu.addAction('字體與字號…', self.font_dialog)
        for label, style in [('刪除線', {'strike':True}), ('上標', {'vertical':'super'}), ('下標', {'vertical':'sub'})]:menu.addAction(label, lambda value=style:self.format_selection(value))
        menu.addAction('清除格式', self.clear_format);menu.addSeparator()
        for label, command in [('無序清單','bullet'),('有序清單','ordered'),('待辦清單','task'),('引用','quote')]:menu.addAction(label, lambda value=command:self.paragraph(value))
        align = menu.addMenu('段落對齊')
        for label, value in [('靠左','left'),('置中','center'),('靠右','right'),('左右對齊','justify')]:align.addAction(label, lambda direction=value:self.paragraph('align', direction))
        menu.addAction('增加縮排', lambda:self.paragraph('indent', self.preferences['tab_width']))
        menu.addAction('減少縮排', lambda:self.paragraph('outdent', self.preferences['tab_width']))
        menu.addAction('插入分隔線', self.insert_rule)
        menu.addSeparator();menu.addAction('全部折疊', lambda:self.editor.fold_all(True));menu.addAction('全部展開', lambda:self.editor.fold_all(False))
        menu.exec(self.more.mapToGlobal(self.more.rect().bottomLeft()))

    def insert_rule(self):
        snapshot, start, end = self.selection();self.apply([SourceEdit(start,end,'\n\n---\n\n')],start+7)

    def edit_table(self):
        from .markdown_tables import edit_table
        edit_table(self)

    def insert_attachment(self):
        from .markdown_attachments import choose
        choose(self)

    def insert_content(self,kind):
        if self.mode=='read' or self.page.buffer.composing:return
        snapshot,start,end=self.selection();selected=snapshot.text[start:end]
        if kind=='footnote':
            number=1
            while '[^note-'+str(number)+']' in snapshot.text:number+=1
            label='note-'+str(number);reference='[^'+label+']';definition='\n\n'+reference+': 腳註內容\n'
            if end==len(snapshot.text):self.apply([SourceEdit(end,end,reference+definition)],end+len(reference)+len(definition)-1)
            else:self.apply([SourceEdit(end,end,reference),SourceEdit(len(snapshot.text),len(snapshot.text),definition)],len(snapshot.text)+len(reference)+len(definition)-1)
            return
        defaults={'code':'','inline-math':'E=mc^2','math':'E=mc^2','mermaid':'flowchart LR\n    A[想法] --> B[草稿]\n    B --> C[完成]','callout':'提示內容','comment':'註解內容'}
        content=selected or defaults[kind]
        if kind=='code':
            length=max([len(m.group()) for m in re.finditer('`+',content)]+[2])+1;fence='`'*length
            prefix='\n\n'+fence+'\n';suffix='\n'+fence+'\n'
        elif kind=='mermaid':prefix='\n\n```mermaid\n';suffix='\n```\n'
        elif kind=='math':prefix='\n\n$$\n';suffix='\n$$\n'
        elif kind=='inline-math':prefix='$';suffix='$'
        elif kind=='callout':prefix='\n\n> [!note] 提示\n> ';suffix='\n';content=content.replace('\n','\n> ')
        else:prefix='%%';suffix='%%'
        self.apply([SourceEdit(start,end,prefix+content+suffix)],start+len(prefix))

    def report(self, message):
        self.message = message;self.update_statistics()
        workspace=self.page.file_title.workspace
        if workspace:workspace.show_notice(message)

    def refresh(self):
        selected = self.outline.currentRow();self.outline.clear()
        for section in heading_sections(self.page.buffer.snapshot().text):
            item = QListWidgetItem('  '*(section[2]-1)+visible_text(section[3]));item.setData(Qt.ItemDataRole.UserRole, section);self.outline.addItem(item)
        if 0 <= selected < self.outline.count():self.outline.setCurrentRow(selected)
        if self.mode == 'read':self.reader.refresh()
        self.search_changed();self.update_statistics();self.update_controls();self.update_breadcrumb()

    def update_statistics(self, *args):
        if not hasattr(self, 'statistics'):return
        text = self.page.buffer.snapshot().text;cursor = self.editor.textCursor()
        selected = self.reader.selected_text() if self.mode == 'read' else cursor.selectedText()
        count = len(re.findall(r'[\u3400-\u9fff]|\w+(?:[\x27-]\w+)*', visible_text(text)))
        state = self.page.error or self.message or ('尚未保存' if self.page.dirty else '已保存')
        self.statistics.setText(f'{count} 詞　{len(text)} 字元'+(f'　選取 {len(selected)} 字元' if selected else '')+'　·　'+state)
        self.statistics.setToolTip(self.statistics.text());self.statistics.setAccessibleName(self.statistics.text())

    def toggle_outline(self):self.outline.setVisible(not self.outline.isVisible());self.splitter.setSizes([190, max(260,self.width()-190)])

    def navigate(self, position):
        snapshot = self.page.buffer.snapshot();line = snapshot.text[:position].count('\n')
        if self.mode == 'read':self.reader.scrollToAnchor('line-'+str(line));return
        self.editor.fold_all(False);cursor = self.editor.textCursor();cursor.setPosition(utf16_position(snapshot.text, position));self.editor.setTextCursor(cursor);self.editor.ensureCursorVisible();self.editor.setFocus()

    def open_find(self, replace=False):
        self.find_bar.show();self.replace_row.setVisible(bool(replace) and self.mode != 'read');self.query.setFocus();self.query.selectAll();self.search_changed()

    def matches(self):
        query = self.query.text()
        if not query:return []
        text = self.reader.toPlainText() if self.mode == 'read' else self.page.buffer.snapshot().text
        return [match.span() for match in re.finditer(re.escape(query), text, 0 if self.case_sensitive.isChecked() else re.I)]

    def search_changed(self, *args):
        if hasattr(self, 'query') and hasattr(self, 'reader'):self.match_count.setText(str(len(self.matches()))+' 筆' if self.query.text() else '')

    def find_next(self, direction=1):
        if self.mode=='read' and self.reader.advanced and self.reader.rich:
            from PySide6.QtWebEngineCore import QWebEnginePage
            flags=QWebEnginePage.FindFlag(0)
            if direction<0:flags|=QWebEnginePage.FindFlag.FindBackward
            if self.case_sensitive.isChecked():flags|=QWebEnginePage.FindFlag.FindCaseSensitively
            self.reader.rich.findText(self.query.text(),flags);return
        matches = self.matches()
        if not matches:return
        view = self.reader if self.mode == 'read' else self.editor
        text = view.toPlainText();cursor = view.textCursor();at = python_position(text, cursor.selectionStart() if direction < 0 else cursor.selectionEnd())
        found = next((match for match in reversed(matches) if match[1] <= at), matches[-1]) if direction < 0 else next((match for match in matches if match[0] >= at), matches[0])
        if self.mode != 'read':self.editor.fold_all(False)
        cursor.setPosition(utf16_position(text, found[0]));cursor.setPosition(utf16_position(text, found[1]), QTextCursor.MoveMode.KeepAnchor);view.setTextCursor(cursor);view.ensureCursorVisible()

    def replace_one(self):
        if self.mode == 'read':return
        snapshot, start, end = self.selection()
        if (start, end) not in self.matches():self.find_next();snapshot, start, end = self.selection()
        if (start, end) in self.matches():self.apply([SourceEdit(start, end, self.replacement.text())], start+len(self.replacement.text()));self.find_next()

    def replace_all(self):
        if self.mode == 'read':return
        matches = self.matches()
        if matches:self.apply([SourceEdit(a, b, self.replacement.text()) for a, b in matches])

    def command_entries(self):
        entries = [('切換至編寫',lambda:self.set_mode('write')),('切換至原文',lambda:self.set_mode('source')),('切換至閱讀',lambda:self.set_mode('read')),
            ('搜尋此文件',self.open_find),('取代此文件',lambda:self.open_find(True)),('顯示／隱藏大綱',self.toggle_outline),('插入／編輯表格',self.edit_table),('插入圖片、影音或 PDF',self.insert_attachment),
            ('粗體',lambda:self.toggle_style('bold')),('斜體',lambda:self.toggle_style('italic')),('清除格式',self.clear_format),
            ('無序清單',lambda:self.paragraph('bullet')),('有序清單',lambda:self.paragraph('ordered')),('待辦清單',lambda:self.paragraph('task')),
            ('引用',lambda:self.paragraph('quote')),('分隔線',self.insert_rule),('全部折疊',lambda:self.editor.fold_all(True)),('全部展開',lambda:self.editor.fold_all(False)),
            ('復原',self.page.undo),('重做',self.page.redo),('字體與字號',self.font_dialog),
            ('文字顏色',lambda:self.color_menu(False)),('高亮與底色',lambda:self.color_menu(True)),
            ('底線',lambda:self.toggle_style('underline')),('刪除線',lambda:self.toggle_style('strike')),
            ('上標',lambda:self.format_selection({'vertical':'super'})),('下標',lambda:self.format_selection({'vertical':'sub'})),
            ('增加縮排',lambda:self.indent_paragraph(1)),('減少縮排',lambda:self.indent_paragraph(-1)),
            ('編輯與閱讀偏好',self.preferences_menu),('移至工具列 · Alt+F10',self.focus_toolbar)]
        for level in range(7):entries.append(('本文' if not level else '標題 H'+str(level),lambda value=level:self.paragraph('heading',value)))
        for label,kind in [('程式碼區塊','code'),('行內公式','inline-math'),('區塊公式','math'),('Mermaid 圖表','mermaid'),('提示區塊 Callout','callout'),('腳註','footnote'),('隱藏註解','comment')]:
            entries.append(('插入'+label,lambda value=kind:self.insert_content(value)))
        for label,value in [('靠左','left'),('置中','center'),('靠右','right'),('左右對齊','justify')]:
            entries.append(('段落'+label,lambda direction=value:self.paragraph('align',direction)))
        return entries

    def open_commands(self):
        if self.page.buffer.composing:return
        self.command_box.show();self.command_query.clear();self.refresh_commands();self.command_query.setFocus()

    def refresh_commands(self):
        self.command_list.clear()
        for index, (name, _) in enumerate(self.commands):
            if self.command_query.text().casefold() in name.casefold():
                item = QListWidgetItem(name);item.setData(Qt.ItemDataRole.UserRole,index);self.command_list.addItem(item)
        if self.command_list.count():self.command_list.setCurrentRow(0)

    def run_selected_command(self, *args):
        item = self.command_list.currentItem()
        if item:self.command_box.hide();self.commands[item.data(Qt.ItemDataRole.UserRole)][1]()
