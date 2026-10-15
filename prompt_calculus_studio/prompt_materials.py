"""Explicit material levels and independent, serializable selection state.

This module has no UI or storage side effects. Resolving a value returns the
consumed copy and the next copy; only successful persistence commits the latter.
"""
import copy
import hashlib
import json
import uuid

TYPES = {'text': '文字模塊', 'combination': '組合', 'flow': '流程'}
RUNTIME_FIELDS = {'selection_cursor', 'supply_cursor', 'round_id', 'round_override',
                  'round_drafts', 'supply_origin'}


def kind(value):
    root = value.get('composition', value)
    return root.get('material_type') or value.get('material_type') or (
        'flow' if root.get('sequence') else 'combination' if root.get('combination') or root.get('children') or root.get('overlays') else 'text')


def as_group(root):
    from .composition import clone_node
    from .prompt_groups import new_group
    if root.get('combination'):return copy.deepcopy(root['combination'])
    if not root.get('prompt') and not root.get('children') and not root.get('overlays') and not root.get('variants'):
        result=new_group(root['name']);result['input']='modules';return result
    body=clone_node(root)
    return new_group(root['name'], modules=[body])


def summary(value, *, detail=False):
    from .composition import render
    from .prompt_groups import module_summary
    root=value.get('composition',value);material_type=kind(value)
    if material_type=='flow':
        rows=root['sequence']['groups']
        heading=f"{len(rows)} 個組合 · {sum(g['repeat'] for g in rows)} 輪"
        return heading+('\n\n'+'\n'.join(f"{i}. {g['name']} · {g['repeat']} 次" for i,g in enumerate(rows,1)) if detail else '')
    if material_type=='combination':
        group=root.get('combination')
        return ('直接輸入' if group and group['input']=='text' else module_summary(group['modules'] if group else root.get('children',[]))) + ('\n\n'+render(root) if detail else '')
    if root.get('variants'):
        return f"{len(root['variants']['groups'])} 個變體"+ ('\n\n'+render(root) if detail else '')
    return value.get('prompt','')


def variants(root):
    from .prompt_groups import new_group
    first = new_group('變體 1', root.get('prompt', ''), content_kind=root.get('content_kind', 'tags'))
    return dict(version=1, mode='fixed', random_repeats=True,
                start=first['id'], selected=first['id'], groups=[first])


def new(material_type, name):
    from .composition import node
    from .prompt_groups import new_group
    root = node(name); root['material_type'] = material_type
    if material_type == 'combination':
        root['combination'] = new_group(name); root['combination']['input'] = 'modules'
    elif material_type == 'flow':
        group = new_group(); group['input'] = 'modules'
        root['sequence'] = dict(version=1, mode='fixed', random_repeats=True,
                                start=group['id'], selected=group['id'], groups=[group])
    elif material_type != 'text':
        raise ValueError('素材類型不支援。')
    return root


def definition(value):
    """A stable definition, excluding every nested cursor and preview position."""
    if isinstance(value, list): return [definition(v) for v in value]
    if not isinstance(value, dict): return value
    return {k: definition(v) for k, v in value.items()
            if k not in RUNTIME_FIELDS and k != 'selected'}


def signature(root):
    return hashlib.sha256(json.dumps(definition(root), sort_keys=True,
                                     ensure_ascii=False).encode()).hexdigest()


def reset(root):
    """Start a detached placement from its own definition, not its original's cursor."""
    from .composition import walk
    for part in walk(root):
        for field in RUNTIME_FIELDS: part.pop(field, None)
        for field in ('sequence', 'variants'):
            if part.get(field): part[field]['selected'] = part[field]['start']
    return root


def migrate(state):
    """Run only at load/import boundaries. Keep legacy bodies and all IDs intact."""
    from .composition import walk
    changed = False
    for owner in [state, *state.get('workspaces', []), *state.get('workspace_scenes', {}).get('items', {}).values()]:
        if not isinstance(owner, dict): continue
        roots = [*owner.get('uses', {}).values(), *owner.get('instances', {}).values()]
        roots += [v['root'] for v in owner.get('canvas_functions', {}).get('text_sources', {}).values()]
        for item in owner.get('items', []):
            if 'material_type' not in item:
                item['material_type'] = kind(item); changed = True
            if item.get('composition'): roots.append(item['composition'])
        for root in roots:
            for part in walk(root):
                if 'material_type' not in part:
                    part['material_type'] = kind(part); changed = True
    if changed: state['version'] = max(7, state['version'])
    return state


def validate(root, visit, depth):
    from .prompt_groups import validate as validate_sequence
    material_type = root.get('material_type')
    if material_type is not None and material_type not in TYPES:
        raise ValueError('素材類型不支援，原資料保留。')
    present = [k for k in ('sequence', 'combination', 'variants') if k in root]
    if len(present) > 1:
        raise ValueError('素材同時包含不同層級的定義，原資料保留。')
    expected = {'sequence': 'flow', 'combination': 'combination', 'variants': 'text'}
    if present and material_type is not None and material_type != expected[present[0]]:
        raise ValueError('素材類型與內容不符，原資料保留。')
    if 'variants' in root:
        validate_sequence(root['variants'])
        if any(g['input'] != 'text' or g['modules'] for g in root['variants']['groups']):
            raise ValueError('文字變體只能包含文字。')
    if 'combination' in root:
        group = root['combination']
        validate_sequence(dict(version=1, mode='fixed', start=group.get('id'),
                               selected=group.get('id'), groups=[group]), visit, depth)
    if 'selection_cursor' in root:
        cursor = root['selection_cursor']
        if not isinstance(cursor, dict) or not isinstance(cursor.get('signature'), str):
            raise ValueError('素材的取值進度無效。')
        from .prompt_groups import validate_cursor
        validate_cursor(cursor.get('position'))


