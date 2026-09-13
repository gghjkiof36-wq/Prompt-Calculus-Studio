"""Browse ComfyUI's saved workflows without importing the whole collection."""
import uuid
from pathlib import PurePosixPath
from urllib.parse import quote
from PySide6.QtCore import QObject,Signal


def workflow_path(value):
    if not isinstance(value,str) or '\\' in value or '\x00' in value: raise ValueError('工作流路徑無效。')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or not value.lower().endswith('.json'): raise ValueError('工作流路徑無效。')
    return str(path)


class WorkflowCatalog(QObject):
    changed=Signal()
    def __init__(self,client):
        super().__init__(client); self.client=client; self.server=client.url; self.files=[]; self.message=''; self.busy=False; self.loaded=False; self.serial=0; self.epoch=client.epoch
        client.stateChanged.connect(self.connection_changed)
    def connection_changed(self):
        if self.server!=self.client.url or self.epoch!=self.client.epoch:
            self.serial+=1; self.epoch=self.client.epoch; self.server=self.client.url; self.files=[]; self.busy=False; self.loaded=False; self.message=''; self.changed.emit()
        elif self.busy and not self.client.enabled:
            self.serial+=1; self.busy=False; self.loaded=False; self.changed.emit()
    def refresh(self):
        if self.busy: return
        self.server=self.client.url; self.busy=True; self.serial+=1; serial=self.serial; self.message='讀取 ComfyUI 工作流…'; self.changed.emit()
        def done(values):
            if serial!=self.serial: return
            files=[]
            for value in values if isinstance(values,list) else []:
                try: files.append(workflow_path(value))
                except ValueError: continue
            self.files=sorted(set(files),key=str.casefold); self.loaded=True; self.busy=False; self.message=''; self.changed.emit()
        def failed(error):
            if serial!=self.serial: return
            if getattr(error,'status_code',None)==404: done([]); return
            self.busy=False; self.message='無法讀取 ComfyUI 已儲存工作流，請確認連線後重新整理。'; self.changed.emit()
        self.client.request('/userdata?dir=workflows&recurse=true',done=done,failed=failed)
    def identity(self,path): return uuid.uuid5(uuid.NAMESPACE_URL,self.server+'/workflows/'+workflow_path(path)).hex
    def read(self,path,done,failed):
        self.client.request('/userdata/'+quote('workflows/'+workflow_path(path),safe=''),done=done,failed=failed)
    def trash(self,path,done,failed):
        path=workflow_path(path); target='prompt_studio/workflow-trash/'+uuid.uuid4().hex+'/'+path
        def moved(_):
            self.files=[p for p in self.files if p!=path]; self.changed.emit(); done(dict(server=self.server,path=path,trash=target))
        self.client.request('/userdata/'+quote('workflows/'+path,safe='')+'/move/'+quote(target,safe='')+'?overwrite=false',data={},done=moved,failed=failed)
    def restore(self,record,done,failed):
        if record['server']!=self.client.url: failed('請先連線到原本的 ComfyUI 再復原。'); return
        def moved(_): self.loaded=False; self.refresh(); done()
        self.client.request('/userdata/'+quote(record['trash'],safe='')+'/move/'+quote('workflows/'+workflow_path(record['path']),safe='')+'?overwrite=false',data={},done=moved,failed=failed)
