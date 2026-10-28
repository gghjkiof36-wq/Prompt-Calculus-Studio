"""Stage interpreter using the existing native submission/completion journal.

Frames and queue rows survive restarts. Only the active parent's child scope is
expanded; results leave a scope after all its entries have finished.
"""
import copy
import hashlib
import json
import time
import uuid
from contextlib import contextmanager
from urllib.parse import quote
from PySide6.QtCore import QObject,QTimer
from .stage_store import StageStore
from .stage_model import compile_plan,execution_signature,terminals,controls,connected_terminals,input_nodes as upstream
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
        self.results={};self.preview_results={};self.preview_stamps={};self.load_results()
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
        return self.store.open_runs(self.window.state['workspace']) if active else self.store.rows('run',self.window.state['workspace'])

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

    def capture_scope(self,plan,node,state=None,per_round=False):
        state=state or self.context();keys=self.scope_keys(plan,node)
        policies=plan['scopes'][node['key']]['policies'] if node['kind']=='scope' else {b['key']:'live' for k in keys for b in plan['stages'][k]['bindings']}
        saved=capture(state,plan,keys,self.window.store.directory,policies,per_round=per_round)
        if node['kind']=='scope':
            from .workspace_scene import scene
            saved['scene']=scene(state)
        return saved

    def manual_rounds(self,plan,keys,count,state=None):
        from . import prompt_sources as supply
        state=state or self.context()
        origins=[b['source'] for k in keys for b in plan['stages'][k]['bindings'] if b['source']]
        ids=[key for key in supply.connected(state,origins) if supply.source(state,key)]
        for key in keys:
            if not plan['stages'][key]['auto']:
                ids.extend(source for source in plan['stages'][key].get('suppliers',[]) if source not in ids)
        auto={key for k in keys if plan['stages'][k]['auto'] for key in plan['stages'][k].get('suppliers',[])}
        if auto:
            # In mixed routes the direct Stage expands its own actual inputs.
            # Do not pre-consume its independent readers in a discarded outer
            # manual batch before the shared automatic source is reached.
            for k in keys:
                if not plan['stages'][k]['scheduler']:
                    auto.update(supply.connected(state,[b['source'] for b in plan['stages'][k]['bindings'] if b['source']]))
        ids=[key for key in ids if key not in auto]
        if not ids:return [state for _ in range(count)],None
        working=copy.deepcopy(state);states=[];batches=[]
        for _ in range(count):
            frozen=supply.build(working,ids,count=None if supply.automatic(working,ids) else 1)
            for picks in frozen['rounds']:
                current=copy.deepcopy(working);images,_=supply.project(current,frozen,picks)
                states.append(project(current,self.window.store.directory,source_values=images))
            supply.reserve(working,copy.deepcopy(frozen));batches.append(frozen)
        return states,dict(batches=batches)

    def reserve_supply(self,plan,select_next=True,through=None):
        if not plan:return
        from .prompt_sources import reserve
        reserve(self.window.state,plan,select_next=select_next,through=through)
        self.refresh_reserved_images(plan)
        self.window.store.save(self.window.state,commit=not self.store.transaction_depth)
        self.refresh_results()
        self.window.changed('prompt')

    def refresh_reserved_images(self,plan):
        from .composition_image import source_path
        from .image_source import read_content
        plans=plan.get('batches',[plan]);ids={p['id'] for p in plans}
        for image in self.window.state.get('canvas_functions',{}).get('images',{}).values():
            if image.get('supply_reservation',{}).get('plan') not in ids:continue
            image.pop('content',None);image.pop('content_error',None)
            try:image['content']=read_content(source_path(self.window.store.directory,image['source'],verify=True),image.get('prompt_choice'))
            except (ValueError,OSError) as exc:image['content_error']=str(exc)

    @contextmanager
    def accepting(self):
        before=copy.deepcopy(self.window.state) if not self.store.transaction_depth else None
        try:
            with self.store.transaction():yield
        except Exception:
            if before is not None:
                self.window.state.clear();self.window.state.update(before)
            raise

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
                states,supply=self.manual_rounds(plan,self.scope_keys(plan,frame['node']),count)
                rows=[r for r in self.store.rows('entry',self.window.state['workspace'],active=True) if r['scheduler']==frame['node']['key']]
                if len(rows)+len(states)>10:raise ValueError('預排程最多十項；這次尚未加入。')
                saved=[self.capture_scope(plan,frame['node'],state) for state in states]
                with self.accepting():
                    for value in saved:self.add_entry(active,frame['owner'],frame['node'],value)
                    self.reserve_supply(supply)
                self.client.dispatch_allowed
                self.notify('已保存 '+str(len(states))+' 項至 '+self.window.state['multi_output']['schedulers'][frame['node']['key']]['name']);self.later();return
            if any(f.get('auto') for f in active['stack']):self.notify('這批內容已在供應。');return
            # A scope already left is not re-entered in the same execution.
            self.create_run(plan,count,status='waiting');self.notify('已保存為下一次完整流程。');return
        if active and len(plan['order'])>1:
            self.sync_free();self.notify('這條流程尚未結束；目前點擊不追加相同流程。');return
        if len(plan['order'])==1 and not scheduling:
            states,supply=self.manual_rounds(plan,plan['order'],count)
            with self.accepting():
                for state in states:self.create_run(plan,1,direct=True,frozen_state=state)
                self.reserve_supply(supply)
        else:self.create_run(plan,count)
        # The journal and consumed-source cursor must commit before a library
        # registration can persist state or any asynchronous dispatch starts.
        self.client.dispatch_allowed
        self.sync_free();self.later()

    def create_run(self,plan,count,direct=False,status='running',frozen_state=None):
        with self.accepting():
            return self._create_run(plan,count,direct,status,frozen_state)

    def _create_run(self,plan,count,direct=False,status='running',frozen_state=None):
        state=frozen_state if frozen_state is not None else self.context();initial={}
        states,supply=([state],None) if frozen_state is not None else self.manual_rounds(plan,plan['order'],count,state)
        state=states[0]
        paused=any(self.store.scheduler_paused(state['workspace'],key) for key in self.scheduler_keys(plan))
        if paused and status=='running':status='paused'
        def first(nodes):
            for node in nodes:
                if node['kind'] in ('scope','data'):
                    initial[node['key']]=[self.capture_scope(plan,node,current) for current in (states if node['kind']=='scope' or not node.get('auto') else [state])]
        first(plan['tree'])
        # Reject an already exhausted local source before creating a journal
        # run. Future Stage results are checked when their scope is reached.
        from .prompt_sources import build as build_supply
        for node in plan['tree']:
            if node['kind']!='data' or not node.get('auto'):continue
            stage=plan['stages'][node['stage']]
            future=any(upstream(state,[b['source']])&plan['stages'].keys() for b in stage['bindings'] if b['source'])
            if not future:build_supply(state,stage.get('suppliers') or [stage['auto']],count_only=True)
        pending=self.store.rows('entry',state['workspace'],active=True)
        if any(len(items)+sum(r['scheduler']==key for r in pending)>10 for key,items in initial.items()):
            raise ValueError('預排程最多十項，這次尚未加入。')
        saved=capture(state,plan,plan['order'],self.window.store.directory) if direct or (supply and not initial) else {}
        round_saved=[capture(current,plan,plan['order'],self.window.store.directory) for current in states] if supply and not initial and not direct else []
        from .stage_parameters import capture as capture_parameters
        saved['parameters']=capture_parameters(state,plan['order'])
        ctx=dict(results={},saved=saved,sources={})
        from .workspace_scene import scene
        run=self.store.add('run',state['workspace'],server=self.client.url,plan=copy.deepcopy(plan),status=status,frozen_scene=scene(state),round_saved=round_saved,
            direct=direct,round=1,rounds=(count if any(n.get('auto') for n in plan['tree']) else 1) if initial or direct else len(states),initial=initial,parameters=copy.deepcopy(saved['parameters']),
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
        self.reserve_supply(supply)
        return run

    def add_entry(self,run,owner,node,saved,source=None,parent=None):
        keys=self.scope_keys(run['plan'],node)
        return self.store.add('entry',run['workspace'],owner,status='waiting',run=run['id'],scheduler=node['key'],stages=keys,
            label=' → '.join(run['plan']['stages'][k]['name'] for k in keys),saved=saved,source=source,parent=parent,error='')

    def check_route(self,run):
        if run['workspace']!=self.window.state['workspace'] or run['server']!=self.client.url:raise ValueError('已切換工作區或 ComfyUI，後續保留並暫停。')
        current=compile_plan(self.window.state)
        if execution_signature(current)!=execution_signature(run['plan']):raise ValueError('Stage 或接線已修改；原項目保留，恢復接線後可繼續。')

    def save(self,run):self.store.update(run['id'],stack=run['stack'],initial=run['initial'],round=run['round'],source_positions=run.get('source_positions',{}),supply_progress=run.get('supply_progress',{}),supply_images=run.get('supply_images',{}),supply_shared=run.get('supply_shared',{}))

    def pump(self):
        if self.closed or self.pumping or not self.client.connected:return
        if (self.apply_queue or any(r['status']=='running' for r in self.runs(True))) and not self.client.dispatch_allowed:return
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
                    run['round']+=1
                    saved=copy.deepcopy(run['round_saved'][run['round']-1]) if run.get('round_saved') else dict(parameters=copy.deepcopy(run.get('parameters',{})))
                    stack.append(dict(kind='seq',nodes=run['plan']['tree'],index=0,ctx=dict(results={},saved=saved,sources={}),produced={}))
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
                    if frame.get('feed') and frame['feed']['next']<frame['feed']['total']:return
                    self.pop(run,frame['produced']);continue
                item=entries[0]
                if item['status']!='waiting':return
                frame['active']=item['id'];self.store.update(item['id'],status='running')
                ctx=copy.deepcopy(frame['ctx']);ctx['saved']=merge_saved(ctx.get('saved',{}),item['saved'])
                if item['saved'].get('scene'):ctx['scene']=item['saved']['scene']
                if item.get('source'):ctx.setdefault('sources',{})[frame['auto']]=item['source']
                if item.get('supply_images'):ctx.setdefault('sources',{}).update(item['supply_images'])
                ctx['parent']=item['id']
                stack.append(dict(kind='seq',nodes=frame['node']['children'],index=0,ctx=ctx,produced={}))
                self.save(run);continue
            if frame['kind']=='stage':
                stage=run['plan']['stages'][frame['key']]
                if 'variants' not in frame:frame['variants']=self.variants(run,stage,frame['ctx'],frame);self.save(run)
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
            if attempt.get('cancel_requested'):return 'cancelling'
            if status in ('unconfirmed','failed','result_error','preparing','collecting'):return status
            if status=='submitted':return 'submitted'
        run=self.store.read(item['run'])
        return 'paused' if run and run['status']=='paused' else item['status']

    def initialize_loop(self,run,frame):
        node=frame['node'];frame['initialized']=True
        if frame['auto']:
            initial=run['initial'].pop(node['key'],None)
            if initial:frame['ctx']['saved']=merge_saved(frame['ctx'].get('saved',{}),initial[0])
            stage=run['plan']['stages'][node['stage']]
            if stage.get('suppliers'):
                from . import prompt_sources as supply
                from .workspace_scene import scene,load
                base=copy.deepcopy(self.window.state)
                load(base,frame['ctx'].get('scene') or run.get('frozen_scene') or scene(base))
                from .prompt_materials import apply_progress
                for key, progress in run.get('supply_progress', {}).items():
                    definition=supply.source(base,key)
                    if definition:apply_progress(definition['root'],progress)
                for key, progress in run.get('supply_images', {}).items():
                    image=base.get('canvas_functions',{}).get('images',{}).get(key)
                    if image and image.get('batch_id')==progress['batch']:
                        image['index']=progress['next'];image['supply_reservation']=copy.deepcopy(progress)
                for key,items in frame['ctx'].get('saved',{}).get('batches',{}).items():
                    if key in base.get('canvas_functions',{}).get('images',{}):
                        image=base['canvas_functions']['images'][key];image['items']=copy.deepcopy(items)
                        if frame['ctx'].get('parent'):
                            image['index']=0;image.pop('supply_reservation',None)
                        if items:image['source']=copy.deepcopy(items[min(image.get('index',0),len(items)-1)])
                state=project(base,self.window.store.directory,frame['ctx']['results'],frame['ctx'].get('sources'),frame['ctx'].get('saved',{}).get('readers'))
                # Separate Stage routes can fan out from the same placement.
                # One parent iteration shares its choices, while a later batch
                # or a different parent gets a new independent selection set.
                shared_key=str(run['round'])+':'+str(frame['ctx'].get('parent') or '')
                shared=run.setdefault('supply_shared',{}).setdefault(shared_key,{})
                frozen=supply.build(state,stage['suppliers'],shared=shared)
                frozen['shared_key']=shared_key
                # Freeze bypass inputs once for the whole batch. They advance
                # only after this scope finishes, while participating inputs zip.
                outer=capture(state,run['plan'],[node['stage']],self.window.store.directory)
                frame['ctx']['saved']=merge_saved(frame['ctx'].get('saved',{}),outer)
                frame['feed']=dict(plan=frozen,scene=scene(state),next=0,total=len(frozen['rounds']))
                return
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
        if feed.get('plan'):
            return self.fill_supply(run,frame)
        while sum(r['scheduler']==frame['node']['key'] for r in self.store.rows('entry',run['workspace'],active=True))<10 and feed['next']<feed['total']:
            source=feed['items'][feed['next']];sources=dict(frame['ctx'].get('sources',{}));sources[frame['auto']]=source
            base=self.window.state
            if frame['ctx'].get('scene'):
                from .workspace_scene import load
                base=copy.deepcopy(base);load(base,frame['ctx']['scene'])
            state=project(base,self.window.store.directory,frame['ctx']['results'],sources,frame['ctx'].get('saved',{}).get('readers'))
            saved=self.capture_scope(run['plan'],frame['node'],state,per_round=True)
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

    def fill_supply(self,run,frame):
        with self.accepting():
            return self._fill_supply(run,frame)

    def _fill_supply(self,run,frame):
        from . import prompt_sources as supply
        from .workspace_scene import load
        from .flow_data import capture_inputs
        feed=frame['feed'];frozen=feed['plan'];node=frame['node']
        while sum(r['scheduler']==node['key'] for r in self.store.rows('entry',run['workspace'],active=True))<10 and feed['next']<feed['total']:
            base=copy.deepcopy(self.window.state);load(base,feed['scene'])
            images,positions=supply.project(base,frozen,frozen['rounds'][feed['next']])
            if frozen.get('version')!=2:
                following=frozen['rounds'][min(feed['next']+1,feed['total']-1)]
                for key,spec in frozen['sources'].items():
                    if spec['kind']=='text':
                        positions[key]=dict(kind='text',plan=frozen['id'],signature=spec['signature'],group=following[key]['group'])
            sources=dict(frame['ctx'].get('sources',{}));sources.update(images)
            state=project(base,self.window.store.directory,frame['ctx']['results'],sources,frame['ctx'].get('saved',{}).get('readers'))
            saved=self.capture_scope(run['plan'],node,state,per_round=True)
            inherited=frame['ctx'].get('saved',{})
            saved['parameters']=copy.deepcopy(inherited.get('parameters',{}))
            for kind in ('parameter_overrides','parameter_profiles'):saved[kind]=copy.deepcopy(inherited.get(kind,{}))
            saved['batches']={};saved['inputs']=capture_inputs(state,node['key'])
            saved['source_positions'].update(positions)
            entry=self.add_entry(run,frame['owner'],node,saved,parent=frame['ctx'].get('parent'))
            self.store.update(entry['id'],supply_images=images,supply_plan=frozen['id'],supply_round=feed['next'])
            feed['next']+=1
            if frozen.get('version')==2:
                shared=run.get('supply_shared',{}).get(frozen.get('shared_key'))
                accepted=supply.reserve(self.window.state,frozen,through=feed['next'],shared=shared)
                self.remember_supply_progress(run,frozen,accepted)
        self.save(run)
        if frozen.get('version')==2:
            self.refresh_reserved_images(frozen)
            self.window.store.save(self.window.state,commit=False)
            self.refresh_results();self.window.changed('prompt')

    def remember_supply_progress(self,run,frozen,accepted):
        for key,record in accepted.items():
            for ident,fields in record.get('progress',{}).items():
                run.setdefault('supply_progress',{}).setdefault(key,{}).setdefault(ident,{}).update(copy.deepcopy(fields))
            spec=frozen['sources'][key]
            if spec['kind']=='image':
                run.setdefault('supply_images',{})[key]=dict(batch=spec['batch'],plan=frozen['id'],next=(record['index']+1)%len(spec['items']))

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
                        participants=set(parent.get('feed',{}).get('plan',{}).get('sources',{})) or {parent['auto']}
                        self.advance_sources(dict(sources={k:v for k,v in positions.items() if k in participants}))
                parent['active']=None;parent['done']+=1
        elif run['workspace']==self.window.state['workspace']:
            self.advance_sources(dict(sources=run.get('source_positions',{})));run['source_positions']={}
        self.save(run)

    def direct_supply_variants(self,run,stage,ctx,frame,minimum=1):
        """A direct Stage shares an existing automatic batch without a fake queue."""
        if frame is None or stage['scheduler']:return None
        from . import prompt_sources as supply
        from .prompt_materials import apply_progress
        from .workspace_scene import load,scene
        if not any(other['auto'] for other in run['plan']['stages'].values()):return None
        ids=supply.connected(self.window.state,[b['source'] for b in stage['bindings'] if b['source']])
        if not ids:return None
        base=copy.deepcopy(self.window.state)
        load(base,ctx.get('scene') or run.get('frozen_scene') or scene(base))
        for key,progress in run.get('supply_progress',{}).items():
            definition=supply.source(base,key)
            if definition:apply_progress(definition['root'],progress)
        for key,progress in run.get('supply_images',{}).items():
            image=base.get('canvas_functions',{}).get('images',{}).get(key)
            if image and image.get('batch_id')==progress['batch']:
                image['index']=progress['next'];image['supply_reservation']=copy.deepcopy(progress)
        state=project(base,self.window.store.directory,ctx['results'],ctx.get('sources'),ctx.get('saved',{}).get('readers'))
        shared_key=str(run['round'])+':'+str(ctx.get('parent') or '')
        shared=run.setdefault('supply_shared',{}).setdefault(shared_key,{})
        total=supply.build(state,ids,shared=shared,count_only=True) if supply.automatic(state,ids) else 1
        frozen=supply.build(state,ids,shared=shared,count=max(total,minimum));frozen['shared_key']=shared_key
        frame['supply_plan']=frozen;variants=[]
        for index,picks in enumerate(frozen['rounds']):
            current=copy.deepcopy(state);images,positions=supply.project(current,frozen,picks)
            current=project(current,self.window.store.directory,ctx['results'],dict(ctx.get('sources',{}),**images),ctx.get('saved',{}).get('readers'))
            saved=capture(current,run['plan'],[stage['id']],self.window.store.directory,per_round=True)
            variants.append(dict(values=saved['values'],sources=images,results={},source_positions=positions,supply_round=index))
        return variants

    def variants(self,run,stage,ctx,frame=None):
        state=self.context(ctx['results'],ctx.get('sources'),ctx.get('saved',{}).get('readers'));collections=[]
        for binding in stage['bindings']:
            if binding['kind']!='image' or not binding['source'] or binding['key'] in ctx.get('saved',{}).get('values',{}):continue
            nodes=upstream(state,[binding['source']]);refs=nodes&run['plan']['stages'].keys()
            refs|={state.get('canvas_functions',{}).get('images',{}).get(k,{}).get('stage_reference') for k in nodes}-{None}
            if refs and not any(k in ctx.get('sources',{}) for k in nodes):collections.append((binding['key'],image_list(state,binding['source']),nodes,refs))
        if not collections:return self.direct_supply_variants(run,stage,ctx,frame) or [dict(values={},sources={},results={})]
        sizes=[len(v) for k,v,n,r in collections]
        if not all(sizes):raise ValueError('圖片來源沒有可供應的圖片。')
        variants=[]
        for i in range(max(sizes)):
            variant=dict(values={},sources={},results={})
            for key,items,nodes,refs in collections:
                image=items[i%len(items)];variant['values'][key]=dict(type='image',value=image,origin={})
                for source in nodes&state.get('canvas_functions',{}).get('images',{}).keys():variant['sources'][source]=image
                for ref in refs:
                    original=ctx['results'].get(ref,{})
                    record=next((r for r in original.get('records',[]) if r['attempt']==image.get('reference',{}).get('attempt')),None)
                    if record:variant['results'][ref]=dict(original,images=[image],text=record['text'],texts=copy.deepcopy(record.get('texts',[])),selected=True)
            variants.append(variant)
        supplied=self.direct_supply_variants(run,stage,ctx,frame,minimum=len(variants))
        if not supplied:return variants
        paired=[]
        for i in range(max(len(supplied),len(variants))):
            value=copy.deepcopy(supplied[i%len(supplied)]);image=variants[i%len(variants)]
            for field in ('values','sources','results'):value[field].update(copy.deepcopy(image[field]))
            paired.append(value)
        return paired

    def dispatch(self,run,frame,stage):
        from .native_submission import prepare
        ctx=copy.deepcopy(frame['ctx']);variant=frame['variants'][frame['index']]
        ctx['saved']=merge_saved(ctx.get('saved',{}),dict(values=variant['values'],source_positions=variant.get('source_positions',{})))
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
        with self.accepting():
            attempt=self.store.add('attempt',run['workspace'],run['id'],status='preparing',stage=stage['id'],parent=ctx.get('parent'),
                round=run['round'],position=frame['index'],snapshot_state=state,images=images,error='',sources=ctx.get('saved',{}).get('source_positions',{}),parameter_replay=bool(retry))
            if frame.get('supply_plan'):
                from .prompt_sources import reserve
                frozen=frame['supply_plan'];shared=run.get('supply_shared',{}).get(frozen.get('shared_key'))
                through=max(frozen.get('committed',0),variant['supply_round']+1)
                accepted=reserve(self.window.state,frozen,through=through,shared=shared)
                self.remember_supply_progress(run,frozen,accepted);self.refresh_reserved_images(frozen)
                self.window.store.save(self.window.state,commit=False)
            frame['job']=attempt['id'];self.save(run)
        self.preparing=attempt['id']
        if run['workspace']==self.window.state['workspace']:self.remember_preview(attempt,dict(images=[],attempts=[],records=[]))
        if frame.get('supply_plan'):self.refresh_results();self.window.changed('prompt')
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
            if attempt.get('cancel_requested'):
                if job and job['state'] in ('complete','failed'):
                    from .stage_queue_actions import finish_cancel
                    finish_cancel(self,attempt)
                continue
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
                    source=import_source(match['path'],self.window.store.directory);source['reference']=dict(run=run['id'],stage=stage['id'],attempt=attempt['id'],prompt_id=job['prompt_id'],node=node,index=i,
                        round=attempt.get('round'),parent=attempt.get('parent'))
                    images.append(source)
                field=stage.get('text_output');text=None
                if field:text=job.get('payload',{}).get('prompt',{}).get(field[0],{}).get('inputs',{}).get(field[1])
                from .generation import profile_text_fields
                graph=job.get('payload',{}).get('prompt',{})
                profile=next((p for p in attempt.get('snapshot_state',{}).get('generation',{}).get('profiles',[]) if p['id']==stage['workflow']),{})
                texts=[dict(node=node,field=field,text=graph[node]['inputs'][field]) for node,field in profile_text_fields(dict(profile,graph=graph))]
                result=dict(run=run['id'],images=images,text=text,texts=texts,attempts=[attempt['id']],records=[dict(attempt=attempt['id'],text=text,texts=texts)])
                self.store.update(attempt['id'],status='complete',result=result,error='');self.collecting.discard(attempt['id'])
                current=self.store.read(run['id'])
                frame=next((f for f in current['stack'] if f.get('job')==attempt['id']),None)
                if frame and current['status']!='cancelled':
                    merge_results(frame['produced'],{stage['id']:result});frame['job']=None;frame['index']+=1
                    autos={key for s in current['plan']['stages'].values() if s['auto'] for key in (s.get('suppliers') or [s['auto']])}
                    for key,value in attempt.get('sources',{}).items():
                        if key not in autos:current.setdefault('source_positions',{}).setdefault(key,value)
                    self.save(current)
                if current['workspace']==self.window.state['workspace']:
                    self.remember_preview(attempt,result)
                    self.results[stage['id']]=copy.deepcopy(frame['produced'][stage['id']]) if frame and stage['id'] in frame['produced'] else result
                    self.refresh_results()
                if current['status']=='paused':self.settle(self.store.read(current['id']))
                self.notify();self.later()
            except (ValueError,OSError) as exc:fail(str(exc))
        if expected:self.client.request('desktop/results?prompt_id='+quote(job['prompt_id'],safe=''),done=received,failed=fail)
        else:received([])

    def advance_sources(self,attempt):
        from .image_source import select
        changed=False
        for key,position in attempt.get('sources',{}).items():
            if position.get('accepted'):continue
            if position.get('kind')=='text':
                from .prompt_sources import source,signature
                current=source(self.window.state,key)
                if current and current['root'].get('supply_cursor',{}).get('plan')==position['plan'] and signature(current['root'])==position['signature']:
                    current['root']['sequence']['selected']=position['group']
                    changed=True
                continue
            value=self.window.state.get('canvas_functions',{}).get('images',{}).get(key,{})
            if value.get('batch_id')!=position.get('batch'):continue
            index=position.get('index',0);reservation=value.get('supply_reservation',{})
            if position.get('plan'):
                # A cancelled row leaves a gap; a later completed row still
                # advances. Manual selection/new batches invalidate this token.
                if reservation.get('plan')!=position['plan'] or value.get('index',0)>index:continue
            elif value.get('index')!=index:continue
            if index+1<len(value.get('items',[])):
                select(self.window.state,key,index+1,self.window.store.directory,preserve_reservation=True)
                changed=True
        if changed:
            # Readers wired through a Canvas own derived roots as well as a
            # source cursor. Materialize that view before publishing its Prompt.
            self.refresh_results()
            self.window.changed('prompt')

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
            if frame.get('auto') and frame.get('feed') and (frame['auto']==source or source in frame['feed'].get('plan',{}).get('sources',{})):
                frame['feed']['next']=frame['feed']['total']
        self.save(run);self.pause(ident,'已停止這批補入；已保存項目保留並暫停。',reason='manual')

    def resume(self,ident):
        run=self.store.read(ident)
        if not run or run['status'] in ('complete','cancelled'):return
        self.check_route(run)
        if any(a.get('cancel_requested') or a['status'] in ('failed','unconfirmed','result_error') for a in self.store.rows('attempt',owner=ident,active=True)):raise ValueError('這份流程有失敗或取消待確認項目，請先處理該項；其他流程仍可使用。')
        self.store.restore_retained(run)
        for key in self.scheduler_keys(run['plan']):self.store.set_scheduler_paused(run['workspace'],key,False)
        self.store.update(ident,status='running',message='',pause_reason='')
        self.client.dispatch_allowed
        self.later();self.notify('已繼續這份流程。')

    def cancel(self,ident=None):
        runs=[self.store.read(ident)] if ident else self.runs(True)
        runs=[run for run in runs if run and run['status'] not in ('complete','cancelled')]
        workspace=self.window.state['workspace']
        if any(run['workspace']!=workspace for run in runs):raise ValueError('工作區已切換，未取消其他工作。')
        attempts=[];released=False
        # Commit the stop, queue dispositions and cancellation intentions before
        # sending anything. A callback may run synchronously during a request.
        with self.accepting():
            for run in runs:
                self.store.update(run['id'],status='cancelled',message='已停止後續流程；已送出的工作仍須核對取消結果。')
                # Whole-batch cancellation releases its future image range.
                # Keep the visible next image, and never reset another batch.
                for frame in run['stack']:
                    plan=(frame.get('feed') or {}).get('plan') or {}
                    for key,spec in plan.get('sources',{}).items():
                        image=self.window.state.get('canvas_functions',{}).get('images',{}).get(key,{})
                        if spec['kind']=='image' and image.get('supply_reservation',{}).get('plan')==plan['id']:
                            image.pop('supply_reservation',None);released=True
                for item in self.store.rows('entry',workspace,active=True):
                    if item['run']!=run['id']:continue
                    # The native attempt continues to reconcile independently.
                    # Ended schedule entries must not occupy the next batch's slots.
                    self.store.update(item['id'],status='retained' if item['status']=='waiting' else 'cancelled')
                for attempt in self.store.rows('attempt',owner=run['id'],active=True):
                    self.store.update(attempt['id'],cancel_requested=True)
                    attempts.append(attempt['id'])
            if released:self.window.store.save(self.window.state,commit=False)
        if self.preparing in attempts:self.preparing=None
        if ident is None:
            self.apply_queue=[item for item in self.apply_queue if item[7][1]!=workspace]
            if self.apply_operation and self.apply_operation['workspace']==workspace:self.cancel_apply('已取消這次輸入同步。')
        for run in runs:self.cancel_inputs(run)
        from .stage_queue_actions import request_cancel
        for key in attempts:request_cancel(self,self.store.read(key))
        if released:self.window.changed('prompt',refresh=False)
        self.notify('已停止此工作區的後續流程；已送出的工作依原任務回覆確認取消。');self.later()

    def retry(self,ident,reprepare=False,allow_unknown=False):
        item=self.store.read(ident);run=self.store.read(item['owner']);self.check_route(run)
        if item.get('cancel_requested'):raise ValueError('這項取消仍待確認，不能重新生成。')
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

    def cancel_current(self,entry_id=None):
        from .stage_queue_actions import cancel_current
        cancel_current(self,entry_id)

    def remove_entry(self,ident):
        from .stage_queue_actions import remove_entry
        remove_entry(self,ident)

    def can_remove_entry(self,ident):
        from .stage_queue_actions import can_remove
        return can_remove(self,ident)

    def disconnected(self):
        if self.closed:return
        for run in self.runs(True):
            if run['status'] in ('running','waiting'):
                self.pause(run['id'],'連線中斷；已提交紀錄保留，恢復後請繼續。',reason='disconnect')
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
        self.results={};self.preview_results={};self.preview_stamps={}

    def remember_preview(self,attempt,result):
        """Keep the last completed image batch while the next one is pending.

        Ordered attempt creation prevents a late download from restoring an old
        iteration; the journal and results consumed by later Stages stay intact.
        """
        # A pending/failed/text-only attempt is not a replacement image. Also
        # allow the first completed result to appear when newer jobs were
        # already submitted concurrently; only a newer completed batch wins.
        if not result.get('images'):return
        key=attempt['stage'];scope=(attempt['owner'],attempt.get('round'),attempt.get('parent'))
        stamp=attempt.get('created',0);previous=self.preview_stamps.get(key)
        if previous and scope!=previous[1] and stamp<previous[0]:return
        if previous and scope==previous[1]:
            shown=self.preview_results[key]
            if attempt['id'] not in shown.get('attempts',[]):merge_results(self.preview_results,{key:result})
        else:self.preview_results[key]=copy.deepcopy(result)
        self.preview_stamps[key]=(max(stamp,previous[0]) if previous else stamp,scope)

    def load_results(self):
        self.results={};self.preview_results={};self.preview_stamps={};latest={}
        for item in self.store.rows('attempt',self.window.state['workspace']):
            if item['status']!='complete' or not item.get('result'):
                self.remember_preview(item,dict(images=[],attempts=[],records=[]));continue
            result=item['result'];key=item['stage'];stamp=(item['owner'],item['round'])
            self.remember_preview(item,result)
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
            if source:
                try:
                    from .prompt_sources import build
                    number=build(self.context(self.results),stage.get('suppliers') or [source],count_only=True)
                except ValueError:number=None
            elif any((upstream(self.window.state,[b['source']])&plan['stages'].keys()) for b in stage['bindings'] if b['source']):number=None
            if number is None:pieces.append(stage['name']+'：依本輪來源展開');uncertain=True
            else:pieces.append(stage['name']+'：'+str(number)+' 輪');total+=number
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
        state=self.context();data=state['multi_output'];used=connected_terminals(state)
        groups={}
        for key,target in terminals(state).items():
            if key in used and not all_inputs or not target.get('workflow'):continue
            kind='clip' if key in data['clip_inputs'] else 'image';source=incoming(state,key,kind)
            if not source:continue
            # A result-dependent free input belongs to the new run. It is
            # applied after that run ends, never from a previous preview here.
            if upstream(state,[source]) & data.get('stages',{}).keys():continue
            try:value=__import__('prompt_calculus_studio.flow_data',fromlist=['resolve']).resolve(state,source,kind)
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
            ended=state in ('failed','settled','applied','opened','inspected','captured')
            if not ended and store.read(ident).get('ended'):
                store.update(ident,cancel_state=state,cancel_error=str(error or ''));return
            changes=dict(cancel_state=state,cancel_error=str(error or ''),ended=ended,
                status='cancelled' if state=='failed' else state if ended else 'unconfirmed')
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
        if not self.client.dispatch_allowed:return
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
                if getattr(e,'rejected',False):
                    journal(ended=True,rejected=True)
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
