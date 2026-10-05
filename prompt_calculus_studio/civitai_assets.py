"""0.81 installation contracts and durable download receipts (no Qt/UI reads)."""
import copy
import json
import os
import time
import uuid
import threading
from pathlib import Path
from .civitai import download_version, normalize_version, sha256_file, safe_filename

TYPE_TARGETS={'LORA':('LoRA','loras'),'LoCon':('LoRA','loras'),
              'Checkpoint':('CKPT','checkpoints'),'VAE':('VAE','vae'),
              'TextualInversion':('Embedding','embeddings'),'Upscaler':('Upscaler','upscale_models')}
LOCAL_TYPES=('LoRA','CKPT','Diffusion','VAE','Embedding','Upscaler','其他')
UNCLASSIFIED='未分類'

def categories(settings, kind=None):
    names=list(dict.fromkeys([UNCLASSIFIED,*settings.get('model_categories',[])]))
    scopes=settings.get('model_category_types',{})
    return [n for n in names if n==UNCLASSIFIED or not kind or not scopes.get(n) or kind in scopes[n]]

def category_for(row): return row.get('category','其他')

def apply_categories(catalog,settings,names,scopes,mapping):
    names=list(dict.fromkeys([UNCLASSIFIED,*names]))
    # Keep prior memberships even when scope is narrowed; do not reassign on a filter edit.
    with catalog.db:
        for record in catalog.rows('model',limit=20000):
            prior=category_for(record); value=mapping.get(prior,prior)
            record['category']=value if value in names else UNCLASSIFIED
            catalog.db.execute('UPDATE resources SET body=? WHERE id=?',
                (json.dumps(record,ensure_ascii=False),record['id']))
    settings['model_categories']=names
    settings['model_category_types']={name:list(scopes.get(name,[])) for name in names if name!=UNCLASSIFIED}

def suggested_target(root,source_type):
    kind,folder=TYPE_TARGETS.get(source_type,('其他',''))
    return kind,str(Path(root)/folder) if root and folder else ''

def make_plan(parent,version,file,target,filename,category,root,kind=None):
    if not isinstance(file,dict) or not file.get('downloadUrl'): raise ValueError('這個檔案沒有可用的下載入口。')
    selected={**copy.deepcopy(version),'files':[copy.deepcopy(file)]}
    source=normalize_version(selected,parent=parent)
    target=Path(target).expanduser().resolve()
    if not target.is_dir(): raise ValueError('請選擇已存在的模型安裝資料夾。')
    filename=safe_filename(filename)
    if (target/filename).exists(): raise ValueError('目標已有同名檔案，請更改檔名或安裝位置。')
    return dict(id=uuid.uuid4().hex,name=source['model_name'],version=selected,parent=copy.deepcopy(parent),
                source=source,filename=filename,target=str(target),root=str(Path(root).resolve()) if root else str(target),
                kind=kind or TYPE_TARGETS.get(source['model_type'],('其他',''))[0],category=category or UNCLASSIFIED,
                state='queued',created=time.time(),updated=time.time(),destination='comfyui',schema=1)

class DownloadReceipts:
    def __init__(self,directory):
        self.directory=Path(directory)/'civitai/downloads'; self.directory.mkdir(parents=True,exist_ok=True); self.lock=threading.RLock()
    def path(self,ident):
        if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('下載紀錄識別碼無效。')
        return self.directory/(ident+'.json')
    def save(self,record):
        record=copy.deepcopy(record); record['updated']=time.time()
        path=self.path(record['id']); pending=path.with_suffix('.tmp')
        with self.lock:
            pending.write_text(json.dumps(record,ensure_ascii=False),encoding='utf-8'); os.replace(pending,path)
        return record
    def get(self,ident):
        with self.lock:return json.loads(self.path(ident).read_text(encoding='utf-8'))
    def rows(self):
        values=[]
        for path in self.directory.glob('*.json'):
            try:
                with self.lock:values.append(json.loads(path.read_text(encoding='utf-8')))
            except (OSError,ValueError): continue
        return sorted(values,key=lambda r:r.get('created',0),reverse=True)
    def recover(self):
        for record in self.rows():
            if record.get('state') in ('queued','downloading','checking'):
                record.update(state='interrupted',error='上次下載未完成，請重新嘗試。'); self.save(record)
            elif record.get('state')=='registering':
                record.update(state='registration_failed',error='檔案已下載，請重新登記。'); self.save(record)

