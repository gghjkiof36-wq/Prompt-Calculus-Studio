"""Explicit finite supply plans. Scheduling consumes a plan, never invents one."""
import copy
import hashlib
import json
import uuid
from . import prompt_groups as groups, composition as comp, prompt_materials as pm


def definitions(state):
    return state.get('canvas_functions', {}).get('text_sources', {})


def add(state, position=(0, 0), root=None):
    unselected = root is None
    root = copy.deepcopy(root or comp.node('讀取流程'))
    root.setdefault('sequence', groups.from_node(root))
    if 'material_type' in root: root['material_type'] = 'flow'
    key = 'groups_' + uuid.uuid4().hex
    state.setdefault('canvas_functions', dict(images={}, preview=True, preview_attached=False)).setdefault('text_sources', {})[key] = dict(
        name=root['name'], root=root, supply='batch', count_mode='auto', limit=10, unselected=unselected)
    state.setdefault('text_positions', {})[key] = list(position)
    groups.mark_version(state)
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
    return pm.reset(root)


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
        if source.get('count_mode', 'auto') not in ('auto', 'specified'):
            raise ValueError('流程來源的批次數量設定無效。')
        comp.validate_node(source['root'])
        if not source['root'].get('sequence'): raise ValueError('流程來源缺少流程定義。')


def legacy_signature(root):
    seq = copy.deepcopy(root['sequence']); seq.pop('selected', None)
    return hashlib.sha256(json.dumps(seq, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def signature(root):
    from .prompt_materials import signature as material_signature
    return material_signature(root)


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
                if not state['uses'][ident].get('supply_origin') and any(pm.sequence(n) for n in comp.walk(state['uses'][ident])):
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
    return pm.position(root, restart)


def build(state, ids, *, count=None, restart=False, count_only=False):
    """Freeze choices now, including random draws, repeats and both draft forms."""
    from .flow_data import image_list
    specs, lengths, working = {}, [], {}
    for key in dict.fromkeys(ids):
        definition = source(state, key)
        if definition:
            if definition.get('unselected'):
                raise ValueError('請先選擇要讀取的流程。')
            root = copy.deepcopy(definition['root'])
            if restart: pm.reset(root)
            batched = not key.startswith('use:') and definition['supply'] == 'batch'
            specs[key] = dict(kind='text', root=root, batch=batched, signature=signature(root))
            working[key] = copy.deepcopy(root)
            if batched:
                lengths.append(definition['limit'] if definition.get('count_mode') == 'specified' else pm.cycle_length(root))
        else:
            image = state['canvas_functions']['images'][key]; items = image_list(state, key)
            index = image.get('index', 0)
            reservation = image.get('supply_reservation', {})
            if reservation.get('batch') == image.get('batch_id') and not restart:
                index = reservation.get('next', index)
            # Upstream Stage results are scoped collections, not the prior manual index.
            from .flow_data import incoming
            if incoming(state, key, 'image') or image.get('stage_reference'): index = 0
            if not items: raise ValueError('圖片來源沒有可供應的圖片。')
            batched = image.get('supply', 'batch') == 'batch'
            index %= len(items)
            specs[key] = dict(kind='image', items=items, index=index, batch=image.get('batch_id'), batched=batched)
            if batched: lengths.append(len(items))
    total = groups.paired_length(lengths, count)
    if not total: raise ValueError('來源已供應完畢，請選擇下一個起點。')
    if count_only:return total
    rounds = []; constants = {}
    for index in range(total):
        picks = {}
        for key, spec in specs.items():
            if spec['kind'] == 'text':
                if index and not spec['batch']:
                    picks[key] = dict(constants[key], progress={})
                    continue
                before = working[key]; current, after = pm.resolve(before)
                record = dict(selections=pm.view(current), progress=pm.progress(before, after))
                # Keep the outer ID available to existing history/selection views.
                if current.get('sequence'): record['group'] = current['sequence']['selected']
                picks[key] = record; working[key] = after
                constants[key] = record
            else: picks[key] = dict(index=(spec['index']+(index if spec['batched'] else 0)) % len(spec['items']))
        rounds.append(picks)
    return dict(version=2, id=uuid.uuid4().hex, sources=specs, rounds=rounds, committed=0)


def project(state, plan, picks):
    """Apply a frozen round to a private context and return image substitutions."""
    images = {}; positions = {}
    for key, record in picks.items():
        spec = plan['sources'][key]
        if spec['kind'] == 'text':
            root = (pm.project(spec['root'], record['selections']) if 'selections' in record else
                    groups.resolve_round(spec['root'], record, record['round'], use_override=record['override']))
            if key.startswith('use:'): state['uses'][key[4:]] = root
            else: state['canvas_functions']['text_sources'][key]['root'] = root
        else:
            image = copy.deepcopy(spec['items'][record['index']]); images[key] = image
            state['canvas_functions']['images'][key]['index'] = record['index']
            positions[key] = dict(batch=spec['batch'], index=record['index'], plan=plan['id'], accepted=plan.get('version')==2)
            state['canvas_functions']['images'][key]['supply_position'] = copy.deepcopy(positions[key])
    return images, positions


def reserve(state, plan, select_next=True, through=None):
    """Advance reservation positions only after the entire enqueue is accepted."""
    if 'batches' in plan:
        for batch in plan['batches']: reserve(state, batch, select_next)
        return
    if plan.get('version') == 2:
        end = len(plan['rounds']) if through is None else through
        if not plan.get('committed', 0) <= end <= len(plan['rounds']):
            raise ValueError('批次保存進度無效。')
        for picks in plan['rounds'][plan.get('committed', 0):end]:
            for key, record in picks.items():
                spec = plan['sources'][key]
                if spec['kind'] == 'text':
                    value = source(state, key)
                    if value and value['root']['id'] == spec['root']['id'] and signature(value['root']) == spec['signature']:
                        pm.apply_progress(value['root'], record['progress'])
                else:
                    image = state.get('canvas_functions', {}).get('images', {}).get(key)
                    if image and image.get('batch_id') == spec['batch']:
                        index = (record['index']+1) % len(spec['items'])
                        image['supply_reservation'] = dict(batch=spec['batch'], plan=plan['id'], next=index)
                        image['index'] = index; image['source'] = copy.deepcopy(spec['items'][index])
        plan['committed'] = end
        groups.mark_version(state)
        return
    for key, spec in plan['sources'].items():
        if spec['kind'] == 'image':
            image = state.get('canvas_functions', {}).get('images', {}).get(key)
            if image and image.get('batch_id') == spec['batch']:
                image['supply_reservation'] = dict(batch=spec['batch'], plan=plan['id'], next=spec['index']+len(plan['rounds']))
            continue
        current = source(state, key)
        if (not current or current['root']['id'] != spec['root']['id'] or
                legacy_signature(current['root']) != spec['signature']): continue
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
