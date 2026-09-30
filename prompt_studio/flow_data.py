"""Typed Canvas values and input-only scheduling. No Qt, transport or execution."""
import copy
import hashlib
import json

from .module_contracts import DATA_TYPES,WIRES
TYPES = DATA_TYPES
TYPE_NAMES = {key:WIRES[key].label for key in TYPES}
CAPACITY = 10


def upgrade(state):
    if state['multi_output']['version'] >= 6:
        return state
    result = copy.deepcopy(state)
    data = result['multi_output']
    data.setdefault('schedulers',{});data.setdefault('image_inputs',{});data['version']=6
    for output in data['outputs'].values():
        output.setdefault('canvases',[output['canvas']] if output['canvas'] else [])
        output['text_sources']=list(output['canvases'])
    for canvas in data['canvases'].values():canvas.pop('source_revision',None)
    return result


def canvas_ids(output):
    return output.get('canvases', [output['canvas']] if output.get('canvas') else [])


def text_sources(output):return output.get('text_sources',canvas_ids(output))


def source_name(state,key):
    data=state['multi_output']
    if key in data['canvases']:return data['canvases'][key]['name']
    if key in data['outputs']:return data['outputs'][key]['name']
    if key in data.get('stages',{}):return data['stages'][key]['name']
    route=channel(state,key)
    if route:return data['schedulers'][route[0]]['name']+' · '+route[1]['name']
    image=state.get('canvas_functions',{}).get('images',{}).get(key,{})
    return image.get('name') or (image.get('source') or {}).get('name') or '圖片來源'


def endpoint(key, channel):
    return key + '::' + channel


def channel(state, key):
    parent, separator, name = key.partition('::')
    value = state['multi_output'].get('schedulers', {}).get(parent)
    if separator and value:
        entry = next((c for c in value['channels'] if c['id'] == name), None)
        if entry:
            return parent, entry
    return None


def incoming(state, destination, kind):
    return next((c['source'] for c in state['multi_output']['connections']
                 if c['destination'] == destination and c['kind'] == kind), None)


def add_scheduler(state, position=(400, 300)):
    from .multi_output import ident
    key = ident('schedule_')
    state['multi_output']['schedulers'][key] = dict(name='預排程', channels=[
        dict(id=kind+'1', type=kind, name=TYPE_NAMES[kind]) for kind in TYPES])
    state.setdefault('text_positions', {})[key] = list(position)
    return key


def add_image_input(state, position=(1000, 300)):
    from .multi_output import ident
    key = ident('image_input_')
    state['multi_output']['image_inputs'][key] = dict(name='ComfyUI 圖片輸入', workflow=None, node=None)
    state.setdefault('text_positions', {})[key] = list(position)
    return key


def valid_edge(state, source, destination, kind):
    if state['multi_output']['version']>=7:
        from .module_contracts import can_connect
        return can_connect(state,source,destination,kind)
    elif kind=='control':
        from .chain_connections import valid_edge as control_edge
        return control_edge(state,source,destination)
    data = state['multi_output']
    images = state.get('canvas_functions', {}).get('images', {})
    src, dst = channel(state, source), channel(state, destination)
    if src and src[1]['type'] != kind or dst and dst[1]['type'] != kind:
        return False
    sources = {
        'clip': set(data['outputs']) | set(images),
        'image': set(images) | set(data['canvases']),
        'content': set(images),
        'text': set(data['canvases']),
    }
    targets = {
        'clip': set(data['clip_inputs']) | set(data['canvases']) | (set(data['outputs']) if data['version']>=6 else set()),
        'image': set(data['canvases']) | set(data['outputs']) | set(data.get('image_inputs', {})) | {'__result_preview__'},
        'content': set(data['canvases']),
        'text': set(data['outputs']),
    }
    if kind not in sources:
        return False
    return bool((src or source in sources[kind]) and (dst or destination in targets[kind])
                and source.split('::')[0] != destination.split('::')[0])


def check_cycle(state, source, destination):
    start, target = destination.split('::')[0], source.split('::')[0]
    todo, seen = [start], set()
    while todo:
        key = todo.pop()
        if key == target:
            raise ValueError('這條連線會形成循環；完成後換圖由執行器處理，不需要回接。')
        if key in seen:
            continue
        seen.add(key)
        todo.extend(c['destination'].split('::')[0] for c in state['multi_output']['connections']
                    if c['source'].split('::')[0] == key)


def value(kind, content, **origin):
    result = dict(type=kind, value=copy.deepcopy(content), origin=copy.deepcopy(origin))
    validate_value(result)
    return result


