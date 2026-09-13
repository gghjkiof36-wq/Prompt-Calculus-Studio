"""Raster graphics items for the shared builder and visible composition trees."""
import math

from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QTimer
from PySide6.QtGui import QColor, QPen, QFontMetricsF, QPainter, QPainterPath, QWheelEvent
from PySide6.QtWidgets import (QGraphicsObject, QGraphicsItem, QGraphicsProxyWidget,
    QGraphicsView, QGraphicsScene, QFrame, QRubberBand, QAbstractScrollArea, QApplication)
from . import composition as comp


class ResizableCard(QGraphicsObject):
    def init_interaction(self):
        self.setAcceptHoverEvents(True); self.hot=None; self.pressed=None; self.resizing=None
        self.requested_size=self.canvas.window.state.get('text_sizes',{}).get(self.key,[0,0])

    def resize_rect(self): return QRectF(self.width-22,self.height-22,22,22)

    def resize_corner(self,pos):
        for corner,x,y in [('tl',0,0),('tr',self.width-18,0),('bl',0,self.height-18),('br',self.width-22,self.height-22)]:
            if QRectF(x,y,22 if corner=='br' else 18,22 if corner=='br' else 18).contains(pos): return corner

    def actions(self): return {}

    def action_at(self,pos):
        if self.resize_corner(pos): return 'resize'
        return next((name for name,rect in self.actions().items() if rect.contains(pos)),None)

    def hoverMoveEvent(self,event):
        hot=self.action_at(event.pos())
        if hot!=self.hot: self.hot=hot; self.update()
        self.setToolTip({'resize':'調整尺寸','size':'調整尺寸','weight':'輸入權重','minus':'權重 −0.1','plus':'權重 +0.1','add':'加入子元素'}.get(hot,''))
        if hot=='resize': self.setCursor(Qt.CursorShape.SizeFDiagCursor if self.resize_corner(event.pos()) in ('tl','br') else Qt.CursorShape.SizeBDiagCursor)
        elif hot: self.setCursor(Qt.CursorShape.PointingHandCursor)
        else: self.unsetCursor()
        super().hoverMoveEvent(event)

    def hoverLeaveEvent(self,event):
        self.hot=None; self.unsetCursor(); self.update(); super().hoverLeaveEvent(event)

    def press_control(self,event):
        if event.button()!=Qt.MouseButton.LeftButton: return False
        action=self.action_at(event.pos())
        if not action: return False
        self.pressed=action
        if action=='resize':
            self.resizing=(event.scenePos(),self.width,self.height)
            self.resize_anchor=QPointF(self.pos()); self.resize_edge=self.resize_corner(event.pos())
        self.update(); event.accept(); return True

    def mouseMoveEvent(self,event):
        if self.resizing:
            start,width,height=self.resizing; delta=event.scenePos()-start
            corner=getattr(self,'resize_edge','br'); left=corner.endswith('l'); top=corner.startswith('t')
            self.requested_size=[max(100,min(10000,width+(-delta.x() if left else delta.x()))),max(60,min(10000,height+(-delta.y() if top else delta.y())))]
            self.layout_card()
            if self.parentItem() is None:
                self.setPos(self.resize_anchor+QPointF(width-self.width if left else 0,height-self.height if top else 0))
            self.canvas.functions.layout(); event.accept(); return
        if self.pressed: event.accept(); return
        super().mouseMoveEvent(event)

    def release_control(self,event):
        if self.pressed is None: return False
        action=self.pressed; self.pressed=None; canvas=self.canvas; key=self.key
        if self.resizing:
            self.resizing=None; width,height=self.width,self.height
            position=[self.x(),self.y()] if self.parentItem() is None else None
            QTimer.singleShot(0,lambda:canvas.resize_card(key,width,height,position))
        elif action==self.action_at(event.pos()):
            if action=='size': QTimer.singleShot(0,lambda:canvas.size_dialog(key))
            elif action=='add': QTimer.singleShot(0,lambda:canvas.palette(target=key))
            elif action=='weight': QTimer.singleShot(0,lambda:canvas.weight_dialog(key))
            elif action in ('minus','plus'):
                delta=-1 if action=='minus' else 1
                QTimer.singleShot(0,lambda:canvas.change_weight(key,delta=delta))
        self.update(); event.accept(); return True

    def draw_controls(self,painter):
        painter.setPen(QPen(QColor('#afc2db' if self.hot=='resize' else '#687b91'),1.6))
        for offset in (7,12):
            painter.drawLine(QPointF(self.width-offset,self.height-5),QPointF(self.width-5,self.height-offset))
        for x,y,sx,sy in [(0,0,1,1),(self.width,0,-1,1),(0,self.height,1,-1)]:
            painter.drawLine(QPointF(x+sx*5,y+sy*12),QPointF(x+sx*12,y+sy*5))
        for action,rect in self.actions().items():
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor('#486487' if self.pressed==action else '#35485f' if self.hot==action else '#2b333f'))
            painter.drawRoundedRect(rect,5,5); painter.setPen(QColor('#edf3fb' if self.hot==action else '#adc0d8'))
            text={'size':'↗','minus':'−','plus':'+','add':'＋' if getattr(self,'compact',False) else '＋ 加入子元素'}.get(action)
            if action=='weight': text=f"{self.value['weight']/10:.1f}"
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text)


