"""Explicit linear stage instances; data only, shared with the extension."""
import copy
import hashlib
import json
import uuid


def definition(state):
    return state.get('multi_output',{}).get('workflow_order',{}).get('chain')


def enabled(state):
    value=definition(state)
    return bool(value and value.get('enabled'))


def new_definition():
    return dict(version=1,id=uuid.uuid4().hex,name='工作流串接',enabled=False,stages=[],source=None)


def stage(workflow,name):
    return dict(id=uuid.uuid4().hex,workflow=workflow,name=name,output=None,upstream=None,texts=[],images=[])


def revision(plan):
    return hashlib.sha256(json.dumps(plan,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def topology(graph):
    """Native node identity/wiring, excluding editable scalar parameters."""
    return {key:dict(type=node['class_type'],links={field:value for field,value in node.get('inputs',{}).items()
                 if isinstance(value,list) and len(value)==2 and str(value[0]) in graph}) for key,node in graph.items()}


def validate(plan):
    if not isinstance(plan,dict) or plan.get('version')!=1 or type(plan.get('enabled')) is not bool:
        raise ValueError('不支援的串接設定。')
    if any(not isinstance(plan.get(k),str) or not plan[k] for k in ('id','name')):
        raise ValueError('串接名稱或識別無效。')
    stages=plan.get('stages')
    if not isinstance(stages,list) or len(stages)>100:raise ValueError('串接最多 100 個有限階段。')
    if plan.get('source') is not None and not isinstance(plan['source'],str):raise ValueError('入口圖片來源無效。')
    seen=set()
    for index,item in enumerate(stages):
        if not isinstance(item,dict) or any(not isinstance(item.get(k),str) or not item[k] for k in ('id','workflow','name')):
            raise ValueError('階段資料無效。')
        if item['id'] in seen:raise ValueError('階段識別重複；同一工作流請新增不同階段。')
        seen.add(item['id'])
        if item.get('output') is not None and not isinstance(item['output'],str):raise ValueError('輸出節點無效。')
        if item.get('control') is not None and not isinstance(item['control'],str):raise ValueError('流程控制接線無效。')
        previous=item.get('upstream');targets=set()
        if previous is not None and not isinstance(previous,dict):raise ValueError('上游圖片映射無效。')
        for kind in ('texts','images'):
            if not isinstance(item.get(kind),list) or any(not isinstance(m,dict) for m in item[kind]):raise ValueError('階段輸入清單無效。')
        if previous:
            if not index or previous.get('stage')!=stages[index-1]['id']:
                raise ValueError('圖片依賴必須指向緊接在前的階段；不能將接收階段移到來源之前。')
            if not isinstance(previous.get('node'),str) or not previous['node']:raise ValueError('請指定接收上游圖片的 LoadImage 節點。')
            if previous.get('mode') not in ('all','single'):raise ValueError('請選全部結果或指定單張。')
            if previous['mode']=='single' and (type(previous.get('index')) is not int or previous['index']<0):raise ValueError('圖片序號無效。')
            targets.add((previous['node'],'image'))
        elif index:raise ValueError('後續階段必須明確接收上一階段結果。')
        for mapping in item.get('texts',[])+item.get('images',[]):
            field=mapping.get('field','image');key=(mapping.get('node'),field)
            if any(not isinstance(k,str) or not k for k in key) or key in targets:raise ValueError('同一階段的目標欄位只能有一個來源。')
            targets.add(key)
        for mapping in item.get('texts',[]):
            if mapping.get('mode') not in ('web','pcs','upstream'):raise ValueError('文字來源無效。')
            if mapping['mode']=='pcs' and not isinstance(mapping.get('source'),str):raise ValueError('請指定 PCS 文字來源。')
            if mapping['mode']=='upstream':
                source=mapping.get('source',{})
                if not isinstance(source,dict) or source.get('stage') not in {s['id'] for s in stages[:index]} or any(not isinstance(source.get(k),str) for k in ('node','field')):
                    raise ValueError('上游文字必須指定前一階段或其祖先的實際欄位。')
        for mapping in item.get('images',[]):
            if not isinstance(mapping.get('source'),str):raise ValueError('固定圖片來源無效。')
    return plan


def prepare_plan(state):
    from .generation import text_fields,validate_profile
    from .flow_data import channel,upstream
    plan=copy.deepcopy(validate(definition(state)))
    if not plan['stages']:raise ValueError('請先加入串接階段。')
    profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}
    schedulers=set()
    for index,item in enumerate(plan['stages']):
        profile=profiles.get(item['workflow'])
        if not profile:raise ValueError(item['name']+'：目標工作流已移除。')
        validate_profile(profile)
        if item.get('control'):
            from .chain_connections import endpoint,terminal
            control=terminal(state,item['control'])
            if not control or control.get('workflow')!=item['workflow'] or not any(c['source']==item['control'] and c['destination']==endpoint(item['id']) and c['kind']=='control' for c in state['multi_output']['connections']):
                raise ValueError(item['name']+'：末端流程控制線已斷開或工作流綁定已變更。')
        if not profile.get('frontend_id'):raise ValueError(item['name']+'：請重新選擇具原生身分的工作流。')
        graph=profile['graph'];out=item.get('output')
        if not out or graph.get(out,{}).get('class_type') not in ('SaveImage','PreviewImage'):
            raise ValueError(item['name']+'：請選擇 SaveImage／PreviewImage 輸出節點。')
        item['identity']=dict(frontend_id=profile['frontend_id'],origin=copy.deepcopy(profile.get('origin',{})))
        item['topology']=topology(graph)
        for m in item.get('texts',[]):
            if (m['node'],m['field']) not in text_fields(graph):raise ValueError(item['name']+'：文字接收欄位已失效。')
            if m['mode']=='upstream':
                parent=next(s for s in plan['stages'][:index] if s['id']==m['source']['stage'])
                if (m['source']['node'],m['source']['field']) not in text_fields(profiles[parent['workflow']]['graph']):
                    raise ValueError(item['name']+'：上游文字欄位已失效。')
            if m['mode']=='pcs':
                pair=channel(state,m['source'])
                if pair:
                    if index:raise ValueError(item['name']+'：首版預排程只能接在串接入口。')
                    schedulers.add(pair[0])
        images=item.get('images',[])+([item['upstream']] if item.get('upstream') else [])
        for m in images:
            node=graph.get(m['node'],{})
            if node.get('class_type')!='LoadImage' or not isinstance(node.get('inputs',{}).get('image'),str):
                raise ValueError(item['name']+'：接收图片的 LoadImage 節點已失效。')
            if m.get('source'):
                pair=channel(state,m['source'])
                if pair:
                    if index:raise ValueError(item['name']+'：下游不可再消耗獨立預排程。')
                    schedulers.add(pair[0])
        if index==0 and plan.get('source'):
            if not any(plan['source'] in upstream(state,[m['source']]) for m in item.get('images',[])):
                raise ValueError('入口圖片清單須連到第一階段的一個圖片接收欄位。')
    if len(schedulers)>1:raise ValueError('串接入口只能由一個預排程協調。')
    plan['scheduler']=next(iter(schedulers),None)
    return plan


def output_indices(count,choice):
    if not count:raise ValueError('階段已結束但所選輸出無結果。')
    if not choice or choice['mode']=='all':return range(count)
    index=choice['index']
    if index>=count:raise ValueError('所選第 '+str(index+1)+' 張不存在；本次只有 '+str(count)+' 張。')
    return [index]


def provenance(value):
    if not isinstance(value,dict) or any(not isinstance(value.get(k),str) or not value[k] for k in ('run','stage','item','attempt','revision')):
        raise ValueError('串接項目的歸屬無效。')
    if type(value.get('round')) is not int or not 1<=value['round']<=100:raise ValueError('串接輪次無效。')
    return copy.deepcopy(value)