def validate_value(item):
    if not isinstance(item, dict) or item.get('type') not in TYPES or not isinstance(item.get('origin', {}), dict):
        raise ValueError('預排程資料型別無效。')
    content = item.get('value')
    if item['type'] == 'clip' and not isinstance(content, str):
        raise ValueError('文字輸入必須為文字，空字串也是有效內容。')
    if item['type'] == 'image':
        from .generation import validate_generation
        validate_generation(dict(mode='img2img',profiles=[],source=content))
    if item['type'] == 'content':
        if not isinstance(content, dict) or content.get('kind') not in ('modules', 'raw'):
            raise ValueError('圖片中的文字組合不可辨識。')
        if content['kind'] == 'raw' and not isinstance(content.get('text'), str):
            raise ValueError('圖片缺少原始正面提示詞。')
        if content['kind'] == 'modules':
            from .composition import validate_node
            if not isinstance(content.get('roots'), list):
                raise ValueError('圖片中的文字模塊無效。')
            for root in content['roots']:
                validate_node(root)
    if len(json.dumps(item, ensure_ascii=False).encode()) > 4 * 1024 * 1024:
        raise ValueError('單項預排程輸入超過 4 MB。')
    return item


def resolve(state, key, kind, seen=None):
    seen = set() if seen is None else set(seen)
    marker = (key, kind)
    if marker in seen:
        raise ValueError('資料流包含循環。')
    seen.add(marker)
    route = channel(state, key)
    if route:
        if route[1]['type'] != kind:
            raise ValueError('預排程端口型別不符。')
        saved = state.get('_execution_inputs', {}).get(key)
        if saved is not None:
            if validate_value(saved)['type'] != kind:
                raise ValueError('保存資料與目前端口不符。')
            return copy.deepcopy(saved)
        source = incoming(state, key, kind)
        if source is None:
            raise ValueError('預排程的「'+route[1]['name']+'」尚未接入資料。')
        return resolve(state, source, kind, seen)
    data = state['multi_output']
    if key in data.get('stages',{}):
        result=state.get('_stage_results',{}).get(key)
        if not result:raise ValueError(data['stages'][key]['name']+'：本次結果尚未產生。')
        if kind=='clip':
            from .result_data import text_value
            text=text_value(state,key)
            return value('clip',text,stage=key,run=result.get('run'))
        if kind=='image':
            images=result.get('images',[])
            if not images:raise ValueError('Stage 本次沒有圖片結果。')
            return value('image',images[0],stage=key,run=result.get('run'))
    if kind == 'clip' and key in data['outputs']:
        from .multi_output import compile_output
        result = compile_output(state, key)
        return value(kind, result['final_prompt'], output=key, canvases=canvas_ids(data['outputs'][key]),
                     canvas_names=[data['canvases'][cid]['name'] for cid in canvas_ids(data['outputs'][key])],manual_draft=result['manual_draft'])
    images = state.get('canvas_functions', {}).get('images', {})
    if key in images:
        if kind=='clip' and (images[key].get('reader')=='text' or images[key].get('text_field')):
            from .result_data import stage_source,text_value
            stage=stage_source(state,key,'clip' if images[key].get('reader')=='text' else 'image')
            if not stage:raise ValueError('請連接 Stage 文字結果。')
            return value('clip',text_value(state,stage,images[key].get('text_field')),stage=stage,source=key)
        linked=incoming(state,key,'image') or images[key].get('stage_reference')
        if linked and kind=='image':return value('image',image_list(state,key)[0],source=key)
        source = images[key].get('source')
        if not source:
            raise ValueError('圖片來源尚未選擇圖片。')
        origin = dict(source=key, image=source.get('sha256'), name=source.get('name','圖片提示詞'),index=images[key].get('index', 0))
        if kind == 'image':
            return value(kind, source, **origin)
        content = images[key].get('content')
        if not content:
            raise ValueError(images[key].get('content_error') or '這張圖片沒有可辨識的正面提示詞或 PCS 文字模塊。')
        if kind == 'content':
            return value(kind, content, **origin)
        if kind == 'clip':
            return value(kind, content.get('text', ''), **origin)
    raise ValueError('資料來源已移除或型別不相容。')


