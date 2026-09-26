"""Portable composition documents; Qt is imported only when rasterizing."""
import copy
import hashlib
import json
import math
import re
import uuid
from collections import OrderedDict
from pathlib import Path

MAX_PIXELS=40_000_000


def document(width=1024,height=1024): return dict(version=1,width=width,height=height,background='#ffffff',layers=[])


def layer(kind,x=0,y=0,width=200,height=200,**values):
    result=dict(id=uuid.uuid4().hex,type=kind,name={'image':'圖片','rect':'矩形','ellipse':'橢圓','stroke':'筆畫','erase':'橡皮擦'}[kind],
                x=x,y=y,width=width,height=height,visible=True,locked=False,fill='#718caa',stroke='#222222',stroke_width=0)
    result.update(values); return result


def resize_layer(item,width,height):
    if item['type'] in ('stroke','erase'):
        item['points']=[[x*width/item['width'],y*height/item['height']] for x,y in item['points']]
    item.update(width=width,height=height)


def validate_image(value):
    if not isinstance(value,dict) or value.get('version')!=1: raise ValueError('構圖資料版本無效。')
    if any(type(value.get(k)) is not int or not 1<=value[k]<=16384 for k in ('width','height')) or value['width']*value['height']>MAX_PIXELS:
        raise ValueError('底圖尺寸須為 1–16384，最多四千萬像素。')
    def color(v): return isinstance(v,str) and re.fullmatch(r'#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?',v)
    if not color(value.get('background')): raise ValueError('背景顏色無效。')
    layers=value.get('layers')
    if not isinstance(layers,list) or len(layers)>1000: raise ValueError('構圖最多保存 1000 個圖層。')
    ids=set(); points=0
    for item in layers:
        if not isinstance(item,dict) or not isinstance(item.get('id'),str) or item['id'] in ids: raise ValueError('圖層 ID 缺少或重複。')
        ids.add(item['id'])
        if item.get('type') not in ('image','rect','ellipse','stroke','erase') or not isinstance(item.get('name'),str): raise ValueError('圖層類型無效。')
        if any(type(item.get(k)) is not bool for k in ('visible','locked')): raise ValueError('圖層顯示／鎖定狀態無效。')
        for k in ('x','y','width','height','stroke_width'):
            n=item.get(k)
            if type(n) not in (int,float) or not math.isfinite(n) or abs(n)>1000000: raise ValueError('圖層位置或尺寸無效。')
        if item['width']<=0 or item['height']<=0 or not 0<=item['stroke_width']<=4096: raise ValueError('圖層尺寸或筆寬無效。')
        if not color(item.get('fill')) or not color(item.get('stroke')): raise ValueError('圖層顏色無效。')
        if item['type']=='image':
            from .generation import validate_generation
            validate_generation(dict(mode='img2img',profiles=[],source=item.get('source'),chosen={}))
            if item.get('source') is None: raise ValueError('圖片圖層缺少來源。')
            crop=item.get('crop',[0,0,1,1])
            if not isinstance(crop,list) or len(crop)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in crop) or crop[2]<=crop[0] or crop[3]<=crop[1]: raise ValueError('裁切範圍無效。')
        if item['type'] in ('stroke','erase'):
            path=item.get('points'); points+=len(path) if isinstance(path,list) else 0
            if not isinstance(path,list) or not path or points>100000: raise ValueError('筆畫資料無效或過多。')
            if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1000000 for v in p) for p in path): raise ValueError('筆畫座標無效。')
    return value


class PreviewCache:
    def __init__(self,max_bytes=64*1024*1024): self.items=OrderedDict(); self.bytes=0; self.max_bytes=max_bytes
    def get(self,path,limit=2048):
        from PySide6.QtCore import QSize,Qt
        from PySide6.QtGui import QImageReader
        path=Path(path); stat=path.stat(); key=(str(path),stat.st_mtime_ns,stat.st_size,limit)
        if key in self.items: self.items.move_to_end(key); return self.items[key]
        reader=QImageReader(str(path)); reader.setAutoTransform(True); size=reader.size()
        if size.width()<=0 or size.height()<=0 or size.width()*size.height()>MAX_PIXELS: raise ValueError('圖片尺寸無效或超過四千萬像素。')
        if limit and max(size.width(),size.height())>limit:
            size.scale(QSize(limit,limit),Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size)
        image=reader.read()
        if image.isNull(): raise ValueError('無法讀取構圖圖片：'+path.name)
        size=image.sizeInBytes()
        if limit and size<=self.max_bytes:
            while self.items and self.bytes+size>self.max_bytes: _,old=self.items.popitem(last=False); self.bytes-=old.sizeInBytes()
            self.items[key]=image; self.bytes+=size
        return image


def source_path(directory,source,verify=False):
    root=Path(directory).resolve(); path=(root/source['relative']).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise ValueError('構圖圖片遺失：'+source['name'])
    if verify:
        if path.stat().st_size>25*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']: raise ValueError('構圖圖片已變更：'+source['name'])
    return path


