"""Inline source groups and owner-preserving text module ordering."""
import uuid
from shiboken6 import isValid
from PySide6.QtCore import Qt,QSize,QMimeData,QTimer,QSignalBlocker
from PySide6.QtGui import QDrag,QPainter,QPen,QColor,QBrush
from PySide6.QtWidgets import QTreeWidget,QTreeWidgetItem,QAbstractItemView,QApplication
from .views import BuilderTree
from .module_lists import SourceDelegate,DETAIL_ROLE,source_kind
from .flow_data import text_sources,source_name
from .ui_icons import icon
from . import composition as comp

ROLE=Qt.ItemDataRole.UserRole


class OutputSources(QTreeWidget):
    mime='application/x-pcs-output-sources'
    drag_preview=BuilderTree.drag_preview

    def __init__(self,canvas,output):
        super().__init__();self.canvas=canvas;self.output=output;self.signature=None;self.colors={}
        self._press_pos=None;self._source=None;self._drag_token=b'';self._hint=None
        self.setObjectName('ModuleSourceOrder');self.setHeaderHidden(True)
        self.setIndentation(0);self.setRootIsDecorated(False)
        self.setIconSize(QSize(16,16));self.setItemDelegate(SourceDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.TextElideMode.ElideRight);self.setExpandsOnDoubleClick(False)
        self.setAnimated(False);self.setAccessibleName('文字來源與模組排序')
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction);self.setDropIndicatorShown(False)
        self.itemExpanded.connect(lambda item:self.save_fold(item,False))
        self.itemCollapsed.connect(lambda item:self.save_fold(item,True))
        self._scroll=QTimer(self);self._scroll.setInterval(70);self._scroll.timeout.connect(self.drag_scroll)
        self._direction=0;self._position=None

    @staticmethod
    def token(item):return tuple(item.data(0,ROLE)) if item else None

    def refresh(self,state,colors):
        output=state['multi_output']['outputs'][self.output];canvases=state['multi_output']['canvases']
        collapsed=output.get('source_collapsed',[]);rows=[]
        for key in text_sources(output):
            children=[]
            for mid in canvases.get(key,{}).get('members',[]):
                root=state['uses'][mid]
                name=root['name']+(f" · ×{root['weight']/10:.1f}" if root['weight']!=10 else '')+(' · 停用' if not root['enabled'] else '')
                children.append((mid,name,comp.render(root)))
            rows.append((key,source_name(state,key),source_kind(state,key),children))
        self.colors=colors
        signature=(rows,list(collapsed),colors['surface'],colors['text'],colors['secondary'],self.fontMetrics().height())
        if signature==self.signature:return
        self.signature=signature;self.colors=colors
        selected=self.token(self.currentItem());scroll=self.verticalScrollBar().value()
        with QSignalBlocker(self):
            self.clear()
            for key,name,kind,children in rows:
                parent=QTreeWidgetItem([name]);parent.setData(0,ROLE,('source',key))
                parent.setData(0,DETAIL_ROLE,str(len(children))+' 項' if key in canvases else kind)
                if key not in canvases:parent.setIcon(0,icon('arrow-down-up',colors['text'],16))
                parent.setBackground(0,QBrush(QColor(colors['surface'])))
                parent.setSizeHint(0,QSize(0,max(40,self.fontMetrics().height()+20)))
                parent.setToolTip(0,'拖曳調整來源順序；點擊展開或收合' if children else name)
                self.addTopLevelItem(parent)
                for mid,title,prompt in children:
                    child=QTreeWidgetItem([title]);child.setData(0,ROLE,('member',key,mid))
                    child.setIcon(0,icon('grip-vertical',colors['secondary'],16));child.setToolTip(0,prompt)
                    child.setSizeHint(0,QSize(0,max(36,self.fontMetrics().height()+16)));parent.addChild(child)
                    if self.token(child)==selected:self.setCurrentItem(child)
                parent.setExpanded(key not in collapsed)
                if self.token(parent)==selected:self.setCurrentItem(parent)
        self.fit_visible_rows();self.verticalScrollBar().setValue(scroll)

    def fit_visible_rows(self):
        count=sum(1+(self.topLevelItem(i).childCount() if self.topLevelItem(i).isExpanded() else 0)
                  for i in range(self.topLevelItemCount()))
        row_height=max(42,self.fontMetrics().height()+20)
        self.setMinimumHeight(max(96,min(6,count)*row_height+8))

    def save_fold(self,item,collapsed):
        key=self.token(item)[1];output=self.canvas.data()['outputs'][self.output]
        values=set(output.get('source_collapsed',[]))
        if collapsed:values.add(key)
        else:values.discard(key)
        output['source_collapsed']=[source for source in text_sources(output) if source in values]
        self.signature=None;self.fit_visible_rows()
        card=self.canvas.outputs.get(self.output)
        if card:
            card.panel.layout().activate();card.layout_card()
        self.canvas.window.changed('layout')

    def mousePressEvent(self,event):
        self._press_pos=event.position().toPoint() if event.button()==Qt.MouseButton.LeftButton else None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self,event):
        item=self.itemAt(event.position().toPoint());press=self._press_pos;self._press_pos=None
        super().mouseReleaseEvent(event)
        if (item and item.parent() is None and item.childCount() and press is not None
                and event.button()==Qt.MouseButton.LeftButton
                and (event.position().toPoint()-press).manhattanLength()<QApplication.startDragDistance()
                and self.visualItemRect(item).contains(press)):
            item.setExpanded(not item.isExpanded())

    def keyPressEvent(self,event):
        item=self.currentItem()
        if event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Space) and item and item.childCount():
            item.setExpanded(not item.isExpanded());event.accept();return
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):event.accept();return
        super().keyPressEvent(event)

    def startDrag(self,actions):
        self._source=self.token(self.currentItem())
        if self._source is None:return
        self._drag_token=uuid.uuid4().hex.encode('ascii');drag=QDrag(self);mime=QMimeData()
        mime.setData(self.mime,self._drag_token);drag.setMimeData(mime)
        pixmap,anchor=self.drag_preview();drag.setPixmap(pixmap);drag.setHotSpot(anchor)
        try:drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._scroll.stop();self._source=None;self._drag_token=b'';self._hint=None;self._press_pos=None
            self.viewport().update();drag.deleteLater()

    def owns_drag(self,event):
        return bool(self._source and self._drag_token and bytes(event.mimeData().data(self.mime))==self._drag_token)

    def insertion(self,pos):
        target=self.itemAt(pos)
        if target is None:
            if self._source[0]=='source':return self.topLevelItemCount()
            return None
        if self._source[0]=='source':
            while target.parent():target=target.parent()
            return self.indexOfTopLevelItem(target)+int(pos.y()>self.visualItemRect(target).center().y())
        parent=target.parent()
        if parent is None or self.token(parent)[1]!=self._source[1]:return None
        return parent.indexOfChild(target)+int(pos.y()>self.visualItemRect(target).center().y())

    def dragEnterEvent(self,event):
        if self.owns_drag(event):event.acceptProposedAction()
        else:event.ignore()

    def dragMoveEvent(self,event):
        if not self.owns_drag(event):event.ignore();return
        self._position=event.position().toPoint();self._hint=self.insertion(self._position)
        self._direction=-1 if self._position.y()<24 else 1 if self._position.y()>self.viewport().height()-24 else 0
        if self._direction:self._scroll.start()
        else:self._scroll.stop()
        self.viewport().update()
        if self._hint is None:event.ignore()
        else:event.acceptProposedAction()

    def drag_scroll(self):
        if not self._source:self._scroll.stop();return
        bar=self.verticalScrollBar();bar.setValue(bar.value()+self._direction*max(1,bar.singleStep()))
        self._hint=self.insertion(self._position);self.viewport().update()

    def dragLeaveEvent(self,event):
        self._scroll.stop();self._hint=None;self.viewport().update();event.accept()

    def dropEvent(self,event):
        self._scroll.stop()
        index=self.insertion(event.position().toPoint()) if self.owns_drag(event) else None
        if index is None:event.ignore();return
        self.move_entry(self._source,index);self._hint=None;self.viewport().update()
        event.setDropAction(Qt.DropAction.MoveAction);event.accept()

    def move_entry(self,source,index):
        data=self.canvas.data();kind,owner=source[:2]
        before=list(text_sources(data['outputs'][self.output]) if kind=='source' else data['canvases'][owner]['members'])
        key=owner if kind=='source' else source[2]
        if key not in before:return
        old=before.index(key);index=max(0,min(len(before),index))-int(old<index)
        if old==index:return
        ordered=list(before);ordered.insert(index,ordered.pop(old));canvas=self.canvas;output=self.output
        window=canvas.window;store=window.store;workspace=window.state['workspace']
        context=window.canvas_edit_context;card=canvas.outputs.get(output)
        container=canvas.containers.get(owner) if kind=='member' else None
        def apply(state):
            current=state['multi_output']
            target=current['outputs'].get(output)
            if target is None or (kind=='member' and owner not in text_sources(target)):
                raise ValueError('排序來源已移除，請重新選擇。')
            value=current['outputs'].get(output) if kind=='source' else current['canvases'].get(owner)
            actual=text_sources(value) if kind=='source' and value else value.get('members') if value else None
            if actual!=before:raise ValueError('排序內容已更新，請重新拖曳。')
            if kind=='source':
                canvases=[k for k in ordered if k in current['canvases']]
                value.update(text_sources=ordered,canvases=canvases,canvas=canvases[0] if canvases else None)
            else:value['members']=ordered
        def commit_if_current():
            # IDs can survive workspace copies and whole-document imports.
            # Keep normal transactions valid, but never revive an old UI visit.
            if (not isValid(window) or not isValid(canvas) or window.closing
                    or window.store is not store or window.canvas is not canvas
                    or window.canvas_edit_context is not context or window.state['workspace']!=workspace
                    or card is None or canvas.outputs.get(output) is not card or not isValid(card)
                    or card.panel.order is not self):return
            if kind=='member' and (container is None or canvas.containers.get(owner) is not container
                                   or not isValid(container)):return
            canvas.commit(apply,label='排列文字來源' if kind=='source' else '排列文字模組')
        QTimer.singleShot(0,self,commit_if_current)

    def paintEvent(self,event):
        super().paintEvent(event)
        if self._hint is None or not self._source:return
        if self._source[0]=='source':items=[self.topLevelItem(i) for i in range(self.topLevelItemCount())]
        else:
            parent=next((self.topLevelItem(i) for i in range(self.topLevelItemCount()) if self.token(self.topLevelItem(i))[1]==self._source[1]),None)
            items=[parent.child(i) for i in range(parent.childCount())] if parent else []
        if not items:return
        at=min(self._hint,len(items)-1);rect=self.visualItemRect(items[at])
        y=rect.top() if self._hint<len(items) else rect.bottom()
        if self._source[0]=='source' and self._hint>=len(items) and items[-1].isExpanded() and items[-1].childCount():
            y=self.visualItemRect(items[-1].child(items[-1].childCount()-1)).bottom()
        painter=QPainter(self.viewport());painter.setPen(QPen(QColor(self.colors['accent']),2))
        painter.drawLine(rect.left(),y,self.viewport().width()-4,y)
