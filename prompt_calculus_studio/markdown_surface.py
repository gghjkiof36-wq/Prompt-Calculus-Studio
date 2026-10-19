"""Document-local Markdown controls; the workspace shell remains unchanged."""
import html
import json
import re
from PySide6.QtCore import Qt, QTimer, QSaveFile, QIODevice, QEvent, QPoint
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor, QColor, QKeySequence, QShortcut, QPixmap, QIcon
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QStackedWidget,
    QSplitter, QToolButton, QComboBox, QSpinBox, QLineEdit, QLabel, QCheckBox,
    QListWidget, QListWidgetItem, QMenu, QColorDialog, QFontDialog, QGraphicsDropShadowEffect, QFrame)
from .ui_icons import icon
from .markdown_source import SourceEdit, python_position, utf16_position, source_text
from .markdown_commands import format_edits, clear_edits, paragraph_edits, heading_sections, move_section_edits, selection_lines
from .markdown_syntax import COLORS, text_colors, style_css, visible_text, spans
from .markdown_view import SourceView, ReadingView
from .markdown_toolbar import make_toolbar
from .theme import visual_tokens, window_appearance
from .widgets import ElidedLabel
from .markdown_outline import OutlinePanel
from .markdown_zoom import DocumentZoom


class MarkdownSurface(QWidget):
    def __init__(self, page):
        super().__init__(page)
        self.page = page;self.mode = 'write';self.typing_style = {};self.clear_next = False;self.message = ''
        self.preferences = dict(line_numbers=False, auto_pairs=True, wrap=True, tab_width=4,
                                hard_breaks=False, reading_width=880, reading_size=12,
                                reading_family='Microsoft JhengHei UI', line_height=150,
                                vim=False, spellcheck=False, slash_commands=True)
        self.preferences_loaded = False;self.zoom_percent = 100;self.display_panel = None
        self.setObjectName('MarkdownSurface')
        body = QVBoxLayout(self);body.setContentsMargins(0, 0, 0, 0);body.setSpacing(0)
        top = QWidget(self);top.setObjectName('MarkdownToolbar')
        heading = QHBoxLayout(top);heading.setContentsMargins(20, 9, 18, 6);heading.setSpacing(5)
        self.breadcrumb = ElidedLabel('工作區 /', top);self.breadcrumb.setObjectName('MarkdownBreadcrumb')
        self.breadcrumb.setMinimumWidth(0);heading.addWidget(self.breadcrumb)
        self.breadcrumb_name = ElidedLabel(page.path.stem, top);self.breadcrumb_name.setObjectName('MarkdownFileTitle')
        page.file_title.filenameChanged.connect(self.breadcrumb_name.setText)
        heading.addWidget(self.breadcrumb_name, 1)
        self.outline_button=self.tool('大綱',self.toggle_outline,'list')
        self.outline_button.setCheckable(True);heading.addWidget(self.outline_button)
        self.mode_box = self.tool('編寫 · 切換文件視圖', self.mode_menu, 'pencil')
        self.mode_box.setObjectName('MarkdownMode')
        heading.addWidget(self.mode_box)
        self.view_tools = self.tool('搜尋、大綱與編輯偏好', self.view_menu, 'more-horizontal')
        self.view_tools.setObjectName('MarkdownViewTools')
        heading.addWidget(self.view_tools)
        body.addWidget(top)
        self.toolbar_host = QWidget(self)
        tools_layout = QHBoxLayout(self.toolbar_host);tools_layout.setContentsMargins(12, 4, 12, 8)
        tools_layout.addStretch(1)
        self.toolbar = make_toolbar(self);tools_layout.addWidget(self.toolbar);tools_layout.addStretch(1)
        self.toolbar_shadow = QGraphicsDropShadowEffect(self.toolbar)
        self.toolbar_shadow.setOffset(0, 2);self.toolbar_shadow.setBlurRadius(6)
        self.toolbar.setGraphicsEffect(self.toolbar_shadow)
        body.addWidget(self.toolbar_host)
        self.find_bar = QWidget(self);self.find_bar.setObjectName('MarkdownFind')
        find_host = QHBoxLayout(self.find_bar);find_host.setContentsMargins(12,0,12,8);find_host.addStretch()
        self.find_panel = QWidget();self.find_panel.setObjectName('MarkdownFindPanel');self.find_panel.setMaximumWidth(540)
        find_host.addWidget(self.find_panel,1)
        find = QVBoxLayout(self.find_panel);find.setContentsMargins(6,4,6,4);find.setSpacing(4)
        query_row = QHBoxLayout();query_row.setSpacing(2)
        self.query = QLineEdit();self.query.setPlaceholderText('搜尋');self.query.setAccessibleName('搜尋此文件')
        self.query.setMinimumWidth(40);self.query.setFixedHeight(30);query_row.addWidget(self.query,1)
        self.match_count = QLabel();self.match_count.setMinimumWidth(34);self.match_count.setAlignment(Qt.AlignmentFlag.AlignCenter);query_row.addWidget(self.match_count)
        self.case_sensitive = QToolButton();self.case_sensitive.setText('Aa');self.case_sensitive.setObjectName('MarkdownCase');self.case_sensitive.setCheckable(True);self.case_sensitive.setFixedSize(32,28)
        self.case_sensitive.setAccessibleName('區分大小寫');self.case_sensitive.setToolTip('區分大小寫');query_row.addWidget(self.case_sensitive)
        self.find_previous = self.tool('上一筆', lambda:self.find_next(-1), 'chevron-up');query_row.addWidget(self.find_previous)
        self.find_forward = self.tool('下一筆', self.find_next, 'chevron-down');query_row.addWidget(self.find_forward)
        query_row.addWidget(self.tool('關閉搜尋', lambda:self.close_panel(self.find_bar), 'close'));find.addLayout(query_row)
        self.replace_row = QWidget();replace_layout = QHBoxLayout(self.replace_row);replace_layout.setContentsMargins(0,0,0,0);replace_layout.setSpacing(4)
        self.replacement = QLineEdit();self.replacement.setPlaceholderText('取代為');self.replacement.setAccessibleName('取代為');self.replacement.setFixedHeight(30);self.replacement.setMinimumWidth(40);replace_layout.addWidget(self.replacement,1)
        replace_layout.addWidget(self.tool('取代這一筆', self.replace_one, text='取代'))
        replace_layout.addWidget(self.tool('全部取代', self.replace_all, text='全部'))
        find.addWidget(self.replace_row);body.insertWidget(1,self.find_bar);self.find_bar.hide();self.replace_row.hide()
        self.query.textChanged.connect(self.search_changed);self.case_sensitive.toggled.connect(self.search_changed)
        self.query.returnPressed.connect(self.find_next)
        self.command_box = QFrame(self,Qt.WindowType.Popup);self.command_box.setObjectName('MarkdownCommands')
        commands = QVBoxLayout(self.command_box);commands.setContentsMargins(10,10,10,10);commands.setSpacing(6)
        self.command_query = QLineEdit();self.command_query.setPlaceholderText('搜尋命令');self.command_query.setAccessibleName('搜尋 Markdown 命令')
        self.command_query.setFixedHeight(32);self.command_query.installEventFilter(self)
        self.command_list = QListWidget();self.command_list.setObjectName('MarkdownCommandList')
        self.command_list.setAccessibleName('文件命令');self.command_list.setUniformItemSizes(True)
        self.command_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        command_header = QHBoxLayout();command_header.addWidget(self.command_query, 1)
        command_header.addWidget(self.tool('關閉命令', lambda:self.close_panel(self.command_box), 'close'))
        commands.addLayout(command_header);commands.addWidget(self.command_list);self.command_box.hide()
        self.command_query.textChanged.connect(self.refresh_commands)
        self.command_query.returnPressed.connect(self.run_selected_command)
        self.command_list.itemActivated.connect(self.run_selected_command)
        self.splitter = QSplitter();self.outline_panel = OutlinePanel(self);self.outline = self.outline_panel.tree;self.outline_panel.hide()
        self.content_pane = QWidget();content = QVBoxLayout(self.content_pane);content.setContentsMargins(0,0,0,0);content.setSpacing(0)
        self.outline_panel.setParent(self.content_pane)
        self.title_host = QWidget();self.title_host.setObjectName('MarkdownTitleHost')
        self.title_layout = QHBoxLayout(self.title_host);self.title_layout.setContentsMargins(50,24,28,10)
        page.file_title.setParent(self.title_host);page.file_title.setObjectName('MarkdownInlineTitle');page.file_title.setMinimumHeight(48);page.file_title.setToolTip('')
        self.title_layout.addWidget(page.file_title);content.addWidget(self.title_host)
        self.stack = QStackedWidget();self.editor = SourceView(self);self.reader = ReadingView(self)
        self.stack.addWidget(self.editor);self.stack.addWidget(self.reader);content.addWidget(self.stack,1);self.splitter.addWidget(self.content_pane)
        self.splitter.setChildrenCollapsible(False);self.splitter.setStretchFactor(0,1);body.addWidget(self.splitter,1)
        from .markdown_outline_layout import OutlineLayout
        from .markdown_search import SearchHighlight
        self.outline_layout=OutlineLayout(self);self.search_highlight=SearchHighlight(self)
        self.content_pane.installEventFilter(self);self.zoom_handler = DocumentZoom(self)
        self.statistics = QLabel();self.statistics.setObjectName('MarkdownStatus');self.statistics.setMinimumHeight(28)
        self.statistics.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse);body.addWidget(self.statistics)
        from .markdown_statistics import WordStatistics
        if not hasattr(page.buffer,'word_statistics'):page.buffer.word_statistics=WordStatistics(page.buffer)
        page.buffer.word_statistics.updated.connect(self.update_statistics)
        self.refresh_timer = QTimer(self);self.refresh_timer.setSingleShot(True);self.refresh_timer.timeout.connect(self.refresh)
        page.buffer.text.contentsChanged.connect(lambda:self.refresh_timer.start(40))
        page.buffer.text.modificationChanged.connect(self.update_statistics)
        page.buffer.text.undoAvailable.connect(self.update_controls)
        page.buffer.text.redoAvailable.connect(self.update_controls)
        page.buffer.compositionChanged.connect(self.update_controls)
        self.commands = self.command_entries()
        from .markdown_advanced import AdvancedEditing
        self.advanced = AdvancedEditing(self)
        self.command_shortcuts = {}
        for sequence, callback in [('Ctrl+F', self.open_find), ('Ctrl+H', lambda:self.open_find(True)), ('Ctrl+K',self.edit_link), ('Ctrl+Return',self.open_current_link), ('Ctrl+;',self.edit_properties), ('Ctrl+Shift+P', self.open_commands), ('Alt+F10', self.focus_toolbar)]:
            shortcut = QShortcut(QKeySequence(sequence), self);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut);shortcut.activated.connect(callback)
            self.command_shortcuts[sequence] = shortcut
        for panel in (self.command_box, self.find_bar, self.toolbar):
            shortcut = QShortcut(QKeySequence('Esc'), panel);shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(self.focus_document if panel is self.toolbar else lambda p=panel:self.close_panel(p))
        self.apply_appearance();self.apply_preferences();self.refresh()
        self.editor.document().setLayoutEnabled(True)
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
        if hasattr(self,'command_box') and self.command_box.isVisible():self.position_commands()

    def keyPressEvent(self, event):
        # QLineEdit emits returnPressed, then allows Return to bubble. This
        # surface is hosted by a legacy editor adapter, which must never treat
        # a search, command or toolbar key as a second document edit.
        event.accept()

    def eventFilter(self, watched, event):
        if watched is getattr(self,'content_pane',None) and event.type() == QEvent.Type.Resize:self.update_title_layout()
        if watched is getattr(self,'command_query',None) and event.type()==QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down):
            count=self.command_list.count()
            if count:self.command_list.setCurrentRow((self.command_list.currentRow()+(-1 if event.key()==Qt.Key.Key_Up else 1))%count)
            event.accept();return True
        return super().eventFilter(watched,event)

    def update_title_layout(self):
        view = self.reader if self.mode == 'read' else self.editor
        margins = view.viewportMargins();inset = round(view.document().documentMargin())
        left,right = margins.left()+inset,margins.right()+inset
        if self.mode == 'read' and self.reader.advanced and self.reader.rich:
            left,right = getattr(self.reader.rich,'content_margins',(left,right))
        self.title_layout.setContentsMargins(left,20,right,6)
        style_key=(self.preferences['reading_family'],self.zoom_percent)
        if getattr(self,'_title_style_key',None)==style_key:return
        self._title_style_key=style_key
        font = QFont(self.preferences['reading_family']);font.setPointSizeF(24*self.zoom_percent/100);font.setBold(True)
        self.page.file_title.setFont(font)
        family = self.preferences['reading_family'].replace('"','').replace('\\','')
        points = 24*self.zoom_percent/100
        self.page.file_title.setStyleSheet(f'QLabel#MarkdownInlineTitle, QLabel#MarkdownInlineTitle QLineEdit {{font-family:"{family}";font-size:{points}pt;font-weight:600;}}')
        self.page.file_title.setMinimumHeight(round(44*self.zoom_percent/100))

    def apply_appearance(self):
        appearance=window_appearance(self);t = visual_tokens(appearance)
        self.editor._tokens_cache=t
        appearance_key = (tuple(sorted(t.items())),appearance.get('ui_size',12))
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
            QToolBar#MarkdownRibbon {{background:{rgba(fill)};border:1px solid {rgba(edge)};border-top-color:{rgba(top_edge)};border-radius:7px;padding:2px;spacing:4px;}}
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
        from .markdown_ui_style import styles
        self.setStyleSheet(self.styleSheet()+styles(t,appearance.get('ui_size',12)))
        if hasattr(self,'outline_layout'):self.outline_layout.schedule()
        if hasattr(self,'editor'):self.editor.update_metadata();self.reader.apply_preferences()

    def focus_document(self):
        (self.reader if self.mode == 'read' else self.editor).setFocus()

    def close_panel(self, panel):
        panel.hide()
        if panel is self.find_bar:self.clear_rich_search();self.search_highlight.clear()
        self.focus_document()

    def focus_toolbar(self):
        if self.mode == 'read':self.view_tools.setFocus(Qt.FocusReason.ShortcutFocusReason);return
        for action in self.toolbar.actions():
            button=self.toolbar.widgetForAction(action)
            if button and button.isVisible() and button.isEnabled():
                button.setProperty('keyboardFocus',True);button.style().unpolish(button);button.style().polish(button)
                button.setFocus(Qt.FocusReason.ShortcutFocusReason);return

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, 'reader'):
            QTimer.singleShot(0, self, self.apply_appearance)

    def view_menu(self):
        menu = QMenu(self)
        menu.addAction(icon('search'), '搜尋\tCtrl+F', self.open_find)
        if self.mode != 'read':menu.addAction('取代\tCtrl+H', lambda:self.open_find(True))
        menu.addSeparator()
        menu.addAction(icon('list'), '隱藏大綱' if self.outline_panel.isVisible() else '大綱', self.toggle_outline)
        menu.addAction(icon('settings'), '編輯與閱讀…', self.preferences_menu)
        menu.addAction('筆記屬性…\tCtrl+;',self.edit_properties)
        menu.addSeparator()
        menu.addAction('開啟連結\tCtrl+Enter',self.open_current_link)
        workspace=self.page.file_title.workspace
        if workspace and hasattr(workspace,'notebook'):
            manager=workspace.notebook;actions=manager.operations();notes=menu.addMenu('筆記')
            for name,callback in [('加入書籤',manager.bookmark),('插入範本…',lambda:actions.insert_template(self)),
                                  ('開啟今日筆記',actions.daily),('合併筆記…',lambda:actions.compose(self)),
                                  ('擷取選取內容…',lambda:actions.compose(self,True)),('檔案復原…',lambda:actions.recover(self))]:
                notes.addAction(name,lambda checked=False,fn=callback:actions.guarded(fn))
            menu.addAction('匯出 PDF…',lambda:actions.guarded(lambda:actions.pdf(self)))
        menu.addAction('命令…\tCtrl+Shift+P', self.open_commands)
        menu.exec(self.view_tools.mapToGlobal(self.view_tools.rect().bottomLeft()))

    def load_preferences(self):
        workspace = self.page.file_title.workspace
        if workspace is None:return
        previous=dict(self.preferences)
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
        self.preferences_loaded = True
        if self.preferences!=previous:self.apply_preferences()
        self.advanced.configure()
        from .markdown_shortcuts import apply_shortcuts
        apply_shortcuts(self)

    def save_preferences(self):
        workspace = self.page.file_title.workspace
        if workspace:
            output = QSaveFile(str(workspace.data/'md-preferences.json'));output.setDirectWriteFallback(False)
            if output.open(QIODevice.OpenModeFlag.WriteOnly):
                raw = json.dumps(self.preferences).encode()
                if output.write(raw) != len(raw) or not output.commit():self.report('編輯偏好尚未保存，請稍後重試。')
        self.apply_preferences()

    def set_editor_font(self, font):
        family = font.family().replace('"','').replace('\\','')
        self.editor.setStyleSheet(f'QPlainTextEdit {{border:0;padding:0;background:transparent;font-family:"{family}";font-size:{font.pointSizeF()}pt;}}')
        self.editor.setFont(font);self.editor.document().setDefaultFont(font)

    def apply_preferences(self):
        font = QFont(self.preferences['reading_family']);font.setPointSizeF(self.preferences['reading_size']*self.zoom_percent/100)
        self.set_editor_font(font)
        self.update_title_layout()
        self.editor.setLineWrapMode(self.editor.LineWrapMode.WidgetWidth if self.preferences['wrap'] else self.editor.LineWrapMode.NoWrap)
        self.editor.setTabStopDistance(self.editor.fontMetrics().horizontalAdvance(' ')*self.preferences['tab_width'])
        self.editor.update_metadata();self.reader.apply_preferences()
        if self.mode == 'read':self.reader.refresh()
        self.update_breadcrumb()
        self.outline_layout.schedule()

    def preferences_menu(self):
        from .markdown_preferences import DisplayPreferences
        if self.display_panel:self.display_panel.close();self.display_panel.deleteLater()
        self.display_panel = DisplayPreferences(self);self.display_panel.open()

    def set_zoom(self, value):
        value = max(60,min(200,int(value)))
        if value == self.zoom_percent:return
        self.zoom_percent = value
        font = QFont(self.preferences['reading_family']);font.setPointSizeF(self.preferences['reading_size']*value/100)
        self.set_editor_font(font);self.editor.update_metadata()
        self.reader.apply_preferences()
        if self.reader.rich:
            self.reader.rich.setZoomFactor(value/100);QTimer.singleShot(0,self.reader.rich,self.reader.rich.sync_title_alignment)
        if self.mode == 'read' and not self.reader.advanced:self.reader.refresh()
        self.update_title_layout();self.update_statistics()
        self.outline_layout.schedule();self.search_changed()
        if self.display_panel:self.display_panel.zoom.setText(str(value)+'%')

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
        self.title_host.setVisible(True)
        self.outline.setDragEnabled(mode != 'read')
        if mode == 'read':self.reader.refresh_cached();self.stack.setCurrentWidget(self.reader);self.reader.setFocus()
        else:
            self.editor.mode = mode;self.editor.update_metadata();self.stack.setCurrentWidget(self.editor);self.editor.setFocus()
        self.update_title_layout();self.search_changed();self.update_statistics();self.update_controls()
        self.outline_layout.schedule()

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
            self.clear_next=False;self.typing_style.update(style);self.message = '';self.update_statistics();self.editor.setFocus();return
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
            self.typing_style = {};self.clear_next=True;self.message = '';self.update_statistics();self.focus_document();return
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
        offset = self.editor.textCursor().selectionStart()
        block=self.editor.document().findBlock(offset);text=block.text()
        try:start = python_position(text,offset-block.position())
        except UnicodeError:return dict(self.typing_style)
        values = {}
        for item in spans(text):
            if item.first <= start <= item.last:values.update(item.style)
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
        self.breadcrumb_name.setText(self.page.path.stem)
        self.page.file_title.setText(self.page.path.stem)

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

    def edit_link(self,embed=False):
        if self.mode=='read' or self.page.buffer.composing:return
        from .markdown_relations import LinkDialog
        dialog=LinkDialog(self,embed);dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.link_editor=dialog;dialog.show()

    def open_current_link(self,position=None):
        from .markdown_relations import link_at,open_link
        link=link_at(self,position)
        if link:open_link(self,link.target,wiki=link.wiki)
        else:self.report('請將游標放在要開啟的連結內。')

    def edit_properties(self):
        if self.page.buffer.composing:return
        from .markdown_property_editor import PropertyEditor
        dialog=PropertyEditor(self);dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.property_editor=dialog;dialog.show()

    def refresh(self):
        if not self.editor.lazy_presentation or self.outline_layout.requested:self.outline.rebuild(self.page.buffer.snapshot().text)
        if self.mode == 'read':self.reader.refresh_cached()
        snapshot=self.page.buffer.snapshot()
        if getattr(self,'_relations_snapshot',None) is not snapshot:
            self._relations_snapshot=snapshot
            workspace=self.page.file_title.workspace
            if workspace:
                for tab in workspace.all_pages():
                    other=getattr(tab.widget,'md_surface',None)
                    if other is None or other.page.buffer is self.page.buffer:continue
                    rich=other.reader.rich
                    if rich and rich.projection and self.page.path in rich.projection.dependencies:
                        rich.relations.timer.start()
        self.search_changed();self.update_statistics();self.update_controls();self.update_breadcrumb()

    def update_statistics(self, *args):
        if not hasattr(self, 'statistics'):return
        snapshot=self.page.buffer.snapshot();text=snapshot.text;cursor = self.editor.textCursor()
        selected = self.reader.selected_text() if self.mode == 'read' else cursor.selectedText()
        count=self.page.buffer.word_statistics.request(snapshot)
        state = self.page.error or self.message or ('尚未保存' if self.page.dirty else '已保存')
        self.statistics.setText((f'{count} 詞　' if count is not None else '')+f'{len(text)} 字元'+(f'　選取 {len(selected)} 字元' if selected else '')+'　·　'+state+(f'　·　{self.zoom_percent}%' if self.zoom_percent != 100 else ''))
        self.statistics.setToolTip(self.statistics.text());self.statistics.setAccessibleName(self.statistics.text())

    def toggle_outline(self):self.outline_layout.toggle()

    def navigate(self, position):
        snapshot = self.page.buffer.snapshot();line = snapshot.text[:position].count('\n')
        if self.mode == 'read':self.reader.scrollToAnchor('line-'+str(line));return
        self.editor.fold_all(False);cursor = self.editor.textCursor();cursor.setPosition(utf16_position(snapshot.text, position));self.editor.setTextCursor(cursor);self.editor.ensureCursorVisible();self.editor.setFocus()

    def open_find(self, replace=False):
        self.find_bar.show();self.replace_row.setVisible(bool(replace) and self.mode != 'read');self.query.setFocus();self.query.selectAll();self.search_changed()

    def matches(self):
        return self.search_highlight.matches()

    def search_changed(self, *args):
        if not hasattr(self,'query') or not hasattr(self,'reader'):return
        self.search_highlight.update()
        if not self.query.text():
            self.clear_rich_search()
            self.match_count.clear();self.find_previous.setEnabled(False);self.find_forward.setEnabled(False)
            return
        if self.mode=='read' and self.reader.advanced and self.reader.rich:
            self.reader.rich.search(self.query.text() if self.find_bar.isVisible() else '',self.case_sensitive.isChecked());return
        matches = self.matches()
        view = self.reader if self.mode == 'read' else self.editor
        number=self.search_highlight.current_index(view)+1
        self.match_count.setText(f'{number}/{len(matches)}' if self.query.text() else '')
        self.find_previous.setEnabled(bool(matches));self.find_forward.setEnabled(bool(matches))

    def clear_rich_search(self):
        if self.reader.rich:self.reader.rich.search('')

    def find_next(self, direction=1):
        if self.mode=='read' and self.reader.advanced and self.reader.rich:
            self.reader.rich.search(self.query.text(),self.case_sensitive.isChecked(),direction);return
        view = self.reader if self.mode == 'read' else self.editor
        cursor=view.textCursor();found=self.search_highlight.next_span(cursor,direction)
        if found is None:return
        if self.mode != 'read':self.editor.fold_all(False)
        cursor.setPosition(found[0]);cursor.setPosition(found[1],QTextCursor.MoveMode.KeepAnchor);view.setTextCursor(cursor);view.ensureCursorVisible()
        self.search_changed()

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
            ('插入／編輯連結',self.edit_link),('嵌入筆記或區塊',lambda:self.edit_link(True)),('開啟游標處連結',self.open_current_link),('筆記屬性',self.edit_properties),
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
        self.commands=self.command_entries()
        workspace=self.page.file_title.workspace
        if workspace and hasattr(workspace,'notebook'):self.commands+=workspace.notebook.entries(self)
        self.command_query.clear();self.refresh_commands();self.position_commands()
        self.command_box.show();self.command_box.raise_();self.command_query.setFocus()

    def position_commands(self):
        self.command_box.setFixedWidth(max(160,min(520,self.width()-24)))
        self.command_box.ensurePolished();self.command_list.ensurePolished()
        self.command_list.doItemsLayout()
        row_height=max(self.command_list.fontMetrics().lineSpacing()+10,self.command_list.sizeHintForRow(0))
        self.command_list.setFixedHeight(row_height*max(1,min(7,self.command_list.count()))+2*self.command_list.frameWidth()+4)
        self.command_box.adjustSize()
        point=self.mapToGlobal(QPoint((self.width()-self.command_box.width())//2,self.toolbar_host.geometry().bottom()+6))
        available=self.screen().availableGeometry()
        point.setX(max(available.left(),min(point.x(),available.right()-self.command_box.width()+1)))
        point.setY(max(available.top(),min(point.y(),available.bottom()-self.command_box.height()+1)))
        self.command_box.move(point)

    def refresh_commands(self):
        self.command_list.clear()
        for index, (name, _) in enumerate(self.commands):
            if self.command_query.text().casefold() in name.casefold():
                item = QListWidgetItem(name);item.setData(Qt.ItemDataRole.UserRole,index);self.command_list.addItem(item)
        if self.command_list.count():self.command_list.setCurrentRow(0)
        if self.command_box.isVisible():self.position_commands()

    def run_selected_command(self, *args):
        item = self.command_list.currentItem()
        if item:self.command_box.hide();self.commands[item.data(Qt.ItemDataRole.UserRole)][1]()
