"""Atomic document-only Canvas selection operations. Never copy execution jobs."""
import copy
from . import composition as comp
from . import multi_output as model
from .core import uid

COLLECTIONS = ('canvases', 'outputs', 'clip_inputs', 'schedulers', 'image_inputs', 'stages')
MIME = 'application/x-pcs-canvas-selection+json'


def capture(state, keys):
    data=state['multi_output']; selected=set(keys); roots={}; nodes={}
    # Selecting a containing card also copies its content, exactly once.
    for key in selected & data['canvases'].keys():
        selected.update(data['canvases'][key]['members'])
    for key in selected:
        root_key, _, node_id=key.partition(':')
        root=state.get('uses',{}).get(root_key)
        if root is None:continue
        if not node_id:roots[root_key]=copy.deepcopy(root)
        elif root_key not in selected:
            part=comp.find(root,node_id)
            if part:nodes[key]=copy.deepcopy(part)
    for key in list(nodes):
        part_ids={p['id'] for other,value in nodes.items() if other!=key for p in comp.walk(value)}
        if nodes[key]['id'] in part_ids:nodes.pop(key)
    collections={name:{key:copy.deepcopy(value) for key,value in data.get(name,{}).items() if key in selected} for name in COLLECTIONS}
    images={key:copy.deepcopy(value) for key,value in state.get('canvas_functions',{}).get('images',{}).items() if key in selected}
    sources={key:copy.deepcopy(value) for key,value in state.get('canvas_functions',{}).get('text_sources',{}).items() if key in selected}
    included=set(roots)|set(nodes)|set(images)|set(sources)|set().union(*(set(v) for v in collections.values()))
    positions={key:copy.deepcopy(state.get('text_positions',{}).get(key,[0,0])) for key in included}
    for key,value in collections['canvases'].items():positions[key]=list(value['position'])
    for key,value in collections.get('clip_inputs',{}).items():
        value['binding_template']=[copy.deepcopy(b) for b in data.get('bindings',[]) if b.get('clip')==key]
    edges=[copy.deepcopy(edge) for edge in data['connections'] if edge['source'].split('::')[0] in included and edge['destination'].split('::')[0] in included]
    return dict(format='pcs-canvas-selection',version=1,roots=roots,nodes=nodes,collections=collections,images=images,text_sources=sources,
                positions=positions,sizes={k:copy.deepcopy(v) for k,v in state.get('text_sizes',{}).items() if k in included},connections=edges)


