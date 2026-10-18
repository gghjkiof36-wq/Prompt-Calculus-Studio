"""A native, overflowing document ribbon; all entries perform real B actions."""
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (QToolBar, QToolButton, QMenu, QSizePolicy,
    QProxyStyle, QStyle, QStyleFactory, QApplication)
from .ui_icons import icon


class RibbonStyle(QProxyStyle):
    """Keep the native overflow menu, with the same target as other tools."""
    def pixelMetric(self, metric, option=None, widget=None):
        if metric == QStyle.PixelMetric.PM_ToolBarExtensionExtent:return 29
        return super().pixelMetric(metric, option, widget)


def make_toolbar(surface):
    bar = QToolBar(surface)
    bar.setObjectName('MarkdownRibbon')
    bar.setAccessibleName('文件格式工具列')
    bar.setMovable(False)
    bar.setFloatable(False)
    bar.setIconSize(QSize(18, 18))
    bar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonIconOnly)
    bar.setMinimumWidth(0)
    bar.setFixedHeight(35)
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
    return bar
