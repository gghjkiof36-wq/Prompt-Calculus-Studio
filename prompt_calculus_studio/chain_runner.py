"""Linear orchestration over the existing native journal and history observer."""
import copy
from urllib.parse import quote
from PySide6.QtCore import QTimer
from .chain_model import definition,enabled,prepare_plan,output_indices
from .chain_store import ChainStore


class ChainRunner:
    def __init__(self,flow):
        self.flow=flow;self.client=flow.client;self.window=flow.window
        self.store=ChainStore(self.window.store.db);self.preparing=None;self.collecting=set();self.pumping=False;self.epoch=0

    def current(self):
        return next((r for r in self.store.runs(self.window.state['workspace']) if r['status'] not in ('complete','cancelled')),None)

    def notify(self,message=''):
        if message:self.window.notice(message)
        self.client.stateChanged.emit()

    def start(self,count):
        if self.current():self.notify('這個工作區已有串接；請從串接模塊或任務紀錄查看／繼續。');return
        if not self.client.connected:raise ValueError('請先連線至 ComfyUI。')
        if type(count) is not int or not 1<=count<=100:raise ValueError('串接輪次須為 1–100。')
        plan=prepare_plan(self.window.state);entries=[dict(root={},ancestors={})]
        source=plan.get('source');scheduler=plan.get('scheduler');claimed=[]
        if source:
            if count!=1:raise ValueError('圖片清單每張執行一次完整串接，輪次請設為 1。')
            value=self.window.state.get('canvas_functions',{}).get('images',{}).get(source,{})
            sources=value.get('items') or ([value['source']] if value.get('source') else [])
            if not sources:raise ValueError('入口圖片清單沒有圖片。')
            entries=[dict(root=dict(source=copy.deepcopy(s),index=i),ancestors={}) for i,s in enumerate(sources)]
        if scheduler:
            owner=self.flow.owner(scheduler,plan['stages'][0]['workflow'])
            rows=self.flow.store.rows(owner)
            if any(r['state']!='waiting' for r in rows):raise ValueError('入口預排程已有正在處理的項目。')
            if rows:
                if source or count!=1:raise ValueError('已有預排程項目時使用該清單；請設一輪且不另加圖片清單。')
                entries=[dict(root=dict(inputs=r['inputs'],queue_item=r['id']),ancestors={}) for r in rows];claimed=rows
        # Validate every mapping before the first submission, without freezing
        # native parameters or later unscheduled PCS text at chain start.
        self.validate_inputs(plan)
        run=self.store.create(self.window.state['workspace'],self.client.url,plan,count,entries,claimed)
        if scheduler:self.flow.store.set_control(owner,paused=True,feed=None,chain_run=run['id'])
        self.notify('已開始串接：'+' → '.join(s['name'] for s in plan['stages']));self.pump()

    def validate_inputs(self,plan):
        from .flow_data import resolve,channel
        from .composition_image import freeze_image_source
        state=self.flow.context()
        for stage in plan['stages']:
            for m in stage['texts']:
                if m['mode']=='pcs':resolve(state,m['source'],'clip')
            for m in stage['images']:
                key=m['source']
                if key in state['multi_output']['canvases']:freeze_image_source(state,self.window.store.directory,key)
                else:resolve(state,key,'image')

    def fill(self,run):
        """At most ten first-stage input captures, not the whole image folder."""
        scheduler=run['plan'].get('scheduler')
        if not scheduler or run['stage']!=0:return
        from .chain_context import apply_root
        items=[i for i in self.store.items(run['id'],run['round'],0) if i['status']!='complete'][:10]
        for item in items:
            root=item['entry'].get('root',{})
            if 'inputs' in root:continue
            state=self.flow.context();apply_root(state,run['plan'],root,self.window.store.directory)
            root['inputs']=self.flow.capture(state,scheduler)
            self.store.update_item(item['id'],entry=dict(item['entry'],root=root))

    def pause(self,ident,message='已暫停後續；目前工作繼續收結果。'):
        run=self.store.read('chain_runs',ident)
        if run and run['status'] not in ('complete','cancelled'):self.store.update_run(ident,status='paused',message=message)
        # Stop an upload still before the native submission journal. A sent
        # operation instead continues to reconcile its own receipt.
        if self.preparing:
            item=next((i for i in self.store.items(ident) if i.get('attempt')==self.preparing),None)
            if item and not self.client.generation.record(self.preparing):
                self.store.update_item(item['id'],status='waiting');self.preparing=None
        self.notify(message)

    def resume(self,ident):
        run=self.store.read('chain_runs',ident)
        if not run or run['status'] in ('complete','cancelled'):return
        self.require_scope(run)
        items=self.store.items(ident)
        if any(i['status'] in ('failed','unconfirmed','result_error') for i in items):raise ValueError('請先處理該階段的失敗項目；成功項目會保留。')
        self.store.update_run(ident,status='running',message='');self.observe({});self.pump();self.notify('串接已繼續。')

    def require_scope(self,run):
        if not self.client.connected or run['server']!=self.client.url or run['workspace']!=self.window.state['workspace']:
            raise ValueError('請回到原工作區並連線至原 ComfyUI 服務。')
        plan=definition(self.window.state)
        if not enabled(self.window.state) or not plan or plan['id']!=run['plan']['id']:
            raise ValueError('串接設定已移除或停用；執行紀錄保留，請恢復設定後繼續。')
        current=prepare_plan(self.window.state)
        # Explicit re-preparation may accept an edited native graph, while the
        # chain's workflow/ports/dependencies remain the original route.
        def routing(plan):
            value=copy.deepcopy(plan)
            for stage in value['stages']:stage.pop('topology',None)
            return value
        if routing(current)!=routing(run['plan']):raise ValueError('串接路由已修改；請恢復本次設定，或取消後建立新的執行。')

    def owns(self,owner):
        return any(r['status'] not in ('complete','cancelled') and r['plan'].get('scheduler') and
                   owner=='schedule:'+r['workspace']+':'+r['plan']['scheduler'] for r in self.store.runs())

    def pump(self):
        if self.pumping or self.preparing or not self.client.connected:return
        self.pumping=True
        try:
            run=self.current()
            if not run or run['status']!='running':return
            try:self.require_scope(run)
            except ValueError as exc:self.pause(run['id'],str(exc));return
            try:self.fill(run)
            except (ValueError,OSError) as exc:self.pause(run['id'],str(exc));return
            items=self.store.items(run['id'],run['round'],run['stage'])
            if any(i['status']!='complete' for i in items):
                item=next((i for i in items if i['status']!='complete'),None)
                if item and item['status']=='waiting' and not self.flow.direct.current and not (self.flow.current and not self.flow.current.get('waiting')):
                    self.dispatch(run,item)
                return
            if run['stage']+1<len(run['plan']['stages']):
                choice=run['plan']['stages'][run['stage']+1]['upstream'];entries=[]
                for item in items:
                    results=self.store.results(item['id'])
                    try:indices=output_indices(len(results),choice)
                    except ValueError as exc:self.pause(run['id'],str(exc));return
                    for index in indices:
                        result=results[index];ancestors=copy.deepcopy(item['entry'].get('ancestors',{}))
                        ancestors[run['plan']['stages'][run['stage']]['id']]=item['attempt']
                        entries.append(dict(source=result['source'],parent=result['id'],ancestors=ancestors,root=item['entry'].get('root',{})))
                self.store.expand(run,entries)
            elif run['round']<run['rounds']:self.store.update_run(run['id'],round=run['round']+1,stage=0)
            else:self.store.update_run(run['id'],status='complete',message='所有階段完成。');self.notify('串接已完成。')
            QTimer.singleShot(0,self.pump)
        finally:self.pumping=False

    def dispatch(self,run,item):
        from .chain_context import context,marker
        from .native_submission import prepare
        try:
            prepared=item.get('retry_prepared') or context(self,run,item)
            attempt=self.store.attempt(item,prepared);self.preparing=attempt
            def valid():return self.preparing==attempt and run['server']==self.client.url and self.client.connected
            def fail(error):
                if not valid():return
                self.preparing=None;record=self.client.generation.record(attempt)
                self.store.update_item(item['id'],status='unconfirmed' if record and record['state']=='unconfirmed' else 'failed',error=str(error))
                self.pause(run['id'],run['plan']['stages'][item['stage']]['name']+'：'+str(error))
            def submit(snapshot,images):
                if not valid():return
                snapshot['chain']=marker(run,item,attempt)
                if prepared.get('replay'):snapshot['chain_replay']=prepared['replay']
                def queued(job):
                    if not valid():return
                    self.preparing=None
                    self.store.update_item(item['id'],status='submitted',prompt_id=job['prompt_id'])
                    self.notify();QTimer.singleShot(0,self.flow.direct.pump)
                self.client.generation.submit_native(snapshot,run['plan']['stages'][item['stage']]['workflow'],queued,fail,valid,images=images,ident=attempt)
            prepare(self.client,prepared['state'],run['plan']['stages'][item['stage']]['workflow'],valid,submit,fail,image_values=prepared['images'])
        except (ValueError,OSError) as exc:
            self.preparing=None;self.store.update_item(item['id'],status='failed',error=str(exc));self.pause(run['id'],str(exc))

    def observe(self,status):
        for run in self.store.runs():
            if run['server']!=self.client.url:continue
            for item in self.store.items(run['id']):
                if item['status'] not in ('preparing','submitted','unconfirmed','collecting'):continue
                job=self.client.generation.record(item.get('attempt'))
                if not job:
                    if self.preparing!=item.get('attempt'):
                        self.store.update_item(item['id'],status='failed',error='準備時中斷，未找到已送出的任務。');self.pause(run['id'],'準備時中斷，請重試該項。')
                    continue
                if job['state']=='complete':self.collect(run,item,job)
                elif job['state']=='failed':
                    self.store.update_item(item['id'],status='failed',error=job.get('error','生成失敗。'));self.pause(run['id'],job.get('error','生成失敗。'))
                elif job['state']=='unconfirmed':
                    self.store.update_item(item['id'],status='unconfirmed',error=job.get('error','提交未確認。'));self.pause(run['id'],'此項提交未確認；只暫停這條串接，沒有自動重送。')
                    self.client.generation.jobs[job['id']]=job
                else:self.client.generation.jobs.setdefault(job['id'],job)
                if job['state'] in ('queued','running') and item['status'] in ('preparing','unconfirmed'):
                    self.store.update_item(item['id'],status='submitted',prompt_id=job['prompt_id'],error='')
                    if self.preparing==item.get('attempt'):self.preparing=None
        self.pump()

    def collect(self,run,item,job):
        if item['id'] in self.collecting:return
        node=run['plan']['stages'][item['stage']]['output'];expected=job.get('outputs',{}).get(node,{}).get('images',[])
        if not expected:
            self.store.update_item(item['id'],status='result_error',error='階段已結束但所選輸出無結果。');self.pause(run['id'],'階段已結束但所選輸出無結果。');return
        self.collecting.add(item['id']);self.store.update_item(item['id'],status='collecting');epoch=self.epoch
        def valid():return self.epoch==epoch and self.client.connected and self.client.url==run['server']
        def fail(error):
            if not valid():return
            self.collecting.discard(item['id']);self.store.update_item(item['id'],status='result_error',error=str(error))
            self.pause(run['id'],'圖片尚未保存：'+str(error)+'；重取結果不會重新生成。')
        def received(rows):
            if not valid():return
            try:
                from .image_bindings import import_source
                existing={r['index'] for r in self.store.results(item['id'])}
                for index,image in enumerate(expected):
                    if index in existing:continue
                    matches=[r for r in rows if r.get('prompt_id')==job['prompt_id'] and str(r.get('node_id'))==node and r.get('image')==image and r.get('path')]
                    if not matches:raise ValueError('指定任務的節點 #'+node+' 第 '+str(index+1)+' 張已無法讀取。')
                    source=import_source(matches[0]['path'],self.window.store.directory)
                    self.store.save_result(item['id'],node,index,source,dict(server=run['server'],run=run['id'],round=item['round'],
                        stage=run['plan']['stages'][item['stage']]['id'],attempt=item['attempt'],prompt_id=job['prompt_id'],node=node,index=index,image=image))
                self.collecting.discard(item['id']);self.store.update_item(item['id'],status='complete',error='')
                original=item['entry'].get('root',{}).get('queue_item')
                if original and item['stage']==0:self.flow.store.update(original,state='complete',operation=item['attempt'])
                self.notify();QTimer.singleShot(0,self.pump)
            except (ValueError,OSError) as exc:fail(exc)
        self.client.request('desktop/results?prompt_id='+quote(job['prompt_id'],safe=''),done=received,failed=fail)

    def retry(self,ident,reprepare=False,allow_unknown=False):
        item=self.store.read('chain_items',ident);run=self.store.read('chain_runs',item['run']);self.require_scope(run)
        if run['status'] in ('complete','cancelled'):raise ValueError('此流程已結束，紀錄保留；請明確建立新的串接。')
        if item['status']=='result_error':
            self.store.update_item(ident,status='collecting');self.collect(run,item,self.client.generation.record(item['attempt']));return
        if item['status'] not in ('failed','unconfirmed'):raise ValueError('這項工作不是可重試的失敗項目。')
        if item['status']=='unconfirmed' and not allow_unknown:raise ValueError('此項可能已生成，須明確選擇建立新嘗試。')
        previous=self.store.read('chain_attempts',item.get('attempt'));prepared=None
        if previous and not reprepare:
            prepared=previous['prepared'];job=self.client.generation.record(previous['id'])
            if job and job.get('payload',{}).get('prompt'):
                prepared['replay']=copy.deepcopy(job['payload']['prompt'])
                for binding in prepared['state']['multi_output']['bindings']:
                    if binding['workflow']!=run['plan']['stages'][item['stage']]['workflow']:continue
                    clip=binding['clip'];out=next(c['source'] for c in prepared['state']['multi_output']['connections'] if c['destination']==clip and c['kind']=='clip')
                    prepared['state']['multi_output']['outputs'][out]['draft']=prepared['replay'][binding['node']]['inputs'][binding['field']]
                    prepared['state']['multi_output']['clip_inputs'][clip]['text_source']='pcs'
        self.store.update_item(ident,status='waiting',error='',retry_prepared=prepared)
        self.store.update_run(run['id'],status='running',message='');self.pump()

    def cancel(self,ident):
        run=self.store.read('chain_runs',ident)
        self.store.update_run(ident,status='cancelled',message='已停止後續；成功結果及嘗試紀錄保留。')
        for item in self.store.items(ident):
            if item['status'] in ('preparing','submitted','unconfirmed') and item.get('attempt'):
                self.client.request('workflow/native/cancel',dict(id=item['attempt']),
                    done=lambda r:self.notify('已停止後續；目前項目依後端回覆結束。'),failed=lambda e:self.notify('已停止後續；目前項目仍在結束：'+str(e)))
        if self.preparing and any(i.get('attempt')==self.preparing for i in self.store.items(ident)):self.preparing=None
        for row in self.flow.store.rows(history=True):
            if row.get('chain_run')==ident and row['state']=='chain':self.flow.store.update(row['id'],state='removed',error='所屬串接已取消，輸入紀錄保留。')
        self.notify('已取消這次串接的後續派送。')
        QTimer.singleShot(0,self.flow.direct.pump)

    def disconnected(self):
        self.preparing=None;self.collecting.clear();self.epoch+=1
        for run in self.store.runs():
            if run['status']=='running':self.pause(run['id'],'連線中斷；已提交工作保留，恢復後請明確繼續。')

    def workspace_changed(self,previous):
        for run in self.store.runs(previous):
            if run['status']=='running':self.pause(run['id'],'已離開原工作區，後續階段暫停。')

    def progress(self):
        run=self.current()
        if not run:return ''
        items=self.store.items(run['id'],run['round'],run['stage']);done=sum(i['status']=='complete' for i in items)
        return f"第 {run['round']}／{run['rounds']} 輪 · {run['plan']['stages'][run['stage']]['name']} · {done}／{len(items)} 項"+(' · 已暫停' if run['status']=='paused' else '')
