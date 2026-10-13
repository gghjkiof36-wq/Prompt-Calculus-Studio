"""Reusable text materials and detached canvas copies. No Qt or disk access."""
import copy
import hashlib
import json
import math
import time
from . import composition as comp

MIME_TYPE = 'application/x-pcs-material'
CONTENT_KINDS = ('tags', 'text', 'markdown')


def item(state, ident):
    value = next((v for v in state['items'] if v['id'] == ident), None)
    if value is None:
        raise ValueError('素材已不存在，請重新選擇。')
    return value


def fingerprint(value):
    fields = ('name', 'prompt', 'composition', 'excludes', 'content_kind')
    body = {key: value[key] for key in fields if key in value}
    return hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()


def validate_fields(value):
    if value.get('content_kind', 'tags') not in CONTENT_KINDS:
        raise ValueError('文字內容類型不支援，原資料保留。')
    if type(value.get('pinned', False)) is not bool:
        raise ValueError('素材釘選格式無效。')
    if 'last_used' in value and (type(value['last_used']) not in (float, int)
            or not math.isfinite(value['last_used']) or value['last_used'] < 0):
        raise ValueError('素材使用時間無效。')
    if 'source_revision' in value and not isinstance(value['source_revision'], str):
        raise ValueError('素材來源版本無效。')
    if 'collapsed' in value and type(value['collapsed']) is not bool:
        raise ValueError('文字卡片收合格式無效。')


def search(state, query='', category=None, *, pinned=False, order='name'):
    query = query.strip().casefold()
    found = [value for value in state['items'] if (not category or value['module'] == category)
             and (not pinned or value.get('pinned', False))
             and (not query or query in ' '.join([value['name'], value['prompt'], value.get('notes', ''),
                                                 *value.get('aliases', [])]).casefold())]
    if order == 'recent':
        return sorted(found, key=lambda v: (-v.get('last_used', 0), v['name'].casefold()))
    return sorted(found, key=lambda v: (not v.get('pinned', False), v['name'].casefold()))


def save(state, value):
    """Update a known material without dropping future/foreign optional fields."""
    from .core import uid
    current = next((v for v in state['items'] if v['id'] == value.get('id')), None)
    result = copy.deepcopy(current or {})
    result.update(copy.deepcopy(value))
    result.setdefault('id', uid())
    result.setdefault('aliases', [])
    result.setdefault('notes', '')
    result.setdefault('preview', '')
    result.setdefault('excludes', [])
    result.setdefault('content_kind', 'tags')
    if result.get('module') is None:
        result['module'] = add_category(state, '未分類')
    validate_fields(result)
    if not result.get('name', '').strip() or not isinstance(result.get('prompt'), str):
        raise ValueError('請填寫素材名稱與文字內容。')
    if not result['prompt'].strip() and 'composition' not in result:
        raise ValueError('請填寫文字內容。')
    if result.get('module') not in {m['id'] for m in state['modules']}:
        raise ValueError('請選擇素材分類。')
    if 'composition' in result:
        root = result['composition']
        root.update(name=result['name'], content_kind=result['content_kind'])
        result['prompt'] = comp.render(root)
    if current is None:
        state['items'].append(result)
    else:
        state['items'][state['items'].index(current)] = result
    return result['id']


def move_category(state, ident, destination):
    """Move only the library original; canvas copies keep their provenance."""
    value=item(state,ident)
    if destination=='@uncategorized':destination=add_category(state,'未分類')
    if destination not in {entry['id'] for entry in state['modules']}:
        raise ValueError('分類已更新，請重新拖曳。')
    value['module']=destination


def add_category(state, name):
    from .core import uid
    name = name.strip()
    if not name:
        raise ValueError('請填寫分類名稱。')
    existing = next((v for v in state['modules'] if v['name'] == name), None)
    if existing:
        return existing['id']
    ident = uid()
    state['modules'].append(dict(id=ident, name=name, mode='multiple'))
    return ident


def category(state, ident):
    value = next((v for v in state['modules'] if v['id'] == ident), None)
    if value is None:
        raise ValueError('分類已不存在，請重新選擇。')
    if value['name'] == '未分類':
        raise ValueError('「未分類」用來保留未歸類的素材，不能重新命名或刪除。')
    return value


def rename_category(state, ident, name):
    value = category(state, ident); name = name.strip()
    if not name:
        raise ValueError('請輸入分類名稱。')
    if name == '未分類' or any(v['id'] != ident and v['name'].casefold() == name.casefold() for v in state['modules']):
        raise ValueError('已有同名分類，請使用其他名稱。')
    value['name'] = name


