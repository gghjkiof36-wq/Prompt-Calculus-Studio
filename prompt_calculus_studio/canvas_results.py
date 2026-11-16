"""Canvas-local viewing and collection over the existing recent-result catalog."""
from pathlib import Path
from os.path import join as join_path,normpath
from PySide6.QtCore import Qt,QSize,QRectF,Signal,QTimer
from PySide6.QtGui import QImageReader,QPixmap,QIcon,QPainter,QColor
from PySide6.QtWidgets import QFrame,QVBoxLayout,QListWidget,QListWidgetItem,QPlainTextEdit,QPushButton,QSizePolicy
from .widgets import label,button,row,ComboBox,StudioDialog,dialog_buttons,thumb_icon,preview_path,ElidedLabel
from .metadata_view import readable_metadata
from .snapshots import image_snapshots
from .preview_scope import current_iteration
from .preview_requests import use_worker,decoded_bytes,worker_batch,live_result


class ResultImage(QPushButton):
    """The picture follows the space allocated by the graphics card."""
    imageActivated=Signal(int)
    def __init__(self):
        super().__init__('尚無圖片'); self.picture=QPixmap(); self.pictures=[];self.pending=set()
        self.apply_timer=QTimer(self);self.apply_timer.setSingleShot(True);self.apply_timer.timeout.connect(self.update)
        self.setObjectName('ResultPreview'); self.setMinimumSize(160,120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
    def sizeHint(self): return QSize(280,320)
    def set_picture(self,picture):
        self.apply_timer.stop();self.pending.clear()
        self.pictures=[]; self.picture=picture; self.setIcon(QIcon(picture)); self.fit_picture(); self.update()
    def set_pictures(self,pictures):
        self.apply_timer.stop();self.pending.clear()
        self.pictures=pictures; self.picture=QPixmap(); self.setIcon(QIcon()); self.setText(''); self.update()
    def image_rects(self):
        import math
        count=len(self.pictures)
        if not count:return []
        columns=math.ceil(math.sqrt(count)); rows=math.ceil(count/columns)
        area=QRectF(self.contentsRect()).adjusted(8,8,-8,-8); gap=8
        width=max(1,(area.width()-gap*(columns-1))/columns); height=max(1,(area.height()-gap*(rows-1))/rows)
        return [QRectF(area.x()+(i%columns)*(width+gap),area.y()+(i//columns)*(height+gap),width,height) for i in range(count)]
    def paintEvent(self,event):
        super().paintEvent(event)
        if not self.pictures:return
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        for index,(picture,cell) in enumerate(zip(self.pictures,self.image_rects())):
            caption=20 if cell.height()>=40 else 0
            area=cell.adjusted(0,0,0,-caption)
            if not picture.isNull():
                size=picture.size().scaled(area.size().toSize(),Qt.AspectRatioMode.KeepAspectRatio)
                target=QRectF(0,0,size.width(),size.height()); target.moveCenter(area.center())
                painter.drawPixmap(target,picture,QRectF(picture.rect()))
            if caption and index not in self.pending:
                painter.setPen(self.palette().color(self.foregroundRole()))
                painter.drawText(cell.adjusted(0,cell.height()-caption,0,0),Qt.AlignmentFlag.AlignCenter,str(index+1) if not picture.isNull() else str(index+1)+' · 圖片無法讀取')
    def mouseReleaseEvent(self,event):
        if self.pictures and event.button()==Qt.MouseButton.LeftButton:
            self.setDown(False)
            for index,cell in enumerate(self.image_rects()):
                if cell.contains(event.position()):self.imageActivated.emit(index);break
            event.accept();return
        super().mouseReleaseEvent(event)
    def fit_picture(self):
        if not self.picture.isNull():
            self.setIconSize(self.picture.size().scaled(self.size()-QSize(12,12),Qt.AspectRatioMode.KeepAspectRatio))
    def resizeEvent(self,event): super().resizeEvent(event); self.fit_picture()


class CanvasResults(QFrame):
    def __init__(self,window,key='__result_preview__'):
        super().__init__(); self.window=window; self.record=None; self.ids=[]; self.loading=False; self.image_key=None; self.input_active=False; self.input_record=None;self.input_signature=None; self.input_records=[]
        self.key=key
        self.adapter=None;self.thumb_adapter=None;self._thumb_items={};self._thumb_relatives={}
        self._picture_paths=[];self._preview_sources=[];self._single=False
        self.setObjectName('CanvasModuleBody'); layout=QVBoxLayout(self); layout.setContentsMargins(4,8,4,4); layout.setSpacing(10)
        self.preview=ResultImage(); self.preview.clicked.connect(self.show_image); self.preview.imageActivated.connect(self.show_image); layout.addWidget(self.preview,1)
        self.images=QListWidget(self); self.images.hide(); self.images.currentItemChanged.connect(self.select)
        self.images.setViewMode(QListWidget.ViewMode.IconMode)
        self.images.setFlow(QListWidget.Flow.LeftToRight); self.images.setWrapping(False)
        self.images.setMovement(QListWidget.Movement.Static)
        self.images.setIconSize(QSize(72,56)); self.images.setGridSize(QSize(84,72))
        self.images.setFixedHeight(92); self.images.setMinimumWidth(0)
        self.images.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.images.setAccessibleName('生成圖片選擇')
        layout.addWidget(self.images)
        self.destination=ComboBox(); self.destination.setMinimumWidth(100); self.destination.currentIndexChanged.connect(self.change_destination)
        self.choose_folder=button('資料夾…',self.choose_directory,'Quiet'); layout.addLayout(row(self.destination,self.choose_folder))
        self.save_button=button('儲存圖片',self.save,'Primary')
        from .image_source_action import ACTION
        self.reuse=button(ACTION if window.state.get('multi_output',{}).get('version',0)>=7 else '導入圖生圖',self.use_source,'Quiet')
        self.recent_button=button('最近生成',lambda:self.window.show_page(self.window.recent),'Quiet'); layout.addLayout(row(self.save_button,self.reuse,self.recent_button))
        self.feedback=label('','Subtle',True); layout.addWidget(self.feedback); self.feedback.hide()
        fetch=getattr(getattr(getattr(self.window,'comfy',None),'events',None),'results',None)
        if fetch is not None:fetch.repaired.connect(self.result_repaired)
        self.refresh()

    def cancel_pictures(self):
        if self.adapter is not None:self.adapter.load([])

    def refresh_preview_owner(self):
        if self.adapter is None and self.thumb_adapter is None:return
        from .preview_loader import scope
        current=scope(self.window,self.window.store)
        if any(adapter is not None and adapter.current_scope!=current
               for adapter in (self.adapter,self.thumb_adapter)):
            self.cancel_pictures();self.clear_thumbs()
            self.image_key=None;self.input_signature=None;self.ids=[]
            self._picture_paths=[];self.preview.set_picture(QPixmap())

    def schedule_thumbs(self,specs):
        if not specs:return
        if self.thumb_adapter is None:
            from .preview_loader import PreviewAdapter
            self.thumb_adapter=PreviewAdapter(self,self.window,self.window.store,'legacy')
            self.thumb_adapter.ready.connect(self.thumb_ready)
        self.thumb_adapter.load(specs)

    def clear_thumbs(self):
        if self.thumb_adapter is not None:self.thumb_adapter.load([])
        self._thumb_items={};self._thumb_relatives={}

    def thumb_ready(self,index,image,status):
        item=self._thumb_items.get(index)
        if item is not None:item.setIcon(QIcon(QPixmap.fromImage(image)))
        relative=self._thumb_relatives.get(index,'')
        repair=getattr(self.window.store,'result_repair',None)
        if repair and relative.startswith('thumbnails/results/'):repair(relative,failed=image.isNull())

    def schedule_pictures(self,specs,records,sources=(),single=False):
        self._picture_paths=[r['path'] for r in records]
        self._preview_sources=list(sources);self._single=single
        self.preview.pending={s[0] for s in specs}
        if not specs:return
        if self.adapter is None:
            from .preview_loader import PreviewAdapter
            self.adapter=PreviewAdapter(self,self.window,self.window.store,'result')
            self.adapter.ready.connect(self.picture_ready)
        self.adapter.load(specs)

    def picture_ready(self,index,image,status):
        picture=QPixmap.fromImage(image)
        if self._single:
            self.preview.set_picture(picture);self.preview.setText('' if not picture.isNull() else '圖片無法讀取')
        elif index<len(self.preview.pictures):
            first=all(p.isNull() for p in self.preview.pictures)
            self.preview.pictures[index]=picture;self.preview.pending.discard(index)
            if first or not self.preview.pending:
                self.preview.apply_timer.stop();self.preview.update()
            elif not self.preview.apply_timer.isActive():self.preview.apply_timer.start(16)
        if picture.isNull() and index<len(self._preview_sources):
            fetch=getattr(getattr(self.window.comfy,'events',None),'results',None)
            if fetch is not None:fetch.request_repair(self._preview_sources[index]['relative'],failed=True)

    def result_repaired(self, relative):
        path=str(self.window.store.directory/relative)
        if any(record['path']==path for record in self.input_records):
            self.image_key=None;self.refresh()

    def refresh(self):
        if self.key=='__result_preview__':
            for key,card in getattr(getattr(self.window,'canvas',None),'preview_cards',{}).items():
                if key!=self.key:card.panel.refresh()
        if self.refresh_input():return
        # Canvas v4 displays only its image input, never the global catalog.
        if self.window.state.get('multi_output',{}).get('version',1)>=4:
            self.cancel_pictures();self.clear_thumbs()
            self.record=None; self.input_record=None; self.image_key=None; self.input_signature=None; self.ids=[]
            self.images.blockSignals(True); self.images.clear(); self.images.blockSignals(False)
            for widget in (self.images,self.destination,self.choose_folder,self.save_button,self.reuse,self.feedback):widget.hide()
            self.preview.set_picture(QPixmap()); self.preview.setText('請連接圖片來源'); self.preview.setEnabled(False)
            self.recent_button.setEnabled(True)
            return
        rows=[r for r in self.window.catalog.rows('recent',limit=120) if Path(r['path']).is_file()]
        if self.window.state.get('multi_output',{}).get('version',1)>=2:
            from .multi_output import connected_outputs
            outputs=connected_outputs(self.window.state,'preview')
            if not hasattr(self,'route_cache'): self.route_cache={}
            self.route_cache={k:v for k,v in self.route_cache.items() if any(r['id']==k for r in rows)}
            selected=[]
            for r in rows if outputs else []:
                if r['id'] not in self.route_cache:
                    record=self.window.catalog.get(r['id']); metadata=record.get('metadata',{}); envelope=metadata.get('raw',{}).get('prompt_studio',{})
                    texts=envelope.get('texts',[]) if isinstance(envelope,dict) else []
                    routed={b['output'] for b in texts if isinstance(b,dict) and isinstance(b.get('output'),str)} if isinstance(texts,list) else set()
                    if not routed:
                        for binding in image_snapshots(metadata):
                            state=binding['snapshot']['state']; data=state.get('multi_output',{})
                            if not data: routed.add('__text_output__')
                            if data.get('version',1)>=3:
                                # Route only from the recorded snapshot, never today's text.
                                from .clip_flow import source
                                routed.update(source(state,b['clip']) for b in data['bindings'])
                            else: routed.update(b['output'] for b in data.get('bindings',[]) if b.get('output') in data.get('outputs',{}))
                    self.route_cache[r['id']]=routed
                if outputs & self.route_cache[r['id']]: selected.append(r)
            rows=selected
            self.recent_button.setEnabled(bool(outputs))
        ids=[r['id'] for r in rows]
        prior=self.record['id'] if self.record else self.window.state.get('canvas_result_id')
        follow=not prior or (self.ids and prior==self.ids[0])
        if ids!=self.ids:
            self.loading=True;self.clear_thumbs();thumbs=[];self.images.clear()
            paths=[preview_path(self.window.store,record.get('thumb','')) for record in rows]
            deferred=worker_batch(paths)
            for index,record in enumerate(rows):
                item=QListWidgetItem('')
                path=paths[index]
                if index in deferred:
                    self._thumb_items[index]=item;self._thumb_relatives[index]=record.get('thumb','')
                    thumbs.append((index,path,'',72,72,'legacy-thumb'))
                else:item.setIcon(thumb_icon(self.window.store,record.get('thumb',''),72))
                item.setToolTip(record['name']); item.setData(Qt.ItemDataRole.UserRole,record['id']); self.images.addItem(item)
            self.schedule_thumbs(thumbs)
            selected=0 if follow or prior not in ids else ids.index(prior)
            self.images.setCurrentRow(selected); self.ids=ids; self.loading=False
        self.images.setVisible(len(ids)>1)
        self.select(self.images.currentItem()); self.refresh_destination()
        if self.window.state.get('multi_output',{}).get('version',1)>=2 and not outputs: self.preview.setText('尚未連接 Prompt')

    def refresh_input(self):
        if self.window.state.get('multi_output',{}).get('version',0)>=7:return self.refresh_stage_input()
        self.refresh_preview_owner()
        data=self.window.state.get('multi_output',{})
        line=next((c for c in data.get('connections',[]) if c['destination']==self.key and c['kind']=='image'),None)
        was_input=self.input_active;self.input_active=bool(line); self.input_record=None; self.input_records=[]
        if not line:
            if was_input:self.ids=[];self.input_signature=None;self.image_key=None
            return False
        canvas=getattr(self.window,'canvas',None)
        if canvas is None:return True
        source=canvas.functions.current_source(line['source']) if line['source'] in self.window.state.get('canvas_functions',{}).get('images',{}) else None
        for widget in (self.images,self.destination,self.choose_folder,self.save_button,self.reuse,self.feedback):widget.hide()
        collection=self.window.comfy.images.collection(line['source'])
        signature=(line['source'],collection['collection']) if collection else None
        self.images.blockSignals(True)
        if signature!=self.input_signature or (was_input is False):
            self.clear_thumbs();thumbs=[];self.images.clear();self.ids=[];self.input_signature=signature
            sources=collection['items'] if collection else []
            deferred=worker_batch([self.window.store.directory/item['relative'] for item in sources],sources)
            for index,item_source in enumerate(collection['items'] if collection else []):
                item=QListWidgetItem('');item.setToolTip(item_source['name'])
                path=str(self.window.store.directory/item_source['relative'])
                if index in deferred:
                    self._thumb_items[index]=item
                    thumbs.append((index,path,item_source['sha256'],72,56,'legacy-icon'))
                else:
                    reader=QImageReader(path);size=reader.size()
                    if size.isValid():size.scale(72,56,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
                    item.setIcon(QIcon(QPixmap.fromImage(reader.read())))
                item.setData(Qt.ItemDataRole.UserRole,dict(key=line['source'],collection=collection['collection'],index=index));self.images.addItem(item)
            self.schedule_thumbs(thumbs)
        selected=next((i for i,v in enumerate(collection['items']) if source and v['reference']['image']==source.get('reference',{}).get('image')),-1) if collection else -1
        self.images.setCurrentRow(selected);self.images.blockSignals(False)
        self.recent_button.setEnabled(True)
        if collection and len(collection['items'])>1:
            self.input_records=[dict(path=str(self.window.store.directory/item['relative']),name=item['name']) for item in collection['items']]
            key=('batch',line['source'],collection['collection'],tuple(item['sha256'] for item in collection['items']))
            self.preview.setEnabled(True);self.preview.setStyleSheet('')
            if self.image_key!=key:
                self.cancel_pictures();self.image_key=key; pictures=[];specs=[]
                # Keep total decoded preview pixels bounded even for 64 images.
                limit=min(1536,int((4_000_000/len(self.input_records))**.5))
                deferred=worker_batch([r['path'] for r in self.input_records],collection['items'])
                for index,record in enumerate(self.input_records):
                    if index in deferred:
                        pictures.append(QPixmap());specs.append((index,record['path'],'',limit,limit,'shrink'));continue
                    reader=QImageReader(record['path']);reader.setAutoTransform(True);reader.setAllocationLimit(128);size=reader.size()
                    if size.isValid() and max(size.width(),size.height())>limit:size.scale(limit,limit,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
                    pictures.append(QPixmap.fromImage(reader.read()))
                self.preview.set_pictures(pictures)
                self.schedule_pictures(specs,self.input_records)
                self.preview.setAccessibleName(str(len(pictures))+' 張圖片，依輸出順序排列；點選可放大')
            return True
        key=source['sha256'] if source else None
        if source:self.input_record=dict(path=str(self.window.store.directory/source['relative']),name=source['name'])
        picture=canvas.image_previews.get(line['source']) if hasattr(canvas,'image_previews') else None
        self.preview.setEnabled(bool(source)); self.preview.setStyleSheet('')
        if key!=self.image_key or key is None:
            self.cancel_pictures()
            self.image_key=key; self.preview.set_picture(QPixmap()); self.preview.setText('請從下方選擇圖片' if collection and len(collection['items'])>1 else '尚未載入圖片')
            if source:
                if worker_batch([self.input_record['path']],[source]):
                    self.preview.setText('')
                    self.schedule_pictures([(0,self.input_record['path'],key,2048,2048,'fit')],[self.input_record],single=True)
                    return True
                reader=QImageReader(self.input_record['path']); reader.setAutoTransform(True); size=reader.size()
                if size.isValid():size.scale(2048,2048,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size)
                pixmap=QPixmap.fromImage(reader.read()); self.preview.set_picture(pixmap); self.preview.setText('' if not pixmap.isNull() else '圖片無法讀取')
            elif not collection and picture is not None and not picture.isNull():self.preview.set_picture(QPixmap.fromImage(picture)); self.preview.setText('')
        return True

    def refresh_stage_input(self):
        self.refresh_preview_owner()
        data=self.window.state['multi_output'];self.recent_button.hide()
        for widget in (self.images,self.destination,self.choose_folder,self.save_button,self.reuse,self.feedback):widget.hide()
        line=next((c for c in data['connections'] if c['destination']==self.key and c['kind']=='image'),None)
        self.input_active=True;previous_records=self.input_records;self.input_records=[];self.input_record=None
        if not line:self.cancel_pictures();self.preview.set_picture(QPixmap());self.preview.setText('');self.preview.setEnabled(False);self.image_key=None;return True
        key=line['source'];images=self.window.comfy.images
        runner=getattr(getattr(self.window.comfy,'input_flow',None),'chain',None)
        fetch=getattr(getattr(self.window.comfy,'events',None),'results',None)
        candidate=fetch.preview(key) if fetch is not None else None
        if fetch is not None and fetch.awaiting(key):
            self.feedback.setText('結果待確認');self.feedback.show()
        if candidate:
            items=candidate['items']
            signature=('candidates',tuple((i['sha256'],str(i['reference'])) for i in items))
            self.input_records=[dict(path=str(self.window.store.directory/i['relative']),name=i['name']) for i in items]
            if self.image_key!=signature:
                self.cancel_pictures()
                self.preview.set_pictures([QPixmap.fromImage(image) for image in candidate['pictures']])
                self.image_key=signature
            self.input_record=self.input_records[0];self.preview.setEnabled(True)
            self.feedback.setText(candidate['status']);self.feedback.show()
            return True
        if runner is not None:
            from .preview_scope import preview_images
            collection=dict(items=preview_images(self.window.state,runner.preview_results,key));source=None
        else:collection=images.collection(key);source=images.source(key)
        # Filter before creating image readers. Historical and downstream
        # collections remain complete; only this display uses the latest scope.
        items=current_iteration(collection['items'] if collection else [source] if source else [])
        signature=tuple((i['sha256'],str(i.get('reference')),i['relative'],i['name']) for i in items)
        directory=str(self.window.store.directory)
        self.input_records=(previous_records if self.image_key==signature else
                            [dict(path=normpath(join_path(directory,i['relative'])),name=i['name']) for i in items])
        if items:
            self.preview.setEnabled(True)
            if self.image_key!=signature:
                self.cancel_pictures();specs=[]
                pictures=[];limit=min(1536,int((4_000_000/max(1,len(items)))**.5))
                # D-B publishes while the accepted attempt still belongs to
                # fetch.pending. Preserve its old synchronous display path;
                # reopening history (no matching live attempt) uses the batch
                # policy. This never changes fetch IO, receipts or scheme C.
                live=live_result(items,getattr(fetch,'pending',{}))
                deferred=(set() if live else
                          worker_batch([r['path'] for r in self.input_records],items))
                for item,record in zip(items,self.input_records):
                    if deferred and len(pictures) in deferred:
                        index=len(pictures)
                        previous=self.preview.pictures
                        pictures.append(previous[index] if index<len(previous) and index<len(self._picture_paths) and self._picture_paths[index]==record['path'] else QPixmap())
                        specs.append((index,record['path'],item['sha256'],limit,limit,'fit',decoded_bytes(item)));continue
                    reader=QImageReader(record['path']);reader.setAutoTransform(True);reader.setAllocationLimit(128)
                    # Accepted D receipts already contain the validated raw
                    # header dimensions. Keep the same reader/transform and
                    # pixels, without a second GUI header query competing with
                    # the fetch pool. Unclassified legacy sources still ask
                    # their reader exactly as before.
                    size=QSize(item['width'],item['height']) if live and decoded_bytes(item) else reader.size()
                    if size.isValid():size.scale(limit,limit,Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
                    picture=QPixmap.fromImage(reader.read());pictures.append(picture)
                    if picture.isNull() and fetch is not None:fetch.request_repair(item['relative'],failed=True)
                self.preview.set_pictures(pictures);self.image_key=signature
                self._picture_paths=[record['path'] for record in self.input_records]
                if specs:self.schedule_pictures(specs,self.input_records,items)
            self.input_record=self.input_records[0]
        else:
            canvas=getattr(self.window,'canvas',None);picture=canvas.image_previews.get(key) if canvas else None
            empty_key=picture.cacheKey() if picture is not None and not picture.isNull() else 0
            if (self.image_key is None and not self.preview.pictures and not self.preview.isEnabled() and
                    getattr(self,'_empty_picture_key',None)==empty_key):return True
            self._empty_picture_key=empty_key;self.cancel_pictures()
            self.image_key=None;self.preview.set_picture(QPixmap.fromImage(picture) if picture is not None and not picture.isNull() else QPixmap())
            self.preview.setText('');self.preview.setEnabled(False)
        return True

    def refresh_destination(self):
        self.destination.blockSignals(True); self.destination.clear()
        picker=self.window.recent.destination_picker
        for index in range(picker.count()): self.destination.addItem(picker.itemText(index),picker.itemData(index))
        self.destination.setCurrentIndex(picker.currentIndex()); self.destination.setToolTip(picker.toolTip()); self.destination.blockSignals(False)
        self.update_save()

    def change_destination(self):
        picker=self.window.recent.destination_picker; index=picker.findData(self.destination.currentData())
        if index>=0: picker.setCurrentIndex(index)
        self.update_save()

    def choose_directory(self): self.window.recent.choose_directory(); self.refresh_destination()

    def select(self,item,*_):
        if self.loading: return
        if self.input_active:
            value=item.data(Qt.ItemDataRole.UserRole) if item else None
            if isinstance(value,dict):self.show_image(value['index'])
            return
        self.record=self.window.catalog.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if self.record:
            self.window.state['canvas_result_id']=self.record['id']
        for widget in (self.destination,self.choose_folder,self.save_button,self.reuse): widget.setVisible(bool(self.record))
        self.preview.setEnabled(bool(self.record))
        self.preview.setStyleSheet('' if self.record else 'QPushButton#ResultPreview {background:transparent; border:0;}')
        self.update_image(); self.update_save()

    def update_image(self):
        if self.input_active:self.refresh_input();return
        key=self.record['id'] if self.record else None
        if key==self.image_key: return
        self.cancel_pictures()
        self.image_key=key; self.preview.set_picture(QPixmap()); self.preview.setText('尚無圖片')
        if not self.record: return
        if worker_batch([self.record['path']]):
            self.preview.setText('')
            self.schedule_pictures([(0,self.record['path'],'',2048,2048,'fit')],[self.record],single=True)
            return
        reader=QImageReader(self.record['path']); reader.setAutoTransform(True); target=reader.size()
        if not target.isValid(): self.preview.setText('原圖已移動或暫存已清除'); return
        target.scale(2048,2048,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(target)
        pixmap=QPixmap.fromImage(reader.read())
        if pixmap.isNull(): self.preview.setText('圖片無法讀取'); return
        self.preview.setText(''); self.preview.set_picture(pixmap)

    def resizeEvent(self,event): super().resizeEvent(event); self.update_image()

    def update_save(self):
        if self.input_active:return
        recent=self.window.recent; record=self.record
        self.save_button.setText('儲存圖片'); self.feedback.hide()
        self.reuse.setEnabled(bool(record)); self.save_button.setEnabled(bool(record and self.window.comfy.connected))
        if not record: return
        if record['id'] in recent.collecting: self.save_button.setText('正在儲存…'); self.save_button.setEnabled(False); return
        try: destination,_=recent.destination()
        except (ValueError,OSError): return
        saved=record.get('collected',{}).get(destination)
        if saved and Path(saved['path']).is_file():
            self.save_button.setText('✓ 已儲存'); self.save_button.setEnabled(False); self.feedback.setText(saved['path']); self.feedback.show()

    def save(self):
        if not self.record: return
        record=dict(self.record)  # Result identity remains fixed while choosing a folder.
        try: self.window.recent.destination()
        except (ValueError,OSError):
            self.choose_directory()
            try: self.window.recent.destination()
            except (ValueError,OSError): return
        self.window.recent.collect(record); self.update_save()

    def use_source(self):
        if not self.record: return
        data=self.window.state.get('multi_output',{}); current=data.get('current_output')
        routed=getattr(self,'route_cache',{}).get(self.record['id'],set()) & set(data.get('outputs',{}))
        output=current if current in routed or not routed else next(key for key in data['outputs'] if key in routed)
        self.window.use_image_for_generation(self.record['path'],output)

    def show_image(self,index=None):
        record=self.input_records[index] if type(index) is int and 0<=index<len(self.input_records) else self.input_record if self.input_active else self.record
        if not record: return
        from .image_viewer import show_image
        show_image(self.window,record['path'],record['name'])

    def show_metadata(self):
        if not self.record: return
        record=dict(self.record); dialog=StudioDialog(self.window); dialog.setWindowTitle('這張圖片的生成資訊'); dialog.resize(760,650)
        text=QPlainTextEdit(readable_metadata(record)); text.setReadOnly(True); dialog.body.addWidget(text,1)
        def restore(): dialog.accept(); self.window.gallery.restore_combination(record)
        restore_button=button('載入模組組合…',restore,'Quiet'); restore_button.setEnabled(bool(image_snapshots(record.get('metadata',{}))))
        dialog.body.addLayout(row(restore_button,None,button('關閉',dialog.reject))); dialog.exec()
