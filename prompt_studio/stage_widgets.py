"""Independent Stage cards and scoped schedule editors."""
import copy
import json
from PySide6.QtCore import Qt,QTimer,QSize,QEvent,QObject
from PySide6.QtGui import QIcon,QImageReader,QPixmap
from PySide6.QtWidgets import QFrame,QVBoxLayout,QListWidget,QListWidgetItem,QPlainTextEdit,QDialog,QLineEdit
from .widgets import label,button,row,ComboBox,StudioDialog,RoundMenu,dialog_buttons
from .workflow_binding import WorkflowBindingDialog
from .output_order import OutputOrderList
from .stage_model import choose,compile_plan

NAMES=dict(waiting='等待',running='執行中',preparing='準備輸入',submitted='已提交',collecting='保存結果',
    complete='完成',paused='已暫停',failed='失敗',unconfirmed='提交未確認',result_error='圖片尚未取得',cancelled='已取消',removed='已移除',retained='已保留',retried='已建立新嘗試')


def entry_image(item):
    if item.get('source'):return item['source']
    saved=item.get('saved',{})
    value=next((v['value'] for v in [*saved.get('values',{}).values(),*saved.get('inputs',{}).values()] if v['type']=='image'),None)
    return value or next((images[0] for images in saved.get('batches',{}).values() if images),None)


class RecentCorner(QObject):
    def __init__(self,canvas):
        super().__init__(canvas);self.canvas=canvas;self.viewport=canvas.view.viewport()
        self.button=button('最近生成',canvas.window.show_recent_sheet,'Quiet');self.button.setParent(self.viewport)
        self.button.setToolTip('開啟右側歷史面板；不改變流程圖片來源');self.viewport.installEventFilter(self);self.place()
    def place(self):
        self.button.adjustSize();self.button.move(max(8,self.viewport.width()-self.button.width()-16),max(8,self.viewport.height()-self.button.height()-16));self.button.show();self.button.raise_()
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):self.place()
        return False


class PendingList(OutputOrderList):
    def __init__(self,panel):super().__init__();self.panel=panel
    def startDrag(self,actions):
        item=self.panel.selected()
        if not item or item['status']!='waiting':return
        self.panel.dragging=True
        try:super().startDrag(actions)
        finally:self.panel.dragging=False;self.panel.refresh()
    def move_row(self,source,index):
        item=self.panel.runner.store.read(source)
        if not item or item['status']!='waiting':return
        fixed=sum(self.item(i).data(Qt.ItemDataRole.UserRole) in {r['id'] for r in self.panel.rows if r['status']!='waiting'} for i in range(self.count()))
        super().move_row(source,max(fixed,index))


class StageDialog(WorkflowBindingDialog):
    manual_choice=True
    empty_choice='選擇工作流'
    def __init__(self,canvas,key):
        value=canvas.data()['stages'][key];self.image_binding=value if value.get('workflow') else None
        super().__init__(canvas,key);self.setWindowTitle('選擇工作流')
        self.nodes_label.hide();self.target.hide()

    def refresh_targets(self):
        self.target.setEnabled(self.profile() is not None)
        self.hint.clear();self.hint.hide()

    def apply(self):
        from .generation import store_profile
        profile=self.binding_profile()
        if profile is None:raise ValueError('請選擇工作流。')
        def change(state):
            store_profile(state,copy.deepcopy(profile));choose(state,self.key,profile['id'])
        self.canvas.commit(change)


class StagePanel(QFrame):
    def __init__(self,canvas,key):
        super().__init__();self.canvas=canvas;self.key=key;self.setObjectName('InsetPanel')
        body=QVBoxLayout(self);body.setContentsMargins(14,10,14,14)
        body.addWidget(button('選擇工作流',self.edit,'Quiet'))
        self.status=label('','Subtle',True);body.addWidget(self.status)
        self.canvas.window.comfy.stateChanged.connect(self.refresh)

    def edit(self):
        dialog=StageDialog(self.canvas,self.key)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            try:dialog.apply()
            except ValueError as exc:self.canvas.window.notice(str(exc))

    def refresh(self):
        runner=self.canvas.window.comfy.input_flow.chain
        error=next((a.get('error','') for a in reversed(runner.store.rows('attempt',self.canvas.window.state['workspace']))
                    if a.get('stage')==self.key and a['status'] in ('failed','unconfirmed','result_error')), '')
        latest=next((a for a in reversed(runner.store.rows('attempt',self.canvas.window.state['workspace'])) if a.get('stage')==self.key),None)
        if latest and latest['status'] not in ('failed','unconfirmed','result_error'):error=''
        self.status.setText(error);self.status.setVisible(bool(error))