def image_list(state,key,seen=None):
    seen=set(seen or ())
    if key in seen:raise ValueError('圖片來源形成循環。')
    seen.add(key);data=state['multi_output']
    if key in data.get('stages',{}):
        result=state.get('_stage_results',{}).get(key,{})
        images=result.get('images',[])
        if not images:raise ValueError(data['stages'][key]['name']+'：本次圖片尚未產生。')
        return copy.deepcopy(images)
    images=state.get('canvas_functions',{}).get('images',{})
    if key in images:
        item=images[key];link=incoming(state,key,'image') or item.get('stage_reference')
        if link:
            values=image_list(state,link,seen);index=item.get('input_index')
            node=item.get('output_node')
            if node:
                values=[v for v in values if v.get('reference',{}).get('node')==node]
                if not values:raise ValueError('本次結果的圖片節點 #'+node+' 沒有圖片。')
            if index is not None:
                if type(index) is not int or not 0<=index<len(values):raise ValueError('圖片來源指定序號不存在。')
                values=[values[index]]
            return values
        return copy.deepcopy(item.get('items') or ([item['source']] if item.get('source') else []))
    from .flow_data import channel
    route=channel(state,key)
    if route:
        saved=state.get('_execution_inputs',{}).get(key)
        if saved:return [copy.deepcopy(saved['value'])]
        source=incoming(state,key,'image')
        if source:return image_list(state,source,seen)
    return [resolve(state,key,'image')['value']]


def capture_inputs(state, scheduler, image_resolver=None):
    channels = state['multi_output']['schedulers'][scheduler]['channels']
    result = {}
    for item in channels:
        key = endpoint(scheduler, item['id'])
        source = incoming(state, key, item['type'])
        if source is not None:
            captured=(value('image',image_resolver(state,source),canvas=source)
                      if item['type']=='image' and source in state['multi_output']['canvases'] and image_resolver
                      else resolve(state, source, item['type']))
            output=captured['origin'].get('output')
            if item['type']=='clip' and output in state['multi_output']['outputs']:
                current=state['multi_output']['outputs'][output]
                roots=[copy.deepcopy(state['uses'][k]) for cid in canvas_ids(current)
                       for k in state['multi_output']['canvases'][cid]['members']]
                captured['origin']['composition']=dict(kind='modules' if roots else 'raw',roots=roots,
                    text=captured['value'],manual_text=captured['value'] if current.get('draft') is not None or
                    any(state['multi_output']['canvases'][cid].get('raw_prompt') is not None for cid in canvas_ids(current)) else None)
                if state['multi_output']['version']>=7:
                    captured['origin']['canvas_records']={cid:dict(canvas=copy.deepcopy(state['multi_output']['canvases'][cid]),
                        roots={rid:copy.deepcopy(state['uses'][rid]) for rid in state['multi_output']['canvases'][cid]['members']}) for cid in canvas_ids(current)}
            result[key] = captured
    if not result:
        raise ValueError('請先將文字、圖片或文字組合接入預排程。')
    return result


def upstream(state, keys):
    todo, seen = list(keys), set()
    while todo:
        key = todo.pop()
        if key in seen:
            continue
        seen.add(key)
        route = channel(state, key)
        if route:
            seen.add(route[0])
        todo.extend(c['source'] for c in state['multi_output']['connections'] if c['destination'] == key)
        if key in state['multi_output']['outputs']:
            todo.extend(canvas_ids(state['multi_output']['outputs'][key]))
    return seen


def workflow_nodes(state, workflow):
    data = state['multi_output']
    sinks = [key for key, node in data['clip_inputs'].items() if node.get('workflow') == workflow]
    sinks += [key for key, node in data.get('image_inputs', {}).items() if node.get('workflow') == workflow]
    return upstream(state, sinks)


def scheduler_for(state, workflow):
    selected = workflow_nodes(state, workflow) & state['multi_output'].get('schedulers', {}).keys()
    if len(selected) > 1:
        raise ValueError('同一工作流請使用一個預排程，將多路輸入接到它的不同端口。')
    return next(iter(selected), None)


def source_for(state, workflow):
    selected = [key for key in workflow_nodes(state, workflow)
                if state.get('canvas_functions', {}).get('images', {}).get(key, {}).get('iterate')]
    if len(selected) > 1:
        raise ValueError('同一輪只允許一個圖片來源逐張切換；其他圖片來源請使用單張。')
    return selected[0] if selected else None


