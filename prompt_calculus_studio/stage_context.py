"""Isolated per-item intentions; unspecified native fields stay live at dispatch."""
import copy
from contextlib import contextmanager, nullcontext
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from .image_capture import CapturedImage
from .pnginfo import png_metadata_stream
from .flow_data import incoming,resolve,materialize,capture_inputs
from .stage_model import input_nodes as upstream


from .flow_data import image_list

MAX_SOURCE_BYTES = 25 * 1024 * 1024


_ABSENT = object()


def _restore(target, saved):
    """Preserve containers still at the same position, including descendants."""
    if isinstance(target, dict) and isinstance(saved, dict):
        values = {k: _restore(target[k], v) if k in target else v for k, v in saved.items()}
        target.clear()
        target.update(values)
        return target
    if isinstance(target, list) and isinstance(saved, list):
        target[:] = [_restore(target[i], v) if i < len(target) else v for i, v in enumerate(saved)]
        return target
    return saved


class ExecutionTransaction:
    """Write-ahead recovery for live JSON branches; never an Undo or read view.

    Writers register a path before its first mutation. Paths are tuples of keys
    (list indices are allowed); a missing ancestor is recorded at that ancestor.
    Nested scopes share the first snapshots. Rollback restores in reverse order;
    successful outer commit publishes registered paths as a frozenset, changes.
    """
    def __init__(self, state):
        self.state = state
        self.records = {}
        self.changes = frozenset()
        self.committed = False

    def register(self, *path):
        if not path: raise ValueError('Register a branch, not the whole document.')
        if self.committed: raise RuntimeError('Execution transaction has ended.')
        parent = self.state
        for index, key in enumerate(path):
            missing = key not in parent if isinstance(parent, dict) else key >= len(parent)
            if missing:
                path = path[:index+1]
                break
            if index < len(path)-1: parent = parent[key]
        path = tuple(path)
        if any(path[:len(prior)] == prior for prior in self.records): return
        value = parent[path[-1]] if not missing else _ABSENT
        self.records[path] = _ABSENT if value is _ABSENT else copy.deepcopy(value)

    def rollback(self):
        for path, saved in reversed(self.records.items()):
            parent = self.state
            for key in path[:-1]: parent = parent[key]
            key = path[-1]
            if saved is _ABSENT:
                if isinstance(parent, dict): parent.pop(key, None)
                elif key < len(parent): del parent[key:]
            elif isinstance(parent, dict):
                parent[key] = _restore(parent.get(key), saved)
            else:
                parent[key] = _restore(parent[key], saved)

    def commit(self):
        self.committed = True
        self.changes = frozenset(self.records)


@contextmanager
def execution_scope(window, journal):
    """Join the synchronous execution owner; publish only after database commit.

    Live writes must register first. Borrowed reads still use borrowed_base;
    child contexts must not edit existing saved items. No event-loop yield is
    allowed before serialization and the outer commit. Caught inner exceptions
    retain their writes, matching Storage.transaction (no savepoints).
    """
    storage = getattr(window, 'store', None)
    manager = journal._transactions if hasattr(journal, '_transactions') else journal.db._transactions
    transaction = getattr(manager, 'execution', None)
    outer = transaction is None
    if outer:
        if manager.depth:
            raise RuntimeError('Start accepting() before an enclosing database transaction.')
        transaction = ExecutionTransaction(window.state)
        manager.execution = transaction
    elif transaction.state is not window.state:
        raise RuntimeError('Cannot replace the live document inside execution.')
    def committed():
        # Clear ownership before callbacks can start another acceptance.
        manager.execution = None
        transaction.commit()
        changed = getattr(window, 'document_changed', None)
        if changed:
            changed(frozenset(path[0] for path in transaction.changes),
                    reason='execution', kind='content')
    try:
        with journal.transaction(), (storage.coalesce_saves() if storage else nullcontext(False)) as saving:
            if outer: manager.after_commit(committed)
            yield transaction
            persist = getattr(window, 'persist', None)
            if saving and storage.save_pending and persist is not None:
                # persist delegates the UI's view capture; keep that write in
                # this boundary without changing the UI-only autosave path.
                transaction.register('canvas_view')
                if not persist(): raise OSError('資料儲存失敗。')
    except BaseException:
        if outer and not transaction.committed:
            try:transaction.rollback()
            finally:manager.execution = None
        raise


