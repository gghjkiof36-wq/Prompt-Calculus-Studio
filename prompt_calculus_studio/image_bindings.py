"""Image-node lookup over the existing localhost client and immutable local copies."""
import copy
import hashlib
from pathlib import Path
from PySide6.QtGui import QImageReader


def import_source(path,directory):
    source=Path(path).resolve(strict=True)
    if source.stat().st_size>25*1024*1024: raise ValueError('來源圖片超過 25 MB。')
    reader=QImageReader(str(source)); size=reader.size(); fmt=bytes(reader.format()).decode('ascii')
    if fmt not in ('png','jpeg','webp','bmp') or not reader.canRead() or not size.isValid() or size.width()*size.height()>40_000_000 or reader.imageCount()>1:
        raise ValueError('請選擇有效的單張 PNG、JPEG、WebP 或 BMP 圖片，最多四千萬像素。')
    content=source.read_bytes(); digest=hashlib.sha256(content).hexdigest()
    relative='originals/generation/'+digest+'.'+fmt; target=Path(directory)/relative; target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        with target.open('xb') as stream: stream.write(content)
    elif hashlib.sha256(target.read_bytes()).hexdigest()!=digest: raise ValueError('圖片保存副本不一致，原資料保留。')
    return dict(relative=relative,name=source.name,sha256=digest,width=size.width(),height=size.height())


def queries(state,keys,completed=None):
    profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}; result=[]
    for key in keys:
        binding=state.get('canvas_functions',{}).get('images',{}).get(key,{}).get('binding')
        if not binding: continue
        profile=profiles.get(binding['workflow']); node=profile['graph'].get(binding['node']) if profile else None
        if node is None: raise ValueError('綁定的圖片工作流或節點已移除。')
        result.append(dict(key=key,**binding,class_type=node['class_type'],image=node.get('inputs',{}).get('image'),origin=profile.get('origin'),frontend_id=profile.get('frontend_id'),prompt_id=(completed or {}).get(binding['workflow']),
                           explicit_selection=True,selection=copy.deepcopy(state['canvas_functions']['images'][key].get('selection'))))
    return result


