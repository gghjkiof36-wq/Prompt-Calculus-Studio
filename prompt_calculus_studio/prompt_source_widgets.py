"""Read library groups, inspect a round, and edit its detached text modules."""
import copy
from PySide6.QtCore import QRectF,QTimer,Qt
from PySide6.QtWidgets import QFrame, QVBoxLayout, QPlainTextEdit, QSpinBox, QDialog, QScrollArea, QAbstractSpinBox, QSizePolicy
from .canvas_items import TextCard
from .widgets import label, button, row, ComboBox, RoundMenu, StudioDialog, ElidedLabel
from .prompt_group_editor import EntryList, PageStack, page, tool, field, mode_options
from .material_editor_page import Disclosure
from .ui_icons import icon, MODE_ICONS
from . import composition as comp, prompt_groups as groups, prompt_sources as sources, prompt_materials as pm
from .material_picker import MaterialPickerMixin


class GroupSourceDialog(MaterialPickerMixin,StudioDialog):
    def __init__(self, canvas, key):
        super().__init__(canvas.window,defer_style=True); self.canvas=canvas; self.key=key
        from .module_chrome import apply_prompt_dialog, prompt_divider
        apply_prompt_dialog(self,canvas.window)
        from .material_editor_panels import MaterialPanels
        self.panels=MaterialPanels(self,None,lambda:self.preview_toggle.setChecked(False),title='Prompt 預覽')
        self.panels.set_main_width(720)
        self.setWindowTitle('讀取流程');self.loading=False;self.current_group=None;self.active_node_dialog=None
        original=sources.definitions(canvas.window.state)[key]
        self.root=None if original.get('unselected') else copy.deepcopy(original['root'])
        self.material_id=self.root.get('source_id') if self.root else None;self.drafts={}
        self.host=canvas.window;self.picker_kind='flow';self.category=getattr(self.host,'_material_browser_category',None)
        self.stack=PageStack()
        self.overview,overview=page();self.stack.addWidget(self.overview)
        self.library=button('選擇流程',self.open_picker);self.library.setAccessibleName('選擇素材庫流程')
        self.reload_button=tool('refresh','重新讀取素材庫原件',self.reload_material)
        self.library_label=label('流程','Subtle');overview.addWidget(self.library_label)
        overview.addLayout(row(self.library,self.reload_button))
        self.mode=ComboBox();mode_options(self.mode);self.mode.setMaximumWidth(152)
        self.start=ComboBox();self.start.setMinimumWidth(140)
        self.variables,variables=page();variables.addLayout(row(field('變量',self.mode),None,field('起始組合',self.start)))
        overview.addWidget(self.variables);self.groups_label=label('組合','SectionTitle');overview.addWidget(self.groups_label)
        self.listing=EntryList('groups',readonly=True,reorderable=True);self.listing.setAccessibleName('流程內的組合')
        overview.addWidget(self.listing,1);self.listing.activatedRow.connect(self.open_group)
        self.listing.reorderRequested.connect(lambda before,after:self.reorder(self.root['sequence']['groups'],before,after))
        self.empty=label('素材庫尚無流程','Subtle');overview.addWidget(self.empty)
        self.detail,detail=page();self.stack.addWidget(self.detail)
        self.detail_meta=label('','Subtle');detail.addWidget(self.detail_meta)
        self.modules_label=label('組合內素材','SectionTitle');detail.addWidget(self.modules_label)
        self.modules=EntryList('modules',readonly=True,reorderable=True);detail.addWidget(self.modules,1)
        self.modules.activatedRow.connect(self.open_module)
        self.modules.reorderRequested.connect(lambda before,after:self.reorder(self.current_group['modules'],before,after))
        self.preview_toggle=Disclosure('Prompt 預覽');detail.addWidget(self.preview_toggle)
        self.preview_toggle.setIcon(icon('chevron-right'))
        self.preview_holder,preview_layout=page();self.preview_holder.setParent(self);self.preview_holder.hide()
        self.preview=QPlainTextEdit();self.preview.setReadOnly(True);self.preview.setAccessibleName('本輪 Prompt 預覽')
        self.preview.setMinimumHeight(240);preview_layout.addWidget(self.preview,1)
        self.preview_toggle.toggled.connect(self.show_preview)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidget(self.stack);self.body.addWidget(self.scroll,1)
        self.stack.setAutoFillBackground(False)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.stack.currentChanged.connect(self.page_changed)
        self.mode.currentIndexChanged.connect(self.configure);self.start.currentIndexChanged.connect(self.configure)
        self.error=label('', 'Draft', True); self.body.addWidget(self.error);self.error.hide()
        self.back_button=tool('back','返回流程',self.back);self.heading_row.insertWidget(0,self.back_button)
        self.apply_button=button('讀取' if original.get('unselected') else '套用',self.save,'Primary')
        self.done_button=button('完成',self.back,'Primary');self.cancel_button=button('取消',self.reject,'Quiet')
        self.footer_divider=prompt_divider();self.body.addWidget(self.footer_divider)
        self.body.addLayout(row(None,self.cancel_button,self.apply_button,self.done_button))
        self.resize(min(720,max(380,canvas.window.width()-24)),min(600,max(380,canvas.window.height()-24)))
        self.panels.content=self.stack;self.panels.form_scroll=self.scroll
        self.panels.set_content(self.preview_holder,'Prompt 預覽',lambda:self.preview_toggle.setChecked(False))
        self.refresh_overview();self.page_changed(0)

    def fit_editor(self,*_):
        self.panels.set_main_width(880 if self.stack.currentIndex()==2 else 720 if self.root else 460)
        self.panels.fit_content()

    def show_preview(self,opened):
        self.preview_toggle.setIcon(icon('chevron-left' if opened else 'chevron-right'))
        self.panels.set_open(opened)

    def page_changed(self,index):
        inside=index==1
        self.preview_toggle.setChecked(False);self.panels.set_open(False,animate=False)
        self.back_button.setVisible(index>0);self.done_button.setVisible(inside);self.apply_button.setVisible(index==0 and bool(self.root))
        self.footer_divider.setVisible(bool(self.root))
        self.heading.setText(self.current_group['name'] if inside else '選擇流程' if index==2 else '讀取流程')
        self.scroll.verticalScrollBar().setValue(0)
        self.fit_editor()

    def back(self):
        self.refresh_overview();self.stack.setCurrentIndex(0)

    def open_picker(self):
        if not hasattr(self,'candidates'):
            self._picker();self.picker_navigation.hide()
        self.refresh_folders();self.fit_picker();self.stack.setCurrentIndex(2);self.search.setFocus()

    def pick_material(self,ident,position=None):
        if self.load_material(ident):self.stack.setCurrentIndex(0)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.fit_picker()

    def showEvent(self,event):
        self.panels.set_main_width(720 if self.root else 460)
        self.panels.fit_content(before_show=True)
        super().showEvent(event)

    def load_material(self,ident):
        if self.loading:return
        if ident is None:return
        if self.root:self.drafts[self.material_id]=copy.deepcopy(self.root)
        try:root=copy.deepcopy(self.drafts[ident]) if ident in self.drafts else sources.library_root(self.canvas.window.state,ident)
        except ValueError as exc:
            self.error.setText(str(exc));self.error.show();self.fit_editor();return
        self.root=root;self.material_id=ident;self.error.hide();self.refresh_overview();return True

    def reload_material(self):
        try:root=sources.library_root(self.canvas.window.state,self.material_id)
        except ValueError as exc:self.error.setText(str(exc));self.error.show();self.fit_editor();return
        self.root=root;self.drafts[self.material_id]=copy.deepcopy(root)
        self.error.hide();self.refresh_overview()

    def refresh_overview(self):
        self.loading=True;self.start.clear()
        seq=self.root['sequence'] if self.root else None
        self.panels.minimum_height=480 if seq else 240
        for control in (self.library_label,self.variables,self.groups_label,self.listing,self.reload_button):control.setVisible(bool(seq))
        self.library.setText(self.root['name'] if self.root else '選擇流程')
        self.empty.hide()
        self.footer_divider.setVisible(bool(seq))
        self.apply_button.setVisible(bool(seq) and self.stack.currentIndex()==0)
        self.apply_button.setEnabled(bool(seq));self.reload_button.setEnabled(bool(self.material_id))
        if seq:
            self.mode.setCurrentIndex(self.mode.findData(seq['mode']))
            for group in seq['groups']:self.start.addItem(group['name'],group['id'])
            self.start.setCurrentIndex(self.start.findData(seq['start']))
            self.listing.fill([dict(g,subtitle=' · '.join(filter(None,(
                groups.module_summary(g['modules']) if g['input']=='modules' else '',
                '起始' if g['id']==seq['start'] else '')))) for g in seq['groups']])
        # Bind deferred layout to the dialog's QObject lifetime. A queued
        # callback must not touch its PageStack after forced DeferredDelete.
        self.loading=False;QTimer.singleShot(0,self,self.fit_editor)

    def preserve_position(self):
        if self.root.get('supply_cursor'):
            self.root['supply_cursor']['signature']=sources.legacy_signature(self.root)
        if self.root.get('selection_cursor'):
            self.root['selection_cursor']['signature']=pm.signature(self.root)

    def reorder(self,rows,before,after):
        if [item['id'] for item in rows]!=before or len(after)!=len(before) or set(before)!=set(after):return
        mapping={item['id']:item for item in rows};rows[:]=[mapping[ident] for ident in after]
        self.preserve_position();self.refresh_overview()
        if self.stack.currentIndex()==1:self.refresh_group()

    def configure(self):
        if self.loading or not self.root:return
        seq=self.root['sequence'];seq.update(mode=self.mode.currentData(),start=self.start.currentData())
        seq['selected']=seq['start'];self.root.pop('supply_cursor',None);self.root.pop('selection_cursor',None)
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
        self.preview.setPlainText(comp.render(preview));QTimer.singleShot(0,self,self.fit_editor)

    def open_module(self,ident):
        from .text_canvas import NodeDialog
        part=next(m for m in self.current_group['modules'] if m['id']==ident)
        dialog=NodeDialog(self.canvas.window,part,title='編輯流程' if part.get('sequence') else '編輯文字模塊');self.active_node_dialog=dialog
        try:accepted=dialog.exec()==QDialog.DialogCode.Accepted
        finally:self.active_node_dialog=None
        if accepted:
            part.update(dialog.values())
            self.preserve_position()
            self.refresh_group()

    def save(self):
        if not self.root:return
        root=copy.deepcopy(self.root)
        try: comp.validate_node(root)
        except ValueError as exc:self.error.setText(str(exc));self.error.show();self.fit_editor();return
        def apply(state):
            value=sources.definitions(state).get(self.key)
            if value is None:raise ValueError('讀取流程節點已移除。')
            value.update(root=root,name=root['name'],unselected=False)
        if self.canvas.commit(apply,label='讀取流程') is not False:self.accept()


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
        for name,value in (('整個流程','batch'),('逐輪','step')):self.supply.addItem(name,value)
        self.mode=button('',self.choose_mode,'SourceMode');self.mode.setFixedSize(38,38)
        self.mode.setAccessibleName('變量')
        layout.addLayout(row(self.supply,self.mode))
        self.limit=QSpinBox();self.limit.setRange(1,groups.MAX_ROUNDS);self.limit.setSuffix(' 輪');self.limit.setAccessibleName('批次輪數')
        self.count_mode=ComboBox();self.count_mode.setAccessibleName('批次輪數模式')
        self.count_mode.addItem('自動','auto');self.count_mode.addItem('指定數量','specified')
        count_controls,counts=page();counts.addLayout(row(self.count_mode,self.limit))
        self.limit_row=field('批次輪數',count_controls);layout.addWidget(self.limit_row)
        self.edit_button=button('選擇流程',self.edit);self.edit_button.setAccessibleName('選擇流程');layout.addWidget(self.edit_button)
        self.supply.currentIndexChanged.connect(self.configure)
        self.count_mode.currentIndexChanged.connect(self.configure)
        self.limit.valueChanged.connect(self.configure);self.index.valueChanged.connect(self.select)

    def value(self):return sources.definitions(self.owner.window.state)[self.key]

    def refresh(self):
        self.loading=True;value=self.value();seq=value['root']['sequence']
        ready=not value.get('unselected');current=groups.selected(seq)
        self.group_name.setText(current['name'] if ready else '');self.group_name.setToolTip(current['name'] if ready else '')
        self.group_name.setVisible(ready)
        self.edit_button.setText(value['name'] if ready else '選擇流程');self.edit_button.setToolTip('選擇流程')
        for control in (self.index,self.previous,self.next,self.supply,self.mode):control.setEnabled(ready)
        for control in (self.index,self.previous,self.next,self.supply,self.mode):control.setVisible(ready)
        self.supply.setCurrentIndex(self.supply.findData(value['supply']))
        self.mode.setIcon(icon(MODE_ICONS[seq['mode']]));self.mode.setToolTip('變量：'+groups.MODES[seq['mode']])
        self.mode.setAccessibleName('變量：'+groups.MODES[seq['mode']])
        self.index.setMaximum(len(seq['groups']));self.index.setSuffix(' / '+str(len(seq['groups'])))
        self.index.setFixedWidth(max(64,self.index.fontMetrics().horizontalAdvance(str(len(seq['groups']))+' / '+str(len(seq['groups'])))+12))
        self.index.setValue(next(i for i,g in enumerate(seq['groups'],1) if g['id']==seq['selected']))
        self.previous.setEnabled(ready and self.index.value()>1);self.next.setEnabled(ready and self.index.value()<len(seq['groups']))
        self.limit.setValue(value['limit']);self.limit_row.setVisible(ready and value['supply']=='batch')
        self.count_mode.setCurrentIndex(self.count_mode.findData(value.get('count_mode','auto')))
        self.limit.setVisible(value.get('count_mode','auto')=='specified')
        self.loading=False

    def configure(self):
        if self.loading:return
        supply,limit,count_mode=self.supply.currentData(),self.limit.value(),self.count_mode.currentData()
        def apply(state):
            value=sources.definitions(state)[self.key];value.update(supply=supply,limit=limit,count_mode=count_mode)
        self.owner.canvas.commit(apply,label='設定流程供應')

    def choose_mode(self):
        selected=self.value()['root']['sequence']['mode'];menu=RoundMenu(self)
        for mode,name in groups.MODES.items():
            action=menu.addAction(icon(MODE_ICONS[mode]),name,lambda checked=False,m=mode:self.set_mode(m))
            action.setCheckable(True);action.setChecked(mode==selected)
        if selected=='random':
            menu.addSeparator()
            for enabled,name in ((True,'允許重複'),(False,'一輪內不重複')):
                action=menu.addAction(name,lambda checked=False,v=enabled:self.set_random_repeats(v))
                action.setCheckable(True);action.setChecked(self.value()['root']['sequence'].get('random_repeats',True)==enabled)
        menu.open_for(self.mode,self.owner.canvas.view)

    def set_mode(self,mode):
        def apply(state):
            root=sources.definitions(state)[self.key]['root'];root['sequence']['mode']=mode;root.pop('supply_cursor',None);root.pop('selection_cursor',None)
        self.owner.canvas.commit(apply,label='設定流程變量')

    def set_random_repeats(self,enabled):
        def apply(state):
            root=sources.definitions(state)[self.key]['root'];root['sequence']['random_repeats']=enabled
            root.pop('supply_cursor',None);root.pop('selection_cursor',None);groups.mark_version(state)
        self.owner.canvas.commit(apply,label='設定隨機重複')

    def select(self,index):
        if self.loading:return
        def apply(state):
            root=sources.definitions(state)[self.key]['root'];seq=root['sequence']
            seq['selected']=seq['groups'][index-1]['id'];root.pop('round_override',None)
            root.pop('supply_cursor',None)
            root['selection_cursor']=dict(signature=sources.signature(root),position=groups.cursor(seq,start=seq['selected']))
            groups.mark_version(state)
        self.owner.canvas.commit(apply,label='選擇供應組合')

    def move(self,delta):self.index.setValue(self.index.value()+delta)
    def edit(self):
        dialog=GroupSourceDialog(self.owner.canvas,self.key);self.active_dialog=dialog
        try:dialog.exec()
        finally:self.active_dialog=None


class GroupSourceCard(TextCard):
    default_size=(360,250)
    def __init__(self,owner,key):
        super().__init__(owner.canvas);self.owner=owner;self.key=key;self.title='讀取流程';self.module_icon='list-ordered'
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
        if self.panel.value().get('unselected'):height=min(height,174)
        self.proxy.setGeometry(QRectF(14,105,max(300,minimum.width(),width-28),max(minimum.height(),height-119)))
        self.sync_bounds()

    def open_menu(self,position):
        menu=RoundMenu(self.canvas.window);menu.addAction('選擇流程',self.panel.edit)
        menu.add_destructive_action('移除',lambda:self.owner.remove(self.key));menu.open_at(position)
