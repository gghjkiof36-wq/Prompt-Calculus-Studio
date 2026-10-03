"""CLIP destination cards and the viewport execution bar."""
import copy
from PySide6.QtCore import Qt,QRectF,QEvent,QPoint,QTimer
from PySide6.QtGui import QPainter,QColor
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QLineEdit,QWidget,QSpinBox,QProgressBar
from .canvas_items import TextCard,canvas_colors
from .widgets import button,label,ComboBox,StudioDialog,dialog_buttons,RoundMenu
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
        super().__init__();self.canvas=canvas;self.key=key;self.setObjectName('CanvasModuleBody')
        body=QVBoxLayout(self);body.setContentsMargins(4,8,4,4);body.setSpacing(8)
        self.setup_status=label('');self.setup_status.setParent(self);self.setup_status.hide()
        self.binding=button('綁定 CLIP 節點',lambda:canvas.bind_dialog(key))
        from .ui_icons import icon
        self.binding.setIcon(icon('workflow'));self.binding.setProperty('iconName','workflow')
        self.binding.setToolTip('選擇工作流與 CLIP 節點')
        body.addWidget(self.binding)
        self.status=label('','Subtle',True);body.addWidget(self.status)
        body.addStretch()

    def refresh(self):
        state=self.canvas.window.state;binding=clip_flow.selected_binding(state,self.key)
        profile=next((p for p in options(state)['profiles'] if binding and p['id']==binding['workflow']),None)
        error=''
        if binding and profile:
            self.binding.setText(profile['name']+' · #'+binding['node']+' / '+binding['field'])
            if (binding['node'],binding['field']) not in text_fields(profile['graph']):error='綁定節點已移除，請重新選擇。'
        else:
            self.binding.setText('綁定 CLIP 節點')
            if binding:error='綁定的工作流已移除。'
        from .canvas_onboarding import setup_action,setup_badge
        pending=setup_action(self.canvas,self.binding,'clip_inputs',self.key)
        setup_badge(self.canvas,self.setup_status,pending)
        if error:self.binding.setText('重新綁定 CLIP')
        self.status.setText(error);self.status.setVisible(bool(error))


class ClipCard(TextCard):
    default_size=(360,174)
    def __init__(self,canvas,key):
        super().__init__(canvas); self.key=key; self.module_icon='workflow'; self.header_detail=True; self.panel=ClipPanel(canvas,key); self.init_interaction()
    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.ensurePolished()
        self.restore_size()
        self.layout_card(); self.panel.show(); self.update_text()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        self.proxy.setGeometry(QRectF(14,110,max(290,minimum.width(),width-28),max(50,minimum.height(),height-124))); self.sync_bounds()
    def paint(self,painter,option,widget=None):
        super().paint(painter,option,widget)
        from .canvas_items import canvas_colors
        colors=canvas_colors(self.canvas);pending=self.panel.binding.property('setupPending')
        painter.save()
        if pending:
            painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(colors['selected']))
            painter.drawRoundedRect(QRectF(18,38,62,23),5,5)
        font=painter.font();font.setPointSizeF(10);font.setBold(bool(pending));painter.setFont(font)
        painter.setPen(QColor(colors['text' if pending else 'secondary']))
        painter.drawText(QRectF(18,38,62,23),Qt.AlignmentFlag.AlignCenter,self.panel.setup_status.text())
        painter.restore()
    def update_text(self):
        self.title=self.canvas.data()['clip_inputs'][self.key]['name']; self.panel.refresh(); super().update_text()
    def contextMenuEvent(self,event): self.canvas.clip_menu(self.key,event.screenPos()); event.accept()
    def detach(self): pass


class DragHandle(QWidget):
    def __init__(self):
        super().__init__(); self.setFixedSize(14,30); self.setAccessibleName('拖曳執行列')
    def paintEvent(self,event):
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(self.palette().color(self.foregroundRole()))
        for x in (3,8):
            for y in (8,14,20): painter.drawEllipse(x,y,2.5,2.5)


