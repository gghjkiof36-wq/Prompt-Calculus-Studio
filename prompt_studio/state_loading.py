"""Validated conversion at document/import/restore boundaries, without Qt or I/O."""
import copy


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
        result.pop('_execution_inputs',None)
        materialize(result)
        from .workspace_scene import upgrade as upgrade_scenes
        upgrade_scenes(result)
    return validate_state(result)
