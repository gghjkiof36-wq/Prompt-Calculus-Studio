"""Recent ComfyUI results remain references until explicitly collected."""
import hashlib
import json
from pathlib import Path
from PySide6.QtCore import Qt,QSize,QTimer,QRectF
from PySide6.QtGui import QColor,QPainter,QPainterPath,QPen
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QBoxLayout,QWidget,QListWidget,QListWidgetItem,QFileDialog,QScrollArea,QSizePolicy,QStyledItemDelegate,QStyle
from .widgets import label,button,row,ComboBox,RoundMenu,record_icon,set_record_preview,open_file,reveal_file,ElidedLabel
from .media import import_image
from .media_gallery import GalleryPreview,GalleryDetailCard,ImageMetadataDialog
from .theme import visual_tokens
from .ui_icons import icon


class RecentImageDelegate(QStyledItemDelegate):
    """Image-only tiles; accessible text and tooltips retain the full name."""
    def paint(self,painter,option,index):
        colors=visual_tokens(self.parent().window.state['settings'])
        painter.save();painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect=QRectF(option.rect).adjusted(6,6,-6,-6)
        path=QPainterPath();path.addRoundedRect(rect,12,12)
        selected=bool(option.state&QStyle.StateFlag.State_Selected)
        if selected or option.state&QStyle.StateFlag.State_MouseOver:painter.fillPath(path,QColor(colors['selected'] if selected else colors['hover']))
        picture=index.data(Qt.ItemDataRole.DecorationRole)
        if picture and not picture.isNull():
            pixmap=picture.pixmap(rect.size().toSize());size=pixmap.deviceIndependentSize()
            target=QRectF(0,0,size.width(),size.height());target.moveCenter(rect.center())
            clip=QPainterPath();clip.addRoundedRect(target,12,12)
            painter.setClipPath(clip);painter.drawPixmap(target,pixmap,QRectF(pixmap.rect()));painter.setClipping(False)
        else:
            marker=icon('media',colors['secondary']).pixmap(30,30)
            painter.drawPixmap(round(rect.center().x()-15),round(rect.center().y()-25),marker)
            painter.setPen(QColor(colors['secondary']));painter.drawText(rect.adjusted(4,32,-4,-4),Qt.AlignmentFlag.AlignCenter,'無法預覽')
        if selected or option.state&QStyle.StateFlag.State_MouseOver:
            pen=QPen(QColor(colors['accent'] if selected else colors['line']),2 if selected else 1);pen.setCosmetic(True)
            painter.setPen(pen);painter.setBrush(Qt.BrushStyle.NoBrush);painter.drawRoundedRect(rect,12,12)
        painter.restore()