def sequence(root):
    return root.get('sequence') or root.get('variants')


def position(root, restart=False):
    from . import prompt_groups as groups
    seq = sequence(root)
    if not seq: return None
    saved = root.get('selection_cursor')
    if saved and not restart and saved.get('signature') == signature(root):
        value = copy.deepcopy(saved['position'])
        if value.get('group') in {g['id'] for g in seq['groups']}:
            value['exhausted'] = False
            return value
    # Legacy saved positions survive the version transition, including repeats.
    legacy = root.get('supply_cursor')
    if legacy and not restart:
        from .prompt_sources import legacy_signature
        if legacy.get('signature') == legacy_signature(root) and not legacy['position'].get('exhausted'):
            return copy.deepcopy(legacy['position'])
    return groups.cursor(seq, start=seq['selected'] if not restart else None)


def cycle_length(root):
    """Only the reader's outer flow determines its automatic batch length."""
    from .prompt_groups import remaining
    return remaining(sequence(root), position(root)) if sequence(root) else None


def resolve(root, *, rng=None):
    """Resolve one use and advance only the descendants actually used this time."""
    from . import prompt_groups as groups
    from .composition import active_overlay
    current, following = copy.deepcopy(root), copy.deepcopy(root)
    if not root.get('enabled', True): return current, following
    overlay = active_overlay(root)
    if overlay is not None:
        at = next(i for i, v in enumerate(root['overlays']) if v['id'] == overlay['id'])
        current['overlays'][at], following['overlays'][at] = resolve(overlay, rng=rng)
        return current, following
    field = 'sequence' if root.get('sequence') else 'variants' if root.get('variants') else None
    if field:
        record, after = groups.take(root[field], position(root), rng)
        group_id = record['group']
        current[field]['selected'] = group_id
        following[field]['selected'] = after['group']
        following['selection_cursor'] = dict(signature=signature(root), position=after)
        following.pop('supply_cursor', None)
        if field == 'sequence':
            round_id = uuid.uuid4().hex
            current = groups.resolve_round(current, record, round_id, use_override=True)
            group = groups.selected(current[field])
            next_group = next(g for g in following[field]['groups'] if g['id'] == group_id)
            # A manual current-round replacement is the actual selected value.
            if not current.get('round_override') and group['input'] == 'modules':
                for i, module in enumerate(group['modules']):
                    group['modules'][i], next_group['modules'][i] = resolve(module, rng=rng)
            following.pop('round_override', None); following.pop('round_id', None)
            following.get('round_drafts', {}).pop(group_id, None)
        return current, following
    if root.get('combination'):
        if root['combination']['input'] == 'modules':
            for i, module in enumerate(root['combination']['modules']):
                current['combination']['modules'][i], following['combination']['modules'][i] = resolve(module, rng=rng)
    else:
        for i, child in enumerate(root.get('children', [])):
            current['children'][i], following['children'][i] = resolve(child, rng=rng)
    return current, following


def commit_progress(target, following):
    """Merge progress only into the same unchanged placement; never replace edits."""
    from .composition import walk
    if target['id'] != following['id'] or signature(target) != signature(following): return False
    by_id = {part['id']: part for part in walk(following)}
    for part in walk(target):
        saved = by_id[part['id']]
        for field in ('selection_cursor', 'supply_cursor', 'round_id', 'round_override', 'round_drafts'):
            if field in saved: part[field] = copy.deepcopy(saved[field])
            else: part.pop(field, None)
        for field in ('sequence', 'variants'):
            if field in part: part[field]['selected'] = saved[field]['selected']
    return True


def view(root):
    """Small frozen selection map; the immutable definition is stored only once."""
    from .composition import effective_nodes
    result = {}
    for part in effective_nodes(root):
        fields = {name: copy.deepcopy(part[name]) for name in ('round_id', 'round_override') if name in part}
        seq = sequence(part)
        if seq: fields['selected'] = seq['selected']
        if fields: result[part['id']] = fields
    return result


def project(root, selections):
    from .composition import walk
    result = copy.deepcopy(root)
    for part in walk(result):
        fields = selections.get(part['id'], {})
        seq = sequence(part)
        if seq and 'selected' in fields: seq['selected'] = fields['selected']
        for key in ('round_id', 'round_override'):
            if key in fields: part[key] = copy.deepcopy(fields[key])
            else: part.pop(key, None)
        part.pop('round_drafts', None)
    return result


def progress(before, following):
    from .composition import walk
    previous = {v['id']: v for v in walk(before)}; changed = {}
    for part in walk(following):
        old = previous[part['id']]
        fields = {}
        for field in ('selection_cursor', 'supply_cursor', 'round_id', 'round_override', 'round_drafts'):
            if old.get(field) != part.get(field): fields[field] = copy.deepcopy(part.get(field))
        seq = sequence(part)
        if seq and seq['selected'] != sequence(old)['selected']: fields['selected'] = seq['selected']
        if fields: changed[part['id']] = fields
    return changed


def apply_progress(root, changes):
    from .composition import walk
    for part in walk(root):
        for key, value in changes.get(part['id'], {}).items():
            if key == 'selected': sequence(part)['selected'] = value
            elif value is None: part.pop(key, None)
            else: part[key] = copy.deepcopy(value)
