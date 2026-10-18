"""A native, overflowing document ribbon; all entries perform real B actions."""
from PySide6.QtCore import QSize, Qt, QEvent, QPoint, QTimer
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QToolBar, QToolButton, QMenu, QSizePolicy,
    QProxyStyle, QStyle, QStyleFactory, QApplication, QFrame, QGridLayout)
from .ui_icons import icon


class RibbonStyle(QProxyStyle):
    """Keep the native overflow menu, with the same target as other tools."""
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_ToolBarExtensionExtent:return 33
        return super().pixelMetric(metric, option, widget)


class OverflowPanel(QFrame):
    def __init__(self, bar):
        super().__init__(bar, Qt.WindowType.Popup)
        self.bar = bar
        self.setObjectName('MarkdownOverflow')
        self.setAccessibleName('更多文件工具')
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(8, 8, 8, 8);self.grid.setSpacing(4)
        self.buttons = []

    def eventFilter(self, watched, event):
        keys=(Qt.Key.Key_Escape,Qt.Key.Key_Left,Qt.Key.Key_Right,Qt.Key.Key_Up,Qt.Key.Key_Down)
        if event.type() == QEvent.Type.ShortcutOverride and event.key() in keys:
            event.accept();return True
        if event.type() == QEvent.Type.KeyPress and event.key() in keys:
            self.keyPressEvent(event);return True
        return super().eventFilter(watched,event)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.hide();QTimer.singleShot(0,self.bar.surface.focus_document);event.accept();return
        if event.key() in (Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down):
            current = next((i for i,b in enumerate(self.buttons) if b.hasFocus()), 0)
            step = {Qt.Key.Key_Left:-1,Qt.Key.Key_Right:1,Qt.Key.Key_Up:-self.columns,Qt.Key.Key_Down:self.columns}[event.key()]
            self.buttons[(current+step)%len(self.buttons)].setFocus();event.accept();return
        super().keyPressEvent(event)


