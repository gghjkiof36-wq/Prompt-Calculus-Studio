"""Independent image-view instances; the legacy display keeps its identity."""
from .core import uid

LEGACY='__result_preview__'


def contains(state,key):
    return key==LEGACY or key in state.get('canvas_functions',{}).get('previews',{})


def keys(state):
    return [LEGACY,*state.get('canvas_functions',{}).get('previews',{})]


def add(state,position=(600,450)):
    key='preview_'+uid()
    state.setdefault('canvas_functions',dict(images={},preview=True,preview_attached=False)).setdefault('previews',{})[key]=dict(collapsed=False)
    state.setdefault('text_positions',{})[key]=list(position)
    from .state_schema import mark_version
    mark_version(state)
    return key


def display(state,key,writing=False):
    data=state.get('canvas_functions',{})
    if key==LEGACY:
        return data.setdefault('preview_display',{}) if writing else data.get('preview_display',{})
    return data.get('previews',{}).get(key,{})


def validate(data):
    items=data.get('previews',{})
    if not isinstance(items,dict) or len(items)>100:raise ValueError('圖片預覽數量無效。')
    for key,item in items.items():
        if not isinstance(key,str) or not key or key==LEGACY or '::' in key or not isinstance(item,dict):
            raise ValueError('圖片預覽識別無效。')
        if type(item.get('collapsed',False)) is not bool:raise ValueError('圖片預覽收合狀態無效。')
