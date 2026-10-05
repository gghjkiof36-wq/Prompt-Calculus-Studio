"""Separate, verified share exports. Originals and gallery records are never changed."""
import copy
import hashlib
import json
import os
import re
import string
import tempfile
import uuid
from datetime import datetime
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImage, QImageReader, QImageWriter, QPainter, QColorSpace, QTransform
from . import clean_metadata as metadata

EXTENSIONS={'.png','.jpg','.jpeg','.webp','.bmp'}
DEFAULTS=dict(mode='all',format='keep',quality=90,resize='keep',long_side=2048,
              percent=50,width=1024,height=1024,aspect=True,structure=True,
              pattern='{name}',collision='rename',sha256=False)
PRESETS={name:dict(DEFAULTS) for name in ('Public Clean','Discord','Pixiv','Patreon','X','Instagram')}
PRESETS['Archive']=dict(DEFAULTS,mode='generation')
MAX_PIXELS=40_000_000

def fingerprint(path):
    stat=Path(path).stat()
    return [stat.st_dev,stat.st_ino,stat.st_size,stat.st_mtime_ns]

def key(path): return os.path.normcase(str(Path(path).resolve()))

def linked(path):
    return any(p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()) for p in [path,*path.parents])

def check_cancel(cancel):
    if cancel and cancel.is_set(): raise InterruptedError('已取消匯出。')

def options(values):
    result=dict(DEFAULTS); result.update(values)
    for name,choices in dict(mode=('all','generation'),format=('keep','png','jpeg','webp'),
            resize=('keep','long','percent','exact'),collision=('skip','overwrite','rename')).items():
        if result[name] not in choices: raise ValueError('匯出選項無效：'+name)
    for name,low,high in [('quality',1,100),('long_side',1,16000),('percent',1,400),('width',1,16000),('height',1,16000)]:
        if type(result[name]) is not int or not low<=result[name]<=high: raise ValueError('匯出數值超出範圍：'+name)
    for name in ('aspect','structure','sha256'):
        if type(result[name]) is not bool: raise ValueError('匯出選項無效：'+name)
    filename(result['pattern'],Path('image.png'),1,'2026-01-01','png')
    return result

def filename(pattern,path,index,date,fmt):
    if not isinstance(pattern,str) or not pattern or len(pattern)>160: raise ValueError('檔名規則不可空白，最多 160 字。')
    for literal,field,spec,conversion in string.Formatter().parse(pattern):
        if field is not None and (field not in ('name','index','date','folder') or conversion or
                (spec and not (field=='index' and re.fullmatch(r'0?[1-8]?d',spec)))):
            raise ValueError('檔名可使用 {name}、{index:04d}、{date}、{folder}。')
    value=pattern.format(name=path.stem,index=index,date=date,folder=path.parent.name)
    if not value.strip() or value.endswith((' ','.')) or re.search(r'[<>:"/\\|?*\x00-\x1f]',value) or value in ('.','..'):
        raise ValueError('檔名包含不允許的字元或路徑。')
    if re.fullmatch(r'(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',value,re.I): raise ValueError('檔名是 Windows 保留名稱。')
    extension=path.suffix if fmt=={'.png':'png','.jpg':'jpeg','.jpeg':'jpeg','.webp':'webp','.bmp':'bmp'}.get(path.suffix.lower()) else '.'+('jpg' if fmt=='jpeg' else fmt)
    return value+extension

