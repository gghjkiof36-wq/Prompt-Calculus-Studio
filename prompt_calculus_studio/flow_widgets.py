"""Canvas editors for typed input buffers and explicit image destinations."""
import copy
from PySide6.QtCore import Qt,QRectF,QSize,QTimer
from PySide6.QtGui import QImageReader,QPixmap,QIcon,QColor
from PySide6.QtWidgets import QFrame,QVBoxLayout,QListWidget,QListWidgetItem,QPlainTextEdit,QDialog,QAbstractItemView,QGraphicsItem
from .widgets import label,button,row,ComboBox,RoundMenu,StudioDialog,dialog_buttons,scrolling
from .canvas_items import TextCard
from .workflow_binding import WorkflowBindingDialog
from .flow_data import TYPE_NAMES,TYPES,endpoint,CAPACITY
from .output_order import OutputOrderList


def show_job_images(window,record):
    """Only paths resolved from this exact terminal task can enter this view."""
    from urllib.parse import quote
    if record['route']['server']!=window.comfy.url:
        window.notice('請先連線至這項工作的原 ComfyUI 服務。');return
    prompt=record.get('prompt_id')
    if not prompt:window.notice('這項紀錄沒有可確認的圖片結果。');return
    def receive(rows):
        matched=[r for r in rows if r.get('prompt_id')==prompt and r.get('path') and
                 r.get('image') in record.get('outputs',{}).get(str(r.get('node_id')),{}).get('images',[])]
        if not matched:window.notice('這項工作的圖片已不在 ComfyUI 歷史或原圖位置，沒有改用其他圖片。');return
        dialog=StudioDialog(window);dialog.setWindowTitle('這項工作的圖片結果');dialog.resize(740,700)
        container=QFrame();body=QVBoxLayout(container)
        for result in matched:
            reader=QImageReader(result['path']);size=reader.size();size.scale(650,480,Qt.AspectRatioMode.KeepAspectRatio)
            reader.setScaledSize(size);reader.setAutoTransform(True);photo=label('');photo.setAlignment(Qt.AlignmentFlag.AlignCenter)
            photo.setPixmap(QPixmap.fromImage(reader.read()));body.addWidget(photo)
            body.addWidget(label('節點 #'+str(result['node_id'])+' · '+result['image']['filename'],'Subtle',True))
        dialog.body.addWidget(scrolling(container),1);dialog.body.addWidget(button('關閉',dialog.accept,'Quiet'));dialog.exec();dialog.deleteLater()
    window.comfy.request('desktop/results?prompt_id='+quote(prompt,safe=''),done=receive,failed=window.notice)


def retained_records(window):
    """Recovery is explicit; restoring a panel never submits or rewires work."""
    runner=window.comfy.input_flow;dialog=StudioDialog(window);dialog.setWindowTitle('保留的預排程與舊版紀錄');dialog.resize(760,600)
    listing=QListWidget();detail=QPlainTextEdit();detail.setReadOnly(True)
    entries=[('typed',r) for r in runner.store.rows(history=True) if r['state'] not in ('complete','removed')]+[('legacy',r) for r in window.comfy.queue.store.rows()]
    for kind,item in entries:listing.addItem(('預排程' if kind=='typed' else '舊版紀錄')+' · '+item.get('label','')+' · '+('舊版保留' if item['state']=='retained' else item['state']))
    def selected(index):
        if not 0<=index<len(entries):return
        kind,item=entries[index]
        if kind=='typed':
            detail.setPlainText('工作流：'+item['route']['workflow']+'\n'+item.get('error','')+'\n\n'+
                '\n'.join(str(v['value']) if v['type']=='clip' else v['value'].get('name',v['value'].get('text','文字組合')) for v in item['inputs'].values()))
        else:
            work=item['work'];detail.setPlainText('舊版整份工作流快照，保留閱讀；未轉成新預排程。\n工作流：'+work['generation']['workflow']+
                '\n'+item.get('error','')+'\n\n'+work['snapshot'].get('final_prompt',''))
    def restore():
        index=listing.currentRow()
        if not 0<=index<len(entries):return
        kind,item=entries[index]
        if kind!='typed':window.notice('舊整份工作流快照維持原紀錄，不轉成新預排程。');return
        owner=item['owner'];route=item['route'];node=route['scheduler'];canvas=window.canvas
        if route['workspace']!=window.state['workspace']:window.notice('請先切回這項工作的來源工作區。');return
        if node in canvas.data()['schedulers']:
            canvas.view.centerOn(canvas.flow_cards[node]);dialog.accept();return
        point=canvas.view.mapToScene(canvas.view.viewport().rect().center())
        def change(state):
            state['multi_output']['schedulers'][node]=dict(name='恢復的預排程',channels=[dict(id=k.split('::',1)[1],type=v['type'],name=TYPE_NAMES[v['type']]) for k,v in item['inputs'].items()])
            state.setdefault('text_positions',{})[node]=[point.x(),point.y()]
        runner.pause(owner)
        if canvas.commit(change):dialog.accept();window.notice('已恢復預排程模塊；接回原工作流及來源後，按「繼續」。')
    listing.currentRowChanged.connect(selected);dialog.body.addWidget(listing,1);dialog.body.addWidget(detail,1)
    if window.state.get('multi_output',{}).get('version',0)>=7:
        dialog.body.addWidget(label('舊版項目僅保留閱讀；請以新版 Stage 與預排程明確建立新工作。','Subtle',True))
        dialog.body.addWidget(button('關閉',dialog.accept,'Quiet'))
    else:dialog.body.addLayout(row(button('顯示／恢復這份預排程',restore,'Quiet'),None,button('關閉',dialog.accept,'Quiet')))
    listing.setCurrentRow(0);dialog.exec();dialog.deleteLater()


