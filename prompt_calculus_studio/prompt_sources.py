"""Explicit finite supply plans. Scheduling consumes a plan, never invents one."""
import copy
import hashlib
import json
import uuid
from . import prompt_groups as groups, composition as comp


def definitions(state):
    return state.get('canvas_functions', {}).get('text_sources', {})


def add(state, position=(0, 0), root=None):
    unselected = root is None
    root = copy.deepcopy(root or comp.node('讀取流程'))
    root.setdefault('sequence', groups.from_node(root))
    key = 'groups_' + uuid.uuid4().hex
    state.setdefault('canvas_functions', dict(images={}, preview=True, preview_attached=False)).setdefault('text_sources', {})[key] = dict(
        name=root['name'], root=root, supply='step', limit=10, unselected=unselected)
    state.setdefault('text_positions', {})[key] = list(position)
    state['version']=max(5,state['version'])
    return key


def library_root(state, ident):
    """Read an existing group material once; never retain a live library reference."""
    from .materials import item, fingerprint
    material = item(state, ident)
    if not material.get('composition', {}).get('sequence'):
        raise ValueError('請選擇素材庫裡的流程。')
    root = comp.clone_node(comp.prototype(material))
    root.update(name=material['name'], source_id=ident, source_module=material['module'],
                source_revision=fingerprint(material))
    for field in ('supply_cursor', 'round_override', 'round_id', 'round_drafts', 'supply_origin'):
        root.pop(field, None)
    root['sequence']['selected'] = root['sequence']['start']
    return root


def read_library(state, key, ident=None):
    value = definitions(state)[key]
    root = library_root(state, ident or value['root'].get('source_id'))
    value.update(root=root, name=root['name'], unselected=False)


def validate(value):
    if not isinstance(value, dict) or len(value) > 100:
        raise ValueError('流程來源清單無效。')
    for key, source in value.items():
        if (not isinstance(key, str) or not key or '::' in key or not isinstance(source, dict) or
                not isinstance(source.get('name'), str) or not source['name'].strip() or
                source.get('supply') not in ('step', 'batch') or type(source.get('limit')) is not int or
                not 1 <= source['limit'] <= groups.MAX_ROUNDS or type(source.get('unselected', False)) is not bool):
            raise ValueError('流程來源設定無效。')
        comp.validate_node(source['root'])
        if not source['root'].get('sequence'): raise ValueError('流程來源缺少流程定義。')


