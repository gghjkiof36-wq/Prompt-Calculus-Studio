"""Small settings controls shared by CivitAI search and asset management."""
from pathlib import Path
from PySide6.QtCore import Qt,QTimer
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLineEdit,QPlainTextEdit,QFileDialog,QSizePolicy
from .widgets import StudioDialog,label,button,row,ComboBox,scrolling
from .civitai import normalize_version,safe_filename,MODEL_EXTENSIONS
from .civitai_assets import categories,make_plan,suggested_target,LOCAL_TYPES

class FilterRow(QWidget):
    """Keep input widths readable by wrapping filters, without a second toolbar."""
    def __init__(self,widgets):
        super().__init__(); self.widgets=widgets; self.box=QVBoxLayout(self); self.box.setContentsMargins(0,0,0,0)
        self.rows=[]; self.columns=0; self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.reflow()
    def resizeEvent(self,event):
        super().resizeEvent(event); self.reflow()
    def reflow(self):
        cols=max(1,min(len(self.widgets),self.width()//180))
        height=max((max(w.minimumSizeHint().height(),w.sizeHint().height()) for w in self.widgets),default=40)
        rows=(len(self.widgets)+cols-1)//cols; self.setMinimumHeight(rows*height+max(0,rows-1)*self.box.spacing())
        if cols==self.columns:return
        self.columns=cols
        for layout in self.rows:
            while layout.count(): layout.takeAt(0)
            self.box.removeItem(layout); layout.deleteLater()
        self.rows=[]
        for index,widget in enumerate(self.widgets):
            if index%cols==0:
                layout=QHBoxLayout(); layout.setContentsMargins(0,0,0,0); self.box.addLayout(layout); self.rows.append(layout)
            layout.addWidget(widget,1)

def selectable(text='未提供'):
    result=label(text,'Subtle',True); result.setTextFormat(Qt.TextFormat.PlainText)
    result.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    result.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
    return result

def details(source):
    stats=source.get('stats') or {}
    counts=[f'{title} {stats[key]:,}' for key,title in [('downloadCount','下載量'),('generationCount','生成使用量')] if isinstance(stats.get(key),(int,float))]
    reviews=[]
    if isinstance(stats.get('rating'),(int,float)): reviews.append(f"評分 {stats['rating']:g}")
    for key,title in [('ratingCount','評分數'),('thumbsUpCount','喜歡'),('thumbsDownCount','不喜歡')]:
        if isinstance(stats.get(key),(int,float)): reviews.append(f'{title} {stats[key]:,}')
    hashes='\n'.join(f'{k}: {v}' for k,v in (source.get('hashes') or {}).items())
    if hashes and source.get('file_name'):hashes=source['file_name']+'\n'+hashes
    return {'Type':source.get('model_type'),'Stats':' · '.join(counts)+('（所選版本）' if counts else ''),
            'Reviews':' · '.join(reviews)+('（所選版本）' if reviews else ''),
            'Published':source.get('published_at'),'Base Model':source.get('base_model'),
            # Public REST endpoint does not supply strength recommendations.
            'Usage Tips':'','Hash':hashes,'AIR':source.get('air')}

class DownloadDialog(StudioDialog):
    def __init__(self,window,parent,version):
        super().__init__(window); self.window=window; self.parent_record=parent; self.version_record=version; self.plan=None
        self.setWindowTitle('下載模型'); self.resize(660,630)
        inner=QWidget(); form=QFormLayout(inner); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.body.addWidget(scrolling(inner),1)
        form.addRow('版本',selectable(version.get('name') or '未提供'))
        self.files=ComboBox()
        for file in version.get('files',[]):
            if isinstance(file,dict) and Path(file.get('name','')).suffix.lower() in MODEL_EXTENSIONS and file.get('downloadUrl'):
                self.files.addItem(file['name'],file)
        form.addRow('檔案',self.files); form.addRow('安裝到',selectable('ComfyUI'))
        form.addRow('Type',selectable(parent.get('type') or '未提供')); form.addRow('Base Model',selectable(version.get('baseModel') or '未提供'))
        self.local_kind=ComboBox(); self.local_kind.addItems(LOCAL_TYPES)
        kind,target=suggested_target(window.models.root.text(),parent.get('type')); self.local_kind.setCurrentText(kind)
        form.addRow('本機模型類型',self.local_kind)
        self.category=ComboBox(); self.category.addItems(categories(window.state['settings'],kind)); form.addRow('用途分類',self.category)
        self.local_kind.currentTextChanged.connect(self.update_categories)
        self.previous_suggestion=target
        self.location=QLineEdit(target); self.location.setPlaceholderText('選擇 ComfyUI 使用的實際模型資料夾')
        form.addRow('儲存位置',row(self.location,button('選擇…',self.choose_location)))
        self.filename=QLineEdit(); form.addRow('檔名',self.filename)
        self.size=selectable(); form.addRow('檔案大小',self.size)
        self.verification=QPlainTextEdit(); self.verification.setReadOnly(True); self.verification.setFixedHeight(90)
        form.addRow('校驗資訊',self.verification)
        self.feedback=selectable(''); self.body.addWidget(self.feedback)
        self.submit=button('下載並加入資產',self.confirm,'Primary')
        self.body.addLayout(row(None,button('取消',self.reject,'Quiet'),self.submit))
        self.files.currentIndexChanged.connect(self.refresh_file); self.refresh_file()
    def update_categories(self,kind):
        previous=self.category.currentText(); self.category.clear(); self.category.addItems(categories(self.window.state['settings'],kind))
        self.category.setCurrentText(previous)
    def refresh_file(self):
        file=self.files.currentData() or {}; self.filename.setText(file.get('name',''))
        kind,target=suggested_target(self.window.models.root.text(),'VAE' if file.get('type')=='VAE' else self.parent_record.get('type'))
        if self.location.text()==self.previous_suggestion:
            self.location.setText(target); self.local_kind.setCurrentText(kind)
        self.previous_suggestion=target
        size=file.get('sizeKB'); self.size.setText(f'{size/1024:,.1f} MiB' if type(size) in (int,float) else '未提供')
        digest=(file.get('hashes') or {}).get('SHA256')
        self.verification.setPlainText('完成後核對官方 SHA256\n'+digest if digest else '未提供官方 SHA256；將記錄本機 Hash，不宣稱官方校驗成功。')
        self.submit.setEnabled(bool(file))
    def choose_location(self):
        path=QFileDialog.getExistingDirectory(self,'模型安裝位置',self.location.text())
        if path:self.location.setText(path)
    def confirm(self):
        try:
            # Creation of a suggested standard folder occurs only after confirmation.
            target=Path(self.location.text()).expanduser(); root=self.window.models.root.text()
            _,suggestion=suggested_target(root,'VAE' if (self.files.currentData() or {}).get('type')=='VAE' else self.parent_record.get('type'))
            if suggestion and target==Path(suggestion) and target.parent.is_dir(): target.mkdir(exist_ok=True)
            self.plan=make_plan(self.parent_record,self.version_record,self.files.currentData(),target,
                               self.filename.text(),self.category.currentText(),root,self.local_kind.currentText())
        except (OSError,ValueError) as exc:self.feedback.setText(str(exc)); return
        self.accept()