class ImageInputDialog(WorkflowBindingDialog):
    manual_choice=True
    def __init__(self,canvas,key):
        target=canvas.data()['image_inputs'][key]
        self.image_binding=target if target.get('workflow') else None
        super().__init__(canvas,key)
        self.setWindowTitle('圖片輸入 · 選擇接收節點');self.nodes_label.setText('接收圖片的 LoadImage 節點')
        if self.workflow.count():self.workflow.setItemText(0,'不綁定')

    def refresh_targets(self):
        self.target.clear();self.target.addItem('不綁定',None);profile=self.profile()
        for key,node in (profile or {}).get('graph',{}).items():
            if node.get('class_type')=='LoadImage' and isinstance(node.get('inputs',{}).get('image'),str):
                self.target.addItem(node.get('_meta',{}).get('title','LoadImage')+' · #'+key,key)
        old=self.canvas.data()['image_inputs'][self.key]
        self.target.setCurrentIndex(max(0,self.target.findData(old.get('node'))))
        self.target.setEnabled(profile is not None)
        self.hint.setText('這份工作流沒有接收圖片的 LoadImage 節點；PreviewImage／SaveImage 是輸出節點。' if profile and self.target.count()==1 else '')
        self.hint.setVisible(bool(self.hint.text()))

    def apply(self):
        from .generation import store_profile
        try:profile=self.binding_profile()
        except ValueError as exc:self.canvas.window.notice(str(exc));return
        target=self.target.currentData()
        def change(state):
            if profile:store_profile(state,copy.deepcopy(profile))
            state['multi_output']['image_inputs'][self.key].update(workflow=profile['id'] if profile and target else None,node=target)
        self.canvas.commit(change)


class ImageInputPanel(QFrame):
    def __init__(self,canvas,key):
        super().__init__();self.canvas=canvas;self.key=key;self.setObjectName('CanvasModuleBody')
        body=QVBoxLayout(self);body.setContentsMargins(4,8,4,4);body.setSpacing(8)
        self.setup_status=label('');body.addWidget(self.setup_status,0,Qt.AlignmentFlag.AlignLeft)
        self.choose=button('綁定 LoadImage 節點',self.bind);self.choose.setProperty('iconName','media');body.addWidget(self.choose)

    def bind(self):
        dialog=ImageInputDialog(self.canvas,self.key)
        if dialog.exec()==QDialog.DialogCode.Accepted:dialog.apply()

    def refresh(self):
        value=self.canvas.data()['image_inputs'][self.key]
        profile=next((p for p in self.canvas.window.state.get('generation',{}).get('profiles',[]) if p['id']==value.get('workflow')),None)
        from .canvas_onboarding import setup_action,setup_badge
        pending=setup_action(self.canvas,self.choose,'image_inputs',self.key)
        setup_badge(self.canvas,self.setup_status,pending)
        self.choose.setText(profile['name']+' · LoadImage #'+str(value['node']) if not pending else '綁定 LoadImage 節點')


