"""v7 finite list edits; the dialog's candidate never aliases the document."""
import copy
import uuid
from .image_source import set_items, select


def items(value):
    return value.get('items') or ([value['source']] if value.get('source') else [])


def append_items(state,key,added,directory):
    value=state['canvas_functions']['images'][key]
    previous=items(value)
    if not added:return
    if not previous:
        set_items(state,key,added,directory,len(added)>1)
        return
    # Keep the current source, prompt selection and cursor exactly as they were.
    # No need to decode/hash that already selected file a second time.
    value.update(items=copy.deepcopy([*previous,*added]),iterate=True,batch_id=uuid.uuid4().hex)
    value.pop('supply_reservation',None);value.pop('supply_position',None)


def replace_items(state,key,ordered,index,directory):
    value=state['canvas_functions']['images'][key]
    if not ordered:
        # A cleared card has the same source state as a newly created card;
        # retain appearance preferences and old read-only compatibility fields.
        for field in ('items','index','iterate','batch_id','supply','supply_reservation','supply_position',
                      'content','content_error','prompt_choice','stage_reference','input_index','binding','selection'):
            value.pop(field,None)
        value['source']=None
        from .flow_data import materialize
        materialize(state)
        return
    if items(value)!=ordered:
        value.update(items=copy.deepcopy(ordered),iterate=len(ordered)>1,batch_id=uuid.uuid4().hex)
    select(state,key,index,directory)