class SchedulePanel(QFrame):
    def __init__(self,canvas,key):
        super().__init__();self.canvas=canvas;self.key=key;self.rows=[];self.dragging=False;self.setObjectName('InsetPanel')
        body=QVBoxLayout(self);body.setContentsMargins(14,12,14,12)
        self.summary=label('','Subtle',True);body.addWidget(self.summary)
        self.policy_button=button('輸入取值',self.policies,'Quiet')
        body.addLayout(row(self.policy_button,button('增加資料接點',self.add_channel,'Quiet')))
        self.list=PendingList(self);self.list.setIconSize(QSize(48,48));self.list.setMinimumHeight(180)
        self.list.orderChanged.connect(self.reordered);self.list.itemDoubleClicked.connect(self.detail);body.addWidget(self.list,1)
        body.addWidget(label('拖曳等待項目調整順序；雙擊查看完整內容。','Subtle',True))
        body.addLayout(row(button('暫停',lambda:self.action('pause'),'Quiet'),button('繼續',lambda:self.action('resume'),'Quiet'),button('取消流程',lambda:self.action('cancel'),'Quiet')))
        body.addLayout(row(button('移除等待項目',self.remove,'Quiet'),button('歷史',lambda:show_history(canvas.window),'Quiet')))
        self.canvas.window.comfy.stateChanged.connect(self.refresh);self.refresh()

    @property
    def runner(self):return self.canvas.window.comfy.input_flow.chain

    def entries(self):return [r for r in self.runner.store.rows('entry',self.canvas.window.state['workspace'],active=True) if r['scheduler']==self.key]

    def selected(self):
        item=self.list.currentItem();return self.runner.store.read(item.data(Qt.ItemDataRole.UserRole)) if item else None

    def refresh(self):
        if self.dragging or self.runner.closed:return
        rows=self.entries();value=self.canvas.data()['schedulers'].get(self.key,{})
        ids=[r['id'] for r in rows];old=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        selected=self.selected();selected=selected['id'] if selected else None
        self.list.blockSignals(True)
        if ids!=old:
            self.list.clear()
            for item in rows:
                entry=QListWidgetItem();entry.setData(Qt.ItemDataRole.UserRole,item['id']);self.list.addItem(entry)
            if selected in ids:self.list.setCurrentRow(ids.index(selected))
        for index,item in enumerate(rows):
            source=entry_image(item);counts=[len(v) for v in item['saved'].get('batches',{}).values()]
            count=1 if item.get('source') else max(counts) if counts else 1 if source else None
            entry=self.list.item(index);entry.setText(f"{index+1} · {item['label']}\n{NAMES.get(self.runner.entry_status(item),item['status'])}"+(' · '+str(count)+' 張' if count is not None else '')+(' · '+source['name'] if source else ''))
            entry.setToolTip('雙擊查看完整內容');entry.setFlags(entry.flags() | Qt.ItemFlag.ItemIsDragEnabled if item['status']=='waiting' else entry.flags() & ~Qt.ItemFlag.ItemIsDragEnabled)
            if source:entry.setIcon(QIcon(str(self.canvas.window.store.directory/source['relative'])))
        self.list.blockSignals(False);self.rows=rows
        self.policy_button.setVisible(value.get('mode')=='stage')
        mode='Stage 組合' if value.get('mode')=='stage' else '資料輸入'
        if self.runner.store.scheduler_paused(self.canvas.window.state['workspace'],self.key):mode+=' · 已暫停'
        self.summary.setText(mode+' · '+str(len(rows))+'／10 個未結束項目\n'+self.runner.progress()+'\n'+self.runner.expansion(self.key,len(rows) or self.canvas.window.state['settings'].get('comfy_count',1)))

    def reordered(self):
        ids=[self.list.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.list.count())]
        entries={i['id']:i for i in self.entries()}
        try:
            owners={entries[k]['owner'] for k in ids if k in entries}
            if len(owners)>1:raise ValueError('不同外層項目請在各自清單內調整。')
            if owners:self.runner.store.reorder(next(iter(owners)),[k for k in ids if entries[k]['status']=='waiting'])
        except ValueError as exc:self.canvas.window.notice(str(exc))
        self.refresh()

    def detail(self,*_):
        item=self.selected()
        if item:entry_detail(self.canvas.window,item)

    def remove(self):
        item=self.selected()
        try:
            if item:self.runner.store.remove(item['id']);self.runner.later();self.refresh()
        except ValueError as exc:self.canvas.window.notice(str(exc))

    def action(self,method):
        try:
            if method in ('pause','resume'):
                self.runner.store.set_scheduler_paused(self.canvas.window.state['workspace'],self.key,method=='pause')
            entries=[item for item in self.runner.store.rows('entry',self.canvas.window.state['workspace']) if item['scheduler']==self.key and item['status'] in ('waiting','running','retained')]
            runs={item['run'] for item in entries}
            for ident in runs:
                if method=='resume':
                    try:self.runner.check_route(self.runner.store.read(ident))
                    except ValueError:continue
                getattr(self.runner,method)(ident)
            self.refresh()
        except ValueError as exc:self.canvas.window.notice(str(exc))

    def add_channel(self):
        from .flow_data import TYPES,TYPE_NAMES
        menu=RoundMenu(self)
        for kind in TYPES:
            def add(checked=False,kind=kind):
                def change(state):
                    channels=state['multi_output']['schedulers'][self.key]['channels'];number=len(channels)+1
                    channels.append(dict(id=kind+str(number),type=kind,name=TYPE_NAMES[kind]+str(number)))
                self.canvas.commit(change)
            menu.addAction(TYPE_NAMES[kind],add)
        menu.open_at(self.cursor().pos())

    def policies(self):
        try:plan=compile_plan(self.canvas.window.state)
        except ValueError as exc:self.canvas.window.notice(str(exc));return
        scope=plan['scopes'].get(self.key)
        if not scope:self.canvas.window.notice('資料預排程只保存接入的資料；其他輸入保持即時。排整個 Stage 時，將右側流程引用接到預排程左側。');return
        dialog=StudioDialog(self.canvas.window);dialog.setWindowTitle('Stage 預排程 · 輸入取值');pickers={}
        for key in scope['stages']:
            stage=plan['stages'][key];dialog.body.addWidget(label(stage['name']))
            for binding in stage['bindings']:
                picker=ComboBox();picker.addItem('保存設定／內容','saved');picker.addItem('執行時讀取','live')
                picker.setCurrentIndex(picker.findData(scope['policies'].get(binding['key'],'saved')))
                title=self.canvas.data()['clip_inputs'].get(binding['key']) or self.canvas.data()['image_inputs'].get(binding['key'])
                dialog.body.addLayout(row(label(title['name']),picker));pickers[binding['key']]=picker
        dialog.body.addWidget(dialog_buttons(dialog,dialog.accept))
        if dialog.exec()==QDialog.DialogCode.Accepted:self.canvas.commit(lambda s:s['multi_output']['schedulers'][self.key].update(policies={k:p.currentData() for k,p in pickers.items()}))


