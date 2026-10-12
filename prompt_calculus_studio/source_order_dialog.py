"""Reorder the current output's text sources without editing them in place."""
from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import QListWidgetItem, QPushButton
from .widgets import StudioDialog, button, row
from .output_order import OutputOrderList
from .canvas_items import canvas_colors
from .module_chrome import apply_module_dialog, style_action
from .theme import font_pixels
from .ui_icons import icon


class SourceOrderDialog(StudioDialog):
    def __init__(self,canvas,key):
        from .flow_data import text_sources,source_name
        super().__init__(canvas.window)
        self.setWindowTitle('排列文字來源');self.resize(440,360)
        colors=canvas_colors(canvas)
        apply_module_dialog(self,colors,max(16,font_pixels(canvas.window.state['settings']['ui_size'])))
        self.listing=OutputOrderList();self.listing.setProperty('moduleChoices',True)
        self.listing.setAccessibleName('文字來源順序');self.listing.setSpacing(2)
        self.listing.setIconSize(QSize(16,16))
        for cid in text_sources(canvas.data()['outputs'][key]):
            item=QListWidgetItem(icon('grip-vertical',colors['secondary']),source_name(canvas.window.state,cid))
            item.setData(Qt.ItemDataRole.UserRole,cid)
            item.setSizeHint(QSize(0,max(44,self.listing.fontMetrics().height()+18)))
            self.listing.addItem(item)
        self.body.addWidget(self.listing,1)
        self.save_button=button('儲存',self.accept,'Primary')
        self.cancel_button=button('取消',self.reject,'Quiet')
        style_action(self.save_button,colors,primary=True)
        self.body.addLayout(row(None,self.cancel_button,self.save_button))
        for control in self.findChildren(QPushButton):control.setAutoDefault(False)
        self.listing.setFocus()

    def ordered_ids(self):
        return [self.listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.listing.count())]