def scan(inputs,recursive=False,cancel=None):
    candidates=[]; seen=set()
    for raw in inputs:
        path=Path(raw).absolute()
        if path.is_dir():
            if linked(path): raise ValueError('請直接選擇實際資料夾，不使用連結資料夾。')
            for directory,dirs,files in os.walk(path,followlinks=False):
                check_cancel(cancel)
                dirs[:]=sorted(d for d in dirs if not linked(Path(directory)/d)) if recursive else []
                for name in sorted(files,key=str.casefold):
                    child=Path(directory)/name
                    if child.suffix.lower() in EXTENSIONS and not linked(child): candidates.append((child,child.relative_to(path)))
                if len(candidates)>10000: raise ValueError('單次最多 10,000 張，請縮小選取範圍。')
        else: candidates.append((path,Path(path.name)))
    if len(candidates)>10000: raise ValueError('單次最多 10,000 張。')
    rows=[]
    for path,relative in candidates:
        check_cancel(cancel)
        if key(path) in seen: continue
        seen.add(key(path)); record=dict(source=str(path.resolve()),relative=str(relative),error='')
        try:
            record['fingerprint']=fingerprint(path)
            info=metadata.inspect(path,expand=False); reader=QImageReader(str(path)); size=reader.size()
            if not size.isValid() or size.width()*size.height()>MAX_PIXELS: raise ValueError('無法讀取尺寸，或超過 4,000 萬像素。')
            record.update(format=info['format'],width=size.width(),height=size.height(),info=info)
        except Exception as exc: record['error']=str(exc)
        rows.append(record)
    return rows

def dimensions(record,opts):
    width,height=record['width'],record['height']
    if record['info'].get('orientation',1)>=5: width,height=height,width
    if opts['resize']=='long':
        scale=min(1,opts['long_side']/max(width,height)); width,height=round(width*scale),round(height*scale)
    elif opts['resize']=='percent': width,height=round(width*opts['percent']/100),round(height*opts['percent']/100)
    elif opts['resize']=='exact':
        if opts['aspect']:
            size=QSize(width,height); size.scale(QSize(opts['width'],opts['height']),Qt.AspectRatioMode.KeepAspectRatio); width,height=size.width(),size.height()
        else: width,height=opts['width'],opts['height']
    width,height=max(1,width),max(1,height)
    if width*height>MAX_PIXELS: raise ValueError('輸出尺寸超過 4,000 萬像素。')
    return width,height

def protect_target(path,protected,identities,forbidden):
    if linked(path): raise ValueError('輸出路徑包含連結，請選擇實際資料夾。')
    resolved=path.resolve()
    if any(resolved.is_relative_to(Path(root).resolve()) for root in forbidden): raise ValueError('請選擇應用資料庫以外的匯出位置。')
    if key(path) in protected: raise ValueError('此位置是原圖，不能覆蓋母檔。')
    if path.exists():
        if not path.is_file(): raise ValueError('目標是資料夾，不能覆蓋。')
        if tuple(fingerprint(path)[:2]) in identities: raise ValueError('此檔案與原圖共用資料，不能覆蓋母檔。')

def prepare(rows,values,directory,protected_paths=(),forbidden=(),cancel=None):
    opts=options(values); root=Path(directory).absolute()
    if not directory.strip() or not root.is_dir() or linked(root): raise ValueError('請選擇可使用的實際輸出資料夾。')
    root=root.resolve(); sources=[r['source'] for r in rows]+list(protected_paths)
    protected={key(p) for p in sources}; identities=set()
    for source in sources:
        try: identities.add(tuple(fingerprint(source)[:2]))
        except OSError: pass
    plan=dict(id=uuid.uuid4().hex,time=datetime.now().astimezone().isoformat(timespec='seconds'),
              options=opts,root=str(root),protected=sorted(protected),identities=[list(v) for v in identities],
              forbidden=[str(p) for p in forbidden],entries=[])
    date=plan['time'][:10]; reserved=set()
    for index,record in enumerate(rows,1):
        check_cancel(cancel); entry=copy.deepcopy(record); entry.update(target='',action='write',status='待匯出')
        try:
            if entry['error']: raise ValueError(entry['error'])
            if fingerprint(entry['source'])!=entry['fingerprint']: raise ValueError('原圖已變更，請重新掃描。')
            fmt=entry['format'] if opts['format']=='keep' else opts['format']
            entry['output_format']=fmt; entry['output_size']=dimensions(entry,opts)
            parent=root/Path(entry['relative']).parent if opts['structure'] else root
            if not parent.resolve().is_relative_to(root): raise ValueError('原始相對路徑無效。')
            target=parent/filename(opts['pattern'],Path(entry['source']),index,date,fmt)
            if opts['collision']=='rename':
                stem,suffix=target.stem,target.suffix; n=1
                while target.exists() or key(target) in reserved or key(target) in protected:
                    target=parent/f'{stem} ({n}){suffix}'; n+=1
                    if n>100000: raise ValueError('同名檔案過多。')
            protect_target(target,protected,identities,forbidden)
            entry['target']=str(target)
            if target.exists() or key(target) in reserved:
                if opts['collision']=='skip': entry.update(action='skip',status='跳過同名檔')
                elif key(target) in reserved: raise ValueError('本次匯出有重複檔名，請使用自動重新命名或加入序號。')
                else: entry['status']='覆蓋既有匯出檔'
            entry['target_fingerprint']=fingerprint(target) if target.exists() else None
            reserved.add(key(target))
            entry['color_transform']=opts['mode']=='all' and (entry['info']['icc'] or any(v in entry['info']['metadata'] for v in ('gAMA','cHRM','cICP','mDCv','cLLi')))
            transform=opts['format']!='keep' or opts['resize']!='keep' or entry['info']['orientation']!=1 or entry['color_transform'] or fmt=='bmp'
            entry['transform']=transform
            if transform and entry['info']['animated']: raise ValueError('動畫可保持原格式清理；目前不支援動畫轉檔、縮放或色彩轉換。')
            entry['note']='透明區域將以白色輸出' if fmt in ('jpeg','bmp') and transform else ''
        except Exception as exc: entry.update(action='fail',status='無法匯出',error=str(exc))
        plan['entries'].append(entry)
    return plan

