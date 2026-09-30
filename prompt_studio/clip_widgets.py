"""CLIP destination cards and the viewport execution bar."""
import copy
from PySide6.QtCore import Qt,QRectF,QEvent,QPoint,QTimer
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QLineEdit,QWidget,QSpinBox,QSizePolicy,QProgressBar
from .canvas_items import TextCard
from .widgets import button,label,ComboBox,StudioDialog,dialog_buttons
from .generation import active_profile,text_fields,options
from . import clip_flow
from .workflow_binding import WorkflowBindingDialog


class ClipBindingDialog(WorkflowBindingDialog):
    def __init__(self,canvas,key):
        self.image_input=None if self.manual_choice or canvas.data()['version']>=7 else ComboBox()
        super().__init__(canvas,key)
        if self.image_input is not None:
            self.body.insertWidget(4,label('接收 PCS 圖片的節點（選填，同工作流共用）','Subtle'))
            self.body.insertWidget(5,self.image_input)
            self.image_input.setToolTip('Prompt 接入圖片時寫入此 LoadImage；未接圖片時保留 ComfyUI 原值。')

    def target_index(self,target):
        # QVariant's matching does not compare Python tuple payloads by value.
        return next((i for i in range(self.target.count()) if self.target.itemData(i)==target),-1)

    def refresh_targets(self):
        self.target.clear(); self.target.addItem('不綁定',None); profile=self.profile()
        if self.image_input is not None:
            self.image_input.clear(); self.image_input.addItem('不接收 PCS 圖片','')
            for key,node in (profile or {}).get('graph',{}).items():
                if node['class_type']=='LoadImage' and isinstance(node.get('inputs',{}).get('image'),str):
                    self.image_input.addItem(node.get('_meta',{}).get('title','LoadImage')+' · #'+key,key)
            self.image_input.setCurrentIndex(max(0,self.image_input.findData((profile or {}).get('image',''))))
            self.image_input.setEnabled(profile is not None)
        self.target.setEnabled(profile is not None)
        if not profile:
            self.hint.setText(self.catalog.message or '尚無可用工作流，請確認 ComfyUI 連線後重新整理。'); self.hint.show(); return
        binding=next((b for b in self.bindings if b['workflow']==profile['id'] and b['clip']==self.key),None)
        for node,field in text_fields(profile['graph']):
            graph_node=profile['graph'][node]
            legacy=binding and (node,field)==(binding['node'],binding['field'])
            if 'clip' not in graph_node['class_type'].casefold() and not legacy: continue
            title=graph_node.get('_meta',{}).get('title',graph_node['class_type'])
            occupied=next((b for b in self.bindings if (b['workflow'],b['node'],b['field'])==(profile['id'],node,field) and b['clip']!=self.key),None)
            suffix=' · 已被其他 CLIP 輸入綁定' if occupied else ''
            self.target.addItem(title+' · #'+node+' / '+field+suffix,(node,field))
            if occupied: self.target.model().item(self.target.count()-1).setEnabled(False)
        if binding: self.target.setCurrentIndex(max(0,self.target_index((binding['node'],binding['field']))))
        self.hint.setText('此工作流沒有可綁定的 CLIP 文字節點。' if self.target.count()==1 else '')
        self.hint.setVisible(bool(self.hint.text()))

    def apply(self):
        try:profile=self.binding_profile()
        except ValueError as exc:self.canvas.window.notice(str(exc));return
        if profile is None:return
        profile=copy.deepcopy(profile)
        if self.image_input is not None:profile['image']=self.image_input.currentData() or ''
        def bind(state):
            from .generation import store_profile
            store_profile(state,profile); clip_flow.set_binding(state,profile['id'],self.key,self.target.currentData())
        self.canvas.commit(bind)


class ClipPanel(QFrame):
    def __init__(self,canvas,key):
        super().__init__();self.canvas=canvas;self.key=key;self.setObjectName('InsetPanel')
        body=QVBoxLayout(self);body.setContentsMargins(14,10,14,14)
        self.binding=button('選擇工作流與 CLIP 節點',lambda:canvas.bind_dialog(key),'Quiet')
        body.addWidget(self.binding)
        self.status=label('','Subtle',True);body.addWidget(self.status)

    def refresh(self):
        state=self.canvas.window.state;binding=clip_flow.selected_binding(state,self.key)
        profile=next((p for p in options(state)['profiles'] if binding and p['id']==binding['workflow']),None)
        error=''
        if binding and profile:
            self.binding.setText(profile['name']+' · #'+binding['node']+' / '+binding['field'])
            if (binding['node'],binding['field']) not in text_fields(profile['graph']):error='綁定節點已移除，請重新選擇。'
        else:
            self.binding.setText('選擇工作流與 CLIP 節點')
            if binding:error='綁定的工作流已移除。'
        self.status.setText(error);self.status.setVisible(bool(error))


class ClipCard(TextCard):
    default_size=(360,160)
    def __init__(self,canvas,key):
        super().__init__(canvas); self.key=key; self.panel=ClipPanel(canvas,key); self.init_interaction()
    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.ensurePolished()
        self.restore_size()
        self.layout_card(); self.panel.show(); self.update_text()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        self.proxy.setGeometry(QRectF(14,86,max(290,minimum.width(),width-28),max(55,minimum.height(),height-100))); self.sync_bounds()
    def update_text(self):
        self.title=self.canvas.data()['clip_inputs'][self.key]['name']; self.panel.refresh(); super().update_text()
    def contextMenuEvent(self,event): self.canvas.clip_menu(self.key,event.screenPos()); event.accept()
    def detach(self): pass


