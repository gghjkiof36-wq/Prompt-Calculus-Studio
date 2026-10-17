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
        self.setObjectName('CanvasModuleBody'); body=QVBoxLayout(self); body.setContentsMargins(4,8,4,4);body.setSpacing(10)
        self.source_rows=canvas.data().get('version',0)>=7
        if self.source_rows:
            from .output_sources import OutputSources
            self.order=OutputSources(canvas,key)
        else:
            self.order=OutputOrderList()
            self.order.orderChanged.connect(self.reordered)
            self.order.removeRequested.connect(self.remove_items)
            self.order.itemDoubleClicked.connect(lambda item:self.open_source(item.data(Qt.ItemDataRole.UserRole)))
        self.order.setMinimumHeight(96);self.order.setObjectName('ModuleSourceOrder')
        self.order.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.order.customContextMenuRequested.connect(self.context)
        self.split=QuietSplitter(Qt.Orientation.Vertical); self.split.setChildrenCollapsible(False)
        self.split.setObjectName('PromptSplit')
        self.split.setToolTip('拖曳分隔線，調整組合清單與文字欄的比例。')
        listing=QWidget(); list_layout=QVBoxLayout(listing); list_layout.setContentsMargins(0,0,0,0)
        self.source_count=label('文字來源','Subtle')
        list_layout.addWidget(self.source_count);list_layout.addWidget(self.order)
        text_panel=QWidget();self.text_panel=text_panel; text_layout=QVBoxLayout(text_panel); text_layout.setContentsMargins(0,0,0,0)
        self.draft_status=label('Prompt 預覽','Subtle')
        self.preview_toggle=button('收合預覽',self.toggle_preview,'Quiet')
        self.copy_button=button('',lambda:canvas.window.copy_text(self.editor.toPlainText()),'Quiet')
        self.copy_button.setAccessibleName('複製 Prompt');self.copy_button.setToolTip('複製 Prompt')
        from .ui_icons import icon
        self.copy_button.setIcon(icon('copy'));self.copy_button.setProperty('iconName','copy')
        text_layout.addLayout(row(self.draft_status,None,self.preview_toggle,self.copy_button))
        self.editor=QPlainTextEdit(); self.editor.setObjectName('Prompt'); self.editor.setMinimumSize(260,80)
        self.editor.setMaximumHeight(190)
        self.editor.setPlaceholderText('輸入提示詞，或從左側接入文字。'); self.editor.textChanged.connect(self.edited); text_layout.addWidget(self.editor,1)
        self.split.addWidget(listing); self.split.addWidget(text_panel); body.addWidget(self.split,1)
        self.split.splitterMoved.connect(self.save_ratio); self.loaded_ratio=None
        self.conflicts=label('','ConflictNotice',True); body.addWidget(self.conflicts)
        self.clear=button('清除手動內容',self.clear_draft,'ClearDraft')
        self.clear.setText('還原');self.clear.setToolTip('清除手動內容，使用文字來源重新組合')
        self.save_material=button('存入素材庫',lambda:canvas.window.new_item(self.editor.toPlainText()),'Quiet')
        self.save_material.setIcon(icon('package'));self.refresh_visual_theme()
        text_layout.itemAt(0).layout().insertWidget(1,self.clear)
        body.addWidget(self.save_material)

    def refresh_visual_theme(self):
        from .module_chrome import style_action
        from .canvas_items import canvas_colors
        style_action(self.save_material,canvas_colors(self.canvas))
        if self.source_rows:
            self.order.refresh(self.canvas.window.state,canvas_colors(self.canvas))

    def toggle_preview(self):
        output=self.canvas.data()['outputs'][self.key]
        output['preview_collapsed']=not output.get('preview_collapsed',False)
        self.apply_preview_visibility();self.canvas.window.changed('layout')

    def apply_preview_visibility(self):
        collapsed=self.canvas.data()['outputs'][self.key].get('preview_collapsed',False)
        self.editor.setVisible(not collapsed);self.preview_toggle.setText('展開預覽' if collapsed else '收合預覽')
        self.text_panel.setMaximumHeight(self.preview_toggle.sizeHint().height()+8 if collapsed else 240)
        self.preview_toggle.setAccessibleName(self.preview_toggle.text())
        card=self.canvas.outputs.get(self.key)
        if card:card.layout_card()

    def more_actions(self):
        menu=RoundMenu(self)
        menu.addAction('存入素材庫',lambda:self.canvas.window.new_item(self.editor.toPlainText()))
        clear=menu.add_destructive_action('清除手動內容',self.clear_draft);clear.setEnabled(self.clear.isEnabled())
        menu.open_for(self.save_material,self.canvas.view)
    def save_ratio(self,*_):
        sizes=self.split.sizes()
        if sum(sizes):
            self.loaded_ratio=sizes[0]/sum(sizes)
            self.canvas.data()['outputs'][self.key]['panel_ratio']=self.loaded_ratio; self.canvas.window.changed('layout')
    def restore_ratio(self):
        ratio=self.canvas.data()['outputs'][self.key].get('panel_ratio',.62)
        if ratio!=self.loaded_ratio:
            self.split.setSizes([round(ratio*1000),round((1-ratio)*1000)]); self.loaded_ratio=ratio
    def context(self,pos):
        item=self.order.itemAt(pos)
        if item:
            self.order.setCurrentItem(item)
            if self.source_rows:
                token=self.order.token(item)
                if token[0]=='source':
                    menu=RoundMenu(self)
                    if item.childCount():menu.addAction('收合' if item.isExpanded() else '展開',lambda:item.setExpanded(not item.isExpanded()))
                    menu.addAction('開啟文字來源',lambda:self.open_source(token[1]))
                    menu.open_at(widget_global_position(self.order.viewport(),pos,self.canvas.view));return
                key=token[2]
            else:key=item.data(Qt.ItemDataRole.UserRole)
            self.canvas.view.scene().clearSelection()
            if key in self.canvas.cards:self.canvas.cards[key].setSelected(True)
            self.canvas.context(key,widget_global_position(self.order.viewport(),pos,self.canvas.view),[key])
    def open_source(self,key):
        if not self.source_rows:self.canvas.focus_node(key);return
        parent=key.split('::')[0]
        groups=(self.canvas.containers,self.canvas.outputs,self.canvas.flow_cards,self.canvas.functions.cards)
        card=next((group[parent] for group in groups if parent in group),None)
        if card:
            self.canvas.view.scene().clearSelection();card.setSelected(True);self.canvas.view.centerOn(card)
    def remove_items(self,keys):
        if self.source_rows:return
        if self.updating or not self.order.isEnabled():return
        from .flow_data import canvas_ids
        members=[k for cid in canvas_ids(self.canvas.data()['outputs'][self.key]) for k in self.canvas.data()['canvases'][cid]['members']]
        self.canvas.remove_keys([key for key in keys if key in members])
    def reordered(self,*args):
        if self.updating or self.source_rows: return
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
        self.canvas.commit(lambda state:edit(state,self.editor.toPlainText(),self.key),
                           label='修改手動 Prompt',merge_key='output:'+self.key)
    def clear_draft(self):
        from .drafts import clear
        self.canvas.commit(lambda s:clear(s,self.key))
    def refresh(self):
        self.updating=True; state=self.canvas.window.state; output=self.canvas.data()['outputs'][self.key]
        try: compiled=model.compile_output(state,self.key); text=compiled['final_prompt']; affected=compiled['affected']
        except ValueError: text=output['draft'] or ''; affected={}
        if self.editor.toPlainText()!=text: self.editor.setPlainText(text)
        from .flow_data import canvas_ids,text_sources
        cid=output['canvas']; members=text_sources(output) if self.source_rows else [k for c in canvas_ids(output) for k in self.canvas.data()['canvases'][c]['members']]
        if self.source_rows:
            from .canvas_items import canvas_colors
            self.order.refresh(state,canvas_colors(self.canvas))
        else:
            prior=[self.order.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.order.count())]
            if prior!=members:
                self.order.clear()
                for key in members:
                    item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,key);self.order.addItem(item)
            for i,key in enumerate(members):
                root=state['uses'][key];item=self.order.item(i)
                item.setText(root['name']+(f" · ×{root['weight']/10:.1f}" if root['weight']!=10 else '')+(' · 停用' if not root['enabled'] else ''))
                item.setToolTip(comp.render(root))
        manual=output['draft'] is not None
        self.source_count.setText('文字來源'+(' · '+str(len(members)) if members else ''))
        self.order.setEnabled(self.source_rows or not manual); self.clear.setEnabled(manual);self.clear.setVisible(manual)
        self.draft_status.setText('Prompt 預覽')
        self.draft_status.setVisible(bool(self.draft_status.text()))
        self.conflicts.setText('；'.join(', '.join(v['tags'])+' ← '+', '.join(v['by']) for v in affected.values())); self.conflicts.setVisible(bool(affected))
        self.apply_preview_visibility()
        self.updating=False


