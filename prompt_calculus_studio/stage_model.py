"""Stage configuration and nested scheduling scopes. No Qt or runtime state."""
import copy
from .flow_data import incoming,upstream,channel


def upgrade(state):
    if state['multi_output']['version']>=7:return state
    state=copy.deepcopy(state);data=state['multi_output'];data['version']=7;data['stages']={}
    # Keep historical chain definitions readable, never enable a hidden runner.
    if data.get('workflow_order',{}).get('chain'):
        data['workflow_order']['chain']['enabled']=False
    old_controls=[c for c in data['connections'] if c['kind']=='control']
    if old_controls:
        data['workflow_order']['legacy_control_connections']=old_controls
        data['connections']=[c for c in data['connections'] if c['kind']!='control']
    data.get('workflow_order',{})['visible']=False
    for item in data.get('schedulers',{}).values():item.setdefault('mode','data')
    for item in state.get('canvas_functions',{}).get('images',{}).values():
        if item.get('binding'):
            item['legacy_binding']=item.pop('binding')
    return state


def add(state,position=(1000,200),workflow=None):
    from .multi_output import ident
    key=ident('stage_');data=state['multi_output']
    data.setdefault('stages',{})[key]=dict(name='Stage '+str(len(data['stages'])+1),workflow=workflow,
        output=None,text_output=None,selection='all',index=0)
    state.setdefault('text_positions',{})[key]=list(position)
    choose(state,key,workflow)
    return key


def choose(state,key,workflow):
    item=state['multi_output']['stages'][key];item['workflow']=workflow
    profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==workflow),None)
    if profile:
        # Output/field selection is a downstream reader setting.
        item.update(output=None,text_output=None,selection='all',index=0)


def controls(state,key):
    return [c['source'] for c in state['multi_output']['connections'] if c['destination']==key and c['kind']=='control']


def terminals(state):
    data=state['multi_output'];return {**data['clip_inputs'],**data.get('image_inputs',{})}


def validate(state):
    data=state['multi_output'];stages=data.get('stages')
    if not isinstance(stages,dict) or len(stages)>100:raise ValueError('Stage 數量無效。')
    for key,item in stages.items():
        if not isinstance(key,str) or '::' in key or not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].strip():raise ValueError('Stage 名稱或識別無效。')
        for field in ('workflow','output'):
            if item.get(field) is not None and not isinstance(item[field],str):raise ValueError('Stage 工作流或輸出無效。')
        if item.get('selection','all') not in ('all','single') or type(item.get('index',0)) is not int or item.get('index',0)<0:raise ValueError('Stage 圖片選取無效。')
        text=item.get('text_output')
        if text is not None and (not isinstance(text,list) or len(text)!=2 or any(not isinstance(v,str) for v in text)):raise ValueError('Stage 文字輸出無效。')
        if item.get('parameters') is not None:
            from .stage_parameters import validate as validate_parameters
            validate_parameters(item['parameters'])
    for item in data['schedulers'].values():
        if item.get('mode','data') not in ('data','stage'):raise ValueError('預排程範圍無效。')
        if any(v not in ('saved','live') for v in item.get('policies',{}).values()):raise ValueError('預排程輸入取值方式無效。')


def sources(state,key):
    """Input graph upstream, including a source's explicit Stage reference."""
    data=state['multi_output'];found=set();todo=[key];images=state.get('canvas_functions',{}).get('images',{})
    while todo:
        source=todo.pop()
        if source in found:continue
        found.add(source)
        # A Stage is a result boundary. Its internal bindings belong to that Stage.
        if source in data.get('stages',{}):continue
        route=channel(state,source)
        if route:found.add(route[0])
        ref=images.get(source,{}).get('stage_reference')
        if ref:todo.append(ref)
        todo.extend(c['source'] for c in data['connections'] if c['destination']==source and c['kind'] not in ('control','done','flow'))
    return found


def input_nodes(state,keys):
    found=set()
    for key in keys:found.update(sources(state,key))
    return found