class ExecutionBar(QFrame):
    def __init__(self,canvas):
        super().__init__(canvas.view.viewport()); self.canvas=canvas; self.drag=None
        self.compact=False;self._status_text=''
        self.setObjectName('ExecutionBar'); outer=QVBoxLayout(self); outer.setContentsMargins(8,6,8,6)
        body=QHBoxLayout();body.setSpacing(6); self.control_layout=body; outer.addLayout(body)
        self.handle=DragHandle(); self.handle.setToolTip('拖曳執行列'); self.handle.setCursor(Qt.CursorShape.SizeAllCursor)
        self.handle.installEventFilter(self); body.addWidget(self.handle)
        self.controls=canvas.window.run_controls
        self.more=button('更多',self.show_more,'Quiet');self.more.setAccessibleName('更多畫布工具')
        self.more.setToolTip('縮放畫布與任務紀錄');body.addWidget(self.more);self.more.hide()
        self.status=label('','CanvasNotice',True);self.status.setMinimumWidth(0)
        canvas.view.set_execution_status_widget(self.status)
        self.progress=QProgressBar(); self.progress.setTextVisible(True); self.progress.setFixedHeight(20); self.progress.hide(); outer.addWidget(self.progress)
        self.refresh_visual_theme()
        canvas.view.viewport().installEventFilter(self); self.hide()

    def refresh_visual_theme(self):
        colors=canvas_colors(self.canvas)
        self.progress.setStyleSheet(f"QProgressBar {{background:{colors['field']};border:1px solid {colors['line']};border-radius:4px;color:{colors['text']};text-align:center;font-size:12px;}} QProgressBar::chunk {{background:{colors['selected']};border-radius:3px;}}")
    def attach(self):
        if self.controls.parentWidget() is not self: self.control_layout.insertWidget(1,self.controls)
        self.controls.set_execution_only(True); self.controls.show(); self.update_progress(); self.adjustSize(); self.place(); self.show(); self.raise_()
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
        self.place_zoom_controls()
    def hideEvent(self,event):
        super().hideEvent(event); self.parentWidget().update();self.place_zoom_controls()
    def showEvent(self,event):
        super().showEvent(event);self.place_zoom_controls()
    def resizeEvent(self,event):
        super().resizeEvent(event); self.parentWidget().update();self.place_zoom_controls()
    def place_zoom_controls(self):
        controls=getattr(self.canvas,'zoom_controls',None)
        if controls is not None:controls.place()
        self.canvas.view.position_status()
    def tools_menu(self):
        menu=RoundMenu(self)
        menu.addAction('縮小畫布',lambda:self.canvas.zoom(1/1.15))
        menu.addAction('原始大小（100%）',lambda:self.canvas.zoom_to(1.))
        menu.addAction('放大畫布',lambda:self.canvas.zoom(1.15))
        menu.addAction('流程總覽',self.canvas.fit)
        menu.addSeparator()
        menu.addAction(f'任務紀錄（{self.controls.activity_count} 個活動任務）',lambda:self.canvas.window.generation_panel.history())
        return menu
    def show_more(self):
        menu=self.tools_menu();menu.ensurePolished()
        menu.open_at(self.more.mapToGlobal(QPoint(0,-menu.sizeHint().height())))
    def present_status(self):
        self.status.setText(self._status_text);self.status.setToolTip(self._status_text)
        self.status.setAccessibleName(self._status_text)
        self.canvas.view.position_status()
    def update_progress(self):
        client=getattr(self.canvas.window,'comfy',None); pipeline=client.generation.pipeline if client else None
        modern=bool(client and self.canvas.window.state.get('multi_output',{}).get('version',1)>=5)
        running=bool(client and client.running)
        node=client.input_flow.last_status.get('executing_node') if client else None
        status=(('正在生成 · 節點 #'+str(node)) if node is not None else '正在生成') if modern and running else ''
        if not running:
            # This is guidance only: execution eligibility stays with the
            # existing runner, including workflows that only sync inputs.
            from .canvas_onboarding import pending_setup
            pending=pending_setup(self.canvas.window.state)
            notes=[]
            if client and client.connected:
                if not client.snapshot_compatible:notes.append('請重新啟動 ComfyUI')
                elif not client.can_run:notes.append('請在連線設定確認 ComfyUI 擴充')
            if pending:
                notes.append({'clip_inputs':'CLIP 待綁定','image_inputs':'圖片輸入待綁定','stages':'Stage 待選擇工作流'}[pending[0][0]])
            elif not self.controls.run_button.isEnabled() and client and client.can_run:notes.append('請先加入提示詞')
            status=' · '.join(notes)
        chain=client.input_flow.chain.progress() if modern and self.canvas.window.state['multi_output']['version']<7 else ''
        self._status_text=chain or status;self.present_status()
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
        margins=self.layout().contentsMargins()
        required=margins.left()+margins.right()+self.handle.width()+self.controls.expanded_width_hint()
        required+=self.control_layout.spacing()*max(0,self.control_layout.count()-1)
        compact=area.width()<760 or area.height()<480 or required>area.width()
        self.compact=compact;self.more.setVisible(compact)
        self.more.setFixedWidth(max(64,self.more.fontMetrics().horizontalAdvance('更多')+24))
        self.layout().setContentsMargins(8,6,8,6)
        self.layout().setSpacing(4 if compact else 6)
        self.controls.set_compact(compact)
        self.present_status()
        self.controls.layout().activate();self.layout().activate()
        self.setMaximumWidth(max(1,area.width())); self.adjustSize()
        self.present_status()
        x,y=self.canvas.window.state['settings'].get('execution_bar_position',[.5,1.0])
        self.move(area.left()+round(max(0,area.width()-self.width())*x),area.top()+round(max(0,area.height()-self.height())*y))
        self.place_zoom_controls()
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
