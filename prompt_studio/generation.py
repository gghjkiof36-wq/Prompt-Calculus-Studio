"""Portable API workflow profiles and immutable per-submission inputs (no Qt)."""
import copy
import hashlib
import json
import math
import re
import secrets
import uuid
from pathlib import PurePosixPath

MODES=('txt2img','img2img')
PARAMETERS={'seed':'Seed','steps':'步數','cfg':'CFG','sampler_name':'採樣器','scheduler':'調度器',
            'denoise':'Denoise','width':'寬度','height':'高度'}
LIMITS={'seed':(0,2**64-1),'steps':(1,10000),'cfg':(0,100),'denoise':(0,1),'width':(0,16384),'height':(0,16384)}


def options(state):
    return state.get('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))


def active_profile(state):
    settings=options(state); mode=settings['mode']; ident=settings.get('chosen',{}).get(mode)
    return next((p for p in settings['profiles'] if p['id']==ident and p['mode']==mode),None)


def direct_mode(state):
    return options(state)['mode']=='img2img' or active_profile(state) is not None


def api_graph(value):
    graph=value.get('prompt',value) if isinstance(value,dict) else value
    if not isinstance(graph,dict) or not graph or len(graph)>2000 or 'nodes' in graph:
        raise ValueError('請匯入 ComfyUI「匯出 API」格式的工作流 JSON；一般畫布 JSON 不能直接運行。')
    if len(json.dumps(graph,ensure_ascii=False).encode())>2*1024*1024: raise ValueError('工作流超過 2 MB。')
    for key,node in graph.items():
        if not isinstance(key,str) or not isinstance(node,dict) or not isinstance(node.get('class_type'),str) or not isinstance(node.get('inputs'),dict):
            raise ValueError('工作流不是有效的 API 節點資料。')
    return copy.deepcopy(graph)


def text_fields(graph):
    return [(key,field) for key,node in graph.items() for field,value in node['inputs'].items()
            if field in ('text','text_g','text_l') and isinstance(value,str)]


def ancestors(graph,ident):
    found=set(); pending=[ident]
    while pending:
        key=pending.pop()
        if key in found or key not in graph: continue
        found.add(key)
        pending.extend(str(v[0]) for v in graph[key]['inputs'].values() if isinstance(v,list) and len(v)==2 and isinstance(v[0],(str,int)))
    return found


def suggested_text(graph,sampler):
    value=graph.get(sampler,{}).get('inputs',{}).get('positive')
    reachable=ancestors(graph,str(value[0])) if isinstance(value,list) and value else set()
    candidates=[key for key in text_fields(graph) if key[0] in reachable]
    return candidates[0] if len(candidates)==1 else None


def parameter_bindings(profile):
    graph=profile['graph']; result={}
    for group,fields in (('sampler',('seed','steps','cfg','sampler_name','scheduler','denoise')),('size',('width','height'))):
        ident=profile.get(group)
        for field in fields:
            value=graph.get(ident,{}).get('inputs',{}).get(field)
            if isinstance(value,(int,float,str)) and not isinstance(value,bool): result[field]=(ident,field)
    return result


def image_target(profile):
    """An explicit writable image binding, independent of the workflow category."""
    key=profile.get('image'); node=profile['graph'].get(key,{})
    return key if node.get('class_type')=='LoadImage' and isinstance(node.get('inputs',{}).get('image'),str) else None


def validate_profile(profile):
    if not isinstance(profile,dict) or profile.get('mode') not in MODES or not isinstance(profile.get('id'),str) or not profile['id']:
        raise ValueError('工作流設定無效。')
    if not isinstance(profile.get('name'),str) or not profile['name'].strip(): raise ValueError('工作流名稱不能空白。')
    graph=api_graph(profile.get('graph'))
    target=profile.get('prompt')
    if not profile.get('multi_text') and (not isinstance(target,(list,tuple)) or tuple(target) not in text_fields(graph)): raise ValueError('請選擇接收最終 Prompt 的文字欄位；已接線的文字不能覆寫。')
    if profile.get('image') or (profile['mode']=='img2img' and not profile.get('multi_text')):
        image=profile.get('image'); node=graph.get(image,{})
        if node.get('class_type')!='LoadImage' or not isinstance(node.get('inputs',{}).get('image'),str):
            raise ValueError('圖生圖工作流需要可寫入的 LoadImage 圖片節點。')
    for kind,classes in (('sampler',('KSampler',)),('size',('ImageScale','EmptyLatentImage','EmptySD3LatentImage'))):
        if profile.get(kind) and graph.get(profile[kind],{}).get('class_type') not in classes:
            raise ValueError('採樣或尺寸控制節點不支援；可選擇沿用工作流。')
    if profile.get('sampler'):
        inputs=graph[profile['sampler']]['inputs']; positive=inputs.get('positive'); latent=inputs.get('latent_image')
        upstream=ancestors(graph,str(latent[0])) if isinstance(latent,list) and latent else set()
        if not profile.get('multi_text') and profile.get('image') and profile['image'] not in upstream: raise ValueError('選定的來源圖片沒有連到這個採樣器。')
        if profile.get('size') and profile['size'] not in upstream: raise ValueError('選定的尺寸節點沒有連到這個採樣器。')
    if profile.get('seed_mode','fixed') not in ('fixed','increment','decrement','random'): raise ValueError('Seed 變更方式無效。')
    bindings=parameter_bindings(profile)
    values=profile.get('values',{})
    if not isinstance(values,dict) or any(k not in bindings for k in values): raise ValueError('參數沒有可寫入的工作流欄位。')
    effective={field:values.get(field,graph[ident]['inputs'][name]) for field,(ident,name) in bindings.items()}
    for field,value in effective.items():
        if field in LIMITS:
            lo,hi=LIMITS[field]
            if type(value) not in (int,float) or not math.isfinite(value) or not lo<=value<=hi: raise ValueError(PARAMETERS[field]+' 超出範圍。')
            if field in ('seed','steps','width','height') and type(value) is not int: raise ValueError(PARAMETERS[field]+' 必須是整數。')
        elif not isinstance(value,str) or not value.strip(): raise ValueError(PARAMETERS[field]+' 不能空白。')
    return profile


def validate_generation(value):
    if not isinstance(value,dict) or value.get('mode') not in MODES or not isinstance(value.get('profiles'),list) or len(value['profiles'])>50:
        raise ValueError('生成設定無效。')
    profiles={}
    for profile in value['profiles']:
        validate_profile(profile)
        if profile['id'] in profiles: raise ValueError('工作流 ID 重複。')
        profiles[profile['id']]=profile
    chosen=value.get('chosen',{})
    if not isinstance(chosen,dict) or any(mode not in MODES or (ident and (ident not in profiles or profiles[ident]['mode']!=mode)) for mode,ident in chosen.items()):
        raise ValueError('選取的工作流不存在。')
    source=value.get('source')
    if source is not None:
        if not isinstance(source,dict) or not re.fullmatch('[0-9a-f]{64}',str(source.get('sha256',''))): raise ValueError('來源圖片資料無效。')
        path=PurePosixPath(source.get('relative',''))
        if path.is_absolute() or '..' in path.parts or not str(path).startswith('originals/generation/') or '\\' in str(path) or ':' in str(path):
            raise ValueError('來源圖片位置無效。')
        if any(type(source.get(key)) is not int or source[key]<=0 for key in ('width','height')): raise ValueError('來源圖片尺寸無效。')
        if not isinstance(source.get('name'),str): raise ValueError('來源圖片名稱無效。')
    return value


def uploaded_image(value):
    name=value.get('name'); folder=value.get('subfolder','')
    if not isinstance(name,str) or not name or not isinstance(folder,str) or value.get('type','input')!='input': raise ValueError('ComfyUI 未回傳有效的來源圖片。')
    path=PurePosixPath(folder)/name
    if PurePosixPath(name).name!=name or path.is_absolute() or '..' in path.parts or '\\' in str(path) or ':' in str(path): raise ValueError('ComfyUI 回傳的圖片位置無效。')
    return str(path)


def validate_canvas_functions(value):
    if not isinstance(value,dict) or not isinstance(value.get('images'),dict) or len(value['images'])>100:
        raise ValueError('圖片模組資料無效。')
    if any(type(value.get(key,False)) is not bool for key in ('preview','preview_attached')): raise ValueError('圖片模組狀態無效。')
    attached=0
    for key,item in value['images'].items():
        if not isinstance(key,str) or not key.startswith('__source_') or not isinstance(item,dict) or type(item.get('attached')) is not bool:
            raise ValueError('圖片模組格式無效。')
        validate_generation(dict(mode='img2img',profiles=[],chosen={},source=item.get('source')))
        attached+=int(item['attached'])
    if attached>1: raise ValueError('一次只能拼接一張來源圖片。')


def submission(profile,snapshot,source=None,image='',index=0):
    validate_profile(profile); graph=copy.deepcopy(profile['graph']); values=copy.deepcopy(profile.get('values',{}))
    multi='multi_output' in snapshot['state']
    if multi:
        from .multi_output import bound_texts
        texts=bound_texts(snapshot['state'],profile)
        for binding in texts: graph[binding['node']]['inputs'][binding['field']]=binding['text']
        node,field=texts[0]['node'],texts[0]['field']
    else:
        node,field=profile['prompt']; graph[node]['inputs'][field]=snapshot['final_prompt']
    bindings=parameter_bindings(profile)
    actual={key:values.get(key,graph[ident]['inputs'][field]) for key,(ident,field) in bindings.items()}
    if 'seed' in actual:
        rule=profile.get('seed_mode','fixed')
        if rule=='random': actual['seed']=secrets.randbits(64)
        elif rule=='increment': actual['seed']=(int(actual['seed'])+index)%(2**64)
        elif rule=='decrement': actual['seed']=(int(actual['seed'])-index)%(2**64)
    for key,(ident,input_name) in bindings.items(): graph[ident]['inputs'][input_name]=actual[key]
    generation=dict(mode=profile['mode'],workflow=profile['name'],workflow_sha256=hashlib.sha256(json.dumps(profile['graph'],sort_keys=True).encode()).hexdigest(),parameters=actual)
    replace_image=(source is not None or bool(image)) if multi else profile['mode']=='img2img'
    if replace_image:
        target=image_target(profile)
        if not target: raise ValueError('圖片無法載入工作流：請先綁定可接收圖片的 LoadImage 欄位。')
        if not source or not image: raise ValueError('請先選擇來源圖片。')
        graph[target]['inputs']['image']=image
        generation['source']={key:source[key] for key in ('name','sha256','width','height')}
        generation['source']['uploaded']=image
    marker=dict(snapshot=copy.deepcopy(snapshot),node_id=node,field=field,generation=generation)
    if multi: marker['texts']=texts
    return dict(prompt_id=str(uuid.uuid4()),prompt=graph,extra_data={'extra_pnginfo':{'prompt_studio_request':marker}})
