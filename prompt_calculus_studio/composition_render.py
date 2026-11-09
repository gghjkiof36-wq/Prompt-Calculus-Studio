"""Desktop composition rasterization and frozen image sources; not shared with ComfyUI."""
import copy
import hashlib
from collections import OrderedDict
from pathlib import Path

from .composition_image import MAX_PIXELS,validate_image,source_path,canvas_document


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


def freeze_source(state,directory,optional=False):
    """Only image paths leaving a participating Prompt can feed img2img."""
    from .multi_output import bound_texts
    from .generation import active_profile
    data=state['multi_output']
    if data['version']==1: lines=[c for c in data['connections'] if c['kind']=='image']
    else:
        from .workflow_flow import execution_profiles
        profiles=execution_profiles(state) if data['version']>=4 else [active_profile(state)]
        outputs={b['output'] for profile in profiles if profile for b in bound_texts(state,profile)}
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
