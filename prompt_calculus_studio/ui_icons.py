"""Small, font-independent line icons for PCS navigation and actions.

Use ``icon(name, color=tokens['text'], size=20)`` when constructing a themed
control. No installed font, network asset, resource file or cache is required.
"""
from struct import pack
from collections import OrderedDict

from PySide6.QtCore import QPointF, QRectF, QResource, QSize, Qt
from PySide6.QtGui import QColor, QIcon, QIconEngine, QPainter, QPainterPath, QPalette, QPen, QPixmap
from PySide6.QtWidgets import QApplication


ALIASES = {
    'arrow-left':'back', 'refresh':'update', 'refresh-cw':'update',
    'sliders':'settings', 'sliders-horizontal':'settings', 'x':'close',
    'circle-check':'check-circle', 'check-circle-2':'check-circle',
    'alert-circle':'alert', 'triangle-alert':'alert', 'external_link':'external-link',
    'image':'media', 'images':'media', 'nodes':'workflow', 'compass':'explore',
    'loader':'update', 'upload':'export', 'undo':'back', 'link':'external-link',
}
ICON_NAMES = frozenset({
    'home','canvas','media','explore','export','settings','back','forward','search','update',
    'package','download','check','clock','info','close','play','pause','stop','lock','pin','pin-filled','menu','pencil',
    'workflow','history','external-link','alert','server','check-circle',
    'folder','plus','minus','copy','trash','caret-down','chevron-down','chevron-up','chevron-left','chevron-right','grid','list','more-horizontal','fold-all','unfold-all',
    'panel-left','type','file-text','list-ordered','arrow-down-up','arrow-up','arrow-down','shuffle','image-single','scan-eye','link-2','maximize','grip-vertical',
    'text-bold','text-italic','text-underline','text-strike','text-color','highlight',
    'heading','quote','align','indent','outdent','eraser','edit-undo','edit-redo','task','book-open','source-code','tag','bookmark',
})
MODE_ICONS={'fixed':'pin','increment':'arrow-up','decrement':'arrow-down','random':'shuffle'}
_STYLE_RESOURCES = {}


