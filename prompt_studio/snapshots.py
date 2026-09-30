"""Portable, Qt-free prompt snapshots shared with the ComfyUI extension."""
import copy
import hashlib
import json
from .composition import walk
from .core import build_prompt, validate_state, uid, output_groups, TEMPORARY_GROUP, separate_selections

SCHEMA_VERSION = 1
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024


def portable_state(state, selected_only=False):
    validate_state(state)
    if selected_only and separate_selections(state) and 'multi_output' not in state:
        state=copy.deepcopy(state)
        if state.get('selection_view','list')=='canvas':
            state.update(selections={},weights={},instances={},temporary=[],temporary_before=None)
            for workspace in state['workspaces']: workspace.update(picks={},weights={},instances={})
        else:
            state['uses']={}
            for workspace in state['workspaces']: workspace.update(uses={},fixed_uses=[])
        state['output_order']=output_groups(state)
    chosen = {i for values in state['selections'].values() for i in values}
    chosen.update(n.get('source_id') for owner in [state,*state['workspaces']] for root in owner.get('uses',{}).values() for n in walk(root))
    items = [dict(id=i['id'], module=i['module'], name=i['name'], prompt=i['prompt'],
                  aliases=list(i['aliases']), notes=i['notes'], excludes=list(i.get('excludes',[])),
                  **({'composition':copy.deepcopy(i['composition'])} if 'composition' in i else {})) for i in state['items']
             if not selected_only or 'multi_output' in state or i['id'] in chosen]
    allowed = {i['id'] for i in items}
    workspaces = []
    for w in state['workspaces']:
        if selected_only and w['id'] != state['workspace']:
            continue
        workspaces.append(dict(id=w['id'], name=w['name'], fixed=list(w['fixed']),
            picks={m: [i for i in values if i in allowed] for m, values in w['picks'].items()},
            weights={k:v for k,v in w.get('weights',{}).items() if k in allowed},
            **({'instances':{k:copy.deepcopy(v) for k,v in w['instances'].items() if k in allowed}} if 'instances' in w else {}),
            uses=copy.deepcopy(w.get('uses',{})),
            fixed_uses=list(w.get('fixed_uses',[])),
            **({'canvas_owners':copy.deepcopy(w['canvas_owners'])} if 'canvas_owners' in w else {}),
            parameters=copy.deepcopy(w.get('parameters', {}))))
    result = dict(version=state['version'], modules=[dict(id=m['id'], name=m['name'], mode=m['mode'],
        **({'canvas_library':m['canvas_library']} if 'canvas_library' in m else {})) for m in state['modules']],
        items=items, workspaces=workspaces, workspace=state['workspace'],
        selections=copy.deepcopy(state['selections']), temporary=list(state.get('temporary', [])),
        weights={k: v for k, v in state.get('weights', {}).items() if k in allowed},
        **({'instances':{k:copy.deepcopy(v) for k,v in state['instances'].items() if k in allowed}} if 'instances' in state else {}),
        uses=copy.deepcopy(state.get('uses',{})),
        **({'prompt_layout':state['prompt_layout']} if 'prompt_layout' in state else {}),
        output_order=output_groups(state,include_hidden=not selected_only),
        temporary_before=state.get('temporary_before'), draft=state['draft'],
        draft_base=state.get('draft_base', ''), selection_view=state.get('selection_view','list'),
        settings={'separate_selections':separate_selections(state)}, dictionary={})
    if any('pcs_effective' in p for p in state.get('generation',{}).get('profiles',[])):
        result['generation']=copy.deepcopy(state['generation'])
    if 'multi_output' in state:
        from .multi_output import capture_current
        for key in ('multi_output','text_positions','text_sizes','generation','canvas_functions','_execution_inputs'):
            if key in state: result[key]=copy.deepcopy(state[key])
        capture_current(result)
        result['output_order']=output_groups(state,include_hidden=True)
    if not selected_only and 'workspace_scenes' in state:
        from .workspace_scene import capture
        result['workspace_scenes']=copy.deepcopy(state['workspace_scenes']);capture(result)
    return result


def make_snapshot(state, library_id='', library_revision=''):
    clean = portable_state(state, selected_only=True)
    generated = build_prompt(clean)
    result = dict(schema_version=clean['version'], library_id=str(library_id),
        library_revision=str(library_revision), state=clean, generated_prompt=generated,
        final_prompt=clean['draft'] if clean['draft'] is not None else generated,
        manual_draft=clean['draft'] is not None)
    if 'multi_output' in clean:
        from .multi_output import compiled_outputs
        result['outputs']=compiled_outputs(clean)
    validate_snapshot(result)
    return result


