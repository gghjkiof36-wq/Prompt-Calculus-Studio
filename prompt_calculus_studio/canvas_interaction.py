"""Selection and clipboard gestures, independent from embedded text editors."""
from PySide6.QtCore import Qt,QPointF,QRect,QRectF,QTimer
from PySide6.QtWidgets import QGraphicsItem,QGraphicsProxyWidget,QApplication


class CanvasInteraction:
    def __init__(self,view):
        self.view=view;self.canvas=view.canvas;self.box=None;self.group=None
        self.box_dragged=False;self.box_button=None;self.suppress_context_menu=False

    def card_at(self,point):
        from .flow_items import Port
        item=self.view.itemAt(point)
        while item is not None:
            if isinstance(item,(QGraphicsProxyWidget,Port)):return None
            if hasattr(item,'key') and item.flags()&QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:return item
            item=item.parentItem()

    def background_at(self,point):
        """Container bodies are workspace; their headers and controls are not."""
        from .flow_items import CanvasContainer
        item=self.view.itemAt(point)
        if item is None:return True,None
        if isinstance(item,CanvasContainer):
            local=item.mapFromScene(self.view.mapToScene(point))
            if not item.collapsed and local.y()>44 and not item.corner_at(local):return True,item.key
        return False,None

    def pointer(self,event):
        self.view.pointer_scene=self.view.mapToScene(event.position().toPoint())

    def begin_box(self,event,scope):
        right=event.button()==Qt.MouseButton.RightButton
        ctrl_left=(event.button()==Qt.MouseButton.LeftButton and
                   event.modifiers()==Qt.KeyboardModifier.ControlModifier)
        if not (right or ctrl_left):return False
        # Ctrl-left is the ordinary replacement marquee. Only the retained
        # Ctrl-right shortcut adds to the previous selection.
        additive=right and bool(event.modifiers()&Qt.KeyboardModifier.ControlModifier)
        self.box=(event.position().toPoint(),
                  {i.key for i in self.view.scene().selectedItems() if hasattr(i,'key')} if additive else set(),scope)
        self.box_button=event.button();self.box_dragged=False;self.suppress_context_menu=False
        self.pointer(event);self.view.setFocus();self.view.scene().clearFocus()
        event.accept();return True

    def press(self,event):
        self.pointer(event);view=self.view;point=event.position().toPoint();card=self.card_at(point)
        self.suppress_context_menu=False
        ctrl=bool(event.modifiers()&Qt.KeyboardModifier.ControlModifier)
        background,scope=self.background_at(point)
        if background and self.begin_box(event,scope):return True
        if event.button()==Qt.MouseButton.RightButton:
            if card is not None:view.scene().clearSelection();card.setSelected(True)
            return False
        if event.button()!=Qt.MouseButton.LeftButton:return False
        # Let CanvasView pan before selecting a container or starting a group drag.
        if background:return False
        action=corner=None
        if card is not None:
            local=card.mapFromScene(view.pointer_scene)
            action=getattr(card,'action_at',lambda p:None)(local)
            corner=getattr(card,'corner_at',lambda p:None)(local)
            if action or corner:return False
        if card is not None and ctrl:
            card.setSelected(not card.isSelected());view.setFocus();view.scene().clearFocus();event.accept();return True
        if card is not None:
            if not card.isSelected():view.scene().clearSelection();card.setSelected(True)
            selected=[i for i in view.scene().selectedItems() if hasattr(i,'key') and i.flags()&QGraphicsItem.GraphicsItemFlag.ItemIsMovable]
            if len(selected)>1 and not action and not corner:
                moving={i.key:i for i in selected if not any(other.isAncestorOf(i) for other in selected if other is not i)}
                for item in list(moving.values()):
                    for key in getattr(self.canvas,'data',lambda:{})().get('canvases',{}).get(item.key,{}).get('members',[]):
                        if key in self.canvas.cards:moving[key]=self.canvas.cards[key]
                # Container members are scene siblings, so adding them can
                # introduce an ancestor of an explicitly selected child.
                moving={key:item for key,item in moving.items() if not any(other.isAncestorOf(item) for other in moving.values() if other is not item)}
                self.group=(QPointF(view.pointer_scene),{k:(i,QPointF(i.scenePos())) for k,i in moving.items()})
                event.accept();return True
        return False

    def move(self,event):
        self.pointer(event)
        if self.group:
            start,moving=self.group;delta=self.view.pointer_scene-start
            for item,pos in moving.values():
                point=pos+delta
                item.setPos(item.parentItem().mapFromScene(point) if item.parentItem() else point)
            self.canvas.update_lines();event.accept();return True
        if self.box:
            point=event.position().toPoint()
            if not self.box_dragged and (point-self.box[0]).manhattanLength()>=QApplication.startDragDistance():
                # Windows delivers its context-menu event after button release.
                self.box_dragged=True;self.suppress_context_menu=self.box_button==Qt.MouseButton.RightButton
                self.view.scene().clearSelection();self.view.rubber.show()
            if self.box_dragged:self.view.rubber.setGeometry(QRect(self.box[0],point).normalized())
            event.accept();return True
        return False

    def release(self,event):
        if event.button()==Qt.MouseButton.LeftButton and self.group:
            _,moving=self.group;self.group=None
            positions={k:[item.scenePos().x(),item.scenePos().y()] for k,(item,_) in moving.items()}
            QTimer.singleShot(0,lambda:self.canvas.move_selection(positions));event.accept();return True
        if not self.box or event.button()!=self.box_button:return False
        start,previous,scope=self.box;self.box=None;self.box_button=None;self.view.rubber.hide()
        if not self.box_dragged:event.accept();return True
        self.box_dragged=False
        rect=self.view.mapToScene(QRect(start,event.position().toPoint()).normalized()).boundingRect()
        from .canvas_items import NodeCard
        from .flow_items import CanvasContainer,FlowLine,Port
        for item in self.view.scene().items():
            if not hasattr(item,'key') or isinstance(item,(FlowLine,Port)) or not item.flags()&QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:continue
            if scope:
                hit=isinstance(item,NodeCard) and item.key.split(':')[0] in self.canvas.data()['canvases'][scope]['members']
            else:
                hit=item.parentItem() is None and (not isinstance(item,NodeCard) or not any(item.key in c['members'] for c in self.canvas.data()['canvases'].values()))
            if hasattr(item,'selectionRect'):intersects=rect.intersects(item.mapRectToScene(item.selectionRect()))
            elif hasattr(item,'rect'):intersects=rect.intersects(item.mapRectToScene(item.rect()))
            else:intersects=item.mapToScene(item.shape()).intersects(rect)
            hit=hit and rect.width()>3 and rect.height()>3 and intersects
            item.setSelected(hit or item.key in previous)
        event.accept();return True

    def double(self,event):
        if event.button()!=Qt.MouseButton.LeftButton or event.modifiers()!=Qt.KeyboardModifier.NoModifier:return False
        point=event.position().toPoint();self.pointer(event);card=self.card_at(point);pos=QPointF(self.view.pointer_scene)
        from .canvas_items import NodeCard
        from .flow_items import CanvasContainer
        self.box=None;self.group=None;self.view.rubber.hide()
        if isinstance(card,NodeCard):
            if card.action_at(card.mapFromScene(pos)):return False
            key=card.key;QTimer.singleShot(0,lambda:self.canvas.edit(key));event.accept();return True
        if isinstance(card,CanvasContainer):
            key=card.key;QTimer.singleShot(0,lambda:self.canvas.quick_text(key,pos));event.accept();return True
        if self.view.itemAt(point) is None:
            QTimer.singleShot(0,lambda:self.canvas.function_palette(pos));event.accept();return True
        return False

    def key(self,event):
        focus=self.view.scene().focusItem()
        while focus is not None:
            if isinstance(focus,QGraphicsProxyWidget):return False
            focus=focus.parentItem()
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            self.canvas.delete_selected();event.accept();return True
        if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
            if event.key()==Qt.Key.Key_C:self.canvas.copy_selected()
            elif event.key()==Qt.Key.Key_V:self.canvas.paste_selected()
            elif event.key()==Qt.Key.Key_Z:self.canvas.undo()
            elif event.key()==Qt.Key.Key_Y:self.canvas.redo()
            elif event.key()==Qt.Key.Key_A:
                for item in self.view.scene().items():
                    if hasattr(item,'key') and item.parentItem() is None and item.flags()&QGraphicsItem.GraphicsItemFlag.ItemIsSelectable:item.setSelected(True)
            else:return False
            event.accept();return True
        return False