def stylesheet_icon_paths(tokens):
    """Supply palette-colored QSS glyphs from an in-memory Qt resource.

    Qt stylesheets cannot use QIconEngine or data URLs. Keeping these small SVGs
    in a version-1, uncompressed RCC container avoids writing into an installed
    package or relying on a temporary asset directory. The bytes must remain
    alive as long as Qt uses the registered resource.
    """
    root='/pcs-visual-icons/'+tokens['secondary'][1:]+tokens['on_accent'][1:]
    if root not in _STYLE_RESOURCES:
        def name_hash(name):
            value=0
            for char in name:
                value=(value<<4)+ord(char)
                value^=(value&0xf0000000)>>23
                value&=0x0fffffff
            return value
        files={}
        for name,path,color in (
            ('down','m4 6 4 4 4-4',tokens['secondary']),
            ('up','m4 10 4-4 4 4',tokens['secondary']),
            ('right','m6 4 4 4-4 4',tokens['secondary']),
            ('check','m4 8 3 3 5-6',tokens['on_accent']),
        ):
            files[name+'.svg']=(f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>').encode('utf-8')
        files['caret.svg']=(f'<svg xmlns="http://www.w3.org/2000/svg" width="6" height="4" viewBox="0 0 6 4"><path d="M0 0h6L3 4z" fill="{tokens["secondary"]}"/></svg>').encode('utf-8')
        names=bytearray(); data=bytearray(); records=[]
        for name in sorted(files,key=lambda value:(name_hash(value),value)):
            name_offset=len(names); data_offset=len(data)
            names.extend(pack('>HI',len(name),name_hash(name))+name.encode('utf-16-be'))
            data.extend(pack('>I',len(files[name]))+files[name])
            records.append(pack('>IHHHI',name_offset,0,0,1,data_offset))
        tree=pack('>IHII',0,2,len(records),1)+b''.join(records)
        resource=b'qres'+pack('>IIII',1,20+len(data)+len(names),20,20+len(data))+data+names+tree
        resource=bytes(resource)
        if not QResource.registerResourceData(resource,root):
            raise RuntimeError('Could not register the PCS control icons.')
        _STYLE_RESOURCES[root]=resource
    return {name:':'+root+'/'+name+'.svg' for name in ('down','up','right','check','caret')}


def _draw(painter, name, filled=False):
    if name=='forward':
        painter.translate(24,0);painter.scale(-1,1);name='back'
    def line(x1,y1,x2,y2):
        painter.drawLine(QPointF(x1,y1),QPointF(x2,y2))

    def poly(*points, closed=False):
        path=QPainterPath(QPointF(*points[0]))
        for point in points[1:]: path.lineTo(*point)
        if closed: path.closeSubpath()
        painter.drawPath(path)

    def rect(x,y,w,h,radius=1.4):
        painter.drawRoundedRect(QRectF(x,y,w,h),radius,radius)

    def circle(x,y,radius):
        painter.drawEllipse(QPointF(x,y),radius,radius)

    if filled and name in ('home', 'media', 'explore', 'settings'):
        painter.setBrush(painter.pen().color())
        if name == 'home':
            poly((3,11),(12,3),(21,11),(19,11),(19,21),(14,21),(14,15),
                 (10,15),(10,21),(5,21),(5,11),closed=True)
            return
        if name == 'media':
            shape=QPainterPath();shape.addRoundedRect(QRectF(6,3,15,15),2,2)
            holes=QPainterPath();holes.addEllipse(QPointF(16.5,7.5),1.25,1.25)
            holes.moveTo(7,16);holes.lineTo(11,10);holes.lineTo(14,13)
            holes.lineTo(16,11);holes.lineTo(20,16);holes.closeSubpath()
            painter.fillPath(shape.subtracted(holes),painter.pen().color())
            painter.setBrush(Qt.BrushStyle.NoBrush)
            poly((3,7),(3,21),(17,21))
            return
        if name == 'explore':
            shape=QPainterPath();shape.addEllipse(QPointF(12,12),9,9)
            hole=QPainterPath(QPointF(16,8));hole.lineTo(14,14);hole.lineTo(8,16);hole.lineTo(10,10);hole.closeSubpath()
            painter.fillPath(shape.subtracted(hole),painter.pen().color())
            return

    if name == 'caret-down':
        painter.setBrush(painter.pen().color());painter.setPen(Qt.PenStyle.NoPen)
        poly((7,10),(17,10),(12,16),closed=True)
    elif name in ('edit-undo', 'edit-redo'):
        if name == 'edit-redo':painter.translate(24,0);painter.scale(-1,1)
        poly((8,4),(3,9),(8,14))
        path=QPainterPath(QPointF(3,9));path.lineTo(14,9);path.cubicTo(23,9,23,20,14,20);painter.drawPath(path)
    elif name == 'text-bold':
        line(6,3,6,21)
        path=QPainterPath(QPointF(6,3));path.lineTo(12,3);path.cubicTo(20,3,20,11,12,11);path.lineTo(6,11)
        path.moveTo(6,11);path.lineTo(13,11);path.cubicTo(22,11,22,21,13,21);path.lineTo(6,21);painter.drawPath(path)
    elif name == 'text-italic':
        line(10,3,20,3);line(4,21,14,21);line(15,3,9,21)
    elif name == 'text-underline':
        path=QPainterPath(QPointF(6,3));path.lineTo(6,12);path.cubicTo(6,20,18,20,18,12);path.lineTo(18,3);painter.drawPath(path);line(4,22,20,22)
    elif name == 'text-strike':
        path=QPainterPath(QPointF(18,5));path.cubicTo(7,-1,1,8,10,11);path.moveTo(14,13);path.cubicTo(24,17,15,25,5,19);painter.drawPath(path);line(3,12,21,12)
    elif name == 'heading':
        line(4,4,4,20);line(14,4,14,20);line(4,12,14,12);poly((18,16),(21,19),(24,16))
    elif name == 'text-color':
        poly((5,18),(12,3),(19,18));line(8,12,16,12);line(3,22,21,22)
    elif name in ('highlight', 'eraser'):
        poly((3,15),(14,4),(21,11),(10,22),(7,22),(3,18),closed=True);line(8,10,15,17)
        if name == 'highlight':line(2,23,21,23)
    elif name == 'quote':
        for x in (3,14):rect(x,5,7,7,.5);poly((x+7,12),(x+5,18),(x+2,20))
    elif name == 'align':
        for y,width in ((4,18),(9,12),(14,18),(19,12)):line(12-width/2,y,12+width/2,y)
    elif name in ('indent','outdent'):
        for y in (4,9,14,19):line(11,y,22,y)
        poly((2,8),(6,12),(2,16)) if name == 'indent' else poly((6,8),(2,12),(6,16))
    elif name == 'task':
        rect(3,3,18,18,2);poly((7,12),(10,15),(17,8))
    elif name == 'unfold-all':
        poly((7,8),(12,3),(17,8)); poly((7,16),(12,21),(17,16))
    elif name == 'fold-all':
        poly((7,3),(12,8),(17,3)); poly((7,21),(12,16),(17,21))
    elif name == 'home':
        poly((3,11),(12,3),(21,11));poly((5,10),(5,21),(10,21),(10,15),(14,15),(14,21),(19,21),(19,10))
    elif name in ('canvas','workflow'):
        rect(3,3,6,6); rect(15,15,6,6); rect(3,15,6,6)
        poly((6,9),(6,12),(18,12),(18,15)); line(6,12,6,15)
    elif name == 'panel-left':
        rect(3,3,6,18,1);rect(13,3,8,7,1);rect(13,14,8,7,1)
    elif name == 'tag':
        poly((3,4),(11,4),(21,14),(14,21),(3,10),closed=True);circle(7,8,1)
    elif name == 'type':
        poly((4,6),(4,3),(20,3),(20,6));line(12,3,12,21);line(8,21,16,21)
    elif name == 'file-text':
        poly((5,3),(14,3),(20,9),(20,21),(5,21),closed=True)
        poly((14,3),(14,9),(20,9));line(8,13,16,13);line(8,17,16,17)
    elif name == 'list-ordered':
        poly((3,3),(4,2),(4,7));line(2.5,7,5.5,7)
        poly((2,12),(5,12),(5,14),(2,17),(5,17))
        for y in (4,12,20):line(10,y,21,y)
    elif name == 'arrow-down-up':
        line(8,4,8,20);poly((4,8),(8,4),(12,8))
        line(16,4,16,20);poly((12,16),(16,20),(20,16))
    elif name in ('arrow-up','arrow-down'):
        line(12,4,12,20)
        if name=='arrow-up':poly((6,10),(12,4),(18,10))
        else:poly((6,14),(12,20),(18,14))
    elif name=='shuffle':
        poly((3,5),(6,5),(18,19),(21,19));poly((17,15),(21,19),(17,23))
        poly((3,19),(6,19),(10,14));poly((14,10),(18,5),(21,5));poly((17,1),(21,5),(17,9))
    elif name == 'link-2':
        painter.drawArc(QRectF(2,7,12,10),90*16,180*16)
        painter.drawArc(QRectF(10,7,12,10),-90*16,180*16);line(8,12,16,12)
    elif name == 'maximize':
        for sx,sy in ((1,1),(-1,1),(1,-1),(-1,-1)):
            poly((12+sx*4,12+sy*9),(12+sx*9,12+sy*9),(12+sx*9,12+sy*4))
    elif name == 'scan-eye':
        for sx,sy in ((1,1),(-1,1),(1,-1),(-1,-1)):
            poly((12+sx*5,12+sy*10),(12+sx*10,12+sy*10),(12+sx*10,12+sy*5))
        path=QPainterPath(QPointF(3,12));path.quadTo(12,2,21,12);path.quadTo(12,22,3,12);painter.drawPath(path);circle(12,12,3)
    elif name == 'image-single':
        rect(3,3,18,18,2);circle(8,8,1.6);poly((3,18),(10,11),(14,15),(17,12),(21,16))
    elif name == 'media':
        rect(6,3,15,15,2); poly((3,7),(3,21),(17,21)); circle(16.5,7.5,1.2)
        poly((7,15),(11,10),(14,13),(16,11),(20,15))
    elif name == 'explore':
        circle(12,12,9); poly((16,8),(14,14),(8,16),(10,10),closed=True)
    elif name in ('export','download'):
        poly((3,15),(3,20),(21,20),(21,15))
        if name == 'download': line(12,3,12,15); poly((7,10),(12,15),(17,10))
        else: line(12,15,12,3); poly((7,8),(12,3),(17,8))
    elif name == 'settings':
        for y,left,right in ((5,8,11),(12,15,18),(19,6,9)):
            line(3,y,left,y); line(right,y,21,y); rect(left,y-2,3,4,.7)
    elif name == 'back':
        line(4,12,21,12); poly((10,5),(3,12),(10,19))
    elif name == 'search':
        circle(10.5,10.5,6.7); line(15.4,15.4,21,21)
    elif name == 'update':
        painter.drawArc(QRectF(4,4,16,16),35*16,250*16)
        poly((19.8,3.8),(20.3,9.5),(14.8,8.5))
    elif name == 'package':
        poly((3.5,7),(12,2.5),(20.5,7),(20.5,17),(12,21.5),(3.5,17),closed=True)
        poly((3.5,7),(12,11.5),(20.5,7)); line(12,11.5,12,21.5)
        line(7.7,4.8,16.2,9.3)
    elif name == 'check':
        poly((4,12),(9.5,17.5),(20,6.5))
    elif name == 'check-circle':
        circle(12,12,9); poly((7.5,12),(10.5,15),(16.5,9))
    elif name in ('clock','history'):
        if name == 'clock': circle(12,12,9)
        else:
            painter.drawArc(QRectF(4,4,17,17),125*16,-300*16)
            poly((3,3),(3,9),(9,9))
        poly((12,7),(12,12),(15.5,14))
    elif name in ('info','alert'):
        circle(12,12,9)
        if name == 'info': line(12,10.5,12,16.5); line(12,7,12,7.15)
        else: line(12,7,12,13); line(12,16.5,12,16.65)
    elif name == 'close':
        line(6,6,18,18); line(18,6,6,18)
    elif name == 'play':
        poly((7,3.8),(20,12),(7,20.2),closed=True)
    elif name == 'pause':
        rect(5,4,4,16,.7);rect(15,4,4,16,.7)
    elif name == 'stop':
        rect(4,4,16,16,2)
    elif name == 'lock':
        rect(5,11,14,10,2);painter.drawArc(QRectF(8,2,8,14),0,180*16);line(12,15,12,17)
    elif name in ('chevron-left','chevron-right'):
        poly((15,5),(8,12),(15,19)) if name=='chevron-left' else poly((9,5),(16,12),(9,19))
    elif name in ('pin','pin-filled'):
        painter.save();painter.translate(12,12);painter.rotate(45);painter.translate(-12,-12)
        if name == 'pin-filled': painter.setBrush(painter.pen().color())
        poly((8,3),(16,3),(15,10),(18,14),(6,14),(9,10),closed=True)
        line(12,14,12,21)
        painter.restore()
    elif name == 'book-open':
        poly((12,6),(8,4),(3,4),(3,19),(8,19),(12,21),(16,19),(21,19),(21,4),(16,4),(12,6));line(12,6,12,21)
    elif name == 'bookmark':
        poly((6,3),(18,3),(18,21),(12,17),(6,21),closed=True)
    elif name == 'source-code':
        poly((7,6),(2,12),(7,18));poly((17,6),(22,12),(17,18));line(14,4,10,20)
    elif name == 'pencil':
        poly((4,15),(15,4),(20,9),(9,20),(3,21),closed=True)
        line(12,7,17,12);line(4,15,9,20)
    elif name == 'grid':
        for x in (4,14):
            for y in (4,14): rect(x,y,6,6,1)
    elif name == 'list':
        for y in (5,12,19):
            circle(4,y,.65); line(9,y,21,y)
    elif name == 'menu':
        for y in (6,12,18): line(4,y,20,y)
    elif name == 'more-horizontal':
        for x in (5,12,19):circle(x,12,1)
    elif name == 'grip-vertical':
        for x in (9,15):
            for y in (5,12,19):circle(x,y,.7)
    elif name == 'external-link':
        poly((13,3),(21,3),(21,11)); line(21,3,11,13)
        poly((9,5),(4,5),(4,20),(19,20),(19,15))
    elif name == 'server':
        for y in (3,14):
            rect(3,y,18,7,1.8); line(7,y+3.5,7.2,y+3.5); line(11,y+3.5,17,y+3.5)
    elif name == 'folder':
        poly((3,7),(3,4),(9,4),(12,7),(21,7),(21,20),(3,20),closed=True)
    elif name in ('plus','minus'):
        line(5,12,19,12)
        if name == 'plus': line(12,5,12,19)
    elif name == 'copy':
        rect(8,8,13,13,1.5); poly((16,8),(16,3),(3,3),(3,16),(8,16))
    elif name == 'trash':
        line(3,6,21,6); poly((9,6),(9,3),(15,3),(15,6))
        poly((5,6),(6,21),(18,21),(19,6)); line(10,10,10,17); line(14,10,14,17)
    elif name == 'chevron-down': poly((5,9),(12,16),(19,9))
    elif name == 'chevron-up': poly((5,15),(12,8),(19,15))


_PIXMAPS = OrderedDict()


class _LineIconEngine(QIconEngine):
    def __init__(self,name,color,size,filled=False):
        super().__init__(); self.name=name
        self.filled=filled
        self.color=color if isinstance(color,str) and color in ('on-accent','on-run','on-stop','navigation') else QColor(color) if color is not None else None
        self.size=size

    def clone(self):
        return _LineIconEngine(self.name,self.color,self.size,self.filled)

    def availableSizes(self,mode=QIcon.Mode.Normal,state=QIcon.State.Off):
        return [QSize(self.size,self.size)]

    def stroke(self,mode,state):
        if self.color=='navigation':
            stroke=QApplication.palette().color(QPalette.ColorRole.WindowText)
            stroke.setAlphaF(1 if state==QIcon.State.On else .92 if mode in (QIcon.Mode.Active,QIcon.Mode.Selected) else .80)
        elif isinstance(self.color,str):
            property_name={'on-accent':'pcsIconOnAccent','on-run':'pcsIconOnRun','on-stop':'pcsIconOnStop'}[self.color]
            stroke=QColor(QApplication.instance().property(property_name) or QApplication.palette().color(QPalette.ColorRole.ButtonText))
        else:
            stroke=QColor(self.color) if self.color is not None else QApplication.palette().color(QPalette.ColorRole.WindowText)
        if not stroke.isValid(): stroke=QApplication.palette().color(QPalette.ColorRole.WindowText)
        if mode == QIcon.Mode.Disabled:
            stroke=QApplication.palette().color(QPalette.ColorGroup.Disabled,QPalette.ColorRole.ButtonText)
            if self.color in ('on-run','on-stop'):
                property_name='pcsIconOn'+('Run' if self.color=='on-run' else 'Stop')+'Disabled'
                stroke=QColor(QApplication.instance().property(property_name) or stroke)
        return stroke

    def paint(self,painter,rect,mode,state):
        stroke=self.stroke(mode,state)
        painter.save()
        side=min(rect.width(),rect.height())
        painter.translate(rect.x()+(rect.width()-side)/2,rect.y()+(rect.height()-side)/2)
        painter.scale(side/24,side/24)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(stroke,1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _draw(painter,self.name,self.filled and state==QIcon.State.On)
        painter.restore()

    def pixmap(self,size,mode,state):
        key=(self.name,self.stroke(mode,state).rgba(),self.filled and state==QIcon.State.On,
             size.width(),size.height())
        cached=_PIXMAPS.get(key)
        if cached is not None:
            _PIXMAPS.move_to_end(key)
            return QPixmap(cached)
        result=QPixmap(size); result.fill(Qt.GlobalColor.transparent)
        painter=QPainter(result); self.paint(painter,result.rect(),mode,state); painter.end()
        # Usual toolbar icons are tiny; large exported icons stay uncached.
        if size.width()*size.height()<=128*128:
            _PIXMAPS[key]=QPixmap(result)
            if len(_PIXMAPS)>128:_PIXMAPS.popitem(last=False)
        return result


def icon(name, color=None, size=20, *, filled=False):
    """Return a vector-painted QIcon that follows native device scaling.

    ``color`` accepts a QColor or Qt color string. ``'on-accent'``, ``'on-run'``
    and ``'on-stop'`` read their shared foreground roles at paint time.
    ``'navigation'`` softens resting icons while retaining active contrast.
    If omitted, the application's
    WindowText color is read at paint time, so an existing icon follows later
    palette changes. Construct icons after QApplication initialization.
    Unknown names use the information symbol, not an invisible missing glyph.
    """
    if QApplication.instance() is None:
        raise RuntimeError('Create QApplication before constructing UI icons.')
    name=ALIASES.get(name,name)
    if name not in ICON_NAMES: name='info'
    size=max(8,min(128,round(size)))
    return QIcon(_LineIconEngine(name,color,size,filled))
