"""Compact v7 source card. Legacy panels and serialized fields are retained."""
import weakref
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QFrame,QVBoxLayout,QButtonGroup,QSizePolicy
from .canvas_functions import SourcePanel,SourceMenuButton
from .image_preview import SourcePreview
from .widgets import label,button,row,ElidedLabel,RoundMenu
from .source_controls import step,choose_prompt
from .source_list import items
from .ui_icons import icon


class Segments(QFrame):
    def __init__(self, choices, changed):
        super().__init__();self.buttons={};self.group=QButtonGroup(self)
        layout=row();layout.setSpacing(0);layout.setContentsMargins(0,0,0,0);self.setLayout(layout)
        for text,value in choices:
            control=button(text,lambda checked=False,v=value:changed(v),'Quiet')
            control.setCheckable(True);control.setAccessibleName(text)
            control.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
            self.group.addButton(control);self.buttons[value]=control;layout.addWidget(control,1)

    def set_value(self,value,colors):
        from .module_chrome import style_action
        for key,control in self.buttons.items():
            control.setChecked(key==value);style_action(control,colors,primary=key==value)


class Preview(SourcePreview):
    def _load_path(self,path,*args,**kwargs):
        super()._load_path(path,*args,**kwargs)
        if path is None:self.setText('')

    def paintEvent(self,event):
        super().paintEvent(event)
        if self.source_key is None:
            painter=QPainter(self)
            icon('media',self.palette().color(self.foregroundRole()).name(),36).paint(painter,(self.width()-36)//2,(self.height()-36)//2,36,36)


class SourcePanelV7(SourcePanel):
    def __init__(self,owner,key):
        QFrame.__init__(self);self.owner=owner;self.key=key;self.current=None;self.import_task=None
        self._import_actions=weakref.WeakKeyDictionary()
        self.setObjectName('CanvasModuleBody');body=QVBoxLayout(self);body.setContentsMargins(4,8,4,4);body.setSpacing(8)
        self.origin=ElidedLabel();self.origin.setObjectName('Subtle');self.origin.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);body.addWidget(self.origin)
        self.preview=Preview(owner.window.store,owner.window);self.preview.setMinimumSize(240,160)
        self.preview.clicked.connect(self.open_preview);self.preview.pathReady.connect(self.load)
        self.preview.failed.connect(owner.window.notice if hasattr(owner.window,'notice') else lambda *_:None)
        body.addWidget(self.preview,1)
        self.name=ElidedLabel();self.name.setMinimumWidth(0);self.name.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);self.counter=label('','Subtle')
        self.previous=button('',lambda:step(self,-1));self.next=button('',lambda:step(self,1))
        self.pager=QFrame();self.pager.setLayout(row(self.name,self.counter,self.previous,self.next));self.pager.layout().setStretch(0,1);self.pager.layout().setContentsMargins(0,0,0,0);body.addWidget(self.pager)
        self.supply_row=Segments([('每次一張','step'),('整份清單','batch')],self.set_supply_mode);body.addWidget(self.supply_row)
        self.input_mode=Segments([('全部','all'),('只用這張','one')],self.set_input_mode);body.addWidget(self.input_mode)
        self.choose=SourceMenuButton('加入圖片');self.choose.clicked.connect(self.source_menu)
        self.list_button=button('清單 0',self.edit_list)
        self.progress=label('','Subtle');self.cancel_button=button('',self.cancel_import)
        self.add_row=QFrame();self.add_row.setLayout(row(self.choose,self.list_button,self.progress,self.cancel_button));self.add_row.layout().setContentsMargins(0,0,0,0);body.addWidget(self.add_row)
        self.feed_status=label('','Subtle');self.pause_button=button('',self.toggle_feed);self.stop_button=button('',lambda:self.live_control('stop_feed'))
        self.feed_row=QFrame();self.feed_row.setLayout(row(self.feed_status,None,self.pause_button,self.stop_button));self.feed_row.layout().setContentsMargins(0,0,0,0);body.addWidget(self.feed_row)
        owner.window.comfy.stateChanged.connect(self.refresh)

    def linked(self):
        from .flow_data import incoming
        return incoming(self.owner.window.state,self.key,'image')

    def set_supply_mode(self,mode):
        self.owner.canvas.commit(lambda s:s['canvas_functions']['images'][self.key].update(supply=mode),label='設定圖片供應')

    def set_input_mode(self,mode):
        if mode=='all':self.owner.canvas.commit(lambda s:s['canvas_functions']['images'][self.key].pop('input_index',None))
        else:
            value=self.owner.data()['images'][self.key];collection=self.owner.window.comfy.images.collection(self.key)
            if collection:self.owner.window.comfy.images.choose(self.key,collection['collection'],value.get('input_index',0))

    def choose_file(self):
        if self.import_task or self.linked() or self.owner.data()['images'][self.key].get('stage_reference'):return False
        from .source_controls import choose_many
        choose_many(self)

    def load(self,path):
        if self.import_task or self.linked() or self.owner.data()['images'][self.key].get('stage_reference'):return False
        from .source_controls import load_many
        return load_many(self,[path])

    def source_menu(self):
        from .source_picker import add_menu
        add_menu(self).open_for(self.choose,self.owner.canvas.view)

    def edit_list(self):
        from .source_controls import edit_list
        return edit_list(self)

    def cancel_import(self):
        if self.import_task:self.import_task.cancel()

    def import_progress(self,task):
        self.import_task=task;busy=task is not None
        self.set_import_enabled(busy)
        # Hide the outgoing controls first: a proxy can synchronously relayout
        # between visibility changes. Never let both rows widen the card/ports.
        for control in ((self.choose,self.list_button) if busy else (self.progress,self.cancel_button)):control.hide()
        if busy:self.progress.setText(f'匯入中 {task.completed}／{len(task.paths)}')
        for control in ((self.progress,self.cancel_button) if busy else (self.choose,self.list_button)):control.show()

    def set_import_enabled(self,busy):
        for control in (self.pager,self.supply_row,self.input_mode,self.choose,self.list_button):control.setEnabled(not busy)
        self.preview.setEnabled(not busy or self.current is not None)
        from shiboken6 import isValid
        for action,needs_image in list(self._import_actions.items()):
            if isValid(action):action.setEnabled(not busy and (not needs_image or bool(self.owner.current_source(self.key))))

    def refresh_visual_theme(self):
        from .canvas_items import canvas_colors
        from .module_chrome import style_action
        colors=canvas_colors(self.owner.canvas)
        for control,name,title in ((self.choose,'folder','加入圖片'),(self.list_button,'media','圖片清單'),
                (self.previous,'chevron-left','上一張'),(self.next,'chevron-right','下一張'),
                (self.pause_button,'play' if self.pause_button.property('feedPaused') else 'pause','繼續供應' if self.pause_button.property('feedPaused') else '暫停供應'),
                (self.stop_button,'stop','停止供應'),(self.cancel_button,'close','取消匯入')):
            style_action(control,colors,dropdown=control is self.choose);control.setIcon(icon(name,colors['text']));control.setIconSize(QSize(18,18))
            control.setToolTip(title);control.setAccessibleName(title)
            if control not in (self.choose,self.list_button):control.setFixedWidth(36)
        value=self.owner.data()['images'][self.key]
        self.supply_row.set_value(value.get('supply','batch'),colors)
        self.input_mode.set_value('one' if value.get('input_index') is not None else 'all',colors)

    def refresh(self):
        value=self.owner.data()['images'].get(self.key)
        if value is None:return
        linked=self.linked();reference=value.get('stage_reference');connected=bool(linked or reference)
        collection=self.owner.window.comfy.images.collection(self.key) if connected else None
        sources=collection['items'] if collection else [] if connected else items(value)
        index=value.get('input_index') if connected else value.get('index',0)
        at=index if index is not None else 0;count=len(sources)
        self.current=sources[min(at,count-1)] if sources else None
        self.name.setText(self.current['name'] if self.current else '');self.name.setVisible(not connected)
        self.name.setToolTip(self.current['name'] if self.current else '')
        self.counter.setText(f'{at+1} / {count}' if count else '')
        self.pager.setVisible(bool(count));self.previous.setEnabled(count>1 and index is not None and index>0)
        self.next.setEnabled(count>1 and (index is None or index<count-1))
        self.supply_row.setVisible(not connected and count>1 and value.get('iterate',False))
        self.input_mode.setVisible(connected and count>1);self.add_row.setVisible(not connected)
        self.list_button.setText(f'清單 {count}')
        self.origin.setVisible(bool(reference and not linked))
        self.origin.setText('來自 '+self.owner.window.state['multi_output']['stages'].get(reference,{}).get('name','已移除的 Stage') if reference else '')
        self.origin.setToolTip(self.origin.text())
        runs=self.feed_runs();paused=bool(runs) and all(run['status']=='paused' for run in runs)
        self.pause_button.setProperty('feedPaused',paused);self.feed_row.setVisible(bool(runs))
        self.feed_status.setText(f'供應中 {min(value.get("supply_position",at)+1,count)}/{count}')
        self.preview.setVisible(value.get('show_image',True));self.update_image()
        self.import_progress(self.import_task);self.refresh_visual_theme()

    def update_image(self):
        shown=self.owner.data()['images'][self.key].get('show_image',True)
        self.preview.load_path(self.owner.window.store.directory/self.current['relative'] if shown and self.current else None,self.current)

    def menu_actions(self,menu):
        value=self.owner.data()['images'][self.key]
        action=menu.addAction('顯示圖片',lambda:self.toggle_preview(not self.owner.data()['images'][self.key].get('show_image',True)));action.setCheckable(True);action.setChecked(value.get('show_image',True))
        self._import_actions[action]=False
        action=menu.addAction('提示詞來源…',lambda:choose_prompt(self));self._import_actions[action]=True
        if value.get('stage_reference') and not self.linked():
            action=menu.addAction('改用圖片清單',lambda:self.owner.canvas.commit(lambda s:s['canvas_functions']['images'][self.key].pop('stage_reference',None)))
            self._import_actions[action]=False
        self.set_import_enabled(self.import_task is not None)
        menu.addSeparator()
