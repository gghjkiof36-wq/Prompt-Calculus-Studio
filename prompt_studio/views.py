"""Paint only visible rows; no per-item widgets, shadows or animation loops."""
import uuid
from PySide6.QtCore import Qt, QSize, QRectF, QPointF, QPoint, Signal, QMimeData, QTimer, QEvent
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen, QPainterPath, QDrag
from PySide6.QtWidgets import QStyledItemDelegate, QStyle, QTreeWidget, QAbstractItemView, QListWidget
from .theme import font_pixels

DETAIL_ROLE=int(Qt.ItemDataRole.UserRole)+1


def weight_buttons(rect):
    rect = QRectF(rect)
    return (QRectF(rect.right()-83, rect.top()+12, 30, 30), QRectF(rect.right()-47, rect.top()+12, 30, 30))


class PromptList(QListWidget):
    weightRequested=Signal(str, int)

    def __init__(self):
        super().__init__()
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDragDropOverwriteMode(False)
        self.setDropIndicatorShown(True)

    def startDrag(self,actions):
        if getattr(self,'weight_press',False): return
        self.dragged=True
        super().startDrag(actions)

    def mousePressEvent(self, event):
        self.weight_press=False; self.dragged=False
        item=self.itemAt(event.position().toPoint())
        if item and event.button()==Qt.MouseButton.LeftButton:
            for rect, delta in zip(weight_buttons(self.visualItemRect(item)), (-1, 1)):
                if rect.contains(event.position()):
                    self.weight_press=True
                    if self.isEnabled(): self.weightRequested.emit(item.data(Qt.ItemDataRole.UserRole), delta)
                    event.accept(); return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        if getattr(self,'weight_press',False): self.weight_press=False; event.accept(); return
        if getattr(self,'dragged',False): self.dragged=False; event.accept(); return
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item and any(r.contains(event.position()) for r in weight_buttons(self.visualItemRect(item))):
            self.mousePressEvent(event); return
        super().mouseDoubleClickEvent(event)


class PromptDelegate(QStyledItemDelegate):
    def __init__(self,window):
        super().__init__(window); self.window=window

    def sizeHint(self,option,index):
        font=QFont(option.font); font.setPixelSize(font_pixels(self.window.state["settings"]["ui_size"]))
        height=QFontMetrics(font).height()*4+44
        if self.window.state["settings"]["density"]=="compact": height-=16
        return QSize(180,height)

    def paint(self,painter,option,index):
        data=index.data(DETAIL_ROLE) or {}; chosen=data.get("chosen",False)
        hover=bool(option.state & QStyle.StateFlag.State_MouseOver)
        rect=QRectF(option.rect).adjusted(1,5,-4,-5)
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(QPen(QColor('#d5ad4b' if data.get('affected') else "#646464" if chosen else "#444444" if hover else "#292929"),1.5 if data.get('affected') else 1))
        painter.setBrush(QColor("#2b2b2b" if chosen else "#242424" if hover else "#1b1b1b")); painter.drawRoundedRect(rect,12,12)
        font=QFont(option.font); font.setPixelSize(font_pixels(self.window.state["settings"]["ui_size"]))
        fm=QFontMetrics(font); line=fm.height(); x=rect.left()+18; y=rect.top()+17
        circle=QRectF(x,y+2,17,17)
        painter.setPen(QPen(QColor("#dedede" if chosen else "#626262"),1)); painter.setBrush(QColor("#dedede") if chosen else Qt.BrushStyle.NoBrush)
        painter.drawEllipse(circle)
        if chosen:
            painter.setPen(QPen(QColor("#222222"),1.8)); path=QPainterPath(); path.moveTo(x+4,y+10); path.lineTo(x+7,y+13); path.lineTo(x+13,y+6); painter.drawPath(path)
        icon=index.data(Qt.ItemDataRole.DecorationRole)
        has_icon=icon is not None and not icon.isNull()
        width=int(rect.width()-64-(76 if has_icon else 0)); tx=x+29
        font.setWeight(QFont.Weight.DemiBold); painter.setFont(font); painter.setPen(QColor("#f2f2f2"))
        title_width=max(20,int(rect.right()-88-tx))
        painter.drawText(QRectF(tx,y-2,title_width,line+4),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(data.get("name",""),Qt.TextElideMode.ElideRight,title_width))
        for area, symbol in zip(weight_buttons(option.rect), ('−','+')):
            local=self.window.library.viewport().mapFromGlobal(self.window.cursor().pos())
            hot=area.contains(local) and self.window.state['draft'] is None
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#555555' if hot else '#303030'))
            painter.drawRoundedRect(area,6,6); painter.setPen(QColor('#ffffff' if hot else '#aaaaaa'))
            painter.drawText(area,Qt.AlignmentFlag.AlignCenter,symbol)
        font.setWeight(QFont.Weight.Normal); painter.setFont(font); painter.setPen(QColor("#bdbdbd"))
        prompt=data.get("prompt","").replace("\n"," ")
        painter.drawText(QRectF(tx,y+line+8,width,line+2),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(prompt,Qt.TextElideMode.ElideRight,width))
        font.setPixelSize(font_pixels(max(9,self.window.state["settings"]["ui_size"]-1))); painter.setFont(font); painter.setPen(QColor("#969696"))
        aliases=("  ·  ".join(data.get("aliases",[])[:3]) or data.get("module_name",""))+f"  ·  ×{data.get('weight',10)/10:.1f}"
        painter.drawText(QRectF(tx,y+line*2+16,width,line+2),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(aliases,Qt.TextElideMode.ElideRight,width))
        if has_icon:
            preview=QRectF(rect.right()-83,rect.bottom()-69,64,64)
            clip=QPainterPath(); clip.addRoundedRect(preview,8,8); painter.setClipPath(clip)
            icon.paint(painter,preview.toRect()); painter.setClipping(False)
        painter.restore()


class BuilderDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        return QSize(130,QFontMetrics(option.font).height()+22)

    def paint(self,painter,option,index):
        kind,_=index.data(Qt.ItemDataRole.UserRole) or ("","")
        group=kind=="group"; hover=bool(option.state & QStyle.StateFlag.State_MouseOver)
        selected=bool(option.state & QStyle.StateFlag.State_Selected)
        rect=QRectF(option.rect).adjusted(2,3,-5,-3)
        painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setOpacity(getattr(self.parent(),"activity",1.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#444444" if selected else "#333333" if hover else "#292929" if not group else "#202020"))
        painter.drawRoundedRect(rect,8,8)
        font=QFont(option.font); font.setWeight(QFont.Weight.DemiBold if group else QFont.Weight.Normal)
        painter.setFont(font); painter.setPen(QColor("#dddddd" if group else "#eeeeee"))
        x=rect.left()+12
        if group:
            # A small disclosure mark, without a separate square button.
            expanded=bool(option.state & QStyle.StateFlag.State_Open)
            path=QPainterPath(); cy=rect.center().y()
            if expanded: path.moveTo(x,cy-2); path.lineTo(x+4,cy+2); path.lineTo(x+8,cy-2)
            else: path.moveTo(x+2,cy-4); path.lineTo(x+6,cy); path.lineTo(x+2,cy+4)
            painter.setPen(QPen(QColor("#aaaaaa"),1.3)); painter.drawPath(path); x+=22
        else:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor('#858585'))
            for offset in (-4,0,4): painter.drawEllipse(QPointF(x+6,rect.center().y()+offset),1,1)
            x+=20
        painter.setPen(QColor("#eeeeee")); width=int(rect.right()-x-32)
        painter.drawText(QRectF(x,rect.top(),width,rect.height()),Qt.AlignmentFlag.AlignVCenter,QFontMetrics(font).elidedText(str(index.data()),Qt.TextElideMode.ElideRight,width))
        if not group and hover:
            font.setPixelSize(22); painter.setFont(font)
            painter.setPen(QColor("#cccccc")); painter.drawText(QRectF(rect.right()-29,rect.top(),24,rect.height()),Qt.AlignmentFlag.AlignCenter,"×")
        painter.restore()


class BuilderTree(QTreeWidget):
    moveRequested=Signal(object,object,bool)
    removeRequested=Signal(object)
    rejected=Signal(str)

    def __init__(self):
        super().__init__(); self.setHeaderHidden(True); self.setRootIsDecorated(False); self.setIndentation(14)
        self.activity=1.0; self.setAnimated(True)
        self.setMouseTracking(True); self.setItemDelegate(BuilderDelegate(self)); self.setUniformRowHeights(True)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(False); self.setAutoScroll(True)
        self._drag_source=None; self._drop_hint=None; self._drag_token=b''; self._press_pos=None
        self._scroll_timer=QTimer(self); self._scroll_timer.setInterval(70); self._scroll_timer.timeout.connect(self.drag_scroll)
        self._drag_pos=None; self._scroll_direction=0

    def key(self,item): return tuple(item.data(0,Qt.ItemDataRole.UserRole)) if item else None

    def mousePressEvent(self,event):
        self._press_pos=event.position().toPoint() if event.button()==Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def drag_preview(self):
        rect=self.visualItemRect(self.currentItem()); pixmap=self.viewport().grab(rect)
        anchor=(self._press_pos if self._press_pos is not None else rect.center())-rect.topLeft()
        scale=1.0; host=self
        while host is not None and host.graphicsProxyWidget() is None: host=host.parentWidget()
        proxy=host.graphicsProxyWidget() if host is not None else None
        if proxy is not None and proxy.scene() and proxy.scene().views():
            transform=proxy.deviceTransform(proxy.scene().views()[0].viewportTransform())
            scale=(transform.m11()**2+transform.m12()**2)**0.5
        if abs(scale-1.0)>.001:
            pixmap=pixmap.scaled(max(1,round(pixmap.width()*scale)),max(1,round(pixmap.height()*scale)),Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
        return pixmap,QPoint(round(anchor.x()*scale),round(anchor.y()*scale))

    def startDrag(self,actions):
        self._drag_source=self.key(self.currentItem())
        if not self._drag_source: return
        # Own the drag lifecycle: the native item view must not remove rows
        # after our state-driven drop handler has rebuilt the tree.
        self._drag_token=uuid.uuid4().hex.encode('ascii')
        drag=QDrag(self); data=QMimeData(); data.setData("application/x-prompt-studio-order",self._drag_token); drag.setMimeData(data)
        pixmap,anchor=self.drag_preview(); drag.setPixmap(pixmap); drag.setHotSpot(anchor)
        try: drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._scroll_timer.stop(); drag.deleteLater()
            self._drag_source=None; self._drop_hint=None; self._drag_token=b''; self._press_pos=None; self.viewport().update()

    def owns_drag(self,event):
        if not self._drag_source: return False
        if event.source() is self: return True
        # The graphics proxy may replace the native source widget. Only the
        # token from this tree's active gesture may cross that boundary.
        return bool(self._drag_token and bytes(event.mimeData().data('application/x-prompt-studio-order'))==self._drag_token)

    def dragEnterEvent(self,event):
        if self.owns_drag(event): event.acceptProposedAction()
        else: event.ignore()

    def drop_target(self,pos):
        target=self.itemAt(pos)
        if not self._drag_source: return None
        if target is None and self.topLevelItemCount():
            if self._drag_source[0] in ('group','use'): target=self.topLevelItem(self.topLevelItemCount()-1)
            else:
                current=self.currentItem(); parent=current.parent() if current else None
                if parent: target=parent.child(parent.childCount()-1)
        if target is None: return None
        if self._drag_source[0] in ('group','use'):
            while target.parent(): target=target.parent()
        else:
            current=self.currentItem()
            if current is None or target.parent()!=current.parent() or target.parent() is None: return None
        rect=self.visualItemRect(target)
        return self.key(target),pos.y()>rect.center().y(),rect

    def dragMoveEvent(self,event):
        self._drag_pos=event.position().toPoint()
        self._scroll_direction=-1 if self._drag_pos.y()<28 else 1 if self._drag_pos.y()>self.viewport().height()-28 else 0
        if self.owns_drag(event) and self._scroll_direction: self._scroll_timer.start()
        else: self._scroll_timer.stop()
        result=self.drop_target(event.position().toPoint()) if self.owns_drag(event) else None
        if result:
            self._drop_hint=result; event.acceptProposedAction()
        else: self._drop_hint=None; event.ignore()
        self.viewport().update()

    def drag_scroll(self):
        if not self._drag_source: self._scroll_timer.stop(); return
        bar=self.verticalScrollBar(); bar.setValue(bar.value()+self._scroll_direction*max(8,bar.singleStep()))
        if self._drag_pos is not None: self._drop_hint=self.drop_target(self._drag_pos)
        self.viewport().update()

    def dragLeaveEvent(self,event):
        self._scroll_timer.stop()
        self._drop_hint=None; self.viewport().update(); super().dragLeaveEvent(event)

    def dropEvent(self,event):
        self._scroll_timer.stop()
        result=self.drop_target(event.position().toPoint()) if self.owns_drag(event) else None
        source=self._drag_source; self._drop_hint=None; self.viewport().update()
        if result and source:
            target,after,_=result; self.moveRequested.emit(source,target,after); event.acceptProposedAction()
        else:
            self.rejected.emit("提示詞可在原群組內排序；拖曳群組名稱可移動整組。"); event.ignore()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self._drop_hint:
            _,after,rect=self._drop_hint; painter=QPainter(self.viewport()); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QPen(QColor("#cccccc"),2,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
            y=rect.bottom() if after else rect.top(); painter.drawLine(rect.left()+8,y,rect.right()-8,y)

    def mouseReleaseEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item and event.button()==Qt.MouseButton.LeftButton:
            key=self.key(item); rect=self.visualItemRect(item)
            if key[0]!="group" and event.position().x()>rect.right()-34:
                self.removeRequested.emit(key); return
            if key[0]=="group" and event.position().x()<rect.left()+35:
                item.setExpanded(not item.isExpanded()); return
        super().mouseReleaseEvent(event)

    def move_current(self,delta):
        current=self.currentItem()
        if not current: return
        parent=current.parent()
        index=parent.indexOfChild(current) if parent else self.indexOfTopLevelItem(current)
        count=parent.childCount() if parent else self.topLevelItemCount()
        if 0<=index+delta<count:
            target=parent.child(index+delta) if parent else self.topLevelItem(index+delta)
            self.moveRequested.emit(self.key(current),self.key(target),delta>0)

    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Backspace,Qt.Key.Key_Delete):
            key=self.key(self.currentItem())
            if key and key[0]!='group': self.removeRequested.emit(key)
            event.accept(); return
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier and event.key() in (Qt.Key.Key_Up,Qt.Key.Key_Down):
            self.move_current(-1 if event.key()==Qt.Key.Key_Up else 1); event.accept(); return
        super().keyPressEvent(event)
