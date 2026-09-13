"""Canvas-local viewing and collection over the existing recent-result catalog."""
from pathlib import Path
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QImageReader,QPixmap,QIcon
from PySide6.QtWidgets import QFrame,QVBoxLayout,QListWidget,QListWidgetItem,QPlainTextEdit,QPushButton,QSizePolicy
from .widgets import label,button,row,ComboBox,StudioDialog,dialog_buttons,thumb_icon,ElidedLabel
from .metadata_view import readable_metadata
from .snapshots import image_snapshots


class ResultImage(QPushButton):
    """The picture follows the space allocated by the graphics card."""
    def __init__(self):
        super().__init__('尚無圖片'); self.picture=QPixmap()
        self.setObjectName('ResultPreview'); self.setMinimumSize(160,120)
        self.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Expanding)
    def sizeHint(self): return QSize(280,320)
    def set_picture(self,picture):
        self.picture=picture; self.setIcon(QIcon(picture)); self.fit_picture()
    def fit_picture(self):
        if not self.picture.isNull():
            self.setIconSize(self.picture.size().scaled(self.size()-QSize(12,12),Qt.AspectRatioMode.KeepAspectRatio))
    def resizeEvent(self,event): super().resizeEvent(event); self.fit_picture()


class CanvasResults(QFrame):
    def __init__(self,window):
        super().__init__(); self.window=window; self.record=None; self.ids=[]; self.loading=False; self.image_key=None
        self.setObjectName('InsetPanel'); layout=QVBoxLayout(self); layout.setContentsMargins(16,16,16,16); layout.setSpacing(10)
        self.preview=ResultImage(); self.preview.clicked.connect(self.show_image); layout.addWidget(self.preview,1)
        # Retain a small selection model for restoring a chosen result; the gallery
        # itself lives in the floating Recent page, not inside this image card.
        self.images=QListWidget(self); self.images.hide(); self.images.currentItemChanged.connect(self.select)
        self.destination=ComboBox(); self.destination.setMinimumWidth(100); self.destination.currentIndexChanged.connect(self.change_destination)
        self.choose_folder=button('資料夾…',self.choose_directory,'Quiet'); layout.addLayout(row(self.destination,self.choose_folder))
        self.save_button=button('儲存圖片',self.save,'Primary')
        self.reuse=button('導入圖生圖',self.use_source,'Quiet')
        self.recent_button=button('最近生成',lambda:self.window.show_page(self.window.recent),'Quiet'); layout.addLayout(row(self.save_button,self.reuse,self.recent_button))
        self.feedback=label('','Subtle',True); layout.addWidget(self.feedback); self.feedback.hide()
        self.refresh()

    def refresh(self):
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
                            routed.update(b['output'] for b in data.get('bindings',[]) if b.get('output') in data.get('outputs',{}))
                    self.route_cache[r['id']]=routed
                if outputs & self.route_cache[r['id']]: selected.append(r)
            rows=selected
            self.recent_button.setEnabled(bool(outputs))
        ids=[r['id'] for r in rows]
        prior=self.record['id'] if self.record else self.window.state.get('canvas_result_id')
        follow=not prior or (self.ids and prior==self.ids[0])
        if ids!=self.ids:
            self.loading=True; self.images.clear()
            for record in rows:
                item=QListWidgetItem('')
                item.setToolTip(record['name']); item.setData(Qt.ItemDataRole.UserRole,record['id']); self.images.addItem(item)
            selected=0 if follow or prior not in ids else ids.index(prior)
            self.images.setCurrentRow(selected); self.ids=ids; self.loading=False
        self.select(self.images.currentItem()); self.refresh_destination()
        if self.window.state.get('multi_output',{}).get('version',1)>=2 and not outputs: self.preview.setText('尚未連接 Prompt')

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
        self.record=self.window.catalog.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
        if self.record:
            self.window.state['canvas_result_id']=self.record['id']
        for widget in (self.destination,self.choose_folder,self.save_button,self.reuse): widget.setVisible(bool(self.record))
        self.preview.setEnabled(bool(self.record))
        self.preview.setStyleSheet('' if self.record else 'QPushButton#ResultPreview {background:transparent; border:0; color:#929ba9;}')
        self.update_image(); self.update_save()

    def update_image(self):
        key=self.record['id'] if self.record else None
        if key==self.image_key: return
        self.image_key=key; self.preview.set_picture(QPixmap()); self.preview.setText('尚無圖片')
        if not self.record: return
        reader=QImageReader(self.record['path']); reader.setAutoTransform(True); target=reader.size()
        if not target.isValid(): self.preview.setText('原圖已移動或暫存已清除'); return
        target.scale(2048,2048,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(target)
        pixmap=QPixmap.fromImage(reader.read())
        if pixmap.isNull(): self.preview.setText('圖片無法讀取'); return
        self.preview.setText(''); self.preview.set_picture(pixmap)

    def resizeEvent(self,event): super().resizeEvent(event); self.update_image()

    def update_save(self):
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

    def show_image(self):
        if not self.record: return
        dialog=StudioDialog(self.window); dialog.setWindowTitle(self.record['name']); dialog.resize(980,780)
        display=label(''); display.setAlignment(Qt.AlignmentFlag.AlignCenter)
        reader=QImageReader(self.record['path']); reader.setAutoTransform(True); size=reader.size()
        if not size.isValid(): self.window.notice('原圖已移動或暫存已清除。'); return
        size.scale(900,620,Qt.AspectRatioMode.KeepAspectRatio); reader.setScaledSize(size); display.setPixmap(QPixmap.fromImage(reader.read()))
        dialog.body.addWidget(display,1); dialog.body.addWidget(dialog_buttons(dialog)); dialog.exec()

    def show_metadata(self):
        if not self.record: return
        record=dict(self.record); dialog=StudioDialog(self.window); dialog.setWindowTitle('這張圖片的生成資訊'); dialog.resize(760,650)
        text=QPlainTextEdit(readable_metadata(record)); text.setReadOnly(True); dialog.body.addWidget(text,1)
        def restore(): dialog.accept(); self.window.gallery.restore_combination(record)
        restore_button=button('載入模組組合…',restore,'Quiet'); restore_button.setEnabled(bool(image_snapshots(record.get('metadata',{}))))
        dialog.body.addLayout(row(restore_button,None,button('關閉',dialog.reject))); dialog.exec()
