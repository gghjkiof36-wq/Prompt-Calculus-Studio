"""Qt-free, content-backed composition trees. No semantic guessing or references.

Library prototypes and current instances are separate trees. An overlay replaces
its entire target while active; removing it reveals the retained content below.
"""
import copy
import math
import uuid

MAX_DEPTH = 32
MAX_NODES = 5000


def node(name, prompt='', *, children=None, excludes=None, grouped=False):
    return dict(id=uuid.uuid4().hex, name=name, prompt=prompt, enabled=True,
                weight=10, children=children or [], overlays=[], excludes=excludes or [], grouped=grouped)


def validate_node(root):
    ids, objects = set(), set()
    def visit(value, depth):
        if depth > MAX_DEPTH or len(ids) >= MAX_NODES:
            raise ValueError('複合模組超過 32 層或 5000 個子項。')
        if not isinstance(value, dict) or id(value) in objects:
            raise ValueError('複合模組不能循環引用或共用可變子項。')
        objects.add(id(value))
        ident = value.get('id')
        if not isinstance(ident, str) or not ident or ident in ids:
            raise ValueError('複合模組子項 ID 缺少或重複。')
        ids.add(ident)
        if not isinstance(value.get('name'), str) or not value['name'].strip():
            raise ValueError('複合模組名稱不能空白。')
        if not isinstance(value.get('prompt'), str) or type(value.get('enabled')) is not bool:
            raise ValueError('複合模組文字或啟用狀態無效。')
        if type(value.get('weight')) is not int or not 0 <= value['weight'] <= 1000:
            raise ValueError('子項權重須介於 0.0–100.0。')
        if type(value.get('grouped', False)) is not bool:
            raise ValueError('模組分組格式無效。')
        if not isinstance(value.get('excludes'), list) or any(not isinstance(t, str) or not t.strip() for t in value['excludes']):
            raise ValueError('子項排除 Tag 格式無效。')
        for key in ('children', 'overlays'):
            if not isinstance(value.get(key), list):
                raise ValueError('複合模組子項清單無效。')
            for child in value[key]:
                visit(child, depth + 1)
    visit(root, 0)
    return root


def validate_compositions(state):
    items = {i['id']: i for i in state['items']}
    instances = state.get('instances', {})
    if not isinstance(instances, dict) or any(k not in items for k in instances):
        raise ValueError('當次組合引用不存在的素材。')
    for item in items.values():
        if 'composition' in item:
            validate_node(item['composition'])
    for root in instances.values():
        validate_node(root)
    occupied = {i['id'] for field in ('modules', 'items', 'workspaces') for i in state[field]}
    for owner in [state, *state['workspaces']]:
        uses = owner.get('uses', {})
        if not isinstance(uses, dict) or len(uses) > MAX_NODES:
            raise ValueError('模組使用內容格式無效。')
        for ident, root in uses.items():
            if not isinstance(ident, str) or not ident or ident in occupied:
                raise ValueError('模組使用 ID 無效。')
            validate_node(root)
            for part in walk(root):
                for field in ('source_id', 'source_module'):
                    if field in part and not isinstance(part[field], str):
                        raise ValueError('素材來源格式無效。')
        if uses and state['version'] < 3:
            raise ValueError('獨立模組需要第 3 版資料格式。')
        fixed_uses=owner.get('fixed_uses',[])
        if not isinstance(fixed_uses,list) or any(not isinstance(k,str) for k in fixed_uses):
            raise ValueError('固定模組使用資料無效。')
    for workspace in state['workspaces']:
        saved = workspace.get('instances', {})
        if not isinstance(saved, dict) or any(k not in items for k in saved):
            raise ValueError('工作區組合引用不存在的素材。')
        for root in saved.values():
            validate_node(root)
    positions = state.get('text_positions', {})
    if not isinstance(positions, dict) or len(positions) > 50000:
        raise ValueError('Canvas 排列資料無效。')
    for key, pos in positions.items():
        if not isinstance(key, str) or not isinstance(pos, list) or len(pos) != 2 or any(type(v) not in (float, int) or not math.isfinite(v) or abs(v) > 1000000 for v in pos):
            raise ValueError('Canvas 座標無效。')
    sizes = state.get('text_sizes', {})
    if not isinstance(sizes, dict) or len(sizes) > 50000:
        raise ValueError('Canvas 尺寸資料無效。')
    for key, size in sizes.items():
        if not isinstance(key, str) or not isinstance(size, list) or len(size) != 2 or any(type(v) not in (float, int) or not math.isfinite(v) or not 0 <= v <= 10000 for v in size):
            raise ValueError('Canvas 尺寸無效。')
    has_content = instances or any('composition' in i for i in items.values()) or any(w.get('instances') for w in state['workspaces'])
    if has_content and state.get('version') < 2:
        raise ValueError('複合模組需要第 2 版資料格式，請使用支援 Canvas 的版本。')


def prototype(item):
    if 'composition' in item:
        return item['composition']
    return dict(id='source:' + item['id'], name=item['name'], prompt=item['prompt'],
                enabled=True, weight=10, children=[], overlays=[], excludes=list(item.get('excludes', [])))


def root_for(state, item):
    return state.get('instances', {}).get(item['id'], prototype(item))


def instance(state, item_id):
    item = next(i for i in state['items'] if i['id'] == item_id)
    roots = state.setdefault('instances', {})
    if item_id not in roots:
        roots[item_id] = copy.deepcopy(prototype(item))
    state['version'] = max(2, state['version'])
    return roots[item_id]