class RecentPreview(GalleryPreview):
    def keyPressEvent(self,event):QFrame.keyPressEvent(self,event)

    def fit_preview(self):
        if self._fitting:return
        self._fitting=True
        try:
            maximum=104 if self.window().height()<=540 else max(140,min(260,round(self.window().height()*.30)))
            source=self._source
            if source is None or source.isNull():self.setFixedHeight(min(160,maximum));return
            width=max(1,self.contentsRect().width());natural=source.deviceIndependentSize()
            height=min(maximum,max(120,round(width*natural.height()/natural.width())));self.setFixedHeight(height)
            fitted=source.scaled(round(width*source.devicePixelRatioF()),round(height*source.devicePixelRatioF()),Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
            # Bypass GalleryPreview.setPixmap so resizing preserves the cached source.
            super(GalleryPreview,self).setPixmap(fitted)
        finally:self._fitting=False


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
        self.details_open=False;self._adapting=False
        self.setObjectName('WorkspaceSurface'); layout=QVBoxLayout(self); layout.setContentsMargins(24,20,24,20);layout.setSpacing(16)
        self.heading=label('最近生成','Heading')
        self.refresh_button=button('',self.request_refresh,'IconButton');self.refresh_button.setProperty('iconName','refresh');self.refresh_button.setIcon(icon('refresh'));self.refresh_button.setToolTip('重新整理');self.refresh_button.setAccessibleName('重新整理')
        self.close_button=button('',self.close_browser,'IconButton');self.close_button.setProperty('iconName','close');self.close_button.setIcon(icon('close'));self.close_button.setToolTip('關閉最近生成');self.close_button.setAccessibleName('關閉最近生成')
        for control in (self.refresh_button,self.close_button):
            control.setIconSize(QSize(18,18));control.setFixedSize(36,36)
        self.back_button=button('返回圖片',self.hide_details,'Quiet');self.back_button.hide()
        layout.addLayout(row(self.heading,self.back_button,None,self.refresh_button,self.close_button))
        self.destination_picker=ComboBox(); self.destination_picker.setMinimumWidth(0);self.destination_picker.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed);self.destination_picker.currentIndexChanged.connect(self.destination_changed)
        self.destination_warning=label('!','DestinationWarning'); self.destination_warning.setFixedSize(20,20); self.destination_warning.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.destination_warning.setToolTip('請先選擇收藏位置。'); self.destination_warning.setAccessibleName('請先選擇收藏位置。'); self.destination_warning.hide()
        self.destination_picker.setMaximumWidth(320)
        self.folder_button=button('',self.choose_directory,'IconButton');self.folder_button.setProperty('iconName','folder');self.folder_button.setIcon(icon('folder'));self.folder_button.setToolTip('指定磁碟資料夾');self.folder_button.setAccessibleName('指定磁碟資料夾')
        destination_row=QHBoxLayout();destination_row.setSpacing(8);destination_row.addWidget(label('儲存至','Subtle'));destination_row.addWidget(self.destination_picker,1);destination_row.addWidget(self.folder_button);destination_row.addWidget(self.destination_warning);destination_row.addStretch();layout.addLayout(destination_row)
        self.body=QWidget();self.body_layout=QHBoxLayout(self.body);self.body_layout.setContentsMargins(0,0,0,0);self.body_layout.setSpacing(24);layout.addWidget(self.body,1)
        self.grid_panel=QWidget();content=QVBoxLayout(self.grid_panel);content.setContentsMargins(0,0,0,0);content.setSpacing(10);self.body_layout.addWidget(self.grid_panel,1)
        self.images=QListWidget(); self.images.setViewMode(QListWidget.ViewMode.IconMode); self.images.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.images.setMovement(QListWidget.Movement.Static); self.images.setIconSize(QSize(176,176)); self.images.setGridSize(QSize(194,194));self.images.setSpacing(0);self.images.setWordWrap(False)
        self.images.setItemDelegate(RecentImageDelegate(self));self.images.setMouseTracking(True);self.images.setAccessibleName('最近生成圖片');self.images.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.images.currentItemChanged.connect(self.select); self.images.itemDoubleClicked.connect(lambda _:self.open_original())
        self.images.itemClicked.connect(lambda item:self.show_details(item));self.images.itemActivated.connect(self.show_details)
        self.images.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.images.customContextMenuRequested.connect(self.context)
        content.addWidget(self.images,1);self.empty=label('生成的圖片會顯示在這裡。','Subtle',True);self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter);content.addWidget(self.empty,1)
        self.counter=label('0 張','Subtle');self.previous=button('‹',lambda:self.turn(-1),'Quiet');self.previous.setToolTip('上一頁');self.next=button('›',lambda:self.turn(1),'Quiet');self.next.setToolTip('下一頁')
        content.addLayout(row(self.counter,None,self.previous,self.next))
        self.detail_scroll=QScrollArea();self.detail_scroll.setWidgetResizable(True);self.detail_scroll.setFrameShape(QFrame.Shape.NoFrame);self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff);self.body_layout.addWidget(self.detail_scroll)
        wrapper=QWidget();wrap=QVBoxLayout(wrapper);wrap.setContentsMargins(0,0,12,0);wrap.setSpacing(0);self.detail_scroll.setWidget(wrapper)
        self.detail=GalleryDetailCard();right=QVBoxLayout(self.detail);right.setContentsMargins(18,18,18,18);right.setSpacing(12);wrap.addWidget(self.detail);wrap.addStretch()
        self.detail_name=ElidedLabel('');self.detail_name.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.detail_close=button('×',self.hide_details,'IconButton');self.detail_close.setToolTip('關閉詳情');self.detail_close.setAccessibleName('關閉詳情')
        title_row=QHBoxLayout();title_row.addWidget(self.detail_name,1);title_row.addWidget(self.detail_close);right.addLayout(title_row)
        self.detail_body=QBoxLayout(QBoxLayout.Direction.TopToBottom);self.detail_body.setSpacing(20);right.addLayout(self.detail_body)
        self.preview=RecentPreview(window.store);self.preview.setAcceptDrops(False);self.preview.setToolTip('完整圖片預覽');self.preview.setFocusPolicy(Qt.FocusPolicy.NoFocus);self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.detail_body.addWidget(self.preview,0,Qt.AlignmentFlag.AlignTop)
        self.operations=QWidget();actions=QVBoxLayout(self.operations);actions.setContentsMargins(0,0,0,0);actions.setSpacing(12);self.detail_body.addWidget(self.operations)
        self.save_button=button('儲存圖片',self.collect,'Primary');self.save_button.setEnabled(False);actions.addWidget(self.save_button)
        self.info=label('','Subtle',True);actions.addWidget(self.info)
        self.original_button=button('開啟原圖',self.open_original,'Quiet');self.more_button=button('',self.detail_menu,'IconButton');self.more_button.setProperty('iconName','more-horizontal');self.more_button.setIcon(icon('more-horizontal'));self.more_button.setToolTip('更多');actions.addLayout(row(self.original_button,None,self.more_button))
        self.divider=QFrame();self.divider.setObjectName('RecentDetailDivider');self.divider.setFixedHeight(1);actions.addWidget(self.divider)
        self.metadata_button=button('查看圖片資料 ›',self.show_metadata,'Quiet');actions.addWidget(self.metadata_button,0,Qt.AlignmentFlag.AlignLeft)
        self.metadata_summary=label('','Subtle',True);actions.addWidget(self.metadata_summary)
        self.detail_scroll.hide()
        self.detail.resized.connect(self.fit_detail)
        self.refresh_timer=QTimer(self); self.refresh_timer.setSingleShot(True); self.refresh_timer.setInterval(150); self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_destinations(); self.refresh()

    def close_browser(self):
        if getattr(self.window,'recent_sheet',None):self.window.recent_sheet.reject()

    def hide_details(self):
        self.details_open=False;self.adapt_layout();self.images.setFocus()

    def show_details(self,item):
        if item:self.select(item);self.details_open=True;self.adapt_layout()

    def show_record(self,ident):
        identifiers=[record['id'] for record in self.catalog.rows('recent',limit=120)]
        if ident not in identifiers:return
        self.page=identifiers.index(ident)//30;self.refresh()
        for i in range(self.images.count()):
            item=self.images.item(i)
            if item.data(Qt.ItemDataRole.UserRole)==ident:self.images.setCurrentItem(item);self.show_details(item);self.images.scrollToItem(item);break

    def show_metadata(self):
        if self.record:
            self.metadata_dialog=ImageMetadataDialog(self,self.record);self.metadata_dialog.exec()

    def detail_menu(self):
        menu=RoundMenu(self);menu.addAction('指定磁碟資料夾…',self.choose_directory)
        if self.record:
            path=self.record['path'];menu.addAction('匯出圖片…',lambda:self.window.open_export([path]));menu.addAction('顯示檔案位置',lambda:reveal_file(self,path))
        menu.open_at(self.more_button.mapToGlobal(self.more_button.rect().bottomLeft()))

    def adapt_layout(self):
        if self._adapting:return
        self._adapting=True
        try:
            narrow=self.width()<800 or (self.width()<1000 and self.fontMetrics().height()>24)
            self.layout().setContentsMargins(*((16,16,16,16) if self.width()<800 else (24,20,24,20)))
            self.grid_panel.setVisible(not (narrow and self.details_open));self.detail_scroll.setVisible(self.details_open and bool(self.record));self.back_button.setVisible(narrow and self.details_open)
            self.detail_scroll.setMinimumWidth(0 if narrow else 348);self.detail_scroll.setMaximumWidth(16777215 if narrow else 400)
            self.detail_scroll.setSizePolicy(QSizePolicy.Policy.Expanding if narrow else QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Expanding)
            tile=min(194,max(80,self.images.viewport().width()-16));self.images.setGridSize(QSize(tile,tile));self.images.setIconSize(QSize(tile-18,tile-18))
            self.fit_detail()
        finally:self._adapting=False

    def fit_detail(self):
        short=self.window.height()<=540
        margin=12 if short else 18;self.detail.layout().setContentsMargins(margin,margin,margin,margin)
        self.detail.layout().setSpacing(8 if short else 12);self.detail_body.setSpacing(12 if short else 20)
        horizontal=self.detail.width()>=620 and self.fontMetrics().height()<=24
        self.detail_body.setDirection(QBoxLayout.Direction.LeftToRight if horizontal else QBoxLayout.Direction.TopToBottom)
        self.preview.setSizePolicy(QSizePolicy.Policy.Fixed if horizontal else QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.preview.setMinimumWidth(220 if horizontal else 0);self.preview.setMaximumWidth(220 if horizontal else 16777215)
        self.preview.fit_preview()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'detail_scroll'):self.adapt_layout()

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
            self.window.show_recent_sheet()
            if self.record:self.details_open=True;self.adapt_layout()
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
                if album and not self.catalog.get(album):
                    # A late collection callback must not recreate records under
                    # a removed album or mark that collection as successful.
                    if self.window.state['settings'].get('recent_destination')=='album:'+album:
                        self.window.state['settings']['recent_destination']=''; self.window.changed('settings',refresh=False)
                    self.refresh_destinations()
                    message='收藏資料夾已移除，請重新選擇。'
                    self.destination_warning.setToolTip(message); self.destination_warning.setAccessibleName(message); self.destination_warning.show()
                    self.window.notice(message); self.refresh(); QTimer.singleShot(0,self.pump)
                    return
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
            def failed(message):
                self.processing=False; self.collecting.discard(ident)
                self.window.notice('原圖已收藏，但媒體庫加入失敗：'+message); self.refresh(); QTimer.singleShot(0,self.pump)
            def done(value):
                if value[0]: finished(value[0])
                else:failed(value[1])
            self.window.jobs.start('正在將收藏圖片加入媒體庫…',work,done,failed)
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
        scroll=self.images.verticalScrollBar().value()
        self.images.blockSignals(True); self.images.clear()
        total=self.catalog.count('recent'); self.page=min(self.page,max(0,(total-1)//30))
        for record in self.catalog.rows('recent',limit=30,offset=self.page*30):
            item=QListWidgetItem(record['name']); item.setData(Qt.ItemDataRole.UserRole,record['id'])
            item.setData(Qt.ItemDataRole.AccessibleTextRole,record['name']);item.setToolTip(record['name'])
            item.setIcon(record_icon(self.window.store,record,176)); self.images.addItem(item)
            if selected==record['id']: self.images.setCurrentItem(item)
        self.images.blockSignals(False); self.select(self.images.currentItem())
        self.images.verticalScrollBar().setValue(scroll)
        self.counter.setText(f'{total} 張'+(f' · 第 {self.page+1} 頁' if total>30 else ''))
        self.counter.setToolTip('保留最近 120 張');self.previous.setVisible(total>30);self.next.setVisible(total>30)
        self.previous.setEnabled(self.page>0);self.next.setEnabled((self.page+1)*30<total)
        self.empty.setVisible(not total);self.images.setVisible(bool(total));self.adapt_layout()
        if hasattr(self.window,'canvas') and hasattr(self.window.canvas,'results'): self.window.canvas.results.refresh()
        if hasattr(self.window,'canvas_workspace_bar'):self.window.canvas_workspace_bar.refresh()

    def select(self,item):
        self.record=self.catalog.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        set_record_preview(self.preview,self.window.store,self.record or {},size=340)
        self.detail_name.setText((self.record or {}).get('name',''));self.detail_name.setToolTip(self.detail_name.text())
        metadata=(self.record or {}).get('metadata',{})
        self.metadata_summary.setText('含內嵌資料' if metadata.get('raw') else '未含內嵌資料')
        if not item:self.details_open=False
        self.update_save_button();self.adapt_layout()

    def update_save_button(self):
        self.save_button.setText('儲存圖片'); self.save_button.setEnabled(False); self.info.setText('')
        if not self.record: return
        try: destination,_=self.destination()
        except (OSError,ValueError): self.info.setText('選擇儲存位置。'); return
        saved=self.record.get('collected',{}).get(destination)
        if saved and Path(saved['path']).is_file():
            self.save_button.setText('✓ 已儲存'); self.info.setText(('含生成資料' if saved['has_generation'] else '缺少生成資料')+' · '+('含模組快照' if saved['has_snapshot'] else '缺少模組快照')); return
        if self.record['id'] in self.collecting: self.save_button.setText('正在儲存…'); return
        self.save_button.setEnabled(bool(getattr(self.window,'comfy',None) and self.window.comfy.connected))
        self.info.setText('' if self.save_button.isEnabled() else '連接 ComfyUI 後可儲存。')

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
        action=menu.addAction('儲存圖片',self.collect); action.setEnabled(self.save_button.isEnabled())
        record=self.catalog.get(item.data(Qt.ItemDataRole.UserRole))
        if not record: return
        path=record['path']
        # The list stores IDs and may refresh while the menu is open.
        menu.addAction('匯出圖片…',lambda:self.window.open_export([path]))
        menu.addAction('開啟原圖',lambda:open_file(self,path))
        menu.addAction('顯示檔案位置',lambda:reveal_file(self,path))
        menu.open_at(self.images.viewport().mapToGlobal(pos))

    def open_original(self):
        if self.record: open_file(self,self.record['path'])

    def turn(self,delta): self.page=max(0,self.page+delta); self.refresh()
