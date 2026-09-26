"""Typed data ports and selectable connections; repaint only on interaction."""
from PySide6.QtCore import Qt,QRectF,QPointF,QTimer,QObject,QEvent
from PySide6.QtGui import QColor,QPen,QPainter,QPainterPath,QPainterPathStroker
from PySide6.QtWidgets import QGraphicsObject,QGraphicsItem,QGraphicsPathItem,QGraphicsSimpleTextItem
from . import multi_output as model

COLORS={'text':'#9cbff3','image':'#e0bb79','execution':'#93cbb2','preview':'#baa6e0','clip':'#9cbff3'}
LABELS={'text':'文字','image':'圖片','execution':'執行','preview':'預覽','clip':'Prompt'}


def curve(start,end,style='curve'):
    path=QPainterPath(start)
    if style=='straight': path.lineTo(end); return path
    if style=='orthogonal':
        if start.x()!=end.x() and start.y()!=end.y():
            middle=(start.x()+end.x())/2
            path.lineTo(middle,start.y()); path.lineTo(middle,end.y())
        path.lineTo(end); return path
    width=max(60,abs(end.x()-start.x())*.45)
    path.cubicTo(start+QPointF(width,0),end-QPointF(width,0),end)
    return path


class FlowLine(QGraphicsPathItem):
    def __init__(self,canvas,value):
        super().__init__(); self.canvas=canvas; self.value=value; self.key=value['id']
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable); self.setZValue(-.1)
        self.setToolTip(LABELS[value['kind']]+'資料流 · 拖動尾端改接，選取後按 Delete 解除')
    def shape(self):
        stroke=QPainterPathStroker(); stroke.setWidth(16); return stroke.createStroke(self.path())
    def paint(self,painter,option,widget=None):
        self.setPen(QPen(QColor('#ffffff' if self.isSelected() else COLORS[self.value['kind']]),3 if self.isSelected() else 2))
        super().paint(painter,option,widget)
    def contextMenuEvent(self,event):
        from .widgets import RoundMenu
        menu=RoundMenu(self.canvas.window); menu.addAction('解除連線',lambda:self.canvas.commit(lambda s:model.disconnect(s,self.key))); menu.open_at(event.screenPos())


class Port(QGraphicsObject):
    def __init__(self,canvas,parent,key,kind,output,slot=None,index=0):
        super().__init__(parent); self.canvas=canvas; self.key=key; self.kind=kind; self.output=output
        self.slot=slot; self.index=index
        self.highlight=False; self.preview=None; self.target=None; self.setZValue(20)
        self.setAcceptHoverEvents(True); self.setCursor(Qt.CursorShape.CrossCursor)
        self.setToolTip(LABELS[kind]+('輸出' if output else '輸入')+(' · '+canvas.data()['outputs'][slot]['name'] if slot else '')+' · 拖曳或點擊連線')
        name=canvas.data()['outputs'][slot]['name'] if slot else LABELS[kind]
        self.caption=QGraphicsSimpleTextItem(name[:12],self); self.caption.setBrush(QColor(COLORS[kind])); self.caption.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
        font=canvas.font(); font.setPixelSize(11); self.caption.setFont(font)
        self.caption.setPos(-self.caption.boundingRect().width()-13 if output else 13,-self.caption.boundingRect().height()/2)
    def boundingRect(self): return QRectF(-11,-13,22,26)
    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor(COLORS[self.kind]),2)); painter.setBrush(QColor(COLORS[self.kind] if self.highlight else '#202731'))
        painter.drawEllipse(QPointF(),7,7)
        # Port captions stay within the node, above its controls.
        painter.setPen(QColor(COLORS[self.kind]))
    def compatible(self,other):
        if self.output==other.output or self.kind!=other.kind or self.key==other.key: return False
        source,destination=(self,other) if self.output else (other,self)
        if destination.slot and destination.slot!=source.key: return False
        if not model.valid_edge(self.canvas.window.state,source.key,destination.key,self.kind): return False
        if self.kind=='text':
            original=getattr(self.canvas.connection_gesture,'original',None)
            return not any(c['id']!=original and c['kind']=='text' and c['source']==source.key and c['destination']!=destination.key for c in self.canvas.data()['connections'])
        return True
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton: event.ignore(); return
        self.canvas.connection_gesture.begin(self,event.scenePos())
        event.accept()
    def mouseMoveEvent(self,event):
        self.canvas.connection_gesture.move(event.scenePos()); event.accept()
    def mouseReleaseEvent(self,event):
        self.canvas.connection_gesture.release(event.scenePos()); event.accept()
    def contextMenuEvent(self,event):
        self.canvas.connection_gesture.menu(event.screenPos(),self); event.accept()


