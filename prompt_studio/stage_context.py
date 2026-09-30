"""Isolated per-item PCS inputs. Native parameters always come from dispatch."""
import copy
from .flow_data import incoming,resolve,materialize,capture_inputs
from .stage_model import input_nodes as upstream


from .flow_data import image_list

def project(state,directory,results=None,source_values=None,readers=None):
    from .image_source import read_content
    from .composition_image import source_path
    state=copy.deepcopy(state);state['_stage_results']=copy.deepcopy(results or {})
    images=state.get('canvas_functions',{}).get('images',{})
    for key,settings in (readers or {}).items():
        if key in images:
            for name in ('output_node','text_field','input_index','stage_reference'):images[key].pop(name,None)
            images[key].update(copy.deepcopy(settings))
    visiting=set();done=set()
    def load(key):
        if key in done:return
        if key in visiting:raise ValueError('圖片來源形成循環。')
        visiting.add(key);item=images[key];link=incoming(state,key,'image') or item.get('stage_reference')
        source=(source_values or {}).get(key)
        if source is None and link:
            if link in images:load(link)
            try:source=image_list(state,key)[0]
            except ValueError as exc:
                item.update(source=None,content_error=str(exc));item.pop('content',None);visiting.remove(key);done.add(key);return
        if source is not None:
            item['source']=copy.deepcopy(source);item.pop('content',None);item.pop('content_error',None)
            matches=[i for i,v in enumerate(item.get('items',[])) if v==source]
            if matches:item['index']=matches[0]
            try:item['content']=read_content(source_path(directory,source,verify=True),item.get('prompt_choice'))
            except (ValueError,OSError) as exc:item['content_error']=str(exc)
        visiting.remove(key);done.add(key)
    for key in images:load(key)
    # Source overrides select the current item, including wired batch sources.
    for key,source in (source_values or {}).items():
        if key in images:
            images[key].pop('stage_reference',None)
            state['multi_output']['connections']=[c for c in state['multi_output']['connections'] if not(c['destination']==key and c['kind']=='image')]
    materialize(state);return state


def sync_result_view(state,directory,results):
    """Refresh derived Canvas content without persisting runtime task results."""
    projected=project(state,directory,results);changed=False
    for key,value in projected.get('canvas_functions',{}).get('images',{}).items():
        current=state['canvas_functions']['images'][key]
        if incoming(state,key,'image') or current.get('stage_reference'):
            for field in ('source','content','content_error'):
                if current.get(field)!=value.get(field):changed=True
                if field in value:current[field]=copy.deepcopy(value[field])
                else:current.pop(field,None)
    # project() materialized both PNG metadata and selected Stage text. These
    # are the only edit-document fields it derives; user-owned roots survive.
    for field in ('uses','text_positions','output_order'):
        if state.get(field)!=projected.get(field):
            changed=True
            if field in projected:state[field]=copy.deepcopy(projected[field])
            else:state.pop(field,None)
    if state['multi_output']['canvases']!=projected['multi_output']['canvases']:
        changed=True;state['multi_output']['canvases']=copy.deepcopy(projected['multi_output']['canvases'])
    return changed


def capture(state,plan,keys,directory,policies=None):
    """A deferred upstream reference is never replaced with the last preview."""
    saved=dict(values={},inputs={},batches={},source_positions={},readers={})
    for key in keys:
        stage=plan['stages'][key]
        for binding in stage['bindings']:
            source=binding['source'];policy=(policies or {}).get(binding['key'],'saved')
            if policy=='live' or binding.get('text_source')=='web' or not source:continue
            nodes=upstream(state,[source]);future=nodes&plan['stages'].keys()
            for node in nodes:
                ref=state.get('canvas_functions',{}).get('images',{}).get(node,{}).get('stage_reference')
                if ref:future.add(ref)
            if future:
                for node in nodes:
                    image=state.get('canvas_functions',{}).get('images',{}).get(node)
                    if image is not None:saved['readers'][node]={k:copy.deepcopy(image[k]) for k in ('output_node','text_field','input_index','stage_reference') if k in image}
                continue
            from .composition_image import freeze_image_source
            if binding['kind']=='image' and source in state['multi_output']['canvases']:
                from .flow_data import value
                item=value('image',freeze_image_source(state,directory,source))
            else:item=resolve(state,source,binding['kind'])
            if binding['kind']=='clip':
                from .flow_data import canvas_ids
                output=state['multi_output']['outputs'].get(item.get('origin',{}).get('output',source))
                if output:
                    item.setdefault('origin',{})['canvas_records']={cid:dict(canvas=copy.deepcopy(state['multi_output']['canvases'][cid]),
                        roots={rid:copy.deepcopy(state['uses'][rid]) for rid in state['multi_output']['canvases'][cid]['members']}) for cid in canvas_ids(output)}
            saved['values'][binding['key']]=item
        scheduler=stage['scheduler']
        if scheduler:
            # Only connected input channels are frozen; untouched inputs stay live.
            try:saved['inputs'].update(capture_inputs(state,scheduler))
            except ValueError:
                # Deferred Stage results are read only once this parent iteration reaches them.
                if not any((upstream(state,[b['source']])&plan['stages'].keys()) for b in stage['bindings'] if b['source']):raise
        if stage['auto']:
            source=stage['auto'];image=state['canvas_functions']['images'][source]
            if not incoming(state,source,'image') and not image.get('stage_reference'):
                saved['batches'][source]=copy.deepcopy(image.get('items') or ([image['source']] if image.get('source') else []))
            # Per-image text must be evaluated from each image, not the first one.
            for binding in stage['bindings']:
                if source in upstream(state,[binding['source']]) if binding['source'] else False:saved['values'].pop(binding['key'],None)
            for ch in state['multi_output']['schedulers'][scheduler]['channels']:
                endpoint=scheduler+'::'+ch['id'];origin=incoming(state,endpoint,ch['type'])
                if origin and source in upstream(state,[origin]):saved['inputs'].pop(endpoint,None)
        for binding in stage['bindings']:
            for source in upstream(state,[binding['source']]) if binding['source'] else []:
                image=state.get('canvas_functions',{}).get('images',{}).get(source,{})
                if image.get('iterate'):saved['source_positions'][source]=dict(batch=image.get('batch_id'),index=image.get('index',0))
    return saved


