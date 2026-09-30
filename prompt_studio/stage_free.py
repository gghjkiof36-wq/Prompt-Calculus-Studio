"""Apply unconnected terminals after their own run produces the required data.

These are input updates, never an alternative generation entry point. A delayed
result must not write into a newly wired terminal or borrow another run's preview.
"""
import copy
from .flow_data import incoming,resolve
from .stage_model import terminals,controls,input_nodes


def bindings(state):
    data=state['multi_output'];used={k for stage in data.get('stages',{}) for k in controls(state,stage)}
    for key,target in terminals(state).items():
        if key in used or not target.get('workflow'):continue
        kind='clip' if key in data['clip_inputs'] else 'image';source=incoming(state,key,kind)
        if not source:continue
        from .clip_flow import selected_binding
        selected=selected_binding(state,key) if kind=='clip' else target
        if selected:yield dict(key=key,kind=kind,source=source,
            target={field:copy.deepcopy(selected[field]) for field in ('workflow','node','field') if field in selected},text_source='pcs')


def dependencies(state,binding):
    return input_nodes(state,[binding['source']]) & state['multi_output'].get('stages',{}).keys()


def route(state,binding):
    nodes=input_nodes(state,[binding['source']])|{binding['key']}
    images=state.get('canvas_functions',{}).get('images',{})
    return dict(binding=binding,connections=sorted([c['source'],c['destination'],c['kind']] for c in state['multi_output']['connections']
        if c['destination'] in nodes and c['kind'] not in ('control','done','flow')),
        readers={k:{f:images[k][f] for f in ('stage_reference','output_node','text_field','input_index') if f in images[k]} for k in nodes & images.keys()})


def capture(runner,run):
    state=runner.window.state;items=[]
    for binding in bindings(state):
        refs=dependencies(state,binding)
        if refs and refs<=run['plan']['stages'].keys():items.append(dict(binding=binding,route=route(state,binding),requires=sorted(refs)))
    if items:
        from .workspace_scene import scene
        run.update(free_inputs=items,free_scene=scene(state),free_state='waiting')
        runner.store.update(run['id'],free_inputs=items,free_scene=run['free_scene'],free_state='waiting')


def matches(state,items):
    existing={b['key']:route(state,b) for b in bindings(state)}
    return all(existing.get(i['binding']['key'])==i['route'] for i in items)


def route_changed(runner,run):
    if run.get('free_state') not in ('waiting','applying') or matches(runner.window.state,run.get('free_inputs',[])):return
    # A sent apply can still be waiting for the browser. Stop its exact receipt
    # immediately rather than discovering the changed wire after it was applied.
    if run['free_state']=='applying':runner.cancel_inputs(run)
    runner.store.update(run['id'],free_state='retained',free_error='來源或接線已變更，延後輸入已保留，未繼續套用。')


def finish(runner,run):
    current=runner.store.read(run['id']);items=current.get('free_inputs',[])
    if current.get('free_error'):run['free_error']=current['free_error']
    if not items or current.get('free_state') in ('applied','failed','retained'):return True
    if current.get('free_state')=='applying':
        if (runner.applying is not None and runner.apply_owner==run['id']) or any(item[6]==run['id'] for item in runner.apply_queue):return False
        # Restoring a journal cannot prove whether the prior apply reached the
        # browser. Preserve its operation IDs and do not issue a replacement.
        run['free_error']='前次輸入同步回覆中斷，未自動重送。'
        runner.store.update(run['id'],free_state='failed',free_error=run['free_error']);return True
    live=runner.window.state
    if current['status']!='running' or current['workspace']!=live['workspace'] or current['server']!=runner.client.url:
        runner.store.update(run['id'],free_state='retained');return True
    if not matches(live,items):
        runner.store.update(run['id'],free_state='retained');return True
    from .stage_runner import merge_results
    from .workspace_scene import load
    from .stage_context import project
    results={}
    for attempt in runner.store.rows('attempt',owner=run['id']):
        if attempt['status']=='complete' and attempt['round']==run['round'] and attempt.get('result'):
            merge_results(results,{attempt['stage']:attempt['result']})
    groups={}
    try:
        state=copy.deepcopy(live);load(state,current['free_scene'])
        state=project(state,runner.window.store.directory,results)
        for item in items:
            if not set(item['requires'])<=results.keys():raise ValueError('本輪來源 Stage 尚未完成。')
            binding=item['binding'];value=resolve(state,binding['source'],binding['kind'])
            groups.setdefault(binding['target']['workflow'],[]).append((binding,value))
    except (ValueError,OSError) as exc:
        run['free_error']=str(exc);runner.store.update(run['id'],free_state='failed',free_error=str(exc));return True
    runner.store.update(run['id'],free_state='applying')
    def completed(error=None):
        if runner.closed:return
        runner.store.update(run['id'],free_state='failed' if error else 'applied',free_error=str(error) if error else '')
        runner.later()
    def valid():
        owner=runner.store.read(run['id'])
        return (not runner.closed and owner['status']=='running' and owner['workspace']==runner.window.state['workspace']
            and owner['server']==runner.client.url and matches(runner.window.state,items))
    runner.apply_bindings(groups,completed,completed,valid=valid,owner=run['id'])
    return False
