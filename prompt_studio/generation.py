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
    if state.get('multi_output',{}).get('version',1)>=4: return True
    return options(state)['mode']=='img2img' or active_profile(state) is not None


def store_profile(state,profile):
    """Save one validated profile; v4 execution is selected by CLIP bindings."""
    profile=copy.deepcopy(profile)
    if 'multi_output' in state: profile['multi_text']=True
    validate_profile(profile)
    settings=state.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))
    if len(settings['profiles'])>=50 and not any(p['id']==profile['id'] for p in settings['profiles']):
        raise ValueError('最多保留 50 份工作流。')
    settings['profiles']=[profile if p['id']==profile['id'] else p for p in settings['profiles']]
    if not any(p['id']==profile['id'] for p in settings['profiles']): settings['profiles'].append(profile)
    chosen=settings.setdefault('chosen',{})
    for mode,ident in list(chosen.items()):
        if ident==profile['id'] and mode!=profile['mode']: chosen[mode]=''
    if state.get('multi_output',{}).get('version',1)<4:
        chosen[profile['mode']]=profile['id']; settings['mode']=profile['mode']
    return profile


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
    if 'pcs_effective' in profile: validate_effective_profile(profile)
    return profile


def effective_hash(profile):
    """Content identity, never an ordering clock or evidence of backend execution."""
    keys=('graph','mode','prompt','image','sampler','size','seed_mode','multi_text')
    value={key:profile[key] for key in keys if key in profile}
    value['texts']=profile.get('pcs_effective',{}).get('texts',[])
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()


def validate_effective_scope(scope, profile_id):
    if (not isinstance(scope,dict) or set(scope)!={'library','workspace','workflow','server'}
            or any(not isinstance(v,str) or not v or len(v)>1000 for v in scope.values())
            or scope['workflow']!=profile_id):
        raise ValueError('有效工作流身分無效。')


def validate_effective_profile(profile):
    value=profile['pcs_effective']
    if not isinstance(value,dict) or type(value.get('protocol')) is not int or value['protocol']!=1:
        raise ValueError('不支援的有效工作流協定。')
    validate_effective_scope(value.get('scope'),profile['id'])
    if not isinstance(value.get('epoch'),str) or not re.fullmatch('[0-9a-f]{32}',value['epoch']):
        raise ValueError('有效工作流世代無效。')
    if type(value.get('revision')) is not int or value['revision']<1:
        raise ValueError('有效工作流版本無效。')
    if value.get('source') not in ('legacy-selection','pcs','web'):
        raise ValueError('有效工作流來源無效。')
    texts=value.get('texts')
    if not isinstance(texts,list) or len(texts)>100: raise ValueError('有效文字映射無效。')
    targets=set()
    for item in texts:
        if (not isinstance(item,dict) or set(item)!={'node','field','source'}
                or not isinstance(item.get('node'),str) or not isinstance(item.get('field'),str)
                or item.get('source') not in ('pcs','web')): raise ValueError('有效文字來源無效。')
        target=(item['node'],item['field'])
        if target not in text_fields(profile['graph']) or target in targets: raise ValueError('有效文字欄位不存在或重複。')
        targets.add(target)
    bindings=parameter_bindings(profile)
    derived={key:profile['graph'][node]['inputs'][field] for key,(node,field) in bindings.items()}
    if profile.get('values',{})!=derived: raise ValueError('有效工作流參數與舊值不一致，停止覆寫。')
    if value.get('hash')!=effective_hash(profile): raise ValueError('有效工作流內容與版本摘要不符。')
    receipts=value.get('receipts')
    if not isinstance(receipts,dict) or len(receipts)>1000: raise ValueError('有效工作流操作收據無效。')
    for ident,receipt in receipts.items():
        if (not isinstance(ident,str) or not ident or len(ident)>128 or not isinstance(receipt,dict)
                or set(receipt)!={'operation_hash','revision','hash','epoch'} or receipt.get('epoch')!=value['epoch']
                or type(receipt.get('revision')) is not int or not 1<=receipt['revision']<=value['revision']
                or any(not isinstance(receipt.get(key),str) or not re.fullmatch('[0-9a-f]{64}',receipt[key]) for key in ('operation_hash','hash'))):
            raise ValueError('有效工作流操作收據無效。')
    visual=profile.get('pcs_workflow')
    if visual is not None and (not isinstance(visual,dict) or not isinstance(visual.get('nodes'),list)
            or len(json.dumps(visual,ensure_ascii=False,allow_nan=False).encode())>2*1024*1024):
        raise ValueError('可視工作流資料無效。')
    if visual is not None and (type(profile.get('pcs_workflow_revision')) is not int
            or not 1<=profile['pcs_workflow_revision']<=value['revision']):
        raise ValueError('可視工作流版本無效。')


