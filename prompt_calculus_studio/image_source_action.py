"""One cancellable, undoable v7 image reuse action for all result surfaces."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt,QPointF,QRectF,QSizeF
from PySide6.QtWidgets import QDialog,QDialogButtonBox,QListWidget,QListWidgetItem
from .widgets import StudioDialog,dialog_buttons
from .workflow_binding import WorkflowBindingDialog
from .image_input_routing import candidates,valid_image_binding

ACTION='用這張圖當來源'


class ImageInputChoice(StudioDialog):
    def __init__(self,canvas,rows):
        super().__init__(canvas.window);self.setWindowTitle(ACTION);self.resize(440,330)
        rows=[r for r in rows if r['valid']]
        self.inputs=QListWidget();self.body.addWidget(self.inputs,1)
        self.inputs.setProperty('moduleChoices',True)
        from .module_chrome import dialog_context,dialog_colors
        colors=dialog_colors(dialog_context(self)[0])
        self.inputs.setStyleSheet('QListWidget::item:disabled {color:'+colors['disabled_text']+';background:transparent;}')
        for row in rows:
            status='已有來源' if row['occupied'] else '未接來源'
            item=QListWidgetItem('圖片輸入 '+str(row['number'])+' · '+status,self.inputs)
            item.setData(Qt.ItemDataRole.UserRole,row['key'])
            if not row['available']:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        self.buttons=dialog_buttons(self,self.accept);self.body.addWidget(self.buttons)
        self.submit=self.buttons.button(QDialogButtonBox.StandardButton.Save);self.submit.setText('接到圖片輸入');self.submit.setEnabled(False)
        self.inputs.currentItemChanged.connect(lambda item,old:self.submit.setEnabled(bool(item and item.flags() & Qt.ItemFlag.ItemIsEnabled)))
        self.inputs.setCurrentRow(next((i for i,row in enumerate(rows) if row['available']),-1))
        self.inputs.setFocus()

    def selected(self):
        item=self.inputs.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item and item.flags() & Qt.ItemFlag.ItemIsEnabled else None

    def accept(self):
        if self.selected() is not None:super().accept()


class NewImageInputDialog(WorkflowBindingDialog):
    manual_choice=True
    empty_target_allowed=False
    empty_choice='選擇工作流'

    def __init__(self,canvas):
        self.image_binding=None
        super().__init__(canvas,'__new_image_input__')
        self.setWindowTitle('選工作流與載入圖片節點');self.nodes_label.setText('載入圖片節點')
        self.save_button.setText('建立並接上')

    def requires_workflow(self):return True

    def refresh_targets(self):
        self.target.clear();self.target.addItem('選擇節點',None);profile=self.profile()
        for key,node in (profile or {}).get('graph',{}).items():
            if not valid_image_binding(profile,key):continue
            occupied=any(v.get('workflow')==profile['id'] and v.get('node')==key for v in self.canvas.data()['image_inputs'].values())
            self.target.addItem(node.get('_meta',{}).get('title','LoadImage')+' · #'+key+(' · 已綁定' if occupied else ''),key)
            if occupied:self.target.model().item(self.target.count()-1).setEnabled(False)
        self.target.setEnabled(profile is not None)
        self.hint.clear();self.hint.hide()


def _new_card_bounds(canvas,source,profile=None,node=None):
    """Measure the real controls in an isolated preview, not the live document."""
    from .module_preview import ModulePreview
    preview=ModulePreview(canvas)
    try:
        preview.show_kind('single')
        preview.context.functions.data()['images'][preview.card.key]['source']=copy.deepcopy(source)
        preview.card.attach()
        source_rect=QRectF(preview.card.boundingRect())
        target_rect=None
        if profile is not None:
            preview.show_kind('image-input')
            context=preview.context;card=preview.card
            context.window.state['generation']['profiles']=[copy.deepcopy(profile)]
            context.data()['image_inputs'][card.key].update(workflow=profile['id'],node=node)
            context.window.state['text_sizes'].pop(card.key,None)
            card.attach();card.panel.layout().activate();card.layout_card()
            target_rect=QRectF(card.boundingRect())
        return source_rect,target_rect
    finally:
        preview.clear_preview();preview.deleteLater()


def _image_positions(canvas,source,target=None,profile=None,node=None):
    # A result can be reused while Canvas is hidden (Recent / image preview).
    # Populate its current geometry without showing it or editing the document.
    if (getattr(canvas,'_scene_refresh_pending',True)
            or getattr(canvas,'_visible_scene_state',None)!=canvas.history_state()):
        canvas.refresh(prepare_hidden=True)
    occupied=[]
    for collection in (canvas.cards,canvas.containers,canvas.outputs,canvas.clips,
                       canvas.flow_cards,canvas.preview_cards,canvas.functions.cards):
        occupied.extend(card.sceneBoundingRect() for card in collection.values() if card.parentItem() is None)
    if canvas.order_card is not None:occupied.append(canvas.order_card.sceneBoundingRect())
    source_rect,input_rect=_new_card_bounds(canvas,source,profile,node)
    gap=36
    if target is not None:
        anchor=canvas.flow_cards[target].sceneBoundingRect()
        point=anchor.topLeft()-QPointF(source_rect.width()+gap,0)
        size=source_rect.size()
    else:
        point=canvas.view.mapToScene(canvas.view.viewport().rect().center())
        # Reserve both new cards together, including their connecting gap.
        size=QSizeF(source_rect.width()+gap+input_rect.width(),max(source_rect.height(),input_rect.height()))

    def free(candidate):
        rect=QRectF(candidate,size).adjusted(-8,-8,8,8)
        return not any(rect.intersects(other) for other in occupied)

    def find():
        # Same 32-unit expanding rings and 8-unit clearance as material insertion
        # (WorkspaceMaterials.position); prefer the target's left side first.
        if free(point):return point
        for radius in range(1,81):
            offsets=[offset for x in range(-radius,radius+1) for offset in ((x,-radius),(x,radius))]
            offsets.extend(offset for y in range(1-radius,radius) for offset in ((-radius,y),(radius,y)))
            for dx,dy in offsets:
                candidate=point+QPointF(dx*32,dy*32)
                if free(candidate):return candidate
        # Oversized canvas cards can cover all nearby rings. The outer right
        # edge is always free; never fall back to a partially overlapping slot.
        return QPointF(max(r.right() for r in occupied)+gap,point.y())

    position=find()
    return (position-source_rect.topLeft(),
            position+QPointF(source_rect.width()+gap,0)-input_rect.topLeft() if input_rect is not None else None)


def use_image(canvas, value, output=None):
    """Choose first; import and commit only after an explicit/unique target."""
    window=canvas.window;owner=(window.store,window.state['workspace'])
    rows=copy.deepcopy(candidates(window.state,output));available=[r for r in rows if r['available']]
    selection=None;new_profile=None;new_node=None;dialog=None
    if len(available)==1:
        selection=available[0]
    elif available:
        dialog=ImageInputChoice(canvas,rows)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted:return None
            selection=next(r for r in available if r['key']==dialog.selected())
        finally:dialog.deleteLater()
    else:
        dialog=NewImageInputDialog(canvas)
        try:
            if dialog.exec()!=QDialog.DialogCode.Accepted:return None
            new_profile=copy.deepcopy(dialog.binding_profile());new_node=dialog.target.currentData()
            if not valid_image_binding(new_profile,new_node):raise ValueError('圖片綁定失效。')
        finally:dialog.deleteLater()
    if owner!=(window.store,window.state['workspace']):raise ValueError('工作區已改變。')
    def check(state):
        if selection:
            current=next((r for r in candidates(state,output) if r['key']==selection['key']),None)
            if not current or not current['available'] or any(current[k]!=selection[k] for k in ('workflow','node','profile')):
                raise ValueError('圖片輸入已變更。')
        elif any(v.get('workflow')==new_profile['id'] and v.get('node')==new_node for v in state['multi_output']['image_inputs'].values()):
            raise ValueError('圖片節點已綁定。')
    check(window.state)
    from .image_bindings import import_source
    from .generation import validate_source,store_profile
    source=import_source(value,window.store.directory) if isinstance(value,(str,Path)) else copy.deepcopy(value)
    validate_source(source)
    if source is None:raise ValueError('沒有來源圖片。')
    from .core import uid
    from .flow_data import add_image_input
    from .multi_output import connect
    key='__source_'+uid()
    position,input_position=_image_positions(canvas,source,selection['key'] if selection else None,new_profile,new_node)
    def apply(state):
        check(state)
        if selection:target=selection['key']
        else:
            store_profile(state,new_profile)
            target=add_image_input(state,(input_position.x(),input_position.y()))
            state['multi_output']['image_inputs'][target].update(workflow=new_profile['id'],node=new_node)
        state.setdefault('canvas_functions',{}).setdefault('images',{})[key]=dict(source=copy.deepcopy(source),attached=False)
        state.setdefault('text_positions',{})[key]=[position.x(),position.y()]
        connect(state,key,target,'image')
    return key if canvas.commit(apply) else None
