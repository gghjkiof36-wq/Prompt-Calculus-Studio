"""Portable composition data and source paths shared with ComfyUI; no Qt dependency."""
import copy
import hashlib
import math
import re
import uuid
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
            from .generation import validate_source
            validate_source(item.get('source'))
            if item.get('source') is None: raise ValueError('圖片圖層缺少來源。')
            crop=item.get('crop',[0,0,1,1])
            if not isinstance(crop,list) or len(crop)!=4 or any(type(v) not in (int,float) or not math.isfinite(v) or not 0<=v<=1 for v in crop) or crop[2]<=crop[0] or crop[3]<=crop[1]: raise ValueError('裁切範圍無效。')
        if item['type'] in ('stroke','erase'):
            path=item.get('points'); points+=len(path) if isinstance(path,list) else 0
            if not isinstance(path,list) or not path or points>100000: raise ValueError('筆畫資料無效或過多。')
            if any(not isinstance(p,list) or len(p)!=2 or any(type(v) not in (int,float) or not math.isfinite(v) or abs(v)>1000000 for v in p) for p in path): raise ValueError('筆畫座標無效。')
    return value


def source_path(directory,source,verify=False):
    root=Path(directory).resolve(); path=(root/source['relative']).resolve()
    if not path.is_relative_to(root) or not path.is_file(): raise ValueError('構圖圖片遺失：'+source['name'])
    if verify:
        if path.stat().st_size>25*1024*1024 or hashlib.sha256(path.read_bytes()).hexdigest()!=source['sha256']: raise ValueError('構圖圖片已變更：'+source['name'])
    return path


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
