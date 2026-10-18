"""Small, consistent controls for document panels; app chrome stays separate."""
from .ui_icons import stylesheet_icon_paths
from .theme import font_pixels


def styles(t, ui_size=12):
    paths = stylesheet_icon_paths(t);caret = paths['caret'];check = paths['check']
    navigation_size=max(12,round(font_pixels(ui_size)*.875))
    return f'''
        QLabel#MarkdownInlineTitle {{background:transparent;border:0;padding:0;font-weight:600;}}
        QLabel#MarkdownInlineTitle QLineEdit {{background:transparent;border:0;border-bottom:1px solid {t['accent']};border-radius:0;padding:0;}}
        QWidget#MarkdownFindPanel {{background:{t['surface']};border:1px solid {t['line']};border-radius:6px;}}
        QWidget#MarkdownFind QLineEdit {{padding:2px 6px;border-radius:4px;min-height:0;}}
        QWidget#MarkdownFind QLabel {{color:{t['secondary']};font-size:12px;}}
        QWidget#MarkdownFind QToolButton {{background:transparent;border:1px solid transparent;}}
        QWidget#MarkdownFind QToolButton#MarkdownCase {{padding:1px;font-size:13px;}}
        QWidget#MarkdownFind QToolButton:checked {{background:{t['selected']};}}
        QWidget#MarkdownFind QToolButton:hover {{background:{t['hover']};}}
        QFrame#MarkdownCommands {{background:{t['surface']};border:1px solid {t['line']};border-radius:8px;}}
        QFrame#MarkdownCommands QLineEdit {{padding:3px 8px;min-height:0;border-radius:4px;}}
        QFrame#MarkdownCommands QToolButton {{background:transparent;border:1px solid transparent;border-radius:4px;}}
        QFrame#MarkdownCommands QToolButton:hover {{background:{t['hover']};}}
        QListWidget#MarkdownCommandList {{background:transparent;border:0;outline:0;padding:0;font-size:{navigation_size}px;}}
        QListWidget#MarkdownCommandList::item {{padding:5px 9px;min-height:22px;border:0;border-radius:4px;}}
        QListWidget#MarkdownCommandList::item:selected {{background:{t['selected']};color:{t['text']};}}
        QListWidget#MarkdownCommandList::item:hover {{background:{t['hover']};}}
        QFrame#MarkdownOutlineHost {{background:transparent;border:0;}}
        QFrame#MarkdownOutlinePanel {{background:{t['surface']};border:1px solid {t['line']};border-radius:8px;}}
        QLabel#MarkdownPanelTitle {{font-size:{navigation_size}px;font-weight:600;padding:0 6px;}}
        QFrame#MarkdownOutlinePanel QToolButton {{background:transparent;border:1px solid transparent;border-radius:4px;padding:1px;}}
        QFrame#MarkdownOutlinePanel QToolButton:hover {{background:{t['hover']};}}
        QFrame#MarkdownOutlinePanel QToolButton:focus {{border-color:{t['accent']};}}
        QTreeWidget#MarkdownOutlineTree {{font-size:{navigation_size}px;background:transparent;border:0;padding:0;outline:0;}}
        QTreeWidget#MarkdownOutlineTree::item {{padding:0;border:0;border-radius:4px;}}
        QTreeWidget#MarkdownOutlineTree::item:selected {{background:{t['selected']};color:{t['text']};}}
        QTreeWidget#MarkdownOutlineTree::item:hover {{background:{t['hover']};}}
        QTreeWidget#MarkdownOutlineTree::branch {{background:transparent;}}
        QTreeWidget#MarkdownOutlineTree::branch:has-children:closed {{image:url({paths['right']});}}
        QTreeWidget#MarkdownOutlineTree::branch:has-children:open {{image:url({paths['down']});}}
        QWidget#MarkdownToolbar QToolButton:checked {{background:{t['selected']};}}
        QTextEdit[searchHit="true"], QTextBrowser[searchHit="true"] {{selection-background-color:#fff16a;selection-color:#272410;}}
        QToolBar#MarkdownRibbon QToolButton[hasMenu="true"], QFrame#MarkdownOverflow QToolButton[hasMenu="true"] {{padding-right:9px;}}
        QToolBar#MarkdownRibbon QToolButton::menu-indicator, QFrame#MarkdownOverflow QToolButton::menu-indicator {{image:url({caret});width:6px;height:4px;subcontrol-origin:padding;subcontrol-position:right center;right:2px;}}
        QToolBar#MarkdownRibbon QToolButton[singleCaret="true"]::menu-indicator, QFrame#MarkdownOverflow QToolButton[singleCaret="true"]::menu-indicator {{image:none;width:0;height:0;}}
        QToolBar#MarkdownRibbon QToolButton#qt_toolbar_ext_button::menu-indicator {{image:none;width:0;height:0;}}
        QToolBar#MarkdownRibbon QToolButton:focus {{border-color:transparent;}}
        QToolBar#MarkdownRibbon QToolButton[keyboardFocus="true"] {{border-color:{t['accent']};}}
        QMenu {{padding:5px;}}
        QMenu::item {{padding:5px 12px;min-height:22px;}}
        QMenu::indicator {{width:12px;height:12px;border-radius:2px;}}
        QMenu::indicator:checked {{image:url({check});background:{t['accent']};}}
    '''
