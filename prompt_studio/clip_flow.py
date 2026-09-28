"""Independent workflow text destinations. Pure data operations shared with ComfyUI."""
import copy


def source(state, key):
    return next((c['source'] for c in state['multi_output']['connections'] if c['kind']=='clip' and c['destination']==key),None)


def add(state, position=(600,-350)):
    from .multi_output import ident
    data=state['multi_output']; names={v['name'] for v in data['clip_inputs'].values()}; number=1
    while 'CLIP 輸入'+str(number) in names: number+=1
    key=ident('clip_'); data['clip_inputs'][key]=dict(name='CLIP 輸入'+str(number))
    state.setdefault('text_positions',{})[key]=list(position)
    return key


def upgrade(state):
    from .multi_output import compiled_outputs,connect
    from .core import validate_state
    if state['multi_output']['version']>=3: return state
    result=copy.deepcopy(state); data=result['multi_output']; before=compiled_outputs(result)
    active={c['source'] for c in data['connections'] if c['kind']=='execution'}
    # Keep deliberately disconnected outputs disconnected. Preview becomes a
    # viewport action; old preview visibility and saved image records are kept.
    data['connections']=[c for c in data['connections'] if c['kind'] not in ('execution','preview')]
    data['version']=3; data['clip_inputs']={}; destinations={}
    keys=list(data['outputs'])+list(dict.fromkeys(b['output'] for b in data['bindings'] if b['output'] not in data['outputs']))
    for index,output in enumerate(keys):
        pos=result.get('text_positions',{}).get(output,[index*510,-350])
        width=result.get('text_sizes',{}).get(output,[440,550])[0]
        key=add(result,(pos[0]+width+90,pos[1])); destinations[output]=key
        if output in active and output in data['outputs']: connect(result,output,key,'clip')
    for binding in data['bindings']: binding['clip']=destinations[binding.pop('output')]
    if compiled_outputs(result)!=before: raise ValueError('CLIP 遷移前後文字不一致，保留原資料。')
    validate_state(result); return result


def set_binding(state, workflow, key, target):
    from .generation import text_fields
    data=state['multi_output']
    if key not in data['clip_inputs']: raise ValueError('CLIP 輸入不存在。')
    if target is not None:
        profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==workflow),None)
        if profile is None or tuple(target) not in text_fields(profile['graph']): raise ValueError('綁定目標不存在或已由上游接線供值。')
        node,field=target
        for b in data['bindings']:
            if (b['workflow'],b['node'],b['field'])==(workflow,node,field) and b['clip']!=key:
                raise ValueError('這個欄位已由「'+data['clip_inputs'][b['clip']]['name']+'」占用，請先解除原綁定。')
    data['bindings']=[b for b in data['bindings'] if (b['workflow'],b['clip'])!=(workflow,key)]
    if target is not None: data['bindings'].append(dict(workflow=workflow,clip=key,node=node,field=field))
    if target is not None and data['version']>=4:
        from .workflow_flow import register
        register(state,workflow)
        data['clip_inputs'][key]['workflow']=workflow
    elif target is None and data['version']>=4 and data['clip_inputs'][key].get('workflow')==workflow:
        data['clip_inputs'][key]['workflow']=None


def bound_texts(state, profile):
    from .generation import text_fields
    from .multi_output import compile_output
    data=state['multi_output']; available=text_fields(profile['graph']); result=[]
    for b in data['bindings']:
        if b['workflow']!=profile['id']: continue
        if data['version']>=4 and data['clip_inputs'][b['clip']].get('workflow')!=profile['id']:continue
        output=source(state,b['clip'])
        if output is None: continue
        if (b['node'],b['field']) not in available: raise ValueError('CLIP 綁定失效：#'+b['node']+' / '+b['field'])
        if data['version']>=5:
            from .flow_data import resolve
            content=resolve(state,output,'clip')
            item=dict(b,output=content['origin'].get('output',output),text=content['value'])
        else:item=dict(b,output=output,text=compile_output(state,output)['final_prompt'])
        if data['version']>=4:item['text_source']=data['clip_inputs'][b['clip']].get('text_source','pcs')
        result.append(item)
    if not result and not any(v.get('workflow')==profile['id'] for v in data.get('image_inputs',{}).values()):
        raise ValueError('請將 Prompt 輸出連接到已綁定工作流文字欄的 CLIP 輸入。')
    return result


def remove(state,key):
    data=state['multi_output']; data['clip_inputs'].pop(key)
    data['connections']=[c for c in data['connections'] if c['destination']!=key]
    data['bindings']=[b for b in data['bindings'] if b['clip']!=key]
