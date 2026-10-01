"""Bounded canvas containers over the existing text-card editing gestures."""
import copy
from PySide6.QtCore import Qt,QRectF,QPointF,QTimer
from PySide6.QtWidgets import (QFrame,QVBoxLayout,QLineEdit,QPlainTextEdit,QListWidget,QListWidgetItem,
    QAbstractItemView,QDialog,QFormLayout,QGraphicsItem,QSplitter,QWidget)
from .output_order import OutputOrderList
from .text_canvas import TextCanvas,HISTORY_FIELDS
from .canvas_items import TextCard,PreviewCard,NodeCard
from .canvas_functions import CanvasFunctions,SourceCard
from .flow_items import CanvasContainer,Port,FlowLine,curve
from .widgets import label,button,row,ComboBox,RoundMenu,StudioDialog,dialog_buttons,InputDialog,widget_global_position
from .core import build_prompt,validate_state,output_groups
from .generation import active_profile,text_fields
from . import composition as comp
from . import multi_output as model
from . import clip_flow
from .clip_widgets import ClipCard,ExecutionBar,ClipBindingDialog
from .quiet_splitter import QuietSplitter


class OutputPanel(QFrame):
    def __init__(self,canvas,key):
        super().__init__(); self.canvas=canvas; self.key=key; self.updating=False
        self.setObjectName('InsetPanel'); body=QVBoxLayout(self); body.setContentsMargins(14,10,14,14)
        self.order=OutputOrderList(); self.order.setMinimumHeight(60)
        self.order.orderChanged.connect(self.reordered)
        self.order.removeRequested.connect(self.remove_items)
        self.order.itemDoubleClicked.connect(lambda item:canvas.focus_node(item.data(Qt.ItemDataRole.UserRole)))
        self.order.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.order.customContextMenuRequested.connect(self.context)
        self.split=QuietSplitter(Qt.Orientation.Vertical); self.split.setChildrenCollapsible(False)
        self.split.setObjectName('PromptSplit')
        self.split.setToolTip('拖曳分隔線，調整組合清單與文字欄的比例。')
        listing=QWidget(); list_layout=QVBoxLayout(listing); list_layout.setContentsMargins(0,0,0,0)
        list_layout.addWidget(label('目前組合','Subtle')); list_layout.addWidget(self.order)
        list_layout.addWidget(button('排列文字來源',lambda:canvas.order_sources(key),'Quiet'))
        text_panel=QWidget(); text_layout=QVBoxLayout(text_panel); text_layout.setContentsMargins(0,0,0,0)
        self.draft_status=label('自動文字','Subtle'); text_layout.addWidget(self.draft_status)
        self.editor=QPlainTextEdit(); self.editor.setObjectName('Prompt'); self.editor.setMinimumSize(280,90)
        self.editor.setPlaceholderText('連接文字來源後產生內容，也可編輯手動版本。'); self.editor.textChanged.connect(self.edited); text_layout.addWidget(self.editor,1)
        self.split.addWidget(listing); self.split.addWidget(text_panel); body.addWidget(self.split,1)
        self.split.splitterMoved.connect(self.save_ratio); self.loaded_ratio=None
        self.conflicts=label('','ConflictNotice',True); body.addWidget(self.conflicts)
        self.clear=button('清除手動內容',self.clear_draft,'ClearDraft')
        body.addLayout(row(button('存入素材庫',lambda:canvas.window.new_item(self.editor.toPlainText()),'Quiet'),None,self.clear))
        body.addWidget(button('複製完整 Prompt',lambda:canvas.window.copy_text(self.editor.toPlainText()),'Quiet'))
    def save_ratio(self,*_):
        sizes=self.split.sizes()
        if sum(sizes):
            self.loaded_ratio=sizes[0]/sum(sizes)
            self.canvas.data()['outputs'][self.key]['panel_ratio']=self.loaded_ratio; self.canvas.window.changed('layout')
    def restore_ratio(self):
        ratio=self.canvas.data()['outputs'][self.key].get('panel_ratio',.45)
        if ratio!=self.loaded_ratio:
            self.split.setSizes([round(ratio*1000),round((1-ratio)*1000)]); self.loaded_ratio=ratio
    def context(self,pos):
        item=self.order.itemAt(pos)
        if item:
            self.order.setCurrentItem(item)
            key=item.data(Qt.ItemDataRole.UserRole)
            self.canvas.view.scene().clearSelection()
            if key in self.canvas.cards:self.canvas.cards[key].setSelected(True)
            self.canvas.context(key,widget_global_position(self.order.viewport(),pos,self.canvas.view),[key])
    def remove_items(self,keys):
        if self.updating or not self.order.isEnabled():return
        from .flow_data import canvas_ids
        members=[k for cid in canvas_ids(self.canvas.data()['outputs'][self.key]) for k in self.canvas.data()['canvases'][cid]['members']]
        self.canvas.remove_keys([key for key in keys if key in members])
    def reordered(self,*args):
        if self.updating: return
        keys=[self.order.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.order.count())]; canvas=self.canvas
        from .flow_data import canvas_ids
        def reorder(state):
            for cid in canvas_ids(state['multi_output']['outputs'][self.key]):
                value=state['multi_output']['canvases'][cid]
                value['members']=[k for k in keys if k in value['members']]
        QTimer.singleShot(0,lambda:canvas.commit(reorder))
    def edited(self):
        if self.updating: return
        from .drafts import edit
        state=self.canvas.window.state; output=state['multi_output']['outputs'][self.key]
        edit(state,self.editor.toPlainText(),self.key)
        if state['multi_output']['current_output']==self.key:
            self.canvas.window.updating=True; self.canvas.window.final.setPlainText(output['draft']); self.canvas.window.updating=False
        self.canvas.window.changed()
    def clear_draft(self):
        from .drafts import clear
        self.canvas.commit(lambda s:clear(s,self.key))
    def refresh(self):
        self.updating=True; state=self.canvas.window.state; output=self.canvas.data()['outputs'][self.key]
        try: compiled=model.compile_output(state,self.key); text=compiled['final_prompt']; affected=compiled['affected']
        except ValueError: text=output['draft'] or ''; affected={}
        if self.editor.toPlainText()!=text: self.editor.setPlainText(text)
        from .flow_data import canvas_ids
        cid=output['canvas']; members=[k for c in canvas_ids(output) for k in self.canvas.data()['canvases'][c]['members']]
        prior=[self.order.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.order.count())]
        if prior!=members:
            self.order.clear()
            for key in members:
                item=QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole,key); self.order.addItem(item)
        for i,key in enumerate(members):
            root=state['uses'][key]; item=self.order.item(i)
            item.setText(root['name']+(f" · ×{root['weight']/10:.1f}" if root['weight']!=10 else '')+(' · 停用' if not root['enabled'] else ''))
            item.setToolTip(comp.render(root))
        manual=output['draft'] is not None
        self.order.setEnabled(not manual); self.clear.setEnabled(manual)
        self.draft_status.setText('正在使用手動版本' if manual else '自動文字' if cid else '缺少來源畫布')
        self.conflicts.setText('；'.join(', '.join(v['tags'])+' ← '+', '.join(v['by']) for v in affected.values())); self.conflicts.setVisible(bool(affected))
        self.updating=False


