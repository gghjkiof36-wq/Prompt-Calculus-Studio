"""Current input-only wiring and explicit, atomic conversion of old data wires.

Stored channels and journals remain readable. This module is used by editing
actions, never by refresh, autosave, snapshot decoding, or queued dispatch.
"""
import copy


def connect(state, source, destination, kind):
    from .module_contracts import connection_error
    from .multi_output import connect as compatible_connect
    error=connection_error(state,source,destination,kind)
    if error:raise ValueError(error)
    compatible_connect(state,source,destination,kind)


def legacy_edges(state, key):
    from .flow_data import channel
    return [c for c in state['multi_output']['connections'] if any(
        (route:=channel(state,endpoint)) and route[0]==key for endpoint in (c['source'],c['destination']))]


def convert(state, key):
    """Move an unambiguous legacy scheduler after its bound input terminals."""
    from . import multi_output as model, stage_model
    from .flow_data import incoming, channel
    value=copy.deepcopy(state);data=value['multi_output'];edges=legacy_edges(value,key)
    if not edges:return False
    if data['schedulers'][key].get('mode','data')!='data':
        raise ValueError('這份 Stage 預排程仍有舊資料連線，請先解除舊資料連線。')
    endpoint=key+'::bindings'
    terminals=stage_model.terminals(value)
    bound=set(stage_model.binding_routes(value,endpoint))
    bound.update(t for t in terminals if key in stage_model.sources(value,t))
    if not bound:raise ValueError('請先將 CLIP 輸入或圖片輸入連到預排程，再整理舊接線。')
    extras=[]
    for ch in data['schedulers'][key]['channels']:
        port=key+'::'+ch['id'];origin=incoming(value,port,ch['type'])
        outputs=[c for c in edges if c['source']==port]
        if outputs and not origin:raise ValueError('舊資料接點沒有來源，請先解除這條未完成的連線。')
        if origin and not outputs:extras.append((origin,ch['type']))
        for edge in [c for c in edges if port in (c['source'],c['destination'])]:model.disconnect(value,edge['id'])
        for edge in outputs:
            if channel(value,edge['destination']) or channel(value,origin):
                raise ValueError('多層舊資料接線請先改由 CLIP 輸入或圖片輸入串接預排程。')
            model.connect(value,origin,edge['destination'],edge['kind'])
    for origin,kind in extras:
        candidates={incoming(value,t,'clip') for t in bound if t in data['clip_inputs']}
        candidates={c for c in candidates if c in data['outputs']}
        if kind not in ('clip','content') or len(candidates)!=1:
            raise ValueError('這條舊資料連線沒有唯一的 Prompt 輸出目的地，請先手動連接再整理。')
        output=next(iter(candidates))
        # A reader also wired through a Canvas already participates there.
        if origin not in stage_model.sources(value,output):model.connect(value,origin,output,'clip')
    for terminal in sorted(bound):
        targets=[c for c in data['connections'] if c['source']==terminal and c['kind']=='control' and c['destination']!=endpoint]
        for edge in targets:
            model.disconnect(value,edge['id']);model.connect(value,endpoint,edge['destination'],'control')
        if not any(c['source']==terminal and c['destination']==endpoint and c['kind']=='control' for c in data['connections']):
            model.connect(value,terminal,endpoint,'control')
    from .core import validate_state
    validate_state(value)
    state.clear();state.update(value)
    return True
