"""Reusable complete Prompt groups and serializable per-use cursors. Qt-free.

Definitions contain inactive drafts as well as the selected form. Cursors and
resolved rounds are detached values, so queue preparation never edits a source.
"""
import copy
import random
import uuid

MODES = {'fixed': '固定', 'increment': '遞增', 'decrement': '遞減', 'random': '隨機'}
MAX_GROUPS = 5000
MAX_REPEAT = 10000
MAX_ROUNDS = 100000


def module_summary(modules):
    """Describe detached contents without calling a nested flow a text module."""
    from .prompt_materials import kind, TYPES
    counts={key:sum(kind(module)==key for module in modules) for key in TYPES}
    return ' · '.join(f'{count} 個{TYPES[key]}' for key,count in counts.items() if count) or '0 個文字模塊'


def requires_version_five(state):
    """Feature opt-in, not a blanket migration of existing documents."""
    from .composition import walk
    for owner in [state, *state.get('workspaces', []), *state.get('workspace_scenes', {}).get('items', {}).values()]:
        if not isinstance(owner, dict): continue
        if owner.get('canvas_functions', {}).get('text_sources'): return True
        if any(v.get('supply')=='step' for v in owner.get('canvas_functions',{}).get('images',{}).values()):return True
        roots=[*owner.get('uses', {}).values(), *owner.get('instances', {}).values()]
        roots += [item['composition'] for item in owner.get('items', []) if 'composition' in item]
        for root in roots:
            if any(part.get('sequence') or part.get('disabled_tags') or part.get('deduplicate') for part in walk(root)):return True
    return False


def mark_version(state):
    from .state_schema import mark_version as mark
    mark(state)


def new_group(name='組合 1', text='', modules=None, content_kind='tags'):
    return dict(id=uuid.uuid4().hex, name=name, repeat=1,
                input='modules' if modules else 'text', text=text,
                content_kind=content_kind, modules=copy.deepcopy(modules or []))


def from_node(root):
    """Keep the legacy body intact as a first group, including its children."""
    from . import composition as comp
    if root.get('sequence'):
        return copy.deepcopy(root['sequence'])
    body = comp.clone_node(root)
    body.update(weight=10, enabled=True, excludes=[], grouped=False)
    for field in ('sequence', 'round_override', 'disabled_tags', 'deduplicate'):
        body.pop(field, None)
    first = new_group(root.get('name') or '組合 1', root.get('prompt', ''),
                      [body] if root.get('prompt') or root.get('children') or root.get('overlays') else [], root.get('content_kind', 'tags'))
    first['input'] = 'modules' if root.get('children') or root.get('overlays') else 'text'
    return dict(version=1, mode='fixed', start=first['id'], selected=first['id'], groups=[first])


def validate(sequence, visit=None, depth=0):
    if not isinstance(sequence, dict) or sequence.get('version') != 1:
        raise ValueError('流程版本不支援，原資料保留。')
    if sequence.get('mode') not in MODES:
        raise ValueError('流程的變量模式無效。')
    if type(sequence.get('random_repeats', True)) is not bool:
        raise ValueError('隨機重複設定無效。')
    groups = sequence.get('groups')
    if not isinstance(groups, list) or not 1 <= len(groups) <= MAX_GROUPS:
        raise ValueError('流程須包含 1–5000 個組合。')
    ids = set()
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get('id'), str) or not group['id'] or group['id'] in ids:
            raise ValueError('組合 ID 缺少或重複。')
        ids.add(group['id'])
        if not isinstance(group.get('name'), str) or not group['name'].strip():
            raise ValueError('請填寫組合名稱。')
        if type(group.get('repeat')) is not int or not 1 <= group['repeat'] <= MAX_REPEAT:
            raise ValueError('重複次數須為 1–10000 的整數。')
        if group.get('input') not in ('modules', 'text') or not isinstance(group.get('text'), str):
            raise ValueError('組合內容格式無效。')
        if group.get('content_kind', 'tags') not in ('tags', 'text', 'markdown'):
            raise ValueError('組合文字格式不支援。')
        if not isinstance(group.get('modules'), list):
            raise ValueError('組合內的素材清單無效。')
        if type(group.get('deduplicate', False)) is not bool:
            raise ValueError('組合去重設定無效。')
        if visit:
            for module in group['modules']:
                visit(module, depth + 1)
    if sequence.get('start') not in ids or sequence.get('selected') not in ids:
        raise ValueError('請為流程選擇有效的起始組合。')
    return sequence


