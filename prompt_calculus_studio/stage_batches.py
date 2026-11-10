"""Durable click snapshots and bounded, main-thread queue preparation.

Content lives in batch_snapshot rows; small batch_control receipts outlive it.
No borrowed document object escapes the synchronous acceptance transaction.
"""
import copy
import json
import time

from . import prompt_sources as supply, prompt_materials as materials
from .stage_context import borrowed_base, execution_state, execution_scene, project

SLICE_SECONDS = .015
SLICE_ITEMS = 1


def manual_sources(runner, plan, keys, state):
    origins = [b['source'] for k in keys for b in plan['stages'][k]['bindings'] if b['source']]
    ids = [k for k in supply.connected(state, origins) if supply.source(state, k)]
    for k in keys:
        if not plan['stages'][k]['auto']:
            ids.extend(s for s in plan['stages'][k].get('suppliers', []) if s not in ids)
    auto = {s for k in keys if plan['stages'][k]['auto'] for s in plan['stages'][k].get('suppliers', [])}
    if auto:
        for k in keys:
            if not plan['stages'][k]['scheduler']:
                auto.update(supply.connected(state, [b['source'] for b in plan['stages'][k]['bindings'] if b['source']]))
    return [k for k in ids if k not in auto]


def cursors(state, ids):
    """Only cursor fields, never editable source definitions or whole documents."""
    from .composition import walk
    result = {}
    for key in ids:
        definition = supply.source(state, key)
        if definition:
            root = definition['root']
            fields = {}
            for part in walk(root):
                value = {f: copy.deepcopy(part.get(f)) for f in materials.RUNTIME_FIELDS}
                seq = materials.sequence(part)
                if seq: value['selected'] = seq['selected']
                fields[part['id']] = value
            result[key] = dict(kind='text', root=root['id'], signature=materials.signature(root), fields=fields)
        else:
            image = state.get('canvas_functions', {}).get('images', {}).get(key)
            if image:
                result[key] = dict(kind='image', batch=image.get('batch_id'),
                    fields={f: copy.deepcopy(image.get(f)) for f in ('index', 'source', 'supply_reservation', 'supply_position')})
    return result


def restore_cursors(state, snapshot, directory, *, register):
    for key, value in snapshot.items():
        if value['kind'] == 'text':
            definition = supply.source(state, key)
            if not definition or definition['root']['id'] != value['root'] or materials.signature(definition['root']) != value['signature']:
                raise ValueError('來源已修改，位置無法退回。')
            register(*supply.source_path(key))
            materials.apply_progress(definition['root'], value['fields'])
        else:
            image = state.get('canvas_functions', {}).get('images', {}).get(key)
            if not image or image.get('batch_id') != value['batch']:
                raise ValueError('圖片來源已修改，位置無法退回。')
            register('canvas_functions', 'images', key)
            for field, saved in value['fields'].items():
                if saved is None: image.pop(field, None)
                else: image[field] = copy.deepcopy(saved)
            image.pop('content', None)
            image.pop('content_error', None)
            if image.get('source'):
                from .composition_image import source_path
                from .image_source import read_content
                try:image['content']=read_content(source_path(directory,image['source'],verify=True),image.get('prompt_choice'))
                except (ValueError,OSError) as exc:image['content_error']=str(exc)


def release_images(state, control, *, register):
    released=False
    for key,saved in control.get('cursor_after',{}).items():
        if saved['kind']!='image':continue
        image=state.get('canvas_functions',{}).get('images',{}).get(key,{})
        expected=saved['fields'].get('supply_reservation')
        if expected and image.get('supply_reservation')==expected:
            register('canvas_functions', 'images', key)
            image.pop('supply_reservation');released=True
    return released


def freeze_plan(state, ids, count, *, shared=None):
    """Freeze selections without constructing N projected documents.

Each manual iteration uses the same sampler/order as manual_rounds. Definitions
are retained once; compact selection/progress maps are appended in that order.
"""
    if not ids: return None, count
    working = execution_state(state, ids)
    frozen = None
    for _ in range(count):
        current = supply.build(working, ids,
            count=None if supply.automatic(working, ids) else 1, shared=shared)
        supply.reserve(working, current)
        if frozen is None:
            frozen = current
            frozen['committed'] = 0
        else:
            frozen['rounds'].extend(current['rounds'])
    return frozen, len(frozen['rounds'])