class MarkdownRibbon(QToolBar):
    def __init__(self, surface):
        super().__init__(surface);self.surface=surface;self.panel=None

    def eventFilter(self, watched, event):
        if watched.objectName() == 'qt_toolbar_ext_button':
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self.show_overflow();return True
            if event.type() == QEvent.Type.MouseButtonRelease:return True
            if event.type() == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Space,Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Down):
                self.show_overflow();return True
        return super().eventFilter(watched,event)

    def show_overflow(self):
        if self.panel:self.panel.hide();self.panel.deleteLater()
        hidden = [a for a in self.actions() if not a.isSeparator() and a.isVisible()
                  and self.widgetForAction(a) and not self.widgetForAction(a).isVisible()]
        if not hidden:return
        panel=self.panel=OverflowPanel(self)
        panel.columns=max(1,min(7,(self.width()-20)//38,len(hidden)))
        for index,action in enumerate(hidden):
            button=QToolButton(panel);button.setDefaultAction(action);button.setFixedSize(34,34)
            button.setIconSize(QSize(19,19));button.setAccessibleName(action.text())
            button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            button.installEventFilter(panel)
            if action.menu():button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
            else:button.clicked.connect(panel.hide)
            panel.grid.addWidget(button,index//panel.columns,index%panel.columns);panel.buttons.append(button)
        panel.adjustSize()
        pos=self.mapToGlobal(QPoint(self.width()-panel.width(),self.height()+5))
        screen=self.screen().availableGeometry()
        pos.setX(max(screen.left(),min(pos.x(),screen.right()-panel.width()+1)))
        pos.setY(max(screen.top(),min(pos.y(),screen.bottom()-panel.height()+1)))
        panel.move(pos);panel.show();panel.buttons[0].setFocus()


def make_toolbar(surface):
    bar = MarkdownRibbon(surface)
    bar.setObjectName('MarkdownRibbon')
    bar.setAccessibleName('文件格式工具列')
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.setIconSize(QSize(19, 19))
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    bar.setMinimumWidth(0)
    bar.setFixedHeight(39)
    # The proxy owns its base style; never give it the application's shared one.
    bar.ribbon_style = RibbonStyle(QStyleFactory.create(QApplication.style().objectName()) or QStyleFactory.create('Fusion'))
    bar.ribbon_style.setParent(bar)
    bar.setStyle(bar.ribbon_style)
    bar.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
    surface.format_actions = {}

    def action(label, symbol, callback=None, menu=None, key=None):
        result = QAction(icon(symbol), label, bar)
        result.setToolTip(label)
        if callback:result.triggered.connect(lambda checked=False:callback())
        if menu:result.setMenu(menu)
        bar.addAction(result)
        button = bar.widgetForAction(result)
        button.setAccessibleName(label)
        button.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        if menu:button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        if key:surface.format_actions[key] = result
        return result

    surface.undo_action = action('復原 · Ctrl+Z', 'edit-undo', surface.page.undo)
    surface.redo_action = action('重做 · Ctrl+Y', 'edit-redo', surface.page.redo)
    bar.addSeparator()
    action('清除格式', 'eraser', surface.clear_format)
    headings = QMenu('段落與標題', bar)
    headings.addAction('本文', lambda:surface.paragraph('heading', 0))
    for level in range(1, 7):
        headings.addAction('標題 '+str(level), lambda value=level:surface.paragraph('heading', value))
    action('本文／標題 H1–H6', 'heading', menu=headings, key='heading')
    bar.addSeparator()
    for label, symbol, key in [('粗體 · Ctrl+B','text-bold','bold'),
                                ('斜體 · Ctrl+I','text-italic','italic'),
                                ('刪除線','text-strike','strike'),
                                ('底線 · Ctrl+U','text-underline','underline')]:
        item = action(label, symbol, lambda name=key:surface.toggle_style(name), key=key)
        item.setCheckable(True)
    action('字體與字號…', 'type', surface.font_dialog, key='font')
    action('文字顏色', 'text-color', lambda:surface.color_menu(False), key='color')
    action('高亮與底色', 'highlight', lambda:surface.color_menu(True), key='background')
    action('插入／編輯表格', 'grid', surface.edit_table, key='table')
    insert=QMenu('插入內容',bar)
    insert.addAction('連結… · Ctrl+K',surface.edit_link)
    insert.addAction('嵌入筆記或區塊…',lambda:surface.edit_link(True))
    insert.addAction(icon('image-single'),'圖片、影音或 PDF…',surface.insert_attachment)
    for label,kind in [('程式碼區塊','code'),('行內公式','inline-math'),('區塊公式','math'),('Mermaid 圖表','mermaid'),('提示區塊 Callout','callout'),('腳註','footnote'),('隱藏註解','comment')]:
        insert.addAction(label,lambda checked=False,value=kind:surface.insert_content(value))
    action('插入圖片、公式與其他內容','plus',menu=insert,key='insert')
    bar.addSeparator()
    lists = QMenu('清單', bar)
    lists.addAction(icon('list'), '無序清單', lambda:surface.paragraph('bullet'))
    lists.addAction(icon('list-ordered'), '有序清單', lambda:surface.paragraph('ordered'))
    lists.addAction(icon('task'), '待辦清單', lambda:surface.paragraph('task'))
    action('清單', 'list', menu=lists)
    action('待辦清單', 'task', lambda:surface.paragraph('task'))
    action('引用', 'quote', lambda:surface.paragraph('quote'))
    align = QMenu('段落對齊', bar)
    for label, value in [('靠左','left'),('置中','center'),('靠右','right'),('左右對齊','justify')]:
        align.addAction(label, lambda direction=value:surface.paragraph('align', direction))
    action('段落對齊', 'align', menu=align)
    action('增加縮排', 'indent', lambda:surface.indent_paragraph(1))
    action('減少縮排', 'outdent', lambda:surface.indent_paragraph(-1))
    action('插入分隔線', 'minus', surface.insert_rule)
    more = QMenu('其他格式', bar)
    more.addAction('上標', lambda:surface.format_selection({'vertical':'super'}))
    more.addAction('下標', lambda:surface.format_selection({'vertical':'sub'}))
    more.addSeparator()
    more.addAction('全部折疊', lambda:surface.editor.fold_all(True))
    more.addAction('全部展開', lambda:surface.editor.fold_all(False))
    item = action('其他格式', 'more-horizontal', menu=more)
    surface.more = bar.widgetForAction(item)
    extension = bar.findChild(QToolButton, 'qt_toolbar_ext_button')
    if extension:
        extension.setAccessibleName('更多工具');extension.setToolTip('更多工具')
        extension.setIcon(icon('chevron-right'))
        extension.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        extension.installEventFilter(bar)
    return bar
