"""Feature-gated document versions shared by storage, UI and the extension."""


def required_version(state):
    from .prompt_groups import requires_version_five
    from .composition import walk
    for owner in [state, *state.get('workspaces', []), *state.get('workspace_scenes', {}).get('items', {}).values()]:
        if not isinstance(owner, dict):continue
        if any('material_type' in item for item in owner.get('items', [])):return 7
        roots=[*owner.get('uses', {}).values(), *owner.get('instances', {}).values()]
        roots += [v['composition'] for v in owner.get('items', []) if 'composition' in v]
        roots += [v['root'] for v in owner.get('canvas_functions', {}).get('text_sources', {}).values()]
        if any(any(any(field in part for field in ('material_type', 'variants', 'combination', 'selection_cursor')) or
                       'random_repeats' in part.get('sequence', {}) for part in walk(root)) for root in roots):return 7
    for owner in [state, *state.get('workspace_scenes', {}).get('items', {}).values()]:
        if not isinstance(owner, dict):continue
        if owner.get('canvas_functions', {}).get('previews'):return 6
        if any(c.get('kind') == 'control' and
               any(c.get(end, '').endswith('::bindings') for end in ('source', 'destination'))
               for c in owner.get('multi_output', {}).get('connections', [])):return 6
    return 5 if requires_version_five(state) else 1


def mark_version(state):
    state['version'] = max(state['version'], required_version(state))
