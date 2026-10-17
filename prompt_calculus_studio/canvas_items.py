"""Raster graphics items for the shared builder and visible composition trees."""
import math
import time

from PySide6.QtCore import Qt, QRect, QRectF, QPointF, QSizeF, QTimer, Signal, QEvent
from PySide6.QtGui import QColor, QPen, QFontMetricsF, QPainter, QPainterPath, QPolygonF, QWheelEvent, QTextLayout, QTextOption, QRegion, QContextMenuEvent
from PySide6.QtWidgets import (QGraphicsObject, QGraphicsItem, QGraphicsProxyWidget,
    QGraphicsView, QGraphicsScene, QFrame, QRubberBand, QAbstractScrollArea, QApplication, QStyle, QStyleOption, QWidget, QToolTip)
from . import composition as comp
from . import prompt_materials as pm


def canvas_colors(canvas):
    cached=getattr(canvas,'_paint_colors',None)
    if cached is not None:return dict(cached)
    from .theme import visual_tokens,window_appearance
    from .module_chrome import matte_colors
    return matte_colors(visual_tokens(window_appearance(canvas.window)))


def paint_module_surface(painter,outline,colors,header_bottom):
    """Paint only the module's visual layers, keeping its existing outline."""
    from .canvas_raster import paint_cached
    geometry=tuple((e.x,e.y,e.type.value) for i in range(outline.elementCount()) for e in (outline.elementAt(i),))
    pen=painter.pen()
    stroke=(pen.color().rgba(),pen.widthF(),pen.style().value,pen.capStyle().value,
            pen.joinStyle().value,pen.isCosmetic(),tuple(pen.dashPattern()),pen.dashOffset())
    key=('module-surface',geometry,outline.fillRule().value,tuple(sorted(colors.items())),header_bottom,stroke)
    bounds=outline.boundingRect().adjusted(-8,-8,8,14)
    paint_cached(painter,key,bounds,lambda target:_paint_module_surface(target,outline,colors,header_bottom))


def _paint_module_surface(painter,outline,colors,header_bottom):
    body=colors.get('canvas_body',colors['surface'])
    header=colors.get('canvas_header',body)
    from .module_chrome import paint_shadow
    paint_shadow(painter,outline,strength=colors.get('shadow_strength',1.0),
                 covered=colors.get('canvas_alpha',255)==255 and QColor(body).alpha()==255)
    fill=QColor(body);fill.setAlpha(colors.get('canvas_alpha',255))
    painter.setBrush(fill);painter.drawPath(outline)
    if header!=body:
        painter.save();painter.setClipPath(outline)
        bounds=outline.boundingRect()
        fill=QColor(header);fill.setAlpha(colors.get('canvas_alpha',255))
        painter.fillRect(QRectF(bounds.left(),bounds.top(),bounds.width(),header_bottom-bounds.top()),fill)
        painter.restore()
        painter.save();painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawPath(outline);painter.restore()


class ResizableCard(QGraphicsObject):
    default_size=(0,0)

    def restore_size(self):
        """One size policy for initial attach, layout refresh and undo/reset."""
        self.requested_size=list(self.canvas.window.state.get('text_sizes',{}).get(self.key,self.default_size))

    def init_interaction(self):
        self.setAcceptHoverEvents(True); self.hot=None; self.pressed=None; self.resizing=None
        self.restore_size()

    def resize_rect(self): return QRectF(self.width-22,self.height-22,22,22)

    def resize_corner(self,pos):
        if getattr(self,'proxy',None) is not None and self.collapsed:return None
        for corner,x,y in [('tl',0,0),('tr',self.width-18,0),('bl',0,self.height-18),('br',self.width-22,self.height-22)]:
            if QRectF(x,y,22 if corner=='br' else 18,22 if corner=='br' else 18).contains(pos): return corner

    def actions(self): return {}

    def action_at(self,pos):
        if self.resize_corner(pos): return 'resize'
        return next((name for name,rect in self.actions().items() if rect.contains(pos)),None)

    def hoverMoveEvent(self,event):
        hot=self.action_at(event.pos())
        if hot!=self.hot: self.hot=hot; self.update()
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
            elif action=='weight':
                from .canvas_weight import edit_weight
                edit_weight(self)
            elif action=='collapse':
                if isinstance(self,NodeCard):QTimer.singleShot(0,lambda:canvas.toggle_collapsed(key))
                else:
                    from .module_chrome import toggle_module
                    QTimer.singleShot(0,lambda:toggle_module(canvas,key))
            elif action=='more':
                position=event.screenPos()
                QTimer.singleShot(0,lambda:self.open_menu(position))
            elif action in ('minus','plus'):
                delta=-1 if action=='minus' else 1
                QTimer.singleShot(0,lambda:canvas.change_weight(key,delta=delta))
            elif action.startswith('group_'):
                QTimer.singleShot(0,lambda:self.group_action(action))
        self.update(); event.accept(); return True

    def draw_controls(self,painter):
        colors=canvas_colors(self.canvas)
        for action,rect in self.actions().items():
            if action in ('collapse','more'):continue
            painter.setPen(Qt.PenStyle.NoPen)
            if action=='group_mode':
                painter.setPen(QPen(QColor(colors['line']),1))
                painter.setBrush(QColor(colors['field']))
                painter.drawRoundedRect(rect,6,6)
                painter.setPen(Qt.PenStyle.NoPen)
            if self.pressed==action or self.hot==action:
                painter.setBrush(QColor(colors['selected'] if self.pressed==action else colors['hover']))
                painter.drawRoundedRect(rect,6,6)
            painter.setPen(QColor(colors['text'] if self.hot==action or action=='weight' else colors['secondary']))
            if action in ('minus','plus'):
                from .ui_icons import icon
                center=rect.center();icon(action,colors['secondary']).paint(painter,QRect(round(center.x()-8),round(center.y()-8),16,16))
                continue
            text={'size':'↗','add':'＋ 子元素'}.get(action)
            if action=='weight':
                # The transparent inline editor owns the number while editing.
                if getattr(self,'weight_editor',None) is not None:continue
                text=f"{self.value['weight']/10:.1f}"
            if action.startswith('group_'):
                seq=pm.sequence(self.value);index=next(i for i,g in enumerate(seq['groups'],1) if g['id']==seq['selected'])
                if action in ('group_previous','group_next'):
                    from .ui_icons import icon
                    center=rect.center();icon('chevron-left' if action=='group_previous' else 'chevron-right',colors['secondary']).paint(painter,QRect(round(center.x()-8),round(center.y()-8),16,16));continue
                if action=='group_mode':
                    from .ui_icons import icon,MODE_ICONS
                    center=rect.center();icon(MODE_ICONS[seq['mode']],colors['text']).paint(painter,QRect(round(center.x()-8),round(center.y()-8),16,16))
                    continue
                text=f'{index} / {len(seq["groups"])}'
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,text)


