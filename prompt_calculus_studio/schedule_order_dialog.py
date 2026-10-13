"""Draft-only queue ordering. Apply rechecks the live queue before any writes."""
import copy
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QListWidgetItem,QStyledItemDelegate,QStyleOptionViewItem
from .widgets import StudioDialog,button,row,label
from .output_order import OutputOrderList
from .canvas_items import canvas_colors
from .module_chrome import apply_module_dialog,style_action
from .theme import font_pixels
from .ui_icons import icon


class QueueDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        opt=QStyleOptionViewItem(option);opt.rect.adjust(26,0,-38,0)
        super().paint(painter,opt,index)
        waiting=index.data(int(Qt.ItemDataRole.UserRole)+1)
        icon('grip-vertical' if waiting else 'lock',option.palette.color(option.palette.ColorRole.Text).name(),16).paint(
            painter,option.rect.left()+5,option.rect.center().y()-8,16,16)
        if index.data(int(Qt.ItemDataRole.UserRole)+1):
            icon('close',option.palette.color(option.palette.ColorRole.Text).name(),20).paint(
                painter,option.rect.right()-30,option.rect.center().y()-10,20,20)


class QueueDraftList(OutputOrderList):
    def __init__(self,dialog):
        super().__init__();self.dialog=dialog;self.setItemDelegate(QueueDelegate(self))
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.setAccessibleName('拖曳排列等待項目');self.removeRequested.connect(self.remove_waiting)

    def startDrag(self,actions):
        item=self.currentItem()
        if item and self.dialog.rows[item.data(Qt.ItemDataRole.UserRole)]['status']=='waiting':super().startDrag(actions)

    def move_row(self,source,index):
        ids=self.dialog.ordered_ids();entry=self.dialog.rows[source]
        if entry['status']!='waiting':return
        old=ids.index(source);target=max(0,min(len(ids)-1,index-(index>old)))
        crossed=[self.dialog.rows[key] for key in ids[min(old,target):max(old,target)+1]]
        if any(r['status']!='waiting' or r['owner']!=entry['owner'] for r in crossed):return
        super().move_row(source,index)

    def remove_waiting(self,ids):
        for index in reversed(range(self.count())):
            key=self.item(index).data(Qt.ItemDataRole.UserRole)
            if key in ids and self.dialog.rows[key]['status']=='waiting':self.takeItem(index)

    def mouseReleaseEvent(self,event):
        item=self.itemAt(event.position().toPoint())
        if item and event.button()==Qt.MouseButton.LeftButton and event.position().x()>self.visualItemRect(item).right()-38:
            self.remove_waiting([item.data(Qt.ItemDataRole.UserRole)]);event.accept();return
        super().mouseReleaseEvent(event)


class ScheduleOrderDialog(StudioDialog):
    def __init__(self,panel):
        from .stage_widgets import NAMES,entry_image
        super().__init__(panel.canvas.window);self.panel=panel
        self.setWindowTitle('管理預排程');self.resize(640,470)
        colors=canvas_colors(panel.canvas)
        apply_module_dialog(self,colors,max(16,font_pixels(panel.canvas.window.state['settings']['ui_size'])))
        self.rows={r['id']:copy.deepcopy(r) for r in panel.entries()}
        self.listing=QueueDraftList(self);self.listing.setProperty('moduleChoices',True);self.listing.setIconSize(QSize(48,48))
        for key,entry in self.rows.items():
            source=entry_image(entry);waiting=entry['status']=='waiting'
            text=entry['label']+' · '+NAMES.get(panel.runner.entry_status(entry),entry['status'])
            if source:text+='\n'+source['name']
            item=QListWidgetItem(QIcon(str(panel.canvas.window.store.directory/source['relative'])) if source else QIcon(),text)
            item.setData(Qt.ItemDataRole.UserRole,key);item.setData(int(Qt.ItemDataRole.UserRole)+1,waiting)
            item.setSizeHint(QSize(0,max(72,self.listing.fontMetrics().height()*2+24)))
            item.setToolTip('拖曳排序；Delete 移除' if waiting else '已提交項目，保留目前位置')
            if not waiting:item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            self.listing.addItem(item)
        self.body.addWidget(self.listing,1)
        if not self.rows:self.body.addWidget(label('沒有等待執行的任務','Subtle'))
        self.error=label('','ConflictNotice',True);self.error.hide();self.body.addWidget(self.error)
        self.more=button('更多',lambda:panel.more_actions(self.more),'Quiet')
        self.save_button=button('套用',self.apply_changes,'Primary');style_action(self.save_button,colors,primary=True)
        self.cancel_button=button('取消',self.reject,'Quiet')
        self.body.addLayout(row(self.more,None,self.cancel_button,self.save_button))
        self.listing.setFocus()

    def ordered_ids(self):return [self.listing.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.listing.count())]

    def apply_changes(self):
        panel=self.panel;store=panel.runner.store
        try:
            current={r['id']:r for r in panel.entries()}
            if current!=self.rows or list(current)!=list(self.rows):raise ValueError('排程內容已更新，請關閉後重新開啟管理。這次調整尚未套用。')
            ids=self.ordered_ids();removed=set(self.rows)-set(ids)
            if any(self.rows[key]['status']!='waiting' for key in removed):raise ValueError('已提交項目不能移除。')
            waiting=[key for key,r in self.rows.items() if r['status']=='waiting']
            orders={}
            for owner in {self.rows[key]['owner'] for key in waiting}:
                owned=[r['id'] for r in store.rows('entry',owner=owner,active=True) if r['status']=='waiting']
                changed=[key for key in ids if key in waiting and self.rows[key]['owner']==owner]
                changed.extend(key for key in owned if key in removed)
                choices=iter(changed);orders[owner]=[next(choices) if key in waiting else key for key in owned]
            def apply():
                for owner,order in orders.items():store.reorder(owner,order)
                for key in removed:panel.runner.remove_entry(key)
            from .queue_history import perform
            perform(panel.canvas,waiting,apply,'管理等待項目')
        except ValueError as exc:self.error.setText(str(exc));self.error.show();return
        panel.refresh();self.accept()