class TextCard(ResizableCard):
    """Hosts the actual list builder, including its existing signals and controls."""
    def __init__(self, canvas):
        super().__init__(); self.canvas=canvas; self.key='__text_output__'; self.start=QPointF()
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable|QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.panel=canvas.window.builder_panel; self.editor=canvas.window.final
        self.proxy=QGraphicsProxyWidget(self); self.proxy.setPos(14,46)
        self.width=560; self.height=660; self.saved_sizes=None; self.init_interaction()
        self.proxy.geometryChanged.connect(self.sync_bounds)
        self.setToolTip('')

    def attach(self):
        window=self.canvas.window
        if self.proxy.widget() is not self.panel:
            self.saved_sizes=window.split.sizes()
            window.builder_scroll.takeWidget()
            for effect in window.selection_effects: effect.setEnabled(False)
            self.panel.hide(); self.panel.setParent(None); self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(window.styleSheet())
        self.panel.ensurePolished(); self.panel.layout().activate()
        self.requested_size=self.canvas.window.state.get('text_sizes',{}).get(self.key,[0,0])
        self.layout_card(); self.panel.show()

    def layout_card(self):
        minimum=self.panel.minimumSizeHint(); width,height=self.requested_size
        self.prepareGeometryChange()
        top=54+max(1,len(self.canvas.window.state['multi_output']['outputs']))*24 if self.key=='__result_preview__' and 'multi_output' in self.canvas.window.state else 46
        self.proxy.setGeometry(QRectF(14,top,max(532,minimum.width(),width-28),max(600,minimum.height(),height-top-14)))
        self.width=self.panel.width()+28; self.height=self.panel.height()+top+14; self.update()

    def sync_bounds(self):
        # Qt may enlarge the embedded panel after a deferred font/layout change.
        rect=self.proxy.geometry()
        self.prepareGeometryChange(); self.width=rect.right()+14; self.height=rect.bottom()+14; self.update()
        self.canvas.functions.layout()

    def actions(self): return {'size':QRectF(self.width-46,7,30,28)}

    def detach(self):
        if self.proxy.widget() is None: return
        self.panel.hide(); self.proxy.setWidget(None); self.panel.setStyleSheet('')
        self.canvas.window.builder_scroll.setWidget(self.panel)
        for effect in self.canvas.window.selection_effects: effect.setEnabled(True)
        self.panel.show()
        if self.saved_sizes: self.canvas.window.split.setSizes(self.saved_sizes)

    def boundingRect(self): return QRectF(0,0,self.width,self.height)

    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setBrush(QColor('#22262e')); painter.setPen(QPen(QColor('#81aeee' if getattr(self,'dock_hover',False) else '#6d8eb5' if self.isSelected() else '#495563'),2 if getattr(self,'dock_hover',False) else 1.5))
        outline=QPainterPath(); outline.addRoundedRect(QRectF(0,0,self.width,self.height),12,12)
        painter.drawPath(self.canvas.functions.outline(self,outline))
        painter.setPen(QColor('#eef1f5')); painter.setFont(self.canvas.font())
        painter.drawText(QRectF(18,7,self.width-80,32),Qt.AlignmentFlag.AlignVCenter,getattr(self,'title','目前組合與最終 Prompt 輸出'))
        self.draw_controls(painter)

    def update_text(self):
        if self.proxy.widget() is not None and self.panel.styleSheet()!=self.canvas.window.styleSheet():
            self.panel.setStyleSheet(self.canvas.window.styleSheet())
        if self.proxy.widget() is not None and (self.width!=self.panel.width()+28 or self.height!=self.panel.height()+60): self.layout_card()
        self.update()

    def mousePressEvent(self,event):
        self.start=self.pos()
        if self.press_control(event): return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if self.release_control(event): return
        super().mouseReleaseEvent(event)
        if self.pos()!=self.start:
            canvas=self.canvas; pos=[self.pos().x(),self.pos().y()]
            if canvas.functions.dropped(self,event.scenePos()): return
            key=self.key; QTimer.singleShot(0,lambda:canvas.move_cards({key:pos}))

    def mouseMoveEvent(self,event):
        if self.resizing or self.pressed: super().mouseMoveEvent(event); return
        super().mouseMoveEvent(event); self.canvas.functions.moving(self,event.scenePos())

    def contextMenuEvent(self,event): self.canvas.tools_menu(event.screenPos()); event.accept()