class ModuleProxyWidget(QGraphicsProxyWidget):
    def __init__(self,parent=None):
        super().__init__(parent)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemUsesExtendedStyleOption)
        self.relayout=QTimer(self);self.relayout.setSingleShot(True)
        self.relayout.timeout.connect(self.fit_content)

    def fit_content(self):
        owner=self.layout_owner()
        if owner is not None:owner.layout_card()

    def layout_owner(self):
        owner=self.parentItem()
        if self.widget() is None or owner is None or self.scene() is None:return None
        current=getattr(owner,'layout_is_current',None)
        return owner if current is None or current() else None

    def itemChange(self,change,value):
        result=super().itemChange(change,value)
        if change==QGraphicsItem.GraphicsItemChange.ItemSceneHasChanged and self.scene() is None:
            self.relayout.stop()
        return result

    def setWidget(self,panel):
        previous=self.widget()
        if previous is not None and previous is not panel:previous.setProperty('pcsCanvasBody',None)
        if panel is not None:panel.setProperty('pcsCanvasBody',True)
        super().setWidget(panel)

    def setGeometry(self,rect):
        # QWidget layouts use integer logical pixels. Fractional scene resize
        # requests must not leave a mismatched strip around the embedded body.
        rect=QRectF(rect);panel=self.widget()
        width=math.ceil(rect.width());height=math.ceil(rect.height())
        if panel is not None:
            minimum=panel.minimumSizeHint();width=max(width,minimum.width())
            height=max(height,minimum.height(),panel.heightForWidth(width))
        rect.setSize(QSizeF(width,height))
        super().setGeometry(rect)

    def eventFilter(self,watched,event):
        result=super().eventFilter(watched,event)
        # Status wrapping and font changes can arrive after initial attachment.
        # Fit once after Qt invalidates its layout, without changing saved sizes.
        if watched is self.widget() and event.type()==QEvent.Type.LayoutRequest and self.layout_owner() is not None:
            self.relayout.start(0)
        return result

    def paint(self,painter,option,widget=None):
        # Keep native widget primitives independent from the surrounding
        # card's antialiased outline when the scene has a fractional scale.
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,False)
        panel=self.widget()
        if panel is not None:
            # A proxy normally renders a top-level QWidget window background
            # as well as its contents. The card already owns the opaque body;
            # skip that native rectangle instead of painting over its seams.
            region=QRegion(option.exposedRect.toAlignedRect().intersected(panel.rect()))
            panel.render(painter,region.boundingRect().topLeft(),region,QWidget.RenderFlag.DrawChildren)
        else:super().paint(painter,option,widget)
        painter.restore()