def compile_plan(state):
    from .generation import validate_profile
    data=state['multi_output'];all_stages=data['stages'];schedulers=data['schedulers']
    # A card is not an execution request. Only wired inputs, or a reached
    # predecessor, activate a Stage. Output readers do not activate their source.
    reached={key for key in all_stages if controls(state,key)}
    while True:
        following={c['destination'] for c in data['connections'] if c['kind']=='done' and c['source'] in reached and c['destination'] in all_stages}
        if following<=reached:break
        reached.update(following)
    stages={key:copy.deepcopy(value) for key,value in all_stages.items() if key in reached}
    dependencies={k:set() for k in stages};plans={}
    for key,stage in stages.items():
        profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==stage.get('workflow')),None)
        if profile is None:raise ValueError(stage['name']+'：請選擇工作流。')
        validate_profile(profile)
        if not profile.get('frontend_id'):raise ValueError(stage['name']+'：請重新選擇原生工作流。')
        cs=controls(state,key);nodes=set()
        for terminal in cs:nodes.update(sources(state,terminal))
        missing=(nodes&all_stages.keys())-stages.keys()
        if missing:raise ValueError(stage['name']+'：上游 Stage 尚未接入流程。')
        dependencies[key]|=nodes&stages.keys()
        dependencies[key].update(c['source'] for c in data['connections'] if c['kind']=='done' and c['destination']==key)
        bound=[]
        for terminal in cs:
            target=terminals(state)[terminal]
            if not target.get('workflow'):raise ValueError(stage['name']+'：輸入尚未綁定工作流。')
            if terminal in data['clip_inputs']:
                from .clip_flow import selected_binding
                binding=selected_binding(state,terminal)
                if not binding:raise ValueError(stage['name']+'：CLIP 輸入尚未綁定欄位。')
                bound.append(dict(key=terminal,kind='clip',source=incoming(state,terminal,'clip'),target=copy.deepcopy(binding),text_source='pcs'))
            else:
                if not target.get('node'):raise ValueError(stage['name']+'：請選擇 LoadImage。')
                bound.append(dict(key=terminal,kind='image',source=incoming(state,terminal,'image'),target=copy.deepcopy(target)))
        used={s for s in nodes&schedulers.keys() if schedulers[s].get('mode','data')=='data'}
        if len(used)>1:raise ValueError(stage['name']+'：同一階段的資料請接入同一份預排程。')
        auto=[]
        if used:
            scheduler=next(iter(used))
            for ch in schedulers[scheduler]['channels']:
                source=incoming(state,scheduler+'::'+ch['id'],ch['type'])
                # Automatic supply is opt-in by a direct source connection.
                image=state.get('canvas_functions',{}).get('images',{}).get(source,{})
                if image.get('iterate') and source not in auto:auto.append(source)
            if len(auto)>1:raise ValueError(stage['name']+'：一個批次只允許一個自動圖片來源。')
        plans[key]=dict(stage,id=key,bindings=bound,scheduler=next(iter(used),None),auto=next(iter(auto),None),
            identity=dict(frontend_id=profile['frontend_id'],origin=profile.get('origin',{})))
    order=[];pending=set(stages)
    while pending:
        ready=[k for k in stages if k in pending and dependencies[k]<=set(order)]
        if not ready:raise ValueError('Stage 依賴形成循環。')
        if len(ready)>1:raise ValueError('請連接 Stage 的輸出與下一個 Stage 的輸入，決定執行順序。')
        order.append(ready[0]);pending.remove(ready[0])
    scopes={};visiting=set()
    def members(key):
        if key in scopes:return scopes[key]['stages']
        if key in visiting:raise ValueError('預排程包含關係形成循環。')
        visiting.add(key);result=[]
        for c in data['connections']:
            if c['kind']!='flow' or c['destination']!=key+'::flow':continue
            src=c['source'];parent=src.split('::')[0]
            if src in all_stages:
                if src in stages:result.append(src)
            elif parent in schedulers:result.extend(members(parent))
        visiting.remove(key);result=list(dict.fromkeys(result));result.sort(key=order.index)
        if result:
            indices=[order.index(k) for k in result]
            if indices!=list(range(min(indices),max(indices)+1)):raise ValueError('Stage 預排程必須包含連續的流程。')
            scopes[key]=dict(stages=result,start=min(indices),end=max(indices),policies=copy.deepcopy(schedulers[key].get('policies',{})))
        # An unused scheduling card is not an execution entry either.
        return result
    for key,value in schedulers.items():
        if value.get('mode')=='stage':members(key)
    values=list(scopes)
    for at,a in enumerate(values):
        for b in values[at+1:]:
            x,y=set(scopes[a]['stages']),set(scopes[b]['stages'])
            if x&y and not (x<y or y<x):
                # Equal ranges require an explicit outer reference.
                linked=any(c['kind']=='flow' and {c['source'],c['destination']}=={a+'::flow',b+'::flow'} for c in data['connections'])
                if x!=y or not linked:raise ValueError('預排程範圍交錯；請改成完整包含的內外層。')
    def build(start,end,excluded=()):
        result=[];index=start
        while index<=end:
            candidates=[k for k,s in scopes.items() if k not in excluded and s['start']==index and s['end']<=end]
            if candidates:
                # Outer reference takes precedence for identical ranges.
                def depth(k):return 1+max([depth(c['source'].split('::')[0]) for c in data['connections'] if c['kind']=='flow' and c['destination']==k+'::flow' and c['source'].endswith('::flow')]+[0])
                def rank(k):return (scopes[k]['end'],depth(k))
                key=max(candidates,key=rank);scope=scopes[key]
                result.append(dict(kind='scope',key=key,children=build(index,scope['end'],(*excluded,key))));index=scope['end']+1
            else:
                key=order[index];stage=plans[key];leaf=dict(kind='stage',key=key)
                result.append(dict(kind='data',key=stage['scheduler'],stage=key,auto=stage['auto'],children=[leaf]) if stage['scheduler'] else leaf);index+=1
        return result
    return dict(version=2,stages=plans,order=order,scopes=scopes,tree=build(0,len(order)-1),
        connections=copy.deepcopy(data['connections']))