def select_live_image(state, key, index, directory, *, register, preserve_reservation=False):
    """Execution writer for select(), including its derived materialization."""
    from .image_source import select
    register('canvas_functions', 'images', key)
    for path in (('uses',), ('text_positions',), ('output_order',), ('multi_output', 'canvases')):
        register(*path)
    return select(state, key, index, directory, preserve_reservation=preserve_reservation)


def borrowed_base(live, scene=None, *, copy_top=True):
    """Borrow a live document for synchronous, read-only execution preparation.

    By default only the top-level dict is new; its values are shared with live.
    With a scene, workspace_scene.load replaces scene fields with deep copies
    and creates a new generation dict with deep-copied scene-local values.
    Existing live generation profiles and other non-scene values stay shared;
    load removes absent scene fields and _execution_inputs as before.
    Without a scene, copy_top=False returns live itself, without any copy.
    Callers may only read shared parts; this is not an editable document.
    Before borrowed data enters a container that outlives a yield point
    (an in-memory run, frame['ctx'], or asynchronous save), serialize it on the
    main thread and retain only the serialized content or its independent decode.
    Live execution writes require registration in the execution transaction;
    borrowing is not a second write or read ownership boundary. Child contexts
    must not modify existing items in their shared saved mapping.
    """
    if scene is None and not copy_top:return live
    result=dict(live)
    if scene is not None:
        from .workspace_scene import load
        load(result,scene)
    return result


def execution_state(state, suppliers=()):
    """Copy the branches projection writes; all other document data is read-only.

    This view is local to synchronous capture/prepare, never an editable document.
    In particular, history, libraries and inactive workspaces are not projected.
    Supplier progress is the only operation here that edits existing roots.
    """
    result=dict(state)
    result['uses']=dict(state.get('uses',{}))
    if 'text_positions' in state:result['text_positions']=dict(state['text_positions'])
    data=result['multi_output']=dict(state['multi_output'])
    data['canvases']={k:dict(v,members=list(v['members'])) for k,v in data['canvases'].items()}
    functions=dict(state.get('canvas_functions',{}))
    if 'canvas_functions' in state:result['canvas_functions']=functions
    if 'images' in functions:functions['images']={k:dict(v) for k,v in functions['images'].items()}
    if 'text_sources' in functions:
        functions['text_sources']={k:dict(v) for k,v in functions['text_sources'].items()}
    if 'generation' in state:result['generation']=dict(state['generation'])
    from .prompt_sources import source
    for key in suppliers:
        definition=source(result,key)
        if definition:
            root=copy.deepcopy(definition['root'])
            if key.startswith('use:'):result['uses'][key[4:]]=root
            else:functions['text_sources'][key]['root']=root
    return result


def execution_scene(state):
    """Read-only scene for immediate journaling; workspace_scene.load owns edits."""
    from .workspace_scene import FIELDS
    result={k:state[k] for k in FIELDS if k in state}
    result['generation_local']={k:v for k,v in state.get('generation',{}).items() if k!='profiles'}
    return result


def copy_context(context):
    """Fork maps; children must not modify existing saved items or scenes."""
    saved=context.get('saved')
    shared=[context.get('scene'),saved]
    result=copy.deepcopy(context,{id(value):value for value in shared if value is not None})
    if saved is not None:result['saved']=merge_saved(saved,{},borrow=True)
    return result


def _capture_source(path):
    # Raw I/O bounds actual reads, including short reads and a growing file.
    blocks=[];remaining=MAX_SOURCE_BYTES+1
    with path.open('rb',buffering=0) as stream:
        while remaining:
            block=stream.read(min(1024*1024,remaining))
            if not block:break
            blocks.append(block);remaining-=len(block)
    data=b''.join(blocks)
    return CapturedImage(path,data,sha256(data).hexdigest() if len(data)<=MAX_SOURCE_BYTES else None)


