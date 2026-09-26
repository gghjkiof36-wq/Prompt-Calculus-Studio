"""Version 4 workspace data. Canvas ownership and typed flow are independent.

All operations run on the caller's copy; validation never repairs broken bindings
silently. Qt-free so the same snapshots can be checked by the ComfyUI extension.
"""
import copy
import math
import uuid

GENERATOR = '__generation__'
PREVIEW = '__result_preview__'
LIST_FIELDS = ('selections', 'weights', 'instances', 'temporary', 'temporary_before')


def ident(prefix): return prefix + uuid.uuid4().hex


def new_canvas(name='畫布', position=(0, 0)):
    return dict(name=name, members=[], position=list(position), display_size=[760, 620],
                edit_origin=[0, 0], image=None)


def new_output(name='prompt輸出1'):
    return dict(name=name, canvas=None, draft=None, draft_base='', list_state={})


def next_output_name(state):
    names={v['name'] for v in state['multi_output']['outputs'].values()}; number=1
    while 'prompt輸出'+str(number) in names: number+=1
    return 'prompt輸出'+str(number)


def upgrade_output_names(state):
    import re
    legacy=[key for key,v in state['multi_output']['outputs'].items() if re.fullmatch(r'最終 Prompt(?: [0-9]+)?',v['name'])]
    if not legacy: return state
    result=copy.deepcopy(state)
    for key in legacy: result['multi_output']['outputs'][key]['name']=next_output_name(result)
    return result


def next_canvas_name(state):
    names={c['name'] for c in state['multi_output']['canvases'].values()}; number=1
    while '畫布'+str(number) in names: number+=1
    return '畫布'+str(number)


def valid_edge(state,source,destination,kind):
    data=state['multi_output']; canvases=data['canvases']; outputs=data['outputs']; images=state.get('canvas_functions',{}).get('images',{})
    if kind=='text': return source in canvases and destination in outputs
    if data['version']>=3 and kind=='clip': return source in outputs and destination in data['clip_inputs']
    if kind=='image':
        if data['version']==1: return source in set(canvases)|set(images) and destination==GENERATOR
        destinations=set(canvases)|set(outputs)|({PREVIEW} if data['version']>=4 else set())
        return (source in images and destination in destinations) or (source in canvases and destination in set(outputs)|({PREVIEW} if data['version']>=4 else set()))
    if data['version']==2:
        return source in outputs and ((kind=='execution' and destination==GENERATOR) or (kind=='preview' and destination==PREVIEW))
    return False


def input_slot(source,destination,kind):
    # Each output gets a distinct socket on the shared execution/preview card.
    return (destination,kind,source if kind in ('execution','preview') else '')


def connected_outputs(state,kind='execution'):
    if state['multi_output']['version']>=3:
        return {c['source'] for c in state['multi_output']['connections'] if c['kind']=='clip'}
    return {c['source'] for c in state['multi_output']['connections'] if c['kind']==kind}


def active_output(state):
    data=state.get('multi_output', {})
    return data.get('outputs', {}).get(data.get('current_output'))


def owner(state, use):
    return next((key for key,c in state['multi_output']['canvases'].items() if use in c['members']), None)


def reconcile(state):
    """Remove ownership of explicitly deleted/nested roots, without adopting others."""
    data=state.get('multi_output')
    if not data: return
    uses=state.get('uses', {})
    for canvas in data['canvases'].values():
        canvas['members']=[key for key in canvas['members'] if key in uses]
    capture_current(state)


def capture_current(state):
    output=active_output(state)
    if output is None: return
    if state.get('selection_view')=='canvas' or not state['settings'].get('separate_selections',True):
        output.update(draft=state.get('draft'), draft_base=state.get('draft_base',''))
    else:
        output['list_draft']=dict(draft=state.get('draft'),draft_base=state.get('draft_base',''))
    output['list_state']={key:copy.deepcopy(state[key]) for key in LIST_FIELDS if key in state}


def select_output(state,key):
    data=state['multi_output']
    if key not in data['outputs']: raise ValueError('目前輸出不存在。')
    capture_current(state); data['current_output']=key; output=data['outputs'][key]
    saved=output.get('list_state',{})
    for field in LIST_FIELDS: state.pop(field,None)
    state.update(selections={},weights={},instances={},temporary=[])
    state.update(copy.deepcopy(saved))
    draft=output.get('list_draft',{}) if state.get('selection_view')=='list' and state['settings'].get('separate_selections',True) else output
    state.update(draft=draft.get('draft'),draft_base=draft.get('draft_base',''))