def initialize_effective_profile(profile,scope,texts=()):
    """Explicitly select a legacy version once; this does not claim a Web ack."""
    validate_profile(profile); validate_effective_scope(scope,profile['id'])
    if 'pcs_effective' in profile:
        if profile['pcs_effective']['scope']!=scope: raise ValueError('工作流所屬工作區或服務不符。')
        return copy.deepcopy(profile)
    candidate=copy.deepcopy(profile)
    for key,(node,field) in parameter_bindings(candidate).items():
        candidate['graph'][node]['inputs'][field]=candidate.get('values',{}).get(key,candidate['graph'][node]['inputs'][field])
    sources=[]
    for text in texts:
        node,field=text['node'],text['field']
        if (node,field) not in text_fields(candidate['graph']) or not isinstance(text.get('text'),str):
            raise ValueError('初始有效文字映射無效。')
        candidate['graph'][node]['inputs'][field]=text['text']
        sources.append(dict(node=node,field=field,source='pcs'))
    candidate['values']={key:candidate['graph'][node]['inputs'][field] for key,(node,field) in parameter_bindings(candidate).items()}
    candidate['pcs_effective']=dict(protocol=1,scope=copy.deepcopy(scope),epoch=uuid.uuid4().hex,revision=1,
                                    source='legacy-selection',texts=sources,receipts={})
    candidate['pcs_effective']['hash']=effective_hash(candidate)
    return validate_profile(candidate)