class TextCard(ResizableCard):
    """Hosts the actual list builder, including its existing signals and controls."""
    def __init__(self, canvas):
        super().__init__(); self.canvas=canvas; self.key='__text_output__'; self.start=QPointF()
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable|QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.panel=canvas.window.builder_panel; self.editor=canvas.window.final
        self.proxy=ModuleProxyWidget(self); self.proxy.setPos(14,46)
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
        self.sync_panel_style()
        self.panel.ensurePolished(); self.panel.layout().activate()
        self.restore_size()
        self.layout_card(); self.panel.show()

    def sync_panel_style(self):
        # Reapplying identical QSS repolishes every embedded control and queues
        # another layout pass. New panels and actual theme changes still apply.
        from .theme import canvas_stylesheet
        style=canvas_stylesheet(self.canvas.window)
        if self.panel.styleSheet()!=style:self.panel.setStyleSheet(style)

    def layout_card(self):
        minimum=self.panel.minimumSizeHint(); width,height=self.requested_size
        self.prepareGeometryChange()
        top=54+max(1,len(self.canvas.window.state['multi_output']['outputs']))*24 if self.key=='__result_preview__' and 'multi_output' in self.canvas.window.state else 46
        self.proxy.setGeometry(QRectF(14,top,max(532,minimum.width(),width-28),max(600,minimum.height(),height-top-14)))
        self.width=self.panel.width()+28; self.height=self.panel.height()+top+14; self.update()

    def sync_bounds(self):
        # Qt may enlarge the embedded panel after a deferred font/layout change.
        rect=self.proxy.geometry()
        self.prepareGeometryChange(); self.width=rect.right()+14; self.height=rect.bottom()+14
        self.apply_fold(); self.update()
        self.canvas.functions.layout()

    @property
    def collapsed(self):
        from .module_chrome import module_value
        return bool(module_value(self.canvas,self.key).get('collapsed',False))

    def apply_fold(self):
        self.fold_message=next((control.text() for name in ('status','error','conflicts')
                                if (control:=getattr(self.panel,name,None)) is not None and control.text()),'')
        if self.collapsed:
            ports=[p for p in getattr(self.canvas,'ports',{}).values() if p.parentItem() is self]
            self.height=max([66]+[p.y()+22 for p in ports])
            if self.fold_message:self.height+=QFontMetricsF(self.canvas.font()).height()+18
        self.proxy.setVisible(not self.collapsed)

    def actions(self):
        from .module_chrome import header_rects
        font=self.canvas.font();font.setBold(True)
        return header_rects(self.width,getattr(self,'title','Prompt 控制'),font)[2]

    def open_menu(self,position):
        self.canvas.tools_menu(position)

    def detach(self):
        if self.proxy.widget() is None: return
        self.panel.hide(); self.proxy.setWidget(None); self.panel.setStyleSheet('')
        self.canvas.window.builder_scroll.setWidget(self.panel)
        for effect in self.canvas.window.selection_effects: effect.setEnabled(True)
        self.panel.show()
        if self.saved_sizes: self.canvas.window.split.setSizes(self.saved_sizes)

    def boundingRect(self):
        from .module_chrome import visual_bounds
        return visual_bounds(self.width,self.height)
    def shape(self):
        path=QPainterPath();path.addRoundedRect(QRectF(0,0,self.width,self.height),16,16);return path

    def surface_colors(self):
        return canvas_colors(self.canvas)

    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors=self.surface_colors()
        selected=getattr(self,'dock_hover',False) or self.isSelected()
        painter.setPen(QPen(QColor(colors['accent'] if selected else colors['canvas_line']),1.5 if selected else 1))
        if self.key in getattr(self.canvas,'active_keys',set()):painter.setPen(QPen(QColor(colors['info']),2.5))
        outline=QPainterPath(); outline.addRoundedRect(QRectF(0,0,self.width,self.height),16,16)
        divider=44
        paint_module_surface(painter,self.canvas.functions.outline(self,outline),colors,divider)
        from .module_chrome import paint_header
        paint_header(painter,self.width,getattr(self,'title','Prompt 控制'),self.canvas.font(),colors,
                     self.collapsed,getattr(self,'module_icon','canvas'),self.hot)
        pen=QPen(QColor(colors.get('canvas_divider',colors['canvas_line'])),1);pen.setCosmetic(True);painter.setPen(pen)
        painter.drawLine(QPointF(0,divider),QPointF(self.width,divider))
        painter.setFont(self.canvas.font())
        if self.collapsed and getattr(self,'fold_message',''):
            painter.setPen(QColor(colors.get('danger',colors['text'])))
            text=painter.fontMetrics().elidedText(self.fold_message,Qt.TextElideMode.ElideRight,int(self.width-28))
            painter.drawText(QRectF(14,self.height-32,self.width-28,26),Qt.AlignmentFlag.AlignVCenter,text)
        self.draw_controls(painter)

    def update_text(self):
        if self.proxy.widget() is not None:self.sync_panel_style()
        # The proxy owns its actual bounds. Different cards have different port
        # header heights; a fixed +60 comparison incorrectly relaid them out.
        if self.proxy.widget() is not None: self.sync_bounds()
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

    def contextMenuEvent(self,event): self.open_menu(event.screenPos()); event.accept()


class PreviewCard(TextCard):
    default_size=(500,660)
    def __init__(self,canvas,key='__result_preview__',panel=None):
        super().__init__(canvas); self.key=key; self.title='預覽圖片'
        self.module_icon='scan-eye'
        self.panel=panel if panel is not None else canvas.results; self.init_interaction()

    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.sync_panel_style(); self.panel.ensurePolished(); self.panel.layout().activate()
        self.restore_size()
        self.layout_card(); self.panel.show()

    def layout_card(self):
        minimum=self.panel.minimumSizeHint(); width,height=self.requested_size
        top=54+max(1,len(self.canvas.window.state['multi_output']['outputs']))*24 if self.canvas.window.state.get('multi_output',{}).get('version')==2 else 46
        if self.canvas.window.state.get('multi_output',{}).get('version',1)>=4:top=78
        self.proxy.setGeometry(QRectF(14,top,max(340,minimum.width(),width-28),max(220,minimum.height(),height-top-14)))
        self.sync_bounds()

    def open_menu(self,position): self.canvas.functions.context(self.key,position)