def materialize(state):
    """Replace only image-owned roots when the input identity changes."""
    data = state['multi_output'];state.setdefault('uses',{})
    order=[];visiting=set()
    def visit(key):
        if key in order:return
        if key in visiting:raise ValueError('畫布資料流形成循環。')
        visiting.add(key)
        for kind in ('clip','content'):
            source=incoming(state,key,kind)
            if source:
                for other in upstream(state,[source]) & data['canvases'].keys():visit(other)
        visiting.remove(key);order.append(key)
    for key in data['canvases']:visit(key)
    for key in order:
        canvas=data['canvases'][key]
        kind = 'content' if incoming(state, key, 'content') is not None else 'clip'
        source = incoming(state, key, kind)
        if source is None:
            prior=canvas.pop('source_members',[])
            for ident in prior:
                state['uses'].pop(ident,None);state.get('text_positions',{}).pop(ident,None)
            canvas['members']=[i for i in canvas['members'] if i not in prior]
            for field in ('raw_prompt','source_revision','source_error'):canvas.pop(field,None)
            continue
        try:
            item = resolve(state, source, kind)
            content = item['value'] if kind == 'content' else dict(kind='raw', text=item['value'])
            signature = hashlib.sha256(json.dumps(item, sort_keys=True).encode()).hexdigest()
            canvas.pop('source_error', None)
        except ValueError as exc:
            content = dict(kind='raw', text='')
            signature = 'error:'+str(exc)
            canvas['source_error'] = str(exc)
        if canvas.get('source_revision') == signature:
            continue
        prior = canvas.get('source_members', [])
        for ident in prior:
            state['uses'].pop(ident, None)
            state.get('text_positions', {}).pop(ident, None)
        canvas['members'] = [i for i in canvas['members'] if i not in prior]
        new = [];roots=content.get('roots', [])
        if data['version']>=6 and content['kind']=='raw' and not canvas.get('source_error'):
            from .composition import node
            root=node(item.get('origin',{}).get('name') or source_name(state,source),content['text'])
            root['opaque_source']=True;roots=[root]
        for index, root in enumerate(roots):
            ident = 'image_text_'+hashlib.sha256((key+signature+str(index)).encode()).hexdigest()[:24]
            state['uses'][ident] = copy.deepcopy(root)
            new.append(ident)
            x, y = canvas['position']
            state.setdefault('text_positions', {})[ident] = [x+24+(index % 2)*300, y+140+(index//2)*150]
        canvas['members'] = new+canvas['members']
        canvas.update(source_members=new, source_revision=signature,
                      raw_prompt=content.get('text') if content['kind']=='raw' and data['version']<6 else content.get('manual_text'))
    from .core import output_groups
    if 'output_order' in state:state['output_order']=output_groups(state,include_hidden=True)
    return state


def validate(state):
    data = state['multi_output']
    for name in ('schedulers', 'image_inputs'):
        if not isinstance(data.get(name), dict) or len(data[name]) > 100:
            raise ValueError('預排程或圖片輸入資料無效。')
    for name in ('schedulers','image_inputs'):
        for key,item in data[name].items():
            if not isinstance(key,str) or not key or '::' in key or not isinstance(item,dict) or not isinstance(item.get('name'),str) or not item['name'].strip():
                raise ValueError('模塊名稱或識別無效。')
    for scheduler in data['schedulers'].values():
        channels = scheduler.get('channels')
        if not isinstance(channels, list) or not 1 <= len(channels) <= 32:
            raise ValueError('預排程端口數量無效。')
        ids = set()
        for item in channels:
            if not isinstance(item, dict) or item.get('type') not in TYPES or not isinstance(item.get('id'), str) or not item['id'] or '::' in item['id'] or item['id'] in ids:
                raise ValueError('預排程端口格式無效。')
            ids.add(item['id'])
    targets = set()
    for item in data['image_inputs'].values():
        if item.get('workflow') is None and item.get('node') is None:
            continue
        if any(not isinstance(item.get(k), str) or not item[k] for k in ('workflow', 'node')):
            raise ValueError('圖片接收節點綁定無效。')
        pair = (item['workflow'], item['node'])
        if pair in targets:
            raise ValueError('同一個 LoadImage 節點不可重複綁定。')
        targets.add(pair)
    for output in data['outputs'].values():
        ids = canvas_ids(output)
        if not isinstance(ids, list) or len(set(ids)) != len(ids) or any(k not in data['canvases'] for k in ids):
            raise ValueError('Prompt 的畫布來源重複或已移除。')
        if output.get('canvas') != (ids[0] if ids else None):
            raise ValueError('Prompt 的畫布順序不一致。')
        if data['version']>=6:
            sources=text_sources(output)
            if not isinstance(sources,list) or len(sources)!=len(set(sources)):
                raise ValueError('文字來源順序無效。')
            if [s for s in sources if s in data['canvases']]!=ids:
                raise ValueError('文字來源與畫布順序不一致。')
            target=next(k for k,v in data['outputs'].items() if v is output)
            connected=[c['source'] for c in data['connections'] if c['destination']==target and c['kind'] in ('text','clip')]
            if set(connected)!=set(sources):raise ValueError('文字来源連線不一致。')
    for item in state.get('_execution_inputs', {}).values():
        validate_value(item)
