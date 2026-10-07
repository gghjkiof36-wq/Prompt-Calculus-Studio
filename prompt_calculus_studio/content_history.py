"""Bounded local edit journal, separate from generation records and Undo stacks."""
import copy
import hashlib
import json
import math
import time
import uuid

LIMIT = 200
BYTE_LIMIT = 4 * 1024 * 1024
CONTENT_FIELDS = ('name', 'prompt', 'content_kind', 'children', 'overlays', 'enabled', 'weight',
                  'excludes', 'composition', 'aliases', 'notes', 'module', 'preview')


def _content(value):
    if not isinstance(value, dict):
        return value
    result = {key: copy.deepcopy(value[key]) for key in CONTENT_FIELDS if key in value}
    for field in ('children', 'overlays'):
        if field in result:
            result[field] = [dict(id=node['id'], **_content(node)) for node in result[field]]
    if 'composition' in result:
        node = result['composition']
        result['composition'] = dict(id=node['id'], **_content(node))
    return result


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False).encode('utf-8')).hexdigest()


def validate(state):
    journal = state.get('content_history')
    if journal is None:
        return
    if not isinstance(journal, dict) or journal.get('version') != 1 or not isinstance(journal.get('entries'), list):
        raise ValueError('內容歷史版本不支援，原資料保留。')
    if len(journal['entries']) > LIMIT or len(json.dumps(journal, ensure_ascii=False).encode('utf-8')) > BYTE_LIMIT:
        raise ValueError('內容歷史超過容量限制。')
    for entry in journal['entries']:
        if (not isinstance(entry, dict) or not isinstance(entry.get('id'), str)
                or not isinstance(entry.get('label'), str) or not isinstance(entry.get('workspace'), str)
                or type(entry.get('time')) not in (int, float) or not math.isfinite(entry['time'])
                or not isinstance(entry.get('changes'), list) or type(entry.get('content_changed')) is not bool):
            raise ValueError('內容歷史格式無效。')
        for change in entry['changes']:
            if (not isinstance(change, dict) or change.get('kind') not in ('material', 'canvas', 'output')
                    or not isinstance(change.get('target'), str)
                    or any(change.get(field) is not None and not isinstance(change[field], dict)
                           for field in ('before', 'after'))):
                raise ValueError('內容歷史項目無效。')
            if change['kind'] == 'output':
                if change.get('scope') not in ('canvas', 'list'): raise ValueError('輸出歷史範圍無效。')
                for field in ('before', 'after'):
                    saved = change.get(field)
                    if saved is not None and ((saved.get('draft') is not None and not isinstance(saved['draft'], str))
                                               or not isinstance(saved.get('draft_base', ''), str)):
                        raise ValueError('輸出歷史草稿無效。')


def _targets(state):
    result = {('material', v['id'], None): v for v in state.get('items', [])}
    result.update({('canvas', key, None): value for key, value in state.get('uses', {}).items()})
    for key, output in state.get('multi_output', {}).get('outputs', {}).items():
        for scope, saved in (('canvas', output), ('list', output.get('list_draft', {}))):
            result['output', key, scope] = dict(name=output['name'], draft=saved.get('draft'), draft_base=saved.get('draft_base', ''))
    return result


def _version_content(kind, value):
    if kind == 'output':
        value = value or {}
        return dict(draft=value.get('draft'), draft_base=value.get('draft_base', ''))
    return _content(value)


def _operation_state(state):
    # Async execution state and generated images are never edits.
    fields = ('modules', 'items', 'uses', 'text_positions', 'text_sizes', 'selections', 'temporary',
              'output_order', 'instances', 'weights', 'draft', 'draft_base', 'view_drafts')
    value = {key: copy.deepcopy(state[key]) for key in fields if key in state}
    multi = state.get('multi_output', {})
    value['multi_output'] = {key: copy.deepcopy(multi[key]) for key in
                            ('canvases', 'outputs', 'clip_inputs', 'connections', 'stages', 'schedulers') if key in multi}
    for source in value.get('items', []):
        source.pop('last_used', None)
    for kind in ('stages', 'schedulers', 'outputs'):
        for node in value['multi_output'].get(kind, {}).values():
            for key in ('results', 'status', 'running', 'progress', 'error', 'current', 'active_job'):
                node.pop(key, None)
    return value


