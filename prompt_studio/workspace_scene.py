"""Workspace-local editing documents; catalogs and task journals stay shared."""
import copy
import math

FIELDS=('uses','multi_output','canvas_functions','text_positions','text_sizes','selections',
        'weights','instances','temporary','temporary_before','output_order','draft','draft_base',
        'view_drafts','prompt_layout','selection_view','canvas_view')


def scene(state):
    result={k:copy.deepcopy(state[k]) for k in FIELDS if k in state}
    result['generation_local']={k:copy.deepcopy(v) for k,v in state.get('generation',{}).items() if k!='profiles'}
    return result


def capture(state):
    if 'workspace_scenes' not in state:return
    if state['workspace'] in {w['id'] for w in state['workspaces']}:
        state['workspace_scenes']['items'][state['workspace']]=scene(state)


def load(state,value):
    for key in FIELDS:state.pop(key,None)
    state.update({k:copy.deepcopy(value[k]) for k in FIELDS if k in value})
    profiles=state.get('generation',{}).get('profiles',[])
    state['generation']=dict(mode='txt2img',chosen={},source=None,**{})
    state['generation'].update(copy.deepcopy(value.get('generation_local',{})),profiles=profiles)
    state.pop('_execution_inputs',None)


def upgrade(state):
    """Only called at load/import boundary. No jobs or history are copied."""
    if 'workspace_scenes' in state:return state
    from .core import apply_workspace_legacy
    items={}
    for workspace in state['workspaces']:
        projected=copy.deepcopy(state)
        if workspace['id']!=state['workspace']:apply_workspace_legacy(projected,workspace['id'])
        items[workspace['id']]=scene(projected)
    state['workspace_scenes']=dict(version=1,items=items)
    return state


def switch(state,key):
    if key not in {w['id'] for w in state['workspaces']}:raise ValueError('工作區不存在。')
    from .multi_output import capture_current
    if 'multi_output' in state:capture_current(state)
    capture(state)
    value=state['workspace_scenes']['items'][key]
    load(state,value);state['workspace']=key


def create(state,name,duplicate=False):
    from .core import uid
    from .multi_output import migrate
    from .clip_flow import upgrade as clips
    from .workflow_flow import upgrade as workflows
    from .flow_data import upgrade as inputs
    capture(state)
    value=scene(state)
    if not duplicate:
        projected=copy.deepcopy(state)
        for field in FIELDS:projected.pop(field,None)
        projected.update(uses={},selections={},temporary=[],draft=None,draft_base='',selection_view='canvas')
        projected['generation']=dict(mode='txt2img',profiles=[],chosen={},source=None)
        projected=inputs(workflows(clips(migrate(projected))))
        from .stage_model import upgrade as stages
        projected=stages(projected)
        value=scene(projected)
    ident=uid();state['workspaces'].append(dict(id=ident,name=name,fixed=[],picks={},parameters={},history=[]))
    state['workspace_scenes']['items'][ident]=value
    return ident


def validate(state):
    from .core import validate_state
    value=state['workspace_scenes'];ids={w['id'] for w in state['workspaces']}
    if not isinstance(value,dict) or value.get('version')!=1 or not isinstance(value.get('items'),dict) or set(value['items'])!=ids:
        raise ValueError('工作區畫布資料不完整或版本不支援。')
    for key,entry in value['items'].items():
        if not isinstance(entry,dict) or set(entry)-set(FIELDS)-{'generation_local'}:
            raise ValueError('工作區畫布內容無效。')
        view=entry.get('canvas_view')
        if view is not None and (not isinstance(view,list) or len(view)!=3 or any(type(n) not in (int,float) or not math.isfinite(n) for n in view) or not .05<=view[0]<=10):
            raise ValueError('工作區檢視位置無效。')
        projected=dict(state);projected.pop('workspace_scenes')
        load(projected,entry);projected['workspace']=key
        validate_state(projected)
