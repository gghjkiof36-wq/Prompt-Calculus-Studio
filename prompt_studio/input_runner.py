"""One submission journal, with live demands or opt-in typed input buffers."""
import copy
import time
import uuid
from PySide6.QtCore import QTimer
from .flow_data import (CAPACITY,capture_inputs,scheduler_for,source_for,materialize,workflow_nodes)
from .input_queue import InputQueue
from .image_source import select


class InputRunner:
    def __init__(self, client):
        self.client=client;self.window=client.window;self.store=InputQueue(self.window.store.db,lambda:self.window.state['workspace'])
        self.store.recover();self.current=None;self.last_status={};self.pumping=False
        self.message='';self.resume_required=True
        from .input_bridge import InputBridge
        self.bridge=InputBridge(self)
        from .direct_runs import DirectRuns
        self.direct=DirectRuns(self)
        from .chain_runner import ChainRunner
        from .stage_runner import StageRunner
        self.chain=StageRunner(self) if self.window.state.get('multi_output',{}).get('version',0)>=7 else ChainRunner(self)
        # Unsaved live demands also survive reopening, but never resume silently.
        for owner,raw in list(self.store.db.execute('SELECT owner,body FROM input_queue_control')):
            self.store.set_control(owner,paused=True)
            if owner.startswith('live:'):
                control=self.store.control(owner)
                # Keep old accidental clicks as a record, never as a new queue.
                self.store.set_control(owner,legacy_demands=control.get('legacy_demands',0)+control.get('credits',0),credits=0,active=None)
                continue
            active=self.store.control(owner).get('active')
            if active and self.client.generation.record(active['operation']) is None:
                self.store.set_control(owner,active=None,last_error='上次在提交前結束，該張未完成；可重新加入。')
        for item in self.store.rows():
            if item['state']=='preparing' and item.get('operation') and self.client.generation.record(item['operation']) is None:
                self.store.update(item['id'],state='failed',error='上次在提交前結束，未送到 ComfyUI。')

    def notify(self, message=''):
        if message:self.message=message;self.client.generation.status(message);self.window.notice(message)
        self.client.stateChanged.emit()

    def owner(self, scheduler, workflow):
        return self.store.scoped(scheduler) if scheduler else 'live:'+self.window.state['workspace']+':'+workflow

    def owner_for_route(self, route):
        return ('schedule:'+route['workspace']+':'+route['scheduler'] if route.get('scheduler')
                else 'live:'+route['workspace']+':'+route['workflow'])

    def route(self, workflow, scheduler):
        state=self.window.state;data=state['multi_output']
        endpoints=workflow_nodes(state,workflow)
        return dict(server=self.client.url,workspace=state['workspace'],workflow=workflow,scheduler=scheduler,
                    destinations=sorted([b['clip'],b['node'],b['field']] for b in data['bindings']
                                        if b['workflow']==workflow and b['clip'] in endpoints),
                    image_destinations=sorted([k,v.get('node')] for k,v in data.get('image_inputs',{}).items()
                                              if v.get('workflow')==workflow and k in endpoints))

    def context(self, source=None):
        state=copy.deepcopy(self.window.state)
        for key,item in state.get('canvas_functions',{}).get('images',{}).items():
            if item.get('binding'):
                current=self.client.images.source(key)
                item['source']=copy.deepcopy(current)
        if source:
            value=state.get('canvas_functions',{}).get('images',{}).get(source['key'])
            if not value or value.get('batch_id')!=source['batch']:
                raise ValueError('圖片來源清單已變更；已保留等待項目，請核對後繼續。')
            select(state,source['key'],source['index'],self.window.store.directory)
        materialize(state)
        return state

    def execute(self, count=1):
        if self.window.state.get('multi_output',{}).get('version',0)>=7:return self.chain.start(count)
        from .chain_model import enabled
        if enabled(self.window.state):return self.chain.start(count)
        from .workflow_flow import execution_profiles
        if not self.client.connected:raise ValueError('請先連線至 ComfyUI。')
        if type(count) is not int or not 1<=count<=100:raise ValueError('執行次數須介於 1–100。')
        state=self.window.state;profiles=execution_profiles(state)
        if len(profiles)!=1:raise ValueError('本輪請使用一個明確的目標工作流；不自動串接不同工作流。')
        workflow=profiles[0]['id'];scheduler=scheduler_for(state,workflow);source=source_for(state,workflow)
        owner=self.owner(scheduler,workflow);route=self.route(workflow,scheduler);control=self.store.control(owner)
        if not scheduler:
            if source:
                image=state['canvas_functions']['images'][source]
                # Completion/failure can remove an earlier request while a
                # later image is pending. Reserve positions monotonically so
                # that another explicit click cannot submit that later image twice.
                index=self.direct.next_source(source,image)
                if index+count>len(image['items']):raise ValueError('執行次數超過本批剩餘圖片。')
                stages=[]
                for i in range(index,index+count):
                    origin=dict(key=source,batch=image['batch_id'],index=i)
                    stages.append((self.context(origin),origin))
                self.direct.source_positions[self.direct.source_key(source,image['batch_id'])]=index+count
                for stage,origin in stages:self.direct.add(stage,route,source=origin)
            else:self.direct.add(self.context(),route,count)
            return
        outstanding=bool(self.store.rows(owner) or self.direct_count(owner) or control.get('credits',0) or control.get('active') or
                         control.get('feed') and control['feed']['next']<control['feed']['total'])
        if outstanding and control.get('route')!=route:
            self.store.set_control(owner,paused=True)
            raise ValueError('這份預排程或執行需求還綁定原工作流；請恢復原綁定或先處理保留項目。')
        self.store.set_control(owner,route=route)
        if not outstanding:
            self.store.set_control(owner,paused=False);control=self.store.control(owner)
        busy=bool(self.current or self.direct.busy() or self.client.running or self.client.pending)
        if source:
            image=state['canvas_functions']['images'][source]
            old=control.get('feed')
            if old and old['batch']==image['batch_id']:
                self.notify('這批圖片已開始，不會重複加入。');return
            index=image.get('index',0)
            self.store.set_control(owner,feed=dict(key=source,batch=image['batch_id'],next=index,total=len(image['items'])))
            if not busy:
                self.direct.add(self.context(),route)
                self.store.set_control(owner,feed=dict(key=source,batch=image['batch_id'],next=index+1,total=len(image['items'])))
            self.fill(owner)
        else:
            reserve=count if busy else count-1
            if len(self.store.rows(owner))+reserve+self.direct_count(owner)>CAPACITY:
                raise ValueError('預排程最多十項；目前空位不足，未加入這次要求。')
            context=self.context()
            if not busy:
                self.direct.add(context,route)
                count-=1
            if count:
                captured=self.capture(context,scheduler)
                for _ in range(count):self.store.add(owner,captured,route=route,label='目前輸入')
                self.notify('已保存 '+str(count)+' 項至預排程。')
        self.bridge.poll();self.pump()

    def direct_count(self, owner):
        direct=self.direct
        return sum(self.owner_for_route(item['route'])==owner for item in
                   [*direct.submitted.values(),*direct.pending,*([direct.current] if direct.current else [])])

    def fill(self, owner):
        if self.chain.owns(owner):return
        control=self.store.control(owner);feed=control.get('feed')
        if control['paused'] or not feed:return
        if control['route']['workspace']!=self.window.state['workspace'] or control['route']['server']!=self.client.url:
            self.store.set_control(owner,paused=True);self.notify('工作區或連線已切換；原批次已保留並暫停。');return
        while len(self.store.rows(owner))+self.direct_count(owner)<CAPACITY and feed['next']<feed['total']:
            source=dict(key=feed['key'],batch=feed['batch'],index=feed['next'])
            try:
                state=self.context(source)
                captured=self.capture(state,control['route']['scheduler'])
                name=state['canvas_functions']['images'][source['key']]['source']['name']
                self.store.add(owner,captured,route=control['route'],source=source,label=name)
            except (ValueError,OSError) as exc:
                self.store.set_control(owner,paused=True);self.notify(str(exc));return
            feed=dict(feed,next=feed['next']+1)
            self.store.set_control(owner,feed=feed)

    def controls(self):
        return [(owner,self.store.control(owner)) for owner, in self.store.db.execute('SELECT owner FROM input_queue_control')]

    def capture(self,state,scheduler):
        from .composition_image import freeze_image_source
        return capture_inputs(state,scheduler,lambda context,key:freeze_image_source(context,self.window.store.directory,key))

    def next_item(self):
        for item in self.store.rows():
            control=self.store.control(item['owner'])
            if self.chain.owns(item['owner']):continue
            if not control['paused'] and item['state']=='waiting' and item['route']['workspace']==self.window.state['workspace']:
                return item
        return None

    def pump(self):
        if self.window.state.get('multi_output',{}).get('version',0)>=7:return self.chain.pump()
        if self.pumping or self.current or not self.client.connected:return
        self.direct.pump()
        if self.client.running or self.client.pending or self.direct.busy():return
        self.pumping=True
        try:
            item=self.next_item()
            if not item:return
            route=item['route'];owner=item['owner']
            if route['server']!=self.client.url or self.route(route['workflow'],route['scheduler'])!=route:
                self.store.set_control(owner,paused=True);self.notify('工作流或接線已改變；等待內容保留，請核對。');return
            if scheduler_for(self.window.state,route['workflow'])!=route['scheduler']:
                self.store.set_control(owner,paused=True);self.notify('預排程已斷開，等待內容保留。');return
            stage=self.context(item.get('source') if item.get('live') else None)
            if not item.get('live'):
                stage['_execution_inputs']=copy.deepcopy(item['inputs']);materialize(stage)
            token=uuid.uuid4().hex
            self.current=dict(item,token=token,operation=uuid.uuid4().hex,waiting=None)
            if item.get('live'):
                control=self.store.control(owner);self.store.set_control(owner,credits=control['credits']-1,
                    active=dict(operation=self.current['operation'],source=copy.deepcopy(item.get('source'))))
            else:self.store.update(item['id'],state='preparing',operation=self.current['operation'])
            self.prepare(stage,route['workflow'],token)
        except (ValueError,OSError) as exc:
            if self.current:self.fail(str(exc),confirmed=True)
            else:
                if item:self.store.set_control(item['owner'],paused=True)
                self.notify(str(exc))
        finally:self.pumping=False

    def valid(self, token):
        return self.current is not None and self.current['token']==token

    def prepare(self, state, workflow, token):
        from .native_submission import prepare
        prepare(self.client,state,workflow,lambda:self.valid(token),
                lambda snapshot,images:self.submit(snapshot,workflow,images,token),
                lambda error:self.fail(error,confirmed=True))

    def completed(self):
        return {c['route']['workflow']:c['last_prompt'] for _,c in sorted(self.controls(),key=lambda pair:pair[1].get('last_completed',0))
                 if c.get('last_prompt') and c.get('route',{}).get('server')==self.client.url
                 and c['route'].get('workspace')==self.window.state['workspace']}

    def submit(self, snapshot, workflow, images, token):
        operation=self.current['operation']
        snapshot['state']['_entry_point']=self.current.get('entry_point','pcs')
        def queued(job):
            if not self.valid(token):return
            self.current['waiting']=job['prompt_id']
            if not self.current.get('live'):self.store.update(self.current['id'],state='submitted',prompt_id=job['prompt_id'])
            source=self.current.get('source')
            if source and self.current['route']['workspace']==self.window.state['workspace']:
                value=self.window.state.get('canvas_functions',{}).get('images',{}).get(source['key'])
                if value and value.get('batch_id')==source['batch'] and value.get('index')!=source['index']:
                    canvas=getattr(self.window,'canvas',None)
                    if canvas:
                        select(self.window.state,source['key'],source['index'],self.window.store.directory)
                        materialize(self.window.state);canvas.refresh();self.window.changed('prompt',refresh=False)
            self.notify('已交給 ComfyUI；可繼續修改畫布及加入工作。')
            QTimer.singleShot(0,self.direct.pump)
        def failed(error):
            if not self.valid(token):return
            record=self.client.generation.record(operation)
            self.fail(error,confirmed=bool(record and record['state']=='failed'))
        self.client.generation.submit_native(snapshot,workflow,queued,failed,lambda:self.valid(token),images=images,ident=operation)
        self.notify('正在提交本輪輸入…')

    def fail(self, error, confirmed=False):
        current=self.current
        if not current:return
        self.store.set_control(current['owner'],paused=True)
        if not current.get('live'):self.store.update(current['id'],state='failed' if confirmed else 'unconfirmed',error=str(error))
        elif confirmed:self.store.set_control(current['owner'],active=None,last_error=str(error))
        self.current=None;self.notify('已暫停：'+str(error)+'；等待內容保留，不會自動重送。')

    def finished(self, job, entry):
        if job.get('chain'):
            if self.window.state.get('multi_output',{}).get('version',0)>=7:self.chain.observe(self.last_status)
            else:QTimer.singleShot(0,lambda:self.chain.observe(self.last_status))
            return True
        if self.direct.finished(job):return True
        current=self.current
        if not current or job['id']!=current['operation']:return False
        if job['state']!='complete':self.fail(job.get('error') or '工作未完成。',confirmed=True);return True
        owner=current['owner']
        self.store.set_control(owner,last_prompt=job['prompt_id'],last_completed=time.time())
        if current.get('live'):
            control=self.store.control(owner);cursor=control.get('cursor')
            self.store.set_control(owner,active=None,cursor=dict(cursor,index=cursor['index']+1) if cursor else None)
        else:self.store.update(current['id'],state='complete',prompt_id=job['prompt_id'],outputs=job.get('outputs',{}))
        self.current=None
        if not current.get('live'):self.fill(owner)
        self.notify('本輪完成；接續尚未完成的項目。');QTimer.singleShot(0,self.pump)
        return True

    def observe(self, status):
        self.last_status=status
        if self.window.state.get('multi_output',{}).get('version',0)>=7:
            self.chain.observe(status);return
        self.chain.observe(status)
        self.direct.observe()
        for owner,control in self.controls():
            active=control.get('active')
            if not active or self.current and self.current['operation']==active['operation']:continue
            record=self.client.generation.record(active['operation'])
            if not record or record['state'] not in ('complete','failed'):continue
            changes=dict(active=None)
            if record['state']=='complete':
                changes.update(last_prompt=record.get('prompt_id'),last_completed=time.time())
                cursor=control.get('cursor')
                if cursor and active.get('source') and cursor['batch']==active['source']['batch']:
                    changes['cursor']=dict(cursor,index=active['source']['index']+1)
            else:changes.update(paused=True,last_error=record.get('error','工作未完成。'))
            self.store.set_control(owner,**changes)
        # Reconcile journaled attempts after reconnect; never dispatch them again.
        for item in self.store.rows():
            if item['state'] not in ('preparing','submitted','unconfirmed') or not item.get('operation'):continue
            record=self.client.generation.record(item['operation'])
            if record and record['state'] in ('complete','failed'):
                if self.current and self.current['id']==item['id']:continue
                self.store.update(item['id'],state=record['state'],prompt_id=record.get('prompt_id'),error=record.get('error',''),outputs=record.get('outputs',{}))
                if record['state']=='complete':self.store.set_control(item['owner'],last_prompt=record.get('prompt_id'),last_completed=time.time())
        self.pump();self.bridge.poll()

    def pause(self, owner):
        self.store.set_control(owner,paused=True);self.notify('已暫停後續派送與補入；當前工作繼續。')

    def stop_feed(self, owner):
        self.store.set_control(owner,paused=True,feed=None,credits=0)
        self.notify('已停止尚未加入的圖片供應；已保存及正在生成的項目保留。')

    def resume(self, owner):
        owner=self.store.scoped(owner)
        if owner.startswith('live:'):
            self.notify('普通執行已改為直接提交；請按執行開始新工作，舊紀錄不會補跑。');return
        workflow=self.store.control(owner).get('route',{}).get('workflow')
        if any(r['state'] in ('failed','unconfirmed','preparing') for r in self.store.rows(owner)):
            self.notify('請先處理失敗或未確認項目，再繼續。');return
        active=self.store.control(owner).get('active')
        if active:
            record=self.client.generation.record(active['operation'])
            if not record or record['state'] in ('submitting','unconfirmed'):
                self.notify('上一項提交結果仍待核對；確認後才能繼續，不會自動重送。');return
        self.store.set_control(owner,paused=False);self.notify('已繼續。');self.fill(owner);self.pump()

    def detach(self, owner):
        self.store.set_control(owner,paused=True)

    def has_work(self):
        if self.window.state.get('multi_output',{}).get('version',0)>=7:return bool(self.chain.runs(True) or self.chain.applying or self.chain.apply_queue)
        return bool(self.chain.current() or self.direct.busy() or self.current and self.current['route']['workspace']==self.window.state['workspace'] or
                    any(r['route']['workspace']==self.window.state['workspace'] for r in self.store.rows()) or
                    any(c.get('credits',0) or c.get('active') for _,c in self.local_controls()))

    def local_controls(self):
        return [(owner,c) for owner,c in self.controls() if c.get('route',{}).get('workspace')==self.window.state['workspace']
                and c['route'].get('server')==self.client.url]

    def workspace_changed(self,previous):
        self.chain.workspace_changed(previous)
        self.direct.workspace_changed(previous)
        for owner,c in self.controls():
            if c.get('route',{}).get('workspace')==previous:self.store.set_control(owner,paused=True)

    def source_active(self,key):
        if any(item.get('source',{}).get('key')==key for item in
               [*self.direct.submitted.values(),*self.direct.pending,*([self.direct.current] if self.direct.current else [])] if item.get('source')):return True
        for owner,c in self.controls():
            if c.get('route',{}).get('workspace')!=self.window.state['workspace']:continue
            source=c.get('feed') or c.get('cursor')
            if not source or source['key']!=key:continue
            if c.get('active') or c.get('credits',0) or c.get('feed') and source['next']<source['total'] or self.store.rows(owner) or self.current and self.current['owner']==owner:return True
        return False

    def cancel(self, owner=None):
        if self.window.state.get('multi_output',{}).get('version',0)>=7:return self.chain.cancel_current()
        from .chain_model import enabled
        if owner is None and enabled(self.window.state) and self.chain.current():
            return self.chain.cancel(self.chain.current()['id'])
        owner=self.store.scoped(owner)
        for key,_ in self.local_controls():
            if owner is None or key==owner:self.store.set_control(key,paused=True)
        if self.direct.cancel(owner):return
        current=self.current if self.current and self.current['route']['workspace']==self.window.state['workspace'] else None
        if current is None:
            current=next((r for r in self.store.rows(owner) if r.get('operation') and r['state'] in ('preparing','submitted','unconfirmed')
                           and r['route']['server']==self.client.url and r['route']['workspace']==self.window.state['workspace']),None)
            if current is None:
                current=next((dict(owner=key,live=True,operation=c['active']['operation']) for key,c in self.local_controls()
                              if (owner is None or owner==key) and c.get('active') and c['route']['server']==self.client.url),None)
        if current and (owner is None or owner==current['owner']):
            token=current.get('token')
            record=self.client.generation.record(current['operation'])
            if record is None:
                self.fail('已取消尚未提交的工作。',confirmed=True);return
            def cancelled(result):
                if token and not self.valid(token):return
                if result.get('state')=='abandoned':
                    self.client.generation.retired(current['operation'],result)
                elif result.get('state')=='failed':
                    job=self.client.generation.record(current['operation'])
                    if job:
                        job.update(state='failed',error='使用者取消');self.client.generation.save(job)
                        self.client.generation.jobs.get(job['id'],{}).update(state='failed')
                        self.client.generation.jobs.pop(job['id'],None)
                        self.client.generation.native_waiting.discard(job['id'])
                    if token:self.fail('已取消這項工作。',confirmed=True)
                    elif current.get('live'):self.store.set_control(current['owner'],active=None,last_error='使用者取消')
                    else:self.store.update(current['id'],state='failed',error='使用者取消')
                    self.notify('已取消這項工作；後續仍暫停保留。')
                elif result.get('state')=='settled':
                    self.client.generation.recheck(current['operation'])
                    self.notify('工作已離開 ComfyUI 佇列，正在核對完成紀錄；後續保留並暫停。')
                else:self.notify('已要求取消指定工作；後續項目已暫停保留。')
            self.client.request('workflow/native/cancel',dict(id=current['operation']),
                done=cancelled,failed=self.window.notice)
        else:self.notify('已暫停後續項目，目前沒有本次 PCS 提交中的工作。')

    def disconnected(self):
        self.chain.disconnected()
        self.direct.disconnected()
        for owner,_ in self.controls():self.store.set_control(owner,paused=True)
        self.current=None
        self.notify('連線中斷；等待內容已保留，重新連線核對後可繼續。')
