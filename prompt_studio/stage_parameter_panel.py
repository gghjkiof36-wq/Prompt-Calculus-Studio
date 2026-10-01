"""Responsive Stage/task sheet. Drafts never dispatch or edit a native graph."""
import copy
import json
import uuid
from pathlib import Path
from PySide6.QtCore import Qt, QEvent, QTimer, QSize, QRect, Signal
from PySide6.QtGui import QFontMetrics,QIcon
from PySide6.QtWidgets import (QFrame, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QListWidget, QListWidgetItem, QScrollArea, QLineEdit, QCheckBox, QToolButton,
    QPlainTextEdit, QSizePolicy, QApplication,QGraphicsOpacityEffect,QLayout)
from shiboken6 import isValid
from .widgets import label, button, row, ComboBox, RoundMenu, ask
from .stage_parameters import (empty, identity, key, descriptor_fields, parse,
    validate, verify, effective, SEED_MODES)
from .stage_parameter_terms import term
from .stage_parameter_choices import ParameterChoices
from .parameter_display import display_value


def style_parameter_scrollbar(bar):
    """A narrow local rail with understated arrows and a visible hover state."""
    assets=(Path(__file__).parent/'assets').as_posix()
    bar.setObjectName('StageParameterScrollBar')
    bar.setStyleSheet('''
        QScrollBar#StageParameterScrollBar:vertical {background:transparent;width:12px;margin:16px 0;}
        QScrollBar#StageParameterScrollBar::handle:vertical {background:#555853;min-height:32px;border-radius:3px;margin:0 3px;}
        QScrollBar#StageParameterScrollBar::handle:vertical:hover {background:#92968e;}
        QScrollBar#StageParameterScrollBar::sub-line:vertical {height:16px;subcontrol-position:top;subcontrol-origin:margin;background:transparent;border:0;border-radius:3px;}
        QScrollBar#StageParameterScrollBar::add-line:vertical {height:16px;subcontrol-position:bottom;subcontrol-origin:margin;background:transparent;border:0;border-radius:3px;}
        QScrollBar#StageParameterScrollBar::sub-line:vertical:hover, QScrollBar#StageParameterScrollBar::add-line:vertical:hover {background:#393c39;}
        QScrollBar#StageParameterScrollBar::up-arrow:vertical {image:url("ASSETS/chevron-up.svg");width:10px;height:10px;}
        QScrollBar#StageParameterScrollBar::down-arrow:vertical {image:url("ASSETS/chevron-down.svg");width:10px;height:10px;}
        QScrollBar#StageParameterScrollBar::sub-page:vertical, QScrollBar#StageParameterScrollBar::add-page:vertical {background:transparent;}
    '''.replace('ASSETS',assets))


class NodeName(QFrame):
    def __init__(self, node):
        super().__init__();self.node=node
        body=QVBoxLayout(self);body.setContentsMargins(12,8,12,8);body.setSpacing(4)
        self.name=label(node['title']);self.name.setProperty('stageNodeName',True);self.name.setMinimumWidth(0)
        self.name.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        ident=label('#'+'/'.join(node['path']),'Subtle');ident.setToolTip(ident.text())
        ident.setSizePolicy(QSizePolicy.Policy.Fixed,QSizePolicy.Policy.Preferred)
        names=row(self.name,ident);names.setStretch(0,1);body.addLayout(names)
        self.kind=label(node['class_type'],'Eyebrow');self.kind.setProperty('stageSecondary',True);self.kind.setMinimumWidth(0)
        self.kind.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);body.addWidget(self.kind)
        self.setToolTip(node['title']+'\n#'+'/'.join(node['path'])+'\n'+node['class_type'])
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        self.name.setText(QFontMetrics(self.name.font()).elidedText(self.node['title'],Qt.TextElideMode.ElideRight,max(0,self.name.width())))
        self.kind.setText(QFontMetrics(self.kind.font()).elidedText(self.node['class_type'],Qt.TextElideMode.ElideRight,max(0,self.kind.width())))