class PreviewCard(TextCard):
    def __init__(self,canvas):
        super().__init__(canvas); self.key='__result_preview__'; self.title='圖片預覽'
        self.panel=canvas.results; self.init_interaction()

    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.ensurePolished(); self.panel.layout().activate()
        self.requested_size=self.canvas.window.state.get('text_sizes',{}).get(self.key,[500,660])
        self.layout_card(); self.panel.show()

    def layout_card(self):
        minimum=self.panel.minimumSizeHint(); width,height=self.requested_size
        top=54+max(1,len(self.canvas.window.state['multi_output']['outputs']))*24 if 'multi_output' in self.canvas.window.state else 46
        self.proxy.setGeometry(QRectF(14,top,max(340,minimum.width(),width-28),max(220,minimum.height(),height-top-14)))
        self.sync_bounds()

    def contextMenuEvent(self,event): self.canvas.functions.context(self.key,event.screenPos()); event.accept()


class NodeCard(ResizableCard):
    """A variable-size module containing directly visible, selectable child cards."""
    def __init__(self,canvas,key,value,subtitle,muted=False,parent=None):
        super().__init__(parent); self.canvas=canvas; self.key=key; self.value=value
        self.subtitle=subtitle; self.muted=muted; self.start=QPointF(); self.parts=[]; self.drop_size=None; self.init_interaction()
        self.line=max(23,math.ceil(QFontMetricsF(canvas.font()).height())+3)
        flags=QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flags|=QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        self.setFlags(flags); self.setToolTip('')
        canvas.cards[key]=self
        ident=canvas.entries[key][0]
        active=comp.active_overlay(value)
        if ident is not None:
            for index,part in enumerate(value['children']+value['overlays']):
                overlay=part in value['overlays']
                inactive=muted or not value['enabled'] or not part['enabled'] or (part is not active if overlay else bool(active))
                title='覆蓋中' if overlay and part is active else '覆蓋未生效' if overlay else f'{index+1} · 元素'
                if inactive: title+=' · 未生效'
                child=NodeCard(canvas,ident+':'+part['id'],part,title,inactive,self); self.parts.append(child)
        self.compact=parent is not None and not self.parts
        self.own=value['prompt'] if value['prompt'].strip()!=value['name'].strip() else ''
        self.layout_card(propagate=False)

    def layout_card(self,propagate=True):
        self.prepareGeometryChange(); requested_w,requested_h=self.requested_size
        self.width=max(320,requested_w,max((p.width+28 for p in self.parts),default=0),(self.drop_size[0]+28) if self.drop_size else 0)
        metrics=QFontMetricsF(self.canvas.font())
        self.text_height=math.ceil(metrics.boundingRect(QRectF(0,0,self.width-28,100000),Qt.TextFlag.TextWordWrap,self.own).height()) if self.own else 0
        self.header_height=16+self.line*(1 if self.compact else 2)+(self.text_height+8 if self.own else 0)
        y=self.header_height+8
        for child in self.parts: child.setPos(14,y); y+=child.height+10
        self.drop_y=y
        if self.drop_size: y+=self.drop_size[1]+12
        ident=self.canvas.entries[self.key][0]
        self.height=max(requested_h,y+(44 if ident is not None else 24))
        self.add_rect=QRectF(12,self.height-42,self.width-44,28) if ident is not None else QRectF()
        if self.compact: self.add_rect=QRectF(self.width-44,5,30,28)
        weight_y=self.height-42 if self.compact else 6
        self.weight_rect=QRectF(self.width-114,weight_y,50,28)
        self.minus_rect=QRectF(self.width-146,weight_y,28,28)
        self.plus_rect=QRectF(self.width-60,weight_y,28,28)
        if propagate and self.parentItem(): self.parentItem().layout_card()
        self.update()

    def actions(self):
        if self.canvas.entries[self.key][0] is None: return {}
        return dict(add=self.add_rect,minus=self.minus_rect,weight=self.weight_rect,plus=self.plus_rect)

    def boundingRect(self): return QRectF(0,0,self.width,self.height)
    def selectionRect(self): return QRectF(0,0,self.width,self.header_height)

    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#81aeee' if self.drop_size else '#728093' if self.isSelected() and self.muted else '#81aeee' if self.isSelected() else '#383a3e' if self.muted else '#49515f'),1.6))
        painter.setBrush(QColor('#1b1d20' if self.muted else '#283447' if self.isSelected() else '#202731' if self.parentItem() else '#202329'))
        painter.drawRoundedRect(self.boundingRect(),11,11); painter.setFont(self.canvas.font())
        if self.muted: painter.setOpacity(.42)
        metrics=QFontMetricsF(painter.font()); available=self.width-28
        painter.setPen(QColor('#96999e' if self.muted else '#8eabc9'))
        status=self.subtitle+(' · 停用' if not self.value['enabled'] else '')
        if comp.active_overlay(self.value): status+=' · 已覆蓋'

        if not self.compact:
            painter.drawText(QRectF(14,8,available-142,self.line),Qt.AlignmentFlag.AlignVCenter,metrics.elidedText(status,Qt.TextElideMode.ElideRight,available-142))
        painter.setPen(QColor('#a3a7b0' if self.muted else '#eef1f5'))
        title=self.value['name']
        if self.compact and (self.value['weight']!=10 or not self.value['enabled']):
            title+=f" · {self.value['weight']/10:.1f}" if self.value['enabled'] else ' · 停用'
        title_y=8 if self.compact else 10+self.line
        painter.drawText(QRectF(14,title_y,available-(30 if self.compact else 0),self.line),Qt.AlignmentFlag.AlignVCenter,metrics.elidedText(title,Qt.TextElideMode.ElideRight,available-(30 if self.compact else 0)))
        if self.own:
            painter.setPen(QColor('#929ba9'))
            painter.drawText(QRectF(14,16+self.line*(1 if self.compact else 2),available,self.text_height),Qt.TextFlag.TextWordWrap,self.own)
        self.draw_controls(painter)
        if self.drop_size:
            painter.setOpacity(1); painter.setPen(QPen(QColor('#81aeee'),1,Qt.PenStyle.DashLine)); painter.setBrush(QColor('#243446'))
            rect=QRectF(14,self.drop_y,self.width-28,self.drop_size[1]); painter.drawRoundedRect(rect,8,8)
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,'放開以加入')
    def mousePressEvent(self,event):
        self.start=self.pos(); self.canvas.view.setFocus(); self.scene().clearFocus()
        if self.press_control(event): return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if self.release_control(event): return
        super().mouseReleaseEvent(event)
        canvas=self.canvas; key=self.key
        if self.pos()!=self.start:
            if canvas.finish_node_drop(self,event.scenePos()): return
            if self.parentItem(): self.parentItem().layout_card(); return
            moves={i.key:[i.pos().x(),i.pos().y()] for i in self.scene().selectedItems() if isinstance(i,NodeCard) and i.parentItem() is None}
            QTimer.singleShot(0,lambda:canvas.move_cards(moves))
        elif event.button()==Qt.MouseButton.LeftButton and self.add_rect.contains(event.pos()):
            QTimer.singleShot(0,lambda:canvas.palette(target=key))

    def mouseMoveEvent(self,event):
        if self.resizing or self.pressed: super().mouseMoveEvent(event); return
        super().mouseMoveEvent(event)
        if len(self.scene().selectedItems())==1: self.canvas.preview_node_drop(self,event.scenePos())

    def mouseDoubleClickEvent(self,event):
        if self.press_control(event): return
        canvas=self.canvas; key=self.key
        QTimer.singleShot(0,lambda:canvas.palette(target=key)); event.accept()

    def contextMenuEvent(self,event):
        self.canvas.view.setFocus(); self.scene().clearFocus()
        if not self.isSelected(): self.scene().clearSelection(); self.setSelected(True)
        self.canvas.context(self.key,event.screenPos()); event.accept()


