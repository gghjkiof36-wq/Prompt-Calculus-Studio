"""Validated conversion at document/import/restore boundaries, without Qt or I/O."""
import copy


def _canvas_default_names(state):
    # Rename the former built-in label only; custom names and graph IDs stay intact.
    for canvas in state['multi_output']['canvases'].values():
        if canvas['name']=='文字畫布':canvas['name']='畫布'


def prepare_state(state, *, multi=False):
    from .core import validate_state,DEFAULT_SETTINGS
    from .composition import migrate_canvas_library
    result=copy.deepcopy(validate_state(state))
    result['settings']={**copy.deepcopy(DEFAULT_SETTINGS),**result['settings']}
    legacy={m['id'] for m in result['modules'] if m.get('canvas_library')}
    canvas=result.get('uses') or any(result['selections'].get(mid) for mid in legacy)
    listing=any(picks for mid,picks in result['selections'].items() if mid not in legacy)
    result.setdefault('selection_view','canvas' if canvas and not listing else 'list')
    result=migrate_canvas_library(result)
    if multi or 'multi_output' in result:
        from .multi_output import migrate
        result=migrate(result)
        from .clip_flow import upgrade
        result=upgrade(result)
        from .workflow_flow import upgrade as upgrade_workflow_flow
        result=upgrade_workflow_flow(result)
        from .flow_data import upgrade as upgrade_dataflow,materialize
        result=upgrade_dataflow(result)
        from .stage_model import upgrade as upgrade_stages
        result=upgrade_stages(result)
        _canvas_default_names(result)
        from .workspace_scene import load as load_scene,scene as scene_value
        for key,saved in result.get('workspace_scenes',{}).get('items',{}).items():
            # A saved workspace can predate the active workspace schema. Load
            # it independently and run each prerequisite before Stage marks
            # it current; otherwise old scenes lose schedulers/input fields.
            projected=copy.deepcopy(result)
            projected.pop('workspace_scenes',None)
            load_scene(projected,saved);projected['workspace']=key
            projected=migrate(projected)
            projected=upgrade(projected)
            projected=upgrade_workflow_flow(projected)
            projected=upgrade_dataflow(projected)
            projected=upgrade_stages(projected)
            _canvas_default_names(projected)
            projected.pop('_execution_inputs',None)
            projected.pop('_stage_results',None)
            materialize(projected)
            validate_state(projected)
            converted=scene_value(projected)
            # load_scene provides runtime defaults; they are not a document
            # migration and must not cause a second backup on the next load.
            converted['generation_local']=copy.deepcopy(saved.get('generation_local',{}))
            saved.clear();saved.update(converted)
        result.pop('_execution_inputs',None)
        result.pop('_stage_results',None)
        materialize(result)
        from .workspace_scene import upgrade as upgrade_scenes
        upgrade_scenes(result)
    return validate_state(result)