def perform_download(receipts,plan,token,cancel,progress=None):
    record=copy.deepcopy(plan); record.update(state='downloading',error=''); receipts.save(record)
    try:
        result=download_version(record['version'],record['target'],token,cancel,progress,
                                parent=record['parent'],filename=record['filename'])
        # This receipt survives a late cancellation or a crash before the UI callback.
        stat=Path(result['path']).stat()
        record.update(state='downloaded',result=result,size=stat.st_size,mtime=stat.st_mtime_ns)
        receipts.save(record); return record
    except Exception as exc:
        record.update(state='cancelled' if cancel.is_set() else 'failed',error=str(exc)); receipts.save(record)
        raise

def check_download(record,cancel=None):
    result=record.get('result') or {}; path=Path(result.get('path',''))
    if not path.is_file(): raise ValueError('已下載的檔案不存在，請檢查位置。')
    expected={'size':record['size'],'mtime':record['mtime']}
    digest=sha256_file(path,cancel,expected=expected)
    if digest!=result.get('sha256'): raise ValueError('已下載的檔案內容已改變，未重新登記。')
    return record

def register_download(catalog,record,thumb=''):
    result=record['result']; path=Path(result['path']).resolve(strict=True); stat=path.stat()
    if (stat.st_size,stat.st_mtime_ns)!=(record['size'],record['mtime']): raise ValueError('檔案已變更，請重新檢查。')
    ident=uuid.uuid5(uuid.NAMESPACE_URL,str(path).casefold()).hex
    previous=catalog.get(ident) or {}; source=result['source']; root=Path(record['root'])
    row={**previous,'id':ident,'root':str(root),'path':str(path),
         'relative':str(path.relative_to(root)) if path.is_relative_to(root) else str(path),
         'kind':previous.get('kind',record['kind']),'size':stat.st_size,'mtime':stat.st_mtime_ns,'missing':False,
         'sha256':result['sha256'],'hash_size':stat.st_size,'hash_mtime':stat.st_mtime_ns,
         'civitai':source,'source_type':source['model_type'],'base_model':source['base_model'],'creator':source['creator'],
         'civitai_model_id':source['model_id'],'civitai_version_id':source['version_id'],
         'civitai_status':'matched' if result['verification']=='verified' else 'unverified',
         'verification':result['verification'],'installed_by':'civitai','download_id':record['id']}
    row.setdefault('name',source['model_name'] or path.stem); row.setdefault('category',record['category'])
    row.setdefault('trigger',', '.join(source['trained_words'])); row.setdefault('notes',''); row.setdefault('url','')
    if thumb and 'thumb' not in previous: row['thumb']=thumb
    with catalog.db:
        catalog.db.execute('INSERT OR REPLACE INTO resources VALUES (?,?,?,?,?)',
            (ident,'model',str(root),row['name'],json.dumps(row,ensure_ascii=False)))
        catalog.db.execute('INSERT OR REPLACE INTO asset_sources VALUES (?,?,?,?,?,?)',
            (result['sha256'],'civitai',source['model_id'],source['version_id'],json.dumps(source,ensure_ascii=False),time.time()))
    return row

def installed_version(catalog,version_id,file_id=None):
    rows=catalog.db.execute("SELECT body FROM resources WHERE kind='model' AND json_extract(body,'$.civitai_version_id')=?",(version_id,))
    for value, in rows:
        record=json.loads(value); source=record.get('civitai') or {}
        if file_id is not None and source.get('file_id')!=file_id: continue
        if record.get('missing') or record.get('civitai_status') not in ('matched','unverified'): continue
        try: stat=Path(record['path']).stat()
        except OSError: continue
        if (stat.st_size,stat.st_mtime_ns)==(record.get('size'),record.get('mtime')): return record
    return None
