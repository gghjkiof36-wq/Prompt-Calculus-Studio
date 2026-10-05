"""Small, font-independent line icons for PCS navigation and actions.

Use ``icon(name, color=tokens['text'], size=20)`` when constructing a themed
control. No installed font, network asset, resource file or cache is required.
"""
from struct import pack

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
    'canvas','media','explore','export','settings','back','forward','search','update',
    'package','download','check','clock','info','close','play','pin','menu',
    'workflow','history','external-link','alert','server','check-circle',
    'folder','plus','minus','copy','trash','chevron-down','chevron-up','grid','list','more-horizontal',
})
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
            ('check','m4 8 3 3 5-6',tokens['on_accent']),
        ):
            files[name+'.svg']=(f'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"><path d="{path}" fill="none" stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>').encode('utf-8')
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
    return {name:':'+root+'/'+name+'.svg' for name in ('down','up','check')}


def _draw(painter, name):
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

    if name in ('canvas','workflow'):
        rect(3,3,6,6); rect(15,15,6,6); rect(3,15,6,6)
        poly((6,9),(6,12),(18,12),(18,15)); line(6,12,6,15)
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
    elif name == 'pin':
        poly((8,3),(16,3),(15,10),(18,14),(6,14),(9,10),closed=True)
        line(12,14,12,21)
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


class _LineIconEngine(QIconEngine):
    def __init__(self,name,color,size):
        super().__init__(); self.name=name
        self.color=color if isinstance(color,str) and color in ('on-accent','on-run','on-stop') else QColor(color) if color is not None else None
        self.size=size

    def clone(self):
        return _LineIconEngine(self.name,self.color,self.size)

    def availableSizes(self,mode=QIcon.Mode.Normal,state=QIcon.State.Off):
        return [QSize(self.size,self.size)]

    def paint(self,painter,rect,mode,state):
        if isinstance(self.color,str):
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
        painter.save()
        side=min(rect.width(),rect.height())
        painter.translate(rect.x()+(rect.width()-side)/2,rect.y()+(rect.height()-side)/2)
        painter.scale(side/24,side/24)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(stroke,1.7,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap,Qt.PenJoinStyle.RoundJoin))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        _draw(painter,self.name)
        painter.restore()

    def pixmap(self,size,mode,state):
        result=QPixmap(size); result.fill(Qt.GlobalColor.transparent)
        painter=QPainter(result); self.paint(painter,result.rect(),mode,state); painter.end()
        return result


def icon(name, color=None, size=20):
    """Return a vector-painted QIcon that follows native device scaling.

    ``color`` accepts a QColor or Qt color string. ``'on-accent'``, ``'on-run'``
    and ``'on-stop'`` read their shared foreground roles at paint time.
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
    return QIcon(_LineIconEngine(name,color,size))
