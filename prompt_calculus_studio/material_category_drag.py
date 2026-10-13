"""Same-editor material moves; separate from category/folder reordering."""
import copy
import uuid
from PySide6.QtCore import Qt
from PySide6.QtGui import QPainter, QColor, QPen
from . import materials
from .material_folder_navigation import FolderNavigation, ROW

MIME = 'application/x-pcs-material-category'


class MaterialDrag:
    def __init__(self, page, ident):
        self.page=page; self.ident=ident; self.token=uuid.uuid4().hex.encode()
        window=page.window
        self.owner=(window.store,window.state['workspace'],window.canvas_edit_context)
        self.original=copy.deepcopy(materials.item(window.state,ident))

    def current(self):
        page=self.page; window=page.window
        if (self.owner!=(window.store,window.state['workspace'],window.canvas_edit_context) or
                not page.isVisible() or page.active_dialog):return False
        try:return materials.item(window.state,self.ident)==self.original
        except ValueError:return False

    def move(self, destination):
        if not self.current():return False
        def apply(state):
            if materials.item(state,self.ident)!=self.original:raise ValueError('素材已更新，請重新拖曳。')
            materials.move_category(state,self.ident,destination)
        return self.page.window.canvas.commit(apply,label='移動素材分類')


class EditorCategoryNavigation(FolderNavigation):
    def __init__(self, page):
        super().__init__(); self.page=page; self._material_hover=None; self._material_drag=None

    def material_drag(self, event):
        listing=getattr(self.page,'listing',None)
        active=getattr(listing,'category_drag',None)
        if (active and event.source() is listing and bytes(event.mimeData().data(MIME))==active.token
                and active.current()):return active
        return None

    def destination(self, position):
        if not self.viewport().rect().contains(position):return None
        item=self.itemAt(position); data=item.data(ROW) if item else None
        if data and data['id'] is not None and data['kind']!='folder':return data['id']
        return None

    def dragEnterEvent(self,event):
        if event.mimeData().hasFormat(MIME):
            if self.material_drag(event):event.setDropAction(Qt.DropAction.MoveAction); event.accept()
            else:event.ignore()
        else:super().dragEnterEvent(event)

    def dragMoveEvent(self,event):
        if not event.mimeData().hasFormat(MIME):return super().dragMoveEvent(event)
        self._clear_drop(); active=self.material_drag(event)
        if active is None:event.ignore(); return
        self._material_drag=active; self._drag_position=event.position().toPoint()
        self._material_hover=self.destination(self._drag_position)
        self._scroll_timer.start(); self.viewport().update()
        if self._material_hover is None or self._material_hover==active.original['module']:event.ignore()
        else:event.setDropAction(Qt.DropAction.MoveAction); event.accept()

    def _scroll_drag(self):
        if self._material_drag is None:return super()._scroll_drag()
        if not self._material_drag.current():self._clear_drop(); return
        y=self._drag_position.y(); height=self.viewport().height()
        direction=-1 if y<24 else 1 if y>height-24 else 0
        if direction:
            bar=self.verticalScrollBar(); bar.setValue(bar.value()+direction*max(8,self.fontMetrics().height()))
            self._material_hover=self.destination(self._drag_position); self.viewport().update()

    def _clear_drop(self):
        self._material_hover=None; self._material_drag=None
        super()._clear_drop()

    def dropEvent(self,event):
        if not event.mimeData().hasFormat(MIME):return super().dropEvent(event)
        active=self.material_drag(event); destination=self.destination(event.position().toPoint())
        self._clear_drop()
        if active is None or destination is None or not active.move(destination):event.ignore(); return
        event.setDropAction(Qt.DropAction.MoveAction); event.accept()
        self.page.refresh()

    def paintEvent(self,event):
        super().paintEvent(event)
        if self._material_hover is None:return
        item=next((self.item(i) for i in range(self.count())
                   if self.item(i).data(Qt.ItemDataRole.UserRole)==self._material_hover),None)
        if item:
            painter=QPainter(self.viewport()); painter.setPen(QPen(QColor(self.colors()['accent']),2))
            painter.drawRoundedRect(self.visualItemRect(item).adjusted(2,2,-2,-2),7,7)
