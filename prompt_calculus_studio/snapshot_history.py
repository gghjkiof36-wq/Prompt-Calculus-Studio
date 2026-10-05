"""A PNG import participates in the existing Canvas undo/redo stack."""
import copy
from .core import validate_state

MARKER='__snapshot_restore__'


def capture(canvas):
    state=canvas.window.state
    history=canvas.history_state()
    if 'workspace_scenes' in state:
        from .workspace_scene import capture as capture_scene
        capture_scene(state);history['workspace_scenes']=copy.deepcopy(state['workspace_scenes'])
    return {MARKER:dict(history=history,draft=state['draft'],draft_base=state.get('draft_base',''),
        selection_view=state.get('selection_view','list'),separate=state['settings'].get('separate_selections',False))}


def restore(canvas, source, destination, after):
    if not source or MARKER not in source[-1][0]:return False
    before,later=source[-1];expected,target=(before,later) if after else (later,before)
    from .edit_history import merge
    try:saved=merge(capture(canvas),expected,target)[MARKER]
    except ValueError as exc:canvas.window.notice(str(exc));return True
    value=copy.deepcopy(canvas.window.state)
    for key in set(canvas.history_state())|set(saved['history']):value.pop(key,None)
    value.update(copy.deepcopy(saved['history']))
    value.update(draft=saved['draft'],draft_base=saved['draft_base'],selection_view=saved['selection_view'])
    value['settings']['separate_selections']=saved['separate']
    validate_state(value)
    source.pop();destination.append((before,later));canvas.window.state=value;canvas.last_state=canvas.history_state()
    window=canvas.window
    window.refresh_workspaces();window.refresh_modules();window.refresh_library();window.refresh_builder()
    window.canvas_mode=value.get('selection_view')=='canvas'
    if window.canvas_mode:window.enter_canvas()
    else:window.leave_canvas()
    canvas.last_state=canvas.history_state();window.changed('prompt',refresh=False)
    return True