def migrate_bindings(state):
    """Explicit convenience action, never a load-time generation migration."""
    from .multi_output import connect
    groups={}
    for key,value in terminals(state).items():
        if value.get('workflow'):groups.setdefault(value['workflow'],[]).append(key)
    previous=None
    for index,(workflow,keys) in enumerate(groups.items()):
        stage=add(state,(1200+index*530,100),workflow)
        for key in keys:connect(state,key,stage,'control')
        if previous:connect(state,previous,stage,'done')
        previous=stage


def execution_signature(plan):
    """Compare execution wiring, excluding readers, names and future policies.

    Also accepts the older full-Canvas connection list saved in run journals.
    """
    stages=plan['stages'];scopes=plan['scopes'];connections=plan.get('connections',[])
    def stage_signature(value):
        result={field:copy.deepcopy(value.get(field)) for field in ('id','workflow','bindings','scheduler','auto','identity')}
        # Old journals include the image input card's display name in target.
        # Compare only its destination, so renaming remains an editing action.
        for binding in result['bindings']:
            binding['target']={field:binding['target'][field] for field in ('workflow','node','field') if field in binding['target']}
        return result
    needed={b['key'] for stage in stages.values() for b in stage['bindings']}
    todo=list(needed);visited=set()
    while todo:
        key=todo.pop()
        if key in visited or key in stages:continue
        visited.add(key);needed.add(key.split('::')[0])
        todo.extend(c['source'] for c in connections if c['destination']==key and c['kind'] not in ('control','done','flow'))
    needed.update(visited)
    edges=[]
    for c in connections:
        kind=c['kind'];src=c['source'];dst=c['destination']
        if ((kind=='control' and dst in stages) or (kind=='done' and src in stages and dst in stages)
            or (kind=='flow' and dst.split('::')[0] in scopes)
            or (kind not in ('control','done','flow') and dst in needed)):
            edges.append((src,dst,kind))
    return dict(order=plan['order'],tree=plan['tree'],
        stages={key:stage_signature(value) for key,value in stages.items()},
        scopes={key:{field:copy.deepcopy(value[field]) for field in ('stages','start','end')} for key,value in scopes.items()},
        connections=sorted(edges))
