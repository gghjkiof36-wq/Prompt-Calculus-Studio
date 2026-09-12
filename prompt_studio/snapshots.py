"""Portable, Qt-free prompt snapshots shared with the ComfyUI extension."""
import copy
import hashlib
import json
from .core import build_prompt, validate_state, uid, output_groups, TEMPORARY_GROUP

SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def portable_state(state, selected_only=False):
    validate_state(state)
    chosen = {i for values in state['selections'].values() for i in values}
    items = [dict(id=i['id'], module=i['module'], name=i['name'], prompt=i['prompt'],
                  aliases=list(i['aliases']), notes=i['notes'], excludes=list(i.get('excludes',[]))) for i in state['items']
             if not selected_only or i['id'] in chosen]
    allowed = {i['id'] for i in items}
    workspaces = []
    for w in state['workspaces']:
        if selected_only and w['id'] != state['workspace']:
            continue
        workspaces.append(dict(id=w['id'], name=w['name'], fixed=list(w['fixed']),
            picks={m: [i for i in values if i in allowed] for m, values in w['picks'].items()},
            weights={k:v for k,v in w.get('weights',{}).items() if k in allowed},
            parameters=copy.deepcopy(w.get('parameters', {}))))
    return dict(version=1, modules=[dict(id=m['id'], name=m['name'], mode=m['mode']) for m in state['modules']],
        items=items, workspaces=workspaces, workspace=state['workspace'],
        selections=copy.deepcopy(state['selections']), temporary=list(state.get('temporary', [])),
        weights={k: v for k, v in state.get('weights', {}).items() if k in allowed},
        output_order=output_groups(state),
        temporary_before=state.get('temporary_before'), draft=state['draft'],
        draft_base=state.get('draft_base', ''), settings={}, dictionary={})


def make_snapshot(state, library_id='', library_revision=''):
    clean = portable_state(state, selected_only=True)
    generated = build_prompt(clean)
    result = dict(schema_version=SCHEMA_VERSION, library_id=str(library_id),
        library_revision=str(library_revision), state=clean, generated_prompt=generated,
        final_prompt=clean['draft'] if clean['draft'] is not None else generated,
        manual_draft=clean['draft'] is not None)
    validate_snapshot(result)
    return result


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or snapshot.get('schema_version') != SCHEMA_VERSION:
        raise ValueError('不支援的 Prompt Studio 快照版本。')
    if len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        raise ValueError('模組快照過大。')
    state = snapshot.get('state')
    validate_state(state)
    generated = build_prompt(state)
    final = state['draft'] if state['draft'] is not None else generated
    if (snapshot.get('generated_prompt') != generated or snapshot.get('final_prompt') != final
            or type(snapshot.get('manual_draft')) is not bool
            or snapshot['manual_draft'] != (state['draft'] is not None)):
        raise ValueError('模組快照與實際文字不一致。')
    return snapshot


def revision(state):
    return hashlib.sha256(json.dumps(portable_state(state), ensure_ascii=False,
        sort_keys=True).encode('utf-8')).hexdigest()


def image_snapshots(metadata):
    if not isinstance(metadata, dict) or not isinstance(metadata.get('raw', {}), dict):
        return []
    envelope = metadata.get('raw', {}).get('prompt_studio', {})
    if not isinstance(envelope, dict) or envelope.get('schema_version') != 1:
        return []
    if not isinstance(envelope.get('bindings', []), list):
        return []
    found = []
    for binding in envelope.get('bindings', []):
        try:
            validate_snapshot(binding['snapshot'])
            found.append(binding)
        except (KeyError, TypeError, ValueError):
            continue
    return found


def restore_snapshot(current, snapshot):
    """Create a new workspace; preserve library entries and historical text."""
    validate_snapshot(snapshot)
    result = copy.deepcopy(current)
    source = snapshot['state']
    modules = {m['id']: m for m in result['modules']}
    items = {i['id']: i for i in result['items']}
    module_map, item_map = {}, {}
    for m in source['modules']:
        target = modules.get(m['id'])
        if target is None or target['mode'] != m['mode']:
            target = dict(m, id=uid(), name=m['name'] + ' · 歷史快照')
            result['modules'].append(target)
        module_map[m['id']] = target['id']
    for i in source['items']:
        target = items.get(i['id'])
        mid = module_map[i['module']]
        if target is None or target['prompt'] != i['prompt'] or target['module'] != mid or target.get('excludes',[]) != i.get('excludes',[]):
            target = dict(i, id=uid(), module=mid, name=i['name'] + ' · 歷史快照')
            result['items'].append(target)
        item_map[i['id']] = target['id']
    result['output_order'] = [TEMPORARY_GROUP if mid == TEMPORARY_GROUP else module_map[mid]
                              for mid in output_groups(source)]
    result['selections'] = {module_map[m]: [item_map[i] for i in values]
                            for m, values in source['selections'].items()}
    result['weights'] = {item_map[k]: v for k, v in source.get('weights', {}).items() if k in item_map}
    w = source['workspaces'][0]
    workspace = dict(id=uid(), name='圖片 · ' + w['name'],
        fixed=[module_map[m] for m in w['fixed']],
        picks=copy.deepcopy(result['selections']), weights=copy.deepcopy(result['weights']),
        parameters=copy.deepcopy(w.get('parameters', {})))
    result['workspaces'].append(workspace)
    result.update(workspace=workspace['id'], temporary=list(source['temporary']),
        temporary_before=module_map.get(source.get('temporary_before')),
        draft=source['draft'], draft_base=source.get('draft_base', ''))
    validate_state(result)
    if build_prompt(result) != snapshot['generated_prompt']:
        raise ValueError('恢復後的組合不一致，原狀態未變更。')
    return result
