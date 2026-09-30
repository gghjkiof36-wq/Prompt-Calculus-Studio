"""Selection belongs to result readers, never the Stage execution definition."""
from .flow_data import incoming


def stage_source(state,key,kind='image',seen=None):
    seen=set(seen or ())
    if key in seen:return None
    seen.add(key)
    if key in state['multi_output'].get('stages',{}):return key
    item=state.get('canvas_functions',{}).get('images',{}).get(key,{})
    link=incoming(state,key,kind) or item.get('stage_reference')
    return stage_source(state,link,kind,seen) if link else None


def text_value(state,stage,field=None):
    result=state.get('_stage_results',{}).get(stage)
    if not result:raise ValueError('本次文字結果尚未產生。')
    texts=result.get('texts',[])
    if field:
        item=next((v for v in texts if [v['node'],v['field']]==field),None)
        if item is None:raise ValueError('本次結果沒有指定文字欄位：#'+field[0]+' / '+field[1])
        return item['text']
    if isinstance(result.get('text'),str):return result['text']  # Historical selected result.
    if len(texts)==1:return texts[0]['text']
    raise ValueError('請在讀取文字模塊選擇文字節點。' if texts else '本次結果沒有文字欄位。')


def add_text_reader(state,position):
    from .multi_output import ident
    key=ident('__source_')
    state['canvas_functions']['images'][key]=dict(reader='text',name='讀取文字',source=None,attached=False,show_image=False)
    state.setdefault('text_positions',{})[key]=list(position)
    return key