def _project_source(path, digest, readers):
    """Keep bytes/metadata local to one source group, never across project calls."""
    from .image_source import content_from_metadata
    try:
        captured = _capture_source(path)
        if len(captured.data) > MAX_SOURCE_BYTES or captured.digest != digest:
            for item in readers:
                item['content_error'] = '構圖圖片已變更：' + item['source']['name']
            return
        with BytesIO(captured.data) as stream:
            metadata = png_metadata_stream(stream)
    except (ValueError, OSError) as exc:
        for item in readers:
            item['content_error'] = str(exc)
        return
    for item in readers:
        try:
            item['content'] = content_from_metadata(metadata, item.get('prompt_choice'))
        except (ValueError, OSError) as exc:
            item['content_error'] = str(exc)


def project(state,directory,results=None,source_values=None,readers=None,*,execution=False):
    from .composition_image import source_path
    state=execution_state(state) if execution else copy.deepcopy(state)
    state['_stage_results']=copy.deepcopy(results or {})
    images=state.get('canvas_functions',{}).get('images',{})
    for key,settings in (readers or {}).items():
        if key in images:
            for name in ('output_node','text_field','input_index','stage_reference','prompt_choice'):images[key].pop(name,None)
            images[key].update(copy.deepcopy(settings))
    visiting=set();done=set();groups={}
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
            try:
                root=Path(directory).resolve();path=source_path(root,source)
                digest=source['sha256']
                if not isinstance(digest,str):raise ValueError('構圖圖片已變更：'+source['name'])
                groups.setdefault((root,path,digest),[]).append(item)
            except (ValueError,OSError) as exc:item['content_error']=str(exc)
        visiting.remove(key);done.add(key)
    # The existing DFS selects sources before content is derived. image_list's
    # image routes depend on sources, indices/results and frozen inputs, not content.
    for key in images:load(key)
    for (_,path,digest),items in groups.items():
        _project_source(path,digest,items)
    # Source overrides select the current item, including wired batch sources.
    for key,source in (source_values or {}).items():
        if key in images:
            images[key].pop('stage_reference',None)
            state['multi_output']['connections']=[c for c in state['multi_output']['connections'] if not(c['destination']==key and c['kind']=='image')]
    materialize(state);return state


def sync_result_view(state,directory,results,*,register=None):
    """Refresh derived Canvas content without persisting runtime task results."""
    projected=project(state,directory,results,execution=True);changed=False
    for key,value in projected.get('canvas_functions',{}).get('images',{}).items():
        current=state['canvas_functions']['images'][key]
        if incoming(state,key,'image') or current.get('stage_reference'):
            if register:register('canvas_functions','images',key)
            for field in ('source','content','content_error'):
                if current.get(field)!=value.get(field):changed=True
                if field in value:current[field]=copy.deepcopy(value[field])
                else:current.pop(field,None)
    # project() materialized both PNG metadata and selected Stage text. These
    # are the only edit-document fields it derives; user-owned roots survive.
    for field in ('uses','text_positions','output_order'):
        if state.get(field)!=projected.get(field):
            if register:register(field)
            changed=True
            if field in projected:state[field]=copy.deepcopy(projected[field])
            else:state.pop(field,None)
    if state['multi_output']['canvases']!=projected['multi_output']['canvases']:
        if register:register('multi_output','canvases')
        changed=True;state['multi_output']['canvases']=copy.deepcopy(projected['multi_output']['canvases'])
    return changed


