"""Small canvas workspace capsule; image browsing lives in RecentOverlay."""
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QFrame,QHBoxLayout,QStyle,QStyleOptionComboBox
from .widgets import button,ComboBox
from .ui_icons import icon


class CanvasWorkspaceCombo(ComboBox):
    pass


class CanvasWorkspaceBar(QFrame):
    def __init__(self,window,workspace,menu):
        super().__init__();self.window=window;self.workspace=workspace;self.setObjectName('CanvasWorkspaceCapsule')
        body=QHBoxLayout(self);body.setContentsMargins(8,6,8,6);body.setSpacing(4)
        body.addWidget(workspace);body.addWidget(menu)
        self.list_mode=button('切換清單',lambda:window.set_interface_mode('list'),'Quiet')
        self.list_mode.setProperty('iconName','list');self.list_mode.setIcon(icon('list'));self.list_mode.setToolTip('切換清單');self.list_mode.setAccessibleName('切換清單');body.addWidget(self.list_mode)
        self.history=button('',window.show_recent_sheet,'IconButton')
        self.history.setProperty('iconName','history');self.history.setIcon(icon('history'))
        self.history.setFixedSize(32,32);self.history.setToolTip('最近生成');self.history.setAccessibleName('最近生成')
        body.addWidget(self.history)
        window.installEventFilter(self)

    def refresh(self):
        if self.window.isVisible() and not self.window.closing:self.history.setToolTip(f'最近生成 · {self.window.catalog.count("recent")} 張')
        self.list_mode.setText('' if self.window.width()<800 else '切換清單')
        option=QStyleOptionComboBox();self.workspace.initStyleOption(option)
        field=self.workspace.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self.workspace)
        chrome=self.workspace.width()-field.width()
        width=max(156,min(260,self.workspace.fontMetrics().horizontalAdvance('預設工作區')+chrome+4))
        if self.workspace.width()!=width:self.workspace.setFixedWidth(width)
        short=self.window.height()<=640
        self.layout().setContentsMargins(8,2 if short else 6,8,2 if short else 6)
        self.window.canvas_header.layout().setContentsMargins(12 if short else 24,8 if short else 16,12 if short else 24,0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'list_mode'):self.refresh()

    def eventFilter(self,watched,event):
        if watched is self.window and event.type()==QEvent.Type.Resize:self.refresh()
        return super().eventFilter(watched,event)