def assign(state,use,canvas):
    data=state['multi_output']
    if use not in state.get('uses',{}): raise ValueError('文字模組不存在。')
    if canvas is not None and canvas not in data['canvases']: raise ValueError('畫布不存在。')
    old=owner(state,use)
    if old==canvas: return
    if old is not None: data['canvases'][old]['members'].remove(use)
    if canvas is not None: data['canvases'][canvas]['members'].append(use)


def connect(state,source,destination,kind):
    data=state['multi_output']; canvases=data['canvases']; outputs=data['outputs']
    if not valid_edge(state,source,destination,kind): raise ValueError('這兩個端口無法連接。')
    if kind=='text':
        if source not in canvases or destination not in outputs: raise ValueError('文字只能由畫布連到最終 Prompt。')
        if any(c['kind']=='text' and c['source']==source and c['destination']!=destination for c in data['connections']):
            raise ValueError('這張畫布已連接另一個最終 Prompt，請先解除。')
    # Allowed edges form a DAG by type. A result can only be reused explicitly.
    slot=input_slot(source,destination,kind)
    data['connections']=[c for c in data['connections'] if input_slot(c['source'],c['destination'],c['kind'])!=slot]
    data['connections'].append(dict(id=ident('line_'),source=source,destination=destination,kind=kind))
    if kind=='text': outputs[destination]['canvas']=source


def disconnect(state,key):
    data=state['multi_output']
    for c in data['connections']:
        if c['id']==key and c['kind']=='text': data['outputs'][c['destination']]['canvas']=None
    data['connections']=[c for c in data['connections'] if c['id']!=key]


def bind(state,workflow,output,node,field):
    if state['multi_output']['version']>=3:
        from .clip_flow import set_binding as bind_clip
        return bind_clip(state,workflow,output,(node,field))
    from .generation import text_fields
    data=state['multi_output']
    profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==workflow),None)
    if profile is None or output not in data['outputs'] or (node,field) not in text_fields(profile['graph']):
        raise ValueError('綁定目標不存在或已由上游接線供值。')
    for b in data['bindings']:
        if (b['workflow'],b['node'],b['field'])==(workflow,node,field) and b['output']!=output:
            raise ValueError('這個欄位已由「'+data['outputs'][b['output']]['name']+'」占用，請先解除原綁定。')
    data['bindings']=[b for b in data['bindings'] if (b['workflow'],b['output'])!=(workflow,output)]
    data['bindings'].append(dict(workflow=workflow,output=output,node=node,field=field))


def set_binding(state,workflow,output,target):
    """Bind or remove one output target; never remove another workflow's binding."""
    if state['multi_output']['version']>=3:
        from .clip_flow import set_binding as bind_clip
        return bind_clip(state,workflow,output,target)
    if target is not None:return bind(state,workflow,output,*target)
    data=state['multi_output']
    if output not in data['outputs']:raise ValueError('目前輸出不存在。')
    # Removal intentionally permits an obsolete workflow id so stale bindings
    # can be cleared after a profile is removed or imported again.
    data['bindings']=[b for b in data['bindings'] if (b['workflow'],b['output'])!=(workflow,output)]


def canvas_projection(state,canvas_id):
    """Feed one container to the existing exact-text compiler."""
    data=state['multi_output']; canvas=data['canvases'].get(canvas_id)
    if canvas is None: raise ValueError('來源畫布不存在。')
    result=dict(state); result.pop('multi_output',None)
    result['uses']={key:state['uses'][key] for key in canvas['members']}
    result['output_order']=list(canvas['members'])
    result['settings']=dict(state['settings'],separate_selections=False)
    result.update(selections={},weights={},instances={},temporary=[])
    if not state['settings'].get('separate_selections',True):
        output=next((v for v in data['outputs'].values() if v['canvas']==canvas_id),{})
        saved=output.get('list_state',{})
        if output is active_output(state): saved={key:state[key] for key in LIST_FIELDS if key in state}
        result.update(saved)
        from .core import output_groups
        result['output_order']=list(canvas['members'])+[key for key in output_groups(state,include_hidden=True) if key not in state.get('uses',{})]
    return result