class OutputCard(TextCard):
    default_size=(440,410)
    def __init__(self,canvas,key):
        super().__init__(canvas); self.key=key; self.module_icon='list-ordered'; self.panel=OutputPanel(canvas,key); self.init_interaction()
    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.sync_panel_style(); self.panel.ensurePolished()
        self.restore_size(); self.layout_card(); self.panel.show(); self.panel.restore_ratio(); self.panel.refresh()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        from .flow_data import text_sources
        top=max(102,90+24*len(text_sources(self.canvas.data()['outputs'][self.key])))
        self.proxy.setGeometry(QRectF(14,top,max(350,minimum.width(),width-28),max(210,minimum.height(),height-top-14))); self.sync_bounds()
    def update_text(self):
        self.title=self.canvas.data()['outputs'][self.key]['name']; self.panel.refresh(); super().update_text()
    def open_menu(self,position): self.canvas.output_menu(self.key,position)
    def detach(self): pass


class FlowFunctions(CanvasFunctions):
    def outline(self,card,path): return path
    def layout(self,skip=None): self.canvas.update_lines()
    def moving(self,card,position): self.canvas.update_lines()
    def dropped(self,card,position): return False
    def mode_changed(self): pass
    def set_attachment(self,state,key,attached):
        from .preview_nodes import contains
        if contains(state,key): return
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
        available={**data['images'], **data.get('text_sources', {})}
        for key in list(self.cards):
            if key not in available:
                card=self.cards.pop(key); self.canvas.view.scene().removeItem(card); card.deleteLater()
        for index,key in enumerate(data['images']):
            if key not in self.cards: self.cards[key]=SourceCard(self,key); self.canvas.view.scene().addItem(self.cards[key])
            card=self.cards[key]; card.panel.attach_button.hide()
            card.attach(); card.setPos(*self.window.state.get('text_positions',{}).get(key,[900,850+index*390]))
        if self.canvas.preview_card: self.canvas.preview_card.setVisible(data.get('preview',True))
        from .prompt_source_widgets import GroupSourceCard
        for index, key in enumerate(data.get('text_sources', {})):
            if key not in self.cards:
                self.cards[key]=GroupSourceCard(self,key); self.canvas.view.scene().addItem(self.cards[key])
            card=self.cards[key]; card.attach()
            card.setPos(*self.window.state.get('text_positions',{}).get(key,[450,850+index*300]))
    def remove(self,key):
        from .canvas_operations import remove
        self.canvas.commit(lambda s:remove(s,[key]))
    def show_preview(self,position=None):
        from .preview_nodes import add
        position=position or self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
        result=[]
        if self.canvas.commit(lambda state:result.append(add(state,(position.x(),position.y())))):
            return result[0]
    def source_position(self,output):
        card=self.canvas.outputs.get(output)
        rect=card.sceneBoundingRect() if card else QRectF(*self.window.state.get('text_positions',{}).get(output,[650,100]),540,650)
        width,height,gap=360,340,36
        candidates=[QRectF(rect.left()-width-gap,rect.top(),width,height),QRectF(rect.left(),rect.bottom()+gap,width,height),
                    QRectF(rect.right()+gap,rect.top(),width,height),QRectF(rect.left(),rect.top()-height-gap,width,height)]
        others=[c.sceneBoundingRect() for c in list(self.canvas.containers.values())+list(self.canvas.outputs.values())+list(self.cards.values())
                +list(self.canvas.clips.values())+list(self.canvas.preview_cards.values())]
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
        from .function_picker import FUNCTIONS
        actions=self.insertion_callbacks(position)
        return [(title,actions[key]) for key,title,*_ in FUNCTIONS]+[('依現有綁定補建 Stage',self.canvas.migrate_stages)]
    def insertion_callbacks(self,position=None):
        return dict(groups=lambda:self.add_group_source(position),canvas=lambda:self.canvas.add_canvas(position),output=lambda:self.canvas.add_output(position),
                    clip=lambda:self.canvas.add_clip(position),reader=lambda:self.add_text_reader(position),
                    source=lambda:self.add_image(position=position),single=lambda:self.add_image(position=position,enhanced=False),
                    **{'image-input':lambda:self.canvas.add_flow_node('image_inputs',position)},
                    schedule=lambda:self.canvas.add_flow_node('schedulers',position),
                    stage=lambda:self.canvas.add_flow_node('stages',position),preview=lambda:self.show_preview(position))
    def add_group_source(self,position=None):
        from .prompt_sources import add
        position=position or self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
        return self.canvas.commit(lambda state:add(state,(position.x(),position.y())))
    def add_text_reader(self,position=None):
        from .result_data import add_text_reader
        position=position or self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())
        self.canvas.commit(lambda s:add_text_reader(s,(position.x(),position.y())))
    def context(self,key,position):
        menu=RoundMenu(self.window)
        menu.addAction('調整模組尺寸…',lambda:self.canvas.size_dialog(key))
        menu.addAction('恢復自動尺寸',lambda:self.canvas.reset_size(key))
        if key in self.cards:
            if hasattr(self.cards[key].panel,'choose_file'):menu.addAction('選擇圖片…',self.cards[key].panel.choose_file)
            if self.data()['images'][key].get('reader')!='text':
                embed=menu.addMenu('嵌入畫布')
                for cid,value in self.canvas.data()['canvases'].items(): embed.addAction(value['name'],lambda checked=False,c=cid:self.canvas.embed_source(key,c))
        menu.add_destructive_action('移除',lambda:self.remove(key)); menu.open_at(position)


