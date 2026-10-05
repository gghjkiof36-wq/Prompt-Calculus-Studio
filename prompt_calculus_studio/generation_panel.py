"""Shared generation controls beside the final Prompt, in list and Canvas."""
import copy
import hashlib
import json
import uuid
from pathlib import Path
from .error_dialog import import_error
from PySide6.QtCore import Qt, QSize, QTimer, Signal
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QGridLayout,QLineEdit,
    QDoubleSpinBox,QSpinBox,QPlainTextEdit,QListWidget,QListWidgetItem,QFileDialog,QDialog,QLayout)
from .widgets import label,button,row,ComboBox,StudioDialog,scrolling,dialog_buttons,ElidedLabel,RoundMenu
from .image_drop import ImageDropLabel
from .generation import (options,active_profile,api_graph,text_fields,suggested_text,parameter_bindings,
    validate_profile,PARAMETERS,LIMITS)
from .pnginfo import png_metadata
from .snapshots import image_snapshots
from .local_files import choose_file
from .workflow_import import import_graph,infer_profile


def node_title(graph,key):
    node=graph[key]; return f"{node.get('_meta',{}).get('title',node['class_type'])} · #{key}"


class WorkflowDialog(StudioDialog):
    def __init__(self,window,graph,mode,profile=None):
        super().__init__(window); self.window=window; self.graph=graph; self.profile=copy.deepcopy(profile or {})
        self.mode=mode; self.setWindowTitle('工作流對應'); self.resize(660,550)
        self.body.addWidget(label('確認文字與圖片欄位。','Subtle'))
        self.name=QLineEdit(self.profile.get('name','圖生圖工作流' if mode=='img2img' else '文生圖工作流'))
        form=QFormLayout(); self.body.addLayout(form); form.addRow('名稱',self.name)
        self.kind=ComboBox(); self.kind.addItem('文生圖','txt2img'); self.kind.addItem('圖生圖','img2img'); self.kind.setCurrentIndex(self.kind.findData(mode))
        form.addRow('生成方式',self.kind)
        if 'multi_output' in window.state: self.kind.hide(); form.labelForField(self.kind).hide()
        self.sampler=ComboBox(); self.sampler.addItem('沿用工作流，不另外調整','')
        self.size_node=ComboBox(); self.size_node.addItem('沿用來源圖／工作流尺寸','')
        self.image=ComboBox(); self.image.addItem('沿用工作流圖片','')
        for ident,node in graph.items():
            kind=node['class_type']
            if kind=='KSampler': self.sampler.addItem(node_title(graph,ident),ident)
            if kind in ('ImageScale','EmptyLatentImage','EmptySD3LatentImage'): self.size_node.addItem(node_title(graph,ident),ident)
            if kind=='LoadImage' and isinstance(node['inputs'].get('image'),str): self.image.addItem(node_title(graph,ident),ident)
        self.prompt=ComboBox(); self.prompt.addItem('請選擇文字欄位',None)
        for ident,field in text_fields(graph): self.prompt.addItem(node_title(graph,ident)+' / '+field,[ident,field])
        for widget,key in ((self.sampler,'sampler'),(self.image,'image'),(self.size_node,'size')):
            found=widget.findData(self.profile.get(key,''))
            widget.setCurrentIndex(found if profile and found>=0 else 1 if widget.count()==2 else 0)
        suggested=self.profile.get('prompt') or suggested_text(graph,self.sampler.currentData())
        if suggested:
            index=self.prompt.findData(list(suggested)); self.prompt.setCurrentIndex(max(0,index))
        form.addRow('最終 Prompt',self.prompt)
        if 'multi_output' in window.state:
            self.prompt.hide(); form.labelForField(self.prompt).hide()
            form.addRow(label('文字欄由各最終 Prompt 的綁定按鈕指定。','Subtle',True))
        form.addRow('來源圖片',self.image)
        form.addRow('採樣設定',self.sampler); form.addRow('尺寸設定',self.size_node)
        self.body.addWidget(label('未指定的參數沿用工作流。','Subtle'))
        self.error=label('','ConflictNotice',True); self.body.addWidget(self.error); self.error.hide()
        self.body.addStretch(); self.body.addWidget(dialog_buttons(self,self.save))

    def save(self):
        result=dict(id=self.profile.get('id',uuid.uuid4().hex),name=self.name.text().strip(),mode=self.kind.currentData(),graph=copy.deepcopy(self.graph),
                    prompt=self.prompt.currentData(),image=self.image.currentData(),sampler=self.sampler.currentData(),size=self.size_node.currentData(),
                    values={},seed_mode=self.profile.get('seed_mode','fixed'))
        if 'multi_output' in self.window.state: result['multi_text']=True
        result['values']={key:self.profile.get('values',{}).get(key,self.graph[node]['inputs'][field]) for key,(node,field) in parameter_bindings(result).items()}
        try: validate_profile(result)
        except ValueError as exc: self.error.setText(str(exc)); self.error.show(); return
        self.result_profile=result; self.accept()