def compile_output(state,key):
    from .core import compose_details
    data=state['multi_output']; output=data['outputs'][key]
    if output['canvas'] is None: raise ValueError('「'+output['name']+'」缺少來源畫布。')
    isolated_list=state.get('selection_view')=='list' and state['settings'].get('separate_selections',True)
    if isolated_list:
        projection=dict(state); projection.pop('multi_output',None)
        projection.update(uses={},selections={},weights={},instances={},temporary=[])
        projection.update(output.get('list_state',{}))
        if key==data['current_output']: projection.update({k:state[k] for k in LIST_FIELDS if k in state})
        draft=state.get('draft') if key==data['current_output'] else output.get('list_draft',{}).get('draft')
    else:
        projection=canvas_projection(state,output['canvas']); draft=output['draft']
    generated,affected=compose_details(projection)
    if key==data['current_output'] and not isolated_list: draft=state.get('draft')
    return dict(output=key,name=output['name'],canvas=output['canvas'],generated_prompt=generated,
                final_prompt=draft if draft is not None else generated,manual_draft=draft is not None,affected=affected)


def compiled_outputs(state):
    return {key:compile_output(state,key) for key,o in state['multi_output']['outputs'].items() if o['canvas'] is not None}


def bound_texts(state,profile):
    if state['multi_output']['version']>=3:
        from .clip_flow import bound_texts as clip_texts
        return clip_texts(state,profile)
    from .generation import text_fields
    data=state['multi_output']; available=text_fields(profile['graph']); result=[]
    active=connected_outputs(state) if data['version']>=2 else set(data['outputs'])
    for b in data['bindings']:
        if b['workflow']!=profile['id'] or b['output'] not in active: continue
        name=data['outputs'].get(b['output'],{}).get('name',b['output'])
        if (b['node'],b['field']) not in available: raise ValueError('「'+name+'」的綁定失效：#'+b['node']+' / '+b['field'])
        if b['output'] not in data['outputs']: raise ValueError('已綁定的輸出不存在：'+name)
        result.append(dict(b,**{'text':compile_output(state,b['output'])['final_prompt']}))
    if not result: raise ValueError('請將已綁定工作流的 Prompt 連接到執行操作。')
    return result


def validate_multi(state):
    from .composition import validate_node
    data=state['multi_output']
    if not isinstance(data,dict) or data.get('version') not in (1,2,3,4): raise ValueError('多畫布資料版本無效。')
    for name in (('canvases','outputs','clip_inputs') if data['version']>=3 else ('canvases','outputs')):
        if not isinstance(data.get(name),dict) or len(data[name])>100: raise ValueError('畫布或輸出數量無效。')
        for key,v in data[name].items():
            if not isinstance(key,str) or not key or not isinstance(v,dict) or not isinstance(v.get('name'),str) or not v['name'].strip():
                raise ValueError('畫布或輸出的名稱無效。')
    if data['version']>=4:
        from .workflow_flow import validate
        validate(state)
    owned=set()
    for c in data['canvases'].values():
        if not isinstance(c.get('members'),list): raise ValueError('畫布歸屬無效。')
        for key in c['members']:
            if not isinstance(key,str) or key not in state.get('uses',{}) or key in owned: raise ValueError('畫布內容遺失或重複歸屬。')
            owned.add(key)
        for field in ('position','display_size','edit_origin'):
            values=c.get(field)
            if not isinstance(values,list) or len(values)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1000000 for v in values): raise ValueError('畫布座標或尺寸無效。')
        if any(v<100 or v>100000 for v in c['display_size']): raise ValueError('畫布顯示尺寸無效。')
        if c.get('image') is not None:
            from .composition_image import validate_image
            validate_image(c['image'])
    if data.get('current_output') is not None and data['current_output'] not in data['outputs']: raise ValueError('目前輸出不存在。')
    for output in data['outputs'].values():
        ratio=output.get('panel_ratio',.45)
        if type(ratio) not in (float,int) or not math.isfinite(ratio) or not 0<=ratio<=1: raise ValueError('文字欄比例無效。')
        if output.get('canvas') is not None and output['canvas'] not in data['canvases']: raise ValueError('來源畫布不存在。')
        if output.get('draft') is not None and not isinstance(output['draft'],str): raise ValueError('輸出手動稿无效。')
        if not isinstance(output.get('draft_base',''),str): raise ValueError('輸出手動稿基準無效。')
    seen=set(); sources=set(); line_ids=set()
    if not isinstance(data.get('connections'),list) or len(data['connections'])>300: raise ValueError('連線資料無效。')
    for line in data['connections']:
        if not isinstance(line,dict) or any(not isinstance(line.get(k),str) for k in ('id','source','destination','kind')): raise ValueError('連線格式無效。')
        src,dst,kind=line['source'],line['destination'],line['kind']
        slot=input_slot(src,dst,kind)
        if line['id'] in line_ids or slot in seen: raise ValueError('輸入連線重複。')
        line_ids.add(line['id']); seen.add(slot)
        if not valid_edge(state,src,dst,kind): raise ValueError('連線類型或端口無效。')
        if kind=='text':
            if src not in data['canvases'] or dst not in data['outputs'] or src in sources or data['outputs'][dst]['canvas']!=src: raise ValueError('文字連線或來源不一致。')
            sources.add(src)
    for key,output in data['outputs'].items():
        if output['canvas'] is not None and (key,'text','') not in seen: raise ValueError('輸出缺少文字連線。')
    targets=set(); output_targets=set()
    if not isinstance(data.get('bindings'),list) or len(data['bindings'])>5000: raise ValueError('工作流綁定資料無效。')
    for b in data['bindings']:
        owner_field='clip' if data['version']>=3 else 'output'
        if not isinstance(b,dict) or any(not isinstance(b.get(k),str) or not b[k] for k in ('workflow',owner_field,'node','field')): raise ValueError('工作流綁定格式無效。')
        if data['version']>=3 and b['clip'] not in data['clip_inputs']: raise ValueError('綁定的 CLIP 輸入不存在。')
        target=(b['workflow'],b['node'],b['field']); output=(b['workflow'],b[owner_field])
        if target in targets or output in output_targets: raise ValueError('工作流綁定重複。')
        targets.add(target); output_targets.add(output)
    return data


