"""Small downstream selectors for a Stage's node-specific results."""
from PySide6.QtWidgets import QFrame,QVBoxLayout
from .widgets import ComboBox,label,button
from .result_data import stage_source


class ResultSelector(ComboBox):
    def __init__(self,owner,key,kind):
        super().__init__();self.owner=owner;self.key=key;self.kind=kind
        self.currentIndexChanged.connect(self.selected)

    def refresh(self):
        state=self.owner.window.state;stage=stage_source(state,self.key,self.kind)
        item=state['canvas_functions']['images'][self.key]
        workflow=state['multi_output']['stages'].get(stage,{}).get('workflow')
        profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==workflow),None)
        self.blockSignals(True);self.clear()
        self.addItem('全部圖片節點' if self.kind=='image' else '選擇文字節點',None)
        graph=(profile or {}).get('graph',{})
        results=self.owner.window.comfy.input_flow.chain.results.get(stage,{})
        if self.kind=='image':
            choices=[(k,n.get('_meta',{}).get('title',n['class_type'])+' · #'+k) for k,n in graph.items() if n['class_type'] in ('PreviewImage','SaveImage')]
            for image in results.get('images',[]):
                node=image.get('reference',{}).get('node')
                if node and not any(k==node for k,_ in choices):choices.append((node,'圖片 · #'+node))
        else:
            from .generation import text_fields
            choices=[([node,field],graph[node].get('_meta',{}).get('title',graph[node]['class_type'])+' · #'+node+' / '+field) for node,field in text_fields(graph)]
            for text in results.get('texts',[]):
                key=[text['node'],text['field']]
                if not any(k==key for k,_ in choices):choices.append((key,'#'+key[0]+' / '+key[1]))
        for value,title in choices:self.addItem(title,value)
        selected=item.get('output_node' if self.kind=='image' else 'text_field')
        index=next((i for i in range(self.count()) if self.itemData(i)==selected),-1)
        if selected is not None and index<0:self.addItem('節點已移除 · '+str(selected),selected);index=self.count()-1
        self.setCurrentIndex(max(0,index));self.setVisible(bool(stage) or self.kind=='clip');self.blockSignals(False)

    def selected(self):
        value=self.currentData();field='output_node' if self.kind=='image' else 'text_field'
        def change(state):
            item=state['canvas_functions']['images'][self.key];item[field]=value;item.pop('input_index',None)
        if self.owner.canvas.commit(change):
            self.owner.window.comfy.input_flow.chain.refresh_results()


class TextResultPanel(QFrame):
    def __init__(self,owner,key):
        super().__init__();self.owner=owner;self.key=key;self.setObjectName('InsetPanel')
        body=QVBoxLayout(self);body.setContentsMargins(14,14,14,14)
        self.selector=ResultSelector(owner,key,'clip');body.addWidget(self.selector)
        self.attach_button=button('',lambda:None);self.attach_button.hide()
        self.error=label('','Subtle',True);body.addWidget(self.error)

    def refresh(self):
        self.selector.refresh();state=self.owner.window.state;error=''
        if stage_source(state,self.key,'clip') and state['canvas_functions']['images'][self.key].get('text_field'):
            from .flow_data import resolve
            context=self.owner.window.comfy.input_flow.chain.context(self.owner.window.comfy.input_flow.chain.results)
            try:resolve(context,self.key,'clip')
            except ValueError as exc:
                if '尚未產生' not in str(exc):error=str(exc)
        self.error.setText(error);self.error.setVisible(bool(error))