def signature(root):
    seq = copy.deepcopy(root['sequence']); seq.pop('selected', None)
    return hashlib.sha256(json.dumps(seq, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def source(state, key):
    if key.startswith('use:'):
        root = state.get('uses', {}).get(key[4:])
        return dict(root=root, supply='step', name=root['name'], limit=10) if root else None
    return definitions(state).get(key)


def connected(state, keys):
    """Deduplicate fan-out; only traversal before a Stage boundary participates."""
    from .stage_model import input_nodes
    upstream = input_nodes(state, keys); found = []
    for key in sorted(upstream):
        if key in definitions(state): found.append(key)
        if key in state.get('multi_output', {}).get('canvases', {}):
            for ident in state['multi_output']['canvases'][key]['members']:
                if state['uses'][ident].get('sequence') and not state['uses'][ident].get('supply_origin'):
                    found.append('use:'+ident)
        image = state.get('canvas_functions', {}).get('images', {}).get(key)
        if image and image.get('iterate'): found.append(key)
    return list(dict.fromkeys(found))


def scheduler_sources(state, scheduler):
    from .flow_data import incoming
    from .module_contracts import binding_sources
    origins=[origin for ch in state['multi_output']['schedulers'][scheduler]['channels']
        if (origin := incoming(state, scheduler+'::'+ch['id'], ch['type']))]
    return connected(state,origins+binding_sources(state,scheduler))


def automatic(state, ids):
    return any((source(state, key) or {}).get('supply') == 'batch' or
        state.get('canvas_functions', {}).get('images', {}).get(key, {}).get('supply', 'batch') == 'batch'
        and key in state.get('canvas_functions', {}).get('images', {}) for key in ids)


def position(root, *, restart=False):
    if root['sequence']['mode'] == 'fixed':return groups.cursor(root['sequence'])
    saved = root.get('supply_cursor')
    if saved and saved.get('signature') == signature(root) and not restart:
        result = copy.deepcopy(saved['position'])
        if not result['exhausted']: return result
    return groups.cursor(root['sequence'])


def build(state, ids, *, count=None, restart=False, count_only=False):
    """Freeze choices now, including random draws, repeats and both draft forms."""
    from .flow_data import image_list
    specs, lengths = {}, []
    random_limits = []
    for key in ids:
        definition = source(state, key)
        if definition:
            if definition.get('unselected'):
                raise ValueError('請先選擇要讀取的流程。')
            root = copy.deepcopy(definition['root']); pos = position(root, restart=restart)
            specs[key] = dict(kind='text', root=root, position=pos, signature=signature(root))
            length = groups.remaining(root['sequence'], pos); lengths.append(length)
            if root['sequence']['mode'] in ('random','fixed'): random_limits.append(definition['limit'])
        else:
            image = state['canvas_functions']['images'][key]; items = image_list(state, key)
            index = image.get('index', 0)
            reservation = image.get('supply_reservation', {})
            if reservation.get('batch') == image.get('batch_id') and not restart:
                index = reservation.get('next', index)
            # Upstream Stage results are scoped collections, not the prior manual index.
            from .flow_data import incoming
            if incoming(state, key, 'image') or image.get('stage_reference'): index = 0
            index = min(index, len(items)); specs[key] = dict(kind='image', items=items, index=index, batch=image.get('batch_id'))
            lengths.append(len(items)-index)
    if count is None and not any(n is not None for n in lengths):
        count = min(random_limits) if random_limits else None
    total = groups.paired_length(lengths, count)
    if not total: raise ValueError('來源已供應完畢，請選擇下一個起點。')
    if count_only:return total
    rounds = [];used = set()
    for index in range(total):
        picks = {}
        for key, spec in specs.items():
            if spec['kind'] == 'text':
                record, spec['position'] = groups.take(spec['root']['sequence'], spec['position'])
                token = (key, record['group'])
                picks[key] = dict(record, round=uuid.uuid4().hex, override=token not in used)
                used.add(token)
            else: picks[key] = dict(index=spec['index']+index)
        rounds.append(picks)
    return dict(id=uuid.uuid4().hex, sources=specs, rounds=rounds)


def project(state, plan, picks):
    """Apply a frozen round to a private context and return image substitutions."""
    images = {}; positions = {}
    for key, record in picks.items():
        spec = plan['sources'][key]
        if spec['kind'] == 'text':
            root = groups.resolve_round(spec['root'], record, record['round'], use_override=record['override'])
            if key.startswith('use:'): state['uses'][key[4:]] = root
            else: state['canvas_functions']['text_sources'][key]['root'] = root
        else:
            image = copy.deepcopy(spec['items'][record['index']]); images[key] = image
            state['canvas_functions']['images'][key]['index'] = record['index']
            positions[key] = dict(batch=spec['batch'], index=record['index'], plan=plan['id'])
            state['canvas_functions']['images'][key]['supply_position'] = copy.deepcopy(positions[key])
    return images, positions


def reserve(state, plan, select_next=True):
    """Advance reservation positions only after the entire enqueue is accepted."""
    for key, spec in plan['sources'].items():
        if spec['kind'] == 'image':
            image = state.get('canvas_functions', {}).get('images', {}).get(key)
            if image and image.get('batch_id') == spec['batch']:
                image['supply_reservation'] = dict(batch=spec['batch'], plan=plan['id'], next=spec['index']+len(plan['rounds']))
            continue
        current = source(state, key)
        if (not current or current['root']['id'] != spec['root']['id'] or
                signature(current['root']) != spec['signature']): continue
        root = current['root']; pos = copy.deepcopy(spec['position'])
        root['supply_cursor'] = dict(signature=spec['signature'], position=pos, plan=plan['id'])
        if select_next:root['sequence']['selected'] = pos['group']
        root.pop('round_override', None)
        for picks in plan['rounds']:
            if key in picks:root.get('round_drafts', {}).pop(picks[key]['group'], None)
        root.pop('round_id', None)


def resolved_value(state, key, kind):
    from .flow_data import value
    definition = definitions(state)[key]; root = copy.deepcopy(definition['root'])
    if definition.get('unselected'):
        return value('clip', '', source=key, name=definition['name']) if kind == 'clip' else value(
            'content', dict(kind='modules', roots=[], text=''), source=key, name=definition['name'])
    root['supply_origin'] = key
    if kind == 'clip': return value(kind, comp.render(root), source=key, name=definition['name'])
    return value('content', dict(kind='modules', roots=[root], text=comp.render(root)), source=key, name=definition['name'])
