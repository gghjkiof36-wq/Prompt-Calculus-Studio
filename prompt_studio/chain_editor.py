"""Stage-local input mapping, using the existing workflow catalog selector."""
import copy
from PySide6.QtWidgets import QFrame,QFormLayout,QLineEdit,QSpinBox
from .widgets import ComboBox,label,row,scrolling
from .workflow_binding import WorkflowBindingDialog
from .chain_model import definition,stage


def sources(state,kind):
    from .flow_data import source_name,endpoint
    data=state['multi_output'];result=[]
    if kind=='clip':result.extend((key,source_name(state,key)) for key in data['outputs'])
    result.extend((key,(value.get('source') or {}).get('name',value.get('name','圖片來源')))
                  for key,value in state.get('canvas_functions',{}).get('images',{}).items())
    if kind=='image':result.extend((key,c['name']) for key,c in data['canvases'].items())
    for key,scheduler in data['schedulers'].items():
        result.extend((endpoint(key,ch['id']),scheduler['name']+' · '+ch['name']) for ch in scheduler['channels'] if ch['type']==kind)
    return result


class StageDialog(WorkflowBindingDialog):
    manual_choice=True
    def __init__(self,canvas,item=None,index=None):
        self.item=copy.deepcopy(item);self.index=index;self.text_rows=[];self.image_rows=[]
        self.image_binding=dict(workflow=item['workflow']) if item else None
        super().__init__(canvas,'__chain_stage__')
        self.setWindowTitle('串接階段 · 工作流與輸入');self.resize(820,760)
        self.workflow.setItemText(0,'選擇工作流');self.nodes_label.setText('本階段傳出的圖片節點')
        self.name=QLineEdit(item['name'] if item else '');self.name.setPlaceholderText('階段名稱，例如 B 放大')
        self.body.insertLayout(0,row(label('階段名稱','Subtle'),self.name))
        self.form_widget=QFrame();self.form=QFormLayout(self.form_widget)
        self.body.insertWidget(self.body.count()-1,scrolling(self.form_widget),1)
        self.selection=ComboBox();self.selection.addItem('上一階段的全部結果','all');self.selection.addItem('指定一張','single')
        self.number=QSpinBox();self.number.setRange(1,100000);self.number.setPrefix('第 ');self.number.setSuffix(' 張')
        old=(item or {}).get('upstream') or {}
        self.selection.setCurrentIndex(max(0,self.selection.findData(old.get('mode','all'))));self.number.setValue(old.get('index',0)+1)
        self.body.insertLayout(self.body.count()-1,row(self.selection,self.number))
        self.selection.currentIndexChanged.connect(lambda:self.number.setEnabled(self.selection.currentData()=='single'))
        self.number.setEnabled(self.selection.currentData()=='single');self.rebuild_inputs()

    def refresh_targets(self):
        self.target.clear();self.target.addItem('選擇圖片輸出節點',None);profile=self.profile()
        for key,node in (profile or {}).get('graph',{}).items():
            if node['class_type'] in ('SaveImage','PreviewImage'):self.target.addItem(node.get('_meta',{}).get('title',node['class_type'])+' · #'+key,key)
        self.target.setEnabled(profile is not None)
        original=self.item or {};self.target.setCurrentIndex(max(0,self.target.findData(original.get('output'))))
        self.hint.setText('純圖片階段不需要 CLIP。每個欄位選一個來源；其餘參數沿用提交時原生工作流。')
        if hasattr(self,'form'):self.rebuild_inputs()

    def rebuild_inputs(self):
        from .generation import text_fields
        while self.form.rowCount():self.form.removeRow(0)
        self.text_rows=[];self.image_rows=[];profile=self.profile()
        if not profile:return
        state=self.canvas.window.state;plan=definition(state) or {};stages=plan.get('stages',[])
        index=len(stages) if self.index is None else self.index
        original=self.item if self.item and self.item['workflow']==profile['id'] else {}
        for node,field in text_fields(profile['graph']):
            combo=ComboBox();combo.setAccessibleName('文字 #'+node+' / '+field)
            combo.addItem('沿用 ComfyUI 原生文字（含空白）',dict(mode='web'))
            for key,name in sources(state,'clip'):combo.addItem('PCS · '+name,dict(mode='pcs',source=key))
            for ancestor in stages[:index]:
                parent=next((p for p in state['generation']['profiles'] if p['id']==ancestor['workflow']),None)
                for src_node,src_field in text_fields(parent['graph']) if parent else []:
                    combo.addItem(ancestor['name']+' 實際文字 · #'+src_node+' / '+src_field,
                                  dict(mode='upstream',source=dict(stage=ancestor['id'],node=src_node,field=src_field)))
            saved=next((m for m in original.get('texts',[]) if (m['node'],m['field'])==(node,field)),None)
            if saved:combo.setCurrentIndex(max(0,combo.findData({k:v for k,v in saved.items() if k not in ('node','field')})))
            elif not original:
                binding=next((b for b in state['multi_output']['bindings'] if (b['workflow'],b['node'],b['field'])==(profile['id'],node,field)),None)
                if binding:
                    from .clip_flow import source
                    key=source(state,binding['clip'])
                    if key:combo.setCurrentIndex(max(0,combo.findData(dict(mode='pcs',source=key))))
            self.form.addRow('文字 #'+node+' / '+field,combo);self.text_rows.append((node,field,combo))
        for node,value in profile['graph'].items():
            if value['class_type']!='LoadImage' or not isinstance(value.get('inputs',{}).get('image'),str):continue
            combo=ComboBox();combo.setAccessibleName('圖片 #'+node);combo.addItem('沿用 ComfyUI 的固定圖片',None)
            if index:combo.addItem('上一階段：'+stages[index-1]['name'],dict(upstream=stages[index-1]['id']))
            for key,name in sources(state,'image'):combo.addItem('PCS · '+name,dict(source=key))
            upstream=original.get('upstream')
            saved=next((m for m in original.get('images',[]) if m['node']==node),None)
            if upstream and upstream['node']==node:combo.setCurrentIndex(max(0,combo.findData(dict(upstream=upstream['stage']))))
            elif saved:combo.setCurrentIndex(max(0,combo.findData(dict(source=saved['source']))))
            elif index and not self.image_rows:combo.setCurrentIndex(1)
            self.form.addRow('圖片 LoadImage #'+node,combo);self.image_rows.append((node,combo))

    def value(self):
        profile=self.binding_profile()
        if not profile or not self.target.currentData():raise ValueError('請選擇工作流與圖片輸出節點。')
        value=copy.deepcopy(self.item) if self.item else stage(profile['id'],profile['name'])
        value.update(workflow=profile['id'],name=self.name.text().strip() or profile['name'],output=self.target.currentData(),texts=[],images=[],upstream=None)
        for node,field,combo in self.text_rows:value['texts'].append(dict(node=node,field=field,**combo.currentData()))
        for node,combo in self.image_rows:
            choice=combo.currentData()
            if not choice:continue
            if choice.get('upstream'):
                if value['upstream']:raise ValueError('每階段只能有一個變動的上游圖片流。')
                value['upstream']=dict(node=node,stage=choice['upstream'],mode=self.selection.currentData(),index=self.number.value()-1)
            else:value['images'].append(dict(node=node,**choice))
        return value,profile

    def accept(self):
        try:self.value()
        except ValueError as exc:self.hint.setText(str(exc));return
        super().accept()
