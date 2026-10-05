"""Browse ComfyUI's saved workflows without importing the whole collection."""
import uuid
import time
from pathlib import PurePosixPath
from urllib.parse import quote
from PySide6.QtCore import QObject,Signal,QTimer


def workflow_path(value):
    if not isinstance(value,str) or '\\' in value or '\x00' in value: raise ValueError('工作流路徑無效。')
    path=PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or ':' in value or not value.lower().endswith('.json'): raise ValueError('工作流路徑無效。')
    return str(path)


class WorkflowCatalog(QObject):
    changed=Signal()
    def __init__(self,client):
        super().__init__(client); self.client=client; self.server=client.url; self.files=[]; self.message=''; self.busy=False; self.loaded=False; self.serial=0; self.epoch=client.epoch
        self.refresh_on_connect=True
        client.stateChanged.connect(self.connection_changed)
    def connection_changed(self):
        if self.server!=self.client.url or self.epoch!=self.client.epoch:
            different_server=self.server!=self.client.url
            self.serial+=1; self.epoch=self.client.epoch; self.server=self.client.url; self.busy=False
            if different_server:self.files=[]; self.loaded=False
            self.refresh_on_connect=True; self.message=''; self.changed.emit()
        elif self.busy and not self.client.enabled:
            self.serial+=1; self.busy=False; self.loaded=False; self.changed.emit()
        if self.refresh_on_connect and self.client.enabled and self.client.connected:
            self.refresh_on_connect=False; self.refresh()
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
    def read_draft(self,path,profile,done,failed):
        """Prefer a verified open draft; never label a saved fallback as live."""
        path=workflow_path(path);client=self.client;context=(client.url,client.epoch)
        ident=uuid.uuid4().hex;started=time.monotonic()
        def valid():return not client.stopped and context==(client.url,client.epoch)
        def saved(reason):
            if valid():self.read(path,lambda value:done(value,'已儲存版本 · '+reason) if valid() else None,failed)
        if not getattr(client,'native_inspect_supported',False):
            saved('更新擴充後可讀取未儲存節點');return
        def error(message):
            if not valid():return
            # Ambiguity, failed switching or an uncertain in-flight read must
            # remain errors. A saved file cannot stand in for those drafts.
            if '[native_target_missing]' in str(message):saved('工作流尚未在網頁開啟')
            elif '[native_not_ready]' in str(message):saved('網頁未回覆')
            else:failed(message)
        def received(value):
            if not valid():return
            state=value.get('state')
            if state=='inspected':
                data=value.get('inspection',{});identity=data.get('identity',{})
                if identity.get('path')!=path or profile and profile.get('frontend_id') and identity.get('frontend_id')!=profile['frontend_id']:
                    failed('原生讀取回覆與綁定不符。');return
                done(dict(data,format='prompt_studio_native_inspection',version=1),'原生未儲存草稿');return
            if state in ('pending','delivered') and time.monotonic()-started<35:
                QTimer.singleShot(100,self,lambda:client.request('workflow/native/status',dict(id=ident),done=received,failed=error) if valid() else None)
                return
            failed(value.get('error') or '原生工作流未讀取，請重新整理。')
        client.request('workflow/native/inspect',dict(id=ident,target=dict(path=path,frontend_id=(profile or {}).get('frontend_id',''))),done=received,failed=error)
    def trash(self,path,done,failed):
        path=workflow_path(path); target='prompt_studio/workflow-trash/'+uuid.uuid4().hex+'/'+path
        server,epoch=self.client.url,self.client.epoch
        def moved(_):
            if server!=self.client.url or epoch!=self.client.epoch:return
            self.files=[p for p in self.files if p!=path]; self.changed.emit(); done(dict(server=server,path=path,trash=target))
        self.client.request('/userdata/'+quote('workflows/'+path,safe='')+'/move/'+quote(target,safe='')+'?overwrite=false',data={},done=moved,failed=failed)
    def restore(self,record,done,failed):
        if record['server']!=self.client.url: failed('請先連線到原本的 ComfyUI 再復原。'); return
        server,epoch=self.client.url,self.client.epoch
        def moved(_):
            if server!=self.client.url or epoch!=self.client.epoch:return
            self.loaded=False; self.refresh(); done()
        self.client.request('/userdata/'+quote(record['trash'],safe='')+'/move/'+quote('workflows/'+workflow_path(record['path']),safe='')+'?overwrite=false',data={},done=moved,failed=failed)