class ConnectionGesture(QObject):
    """Rewire atomically: blank confirms disconnection, right click preserves it."""
    def __init__(self,canvas):
        super().__init__(canvas); self.canvas=canvas; self.anchor=None; self.preview=None; self.original=None; self.target=None; self.latched=False
        canvas.view.viewport().setMouseTracking(True); canvas.view.viewport().installEventFilter(self); canvas.view.installEventFilter(self)
    def begin(self,port,position):
        self.cancel(); self.anchor=port; self.start=QPointF(position)
        if not port.output:
            value=next((c for c in self.canvas.data()['connections'] if c['destination']==port.key and c['kind']==port.kind and (port.slot is None or c['source']==port.slot)),None)
            if value:
                self.original=value['id']; self.anchor=self.canvas.line_port(value,True)
                if self.original in self.canvas.lines: self.canvas.lines[self.original].hide()
        self.preview=QGraphicsPathItem(); self.preview.setZValue(30); self.preview.setPen(QPen(QColor(COLORS[port.kind]),2,Qt.PenStyle.DashLine))
        self.canvas.view.scene().addItem(self.preview)
        for p in self.canvas.ports.values(): p.highlight=self.anchor.compatible(p); p.update()
        self.move(position)
    def move(self,position):
        if self.anchor is None: return
        view=self.canvas.view; point=view.mapFromScene(position)
        self.target=next((p for p in self.canvas.ports.values() if p.highlight and (view.mapFromScene(p.scenePos())-point).manhattanLength()<20),None)
        end=self.target.scenePos() if self.target else position; start=self.anchor.scenePos()
        self.preview.setPath(curve(start,end,self.canvas.window.state['settings'].get('connection_style','curve')) if self.anchor.output else curve(end,start,self.canvas.window.state['settings'].get('connection_style','curve')))
    def release(self,position):
        if self.anchor is None: return
        if (self.canvas.view.mapFromScene(position)-self.canvas.view.mapFromScene(self.start)).manhattanLength()<5:
            self.latched=True; return
        self.finish(position)
    def finish(self,position):
        if self.anchor is None: return
        self.move(position); anchor=self.anchor; target=self.target; original=self.original; kind=anchor.kind
        src,dst=((anchor.key,target.key) if anchor.output else (target.key,anchor.key)) if target else (None,None)
        self.cancel(); canvas=self.canvas
        def apply(s):
            if original: model.disconnect(s,original)
            if src: model.connect(s,src,dst,kind)
        if original or src: QTimer.singleShot(0,lambda:canvas.commit(apply))
    def cancel(self):
        if self.preview is not None: self.canvas.view.scene().removeItem(self.preview)
        if self.original in self.canvas.lines: self.canvas.lines[self.original].show()
        for p in self.canvas.ports.values(): p.highlight=False; p.update()
        self.preview=None; self.anchor=None; self.target=None; self.original=None; self.latched=False
    def menu(self,position,port=None):
        from .widgets import RoundMenu
        menu=RoundMenu(self.canvas.window)
        original=self.original
        if port and not original:
            value=next((c for c in self.canvas.data()['connections'] if c['destination']==port.key and c['kind']==port.kind and (not port.slot or port.slot==c['source'])),None)
            original=value['id'] if value else None
        if self.anchor: menu.addAction('取消移線',self.cancel)
        if original:
            def remove(): self.cancel(); self.canvas.commit(lambda s:model.disconnect(s,original))
            menu.addAction('解除連線',remove)
        menu.addMenu(self.canvas.functions.menu(menu,self.canvas.view.mapToScene(self.canvas.view.viewport().mapFromGlobal(position))))
        menu.open_at(position)
    def eventFilter(self,watched,event):
        if self.anchor is None: return False
        if event.type()==QEvent.Type.KeyPress and event.key()==Qt.Key.Key_Escape: self.cancel(); return True
        if self.latched and watched is self.canvas.view.viewport():
            if event.type()==QEvent.Type.MouseMove:
                self.move(self.canvas.view.mapToScene(event.position().toPoint())); return True
            if event.type()==QEvent.Type.MouseButtonPress:
                if event.button()==Qt.MouseButton.LeftButton: self.finish(self.canvas.view.mapToScene(event.position().toPoint())); return True
                if event.button()==Qt.MouseButton.RightButton: self.menu(event.globalPosition().toPoint()); return True
            if event.type()==QEvent.Type.ContextMenu: return True
        return False


