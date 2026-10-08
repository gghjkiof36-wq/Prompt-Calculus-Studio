"""Small canvas workspace capsule; image browsing lives in RecentOverlay."""
from PySide6.QtCore import QEvent,Qt
from PySide6.QtWidgets import QWidget,QFrame,QHBoxLayout,QBoxLayout,QStyle,QStyleOptionComboBox
from .widgets import button,ComboBox
from .ui_icons import icon


class CanvasWorkspaceCombo(ComboBox):
    pass


class CanvasWorkspaceBar(QFrame):
    def __init__(self,window,workspace,menu):
        super().__init__();self.window=window;self.workspace=workspace;self.setObjectName('CanvasWorkspaceCapsule')
        body=QBoxLayout(QBoxLayout.Direction.LeftToRight,self);body.setContentsMargins(8,6,8,6);body.setSpacing(4)
        self.workspace_group=QWidget(); workspace_row=QHBoxLayout(self.workspace_group)
        workspace_row.setContentsMargins(0,0,0,0);workspace_row.setSpacing(4)
        workspace_row.addWidget(workspace);workspace_row.addWidget(menu)
        self.action_group=QWidget(); actions=QHBoxLayout(self.action_group)
        actions.setContentsMargins(0,0,0,0);actions.setSpacing(4)
        body.addWidget(self.workspace_group);body.addWidget(self.action_group)
        from .workspace_navigation import WorkspaceModeSwitch
        self.workspace_switch=WorkspaceModeSwitch(window,'canvas');actions.addWidget(self.workspace_switch)
        self.list_mode=self.workspace_switch.editor_button
        self.materials=button('素材',window.toggle_materials,'Quiet');actions.addWidget(self.materials)
        self.history=button('',window.show_recent_sheet,'IconButton')
        self.history.setProperty('iconName','history');self.history.setIcon(icon('history'))
        self.history.setFixedSize(32,32);self.history.setToolTip('最近生成');self.history.setAccessibleName('最近生成')
        actions.addWidget(self.history)
        window.installEventFilter(self)

    def refresh(self):
        if self.window.isVisible() and not self.window.closing:self.history.setToolTip(f'最近生成 · {self.window.catalog.count("recent")} 張')
        option=QStyleOptionComboBox();self.workspace.initStyleOption(option)
        field=self.workspace.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self.workspace)
        chrome=self.workspace.width()-field.width()
        width=max(156,min(260,self.workspace.fontMetrics().horizontalAdvance('預設工作區')+chrome+4))
        if self.workspace.width()!=width:self.workspace.setFixedWidth(width)
        self.layout().setDirection(QBoxLayout.Direction.TopToBottom if self.window.width()<900 else QBoxLayout.Direction.LeftToRight)
        short=self.window.height()<=640
        self.layout().setContentsMargins(8,2 if short else 6,8,2 if short else 6)
        self.window.canvas_header.layout().setContentsMargins(12 if short else 24,8 if short else 16,12 if short else 24,0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'list_mode'):self.refresh()

    def eventFilter(self,watched,event):
        if watched is self.window and event.type()==QEvent.Type.Resize:self.refresh()
        return super().eventFilter(watched,event)
