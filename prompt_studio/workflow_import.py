"""Convert known ComfyUI canvas nodes without guessing custom widget layouts."""
import copy
import uuid
from .generation import api_graph,ancestors,text_fields,suggested_text,validate_profile,parameter_bindings

# The serialized widget order of these standard ComfyUI nodes is stable.
WIDGETS={
    'KSampler':('seed','control_after_generate','steps','cfg','sampler_name','scheduler','denoise'),
    'KSamplerAdvanced':('add_noise','noise_seed','control_after_generate','steps','cfg','sampler_name','scheduler','start_at_step','end_at_step','return_with_leftover_noise'),
    'CLIPTextEncode':('text',),'CLIPTextEncodeSDXL':('width','height','crop_w','crop_h','target_width','target_height','text_g','text_l'),
    'CheckpointLoaderSimple':('ckpt_name',),'VAELoader':('vae_name',),'CLIPLoader':('clip_name','type','device'),
    'UNETLoader':('unet_name','weight_dtype'),'LoraLoader':('lora_name','strength_model','strength_clip'),
    'LoraLoaderModelOnly':('lora_name','strength_model'),'LoadImage':('image','upload'),
    'EmptyLatentImage':('width','height','batch_size'),'EmptySD3LatentImage':('width','height','batch_size'),
    'ImageScale':('upscale_method','width','height','crop'),'ImageScaleBy':('upscale_method','scale_by'),
    'VAEEncode':(),'VAEEncodeForInpaint':('grow_mask_by',),'VAEDecode':(),
    'PreviewImage':(),'SaveImage':('filename_prefix',),'LatentUpscale':('upscale_method','width','height','crop'),
    'CLIPSetLastLayer':('stop_at_clip_layer',),'ConditioningCombine':(),'ConditioningConcat':(),
}

def import_graph(value):
    if not isinstance(value,dict) or 'nodes' not in value: return api_graph(value)
    if not isinstance(value['nodes'],list) or not value['nodes'] or len(value['nodes'])>2000:
        raise ValueError('工作流節點格式無效。')
    if any(not isinstance(n,dict) or 'id' not in n or not isinstance(n.get('inputs',[]),list)
           or any(not isinstance(i,dict) for i in n.get('inputs',[])) for n in value['nodes']):
        raise ValueError('工作流節點格式無效。')
    definitions=value.get('definitions') or {}
    if not isinstance(definitions,dict): raise ValueError('工作流定義格式無效。')
    if definitions.get('subgraphs'): raise ValueError('子工作流請先從 ComfyUI 匯出 API 格式。')
    if not isinstance(value.get('links',[]),list): raise ValueError('工作流接線格式不支援，請匯出 API。')
    nodes={str(n['id']):n for n in value['nodes']}; links={str(v[0]):v for v in value.get('links',[]) if isinstance(v,list) and len(v)>=6}
    if len(nodes)!=len(value['nodes']): raise ValueError('工作流節點編號重複。')
    graph={}
    def linked(link_id,seen=None):
        seen=set() if seen is None else seen
        link=links.get(str(link_id))
        if link is None or str(link_id) in seen: raise ValueError('工作流接線無效。')
        seen.add(str(link_id)); origin=nodes.get(str(link[1]))
        if origin is None: raise ValueError('工作流缺少接線來源。')
        if origin.get('mode')==2: raise ValueError('接線連到停用節點。請先在 ComfyUI 修正。')
        if origin.get('type')=='Reroute' or origin.get('mode')==4:
            inputs=[i for i in origin.get('inputs',[]) if i.get('link') is not None and (origin['type']=='Reroute' or i.get('type')==link[5])]
            if len(inputs)!=1: raise ValueError('略過節點的接線不明確，請匯出 API 格式。')
            return linked(inputs[0]['link'],seen)
        return [str(link[1]),link[2]]
    for ident,node in nodes.items():
        kind=node.get('type')
        if node.get('mode') in (2,4) or kind in ('Reroute','Note','MarkdownNote'): continue
        if kind not in WIDGETS: raise ValueError('節點 '+str(kind)+' 請使用 ComfyUI 匯出的 API 格式。')
        fields=WIDGETS[kind]; values=node.get('widgets_values',[])
        if not isinstance(values,list): raise ValueError('節點 '+str(kind)+' 的欄位格式不支援，請匯出 API。')
        # Some older exports omit the seed's UI-only policy widget.
        if 'control_after_generate' in fields:
            at=fields.index('control_after_generate')
            if len(values)<=at or values[at] not in ('fixed','increment','decrement','randomize'):
                fields=tuple(f for f in fields if f!='control_after_generate')
        optional=1 if kind in ('LoadImage','CLIPLoader') else 0
        if not len(fields)-optional<=len(values)<=len(fields):
            raise ValueError('節點 '+str(kind)+' 的欄位數不符，請匯出 API 格式。')
        inputs={field:copy.deepcopy(v) for field,v in zip(fields,values) if field not in ('control_after_generate','upload')}
        for port in node.get('inputs',[]):
            if port.get('link') is not None: inputs[port['name']]=linked(port['link'])
        graph[ident]=dict(class_type=kind,inputs=inputs,_meta={'title':node.get('title',kind)})
    return api_graph(graph)