class ParametersDialog(StudioDialog):
    def __init__(self,window,profile):
        super().__init__(window); self.profile=profile; self.window=window; self.setWindowTitle('本次生成參數'); self.resize(560,630)
        self.body.addWidget(label('這些值會寫入選定工作流；工作區的建議值不會覆蓋它們。','Subtle',True))
        content=QWidget(); form=QFormLayout(content); self.fields={}
        for key,(node,field) in parameter_bindings(profile).items():
            value=profile.get('values',{}).get(key,profile['graph'][node]['inputs'][field])
            if key=='seed': widget=QLineEdit(str(value))
            elif key in ('sampler_name','scheduler'):
                widget=ComboBox(); widget.setEditable(True); widget.addItem(str(value)); self.load_choices(node,key,widget)
            elif key in ('steps','width','height'):
                widget=QSpinBox(); widget.setRange(*LIMITS[key]); widget.setValue(int(value))
            else:
                widget=QDoubleSpinBox(); widget.setRange(*LIMITS[key]); widget.setDecimals(3); widget.setSingleStep(.05 if key=='denoise' else .1); widget.setValue(float(value))
            self.fields[key]=widget; form.addRow(PARAMETERS[key],widget)
        self.seed_mode=ComboBox()
        for name,value in (('固定','fixed'),('每次遞增','increment'),('每次遞減','decrement'),('每次隨機','random')): self.seed_mode.addItem(name,value)
        self.seed_mode.setCurrentIndex(max(0,self.seed_mode.findData(profile.get('seed_mode','fixed'))))
        if 'seed' in self.fields: form.addRow('Seed 方式',self.seed_mode)
        if not self.fields: form.addRow(label('這份工作流沒有指定可調整的參數，會使用工作流原值。','Subtle',True))
        self.body.addWidget(scrolling(content),1); self.error=label('','ConflictNotice',True); self.body.addWidget(self.error); self.error.hide()
        self.body.addWidget(dialog_buttons(self,self.save))

    def load_choices(self,node,key,widget):
        from urllib.parse import quote
        client=getattr(self.window,'comfy',None)
        if not client or not client.connected: return
        kind=self.profile['graph'][node]['class_type']
        def done(result):
            if not self.isVisible(): return
            spec=result.get(kind,{}).get('input',{}).get('required',{}).get(key,[])
            if spec and isinstance(spec[0],list):
                current=widget.currentText(); widget.clear(); widget.addItems([str(v) for v in spec[0]]); widget.setCurrentText(current)
        client.request('/object_info/'+quote(kind,safe=''),done=done,failed=lambda _:None)

    def save(self):
        result=copy.deepcopy(self.profile); values={}
        try:
            for key,widget in self.fields.items():
                values[key]=int(widget.text()) if key=='seed' else widget.currentText() if key in ('sampler_name','scheduler') else widget.value()
            result.update(values=values,seed_mode=self.seed_mode.currentData()); validate_profile(result)
        except ValueError as exc: self.error.setText(str(exc)); self.error.show(); return
        self.result_profile=result; self.accept()


