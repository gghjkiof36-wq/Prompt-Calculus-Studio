"""Sequential workflow rounds on the existing generation job journal/transport."""
import copy
import hashlib
import uuid
from pathlib import Path
from .workflow_flow import execution_profiles,ORDER_CARD
from .multi_output import bound_texts
from .generation import submission,image_target,uploaded_image
from .composition_image import freeze_image_source


def image_sources(state,profile):
    outputs={b['output'] for b in bound_texts(state,profile)}
    return list(dict.fromkeys(c['source'] for c in state['multi_output']['connections'] if c['kind']=='image' and c['destination'] in outputs))


def bound_sources(state,keys):
    data=state['multi_output']; images=state.get('canvas_functions',{}).get('images',{}); result=[]
    for key in keys:
        if key in images:
            if images[key].get('binding'):result.append(key)
        elif key in data['canvases']:
            for line in data['connections']:
                if line['kind']=='image' and line['destination']==key and images.get(line['source'],{}).get('binding'):result.append(line['source'])
    return list(dict.fromkeys(result))


class WorkflowRun:
    def __init__(self,runner):
        self.runner=runner; self.client=runner.client; self.window=runner.window
        self.token=uuid.uuid4().hex; self.waiting=None; self.index=0; self.round=0; self.completed={}; self.frozen={}; self.raw={}; self.active_keys=set(); self.node=None
    def active(self):return bool(self.runner.batch and self.runner.batch['token']==self.token)
    def start(self,count):
        if type(count) is not int or not 1<=count<=100:raise ValueError('運行次數須介於 1–100。')
        self.snapshot=copy.deepcopy(self.client.snapshot()); self.state=self.snapshot['state']; self.directory=self.window.store.directory
        self.profiles=copy.deepcopy(execution_profiles(self.state)); self.count=count
        # First native acceptance gate: no saved-profile execution fallback.
        # Multi-workflow opening and batch freezing must be verified before
        # enabling them on this path; a second live read is not a frozen batch.
        if not getattr(self.client,'native_supported',False):
            raise ValueError(getattr(self.client,'native_unavailable_reason','') or '請更新 ComfyUI 擴充後使用原生工作流執行；未提交 PCS 舊工作流副本。')
        if count!=1 or len(self.profiles)!=1:
            raise ValueError('此修復候選目前僅開放單一工作流執行一次；跨工作流與多輪仍待整合。')
        # Validate every destination before any submission, and freeze manual
        # images now. Only explicitly bound upstream results are resolved later.
        for profile in self.profiles:
            sources=image_sources(self.state,profile)
            if sources and not image_target(profile):raise ValueError('「'+profile['name']+'」尚未指定接收圖片的 LoadImage 節點。')
            for key in sources:
                if not bound_sources(self.state,[key]):
                    source=freeze_image_source(self.state,self.directory,key); self.frozen[key]=source
                    self.raw[source['sha256']]=(self.directory/source['relative']).read_bytes()
        self.runner.batch=dict(token=self.token,count=count,index=0); self.client.run_id=self.token
        self.next()
    def status(self,phase):
        if not self.active():return
        profile=self.profiles[self.index]
        self.runner.status(f"第 {self.round+1}／{self.count} 輪 · {profile['name']} · {phase}")
    def next(self):
        if not self.active():return
        if self.index==len(self.profiles):self.index=0; self.round+=1
        if self.round==self.count:
            self.runner.finish_batch(f'已完成 {self.count} 輪，共 {self.count*len(self.profiles)} 次工作流。');return
        self.waiting=None; self.node=None; profile=self.profiles[self.index]
        self.active_keys={ORDER_CARD}
        for b in bound_texts(self.state,profile):
            self.active_keys.update((b['clip'],b['output']))
            cid=self.state['multi_output']['outputs'][b['output']]['canvas']
            if cid:
                self.active_keys.add(cid)
                self.active_keys.update(k for k in self.state['multi_output']['canvases'][cid]['members'] if self.state['uses'][k]['enabled'])
        sources=image_sources(self.state,profile); dynamic=bound_sources(self.state,sources); self.active_keys.update(sources+dynamic)
        self.status('準備圖片' if sources else '準備提交')
        def ready(values,errors):
            if not self.active():return
            if errors:self.fail('；'.join(errors.values()));return
            stage=copy.deepcopy(self.snapshot)
            for key,value in values.items():stage['state']['canvas_functions']['images'][key]['source']=value
            try:
                images=[self.frozen.get(key) or freeze_image_source(stage['state'],self.directory,key) for key in sources]
                if len({s['sha256'] for s in images})>1:raise ValueError('同一工作流的 Prompt 接入不同圖片，請統一來源。')
                source=images[0] if images else None
                if source:
                    raw=self.raw.get(source['sha256'])
                    if raw is None:raw=(self.directory/source['relative']).read_bytes()
                    if hashlib.sha256(raw).hexdigest()!=source['sha256']:raise ValueError('來源圖片已變更，停止這輪執行。')
                    self.upload(stage,profile,source,raw)
                else:self.submit(stage,profile,None,'')
            except (ValueError,OSError) as exc:self.fail(str(exc))
        self.client.images.resolve(self.state,dynamic,self.directory,self.completed,ready,self.fail,valid=self.active)
    def upload(self,stage,profile,source,raw):
        boundary='PromptCalculusStudio'+uuid.uuid4().hex; filename='pcs_'+source['sha256']+Path(source['relative']).suffix
        fields={'type':'input','subfolder':'prompt_calculus_studio','overwrite':'false'}
        body=b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode() for key,value in fields.items())
        body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
        self.status('準備來源圖片')
        def ready(result):
            if not self.active():return
            try:self.submit(stage,profile,source,uploaded_image(result))
            except (ValueError,KeyError) as exc:self.fail(str(exc))
        self.client.request('/upload/image',done=ready,failed=self.fail,raw=body,content_type='multipart/form-data; boundary='+boundary)
    def submit(self,stage,profile,source,image):
        if not self.active():return
        self.status('正在交給已綁定的原生工作流')
        def queued(job):
            if not self.active():return
            self.waiting=job['prompt_id']; self.status('等待執行')
        self.runner.submit_native(stage,profile['id'],queued,self.fail,self.active,image=image)
    def observe(self,status):
        if not self.active() or not self.waiting:return
        if self.waiting in status.get('running_ids',[]):
            self.node=status.get('executing_node'); self.status('執行中'+(' · #'+self.node if self.node else ''))
        elif self.waiting in status.get('queued_ids',[]):self.status('等待執行')
    def finished(self,job,entry):
        if not self.active() or job.get('prompt_id')!=self.waiting:return False
        if job['state']!='complete':self.fail(job.get('error') or '工作流執行失敗。');return True
        self.completed[self.profiles[self.index]['id']]=self.waiting
        self.index+=1; self.runner.batch['index']=self.round*len(self.profiles)+self.index
        self.next();return True
    def fail(self,error):
        if self.active():self.runner.finish_batch('流程已停止：'+str(error)+'；後續工作流未提交。')