def accept(runner, run, node, owner, count, group, *, state=None, shared=None, accumulator=None, manual=None, plan=None):
    """Called inside accepting(); persist the snapshot before returning to Qt."""
    state = state if state is not None else runner.context()
    # Policies are part of this click's intention even when routing reuses a run.
    if plan is not None:run=dict(run,plan=plan)
    keys = runner.scope_keys(run['plan'], node)
    ids = (run['plan']['stages'][node['stage']].get('suppliers') or [node['auto']]) if node.get('auto') else manual_sources(runner, run['plan'], keys, state)
    before = cursors(state, ids)
    # Upstream results are references. Their collection is resolved only when
    # this scope is entered, from this frozen document plus the parent's results.
    from .stage_model import input_nodes
    future = bool(node.get('auto') and any(input_nodes(state, [b['source']]) & run['plan']['stages'].keys()
        for k in keys for b in run['plan']['stages'][k]['bindings'] if b['source']))
    if manual is not None:
        ids, frozen, total, before = manual
        frozen=copy.deepcopy(frozen)
    else:
        frozen, total = (None, count) if future else freeze_plan(state, ids, count, shared=shared)
    control = runner.store.batch_control(run['workspace'], node['key'], group)
    if control:
        serial = control['serial']
    else:
        serial = runner.store.next_batch_serial(run['workspace'], node['key'])
    # Exclude duplicate scene caches and edit history; projection receives all
    # other frozen non-scene inputs, including profiles/library references.
    from .workspace_scene import FIELDS
    from .stage_parameters import capture as capture_parameters
    base = {k: v for k, v in state.items() if k not in FIELDS and k not in ('workspace_scenes', 'content_history', '_stage_results')}
    payload=dict(
        run=run['id'], scheduler=node['key'], execution_group=group, node=node, plan=run['plan'],
        scene=execution_scene(state), base=base, supply=frozen, total=total, expanded=0,
        deferred=future, count=count, suppliers=ids, parameters=capture_parameters(state,run['plan']['order']))
    if accumulator is not None:
        prior=accumulator.get(node['key'])
        if prior is None:
            # One owned content snapshot per scheduler/click, even when the
            # surrounding plan has several automatic execution cycles.
            payload=json.loads(json.dumps(payload,ensure_ascii=False))
            payload['segments']=[dict(owner=owner,start=0,total=total,deferred=future)]
            accumulator[node['key']]=dict(payload=payload,owner=owner,before=before,reservations=[])
        else:
            payload=prior['payload']
            payload['segments'].append(dict(owner=owner,start=payload['total'],total=total,deferred=future))
            payload['total']+=total
            if frozen:payload['supply']['rounds'].extend(frozen['rounds'])
        if frozen:
            supply.reserve(state,frozen)
            frozen['committed']=0
            accumulator[node['key']]['reservations'].append(frozen)
        return
    ident=runner.store.add_captured('batch_snapshot',run['workspace'],owner,_return_record=False,**payload)
    runner.store.set_batch_control(run['workspace'], node['key'], group, serial=serial,
        total=control.get('total', 0)+total, expanded=control.get('expanded', 0),
        cursor_before=control.get('cursor_before', before), cursor_keys=list(set(control.get('cursor_keys', [])) | set(ids)),
        run=run['id'], entry_owner=owner, label=' → '.join(run['plan']['stages'][k]['name'] for k in keys))
    if frozen:
        runner.reserve_supply(frozen)
    runner.store.set_batch_control(run['workspace'], node['key'], group, cursor_after=cursors(runner.window.state, ids))
    return ident


def commit_batches(runner,run,accumulator):
    for item in accumulator.values():
        batch=item['payload'];scheduler=batch['scheduler'];group=batch['execution_group']
        runner.store.add_captured('batch_snapshot',run['workspace'],item['owner'],_return_record=False,**batch)
        runner.store.set_batch_control(run['workspace'],scheduler,group,
            serial=runner.store.next_batch_serial(run['workspace'],scheduler),total=batch['total'],expanded=0,
            cursor_before=item['before'],cursor_after=cursors(runner.window.state,batch['suppliers']),cursor_keys=batch['suppliers'],
            run=run['id'],entry_owner=item['owner'],label=' → '.join(run['plan']['stages'][k]['name'] for k in runner.scope_keys(run['plan'],batch['node'])))


def notice(runner, scheduler, group):
    groups = runner.store.schedule_groups(runner.window.state['workspace'], scheduler)
    current = next(g for g in groups if g['id'] == group)
    waiting = [g for g in groups if g['pending'] and not g['cancelled']]
    runner.notify(f"已排入第 {current['serial']} 組（{current['total']} 項）；等待中 {len(waiting)} 組，共 {sum(g['pending'] for g in waiting)} 項")


def resolve_deferred(runner, run, frame):
    for head in runner.store.batch_heads(run['workspace'], owner=frame['owner']):
        if not head['deferred']: continue
        batch = runner.store.read(head['id'])
        base = borrowed_base(batch['base'], batch['scene'])
        state = project(base, runner.window.store.directory, frame['ctx']['results'], frame['ctx'].get('sources'), execution=True)
        frozen, total = freeze_plan(state, batch['suppliers'], batch['count'])
        with runner.accepting():
            segments=batch.get('segments',[])
            segment=next((s for s in segments if s['owner']==frame['owner']),None)
            if segment is not None:
                difference=total-segment['total']
                for following in segments:
                    if following['start']>segment['start']:following['start']+=difference
                segment.update(total=total,deferred=False,supply=frozen,results=frame['ctx']['results'])
                total=batch['total']+difference
                runner.store.patch(batch['id'],total=total,deferred=False,segments=segments)
            else:
                runner.store.patch(batch['id'], supply=frozen, total=total, deferred=False,results=frame['ctx']['results'])
            control = runner.store.batch_control(run['workspace'], batch['scheduler'], batch['execution_group'])
            runner.store.set_batch_control(run['workspace'], batch['scheduler'], batch['execution_group'], total=control['total']-batch['total']+total)
            if frozen: runner.reserve_supply(frozen)
            runner.store.set_batch_control(run['workspace'],batch['scheduler'],batch['execution_group'],cursor_after=cursors(runner.window.state,batch['suppliers']))
        runner._batch_content=None