def render_image(value,directory,cache=None,preview_limit=0,verify=True):
    from PySide6.QtCore import Qt,QPointF,QRectF
    from PySide6.QtGui import QImage,QPainter,QColor,QPen,QPainterPath
    validate_image(value); cache=cache or PreviewCache()
    factor=min(1,preview_limit/max(value['width'],value['height'])) if preview_limit else 1
    width=max(1,round(value['width']*factor)); height=max(1,round(value['height']*factor))
    content=QImage(width,height,QImage.Format.Format_ARGB32_Premultiplied); content.fill(Qt.GlobalColor.transparent)
    painter=QPainter(content); painter.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.SmoothPixmapTransform)
    painter.scale(factor,factor)
    try:
        for item in value['layers']:
            if not item['visible']: continue
            painter.save(); painter.translate(item['x'],item['y'])
            rect=QRectF(0,0,item['width'],item['height']); kind=item['type']
            if kind=='image':
                image=cache.get(source_path(directory,item['source'],verify),2048 if preview_limit else 0)
                left,top,right,bottom=item.get('crop',[0,0,1,1])
                painter.drawImage(rect,image,QRectF(left*image.width(),top*image.height(),(right-left)*image.width(),(bottom-top)*image.height()))
            elif kind in ('rect','ellipse'):
                painter.setBrush(QColor(item['fill'])); painter.setPen(QPen(QColor(item['stroke']),item['stroke_width']) if item['stroke_width'] else Qt.PenStyle.NoPen)
                painter.drawRect(rect) if kind=='rect' else painter.drawEllipse(rect)
            else:
                if kind=='erase': painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                painter.setPen(QPen(QColor(item['stroke']),max(.1,item['stroke_width']),Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin)); painter.setBrush(Qt.BrushStyle.NoBrush)
                path=QPainterPath(QPointF(*item['points'][0]))
                for point in item['points'][1:]: path.lineTo(QPointF(*point))
                if len(item['points'])==1: painter.drawPoint(QPointF(*item['points'][0]))
                else: painter.drawPath(path)
            painter.restore()
    finally: painter.end()
    output=QImage(width,height,QImage.Format.Format_ARGB32); output.fill(QColor(value['background']))
    painter=QPainter(output); painter.drawImage(0,0,content); painter.end(); return output


def canvas_document(state,key):
    """An incoming image is a referenced base layer, not an implicit connection out."""
    data=state['multi_output']; doc=copy.deepcopy(data['canvases'][key].get('image'))
    line=next((c for c in data['connections'] if c['kind']=='image' and c['destination']==key),None)
    if not line: return doc
    source=state.get('canvas_functions',{}).get('images',{}).get(line['source'],{}).get('source')
    if not source: raise ValueError('畫布連接的來源圖片尚未載入。')
    if doc is None: doc=document(source['width'],source['height'])
    scale=min(doc['width']/source['width'],doc['height']/source['height']); w=source['width']*scale; h=source['height']*scale
    base=layer('image',(doc['width']-w)/2,(doc['height']-h)/2,w,h,source=copy.deepcopy(source),locked=True,name='連接的來源圖片')
    base['id']='connected-base'; doc['layers'].insert(0,base); return doc


def freeze_source(state,directory,optional=False):
    """Only image paths leaving a participating Prompt can feed img2img."""
    from .multi_output import bound_texts
    from .generation import active_profile
    data=state['multi_output']
    if data['version']==1: lines=[c for c in data['connections'] if c['kind']=='image']
    else:
        outputs={b['output'] for b in bound_texts(state,active_profile(state))}
        lines=[c for c in data['connections'] if c['kind']=='image' and c['destination'] in outputs]
    if not lines:
        if optional: return None
        raise ValueError('圖生圖需要將圖片連到已接入執行操作的 Prompt 黄色輸入口，並保留文字連線。')
    sources=[freeze_image_source(state,directory,c['source']) for c in lines]
    if len({s['sha256'] for s in sources})!=1: raise ValueError('一次工作流只能使用一張來源圖片；已接入的 Prompt 圖片來源不同。')
    return sources[0]


def freeze_image_source(state,directory,key):
    data=state['multi_output']
    if key in data['canvases']:
        canvas=data['canvases'][key]; doc=canvas_document(state,key)
        if doc is None: raise ValueError('「'+canvas['name']+'」尚未設定底圖尺寸。')
        from PySide6.QtCore import QByteArray,QBuffer,QIODevice
        image=render_image(doc,directory); buffer=QBuffer(); buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        if not image.save(buffer,'PNG'): raise ValueError('底圖合成失敗。')
        raw=bytes(buffer.data()); digest=hashlib.sha256(raw).hexdigest(); relative='originals/generation/'+digest+'.png'
        path=Path(directory)/relative; path.parent.mkdir(parents=True,exist_ok=True)
        if not path.exists():
            with path.open('xb') as stream: stream.write(raw)
        elif hashlib.sha256(path.read_bytes()).hexdigest()!=digest: raise ValueError('底圖快取與內容不一致。')
        return dict(name=canvas['name']+'.png',sha256=digest,width=doc['width'],height=doc['height'],relative=relative)
    source=state.get('canvas_functions',{}).get('images',{}).get(key,{}).get('source')
    if not source: raise ValueError('已連接的圖片模組沒有圖片。')
    source_path(directory,source,True); return copy.deepcopy(source)