class OutputCard(TextCard):
    default_size=(440,550)
    def __init__(self,canvas,key):
        super().__init__(canvas); self.key=key; self.panel=OutputPanel(canvas,key); self.init_interaction()
    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.ensurePolished()
        self.restore_size(); self.layout_card(); self.panel.show(); self.panel.restore_ratio(); self.panel.refresh()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        from .flow_data import text_sources
        top=max(102,90+24*len(text_sources(self.canvas.data()['outputs'][self.key])))
        self.proxy.setGeometry(QRectF(14,top,max(350,minimum.width(),width-28),max(300,minimum.height(),height-top-14))); self.sync_bounds()
    def update_text(self):
        self.title=self.canvas.data()['outputs'][self.key]['name']; self.panel.refresh(); super().update_text()
    def contextMenuEvent(self,event): self.canvas.output_menu(self.key,event.screenPos()); event.accept()
    def detach(self): pass


class FlowFunctions(CanvasFunctions):
    def outline(self,card,path): return path
    def layout(self,skip=None): self.canvas.update_lines()
    def moving(self,card,position): self.canvas.update_lines()
    def dropped(self,card,position): return False
    def mode_changed(self): pass
    def set_attachment(self,state,key,attached):
        if key=='__result_preview__': return
        if attached:
            target=state['multi_output']['current_output']
            if not target: raise ValueError('請先新增 Prompt 輸出。')
            model.connect(state,key,target,'image')
        else:
            for line in list(state['multi_output']['connections']):
                if line['source']==key and line['kind']=='image': model.disconnect(state,line['id'])
    def attach(self,key,attached=True): self.canvas.commit(lambda s:self.set_attachment(s,key,attached)); self.window.generation_panel.changed()
    def refresh(self):
        data=self.data()
        for key in list(self.cards):
            if key not in data['images']:
                card=self.cards.pop(key); self.canvas.view.scene().removeItem(card); card.deleteLater()
        for index,key in enumerate(data['images']):
            if key not in self.cards: self.cards[key]=SourceCard(self,key); self.canvas.view.scene().addItem(self.cards[key])
            card=self.cards[key]; card.panel.attach_button.hide()
            card.attach(); card.setPos(*self.window.state.get('text_positions',{}).get(key,[900,850+index*390]))
        if self.canvas.preview_card: self.canvas.preview_card.setVisible(data.get('preview',True))
    def remove(self,key):
        def apply(s):
            if key=='__result_preview__':
                self.data(s)['preview']=False
                s['multi_output']['connections']=[c for c in s['multi_output']['connections'] if c['destination']!=key]
            else:
                for line in list(s['multi_output']['connections']):
                    if line['source']==key or line['destination']==key: model.disconnect(s,line['id'])
                self.data(s)['images'].pop(key,None)
        self.canvas.commit(apply)
    def source_position(self,output):
        card=self.canvas.outputs.get(output)
        rect=card.sceneBoundingRect() if card else QRectF(*self.window.state.get('text_positions',{}).get(output,[650,100]),540,650)
        width,height,gap=360,340,36
        candidates=[QRectF(rect.left()-width-gap,rect.top(),width,height),QRectF(rect.left(),rect.bottom()+gap,width,height),
                    QRectF(rect.right()+gap,rect.top(),width,height),QRectF(rect.left(),rect.top()-height-gap,width,height)]
        others=[c.sceneBoundingRect() for c in list(self.canvas.containers.values())+list(self.canvas.outputs.values())+list(self.cards.values())
                +list(self.canvas.clips.values())+[c for c in (self.canvas.preview_card,) if c]]
        def overlap(candidate):
            return sum(max(0,r.width())*max(0,r.height()) for r in (candidate.intersected(other) for other in others))
        return min(candidates,key=overlap).topLeft()
    def use_source(self,source,output=None):
        output=output or self.canvas.data()['current_output']
        if output not in self.canvas.data()['outputs']: raise ValueError('請先新增 Prompt 輸出。')
        line=next((c for c in self.canvas.data()['connections'] if c['kind']=='image' and c['destination']==output),None)
        if line and line['source'] in self.data()['images']:
            key=line['source']; self.canvas.commit(lambda s:self.data(s)['images'][key].update(source=copy.deepcopy(source)))
        else:
            from .core import uid
            key='__source_'+uid(); position=self.source_position(output)
            def apply(state):
                self.data(state)['images'][key]=dict(source=copy.deepcopy(source),attached=False)
                state.setdefault('text_positions',{})[key]=[position.x(),position.y()]
                model.connect(state,key,output,'image')
            self.canvas.commit(apply)
        return key
    def menu(self,parent,position=None):
        menu=RoundMenu(parent); menu.setTitle('功能模組')
        for title,action in self.insertion_actions(position): menu.addAction(title,action)
        return menu
    def insertion_actions(self,position=None):
        return [('畫布',lambda:self.canvas.add_canvas(position)),
                ('Prompt 控制',lambda:self.canvas.add_output(position)),
                ('CLIP 輸入',lambda:self.canvas.add_clip(position)),
                ('圖片來源',lambda:self.add_image(position=position)),
                ('加載圖片（單張）',lambda:self.add_image(position=position,enhanced=False)),
                ('讀取文字',lambda:self.add_text_reader(position)),
                ('ComfyUI 圖片輸入',lambda:self.canvas.add_flow_node('image_inputs',position)),
                ('預排程',lambda:self.canvas.add_flow_node('schedulers',position)),
                ('Stage／執行階段',lambda:self.canvas.add_flow_node('stages',position)),
                ('依現有綁定補建 Stage',self.canvas.migrate_stages),
                ('預覽圖片',self.show_preview)]
    def add_text_reader(self,position=None):
        from .result_data import add_text_reader
        position=position or self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
        self.canvas.commit(lambda s:add_text_reader(s,(position.x(),position.y())))
    def context(self,key,position):
        menu=RoundMenu(self.window)
        if key in self.cards:
            if hasattr(self.cards[key].panel,'choose_file'):menu.addAction('選擇圖片…',self.cards[key].panel.choose_file)
            if self.data()['images'][key].get('reader')!='text':
                embed=menu.addMenu('嵌入畫布')
                for cid,value in self.canvas.data()['canvases'].items(): embed.addAction(value['name'],lambda checked=False,c=cid:self.canvas.embed_source(key,c))
        menu.addAction('移除',lambda:self.remove(key)); menu.open_at(position)