def remove_category(state, ident):
    """Remove only the category; retain materials, detached copies and drafts."""
    from . import material_navigation as navigation
    category(state, ident)
    destination = add_category(state, '未分類')
    next(v for v in state['modules'] if v['id'] == destination)['mode'] = 'multiple'
    for value in state['items']:
        if value['module'] == ident: value['module'] = destination

    def remap(owner):
        # Old list selections can also be saved in inactive workspaces/outputs.
        # Repair category references there while retaining IDs and text copies.
        for field in ('selections', 'picks'):
            picks = owner.get(field, {})
            if ident in picks:
                values = picks.pop(ident)
                picks[destination] = list(dict.fromkeys(picks.get(destination, []) + values))
        for field in ('fixed', 'output_order'):
            if ident in owner.get(field, []):
                owner[field] = list(dict.fromkeys(destination if key == ident else key for key in owner[field]))
        if owner.get('temporary_before') == ident: owner['temporary_before'] = destination
        owners = owner.get('canvas_owners', {})
        if ident in owners:
            canvas = owners.pop(ident); owners.setdefault(destination, canvas)
        for output in owner.get('multi_output', {}).get('outputs', {}).values():
            remap(output.get('list_state', {}))

    for owner in (state, *state.get('workspaces', []), *state.get('workspace_scenes', {}).get('items', {}).values()):
        remap(owner)
    state['modules'] = [value for value in state['modules'] if value['id'] != ident]
    if navigation.FIELD in state: state[navigation.FIELD] = navigation.view(state)
    return destination


def category_metadata_only(before, after):
    """Catalog naming/affiliation with no change to any active or saved Prompt."""
    fields = {'modules', 'items', 'material_navigation'}
    if {k: v for k, v in before.items() if k not in fields} != {k: v for k, v in after.items() if k not in fields}:
        return False
    def without(values, field):
        return [{k: v for k, v in value.items() if k != field} for value in values]
    if without(before.get('items', []), 'module') != without(after.get('items', []), 'module'):
        return False
    prior = {v['id']: v for v in before.get('modules', [])}
    later = {v['id']: v for v in after.get('modules', [])}
    for ident in prior.keys() & later.keys():
        if without([prior[ident]], 'name') != without([later[ident]], 'name'): return False
    common = prior.keys() & later.keys()
    return [v['id'] for v in before.get('modules', []) if v['id'] in common] == [v['id'] for v in after.get('modules', []) if v['id'] in common]


def add_to_canvas(state, ident, canvas_id, position=None):
    """Copy, never move/link, the material; commit ownership with its new ID."""
    from .core import uid
    from .multi_output import assign
    if canvas_id not in state.get('multi_output', {}).get('canvases', {}):
        raise ValueError('請先選擇要加入的文字畫布。')
    source = item(state, ident)
    root = comp.clone_node(comp.prototype(source))
    root.update(source_id=source['id'], source_module=source['module'],
                source_revision=fingerprint(source), content_kind=source.get('content_kind', 'tags'))
    root['collapsed'] = True
    key = uid()
    state['version'] = max(3, state['version'])
    state.setdefault('uses', {})[key] = root
    state.setdefault('prompt_layout', 'paragraphs')
    state.setdefault('output_order', []).append(key)
    assign(state, key, canvas_id)
    if position is None:
        owner = state['multi_output']['canvases'][canvas_id]
        offset = 28 * (len(owner['members']) - 1)
        position = [owner['position'][0] + 24 + offset, owner['position'][1] + 108 + offset]
    state.setdefault('text_positions', {})[key] = list(position)
    source['last_used'] = time.time()
    return key


def canvas_node(state, key):
    # Existing nested keys are use_id:node_id. Source IDs inside nodes are not references.
    use_id, _, node_id = key.partition(':')
    root = state.get('uses', {}).get(use_id)
    if root is None:
        raise ValueError('這張文字卡片已移除或不在目前工作區。')
    value = comp.find(root, node_id) if node_id else root
    if value is None:
        raise ValueError('這個文字子項已移除。')
    return value


def edit_canvas(state, key, changes):
    value = canvas_node(state, key)
    allowed = {'name', 'prompt', 'content_kind', 'enabled', 'weight', 'excludes', 'collapsed', 'preview'}
    value.update({name: copy.deepcopy(v) for name, v in changes.items() if name in allowed})
    comp.validate_node(value)


def from_canvas(state, key, category, name=None):
    from .core import uid
    root = comp.clone_node(canvas_node(state, key))
    root['name'] = (name or root['name']).strip()
    value = dict(id=uid(), name=root['name'], prompt=comp.render(root), module=category,
                 aliases=[], notes='', excludes=[], preview=root.get('preview', ''),
                 content_kind=root.get('content_kind', 'tags'), composition=root)
    return value


def remove(state, ident):
    """Retain all detached copies and their provenance when deleting an original."""
    item(state, ident)
    state['items'] = [v for v in state['items'] if v['id'] != ident]
    owners = [state, *state.get('workspaces', []),
              *state.get('workspace_scenes', {}).get('items', {}).values()]
    for owner in owners:
        for field in ('selections', 'picks'):
            for category, values in owner.get(field, {}).items():
                owner[field][category] = [v for v in values if v != ident]
        owner.get('instances', {}).pop(ident, None)
        owner.get('weights', {}).pop(ident, None)
        for output in owner.get('multi_output', {}).get('outputs', {}).values():
            saved = output.get('list_state', {})
            for category, values in saved.get('selections', {}).items():
                saved['selections'][category] = [v for v in values if v != ident]
            saved.get('instances', {}).pop(ident, None)
            saved.get('weights', {}).pop(ident, None)
