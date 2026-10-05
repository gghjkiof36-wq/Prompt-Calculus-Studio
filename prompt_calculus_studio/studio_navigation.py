"""Shared desktop navigation; content reflows instead of scaling text."""
from PySide6.QtCore import Qt, QSize, QEvent, QRectF
from PySide6.QtGui import QPainterPath, QRegion
from PySide6.QtWidgets import QWidget, QHBoxLayout, QStackedWidget, QStyle, QStyleOptionButton
from .widgets import button, label
from .ui_icons import icon
from .theme import visual_tokens


class ContentStack(QStackedWidget):
    def __init__(self,parent=None,*,rounded=False):
        super().__init__(parent)
        self.rounded=rounded

    def minimumSizeHint(self):
        return QSize(0, 0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if self.rounded:
            # One outer contour contains both the pinned sidebar and page.
            # Their internal boundary stays square; only the workspace's
            # outer corners reveal the surrounding window material.
            path=QPainterPath();path.addRoundedRect(QRectF(self.rect()),18,18)
            self.setMask(QRegion(path.toFillPolygon().toPolygon()))


class StudioNavigation(QWidget):
    def __init__(self, window):
        super().__init__()
        self.window = window
        self.caption_inset = 0
        self._adapting = False
        self.setObjectName('StudioNavigation')
        self.layout_box = QHBoxLayout(self)
        self.layout_box.setContentsMargins(20, 10, 20, 10)
        self.layout_box.setSpacing(6)
        self.sidebar=button('',window.toggle_context_sidebar,'IconButton')
        self.sidebar.setCheckable(True)
        self.sidebar.setFixedSize(32,32); self.sidebar.setToolTip('收合／展開側邊欄'); self.sidebar.setAccessibleName('收合／展開側邊欄')
        self.sidebar.setProperty('iconName','menu'); self.layout_box.addWidget(self.sidebar)
        self.history_buttons=(window.navigation_button(-1),window.navigation_button(1))
        for entry in self.history_buttons:self.layout_box.addWidget(entry)
        self.entries = {}
        for key, title, action in (
            ('canvas', '畫布', window.return_to_prompt),
            ('media', '媒體庫', lambda: window.show_page(window.gallery)),
            ('explore', '探索', lambda: window.settings('explore')),
            ('export', '匯出', lambda: window.show_page(window.clean_export)),
            ('settings', '設定', lambda: window.settings('appearance')),
        ):
            entry = button(title, action, 'Navigation')
            entry.setCheckable(True)
            entry.setAccessibleName(title)
            entry.setIconSize(QSize(19, 19))
            self.entries[key] = (entry, title)
            self.layout_box.addWidget(entry)
        self.layout_box.addStretch()
        self.connection=button('',self.request_connection,'ConnectionPill')
        self.connection.setCursor(Qt.CursorShape.PointingHandCursor)
        self._focus_buttons=(self.sidebar,*self.history_buttons,
                             *(entry for entry,_ in self.entries.values()),self.connection)
        for entry in self._focus_buttons:
            entry.setFocusPolicy(Qt.FocusPolicy.TabFocus)
            entry.installEventFilter(self)
        status=QHBoxLayout(self.connection);status.setContentsMargins(11,0,11,0);status.setSpacing(8)
        self.connection_dot=label('');self.connection_dot.setFixedSize(10,10);status.addWidget(self.connection_dot)
        self.brand = label('ComfyUI · 未連線', 'Subtle');status.addWidget(self.brand)
        for child in (self.connection_dot,self.brand):child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.layout_box.addWidget(self.connection)
        self.update_connection()
        self.select('canvas')

    def select(self, key):
        self.current_key=key
        for name, (entry, _) in self.entries.items():
            entry.setChecked(name == key)
        self.adapt_labels()

    def refresh_icons(self):
        color = visual_tokens(self.window.state['settings'])['text']
        for name, (entry, _) in self.entries.items():
            entry.setIcon(icon(name, color))
        self.update_connection()

    def update_connection(self):
        client=getattr(self.window,'comfy',None)
        connected=bool(getattr(client,'connected',False));checking=bool(getattr(client,'connection_checking',False))
        self._connection_short='連線中…' if checking else '已連線' if connected else '未連線'
        text='ComfyUI · '+self._connection_short
        self._connection_full=text
        self.brand.setText(text);self.connection.setAccessibleName(text)
        self.connection.setToolTip(text+'\n'+('正在確認連線，請稍候。' if checking else '使用已儲存的位址重新檢查並連線。'))
        self.connection.setEnabled(not checking)
        colors=visual_tokens(self.window.state['settings'])
        self.connection_dot.setStyleSheet('background:'+colors['warning' if checking else 'connected_indicator' if connected else 'control']+'; border-radius:5px;')
        height=max(32,self.brand.fontMetrics().height()+12);self.connection.setFixedHeight(height)
        self.connection.setStyleSheet(f"""
            QPushButton#ConnectionPill {{ background:{colors['surface']}; border:1px solid {colors['line']}; border-radius:{height//2}px; padding:0; }}
            QPushButton#ConnectionPill:hover {{ background:{colors['hover']}; }}
            QPushButton#ConnectionPill:pressed {{ background:{colors['selected']}; }}
            QPushButton#ConnectionPill[pcsKeyboardFocus="true"]:focus {{ border-color:{colors['accent']}; }}
            QPushButton#ConnectionPill:disabled {{ background:{colors['surface']}; }}
        """)
        self.adapt_labels()

    def request_connection(self):
        client=getattr(self.window,'comfy',None)
        if client:client.recheck_connection()

    def minimumSizeHint(self):
        return QSize(0,super().minimumSizeHint().height())

    @staticmethod
    def _entry_size(entry, text):
        option=QStyleOptionButton();entry.initStyleOption(option);option.text=text
        text_size=entry.fontMetrics().size(Qt.TextFlag.TextShowMnemonic,text)
        width=text_size.width()+entry.iconSize().width()+(4 if text else 0)
        content=QSize(width,max(text_size.height(),entry.iconSize().height()))
        return entry.style().sizeFromContents(QStyle.ContentsType.CT_PushButton,option,content,entry)

    def adapt_labels(self):
        if self._adapting or not hasattr(self,'connection'):return
        self._adapting=True
        try:
            full={name:self._entry_size(entry,title) for name,(entry,title) in self.entries.items()}
            compact={name:self._entry_size(entry,'') for name,(entry,_) in self.entries.items()}
            current=getattr(self,'current_key','canvas')
            controls=sum(entry.width() for entry in (self.sidebar,*self.history_buttons))
            status_layout=self.connection.layout();status_margins=status_layout.contentsMargins()
            dot_width=max(self.connection.height(),status_margins.left()+self.connection_dot.width()+status_margins.right())
            spacing=6;left_margin=12
            gaps=self.layout_box.count()-1
            def available():
                return self.width()-left_margin-12-self.caption_inset-controls-gaps*spacing-dot_width
            named=sum(size.width() for size in full.values())
            short_status=status_layout.spacing()+self.brand.fontMetrics().horizontalAdvance(self._connection_short)
            if available()<named+short_status:spacing=4;left_margin=8
            minimum=sum(size.width() for size in compact.values())+full[current].width()-compact[current].width()
            while spacing>0 and available()<minimum:spacing-=1
            budget=available()
            widths={name:size.width() for name,size in compact.items()}
            widths[current]=full[current].width();visible={current}
            remaining=budget-sum(widths.values())
            # Keep the current destination named; spend remaining space on as
            # many other complete labels as fit, without truncating page names.
            candidates=sorted((name for name in self.entries if name!=current),
                              key=lambda name:full[name].width()-compact[name].width())
            for name in candidates:
                extra=full[name].width()-compact[name].width()
                if extra<=remaining:
                    visible.add(name);widths[name]=full[name].width();remaining-=extra
            # Use a short status before reducing it to a dot. The wider version
            # leaves some title-bar space for dragging the desktop window.
            status='';status_extra=0
            for text,reserve in ((self._connection_full,96),(self._connection_short,0)):
                extra=status_layout.spacing()+self.brand.fontMetrics().horizontalAdvance(text)
                if extra+reserve<=remaining:
                    status=text;status_extra=extra;break
            self.brand.setText(status or self._connection_full)
            self.brand.setVisible(bool(status))
            self.connection.setFixedWidth(dot_width+status_extra)
            self.layout_box.setSpacing(spacing)
            self.layout_box.setContentsMargins(left_margin,6,12+self.caption_inset,6)
            height=max(size.height() for size in full.values())
            for name,(entry,title) in self.entries.items():
                entry.setText(title if name in visible else '')
                entry.setToolTip('' if name in visible else title)
                entry.setFixedSize(widths[name],height)
        finally:self._adapting=False

    def resizeEvent(self, event):
        self.adapt_labels()
        super().resizeEvent(event)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self.adapt_labels()

    def set_caption_inset(self, inset):
        self.caption_inset = max(0, inset)
        self.adapt_labels()

    def set_sidebar_pinned(self, pinned):
        self.sidebar.setChecked(bool(pinned))
        text='收合側欄' if pinned else '固定側欄'
        self.sidebar.setToolTip(text);self.sidebar.setAccessibleName(text)

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.window.windowHandle():
            self.window.windowHandle().startSystemMove()
        super().mousePressEvent(event)

    def eventFilter(self,watched,event):
        if (watched in self._focus_buttons and event.type()==QEvent.Type.MouseButtonPress
                and event.button()==Qt.MouseButton.LeftButton):
            # Mouse navigation uses the checked background. Keep the focus
            # outline for Tab/Space navigation, and leave text-editor focus
            # alone when the user operates these buttons with the mouse.
            for entry in self._focus_buttons:
                if entry.hasFocus():entry.clearFocus()
        return super().eventFilter(watched,event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.window.showNormal() if self.window.isMaximized() else self.window.showMaximized()
        super().mouseDoubleClickEvent(event)