class MultiCanvas(TextCanvas):
    def __init__(self,window):
        super().__init__(window); self.functions=FlowFunctions(self); self.containers={}; self.outputs={}; self.ports={}; self.lines={}
        self.clips={}; self.flow_cards={}; self.preview_cards={}; self.order_card=None; self.execution_bar=ExecutionBar(self)
        self.insertion_canvas=None; self.image_previews={}; self.editor_page=None; self.z_counter=-.9; self.refreshing=False
        from .flow_items import ConnectionGesture
        self.connection_gesture=ConnectionGesture(self)
        self.output_picker=ComboBox(); self.output_picker.currentIndexChanged.connect(self.choose_current)
        self.output_picker.setToolTip('選項隔離開啟時，每份輸出分別保存清單內容；切回畫布模式使用各畫布內容。')
        window.builder_panel.layout().insertWidget(0,self.output_picker)
        from .canvas_onboarding import CanvasOnboarding,CanvasZoomControls
        self.onboarding=CanvasOnboarding(self);self.layout().insertWidget(0,self.onboarding)
        self.zoom_controls=CanvasZoomControls(self)
        from .canvas_starter import guide
        self._starter_view_pending=bool(guide(window.state) and not window.state.get('canvas_view'))
    def restore_view(self):
        # Mode changes can save the empty GraphicsView before its first show.
        # Apply the new-document starting view only after real cards and final
        # viewport geometry exist; an early placeholder center is not a choice.
        if self._starter_view_pending:
            if not self.isVisible():return
            self.layout().activate()
            if not self.containers:self.refresh()
            if self.onboarding.first_view():
                self._starter_view_pending=False
                self._hidden_view=None
                self.window.state.pop('canvas_view',None)
                return
        if self.restore_hidden_view():return
        if self.window.state.get('canvas_view') or not self.onboarding.first_view():
            super().restore_view()
    def data(self): return self.window.state['multi_output']
    def next_z(self): self.z_counter+=.0001; return min(-.2,self.z_counter)
    def history_state(self):
        return self.history_projection(self.window.state)
    def commit(self,operation,label='編輯',merge_key=None):
        from .material_navigation import navigation_only
        library_only = False
        old_connections=copy.deepcopy(self.data()['connections'])
        def apply(s):
            nonlocal library_only
            operation(s)
            library_only = navigation_only(self.window.state, s)
            if library_only: return
            client=getattr(self.window,'comfy',None)
            if s['multi_output']['version']>=7 and client:
                from .stage_context import sync_result_view
                sync_result_view(s,self.window.store.directory,client.input_flow.chain.results)
            elif s['multi_output']['version']>=5:
                from .flow_data import materialize
                materialize(s)
            model.reconcile(s)
        result=super().commit(apply,label=label,merge_key=merge_key)
        client=getattr(self.window,'comfy',None)
        if result and client and not library_only and self.data()['version']>=7:
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
        from .queue_history import restore as restore_queue
        if restore_queue(self,source,destination,after):return
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
            value.update(merged)
            from .content_history import record
            record(value,self.history_projection(previous),self.history_projection(value),label='重做' if after else '復原')
            validate_state(value)
        except ValueError as exc:self.window.notice(str(exc));return
        old_images=self.window.state.get('canvas_functions',{}).get('images',{})
        new_images=value.get('canvas_functions',{}).get('images',{})
        reader_changed=any(old_images.get(key,{}).get(field)!=new_images.get(key,{}).get(field)
            for key in old_images.keys()|new_images.keys() for field in ('output_node','text_field','input_index','stage_reference','prompt_choice'))
        source.pop(); destination.append((before,later)); self.window.state=value; self.last_state=self.history_state()
        self._last_edit=None
        from .changes import layout_only
        from .material_navigation import navigation_only
        library_only = navigation_only(expected, target)
        self.sync('library' if library_only else 'layout' if layout_only(expected,target) else 'prompt')
        if reader_changed and self.data()['version']>=7:self.window.comfy.input_flow.chain.refresh_results()
        if not library_only and self.data()['version']>=7:self.window.comfy.input_flow.chain.route_changed()
        if canvas_focus:self.view.setFocus();self.view.scene().clearFocus()

    def history_projection(self,state):
        from .workspace_scene import history_projection
        return history_projection(state,HISTORY_FIELDS+('multi_output','draft','draft_base'))
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
                (self.preview_cards,{model.PREVIEW,*state.get('canvas_functions',{}).get('previews',{})}),
                (self.functions.cards,{**state.get('canvas_functions',{}).get('images',{}), **state.get('canvas_functions',{}).get('text_sources',{})})):
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
        from .canvas_exclusions import affected_nodes
        self.affected_nodes=affected_nodes(self.window.state)
        # Runtime supply cursors advance without an edit/undo transaction.
        # NodeCard retains a presentation copy, so repainting alone leaves both
        # its round number and rendered text at the previous selection.
        for key,node in self.cards.items():
            ident,node_id=self.entries.get(key,(None,None))
            root=self.window.state.get('uses',{}).get(ident)
            value=comp.find(root,node_id) if root else None
            if value is not None and node.value!=value:
                node.value=copy.deepcopy(value);node.own=comp.render(value);node.layout_card()
            else:node.update()
        if not hasattr(self,'outputs'): return
        if hasattr(self,'onboarding'):self.onboarding.refresh()
        self.prune_cards()
        model.capture_current(self.window.state)
        self.output_picker.blockSignals(True); self.output_picker.clear()
        for key,o in self.data()['outputs'].items(): self.output_picker.addItem('目前輸出 · '+o['name'],key)
        self.output_picker.setCurrentIndex(self.output_picker.findData(self.data()['current_output'])); self.output_picker.blockSignals(False)
        for card in self.outputs.values(): card.update_text()
        for card in self.clips.values(): card.update_text()
        for card in self.flow_cards.values():card.update_text()
        for card in self.functions.cards.values():card.update_text()
        if self.order_card: self.order_card.update_text()
        for card in self.preview_cards.values():card.update_text()
        preview_links=tuple(sorted((c['destination'],c['source'],c['kind']) for c in self.data()['connections'] if c['destination'] in self.preview_cards))
        if self.preview_card and getattr(self,'preview_links',None)!=preview_links:
            self.preview_links=preview_links; self.results.refresh()
        self.update_lines()
        if self.editor_page and not self.editor_page.refreshing: self.editor_page.refresh()
    def refresh_layout(self):
        self.prune_cards()
        if self.isVisible():super().refresh_layout()
        for container in self.containers.values():container.refresh()
        for card in [*self.outputs.values(),*self.clips.values(),*self.flow_cards.values(),*self.functions.cards.values(),*self.preview_cards.values(),self.order_card]:
            if card is not None:card.sync_bounds()
        self.update_lines()
    def refresh(self):
        if not hasattr(self,'outputs') or self.refreshing: return
        if hasattr(self,'onboarding'):self.onboarding.refresh()
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
            from .preview_nodes import keys as preview_keys
            from .canvas_results import CanvasResults
            active_previews=preview_keys(state)
            for key in set(self.preview_cards)-set(active_previews):
                card=self.preview_cards.pop(key);self.view.scene().removeItem(card);card.deleteLater()
            for index,key in enumerate(active_previews):
                if key not in self.preview_cards:
                    panel=self.results if key==model.PREVIEW else CanvasResults(self.window,key)
                    self.preview_cards[key]=PreviewCard(self,key,panel);self.view.scene().addItem(self.preview_cards[key])
                card=self.preview_cards[key];card.attach();card.setSelected(key in selected)
                card.setPos(*state.get('text_positions',{}).get(key,[600+index*540,450]))
            self.preview_card=self.preview_cards[model.PREVIEW];self.results.refresh()
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
                if key in state['canvas_functions'].get('text_sources',{}):
                    self.add_port(card,key,'content',True);self.add_port(card,key,'clip',True,index=1);continue
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
                    from .module_contracts import scheduler_port_specs
                    for endpoint,kind,output,slot,index in scheduler_port_specs(state,key):
                        self.add_port(card,endpoint,kind,output,slot=slot,index=index)
            if self.data()['version']>=4:
                for key,card in self.preview_cards.items():self.add_port(card,key,'image',False)
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
        from .module_contracts import binding_scheduler
        multiple=kind in ('execution','preview','text') or kind=='control' and (key in self.data().get('stages',{}) or binding_scheduler(self.window.state,key)) or kind=='flow' and key.endswith('::flow') or kind=='clip' and key in self.outputs
        return self.ports.get((key,kind,output,value['source']) if not output and multiple else (key,kind,output))
    def update_lines(self):
        if not hasattr(self,'ports'): return
        for port in self.ports.values():
            parent=port.parentItem(); y=58+port.index*24 if port.slot else 82 if port.key in self.outputs or port.key in self.containers else 58
            if not port.slot and port.kind in ('text','execution','clip'): y=58
            if self.data()['version']>=5:y=58+port.index*24
            if port.key in self.data().get('stages',{}) or port.key in self.clips:y+=24
            port.setPos(parent.width if port.output else 0,y)
        for card in [*self.outputs.values(),*self.clips.values(),*self.flow_cards.values(),*self.functions.cards.values(),*self.preview_cards.values(),self.order_card]:
            if card is not None and card.collapsed:
                card.prepareGeometryChange();card.apply_fold();card.update()
        for line in self.lines.values():
            line.setVisible(not self.window.state['settings'].get('hide_connections',False))
            value=line.value; source=self.line_port(value,True); dest=self.line_port(value,False)
            if source and dest: line.setPath(curve(source.scenePos(),dest.scenePos(),self.window.state['settings'].get('connection_style','curve')))
    def minimum_canvas(self,key):
        cards=[self.cards[k] for k in self.data()['canvases'][key]['members'] if k in self.cards]
        return max([360]+[c.width+48 for c in cards]),max([230]+[c.height+186 for c in cards])
    def ensure_bounds(self):
        state=self.window.state
        for key,container in self.containers.items():
            value=self.data()['canvases'][key]; width,height=value['display_size']; x,y=value['position']
            for member in value['members']:
                # Reserve the port area above and the editor button plus its
                # gap below. Taller text/group controls must not cover it.
                card=self.cards[member]; width=max(width,card.width+48); height=max(height,card.height+186)
            value['display_size']=[width,height]; container.refresh()
            for member in value['members']:
                card=self.cards[member]; px=max(x+24,min(card.x(),x+width-card.width-24)); py=max(y+108,min(card.y(),y+height-card.height-78))
                card.setPos(px,py); state.setdefault('text_positions',{})[member]=[px,py]
    def canvas_at(self,position):
        candidates=[c for c in self.containers.values() if c.sceneBoundingRect().contains(position)]
        return max(candidates,key=lambda c:c.zValue()).key if candidates else None
    def palette(self,position=None,target=None):
        self.insertion_canvas=self.canvas_at(position) if isinstance(position,QPointF) else model.owner(self.window.state,target.split(':')[0]) if target else None
        try: return super().palette(position,target)
        finally: self.insertion_canvas=None

    def function_palette(self,position=None):
        from .function_picker import FunctionPicker
        return FunctionPicker(self,position).exec()

    def quick_text(self,key,position):
        previous=(self.root_id,list(self.path),self.insertion_canvas)
        self.root_id=None;self.path=[];self.insertion_canvas=key
        try:return TextCanvas.palette(self,position)
        finally:self.root_id,self.path,self.insertion_canvas=previous

    def toggle_collapsed(self,key):
        self.commit(lambda s:self.selected_node(s,key)[2].update(collapsed=not self.selected_node(s,key)[2].get('collapsed',False)),label='收合／展開文字')

    def selection_keys(self):
        return [item.key for item in self.view.scene().selectedItems() if hasattr(item,'key') and not isinstance(item,Port)]

    def copy_selected(self):
        from .canvas_operations import capture,MIME
        from PySide6.QtCore import QMimeData
        from PySide6.QtWidgets import QApplication
        import json
        payload=capture(self.window.state,self.selection_keys())
        if not payload['positions']:return
        mime=QMimeData();mime.setData(MIME,json.dumps(payload,ensure_ascii=False).encode('utf-8'));QApplication.clipboard().setMimeData(mime)
        from .content_history import record_action
        owners=set(payload['collections'].get('canvases',{}))
        owners.update(model.owner(self.window.state,key.split(':')[0]) for key in self.selection_keys())
        record_action(self.window.state,'複製模組',canvas_ids=owners-{None})
        self.window.changed('settings',refresh=False)
        self.window.notice('已複製所選模組與內部接線。')

    def paste_selected(self,position=None):
        from .canvas_operations import paste,MIME
        from PySide6.QtWidgets import QApplication
        import json
        mime=QApplication.clipboard().mimeData()
        if not mime.hasFormat(MIME):return
        position=position or getattr(self.view,'pointer_scene',None) or self.view.mapToScene(self.view.viewport().rect().center())
        try:
            raw=bytes(mime.data(MIME))
            if len(raw)>8_000_000:raise ValueError('剪貼簿模組超過大小限制。')
            payload=json.loads(raw);added=[]
            if not self.commit(lambda s:added.extend(paste(s,payload,(position.x(),position.y()),self.canvas_at(position))),label='貼上模組'):return
        except (ValueError,TypeError,KeyError) as exc:self.window.notice(str(exc));return
        self.view.scene().clearSelection()
        for item in self.view.scene().items():
            if getattr(item,'key',None) in added and item.flags()&QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:item.setSelected(True)

    def move_selection(self,positions):
        nested={key:self.cards[key] for key in positions if key in self.cards and self.cards[key].parentItem() is not None}
        extracted={key for key,item in nested.items() if not item.parentItem().sceneBoundingRect().contains(QPointF(*positions[key]))}
        def apply(s):
            for key,pos in positions.items():
                if key in nested:
                    if key not in extracted:continue
                    from .core import uid
                    ident,root,part=self.selected_node(s,key)
                    comp.container(root,part['id']).remove(part)
                    new=uid();s['uses'][new]=part
                    target=self.canvas_at(QPointF(*pos))
                    model.assign(s,new,target)
                    s.setdefault('text_positions',{})[new]=list(pos)
                    for child in comp.walk(part):
                        old=ident+':'+child['id'];replacement=new if child is part else new+':'+child['id']
                        if old in s.get('text_sizes',{}):s['text_sizes'][replacement]=s['text_sizes'].pop(old)
                    continue
                if key in s['multi_output']['canvases']:s['multi_output']['canvases'][key]['position']=list(pos)
                else:s.setdefault('text_positions',{})[key]=list(pos)
        result=self.commit(apply,label='移動模組')
        if not result:self.refresh_layout()
        return result
        # Nested child layout is structured; a move inside its parent snaps back,
        # just like a single-card drag. Outside moves detach once in this transaction.
        for key in set(nested)-extracted:
            item=self.cards.get(key)
            if item is not None and item.parentItem():item.parentItem().layout_card()
    def add_root(self,state,root,module_id=None):
        key=super().add_root(state,root,module_id)
        if self.insertion_canvas: model.assign(state,key,self.insertion_canvas)
        return key
    def append_value(self,value,position=None):
        previous=self.insertion_canvas
        if self.root_id is None and isinstance(position,QPointF) and not getattr(self,'_fixed_insertion_canvas',False): self.insertion_canvas=self.canvas_at(position)
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
        from .source_order_dialog import SourceOrderDialog
        dialog=SourceOrderDialog(self,key)
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        ids=dialog.ordered_ids()
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
        menu.addAction('調整模組尺寸…',lambda:self.size_dialog(key));menu.addAction('恢復自動尺寸',lambda:self.reset_size(key))
        menu.addAction('重新命名',lambda:self.rename_function('clip_inputs',key))
        menu.add_destructive_action('移除 CLIP 輸入',lambda:self.commit(lambda s:clip_flow.remove(s,key))); menu.open_at(position)
    def container_menu(self,key,position):
        menu=RoundMenu(self.window); menu.addAction('展開畫布「'+self.data()['canvases'][key]['name']+'」',lambda:self.open_editor(key))
        menu.addAction('調整模組尺寸…',lambda:self.size_dialog(key));menu.addAction('恢復自動尺寸',lambda:self.reset_size(key))
        menu.addAction('加入素材',lambda:self.window.show_materials(key))
        menu.addAction('畫布歷史',lambda:self.window.show_content_history(key))
        def rename():
            text,ok=InputDialog.getText(self.window,'畫布名稱','名稱',text=self.data()['canvases'][key]['name'])
            if ok and text.strip(): self.commit(lambda s:s['multi_output']['canvases'][key].update(name=text.strip()))
        menu.addAction('重新命名',rename)
        menu.addAction('新增 Prompt 輸出並連接',lambda:self.create_connected_output(key))
        menu.addAction('嵌入圖片…',lambda:self.import_canvas_image(key))
        menu.add_destructive_action('移除畫布，保留素材',lambda:self.remove_container(key)); menu.addSeparator(); menu.addAction('復原',self.undo).setEnabled(bool(self.undo_stack)); menu.open_at(position)
    def create_connected_output(self,key):
        container=self.containers[key]; output=self.add_output(QPointF(container.x()+container.width+90,container.y()))
        self.commit(lambda s:model.connect(s,key,output,'text'))
    def output_menu(self,key,position):
        menu=RoundMenu(self.window)
        menu.addAction('調整模組尺寸…',lambda:self.size_dialog(key));menu.addAction('恢復自動尺寸',lambda:self.reset_size(key))
        menu.addAction('重新命名',lambda:self.rename_function('outputs',key))
        if self.data().get('version',0)<7:menu.addAction('排列文字來源',lambda:self.order_sources(key))
        menu.addAction('新增 CLIP 輸入並連接',lambda:self.add_clip(self.outputs[key].pos()+QPointF(self.outputs[key].width+90,0),key))
        menu.addAction('設為目前輸出',lambda:self.set_current(key)); menu.add_destructive_action('移除輸出',lambda:self.remove_output(key)); menu.open_at(position)
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
        from .canvas_operations import remove
        keys=self.selection_keys()
        if not keys:return
        schedulers=[key for key in keys if key in self.data().get('schedulers',{})]
        if self.commit(lambda s:remove(s,keys),label='刪除模組'):
            for key in schedulers:self.window.comfy.input_flow.detach(key)
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
