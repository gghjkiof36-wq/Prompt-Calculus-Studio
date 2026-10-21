"""Explicit responsibility boundary, not a PNG/content-field allowlist."""
import copy

from .. import core, workspace_scene
from .schema import fail

CONTENT_SETTINGS = {'formatter', 'artist_prefix', 'separate_selections', 'comfy_count'}
LOCAL_SETTINGS = (set(core.DEFAULT_SETTINGS) - CONTENT_SETTINGS) | {
    'comfy_enabled', 'comfy_url', 'canvas_palette', 'execution_bar_position',
    'model_category_types', 'ui_language', 'window_geometry', 'window_state', 'canvas_starter',
    'material_library_order', 'material_folder_collapsed',
}
LOCAL_STATE = {'dictionary', 'content_history', 'material_navigation'}
CONTENT_STATE = set(workspace_scene.FIELDS) | {
    'version', 'modules', 'items', 'workspaces', 'workspace', 'generation',
    'settings', 'category_positions', 'library_categories',
}
DERIVED_STATE = {'workspace_scenes', '_execution_inputs', '_stage_results'}


def content_settings(settings):
    unknown = set(settings) - CONTENT_SETTINGS - LOCAL_SETTINGS
    if unknown:
        fail('unsupported', '無法保存：有不支援的設定（'+', '.join(sorted(unknown))+'）。', fields=sorted(unknown))
    # Absence can carry a legacy fallback different from today's default.
    # Conversion belongs to prepare_state; export does not invent new settings.
    return {key: copy.deepcopy(settings[key]) for key in sorted(CONTENT_SETTINGS) if key in settings}


def local_retention(state):
    """For the future storage adapter: data that remains authoritative in local DB.

    Calling this function does not persist or migrate anything. Export never edits
    the input state; P4/P6 must connect this responsibility to document identities.
    """
    return dict(fields={key: copy.deepcopy(state[key]) for key in LOCAL_STATE if key in state},
                settings={key: copy.deepcopy(value) for key, value in state.get('settings', {}).items()
                          if key not in CONTENT_SETTINGS},
                workspace_history={w['id']: copy.deepcopy(w['history'])
                                   for w in state['workspaces'] if 'history' in w})


def validate_state_scope(state):
    unknown = set(state) - CONTENT_STATE - LOCAL_STATE - DERIVED_STATE
    if unknown:
        fail('unsupported', '狀態含尚未分類的欄位；原件保留。', fields=sorted(unknown))
    core.validate_state(state)
    content_settings(state['settings'])


def material_roots(state):
    """Every owned or dependency root, including inactive list/workspace contents."""
    for item in state.get('items', []):
        if item.get('composition'):
            yield item['composition']
    for owner in [state, *state.get('workspaces', []),
                  *(o.get('list_state', {}) for o in state.get('multi_output', {}).get('outputs', {}).values())]:
        yield from owner.get('uses', {}).values()
        yield from owner.get('instances', {}).values()
    for reader in state.get('canvas_functions', {}).get('text_sources', {}).values():
        yield reader['root']