class ScheduleList(OutputOrderList):
    mime='application/x-pcs-schedule-order'
    def __init__(self,panel):
        super().__init__();self.panel=panel;self.dragging=False
        self.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
    def startDrag(self,actions):
        item=self.panel.item()
        if not item or item['state']!='waiting':return
        self.dragging=True
        try:super().startDrag(actions)
        finally:self.dragging=False;QTimer.singleShot(0,self.panel.refresh)
    def move_row(self,source,index):
        before=[self.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.count())]
        fixed={i:key for i,key in enumerate(before) if self.panel.runner.store.read(key)['state']!='waiting'}
        if source not in before:return
        old=before.index(source);index=max(0,min(len(before),index))-int(old<index)
        after=list(before);after.remove(source);after.insert(index,source)
        try:
            if any(after[i]!=key for i,key in fixed.items()):raise ValueError('提交中及生成中的項目固定位置。')
            self.panel.runner.store.reorder(self.panel.key,[key for key in after if key not in fixed.values()])
        except ValueError as exc:self.panel.canvas.window.notice(str(exc))
        self.panel.signature=None;QTimer.singleShot(0,self.panel.refresh)


class InputDetailDialog(StudioDialog):
    # This editor belongs to a persistent Canvas node, unlike one-shot sheets.
    def exec(self):return QDialog.exec(self)


