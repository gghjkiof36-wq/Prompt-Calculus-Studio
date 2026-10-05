"""Prepare workflow changes without mutating the live document or deletion log."""
import copy
import json
from .core import validate_state


def prepare_remove(state,key,remote=None):
    if 'workspace_scenes' in state:
        from .workspace_scene import capture,load,scene
        base=copy.deepcopy(state);capture(base);scenes=base.pop('workspace_scenes');records={};candidate=None
        for workspace,document in list(scenes['items'].items()):
            projected=copy.deepcopy(base);load(projected,document);projected['workspace']=workspace
            changed,records[workspace]=prepare_remove(projected,key,remote)
            scenes['items'][workspace]=scene(changed)
            if workspace==state['workspace']:candidate=changed
        candidate['workspace_scenes']=scenes;validate_state(candidate)
        record=copy.deepcopy(records[state['workspace']]);record['workspace_records']=records
        return candidate,record
    candidate=copy.deepcopy(state)
    settings=candidate.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))
    multi=candidate.get('multi_output',{})
    profile=next((p for p in settings['profiles'] if p['id']==key),None)
    record=dict(profile=copy.deepcopy(profile),bindings=[b for b in multi.get('bindings',[]) if b['workflow']==key],chosen={k:v for k,v in settings['chosen'].items() if v==key},remote=copy.deepcopy(remote))
    record['clip_workflows']={ident:key for ident,clip in multi.get('clip_inputs',{}).items() if clip.get('workflow')==key}
    if multi.get('version',0)>=5:
        record['image_inputs']={ident:copy.deepcopy(value) for ident,value in multi.get('image_inputs',{}).items() if value.get('workflow')==key}
    settings['profiles']=[p for p in settings['profiles'] if p['id']!=key]
    settings['chosen']={k:v for k,v in settings['chosen'].items() if v!=key}
    if multi:
        multi['bindings']=[b for b in multi.get('bindings',[]) if b['workflow']!=key]
        for clip in multi.get('clip_inputs',{}).values():
            if clip.get('workflow')==key:clip['workflow']=None
        for image in multi.get('image_inputs',{}).values():
            if image.get('workflow')==key:image.update(workflow=None,node=None)
    validate_state(candidate)
    return candidate,record


def prepare_restore(state,record):
    if 'workspace_records' in record and 'workspace_scenes' in state:
        from .workspace_scene import capture,load,scene
        base=copy.deepcopy(state);capture(base);scenes=base.pop('workspace_scenes');candidate=None
        for workspace,document in list(scenes['items'].items()):
            projected=copy.deepcopy(base);load(projected,document);projected['workspace']=workspace
            saved=record['workspace_records'].get(workspace)
            if saved is None:
                saved=dict(profile=record['profile'],bindings=[],chosen={},clip_workflows={},image_inputs={})
            changed=prepare_restore(projected,saved);scenes['items'][workspace]=scene(changed)
            if workspace==state['workspace']:candidate=changed
        candidate['workspace_scenes']=scenes;validate_state(candidate);return candidate
    if not isinstance(record,dict) or not isinstance(record.get('bindings'),list) or not isinstance(record.get('chosen'),dict):
        raise ValueError('工作流復原紀錄格式無效。')
    candidate=copy.deepcopy(state);record=copy.deepcopy(record)
    generation=candidate.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))
    profile=record['profile']
    if profile is not None and not isinstance(profile,dict):raise ValueError('工作流復原紀錄格式無效。')
    if profile:
        if any(p['id']==profile['id'] for p in generation['profiles']):raise ValueError('同一工作流已重新匯入，保留目前內容。')
        generation['profiles'].append(profile)
    for key,value in record['chosen'].items():
        if generation['chosen'].get(key) not in (None,value):raise ValueError('工作流選擇已變更，未覆寫目前選擇。')
        generation['chosen'][key]=value
    if record['bindings']:
        multi=candidate.get('multi_output',{})
        if not multi:raise ValueError('原文字綁定的 Canvas 已不存在，未復原。')
        multi['bindings'].extend(record['bindings'])
    if 'clip_workflows' in record:
        validate_state(candidate)
        restore_clip_workflows(candidate,record)
    for key,saved in record.get('image_inputs',{}).items():
        target=candidate.get('multi_output',{}).get('image_inputs',{}).get(key)
        if target is None or target.get('workflow') is not None:raise ValueError('圖片輸入已移除或改綁，保留目前內容。')
        target.update(workflow=saved['workflow'],node=saved['node'])
    # Includes missing CLIPs, duplicate destinations and invalid references.
    validate_state(candidate)
    return candidate


def restore_clip_workflows(candidate,record):
    """Restore recorded choices only; inactive bindings never imply a choice."""
    from .generation import text_fields
    choices=record['clip_workflows']; profile=record['profile']
    if not isinstance(choices,dict) or len(choices)>100 or any(
            not isinstance(key,str) or not key or not isinstance(value,str) or not value
            or not profile or value!=profile['id'] for key,value in choices.items()):
        raise ValueError('CLIP 工作流選擇的復原紀錄格式無效。')
    multi=candidate.get('multi_output',{}); clips=multi.get('clip_inputs',{})
    for key,workflow in choices.items():
        if multi.get('version',0)<4 or key not in clips:raise ValueError('原 CLIP 已不存在，保留目前內容與復原紀錄。')
        if clips[key].get('workflow') is not None:raise ValueError('CLIP 工作流選擇已變更，未覆寫目前選擇。')
        bindings=[b for b in multi['bindings'] if b.get('workflow')==workflow and b.get('clip')==key]
        if len(bindings)!=1 or (bindings[0].get('node'),bindings[0].get('field')) not in text_fields(profile['graph']):
            raise ValueError('原 CLIP 綁定已失效，保留目前內容與復原紀錄。')
        clips[key]['workflow']=workflow


def commit_change(store,candidate,record=None,consume=None):
    """Document and journal insertion/consumption form one SQLite transaction."""
    from .multi_output import capture_current
    if 'multi_output' in candidate:capture_current(candidate)
    from .workspace_scene import capture
    capture(candidate)
    validate_state(candidate)
    db=store.db
    with db:
        if consume is not None:
            latest=db.execute('SELECT id,body FROM workflow_deletions ORDER BY id DESC LIMIT 1').fetchone()
            if not latest or json.loads(latest[1])!=consume:raise ValueError('復原紀錄已變更，請重新讀取。')
            db.execute('DELETE FROM workflow_deletions WHERE id=?',(latest[0],))
        if record is not None:db.execute('INSERT INTO workflow_deletions(body) VALUES (?)',(json.dumps(record,ensure_ascii=False),))
        db.execute('INSERT OR REPLACE INTO document VALUES (1,?)',(json.dumps(candidate,ensure_ascii=False),))