def capture(state,plan,keys,directory,policies=None,per_round=False,*,borrow=False):
    """A deferred upstream reference is never replaced with the last preview."""
    saved=dict(values={},inputs={},batches={},source_positions={},readers={})
    from .stage_parameters import capture as capture_parameters
    saved['parameters']=capture_parameters(state,keys)
    for key in keys:
        stage=plan['stages'][key]
        for binding in stage['bindings']:
            source=binding['source'];policy=(policies or {}).get(binding['key'],'saved')
            if (policy=='live' and not binding.get('scheduler')) or binding.get('text_source')=='web' or not source:continue
            nodes=upstream(state,[source]);future=nodes&plan['stages'].keys()
            for node in nodes:
                ref=state.get('canvas_functions',{}).get('images',{}).get(node,{}).get('stage_reference')
                if ref:future.add(ref)
                image=state.get('canvas_functions',{}).get('images',{}).get(node)
                if image is not None:saved['readers'][node]={k:copy.deepcopy(image[k]) for k in ('output_node','text_field','input_index','stage_reference','prompt_choice') if k in image}
            if future:continue
            from .composition_render import freeze_image_source
            if binding['kind']=='image' and source in state['multi_output']['canvases']:
                from .flow_data import value
                item=value('image',freeze_image_source(state,directory,source))
            else:item=resolve(state,source,binding['kind'])
            if binding['kind']=='clip':
                from .flow_data import canvas_ids
                output=state['multi_output']['outputs'].get(item.get('origin',{}).get('output',source))
                if output:
                    item.setdefault('origin',{})['canvas_records']={cid:dict(canvas=state['multi_output']['canvases'][cid] if borrow else copy.deepcopy(state['multi_output']['canvases'][cid]),
                        roots={rid:state['uses'][rid] if borrow else copy.deepcopy(state['uses'][rid]) for rid in state['multi_output']['canvases'][cid]['members']}) for cid in canvas_ids(output)}
            saved['values'][binding['key']]=item
        scheduler=stage['scheduler']
        if scheduler:
            # Only connected input channels are frozen; untouched inputs stay live.
            try:saved['inputs'].update(capture_inputs(state,scheduler))
            except ValueError:
                # Deferred Stage results are read only once this parent iteration reaches them.
                if not any((upstream(state,[b['source']])&plan['stages'].keys()) for b in stage['bindings'] if b['source']):raise
        for source in (stage.get('suppliers') or [stage['auto']]) if stage['auto'] and not per_round else []:
            image=state.get('canvas_functions',{}).get('images',{}).get(source)
            if image and not incoming(state,source,'image') and not image.get('stage_reference'):
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
                if image.get('iterate'):saved['source_positions'][source]=copy.deepcopy(image.get('supply_position') or dict(batch=image.get('batch_id'),index=image.get('index',0)))
    return saved


def merge_saved(outer,inner,*,borrow=False):
    scene=outer.get('scene')
    result=dict(outer) if borrow else copy.deepcopy(outer,{id(scene):scene} if scene is not None else {})
    for kind in ('values','inputs','batches','source_positions','readers','parameters','parameter_overrides','parameter_profiles'):
        if borrow:result[kind]=dict(outer.get(kind,{}),**inner.get(kind,{}))
        else:result.setdefault(kind,{}).update(copy.deepcopy(inner.get(kind,{})))
    return result


def prepare_state(runner,run,stage,context,source_values=None):
    from .multi_output import new_output
    from .composition_render import freeze_image_source
    live=runner.window.state;state=borrowed_base(live)
    # Route comes from the queued definition; native graphs stay live.
    saved=context.get('saved',{});state['_execution_inputs']=saved.get('inputs',{})
    state=project(state,runner.window.store.directory,context.get('results'),source_values,saved.get('readers'),execution=True)
    # PNG metadata follows the saved text, including its original module structure.
    for value in [*saved.get('values',{}).values(),*saved.get('inputs',{}).values()]:
        for cid,record in value.get('origin',{}).get('canvas_records',{}).items():
            if cid in state['multi_output']['canvases']:
                state['multi_output']['canvases'][cid]=dict(record['canvas']);state['uses'].update(record['roots'])
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
    # These roots are the saved input, not a live reader cache. The reader and
    # its incoming wires are omitted below; restore must not delete its roots
    # as disconnected derived content. Exact manual text is retained in texts.
    for canvas in data['canvases'].values():
        for field in ('source_members','source_revision','raw_prompt','source_error'):canvas.pop(field,None)
    roots={k for c in data['canvases'].values() for k in c['members']}
    state['uses']={k:v for k,v in state.get('uses',{}).items() if k in roots}
    state['output_order']=[k for k in state.get('output_order',[]) if k in roots]
    data.update(outputs={},clip_inputs={},bindings=[],connections=[],image_inputs={},schedulers={},stages={stage['id']:copy.deepcopy(live['multi_output']['stages'].get(stage['id'],stage))},current_output=None)
    from .stage_parameters import effective
    # Config displayed on today's Canvas is not the intention of an old item.
    data['stages'][stage['id']].update(workflow=stage['workflow'],parameters=effective(saved,stage['id']))
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
    profile=saved.get('parameter_profiles',{}).get(stage['id'])
    state['generation']['profiles']=([copy.deepcopy(profile)] if profile is not None else
        [p for p in state['generation']['profiles'] if p['id']==stage['workflow']])
    return state,images,foreign