def record(state, before, after, label='編輯', merge_key=None, coalesce=False):
    """Append real edit versions; a coalesced edit keeps its initial preimage."""
    if _operation_state(before) == _operation_state(after):
        return False
    old, new = _targets(before), _targets(after)
    def owners(value):
        return {key: canvas for canvas, data in value.get('multi_output', {}).get('canvases', {}).items()
                for key in data['members']}
    old_owners, new_owners = owners(before), owners(after)
    canvas_ids = set()
    before_canvases = before.get('multi_output', {}).get('canvases', {})
    after_canvases = after.get('multi_output', {}).get('canvases', {})
    canvas_ids.update(key for key in before_canvases.keys() | after_canvases.keys()
                      if before_canvases.get(key) != after_canvases.get(key))
    changes = []
    for kind, target, scope in sorted(old.keys() | new.keys()):
        a, b = old.get((kind, target, scope)), new.get((kind, target, scope))
        if kind == 'canvas' and (a != b or before.get('text_positions', {}).get(target) != after.get('text_positions', {}).get(target)):
            canvas_ids.update(v for v in (old_owners.get(target), new_owners.get(target)) if v)
        if _version_content(kind, a) != _version_content(kind, b):
            changes.append(dict(kind=kind, target=target, before=copy.deepcopy(a), after=copy.deepcopy(b),
                                before_hash=_digest(_version_content(kind, a)), after_hash=_digest(_version_content(kind, b)),
                                before_owner=old_owners.get(target), after_owner=new_owners.get(target)))
            if kind == 'output':
                changes[-1]['scope'] = scope
                for projected in (before, after):
                    data = projected.get('multi_output', {})
                    output = data.get('outputs', {}).get(target, {})
                    canvas_ids.update(cid for cid in output.get('text_sources', output.get('canvases', [output.get('canvas')]))
                                      if cid in data.get('canvases', {}))
    journal = state.setdefault('content_history', dict(version=1, entries=[]))
    if journal.get('version') != 1:
        raise ValueError('無法寫入此版本的內容歷史。')
    entries = journal['entries']
    workspace = str(state.get('workspace', ''))
    now = time.time()
    if (coalesce and merge_key and entries and entries[-1].get('merge_key') == merge_key
            and entries[-1]['workspace'] == workspace):
        last = entries[-1]
        prior = {(v['kind'], v['target'], v.get('scope')): v for v in last['changes']}
        for change in changes:
            key = (change['kind'], change['target'], change.get('scope'))
            if key in prior:
                prior[key].update(after=change['after'], after_hash=change['after_hash'], after_owner=change['after_owner'])
            else:
                prior[key] = change
        last.update(changes=[v for v in prior.values() if v['before_hash'] != v['after_hash']],
                    time=now, label=label, canvas_ids=sorted(set(last.get('canvas_ids', [])) | canvas_ids))
        last['content_changed'] = bool(last['changes'])
    else:
        entries.append(dict(id=uuid.uuid4().hex, workspace=workspace, time=now, label=str(label),
                            merge_key=str(merge_key) if merge_key is not None else None,
                            content_changed=bool(changes), changes=changes, canvas_ids=sorted(canvas_ids)))
    del entries[:-LIMIT]
    while entries and len(json.dumps(journal, ensure_ascii=False).encode('utf-8')) > BYTE_LIMIT:
        entries.pop(0)
    return True


def entries(state, *, target=None, workspace=None, content_only=False):
    rows = state.get('content_history', {}).get('entries', [])
    return [v for v in reversed(rows) if (workspace is None or v['workspace'] == workspace)
            and (not content_only or v['content_changed'])
            and (target is None or target in v.get('canvas_ids', []) or any(c['target'] == target for c in v['changes']))]


