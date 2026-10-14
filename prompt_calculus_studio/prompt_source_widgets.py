"""Read library groups, inspect a round, and edit its detached text modules."""
import copy
from PySide6.QtCore import QRectF,QTimer,Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QPlainTextEdit, QSpinBox, QDialog, QScrollArea, QAbstractSpinBox, QSizePolicy
from .canvas_items import TextCard
from .widgets import label, button, row, ComboBox, RoundMenu, StudioDialog, ElidedLabel
from .prompt_group_editor import EntryList, PageStack, page, tool, field, mode_options
from .material_editor_page import Disclosure
from .ui_icons import icon, MODE_ICONS
from . import composition as comp, prompt_groups as groups, prompt_sources as sources


class GroupSourceDialog(StudioDialog):
    def __init__(self, canvas, key):
        super().__init__(canvas.window); self.canvas=canvas; self.key=key
        from .module_chrome import apply_prompt_dialog, prompt_divider
        apply_prompt_dialog(self,canvas.window)
        self.setWindowTitle('讀取組合');self.loading=False;self.current_group=None;self.active_node_dialog=None
        original=sources.definitions(canvas.window.state)[key]
        self.root=None if original.get('unselected') else copy.deepcopy(original['root'])
        self.material_id=self.root.get('source_id') if self.root else None;self.drafts={}
        self.stack=PageStack()
        self.overview,overview=page();self.stack.addWidget(self.overview)
        self.library=ComboBox();self.library.setAccessibleName('素材庫組合');self.library.setPlaceholderText('選擇素材庫組合')
        for item in canvas.window.state['items']:
            if item.get('composition',{}).get('sequence'): self.library.addItem(item['name'], item['id'])
        index=self.library.findData(self.material_id) if self.material_id else -1
        if self.root:
            if index<0:
                self.library.insertItem(0,self.root['name'],self.material_id);index=0
                self.library.model().item(0).setEnabled(False)
            else:self.library.setItemText(index,self.root['name'])
        self.library.setCurrentIndex(index)
        self.reload_button=tool('refresh','重新讀取素材庫原件',self.reload_material)
        overview.addWidget(label('組合','Subtle'));overview.addLayout(row(self.library,self.reload_button))
        self.mode=ComboBox();mode_options(self.mode);self.mode.setMaximumWidth(152)
        self.start=ComboBox();self.start.setMinimumWidth(140)
        self.variables,variables=page();variables.addLayout(row(field('變量',self.mode),None,field('起始組合',self.start)))
        overview.addWidget(self.variables);overview.addWidget(label('輪次','SectionTitle'))
        self.listing=EntryList('groups',readonly=True);self.listing.setAccessibleName('輪次總覽')
        overview.addWidget(self.listing,1);self.listing.activatedRow.connect(self.open_group)
        self.empty=label('素材庫尚無組合' if not self.library.count() else '尚未選擇組合','Subtle');overview.addWidget(self.empty)
        self.detail,detail=page();self.stack.addWidget(self.detail)
        self.detail_meta=label('','Subtle');detail.addWidget(self.detail_meta)
        self.modules_label=label('使用的模組','SectionTitle');detail.addWidget(self.modules_label)
        self.modules=EntryList('modules',readonly=True);detail.addWidget(self.modules,1)
        self.modules.activatedRow.connect(self.open_module)
        self.preview_toggle=Disclosure('Prompt 預覽');detail.addWidget(self.preview_toggle)
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setAccessibleName('本輪 Prompt 預覽')
        self.preview.setMinimumHeight(150);self.preview.setMaximumHeight(230);detail.addWidget(self.preview);self.preview.hide()
        self.preview_toggle.toggled.connect(self.preview.setVisible)
        self.preview_toggle.toggled.connect(lambda:QTimer.singleShot(0,self.fit_editor))
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.stack);self.body.addWidget(self.scroll,1)
        self.stack.setAutoFillBackground(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack.currentChanged.connect(self.page_changed)
        self.library.currentIndexChanged.connect(self.load_material)
        self.mode.currentIndexChanged.connect(self.configure);self.start.currentIndexChanged.connect(self.configure)
        self.error=label('', 'Draft', True); self.body.addWidget(self.error);self.error.hide()
        self.back_button=tool('back','返回輪次總覽',self.back);self.heading_row.insertWidget(0,self.back_button)
        self.apply_button=button('讀取' if original.get('unselected') else '套用',self.save,'Primary')
        self.done_button=button('完成',self.back,'Primary');self.cancel_button=button('取消',self.reject,'Quiet')
        self.body.addWidget(prompt_divider())
        self.body.addLayout(row(None,self.cancel_button,self.apply_button,self.done_button))
        self.resize(min(820,max(380,canvas.window.width()-24)),min(600,max(380,canvas.window.height()-24)))
        self.refresh_overview();self.page_changed(0)

    def fit_editor(self,*_):
        if not self.isVisible():return
        self.stack.currentWidget().layout().activate();self.layout().activate()
        wanted=self.stack.sizeHint().height()+self.height()-self.scroll.height()
        limit=min(self.canvas.window.height()-24,self.screen().availableGeometry().height()-40)
        self.resize(self.width(),max(340,min(wanted,limit)))

    def page_changed(self,index):
        inside=index==1
        self.back_button.setVisible(inside);self.done_button.setVisible(inside);self.apply_button.setVisible(not inside)
        self.heading.setText(self.current_group['name'] if inside else '讀取組合')
        self.scroll.verticalScrollBar().setValue(0)
        QTimer.singleShot(0,self.fit_editor)

    def back(self):
        self.refresh_overview();self.stack.setCurrentIndex(0)

    def showEvent(self,event):
        super().showEvent(event);QTimer.singleShot(0,self.fit_editor)

    def load_material(self):
        if self.loading:return
        ident=self.library.currentData()
        if ident is None:return
        if self.root:self.drafts[self.material_id]=copy.deepcopy(self.root)
        try:root=copy.deepcopy(self.drafts[ident]) if ident in self.drafts else sources.library_root(self.canvas.window.state,ident)
        except ValueError as exc:
            self.loading=True;self.library.setCurrentIndex(self.library.findData(self.material_id));self.loading=False
            self.error.setText(str(exc));self.error.show();self.fit_editor();return
        self.root=root;self.material_id=ident;self.error.hide();self.refresh_overview()

    def reload_material(self):
        try:root=sources.library_root(self.canvas.window.state,self.material_id)
        except ValueError as exc:self.error.setText(str(exc));self.error.show();self.fit_editor();return
        self.root=root;self.drafts[self.material_id]=copy.deepcopy(root)
        self.library.setItemText(self.library.currentIndex(),root['name'])
        self.error.hide();self.refresh_overview()

    def refresh_overview(self):
        self.loading=True;self.start.clear()
        seq=self.root['sequence'] if self.root else None
        self.variables.setVisible(bool(seq));self.listing.setVisible(bool(seq));self.empty.setVisible(not seq)
        self.apply_button.setEnabled(bool(seq));self.reload_button.setEnabled(bool(self.material_id))
        if seq:
            self.mode.setCurrentIndex(self.mode.findData(seq['mode']))
            for group in seq['groups']:self.start.addItem(group['name'],group['id'])
            self.start.setCurrentIndex(self.start.findData(seq['start']))
            self.listing.fill([dict(g,subtitle=(f"{len(g['modules'])} 個模組" if g['input']=='modules' else '整段 Prompt')+
                (' · 起始' if g['id']==seq['start'] else '')) for g in seq['groups']])
        self.loading=False;QTimer.singleShot(0,self.fit_editor)

    def configure(self):
        if self.loading or not self.root:return
        seq=self.root['sequence'];seq.update(mode=self.mode.currentData(),start=self.start.currentData())
        seq['selected']=seq['start'];self.root.pop('supply_cursor',None)
        self.refresh_overview()

    def open_group(self,ident):
        self.current_group=next(g for g in self.root['sequence']['groups'] if g['id']==ident)
        self.preview_toggle.setChecked(False);self.refresh_group();self.stack.setCurrentIndex(1)
        if self.current_group['input']=='text':self.preview_toggle.setChecked(True)

    def refresh_group(self):
        group=self.current_group;seq=self.root['sequence'];at=seq['groups'].index(group)+1
        self.detail_meta.setText(f"第 {at} 組 / {len(seq['groups'])}　·　{group['repeat']} 次")
        modules=group['input']=='modules';self.modules.setVisible(modules);self.modules_label.setVisible(modules)
        self.modules.fill([dict(m,subtitle=comp.render(m).replace('\n',' ')) for m in group['modules']])
        preview=copy.deepcopy(self.root);preview['sequence']['selected']=group['id']
        self.preview.setPlainText(comp.render(preview));QTimer.singleShot(0,self.fit_editor)

    def open_module(self,ident):
        from .text_canvas import NodeDialog
        part=next(m for m in self.current_group['modules'] if m['id']==ident)
        dialog=NodeDialog(self.canvas.window,part,title='編輯文字模組');self.active_node_dialog=dialog
        try:accepted=dialog.exec()==QDialog.DialogCode.Accepted
        finally:self.active_node_dialog=None
        if accepted:
            part.update(dialog.values())
            if self.root.get('supply_cursor'):
                self.root['supply_cursor']['signature']=sources.signature(self.root)
            self.refresh_group()

    def save(self):
        if not self.root:return
        root=copy.deepcopy(self.root)
        try: comp.validate_node(root)
        except ValueError as exc:self.error.setText(str(exc));self.error.show();self.fit_editor();return
        def apply(state):
            value=sources.definitions(state).get(self.key)
            if value is None:raise ValueError('讀取組合模組已移除。')
            value.update(root=root,name=root['name'],unselected=False)
        if self.canvas.commit(apply,label='讀取組合') is not False:self.accept()


class GroupSourcePanel(QFrame):
    def __init__(self, owner, key):
        super().__init__();self.owner=owner;self.key=key;self.loading=False
        self.setObjectName('CanvasModuleBody');layout=QVBoxLayout(self);layout.setContentsMargins(4,4,4,4);layout.setSpacing(12)
        self.group_name=ElidedLabel();self.group_name.setObjectName('SectionTitle');self.group_name.setMinimumWidth(0)
        self.group_name.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.previous=tool('chevron-left','上一組',lambda:self.move(-1));self.next=tool('chevron-right','下一組',lambda:self.move(1))
        self.index=QSpinBox();self.index.setMinimum(1);self.index.setAccessibleName('目前組合序號')
        self.index.setObjectName('SourcePager');self.index.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
        self.index.setAlignment(Qt.AlignmentFlag.AlignCenter);self.index.setFixedWidth(76);self.index.setToolTip('輸入序號跳至組合')
        layout.addLayout(row(self.group_name,self.previous,self.index,self.next))
        self.supply=ComboBox();self.supply.setAccessibleName('供應方式')
        for name,value in (('逐輪','step'),('批次','batch')):self.supply.addItem(name,value)
        self.mode=button('',self.choose_mode,'SourceMode');self.mode.setFixedSize(38,38)
        self.mode.setAccessibleName('變量')
        layout.addLayout(row(self.supply,self.mode))
        self.limit=QSpinBox();self.limit.setRange(1,groups.MAX_ROUNDS);self.limit.setSuffix(' 輪');self.limit.setAccessibleName('批次輪數')
        self.limit.setToolTip('沒有有限來源時的批次輪數')
        self.limit_row=field('批次輪數',self.limit);layout.addWidget(self.limit_row)
        self.edit_button=button('選擇組合',self.edit);self.edit_button.setAccessibleName('選擇組合');layout.addWidget(self.edit_button)
        self.supply.currentIndexChanged.connect(self.configure)
        self.limit.valueChanged.connect(self.configure);self.index.valueChanged.connect(self.select)

    def value(self):return sources.definitions(self.owner.window.state)[self.key]

    def refresh(self):
        self.loading=True;value=self.value();seq=value['root']['sequence']
        ready=not value.get('unselected');current=groups.selected(seq)
        self.group_name.setText(current['name'] if ready else '尚未選擇組合');self.group_name.setToolTip(current['name'] if ready else '')
        self.edit_button.setText(value['name'] if ready else '選擇組合');self.edit_button.setToolTip('選擇組合')
        for control in (self.index,self.previous,self.next,self.supply,self.mode):control.setEnabled(ready)
        for control in (self.index,self.previous,self.next):control.setVisible(ready)
        self.supply.setCurrentIndex(self.supply.findData(value['supply']))
        self.mode.setIcon(icon(MODE_ICONS[seq['mode']]));self.mode.setToolTip('變量：'+groups.MODES[seq['mode']])
        self.mode.setAccessibleName('變量：'+groups.MODES[seq['mode']])
        self.index.setMaximum(len(seq['groups']));self.index.setSuffix(' / '+str(len(seq['groups'])))
        self.index.setFixedWidth(max(64,self.index.fontMetrics().horizontalAdvance(str(len(seq['groups']))+' / '+str(len(seq['groups'])))+12))
        self.index.setValue(next(i for i,g in enumerate(seq['groups'],1) if g['id']==seq['selected']))
        self.previous.setEnabled(ready and self.index.value()>1);self.next.setEnabled(ready and self.index.value()<len(seq['groups']))
        self.limit.setValue(value['limit']);self.limit_row.setVisible(value['supply']=='batch' and seq['mode'] in ('random','fixed'))
        self.loading=False

    def configure(self):
        if self.loading:return
        supply,limit=self.supply.currentData(),self.limit.value()
        def apply(state):
            value=sources.definitions(state)[self.key];value.update(supply=supply,limit=limit)
            value['root'].pop('supply_cursor',None)
        self.owner.canvas.commit(apply,label='設定組合供應')

    def choose_mode(self):
        selected=self.value()['root']['sequence']['mode'];menu=RoundMenu(self)
        for mode,name in groups.MODES.items():
            action=menu.addAction(icon(MODE_ICONS[mode]),name,lambda checked=False,m=mode:self.set_mode(m))
            action.setCheckable(True);action.setChecked(mode==selected)
        menu.exec(self.mode.mapToGlobal(self.mode.rect().bottomLeft()))

    def set_mode(self,mode):
        def apply(state):
            root=sources.definitions(state)[self.key]['root'];root['sequence']['mode']=mode;root.pop('supply_cursor',None)
        self.owner.canvas.commit(apply,label='設定組合變量')

    def select(self,index):
        if self.loading:return
        def apply(state):
            root=sources.definitions(state)[self.key]['root'];seq=root['sequence']
            seq['selected']=seq['groups'][index-1]['id'];root.pop('round_override',None)
            root['supply_cursor']=dict(signature=sources.signature(root),position=groups.cursor(seq,start=seq['selected']))
        self.owner.canvas.commit(apply,label='選擇供應組合')

    def move(self,delta):self.index.setValue(self.index.value()+delta)
    def edit(self):
        dialog=GroupSourceDialog(self.owner.canvas,self.key);self.active_dialog=dialog
        try:dialog.exec()
        finally:self.active_dialog=None


class GroupSourceCard(TextCard):
    default_size=(360,250)
    def __init__(self,owner,key):
        super().__init__(owner.canvas);self.owner=owner;self.key=key;self.title='讀取組合';self.module_icon='list-ordered'
        self.panel=GroupSourcePanel(owner,key)

    def sync_panel_style(self):
        from .canvas_items import canvas_colors
        from .module_chrome import source_controls_style
        style=self.canvas.window.styleSheet()+source_controls_style(canvas_colors(self.canvas))
        if self.panel.styleSheet()!=style:self.panel.setStyleSheet(style)

    def attach(self):
        if self.proxy.widget() is not self.panel:self.proxy.setWidget(self.panel)
        self.sync_panel_style();self.panel.refresh();self.restore_size();self.layout_card();self.panel.show()

    def layout_card(self):
        width,height=self.requested_size;minimum=self.panel.minimumSizeHint()
        self.proxy.setGeometry(QRectF(14,105,max(300,minimum.width(),width-28),max(minimum.height(),height-119)))
        self.sync_bounds()

    def open_menu(self,position):
        menu=RoundMenu(self.canvas.window);menu.addAction('選擇組合',self.panel.edit)
        menu.addAction('移除',lambda:self.owner.remove(self.key));menu.exec(position)
