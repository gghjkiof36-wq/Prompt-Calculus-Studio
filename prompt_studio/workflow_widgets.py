"""A workflow-only order card: no prompt editor, execution control or ports."""
from PySide6.QtCore import Qt,QRectF,QTimer
from PySide6.QtWidgets import QFrame,QVBoxLayout,QListWidget,QListWidgetItem,QAbstractItemView
from .canvas_items import TextCard
from .workflow_flow import ORDER_CARD,workflow_ids
from .widgets import RoundMenu


class WorkflowOrderPanel(QFrame):
    def __init__(self,canvas):
        super().__init__(); self.canvas=canvas; self.updating=False; self.setObjectName('InsetPanel')
        body=QVBoxLayout(self); body.setContentsMargins(12,12,12,12)
        self.list=QListWidget(); self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setAccessibleName('工作流執行順序'); self.list.setMinimumSize(260,160); body.addWidget(self.list)
        self.list.model().rowsMoved.connect(self.reordered)
    def refresh(self):
        ids=workflow_ids(self.canvas.window.state)
        current=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        if ids!=current:
            self.updating=True; self.list.clear()
            for ident in ids:
                item=QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,ident); self.list.addItem(item)
            self.updating=False
        names={p['id']:p['name'] for p in self.canvas.window.state.get('generation',{}).get('profiles',[])}
        for i,ident in enumerate(ids): self.list.item(i).setText(str(i+1)+'  '+names.get(ident,'工作流已移除'))
    def reordered(self,*_):
        if self.updating:return
        ids=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        window=self.canvas.window; owner=(window.store,window.state['workspace'])
        def apply():
            if owner==(window.store,window.state['workspace']): self.canvas.commit(lambda s:s['multi_output']['workflow_order'].update(items=ids))
        QTimer.singleShot(0,apply)


class WorkflowOrderCard(TextCard):
    default_size=(400,350)
    def __init__(self,canvas):
        super().__init__(canvas); self.key=ORDER_CARD; self.title='工作流順序'
        self.panel=WorkflowOrderPanel(canvas); self.init_interaction()
    def attach(self):
        if self.proxy.widget() is not self.panel:self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.refresh(); self.panel.ensurePolished()
        self.restore_size(); self.layout_card(); self.panel.show()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        self.proxy.setGeometry(QRectF(14,46,max(290,minimum.width(),width-28),max(190,minimum.height(),height-60))); self.sync_bounds()
    def update_text(self): self.panel.refresh(); super().update_text()
    def contextMenuEvent(self,event):
        menu=RoundMenu(self.canvas.window)
        menu.addAction('移除排序模組',lambda:self.canvas.commit(lambda s:s['multi_output']['workflow_order'].update(visible=False)))
        menu.open_at(event.screenPos()); event.accept()
    def detach(self): pass
