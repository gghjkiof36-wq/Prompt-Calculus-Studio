"""Saved workflow files; Canvas owns all prompt and image bindings."""
import copy,json,sqlite3
from PySide6.QtCore import Qt,QTimer,QSize,QEvent
from PySide6.QtWidgets import QWidget,QVBoxLayout,QBoxLayout,QListWidget,QListWidgetItem,QFileDialog,QLineEdit,QCheckBox,QDialog,QSizePolicy,QStyle,QStyleOptionComboBox
from .widgets import label,button,row,ComboBox,InputDialog,StudioDialog,RoundMenu,ActionHeader,ElidedLabel
from .generation import active_profile,validate_profile
from .workflow_catalog import WorkflowCatalog
from .ui_icons import icon


class FolderComboBox(ComboBox):
    """Keep the default folder readable and elide unusually long paths."""
    def __init__(self):
        super().__init__()
        self.fit_label()

    def fit_label(self):
        option=QStyleOptionComboBox();self.initStyleOption(option)
        field=self.style().subControlRect(QStyle.ComplexControl.CC_ComboBox,option,QStyle.SubControl.SC_ComboBoxEditField,self)
        chrome=max(48,self.width()-field.width())+2
        width=max(130,self.fontMetrics().horizontalAdvance('全部資料夾')+chrome)
        self.setMinimumWidth(width);self.setMaximumWidth(max(220,width))

    def event(self,event):
        result=super().event(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange,QEvent.Type.Show):self.fit_label()
        return result

    def sizeHint(self):
        size=super().sizeHint();size.setWidth(max(self.minimumWidth(),min(self.maximumWidth(),size.width())))
        return size

class FolderActionHeader(ActionHeader):
    def minimumSizeHint(self):
        # Advertise the stacked minimum before the outer scroll area decides
        # whether this row fits; the horizontal sum would prevent reflow.
        size=super().minimumSizeHint()
        actions=[action for action in self.actions if not action.isHidden()]
        action_width=sum(action.minimumSizeHint().width() for action in actions)+8*max(0,len(actions)-1)
        size.setWidth(max(self.title.minimumWidth(),self.title.minimumSizeHint().width(),action_width))
        return size


