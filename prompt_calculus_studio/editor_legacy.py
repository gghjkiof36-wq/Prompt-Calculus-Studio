"""Read-only access to saved pre-0.8.7 list drafts; never activates a view."""
import copy
from .core import compose_details


def documents(state):
    outputs = state.get('multi_output', {}).get('outputs', {})
    current = state.get('multi_output', {}).get('current_output')
    result = []
    for key, output in outputs.items():
        saved = copy.deepcopy(output.get('list_state', {}))
        draft = copy.deepcopy(output.get('list_draft', {}))
        if key == current and state.get('selection_view') == 'list':
            for field in ('selections', 'weights', 'instances', 'temporary', 'temporary_before'):
                if field in state: saved[field] = copy.deepcopy(state[field])
            draft = {field: copy.deepcopy(state.get(field)) for field in ('draft', 'draft_base')}
        if not saved and not draft: continue
        projection = copy.deepcopy(state)
        projection.pop('multi_output', None)
        projection.update(uses={}, selections={}, weights={}, instances={}, temporary=[], output_order=[],
                          settings=dict(state['settings'], separate_selections=False))
        projection.update(saved)
        projection.pop('output_order', None)
        generated, _ = compose_details(projection)
        manual = draft.get('draft')
        result.append(dict(id=key, name=output['name'], generated=generated, manual=manual,
                           draft_base=draft.get('draft_base', ''), final=manual if manual is not None else generated))
    if not outputs:
        projection = copy.deepcopy(state)
        projection['uses'] = {}
        projection['settings'] = dict(state['settings'], separate_selections=False)
        generated, _ = compose_details(projection)
        manual = state.get('view_drafts', {}).get('list', {}).get('draft')
        if state.get('selection_view', 'list') == 'list': manual = state.get('draft')
        result.append(dict(id='legacy', name='舊清單', generated=generated, manual=manual,
                           draft_base=state.get('draft_base', ''), final=manual if manual is not None else generated))
    return result
