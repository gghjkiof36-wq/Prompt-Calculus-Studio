"""Explicit clicks go straight to the native queue, independent of GPU completion.

Only the short browser submission transaction is serialized here. There is no
durable ordinary-run buffer and no replay of old ordinary demands on startup.
"""
import copy
import uuid
from collections import deque
from PySide6.QtCore import QTimer


class DirectRuns:
    def __init__(self, runner):
        self.runner=runner;self.client=runner.client;self.window=runner.window
        self.pending=deque();self.current=None;self.submitted={}

    def add(self, state, route, count=1, source=None):
        # Capture the click, not a later edit while the browser acknowledges it.
        for _ in range(count):
            self.pending.append(dict(operation=uuid.uuid4().hex,state=copy.deepcopy(state),route=copy.deepcopy(route),source=copy.deepcopy(source)))
        self.pump()

    def busy(self):
        return bool(self.current or self.pending or self.submitted)

    def valid(self, item):
        return self.current is item

    def pump(self):
        if self.current or not self.pending or not self.client.connected:return
        flow=self.runner.current
        if flow and not flow.get('waiting'):return
        self.current=item=self.pending.popleft()
        from .native_submission import prepare
        try:
            prepare(self.client,item['state'],item['route']['workflow'],lambda:self.valid(item),
                    lambda snapshot,images:self.submit(item,snapshot,images),lambda error:self.failed(item,error))
        except (ValueError,OSError) as exc:self.failed(item,str(exc))

    def submit(self,item,snapshot,images):
        if not self.valid(item):return
        snapshot['state']['_entry_point']='pcs'
        self.client.generation.submit_native(snapshot,item['route']['workflow'],
            lambda job:self.queued(item,job),lambda error:self.failed(item,error),
            lambda:self.valid(item),images=images,ident=item['operation'])

    def queued(self,item,job):
        if not self.valid(item):return
        job['input_route']=copy.deepcopy(item['route']);self.client.generation.save(job)
        item.pop('state',None);item['prompt_id']=job['prompt_id']
        self.submitted[item['operation']]=item;self.current=None
        self.runner.notify('已提交至 ComfyUI。')
        # Receiving a queue receipt, not finishing a generated image, releases
        # the browser submission slot. The native executor owns the backlog.
        QTimer.singleShot(0,self.pump)

    def failed(self,item,error):
        if not self.valid(item):return
        self.current=None
        self.runner.notify('這次未能確認提交：'+str(error))
        # Each following item is a separate explicit click, never a retry.
        QTimer.singleShot(0,self.pump)

    def finished(self,job):
        item=self.submitted.pop(job['id'],None)
        if not item:return False
        import time
        route=item['route'];owner=self.runner.owner_for_route(route)
        if job['state']=='complete':
            self.runner.store.set_control(owner,last_prompt=job.get('prompt_id'),last_completed=time.time(),route=route)
            source=item.get('source')
            if source and route['workspace']==self.window.state['workspace']:
                value=self.window.state.get('canvas_functions',{}).get('images',{}).get(source['key'])
                if value and value.get('batch_id')==source['batch'] and value.get('index',0)==source['index'] and source['index']+1<len(value['items']):
                    from .image_source import select
                    from .flow_data import materialize
                    select(self.window.state,source['key'],source['index']+1,self.window.store.directory)
                    materialize(self.window.state)
                    if getattr(self.window,'canvas',None):
                        self.window.canvas.refresh();self.window.changed('prompt',refresh=False)
        elif route.get('scheduler'):
            self.runner.store.set_control(owner,paused=True,last_error=job.get('error','工作未完成。'))
        self.runner.notify('生成完成。' if job['state']=='complete' else '生成未完成：'+job.get('error','請查看任務紀錄。'))
        self.runner.fill(owner)
        QTimer.singleShot(0,self.runner.pump)
        return True

    def observe(self):
        known=set(self.runner.last_status.get('running_ids',[]))|set(self.runner.last_status.get('queued_ids',[]))
        buffered={item.get('operation') for item in self.runner.store.rows()}
        for ident,job in self.client.generation.jobs.items():
            if job.get('prompt_id') not in known or ident in self.submitted or ident in buffered:continue
            route=job.get('input_route') or dict(server=job.get('server'),workspace=job.get('workspace'),workflow=job.get('requested_workflow'),scheduler=None)
            if route.get('workspace') and route.get('workflow') and route.get('server')==self.client.url:
                self.submitted[ident]=dict(operation=ident,route=copy.deepcopy(route),prompt_id=job['prompt_id'])
        for ident in list(self.submitted):
            record=self.client.generation.record(ident)
            if record and record['state'] in ('failed','complete'):self.finished(record)
            elif record and record['state']=='unconfirmed':
                item=self.submitted.pop(ident)
                if item['route'].get('scheduler'):
                    self.runner.store.set_control(self.runner.owner_for_route(item['route']),paused=True)

    def cancel(self,owner=None):
        def matches(item):
            route=item['route']
            return route['workspace']==self.window.state['workspace'] and route['server']==self.client.url and (owner is None or self.runner.owner_for_route(route)==owner)
        self.pending=deque(item for item in self.pending if not matches(item))
        current=self.current if self.current and matches(self.current) else None
        running=set(self.runner.last_status.get('running_ids',[]))
        items=[item for item in self.submitted.values() if matches(item)]
        item=current or next((item for item in items if item['prompt_id'] in running),None) or next(iter(items),None)
        if not item:return False
        ident=item['operation'];job=self.client.generation.record(ident)
        if job is None:
            self.current=None;self.runner.notify('已取消尚未提交的工作。');return True
        def done(result):
            state=result.get('state');generation=self.client.generation
            if state=='abandoned':generation.retired(ident,result)
            elif state=='failed':
                saved=generation.jobs.get(ident) or generation.record(ident)
                if saved and saved['state'] not in ('complete','failed'):
                    saved.update(state='failed',error='使用者取消');generation.save(saved)
                    generation.jobs.pop(ident,None);generation.native_waiting.discard(ident)
                    self.finished(saved)
                if self.current is item:self.current=None
                self.runner.notify('已取消指定工作。')
            elif state=='settled':
                self.submitted.pop(ident,None)
                self.runner.notify('這項工作已離開執行佇列，紀錄保留。')
            else:self.runner.notify('已送出取消指令。')
        self.client.request('workflow/native/cancel',dict(id=ident),done=done,failed=self.runner.notify)
        return True

    def disconnected(self):
        count=len(self.pending);self.pending.clear();self.current=None;self.submitted.clear()
        if count:self.runner.notify('連線中斷，'+str(count)+' 次尚未提交的點擊已取消；已提交工作保留紀錄。')

    def workspace_changed(self,workspace):
        retained=deque(item for item in self.pending if item['route']['workspace']!=workspace)
        removed=len(self.pending)-len(retained);self.pending=retained
        if removed:self.runner.notify('工作區已切換，'+str(removed)+' 次尚未提交的點擊已取消；已提交的工作繼續。')
