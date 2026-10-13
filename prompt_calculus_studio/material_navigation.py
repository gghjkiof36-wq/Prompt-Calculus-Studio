"""Library-only folder membership/order. Never reorder modules or Prompt data."""
import copy
import uuid

FIELD = 'material_navigation'
PREFIX = '@folder:'


def is_folder(ident):
    return isinstance(ident, str) and ident.startswith(PREFIX)


def validate(state):
    collapsed = state.get('settings', {}).get('material_folder_collapsed', [])
    if not isinstance(collapsed, list) or any(not isinstance(v, str) for v in collapsed):
        raise ValueError('資料夾展開狀態無效。')
    if FIELD not in state: return
    value = state.get(FIELD)
    if not isinstance(value, dict) or type(value.get('version')) is not int or value['version'] != 1:
        raise ValueError('資料夾收納版本不支援，原資料保留。')
    folders, order = value.get('folders'), value.get('order')
    if not isinstance(folders, list) or not isinstance(order, list):
        raise ValueError('資料夾收納格式無效。')
    ids, names, members = set(), set(), set()
    for folder in folders:
        if (not isinstance(folder, dict) or not is_folder(folder.get('id'))
                or folder['id'] == PREFIX or folder['id'] in ids
                or not isinstance(folder.get('name'), str) or not folder['name'].strip()
                or folder['name'].strip().casefold() in names
                or not isinstance(folder.get('categories'), list)):
            raise ValueError('資料夾名稱或識別碼無效。')
        ids.add(folder['id']); names.add(folder['name'].strip().casefold())
        for ident in folder['categories']:
            if not isinstance(ident, str) or not ident or is_folder(ident) or ident in members:
                raise ValueError('分類不能重複收納。')
            members.add(ident)
    seen = set()
    for ident in order:
        if (not isinstance(ident, str) or not ident or ident in seen or ident in members
                or (is_folder(ident) and ident not in ids)):
            raise ValueError('資料夾顯示順序無效。')
        seen.add(ident)
    if not ids <= seen: raise ValueError('資料夾顯示順序不完整。')


def view(state):
    """Resolve legacy/new/deleted categories without modifying the document.

    Stale category references from old imports/deletions are harmless metadata;
    ignore them here and prune only in the next explicit navigation transaction.
    """
    value = copy.deepcopy(state.get(FIELD, dict(version=1, folders=[], order=[])))
    categories = [v['id'] for v in state['modules'] if v['name'] != '未分類']
    known = set(categories); members = set()
    for folder in value['folders']:
        folder['categories'] = [v for v in folder['categories'] if v in known]
        members.update(folder['categories'])
    folders = {v['id'] for v in value['folders']}
    value['order'] = [v for v in value['order'] if v in folders or v in known-members]
    value['order'].extend(v for v in categories if v not in members and v not in value['order'])
    return value


def folder(state, ident):
    return next((v for v in view(state)['folders'] if v['id'] == ident), None)


def parent(state, category):
    return next((v['id'] for v in view(state)['folders'] if category in v['categories']), None)


def _name(value, name, ident=None):
    name = name.strip()
    if not name: raise ValueError('請輸入資料夾名稱。')
    if any(v['id'] != ident and v['name'].casefold() == name.casefold() for v in value['folders']):
        raise ValueError('已有同名資料夾，請使用其他名稱。')
    return name


def add_folder(state, name):
    value = view(state); name = _name(value, name)
    ident = PREFIX + uuid.uuid4().hex
    value['folders'].append(dict(id=ident, name=name, categories=[])); value['order'].append(ident)
    state[FIELD] = value
    return ident


def rename_folder(state, ident, name):
    value = view(state); name = _name(value, name, ident)
    target = next((v for v in value['folders'] if v['id'] == ident), None)
    if target is None: raise ValueError('資料夾已移除，請重新選擇。')
    target['name'] = name; state[FIELD] = value


def remove_folder(state, ident):
    value = view(state); target = next((v for v in value['folders'] if v['id'] == ident), None)
    if target is None: raise ValueError('資料夾已移除，請重新選擇。')
    slot = value['order'].index(ident)
    value['order'][slot:slot+1] = target['categories']; value['folders'].remove(target)
    state[FIELD] = value


def move(state, ident, destination=None, before=None):
    value = view(state); folders = {v['id']: v for v in value['folders']}
    if destination is not None and destination not in folders:
        raise ValueError('資料夾已移除，請重新選擇。')
    if is_folder(ident):
        if ident not in folders or destination is not None: raise ValueError('資料夾只可放在外層。')
    elif ident not in {v['id'] for v in state['modules'] if v['name'] != '未分類'}:
        raise ValueError('分類已更新，請重新選擇。')
    if before == ident: return
    target = folders[destination]['categories'] if destination else value['order']
    if before is not None and before not in target: raise ValueError('清單已更新，請重新拖曳。')
    for entries in [value['order']] + [v['categories'] for v in value['folders']]:
        if ident in entries: entries.remove(ident)
    target.insert(target.index(before) if before is not None else len(target), ident)
    state[FIELD] = value


def reorder_root(state, before, after):
    value = view(state)
    if before != value['order'] or len(after) != len(before) or set(after) != set(before):
        raise ValueError('清單已更新，請重新拖曳排序。')
    value['order'] = list(after); state[FIELD] = value


def navigation_only(before, after):
    return (before.get(FIELD) != after.get(FIELD)
            and {k: v for k, v in before.items() if k != FIELD}
            == {k: v for k, v in after.items() if k != FIELD})