class GenerationPanel(QWidget):
    importFeedback=Signal(str)
    def __init__(self,window):
        super().__init__(); self.window=window; self.updating=False; self.preview_source=None
        layout=QVBoxLayout(self); layout.setContentsMargins(0,4,0,0); layout.setSpacing(5)
        layout.setSizeConstraint(QLayout.SizeConstraint.SetMinimumSize)
        self.mode=ComboBox(); self.mode.addItem('文生圖','txt2img'); self.mode.addItem('圖生圖','img2img'); self.mode.currentIndexChanged.connect(self.mode_changed)
        self.parameters=button('參數…',self.edit_parameters,'Quiet')
        self.parameters.setParent(self)
        layout.addLayout(row(self.mode,None))
        if 'multi_output' in window.state: self.mode.hide()
        self.workflow=ComboBox(self); self.workflow.setMinimumWidth(100); self.workflow.currentIndexChanged.connect(self.workflow_changed)
        self.mapping=button('對應…',self.edit_mapping,'Quiet')
        self.mapping.setParent(self)
        self.workflow.hide(); self.mapping.hide()
        self.source_box=QWidget(); source_layout=QHBoxLayout(self.source_box); source_layout.setContentsMargins(0,0,0,0)
        self.preview=ImageDropLabel(window.store); self.preview.setFixedSize(120,104); self.preview.setWordWrap(True)
        self.preview.pathReady.connect(self.set_source); self.preview.failed.connect(lambda message:import_error(window,message)); source_layout.addWidget(self.preview)
        controls=QVBoxLayout(); source_layout.addLayout(controls,1)
        self.source_name=ElidedLabel('來源圖片'); self.source_name.setMinimumWidth(0); controls.addWidget(self.source_name)
        controls.addLayout(row(button('選擇圖片 ▾',self.source_menu,'Quiet'),None))
        self.load_prompt=button('載入圖片提示詞／組合…',self.load_image_prompt,'Quiet'); controls.addWidget(self.load_prompt)
        layout.addWidget(self.source_box)
        self.status=label('','Subtle',True); layout.addWidget(self.status); self.status.hide()
        self.refresh()

    def settings(self):
        return self.window.state.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))

    def refresh(self):
        self.updating=True; settings=options(self.window.state); mode=settings['mode']; profile=active_profile(self.window.state)
        self.mode.setCurrentIndex(self.mode.findData(mode)); self.workflow.clear()
        self.workflow.addItem('目前 ComfyUI 網頁工作流' if mode=='txt2img' else '請匯入圖生圖工作流','')
        for p in settings['profiles']:
            if p['mode']==mode: self.workflow.addItem(p['name'],p['id'])
        self.workflow.setCurrentIndex(max(0,self.workflow.findData(settings.get('chosen',{}).get(mode,''))))
        self.parameters.setVisible(profile is not None); self.mapping.setVisible(profile is not None)
        self.source_box.setVisible(mode=='img2img' and self.window.state.get('selection_view')!='canvas'); source=settings.get('source'); self.load_prompt.setEnabled(bool(source))
        if source:
            if self.preview_source!=source['relative']:
                reader=QImageReader(str(self.window.store.directory/source['relative'])); reader.setAutoTransform(True)
                size=reader.size(); size.scale(118,102,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size)
                image=reader.read()
                if not image.isNull(): self.preview.setPixmap(QPixmap.fromImage(image))
                else: self.preview.setText('來源圖片無法讀取')
                self.preview_source=source['relative']
            self.source_name.setText(f"{source['name']} · {source['width']}×{source['height']}"); self.source_name.setToolTip(source['name'])
        else: self.preview.setText(''); self.source_name.setText('尚未選擇圖片，可拖入'); self.preview_source=None
        self.preview.setVisible(bool(source))
        hint=''
        if profile:
            fields=parameter_bindings(profile); values=profile.get('values',{})
            if 'denoise' in fields: hint=f"Denoise {values.get('denoise',profile['graph'][fields['denoise'][0]]['inputs']['denoise'])}"
        elif mode=='img2img': hint='請到設定選擇圖生圖工作流'
        # The compact settings page exposes this summary on its existing control.
        # A parentless QLabel shown here would become an independent OS window.
        self.parameters.setToolTip(hint); self.updating=False

    def changed(self):
        self.window.changed(); self.refresh()
        self.window.changed('generation')

    def mode_changed(self):
        if not self.updating:
            self.settings()['mode']=self.mode.currentData(); self.changed(); self.reveal_controls()
            canvas=getattr(self.window,'canvas',None)
            if canvas and self.window.state.get('selection_view')=='canvas': canvas.functions.mode_changed()

    def reveal_controls(self):
        def reveal():
            if self.window.state.get('selection_view')!='canvas':
                self.window.builder_scroll.ensureWidgetVisible(self.window.run_controls,0,12)
        QTimer.singleShot(0,reveal)

    def workflow_changed(self):
        if not self.updating:
            self.settings().setdefault('chosen',{})[self.mode.currentData()]=self.workflow.currentData(); self.changed()

    def save_profile(self,profile):
        from .generation import store_profile
        store_profile(self.window.state,profile); self.changed()
        if self.window.state.get('selection_view')=='canvas' and self.window.state.get('multi_output',{}).get('version',1)<4:
            self.window.canvas.functions.mode_changed()

    def import_workflow(self,path=None):
        if not isinstance(path,(str,Path)): path=choose_file(self.window,'匯入工作流','工作流 (*.json)')
        if not path: return
        try:
            file=Path(path)
            if file.stat().st_size>2*1024*1024: raise ValueError('工作流超過 2 MB。')
            incoming=json.loads(file.read_text(encoding='utf-8-sig'))
            transfer=incoming if isinstance(incoming,dict) and incoming.get('format')=='prompt_studio_workflow' else None
            graph=import_graph(transfer['graph'] if transfer else incoming)
            profile=infer_profile(graph,file.stem,incoming)
            if 'multi_output' in self.window.state: profile['multi_text']=True
            if transfer:
                from .workflow_transfer import import_transfer
                profile,bindings=import_transfer(self.window.state,transfer)
            try: validate_profile(profile)
            except ValueError:
                dialog=WorkflowDialog(self.window,graph,profile['mode'],profile)
                if not dialog.exec(): return
                profile=dialog.result_profile
            if transfer:
                from .workflow_transfer import apply_transfer
                self.window.state,profile=apply_transfer(self.window.state,transfer)
                self.changed()
            else: self.save_profile(profile)
            self.importFeedback.emit('已匯入'+('圖生圖' if profile['mode']=='img2img' else '文生圖')+'：'+profile['name'])
            return profile
        except (ValueError,OSError,KeyError,TypeError) as exc:
            self.importFeedback.emit(''); import_error(self.window,exc)

    def edit_mapping(self):
        profile=active_profile(self.window.state)
        if profile:
            dialog=WorkflowDialog(self.window,profile['graph'],profile['mode'],profile)
            if dialog.exec(): self.save_profile(dialog.result_profile)

    def edit_parameters(self):
        profile=active_profile(self.window.state)
        if profile:
            dialog=ParametersDialog(self.window,profile)
            if dialog.exec(): self.save_profile(dialog.result_profile)

    def choose_source(self):
        path=choose_file(self.window,'選擇圖片','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path: self.set_source(path)

    def source_menu(self):
        menu=RoundMenu(self.window); menu.addAction('選擇圖片…',self.choose_source)
        menu.addAction('媒體庫',lambda:self.choose_catalog('image')); menu.addAction('最近生成',lambda:self.choose_catalog('recent'))
        menu.open_at(self.cursor().pos())

    def import_source(self,path):
        from .image_bindings import import_source
        return import_source(path,self.window.store.directory)

    def set_source(self,path,output=None):
        try:
            source=self.import_source(path)
            multi='multi_output' in self.window.state
            if not multi: self.settings().update(mode='img2img',source=source)
            canvas=getattr(self.window,'canvas',None)
            if canvas:
                if multi: canvas.functions.use_source(source,output)
                else: canvas.functions.use_source(source)
            self.changed(); self.reveal_controls(); self.window.notice('已更換來源圖片'); return True
        except (ValueError,OSError) as exc: import_error(self.window,exc); return False

    def choose_catalog(self,kind,accept=None):
        dialog=StudioDialog(self.window); dialog.setWindowTitle('從最近生成選圖' if kind=='recent' else '從媒體庫選圖'); dialog.resize(640,620)
        search=QLineEdit(); search.setPlaceholderText('搜尋檔名'); dialog.body.addWidget(search)
        images=QListWidget(); images.setIconSize(QSize(96,96)); dialog.body.addWidget(images,1)
        from .widgets import record_icon
        def refresh():
            images.clear()
            for record in self.window.catalog.rows(kind,limit=120,search=search.text()):
                item=QListWidgetItem(record['name']); item.setIcon(record_icon(self.window.store,record,96)); item.setData(Qt.ItemDataRole.UserRole,record['path']); images.addItem(item)
                if not Path(record['path']).is_file():
                    item.setText(record['name']+' · 原圖遺失'); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled)
        search.textChanged.connect(refresh); refresh()
        dialog.body.addWidget(label('顯示最多 120 張，可輸入檔名縮小範圍。','Subtle'))
        def choose():
            item=images.currentItem()
            if item and (accept or self.set_source)(item.data(Qt.ItemDataRole.UserRole)): dialog.accept()
        images.itemDoubleClicked.connect(lambda _:choose()); dialog.body.addLayout(row(None,button('使用這張圖片',choose),button('取消',dialog.reject)))
        dialog.exec()

    def show_source(self):
        source=options(self.window.state).get('source')
        if not source: return
        dialog=StudioDialog(self.window); dialog.setWindowTitle('來源圖片 · '+source['name']); dialog.resize(900,760)
        image=label(''); image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reader=QImageReader(str(self.window.store.directory/source['relative'])); reader.setAutoTransform(True)
        size=reader.size(); size.scale(820,620,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size)
        image.setPixmap(QPixmap.fromImage(reader.read()))
        dialog.body.addWidget(scrolling(image),1); dialog.body.addWidget(dialog_buttons(dialog)); dialog.exec()

    def load_image_prompt(self,source=None):
        if not isinstance(source,dict): source=options(self.window.state).get('source')
        if not source: return
        try: meta=png_metadata(self.window.store.directory/source['relative']); bindings=image_snapshots(meta)
        except (OSError,ValueError) as exc: self.window.notice('無法讀取來源圖片內容：'+str(exc)); return
        choices=[]
        for binding in bindings: choices.append(('模組快照 · '+str(binding.get('node_id','')),binding['snapshot']['final_prompt'],binding))
        for node in meta.get('nodes',[]):
            text=node.get('values',{}).get('text')
            if isinstance(text,str): choices.append(('文字節點 '+str(node.get('node','')),text,None))
        if not choices: self.window.notice('這張圖片沒有可讀取的 Prompt 或模組快照。'); return
        dialog=StudioDialog(self.window); dialog.setWindowTitle('載入圖片內容'); dialog.resize(720,580)
        picker=ComboBox(); picker.addItems([v[0] for v in choices]); dialog.body.addWidget(picker)
        preview=QPlainTextEdit(); preview.setReadOnly(True); dialog.body.addWidget(preview,1)
        restore=button('載入模組組合',lambda:None)
        def update(): preview.setPlainText(choices[picker.currentIndex()][1]); restore.setEnabled(choices[picker.currentIndex()][2] is not None)
        picker.currentIndexChanged.connect(update); update()
        def load_text(): self.window.final.setPlainText(choices[picker.currentIndex()][1]); dialog.accept()
        def load_combination():
            binding=choices[picker.currentIndex()][2]
            if binding: dialog.accept(); self.window.gallery.restore_combination({'metadata':{'raw':{'prompt_studio':{'schema_version':1,'bindings':[binding]}}}})
        restore.clicked.connect(load_combination)
        dialog.body.addWidget(label('只有按下載入才會替換目前文字或組合。','Subtle',True))
        dialog.body.addLayout(row(button('載入為手動提示詞',load_text),restore,None,button('取消',dialog.reject))); dialog.exec()

    def history(self):
        from .job_details import workflow_name
        dialog=StudioDialog(self.window); dialog.setWindowTitle('生成任務紀錄'); dialog.resize(760,600)
        listing=QListWidget(); detail=QPlainTextEdit(); detail.setReadOnly(True)
        records=self.window.comfy.generation.records()
        names={'submitting':'提交中','queued':'佇列中','running':'生成中','complete':'完成','failed':'失敗','unconfirmed':'未確認'}
        for record in records:
            listing.addItem(names.get(record['state'],record['state'])+' · '+workflow_name(record)+' · '+record['id'][:8])
        def selected(index):
            if index<0 or index>=len(records):return
            from .job_details import describe
            record=self.window.comfy.generation.record(records[index]['id']) or records[index]
            listing.item(index).setText(names.get(record['state'],record['state'])+' · '+workflow_name(record)+' · '+record.get('prompt_id',record['id'])[:8])
            text=describe(record)
            if detail.toPlainText()!=text:
                cursor=detail.textCursor(); position=detail.verticalScrollBar().value()
                detail.setPlainText(text); detail.setTextCursor(cursor); detail.verticalScrollBar().setValue(position)
        def reload():
            nonlocal records
            index=listing.currentRow(); previous=records[index]['id'] if 0<=index<len(records) else None
            records=self.window.comfy.generation.records(); listing.blockSignals(True); listing.clear()
            for record in records:
                listing.addItem(names.get(record['state'],record['state'])+' · '+workflow_name(record)+' · '+record.get('prompt_id',record['id'])[:8])
            listing.setCurrentRow(next((i for i,r in enumerate(records) if r['id']==previous),0)); listing.blockSignals(False); selected(listing.currentRow())
        timer=QTimer(dialog); timer.setInterval(2000); timer.timeout.connect(reload); dialog.finished.connect(timer.stop); timer.start()
        listing.currentRowChanged.connect(selected); dialog.body.addWidget(listing,1); dialog.body.addWidget(detail,2)
        from .flow_widgets import retained_records
        def recovery(method):
            index=listing.currentRow()
            if 0<=index<len(records):getattr(self.window.comfy.generation,method)(records[index]['id'])
        dialog.body.addLayout(row(button('更新這筆紀錄',lambda:recovery('recheck'),'Quiet'),
                                  button('停止追蹤此筆（保留紀錄）',lambda:recovery('abandon'),'Quiet')))
        from .stage_widgets import show_history as chain_history
        dialog.body.addLayout(row(button('串接流程',lambda:chain_history(self.window),'Quiet'),button('保留的預排程／舊紀錄',lambda:retained_records(self.window),'Quiet'),None,button('關閉',dialog.accept,'Quiet')))
        listing.setCurrentRow(0); dialog.exec(); dialog.deleteLater()
