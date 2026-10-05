"""Read an identified run from the existing Comfy queue/history, without submitting."""
import copy
import hashlib
import json
from urllib.parse import urlsplit
from .node_images import reference


def server_identity(value):
    url=urlsplit(value)
    return (url.scheme, '127.0.0.1' if url.hostname=='localhost' else url.hostname, url.port)


def live_state(query,library,server):
    """Compile only the current desktop bindings. Never read or edit a job."""
    from .shared.multi_output import bound_texts
    from .native_queue import digest
    state=library['state']; connection=library.get('connection',{})
    # Stage-era inputs are applied by an explicit Execute transaction only.
    # Library polling remains read-only; native Run never pulls desktop edits.
    if state.get('multi_output',{}).get('version',0)>=7:return None
    if not connection.get('enabled') or server_identity(connection.get('server',''))!=server_identity(server):return None
    if state.get('multi_output',{}).get('version',0)<4:return None
    profiles=[p for p in state.get('generation',{}).get('profiles',[])
              if matches(query,dict(workflow_id=p['id']),p)
              and (not p.get('origin',{}).get('server') or server_identity(p['origin']['server'])==server_identity(server))]
    if len(profiles)!=1:return None
    profile=profiles[0]
    # Keep the existing empty display on unbind/compile failure, but never turn
    # that fallback into a successfully applied source acknowledgement.
    try:
        bindings=bound_texts(state,profile)
        source_revision=digest(bindings)
    except ValueError:
        bindings=[];source_revision=None
    texts=[dict(node=b['node'],field=b['field'],text=b['text'],class_type=profile['graph'][b['node']]['class_type'],
                binding=b.get('clip',b['output'])+'/'+b['output'],text_source=b.get('text_source','pcs')) for b in bindings]
    revision=hashlib.sha256(json.dumps(texts,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
    return dict(owner=library['library_id']+'/'+state['workspace']+'/'+profile['id'],revision=revision,
                source_revision=source_revision,texts=texts,name=profile['name'])


def run_metadata(entry):
    prompt=entry.get('prompt',())
    if not isinstance(prompt,(list,tuple)) or len(prompt)<4:return None
    extra=prompt[3].get('extra_pnginfo',{}) if isinstance(prompt[3],dict) else {}
    marker=extra.get('prompt_studio') or extra.get('prompt_studio_request') or {}
    generation=marker.get('generation')
    if not isinstance(generation,dict) or marker.get('problems'):return None
    snapshot=marker.get('snapshot') or next((b.get('snapshot') for b in marker.get('bindings',[]) if b.get('snapshot')), {})
    profile=next((p for p in snapshot.get('state',{}).get('generation',{}).get('profiles',[]) if p.get('id')==generation.get('workflow_id')), {})
    return prompt,marker,generation,profile


def matches(query,generation,profile):
    origin=generation.get('origin') or profile.get('origin') or {}
    native=generation.get('frontend_id') or profile.get('frontend_id')
    # Duplicating an unsaved graph can retain its embedded PCS identity.
    # A known native ID must match for both saved and unsaved workflows.
    if native and query.get('frontend_id')!=native:return False
    # A saved file has a path identity. A transferred/unsaved workflow has its
    # embedded PCS identity. Display names and graph similarity are never IDs.
    if origin.get('path'):
        return query.get('path')==origin['path']
    return bool(query.get('workflow') and query['workflow']==generation.get('workflow_id'))


def latest_run(query,history,running=(),queued=()):
    if any(not isinstance(query.get(k,''),str) or len(query.get(k,''))>1000 for k in ('workflow','path','frontend_id')):
        raise ValueError('工作流身分無效。')
    candidates=[(dict(prompt=p),'running') for p in running]
    candidates.extend((dict(prompt=p),'queued') for p in queued)
    candidates.extend((e,'failed' if e.get('status',{}).get('status_str')=='error' else 'complete') for e in reversed(list(history.values())))
    for entry,phase in candidates:
        data=run_metadata(entry)
        if not data:continue
        prompt,marker,generation,profile=data
        if not matches(query,generation,profile):continue
        graph=prompt[2]; texts=[]
        for b in marker.get('texts',[]):
            node=graph.get(b.get('node'),{}); value=node.get('inputs',{}).get(b.get('field'))
            if b.get('field') in ('text','text_g','text_l') and isinstance(value,str):
                texts.append(dict(node=b['node'],field=b['field'],text=value,class_type=node['class_type']))
        outputs={}
        for key,value in entry.get('outputs',{}).items():
            if graph.get(key,{}).get('class_type') in ('PreviewImage','SaveImage') and isinstance(value,dict):
                outputs[key]=dict(images=[reference(i) for i in value.get('images',[])][:64],class_type=graph[key]['class_type'])
        images=[]; uploaded=generation.get('source',{}).get('uploaded')
        if uploaded:
            for key,node in graph.items():
                if node.get('class_type')=='LoadImage' and node.get('inputs',{}).get('image')==uploaded:
                    images.append(dict(node=key,field='image',text=uploaded,class_type='LoadImage'))
        return dict(prompt_id=str(prompt[1]),workflow=generation['workflow_id'],name=generation['workflow'],phase=phase,
                    texts=texts,image_inputs=images,outputs=outputs)
    return {}
