"""Transactional thumbnail grid for unconnected v7 image sources."""
import copy
import uuid
from PySide6.QtCore import Qt,QSize,QRect,QTimer
from PySide6.QtGui import QPainter,QPen,QColor
from PySide6.QtWidgets import QListWidget,QListWidgetItem,QAbstractItemView,QStyledItemDelegate,QStyle
from .widgets import StudioDialog,button,row,RoundMenu
from .source_list import items,replace_items
from .source_thumbnails import Thumbnails
from .output_order import OutputOrderList
from .ui_icons import icon

ROLE=Qt.ItemDataRole.UserRole


class ThumbnailDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):return self.parent().gridSize()

    def paint(self,painter,option,index):
        grid=self.parent();token,source=index.data(ROLE);rect=option.rect.adjusted(4,4,-4,-4)
        painter.save();colors=grid.dialog.colors
        current=token==grid.dialog.start_token
        painter.setPen(QPen(QColor(colors['accent'] if current else colors['line']),2 if current else 1))
        painter.setBrush(QColor(colors['surface']));painter.drawRoundedRect(rect,6,6)
        area=rect.adjusted(8,8,-8,-28);picture=grid.dialog.images.get(source['relative'])
        if picture is not None and not picture.isNull():
            size=picture.size().scaled(area.size(),Qt.AspectRatioMode.KeepAspectRatio)
            target=QRect(0,0,size.width(),size.height());target.moveCenter(area.center())
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform);painter.drawImage(target,picture)
        painter.setPen(QColor(colors['text']));painter.drawText(rect.adjusted(8,0,-8,-5),Qt.AlignmentFlag.AlignBottom|Qt.AlignmentFlag.AlignLeft,str(index.row()+1))
        if option.state & QStyle.StateFlag.State_Selected:
            painter.setPen(QPen(QColor(colors['secondary']),1,Qt.PenStyle.DashLine));painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(rect.adjusted(3,3,-3,-3),4,4)
        if not grid.dialog.locked and option.state & QStyle.StateFlag.State_MouseOver:
            close=QRect(rect.right()-26,rect.top()+4,22,22)
            painter.fillRect(close,QColor(colors['surface']));icon('close',colors['text']).paint(painter,close)
        painter.restore()


