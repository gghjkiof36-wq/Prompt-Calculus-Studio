"""Flow-terminal control cables. They carry workflow identity, not prompts."""
from .workflow_flow import ORDER_CARD
from .chain_model import definition,new_definition,stage

ADD=ORDER_CARD+'::add'


def endpoint(ident):return ORDER_CARD+'::'+ident


def terminal(state,key):
    data=state['multi_output']
    return data['clip_inputs'].get(key) or data.get('image_inputs',{}).get(key)


def target(state,key):
    return next((s for s in (definition(state) or {}).get('stages',[]) if endpoint(s['id'])==key),None)


def valid_edge(state,source,destination):
    value=terminal(state,source)
    return bool(value and value.get('workflow') and (destination==ADD or target(state,destination)))


def connect(state,source,destination):
    """Return the concrete stage socket, creating a draft from a dropped flow."""
    from .clip_flow import source as clip_source
    from .flow_data import incoming
    value=terminal(state,source);plan=definition(state)
    if plan is None:
        plan=new_definition();state['multi_output']['workflow_order']['chain']=plan
    if destination==ADD:
        profile=next((p for p in state['generation']['profiles'] if p['id']==value['workflow']),None)
        if profile is None:raise ValueError('請先綁定這條流程的 ComfyUI 工作流。')
        item=stage(profile['id'],profile['name']);graph=profile['graph'];data=state['multi_output']
        outputs=[k for k,n in graph.items() if n['class_type'] in ('SaveImage','PreviewImage')]
        item['output']=outputs[0] if len(outputs)==1 else None
        if plan['stages']:
            inputs=[k for k,n in graph.items() if n['class_type']=='LoadImage' and isinstance(n['inputs'].get('image'),str)]
            if len(inputs)!=1:raise ValueError('這條流程有多個或沒有 LoadImage；請用「新增階段」明確選擇接收節點。')
            item['upstream']=dict(stage=plan['stages'][-1]['id'],node=inputs[0],mode='all',index=0)
        for b in data['bindings']:
            if b['workflow']!=profile['id']:continue
            key=clip_source(state,b['clip']);mode=data['clip_inputs'][b['clip']].get('text_source','pcs')
            if key or mode=='web':item['texts'].append(dict(node=b['node'],field=b['field'],mode=mode,**({'source':key} if mode=='pcs' else {})))
        for key,binding in data.get('image_inputs',{}).items():
            key_source=incoming(state,key,'image')
            if binding.get('workflow')==profile['id'] and key_source and binding.get('node')!=(item.get('upstream') or {}).get('node'):
                item['images'].append(dict(node=binding['node'],source=key_source))
        plan['stages'].append(item);destination=endpoint(item['id'])
    item=target(state,destination)
    if item['workflow']!=value['workflow']:raise ValueError('這條流程與階段的工作流不同；請先編輯該階段。')
    item['control']=source
    return destination


def attach_stage(state,item):
    """An explicitly added stage uses a real CLIP/image terminal when available."""
    from .multi_output import connect as add_edge
    from .flow_data import add_image_input
    data=state['multi_output'];old=item.get('control')
    choices=[k for k,v in {**data['clip_inputs'],**data.get('image_inputs',{})}.items() if v.get('workflow')==item['workflow']]
    key=old if old in choices else next(iter(choices),None)
    if key is None:
        profile=next(p for p in state['generation']['profiles'] if p['id']==item['workflow'])
        node=(item.get('upstream') or {}).get('node') or next((k for k,n in profile['graph'].items() if n['class_type']=='LoadImage'),None)
        if node:
            x,y=state.get('text_positions',{}).get(ORDER_CARD,[1100,450]);key=add_image_input(state,(x-520,y+len(data['image_inputs'])*300))
            data['image_inputs'][key].update(workflow=item['workflow'],node=node,name=item['name']+' · 圖片輸入')
    if key:add_edge(state,key,endpoint(item['id']),'control')


def caption(state,key):
    if key==ADD:return '加入流程'
    value=target(state,key)
    return value['name'] if value else '流程'
