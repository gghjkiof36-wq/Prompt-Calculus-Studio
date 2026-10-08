"""The same explicit workspace modes on Canvas and Prompt Editor."""
from PySide6.QtWidgets import QWidget, QHBoxLayout, QButtonGroup
from .widgets import button
from .ui_icons import icon


class WorkspaceModeSwitch(QWidget):
    def __init__(self, window, active='canvas'):
        super().__init__()
        body = QHBoxLayout(self)
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(4)
        self.group = QButtonGroup(self)
        self.canvas_button = button('Canvas', lambda: window.set_interface_mode('canvas'), 'Navigation')
        self.editor_button = button('Prompt Editor', lambda: window.set_interface_mode('list'), 'Navigation')
        for mode, entry, glyph in (('canvas', self.canvas_button, 'canvas'), ('list', self.editor_button, 'list')):
            entry.setCheckable(True)
            entry.setChecked(mode == active)
            entry.setIcon(icon(glyph))
            entry.setAccessibleName(entry.text())
            self.group.addButton(entry)
            body.addWidget(entry)

    def set_mode(self, active):
        self.canvas_button.setChecked(active=='canvas')
        self.editor_button.setChecked(active=='list')