class MultiCanvas(TextCanvas):
    def __init__(self,window):
        super().__init__(window); self.functions=FlowFunctions(self); self.containers={}; self.outputs={}; self.ports={}; self.lines={}
        self.clips={}; self.flow_cards={}; self.order_card=None; self.execution_bar=ExecutionBar(self)
        if window.state['multi_output']['version']>=7:
            from .stage_widgets import RecentCorner
            self.recent_corner=RecentCorner(self)
        self.insertion_canvas=None; self.image_previews={}; self.editor_page=None; self.z_counter=-.9; self.refreshing=False
        from .flow_items import ConnectionGesture
        self.connection_gesture=ConnectionGesture(self)
        self.output_picker=ComboBox(); self.output_picker.currentIndexChanged.connect(self.choose_current)
        self.output_picker.setToolTip('選項隔離開啟時，每份輸出分別保存清單內容；切回畫布模式使用各畫布內容。')
        window.builder_panel.layout().insertWidget(0,self.output_picker)
    def data(self): return self.window.state['multi_output']
    def next_z(self): self.z_counter+=.0001; return min(-.2,self.z_counter)
    def history_state(self):
        return {k:copy.deepcopy(self.window.state[k]) for k in HISTORY_FIELDS+('multi_output','draft','draft_base') if k in self.window.state}
    def commit(self,operation):
        old_connections=copy.deepcopy(self.data()['connections'])
        def apply(s):
            operation(s)
            client=getattr(self.window,'comfy',None)
            if s['multi_output']['version']>=7 and client:
                from .stage_context import sync_result_view
                sync_result_view(s,self.window.store.directory,client.input_flow.chain.results)
            elif s['multi_output']['version']>=5:
                from .flow_data import materialize
                materialize(s)
            model.reconcile(s)
        result=super().commit(apply)
        client=getattr(self.window,'comfy',None)
        if result and client and self.data()['version']>=7:
            client.input_flow.chain.route_changed()
        if result and client and old_connections!=self.data()['connections']:
            removed=[c for c in old_connections if c not in self.data()['connections']]
            for owner,control in client.input_flow.local_controls():
                scheduler=control.get('route',{}).get('scheduler')
                if scheduler and any(scheduler in (c['source'].split('::')[0],c['destination'].split('::')[0]) for c in removed):
                    client.input_flow.detach(owner)
        if result and self.undo_stack:
            before,_=self.undo_stack[-1]; after=self.history_state(); self.undo_stack[-1]=(before,after); self.last_state=after
        return result
    def restore_history(self,source,destination,after):
        from .snapshot_history import restore as restore_import
        previous=self.window.state
        canvas_focus=self.view.hasFocus() and self.view.scene().focusItem() is None
        if restore_import(self,source,destination,after):
            if self.window.state is not previous:
                client=getattr(self.window,'comfy',None)
                if client:
                    runner=client.input_flow.chain
                    if previous['workspace']!=self.window.state['workspace']:
                        runner.workspace_changed(previous['workspace']);runner.load_results()
                    if self.data()['version']>=7:
                        runner.refresh_results();runner.route_changed()
                if canvas_focus:self.view.setFocus();self.view.scene().clearFocus()
            return
        if not source: return
        before,later=source[-1]; expected,target=(before,later) if after else (later,before)
        from .edit_history import merge
        value=copy.deepcopy(self.window.state)
        try:
            merged=merge(self.history_state(),expected,target)
            for key in HISTORY_FIELDS+('multi_output','draft','draft_base'):value.pop(key,None)
            value.update(merged);validate_state(value)
        except ValueError as exc:self.window.notice(str(exc));return
        old_images=self.window.state.get('canvas_functions',{}).get('images',{})
        new_images=value.get('canvas_functions',{}).get('images',{})
        reader_changed=any(old_images.get(key,{}).get(field)!=new_images.get(key,{}).get(field)
            for key in old_images.keys()|new_images.keys() for field in ('output_node','text_field','input_index','stage_reference'))
        source.pop(); destination.append((before,later)); self.window.state=value; self.last_state=self.history_state()
        from .changes import layout_only
        self.sync('layout' if layout_only(expected,target) else 'prompt')
        if reader_changed and self.data()['version']>=7:self.window.comfy.input_flow.chain.refresh_results()
        if self.data()['version']>=7:self.window.comfy.input_flow.chain.route_changed()
        if canvas_focus:self.view.setFocus();self.view.scene().clearFocus()
    def choose_current(self):
        key=self.output_picker.currentData()
        if key and key!=self.data()['current_output']:
            model.select_output(self.window.state,key); self.last_state=None; self.window.refresh_library(); self.window.refresh_builder(); self.window.changed()
    def prune_cards(self):
        """Discard scene objects whose owners left the current document.

        Import and workspace changes can happen while Settings hides the Canvas.
        Deferred output/layout refreshes must not use cards from the old model;
        constructing new cards remains the visible Canvas refresh's job.
        """
        state=self.window.state; data=self.data(); removed=set()
        node_keys={key for key in state.get('uses',{})}
        for key,root in state.get('uses',{}).items():
            node_keys.update(key+':'+part['id'] for part in comp.walk(root) if part is not root)
        flow_keys={key for kind in ('schedulers','image_inputs','stages') for key in data.get(kind,{})}
        for collection,keys in ((self.containers,data['canvases']), (self.outputs,data['outputs']),
                (self.clips,data['clip_inputs']), (self.flow_cards,flow_keys), (self.cards,node_keys),
                (self.functions.cards,state.get('canvas_functions',{}).get('images',{}))):
            for key in list(collection):
                if key not in keys:removed.add(collection.pop(key))
        if not removed:return
        self.connection_gesture.cancel()
        # Ports belong to cards; discard references before Qt destroys parents.
        for key,port in list(self.ports.items()):
            if port.parentItem() in removed:
                self.ports.pop(key);self.view.scene().removeItem(port);port.setParentItem(None);port.deleteLater()
        connections={value['id'] for value in data['connections']}
        for key,line in list(self.lines.items()):
            if key not in connections or not all(self.line_port(line.value,output) for output in (False,True)):
                self.lines.pop(key);self.view.scene().removeItem(line)
        for item in removed:
            if item.parentItem() not in removed:
                self.view.scene().removeItem(item);item.deleteLater()
        if self.output in removed:self.output=None
    def update_output(self):
        if not hasattr(self,'outputs'): return
        self.prune_cards()
        model.capture_current(self.window.state)
        self.output_picker.blockSignals(True); self.output_picker.clear()
        for key,o in self.data()['outputs'].items(): self.output_picker.addItem('目前輸出 · '+o['name'],key)
        self.output_picker.setCurrentIndex(self.output_picker.findData(self.data()['current_output'])); self.output_picker.blockSignals(False)
        for card in self.outputs.values(): card.update_text()
        for card in self.clips.values(): card.update_text()
        for card in self.flow_cards.values():card.update_text()
        if self.order_card: self.order_card.update_text()
        if self.preview_card: self.preview_card.update_text()
        preview_links=tuple(sorted(model.connected_outputs(self.window.state,'preview')))
        if self.preview_card and getattr(self,'preview_links',None)!=preview_links:
            self.preview_links=preview_links; self.results.refresh()
        self.update_lines()
        if self.editor_page and not self.editor_page.refreshing: self.editor_page.refresh()
    def refresh_layout(self):
        self.prune_cards()
        if self.isVisible():super().refresh_layout()
    def refresh(self):
        if not hasattr(self,'outputs') or self.refreshing: return
        self.refreshing=True
        try:
            self.connection_gesture.cancel()
            now=self.history_state()
            state=self.window.state; selected={c.key for c in self.view.scene().selectedItems() if hasattr(c,'key')}
            self.entries={}
            for key,root in state.get('uses',{}).items():
                self.entries[key]=(key,root['id'])
                for part in comp.walk(root):
                    if part is not root: self.entries[key+':'+part['id']]=(key,part['id'])
            if not self.isVisible(): self.last_state=now; self.update_output(); return
            for line in self.lines.values(): self.view.scene().removeItem(line)
            self.lines={}
            for port in self.ports.values(): self.view.scene().removeItem(port); port.setParentItem(None); port.deleteLater()
            self.ports={}
            for card in list(self.cards.values()):
                if card.parentItem() is None: self.view.scene().removeItem(card); card.deleteLater()
            self.cards={}
            for collection,values in [(self.containers,self.data()['canvases']),(self.outputs,self.data()['outputs']),(self.clips,self.data()['clip_inputs'])]:
                for key in list(collection):
                    if key not in values:
                        item=collection.pop(key); self.view.scene().removeItem(item); item.deleteLater()
            for key,value in self.data()['canvases'].items():
                if key not in self.containers: self.containers[key]=CanvasContainer(self,key); self.view.scene().addItem(self.containers[key])
                self.containers[key].refresh()
            for index,(key,value) in enumerate(state.get('uses',{}).items()):
                cid=model.owner(state,key); card=NodeCard(self,key,copy.deepcopy(value),'模組' if cid else '未分配',not value['enabled'])
                self.view.scene().addItem(card); default=[-1500,index*170]
                if cid:
                    parent=self.containers[cid]; at=self.data()['canvases'][cid]['members'].index(key); default=[parent.x()+24,parent.y()+64+at*170]
                card.setPos(*state.get('text_positions',{}).get(key,default)); card.setSelected(key in selected)
            self.ensure_bounds()
            self.refresh_image_previews()
            for index,key in enumerate(self.data()['outputs']):
                if key not in self.outputs: self.outputs[key]=OutputCard(self,key); self.view.scene().addItem(self.outputs[key])
                card=self.outputs[key]; card.attach(); card.setPos(*state.get('text_positions',{}).get(key,[index*510,-350])); card.setSelected(key in selected)
            self.output=self.outputs.get(self.data()['current_output']) or next(iter(self.outputs.values()),None)
            for index,key in enumerate(self.data()['clip_inputs']):
                if key not in self.clips: self.clips[key]=ClipCard(self,key); self.view.scene().addItem(self.clips[key])
                card=self.clips[key]; card.attach(); card.setPos(*state.get('text_positions',{}).get(key,[650,index*300-350])); card.setSelected(key in selected)
            from .flow_widgets import FlowCard
            active_flow={key:kind for kind in ('schedulers','image_inputs','stages') for key in self.data().get(kind,{})}
            for key in list(self.flow_cards):
                if key not in active_flow:
                    card=self.flow_cards.pop(key);self.view.scene().removeItem(card);card.deleteLater()
            for key,kind in active_flow.items():
                if key not in self.flow_cards:
                    self.flow_cards[key]=FlowCard(self,key,kind);self.view.scene().addItem(self.flow_cards[key])
                card=self.flow_cards[key];card.attach();card.setPos(*state.get('text_positions',{}).get(key,[1000,400]));card.setSelected(key in selected)
            self.execution_bar.attach()
            if self.data().get('workflow_order',{}).get('visible'):
                from .workflow_widgets import WorkflowOrderCard
                if self.order_card is None: self.order_card=WorkflowOrderCard(self); self.view.scene().addItem(self.order_card)
                self.order_card.attach(); self.order_card.setPos(*state.get('text_positions',{}).get(self.order_card.key,[1100,450])); self.order_card.show()
            elif self.order_card: self.order_card.hide()
            if self.preview_card is None: self.preview_card=PreviewCard(self); self.view.scene().addItem(self.preview_card)
            self.preview_card.attach(); self.preview_card.setPos(*state.get('text_positions',{}).get('__result_preview__',[600,450])); self.results.refresh()
            self.functions.refresh()
            for key,container in self.containers.items():
                self.add_port(container,key,'text',True); self.add_port(container,key,'image',True,index=1); self.add_port(container,key,'image',False)
                if self.data()['version']>=5:
                    self.add_port(container,key,'content',False,index=1);self.add_port(container,key,'clip',False,index=2)
            for index,(key,card) in enumerate(self.outputs.items()):
                from .flow_data import text_sources
                inputs=text_sources(self.data()['outputs'][key])
                for at,cid in enumerate(inputs):self.add_port(card,key,'text' if cid in self.data()['canvases'] else 'clip',False,slot=cid,index=at)
                self.add_port(card,key,'text',False,index=len(inputs))
                self.add_port(card,key,'image',False,index=len(inputs)+1)
                self.add_port(card,key,'clip',True)
            for key,card in self.clips.items():
                self.add_port(card,key,'clip',False);self.add_port(card,key,'control',True)
            for key,card in self.functions.cards.items():
                if state['canvas_functions']['images'][key].get('reader')=='text':
                    self.add_port(card,key,'clip',False);self.add_port(card,key,'clip',True);continue
                self.add_port(card,key,'image',True)
                if self.data()['version']>=7:self.add_port(card,key,'image',False)
                if self.data()['version']>=5:
                    self.add_port(card,key,'content',True,index=1);self.add_port(card,key,'clip',True,index=2)
            from .flow_data import endpoint
            for key,card in self.flow_cards.items():
                if card.kind=='image_inputs':
                    self.add_port(card,key,'image',False);self.add_port(card,key,'control',True)
                elif card.kind=='stages':
                    from .stage_model import controls
                    connected=controls(state,key)+[c['source'] for c in self.data()['connections'] if c['kind']=='done' and c['destination']==key]
                    for index,source in enumerate(connected):self.add_port(card,key,'control',False,slot=source,index=index)
                    self.add_port(card,key,'control',False,index=len(connected))
                    for index,kind in enumerate(('flow','image','clip')):self.add_port(card,key,kind,True,index=index)
                else:
                    for index,channel in enumerate(self.data()['schedulers'][key]['channels']):
                        for output in (False,True):self.add_port(card,endpoint(key,channel['id']),channel['type'],output,index=index)
                    if self.data()['version']>=7:
                        index=len(self.data()['schedulers'][key]['channels'])
                        for output in (False,True):self.add_port(card,key+'::flow','flow',output,index=index)
            if self.data()['version']>=4:self.add_port(self.preview_card,model.PREVIEW,'image',False)
            if self.order_card and self.data()['workflow_order'].get('visible'):
                from .chain_connections import endpoint as stage_endpoint,ADD
                from .chain_model import definition
                stages=(definition(state) or {}).get('stages',[])
                for i,stage in enumerate(stages):self.add_port(self.order_card,stage_endpoint(stage['id']),'control',False,index=i)
                self.add_port(self.order_card,ADD,'control',False,index=len(stages))
            for value in self.data()['connections']:
                line=FlowLine(self,value); self.lines[value['id']]=line; self.view.scene().addItem(line)
            self.update_output(); self.view.scene().setSceneRect(self.content_bounds().adjusted(-2400,-1800,2400,1800))
            self.last_state=self.history_state()
            self.remember_layout_defaults()
        finally: self.refreshing=False
    def refresh_image_previews(self):
        from .composition_image import render_image,PreviewCache,canvas_document
        import json
        if not hasattr(self,'composition_cache'): self.composition_cache=PreviewCache(); self.preview_signatures={}
        state=self.window.state
        # A display projection keeps live node images out of saved drafts/undo.
        if hasattr(self.window,'comfy'):
            state=dict(state); state['canvas_functions']=copy.deepcopy(state.get('canvas_functions',{}))
            for key,value in state['canvas_functions'].get('images',{}).items():
                if value.get('binding'):value['source']=self.window.comfy.images.source(key)
        self.image_previews={k:v for k,v in self.image_previews.items() if k in self.data()['canvases']}
        preview_limit=min(1024,int((16*1024*1024/max(1,len(self.data()['canvases'])))**.5))
        for key in self.data()['canvases']:
            try: document=canvas_document(state,key)
            except ValueError: document=None
            signature=str(preview_limit)+json.dumps(document,sort_keys=True)
            if self.preview_signatures.get(key)!=signature:
                try:self.image_previews[key]=render_image(document,self.window.store.directory,self.composition_cache,preview_limit,False) if document else None
                except (OSError,ValueError):self.image_previews[key]=None
                self.preview_signatures[key]=signature
                if key in self.containers:self.containers[key].update()
    def add_port(self,parent,key,kind,output,slot=None,index=0):
        self.ports[(key,kind,output,slot) if slot else (key,kind,output)]=Port(self,parent,key,kind,output,slot,index)
    def line_port(self,value,output):
        key=value['source'] if output else value['destination']; kind=value['kind']
        if kind=='done' and self.data()['version']>=7:kind='flow' if output else 'control'
        return self.ports.get((key,kind,output,value['source']) if not output and (kind in ('execution','preview','text') or kind=='control' and key in self.data().get('stages',{}) or kind=='clip' and key in self.outputs) else (key,kind,output))
    def update_lines(self):
        if not hasattr(self,'ports'): return
        for port in self.ports.values():
            parent=port.parentItem(); y=58+port.index*24 if port.slot else 82 if port.key in self.outputs or port.key in self.containers else 58
            if not port.slot and port.kind in ('text','execution','clip'): y=58
            if self.data()['version']>=5:y=58+port.index*24
            if port.key in self.data().get('stages',{}):y+=24
            port.setPos(parent.width if port.output else 0,y)
        for line in self.lines.values():
            value=line.value; source=self.line_port(value,True); dest=self.line_port(value,False)
            if source and dest: line.setPath(curve(source.scenePos(),dest.scenePos(),self.window.state['settings'].get('connection_style','curve')))
    def minimum_canvas(self,key):
        cards=[self.cards[k] for k in self.data()['canvases'][key]['members'] if k in self.cards]
        return max([360]+[c.width+48 for c in cards]),max([230]+[c.height+132 for c in cards])
    def ensure_bounds(self):
        state=self.window.state
        for key,container in self.containers.items():
            value=self.data()['canvases'][key]; width,height=value['display_size']; x,y=value['position']
            for member in value['members']:
                card=self.cards[member]; width=max(width,card.width+48); height=max(height,card.height+132)
            value['display_size']=[width,height]; container.refresh()
            for member in value['members']:
                card=self.cards[member]; px=max(x+24,min(card.x(),x+width-card.width-24)); py=max(y+108,min(card.y(),y+height-card.height-24))
                card.setPos(px,py); state.setdefault('text_positions',{})[member]=[px,py]
    def canvas_at(self,position):
        candidates=[c for c in self.containers.values() if c.sceneBoundingRect().contains(position)]
        return max(candidates,key=lambda c:c.zValue()).key if candidates else None
    def palette(self,position=None,target=None):
        self.insertion_canvas=self.canvas_at(position) if isinstance(position,QPointF) else model.owner(self.window.state,target.split(':')[0]) if target else None
        try: return super().palette(position,target)
        finally: self.insertion_canvas=None
    def add_root(self,state,root,module_id=None):
        key=super().add_root(state,root,module_id)
        if self.insertion_canvas: model.assign(state,key,self.insertion_canvas)
        return key
    def append_value(self,value,position=None):
        previous=self.insertion_canvas
        if self.root_id is None and isinstance(position,QPointF): self.insertion_canvas=self.canvas_at(position)
        try: return super().append_value(value,position)
        finally: self.insertion_canvas=previous
    def preview_node_drop(self,card,position):
        super().preview_node_drop(card,position); target=self.canvas_at(position)
        for key,container in self.containers.items(): container.hovered=key==target; container.update()
    def finish_node_drop(self,card,position=None):
        for container in self.containers.values(): container.hovered=False; container.update()
        if self.drop_target: return super().finish_node_drop(card,position)
        if card.parentItem() is not None: return super().finish_node_drop(card,position)
        cid=self.canvas_at(position); key=card.key; positions={i.key:[i.x(),i.y()] for i in self.view.scene().selectedItems() if isinstance(i,NodeCard) and i.parentItem() is None}
        def apply(s):
            for k in positions: model.assign(s,k,cid)
            s.setdefault('text_positions',{}).update(positions)
        QTimer.singleShot(0,lambda:self.commit(apply)); return True
    def extract_node(self,key,position):
        self.insertion_canvas=self.canvas_at(position)
        try: return super().extract_node(key,position)
        finally: self.insertion_canvas=None
    def add_canvas(self,position=None):
        position=position or self.view.mapToScene(self.view.viewport().rect().center()); key=model.ident('canvas_')
        self.commit(lambda s:s['multi_output']['canvases'].update({key:model.new_canvas(model.next_canvas_name(s),(position.x(),position.y()))}))
        return key
    def show_workflow_order(self,position=None):
        from .workflow_flow import ORDER_CARD,workflow_ids
        position=position or self.view.mapToScene(self.view.viewport().rect().center())
        def apply(s):
            order=s['multi_output']['workflow_order']
            order.update(visible=True,items=workflow_ids(s))
            s.setdefault('text_positions',{}).setdefault(ORDER_CARD,[position.x(),position.y()])
        self.commit(apply)

    def add_flow_node(self,kind,position=None):
        from .flow_data import add_scheduler,add_image_input
        from .stage_model import add as add_stage
        position=position or self.view.mapToScene(self.view.viewport().rect().center())
        self.commit(lambda state:({'schedulers':add_scheduler,'image_inputs':add_image_input,'stages':add_stage}[kind])(state,(position.x(),position.y())))

    def migrate_stages(self):
        from .stage_model import migrate_bindings
        self.commit(migrate_bindings)

    def remove_flow_node(self,kind,key):
        if kind=='schedulers':self.window.comfy.input_flow.detach(key)
        def change(state):
            for line in list(state['multi_output']['connections']):
                if key in (line['source'].split('::')[0],line['destination'].split('::')[0]):model.disconnect(state,line['id'])
            state['multi_output'][kind].pop(key,None)
        self.commit(change)

    def order_sources(self,key):
        from .flow_data import text_sources,source_name
        dialog=StudioDialog(self.window);dialog.setWindowTitle('Prompt 控制 · 文字合併順序');dialog.resize(400,380)
        listing=QListWidget();listing.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for cid in text_sources(self.data()['outputs'][key]):
            item=QListWidgetItem(source_name(self.window.state,cid));item.setData(Qt.ItemDataRole.UserRole,cid);listing.addItem(item)
        dialog.body.addWidget(label('拖曳排列；由上到下合併，各文字來源保持獨立。','Subtle',True))
        dialog.body.addWidget(listing);dialog.body.addWidget(dialog_buttons(dialog))
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        ids=[listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(listing.count())]
        def change(state):
            canvases=[i for i in ids if i in state['multi_output']['canvases']]
            state['multi_output']['outputs'][key].update(text_sources=ids,canvases=canvases,canvas=canvases[0] if canvases else None)
        self.commit(change)
    def add_output(self,position=None):
        position=position or self.view.mapToScene(self.view.viewport().rect().center()); key=model.ident('output_')
        def apply(s):
            s['multi_output']['outputs'][key]=model.new_output(model.next_output_name(s)); s.setdefault('text_positions',{})[key]=[position.x(),position.y()]
            if s['multi_output']['current_output'] is None: s['multi_output']['current_output']=key
        self.commit(apply); return key
    def bind_dialog(self,key):
        dialog=ClipBindingDialog(self,key)
        if dialog.exec()==QDialog.DialogCode.Accepted: dialog.apply()
    def add_clip(self,position=None,output=None):
        position=position or self.view.mapToScene(self.view.viewport().rect().center()); created=[]
        def apply(s):
            key=clip_flow.add(s,(position.x(),position.y())); created.append(key)
            if output is not None: model.connect(s,output,key,'clip')
        self.commit(apply); return created[0] if created else None
    def clip_menu(self,key,position):
        menu=RoundMenu(self.window); menu.addAction('綁定工作流文字欄',lambda:self.bind_dialog(key))
        menu.addAction('重新命名',lambda:self.rename_function('clip_inputs',key))
        menu.addAction('移除 CLIP 輸入',lambda:self.commit(lambda s:clip_flow.remove(s,key))); menu.open_at(position)
    def container_menu(self,key,position):
        menu=RoundMenu(self.window); menu.addAction('展開編輯',lambda:self.open_editor(key))
        def rename():
            text,ok=InputDialog.getText(self.window,'畫布名稱','名稱',text=self.data()['canvases'][key]['name'])
            if ok and text.strip(): self.commit(lambda s:s['multi_output']['canvases'][key].update(name=text.strip()))
        menu.addAction('重新命名',rename)
        menu.addAction('新增 Prompt 輸出並連接',lambda:self.create_connected_output(key))
        menu.addAction('嵌入圖片…',lambda:self.import_canvas_image(key))
        menu.addAction('移除畫布，保留文字模組',lambda:self.remove_container(key)); menu.addSeparator(); menu.addAction('復原',self.undo).setEnabled(bool(self.undo_stack)); menu.open_at(position)
    def create_connected_output(self,key):
        container=self.containers[key]; output=self.add_output(QPointF(container.x()+container.width+90,container.y()))
        self.commit(lambda s:model.connect(s,key,output,'text'))
    def output_menu(self,key,position):
        menu=RoundMenu(self.window)
        menu.addAction('重新命名',lambda:self.rename_function('outputs',key))
        menu.addAction('排列文字來源',lambda:self.order_sources(key))
        menu.addAction('新增 CLIP 輸入並連接',lambda:self.add_clip(self.outputs[key].pos()+QPointF(self.outputs[key].width+90,0),key))
        menu.addAction('設為目前輸出',lambda:self.set_current(key)); menu.addAction('移除輸出',lambda:self.remove_output(key)); menu.open_at(position)
    def rename_function(self,collection,key):
        text,ok=InputDialog.getText(self.window,'模組名稱','名稱',text=self.data()[collection][key]['name'])
        if ok and text.strip(): self.commit(lambda s:s['multi_output'][collection][key].update(name=text.strip()))
    def set_current(self,key): model.select_output(self.window.state,key); self.last_state=None; self.sync()
    def remove_container(self,key):
        def apply(s):
            for line in list(s['multi_output']['connections']):
                if line['source']==key or line['destination']==key: model.disconnect(s,line['id'])
            s['multi_output']['canvases'].pop(key)
        self.commit(apply)
    def remove_output(self,key):
        def apply(s):
            for line in list(s['multi_output']['connections']):
                if line['destination']==key or line['source']==key: model.disconnect(s,line['id'])
            s['multi_output']['outputs'].pop(key)
            if s['multi_output']['current_output']==key:
                other=next(iter(s['multi_output']['outputs']),None); s['multi_output']['current_output']=other
                output=s['multi_output']['outputs'].get(other,{}); s.update(draft=output.get('draft'),draft_base=output.get('draft_base',''))
        self.commit(apply)
    def delete_selected(self):
        from .flow_widgets import FlowCard
        items=list(self.view.scene().selectedItems()); keys=[i.key for i in items if isinstance(i,NodeCard)]
        self.remove_keys(keys)
        for item in items:
            if isinstance(item,FlowLine): self.commit(lambda s,k=item.key:model.disconnect(s,k))
            elif isinstance(item,CanvasContainer): self.remove_container(item.key)
            elif isinstance(item,OutputCard): self.remove_output(item.key)
            elif isinstance(item,ClipCard): self.commit(lambda s,k=item.key:clip_flow.remove(s,k))
            elif isinstance(item,FlowCard):self.remove_flow_node(item.kind,item.key)
            elif isinstance(item,TextCard) and (item.key in self.functions.cards or item is self.preview_card): self.functions.remove(item.key)
    def release_output(self):
        self.execution_bar.detach()
    def open_editor(self,key):
        from .composition_editor import CompositionEditor
        if self.editor_page: return
        self.editor_page=CompositionEditor(self,key); self.window.surface_stack.addWidget(self.editor_page); self.window.surface_stack.setCurrentWidget(self.editor_page)
        self.editor_page.animate_open()

    def import_canvas_image(self,key):
        from .local_files import choose_file
        path=choose_file(self.window,'嵌入圖片','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            try: self.embed_image(self.window.generation_panel.import_source(path),key)
            except (ValueError,OSError) as exc: self.window.notice(str(exc))
    def embed_source(self,source,key):
        value=self.functions.data()['images'][source].get('source')
        if value: self.embed_image(value,key)
        else: self.window.notice('請先載入來源圖片。')
    def embed_image(self,source,key):
        from .composition_image import document,layer
        def apply(s):
            value=s['multi_output']['canvases'][key]
            if value.get('image') is None: value['image']=document(source['width'],source['height'])
            doc=value['image']; scale=min(doc['width']/source['width'],doc['height']/source['height'])
            w,h=source['width']*scale,source['height']*scale
            doc['layers'].append(layer('image',(doc['width']-w)/2,(doc['height']-h)/2,w,h,source=copy.deepcopy(source),name=source['name']))
        self.commit(apply)

    def group(self):
        roots=[item.key.split(':')[0] for item in self.view.scene().selectedItems() if isinstance(item,NodeCard)]
        owners={model.owner(self.window.state,key) for key in roots}
        if len(owners)>1: self.window.notice('請選擇同一畫布內的模組進行組合。'); return
        self.insertion_canvas=next(iter(owners),None)
        try: return super().group()
        finally: self.insertion_canvas=None

    def detach(self,key):
        self.insertion_canvas=model.owner(self.window.state,key.split(':')[0])
        try: return super().detach(key)
        finally: self.insertion_canvas=None