def validate_snapshot(snapshot):
    if not isinstance(snapshot, dict) or type(snapshot.get('schema_version')) is not int or snapshot.get('schema_version') not in (1, 2, 3, 4):
        raise ValueError('不支援的 Prompt Studio 快照版本。')
    if len(json.dumps(snapshot, ensure_ascii=False).encode('utf-8')) > MAX_SNAPSHOT_BYTES:
        raise ValueError('模組快照過大。')
    state = snapshot.get('state')
    validate_state(state)
    if snapshot['schema_version'] != state['version']:
        raise ValueError('快照與組合資料版本不一致。')
    generated = build_prompt(state)
    final = state['draft'] if state['draft'] is not None else generated
    if (snapshot.get('generated_prompt') != generated or snapshot.get('final_prompt') != final
            or type(snapshot.get('manual_draft')) is not bool
            or snapshot['manual_draft'] != (state['draft'] is not None)):
        raise ValueError('模組快照與實際文字不一致。')
    if 'multi_output' in state:
        from .multi_output import compiled_outputs
        if snapshot.get('outputs')!=compiled_outputs(state): raise ValueError('多輸出快照與實際文字不一致。')
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
    from .workspace_scene import capture,scene
    capture(result)
    source = snapshot['state']
    isolated=separate_selections(source)
    view=source.get('selection_view','canvas' if source.get('uses') and not any(source['selections'].values()) else 'list')
    result['settings']['separate_selections']=isolated
    current_draft_view=current.get('selection_view','list') if separate_selections(current) else 'shared'
    result.setdefault('view_drafts',{})[current_draft_view]=dict(draft=current['draft'],draft_base=current.get('draft_base',''))
    result['selection_view']=view
    # Historical snapshots without a layout keep their exact original text.
    result['prompt_layout']=source.get('prompt_layout','compact')
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
        if target is None or target['prompt'] != i['prompt'] or target['module'] != mid or target.get('excludes',[]) != i.get('excludes',[]) or target.get('composition') != i.get('composition'):
            target = dict(i, id=uid(), module=mid, name=i['name'] + ' · 歷史快照')
            result['items'].append(target)
        item_map[i['id']] = target['id']
    result['uses']={}; use_map={}
    for key,root in source.get('uses',{}).items():
        ident=uid(); use_map[key]=ident; root=copy.deepcopy(root)
        for part in walk(root):
            if part.get('source_id') in item_map: part['source_id']=item_map[part['source_id']]
            if part.get('source_module') in module_map: part['source_module']=module_map[part['source_module']]
        result['uses'][ident]=root
    result['output_order'] = [TEMPORARY_GROUP if mid == TEMPORARY_GROUP else use_map[mid] if mid in use_map else module_map[mid]
                              for mid in output_groups(source)]
    result['selections'] = {module_map[m]: [item_map[i] for i in values]
                            for m, values in source['selections'].items()}
    result['weights'] = {item_map[k]: v for k, v in source.get('weights', {}).items() if k in item_map}
    result['instances'] = {item_map[k]: copy.deepcopy(v) for k,v in source.get('instances', {}).items() if k in item_map}
    result['version'] = max(result['version'], source['version'])
    w = source['workspaces'][0]
    workspace = dict(id=uid(), name='圖片 · ' + w['name'],
        fixed=[module_map[m] for m in w['fixed']],
        picks=copy.deepcopy(result['selections']), weights=copy.deepcopy(result['weights']),
        instances=copy.deepcopy(result['instances']), uses=copy.deepcopy(result['uses']),
        fixed_uses=[use_map[k] for k in w.get('fixed_uses',[]) if k in use_map],
        parameters=copy.deepcopy(w.get('parameters', {})))
    result['workspaces'].append(workspace)
    result.update(workspace=workspace['id'], temporary=list(source['temporary']),
        temporary_before=module_map.get(source.get('temporary_before')),
        draft=source['draft'], draft_base=source.get('draft_base', ''))
    if isolated and 'multi_output' not in source:
        # A snapshot restores the active composition, leaving the other view's
        # choices intact. Its draft has already been retained above.
        keep=('selections','weights','instances','temporary','temporary_before') if view=='canvas' else ('uses',)
        for key in keep:
            if key in current: result[key]=copy.deepcopy(current[key])
            else: result.pop(key,None)
        hidden=[key for key in output_groups(current,include_hidden=True) if (key in current.get('uses',{}))!=(view=='canvas')]
        result['output_order']=list(dict.fromkeys(result['output_order']+hidden))
    if 'multi_output' in source:
        result['multi_output']=copy.deepcopy(source['multi_output'])
        chain=result['multi_output'].get('workflow_order',{}).get('chain')
        if chain:chain['enabled']=False
        workspace['canvas_owners']={use_map[key]:cid for cid,c in source['multi_output']['canvases'].items() for key in c['members']}
        for canvas in result['multi_output']['canvases'].values():
            canvas['members']=[use_map[key] for key in canvas['members']]
            if 'source_members' in canvas:
                canvas['source_members']=[use_map[key] for key in canvas['source_members'] if key in use_map]
            if 'edit_positions' in canvas:
                canvas['edit_positions']={use_map.get(k.split(':')[0],k.split(':')[0])+(':'+k.split(':',1)[1] if ':' in k else ''):v for k,v in canvas['edit_positions'].items()}
        for output in result['multi_output']['outputs'].values():
            saved=output.get('list_state',{})
            saved['selections']={module_map[m]:[item_map[i] for i in picks] for m,picks in saved.get('selections',{}).items()}
            for field in ('weights','instances'):
                saved[field]={item_map.get(k,k):v for k,v in saved.get(field,{}).items()}
            if saved.get('temporary_before') is not None: saved['temporary_before']=module_map.get(saved['temporary_before'])
        for field in ('generation','canvas_functions','text_sizes','text_positions'):
            if field in source: result[field]=copy.deepcopy(source[field])
        for field in ('text_sizes','text_positions'):
            if field in result:
                result[field]={use_map.get(k.split(':')[0],k.split(':')[0])+(':'+k.split(':',1)[1] if ':' in k else ''):v for k,v in result[field].items()}
    elif 'multi_output' in current:
        result.pop('multi_output',None)
    from .state_loading import prepare_state
    if 'workspace_scenes' in result:result['workspace_scenes']['items'][workspace['id']]=scene(result)
    result=prepare_state(result,multi='multi_output' in current)
    if build_prompt(result) != snapshot['generated_prompt']:
        raise ValueError('恢復後的組合不一致，原狀態未變更。')
    return result
