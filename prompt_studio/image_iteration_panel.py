"""Explicit single-workflow image iteration over the existing work queue."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt, QSize
from PySide6.QtGui import QImageReader, QPixmap
from PySide6.QtWidgets import QFileDialog, QListWidget, QPlainTextEdit, QFormLayout, QLabel, QWidget, QVBoxLayout
from .widgets import StudioDialog, ComboBox, button, row, label, scrolling
from .generation import text_fields, image_target
from .image_bindings import import_source
from .image_iteration import prompt_choices
from .pnginfo import png_metadata


class ImageIterationDialog(StudioDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window=window;self.sources=[]
        self.setWindowTitle('圖片逐張加入佇列');self.resize(840,760)
        surface=QWidget();content=QVBoxLayout(surface);content.setContentsMargins(0,0,8,0);content.setSpacing(16)
        self.body.addWidget(scrolling(surface),1)
        content.addWidget(label('固定這份清單後，每張完成才輪到下一張；純圖片工作流不需要 CLIP。','Subtle',True))
        form=QFormLayout();content.addLayout(form)
        self.workflow=ComboBox();self.workflow.addItem('選擇本次工作流',None)
        for profile in window.state.get('generation',{}).get('profiles',[]):
            if profile.get('frontend_id'):
                self.workflow.addItem(profile['name'],copy.deepcopy(profile))
        self.input=ComboBox();self.target=ComboBox()
        form.addRow('執行工作流',self.workflow);form.addRow('接收圖片節點',self.input)
        content.addLayout(row(button('加入圖片…',self.choose_files,'Quiet'),button('加入資料夾…',self.choose_folder,'Quiet'),
                                button('清空清單',self.clear,'Quiet'),None))
        self.collection=ComboBox();self.collection.addItem('選擇已取得的圖片集合',None)
        for key in window.state.get('canvas_functions',{}).get('images',{}):
            collection=window.comfy.images.collection(key)
            if collection:
                self.collection.addItem(f"{key} · {len(collection['items'])} 張",copy.deepcopy(collection))
        content.addLayout(row(self.collection,button('加入這組圖片',self.add_collection,'Quiet')))
        self.list=QListWidget();self.list.setMinimumHeight(120);content.addWidget(self.list,1)
        self.preview=QLabel();self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter);self.preview.setFixedHeight(130)
        content.addWidget(self.preview)
        self.scope=ComboBox();self.scope.addItem('全部圖片，依清單順序','all');self.scope.addItem('只執行明確選定的一張','one')
        self.single=ComboBox()
        content.addLayout(row(button('上一張預覽',lambda:self.turn(-1),'Quiet'),button('下一張預覽',lambda:self.turn(1),'Quiet'),None))
        content.addLayout(row(self.scope,self.single))
        self.mode=ComboBox()
        self.mode.addItem('純圖片，不帶提示詞','none');self.mode.addItem('圖片內嵌 Prompt','metadata');self.mode.addItem('整批固定 Prompt','fixed')
        self.source=ComboBox();self.fixed=QPlainTextEdit();self.fixed.setMaximumHeight(74)
        details=QFormLayout();details.addRow('Prompt 來源',self.mode);details.addRow('圖片內的來源欄位',self.source)
        details.addRow('目標文字欄位',self.target);details.addRow('整批固定文字',self.fixed);content.addLayout(details)
        self.note=label('請在 ComfyUI 網頁開啟所選工作流，再加入佇列。','Subtle',True);self.body.addWidget(self.note)
        self.body.addLayout(row(None,button('保存這批工作',self.enqueue),button('關閉',self.reject,'Quiet')))
        self.workflow.currentIndexChanged.connect(self.workflow_changed)
        self.list.currentRowChanged.connect(self.preview_image)
        self.mode.currentIndexChanged.connect(self.controls)
        self.scope.currentIndexChanged.connect(self.controls)
        self.controls()

    def controls(self):
        mode=self.mode.currentData()
        self.source.setEnabled(mode=='metadata');self.fixed.setEnabled(mode=='fixed');self.target.setEnabled(mode!='none')
        self.single.setEnabled(self.scope.currentData()=='one')

    def workflow_changed(self):
        profile=self.workflow.currentData()
        self.input.clear();self.target.clear()
        if not profile:return
        for key,node in profile['graph'].items():
            if node['class_type']=='LoadImage' and isinstance(node['inputs'].get('image'),str):self.input.addItem('#'+key,key)
        for node,field in text_fields(profile['graph']):self.target.addItem('#'+node+' / '+field,[node,field])
        index=self.input.findData(image_target(profile));self.input.setCurrentIndex(max(0,index))
        self.mode.setCurrentIndex(1 if self.target.count() else 0)
        self.controls()

    def add_paths(self, paths):
        try:
            if len(paths)+len(self.sources)>100:raise ValueError('一次最多 100 張圖片。')
            imported=[import_source(path,self.window.store.directory) for path in paths]
            self.sources.extend(imported);self.refresh_sources()
        except (OSError,ValueError) as exc:self.note.setText(str(exc))

    def choose_files(self):
        paths,_=QFileDialog.getOpenFileNames(self,'選擇圖片','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if paths:self.add_paths(paths)

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'選擇圖片資料夾')
        if folder:
            paths=sorted((p for p in Path(folder).iterdir() if p.is_file() and p.suffix.lower() in ('.png','.jpg','.jpeg','.webp','.bmp')),key=lambda p:p.name.casefold())
            self.add_paths(paths)

    def add_collection(self):
        collection=self.collection.currentData()
        if not collection:return
        if len(self.sources)+len(collection['items'])>100:self.note.setText('一次最多 100 張圖片。');return
        self.sources.extend(copy.deepcopy(collection['items']));self.refresh_sources()

    def clear(self):
        self.sources=[];self.refresh_sources()

    def refresh_sources(self):
        self.list.clear();self.single.clear();self.source.clear()
        for index,source in enumerate(self.sources):
            self.list.addItem(f"{index+1}. {source['name']}");self.single.addItem(str(index+1)+' · '+source['name'],index)
        if self.sources:
            for name,value in prompt_choices(png_metadata(self.window.store.directory/self.sources[0]['relative'])):
                self.source.addItem(name,list(value))
            self.list.setCurrentRow(0)
        else:self.preview.clear()

    def turn(self, step):
        if self.sources:self.list.setCurrentRow((self.list.currentRow()+step)%len(self.sources))

    def preview_image(self, index):
        if not 0<=index<len(self.sources):return
        reader=QImageReader(str(self.window.store.directory/self.sources[index]['relative']))
        size=reader.size();size.scale(QSize(300,126),Qt.AspectRatioMode.KeepAspectRatio);reader.setScaledSize(size)
        self.preview.setPixmap(QPixmap.fromImage(reader.read()))

    def enqueue(self):
        try:
            profile=copy.deepcopy(self.workflow.currentData())
            if not profile:raise ValueError('請選擇本次執行工作流。')
            profile['image']=self.input.currentData()
            if not profile['image']:raise ValueError('請選擇接收圖片的節點。')
            if self.target.count() and self.mode.currentData()=='none':
                raise ValueError('這份工作流含文字欄位，請明確選擇圖片 Prompt 或整批固定 Prompt。')
            sources=self.sources
            if self.scope.currentData()=='one':
                index=self.single.currentData()
                if index is None:raise ValueError('請明確選擇一張圖片。')
                sources=[sources[index]]
            self.window.comfy.queue.enqueue_images(profile,sources,self.mode.currentData(),self.target.currentData(),self.source.currentData(),self.fixed.toPlainText())
            self.accept()
        except (ValueError,OSError) as exc:self.note.setText(str(exc))
