"""Stage interpreter using the existing native submission/completion journal.

Frames and queue rows survive restarts. Only the active parent's child scope is
expanded; results leave a scope after all its entries have finished.
"""
import copy
import hashlib
import json
import time
import uuid
from urllib.parse import quote
from PySide6.QtCore import QObject,QTimer
from .stage_store import StageStore
from .stage_model import compile_plan,execution_signature,terminals,controls,input_nodes as upstream
from .stage_context import capture,project,image_list,merge_saved,prepare_state
from .flow_data import incoming


def merge_results(target,values):
    for key,value in values.items():
        if key not in target:target[key]=copy.deepcopy(value)
        else:
            target[key]['images'].extend(copy.deepcopy(value.get('images',[])))
            target[key]['attempts'].extend(value.get('attempts',[]))
            target[key].setdefault('records',[]).extend(copy.deepcopy(value.get('records',[])))
            target[key]['text']=value.get('text')
            target[key]['texts']=copy.deepcopy(value.get('texts',[]))


class StageRunner(QObject):
    def __init__(self,flow):
        super().__init__(flow.client);self.flow=flow;self.client=flow.client;self.window=flow.window
        self.store=StageStore(self.window.store.db);self.closed=False;self.pumping=False;self.preparing=None;self.collecting=set();self.apply_queue=[];self.applying=None
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.pump)
        self.results={};self.load_results()
        self.apply_abort=None;self.apply_owner=None;self.apply_operation=None;self.apply_cancellations=set()

    def later(self):
        if not self.closed:self.timer.start(0)

    def close(self):
        self.cancel_apply('視窗已關閉，未繼續套用輸入。')
        self.closed=True;self.timer.stop();self.apply_queue.clear()

    def notify(self,message=''):
        if self.closed:return
        if message:self.window.notice(message)
        self.client.stateChanged.emit()

    def runs(self,active=False):
        if self.closed:return []
        return [r for r in self.store.rows('run',self.window.state['workspace']) if not active or r['status'] not in ('complete','cancelled')]

    def current(self,plan=None):
        try:plan=plan or compile_plan(self.window.state)
        except ValueError:return None
        signature=execution_signature(plan)
        return next((r for r in self.runs(True) if r['server']==self.client.url and execution_signature(r['plan'])==signature),None)

    def scheduler_keys(self,plan):
        return set(plan['scopes'])|{s['scheduler'] for s in plan['stages'].values() if s['scheduler']}

    def route_changed(self):
        from .stage_free import route_changed
        for run in self.runs(True):
            route_changed(self,run)
            try:self.check_route(run)
            except ValueError:
                if run.get('pause_reason')!='route':self.pause(run['id'],'接線已修改；已提交項目繼續收取，後續保留並暫停。',reason='route')
                self.cancel_inputs(run)
        self.later()
    def owns(self,owner):return False

    def context(self,results=None,sources=None,readers=None):
        return project(self.window.state,self.window.store.directory,results,sources,readers)

    def scope_keys(self,plan,node):
        return plan['scopes'][node['key']]['stages'] if node['kind']=='scope' else [node['stage']]

    def capture_scope(self,plan,node,state=None):
        state=state or self.context();keys=self.scope_keys(plan,node)
        policies=plan['scopes'][node['key']]['policies'] if node['kind']=='scope' else {b['key']:'live' for k in keys for b in plan['stages'][k]['bindings']}
        saved=capture(state,plan,keys,self.window.store.directory,policies)
        if node['kind']=='scope':
            from .workspace_scene import scene
            saved['scene']=scene(state)
        return saved

    def start(self,count=1):
        if not self.client.connected:raise ValueError('請先連線至 ComfyUI。')
        if type(count) is not int or not 1<=count<=100:raise ValueError('次數須為 1–100。')
        plan=compile_plan(self.window.state)
        if not plan['order']:
            self.sync_free(all_inputs=True);return
        self.route_changed()
        for previous in self.runs(True):
            if previous['status']=='paused':self.settle(previous)
        active=self.current(plan);scheduling=any(n['kind']!='stage' for n in plan['tree'])
        if active and scheduling:
            candidates=[f for f in active['stack'] if f['kind']=='loop' and not f.get('auto')]
            frame=next(iter(candidates),None)
            if frame:
                self.check_route(active)
                saved=self.capture_scope(plan,frame['node'])
                rows=[r for r in self.store.rows('entry',self.window.state['workspace'],active=True) if r['scheduler']==frame['node']['key']]
                if len(rows)+count>10:raise ValueError('預排程最多十項；這次尚未加入。')
                for _ in range(count):self.add_entry(active,frame['owner'],frame['node'],saved)
                self.notify('已保存 '+str(count)+' 項至 '+self.window.state['multi_output']['schedulers'][frame['node']['key']]['name']);self.later();return
            if any(f.get('auto') for f in active['stack']):self.notify('這批圖片已在供應，不會重建同一批清單。');return
            # A scope already left is not re-entered in the same execution.
            self.create_run(plan,count,status='waiting');self.notify('已保存為下一次完整流程。');return
        if active and len(plan['order'])>1:
            self.sync_free();self.notify('這條流程尚未結束；目前點擊不追加相同流程。');return
        if len(plan['order'])==1 and not scheduling:
            for _ in range(count):self.create_run(plan,1,direct=True)
        else:self.create_run(plan,count)
        self.sync_free();self.later()

    def create_run(self,plan,count,direct=False,status='running'):
        state=self.context();initial={}
        paused=any(self.store.scheduler_paused(state['workspace'],key) for key in self.scheduler_keys(plan))
        if paused and status=='running':status='paused'
        def first(nodes):
            for node in nodes:
                if node['kind'] in ('scope','data'):
                    initial[node['key']]=[self.capture_scope(plan,node,state) for _ in range(count if node['kind']=='scope' or not node.get('auto') else 1)]
        first(plan['tree'])
        pending=self.store.rows('entry',state['workspace'],active=True)
        if any(len(items)+sum(r['scheduler']==key for r in pending)>10 for key,items in initial.items()):
            raise ValueError('預排程最多十項，這次尚未加入。')
        saved=capture(state,plan,plan['order'],self.window.store.directory) if direct else {}
        from .stage_parameters import capture as capture_parameters
        saved['parameters']=capture_parameters(state,plan['order'])
        ctx=dict(results={},saved=saved,sources={})
        run=self.store.add('run',state['workspace'],server=self.client.url,plan=copy.deepcopy(plan),status=status,
            direct=direct,round=1,rounds=1 if initial or direct else count,initial=initial,parameters=copy.deepcopy(saved['parameters']),
            stack=[dict(kind='seq',nodes=plan['tree'],index=0,ctx=ctx,produced={})],message='已暫停後續派送。' if paused else '',pause_reason='manual' if paused else '',revision=uuid.uuid4().hex)
        self.capture_free(run)
        # Show saved outer items immediately, even while an earlier Stage runs.
        # Future Stage results remain references in these entries.
        for node in plan['tree']:
            if node['kind']=='stage' or node.get('auto'):continue
            owner=run['id']+':initial:'+node['key']
            for value in run['initial'][node['key']]:self.add_entry(run,owner,node,value)
            run['initial'][node['key']]=dict(owner=owner)
        self.save(run)
        return run

    def add_entry(self,run,owner,node,saved,source=None,parent=None):
        keys=self.scope_keys(run['plan'],node)
        return self.store.add('entry',run['workspace'],owner,status='waiting',run=run['id'],scheduler=node['key'],stages=keys,
            label=' → '.join(run['plan']['stages'][k]['name'] for k in keys),saved=saved,source=source,parent=parent,error='')

    def check_route(self,run):
        if run['workspace']!=self.window.state['workspace'] or run['server']!=self.client.url:raise ValueError('已切換工作區或 ComfyUI，後續保留並暫停。')
        current=compile_plan(self.window.state)
        if execution_signature(current)!=execution_signature(run['plan']):raise ValueError('Stage 或接線已修改；原項目保留，恢復接線後可繼續。')

    def save(self,run):self.store.update(run['id'],stack=run['stack'],initial=run['initial'],round=run['round'],source_positions=run.get('source_positions',{}))

    def pump(self):
        if self.closed or self.pumping or not self.client.connected:return
        self.pumping=True
        try:
            if self.applying:return
            if self.apply_queue:self.apply_next();return
            if self.preparing:return
            for run in self.runs(True):
                if run['status']!='running':
                    self.settle(run);continue
                try:
                    self.check_route(run)
                    self.step(run)
                except (ValueError,OSError) as exc:self.pause(run['id'],str(exc),reason='error')
                if self.applying or self.preparing:return
        finally:self.pumping=False

    def complete(self,run):
        if run['status']=='running':
            if not self.finish_free(run):return
        elif run.get('free_inputs'):
            self.store.update(run['id'],free_state='retained')
        current=self.store.read(run['id'])
        message='生成完成；部分未接 Stage 輸入同步失敗：'+current['free_error'] if current.get('free_error') else '流程完成。'
        self.store.update(run['id'],status='complete',stack=[],message=message);self.notify(message)
        signature=execution_signature(run['plan'])
        future=next((r for r in self.runs(True) if r['status']=='waiting' and r['workspace']==run['workspace'] and r['server']==run['server'] and execution_signature(r['plan'])==signature),None)
        if future:
            paused=any(self.store.scheduler_paused(future['workspace'],key) for key in self.scheduler_keys(future['plan']))
            self.store.update(future['id'],status='paused' if paused else 'running',pause_reason='manual' if paused else '')
            self.later()

    def settle(self,run):
        """Consume finished frames without preparing, supplying or submitting work."""
        if run['status'] in ('cancelled','complete','waiting'):return
        for _ in range(80):
            if not run['stack']:
                if run['round']>=run['rounds']:self.complete(run)
                return
            frame=run['stack'][-1];kind=frame['kind']
            if kind=='stage':
                if frame.get('job') or 'variants' not in frame or frame['index']<len(frame['variants']):return
            elif kind=='seq':
                if frame['index']<len(frame['nodes']):return
            else:
                if not frame.get('initialized') or frame.get('active'):return
                if any(e['status'] not in ('complete','cancelled','removed') for e in self.store.rows('entry',owner=frame['owner'])):return
                feed=frame.get('feed')
                if feed and feed['next']<feed['total']:return
            self.pop(run,frame['produced'])

    def step(self,run):
        # Bound synchronous transitions; callbacks resume through an owned timer.
        for _ in range(80):
            stack=run['stack']
            if not stack:
                if run['round']<run['rounds']:
                    run['round']+=1;stack.append(dict(kind='seq',nodes=run['plan']['tree'],index=0,ctx=dict(results={},saved=dict(parameters=copy.deepcopy(run.get('parameters',{}))),sources={}),produced={}))
                else:
                    self.complete(run);return
            frame=stack[-1]
            if frame['kind']=='seq':
                if frame['index']>=len(frame['nodes']):self.pop(run,frame['produced']);continue
                node=frame['nodes'][frame['index']];ctx=copy.deepcopy(frame['ctx'])
                if node['kind']=='stage':stack.append(dict(kind='stage',key=node['key'],ctx=ctx,index=0,produced={},job=None))
                else:
                    owner=run['id']+':'+uuid.uuid4().hex
                    stack.append(dict(kind='loop',node=node,ctx=ctx,owner=owner,produced={},active=None,initialized=False,auto=node.get('auto'),feed=None,done=0))
                self.save(run);continue
            if frame['kind']=='loop':
                if not frame['initialized']:self.initialize_loop(run,frame);self.save(run)
                if frame['active']:return
                self.fill(run,frame)
                entries=self.store.rows('entry',owner=frame['owner'],active=True)
                if not entries:
                    self.pop(run,frame['produced']);continue
                item=entries[0]
                if item['status']!='waiting':return
                frame['active']=item['id'];self.store.update(item['id'],status='running')
                ctx=copy.deepcopy(frame['ctx']);ctx['saved']=merge_saved(ctx.get('saved',{}),item['saved'])
                if item['saved'].get('scene'):ctx['scene']=item['saved']['scene']
                if item.get('source'):ctx.setdefault('sources',{})[frame['auto']]=item['source']
                ctx['parent']=item['id']
                stack.append(dict(kind='seq',nodes=frame['node']['children'],index=0,ctx=ctx,produced={}))
                self.save(run);continue
            if frame['kind']=='stage':
                stage=run['plan']['stages'][frame['key']]
                if 'variants' not in frame:frame['variants']=self.variants(run,stage,frame['ctx']);self.save(run)
                if frame['index']>=len(frame['variants']):self.pop(run,frame['produced']);continue
                if frame.get('job'):return
                self.dispatch(run,frame,stage);return
        self.save(run);self.later()

    def entry_status(self,item):
        """Project execution state without changing scope ownership/history."""
        if item['status']!='running':return item['status']
        entries={e['id']:e for e in self.store.rows('entry',item['workspace'])}
        def belongs(attempt):
            parent=attempt.get('parent');seen=set()
            while parent and parent not in seen:
                if parent==item['id']:return True
                seen.add(parent);parent=entries.get(parent,{}).get('parent')
            return False
        attempts=[a for a in self.store.rows('attempt',item['workspace']) if belongs(a)]
        if attempts:
            attempt=attempts[-1];status=attempt['status']
            if status in ('unconfirmed','failed','result_error','preparing','collecting'):return status
            if status=='submitted':return 'submitted'
        run=self.store.read(item['run'])
        return 'paused' if run and run['status']=='paused' else item['status']

    def initialize_loop(self,run,frame):
        node=frame['node'];frame['initialized']=True
        if frame['auto']:
            initial=run['initial'].pop(node['key'],None)
            if initial:frame['ctx']['saved']=merge_saved(frame['ctx'].get('saved',{}),initial[0])
            state=self.context(frame['ctx']['results'],frame['ctx'].get('sources'),frame['ctx'].get('saved',{}).get('readers'))
            saved=frame['ctx'].get('saved',{})
            images=saved.get('batches',{}).get(frame['auto'])
            if images is None:images=image_list(state,frame['auto'])
            if not images:raise ValueError('圖片来源沒有可供應的圖片。')
            frame['feed']=dict(items=images,next=0,total=len(images))
        else:
            saved=run['initial'].pop(node['key'],None)
            if isinstance(saved,dict) and 'owner' in saved:
                frame['owner']=saved['owner'];return
            if saved is None:
                # Nested manual scopes do not count one click twice.
                saved=[{}] if frame['ctx'].get('parent') else [self.capture_scope(run['plan'],node,self.context(frame['ctx']['results'],frame['ctx'].get('sources')))]
            if len(saved)>10:raise ValueError('預排程最多十項，請減少次數。')
            for value in saved:self.add_entry(run,frame['owner'],node,value,parent=frame['ctx'].get('parent'))

    def fill(self,run,frame):
        feed=frame.get('feed')
        if not feed:return
        while sum(r['scheduler']==frame['node']['key'] for r in self.store.rows('entry',run['workspace'],active=True))<10 and feed['next']<feed['total']:
            source=feed['items'][feed['next']];sources=dict(frame['ctx'].get('sources',{}));sources[frame['auto']]=source
            base=self.window.state
            if frame['ctx'].get('scene'):
                from .workspace_scene import load
                base=copy.deepcopy(base);load(base,frame['ctx']['scene'])
            state=project(base,self.window.store.directory,frame['ctx']['results'],sources,frame['ctx'].get('saved',{}).get('readers'))
            saved=self.capture_scope(run['plan'],frame['node'],state)
            # A refill resolves one image's data, never recaptures the Stage
            # intentions or task target already owned by the outer item.
            inherited=frame['ctx'].get('saved',{})
            saved['parameters']={key:copy.deepcopy(inherited.get('parameters',{}).get(key)) for key in saved.get('parameters',{})}
            for kind in ('parameter_overrides','parameter_profiles'):
                saved[kind]=copy.deepcopy(inherited.get(kind,{}))
            # This child is exactly one image. The complete feed belongs to the
            # parent frame; retaining it here misrepresents editable child data.
            saved.get('batches',{}).pop(frame['auto'],None)
            # Current per-image inputs override the first-image capture.
            from .flow_data import capture_inputs
            saved['inputs']=capture_inputs(state,frame['node']['key'])
            self.add_entry(run,frame['owner'],frame['node'],saved,source,parent=frame['ctx'].get('parent'))
            feed['next']+=1
        self.save(run)

    def pop(self,run,results):
        run['stack'].pop()
        if run['workspace']==self.window.state['workspace'] and results:
            self.results.update(copy.deepcopy(results));self.refresh_results()
        if run['stack']:
            parent=run['stack'][-1]
            if parent['kind']=='seq':
                parent['ctx']['results'].update(copy.deepcopy(results));merge_results(parent['produced'],results);parent['index']+=1
            elif parent['kind']=='loop':
                merge_results(parent['produced'],results)
                if parent['active']:
                    item=self.store.update(parent['active'],status='complete',results=results)
                    if parent.get('auto') and run['workspace']==self.window.state['workspace']:
                        positions=item['saved'].get('source_positions',{})
                        self.advance_sources(dict(sources={k:v for k,v in positions.items() if k==parent['auto']}))
                parent['active']=None;parent['done']+=1
        elif run['workspace']==self.window.state['workspace']:
            self.advance_sources(dict(sources=run.get('source_positions',{})));run['source_positions']={}
        self.save(run)

    def variants(self,run,stage,ctx):
        state=self.context(ctx['results'],ctx.get('sources'),ctx.get('saved',{}).get('readers'));collections=[]
        for binding in stage['bindings']:
            if binding['kind']!='image' or not binding['source'] or binding['key'] in ctx.get('saved',{}).get('values',{}):continue
            nodes=upstream(state,[binding['source']]);refs=nodes&run['plan']['stages'].keys()
            refs|={state.get('canvas_functions',{}).get('images',{}).get(k,{}).get('stage_reference') for k in nodes}-{None}
            if refs and not any(k in ctx.get('sources',{}) for k in nodes):collections.append((binding['key'],image_list(state,binding['source']),nodes,refs))
        if not collections:return [dict(values={},sources={},results={})]
        sizes={len(v) for k,v,n,r in collections}
        if len(sizes)>1:raise ValueError('多路圖片數量不同；請明確選定對應圖片，不自動配對。')
        variants=[]
        for i in range(next(iter(sizes))):
            variant=dict(values={},sources={},results={})
            for key,items,nodes,refs in collections:
                image=items[i];variant['values'][key]=dict(type='image',value=image,origin={})
                for source in nodes&state.get('canvas_functions',{}).get('images',{}).keys():variant['sources'][source]=image
                for ref in refs:
                    original=ctx['results'].get(ref,{})
                    record=next((r for r in original.get('records',[]) if r['attempt']==image.get('reference',{}).get('attempt')),None)
                    if record:variant['results'][ref]=dict(original,images=[image],text=record['text'],texts=copy.deepcopy(record.get('texts',[])),selected=True)
            variants.append(variant)
        return variants

    def dispatch(self,run,frame,stage):
        from .native_submission import prepare
        ctx=copy.deepcopy(frame['ctx']);variant=frame['variants'][frame['index']]
        ctx['saved']=merge_saved(ctx.get('saved',{}),dict(values=variant['values']))
        ctx.setdefault('sources',{}).update(variant['sources']);ctx['results'].update(variant['results'])
        from .stage_parameters import effective
        parameters=effective(ctx['saved'],stage['id'])
        if parameters is not None:
            stage=copy.deepcopy(stage);stage['workflow']=parameters['identity']['workflow']
        state,images,foreign=prepare_state(self,run,stage,ctx,ctx.get('sources'))
        cfg=state['multi_output']['stages'][stage['id']]
        retry=frame.pop('retry',None)
        if retry:state,images=retry['snapshot_state'],retry['images']
        else:cfg['parameters']=self.store.resolve_parameters(run['workspace'],stage['id'],cfg.get('parameters'))
        attempt=self.store.add('attempt',run['workspace'],run['id'],status='preparing',stage=stage['id'],parent=ctx.get('parent'),
            round=run['round'],position=frame['index'],snapshot_state=state,images=images,error='',sources=ctx.get('saved',{}).get('source_positions',{}),parameter_replay=bool(retry))
        frame['job']=attempt['id'];self.save(run);self.preparing=attempt['id']
        def valid():
            if self.closed or self.preparing!=attempt['id'] or not self.client.connected:return False
            try:self.check_route(run)
            except ValueError as exc:
                self.preparing=None
                if self.client.generation.record(attempt['id']) is None:self.store.update(attempt['id'],status='failed',error='提交前接線已變更；這項尚未生成。')
                self.pause(run['id'],str(exc),reason='route');return False
            return True
        def fail(error):
            if not valid():return
            self.preparing=None;job=self.client.generation.record(attempt['id'])
            self.store.update(attempt['id'],status='unconfirmed' if job and job['state']=='unconfirmed' else 'failed',error=str(error))
            self.pause(run['id'],str(error),reason='error');self.later()
        def submit(snapshot,uploads):
            if not valid():return
            if retry and retry.get('replay'):snapshot['chain_replay']=retry['replay']
            snapshot['chain']=dict(run=run['id'],round=run['round'],stage=stage['id'],item=attempt['id'],attempt=attempt['id'],revision=run['revision'],parent=ctx.get('parent'))
            def queued(job):
                if not valid():return
                self.store.record_parameters(attempt,job)
                self.preparing=None;self.store.update(attempt['id'],status='submitted',prompt_id=job['prompt_id']);self.notify();self.later()
            self.client.generation.submit_native(snapshot,stage['workflow'],queued,fail,valid,images=uploads,ident=attempt['id'])
        def prepared():
            try:prepare(self.client,state,stage['workflow'],valid,submit,fail,image_values=images)
            except (ValueError,OSError) as exc:fail(str(exc))
        if foreign:self.apply_bindings(foreign,prepared,fail,valid=valid,owner=run['id'])
        else:prepared()

    def observe(self,status):
        if self.closed:return
        for attempt in self.store.rows('attempt',active=True):
            job=self.client.generation.record(attempt['id'])
            if job is None:
                if attempt['status']=='preparing' and self.preparing!=attempt['id']:
                    self.store.update(attempt['id'],status='failed',error='提交前中斷，未找到原生要求。')
                continue
            self.store.record_parameters(attempt,job)
            if job['state']=='complete' and attempt['status']!='result_error':self.collect(attempt,job)
            elif job['state'] in ('failed','unconfirmed'):
                if attempt['status']!=job['state']:
                    self.store.update(attempt['id'],status=job['state'],error=job.get('error',''))
                    self.pause(attempt['owner'],job.get('error','這項提交未完成。'),reason='error')
            else:
                if job['state'] in ('queued','running') and job.get('prompt_id') and attempt['status']!='submitted':
                    self.store.update(attempt['id'],status='submitted',prompt_id=job['prompt_id'],error='')
                self.client.generation.jobs.setdefault(job['id'],job)
        self.later()

    def collect(self,attempt,job):
        if attempt['id'] in self.collecting:return
        run=self.store.read(attempt['owner']);stage=run['plan']['stages'][attempt['stage']]
        graph=job.get('payload',{}).get('prompt',{})
        expected=[(str(node),i,image) for node,output in job.get('outputs',{}).items() if str(node) in graph for i,image in enumerate(output.get('images',[]))]
        self.collecting.add(attempt['id'])
        def fail(error):
            if self.closed:return
            self.collecting.discard(attempt['id']);self.store.update(attempt['id'],status='result_error',error=str(error));self.pause(run['id'],'結果尚未取得：'+str(error),reason='error')
        def received(rows):
            if self.closed:return
            try:
                from .image_bindings import import_source
                images=[]
                for node,i,image in expected:
                    match=next((r for r in rows if r.get('prompt_id')==job['prompt_id'] and str(r.get('node_id'))==node and r.get('image')==image and r.get('path')),None)
                    if not match:raise ValueError('第 '+str(i+1)+' 張指定結果無法讀取。')
                    source=import_source(match['path'],self.window.store.directory);source['reference']=dict(run=run['id'],stage=stage['id'],attempt=attempt['id'],prompt_id=job['prompt_id'],node=node,index=i)
                    images.append(source)
                field=stage.get('text_output');text=None
                if field:text=job.get('payload',{}).get('prompt',{}).get(field[0],{}).get('inputs',{}).get(field[1])
                from .generation import text_fields
                graph=job.get('payload',{}).get('prompt',{})
                texts=[dict(node=node,field=field,text=graph[node]['inputs'][field]) for node,field in text_fields(graph)]
                result=dict(run=run['id'],images=images,text=text,texts=texts,attempts=[attempt['id']],records=[dict(attempt=attempt['id'],text=text,texts=texts)])
                self.store.update(attempt['id'],status='complete',result=result,error='');self.collecting.discard(attempt['id'])
                current=self.store.read(run['id'])
                frame=next((f for f in current['stack'] if f.get('job')==attempt['id']),None)
                if frame and current['status']!='cancelled':
                    merge_results(frame['produced'],{stage['id']:result});frame['job']=None;frame['index']+=1
                    autos={s['auto'] for s in current['plan']['stages'].values() if s['auto']}
                    for key,value in attempt.get('sources',{}).items():
                        if key not in autos:current.setdefault('source_positions',{}).setdefault(key,value)
                    self.save(current)
                if current['workspace']==self.window.state['workspace']:
                    self.results[stage['id']]=copy.deepcopy(frame['produced'][stage['id']]) if frame and stage['id'] in frame['produced'] else result
                    self.refresh_results()
                if current['status']=='paused':self.settle(self.store.read(current['id']))
                self.notify();self.later()
            except (ValueError,OSError) as exc:fail(str(exc))
        if expected:self.client.request('desktop/results?prompt_id='+quote(job['prompt_id'],safe=''),done=received,failed=fail)
        else:received([])

    def advance_sources(self,attempt):
        from .image_source import select
        for key,position in attempt.get('sources',{}).items():
            value=self.window.state.get('canvas_functions',{}).get('images',{}).get(key,{})
            if value.get('batch_id')==position.get('batch') and value.get('index')==position.get('index') and value['index']+1<len(value.get('items',[])):
                select(self.window.state,key,value['index']+1,self.window.store.directory)
                self.window.changed('prompt',refresh=False)

    def refresh_results(self):
        canvas=getattr(self.window,'canvas',None)
        if canvas:
            # Update derived source text on the visible Canvas without an edit
            # transaction; the task journal and the user's undo history stay separate.
            from .stage_context import sync_result_view
            changed=sync_result_view(self.window.state,self.window.store.directory,self.results)
            if changed:
                canvas.refresh();self.window.changed('prompt')
            else:canvas.functions.refresh();canvas.results.refresh();canvas.refresh_image_previews()

    def pause(self,ident,message='已暫停後續派送；目前工作繼續。',reason='manual'):
        run=self.store.read(ident)
        if run and run['status'] not in ('cancelled','complete'):
            self.store.update(ident,status='paused',message=message,pause_reason=reason)
            if reason=='manual':
                for key in self.scheduler_keys(run['plan']):self.store.set_scheduler_paused(run['workspace'],key,True)
            if reason=='route':
                for item in self.store.rows('entry',run['workspace'],active=True):
                    if item['run']==ident and item['status']=='waiting':self.store.update(item['id'],status='retained')
        self.notify(message)


    def stop_feed(self,ident,source):
        run=self.store.read(ident)
        if not run or run['status'] in ('cancelled','complete'):return
        for frame in run['stack']:
            if frame.get('auto')==source and frame.get('feed'):
                frame['feed']['next']=frame['feed']['total']
        self.save(run);self.pause(ident,'已停止這批補入；已保存項目保留並暫停。',reason='manual')

    def resume(self,ident):
        run=self.store.read(ident)
        if not run or run['status'] in ('complete','cancelled'):return
        self.check_route(run)
        if any(a['status'] in ('failed','unconfirmed','result_error') for a in self.store.rows('attempt',owner=ident,active=True)):raise ValueError('這份流程有失敗項目，請從紀錄處理該項；其他流程仍可使用。')
        self.store.restore_retained(run)
        for key in self.scheduler_keys(run['plan']):self.store.set_scheduler_paused(run['workspace'],key,False)
        self.store.update(ident,status='running',message='',pause_reason='');self.later();self.notify('已繼續這份流程。')

    def cancel(self,ident=None):
        if ident is None:
            workspace=self.window.state['workspace']
            self.apply_queue=[item for item in self.apply_queue if item[7][1]!=workspace]
            if self.apply_operation and self.apply_operation['workspace']==workspace:self.cancel_apply('已取消這次輸入同步。')
        runs=[self.store.read(ident)] if ident else self.runs(True)
        for run in runs:
            if not run:continue
            self.store.update(run['id'],status='cancelled',message='已取消，保留結果與紀錄。')
            self.cancel_inputs(run)
            for item in self.store.rows('entry',active=True):
                if item['run']==run['id']:
                    # The native attempt continues to reconcile independently.
                    # Ended schedule entries must not occupy the next batch's slots.
                    self.store.update(item['id'],status='retained' if item['status']=='waiting' else 'cancelled')
            for attempt in self.store.rows('attempt',owner=run['id'],active=True):
                if self.client.generation.record(attempt['id']):
                    self.client.request('workflow/native/cancel',dict(id=attempt['id']),done=lambda r:self.notify('已送出指定工作取消要求。'),failed=lambda e:self.notify(str(e)))
                else:self.store.update(attempt['id'],status='cancelled')
                if self.preparing==attempt['id']:self.preparing=None
        self.notify('已取消 PCS 流程；其他來源任務保留。');self.later()

    def retry(self,ident,reprepare=False,allow_unknown=False):
        item=self.store.read(ident);run=self.store.read(item['owner']);self.check_route(run)
        if run['status'] in ('complete','cancelled'):raise ValueError('流程已結束，請明確建立新的執行。')
        if item['status']=='result_error':self.collect(item,self.client.generation.record(item['id']));return
        if item['status']!='failed':raise ValueError('提交結果不明時不可重新生成；請先查看原任務紀錄。')
        frame=next((f for f in run['stack'] if f.get('job')==ident),None)
        if frame:
            frame['job']=None
            if not reprepare:
                frame['retry']=copy.deepcopy(item);job=self.client.generation.record(item['id'])
                if job and job.get('payload',{}).get('prompt'):frame['retry']['replay']=copy.deepcopy(job['payload']['prompt'])
            self.save(run)
        self.store.update(ident,status='retried');self.store.update(run['id'],status='running');self.later()

    def disconnected(self):
        if self.closed:return
        for run in self.runs(True):self.pause(run['id'],'連線中斷；已提交紀錄保留，恢復後請繼續。',reason='disconnect')
        self.cancel_apply('輸入同步連線中斷，未自動重送。')
        for run in self.runs(True):
            if run.get('free_state')=='applying':self.store.update(run['id'],free_state='failed',free_error='輸入同步連線中斷，未自動重送。')
        self.preparing=None;self.applying=None;self.apply_queue.clear();self.collecting.clear()

    def workspace_changed(self,previous):
        self.apply_queue=[item for item in self.apply_queue if item[7][1]!=previous]
        if self.apply_operation and self.apply_operation['workspace']==previous:self.cancel_apply('工作區已切換，未繼續套用輸入。')
        for run in self.store.rows('run',previous):
            if run['status'] in ('running','waiting'):
                self.pause(run['id'],'已切換工作區，後續保留並暫停。',reason='workspace');self.cancel_inputs(run)
        self.results={}

    def load_results(self):
        self.results={};latest={}
        for item in self.store.rows('attempt',self.window.state['workspace']):
            if item['status']!='complete' or not item.get('result'):continue
            result=item['result'];key=item['stage'];stamp=(item['owner'],item['round'])
            if latest.get(key)!=stamp:self.results[key]=copy.deepcopy(result);latest[key]=stamp
            else:merge_results(self.results,{key:result})

    def progress(self):
        run=self.current()
        if not run:return ''
        parts=[]
        for frame in run['stack']:
            if frame['kind']=='loop':
                feed=frame.get('feed');total=feed['total'] if feed else len(self.store.rows('entry',owner=frame['owner']))
                parts.append(('內層' if parts else '外層')+f" {min(frame['done']+1,total)}／{total} "+('張' if feed else '項'))
            if frame['kind']=='stage':parts.append(run['plan']['stages'][frame['key']]['name'])
        return ' · '.join(parts)+('\n已暫停：'+run.get('message','') if run['status']=='paused' else '')

    def expansion(self,scheduler,count=1):
        """Explain only known PCS multipliers; native output batch size is live."""
        try:plan=compile_plan(self.window.state)
        except ValueError:return '最多十個未結束項目'
        keys=plan['scopes'].get(scheduler,{}).get('stages') or [k for k,s in plan['stages'].items() if s['scheduler']==scheduler]
        pieces=[];total=0;uncertain=False
        for key in keys:
            stage=plan['stages'][key];source=stage['auto'];number=1
            if source and scheduler in plan['scopes']:
                try:number=len(image_list(self.context(self.results),source))
                except ValueError:number=None
            elif any((upstream(self.window.state,[b['source']])&plan['stages'].keys()) for b in stage['bindings'] if b['source']):number=None
            if number is None:pieces.append(stage['name']+'：依本輪上游圖片展開');uncertain=True
            else:pieces.append(stage['name']+'：'+str(number)+(' 張' if source else ' 次'));total+=number
        text='；'.join(pieces)
        if not uncertain and total:text+=f'\n{count} 個項目 × 每項 {total} 次生成 = {count*total} 次'
        return text or '接入資料，或以流程引用指定 Stage 範圍。'

    def capture_free(self,run):
        from .stage_free import capture
        capture(self,run)

    def finish_free(self,run):
        from .stage_free import finish
        return finish(self,run)

    def sync_free(self,all_inputs=False):
        state=self.context();data=state['multi_output'];used={key for stage in data.get('stages',{}) for key in controls(state,stage)}
        groups={}
        for key,target in terminals(state).items():
            if key in used and not all_inputs or not target.get('workflow'):continue
            kind='clip' if key in data['clip_inputs'] else 'image';source=incoming(state,key,kind)
            if not source:continue
            # A result-dependent free input belongs to the new run. It is
            # applied after that run ends, never from a previous preview here.
            if upstream(state,[source]) & data.get('stages',{}).keys():continue
            try:value=__import__('prompt_studio.flow_data',fromlist=['resolve']).resolve(state,source,kind)
            except ValueError as exc:self.notify(str(exc));continue
            from .clip_flow import selected_binding
            binding=selected_binding(state,key) if kind=='clip' else target
            if binding:groups.setdefault(target['workflow'],[]).append((dict(key=key,kind=kind,target=binding,text_source='pcs'),value))
        if groups:self.apply_bindings(groups,lambda:self.notify('已同步綁定輸入。'),lambda e:self.notify('同步未完成：'+str(e)))

    def cancel_inputs(self,run):
        if self.apply_owner==run['id']:self.cancel_apply('所屬流程已停止，未繼續套用輸入。')
        self.apply_queue=[item for item in self.apply_queue if item[6]!=run['id']]
        current=self.store.read(run['id'])
        for operation in current.get('input_operations',[]):
            if operation.get('sent') and not operation.get('ended'):
                self.request_input_cancel(dict(operation,server=run['server']))

    def request_input_cancel(self,operation):
        ident=operation['id'];store=self.store
        if ident in self.apply_cancellations:return
        recorded=store.read(ident)
        if recorded and recorded.get('ended') and recorded.get('cancel_state') in ('failed','settled'):return
        # Keep uncertain delivery as evidence; cancellation never replays input.
        if store.read(ident):store.update(ident,status='unconfirmed',cancel_state='requested')
        if self.client.stopped or operation['server']!=self.client.url:return
        self.apply_cancellations.add(ident)
        def finish(result=None,error=None):
            self.apply_cancellations.discard(ident)
            if self.closed or store is not self.store or not store.read(ident):return
            state=(result or {}).get('state','unknown')
            changes=dict(cancel_state=state,cancel_error=str(error or ''),ended=state in ('failed','settled'),
                status='cancelled' if state=='failed' else 'settled' if state=='settled' else 'unconfirmed')
            store.update(ident,**changes)
        self.client.request('workflow/native/cancel',dict(id=ident),done=lambda result:finish(result),failed=lambda error:finish(error=error))

    def cancel_apply(self,message):
        operation=self.apply_operation
        if not operation:return
        if self.apply_abort:self.apply_abort(message)
        if operation['sent'] and not operation['ended']:self.request_input_cancel(operation)

    def apply_bindings(self,groups,done,failed,valid=None,owner=None):
        context=(self.window.store,self.window.state['workspace'],self.client.url,self.client.epoch);batch=uuid.uuid4().hex
        def guard():return context==(self.window.store,self.window.state['workspace'],self.client.url,self.client.epoch) and (valid is None or valid())
        for workflow,bindings in groups.items():self.apply_queue.append((workflow,copy.deepcopy(bindings),done if workflow==next(reversed(groups)) else lambda:None,failed,guard,batch,owner,context))
        self.apply_next()

    def apply_next(self):
        if self.closed or self.applying or not self.apply_queue:return
        from .native_submission import prepare
        from .multi_output import new_output
        from .snapshots import make_snapshot
        workflow,bindings,done,failed,guard,batch,owner,context=self.apply_queue.pop(0)
        if guard is not None and not guard():
            self.apply_queue=[item for item in self.apply_queue if item[5]!=batch]
            failed('輸入接線或所屬流程已變更，未套用延後結果。');self.later();return
        operation=self.store.add('input',context[1],owner or '',workflow=workflow,server=context[2],epoch=context[3],batch=batch,
            sent=False,ended=False,status='preparing')
        ident=operation['id'];self.applying=ident;self.apply_owner=owner;self.apply_operation=operation
        def journal(**changes):
            operation.update(changes);self.store.update(ident,**changes)
            if owner:
                run=self.store.read(owner);operations=run.get('input_operations',[])
                item=next((r for r in operations if r['id']==ident),None)
                if item is None:item=dict(id=ident,workflow=workflow,sent=False,ended=False);operations.append(item)
                item.update(changes);self.store.update(owner,input_operations=operations)
        journal()
        state=copy.deepcopy(self.window.state);data=state['multi_output'];images=[]
        data.update(canvases={},outputs={},clip_inputs={},image_inputs={},bindings=[],connections=[],schedulers={},stages={},current_output=None)
        data['workflow_order']=dict(visible=False,items=[],established=[]);state.update(uses={},output_order=[],draft=None,draft_base='');state.pop('workspace_scenes',None)
        state['canvas_functions']=dict(images={},preview=True,preview_attached=False)
        for index,(binding,value) in enumerate(bindings):
            target=binding['target'];key=str(index)
            if binding['kind']=='image':images.append((target['node'],value['value']));continue
            data['outputs'][key]=dict(new_output('同步文字'),draft=value['value'] if value else '',canvases=[],text_sources=[])
            data['clip_inputs'][key]=dict(name='CLIP 輸入',workflow=workflow,text_source=binding.get('text_source','pcs'))
            data['bindings'].append(dict(workflow=workflow,clip=key,node=target['node'],field=target['field']))
            data['connections'].append(dict(id=key,source=key,destination=key,kind='clip'))
        # Separate output/terminal keys so the graph remains acyclic.
        data['outputs']={'out_'+k:v for k,v in data['outputs'].items()}
        for c in data['connections']:c['source']='out_'+c['source']
        def error(e):
            if not self.closed and self.applying==ident:
                self.applying=None;self.apply_owner=None;self.apply_abort=None;self.apply_operation=None
                self.apply_queue=[item for item in self.apply_queue if item[5]!=batch]
                journal(error=str(e),status='unconfirmed' if operation['sent'] and not operation['ended'] else 'failed');failed(str(e));self.later()
        self.apply_abort=error
        self.notify()
        def valid():
            if self.closed or self.applying!=ident:return False
            if not self.client.connected or not guard():
                self.cancel_apply('輸入接線、工作區或所屬流程已變更，未繼續套用。');return False
            return True
        started=time.monotonic()
        def receive(result):
            if not valid():return
            if result.get('state')=='applied':
                journal(ended=True,status='applied');self.applying=None;self.apply_abort=None;self.apply_owner=None;self.apply_operation=None;done();self.later()
            elif result.get('state') in ('failed','unconfirmed') or time.monotonic()-started>35:
                journal(ended=result.get('state')=='failed');error(result.get('error') or '同步逾時。')
            else:
                timer=QTimer(self);timer.setSingleShot(True)
                timer.timeout.connect(lambda:(self.client.request('workflow/native/status',dict(id=ident),done=receive,failed=error) if valid() else None,timer.deleteLater()));timer.start(300)
        def submit(snapshot,uploads):
            if valid():
                journal(sent=True,status='submitted');self.client.request('workflow/native/apply',dict(id=ident,snapshot=snapshot,workflow=workflow,images=uploads),done=receive,failed=error)
        try:prepare(self.client,state,workflow,valid,submit,error,image_values=images)
        except (ValueError,OSError) as exc:error(str(exc))