def selected(sequence):
    return next(g for g in sequence['groups'] if g['id'] == sequence['selected'])


def select(sequence, ident):
    if ident not in {g['id'] for g in sequence['groups']}:
        raise ValueError('這一組已不存在。')
    sequence['selected'] = ident


def duplicate(sequence, ident):
    from .composition import clone_node
    at = next(i for i, g in enumerate(sequence['groups']) if g['id'] == ident)
    value = copy.deepcopy(sequence['groups'][at])
    value.update(id=uuid.uuid4().hex, name=value['name'] + ' 副本')
    value['modules'] = [clone_node(m) for m in value['modules']]
    sequence['groups'].insert(at + 1, value)
    return value


def remove(sequence, ident, replacement=None):
    remaining = [g for g in sequence['groups'] if g['id'] != ident]
    if not remaining:
        raise ValueError('至少保留一組內容。')
    if sequence['start'] == ident and replacement not in {g['id'] for g in remaining}:
        raise ValueError('刪除起始組合前，請選擇新的起始組合。')
    sequence['groups'] = remaining
    if sequence['start'] == ident:
        sequence['start'] = replacement
    if sequence['selected'] == ident:
        sequence['selected'] = sequence['start']


def render_group(group, transform=None):
    from .composition import render, join_fragments
    if group['input'] == 'text':
        text = group['text']
        result = transform(text) if transform and group.get('content_kind', 'tags') == 'tags' else text
        tag_content = group.get('content_kind', 'tags') == 'tags'
    else:
        fragments=[];seen=set()
        for module in group['modules']:
            text=render(module,transform);kind=output_kind(module)
            if group.get('deduplicate') and kind=='tags':
                from .prompt_tags import tokens
                unique=[]
                for tag in tokens(text):
                    if tag not in seen:unique.append(tag);seen.add(tag)
                text=', '.join(unique)
            fragments.append((text,kind))
        result = join_fragments(fragments)
        tag_content = False  # Tag fragments were handled without touching prose or weights.
    if group.get('deduplicate') and tag_content:
        from .prompt_tags import apply
        result = apply(result, deduplicate=True)
    return result


def output_kind(root):
    """Do not parse prose hidden inside a composition as tags."""
    if not root.get('enabled', True):
        return 'tags'
    from .composition import active_overlay
    overlay = active_overlay(root)
    if overlay is not None:
        return output_kind(overlay)
    if root.get('sequence') or root.get('combination') or root.get('variants'):
        group = root.get('combination') or selected(root.get('sequence') or root['variants'])
        if group['input'] == 'text':
            return group.get('content_kind', 'tags')
        children = group['modules']
        own = 'tags'
    else:
        children = root.get('children', [])
        own = root.get('content_kind', 'tags')
    return 'tags' if own == 'tags' and all(output_kind(c) == 'tags' for c in children) else 'text'


def render_body(root, transform=None):
    sequence = root['sequence']; group = selected(sequence)
    override = root.get('round_override')
    if (override and override.get('group') == group['id'] and
            override.get('round') == root.get('round_id')):
        text = override['text']
        return transform(text) if transform and output_kind(root) == 'tags' else text
    if group['id'] in root.get('round_drafts', {}):
        text = root['round_drafts'][group['id']]
        return transform(text) if transform and output_kind(root) == 'tags' else text
    return render_group(group, transform)


