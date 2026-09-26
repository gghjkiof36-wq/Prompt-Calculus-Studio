"""Order-only drag gestures with graphics-view scale and the original grab point."""
import uuid
from PySide6.QtCore import Qt,QMimeData,Signal,QTimer
from PySide6.QtGui import QDrag,QPainter,QPen,QColor
from PySide6.QtWidgets import QListWidget,QAbstractItemView
from .views import BuilderTree

class OutputOrderList(QListWidget):
    orderChanged=Signal()
    removeRequested=Signal(list)
    mime='application/x-prompt-studio-output-order'
    drag_preview=BuilderTree.drag_preview
    def __init__(self):
        super().__init__(); self._press_pos=None; self._drag_token=b''; self._source=None; self._hint=None
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove); self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setDropIndicatorShown(False); self.setAutoScroll(True)
        self._scroll=QTimer(self); self._scroll.setInterval(70); self._scroll.timeout.connect(self.drag_scroll); self._position=None; self._direction=0
    def mousePressEvent(self,event):
        self._press_pos=event.position().toPoint() if event.button()==Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)
    def keyPressEvent(self,event):
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace) and event.modifiers()==Qt.KeyboardModifier.NoModifier:
            keys=[item.data(Qt.ItemDataRole.UserRole) for item in self.selectedItems()]
            if keys:self.removeRequested.emit(keys)
            event.accept();return
        super().keyPressEvent(event)
    def startDrag(self,actions):
        item=self.currentItem()
        if item is None: return
        self._source=item.data(Qt.ItemDataRole.UserRole); self._drag_token=uuid.uuid4().hex.encode('ascii')
        drag=QDrag(self); mime=QMimeData(); mime.setData(self.mime,self._drag_token); drag.setMimeData(mime)
        pixmap,point=self.drag_preview(); drag.setPixmap(pixmap); drag.setHotSpot(point)
        try: drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._scroll.stop()
            self._source=None; self._drag_token=b''; self._hint=None; self._press_pos=None
            self.viewport().update(); drag.deleteLater()
    def owns_drag(self,event): return bool(self._source and self._drag_token and bytes(event.mimeData().data(self.mime))==self._drag_token)
    def dragEnterEvent(self,event):
        if self.owns_drag(event): event.acceptProposedAction()
        else: event.ignore()
    def insertion(self,pos):
        item=self.itemAt(pos)
        if item is None: return self.count()
        return self.row(item)+(pos.y()>self.visualItemRect(item).center().y())
    def dragMoveEvent(self,event):
        if not self.owns_drag(event): event.ignore(); return
        self._position=event.position().toPoint(); self._hint=self.insertion(self._position)
        self._direction=-1 if self._position.y()<24 else 1 if self._position.y()>self.viewport().height()-24 else 0
        if self._direction: self._scroll.start()
        else: self._scroll.stop()
        self.viewport().update(); event.acceptProposedAction()
    def drag_scroll(self):
        if not self._source: self._scroll.stop(); return
        bar=self.verticalScrollBar(); bar.setValue(bar.value()+self._direction*max(1,bar.singleStep()))
        self._hint=self.insertion(self._position); self.viewport().update()
    def dragLeaveEvent(self,event): self._scroll.stop(); self._hint=None; self.viewport().update(); event.accept()
    def move_row(self,source,index):
        old=next((i for i in range(self.count()) if self.item(i).data(Qt.ItemDataRole.UserRole)==source),None)
        if old is None: return
        index=max(0,min(self.count(),index)); index-=int(old<index)
        if index==old: return
        item=self.takeItem(old); self.insertItem(index,item); self.setCurrentItem(item); self.orderChanged.emit()
    def dropEvent(self,event):
        self._scroll.stop()
        if not self.owns_drag(event): event.ignore(); return
        self.move_row(self._source,self.insertion(event.position().toPoint()))
        self._hint=None; self.viewport().update(); event.setDropAction(Qt.DropAction.MoveAction); event.accept()
    def paintEvent(self,event):
        super().paintEvent(event)
        if self._hint is None or not self.count(): return
        y=self.visualItemRect(self.item(self._hint)).top() if self._hint<self.count() else self.visualItemRect(self.item(self.count()-1)).bottom()
        painter=QPainter(self.viewport()); painter.setPen(QPen(QColor('#99c6ff'),2)); painter.drawLine(4,y,self.viewport().width()-4,y)