class CanvasView(QGraphicsView):
    def __init__(self,canvas):
        super().__init__(canvas); self.canvas=canvas; self.pan=None; self.pan_button=None; self.box_start=None
        self.rubber=QRubberBand(QRubberBand.Shape.Rectangle,self.viewport()); self.box_selection=set()
        self.setScene(QGraphicsScene(self)); self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        # The top-level window is translucent. Partial repaint/scroll blits can
        # retain old antialiased edges underneath moved items on Windows.
        # Repaint the visible raster surface on changes, never on an idle timer.
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setBackgroundBrush(QColor('#17191e')); self.setMinimumSize(220,64); self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptDrops(True); self.viewport().setAcceptDrops(True)

    def dragEnterEvent(self,event):
        from .image_drop import dropped_source
        if dropped_source(event.mimeData())[0]: event.acceptProposedAction(); return
        super().dragEnterEvent(event)
    def dragMoveEvent(self,event):
        from .image_drop import dropped_source
        if dropped_source(event.mimeData())[0]: event.acceptProposedAction(); return
        super().dragMoveEvent(event)
    def dropEvent(self,event):
        from .image_drop import dropped_source
        if not dropped_source(event.mimeData())[0]: super().dropEvent(event); return
        self.canvas.drop_position=self.mapToScene(event.position().toPoint())
        self.canvas.drop_loader.receive(event.mimeData()); event.acceptProposedAction()

    def set_status_widget(self,widget):
        self.status_widget=widget; widget.setParent(self.viewport())
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        self.position_status()

    def position_status(self):
        widget=getattr(self,'status_widget',None)
        if widget is None: return
        if not widget.text(): widget.hide(); return
        widget.ensurePolished(); widget.setWordWrap(False)
        width=min(widget.sizeHint().width(),720,max(1,self.viewport().width()-16))
        widget.setWordWrap(True)
        height=max(widget.minimumSizeHint().height(),widget.heightForWidth(width))
        widget.setGeometry(8,max(0,self.viewport().height()-height-8),width,height)
        widget.show(); widget.raise_()

    def resizeEvent(self,event):
        super().resizeEvent(event); self.position_status()

    def drawBackground(self,painter,rect):
        painter.save()
        painter.fillRect(rect,QColor('#17191e'))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,False)
        pen=QPen(QColor('#30343b'),1); pen.setCosmetic(True); painter.setPen(pen)
        spacing=28 if self.transform().m11()>=.6 else 112
        for x in range(math.floor(rect.left()/spacing)*spacing,math.ceil(rect.right()),spacing):
            for y in range(math.floor(rect.top()/spacing)*spacing,math.ceil(rect.bottom()),spacing): painter.drawPoint(QPointF(x,y))
        painter.restore()

    def wheelEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        while item is not None and not isinstance(item,QGraphicsProxyWidget): item=item.parentItem()
        if item is not None and item.widget() is not None:
            root=item.widget(); point=item.mapFromScene(self.mapToScene(event.position().toPoint())).toPoint()
            child=root.childAt(point) or root
            while child is not None:
                if isinstance(child,QAbstractScrollArea) and child.isEnabled():
                    bars=(child.verticalScrollBar(),child.horizontalScrollBar())
                    if any(bar.maximum()>bar.minimum() for bar in bars):
                        position=child.viewport().mapFrom(root,point)
                        forwarded=QWheelEvent(QPointF(position),event.globalPosition(),event.pixelDelta(),event.angleDelta(),event.buttons(),event.modifiers(),event.phase(),event.inverted())
                        QApplication.sendEvent(child.viewport(),forwarded)
                        # Keep ownership even at a boundary; a list reaching its
                        # end must not unexpectedly zoom the surrounding Canvas.
                        event.accept(); return
                if child is root: break
                child=child.parentWidget()
        self.canvas.zoom(1.15**(event.angleDelta().y()/120)); event.accept()

    def mousePressEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if event.button()==Qt.MouseButton.LeftButton and event.modifiers()&Qt.KeyboardModifier.ControlModifier and not isinstance(item,QGraphicsProxyWidget):
            self.setFocus(); self.scene().clearFocus(); self.box_start=event.position().toPoint()
            self.box_selection={i.key for i in self.scene().selectedItems() if isinstance(i,NodeCard)}
            self.rubber.setGeometry(QRect(self.box_start,self.box_start)); self.rubber.show(); event.accept(); return
        if event.button()==Qt.MouseButton.MiddleButton or (event.button()==Qt.MouseButton.LeftButton and item is None):
            self.setFocus(); self.scene().clearFocus(); self.scene().clearSelection()
            self.pan=event.position(); self.pan_button=event.button(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if self.box_start is not None:
            self.rubber.setGeometry(QRect(self.box_start,event.position().toPoint()).normalized()); event.accept(); return
        if self.pan is not None:
            delta=event.position()-self.pan; self.pan=event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-round(delta.y()))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if self.box_start is not None and event.button()==Qt.MouseButton.LeftButton:
            end=event.position().toPoint(); rect=self.mapToScene(QRect(self.box_start,end).normalized()).boundingRect()
            clicked=self.itemAt(end) if (end-self.box_start).manhattanLength()<4 else None
            for card in self.canvas.cards.values():
                hit=card is clicked if clicked else rect.intersects(card.mapRectToScene(card.selectionRect()))
                card.setSelected((card.key not in self.box_selection) if hit and clicked else hit or card.key in self.box_selection)
            self.box_start=None; self.rubber.hide(); event.accept(); return
        if self.pan is not None and event.button()==self.pan_button:
            self.pan=None; self.pan_button=None; self.unsetCursor(); event.accept(); return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.itemAt(event.position().toPoint()) is None:
            self.pan=None; self.pan_button=None; self.unsetCursor()
            position=self.mapToScene(event.position().toPoint())
            QTimer.singleShot(0,lambda:self.canvas.palette(position)); event.accept(); return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self,event):
        # Editors and the shared builder receive their own text/navigation shortcuts.
        if self.scene().focusItem() is not None:
            super().keyPressEvent(event); return
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            self.canvas.delete_selected(); event.accept(); return
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            if event.key()==Qt.Key.Key_Z: self.canvas.undo(); event.accept(); return
            if event.key()==Qt.Key.Key_Y: self.canvas.redo(); event.accept(); return
        super().keyPressEvent(event)

    def contextMenuEvent(self,event):
        if self.itemAt(event.pos()) is None:
            self.canvas.tools_menu(event.globalPos()); event.accept(); return
        super().contextMenuEvent(event)