def paste(state, payload, position, canvas=None):
    if not isinstance(payload,dict) or payload.get('format')!='pcs-canvas-selection' or payload.get('version')!=1:
        raise ValueError('剪貼簿不是可用的 PCS 模組。')
    collections=payload['collections']; positions=payload['positions']; data=state['multi_output']
    if not positions:return []
    if len(positions)>500:raise ValueError('一次最多貼上 500 個模組。')
    dx=position[0]-min(p[0] for p in positions.values());dy=position[1]-min(p[1] for p in positions.values())
    mapping={key:uid() for key in positions}
    def endpoint(key):
        root,sep,part=key.partition('::')
        return mapping.get(root,root)+(sep+part if sep else '')
    for name in COLLECTIONS:
        for key,value in collections.get(name,{}).items():
            item=copy.deepcopy(value)
            if name=='canvases':
                item['members']=[mapping[m] for m in item['members'] if m in mapping]
                item['position']=[item['position'][0]+dx,item['position'][1]+dy]
            elif name=='outputs':
                item.update(canvas=None,canvases=[],text_sources=[],list_state={})
            elif name in ('clip_inputs','image_inputs'):
                # A destination is unique. Copies must be deliberately rebound.
                if name=='image_inputs':item['binding_template']={k:item.get(k) for k in ('workflow','node')}
                item['workflow']=None
                if name=='image_inputs':item['node']=None
            elif name=='schedulers':
                item['policies']={endpoint(k):v for k,v in item.get('policies',{}).items() if k.split('::')[0] in mapping}
            data.setdefault(name,{})[mapping[key]]=item
    for key,root in {**payload.get('roots',{}),**payload.get('nodes',{})}.items():
        state.setdefault('uses',{})[mapping[key]]=comp.clone_node(root)
        if not any(mapping[key] in c['members'] for c in data['canvases'].values()) and canvas in data['canvases']:
            model.assign(state,mapping[key],canvas)
    for key,value in payload.get('images',{}).items():
        item=copy.deepcopy(value);item.pop('binding',None);item.pop('legacy_binding',None)
        if item.get('stage_reference') in mapping:item['stage_reference']=mapping[item['stage_reference']]
        else:item.pop('stage_reference',None)
        state.setdefault('canvas_functions',{}).setdefault('images',{})[mapping[key]]=item
    for key,value in payload.get('text_sources',{}).items():
        item=copy.deepcopy(value);item['root']=comp.clone_node(item['root']);item['root'].pop('supply_cursor',None)
        state.setdefault('canvas_functions',{}).setdefault('text_sources',{})[mapping[key]]=item
    for key,pos in positions.items():state.setdefault('text_positions',{})[mapping[key]]=[pos[0]+dx,pos[1]+dy]
    for key,size in payload.get('sizes',{}).items():state.setdefault('text_sizes',{})[mapping[key]]=copy.deepcopy(size)
    for edge in payload.get('connections',[]):model.connect(state,endpoint(edge['source']),endpoint(edge['destination']),edge['kind'])
    # Source order is a document choice, independent of connection creation.
    # Keep only copied, still-connected sources; never revive an external edge.
    from .flow_data import text_sources,canvas_ids
    for key,value in collections.get('outputs',{}).items():
        item=data['outputs'][mapping[key]]
        connected={e['source'] for e in data['connections'] if e['destination']==mapping[key] and e['kind'] in ('text','clip')}
        item['text_sources']=[endpoint(k) for k in text_sources(value) if k.split('::')[0] in mapping and endpoint(k) in connected]
        item['canvases']=[mapping[k] for k in canvas_ids(value) if k in mapping and mapping[k] in connected]
        item['canvas']=next(iter(item['canvases']),None)
    model.reconcile(state)
    from .prompt_groups import mark_version
    mark_version(state)
    return list(mapping.values())


def remove(state, keys):
    """Delete one selection in a single transaction, keeping container members."""
    data=state['multi_output']; selected=set(keys); removed=set()
    nested={}
    for key in selected:
        root_key,_,node_id=key.partition(':')
        if node_id and root_key in state.get('uses',{}) and root_key not in selected:
            nested.setdefault(root_key,set()).add(node_id)
    for root_key,ids in nested.items():
        def prune(root):
            for field in ('children','overlays'):
                root[field]=[part for part in root[field] if part['id'] not in ids]
                for part in root[field]:prune(part)
        prune(state['uses'][root_key])
    for edge in list(data['connections']):
        if edge['id'] in selected or edge['source'].split('::')[0] in selected or edge['destination'].split('::')[0] in selected:
            model.disconnect(state,edge['id'])
    for key in selected:
        root_key,_,node_id=key.partition(':');root=state.get('uses',{}).get(root_key)
        if root is not None:
            if not node_id:comp.remove_usage(state,root_key);removed.add(root_key)
            continue
        for name in COLLECTIONS:
            if key in data.get(name,{}):data[name].pop(key);removed.add(key)
        if key in state.get('canvas_functions',{}).get('images',{}):state['canvas_functions']['images'].pop(key);removed.add(key)
        if key in state.get('canvas_functions',{}).get('text_sources',{}):state['canvas_functions']['text_sources'].pop(key);removed.add(key)
        if key=='__result_preview__':state.setdefault('canvas_functions',{})['preview']=False
    for edge in list(data['connections']):
        if edge['id'] in selected or edge['source'].split('::')[0] in removed or edge['destination'].split('::')[0] in removed:
            model.disconnect(state,edge['id'])
    data['bindings']=[b for b in data['bindings'] if b.get('clip') not in removed and b.get('output') not in removed]
    if data.get('current_output') in removed:
        other=next(iter(data['outputs']),None)
        if other is not None:model.select_output(state,other)
        else:
            data['current_output']=None
            state.update(draft=None,draft_base='')
    model.reconcile(state)