def merge_saved(outer,inner):
    result=copy.deepcopy(outer)
    for kind in ('values','inputs','batches','source_positions','readers'):
        result.setdefault(kind,{}).update(copy.deepcopy(inner.get(kind,{})))
    return result


def prepare_state(runner,run,stage,context,source_values=None):
    from .multi_output import new_output
    from .composition_image import freeze_image_source
    live=runner.window.state;state=copy.deepcopy(live)
    # Route comes from the queued definition; native graphs stay live.
    saved=context.get('saved',{});state['_execution_inputs']=copy.deepcopy(saved.get('inputs',{}))
    state=project(state,runner.window.store.directory,context.get('results'),source_values,saved.get('readers'))
    # PNG metadata follows the saved text, including its original module structure.
    for value in [*saved.get('values',{}).values(),*saved.get('inputs',{}).values()]:
        for cid,record in value.get('origin',{}).get('canvas_records',{}).items():
            if cid in state['multi_output']['canvases']:
                state['multi_output']['canvases'][cid]=copy.deepcopy(record['canvas']);state['uses'].update(copy.deepcopy(record['roots']))
    data=state['multi_output'];texts=[];images=[];foreign={};text_views={}
    for binding in stage['bindings']:
        source=binding['source'];kind=binding['kind'];target=binding['target'];value=saved.get('values',{}).get(binding['key'])
        if value is None and binding.get('text_source')!='web':
            if not source:raise ValueError('輸入 '+binding['key']+' 尚未接入內容。')
            value=(dict(type='image',value=freeze_image_source(state,runner.window.store.directory,source)) if kind=='image' and source in data['canvases'] else resolve(state,source,kind))
        if target['workflow']!=stage['workflow']:
            foreign.setdefault(target['workflow'],[]).append((binding,value));continue
        if kind=='clip':
            texts.append((binding,'' if value is None else value['value']))
            origin=(value or {}).get('origin',{});text_views[binding['key']]=dict(canvases=origin.get('canvases',[]),automatic=origin.get('manual_draft') is False)
        else:images.append((target['node'],value['value']))
    # Snapshot retains relevant Canvas content, but never unresolved future data.
    used=set()
    for b in stage['bindings']:
        if b['source']:used|=upstream(state,[b['source']])
    keep_canvases=set(data['canvases'])&used
    data['canvases']={k:v for k,v in data['canvases'].items() if k in keep_canvases}
    roots={k for c in data['canvases'].values() for k in c['members']}
    state['uses']={k:v for k,v in state.get('uses',{}).items() if k in roots}
    state['output_order']=[k for k in state.get('output_order',[]) if k in roots]
    data.update(outputs={},clip_inputs={},bindings=[],connections=[],image_inputs={},schedulers={},stages={stage['id']:copy.deepcopy(live['multi_output']['stages'].get(stage['id'],stage))},current_output=None)
    data['workflow_order']=dict(visible=False,items=[],established=[])
    for i,(binding,text) in enumerate(texts):
        out='stage_text_'+str(i);clip=binding['key']
        view=text_views[clip];canvases=[cid for cid in view['canvases'] if cid in keep_canvases]
        data['outputs'][out]=dict(new_output('提交文字'),canvases=canvases,text_sources=canvases,canvas=next(iter(canvases),None),draft=text)
        for cid in canvases:data['connections'].append(dict(id=out+cid,source=cid,destination=out,kind='text'))
        if canvases and view['automatic']:
            from .multi_output import compile_output
            if compile_output(state,out)['generated_prompt']==text:data['outputs'][out]['draft']=None
        data['clip_inputs'][clip]=dict(name='CLIP 輸入',workflow=stage['workflow'],text_source=binding.get('text_source','pcs'))
        data['connections'].append(dict(id=out+clip,source=out,destination=clip,kind='clip'))
        data['bindings'].append(dict(workflow=stage['workflow'],clip=clip,node=binding['target']['node'],field=binding['target']['field']))
        data['current_output']=out
    for i,(node,image) in enumerate(images):data['image_inputs']['stage_image_'+str(i)]=dict(name='圖片輸入',workflow=stage['workflow'],node=node)
    state['canvas_functions']=dict(images={},preview=True,preview_attached=False)
    state['draft']=data['outputs'][data['current_output']]['draft'] if texts else None;state['draft_base']='';state['selection_view']='canvas'
    state.pop('workspace_scenes',None);state.pop('_execution_inputs',None);state.pop('_stage_results',None)
    state['generation']['profiles']=[p for p in state['generation']['profiles'] if p['id']==stage['workflow']]
    return state,images,foreign