def entry_detail(window,item):
    runner=window.comfy.input_flow.chain;dialog=StudioDialog(window);dialog.setWindowTitle(item['label']+' · 完整內容');dialog.resize(740,620)
    saved=copy.deepcopy(item['saved']);status=label('','Subtle',True);dialog.body.addWidget(status)
    run=runner.store.read(item['run']);names={}
    policy=[]
    for stage_id in item['stages']:
        stage=run['plan']['stages'][stage_id]
        for binding in stage['bindings']:
            name=stage['name']+' · '+binding['target']['workflow']+' · #'+binding['target']['node']+('/'+binding['target'].get('field','') if binding['kind']=='clip' else '')
            names[binding['key']]=name
            policy.append(name+'：'+('已保存' if binding['key'] in saved.get('values',{}) else '經資料預排程' if stage['scheduler'] else '本輪結果／執行時讀取'))
    dialog.body.addWidget(label('\n'.join(policy),'Subtle',True))
    children=QListWidget();children.setMaximumHeight(125);dialog.body.addWidget(children)
    def open_child(*_):
        selected=children.currentItem()
        if selected:entry_detail(window,runner.store.read(selected.data(Qt.ItemDataRole.UserRole)))
    children.itemDoubleClicked.connect(open_child)
    def refresh_children():
        values=[e for e in runner.store.rows('entry',item['workspace']) if e.get('parent')==item['id']]
        children.setVisible(bool(values))
        ids=[v['id'] for v in values];old=[children.item(i).data(Qt.ItemDataRole.UserRole) for i in range(children.count())]
        if ids!=old:
            selected=children.currentItem();selected=selected.data(Qt.ItemDataRole.UserRole) if selected else None
            children.clear()
            for child in values:
                row=QListWidgetItem();row.setData(Qt.ItemDataRole.UserRole,child['id']);children.addItem(row)
            if selected in ids:children.setCurrentRow(ids.index(selected))
        for i,child in enumerate(values):children.item(i).setText('內層 · '+child['label']+' · '+NAMES.get(runner.entry_status(child),child['status']))
    values={('values',k):v for k,v in saved.get('values',{}).items()}
    values.update({('inputs',k):v for k,v in saved.get('inputs',{}).items()})
    batch_refs={}
    for source,images in saved.get('batches',{}).items():
        for index,image in enumerate(images):
            key=('batch:'+source,str(index));batch_refs[key]=(source,index)
            values[key]=dict(type='image',value=image,origin=dict(name=f'來源清單第 {index+1}／{len(images)} 張 · '+image['name']))
    picker=ComboBox();editor=QPlainTextEdit();editor.setMinimumHeight(280)
    for key,value in values.items():picker.addItem(names.get(key[1]) or ('文字' if value['type']=='clip' else '圖片' if value['type']=='image' else '文字組合')+' · '+value.get('origin',{}).get('name','保存輸入'),key)
    picture=label('');picture.setAlignment(Qt.AlignmentFlag.AlignCenter);picture.setMaximumHeight(180)
    prior=[None]
    def persist():
        if prior[0] in values and values[prior[0]]['type']=='clip' and item['status']=='waiting':values[prior[0]]['value']=editor.toPlainText()
    def selected(*_):
        persist();key=picker.currentData();prior[0]=key;value=values.get(key,{})
        editor.setReadOnly(item['status']!='waiting' or value.get('type')!='clip')
        picture.clear();picture.setVisible(value.get('type')=='image')
        if value.get('type')=='image':
            source=value['value'];editor.setPlainText('圖片：'+source['name']+'\n'+('逐張執行時讀取此圖片的文字資料。' if key in batch_refs else '替換只影響此圖片輸入；另外保存的文字保持原內容。'))
            from .composition_image import source_path
            try:
                reader=QImageReader(str(source_path(window.store.directory,source,verify=True)));size=reader.size();size.scale(640,180,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
                picture.setPixmap(QPixmap.fromImage(reader.read()))
            except (ValueError,OSError) as exc:editor.appendPlainText(str(exc))
        else:editor.setPlainText(value.get('value','') if value.get('type')=='clip' else json.dumps(value.get('value',{}),ensure_ascii=False,indent=2))
    picker.currentIndexChanged.connect(selected);dialog.body.addWidget(picker);dialog.body.addWidget(picture);dialog.body.addWidget(editor);selected()
    if not values:
        picker.hide();editor.setPlainText('上游結果將使用本輪的實際輸出；未保存的輸入於提交時讀取。')
    def save():
        persist()
        for key,(source,index) in batch_refs.items():saved['batches'][source][index]=values[key]['value']
        try:runner.store.edit(item['id'],saved);dialog.accept()
        except ValueError as exc:window.notice(str(exc))
    save_button=button('保存此項',save,'Primary')
    def image_change():
        from PySide6.QtWidgets import QFileDialog
        from .image_bindings import import_source
        key=picker.currentData()
        if item['status']!='waiting' or values.get(key,{}).get('type')!='image':return
        path,_=QFileDialog.getOpenFileName(dialog,'替換此項圖片','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path:
            try:values[key]['value']=import_source(path,window.store.directory);values[key]['origin']={};selected()
            except (ValueError,OSError) as exc:window.notice(str(exc))
    image_button=button('替換此項圖片',image_change,'Quiet')
    dialog.body.addLayout(row(save_button,image_button,None,button('關閉',dialog.reject,'Quiet')))
    def refresh_status():
        if runner.closed:return
        current=runner.store.read(item['id']);item['status']=current['status']
        waiting=item['status']=='waiting';save_button.setEnabled(waiting and bool(values))
        image_button.setEnabled(waiting and values.get(picker.currentData(),{}).get('type')=='image')
        editor.setReadOnly(not waiting or values.get(picker.currentData(),{}).get('type')!='clip')
        status.setText(NAMES.get(runner.entry_status(current),item['status'])+' · '+('修改只影響這一項' if waiting else '已提交，僅供查看'))
        refresh_children()
    timer=QTimer(dialog);timer.setInterval(150);timer.timeout.connect(refresh_status);timer.start();dialog.finished.connect(timer.stop);picker.currentIndexChanged.connect(refresh_status);refresh_status()
    dialog.exec()


def show_history(window,stage=None):
    runner=window.comfy.input_flow.chain
    if not hasattr(runner.store,'rows'):
        from .chain_history import show
        return show(window)
    dialog=StudioDialog(window);dialog.setWindowTitle('Stage 執行紀錄');dialog.resize(850,650)
    listing=QListWidget();detail=QPlainTextEdit();detail.setReadOnly(True)
    rows=[]
    for run in runner.store.rows('run',window.state['workspace']):
        if stage is not None and stage not in run['plan']['stages']:continue
        rows.append(run);listing.addItem('流程：'+' → '.join(run['plan']['stages'][k]['name'] for k in run['plan']['order'])+' · '+NAMES.get(run['status'],run['status']))
        for item in runner.store.rows('attempt',owner=run['id']):
            if stage is None or item['stage']==stage:
                rows.append(item);listing.addItem('    '+run['plan']['stages'][item['stage']]['name']+' · '+NAMES.get(item['status'],item['status'])+' · '+item['id'][:8])
    def select(*_):
        if 0<=listing.currentRow()<len(rows):
            item=runner.store.read(rows[listing.currentRow()]['id']);job=window.comfy.generation.record(item['id'])
            from .job_details import describe
            if item['kind']=='run':
                entries=[e for e in runner.store.rows('entry') if e['run']==item['id']]
                detail.setPlainText(item.get('message','')+'\n'+NAMES.get(item['status'],item['status'])+'\n\n'+ '\n'.join(e['label']+' · '+NAMES.get(e['status'],e['status']) for e in entries))
            else:detail.setPlainText(item.get('error','')+'\n'+(describe(job) if job else '尚未提交')+'\n'+json.dumps(item.get('result',{}),ensure_ascii=False,indent=2))
    listing.currentRowChanged.connect(select);dialog.body.addWidget(listing);dialog.body.addWidget(detail)
    def action(method):
        if not 0<=listing.currentRow()<len(rows):return
        item=rows[listing.currentRow()]
        try:
            if method in ('recheck','retry') and item['kind']=='run':raise ValueError('請選擇下方的實際提交項目；尚未提交的流程修正原因後可按繼續。')
            if method=='recheck':window.comfy.generation.recheck(item['id'])
            elif method=='retry':runner.retry(item['id'])
            else:getattr(runner,method)(item['id'] if item['kind']=='run' else item['owner'])
            select()
        except ValueError as exc:window.notice(str(exc))
    dialog.body.addLayout(row(button('繼續',lambda:action('resume'),'Quiet'),button('取消這份流程',lambda:action('cancel'),'Quiet'),button('重取結果／重試失敗項',lambda:action('retry'),'Quiet'),button('查看原任務回覆',lambda:action('recheck'),'Quiet'),button('關閉',dialog.accept,'Quiet')))
    listing.setCurrentRow(len(rows)-1);dialog.exec()