class SchedulePanel(QFrame):
    def __init__(self,canvas,key):
        super().__init__();self.canvas=canvas;self.key=key;self.updating=False;self.selected=None;self.setObjectName('InsetPanel')
        body=QVBoxLayout(self);body.setContentsMargins(14,12,14,12)
        self.status=label('只保存接入資料 · 最多十項','Subtle',True);body.addWidget(self.status)
        self.add_channel_button=button('增加端口',self.add_channel,'Quiet')
        body.addLayout(row(button('暫停',lambda:self.action('pause'),'Quiet'),button('繼續',lambda:self.action('resume'),'Quiet'),
                           button('取消目前',lambda:self.action('cancel'),'Quiet'),self.add_channel_button))
        self.listing=ScheduleList(self);self.listing.setMinimumHeight(210);self.listing.setIconSize(QSize(52,52));self.listing.currentItemChanged.connect(self.selection_changed);body.addWidget(self.listing,1)
        self.listing.itemClicked.connect(self.open_detail)
        body.addWidget(label('拖曳等待項目調整順序；點擊查看完整內容。','Subtle',True))
        self.failure_button=button('處理失敗項目',self.failure_menu,'Quiet')
        body.addLayout(row(button('上移',lambda:self.move('up'),'Quiet'),button('下移',lambda:self.move('down'),'Quiet'),
                           button('移除',lambda:self.move('remove'),'Quiet'),self.failure_button))
        self.detail_id=None;self.detail_state=None
        self.detail=InputDetailDialog(canvas.window);self.detail.setWindowTitle('預排程完整內容');self.detail.resize(720,580)
        self.destroyed.connect(self.detail.deleteLater)
        self.ports=ComboBox();self.ports.currentIndexChanged.connect(self.show_value);self.detail.body.addWidget(self.ports)
        self.editor=QPlainTextEdit();self.editor.setPlaceholderText('選取等待項目，檢視或修改保存的文字。');self.detail.body.addWidget(self.editor,1)
        self.image_name=label('','Subtle',True);self.detail.body.addWidget(self.image_name)
        self.save_button=button('保存文字',self.save_text,'Quiet');self.replace_button=button('替換圖片',self.replace_image,'Quiet')
        self.detail.body.addLayout(row(self.save_button,self.replace_button,None,button('關閉',self.detail.accept,'Quiet')))
        body.addWidget(button('已完成紀錄',self.history,'Quiet'))
        body.addWidget(button('停止這批後續供應（已保存項目保留）',lambda:self.action('stop_feed'),'Quiet'))
        self.canvas.window.comfy.stateChanged.connect(self.refresh)

    @property
    def runner(self):return self.canvas.window.comfy.input_flow

    def action(self,name):
        getattr(self.runner,name)(self.key);self.refresh()

    def item(self):
        current=self.listing.currentItem()
        return self.runner.store.read(current.data(Qt.ItemDataRole.UserRole)) if current else None

    def detail_item(self):
        return self.runner.store.read(self.detail_id) if self.detail_id else self.item()

    def refresh(self):
        if self.key not in self.canvas.data().get('schedulers',{}):return
        if self.listing.dragging:return
        current=self.listing.currentItem();ident=current.data(Qt.ItemDataRole.UserRole) if current else self.selected
        rows=self.runner.store.rows(self.key);control=self.runner.store.control(self.key)
        signature=[(r['id'],r['state'],r.get('error'),r.get('label'),r.get('modified')) for r in rows]
        self.updating=True
        if signature!=getattr(self,'signature',None):
            self.signature=signature
            names={'waiting':'等待','preparing':'提交中','submitted':'已提交','unconfirmed':'待核對','failed':'失敗'}
            existing={self.listing.item(i).data(Qt.ItemDataRole.UserRole):self.listing.item(i) for i in range(self.listing.count())}
            for number,record in enumerate(rows):
                item=existing.pop(record['id'],None)
                if item is None:item=QListWidgetItem();self.listing.insertItem(number,item)
                elif self.listing.row(item)!=number:self.listing.insertItem(number,self.listing.takeItem(self.listing.row(item)))
                sources=[name for value in record['inputs'].values() for name in value.get('origin',{}).get('canvas_names',[])]
                title=record.get('label') or '保存輸入'
                if title=='目前輸入':title='、'.join(dict.fromkeys(sources)) or next((v['value']['name'] for v in record['inputs'].values() if v['type']=='image'),'文字輸入')
                item.setText(str(number+1)+' · '+title+'\n'+names.get(record['state'],record['state']))
                item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable|(Qt.ItemFlag.ItemIsDragEnabled if record['state']=='waiting' else Qt.ItemFlag.NoItemFlags))
                picture=next((v['value'] for v in record['inputs'].values() if v['type']=='image'),None)
                if picture:
                    reader=QImageReader(str(self.canvas.window.store.directory/picture['relative']));reader.setScaledSize(QSize(52,52));item.setIcon(QIcon(QPixmap.fromImage(reader.read())))
                profile=next((p['name'] for p in self.canvas.window.state.get('generation',{}).get('profiles',[]) if p['id']==record['route'].get('workflow')),record['route'].get('workflow',''))
                item.setData(Qt.ItemDataRole.UserRole,record['id']);item.setToolTip('\n'.join([profile,'、'.join(dict.fromkeys(sources)),record.get('error') or '點擊查看完整內容']))
                if record['id']==ident:self.listing.setCurrentItem(item)
            for item in existing.values():self.listing.takeItem(self.listing.row(item))
        feed=control.get('feed');remaining=max(0,feed['total']-feed['next']) if feed else 0
        self.status.setText(('已暫停 · ' if control['paused'] else '')+str(len(rows))+'／10 項'+(' · 尚有 '+str(remaining)+' 張待補入' if feed else '')+'\n'+(self.runner.message or '按執行保存接入資料，空閒時自動送出。'))
        self.updating=False
        if not self.listing.currentItem() and self.listing.count():self.listing.setCurrentRow(0)
        if self.listing.currentItem() and self.ports.count()==0:self.selection_changed()
        if self.detail_id and self.detail_item()['state']!=self.detail_state:self.show_value()

    def selection_changed(self,*_):
        if self.updating or self.detail.isVisible():return
        item=self.item();self.selected=item['id'] if item else None;self.ports.blockSignals(True);self.ports.clear()
        if item:
            names={c['id']:c['name'] for c in self.canvas.data()['schedulers'][self.key]['channels']}
            for key,value in item['inputs'].items():self.ports.addItem(names.get(key.split('::')[-1],TYPE_NAMES[value['type']]),key)
        self.ports.blockSignals(False);self.show_value()

    def open_detail(self,*_):
        if not self.item():return
        self.detail_id=self.item()['id'];self.selection_changed();self.detail.exec()
        self.detail_id=None;self.selection_changed()

    def show_value(self,*_):
        item=self.detail_item();key=self.ports.currentData();value=item['inputs'].get(key) if item else None
        self.detail_state=item['state'] if item else None
        mutable=bool(item and item['state']=='waiting');self.editor.setReadOnly(not mutable)
        self.save_button.setEnabled(mutable and bool(value and value['type']!='image'))
        self.replace_button.setEnabled(mutable and bool(value and value['type']=='image'))
        self.editor.setVisible(bool(value and value['type']!='image'));self.image_name.setVisible(bool(value and value['type']=='image'))
        if not value:self.editor.clear();return
        if value['type']=='clip':self.editor.setPlainText(value['value'])
        elif value['type']=='content':
            self.editor.setPlainText(value['value'].get('text',''));self.editor.setToolTip('保存修改後，此項改用完整手動文字；其他項目與原圖片不變。')
        else:self.image_name.setText(value['value']['name']+'\n'+str(value['value']['width'])+' × '+str(value['value']['height']))

    def save_text(self):
        item=self.detail_item();key=self.ports.currentData()
        if not item or key not in item['inputs']:return
        value=item['inputs'][key]
        if value['type']=='image':return
        value['value']=self.editor.toPlainText() if value['type']=='clip' else dict(kind='raw',text=self.editor.toPlainText())
        if 'composition' in value.get('origin',{}):value['origin']['composition']=dict(kind='raw',text=self.editor.toPlainText())
        try:self.runner.store.edit(item['id'],item['inputs']);self.runner.notify('已保存這一項的修改。')
        except ValueError as exc:self.canvas.window.notice(str(exc))

    def replace_image(self):
        from .local_files import choose_file
        item=self.detail_item();key=self.ports.currentData()
        if not item or key not in item['inputs'] or item['inputs'][key]['type']!='image':return
        path=choose_file(self.canvas.window,'替換這項圖片','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not path:return
        try:
            item['inputs'][key]['value']=self.canvas.window.generation_panel.import_source(path)
            self.runner.store.edit(item['id'],item['inputs']);self.runner.notify('已替換這一項的圖片；保存的文字維持原內容。');self.show_value()
        except (ValueError,OSError) as exc:self.canvas.window.notice(str(exc))

    def move(self, action):
        item=self.item()
        if not item:return
        try:self.runner.store.edit_pending(item['id'],action);self.refresh()
        except ValueError as exc:self.canvas.window.notice(str(exc))

    def failure_menu(self):
        item=self.item()
        if not item or item['state']!='failed':
            self.canvas.window.notice('未確認的提交須先核對任務紀錄，不可重送。');return
        menu=RoundMenu(self.canvas.window)
        def change(state):
            self.runner.store.update(item['id'],state=state,operation=None,error='');self.refresh()
        menu.addAction('重試這項已確認失敗的輸入',lambda:change('waiting'))
        menu.addAction('略過這項',lambda:change('removed'))
        menu.open_for(self.failure_button,self.canvas.view)

    def add_channel(self):
        menu=RoundMenu(self.canvas.window)
        for kind in TYPES:
            def add(checked=False,kind=kind):
                def change(state):
                    channels=state['multi_output']['schedulers'][self.key]['channels'];number=1
                    while any(c['id']==kind+str(number) for c in channels):number+=1
                    channels.append(dict(id=kind+str(number),type=kind,name=TYPE_NAMES[kind]+str(number)))
                self.canvas.commit(change)
            menu.addAction(TYPE_NAMES[kind],add)
        menu.open_for(self.add_channel_button,self.canvas.view)

    def history(self):
        window=self.canvas.window
        dialog=StudioDialog(window);dialog.setWindowTitle('預排程完成紀錄');dialog.resize(720,580)
        listing=QListWidget();detail=QPlainTextEdit();detail.setReadOnly(True)
        records=[item for item in self.runner.store.rows(self.key,history=True) if item['state'] in ('complete','removed','retained')]
        for item in reversed(records):
            listing.addItem(('完成' if item['state']=='complete' else '舊版保留' if item['state']=='retained' else '移除')+' · '+item.get('label','')+' · '+item.get('prompt_id',''))
        records.reverse()
        def selected(index):
            if not 0<=index<len(records):return
            item=records[index];parts=['工作流：'+item['route']['workflow']]
            for value in item['inputs'].values():
                parts.append(TYPE_NAMES[value['type']]+'：'+(value['value'] if value['type']=='clip' else
                    value['value'].get('name','') if value['type']=='image' else value['value'].get('text','')))
            detail.setPlainText('\n\n'.join(parts))
        def results():
            index=listing.currentRow()
            if not 0<=index<len(records):return
            dialog.accept();show_job_images(window,records[index])
        listing.currentRowChanged.connect(selected);dialog.body.addWidget(listing,1);dialog.body.addWidget(detail,1)
        dialog.body.addLayout(row(button('查看這項圖片結果',results,'Quiet'),None,button('關閉',dialog.accept,'Quiet')))
        listing.setCurrentRow(0);dialog.exec();dialog.deleteLater()


class FlowCard(TextCard):
    def __init__(self,canvas,key,kind):
        self.kind=kind;super().__init__(canvas);self.key=key
        self.module_icon='clock' if kind=='schedulers' else 'workflow' if kind=='stages' else 'media'
        if canvas.data()['version']>=7 and kind in ('schedulers','stages'):
            from .stage_widgets import StagePanel,SchedulePanel as StageSchedule
            self.panel=StagePanel(canvas,key) if kind=='stages' else StageSchedule(canvas,key)
        else:self.panel=SchedulePanel(canvas,key) if kind=='schedulers' else ImageInputPanel(canvas,key)
        self.init_interaction()
        if self.kind=='stages':self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable,True)
    @property
    def default_size(self):return (500,550) if self.kind=='schedulers' else (360,245) if self.kind=='stages' else (360,160)
    def attach(self):
        if self.proxy.widget() is not self.panel:self.proxy.setWidget(self.panel)
        self.title=self.canvas.data()[self.kind][self.key]['name'];self.panel.setStyleSheet(self.canvas.window.styleSheet())
        self.panel.ensurePolished();self.restore_size();self.layout_card();self.panel.show();self.panel.refresh()
    def layout_card(self):
        top=82
        if self.kind=='stages':
            from .stage_model import controls
            dependencies=sum(c['kind']=='done' and c['destination']==self.key for c in self.canvas.data()['connections'])
            top=max(160,106+24*(len(controls(self.canvas.window.state,self.key))+dependencies+1))
        if self.kind=='schedulers':top=70+24*len(self.canvas.data()[self.kind][self.key]['channels'])
        if self.kind=='schedulers' and self.canvas.data()['version']>=7:top+=24
        w,h=self.requested_size;minimum=self.panel.minimumSizeHint()
        self.proxy.setGeometry(QRectF(14,top,max(290,w-28,minimum.width()),max(h-top-14,minimum.height())));self.sync_bounds()
    def update_text(self):self.panel.refresh();super().update_text()
    def release_control(self,event):
        if self.kind=='stages' and self.pressed=='size' and not self.resizing:
            self.pressed=None
            if self.action_at(event.pos())=='size':QTimer.singleShot(0,self.panel.edit)
            self.update();event.accept();return True
        return super().release_control(event)
    def hoverMoveEvent(self,event):
        super().hoverMoveEvent(event)
        if self.kind=='stages' and self.hot=='size':self.setToolTip('參數')
    def mouseDoubleClickEvent(self,event):
        if self.kind=='stages' and event.button()==Qt.MouseButton.LeftButton:
            QTimer.singleShot(0,self.panel.edit);event.accept();return
        super().mouseDoubleClickEvent(event)
    def keyPressEvent(self,event):
        if self.kind=='stages':
            if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
                self.canvas.delete_selected();event.accept();return
            if event.modifiers()&Qt.KeyboardModifier.ControlModifier:
                if event.key()==Qt.Key.Key_Z:self.canvas.undo();event.accept();return
                if event.key()==Qt.Key.Key_Y:self.canvas.redo();event.accept();return
        if self.kind=='stages' and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter):
            QTimer.singleShot(0,self.panel.edit);event.accept();return
        super().keyPressEvent(event)
    def paint(self,painter,option,widget=None):
        super().paint(painter,option,widget)
        if self.kind=='stages':
            value=self.canvas.data()['stages'][self.key]
            profile=next((p for p in self.canvas.window.state.get('generation',{}).get('profiles',[]) if p['id']==value.get('workflow')),None)
            from .canvas_items import canvas_colors
            colors=canvas_colors(self.canvas);left=18
            if not profile:
                painter.save();painter.setPen(Qt.PenStyle.NoPen);painter.setBrush(QColor(colors['selected']))
                badge=QRectF(left,37,62,24);painter.drawRoundedRect(badge,5,5)
                font=painter.font();font.setPointSizeF(10);font.setBold(True);painter.setFont(font);painter.setPen(QColor(colors['text']))
                painter.drawText(badge,Qt.AlignmentFlag.AlignCenter,'待設定');painter.restore();left+=72
            painter.setPen(QColor(colors['secondary'] if profile else colors['text']))
            caption=painter.fontMetrics().elidedText(profile['name'] if profile else '尚未選擇工作流',Qt.TextElideMode.ElideRight,int(self.width-left-18))
            painter.drawText(QRectF(left,38,self.width-left-18,23),Qt.AlignmentFlag.AlignVCenter,caption)
    def contextMenuEvent(self,event):
        menu=RoundMenu(self.canvas.window)
        if self.kind=='stages':menu.addAction('參數',self.panel.edit)
        menu.addAction('重新命名',lambda:self.canvas.rename_function(self.kind,self.key))
        menu.addAction('移除模塊（保留並暫停等待項目）',lambda:self.canvas.remove_flow_node(self.kind,self.key))
        menu.open_at(event.screenPos());event.accept()