class ThumbnailGrid(OutputOrderList):
    def __init__(self,dialog):
        super().__init__();self.dialog=dialog;self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setResizeMode(QListWidget.ResizeMode.Adjust);self.setMovement(QListWidget.Movement.Snap)
        self.setGridSize(QSize(160,148));self.setIconSize(QSize(144,108));self.setUniformItemSizes(True)
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove);self.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection);self.setMouseTracking(True)
        self.setItemDelegate(ThumbnailDelegate(self));self.setAccessibleName('圖片清單')
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu);self.customContextMenuRequested.connect(self.context)
        self.model().rowsMoved.connect(lambda *_:self.viewport().update())

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if getattr(self,'_fitting',False):return
        self._fitting=True
        try:
            margins=self.viewportMargins()
            width=self.viewport().width()+margins.left()+margins.right()
            columns=max(1,width//160);cell=max(1,(width-1)//columns)
            # QListView's inclusive wrapping boundary needs one spare pixel
            # inside the viewport. Balance it with the outer rounding margins.
            spare=max(0,width-columns*cell-1)
            self.setViewportMargins((spare+1)//2,0,spare//2,0)
            self.setGridSize(QSize(cell,148))
        finally:self._fitting=False

    def insertion(self,pos):
        item=self.itemAt(pos)
        if item is None:return self.count()
        return self.row(item)+int(pos.x()>self.visualItemRect(item).center().x())

    def startDrag(self,actions):
        if not self.dialog.locked:super().startDrag(actions)

    def move_row(self,source,index):
        if not self.dialog.locked:super().move_row(source,index)

    def paintEvent(self,event):
        QListWidget.paintEvent(self,event)
        if self._hint is None or not self.count():return
        rect=self.visualItemRect(self.item(min(self._hint,self.count()-1)))
        x=rect.left() if self._hint<self.count() else rect.right()
        painter=QPainter(self.viewport());painter.setPen(QPen(QColor(self.dialog.colors['accent']),2))
        painter.drawLine(x,rect.top()+4,x,rect.bottom()-4)

    def mouseReleaseEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item is not None and not self.dialog.locked and event.button()==Qt.MouseButton.LeftButton:
            rect=self.visualItemRect(item).adjusted(4,4,-4,-4)
            if QRect(rect.right()-26,rect.top()+4,22,22).contains(event.position().toPoint()):
                self.dialog.remove_item(item);event.accept();return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Delete and not self.dialog.locked:
            if self.currentItem():self.dialog.remove_item(self.currentItem())
            return
        super().keyPressEvent(event)

    def context(self,pos):
        item=self.itemAt(pos)
        if item is None or self.dialog.locked:return
        menu=RoundMenu(self);menu.addAction('從這張開始',lambda:self.dialog.start_from(item))
        menu.open_at(self.viewport().mapToGlobal(pos))


class SourceListDialog(StudioDialog):
    def __init__(self,panel):
        self.panel=panel;self.window=panel.owner.window;super().__init__(self.window)
        self.original=copy.deepcopy(panel.owner.data()['images'][panel.key]);self.workspace=self.window.state['workspace'];self.context=self.window.canvas_edit_context
        self.locked=self.window.comfy.input_flow.source_active(panel.key);self.closed=False;self.images={}
        from .canvas_items import canvas_colors
        self.colors=canvas_colors(panel.owner.canvas);self.resize(760,600)
        self.add_button=button('加入圖片',self.add_images);self.clear_button=button('清空',self.clear_items,'Quiet')
        self.heading_row.insertWidget(2,self.add_button);self.heading_row.insertWidget(3,self.clear_button)
        self.listing=ThumbnailGrid(self);self.body.addWidget(self.listing,1)
        self.done_button=button('完成',self.apply_changes,'Primary');self.body.addLayout(row(None,self.done_button))
        self.thumbs=Thumbnails(self,self.window);self.thumbs.ready.connect(self.thumbnail)
        self.start_token=None
        self.append_images(items(self.original))
        if self.listing.count():self.start_token=self.listing.item(min(self.original.get('index',0),self.listing.count()-1)).data(ROLE)[0]
        self.add_button.setEnabled(not self.locked);self.clear_button.setEnabled(not self.locked)
        if self.locked:self.listing.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.finished.connect(self.finish)
        self.poll=QTimer(self);self.poll.setInterval(80);self.poll.timeout.connect(self.busy);self.poll.start()
        if self.locked:self.window.notice('執行期間圖片清單已固定；可檢視及切圖，不能改變這批清單。')

    def append_images(self,images):
        if self.closed:return
        for source in images:
            item=QListWidgetItem();item.setToolTip(source['name'])
            item.setData(ROLE,(uuid.uuid4().hex,copy.deepcopy(source)));self.listing.addItem(item)
        if self.start_token is None and self.listing.count():self.start_token=self.listing.item(0).data(ROLE)[0]
        self.update_title();QTimer.singleShot(0,self.load_thumbnails)

    def load_thumbnails(self):
        if self.closed or self.window.is_closed():return
        self.thumbs.load([(source['relative'],self.window.store.directory/source['relative']) for source in self.ordered()])

    def thumbnail(self,key,image):
        if not self.closed:self.images[key]=image;self.listing.viewport().update()

    def ordered(self):return [self.listing.item(i).data(ROLE)[1] for i in range(self.listing.count())]

    def update_title(self):self.setWindowTitle(f'圖片清單 · {self.listing.count()} 張')

    def start_from(self,item):
        if not self.locked:self.start_token=item.data(ROLE)[0];self.listing.viewport().update()

    def remove_item(self,item):
        if self.locked:return
        at=self.listing.row(item);token=item.data(ROLE)[0];self.listing.takeItem(at)
        if token==self.start_token:
            self.start_token=self.listing.item(min(at,self.listing.count()-1)).data(ROLE)[0] if self.listing.count() else None
        self.update_title();self.listing.viewport().update()
        self.load_thumbnails()

    def clear_items(self):
        if self.locked:return
        self.listing.clear();self.start_token=None;self.update_title();self.load_thumbnails()

    def busy(self):
        from .source_import import active
        busy=active(self.window,self.panel.key)
        self.add_button.setEnabled(not self.locked and not busy);self.done_button.setEnabled(not busy)

    def add_images(self):
        from .source_picker import add_menu
        add_menu(self.panel,self.append_images).open_for(self.add_button)

    def apply_changes(self):
        if self.locked:self.accept();return
        from .source_import import active
        if active(self.window,self.panel.key):return
        from .flow_data import incoming
        current=self.window.state.get('canvas_functions',{}).get('images',{}).get(self.panel.key)
        if (self.window.is_closed() or self.workspace!=self.window.state['workspace'] or self.context is not self.window.canvas_edit_context or current!=self.original or incoming(self.window.state,self.panel.key,'image')):
            self.window.notice('圖片來源已更新，請重新開啟清單。');return
        if self.window.comfy.input_flow.source_active(self.panel.key):
            self.window.notice('執行期間圖片清單已固定；可檢視及切圖，不能改變這批清單。');return
        ordered=self.ordered();index=next((i for i in range(self.listing.count()) if self.listing.item(i).data(ROLE)[0]==self.start_token),0)
        if ordered==items(self.original) and index==self.original.get('index',0):self.accept();return
        if self.panel.owner.canvas.commit(lambda state:replace_items(state,self.panel.key,ordered,index,self.window.store.directory),label='編輯圖片清單') is not False:
            self.window.comfy.input_flow.direct.reset_source(self.panel.key);self.accept()

    def finish(self,*_):
        self.closed=True;self.poll.stop();self.thumbs.cancel()
        task=self.panel.import_task
        if task and task.accepted==self.append_images:task.cancel()