def record_action(state, label, canvas_ids=()):
    """Record read/copy gestures without inventing content versions or Undo edits."""
    journal = state.setdefault('content_history', dict(version=1, entries=[]))
    if journal.get('version') != 1: raise ValueError('無法寫入此版本的內容歷史。')
    journal['entries'].append(dict(id=uuid.uuid4().hex, workspace=str(state.get('workspace', '')),
        time=time.time(), label=str(label), merge_key=None, content_changed=False, changes=[],
        canvas_ids=sorted(set(canvas_ids))))
    del journal['entries'][:-LIMIT]
    while journal['entries'] and len(json.dumps(journal, ensure_ascii=False).encode('utf-8')) > BYTE_LIMIT:
        journal['entries'].pop(0)


def _restore_content(current, snapshot):
    """Restore known content while retaining current and historical extension data."""
    for key in CONTENT_FIELDS:
        if key not in snapshot:
            current.pop(key, None)
        elif key in ('children', 'overlays'):
            prior = {v['id']: v for v in current.get(key, [])}
            restored = []
            for child in snapshot[key]:
                value = copy.deepcopy(child)
                value.update(copy.deepcopy(prior.get(child['id'], {})))
                _restore_content(value, child)
                restored.append(value)
            current[key] = restored
        elif key == 'composition':
            value = copy.deepcopy(snapshot[key])
            value.update(copy.deepcopy(current.get(key, {})))
            _restore_content(value, snapshot[key])
            current[key] = value
        else:
            current[key] = copy.deepcopy(snapshot[key])


def restore(state, entry_id, target, *, previous=False, scope=None):
    """Restore content through a caller-owned transaction; never rewinds this log."""
    from . import materials
    entry = next((v for v in state.get('content_history', {}).get('entries', []) if v['id'] == entry_id), None)
    if entry is None:
        raise ValueError('此版本已不在保留範圍內。')
    matching = [v for v in entry['changes'] if v['target'] == target and (scope is None or v.get('scope') == scope)]
    if len(matching) > 1: raise ValueError('請選擇要還原的畫布或舊清單草稿。')
    change = matching[0] if matching else None
    value = change.get('before' if previous else 'after') if change else None
    if value is None:
        raise ValueError('此版本沒有可還原的文字內容。')
    if change['kind'] == 'output':
        if entry['workspace'] != state['workspace']: raise ValueError('請切回此紀錄的工作區再還原。')
        data = state.get('multi_output', {})
        output = data.get('outputs', {}).get(target)
        if output is None: raise ValueError('原輸出已移除；請先復原輸出，再還原草稿。')
        scope = change['scope']
        draft = dict(draft=value.get('draft'), draft_base=value.get('draft_base', ''))
        destination = output.setdefault('list_draft', {}) if scope == 'list' else output
        destination.update(copy.deepcopy(draft))
        active_scope = 'list' if state.get('selection_view') == 'list' and state['settings'].get('separate_selections', True) else 'canvas'
        if target == data.get('current_output') and scope == active_scope: state.update(copy.deepcopy(draft))
        return target
    if change['kind'] == 'material':
        current = next((v for v in state['items'] if v['id'] == target), None)
        if current is None:
            result = copy.deepcopy(value)
            result['id'] = uuid.uuid4().hex
            if result.get('module') not in {v['id'] for v in state['modules']}: result['module'] = None
            return materials.save(state, result)
        result = copy.deepcopy(current)
        _restore_content(result, value)
        materials.save(state, result)
    else:
        if entry['workspace'] != state['workspace']:
            raise ValueError('請切回此紀錄的工作區再還原。')
        current = state.get('uses', {}).get(target)
        if current is None:
            from .composition import clone_node
            from .multi_output import assign
            owner = change.get('before_owner' if previous else 'after_owner')
            if owner not in state.get('multi_output', {}).get('canvases', {}):
                raise ValueError('原文字畫布已移除；可先復原畫布，再還原此內容。')
            key = uuid.uuid4().hex
            state.setdefault('uses', {})[key] = clone_node(value)
            state.setdefault('output_order', []).append(key)
            assign(state, key, owner)
            position = state['multi_output']['canvases'][owner]['position']
            state.setdefault('text_positions', {})[key] = [position[0] + 24, position[1] + 108]
            return key
        _restore_content(current, value)
