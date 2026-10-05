"""Explicit stage configuration; ordinary native execution stays independent."""
import copy
from PySide6.QtCore import Qt,QRectF,QTimer
from PySide6.QtWidgets import QFrame,QVBoxLayout,QListWidget,QListWidgetItem,QAbstractItemView,QCheckBox,QDialog
from .canvas_items import TextCard
from .workflow_flow import ORDER_CARD,workflow_ids
from .widgets import RoundMenu,button,row,label,ComboBox
from .chain_model import definition,new_definition,validate


class WorkflowOrderPanel(QFrame):
    def __init__(self,canvas):
        super().__init__(); self.canvas=canvas; self.updating=False; self.setObjectName('InsetPanel')
        body=QVBoxLayout(self); body.setContentsMargins(12,12,12,12)
        self.enabled=QCheckBox('啟用工作流串接');body.addWidget(self.enabled)
        self.enabled.toggled.connect(lambda value:self.change(lambda p:p.update(enabled=value)))
        self.summary=label('明確加入階段，再從底部執行。','Subtle',True);body.addWidget(self.summary)
        self.list=QListWidget(); self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list.setAccessibleName('工作流執行順序'); self.list.setMinimumSize(260,160); body.addWidget(self.list)
        self.list.model().rowsMoved.connect(self.reordered)
        self.list.itemDoubleClicked.connect(lambda _:self.edit())
        body.addLayout(row(button('新增階段',lambda:self.edit(True),'Quiet'),button('編輯',self.edit,'Quiet'),button('移除',self.remove,'Quiet')))
        self.source=ComboBox();self.source.setAccessibleName('串接入口圖片清單');body.addWidget(self.source)
        self.source.currentIndexChanged.connect(lambda:self.change(lambda p:p.update(source=self.source.currentData())))
        body.addLayout(row(button('暫停',lambda:self.control('pause'),'Quiet'),button('繼續',lambda:self.control('resume'),'Quiet'),
                           button('階段紀錄與結果',self.history,'Quiet')))
        self.progress=label('','Subtle',True);body.addWidget(self.progress)
        canvas.window.comfy.stateChanged.connect(self.refresh)

    def change(self,mutate):
        if self.updating:return
        plan=copy.deepcopy(definition(self.canvas.window.state)) or new_definition()
        try:mutate(plan);validate(plan)
        except ValueError as exc:self.canvas.window.notice(str(exc));self.refresh();return
        self.canvas.commit(lambda s:s['multi_output']['workflow_order'].update(chain=plan))

    def edit(self,adding=False):
        from .chain_editor import StageDialog
        plan=definition(self.canvas.window.state) or new_definition();index=self.list.currentRow()
        old=plan['stages'][index] if not adding and 0<=index<len(plan['stages']) else None
        if not adding and old is None:return
        dialog=StageDialog(self.canvas,old,None if adding else index)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:
                value,profile=dialog.value();updated=copy.deepcopy(plan)
                if adding:updated['stages'].append(value)
                else:updated['stages'][index]=value
                validate(updated)
                def apply(state):
                    from .generation import store_profile
                    store_profile(state,copy.deepcopy(profile));state['multi_output']['workflow_order']['chain']=updated
                    from .chain_connections import attach_stage
                    attach_stage(state,updated['stages'][-1] if adding else updated['stages'][index])
                self.canvas.commit(apply)
            except ValueError as exc:self.canvas.window.notice(str(exc))
        dialog.deleteLater()

    def remove(self):
        index=self.list.currentRow()
        plan=definition(self.canvas.window.state)
        if plan and 0<=index<len(plan['stages']):
            updated=copy.deepcopy(plan);removed=updated['stages'].pop(index)
            try:validate(updated)
            except ValueError as exc:self.canvas.window.notice(str(exc));return
            from .chain_connections import endpoint
            def apply(state):
                data=state['multi_output'];data['workflow_order']['chain']=updated
                data['connections']=[c for c in data['connections'] if c['destination']!=endpoint(removed['id'])]
            self.canvas.commit(apply)

    def history(self):
        from .chain_history import show
        show(self.canvas.window)

    def control(self,method):
        runner=self.canvas.window.comfy.input_flow.chain;run=runner.current()
        if not run:self.canvas.window.notice('目前沒有正在處理的串接。');return
        try:getattr(runner,method)(run['id'])
        except ValueError as exc:self.canvas.window.notice(str(exc))
    def refresh(self):
        state=self.canvas.window.state;plan=definition(state)
        self.updating=True
        self.enabled.setChecked(bool(plan and plan['enabled']))
        chosen=plan.get('source') if plan else None
        self.source.clear();self.source.addItem('單次輸入 · 次數表示完整串接的輪數',None)
        for key,value in state.get('canvas_functions',{}).get('images',{}).items():
            if value.get('items'):self.source.addItem('入口清單 · '+value.get('name',(value.get('source') or {}).get('name',key)),key)
        self.source.setCurrentIndex(max(0,self.source.findData(chosen)))
        self.updating=False
        ids=[s['id'] for s in plan['stages']] if plan else workflow_ids(state)
        current=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        if ids!=current:
            self.updating=True; self.list.clear()
            for ident in ids:
                item=QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,ident); self.list.addItem(item)
            self.updating=False
        names={p['id']:p['name'] for p in (plan['stages'] if plan else state.get('generation',{}).get('profiles',[]))}
        for i,ident in enumerate(ids): self.list.item(i).setText(str(i+1)+'  '+names.get(ident,'工作流已移除'))
        self.summary.setText(' → '.join(s['name']+'（輸出 #'+str(s.get('output') or '未選')+'）' for s in plan['stages']) if plan and plan['stages'] else '從 CLIP／圖片輸入的「流程」端口接入，或新增階段。選好各階段輸入後，勾選啟用。')
        self.progress.setText(self.canvas.window.comfy.input_flow.chain.progress())
    def reordered(self,*_):
        if self.updating:return
        ids=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        window=self.canvas.window; owner=(window.store,window.state['workspace'])
        def apply():
            if owner!=(window.store,window.state['workspace']):return
            if definition(window.state):
                def reorder(plan):
                    by_id={s['id']:s for s in plan['stages']};plan['stages']=[by_id[k] for k in ids]
                self.change(reorder)
            else:self.canvas.commit(lambda s:s['multi_output']['workflow_order'].update(items=ids))
        QTimer.singleShot(0,apply)