def accept_effective_profile(profile,operation):
    """Pure full-graph CAS. Caller must persist candidate and receipt together."""
    validate_profile(profile)
    current=profile.get('pcs_effective')
    if current is None: raise ValueError('工作流尚未確認有效版本。')
    required={'operation_id','scope','epoch','base_revision','source','graph','text_edits'}
    if not isinstance(operation,dict) or not required<=set(operation) or set(operation)-required-{'workflow'}:
        raise ValueError('工作流更新資料無效。')
    ident=operation['operation_id']
    if not isinstance(ident,str) or not ident or len(ident)>128: raise ValueError('工作流操作識別碼無效。')
    validate_effective_scope(operation['scope'],profile['id'])
    if operation['scope']!=current['scope']: raise ValueError('工作流所屬工作區或服務不符。')
    if operation['epoch']!=current['epoch']: raise ValueError('工作流已重建或還原，拒絕舊世代更新。')
    if type(operation['base_revision']) is not int or operation['base_revision']<1 or operation['source'] not in ('pcs','web'):
        raise ValueError('工作流更新版本或來源無效。')
    encoded=json.dumps(operation,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()
    if len(encoded)>4*1024*1024: raise ValueError('工作流更新超過 4 MB。')
    fingerprint=hashlib.sha256(encoded).hexdigest()
    prior=current['receipts'].get(ident)
    if prior:
        if prior['operation_hash']!=fingerprint: raise ValueError('同一操作識別碼包含不同內容。')
        return copy.deepcopy(profile),copy.deepcopy(prior)
    if operation['base_revision']!=current['revision']:
        raise ValueError('工作流版本衝突：目前為 '+str(current['revision'])+'，未覆寫有效內容。')
    if len(current['receipts'])>=1000: raise ValueError('工作流未清理收據已達上限，請先完成同步對帳。')
    candidate=copy.deepcopy(profile); candidate['graph']=api_graph(operation['graph'])
    edits=operation['text_edits']
    if not isinstance(edits,list) or len(edits)>100: raise ValueError('文字編輯欄位無效。')
    checked=set()
    for target in edits:
        if (not isinstance(target,list) or len(target)!=2 or any(not isinstance(v,str) for v in target)
                or tuple(target) not in text_fields(candidate['graph']) or tuple(target) in checked):
            raise ValueError('文字編輯欄位不存在或重複。')
        checked.add(tuple(target))
    sources={(v['node'],v['field']):copy.deepcopy(v) for v in current['texts']}
    for target,item in sources.items():
        node,field=target
        if target not in text_fields(candidate['graph']): raise ValueError('已綁文字欄位遭移除，請先處理綁定。')
        if candidate['graph'][node]['inputs'][field]!=profile['graph'][node]['inputs'][field] and target not in checked:
            raise ValueError('文字內容改動缺少明確編輯來源。')
    for node,field in checked: sources[node,field]=dict(node=node,field=field,source=operation['source'])
    value=candidate['pcs_effective']; value['texts']=[sources[key] for key in sorted(sources)]
    value['revision']+=1; value['source']=operation['source']
    if 'workflow' in operation:
        candidate['pcs_workflow']=copy.deepcopy(operation['workflow'])
        candidate['pcs_workflow_revision']=value['revision']
    # Preserve an older visual layout, but do not advance its revision merely
    # because API values changed. A frontend adapter must reconcile it first.
    candidate['values']={key:candidate['graph'][node]['inputs'][field] for key,(node,field) in parameter_bindings(candidate).items()}
    value['hash']=effective_hash(candidate)
    receipt=dict(operation_hash=fingerprint,revision=value['revision'],hash=value['hash'],epoch=value['epoch'])
    value['receipts'][ident]=receipt
    validate_profile(candidate)
    return candidate,copy.deepcopy(receipt)


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
        selection=item.get('selection')
        if selection is not None:
            if (not isinstance(selection,dict) or not isinstance(selection.get('collection'),str) or
                len(selection['collection'])!=64 or any(c not in '0123456789abcdef' for c in selection['collection'])):
                raise ValueError('圖片集合選擇無效。')
            image=selection.get('image')
            if not isinstance(image,dict) or any(not isinstance(image.get(k),str) for k in ('filename','subfolder','type')):
                raise ValueError('圖片選擇參照無效。')
        attached+=int(item['attached'])
    if attached>1: raise ValueError('一次只能拼接一張來源圖片。')


def submission(profile,snapshot,source=None,image='',index=0):
    validate_profile(profile); graph=copy.deepcopy(profile['graph']); values=copy.deepcopy(profile.get('values',{}))
    effective=profile.get('pcs_effective')
    multi='multi_output' in snapshot['state']
    if effective:
        original_texts,texts=effective_submission_texts(profile,snapshot)
        node,field=texts[0]['node'],texts[0]['field']
    elif multi:
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
    generation=dict(mode=profile['mode'],workflow=profile['name'],workflow_id=profile['id'],workflow_sha256=hashlib.sha256(json.dumps(profile['graph'],sort_keys=True).encode()).hexdigest(),parameters=actual)
    if profile.get('origin'):generation['origin']=copy.deepcopy(profile['origin'])
    if profile.get('frontend_id'):generation['frontend_id']=profile['frontend_id']
    replace_image=(source is not None or bool(image)) if multi else profile['mode']=='img2img'
    if replace_image:
        target=image_target(profile)
        if not target: raise ValueError('圖片無法載入工作流：請先綁定可接收圖片的 LoadImage 欄位。')
        if not source or not image: raise ValueError('請先選擇來源圖片。')
        graph[target]['inputs']['image']=image
        generation['source']={key:source[key] for key in ('name','sha256','width','height')}
        generation['source']['uploaded']=image
    marker=dict(snapshot=copy.deepcopy(snapshot),node_id=node,field=field,generation=generation)
    if effective:
        generation['effective']={key:copy.deepcopy(effective[key]) for key in ('scope','epoch','revision','hash')}
        generation['batch_index']=index
        marker['source_texts']=original_texts; marker['texts']=texts
    elif multi: marker['texts']=texts
    return dict(prompt_id=str(uuid.uuid4()),prompt=graph,extra_data={'extra_pnginfo':{'prompt_studio_request':marker}})


def effective_submission_texts(profile,snapshot):
    """Keep original composition and accepted destination text separately."""
    validate_profile(profile); value=profile['pcs_effective']; scope=value['scope']
    if scope['library']!=snapshot.get('library_id') or scope['workspace']!=snapshot['state']['workspace']:
        raise ValueError('有效工作流與提交資料庫／工作區不符。')
    if 'multi_output' in snapshot['state']:
        from .multi_output import bound_texts
        originals=bound_texts(snapshot['state'],profile)
    else:
        node,field=profile['prompt']
        originals=[dict(node=node,field=field,text=snapshot['final_prompt'])]
    origins={(item['node'],item['field']):item['source'] for item in value['texts']}
    texts=[]
    for original in originals:
        node,field=original['node'],original['field']; source=origins.get((node,field))
        if source is None: raise ValueError('文字映射尚未加入有效版本。')
        text=profile['graph'][node]['inputs'][field]
        if source=='pcs' and text!=original['text']: raise ValueError('PCS文字尚未接受為有效版本，未使用舊值提交。')
        texts.append(dict(original,text=text,source=source))
    return copy.deepcopy(originals),texts


def validate_effective_submission(direct,graph):
    """Verify actual payload, including only declared seed/image transforms."""
    snapshot=direct['snapshot']; generation=direct['generation']
    profile=next((p for p in snapshot['state'].get('generation',{}).get('profiles',[])
                  if p['id']==generation.get('workflow_id')),None)
    if profile is None or 'pcs_effective' not in profile: raise ValueError('有效工作流不在提交快照中。')
    originals,texts=effective_submission_texts(profile,snapshot)
    value=profile['pcs_effective']
    if generation.get('effective')!={key:value[key] for key in ('scope','epoch','revision','hash')}:
        raise ValueError('提交的有效版本與快照不符。')
    metadata=dict(mode=profile['mode'],workflow=profile['name'],workflow_id=profile['id'],
        workflow_sha256=hashlib.sha256(json.dumps(profile['graph'],sort_keys=True).encode()).hexdigest())
    if any(generation.get(key)!=expected for key,expected in metadata.items()):
        raise ValueError('執行來源與有效工作流不符。')
    if generation.get('origin')!=profile.get('origin') or generation.get('frontend_id')!=profile.get('frontend_id'):
        raise ValueError('執行來源的工作流身分不符。')
    if direct.get('texts')!=texts or direct.get('source_texts')!=originals:
        raise ValueError('有效文字或原稿來源與快照不符。')
    if (direct.get('node_id'),direct.get('field'))!=(texts[0]['node'],texts[0]['field']):
        raise ValueError('有效文字的主要來源不符。')
    expected=copy.deepcopy(profile['graph'])
    actual={key:expected[node]['inputs'][field] for key,(node,field) in parameter_bindings(profile).items()}
    index=generation.get('batch_index')
    if type(index) is not int or not 0<=index<100: raise ValueError('批次索引無效。')
    if 'seed' in actual:
        rule=profile.get('seed_mode','fixed')
        if rule=='random':
            seed=generation.get('parameters',{}).get('seed')
            if type(seed) is not int or not 0<=seed<2**64: raise ValueError('Seed 無效。')
            actual['seed']=seed
        elif rule=='increment': actual['seed']=(int(actual['seed'])+index)%(2**64)
        elif rule=='decrement': actual['seed']=(int(actual['seed'])-index)%(2**64)
    if generation.get('parameters')!=actual: raise ValueError('實際參數與有效版本不符。')
    for key,(node,field) in parameter_bindings(profile).items(): expected[node]['inputs'][field]=actual[key]
    source=generation.get('source')
    if source is not None:
        saved=snapshot['state']['generation'].get('source'); target=image_target(profile)
        if not target or not isinstance(saved,dict) or not isinstance(source,dict): raise ValueError('來源圖片與快照不符。')
        expected_source={key:saved[key] for key in ('name','sha256','width','height')}
        uploaded=source.get('uploaded')
        if not isinstance(uploaded,str) or uploaded_image(dict(name=PurePosixPath(uploaded).name,subfolder=str(PurePosixPath(uploaded).parent)))!=uploaded:
            raise ValueError('來源圖片路徑無效。')
        if source!={**expected_source,'uploaded':uploaded}: raise ValueError('來源圖片與快照不符。')
        expected[target]['inputs']['image']=uploaded
    if graph!=expected: raise ValueError('實際提交內容與有效版本不符。')
    return profile