def infer_profile(graph,name,workflow=None):
    samplers=[k for k,n in graph.items() if n['class_type']=='KSampler']
    sampler=samplers[0] if len(samplers)==1 else ''
    inputs=graph.get(sampler,{}).get('inputs',{}); latent=inputs.get('latent_image')
    upstream=ancestors(graph,str(latent[0])) if isinstance(latent,list) else set(graph)
    images=[k for k,n in graph.items() if n['class_type']=='LoadImage' and k in upstream]
    mode='img2img' if images else 'txt2img'; fields=text_fields(graph)
    prompt=suggested_text(graph,sampler) or (fields[0] if len(fields)==1 else None)
    sizes=[k for k,n in graph.items() if k in upstream and n['class_type'] in ('ImageScale','EmptyLatentImage','EmptySD3LatentImage')]
    profile=dict(id=uuid.uuid4().hex,name=name,mode=mode,graph=graph,prompt=list(prompt) if prompt else None,
        image=images[0] if len(images)==1 else '',sampler=sampler,size=sizes[0] if len(sizes)==1 else '',values={},seed_mode='random')
    # API graphs omit frontend controls. Recover the policy from the accompanying
    # saved workflow; API-only imports default to fresh seeds.
    if isinstance(workflow,dict):
        workflow=workflow.get('workflow',workflow)
        if isinstance(workflow.get('id'),str) and workflow['id']:profile['frontend_id']=workflow['id']
        for node in workflow.get('nodes',[]) if isinstance(workflow,dict) and isinstance(workflow.get('nodes'),list) else []:
            if not isinstance(node,dict) or str(node.get('id'))!=sampler: continue
            fields=WIDGETS.get(node.get('type'),()); values=node.get('widgets_values',[])
            if 'control_after_generate' in fields and isinstance(values,list):
                index=fields.index('control_after_generate'); rule=values[index] if len(values)>index else None
                if rule in ('fixed','increment','decrement','randomize'): profile['seed_mode']='random' if rule=='randomize' else rule
    profile['values']={field:graph[key]['inputs'][field] for field,(key,_) in parameter_bindings(profile).items()}
    return profile


def read_profile(value,name,ident,origin):
    """Read nodes without applying a transfer's unrelated desktop bindings."""
    if not isinstance(value,dict): raise ValueError('工作流格式無效。')
    if value.get('format')=='prompt_studio_workflow':
        if value.get('version')!=1 or not isinstance(value.get('id'),str) or not value['id']:
            raise ValueError('工作流交換檔格式無效。')
        profile=infer_profile(import_graph(value.get('graph')),value.get('name'),value.get('workflow'))
        ident=value['id']
    else: profile=infer_profile(import_graph(value),name,value)
    profile.update(id=ident,multi_text=True,origin=origin)
    return validate_profile(profile)
