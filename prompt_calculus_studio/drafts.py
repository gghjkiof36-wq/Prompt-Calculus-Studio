"""Manual text operations shared by the list editor and individual canvas outputs.

Typing retains the editor's local undo. Explicit clear operations are wrapped in
the caller's existing canvas transaction; this module does not create history.
"""
from .core import build_prompt
from . import multi_output


def edit(state, text, output=None):
    if not isinstance(text,str):raise ValueError('手動稿必須是文字。')
    target=state if output is None else state['multi_output']['outputs'][output]
    if target.get('draft') is None:
        if output is None:base=build_prompt(state)
        else:
            # Disconnected canvas outputs still permit preserving a manual draft.
            try:base=multi_output.compile_output(state,output)['generated_prompt']
            except ValueError:base=''
        target['draft_base']=base
    target['draft']=text
    _mirror(state,target,output)


def clear(state, output=None):
    target=state if output is None else state['multi_output']['outputs'][output]
    target.update(draft=None,draft_base='')
    _mirror(state,target,output)


def _mirror(state,target,output):
    if output is None:
        # capture_current respects the separate list/canvas selection contract.
        if 'multi_output' in state:multi_output.capture_current(state)
    elif state['multi_output']['current_output']==output:
        state.update(draft=target['draft'],draft_base=target['draft_base'])