class DragHandle(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedSize(18,34); self.setAccessibleName('拖曳執行列')
    def paintEvent(self,event):
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#737981'))
        for x in (5,11):
            for y in (10,16,22): painter.drawEllipse(x,y,3,3)


class ExecutionBar(QFrame):
    def __init__(self,canvas):
        super().__init__(canvas.view.viewport()); self.canvas=canvas; self.drag=None
        self.setObjectName('InsetPanel'); outer=QVBoxLayout(self); outer.setContentsMargins(10,8,10,8)
        body=QHBoxLayout(); self.control_layout=body; outer.addLayout(body)
        self.handle=DragHandle(); self.handle.setToolTip('拖曳執行列'); self.handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self.handle.installEventFilter(self); body.addWidget(self.handle)
        self.controls=canvas.window.run_controls
        self.status=label('','Subtle');self.status.setAlignment(Qt.AlignmentFlag.AlignRight);self.status.hide();outer.addWidget(self.status)
        self.progress=QProgressBar(); self.progress.setTextVisible(True); self.progress.setFixedHeight(20); self.progress.hide(); outer.addWidget(self.progress)
        self.progress.setStyleSheet('QProgressBar {background:#24282d;border:1px solid #393e45;border-radius:3px;color:#d4dce5;text-align:center;font-size:12px;} QProgressBar::chunk {background:#315c83;border-radius:2px;}')
        canvas.view.viewport().installEventFilter(self); self.hide()
    def attach(self):
        if self.controls.parentWidget() is not self: self.control_layout.insertWidget(1,self.controls)
        self.controls.set_execution_only(True); self.controls.show(); self.adjustSize(); self.place(); self.show(); self.raise_()
    def detach(self):
        self.hide(); self.controls.set_execution_only(False)
        self.canvas.window.builder_panel.layout().addWidget(self.controls); self.controls.show()
    def preview(self):
        self.canvas.functions.show_preview()
        if self.canvas.preview_card: self.canvas.view.centerOn(self.canvas.preview_card)
    def moveEvent(self,event):
        super().moveEvent(event)
        # This is a transparent viewport child, not a scene item. Moving it
        # does not invalidate the scene; repaint the exposed raster surface.
        self.parentWidget().update()
    def hideEvent(self,event):
        super().hideEvent(event); self.parentWidget().update()
    def resizeEvent(self,event):
        super().resizeEvent(event); self.parentWidget().update()
    def update_progress(self):
        client=getattr(self.canvas.window,'comfy',None); pipeline=client.generation.pipeline if client else None
        modern=bool(client and self.canvas.window.state.get('multi_output',{}).get('version',1)>=5)
        running=bool(client and client.running)
        self.status.setVisible(modern and running)
        node=client.input_flow.last_status.get('executing_node') if client else None
        self.status.setText(('正在生成 · 節點 #'+str(node)) if node is not None else '正在生成')
        chain=client.input_flow.chain.progress() if modern and self.canvas.window.state['multi_output']['version']<7 else ''
        if chain:self.status.setText(chain);self.status.show()
        active=pipeline is not None and pipeline.active()
        self.progress.setVisible(active)
        if active:
            total=len(pipeline.profiles)*pipeline.count
            self.progress.setRange(0,total); self.progress.setValue(pipeline.round*len(pipeline.profiles)+pipeline.index)
            self.progress.setFormat(f'第 {pipeline.round+1}／{pipeline.count} 輪 · 已完成 %v／%m')
            self.progress.setToolTip(client.generation.message)
        keys=set(pipeline.active_keys) if active else set()
        changed=keys.symmetric_difference(getattr(self.canvas,'active_keys',set()))
        self.canvas.active_keys=keys
        if changed:
            for item in self.canvas.view.scene().items():
                if getattr(item,'key',None) in changed:item.update()
        if self.isVisible():self.place()
    def place(self):
        area=self.parentWidget().rect().adjusted(12,12,-12,-12)
        self.setMaximumWidth(max(1,area.width())); self.adjustSize()
        x,y=self.canvas.window.state['settings'].get('execution_bar_position',[.5,1.0])
        self.move(area.left()+round(max(0,area.width()-self.width())*x),area.top()+round(max(0,area.height()-self.height())*y))
    def eventFilter(self,watched,event):
        if watched is self.parentWidget() and event.type()==QEvent.Type.Resize: QTimer.singleShot(0,self.place)
        if watched is self.handle:
            if event.type()==QEvent.Type.MouseButtonPress and event.button()==Qt.MouseButton.LeftButton:
                self.drag=(event.globalPosition().toPoint(),self.pos()); return True
            if event.type()==QEvent.Type.MouseMove and self.drag:
                pos=self.drag[1]+event.globalPosition().toPoint()-self.drag[0]; area=self.parentWidget().rect().adjusted(12,12,-12,-12)
                self.move(max(area.left(),min(pos.x(),area.right()-self.width()+1)),max(area.top(),min(pos.y(),area.bottom()-self.height()+1))); return True
            if event.type()==QEvent.Type.MouseButtonRelease and self.drag:
                self.drag=None; area=self.parentWidget().rect().adjusted(12,12,-12,-12)
                self.canvas.window.state['settings']['execution_bar_position']=[max(0,min(1,(self.x()-area.left())/max(1,area.width()-self.width()))),max(0,min(1,(self.y()-area.top())/max(1,area.height()-self.height())))]
                self.canvas.window.changed('settings'); return True
        return super().eventFilter(watched,event)
