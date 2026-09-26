"""Local image-node references; never resolve a path outside Comfy's image roots."""
import copy
import hashlib
import json
import time
from pathlib import Path,PurePosixPath
from urllib.parse import urlsplit


def reference(value):
    if isinstance(value,str):
        kind='input'
        for suffix in (' [input]',' [output]',' [temp]'):
            if value.endswith(suffix): value=value[:-len(suffix)]; kind=suffix[2:-1]; break
        p=PurePosixPath(value.replace('\\','/'))
        value=dict(filename=p.name,subfolder='' if str(p.parent)=='.' else str(p.parent),type=kind)
    if not isinstance(value,dict): raise ValueError('圖片參照無效。')
    result={k:value.get(k,'input' if k=='type' else '') for k in ('filename','subfolder','type')}
    if result['type'] not in ('input','output','temp') or any(not isinstance(v,str) for v in result.values()): raise ValueError('圖片參照無效。')
    for key in ('filename','subfolder'):
        p=PurePosixPath(result[key].replace('\\','/'))
        if p.is_absolute() or '..' in p.parts or ':' in result[key]: raise ValueError('圖片路徑不可超出 ComfyUI 目錄。')
    if not result['filename'] or '/' in result['filename'] or '\\' in result['filename']: raise ValueError('圖片檔名無效。')
    return result


def source_origin(value):
    """Absent/null provenance is valid for imported workflows; other types are not."""
    if value is None:return {}
    if not isinstance(value,dict):raise ValueError('工作流來源格式無效。')
    if any(not isinstance(value.get(key,''),str) or len(value.get(key,''))>1000 for key in ('path','server')):
        raise ValueError('工作流來源格式無效。')
    return value


def history_identity(entry):
    prompt=entry.get('prompt',[]); extra=prompt[3] if isinstance(prompt,(list,tuple)) and len(prompt)>3 and isinstance(prompt[3],dict) else {}
    info=extra.get('extra_pnginfo',{})
    # Service.prepare_prompt consumes the request marker before Comfy queues it.
    # Queue history uses tuples in-process, arrays after JSON serialization.
    marker=info.get('prompt_studio',{}).get('generation',{}) or info.get('prompt_studio_request',{}).get('generation',{})
    visual=info.get('workflow',{})
    return dict(workflow=marker.get('workflow_id') or visual.get('extra',{}).get('prompt_studio_v08',{}).get('id'),
                path=source_origin(marker.get('origin')).get('path',''),frontend_id=marker.get('frontend_id') or visual.get('id',''))


def history_workflow(entry):return history_identity(entry)['workflow']


def query_identity(query):
    origin=source_origin(query.get('origin')); native=query.get('frontend_id')
    if native is not None and (not isinstance(native,str) or len(native)>200):raise ValueError('工作流身分無效。')
    return origin,native


def matches_source(query,value):
    origin,native=query_identity(query); path=origin.get('path')
    if path:
        if value.get('path')!=path and not (native and value.get('frontend_id')==native and not value.get('path')):return False
    elif value.get('workflow')!=query['workflow'] and not (native and value.get('frontend_id')==native and not value.get('workflow')):return False
    return not native or value.get('frontend_id')==native


def server_identity(value):
    url=urlsplit(value)
    return url.scheme,'127.0.0.1' if url.hostname=='localhost' else url.hostname,url.port or (443 if url.scheme=='https' else 80)