class NodeCard(ResizableCard):
    """A variable-size module containing directly visible, selectable child cards."""
    def __init__(self,canvas,key,value,subtitle,muted=False,parent=None):
        super().__init__(parent); self.canvas=canvas; self.key=key; self.value=value
        self.subtitle=subtitle; self.muted=muted; self.start=QPointF(); self.parts=[]; self.drop_size=None; self.init_interaction()
        self.collapsed=value.get('collapsed',False)
        self.line=max(23,math.ceil(QFontMetricsF(canvas.font()).height())+3)
        flags=QGraphicsItem.GraphicsItemFlag.ItemIsSelectable
        flags|=QGraphicsItem.GraphicsItemFlag.ItemIsMovable
        self.setFlags(flags); self.setToolTip('')
        canvas.cards[key]=self
        ident=canvas.entries[key][0]
        active=comp.active_overlay(value)
        if ident is not None and not self.collapsed:
            for index,part in enumerate(value['children']+value['overlays']):
                overlay=part in value['overlays']
                inactive=muted or not value['enabled'] or not part['enabled'] or (part is not active if overlay else bool(active))
                title='覆蓋中' if overlay and part is active else '覆蓋未生效' if overlay else f'{index+1} · 元素'
                if inactive: title+=' · 未生效'
                child=NodeCard(canvas,ident+':'+part['id'],part,title,inactive,self); self.parts.append(child)
        self.compact=parent is not None and not self.parts
        self.own=comp.render(value)
        self.layout_card(propagate=False)

    def natural_width(self):
        controls=0
        if pm.sequence(self.value) and len(pm.sequence(self.value)['groups'])>1:
            height,index,weight=self.group_control_sizes();controls=3*height+index+200
        return max(320,controls,self.requested_size[0],max((p.natural_width()+28 for p in self.parts),default=0),
                   (self.drop_size[0]+28) if self.drop_size else 0)

    def group_control_sizes(self):
        metrics=QFontMetricsF(self.canvas.font());count=len(pm.sequence(self.value)['groups'])
        return (max(32,math.ceil(metrics.height())+12),
                max(48,math.ceil(metrics.horizontalAdvance(f'{count} / {count}'))+12),
                max(48,math.ceil(metrics.horizontalAdvance(f"{self.value['weight']/10:.1f}"))+16))

    def layout_card(self,propagate=True,available_width=0):
        self.prepareGeometryChange(); requested_w,requested_h=self.requested_size
        self.width=max(self.natural_width(),available_width)
        metrics=QFontMetricsF(self.canvas.font())
        self.text_height=self.line if self.own else 0
        status=[]
        if '覆蓋' in self.subtitle or '未生效' in self.subtitle or self.subtitle=='未分配':status.append(self.subtitle)
        if not self.value['enabled']:status.append('停用')
        if comp.active_overlay(self.value):status.append('已覆蓋')
        self.status=' · '.join(status)
        self.body_top=54+(self.line if self.status else 0)
        self.header_height=self.body_top+(self.text_height+8 if self.own else 0)
        ident=self.canvas.entries[self.key][0]
        self.group_rects={};self.group_bar=QRectF()
        weight_width=max(42,math.ceil(metrics.horizontalAdvance(f"{self.value['weight']/10:.1f}"))+12)
        self.header_extra=weight_width+60 if ident is not None else 0
        weight_x=self.width-50-self.header_extra
        self.minus_rect=QRectF(weight_x,7,28,30)
        self.weight_rect=QRectF(weight_x+28,7,weight_width,30)
        self.plus_rect=QRectF(weight_x+28+weight_width,7,28,30)
        self.prompt_width=self.width-28
        y=self.header_height+6
        if pm.sequence(self.value) and len(pm.sequence(self.value)['groups'])>1:
            height,index_width,_=self.group_control_sizes();height=max(28,self.line)
            index_x=self.width-14-3*height-index_width;control_y=self.body_top-3
            self.group_rects=dict(group_mode=QRectF(index_x,control_y,height,height),
                group_previous=QRectF(index_x+height,control_y,height,height),
                group_index=QRectF(index_x+2*height,control_y,index_width,height),
                group_next=QRectF(index_x+2*height+index_width,control_y,height,height))
            self.prompt_width=index_x-22;y=max(y,control_y+height+8)
        self.add_rect=QRectF()
        if ident is not None and not self.collapsed:
            self.add_rect=QRectF(14,y,self.width-28,28);y+=36
        for child in self.parts:
            child.layout_card(propagate=False,available_width=self.width-28)
            child.setPos(14,y); y+=child.height+10
        self.drop_y=y
        if self.drop_size: y+=self.drop_size[1]+12
        self.height=y+8 if self.collapsed else max(requested_h,y+8)
        from .module_chrome import header_rects
        font=self.canvas.font();font.setBold(True)
        self.title_text,self.title_rect,header=header_rects(self.width,self.value['name'],font,False,self.header_extra)
        self.collapse_rect=header['collapse'];self.more_rect=header['more']
        if propagate and self.parentItem(): self.parentItem().layout_card()
        self.update()

    def actions(self):
        if self.canvas.entries[self.key][0] is None: return {}
        result=dict(minus=self.minus_rect,weight=self.weight_rect,plus=self.plus_rect,collapse=self.collapse_rect,more=self.more_rect,**self.group_rects)
        if not self.collapsed:result['add']=self.add_rect
        return result

    def boundingRect(self):
        from .module_chrome import visual_bounds
        return visual_bounds(self.width,self.height)
    def shape(self):
        path=QPainterPath();path.addRoundedRect(QRectF(0,0,self.width,self.height),12,12);return path
    def selectionRect(self): return QRectF(0,0,self.width,self.header_height)

    def paint(self,painter,option,widget=None):
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors=canvas_colors(self.canvas)
        affected=getattr(self.canvas,'affected_nodes',{}).get(self.key,{})
        active=getattr(self.canvas.window,'_material_editor_session',{}).get('active')
        from .canvas_raster import paint_cached
        key=('text-surface',self.width,self.height,tuple(sorted(colors.items())),self.group_bar.top(),self.group_bar.height(),
             self.muted,self.parentItem() is not None,self.isSelected(),
             bool(affected),bool(affected.get('all')),self.key in getattr(self.canvas,'active_keys',set()),
             active==('canvas',self.key,self.canvas.window.state.get('workspace')))
        paint_cached(painter,key,self.boundingRect(),lambda target:self._paint_surface(target,colors,affected))
        # Text and controls stay vector drawn. In particular, native glyph
        # hinting must not jump when an image's origin changes at fractional zoom.
        self._paint_content(painter,colors)

    def _paint_surface(self,painter,colors,affected):
        border=colors['secondary'] if self.muted or affected.get('all') else '#d4ac51' if affected else colors['canvas_line']
        painter.setPen(QPen(QColor(border),2 if affected or self.muted else 1))
        if self.key in getattr(self.canvas,'active_keys',set()):painter.setPen(QPen(QColor(colors['info']),2.5))
        body=colors['field'] if self.muted else colors['selected'] if self.isSelected() else colors['canvas_inset'] if self.parentItem() else colors['canvas_body']
        painter.setBrush(QColor(body))
        surface=QRectF(0,0,self.width,self.height)
        from .module_chrome import paint_shadow
        paint_shadow(painter,self.shape(),self.parentItem() is not None,covered=QColor(body).alpha()==255)
        painter.drawRoundedRect(surface,12,12); painter.setFont(self.canvas.font())
        if not self.parentItem():
            painter.save();painter.setClipPath(self.shape())
            painter.fillRect(QRectF(0,0,self.width,44),QColor(colors['canvas_header']))
            painter.restore();painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(surface,12,12)
            painter.save();painter.setPen(QPen(QColor(colors['canvas_line']),1))
            painter.drawLine(QPointF(0,44),QPointF(self.width,44));painter.restore()
        if not self.group_bar.isEmpty():
            painter.save();painter.setClipPath(self.shape())
            painter.fillRect(self.group_bar,QColor(colors['field']))
            painter.setPen(QPen(QColor(colors['canvas_line']),1))
            painter.drawLine(self.group_bar.topLeft(),self.group_bar.topRight())
            painter.restore()
        if self.isSelected():
            painter.save();pen=QPen(QColor(colors['accent']),2);pen.setCosmetic(True);painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(surface.adjusted(4,4,-4,-4),9,9);painter.restore()
        active=getattr(self.canvas.window,'_material_editor_session',{}).get('active')
        if active==('canvas',self.key,self.canvas.window.state.get('workspace')):
            painter.save();pen=QPen(QColor(colors['info']),1.5,Qt.PenStyle.DashLine);pen.setCosmetic(True)
            painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(surface.adjusted(7,7,-7,-7),7,7);painter.restore()

    def _paint_content(self,painter,colors):
        from .module_chrome import paint_header
        painter.setFont(self.canvas.font())
        if self.muted: painter.setOpacity(.42)
        metrics=QFontMetricsF(painter.font()); available=self.width-28
        paint_header(painter,self.width,self.value['name'],self.canvas.font(),colors,self.collapsed,hot=self.hot,extra=self.header_extra)
        if self.status:
            painter.setPen(QColor(colors['secondary']))
            painter.drawText(QRectF(14,42,available,self.line),Qt.AlignmentFlag.AlignVCenter,metrics.elidedText(self.status,Qt.TextElideMode.ElideRight,available))
        if self.own:
            painter.setPen(QColor(colors['text']))
            painter.drawText(QRectF(14,self.body_top,self.prompt_width,self.text_height),Qt.AlignmentFlag.AlignVCenter,metrics.elidedText(self.own.replace('\n',' '),Qt.TextElideMode.ElideRight,self.prompt_width))
        self.draw_controls(painter)
        if self.drop_size:
            painter.setOpacity(1); painter.setPen(QPen(QColor(colors['accent']),1,Qt.PenStyle.DashLine)); painter.setBrush(QColor(colors['selected']))
            rect=QRectF(14,self.drop_y,self.width-28,self.drop_size[1]); painter.drawRoundedRect(rect,8,8)
            painter.drawText(rect,Qt.AlignmentFlag.AlignCenter,'放開以加入')

    def hoverMoveEvent(self,event):
        super().hoverMoveEvent(event)
        from .prompt_groups import MODES
        tip=({'group_mode':'變量：'+MODES[pm.sequence(self.value)['mode']],
              'group_previous':'上一項','group_next':'下一項','group_index':'切換內容',
              'weight':'調整權重','minus':'減少權重','plus':'增加權重'}.get(self.hot,'')
             if pm.sequence(self.value) else '')
        if self.toolTip()!=tip:self.setToolTip(tip)

    def mousePressEvent(self,event):
        self.start=self.pos(); self.canvas.view.setFocus(); self.scene().clearFocus()
        if self.press_control(event): return
        super().mousePressEvent(event)

    def group_action(self,action):
        from .prompt_groups import MODES
        seq=pm.sequence(self.value);at=next(i for i,g in enumerate(seq['groups']) if g['id']==seq['selected'])
        def change(index=None,mode=None,random_repeats=None):
            def apply(state):
                root=self.canvas.selected_node(state,self.key)[2];current=pm.sequence(root)
                if mode:current['mode']=mode
                if random_repeats is not None:current['random_repeats']=random_repeats
                if index is not None:current['selected']=current['groups'][max(0,min(len(current['groups'])-1,index))]['id']
                root.pop('supply_cursor',None);root.pop('round_override',None)
                from .prompt_groups import cursor
                root['selection_cursor']=dict(signature=pm.signature(root),position=cursor(current,start=current['selected']))
                from .state_schema import mark_version
                mark_version(state)
            self.canvas.commit(apply,label='切換內容')
        if action in ('group_previous','group_next'):
            change(at+(-1 if action=='group_previous' else 1));return
        if action=='group_mode':
            from .widgets import RoundMenu
            from .ui_icons import icon,MODE_ICONS
            menu=RoundMenu(self.canvas.window)
            for mode,name in MODES.items():
                if mode=='random':
                    submenu=menu.addMenu(icon(MODE_ICONS[mode]),name)
                    for repeat,title in ((True,'允許重複'),(False,'不重複')):
                        choice=submenu.addAction(title,lambda checked=False,r=repeat:change(mode='random',random_repeats=r))
                        choice.setCheckable(True);choice.setChecked(seq['mode']=='random' and seq.get('random_repeats',True)==repeat)
                    continue
                choice=menu.addAction(icon(MODE_ICONS[mode]),name,lambda checked=False,m=mode:change(mode=m))
                choice.setCheckable(True);choice.setChecked(mode==seq['mode'])
            menu.exec(self.canvas.view.mapToGlobal(self.canvas.view.mapFromScene(self.mapToScene(self.group_rects[action].bottomLeft()))));return
        from PySide6.QtWidgets import QSpinBox
        proxy=QGraphicsProxyWidget(self);edit=QSpinBox();edit.setRange(1,len(seq['groups']));edit.setValue(at+1)
        proxy.setWidget(edit);proxy.setGeometry(self.group_rects['group_index']);edit.setFocus();edit.selectAll();done=[False]
        def finish():
            if done[0]:return
            done[0]=True;value=edit.value();proxy.hide();proxy.deleteLater();QTimer.singleShot(0,lambda:change(value-1))
        edit.editingFinished.connect(finish)

    def wheelEvent(self,event):
        if pm.sequence(self.value) and self.group_rects.get('group_index',QRectF()).contains(event.pos()):
            self.group_action('group_previous' if event.delta()>0 else 'group_next');event.accept();return
        event.ignore()

    def mouseReleaseEvent(self,event):
        if self.release_control(event): return
        super().mouseReleaseEvent(event)
        canvas=self.canvas
        if self.pos()!=self.start:
            if canvas.finish_node_drop(self,event.scenePos()): return
            if self.parentItem(): self.parentItem().layout_card(); return
            moves={i.key:[i.pos().x(),i.pos().y()] for i in self.scene().selectedItems() if isinstance(i,NodeCard) and i.parentItem() is None}
            QTimer.singleShot(0,lambda:canvas.move_cards(moves))

    def mouseMoveEvent(self,event):
        if self.resizing or self.pressed: super().mouseMoveEvent(event); return
        super().mouseMoveEvent(event)
        if len(self.scene().selectedItems())==1: self.canvas.preview_node_drop(self,event.scenePos())

    def mouseDoubleClickEvent(self,event):
        if self.press_control(event): return
        canvas=self.canvas; key=self.key
        QTimer.singleShot(0,lambda:canvas.edit(key)); event.accept()

    def contextMenuEvent(self,event):
        self.open_menu(event.screenPos());event.accept()

    def open_menu(self,position):
        self.canvas.view.setFocus(); self.scene().clearFocus()
        if not self.isSelected():self.scene().clearSelection();self.setSelected(True)
        self.canvas.context(self.key,position)


