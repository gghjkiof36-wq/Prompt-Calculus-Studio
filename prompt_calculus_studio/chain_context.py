"""Build one stage's input context without editing the user's visible canvas."""
import copy
from .chain_model import provenance
from .flow_data import resolve,materialize


def apply_root(state,plan,root,directory):
    if root.get('source'):
        from .image_source import select
        value=state.get('canvas_functions',{}).get('images',{}).get(plan['source'])
        if value is None:raise ValueError('入口圖片來源模塊已移除。')
        # Validate the managed file/hash and read metadata through the same
        # path as a manual image selection. Never change the visible canvas.
        value['items']=[copy.deepcopy(root['source'])];value.pop('binding',None)
        select(state,plan['source'],0,directory)
        value['index']=root.get('index',0)
    state['_execution_inputs']=copy.deepcopy(root.get('inputs',{}));materialize(state)


def context(runner,run,item):
    from .composition_image import freeze_image_source
    from .multi_output import new_output,connect
    state=runner.flow.context();stage=run['plan']['stages'][item['stage']]
    profile=next((p for p in state['generation']['profiles'] if p['id']==stage['workflow']),None)
    if not profile or dict(frontend_id=profile.get('frontend_id'),origin=profile.get('origin',{}))!=stage['identity']:
        raise ValueError('階段的原生工作流身分已變更，請修復綁定。')
    entry=item['entry'];data=state['multi_output'];directory=runner.window.store.directory
    # A source list was fixed at chain start; metadata is read only for the
    # selected input, independently of the canvas currently on screen.
    root=entry.get('root',{})
    apply_root(state,run['plan'],root,directory)
    texts=[];images=[]
    for mapping in stage['texts']:
        mode=mapping['mode']
        if mode=='pcs':text=resolve(state,mapping['source'],'clip')['value']
        elif mode=='web':text=''
        else:
            source=mapping['source'];ancestor=entry.get('ancestors',{}).get(source['stage'])
            if not ancestor:raise ValueError('找不到這一項指定的上游文字。')
            job=runner.client.generation.record(ancestor)
            text=(job or {}).get('payload',{}).get('prompt',{}).get(source['node'],{}).get('inputs',{}).get(source['field'])
            if not isinstance(text,str):raise ValueError('上游實際提交沒有指定文字欄位，未沿用上一張。')
        texts.append(dict(mapping,text=text,text_source='web' if mode=='web' else 'pcs'))
    for mapping in stage['images']:
        key=mapping['source']
        image=freeze_image_source(state,directory,key) if key in data['canvases'] else resolve(state,key,'image')['value']
        images.append((mapping['node'],copy.deepcopy(image)))
    if stage.get('upstream'):
        if not entry.get('source'):raise ValueError('上游圖片尚未保存，未提交。')
        images.append((stage['upstream']['node'],copy.deepcopy(entry['source'])))
    # Stage-local bindings exist solely in this submission snapshot; A→B→A
    # never rewrites or shares the global binding instances of the two As.
    data['bindings']=[b for b in data['bindings'] if b['workflow']!=stage['workflow']]
    removed={k for k,v in data['image_inputs'].items() if v.get('workflow')==stage['workflow']}
    data['image_inputs']={k:v for k,v in data['image_inputs'].items() if k not in removed}
    data['connections']=[c for c in data['connections'] if c['destination'] not in removed]
    for i,mapping in enumerate(texts):
        out='chain_text_'+str(i);clip='chain_clip_'+str(i)
        data['outputs'][out]=dict(new_output('階段文字 '+str(i+1)),canvases=[],text_sources=[],draft=mapping['text'])
        data['clip_inputs'][clip]=dict(name='階段文字輸入',workflow=stage['workflow'],text_source=mapping['text_source'])
        connect(state,out,clip,'clip')
        data['bindings'].append(dict(workflow=stage['workflow'],clip=clip,node=mapping['node'],field=mapping['field']))
    # A pure image/native-text stage need not invent a CLIP input.
    for i,(node,image) in enumerate(images):data['image_inputs']['chain_image_'+str(i)]=dict(name='階段圖片輸入',workflow=stage['workflow'],node=node)
    # Removed image input endpoints cannot leave invalid snapshot connections.
    from .multi_output import valid_edge
    data['connections']=[c for c in data['connections'] if valid_edge(state,c['source'],c['destination'],c['kind'])]
    state.pop('workspace_scenes',None)
    # Execution metadata needs only the current native graph. Other stages'
    # profiles remain in the persistent run, not duplicated in every PNG.
    state['generation']['profiles']=[profile]
    return dict(state=state,images=images,texts=texts)


def marker(run,item,attempt):
    value=dict(run=run['id'],round=item['round'],stage=run['plan']['stages'][item['stage']]['id'],
               item=item['id'],attempt=attempt,revision=run['revision'])
    if item['entry'].get('parent'):value['parent_result']=item['entry']['parent']
    return provenance(value)
