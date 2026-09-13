"""Saved workflows and explicit desktop-output bindings in one settings page."""
import copy,json
from PySide6.QtCore import Qt,QTimer,QSize
from PySide6.QtWidgets import QWidget,QVBoxLayout,QListWidget,QListWidgetItem,QFormLayout,QFileDialog,QLineEdit,QCheckBox,QDialog
from .widgets import label,button,row,ComboBox,InputDialog,StudioDialog
from .generation import active_profile,text_fields,validate_profile
from . import multi_output as model
from .workflow_catalog import WorkflowCatalog

class WorkflowManager(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.signature=None; self.deleted=None; self.updating=False; self.loading=False; self.connection_epoch=window.comfy.epoch
        self.catalog=WorkflowCatalog(window.comfy); self.catalog.changed.connect(self.rebuild)
        window.store.db.execute('CREATE TABLE IF NOT EXISTS workflow_deletions (id INTEGER PRIMARY KEY, body TEXT NOT NULL)'); window.store.db.commit()
        saved=window.store.db.execute('SELECT body FROM workflow_deletions ORDER BY id DESC LIMIT 1').fetchone()
        if saved: self.deleted=json.loads(saved[0])
        body=QVBoxLayout(self); body.setContentsMargins(0,0,0,0); body.setSpacing(12)
        self.query=QLineEdit(); self.query.setPlaceholderText('搜尋工作流名稱或資料夾'); self.query.setClearButtonEnabled(True)
        self.source=ComboBox(); self.source.addItem('全部來源','all'); self.source.addItem('ComfyUI 已儲存','comfy'); self.source.addItem('桌面已連結','desktop')
        self.folder=ComboBox(); self.folder.addItem('全部資料夾',''); self.folder.setMinimumWidth(130); self.folder.setMaximumWidth(220)
        body.addLayout(row(self.query,self.source)); body.addLayout(row(self.folder,button('重新整理',self.reload,'Quiet'),None,button('匯入檔案…',window.generation_panel.import_workflow,'Quiet')))
        self.catalog_status=label('','Subtle',True); body.addWidget(self.catalog_status); self.catalog_status.hide()
        self.list=QListWidget(); self.list.setTextElideMode(Qt.TextElideMode.ElideRight); self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet('QListWidget::item { padding:7px 12px; margin:1px 0; border-radius:7px; }')
        self.list.currentItemChanged.connect(self.details); body.addWidget(self.list)
        self.query.textChanged.connect(self.rebuild); self.source.currentIndexChanged.connect(self.rebuild); self.folder.currentIndexChanged.connect(self.rebuild)
        self.activate_button=button('設為活動工作流',self.activate,'Primary')
        self.rename_button=button('重新命名',self.rename,'Quiet'); self.reimport_button=button('重新匯入…',self.reimport,'Quiet')
        self.delete_button=button('刪除',self.remove,'DeleteWorkflow'); self.undo_button=button('復原刪除',self.undo_remove,'Quiet'); self.undo_button.setEnabled(bool(self.deleted))
        body.addLayout(row(self.activate_button,None,self.undo_button,self.delete_button))
        body.addLayout(row(self.rename_button,self.reimport_button,None))
        self.info=label('','Subtle',True); body.addWidget(self.info)
        self.form=QFormLayout(); self.form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); body.addLayout(self.form); self.pickers={}
        window.generation_panel.importFeedback.connect(self.refresh)
        window.comfy.stateChanged.connect(self.refresh); self.refresh()
    def entry(self):
        item=self.list.currentItem(); return self.entries.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
    def selected(self):
        entry=self.entry(); key=entry['id'] if entry else None
        return next((p for p in self.window.state.get('generation',{}).get('profiles',[]) if p['id']==key),None)
    def reload(self): self.signature=None; self.refresh(); self.catalog.refresh()
    def showEvent(self,event):
        super().showEvent(event)
        if not self.catalog.loaded: self.catalog.refresh()
    def rebuild(self,*_): self.signature=None; self.refresh()
    def refresh(self,*_):
        if self.connection_epoch!=self.window.comfy.epoch:
            self.connection_epoch=self.window.comfy.epoch; self.loading=False; self.signature=None
        state=self.window.state; data=state.get('multi_output',{}); generation=state.get('generation',{})
        signature=json.dumps([generation,data.get('bindings'),[(k,v['name'],v['canvas']) for k,v in data.get('outputs',{}).items()]],sort_keys=True)
        if signature==self.signature: return
        self.signature=signature; prior=self.list.currentItem(); current=active_profile(state); chosen=prior.data(Qt.ItemDataRole.UserRole) if prior else current['id'] if current else None
        folder=self.folder.currentData(); self.folder.blockSignals(True); self.folder.clear(); self.folder.addItem('全部資料夾','')
        from pathlib import PurePosixPath
        folders=sorted({str(PurePosixPath(p).parent) for p in self.catalog.files},key=str.casefold)
        for path in folders: self.folder.addItem('根目錄' if path=='.' else path,path)
        self.folder.setCurrentIndex(max(0,self.folder.findData(folder))); self.folder.blockSignals(False)
        self.folder.setVisible(bool(self.catalog.files) and self.source.currentData()!='desktop')
        entries={}
        for p in generation.get('profiles',[]): entries[p['id']]=dict(id=p['id'],name=p['name'],path='',kind='desktop')
        for path in self.catalog.files:
            profile=next((p for p in generation.get('profiles',[]) if p.get('origin')==dict(server=self.catalog.server,path=path)),None)
            ident=profile['id'] if profile else self.catalog.identity(path)
            entries['comfy:'+path]=dict(id=ident,name=profile['name'] if profile else PurePosixPath(path).stem,path=path,kind='comfy')
            if profile and self.source.currentData()!='desktop': entries.pop(profile['id'],None)
        self.entries=entries
        self.updating=True; self.list.clear()
        query=self.query.text().strip().casefold(); folder=self.folder.currentData(); source=self.source.currentData()
        for key,p in sorted(entries.items(),key=lambda pair: (not(current and pair[1]['id']==current['id']),pair[1]['name'].casefold())):
            if source!='all' and p['kind']!=source: continue
            if query not in (p['name']+' '+p['path']).casefold(): continue
            if folder and p['kind']=='comfy' and str(PurePosixPath(p['path']).parent)!=folder: continue
            origin='桌面' if p['kind']=='desktop' else 'ComfyUI'+(' / '+str(PurePosixPath(p['path']).parent) if '/' in p['path'] else '')
            item=QListWidgetItem(('● ' if current and p['id']==current['id'] else '')+p['name']+'   ·   '+origin)
            item.setData(Qt.ItemDataRole.UserRole,key); item.setToolTip(p['path'] or p['name']); self.list.addItem(item)
            if key==chosen or (chosen==p['id'] and not self.list.currentItem()): self.list.setCurrentItem(item)
        if not self.list.currentItem() and self.list.count(): self.list.setCurrentRow(0)
        self.update_list_height()
        status=self.catalog.message or ('' if self.list.count() else '沒有符合的工作流')
        self.catalog_status.setText(status); self.catalog_status.setVisible(bool(status))
        self.updating=False; self.details()
    def update_list_height(self):
        self.list.ensurePolished(); self.list.doItemsLayout()
        height=max(32,self.list.sizeHintForRow(0))
        self.list.setFixedHeight(min(7,max(2,self.list.count()))*height+8)
    def details(self,*_):
        if self.updating: return
        while self.form.rowCount(): self.form.removeRow(0)
        self.pickers={}; profile=self.selected(); entry=self.entry()
        for widget in (self.activate_button,self.delete_button): widget.setEnabled(entry is not None and not self.loading)
        for widget in (self.rename_button,self.reimport_button): widget.setEnabled(profile is not None and not self.loading)
        self.reimport_button.setText('重新讀取' if entry and entry['kind']=='comfy' else '重新匯入…')
        if not profile: self.info.setText(entry['path'] if entry else ''); return
        current=active_profile(self.window.state); self.activate_button.setEnabled(not current or current['id']!=profile['id'])
        self.info.setText(profile['name']+' · '+str(len(profile['graph']))+' 個節點')
        data=self.window.state.get('multi_output')
        if not data: return
        fields=text_fields(profile['graph'])
        for oid,output in data['outputs'].items():
            picker=ComboBox(); picker.setMinimumWidth(230); picker.addItem('未綁定',None)
            binding=next((b for b in data['bindings'] if b['workflow']==profile['id'] and b['output']==oid),None)
            for node,field in fields:
                entry=profile['graph'][node]; title=entry.get('_meta',{}).get('title',entry['class_type'])
                occupied=next((b for b in data['bindings'] if b['workflow']==profile['id'] and b['node']==node and b['field']==field and b['output']!=oid),None)
                summary=entry['inputs'][field].replace('\n',' ')[:40]
                text=title+' · #'+node+' / '+field+' · '+summary
                if occupied: text+='（'+data['outputs'].get(occupied['output'],{}).get('name','其他輸出')+' 已使用）'
                picker.addItem(text,(node,field)); picker.model().item(picker.count()-1).setEnabled(not occupied)
            if binding:
                target=(binding['node'],binding['field']); index=picker.findData(target)
                if index<0: picker.addItem('綁定失效 · #'+target[0]+' / '+target[1],target); index=picker.count()-1
                picker.setCurrentIndex(index)
            picker.currentIndexChanged.connect(lambda _,o=oid,p=picker,w=profile['id']:self.bind_output(w,o,p.currentData()))
            self.pickers[oid]=picker; self.form.addRow(output['name'],picker)
    def bind_output(self,workflow,output,target):
        self.window.canvas.commit(lambda state:model.set_binding(state,workflow,output,target)); QTimer.singleShot(0,self.refresh)
    def activate(self):
        entry=self.entry(); profile=self.selected()
        if entry and entry['kind']=='comfy': self.load_remote(entry)
        elif profile: self.window.generation_panel.save_profile(profile); self.refresh()
    def load_remote(self,entry):
        self.loading=True; self.details(); server=self.catalog.server
        def failed(error): self.loading=False; self.details(); self.window.notice('無法連結這份工作流：'+str(error))
        def received(value):
            try:
                from .workflow_import import import_graph,infer_profile
                from .generation_panel import WorkflowDialog
                from .workflow_transfer import apply_transfer
                if value.get('format')=='prompt_studio_workflow':
                    state,profile=apply_transfer(self.window.state,value); self.window.state=state
                else:
                    profile=infer_profile(import_graph(value),entry['name'],value); profile.update(id=entry['id'],multi_text=True)
                    try: validate_profile(profile)
                    except ValueError:
                        dialog=WorkflowDialog(self.window,profile['graph'],profile['mode'],profile)
                        if dialog.exec()!=QDialog.DialogCode.Accepted: self.loading=False; self.details(); return
                        profile=dialog.result_profile
                profile['origin']=dict(server=server,path=entry['path'])
                self.window.generation_panel.save_profile(profile); self.loading=False; self.rebuild()
            except (ValueError,KeyError,TypeError) as exc: failed(exc)
        self.catalog.read(entry['path'],received,failed)
    def rename(self):
        profile=self.selected()
        if not profile: return
        name,ok=InputDialog.getText(self.window,'工作流名稱','名稱',text=profile['name'])
        if ok and name.strip(): profile['name']=name.strip(); self.window.generation_panel.changed(); self.refresh()
    def reimport(self):
        profile=self.selected()
        if not profile: return
        entry=self.entry()
        if entry and entry['kind']=='comfy': self.load_remote(entry); return
        path,_=QFileDialog.getOpenFileName(self.window,'重新匯入「'+profile['name']+'」','','工作流 (*.json)')
        if not path: return
        try:
            from pathlib import Path
            from .workflow_import import import_graph,infer_profile
            from .workflow_transfer import apply_transfer
            incoming=json.loads(Path(path).read_text(encoding='utf-8-sig'))
            if incoming.get('format')=='prompt_studio_workflow':
                if incoming.get('id')!=profile['id']: raise ValueError('此檔案屬於另一份工作流，請使用「匯入工作流」。')
                state,_=apply_transfer(self.window.state,incoming); self.window.state=state
            else:
                updated=infer_profile(import_graph(incoming),profile['name']); updated.update(id=profile['id'],multi_text=True); validate_profile(updated)
                self.window.generation_panel.save_profile(updated)
            self.window.generation_panel.changed(); self.window.canvas.refresh(); self.refresh()
        except (ValueError,KeyError,TypeError,OSError) as exc: self.window.notice('工作流未更新：'+str(exc))
    def confirm_delete(self,entry):
        settings=self.window.state['settings']
        if settings.get('skip_workflow_delete_confirmation',False): return True
        dialog=StudioDialog(self.window); dialog.setWindowTitle('刪除工作流'); dialog.resize(480,260)
        text='將「'+entry['name']+'」'+('從 ComfyUI 移入回收區，並解除桌面綁定。' if entry['kind']=='comfy' else '從桌面移除，並解除其文字綁定。')
        dialog.body.addWidget(label(text+'\n可使用「復原刪除」恢復。',wrap=True))
        skip=QCheckBox('不再提醒'); dialog.body.addWidget(skip)
        dialog.body.addLayout(row(None,button('取消',dialog.reject,'Quiet'),button('刪除',dialog.accept,'DeleteWorkflow')))
        if dialog.exec()!=QDialog.DialogCode.Accepted: return False
        if skip.isChecked(): settings['skip_workflow_delete_confirmation']=True; self.window.changed()
        return True
    def remove(self):
        entry=self.entry()
        if entry is None or self.loading or not self.confirm_delete(entry): return
        if entry['kind']=='comfy':
            self.loading=True; self.details()
            self.catalog.trash(entry['path'],lambda remote:self.finish_remove(entry['id'],remote),self.operation_failed)
        else: self.finish_remove(entry['id'])
    def operation_failed(self,error): self.loading=False; self.details(); self.window.notice(str(error))
    def finish_remove(self,key,remote=None):
        state=self.window.state; settings=self.window.generation_panel.settings(); bindings=state.get('multi_output',{}).get('bindings',[])
        profile=next((p for p in settings['profiles'] if p['id']==key),None)
        self.deleted=dict(profile=copy.deepcopy(profile),bindings=[copy.deepcopy(b) for b in bindings if b['workflow']==key],chosen={k:v for k,v in settings['chosen'].items() if v==key},remote=remote)
        db=self.window.store.db; db.execute('INSERT INTO workflow_deletions(body) VALUES (?)',(json.dumps(self.deleted,ensure_ascii=False),)); db.commit()
        settings['profiles']=[p for p in settings['profiles'] if p['id']!=key]; settings['chosen']={k:v for k,v in settings['chosen'].items() if v!=key}
        if 'multi_output' in state: state['multi_output']['bindings']=[b for b in bindings if b['workflow']!=key]
        self.loading=False; self.undo_button.setEnabled(True); self.window.generation_panel.changed(); self.rebuild()
    def undo_remove(self):
        if not self.deleted: return
        if self.loading: return
        profile=self.deleted['profile']; state=self.window.state
        if profile and any(p['id']==profile['id'] for p in state.get('generation',{}).get('profiles',[])): self.window.notice('同一工作流已重新匯入，保留目前內容。'); return
        if self.deleted.get('remote'):
            self.loading=True; self.details(); self.catalog.restore(self.deleted['remote'],self.finish_restore,self.operation_failed)
        else: self.finish_restore()
    def finish_restore(self):
        data=self.deleted; state=self.window.state
        self.window.generation_panel.settings()
        if data['profile']: state['generation']['profiles'].append(data['profile'])
        state['generation']['chosen'].update(data['chosen'])
        if 'multi_output' in state: state['multi_output']['bindings'].extend(data['bindings'])
        db=self.window.store.db; db.execute('DELETE FROM workflow_deletions WHERE id=(SELECT MAX(id) FROM workflow_deletions)'); db.commit()
        prior=db.execute('SELECT body FROM workflow_deletions ORDER BY id DESC LIMIT 1').fetchone()
        self.deleted=json.loads(prior[0]) if prior else None; self.loading=False; self.undo_button.setEnabled(bool(self.deleted)); self.window.generation_panel.changed(); self.rebuild()
