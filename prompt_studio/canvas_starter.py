"""Starter layout for new documents/workspaces, never a historical migration."""
import copy


def initialize(state, *, fresh_install=False):
    """Return the starter only for a pristine new document supplied by the host.

    The host decides freshness at the storage boundary. Existing documents,
    imports, deleted layouts and intentionally empty manual drafts are retained.
    """
    data=state.get('multi_output',{})
    if not fresh_install or data.get('version',0)<7 or state['settings'].get('canvas_starter'):
        return state
    if (state.get('uses') or state.get('draft') is not None
            or state.get('generation',{}).get('profiles') or data.get('bindings')
            or data.get('stages') or data.get('schedulers')
            or state.get('text_sizes') or state.get('canvas_view')
            or state.get('canvas_functions',{}).get('images')
            or len(state.get('workspaces',[]))!=1
            or len(data.get('canvases',{}))!=1 or len(data.get('outputs',{}))!=1
            or len(data.get('clip_inputs',{}))!=1):
        return state
    canvas=next(iter(data['canvases']));output=next(iter(data['outputs']));clip=next(iter(data['clip_inputs']))
    source=data['canvases'][canvas];destination=data['outputs'][output]
    edges={(c['source'],c['destination'],c['kind']) for c in data['connections']}
    if (source['members'] or source['position']!=[-900,-400] or source['display_size']!=[760,620]
            or source.get('image') is not None or destination['draft'] is not None
            or destination.get('list_draft',{}).get('draft') is not None
            or state.get('text_positions',{})!={clip:[530,-350]}
            or edges!={(canvas,output,'text'),(output,clip,'clip')}):
        return state
    from .core import validate_state
    from .workspace_scene import capture
    result=copy.deepcopy(state)
    _populate_pristine(result)
    capture(result)
    return validate_state(result)


def _populate_pristine(state):
    """Seed only a newly constructed workspace; callers establish freshness.

    This is shared by first launch and explicit workspace creation. It must not
    be used to infer whether an existing or imported document is replaceable.
    """
    from .multi_output import connect,PREVIEW
    from .flow_data import add_scheduler,endpoint
    from .stage_model import add as add_stage
    result=state;data=result['multi_output']
    canvas=next(iter(data['canvases']));output=next(iter(data['outputs']));clip=next(iter(data['clip_inputs']))
    data['canvases'][canvas].update(name='文字畫布',position=[0,0],display_size=[560,280])
    data['outputs'][output]['name']='Prompt 輸出'
    data['clip_inputs'][clip]['name']='CLIP 輸入'
    scheduler=add_scheduler(result,(1220,0));data['schedulers'][scheduler]['mode']='data'
    stage=add_stage(result,(2220,0))
    positions={output:[680,0],clip:[1780,0],stage:[2220,0],PREVIEW:[2680,0]}
    result.setdefault('text_positions',{}).update(positions)
    result.setdefault('text_sizes',{}).update({output:[440,450],scheduler:[460,400],clip:[360,180],stage:[360,245],PREVIEW:[420,450]})
    data['connections']=[]
    connect(result,canvas,output,'text')
    connect(result,output,endpoint(scheduler,'clip1'),'clip')
    connect(result,endpoint(scheduler,'clip1'),clip,'clip')
    connect(result,clip,stage,'control')
    connect(result,stage,PREVIEW,'image')
    result['canvas_functions']['preview']=True
    result['selection_view']='canvas';result.pop('canvas_view',None)
    result['settings']['interface_mode']='canvas'
    result['settings']['canvas_starter']=dict(workspace=result['workspace'],canvas=canvas,output=output,
        scheduler=scheduler,clip=clip,stage=stage,dismissed=False)
    return result['settings']['canvas_starter']


def guide(state):
    """A guide is local to its original workspace and tolerates deleted nodes."""
    workspace=next((item for item in state.get('workspaces',[]) if item['id']==state.get('workspace')), {})
    value=workspace.get('canvas_starter',state.get('settings',{}).get('canvas_starter'))
    if (not isinstance(value,dict) or value.get('workspace')!=state.get('workspace')
            or any(not isinstance(value.get(key),str) for key in ('canvas','output','scheduler','clip','stage'))):
        return None
    return value
