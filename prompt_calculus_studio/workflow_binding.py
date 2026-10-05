"""Workflow-first node selection using the existing saved-workflow catalog."""
import copy
from shiboken6 import isValid
from pathlib import PurePosixPath
from .generation import options
from .widgets import StudioDialog,ComboBox,label,button,row,dialog_buttons


class WorkflowBindingDialog(StudioDialog):
    manual_choice=False
    empty_choice='不綁定 · 手動圖片'

    def __init__(self,canvas,key):
        super().__init__(canvas.window); self.setWindowTitle('綁定 CLIP 輸入')
        self.canvas=canvas; self.key=key; self.owner=(canvas.window.store,canvas.window.state['workspace'])
        self.profiles=copy.deepcopy(options(canvas.window.state)['profiles'])
        self.originals={p['id']:copy.deepcopy(p) for p in self.profiles}
        self.bindings=copy.deepcopy(canvas.data()['bindings'])
        self.catalog=canvas.window.settings_page.workflow_manager.catalog
        self.staged={}; self.read_sources={}; self.remote_entries={}; self.serial=0; self.loading=False; self.closed=False; self.force_read=False
        self.connection=(self.catalog.client.url,self.catalog.client.epoch)
        self.workflow=ComboBox(); self.workflow.setMinimumWidth(400)
        self.target=ComboBox(); self.target.setMinimumWidth(400)
        self.body.addLayout(row(label('工作流','Subtle'),None,button('重新整理',self.reload_workflows,'Quiet')))
        self.body.addWidget(self.workflow)
        self.nodes_label=label('CLIP 節點','Subtle'); self.body.addWidget(self.nodes_label); self.body.addWidget(self.target)
        self.hint=label('','Subtle',True); self.body.addWidget(self.hint)
        self.source_hint=label('','Subtle',True); self.source_hint.hide(); self.body.addWidget(self.source_hint)
        selected=canvas.data().get('clip_inputs',{}).get(key,{}).get('workflow')
        existing=next((b for b in self.bindings if b['clip']==key and (not selected or b['workflow']==selected)),None)
        self.initial=existing['workflow'] if existing else selected
        if self.manual_choice:self.initial=self.image_binding['workflow'] if self.image_binding else None
        self.workflow.currentIndexChanged.connect(self.read_current)
        self.body.addWidget(dialog_buttons(self,self.accept))
        self.catalog.changed.connect(self.refresh_workflows); self.finished.connect(self.finish_requests)
        self.refresh_workflows()
        if not self.catalog.loaded and not self.catalog.busy:self.catalog.refresh()

    def profile(self):
        key=self.workflow.currentData()
        return self.staged.get(key) or next((p for p in self.profiles if p['id']==key),None)

    def finish_requests(self,*_):
        if self.closed:return
        self.closed=True; self.serial+=1; self.catalog.changed.disconnect(self.refresh_workflows)

    def refresh_workflows(self):
        if self.closed:return
        connection=(self.catalog.client.url,self.catalog.client.epoch)
        if connection!=self.connection:
            self.connection=connection; self.staged.clear(); self.read_sources.clear(); self.serial+=1; self.loading=False
        if self.catalog.busy:
            # refresh() emits both its starting and finished state. Only the
            # completed list can initiate a new native read/navigation.
            self.loading=True;self.target.setEnabled(False);return
        selected=self.workflow.currentData() if self.workflow.count() else self.initial
        self.remote_entries={}
        for path in self.catalog.files:
            origin=dict(server=self.catalog.server,path=path)
            prior=next((p for p in self.profiles if p.get('origin')==origin),None)
            key=prior['id'] if prior else self.catalog.identity(path)
            self.remote_entries[key]=dict(path=path,name=prior['name'] if prior else PurePosixPath(path).stem,origin=origin)
        self.workflow.blockSignals(True); self.workflow.clear()
        if self.manual_choice:self.workflow.addItem(self.empty_choice,None)
        for p in self.profiles:
            if p['id'] not in self.remote_entries:self.workflow.addItem(p['name'],p['id'])
        for key,entry in self.remote_entries.items():self.workflow.addItem(entry['name']+' · ComfyUI',key)
        self.workflow.setCurrentIndex(max(0,self.workflow.findData(selected))); self.workflow.blockSignals(False)
        self.read_current()

    def reload_workflows(self):
        self.staged.clear(); self.read_sources.clear(); self.serial+=1; self.force_read=True; self.catalog.refresh()

    def show_read_source(self,key):
        source=self.read_sources.get(key,'')
        if source.startswith('已儲存版本'):self.source_hint.setText(source);self.source_hint.show()

    def read_current(self):
        self.serial+=1; serial=self.serial; key=self.workflow.currentData(); self.loading=False
        self.source_hint.hide()
        entry=self.remote_entries.get(key)
        # Adding another CLIP must preserve an already linked workflow's local
        # parameter overrides. Re-reading that graph is an explicit refresh.
        cached=not self.force_read and key in self.originals
        if not entry or key in self.staged or cached:self.refresh_targets();self.show_read_source(key);return
        self.loading=True; self.target.clear(); self.target.setEnabled(False)
        self.hint.setText('讀取工作流節點…'); self.hint.show()
        client=self.catalog.client; context=(client.url,client.epoch)
        def valid():
            if not isValid(self) or not isValid(self.canvas.window):return False
            w=self.canvas.window
            return not self.closed and serial==self.serial and context==(client.url,client.epoch) and self.owner==(w.store,w.state['workspace'])
        def failed(error):
            if not valid():return
            self.loading=False; self.hint.setText('無法讀取工作流：'+str(error)); self.hint.show()
        def received(value,source='已儲存版本'):
            if not valid():return
            try:
                from .workflow_import import read_profile
                profile=read_profile(value,entry['name'],key,entry['origin'])
            except (ValueError,KeyError,TypeError) as exc:failed(exc);return
            self.staged[key]=profile; self.read_sources[key]=source; self.loading=False; self.refresh_targets()
            # Only the saved fallback needs an extra label; ordinary live
            # operation remains visually quiet.
            self.show_read_source(key)
        self.catalog.read_draft(entry['path'],self.originals.get(key),received,failed)

    def accept(self):
        if self.loading or (self.workflow.currentData() is not None and not self.target.isEnabled()):return
        if not self.profile() and not self.manual_choice:return
        super().accept()

    def binding_profile(self):
        w=self.canvas.window; profile=self.profile()
        if self.loading or (self.workflow.currentData() is not None and not self.target.isEnabled()):raise ValueError('請先讀取工作流節點。')
        if self.owner!=(w.store,w.state['workspace']):raise ValueError('工作區已改變，請重新選擇綁定。')
        if self.workflow.currentData() in self.staged and self.connection!=(w.comfy.url,w.comfy.epoch):raise ValueError('連線已改變，請重新讀取工作流。')
        if profile:
            current=next((p for p in options(w.state)['profiles'] if p['id']==profile['id']),None)
            if current!=self.originals.get(profile['id']):raise ValueError('工作流已更新，請重新選擇節點。')
        return profile
