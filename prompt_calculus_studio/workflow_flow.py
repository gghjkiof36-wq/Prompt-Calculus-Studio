"""Workflow order and image bindings, shared without Qt or filesystem access."""
import copy

ORDER_CARD='__workflow_order__'
IMAGE_TYPES=('LoadImage','PreviewImage','SaveImage')


def upgrade(state):
    result=copy.deepcopy(state); data=result['multi_output']
    if data['version']<4:
        data['version']=4
        data['workflow_order']=dict(visible=False,items=[],established=list(dict.fromkeys(b['workflow'] for b in data['bindings'])))
        from .generation import active_profile
        profile=active_profile(result)
        for key,clip in data['clip_inputs'].items():
            bindings=[b for b in data['bindings'] if b['clip']==key]
            clip['workflow']=next((b['workflow'] for b in bindings if profile and b['workflow']==profile['id']),bindings[0]['workflow'] if bindings else None)
    return result


def image_nodes(profile):
    return [(key,node) for key,node in profile['graph'].items() if node['class_type'] in IMAGE_TYPES]


def register(state,workflow):
    order=state['multi_output'].get('workflow_order')
    if order is not None and workflow not in order['established']: order['established'].append(workflow)


def ordered_ids(state,ids):
    order=state['multi_output'].get('workflow_order',{})
    preferred=order.get('items',[])
    return list(dict.fromkeys(i for i in preferred+order.get('established',[])+list(ids) if i in ids))


def workflow_ids(state):
    """One row per bound workflow, including disconnected bindings for editing."""
    data=state['multi_output']
    ids=list(dict.fromkeys(v['workflow'] for v in data['clip_inputs'].values() if v.get('workflow')))
    return ordered_ids(state,ids)


def set_position(state,workflow,position):
    ids=workflow_ids(state)
    if workflow not in ids:raise ValueError('工作流尚未綁定。')
    ids.remove(workflow); ids.insert(max(0,min(len(ids),position-1)),workflow)
    state['multi_output']['workflow_order']['items']=ids


def execution_profiles(state):
    from .clip_flow import source
    from .generation import validate_profile
    data=state['multi_output']; profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}
    used={v['workflow'] for key,v in data['clip_inputs'].items() if v.get('workflow') and source(state,key) is not None}
    from .flow_data import incoming
    used.update(v['workflow'] for key,v in data.get('image_inputs',{}).items() if v.get('workflow') and incoming(state,key,'image') is not None)
    result=[]
    for ident in ordered_ids(state,used):
        if ident not in profiles:raise ValueError('執行工作流已移除，請重新綁定。')
        profile=profiles[ident]; validate_profile(profile); result.append(profile)
    if not result:raise ValueError('請連接 Prompt 輸出，並選擇 CLIP 的工作流與節點。')
    return result


def bind_image(state,key,workflow,node):
    images=state.get('canvas_functions',{}).get('images',{})
    if key not in images: raise ValueError('加載圖片模組已移除。')
    if workflow is None:
        images[key].pop('binding',None);images[key].pop('selection',None); return
    profile=next((p for p in state.get('generation',{}).get('profiles',[]) if p['id']==workflow),None)
    if profile is None or node not in dict(image_nodes(profile)): raise ValueError('圖片節點已移除或不支援。')
    binding=dict(workflow=workflow,node=node)
    if images[key].get('binding')!=binding:images[key].pop('selection',None)
    images[key]['binding']=binding


def validate(state):
    data=state['multi_output']; order=data.get('workflow_order',{})
    if order.get('chain') is not None:
        from .chain_model import validate as validate_chain
        validate_chain(order['chain'])
    if not isinstance(order,dict) or type(order.get('visible')) is not bool: raise ValueError('工作流排序設定無效。')
    for name in ('items','established'):
        values=order.get(name)
        if not isinstance(values,list) or len(values)>5100 or any(not isinstance(v,str) or not v for v in values) or len(set(values))!=len(values):
            raise ValueError('工作流排序重複或無效。')
    for value in state.get('canvas_functions',{}).get('images',{}).values():
        if type(value.get('show_image',True)) is not bool: raise ValueError('圖片顯示選項無效。')
        binding=value.get('binding')
        if binding is not None and (not isinstance(binding,dict) or any(not isinstance(binding.get(k),str) or not binding[k] for k in ('workflow','node'))):
            raise ValueError('圖片工作流綁定無效。')
    for value in data.get('clip_inputs',{}).values():
        if value.get('text_source','pcs') not in ('pcs','web'):raise ValueError('CLIP 提示詞來源無效。')
        if value.get('workflow') is not None and (not isinstance(value['workflow'],str) or not value['workflow']): raise ValueError('CLIP 工作流選擇無效。')