def cursor(sequence, *, start=None):
    validate(sequence)
    ident = start or (sequence['selected'] if sequence['mode'] == 'fixed' else sequence['start'])
    if ident not in {g['id'] for g in sequence['groups']}:
        raise ValueError('供應起點已不存在。')
    return dict(group=ident, repeat=0, exhausted=False, draws=0)


def validate_cursor(position):
    if (not isinstance(position, dict) or not isinstance(position.get('group'), str) or
            type(position.get('repeat')) is not int or not 0 <= position['repeat'] < MAX_REPEAT or
            type(position.get('draws')) is not int or position['draws'] < 0 or
            type(position.get('exhausted', False)) is not bool):
        raise ValueError('素材取值進度無效。')
    if 'bag' in position and (not isinstance(position['bag'], list) or
            any(not isinstance(v, str) for v in position['bag']) or
            len(position['bag']) != len(set(position['bag'])) or
            type(position.get('bag_index')) is not int or not 0 <= position['bag_index'] < len(position['bag'])):
        raise ValueError('隨機取值進度無效。')
    if 'ready' in position and type(position['ready']) is not bool:
        raise ValueError('隨機取值進度無效。')


def remaining(sequence, position=None):
    """Length of a full automatic cycle, even when starting in its middle."""
    mode = sequence['mode']
    if mode == 'fixed' or mode == 'random' and sequence.get('random_repeats', True):
        return None
    return sum(g['repeat'] for g in sequence['groups'])


def take(sequence, position, rng=None):
    """Reserve exactly one round and return the next cursor, without mutation."""
    result = copy.deepcopy(position); mode = sequence['mode']; groups = sequence['groups']
    result['exhausted'] = False; rng = rng or random.SystemRandom()
    def draw():
        if sequence.get('random_repeats', True):
            result['group'] = rng.choice(groups)['id']
        else:
            bag = result.get('bag', [])
            index = result.get('bag_index', -1) + 1
            if set(bag) != {g['id'] for g in groups} or index >= len(bag):
                bag = [g['id'] for g in groups]; rng.shuffle(bag); index = 0
            result.update(bag=bag, bag_index=index, group=bag[index])
        result['draws'] += 1; result['ready'] = True
    if mode == 'random' and not result.get('ready'): draw()
    at = next((i for i, g in enumerate(groups) if g['id'] == result['group']), None)
    if at is None:
        raise ValueError('供應中的組合已不存在，請重新建立批次。')
    group = groups[at]
    record = dict(group=group['id'], repetition=result['repeat'] + 1)
    if mode == 'fixed':
        return record, result
    result['repeat'] += 1
    if result['repeat'] >= group['repeat']:
        result['repeat'] = 0
        if mode == 'random':
            draw()
        else:
            following = at + (1 if mode == 'increment' else -1)
            result['group'] = groups[following % len(groups)]['id']
    return record, result


def resolve_round(root, selection, round_id, *, use_override=False):
    """Freeze one use; the same group repeated twice has different round IDs."""
    value = copy.deepcopy(root)
    select(value['sequence'], selection['group'])
    override = value.get('round_override')
    if use_override and selection['group'] in value.get('round_drafts', {}):
        override = dict(group=selection['group'], text=value['round_drafts'][selection['group']], round=round_id)
        value['round_override'] = override
    if use_override and override and override.get('group') == selection['group']:
        override['round'] = round_id
    else:
        value.pop('round_override', None)
    value['round_id'] = round_id
    value.pop('round_drafts', None)
    return value


def paired_length(lengths, limit=None):
    """Finite participants bound a batch; constants do not count as length one."""
    finite = [n for n in lengths if n is not None]
    if any(type(n) is not int or n < 0 for n in finite):
        raise ValueError('來源輪數無效。')
    if limit is not None:
        if type(limit) is not int or not 1 <= limit <= MAX_ROUNDS:
            raise ValueError('批次輪數須為 1–100000。')
        return limit
    count = max(finite) if finite else 10
    if count > MAX_ROUNDS:
        raise ValueError('本批超過 100000 輪，請縮小供應範圍。')
    return count