def usage_root(state, ident, mutable=False):
    if ident in state.get('uses', {}):
        return state['uses'][ident]
    item = next((i for i in state['items'] if i['id'] == ident), None)
    if item is None or ident not in state['selections'].get(item['module'], []):
        return None
    return instance(state, ident) if mutable else root_for(state, item)


def remove_usage(state, ident):
    state.get('uses', {}).pop(ident, None)
    for picks in state['selections'].values():
        if ident in picks: picks.remove(ident)
    if 'output_order' in state:
        state['output_order'] = [key for key in state['output_order'] if key != ident]


def migrate_canvas_library(state):
    """Remove the obsolete synthetic category, retaining all content and order.

    The caller backs up storage before saving this returned copy. Unused former
    prototypes remain archived in the document, never silently discarded.
    """
    from .core import output_groups, build_prompt, validate_state
    buckets = {m['id'] for m in state['modules'] if m.get('canvas_library')}
    if not buckets: return state
    def complete_prompt(value):
        shared=dict(value,settings={**value['settings'],'separate_selections':False})
        return build_prompt(shared)
    result = copy.deepcopy(state); before = complete_prompt(state)
    old = {i['id']: i for i in state['items'] if i['module'] in buckets}
    result.setdefault('legacy_canvas_assets', []).extend(copy.deepcopy(list(old.values())))
    order = output_groups(state,include_hidden=True)
    result['output_order'] = [key for mid in order for key in
                              (state['selections'].get(mid, []) if mid in buckets else [mid])]
    for owner in [result, *result['workspaces']]:
        field = 'selections' if owner is result else 'picks'
        for mid in buckets:
            for ident in owner[field].pop(mid, []):
                root = copy.deepcopy(owner.get('instances', {}).get(ident, prototype(old[ident])))
                root['weight'] = owner.get('weights', {}).get(ident, 10)
                owner.setdefault('uses', {})[ident] = root
        for field in ('weights', 'instances'):
            if field in owner: owner[field] = {k:v for k,v in owner[field].items() if k not in old}
        if 'fixed' in owner:
            owner['fixed_uses']=list(dict.fromkeys(owner.get('fixed_uses',[])+[ident for ident,item in old.items() if item['module'] in owner['fixed']]))
            owner['fixed'] = [mid for mid in owner['fixed'] if mid not in buckets]
    result['modules'] = [m for m in result['modules'] if m['id'] not in buckets]
    result['items'] = [i for i in result['items'] if i['id'] not in old]
    if result.get('temporary_before') in buckets: result.pop('temporary_before')
    if result.get('current_module') in buckets:
        result['current_module']=result['modules'][0]['id'] if result['modules'] else None
    result['version'] = 3
    validate_state(result)
    if complete_prompt(result) != before: raise ValueError('Canvas 資料轉換前後輸出不一致，原資料保留。')
    return result


def walk(root):
    yield root
    for child in root['children'] + root['overlays']:
        yield from walk(child)


def find(root, ident):
    return next((n for n in walk(root) if n['id'] == ident), None)


def container(root, ident):
    for parent in walk(root):
        for key in ('children', 'overlays'):
            for child in parent[key]:
                if child['id'] == ident:
                    return parent[key]
    raise ValueError('找不到子項；整組請使用「移除當次組合」。')


def clone_node(root):
    validate_node(root)
    result = copy.deepcopy(root)
    for part in walk(result):
        part['id'] = uuid.uuid4().hex
    return result


def active_overlay(root):
    return next((n for n in reversed(root['overlays']) if n['enabled']), None)


def effective_nodes(root):
    if not root['enabled']:
        return
    overlay = active_overlay(root)
    if overlay is not None:
        yield from effective_nodes(overlay)
    else:
        yield root
        for child in root['children']:
            yield from effective_nodes(child)


def render(root, transform=None, *, grouped=None):
    if not root['enabled']:
        return ''
    grouped = root.get('grouped', False) if grouped is None else grouped
    overlay = active_overlay(root)
    if overlay is not None:
        text = render(overlay, transform)
    else:
        own = transform(root['prompt']) if transform else root['prompt']
        parts = [own.strip()] if own.strip() else []
        parts.extend(text for child in root['children'] if (text := render(child, transform)))
        text = ', '.join(parts)
    return f"({text}:{root['weight'] / 10:.1f})" if text and (grouped or root['weight'] != 10) else text


def detach(root, ident):
    target = find(root, ident)
    if target is None or not target['children']:
        raise ValueError('請選擇含有子項的複合模組。')
    if target['prompt'].strip() or target['overlays'] or target['weight'] != 10 or not target['enabled'] or target['excludes']:
        raise ValueError('拆解前請先移除群組本身的文字、覆蓋、排除與權重，並啟用群組，以保留相同輸出。')
    siblings = container(root, ident)
    at = next(i for i, n in enumerate(siblings) if n['id'] == ident)
    siblings[at:at + 1] = target['children']


def prune_instances(state):
    # Deselection ends that usage; library prototypes always remain intact.
    chosen = {i for ids in state['selections'].values() for i in ids}
    if 'instances' in state:
        state['instances'] = {k: v for k, v in state['instances'].items() if k in chosen}
    if state['version'] >= 2:
        for item in state['items']:
            if item['id'] in chosen:
                instance(state, item['id'])
    valid = {i['id'] for i in state['items']}
    for workspace in state['workspaces']:
        if 'instances' in workspace:
            workspace['instances'] = {k: v for k, v in workspace['instances'].items() if k in valid}
