"""Feature-gated document versions shared by storage, UI and the extension."""


def required_version(state):
    from .prompt_groups import requires_version_five
    for owner in [state, *state.get('workspace_scenes', {}).get('items', {}).values()]:
        if not isinstance(owner, dict):continue
        if owner.get('canvas_functions', {}).get('previews'):return 6
        if any(c.get('kind') == 'control' and
               any(c.get(end, '').endswith('::bindings') for end in ('source', 'destination'))
               for c in owner.get('multi_output', {}).get('connections', [])):return 6
    return 5 if requires_version_five(state) else 1


def mark_version(state):
    state['version'] = max(state['version'], required_version(state))
