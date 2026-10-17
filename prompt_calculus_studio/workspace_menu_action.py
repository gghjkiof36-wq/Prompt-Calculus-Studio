"""A normal menu row with its symbol in the trailing accessory column."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidgetAction, QFrame, QHBoxLayout, QLabel
from .theme import visual_tokens
from .popup_surface import popup_row_height
from .ui_icons import icon


def trailing_action(menu, workspace, title, symbol, callback, checked=None):
    action = QWidgetAction(menu)
    action.setText(title)
    row = QFrame()
    row.setObjectName('WorkspaceMenuEntry')
    row.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    row.setMinimumHeight(popup_row_height(menu.fontMetrics()))
    layout = QHBoxLayout(row)
    layout.setContentsMargins(10, 5, 10, 5)
    text = QLabel(title)
    text.setTextFormat(Qt.TextFormat.PlainText)
    layout.addWidget(text)
    layout.addStretch()
    mark = QLabel()
    mark.setFixedSize(18, 18)
    layout.addWidget(mark)
    tokens = visual_tokens(workspace.display_settings())
    row.setStyleSheet('QFrame#WorkspaceMenuEntry {border:0; border-radius:7px; background:transparent;}'
                     'QFrame#WorkspaceMenuEntry[active="true"] {background:'+tokens['popup_active']+';}'
                     'QFrame#WorkspaceMenuEntry QLabel {background:transparent; padding:0; border:0;}')
    if checked is not None:
        action.setCheckable(True)
        action.setChecked(checked)
    def paint_mark():
        mark.setPixmap(icon(symbol, 'navigation').pixmap(18, 18))
        mark.setVisible(checked is None or action.isChecked())
    paint_mark()
    action.toggled.connect(lambda *_: paint_mark())
    action.setDefaultWidget(row)
    action.triggered.connect(callback)
    menu.addAction(action)
    def hovered(current):
        if row.property('active') != (current is action):
            row.setProperty('active', current is action)
            row.style().unpolish(row)
            row.style().polish(row)
            row.update()
    menu.hovered.connect(hovered)
    return action
