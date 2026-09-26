"""Recent ComfyUI results remain references until explicitly collected."""
import hashlib
import json
from pathlib import Path
from PySide6.QtCore import Qt,QSize,QTimer
from PySide6.QtWidgets import QFrame,QVBoxLayout,QSplitter,QListWidget,QListWidgetItem,QFileDialog,QPlainTextEdit
from .widgets import label,button,row,panel,ComboBox,RoundMenu,thumb_icon,set_preview,open_file,reveal_file
from .media import import_image
from .metadata_view import readable_metadata


def result_id(source):
    return 'recent-'+hashlib.sha256(json.dumps([source['prompt_id'],source['image']],sort_keys=True).encode()).hexdigest()


def album_directory(store,album):
    if album.get('directory'):
        path=Path(album['directory'])
        if not path.is_absolute() or not path.is_dir(): raise ValueError('資料夾的儲存位置無法使用，請重新指定。')
        return path
    path=store.directory/'originals'/'albums'/hashlib.sha256(album['id'].encode()).hexdigest()[:20]
    path.mkdir(parents=True,exist_ok=True)
    return path


class RecentPage(QFrame):
    def __init__(self,window):
        super().__init__(); self.window=window; self.catalog=window.catalog
        self.record=None; self.page=0; self.pending=[]; self.saves=[]; self.processing=False; self.collecting=set()
        self.known={r[0] for r in self.catalog.db.execute("SELECT id FROM resources WHERE kind='recent'")}
        self.setObjectName('WorkspaceSurface'); layout=QVBoxLayout(self); layout.setContentsMargins(12,16,12,16)
        self.heading=label('最近生成','Heading'); layout.addWidget(self.heading)
        self.destination_picker=ComboBox(); self.destination_picker.setMinimumWidth(190); self.destination_picker.currentIndexChanged.connect(self.destination_changed)
        self.destination_warning=label('!'); self.destination_warning.setFixedSize(20,20); self.destination_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.destination_warning.setStyleSheet('color:#ed5a5a;border:1.5px solid #ed5a5a;border-radius:10px;font-weight:700;')
        self.destination_warning.setToolTip('請先選擇收藏位置。'); self.destination_warning.setAccessibleName('請先選擇收藏位置。'); self.destination_warning.hide()
        layout.addLayout(row(label('收藏至'),self.destination_picker,button('指定磁碟資料夾…',self.choose_directory),self.destination_warning,None,button('重新整理',self.request_refresh)))
        split=QSplitter(); layout.addWidget(split,1)
        middle,content=panel(); split.addWidget(middle)
        self.images=QListWidget(); self.images.setViewMode(QListWidget.ViewMode.IconMode); self.images.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.images.setMovement(QListWidget.Movement.Static); self.images.setIconSize(QSize(156,156)); self.images.setGridSize(QSize(194,214)); self.images.setWordWrap(True)
        self.images.currentItemChanged.connect(self.select); self.images.itemDoubleClicked.connect(lambda _:self.open_original())
        self.images.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.images.customContextMenuRequested.connect(self.context)
        content.addWidget(self.images,1); self.counter=label('尚無生成圖片','Subtle')
        content.addLayout(row(self.counter,None,button('上一頁',lambda:self.turn(-1)),button('下一頁',lambda:self.turn(1))))
        detail,right=panel('InsetPanel'); split.addWidget(detail); split.setSizes([750,360])
        self.preview=label('生成後可在這裡挑圖'); self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter); self.preview.setMinimumHeight(160); right.addWidget(self.preview)
        self.info=label('','Subtle',True); right.addWidget(self.info)
        self.save_button=button('收藏到指定資料夾',self.collect,'Primary'); self.save_button.setEnabled(False); right.addWidget(self.save_button)
        right.addWidget(button('開啟原圖',self.open_original))
        self.img2img=button('導入圖生圖',self.use_for_generation); self.img2img.setEnabled(False); right.addWidget(self.img2img)
        self.metadata=QPlainTextEdit(); self.metadata.setReadOnly(True); right.addWidget(self.metadata,1)
        self.refresh_timer=QTimer(self); self.refresh_timer.setSingleShot(True); self.refresh_timer.setInterval(150); self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_destinations(); self.refresh()

    def refresh_destinations(self):
        selected=self.window.state['settings'].get('recent_destination','')
        self.destination_picker.blockSignals(True); self.destination_picker.clear(); self.destination_picker.addItem('選擇媒體庫資料夾…','')
        for album in self.catalog.rows('album',limit=10000): self.destination_picker.addItem(album['name'],'album:'+album['id'])
        external=self.window.state['settings'].get('recent_external','')
        if external: self.destination_picker.addItem('磁碟 · '+Path(external).name,'external')
        self.destination_picker.setCurrentIndex(max(0,self.destination_picker.findData(selected))); self.destination_picker.blockSignals(False)
        self.update_destination_label()

    def destination_changed(self):
        self.window.state['settings']['recent_destination']=self.destination_picker.currentData() or ''
        self.window.changed('settings'); self.update_destination_label(); self.update_save_button()

    def update_destination_label(self):
        value=self.destination_picker.currentData() or ''
        if value=='external': text=self.window.state['settings'].get('recent_external','')
        elif value.startswith('album:'):
            album=self.catalog.get(value[6:]) or {}; text=album.get('directory','')
        else: text=''
        self.destination_picker.setToolTip(text)
        if value: self.destination_warning.hide()

    def choose_directory(self):
        path=QFileDialog.getExistingDirectory(self.window,'選擇收藏資料夾',self.window.state['settings'].get('recent_external',''),QFileDialog.Option.DontUseNativeDialog)
        if path:
            self.window.state['settings'].update(recent_external=path,recent_destination='external')
            self.refresh_destinations(); self.window.changed('settings'); self.update_save_button()

    def destination(self):
        value=self.destination_picker.currentData() or ''
        if value.startswith('album:'):
            album=self.catalog.get(value[6:])
            if not album: raise ValueError('收藏資料夾已不存在，請重新選擇。')
            return str(album_directory(self.window.store,album)),album['id']
        if value=='external':
            path=Path(self.window.state['settings'].get('recent_external',''))
            if path.is_absolute() and path.is_dir(): return str(path),''
            raise ValueError('收藏目的地無法使用，請重新選擇。')
        raise ValueError('請先在「最近生成」選擇收藏資料夾。')

    def ensure_destination(self):
        try: self.destination(); return True
        except (ValueError,OSError) as exc:
            self.window.tabs.setCurrentWidget(self)
            message='請先選擇收藏位置。' if not self.destination_picker.currentData() else str(exc)
            self.destination_warning.setToolTip(message); self.destination_warning.setAccessibleName(message); self.destination_warning.show()
            self.destination_picker.setFocus(); return False

    def request_refresh(self):
        self.refresh_destinations()
        if self.window.comfy.connected: self.window.comfy.request('desktop/results',done=self.receive_results)
        else: self.window.notice('連接 ComfyUI 後可更新生成結果。'); self.refresh()

    def receive_results(self,records):
        queued={result_id(r) for r in self.pending}
        for record in reversed(records):
            ident=result_id(record)
            if record.get('path') and ident not in self.known and ident not in queued:
                self.pending.append(record); queued.add(ident)
        self.pump()

    def pump(self):
        if self.window.closing:
            self.pending=[]
            if not self.saves: return
        if self.processing or not (self.pending or self.saves): return
        if self.window.jobs.active: QTimer.singleShot(250,self.pump); return
        self.processing=True
        if self.saves:
            ident,result,destination,album=self.saves.pop(0)
            def finished(record):
                self.processing=False; self.collecting.discard(ident)
                if album:
                    duplicate=self.catalog.db.execute("SELECT id FROM resources WHERE kind='image' AND parent=? AND json_extract(body,'$.path')=?",(album,record['path'])).fetchone()
                    if not duplicate:
                        path=Path(record['path'])
                        if path.is_relative_to(self.window.store.directory): record.update(owned=True,original_relative=str(path.relative_to(self.window.store.directory)).replace('\\','/'))
                        self.catalog.put('image',record,album)
                recent=self.catalog.get(ident)
                if recent:
                    recent.setdefault('collected',{})[destination]=result; self.catalog.put('recent',recent)
                self.window.notice('已收藏：'+result['path']); self.refresh(); QTimer.singleShot(0,self.pump)
            def work(cancel):
                try: return import_image(result['path'],self.window.store.directory,album),''
                except Exception as exc: return None,str(exc)
            def done(value):
                if value[0]: finished(value[0])
                else:
                    self.processing=False; self.collecting.discard(ident); self.window.notice('原圖已收藏，但媒體庫加入失敗：'+value[1]); self.refresh(); QTimer.singleShot(0,self.pump)
            self.window.jobs.start('正在將收藏圖片加入媒體庫…',work,done)
        else:
            source=self.pending.pop(0); ident=result_id(source)
            def work(cancel):
                try: return import_image(source['path'],self.window.store.directory,''),''
                except Exception as exc: return None,str(exc)
            def done(value):
                self.processing=False; record,error=value
                if record:
                    record.update(id=ident,source=source,collected={}); self.catalog.put('recent',record); self.known.add(ident)
                    self.catalog.db.execute("DELETE FROM resources WHERE kind='recent' AND id NOT IN (SELECT id FROM resources WHERE kind='recent' ORDER BY json_extract(body,'$.created') DESC LIMIT 120)"); self.catalog.db.commit()
                    self.refresh_timer.start()
                    self.window.show_latest_generated(record)
                    if not self.pending and not self.saves:self.window.notice('最近生成預覽已更新。')
                elif error: self.window.notice('結果預覽無法讀取：'+error)
                QTimer.singleShot(0,self.pump)
            self.window.jobs.start('更新最近生成預覽…',work,done)

    def refresh(self):
        selected=self.record['id'] if self.record else None
        self.images.blockSignals(True); self.images.clear()
        total=self.catalog.count('recent'); self.page=min(self.page,max(0,(total-1)//30))
        for record in self.catalog.rows('recent',limit=30,offset=self.page*30):
            item=QListWidgetItem(record['name']); item.setData(Qt.ItemDataRole.UserRole,record['id'])
            item.setIcon(thumb_icon(self.window.store,record.get('thumb'),156)); self.images.addItem(item)
            if selected==record['id']: self.images.setCurrentItem(item)
        if self.images.currentItem() is None and self.images.count(): self.images.setCurrentRow(0)
        self.images.blockSignals(False); self.select(self.images.currentItem())
        self.counter.setText(f'{total} 張 · 第 {self.page+1} 頁 · 保留最近 120 筆結果紀錄')
        if hasattr(self.window,'canvas') and hasattr(self.window.canvas,'results'): self.window.canvas.results.refresh()

    def select(self,item):
        self.record=self.catalog.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        set_preview(self.preview,self.window.store,(self.record or {}).get('thumb'))
        self.metadata.setPlainText(readable_metadata(self.record) if self.record else '')
        self.img2img.setEnabled(bool(self.record))
        self.update_save_button()

    def use_for_generation(self):
        if self.record: self.window.use_image_for_generation(self.record['path'])

    def update_save_button(self):
        self.save_button.setText('收藏到指定資料夾'); self.save_button.setEnabled(False); self.info.setText('')
        if not self.record: return
        try: destination,_=self.destination()
        except (OSError,ValueError): self.info.setText('先選擇上方收藏位置。'); return
        saved=self.record.get('collected',{}).get(destination)
        if saved and Path(saved['path']).is_file():
            self.save_button.setText('✓ 已收藏'); self.info.setText(('含生成資料' if saved['has_generation'] else '缺少生成資料')+' · '+('含模組快照' if saved['has_snapshot'] else '缺少模組快照')); return
        if self.record['id'] in self.collecting: self.save_button.setText('正在收藏…'); return
        self.save_button.setEnabled(bool(getattr(self.window,'comfy',None) and self.window.comfy.connected))
        self.info.setText('收藏時複製原始 PNG，保留它自己的生成資料。')

    def collect(self,record=None):
        record=record if isinstance(record,dict) else self.record
        if not record or not self.window.comfy.connected: return
        if record['id'] in self.collecting: return
        try: destination,album=self.destination()
        except (ValueError,OSError) as exc: self.window.notice(str(exc)); return
        ident=record['id']; source=record['source']; self.collecting.add(ident); self.update_save_button()
        def done(result): self.saves.append((ident,result,destination,album)); self.pump()
        def fail(message): self.collecting.discard(ident); self.window.notice('收藏失敗：'+message); self.update_save_button(); self.refresh()
        self.window.comfy.request('desktop/collect',dict(image=source['image'],prompt_id=source['prompt_id'],destination=destination),done,fail)

    def context(self,pos):
        item=self.images.itemAt(pos)
        if not item: return
        self.images.setCurrentItem(item); menu=RoundMenu(self)
        action=menu.addAction('收藏到目前指定的資料夾',self.collect); action.setEnabled(self.save_button.isEnabled())
        record=self.catalog.get(item.data(Qt.ItemDataRole.UserRole))
        if not record: return
        path=record['path']
        menu.addAction('導入圖生圖',lambda:self.window.use_image_for_generation(path))
        # The list stores IDs and may refresh while the menu is open.
        menu.addAction('匯出圖片…',lambda:self.window.open_export([path]))
        menu.addAction('開啟原圖',lambda:open_file(self,path))
        menu.addAction('顯示檔案位置',lambda:reveal_file(self,path))
        menu.open_at(self.images.viewport().mapToGlobal(pos))

    def open_original(self):
        if self.record: open_file(self,self.record['path'])

    def turn(self,delta): self.page=max(0,self.page+delta); self.refresh()