class ImageBindings:
    def stage_collection(self,key):
        w=self.client.window
        from .stage_context import image_list
        from .flow_data import incoming
        image=w.state.get('canvas_functions',{}).get('images',{}).get(key,{})
        if key not in w.state['multi_output'].get('stages',{}) and not incoming(w.state,key,'image') and not image.get('stage_reference'):
            return None
        try:
            state=w.comfy.input_flow.chain.context(w.comfy.input_flow.chain.results)
            if key in state.get('canvas_functions',{}).get('images',{}):state['canvas_functions']['images'][key].pop('input_index',None)
            images=image_list(state,key)
            ident=hashlib.sha256(str([(i['sha256'],i.get('reference')) for i in images]).encode()).hexdigest()
            return dict(collection=ident,items=images)
        except (ValueError,OSError):return None

    def __init__(self,client): self.client=client; self.values={}; self.collections={}; self.errors={}; self.pending=False; self.owner=None; self.cache={}; self.serial=0
    def identity(self):
        w=self.client.window
        return (w.store,w.state['workspace'],self.client.epoch)
    def reset(self): self.serial+=1; self.values.clear(); self.collections.clear(); self.errors.clear(); self.cache.clear(); self.pending=False; self.owner=None
    def scope(self,key,state=None):
        try:
            rows=queries(self.client.window.state if state is None else state,[key])
            return {k:v for k,v in rows[0].items() if k not in ('selection','prompt_id')} if rows else None
        except ValueError:return None
    def collection(self,key):
        if self.client.window.state.get('multi_output',{}).get('version',0)>=7:
            return self.stage_collection(key)
        scope=self.scope(key)
        record=self.collections.get(key)
        return record[1] if self.owner==self.identity() and record and scope and record[0]==scope else None
    def source(self,key):
        w=self.client.window; value=w.state.get('canvas_functions',{}).get('images',{}).get(key,{})
        if w.state.get('multi_output',{}).get('version',0)>=7:
            from .flow_data import incoming,resolve
            if key in w.state['multi_output']['stages'] or incoming(w.state,key,'image') or value.get('stage_reference'):
                try:return resolve(w.comfy.input_flow.chain.context(w.comfy.input_flow.chain.results),key,'image')['value']
                except (ValueError,OSError):return None
            return value.get('source')
        if not value.get('binding'): return value.get('source')
        collection=self.collection(key)
        if collection:
            items=collection['items'];choice=value.get('selection') or {}
            if len(items)==1:return items[0]
            if choice.get('collection')==collection['collection']:
                return next((item for item in items if item['reference']['image']==choice.get('image')),None)
            return None
        record=self.values.get(key)
        return record[1] if self.owner==self.identity() and record and record[0]==self.scope(key) else None
    def choose(self,key,collection_id,index):
        collection=self.collection(key)
        if not collection or collection['collection']!=collection_id or type(index) is not int or not 0<=index<len(collection['items']):
            self.client.window.notice('圖片集合已更新，請重新選擇。');return
        window=self.client.window
        if window.state.get('multi_output',{}).get('version',0)>=7:
            if key not in window.state.get('canvas_functions',{}).get('images',{}):return
            if window.canvas.commit(lambda state:state['canvas_functions']['images'][key].update(input_index=index)):
                window.comfy.input_flow.chain.refresh_results()
            return
        selection=dict(collection=collection_id,image=copy.deepcopy(collection['items'][index]['reference']['image']))
        if not self.client.window.canvas.commit(lambda state:state['canvas_functions']['images'][key].update(selection=selection)):return
        self.errors[key]=''
        card=self.client.window.canvas.functions.cards.get(key)
        if card:card.panel.refresh()
        self.client.window.canvas.refresh_image_previews();self.client.window.canvas.results.refresh()
    def resolve(self,state,keys,directory,completed,done,failed,valid=lambda:True,collections=False):
        try: requested=queries(state,keys,completed)
        except ValueError as exc: failed(str(exc)); return
        if not requested: done({},{}); return
        if collections:
            for query in requested:query['collection']=True
        def load(row):
            if row.get('error') or not row.get('path'):raise ValueError(row.get('error','未收到節點圖片。'))
            cache_key=(str(directory),row['path'],row.get('mtime'),row.get('size'))
            if cache_key not in self.cache:self.cache[cache_key]=import_source(row['path'],directory)
            value=copy.deepcopy(self.cache[cache_key])
            value['reference']={k:copy.deepcopy(row.get(k)) for k in ('origin','prompt_id','workflow','node','image')}
            if row.get('origin')=='workflow':value['reference_origin']='imported_workflow'
            return value
        def receive(rows):
            if not valid():done({},{});return
            values={}; errors={}
            for query in requested:
                key=query['key']; row=rows.get(key,{})
                try:
                    if collections:
                        if not row.get('collection') or not isinstance(row.get('items'),list):raise ValueError(row.get('error') or '請更新 ComfyUI 擴充後重新連線，才能讀取完整圖片清單。')
                        if not 1<=len(row['items'])<=64:raise ValueError('圖片集合數量無效。')
                        values[key]=dict(collection=row['collection'],items=[load(item) for item in row['items']])
                    else:values[key]=load(row)
                except (ValueError,OSError) as exc: errors[key]=str(exc)
            while len(self.cache)>100:self.cache.pop(next(iter(self.cache)))
            done(values,errors)
        self.client.request('desktop/images',dict(queries=requested),done=receive,failed=failed)
    def poll(self):
        w=self.client.window
        if w.state.get('multi_output',{}).get('version',0)>=7:return
        if self.pending or not self.client.connected or not getattr(self.client,'images_supported',False) or w.closing:return
        bound={k:copy.deepcopy(self.scope(k)) for k,v in w.state.get('canvas_functions',{}).get('images',{}).items() if v.get('binding')}
        if not bound:return
        owner=self.identity(); state=copy.deepcopy({k:w.state.get(k,{}) for k in ('generation','canvas_functions')})
        self.pending=True; self.serial+=1; serial=self.serial
        def receive(values,errors):
            if serial!=self.serial:return
            self.pending=False
            if owner!=self.identity() or w.closing:return
            self.owner=owner
            for key,scope in bound.items():
                if self.scope(key)!=scope:continue
                value=values.get(key)
                if value and 'collection' in value:
                    self.collections[key]=(scope,value);self.values.pop(key,None)
                else:
                    self.collections.pop(key,None);self.values[key]=(scope,value)
                self.errors[key]=errors.get(key,'')
            canvas=getattr(w,'canvas',None)
            if canvas:
                for card in canvas.functions.cards.values():card.panel.refresh()
                canvas.refresh_image_previews()
                canvas.results.refresh()
        def failed(error):
            receive({},dict.fromkeys(bound,str(error)))
        # The live panel follows the latest result of this exact workflow/node.
        # A previous PCS receipt must not pin it forever after manual native runs.
        # Dispatch-time dependent reads still pass explicit prompt IDs to resolve.
        completed={}
        self.resolve(state,bound,w.store.directory,completed,receive,failed,valid=lambda:owner==self.identity() and not w.closing and all(self.scope(k)==scope for k,scope in bound.items()),collections=True)