class NodeImages:
    def __init__(self,roots): self.roots={k:Path(v).resolve() for k,v in roots.items()}; self.live={}
    def publish(self,value):
        ident=value.get('workflow'); nodes=value.get('nodes')
        if not isinstance(ident,str) or not ident or len(ident)>200 or not isinstance(nodes,dict) or len(nodes)>500: raise ValueError('工作流圖片狀態無效。')
        checked={}
        for key,node in nodes.items():
            if not isinstance(key,str) or not isinstance(node,dict) or node.get('type') not in ('LoadImage','PreviewImage','SaveImage'): raise ValueError('圖片節點狀態無效。')
            images=node.get('images',[])
            if not isinstance(images,list) or len(images)>64: raise ValueError('圖片清單過大。')
            checked[key]=dict(type=node['type'],images=[reference(v) for v in images],selected=node.get('selected'))
            if node.get('overflow') is True:checked[key]['overflow']=True
            index=checked[key]['selected']
            if index is not None and (type(index) is not int or not 0<=index<len(images)): raise ValueError('圖片選擇無效。')
        path=value.get('path',''); native=value.get('frontend_id','')
        if not isinstance(path,str) or len(path)>1000 or not isinstance(native,str) or len(native)>200:raise ValueError('工作流身分無效。')
        identity=(ident,path,native); previous=self.live.get(identity,{})
        if previous.get('nodes')!=checked or previous.get('path')!=path or previous.get('frontend_id')!=native:
            now=time.time()
            times={key:previous.get('node_times',{}).get(key,now) if
                   all(previous.get('nodes',{}).get(key,{}).get(field)==node.get(field) for field in ('type','images','overflow')) else now for key,node in checked.items()}
            self.live[identity]=dict(workflow=ident,nodes=checked,updated=now,node_times=times,path=path,frontend_id=native)
            while len(self.live)>50: self.live.pop(next(iter(self.live)))
        return {'ok':True}
    def resolve(self,query,history,server=None):
        workflow,node=query['workflow'],query['node']; kind=query['class_type']; prompt_id=query.get('prompt_id')
        if kind not in ('LoadImage','PreviewImage','SaveImage'): raise ValueError('不支援的圖片節點。')
        identity,_=query_identity(query); expected_server=identity.get('server')
        if server and expected_server and server_identity(server)!=server_identity(expected_server):raise ValueError('圖片來源屬於另一個 ComfyUI 伺服器。')
        entry=history.get(prompt_id) if prompt_id else next((v for v in reversed(list(history.values())) if history_workflow(v) in (None,workflow) and matches_source(query,history_identity(v)) and v.get('status',{}).get('completed')),None)
        images=[]; selected=None; origin=''
        candidates=[v for v in self.live.values() if matches_source(query,v)]
        if len(candidates)>1 and not prompt_id:raise ValueError('有多份相符工作流圖片來源，請確認工作流與節點綁定。')
        published=candidates[0] if len(candidates)==1 else {}
        live=published.get('nodes',{}).get(node)
        messages=entry.get('status',{}).get('messages',[]) if entry else []
        completed=max((m[1]['timestamp']/1000 for m in messages if isinstance(m,(list,tuple)) and len(m)>1 and m[0]=='execution_success' and isinstance(m[1],dict) and type(m[1].get('timestamp')) in (int,float)),default=0)
        if prompt_id and entry and (history_workflow(entry) not in (None,workflow) or not matches_source(query,history_identity(entry))):raise ValueError('圖片任務不屬於綁定的工作流。')
        if entry:
            images=entry.get('outputs',{}).get(node,{}).get('images',[]); origin='result'
        if kind=='LoadImage' and entry:
            graph=entry.get('prompt',[None,None,{}])[2]
            value=graph.get(node,{}).get('inputs',{}).get('image')
            if isinstance(value,str):images=[reference(value)]; origin='submitted'
        # A late/reopened browser may publish its previous thumbnails after a
        # real completion. For an exact native binding, use that completion's
        # whole batch; publication time is not execution time.
        native_result=bool(query.get('frontend_id') and images)
        if not prompt_id and live and live.get('overflow') and not native_result:raise ValueError('節點超過 64 張圖片，請減少單次批量後再讀取。')
        if not prompt_id and live and live['type']==kind and live['images'] and (kind=='LoadImage' or not images or not native_result and published.get('node_times',{}).get(node,published.get('updated',0))>=completed):
            # A current browser selection wins only for a free-standing read.
            # A dependent stage explicitly reads its upstream prompt_id.
            images=live['images']; selected=live['selected']; origin='live'
        if not images and kind=='LoadImage' and not prompt_id and isinstance(query.get('image'),str):
            images=[reference(query['image'])]; origin='workflow'
        if not images: raise ValueError('此節點尚無圖片。')
        if len(images)>64:raise ValueError('節點超過 64 張圖片，請減少單次批量後再讀取。')
        if len(images)==1:selected=0
        if selected is None and live:
            index=live.get('selected')
            current=live['images'][index] if index is not None else None
            selected=next((i for i,v in enumerate(images) if reference(v)==current),None)
        actual_id=(entry.get('prompt') or [None,None])[1] if entry and origin in ('result','submitted') else None
        rows=[]
        for value in images:
            image=reference(value);root=self.roots[image['type']]
            path=(root/image['subfolder']/image['filename']).resolve()
            if not path.is_relative_to(root) or not path.is_file():raise ValueError('節點圖片不存在。')
            stat=path.stat()
            if stat.st_size>25*1024*1024:raise ValueError('節點圖片超過 25 MB。')
            rows.append(dict(path=str(path),image=copy.deepcopy(image),size=stat.st_size,mtime=stat.st_mtime_ns,
                             origin=origin,prompt_id=actual_id,workflow=workflow,node=node))
        collection=hashlib.sha256(json.dumps([identity,query.get('frontend_id'),kind,rows],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        choice=query.get('selection')
        if query.get('explicit_selection'):
            selected=0 if len(rows)==1 else None
            if isinstance(choice,dict) and choice.get('collection')==collection:
                selected=next((i for i,row in enumerate(rows) if row['image']==choice.get('image')),None)
        if query.get('collection') is True:return dict(collection=collection,items=rows,selected=selected)
        if selected is None or not 0<=selected<len(rows):raise ValueError('節點有多張圖片，請先在 PCS 選擇其中一張。')
        return rows[selected]
