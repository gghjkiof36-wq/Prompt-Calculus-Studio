"""Validate explicit workflow exchange without inferring output roles by name."""
import copy
from .workflow_import import import_graph,infer_profile
from .generation import text_fields,validate_profile


def import_transfer(state,value):
    if 'multi_output' not in state: raise ValueError('多輸出工作流需要 v0.8 Alpha。')
    if value.get('version')!=1 or not isinstance(value.get('id'),str) or not value['id'] or not isinstance(value.get('name'),str) or not value['name'].strip(): raise ValueError('工作流交換檔格式無效。')
    graph=import_graph(value.get('graph')); profile=infer_profile(graph,value['name'],value.get('workflow')); profile.update(id=value['id'],multi_text=True)
    validate_profile(profile)
    if state['multi_output'].get('version',1)>=4:
        # A transfer updates workflow content only. Canvas owns binding edits.
        return profile,[]
    bindings=value.get('bindings'); targets=set(); outputs=set()
    if not isinstance(bindings,list) or len(bindings)>100: raise ValueError('工作流綁定清單無效。')
    for b in bindings:
        if not isinstance(b,dict) or any(not isinstance(b.get(k),str) or not b[k] for k in ('workflow','output','node','field')) or b['workflow']!=profile['id']: raise ValueError('工作流綁定格式無效。')
        if b['output'] not in state['multi_output']['outputs']: raise ValueError('桌面輸出不存在：'+b['output']+'。請在 ComfyUI 重新讀取桌面資料並指定。')
        target=(b['node'],b['field'])
        if target in targets or b['output'] in outputs: raise ValueError('工作流綁定重複。')
        if target not in text_fields(graph): raise ValueError('匯入的文字目標已移除或接線：#'+b['node']+' / '+b['field'])
        targets.add(target); outputs.add(b['output'])
    return profile,copy.deepcopy(bindings)


def apply_transfer(state,value):
    """Validate the entire exchange before replacing the profile or bindings."""
    from .core import validate_state
    profile,bindings=import_transfer(state,value); candidate=copy.deepcopy(state)
    settings=candidate.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))
    previous=next((p for p in settings['profiles'] if p['id']==profile['id']),None)
    if previous and previous.get('origin'):profile['origin']=copy.deepcopy(previous['origin'])
    if len(settings['profiles'])>=50 and not any(p['id']==profile['id'] for p in settings['profiles']): raise ValueError('最多保存 50 份工作流。')
    settings['profiles']=[p for p in settings['profiles'] if p['id']!=profile['id']]+[profile]
    if candidate['multi_output'].get('version',1)>=4:
        validate_state(candidate); return candidate,profile
    settings['chosen']={k:v for k,v in settings.get('chosen',{}).items() if v!=profile['id']}
    settings['chosen'][profile['mode']]=profile['id']; settings['mode']=profile['mode']
    data=candidate['multi_output']
    if data['version']>=3:
        from .clip_flow import add,source,set_binding
        from .multi_output import connect
        previous=[b for b in data['bindings'] if b['workflow']==profile['id']]
        data['bindings']=[b for b in data['bindings'] if b['workflow']!=profile['id']]; used=set()
        for binding in bindings:
            target=(binding['node'],binding['field'])
            key=next((b['clip'] for b in previous if (b['node'],b['field'])==target and b['clip'] not in used),None)
            if key is None: key=next((k for k in data['clip_inputs'] if k not in used and source(candidate,k)==binding['output']),None)
            if key is None: key=add(candidate)
            used.add(key); connect(candidate,binding['output'],key,'clip'); set_binding(candidate,profile['id'],key,target)
    else: data['bindings']=[b for b in data['bindings'] if b['workflow']!=profile['id']]+bindings
    validate_state(candidate); return candidate,profile