class WorkflowOrderCard(TextCard):
    default_size=(530,540)
    def __init__(self,canvas):
        super().__init__(canvas); self.key=ORDER_CARD; self.title='工作流串接'
        self.panel=WorkflowOrderPanel(canvas); self.init_interaction()
    def attach(self):
        if self.proxy.widget() is not self.panel:self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.refresh(); self.panel.ensurePolished()
        self.restore_size(); self.layout_card(); self.panel.show()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        plan=definition(self.canvas.window.state) or {};top=90+24*len(plan.get('stages',[]))
        self.proxy.setGeometry(QRectF(14,top,max(290,minimum.width(),width-28),max(190,minimum.height(),height-top-14))); self.sync_bounds()
    def update_text(self): self.panel.refresh(); super().update_text()
    def contextMenuEvent(self,event):
        menu=RoundMenu(self.canvas.window)
        def remove():
            runner=self.canvas.window.comfy.input_flow.chain;run=runner.current()
            if run:runner.pause(run['id'],'串接模塊已隱藏，後續暫停，紀錄保留。')
            self.canvas.commit(lambda s:s['multi_output']['workflow_order'].update(visible=False))
        menu.addAction('隱藏串接模塊',remove)
        menu.open_at(event.screenPos()); event.accept()
    def detach(self): pass