class WorkflowManager(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.signature=None; self.deleted=None; self.updating=False; self.loading=False; self.connection_epoch=window.comfy.epoch
        self.owner=(window.store,window.state['workspace']); self.request_serial=0
        self.catalog=WorkflowCatalog(window.comfy); self.catalog.changed.connect(self.rebuild)
        window.store.db.execute('CREATE TABLE IF NOT EXISTS workflow_deletions (id INTEGER PRIMARY KEY, body TEXT NOT NULL)'); window.store.db.commit()
        saved=window.store.db.execute('SELECT body FROM workflow_deletions ORDER BY id DESC LIMIT 1').fetchone()
        if saved: self.deleted=json.loads(saved[0])
        body=QVBoxLayout(self); body.setContentsMargins(0,0,0,0); body.setSpacing(12)
        self.query=QLineEdit(); self.query.setPlaceholderText('搜尋工作流名稱或資料夾'); self.query.setClearButtonEnabled(True)
        self.query.setAccessibleName('搜尋工作流'); self.query.setMaximumWidth(680)
        self.source=ComboBox(); self.source.addItem('全部來源','all'); self.source.addItem('ComfyUI 已儲存','comfy'); self.source.addItem('桌面已連結','desktop')
        self.source.setAccessibleName('工作流來源'); self.source.setMaximumWidth(220)
        self.folder=FolderComboBox(); self.folder.addItem('全部資料夾','')
        self.search_row=QBoxLayout(QBoxLayout.Direction.LeftToRight)
        self.search_row.addWidget(self.query,1); self.search_row.addWidget(self.source); self.search_row.addStretch()
        body.addLayout(self.search_row)
        self.reload_button=button('重新整理',self.reload,'Quiet'); self.reload_button.setProperty('iconName','refresh')
        self.import_button=button('匯入工作流…',window.generation_panel.import_workflow,'Primary'); self.import_button.setIcon(icon('plus',color='on-accent'))
        self.folder_actions=FolderActionHeader(self.folder,self.reload_button,self.import_button)
        body.addWidget(self.folder_actions)
        self.catalog_status=label('','Subtle',True); body.addWidget(self.catalog_status); self.catalog_status.hide()
        self.list=QListWidget(); self.list.setTextElideMode(Qt.TextElideMode.ElideRight); self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setStyleSheet('QListWidget::item { padding:7px 12px; margin:1px 0; border-radius:7px; }')
        self.list.currentItemChanged.connect(self.details); body.addWidget(self.list)
        self.query.textChanged.connect(self.rebuild); self.source.currentIndexChanged.connect(self.rebuild); self.folder.currentIndexChanged.connect(self.rebuild)
        self.read_button=button('讀取節點',self.read_selected,'Quiet')
        self.rename_button=button('重新命名',self.rename,'Quiet'); self.reimport_button=button('重新匯入…',self.reimport,'Quiet')
        self.delete_button=button('刪除',self.remove,'DeleteWorkflow'); self.undo_button=button('復原刪除',self.undo_remove,'Quiet'); self.undo_button.setEnabled(bool(self.deleted))
        # Keep the original controls as the action state holders; the menu
        # dispatches their existing signals instead of duplicating behaviour.
        for control in (self.rename_button,self.reimport_button,self.delete_button,self.undo_button):
            control.setParent(self); control.hide()
        self.more_button=button('更多',self.open_actions,'Quiet'); self.more_button.setProperty('iconName','chevron-down')
        self.info=ElidedLabel(''); self.info.setObjectName('Subtle'); self.info.setMinimumWidth(0)
        self.info.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        body.addWidget(ActionHeader(self.info,self.read_button,self.more_button))
        window.generation_panel.importFeedback.connect(self.refresh)
        window.comfy.stateChanged.connect(self.refresh); self.refresh()

    def open_actions(self):
        menu=RoundMenu(self)
        for control in (self.rename_button,self.reimport_button,self.delete_button,self.undo_button):
            if control is self.delete_button:menu.addSeparator()
            action=menu.addAction(control.text(),control.click); action.setEnabled(control.isEnabled())
        menu.open_for(self.more_button)

    def resizeEvent(self,event):
        self.search_row.setDirection(QBoxLayout.Direction.TopToBottom if self.width()<500 else QBoxLayout.Direction.LeftToRight)
        super().resizeEvent(event)
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
        if self.connection_epoch!=self.window.comfy.epoch or self.owner!=(self.window.store,self.window.state['workspace']):
            self.connection_epoch=self.window.comfy.epoch; self.owner=(self.window.store,self.window.state['workspace']); self.loading=False; self.signature=None; self.request_serial+=1
        state=self.window.state; data=state.get('multi_output',{}); generation=state.get('generation',{})
        signature=json.dumps([generation,data.get('bindings'),data.get('clip_inputs'),data.get('connections'),[(k,v['name'],v['canvas']) for k,v in data.get('outputs',{}).items()]],sort_keys=True)
        if signature==self.signature: return
        self.signature=signature; prior=self.list.currentItem(); current=active_profile(state) if data.get('version',1)<4 else None; chosen=prior.data(Qt.ItemDataRole.UserRole) if prior else current['id'] if current else None
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
        profile=self.selected(); entry=self.entry()
        self.delete_button.setEnabled(entry is not None and not self.loading)
        modern=self.window.state.get('multi_output',{}).get('version',1)>=4
        self.read_button.setText('讀取節點' if modern else '設為活動工作流')
        self.read_button.setVisible(not modern or bool(entry and entry['kind']=='comfy' and not profile))
        self.read_button.setEnabled(entry is not None and not self.loading)
        for widget in (self.rename_button,self.reimport_button): widget.setEnabled(profile is not None and not self.loading)
        self.reimport_button.setText('重新讀取' if entry and entry['kind']=='comfy' else '重新匯入…')
        self.more_button.setEnabled(entry is not None or self.undo_button.isEnabled())
        if not profile: self.info.setText(entry['path'] if entry else ''); return
        self.info.setText(profile['name']+' · '+str(len(profile['graph']))+' 個節點')
    def read_selected(self):
        entry=self.entry(); profile=self.selected()
        if entry and entry['kind']=='comfy': self.load_remote(entry)
        elif profile: self.window.generation_panel.save_profile(profile); self.refresh()
    def load_remote(self,entry):
        self.loading=True; self.details(); server=self.catalog.server
        self.request_serial+=1; serial=self.request_serial; owner=self.owner; epoch=self.window.comfy.epoch
        originals=copy.deepcopy(self.window.state.get('generation',{}).get('profiles',[]))
        def valid():
            return serial==self.request_serial and owner==(self.window.store,self.window.state['workspace']) and epoch==self.window.comfy.epoch and server==self.window.comfy.url and not self.window.comfy.stopped
        def failed(error):
            if valid():self.loading=False; self.details(); self.window.notice('無法連結這份工作流：'+str(error))
        def received(value):
            if not valid():return
            try:
                from .workflow_import import read_profile
                profile=read_profile(value,entry['name'],entry['id'],dict(server=server,path=entry['path']))
                previous=next((p for p in originals if p['id']==profile['id']),None)
                current=next((p for p in self.window.state.get('generation',{}).get('profiles',[]) if p['id']==profile['id']),None)
                if current!=previous:raise ValueError('工作流已更新，請重新讀取。')
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
        from .workflow_deletion import prepare_remove
        try:prepare_remove(self.window.state,entry['id'])
        except (ValueError,KeyError,TypeError) as exc:self.operation_failed('刪除未完成：'+str(exc));return
        if entry['kind']=='comfy':
            context=self.operation_context();before=copy.deepcopy(self.window.state)
            def moved(remote):
                if not self.operation_current(context):return
                if self.window.state!=before:
                    self.operation_failed('ComfyUI 檔案已移至回收區，但桌面期間有修改，未改動桌面；請先從 ComfyUI 回收路徑復原：'+remote['trash']);return
                self.finish_remove(entry['id'],remote)
            self.loading=True; self.details()
            self.catalog.trash(entry['path'],moved,lambda error:self.operation_failed(error) if self.operation_current(context) else None)
        else: self.finish_remove(entry['id'])
    def operation_context(self):
        self.request_serial+=1
        return self.window.store,self.window.state['workspace'],self.window.comfy.epoch,self.window.comfy.url,self.request_serial
    def operation_current(self,context):
        return context==(self.window.store,self.window.state['workspace'],self.window.comfy.epoch,self.window.comfy.url,self.request_serial) and not self.window.comfy.stopped
    def operation_failed(self,error): self.loading=False; self.details(); self.window.notice(str(error))
    def finish_remove(self,key,remote=None):
        from .workflow_deletion import prepare_remove,commit_change
        try:
            candidate,record=prepare_remove(self.window.state,key,remote)
            commit_change(self.window.store,candidate,record=record)
        except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:self.operation_failed('刪除未完成：'+str(exc));return
        self.window.state.clear();self.window.state.update(candidate);self.deleted=record
        self.loading=False; self.undo_button.setEnabled(True); self.window.generation_panel.changed(); self.rebuild()
    def undo_remove(self):
        if not self.deleted: return
        if self.loading: return
        from .workflow_deletion import prepare_restore
        try:prepare_restore(self.window.state,self.deleted)
        except (ValueError,KeyError,TypeError) as exc:self.operation_failed('復原未完成：'+str(exc));return
        if self.deleted.get('remote'):
            context=self.operation_context();record=copy.deepcopy(self.deleted)
            def restored():
                if not self.operation_current(context):return
                # The external move has completed. Persist that fact using the
                # existing optional remote field before revalidating the local
                # candidate; never replay the move after a local conflict.
                updated=copy.deepcopy(record);updated['remote']=None
                try:
                    db=self.window.store.db
                    with db:
                        row=db.execute('SELECT id,body FROM workflow_deletions ORDER BY id DESC LIMIT 1').fetchone()
                        if not row or json.loads(row[1])!=record:raise ValueError('復原紀錄已變更。')
                        db.execute('UPDATE workflow_deletions SET body=? WHERE id=?',(json.dumps(updated,ensure_ascii=False),row[0]))
                    self.deleted=updated;self.finish_restore()
                except (ValueError,sqlite3.Error) as exc:
                    self.operation_failed('ComfyUI 檔案已復原，桌面紀錄尚未保存；請勿重送遠端復原：'+str(exc))
            self.loading=True; self.details(); self.catalog.restore(record['remote'],restored,lambda error:self.operation_failed(error) if self.operation_current(context) else None)
        else: self.finish_restore()
    def finish_restore(self):
        from .workflow_deletion import prepare_restore,commit_change
        try:
            candidate=prepare_restore(self.window.state,self.deleted)
            legacy_selection='clip_workflows' not in self.deleted and bool(self.deleted.get('bindings')) and self.window.state.get('multi_output',{}).get('version',0)>=4
            commit_change(self.window.store,candidate,consume=self.deleted)
        except (ValueError,KeyError,TypeError,sqlite3.Error) as exc:self.operation_failed('復原未完成：'+str(exc));return
        self.window.state.clear();self.window.state.update(candidate)
        db=self.window.store.db
        prior=db.execute('SELECT body FROM workflow_deletions ORDER BY id DESC LIMIT 1').fetchone()
        self.deleted=json.loads(prior[0]) if prior else None; self.loading=False; self.undo_button.setEnabled(bool(self.deleted)); self.window.generation_panel.changed(); self.rebuild()
        if legacy_selection:self.window.notice('工作流已復原；舊紀錄未保存各 CLIP 的工作流選擇，請在 Canvas 確認，未改動目前選擇。')