class SeedEditor(QFrame):
    changed=Signal(str,str)
    def __init__(self,value,mode,language,editable=True,reason=''):
        super().__init__();self.mode=mode;self.language=language;self.setProperty('stageSeed',True)
        body=QHBoxLayout(self);body.setContentsMargins(0,0,5,0);body.setSpacing(0)
        self.text=QLineEdit(str(value));self.text.setFrame(False)
        self.text.setReadOnly(not editable)
        self.action=QToolButton();self.action.setFixedSize(34,30);self.action.setIconSize(QSize(16,16))
        self.action.setStyleSheet('QToolButton {border:0;border-radius:6px;background:#393c39;padding:0;} QToolButton:hover {background:#484b47;}')
        self.mode_icon()
        self.action.setEnabled(editable and mode in SEED_MODES)
        if reason:
            self.text.setToolTip(reason);self.action.setToolTip(self.action.toolTip()+'\n'+reason)
            self.setAccessibleDescription(reason)
        self.action.clicked.connect(self.menu);self.text.textEdited.connect(lambda _:self.changed.emit(self.text.text(),self.mode))
        for widget in (self.text,self.action):widget.installEventFilter(self)
        body.addWidget(self.text,1);body.addWidget(self.action);self.setMinimumHeight(42)
        self.focus_style()
    def focus_style(self):
        self.setStyleSheet('QFrame[stageSeed="true"] {background:#171819;border:1px solid '+('#7d9cbf' if self.text.hasFocus() or self.action.hasFocus() else '#414342')+'; border-radius:8px;} QFrame[stageSeed="true"] QLineEdit {border:0;background:transparent;}')
    def menu(self):
        if not self.action.isEnabled():return
        menu=RoundMenu(self);menu.setToolTipsVisible(True)
        check=(Path(__file__).parent/'assets'/'check.svg').as_posix()
        menu.setStyleSheet('QMenu::indicator {width:16px;height:16px;} QMenu::indicator:checked {background:#edeae5;border-radius:3px;image:url("'+check+'");}')
        for mode in SEED_MODES:
            action=menu.addAction(term(mode,self.language),lambda m=mode:self.select(m));action.setCheckable(True);action.setChecked(mode==self.mode)
            action.setToolTip(term(mode+'_hint',self.language))
        menu.aboutToHide.connect(self.action.setFocus);menu.open_at(self.action.mapToGlobal(self.action.rect().bottomLeft()))
    def select(self,mode):
        self.mode=mode;self.mode_icon();self.changed.emit(self.text.text(),mode)
    def mode_icon(self):
        name={'fixed':'fixed','increment':'increment','decrement':'decrement','randomize':'shuffle'}.get(self.mode)
        text=term(self.mode,self.language) if name else term('unknown_seed_mode',self.language)
        if name:self.action.setIcon(QIcon(str(Path(__file__).parent/'assets'/('seed-'+name+'.svg'))))
        else:self.action.setText('—')
        self.action.setToolTip(text);self.action.setAccessibleName(text)
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.FocusIn,QEvent.Type.FocusOut):
            QTimer.singleShot(0,self,self.focus_style)
        return False