class CanvasView(QGraphicsView):
    transformChanged=Signal()

    def scale(self,sx,sy):
        if sx==1 and sy==1:return
        self._raster_direct_until=time.monotonic()+.12
        super().scale(sx,sy);self.transformChanged.emit()

    def resetTransform(self):
        self._raster_direct_until=time.monotonic()+.12
        super().resetTransform();self.transformChanged.emit()

    def setTransform(self,matrix,combine=False):
        self._raster_direct_until=time.monotonic()+.12
        super().setTransform(matrix,combine);self.transformChanged.emit()

    def paintEvent(self,event):
        from .canvas_raster import paint_scope
        # Zoom changes every card's device raster. Draw vectors while the
        # transform is changing; reuse resumes without a repaint timer.
        # A frame has one appearance. Resolve it once rather than merging
        # workspace settings and deriving the palette for every port/card.
        previous=getattr(self.canvas,'_paint_colors',None)
        self.canvas._paint_colors=canvas_colors(self.canvas)
        try:
            with paint_scope(time.monotonic()>=getattr(self,'_raster_direct_until',0)):
                super().paintEvent(event)
        finally:self.canvas._paint_colors=previous

    def __init__(self,canvas):
        super().__init__(canvas); self.canvas=canvas; self.pan=None; self.pan_button=None; self.box_start=None
        self.click_start=None; self.click_dragged=False; self.blank_press=False
        self.last_blank_click=None; self.pending_palette=None
        self.rubber=QRubberBand(QRubberBand.Shape.Rectangle,self.viewport()); self.box_selection=set()
        from .canvas_interaction import CanvasInteraction
        self.interaction=CanvasInteraction(self);self.pointer_scene=None
        self.setScene(QGraphicsScene(self)); self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        # The top-level window is translucent. Partial repaint/scroll blits can
        # retain old antialiased edges underneath moved items on Windows.
        # Repaint the visible raster surface on changes, never on an idle timer.
        self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.FullViewportUpdate)
        self.viewport().setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent,True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        # Resizing reveals or conceals the right/bottom of the workspace. Keep
        # the readable starting edge (and a user's pan) in the same place.
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setBackgroundBrush(QColor(canvas_colors(canvas)['base'])); self.setMinimumSize(220,64); self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAcceptDrops(True); self.viewport().setAcceptDrops(True)

    def viewportEvent(self,event):
        if event.type()==QEvent.Type.ToolTip:
            # An empty child tip inherits its enclosing CanvasArea's tip.
            # Stop scene help before Qt walks that parent chain or dispatches
            # it to an embedded module widget; Canvas chrome stays separate.
            QToolTip.hideText();event.accept();return True
        return super().viewportEvent(event)

    def dragEnterEvent(self,event):
        if event.mimeData().hasFormat('application/x-pcs-material'):event.acceptProposedAction();return
        from .image_drop import dropped_source
        if dropped_source(event.mimeData())[0]: event.acceptProposedAction(); return
        super().dragEnterEvent(event)
    def dragMoveEvent(self,event):
        if event.mimeData().hasFormat('application/x-pcs-material'):event.acceptProposedAction();return
        from .image_drop import dropped_source
        if dropped_source(event.mimeData())[0]: event.acceptProposedAction(); return
        super().dragMoveEvent(event)
    def dropEvent(self,event):
        if event.mimeData().hasFormat('application/x-pcs-material'):
            key=bytes(event.mimeData().data('application/x-pcs-material')).decode('utf-8')
            self.canvas.window.add_material_to_canvas(key,self.mapToScene(event.position().toPoint()));event.acceptProposedAction();return
        from .image_drop import dropped_source
        if not dropped_source(event.mimeData())[0]: super().dropEvent(event); return
        self.canvas.drop_position=self.mapToScene(event.position().toPoint())
        self.canvas.drop_loader.receive(event.mimeData()); event.acceptProposedAction()

    def set_status_widget(self,widget):
        self.status_widget=widget; widget.setParent(self.viewport())
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        widget.installEventFilter(self)
        self.position_status()

    def set_execution_status_widget(self,widget):
        self.execution_status_widget=widget; widget.setParent(self.viewport())
        widget.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents,True)
        widget.installEventFilter(self)
        self.position_status()

    def position_status(self):
        # All viewport notices share the lower-left corner. The execution bar
        # can move independently; keep both notices outside its hit targets.
        bar=getattr(self.canvas,'execution_bar',None)
        widgets=[]
        for name in ('status_widget','execution_status_widget'):
            widget=getattr(self,name,None)
            if widget is None:continue
            if not widget.text() or (name=='execution_status_widget' and (bar is None or bar.isHidden())):
                widget.hide();continue
            widget.ensurePolished();widget.setWordWrap(False)
            width=min(widget.sizeHint().width(),720,max(1,self.viewport().width()-24))
            widget.setWordWrap(True)
            margins=widget.contentsMargins();padding=margins.top()+margins.bottom()
            lines=1 if name=='execution_status_widget' else 3
            natural=max(widget.minimumSizeHint().height(),widget.heightForWidth(width))
            height=min(natural,padding+widget.fontMetrics().lineSpacing()*lines)
            widgets.append((widget,width,height,natural,padding,lines))
        if not widgets:return
        gap=6;total=sum(item[2] for item in widgets)+gap*(len(widgets)-1)
        width=max(item[1] for item in widgets)
        obstacles=[item.geometry() for item in (bar,getattr(self.canvas,'zoom_controls',None))
                   if item is not None and not item.isHidden()]
        intervals=[(12,max(12,self.viewport().height()-12))]
        # Use an actual free interval, including below a bar dragged upward.
        # Keeping a capped notice stack also protects short, high-DPI views.
        for obstacle in obstacles:
            rect=obstacle.adjusted(-6,-6,6,6)
            if rect.right()<12 or rect.left()>=12+width:continue
            remaining=[]
            for top,bottom in intervals:
                if rect.bottom()<top or rect.top()>=bottom:remaining.append((top,bottom));continue
                if top<rect.top():remaining.append((top,rect.top()))
                if rect.bottom()+1<bottom:remaining.append((rect.bottom()+1,bottom))
            intervals=remaining
        fitting=[span for span in intervals if span[1]-span[0]>=total]
        top,bottom=max(fitting,key=lambda span:span[1]) if fitting else max(intervals,key=lambda span:span[1]-span[0],default=(0,0))
        available=bottom-top
        if total>available:
            # Preserve the persistent execution hint before a transient notice.
            resized=[];remaining=available
            for widget,width,height,natural,padding,lines in reversed(widgets):
                count=min(lines,max(0,(remaining-padding)//widget.fontMetrics().lineSpacing()))
                if not count:widget.hide();continue
                height=min(height,padding+count*widget.fontMetrics().lineSpacing())
                resized.insert(0,(widget,width,height,natural,padding,count));remaining-=height+gap
            widgets=resized;total=sum(item[2] for item in widgets)+gap*max(0,len(widgets)-1)
        y=bottom-total
        for widget,width,height,natural,padding,lines in widgets:
            widget._canvas_status_lines=lines
            widget._canvas_status_elided=height<natural or (lines==1 and widget.fontMetrics().horizontalAdvance(widget.text())>width-widget.contentsMargins().left()-widget.contentsMargins().right())
            widget.setAccessibleName(widget.text())
            widget.setGeometry(12,y,width,height);widget.show();widget.raise_();widget.update()
            y+=height+gap

    def eventFilter(self,watched,event):
        if event.type()==QEvent.Type.Paint and getattr(watched,'_canvas_status_elided',False):
            # Paint an abbreviated view only: QLabel.text() and accessibility
            # keep the original message for subsequent resize/layout passes.
            painter=QPainter(watched);option=QStyleOption();option.initFrom(watched)
            watched.style().drawPrimitive(QStyle.PrimitiveElement.PE_Widget,option,painter,watched)
            rect=watched.contentsRect();painter.setClipRect(rect)
            painter.setPen(watched.palette().color(watched.foregroundRole()))
            text=watched.text();layout=QTextLayout(text,watched.font())
            wrap=QTextOption();wrap.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere);layout.setTextOption(wrap)
            layout.beginLayout();lines=[]
            for index in range(watched._canvas_status_lines+1):
                line=layout.createLine()
                if not line.isValid():break
                line.setLineWidth(rect.width());line.setPosition(QPointF(0,index*watched.fontMetrics().lineSpacing()));lines.append(line)
            layout.endLayout()
            for index,line in enumerate(lines[:watched._canvas_status_lines]):
                if index==watched._canvas_status_lines-1 and len(lines)>watched._canvas_status_lines:
                    remainder=text[line.textStart():].replace('\n',' ')
                    shortened=watched.fontMetrics().elidedText(remainder,Qt.TextElideMode.ElideRight,rect.width())
                    painter.drawText(QRectF(rect.left(),rect.top()+line.y(),rect.width(),watched.fontMetrics().lineSpacing()),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,shortened)
                else:line.draw(painter,QPointF(rect.topLeft()))
            painter.end();return True
        return super().eventFilter(watched,event)

    def resizeEvent(self,event):
        super().resizeEvent(event); self.position_status()
        canvas=self.canvas;state=canvas.window.state
        if (canvas.isVisible() and not getattr(canvas,'_hidden_view',None)
                and getattr(canvas,'_presented_owner',None)==(canvas.window.store,state.get('workspace'))
                and state.get('canvas_view')):
            # A show/layout pass can resize the viewport more than once. Keep
            # its saved center current so the next restore retains the same
            # top-left edge instead of centering the previous viewport size.
            point=self.viewportTransform().inverted()[0].map(QPointF(self.viewport().width()/2,self.viewport().height()/2))
            state['canvas_view']=[self.transform().m11(),point.x(),point.y()]

    def drawBackground(self,painter,rect):
        painter.save()
        colors=canvas_colors(self.canvas);painter.fillRect(rect,QColor(colors['canvas_background']))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing,False)
        dot=QColor(colors['line']);dot.setAlpha(90)
        pen=QPen(dot,1); pen.setCosmetic(True); painter.setPen(pen)
        spacing=28 if self.transform().m11()>=.6 else 112
        xs=range(math.floor(rect.left()/spacing)*spacing,math.ceil(rect.right()),spacing)
        ys=range(math.floor(rect.top()/spacing)*spacing,math.ceil(rect.bottom()),spacing)
        # Keep a modest margin so small pans/zoom steps reuse the point array,
        # instead of rebuilding thousands of Python points on every wheel event.
        block=spacing*16
        x0=math.floor(xs.start/block)*block; y0=math.floor(ys.start/block)*block
        x1=math.ceil(xs.stop/block)*block; y1=math.ceil(ys.stop/block)*block
        key=(spacing,x0,y0,x1,y1)
        if getattr(self,'_grid_key',None)!=key:
            self._grid_key=key
            batches=getattr(self,'_grid_batches',{})
            points=batches.pop(key,None)
            if points is None:
                points=QPolygonF([QPointF(x,y) for x in range(x0,x1,spacing) for y in range(y0,y1,spacing)])
            batches[key]=points
            if len(batches)>4:batches.pop(next(iter(batches)))
            self._grid_batches=batches;self._grid_points=points
        painter.setClipRect(QRectF(xs.start-spacing/2,ys.start-spacing/2,len(xs)*spacing,len(ys)*spacing),Qt.ClipOperation.IntersectClip)
        painter.drawPoints(self._grid_points)
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
        # Anchor to this event, not QGraphicsView's previous mouse/context menu.
        point=event.position().toPoint();before=self.mapToScene(point)
        anchor=self.transformationAnchor();self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        factor=1.15**(event.angleDelta().y()/120);scale=self.transform().m11();target=max(.12,min(3.,scale*factor))
        self.scale(target/scale,target/scale)
        after=self.mapToScene(point);delta=after-before
        self.translate(delta.x(),delta.y());self.setTransformationAnchor(anchor)
        self.pointer_scene=before
        if hasattr(self.canvas,'save_view'):self.canvas.save_view()
        event.accept()

    def mousePressEvent(self,event):
        workspace=hasattr(self.canvas,'data')
        if workspace and self.interaction.press(event):return
        item=self.itemAt(event.position().toPoint())
        if event.button()==Qt.MouseButton.LeftButton:
            self.click_start=event.position().toPoint(); self.click_dragged=False
            self.blank_press=item is None and event.modifiers()==Qt.KeyboardModifier.NoModifier
            self.pending_palette=None
        if not workspace and event.button()==Qt.MouseButton.LeftButton and event.modifiers()&Qt.KeyboardModifier.ControlModifier and not isinstance(item,QGraphicsProxyWidget):
            self.setFocus(); self.scene().clearFocus(); self.box_start=event.position().toPoint()
            self.box_selection={i.key for i in self.scene().selectedItems() if isinstance(i,NodeCard)}
            self.rubber.setGeometry(QRect(self.box_start,self.box_start)); self.rubber.show(); event.accept(); return
        background=self.interaction.background_at(event.position().toPoint())[0] if workspace else item is None
        if event.button()==Qt.MouseButton.MiddleButton or (event.button()==Qt.MouseButton.LeftButton and background):
            self.setFocus(); self.scene().clearFocus()
            if not event.modifiers()&Qt.KeyboardModifier.ControlModifier:self.scene().clearSelection()
            self.pan=event.position(); self.pan_button=event.button(); self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept(); return
        super().mousePressEvent(event)

    def mouseMoveEvent(self,event):
        if hasattr(self.canvas,'data') and self.interaction.move(event):return
        if self.click_start is not None and (event.position().toPoint()-self.click_start).manhattanLength()>=QApplication.startDragDistance():
            self.click_dragged=True; self.last_blank_click=None; self.pending_palette=None
        if self.box_start is not None:
            self.rubber.setGeometry(QRect(self.box_start,event.position().toPoint()).normalized()); event.accept(); return
        if self.pan is not None:
            delta=event.position()-self.pan; self.pan=event.position()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-round(delta.x()))
            self.verticalScrollBar().setValue(self.verticalScrollBar().value()-round(delta.y()))
            event.accept(); return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self,event):
        if hasattr(self.canvas,'data') and self.interaction.release(event):return
        if event.button()==Qt.MouseButton.LeftButton:
            point=event.position().toPoint()
            still=self.click_start is not None and not self.click_dragged and (point-self.click_start).manhattanLength()<QApplication.startDragDistance()
            position=self.pending_palette if still and self.blank_press else None
            self.last_blank_click=(point,time.monotonic()) if still and self.blank_press and position is None else None
            self.click_start=None; self.pending_palette=None
            if position is not None: QTimer.singleShot(0,lambda:self.canvas.palette(position))
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
        if hasattr(self.canvas,'data') and self.interaction.double(event):return
        if event.button()==Qt.MouseButton.LeftButton and self.itemAt(event.position().toPoint()) is None:
            prior=self.last_blank_click
            valid=prior is not None and not self.click_dragged and event.modifiers()==Qt.KeyboardModifier.NoModifier and (event.position().toPoint()-prior[0]).manhattanLength()<QApplication.startDragDistance() and time.monotonic()-prior[1]<=QApplication.doubleClickInterval()/1000
            self.mousePressEvent(event)
            if valid: self.pending_palette=self.mapToScene(event.position().toPoint())
            event.accept(); return
        super().mouseDoubleClickEvent(event)

    def keyPressEvent(self,event):
        if hasattr(self.canvas,'data') and self.interaction.key(event):return
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
        if event.reason()==QContextMenuEvent.Reason.Mouse and self.interaction.suppress_context_menu:
            self.interaction.suppress_context_menu=False;event.accept();return
        card=self.interaction.card_at(event.pos())
        if card is not None:self.scene().clearSelection();card.setSelected(True)
        if self.itemAt(event.pos()) is None:
            self.canvas.tools_menu(event.globalPos()); event.accept(); return
        super().contextMenuEvent(event)
