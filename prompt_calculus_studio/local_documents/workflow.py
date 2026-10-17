"""Whole-workspace content with pinned catalog/profile dependency closure."""
import copy

from .. import core, multi_output, workspace_scene
from .policy import CONTENT_STATE, CONTENT_SETTINGS, content_settings, validate_state_scope
from .schema import fail, new_document, validate


def catalog_references(state):
    modules, items = set(), set()
    owners = [state, *state['workspaces'],
              *(v.get('list_state', {}) for v in state.get('multi_output', {}).get('outputs', {}).values())]
    for owner in owners:
        for field in ('selections', 'picks'):
            for key, selected in owner.get(field, {}).items():
                modules.add(key)
                items.update(selected)
        modules.update(owner.get('fixed', []))
        modules.update(owner.get('canvas_owners', {}))
        if owner.get('temporary_before'):
            modules.add(owner['temporary_before'])
        items.update(owner.get('weights', {}))
        items.update(owner.get('instances', {}))
    known_modules = {m['id'] for m in state['modules']}
    modules.update(key for key in state.get('output_order', []) if key in known_modules)
    known_items = {i['id']: i for i in state['items']}
    if items - known_items.keys():
        fail('dependency', '素材參照缺少定義。', ids=sorted(items - known_items.keys()))
    modules.update(known_items[key]['module'] for key in items)
    if modules - known_modules:
        fail('dependency', '分類參照缺少定義。', ids=sorted(modules - known_modules))
    return modules, items


def profile_references(state):
    generation = state.get('generation', {})
    result = {v for v in generation.get('chosen', {}).values() if v}
    data = state.get('multi_output', {})
    for collection in ('bindings', 'clip_inputs', 'image_inputs', 'stages'):
        entries = data.get(collection, [])
        if isinstance(entries, dict):
            entries = entries.values()
        result.update(v['workflow'] for v in entries if v.get('workflow'))
        for entry in entries:
            identity = (entry.get('parameters') or {}).get('identity', {})
            if identity.get('workflow'):
                result.add(identity['workflow'])
    order = data.get('workflow_order', {})
    for field in ('items', 'established'):
        result.update(order.get(field, []))
    result.update(s['workflow'] for s in (order.get('chain') or {}).get('stages', []))
    for image in state.get('canvas_functions', {}).get('images', {}).values():
        for key in ('binding', 'legacy_binding'):
            if image.get(key):
                result.add(image[key]['workflow'])
    return result


def validate_payload(payload):
    if set(payload) != {'state', 'catalog_role', 'profile_role'}:
        fail('unsupported', '工作流封裝含未支援欄位。')
    if payload['catalog_role'] != 'pinned_dependency' or payload['profile_role'] != 'pinned_dependency':
        fail('invalid', '工作流不能註冊成第二個素材或服務工作流擁有者。')
    state = payload['state']
    if not isinstance(state, dict) or set(state) - CONTENT_STATE - {'dictionary'}:
        fail('unsupported', '文件包含本機、衍生或未分類狀態。')
    if state.get('dictionary') != {} or len(state.get('workspaces', [])) != 1:
        fail('invalid', '工作流必須只包含一個 workspace。')
    if 'history' in state['workspaces'][0]:
        fail('invalid', '本機參數歷史必須留在正式歷史機制。')
    if set(state.get('settings', {})) - CONTENT_SETTINGS:
        fail('unsupported', '工作流包含非內容設定。')
    core.validate_state(state)
    occupied = set()
    data = state.get('multi_output', {})
    functions = state.get('canvas_functions', {})
    for collection in [state.get('uses', {}),
            *(data.get(key, {}) for key in ('canvases', 'outputs', 'clip_inputs', 'image_inputs', 'schedulers', 'stages')),
            *(functions.get(key, {}) for key in ('images', 'text_sources', 'previews'))]:
        if occupied.intersection(collection):
            fail('invalid', '工作流節點 ID 在不同種類之間重複。')
        occupied.update(collection)
    modules, items = catalog_references(state)
    if modules != {m['id'] for m in state['modules']} or items != {i['id'] for i in state['items']}:
        fail('dependency', '依賴目錄包含未使用定義或缺少定義。')
    profiles = state.get('generation', {}).get('profiles', [])
    ids = [p['id'] for p in profiles]
    if len(ids) != len(set(ids)) or set(ids) != profile_references(state):
        fail('dependency', '工作流 profile 閉包遺失、重複或包含未用項目。')
    by_id = {p['id']: p for p in profiles}
    for binding in state.get('multi_output', {}).get('bindings', []):
        profile = by_id.get(binding['workflow'])
        if profile is None or binding['field'] not in profile['graph'].get(binding['node'], {}).get('inputs', {}):
            fail('dependency', '綁定的外部節點或欄位不存在。')
    # Legacy core validates active selections, but not every inactive list draft.
    for output in state.get('multi_output', {}).get('outputs', {}).values():
        projected = copy.deepcopy(state)
        projected.update(copy.deepcopy(output.get('list_state', {})))
        core.validate_state(projected)
        saved = output.get('list_draft')
        if saved is not None and (not isinstance(saved, dict) or
                saved.get('draft') is not None and not isinstance(saved['draft'], str) or
                not isinstance(saved.get('draft_base', ''), str)):
            fail('invalid', '輸出清單手動稿無效。')


def from_state(state, workspace_id=None):
    validate_state_scope(state)
    candidate = copy.deepcopy(state)
    target = workspace_id or candidate['workspace']
    if target != candidate['workspace']:
        workspace_scene.upgrade(candidate)
        workspace_scene.switch(candidate, target)
    if 'multi_output' in candidate:
        multi_output.capture_current(candidate)
    workspace = next((w for w in candidate['workspaces'] if w['id'] == target), None)
    if workspace is None:
        fail('missing', '工作區不存在。')
    workspace = copy.deepcopy(workspace)
    workspace.pop('history', None)
    result = {key: copy.deepcopy(value) for key, value in candidate.items() if key in CONTENT_STATE}
    result.update(workspaces=[workspace], workspace=target, dictionary={},
                  settings=content_settings(candidate['settings']))
    modules, items = catalog_references(result)
    result['modules'] = [m for m in result['modules'] if m['id'] in modules]
    result['items'] = [i for i in result['items'] if i['id'] in items]
    # Positions are catalog UI metadata, not a reason to export unused materials.
    if 'category_positions' in result:
        result['category_positions'] = {k: v for k, v in result['category_positions'].items() if k in modules}
    for output in result.get('multi_output', {}).get('outputs', {}).values():
        listing = output.get('list_state', {})
        if 'category_positions' in listing:
            listing['category_positions'] = {k: v for k, v in listing['category_positions'].items() if k in modules}
    referenced = profile_references(result)
    if 'generation' in result:
        result['generation']['profiles'] = [p for p in result['generation'].get('profiles', []) if p['id'] in referenced]
    return new_document('workflow', dict(state=result, catalog_role='pinned_dependency', profile_role='pinned_dependency'))


def to_state(document):
    validate(document)
    if document['kind'] != 'workflow':
        fail('invalid', '不是工作流文件。')
    return copy.deepcopy(document['payload']['state'])