class ParameterSheet(QFrame):
    applied=Signal(object)
    def __init__(self,canvas,stage_id,item=None,language=None):
        window=canvas.window;super().__init__(window.centralWidget())
        self.canvas=canvas;self.window=window;self.stage_id=stage_id;self.item=copy.deepcopy(item)
        self.client=window.comfy;self.catalog=window.settings_page.workflow_manager.catalog
        self.owner=(window.store,window.state['workspace'],self.client.url,self.client.epoch)
        self.language=language or window.state.get('settings',{}).get('language','zh-TW')
        self.profiles=copy.deepcopy(window.state.get('generation',{}).get('profiles',[]));self.serial=0;self.revision=0;self.closed=False;self.loading=False
        self.configs={};self.raw={};self.draft_fields={};self.modes={};self.descriptions={};self.scroll_positions={};self.group_expanded={};self.selected=None;self.form_owner=None;self.seed_intents={}
        self.task_base=copy.deepcopy(item['saved']) if item else None
        self.stage=(copy.deepcopy(window.comfy.input_flow.chain.store.read(item['run'])['plan']['stages'][stage_id])
            if item else copy.deepcopy(canvas.data()['stages'][stage_id]))
        self.initial_stage=copy.deepcopy(self.stage)
        config=effective(item['saved'],stage_id) if item else self.stage.get('parameters')
        self.initial_workflow=config['identity']['workflow'] if config else self.stage.get('workflow')
        self.initial_config=copy.deepcopy(config)
        if config:self.configs[self.initial_workflow]=copy.deepcopy(config)
        self.setObjectName('StageParameterMask');self.setStyleSheet('''
            #StageParameterMask {background:rgba(0,0,0,145);}
            #StageParameterPanel {background:#191a1b;border:1px solid #414342;border-radius:20px;}
            #StageParameterDrawer {background:#222324;border:1px solid #414342;border-radius:14px;}
            #StageParameterEditor {background:#222324;border:0;border-radius:14px;}
            #StageParameterFooter {background:#222324;border:0;border-bottom-left-radius:20px;border-bottom-right-radius:20px;}
            #StageWorkflowPicker {background:#171819;border:1px solid #414342;border-radius:10px;}
            #StageParameterPanel QLabel[stageNodeName="true"] {font-weight:500;}
            #StageParameterPanel QLabel[stageSecondary="true"] {color:#aaa9a4;}
            #StageParameterPanel QLabel#DialogTitle, #StageParameterPanel QLabel#Heading {font-weight:500;}
            #StageParameterPanel QLineEdit, #StageParameterPanel QComboBox {min-height:40px;border-radius:8px;}
            #StageParameterPanel QComboBox {padding:0 40px 0 10px;background:#171819;border-color:#414342;}
            #StageParameterPanel QLineEdit {padding:0 10px;background:#171819;border-color:#414342;}
            #StageParameterPanel QLineEdit[stageReadOnly="true"] {color:#aaa9a4;border-color:#353735;}
            #StageWorkflowPicker QComboBox {background:transparent;border:0;padding-left:0;}
            #StageParameterClose {background:transparent;border:0;}
            #StageParameterClose:hover, #StageParameterClose:focus {background:#303332;border-radius:8px;}
            #StageParameterFooter QPushButton#StageParameterDiff {background:#303332;border:1px solid #414342;border-radius:8px;}
            #StageParameterPanel QScrollArea {border:0;background:transparent;}
            #StageParameterPanel QListWidget {background:transparent;border:0;}
            #StageParameterPanel QListWidget::item {padding:0;margin:0;border:0;}
            #StageParameterPanel QListWidget::item:selected {background:#393c39;border-radius:10px;}
            #StageParameterPanel QToolButton[stageNodeGroup="true"] {text-align:left;color:#aaa9a4;background:transparent;border:0;border-radius:6px;padding:8px 10px;}
            #StageParameterPanel QToolButton[stageNodeGroup="true"]:hover, #StageParameterPanel QToolButton[stageNodeGroup="true"]:focus {color:#edeae5;background:#303332;}
            #StageParameterPanel QToolButton[stageNodeGroup="true"]:disabled {color:#aaa9a4;}
        ''')
        self.panel=QFrame(self);self.panel.setObjectName('StageParameterPanel')
        main=QVBoxLayout(self.panel);main.setContentsMargins(1,1,1,1);main.setSpacing(0)
        self.header=QWidget();heading=QHBoxLayout(self.header);heading.setContentsMargins(24,18,24,18)
        title=self.stage['name']+((' · '+item['label']+' · '+item['id'][:8]) if item else '')
        self.title=label(title,'DialogTitle')
        self.close_button=button('×',self.request_close,'Quiet');self.close_button.setObjectName('StageParameterClose')
        self.close_button.setFixedSize(44,44);self.close_button.setStyleSheet('font-size:24px;padding:0;')
        self.close_button.setToolTip(term('close',self.language));self.close_button.setAccessibleName(term('close',self.language))
        self.title.setMinimumWidth(0);self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred);self.title.setToolTip(title)
        heading.addWidget(self.title,1);heading.addWidget(self.close_button);main.addWidget(self.header)
        self.workflow=ComboBox();self.workflow.setMinimumWidth(0)
        self.nodes_button=button(term('nodes',self.language),self.toggle_nodes,'Quiet')
        self.refresh_button=button('',self.reload,'Quiet');self.refresh_button.setIcon(QIcon(str(Path(__file__).parent/'assets'/'refresh.svg')))
        self.refresh_button.setFixedSize(32,28);self.refresh_button.setToolTip(term('refresh',self.language));self.refresh_button.setAccessibleName(term('refresh',self.language))
        self.workflow_area=QFrame();self.workflow_area.setObjectName('StageWorkflowPicker')
        workflow_body=QVBoxLayout(self.workflow_area);workflow_body.setContentsMargins(12,8,8,8);workflow_body.setSpacing(0)
        workflow_body.addLayout(row(label(term('workflow_label',self.language),'Subtle'),None,self.refresh_button));workflow_body.addWidget(self.workflow)
        self.narrow_bar=QWidget();self.narrow_body=QHBoxLayout(self.narrow_bar);self.narrow_body.setContentsMargins(24,0,24,16)
        self.narrow_body.addWidget(self.nodes_button);main.addWidget(self.narrow_bar);self.narrow_bar.hide()
        content=QHBoxLayout();self.content=content;self.docked=True;content.setContentsMargins(24,0,24,22);content.setSpacing(24)
        self.left=QFrame();left=QVBoxLayout(self.left);self.left_body=left;left.setContentsMargins(0,0,0,0);left.setSpacing(12)
        left.addWidget(self.workflow_area)
        self.search=QLineEdit();self.search.setPlaceholderText(term('search',self.language));self.search.textChanged.connect(self.filter_nodes)
        self.list=QListWidget();self.list.currentItemChanged.connect(self.select_node)
        style_parameter_scrollbar(self.list.verticalScrollBar())
        left.addWidget(self.search);left.addWidget(self.list,1);content.addWidget(self.left)
        self.editor=QFrame();self.editor.setObjectName('StageParameterEditor')
        editor_body=QVBoxLayout(self.editor);editor_body.setContentsMargins(22,22,16,22)
        self.scroll=QScrollArea();self.scroll.setWidgetResizable(True);self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll.viewport().setObjectName('StageParameterViewport');self.scroll.viewport().setStyleSheet('#StageParameterViewport {background:transparent;}')
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        style_parameter_scrollbar(self.scroll.verticalScrollBar())
        # A transparent idle rail reserves the same width without an extra track.
        self.rail_effect=QGraphicsOpacityEffect(self.scroll.verticalScrollBar());self.rail_effect.setOpacity(0)
        self.scroll.verticalScrollBar().setGraphicsEffect(self.rail_effect)
        self.scroll.verticalScrollBar().rangeChanged.connect(lambda a,b:self.rail_effect.setOpacity(1 if b>a else 0))
        self.form=QWidget();self.form.setObjectName('StageParameterForm');self.form.setStyleSheet('#StageParameterForm {background:transparent;}')
        self.form_body=QVBoxLayout(self.form);self.form_body.setContentsMargins(0,0,6,6);self.form_body.setSpacing(16)
        self.form_body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.scroll.setWidget(self.form);editor_body.addWidget(self.scroll);content.addWidget(self.editor,1);main.addLayout(content,1)
        self.reset_button=button(term('reset',self.language),self.reset,'Quiet')
        self.diff_button=button(term('diff',self.language),self.toggle_diff);self.diff_button.setObjectName('StageParameterDiff')
        self.apply_button=button(term('apply_task' if item else 'apply',self.language),self.apply,'Primary')
        self.footer=QFrame();self.footer.setObjectName('StageParameterFooter')
        self.footer_body=QHBoxLayout(self.footer);self.footer_body.setContentsMargins(24,15,24,15);self.footer_body.setSpacing(8)
        self.status=label('','Subtle',True);self.status.setMinimumWidth(0);self.status.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.problem='';self.footer_body.addWidget(self.status,1)
        for action in (self.reset_button,self.diff_button,self.apply_button):action.setMinimumHeight(40);self.footer_body.addWidget(action)
        main.addWidget(self.footer)
        self.drawer=QFrame(self);self.drawer.setObjectName('StageParameterDrawer');drawer=QVBoxLayout(self.drawer)
        self.diff_title=label(term('diff',self.language));drawer.addLayout(row(self.diff_title,None,button('×',self.drawer.hide,'Quiet')))
        self.diff=QPlainTextEdit();self.diff.setReadOnly(True);drawer.addWidget(self.diff,1);self.drawer.hide()
        self.layout_watch=QTimer(self);self.layout_watch.setSingleShot(True);self.layout_watch.timeout.connect(self.refresh_metrics)
        self.font_signature=None
        self.parentWidget().installEventFilter(self);self.catalog.changed.connect(self.catalog_changed)
        QApplication.instance().installEventFilter(self)
        self.workflow.currentIndexChanged.connect(self.choose_workflow)
        self.fill_workflows();self.place();self.show();self.raise_();self.close_button.setFocus()
        self.watch=QTimer(self);self.watch.setInterval(250);self.watch.timeout.connect(self.check_owner);self.watch.start()
        if not self.catalog.loaded and not self.catalog.busy and self.client.connected:self.catalog.refresh()

    def current(self):
        return isValid(self) and not self.closed and self.owner==(self.window.store,self.window.state['workspace'],self.client.url,self.client.epoch)

    def refresh_metrics(self):
        if not self.current():return
        signature=self.search.font().toString()
        if signature==self.font_signature:return
        self.font_signature=signature;self.place();self.build_nodes()

    def check_owner(self):
        if not self.current():self.finish();return
        if self.item:
            item=self.window.comfy.input_flow.chain.store.read(self.item['id'])
            if not item or item['status']!='waiting':self.apply_button.setEnabled(False);self.status.setText('此項已開始準備，僅供查看；尚未套用草稿仍保留。')

    def fill_workflows(self):
        selected=self.workflow.currentData() if self.workflow.count() else self.initial_workflow
        if selected is None:
            from .stage_model import controls,terminals
            sources={terminals(self.window.state).get(k,{}).get('workflow') for k in controls(self.window.state,self.stage_id)}-{None}
            if len(sources)==1:selected=next(iter(sources))
        self.workflow.blockSignals(True);self.workflow.clear();self.workflow.addItem(term('workflow',self.language),None)
        self.entries={p['id']:dict(profile=p,name=p['name'],path=p.get('origin',{}).get('path')) for p in self.profiles}
        for path in self.catalog.files:
            found=next((p for p in self.profiles if p.get('origin')==dict(server=self.catalog.server,path=path)),None)
            key=found['id'] if found else self.catalog.identity(path)
            self.entries.setdefault(key,dict(name=path.rsplit('/',1)[-1].removesuffix('.json'),path=path,origin=dict(server=self.catalog.server,path=path)))
        names=[entry['name'] for entry in self.entries.values()]
        for key,entry in self.entries.items():
            origin=entry.get('origin') or entry.get('profile',{}).get('origin',{})
            source=entry.get('path') or origin.get('path') or origin.get('server','')
            caption=entry['name']+(' · '+source if names.count(entry['name'])>1 and source else '')
            self.workflow.addItem(caption,key)
            self.workflow.setItemData(self.workflow.count()-1,'\n'.join(v for v in (entry['name'],origin.get('server'),source) if v),Qt.ItemDataRole.ToolTipRole)
        if selected and selected not in self.entries:self.workflow.addItem('工作流已失效 · '+selected,selected)
        self.workflow.setCurrentIndex(max(0,self.workflow.findData(selected)));self.workflow.blockSignals(False)
        self.reload()

    def catalog_changed(self):
        if self.current() and not self.catalog.busy:self.fill_workflows()

    def choose_workflow(self):
        self.save_position()
        self.revision+=1;self.selected=None;self.reload()

    def read(self,done,failed):
        flow=self.workflow.currentData();entry=self.entries.get(flow)
        if not entry or not entry.get('path'):failed('此綁定沒有可讀取的原生工作流路徑，請重新選擇工作流。');return
        if not self.client.connected:failed('ComfyUI 未連線；保留設定，連線後重新整理。');return
        from .workflow_import import read_profile
        def received(value,source):
            try:
                origin=entry.get('origin') or entry['profile'].get('origin',{})
                profile=read_profile(value,entry['name'],flow,origin)
                desc=profile.get('parameter_description')
                if not desc:raise ValueError('需要同版擴充與已開啟的 ComfyUI 工作流，才能核對可編輯欄位。')
                done(profile,desc)
            except (ValueError,KeyError,TypeError) as exc:failed(str(exc))
        self.catalog.read_draft(entry['path'],entry.get('profile'),received,failed)

    def reload(self):
        if not self.current():return
        self.workflow.setToolTip(self.workflow.currentData(Qt.ItemDataRole.ToolTipRole) or '')
        self.save_position();self.serial+=1;serial=self.serial;flow=self.workflow.currentData();self.loading=True;self.set_status('讀取原生節點…');self.update_actions()
        self.node_items=[];self.group_items=[]
        self.list.blockSignals(True);self.list.clear();self.list.blockSignals(False);self.clear_form();self.form_owner=None
        def failed(error):
            if not self.current() or serial!=self.serial:return
            self.loading=False;self.set_status(error);self.update_actions()
        def received(profile,desc):
            if not self.current() or serial!=self.serial:return
            self.loading=False;self.entries[flow]['profile']=profile;self.descriptions[flow]=desc
            self.configs.setdefault(flow,empty(profile));self.set_status('');self.build_nodes();self.update_actions()
        if flow is None:
            self.loading=False;self.set_status('');self.list.clear();self.clear_form();self.update_actions();return
        self.read(received,failed)

    def clear_form(self):
        while self.form_body.count():
            item=self.form_body.takeAt(0)
            widget=item.widget()
            if widget:widget.hide();widget.setParent(None);widget.deleteLater()

    def build_nodes(self):
        selected=self.selected;self.list.blockSignals(True);self.list.clear()
        groups={'sampling':[],'latent':[],'other':[]};self.node_items=[];self.group_items=[]
        sampling={'KSampler','KSamplerAdvanced','SamplerCustom','SamplerCustomAdvanced','KSamplerSelect','BasicScheduler','RandomNoise','CFGGuider','BasicGuider','DisableNoise'}
        latent={'EmptyLatentImage','EmptySD3LatentImage','LatentUpscale','LatentUpscaleBy'}
        for node in self.description().get('nodes',[]):
            groups['sampling' if node['class_type'] in sampling else 'latent' if node['class_type'] in latent else 'other'].append(node)
        for group,nodes in groups.items():
            if not nodes:continue
            header=QListWidgetItem();header.setFlags(Qt.ItemFlag.NoItemFlags);self.list.addItem(header)
            toggle=QToolButton();toggle.setProperty('stageNodeGroup',True);toggle.setObjectName('StageNodeGroup_'+group)
            toggle.setText(term(group,self.language));toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
            toggle.setCheckable(True);toggle.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
            toggle.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
            toggle.clicked.connect(lambda checked,g=group:self.toggle_group(g,checked))
            self.list.setItemWidget(header,toggle);toggle.ensurePolished()
            header.setSizeHint(QSize(0,max(36,toggle.sizeHint().height())));members=[];self.group_items.append((header,members,toggle,group))
            for node in nodes:
                item=QListWidgetItem();item.setData(Qt.ItemDataRole.UserRole,node['path']);item.setToolTip(node['title']+' · #'+ '/'.join(node['path']))
                self.list.addItem(item);names=NodeName(node);self.list.setItemWidget(item,names);names.ensurePolished()
                item.setSizeHint(QSize(0,max(60,names.sizeHint().height()+6)))
                self.node_items.append((item,node));members.append(item)
                if node['path']==selected:self.list.setCurrentItem(item)
        self.list.blockSignals(False);self.filter_nodes()
        if self.list.currentRow()<0 and self.node_items:self.list.setCurrentItem(self.node_items[0][0])
        else:self.select_node()

    def description(self):return self.descriptions.get(self.workflow.currentData(),{})

    def filter_nodes(self):
        query=self.search.text().casefold()
        matches={id(item):query in (node['title']+' '+node['class_type']+' '+'/'.join(node['path'])).casefold() for item,node in getattr(self,'node_items',[])}
        # Filtering reveals matches temporarily; it does not alter the user's
        # category state, current editor or draft for the selected node.
        self.list.blockSignals(True)
        for header,members,toggle,group in getattr(self,'group_items',[]):
            expanded=bool(query) or self.group_expanded.get((self.workflow.currentData(),group),True)
            header.setHidden(not any(matches[id(item)] for item in members))
            toggle.setChecked(expanded);toggle.setArrowType(Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow)
            toggle.setEnabled(not query)
            action=term('collapse' if expanded else 'expand',self.language)
            toggle.setAccessibleName(action+' '+term(group,self.language))
            toggle.setToolTip(term('search_expands_groups',self.language) if query else action)
            for item in members:item.setHidden(not expanded or not matches[id(item)])
        self.list.blockSignals(False)

    def toggle_group(self,group,expanded):
        self.group_expanded[(self.workflow.currentData(),group)]=expanded;self.filter_nodes()

    def field_key(self,field):return self.workflow.currentData(),key(field)

    def edits(self,field,value,mode=None):
        if self.loading or not self.current() or self.form_owner is None or self.form_owner[0]!=self.workflow.currentData():return
        self.draft_fields.setdefault(self.field_key(field),copy.deepcopy(field))
        self.raw[self.field_key(field)]=value
        if mode is not None:self.modes[self.field_key(field)]=mode
        self.revision+=1;self.update_actions()

    def fields(self,node,container):
        grid=QGridLayout(container);grid.setContentsMargins(0,0,0,0);grid.setHorizontalSpacing(18);grid.setVerticalSpacing(16)
        grid.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        columns=2 if self.scroll.viewport().width()>=2*self.field_width()+24 else 1
        patches={key(p):p for p in self.configs.get(self.workflow.currentData(),{}).get('patches',[])}
        owned={(b['node'],b['field']) for b in self.window.state['multi_output']['bindings'] if b['workflow']==self.workflow.currentData()}
        fields=node['fields']
        if node['class_type']=='KSampler':
            order=['steps','cfg','sampler_name','scheduler','denoise','seed']
            fields=sorted(fields,key=lambda f:order.index(f['field']) if f['field'] in order else len(order))
        position=0
        for f in fields:
            field=dict(f,node=node['id'],path=node['path'],class_type=node['class_type'],node_title=node['title'])
            k=self.field_key(field);patch=patches.get(key(field));value=self.raw.get(k,patch['value'] if patch else field['value'])
            shown=display_value(value,field['type'])
            cell=QWidget();body=QVBoxLayout(cell);body.setContentsMargins(0,0,0,0);body.setSpacing(8);body.addWidget(label(term(field['field'],self.language)))
            body.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
            editable=field['editable'] and (field['node'],field['field']) not in owned
            reason='由 PCS 輸入供應' if (field['node'],field['field']) in owned else field.get('reason','') or self.description().get('projection_reason','')
            if field.get('seed_control') or 'seed_mode' in field:
                native_mode=field.get('seed_mode',field.get('seed_control_mode'))
                mode=self.modes.get(k,patch.get('seed_mode',native_mode) if patch else native_mode)
                seed_reason=reason or field.get('seed_control_reason','')
                editor=SeedEditor(value,mode,self.language,editable=editable and 'seed_mode' in field,reason=seed_reason)
                if editable and 'seed_mode' in field:editor.changed.connect(lambda v,m,f=field:self.edits(f,v,m))
                body.addWidget(editor)
            elif not editable:
                editor=QLineEdit(shown);editor.setReadOnly(True);editor.setMinimumWidth(0)
                editor.setProperty('stageReadOnly',True);editor.setAccessibleDescription(reason);body.addWidget(editor)
            elif field['type']=='ENUM':
                editor=ParameterChoices();editor.setMinimumWidth(0)
                for choice in field['choices']:editor.addItem(str(choice),choice)
                selected=editor.find_value(value)
                if selected<0:
                    editor.addItem(str(value)+' · 已失效',value);selected=editor.count()-1
                editor.setCurrentIndex(selected);editor.currentIndexChanged.connect(lambda _,e=editor,f=field:self.edits(f,e.currentData()));body.addWidget(editor)
            elif field['type']=='BOOLEAN':
                editor=QCheckBox();editor.setChecked(bool(value));editor.setMinimumHeight(42);editor.toggled.connect(lambda v,f=field:self.edits(f,v));body.addWidget(editor)
            else:
                editor=QLineEdit(shown);editor.setMinimumWidth(0);editor.textEdited.connect(lambda v,f=field:self.edits(f,v));body.addWidget(editor)
            editor.setObjectName('parameter_'+'_'.join(node['path'])+'_'+field['field'])
            editor.setToolTip('#'+'/'.join(node['path'])+' / '+field['field']+('\n'+('唯讀：' if self.language=='zh-TW' else 'Read only: ')+reason if not editable else ''))
            if shown!=str(value):
                exact=('原始值：' if self.language=='zh-TW' else 'Exact value: ')+str(value)
                editor.setToolTip(editor.toolTip()+'\n'+exact)
                editor.setAccessibleDescription(editor.accessibleDescription()+'\n'+exact)
            wide=field['field']=='batch_size' and node['class_type'] in ('EmptyLatentImage','EmptySD3LatentImage')
            if wide and position%columns:position+=columns-position%columns
            grid.addWidget(cell,position//columns,position%columns,1,columns if wide else 1)
            position+=columns if wide else 1
        for column in range(columns):grid.setColumnStretch(column,1)

    def position_key(self):
        from .stage_parameters import fingerprint
        schema=[(n['path'],n['class_type'],[f['schema'] for f in n['fields']]) for n in self.description().get('nodes',[])]
        return (self.workflow.currentData(),fingerprint(schema),tuple(self.selected or []))

    def save_position(self):
        if self.form_owner:self.scroll_positions[self.form_owner]=self.scroll.verticalScrollBar().value()

    def select_node(self,*_):
        item=self.list.currentItem()
        self.save_position()
        self.selected=item.data(Qt.ItemDataRole.UserRole) if item else None;self.clear_form()
        node=next((n for n in self.description().get('nodes',[]) if n['path']==self.selected),None)
        if not node:return
        self.form_owner=self.position_key();form_owner=self.form_owner
        projection_reason=self.description().get('projection_reason','')
        if projection_reason:
            notice=label(projection_reason,'Subtle',True)
            notice.setObjectName('StageParameterReadOnlyReason');notice.setToolTip(projection_reason)
            self.form_body.addWidget(notice)
        heading=QWidget();head=QVBoxLayout(heading);head.setContentsMargins(0,0,0,0);head.setSpacing(4)
        head.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        head.addWidget(label(node['title']+' · #'+'/'.join(node['path']),'Heading',True))
        kind=label(node['class_type'],'Eyebrow');kind.setProperty('stageSecondary',True);head.addWidget(kind)
        self.form_body.addWidget(heading)
        if node.get('mode'):self.form_body.addWidget(label('旁路' if node['mode']==4 else '停用','Subtle'))
        if node['fields']:
            group=QWidget();self.fields(node,group);self.form_body.addWidget(group)
        elif not node.get('reason') and not projection_reason:
            self.form_body.addWidget(label(term('no_parameters',self.language),'Subtle',True))
        if node.get('reason') and node['reason']!=projection_reason:self.form_body.addWidget(label(node['reason'],'Subtle',True))
        self.form_body.addStretch()
        for index in range(self.form_body.count()):
            widget=self.form_body.itemAt(index).widget()
            if widget:widget.show()
        self.form_body.activate()
        position=self.scroll_positions.get(form_owner,0)
        QTimer.singleShot(0,self,lambda:self.scroll.verticalScrollBar().setValue(position) if self.current() and self.form_owner==form_owner else None)
        if self.nodes_button.isVisible():self.left.hide()

    def field_width(self):
        # Font metrics are already logical pixels, including the selected font
        # and DPI. Keep large seed values readable without applying DPI twice.
        return max(220,self.search.fontMetrics().horizontalAdvance('00000000000000000000')+40)

    def jump(self,path):
        for i in range(self.list.count()):
            if self.list.item(i).data(Qt.ItemDataRole.UserRole)==path:self.list.setCurrentRow(i);return

    def proposed(self,flow=None):
        flow=self.workflow.currentData() if flow is None else flow;config=copy.deepcopy(self.configs.get(flow))
        if config is None:raise ValueError('請讀取工作流節點。')
        fields=descriptor_fields(self.descriptions.get(flow,{}));patches={key(p):p for p in config['patches']}
        for (workflow,k),raw in self.raw.items():
            if workflow!=flow:continue
            if k not in fields:raise ValueError('草稿欄位已不存在：#'+'/'.join(k[0])+' / '+k[1])
            field=self.draft_fields.get((workflow,k),fields[k]);value=parse(raw,field) if field['type'] in ('INT','FLOAT') else raw
            previous=patches.get(k)
            patch={name:copy.deepcopy(field[name]) for name in ('node','path','class_type','field','type','schema','min','max','step','enforce_step','choices','node_title') if name in field}
            patch.update(base=copy.deepcopy(previous['base'] if previous else field['value']),value=value)
            if 'seed_mode' in field:
                patch.update(seed_mode=self.modes.get((flow,k),previous.get('seed_mode',field['seed_mode']) if previous else field['seed_mode']),seed_timing=field['seed_timing'])
                intent=(flow,k,value,patch['seed_mode'])
                patch['intent_id']=(previous.get('intent_id') if previous and previous.get('value')==value and previous.get('seed_mode')==patch['seed_mode'] else self.seed_intents.setdefault(intent,uuid.uuid4().hex))
            patches[k]=patch
        config['patches']=list(patches.values());validate(config);return config

    def dirty(self):
        if self.workflow.currentData()!=self.initial_workflow:return True
        try:return self.parameters_changed(self.workflow.currentData())
        except (ValueError,KeyError):return bool(self.raw)

    def parameters_changed(self,flow):
        config=self.proposed(flow);initial=self.initial_config if flow==self.initial_workflow else None
        # An absent override and an explicit empty configuration both leave
        # every native field live. Metadata alone is not an unapplied edit.
        if not config['patches'] and not (initial or {}).get('patches'):return False
        return config!=initial

    def other_drafts(self):
        for flow in self.configs:
            if flow==self.workflow.currentData():continue
            try:
                if self.parameters_changed(flow):return True
            except (ValueError,KeyError):return True
        return False

    def update_actions(self):
        error=''
        try:
            config=self.proposed();valid=True
            self.apply_button.setToolTip('')
        except (ValueError,KeyError) as exc:
            valid=False;error=str(exc);self.apply_button.setToolTip(error)
        editable=not self.item or (self.window.comfy.input_flow.chain.store.read(self.item['id']) or {}).get('status')=='waiting'
        self.apply_button.setEnabled(valid and not self.loading and editable and self.dirty())
        self.reset_button.setEnabled(valid and editable and not self.loading)
        if self.loading:message=term('loading',self.language)
        elif self.problem:message=self.problem
        elif self.workflow.currentData() is None:message=term('workflow',self.language)
        elif not valid:message=error
        elif self.dirty():
            message=(('工作流選擇尚未套用' if self.language=='zh-TW' else 'Workflow selection not applied')
                     if self.workflow.currentData()!=self.initial_workflow and not config['patches'] else term('unsaved',self.language))
        else:message=term('unchanged',self.language)
        self.status.setText(message);self.status.setToolTip(message)
        if self.drawer.isVisible():self.show_diff()

    def set_status(self,value):
        self.problem=str(value);self.status.setText(self.problem);self.status.setToolTip(self.problem)

    def reset(self):
        flow=self.workflow.currentData();config=self.configs.get(flow)
        if config:config['patches']=[]
        self.raw={k:v for k,v in self.raw.items() if k[0]!=flow};self.modes={k:v for k,v in self.modes.items() if k[0]!=flow}
        self.draft_fields={k:v for k,v in self.draft_fields.items() if k[0]!=flow}
        self.revision+=1;self.select_node();self.update_actions()

    def show_diff(self):
        try:
            config=self.proposed();before={key(p):p for p in (self.initial_config or {}).get('patches',[])}
            lines=[self.title.text(),self.workflow.currentText(),'']
            if self.workflow.currentData()!=self.initial_workflow:lines.append('工作流：'+str(self.initial_workflow)+' → '+str(self.workflow.currentData())+'\n原覆寫不搬移至新工作流。')
            for p in config['patches']:
                old=before.pop(key(p),{});value=old.get('value',p['base'])
                if old!=p:lines.append(p.get('node_title',p['class_type'])+' #'+ '/'.join(p['path'])+' / '+term(p['field'],self.language)+'\n'+str(value)+' → '+str(p['value'])+(' · '+term(p['seed_mode'],self.language) if p.get('seed_mode') else ''))
            for p in before.values():lines.append('#'+'/'.join(p['path'])+' / '+p['field']+'：移除 PCS 覆寫，提交時讀取原生值')
            self.diff.setPlainText('\n\n'.join(lines))
        except (ValueError,KeyError) as exc:self.diff.setPlainText(str(exc))

    def toggle_diff(self):
        self.drawer.setVisible(not self.drawer.isVisible());self.place();self.show_diff();self.drawer.raise_()

    def toggle_nodes(self):
        self.left.setVisible(not self.left.isVisible());self.place()

    def apply(self):
        try:config=self.proposed()
        except (ValueError,KeyError) as exc:self.set_status(exc);return
        if self.other_drafts() and not ask(self,'套用目前工作流','其他工作流仍有草稿。套用目前工作流並放棄其他草稿？'):return
        revision=self.revision;serial=self.serial;self.loading=True;self.update_actions()
        def failed(error):
            if self.current() and serial==self.serial:self.loading=False;self.set_status(error);self.update_actions()
        def received(profile,desc):
            if not self.current() or serial!=self.serial:return
            if revision!=self.revision:failed('草稿已變更，請再次套用；這次未保存。');return
            try:
                owned={(b['node'],b['field']) for b in self.window.state['multi_output']['bindings'] if b['workflow']==profile['id']}
                verify(config,desc,owned=owned)
                if self.item:
                    self.window.comfy.input_flow.chain.store.edit_parameters(self.item['id'],self.stage_id,config,self.task_base,profile)
                else:
                    if self.canvas.data()['stages'].get(self.stage_id)!=self.initial_stage:raise ValueError('Stage 已被修改，草稿保留，請重新比較。')
                    from .generation import store_profile
                    from .stage_model import choose
                    def change(state):
                        store_profile(state,profile)
                        if state['multi_output']['stages'][self.stage_id].get('workflow')!=profile['id']:choose(state,self.stage_id,profile['id'])
                        state['multi_output']['stages'][self.stage_id]['parameters']=copy.deepcopy(config)
                    if not self.canvas.commit(change):raise ValueError('套用未保存，草稿已保留。')
                self.applied.emit(config);self.finish()
            except (ValueError,KeyError) as exc:failed(str(exc))
        self.read(received,failed)

    def request_close(self):
        pending=self.dirty() or self.other_drafts()
        if pending and not ask(self,'放棄未套用的變更','關閉並放棄這次草稿？取消可繼續編輯。'):return
        self.finish()

    def finish(self):
        if self.closed:return
        self.closed=True;self.serial+=1;self.watch.stop();self.catalog.changed.disconnect(self.catalog_changed)
        QApplication.instance().removeEventFilter(self)
        self.parentWidget().removeEventFilter(self);self.hide();self.deleteLater()

    def place(self):
        area=self.parentWidget().rect();self.setGeometry(area);margin=8 if area.height()<700 or area.width()<900 else 20
        w=min(1120,max(100,area.width()-2*margin));h=min(820,max(100,area.height()-2*margin))
        self.panel.setGeometry((area.width()-w)//2,(area.height()-h)//2,w,h)
        narrow=w<max(880,2*self.field_width()+368);self.nodes_button.setVisible(narrow);self.narrow_bar.setVisible(narrow)
        if not narrow:
            if not self.docked:self.content.insertWidget(0,self.left);self.docked=True
            self.narrow_body.removeWidget(self.workflow_area);self.left_body.insertWidget(0,self.workflow_area)
            self.left.setFixedWidth(280 if w>1050 else 250)
            self.left.show()
        else:
            self.left_body.removeWidget(self.workflow_area);self.narrow_body.insertWidget(0,self.workflow_area,1)
            if self.docked:self.content.removeWidget(self.left);self.left.setParent(self.panel);self.docked=False;self.left.hide()
            self.left.setFixedWidth(min(max(280,self.field_width()),w-48))
        full_actions=((self.reset_button,term('reset',self.language)),(self.diff_button,term('diff',self.language)),(self.apply_button,self.apply_button.text()))
        compact=w<760 or sum(a.fontMetrics().horizontalAdvance(text)+32 for a,text in full_actions)+200>w
        self.reset_button.setText(('重設' if self.language=='zh-TW' else 'Reset') if compact else term('reset',self.language))
        self.diff_button.setText(('變更' if self.language=='zh-TW' else 'Changes') if compact else term('diff',self.language))
        self.footer.setFixedHeight(max(82,self.status.fontMetrics().height()*2+30,self.apply_button.sizeHint().height()+30))
        self.panel.layout().activate()
        if narrow and self.left.isVisible():
            self.left.setParent(self.panel);self.left.setGeometry(24,self.editor.y(),self.left.width(),self.editor.height());self.left.raise_()
        self.reset_button.setToolTip(term('reset',self.language));self.diff_button.setToolTip(term('diff',self.language))
        rect=self.panel.geometry();dw=min(360,max(220,w-40));x=rect.right()+12
        if x+dw>area.right()-8:x=rect.right()-dw-12
        self.drawer.setGeometry(x,rect.top()+50,dw,max(140,h-105))

    def eventFilter(self,watched,event):
        if watched is self.search and event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):
            self.layout_watch.start(0)
        if event.type()==QEvent.Type.ShortcutOverride and isinstance(watched,QWidget) and (watched is self or self.isAncestorOf(watched)):
            if event.key()==Qt.Key.Key_Escape:event.accept();return True
        if watched is self.parentWidget() and event.type()==QEvent.Type.Resize:
            self.place();self.select_node()
        return False

    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:
            if self.drawer.isVisible():self.drawer.hide()
            elif self.nodes_button.isVisible() and self.left.isVisible():self.left.hide()
            else:self.request_close()
            event.accept();return
        super().keyPressEvent(event)

    def mousePressEvent(self,event):
        if not self.panel.geometry().contains(event.position().toPoint()) and not (self.drawer.isVisible() and self.drawer.geometry().contains(event.position().toPoint())):self.request_close()
        event.accept()


def open_parameters(canvas,stage_id,item=None):
    prior=getattr(canvas.window,'stage_parameter_sheet',None)
    if prior and isValid(prior) and not prior.closed:prior.raise_();return prior
    sheet=ParameterSheet(canvas,stage_id,item);canvas.window.stage_parameter_sheet=sheet;return sheet