def decode(path):
    reader=QImageReader(str(path)); reader.setAllocationLimit(256); reader.setAutoTransform(False)
    size=reader.size()
    if not size.isValid() or size.width()*size.height()>MAX_PIXELS: raise ValueError('圖片尺寸無效或超過記憶體限制。')
    image=reader.read()
    if image.isNull(): raise ValueError('圖片解碼失敗：'+reader.errorString())
    return image

def encode(entry,opts,destination):
    image=decode(entry['source']); orient=entry['info']['orientation']
    if orient in (2,5,7): image=image.mirrored(True,False)
    elif orient==4: image=image.mirrored(False,True)
    angle={3:180,5:270,6:90,7:90,8:270}.get(orient,0)
    if angle: image=image.transformed(QTransform().rotate(angle))
    if entry.get('color_transform'):
        if not image.colorSpace().isValid(): raise ValueError('無法辨識色彩描述，未匯出可能變色的圖片。')
        image=image.convertedToColorSpace(QColorSpace(QColorSpace.NamedColorSpace.SRgb))
    width,height=entry['output_size']
    if image.width()!=width or image.height()!=height:
        image=image.scaled(width,height,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
    fmt=entry['output_format']
    if fmt in ('jpeg','bmp'):
        canvas=QImage(image.size(),QImage.Format.Format_RGB32); canvas.fill(Qt.GlobalColor.white)
        painter=QPainter(canvas); painter.drawImage(0,0,image); painter.end(); canvas.setColorSpace(image.colorSpace()); image=canvas
    writer=QImageWriter(str(destination),fmt.encode())
    if fmt in ('jpeg','webp'): writer.setQuality(opts['quality'])
    if not writer.write(image): raise ValueError('圖片儲存失敗：'+writer.errorString())
    if fmt=='bmp':
        # Fixed 24-bit BITMAPINFOHEADER: clear resolution and colour-table hints.
        with Path(destination).open('r+b') as stream:
            stream.seek(38); stream.write(b'\0'*16)

def sha256(path,cancel=None):
    digest=hashlib.sha256()
    with Path(path).open('rb') as stream:
        while block:=stream.read(1024*1024): check_cancel(cancel); digest.update(block)
    return digest.hexdigest()

def temporary(parent):
    handle,name=tempfile.mkstemp(prefix='.prompt-studio-export-',suffix='.tmp',dir=parent); os.close(handle); return Path(name)

def execute(plan,cancel=None,progress=None):
    opts=plan['options']; protected=set(plan['protected']); identities={tuple(v) for v in plan['identities']}; results=[]
    for entry in plan['entries']:
        result={k:entry.get(k) for k in ('source','target','error','output_size','output_format')}; result['status']='Failed'
        if result.get('output_size'): result['output_size']=list(result['output_size'])
        staged=encoded=None
        try:
            check_cancel(cancel)
            if entry['action']=='fail': raise ValueError(entry['error'])
            if entry['action']=='skip': result['status']='Skipped'; continue
            source=Path(entry['source']); target=Path(entry['target'])
            if fingerprint(source)!=entry['fingerprint']: raise ValueError('原圖在預覽後已變更，請重新掃描。')
            protect_target(target,protected,identities,plan['forbidden'])
            actual=fingerprint(target) if target.exists() else None
            if actual!=entry['target_fingerprint']: raise ValueError('輸出位置在預覽後已變更，請重新預覽。')
            target.parent.mkdir(parents=True,exist_ok=True); staged=temporary(target.parent)
            if opts['sha256']: result['source_sha256']=sha256(source,cancel)
            if entry['transform']:
                encoded=temporary(target.parent); encode(entry,opts,encoded)
                if entry['output_format']=='bmp': os.replace(encoded,staged); encoded=None
                else: metadata.sanitize(encoded,staged,opts['mode'])
            else: metadata.sanitize(source,staged,opts['mode'])
            check_cancel(cancel); retained=metadata.verify(staged,opts['mode']); image=decode(staged)
            if [image.width(),image.height()]!=list(entry['output_size']): raise ValueError('匯出尺寸與預覽不符。')
            del image
            if fingerprint(source)!=entry['fingerprint']: raise ValueError('匯出期間原圖已變更，未交付輸出。')
            protect_target(target,protected,identities,plan['forbidden'])
            if (fingerprint(target) if target.exists() else None)!=entry['target_fingerprint']:
                raise ValueError('輸出位置已變更，請重新預覽。')
            if opts['sha256']: result['output_sha256']=sha256(staged,cancel)
            check_cancel(cancel)
            if opts['collision']=='overwrite' and target.exists(): os.replace(staged,target)
            else:
                # Link is an atomic no-replace publication, including on POSIX.
                # Remove the temporary name immediately; target has its own new inode.
                try: os.link(staged,target); staged.unlink()
                except OSError:
                    if os.name!='nt' or target.exists(): raise
                    os.rename(staged,target)
            staged=None; metadata.verify(target,opts['mode']); image=decode(target); del image
            result.update(status='Passed',error='',retained_metadata=retained)
        except InterruptedError as exc: result.update(status='Cancelled',error=str(exc))
        except Exception as exc: result['error']=str(exc)
        finally:
            for path in (staged,encoded):
                if path and path.exists(): path.unlink()
            result['time']=datetime.now().astimezone().isoformat(timespec='seconds'); results.append(result)
            if progress: progress(len(results),len(plan['entries']))
        if result['status']=='Cancelled': break
    return dict(id=plan['id'],time=plan['time'],finished=datetime.now().astimezone().isoformat(timespec='seconds'),
                preset=plan.get('preset','自訂'),options=opts,root=plan['root'],results=results,
                remaining=len(plan['entries'])-len(results))

class ExportRecords:
    """Audit data stays in SQLite, never in image metadata or the gallery catalog."""
    def __init__(self,store):
        self.db=store.db
        with self.db:
            self.db.execute('CREATE TABLE IF NOT EXISTS clean_exports (id TEXT PRIMARY KEY, time TEXT, body TEXT)')
            self.db.execute('CREATE TABLE IF NOT EXISTS clean_presets (name TEXT PRIMARY KEY, body TEXT)')

    def save_run(self,run):
        with self.db: self.db.execute('INSERT INTO clean_exports VALUES (?,?,?)',(run['id'],run['time'],json.dumps(run,ensure_ascii=False)))

    def history(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM clean_exports ORDER BY time DESC LIMIT 100')]

    def presets(self):
        values=copy.deepcopy(PRESETS)
        for name,body in self.db.execute('SELECT name,body FROM clean_presets ORDER BY name'): values[name]=options(json.loads(body))
        return values

    def save_preset(self,name,values):
        if not name.strip() or len(name)>80: raise ValueError('預設名稱需為 1～80 字。')
        with self.db: self.db.execute('INSERT OR REPLACE INTO clean_presets VALUES (?,?)',(name.strip(),json.dumps(options(values))))