def expand(runner, capacity):
    """One transaction/short slice, then give Qt a turn. Return rows prepared."""
    store = runner.store
    workspace = runner.window.state['workspace']
    heads = store.batch_heads(workspace)
    if not heads:
        runner._batch_content=None
        return 0
    started = time.perf_counter()
    prepared = 0
    with runner.accepting():
        for scheduler in dict.fromkeys(h['scheduler'] for h in heads):
            remaining = capacity
            for group in store.schedule_groups(workspace, scheduler):
                if group['cancelled'] or group['bypassed']: continue
                ready = sum(group['counts'].get(s, 0) for s in ('waiting', 'running', 'retained'))
                wanted = max(0, min(group['unexpanded'], remaining-ready))
                remaining -= ready + wanted
                for head in heads:
                    if not wanted: break
                    if head['execution_group'] != group['id'] or head['scheduler'] != scheduler or head['deferred']: continue
                    cached=getattr(runner,'_batch_content',None)
                    if cached is None or cached[0]!=head['id']:
                        # This is an owned journal decode, never a borrowed
                        # live document. Retain at most one scene between slices
                        # to avoid repeatedly allocating thousands of containers.
                        cached=(head['id'],store.read(head['id']))
                        runner._batch_content=cached
                    batch=cached[1]
                    # Progress always comes from the transaction's durable head;
                    # a failed slice cannot advance the in-memory retry cursor.
                    batch['expanded']=head['expanded']
                    run = store.read(batch['run'])
                    if not run or run['status'] not in ('running','waiting'): continue
                    # The journal decode already owns this scene. Borrow its
                    # immutable branches, then copy only projection's writable
                    # containers; cloning every unrelated root per item creates
                    # long GC pauses on large documents.
                    from .workspace_scene import load
                    frozen_base=dict(batch['base'])
                    load(frozen_base,batch['scene'],readonly=True)
                    for _ in range(min(wanted, batch['total']-batch['expanded'])):
                        segment=next((s for s in batch.get('segments',[]) if s['start']<=batch['expanded']<s['start']+s['total']),{})
                        if segment.get('deferred'):break
                        base = execution_state(borrowed_base(frozen_base))
                        frozen = segment.get('supply',batch['supply'])
                        at=batch['expanded']-segment.get('start',0) if 'supply' in segment else batch['expanded']
                        images, positions = supply.project(base, frozen, frozen['rounds'][at]) if frozen else ({}, {})
                        state = project(base, runner.window.store.directory, segment.get('results',batch.get('results')), source_values=images, execution=True)
                        saved = runner.capture_scope(batch['plan'], batch['node'], state, per_round=bool(batch['node'].get('auto')))
                        if batch['node'].get('auto'):
                            from .flow_data import capture_inputs
                            saved['parameters']=copy.deepcopy(batch['parameters'])
                            saved['parameter_overrides']={};saved['parameter_profiles']={}
                            saved['batches'] = {}
                            saved['inputs'] = capture_inputs(state, scheduler)
                            saved['source_positions'].update(positions)
                        owner=segment.get('owner',batch['owner'])
                        entry = runner.add_entry(run, owner, batch['node'], saved, execution_group=batch['execution_group'], _return_record=bool(frozen and batch['node'].get('auto')))
                        if frozen and batch['node'].get('auto'):
                            store.patch(entry['id'], supply_images=images, supply_plan=frozen['id'], supply_round=batch['expanded'])
                        batch['expanded'] += 1
                        prepared += 1
                        wanted -= 1
                        if prepared>=SLICE_ITEMS or time.perf_counter()-started >= SLICE_SECONDS: break
                    delta = batch['expanded']-head['expanded']
                    control = store.batch_control(workspace, scheduler, group['id'])
                    store.set_batch_control(workspace, scheduler, group['id'], expanded=control['expanded']+delta)
                    if batch['expanded'] == batch['total']:
                        store.delete_batch_snapshot(batch['id'])
                        runner._batch_content=None
                    else:
                        deferred=any(s.get('deferred') and s['start']==batch['expanded'] for s in batch.get('segments',[]))
                        store.patch(batch['id'], expanded=batch['expanded'],deferred=deferred)
                    if prepared>=SLICE_ITEMS or time.perf_counter()-started >= SLICE_SECONDS: break
                if remaining <= 0 or prepared>=SLICE_ITEMS or time.perf_counter()-started >= SLICE_SECONDS: break
            if prepared>=SLICE_ITEMS or time.perf_counter()-started >= SLICE_SECONDS: break
    if prepared:
        runner.notify()
        runner.later()
    return prepared