class CanvasContainer(QGraphicsObject):
    def __init__(self,canvas,key):
        super().__init__(); self.canvas=canvas; self.key=key; self.width=760; self.height=620; self.start=None; self.corner=None; self.hovered=False
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable|QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.setAcceptHoverEvents(True); self.setZValue(-1)
    def refresh(self):
        value=self.canvas.data()['canvases'][self.key]; self.prepareGeometryChange()
        self.width,self.height=value['display_size']; self.setPos(*value['position']); self.update()
    def boundingRect(self): return QRectF(-4,-4,self.width+8,self.height+8)
    def rect(self): return QRectF(0,0,self.width,self.height)
    def corner_at(self,pos):
        for name,point in [('tl',QPointF()),('tr',QPointF(self.width,0)),('bl',QPointF(0,self.height)),('br',QPointF(self.width,self.height))]:
            if abs(pos.x()-point.x())<17 and abs(pos.y()-point.y())<17: return name
    def hoverMoveEvent(self,event):
        corner=self.corner_at(event.pos()); self.setCursor(Qt.CursorShape.SizeFDiagCursor if corner in ('tl','br') else Qt.CursorShape.SizeBDiagCursor if corner else Qt.CursorShape.ArrowCursor)
        super().hoverMoveEvent(event)
    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(0,0,0,36)); painter.drawRoundedRect(self.rect().translated(3,4),15,15)
        painter.setBrush(QColor(81,104,132,34)); painter.setPen(QPen(QColor('#96c4fb' if self.hovered else '#8daecf' if self.isSelected() else '#4d647d'),2 if self.hovered else 1))
        if self.key in getattr(self.canvas,'active_keys',set()):painter.setPen(QPen(QColor('#55aaff'),2.5))
        painter.drawRoundedRect(self.rect(),14,14)
        value=self.canvas.data()['canvases'][self.key]
        painter.setPen(QColor('#cedbeb')); painter.setFont(self.canvas.font())
        painter.drawText(QRectF(20,9,self.width-205,30),Qt.AlignmentFlag.AlignVCenter,value['name'])
        painter.setPen(QColor('#9db4ce')); painter.drawText(QRectF(self.width-174,9,154,30),Qt.AlignmentFlag.AlignCenter,'展開編輯 ↗')
        painter.setPen(QPen(QColor(170,193,219,45),1)); painter.drawLine(QPointF(18,48),QPointF(self.width-18,48))
        preview=self.canvas.image_previews.get(self.key)
        if preview is not None:
            area=QRectF(20,124,self.width-40,self.height-144); size=preview.size(); size.scale(area.size().toSize(),Qt.AspectRatioMode.KeepAspectRatio)
            target=QRectF(QPointF(),size); target.moveCenter(area.center()); painter.drawImage(target,preview)
        if self.isSelected() or self.hovered:
            painter.setPen(QPen(QColor('#afc5dd'),2))
            for x,y,sx,sy in [(0,0,1,1),(self.width,0,-1,1),(0,self.height,1,-1),(self.width,self.height,-1,-1)]:
                painter.drawLine(QPointF(x+sx*6,y+sy*6),QPointF(x+sx*18,y+sy*6)); painter.drawLine(QPointF(x+sx*6,y+sy*6),QPointF(x+sx*6,y+sy*18))
    def mousePressEvent(self,event):
        if event.button()!=Qt.MouseButton.LeftButton: super().mousePressEvent(event); return
        if QRectF(self.width-178,6,158,36).contains(event.pos()):
            QTimer.singleShot(0,lambda:self.canvas.open_editor(self.key)); event.accept(); return
        self.corner=self.corner_at(event.pos()); self.start=(QPointF(event.scenePos()),QPointF(self.pos()),self.width,self.height)
        self.card_starts={key:QPointF(self.canvas.cards[key].pos()) for key in self.canvas.data()['canvases'][self.key]['members'] if key in self.canvas.cards}
        self.setZValue(self.canvas.next_z()); super().mousePressEvent(event)
    def mouseMoveEvent(self,event):
        if self.start is None: return
        start,pos,w,h=self.start
        if self.corner:
            delta=event.scenePos()-start; left=self.corner.endswith('l'); top=self.corner.startswith('t')
            width=max(self.canvas.minimum_canvas(self.key)[0],w+(-delta.x() if left else delta.x()))
            height=max(self.canvas.minimum_canvas(self.key)[1],h+(-delta.y() if top else delta.y()))
            self.prepareGeometryChange(); self.width,self.height=width,height
            self.setPos(pos+QPointF(w-width if left else 0,h-height if top else 0)); self.update()
        else: super().mouseMoveEvent(event)
        delta=self.pos()-pos
        for key,p in self.card_starts.items():
            if key in self.canvas.cards: self.canvas.cards[key].setPos(p+delta)
        self.canvas.update_lines(); event.accept()
    def mouseReleaseEvent(self,event):
        super().mouseReleaseEvent(event)
        if self.start is None: return
        pos=[self.x(),self.y()]; size=[self.width,self.height]; key=self.key; canvas=self.canvas
        moved={k:[c.x(),c.y()] for k,c in canvas.cards.items() if k in self.card_starts}
        self.start=None; self.corner=None
        def apply(s):
            s['multi_output']['canvases'][key].update(position=pos,display_size=size); s.setdefault('text_positions',{}).update(moved)
        QTimer.singleShot(0,lambda:canvas.commit(apply))
    def mouseDoubleClickEvent(self,event):
        pos=QPointF(event.scenePos()); QTimer.singleShot(0,lambda:self.canvas.palette(pos)); event.accept()
    def contextMenuEvent(self,event): self.canvas.container_menu(self.key,event.screenPos()); event.accept()