def migrate(state):
    """Return a fully verified candidate; caller backs up before saving it."""
    if 'multi_output' in state: return upgrade_output_names(upgrade_connections(state))
    from .core import build_prompt,output_groups,activate_selection_view,validate_state
    from .composition import migrate_canvas_library
    result=migrate_canvas_library(copy.deepcopy(state))
    result['settings'].setdefault('separate_selections',False)
    canvas_state=copy.deepcopy(result); activate_selection_view(canvas_state,'canvas')
    before=build_prompt(canvas_state); final=canvas_state['draft'] if canvas_state['draft'] is not None else before
    cid=ident('canvas_'); oid='__text_output__'; canvas=new_canvas('畫布1',(-900,-400))
    canvas['members']=[key for key in output_groups(canvas_state) if key in result.get('uses',{})]
    output=new_output(); output.update(canvas=cid,draft=canvas_state['draft'],draft_base=canvas_state.get('draft_base',''))
    result['multi_output']=dict(version=1,canvases={cid:canvas},outputs={oid:output},current_output=oid,connections=[],bindings=[])
    connect(result,cid,oid,'text')
    for profile in result.get('generation',{}).get('profiles',[]):
        profile['multi_text']=True
        target=profile.get('prompt')
        if target: result['multi_output']['bindings'].append(dict(workflow=profile['id'],output=oid,node=target[0],field=target[1]))
    images=result.setdefault('canvas_functions',dict(images={},preview=True,preview_attached=False))['images']
    if not images and result.get('generation',{}).get('source'):
        images[ident('__source_')]=dict(source=copy.deepcopy(result['generation']['source']),attached=True)
    for key,value in images.items():
        if value.get('attached'): connect(result,key,GENERATOR,'image')
        value['attached']=False
    result['version']=4
    if result.get('selection_view')=='canvas': result.update(draft=output['draft'],draft_base=output['draft_base'])
    capture_current(result)
    checked_state=copy.deepcopy(result); activate_selection_view(checked_state,'canvas')
    checked=compile_output(checked_state,oid)
    if checked['generated_prompt']!=before or checked['final_prompt']!=final:
        raise ValueError('遷移前後的 Canvas 文字不一致；已保留原資料，尚未套用。')
    validate_state(result)
    return upgrade_connections(result)


def upgrade_connections(state):
    """Preserve the previously active flows once; never reconnect deleted links."""
    from .core import validate_state
    result=copy.deepcopy(state); data=result['multi_output']
    if data['version']>=2: return result
    before=compiled_outputs(result); image=next((c for c in data['connections'] if c['kind']=='image'),None)
    data['connections']=[c for c in data['connections'] if c['kind']!='image']; data['version']=2
    for key in data['outputs']:
        connect(result,key,GENERATOR,'execution')
        if result.get('canvas_functions',{}).get('preview',True): connect(result,key,PREVIEW,'preview')
    target=data.get('current_output') or next(iter(data['outputs']),None)
    if image and target: connect(result,image['source'],target,'image')
    for c in data['canvases'].values():
        if c['name']=='預設畫布' or (c['name'].startswith('畫布 ') and c['name'][3:].isdigit()): c['name']=next_canvas_name(result)
    if compiled_outputs(result)!=before: raise ValueError('連線更新前後文字不一致，保留原資料。')
    validate_state(result); return result
