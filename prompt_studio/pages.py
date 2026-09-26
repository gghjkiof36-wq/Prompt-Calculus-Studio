import json
import time
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QTimer, QFile, QUrl, QEvent
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QPlainTextEdit, QFormLayout, QFileDialog,
    QCheckBox, QDialog, QSizePolicy, QFrame, QAbstractItemView, QApplication)
from .widgets import label, button, row, panel, image_path, preview_path, set_preview, ask, open_file, open_url, scrolling, thumb_icon, StudioDialog, InputDialog as QInputDialog, RoundMenu as QMenu, ComboBox as QComboBox
from .widgets import record_icon,set_record_preview
from .error_dialog import import_error,error_message
from .core import uid
from .media import scan_models, checked_model, copy_model, thumbnail, import_image, IMAGE_EXTENSIONS, model_root
from .civitai import CivitAIClient,identify_models
from .credentials import load_token
from .civitai_network import network_policy
from .civitai_controls import FilterRow,selectable
from .civitai_assets import categories,category_for,apply_categories,LOCAL_TYPES
from .metadata_view import readable_metadata
from .snapshots import image_snapshots, restore_snapshot
from .image_drop import ImageDropLabel
from .widgets import reveal_file


class ModelPage(QFrame):
    def __init__(self, window):
        super().__init__()
        self.window, self.catalog = window,window.catalog
        self.record = None
        self.loading = False
        self.page = 0
        self.setObjectName("WorkspaceSurface")
        layout = QVBoxLayout(self); layout.setContentsMargins(20,20,20,20); layout.setSpacing(16)
        self.root = QLineEdit(window.state["settings"]["model_root"]); self.root.setReadOnly(True)
        if self.root.text():
            try: self.root.setText(str(model_root(self.root.text())))
            except (ValueError,OSError): pass
        self.root.setPlaceholderText("選擇 ComfyUI 的 models 資料夾")
        layout.addLayout(row(self.root,button("選擇資料夾",self.choose_root),button("重新掃描",self.scan),button('CivitAI 搜尋',lambda:self.window.settings('civitai'),'Quiet')))
        layout.addWidget(label('請選擇 ComfyUI 的 models 根目錄；選到 loras 等分類資料夾時會自動使用其上層。','Subtle',True))
        split = QSplitter(); layout.addWidget(split,1)
        library, left = panel(); split.addWidget(library)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜尋名稱、檔名、觸發詞或備註")
        self.search.textChanged.connect(self.filter_changed)
        self.kind = QComboBox()
        for value in ['所有類型',*LOCAL_TYPES]: self.kind.addItem(value)
        self.kind.currentIndexChanged.connect(self.filter_changed)
        self.category_filter = QComboBox(); self.refresh_categories()
        self.category_filter.currentIndexChanged.connect(self.filter_changed)
        self.base_filter=QComboBox(); self.base_filter.addItem('所有 Base Model'); self.base_filter.currentIndexChanged.connect(self.filter_changed)
        left.addWidget(self.search); left.addWidget(FilterRow([self.kind,self.base_filter,self.category_filter]))
        self.list = QListWidget(); self.list.setIconSize(QSize(100,100))
        self.list.currentItemChanged.connect(self.select)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context)
        left.addWidget(self.list,1)
        self.count = label("尚未掃描","Subtle")
        left.addWidget(self.count)
        left.addLayout(row(button('匯入檔案…',self.import_file),button('辨識來源',self.identify_civitai),button('管理分類',self.manage_categories)))
        left.addLayout(row(button('清除缺失項目',self.clean_missing,'Quiet'),button('還原已清除',self.restore_missing,'Quiet'),None,button('上一頁',lambda:self.turn(-1)),button('下一頁',lambda:self.turn(1))))
        detail, right = panel("InsetPanel"); split.addWidget(scrolling(detail)); split.setSizes([680,450])
        self.detail=detail
        split.setChildrenCollapsible(False)
        self.preview = ImageDropLabel(window.store); self.preview.pathReady.connect(self.receive_image); self.preview.failed.connect(window.notice); self.preview.setMinimumHeight(200)
        right.addWidget(self.preview)
        right.addLayout(row(button("設定預覽圖",self.choose_image),button("移除預覽",self.clear_preview)))
        form = QFormLayout(); self.name = QLineEdit(); self.category = QComboBox()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.category.addItems(categories(window.state['settings']))
        self.trigger = QPlainTextEdit(); self.trigger.setMaximumHeight(85); self.trigger.setPlaceholderText("觸發詞與建議權重，可手動填寫")
        self.url = QLineEdit(); self.url.setPlaceholderText("https://civitai.com/models/…")
        self.base_model=label('—','Subtle',True); self.creator=label('—','Subtle',True); self.civitai_status=label('尚未辨識','Subtle',True)
        self.source_type=selectable(); self.source_version=selectable(); self.source_trigger=selectable()
        self.source_link=button('開啟 CivitAI 版本頁面',self.open_source_link,'Quiet')
        self.notes = QPlainTextEdit(); self.notes.setMaximumHeight(90)
        for title,widget in [('顯示名稱',self.name),('用途分類',self.category),('Type',self.source_type),('Base Model',self.base_model),('來源版本',self.source_version),('作者',self.creator),('CivitAI',self.civitai_status),('來源連結',self.source_link),('來源 Trigger',self.source_trigger),('觸發詞',self.trigger),('相關網址',self.url),('我的備註',self.notes)]: form.addRow(title,widget)
        right.addLayout(form)
        self.path = label("選擇左側模型即可編輯。","Subtle",True); self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Long unbroken filenames must wrap inside the detail pane, rather than
        # forcing the entire scroll content wider than its viewport.
        self.path.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        right.addWidget(self.path)
        right.addLayout(row(button("開啟網址",self.open_link),button("複製觸發詞",lambda:self.window.copy_text(self.trigger.toPlainText()))))
        right.addLayout(row(button("觸發詞加入組合",lambda:self.window.add_temporary(self.trigger.toPlainText())),button("顯示檔案位置",self.reveal)))
        right.addStretch()
        right.addWidget(button("將模型檔案移至資源回收筒…",self.recycle,"Danger"))
        self.saver = QTimer(self); self.saver.setSingleShot(True); self.saver.timeout.connect(self.save)
        for widget in (self.name,self.url,self.trigger,self.notes): widget.textChanged.connect(self.edit_changed)
        self.category.currentTextChanged.connect(self.edit_changed)
        self.detail_relayout=QTimer(self); self.detail_relayout.setSingleShot(True)
        self.detail_relayout.timeout.connect(self.refresh_detail_layout)

    def refresh_detail_layout(self):
        # A hidden tab can retain its form's old height-for-width geometry after
        # a font change. Refresh once after the final viewport width is assigned.
        if not self.isVisible(): return
        self.detail.layout().invalidate(); self.detail.layout().activate(); self.detail.updateGeometry()

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self,'detail_relayout'): self.detail_relayout.start(0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'detail_relayout'): self.detail_relayout.start(0)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.FontChange and hasattr(self,'detail_relayout'): self.detail_relayout.start(0)

    def edit_changed(self):
        if not self.loading and self.record: self.saver.start(600)

    def refresh_categories(self):
        current = self.category_filter.currentText() if hasattr(self,"category_filter") else ""
        self.category_filter.blockSignals(True); self.category_filter.clear(); self.category_filter.addItem("所有分類")
        self.category_filter.addItems(categories(self.window.state['settings'],self.kind.currentText() if self.kind.currentIndex()>0 else None))
        self.category_filter.setCurrentIndex(max(0,self.category_filter.findText(current))); self.category_filter.blockSignals(False)

    def choose_root(self):
        root = QFileDialog.getExistingDirectory(self,"選擇 ComfyUI 的 models 資料夾",self.root.text())
        if root:
            try: normalized=str(model_root(root))
            except (ValueError,OSError) as exc: self.window.notice(str(exc)); return
            self.save(); self.root.setText(normalized); self.window.state["settings"]["model_root"]=self.root.text()
            self.window.changed('models'); self.scan()

    def scan(self):
        root = self.root.text()
        if not Path(root).is_dir() or not root:
            self.window.notice("請先選擇模型資料夾。"); return
        root=str(model_root(root)); self.root.setText(root)
        self.window.state['settings']['model_root']=root
        self.save()
        def done(rows):
            self.save(); self.record=None; self.catalog.merge_models(rows,root); self.page=0; self.refresh()
            self.window.notice(f"掃描完成：{len(rows)} 個模型，未讀取模型權重。")
        self.window.jobs.start("正在讀取模型檔案清單…",lambda cancel:scan_models(root,cancel),done)

    def clean_missing(self):
        if not self.root.text(): self.window.notice('請先選擇模型根目錄。'); return
        self.save()
        try: count=len(self.catalog.archive_missing_models(self.root.text()))
        except (ValueError,OSError) as exc: self.window.notice(str(exc)); return
        self.record=None; self.refresh()
        self.window.notice(f'已清除 {count} 個確認缺失的項目，可使用「還原已清除」取回；未刪除模型檔案。')

    def restore_missing(self):
        if not self.root.text(): self.window.notice('請先選擇模型根目錄。'); return
        self.save()
        try: count=self.catalog.restore_missing_models(self.root.text())
        except (ValueError,OSError) as exc: self.window.notice(str(exc)); return
        self.record=None; self.refresh(); self.window.notice(f'已還原 {count} 個資產項目。')

    def identify_civitai(self):
        root=self.root.text()
        if not root or not Path(root).is_dir(): self.window.notice('請先選擇並掃描 ComfyUI models 資料夾。'); return
        policy=network_policy(self.window)
        if not policy.refresh(): self.window.notice('目前已關閉網路功能；請先在候選與翻譯設定中啟用。'); return
        self.save(); rows=[r for r in self.catalog.rows('model',str(Path(root).resolve()),limit=20000) if not r.get('missing')]
        if not rows: self.window.notice('沒有可辨識的模型檔案。'); return
        try: token=load_token(self.window.store.directory)
        except ValueError as exc: self.window.error(str(exc)); return
        def done(result):
            if not policy.refresh(): return
            self.save()
            self.catalog.update_identified_models(result); self.record=None; self.refresh()
            matched=sum(r.get('civitai_status')=='matched' for r in result)
            self.window.notice(f'CivitAI 辨識完成：{matched} 個相符，{len(result)-matched} 個未找到。')
        import copy
        rows=copy.deepcopy(rows)
        if self.window.jobs.start(f'正在計算 {len(rows)} 個模型的 SHA256 並查詢 CivitAI…',lambda cancel:identify_models(rows,CivitAIClient(token,cancel=cancel),cancel),done):
            policy.track(self.window.jobs.active.cancel)

    def filter_changed(self):
        self.refresh_categories(); self.page=0; self.refresh()

    def filtered(self):
        if not self.root.text(): return []
        rows = self.catalog.rows("model",str(Path(self.root.text()).resolve()),limit=20000)
        query = self.search.text().strip().casefold()
        return [r for r in rows if (self.kind.currentIndex()==0 or r["kind"]==self.kind.currentText())
                and (self.category_filter.currentIndex()==0 or r.get("category","其他")==self.category_filter.currentText())
                and (self.base_filter.currentIndex()==0 or r.get('base_model','')==self.base_filter.currentText())
                and (not query or query in " ".join(str(r.get(k,"")) for k in ("name","path","trigger","notes","base_model","creator")).casefold())]

    def turn(self, delta):
        self.page=max(0,self.page+delta); self.refresh()

    def refresh(self):
        self.save()
        all_rows=self.catalog.rows('model',str(Path(self.root.text()).resolve()),limit=20000) if self.root.text() else []
        base=self.base_filter.currentText(); self.base_filter.blockSignals(True); self.base_filter.clear(); self.base_filter.addItem('所有 Base Model')
        self.base_filter.addItems(sorted({r['base_model'] for r in all_rows if r.get('base_model')})); self.base_filter.setCurrentIndex(max(0,self.base_filter.findText(base))); self.base_filter.blockSignals(False)
        selected = self.record["id"] if self.record else None
        self.list.blockSignals(True); self.list.clear()
        rows = self.filtered(); self.page=min(self.page,max(0,(len(rows)-1)//60))
        for record in rows[self.page*60:(self.page+1)*60]:
            missing = " · 原檔不存在" if record.get("missing") else ""
            source=' · CivitAI' if record.get('civitai_status')=='matched' else ''
            base=(' · '+record.get('base_model','')) if record.get('base_model') else ''
            text = self.record_text(record)
            item = QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole,record)
            item.setIcon(thumb_icon(self.window.store,record.get("thumb"),100))
            self.list.addItem(item)
            if selected==record["id"]: self.list.setCurrentItem(item)
        self.count.setText(f"{len(rows)} 個模型 · 第 {self.page+1} 頁")
        self.list.blockSignals(False)
        self.select(self.list.currentItem())

    def record_text(self,record):
        source=record.get('civitai') or {}; status={'matched':'CivitAI 已連結','unverified':'CivitAI · 無官方校驗','stale':'來源需重新辨識'}.get(record.get('civitai_status'),'未連結來源')
        missing=record.get('missing') or not Path(record['path']).is_file()
        return f"{record['name']}\n{source.get('model_type') or record['kind']} · {record.get('base_model') or '底模未提供'} · {category_for(record)}\n{record['size']/1024**2:,.1f} MiB · {'檔案不存在' if missing else '檔案存在'} · {status}"

    def select(self,item):
        self.save(); self.loading=True
        self.record = self.catalog.get(item.data(Qt.ItemDataRole.UserRole)["id"]) if item else None
        r = self.record or {}
        self.name.setText(r.get("name","")); self.trigger.setPlainText(r.get("trigger","")); self.url.setText(r.get("url","")); self.notes.setPlainText(r.get("notes",""))
        names=categories(self.window.state['settings'],r.get('kind')); previous=category_for(r)
        if previous not in names:names.append(previous)
        self.category.clear(); self.category.addItems(names); self.category.setCurrentText(previous)
        self.base_model.setText(r.get('base_model') or '—'); self.creator.setText(r.get('creator') or '—')
        status={'matched':'已連結 CivitAI','not_found':'CivitAI 找不到相符 Hash','stale':'檔案已變更，需重新辨識','unverified':'已下載，缺少官方 Hash 校驗'}.get(r.get('civitai_status'),'尚未辨識')
        if r.get('civitai_version_id'): status+=f" · Version {r['civitai_version_id']}"
        self.civitai_status.setText(status)
        source=r.get('civitai') or {}; self.source_type.setText((source.get('model_type') or '未提供')+(' · 本機 '+r['kind'] if r.get('kind') else ''))
        self.source_version.setText(source.get('version_name') or '未提供'); self.source_link.setEnabled(bool(source.get('url')))
        self.source_trigger.setText(', '.join(source.get('trained_words',[])) or '未提供')
        set_preview(self.preview,self.window.store,r.get("thumb"))
        self.path.setText(r.get("path","選擇左側模型即可編輯。"))
        self.path.setToolTip(r.get("path",""))
        self.loading=False

    def save(self):
        self.saver.stop()
        if self.loading or not self.record: return
        latest=self.catalog.get(self.record['id'])
        if not latest: return
        self.record=latest
        self.record.update(name=self.name.text().strip() or Path(self.record["path"]).stem,
            category=self.category.currentText(),trigger=self.trigger.toPlainText(),url=self.url.text().strip(),notes=self.notes.toPlainText())
        self.catalog.put("model",self.record,self.record["root"])
        current=self.list.currentItem()
        if current and current.data(Qt.ItemDataRole.UserRole)["id"]==self.record["id"]:
            r=self.record
            current.setText(self.record_text(r))

    def choose_image(self):
        if not self.record: return
        path=image_path(self)
        if path: self.receive_image(path)

    def receive_image(self,path):
        if not self.record: self.window.notice('先選取模型，再拖入預覽圖片。'); return
        try:
            preview=thumbnail(path,self.window.store.directory); self.save(); self.record=self.catalog.get(self.record['id'])
            self.record['thumb']=preview; self.catalog.put('model',self.record,self.record['root']); self.refresh()
        except Exception as exc: self.window.error(str(exc))

    def clear_preview(self):
        if self.record:
            self.save(); self.record['thumb']=''; self.catalog.put('model',self.record,self.record['root']); self.refresh()

    def open_source_link(self):
        source=(self.record or {}).get('civitai') or {}
        if source.get('url'):open_url(self,source['url'])

    def open_link(self):
        if self.url.text(): open_url(self,self.url.text())

    def reveal(self):
        if self.record:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(Path(self.record["path"]).parent)))

    def manage_categories(self):
        from .dialogs import CategoryDialog
        dialog=CategoryDialog(self.window)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.save(); apply_categories(self.catalog,self.window.state['settings'],dialog.names(),dialog.scopes,dialog.mapping); self.window.changed('models')
            self.record=None; self.loading=True; self.category.clear(); self.category.addItems(categories(self.window.state['settings'])); self.loading=False
            self.refresh_categories(); self.refresh()

    def import_file(self):
        if not self.root.text() or not Path(self.root.text()).is_dir(): self.window.notice("請先選擇模型根目錄。"); return
        source = QFileDialog.getOpenFileName(self,"選擇已下載的模型檔案","","模型 (*.safetensors *.ckpt *.pt *.pth *.bin *.gguf)")[0]
        if not source: return
        kind,ok=QInputDialog.getItem(self,"匯入位置","複製到哪個資料夾？",["loras","checkpoints","diffusion_models","unet"],0,False)
        if not ok: return
        target = Path(self.root.text())/kind/Path(source).name
        if not ask(self,"匯入模型",f"將複製模型到：\n{target}\n\n原檔會保留，同名檔案不會覆寫。"): return
        root=self.root.text()
        self.window.jobs.start("正在複製模型，可從右下角取消…",lambda cancel:copy_model(source,root,kind,cancel),lambda _:self.scan())

    def recycle(self):
        if not self.record: return
        try:
            path = checked_model(self.root.text(),self.record["path"],self.record)
            if not ask(self,"移至資源回收筒",f"將移除實際模型檔案：\n\n{self.record['name']}\n{path}\n\nComfyUI 將無法再從此位置載入它。模型說明與預覽會保留；可由 Windows 資源回收筒還原。"): return
            # Revalidate after the confirmation, immediately before recycling.
            path = checked_model(self.root.text(),self.record["path"],self.record)
            result = QFile.moveToTrash(path.as_posix())
            success = result[0] if isinstance(result,tuple) else result
            if not success: raise ValueError("無法移至資源回收筒；檔案未刪除。可能正被使用，或磁碟不支援回收。")
            self.save(); self.record["missing"]=True; self.catalog.put('model',self.record,self.record['root']); self.refresh(); self.window.notice("模型已移至資源回收筒，說明與預覽仍保留。")
        except Exception as exc: self.window.error(str(exc))

    def context(self,pos):
        item=self.list.itemAt(pos)
        if not item: return
        self.list.setCurrentItem(item)
        menu=QMenu(self); menu.addAction("複製觸發詞",lambda:self.window.copy_text(self.trigger.toPlainText())); menu.addAction("加入目前組合",lambda:self.window.add_temporary(self.trigger.toPlainText()))
        menu.addAction("設定預覽圖",self.choose_image); menu.addAction("顯示檔案位置",self.reveal); menu.addSeparator(); menu.addAction("移至資源回收筒…",self.recycle)
        menu.open_at(self.list.viewport().mapToGlobal(pos))


class GalleryPage(QFrame):
    def __init__(self,window):
        super().__init__()
        self.window,self.catalog=window,window.catalog
        self.album=None; self.page=0; self.record=None; self.queue=[]; self.importing=False
        self.setObjectName("WorkspaceSurface")
        layout=QVBoxLayout(self); layout.setContentsMargins(12,16,12,16)
        split=QSplitter(); layout.addWidget(split)
        sidebar,left=panel(); split.addWidget(sidebar); sidebar.setMaximumWidth(260)
        left.addWidget(label("資料夾","Heading")); self.albums=QListWidget(); self.albums.currentItemChanged.connect(self.choose_album); left.addWidget(self.albums)
        self.albums.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.albums.customContextMenuRequested.connect(self.album_context)
        left.addWidget(button("＋ 新增資料夾",self.add_album)); left.addWidget(button("重新命名",self.rename_album)); left.addWidget(button("移除資料夾…",self.delete_album))
        left.addWidget(button('設定收藏儲存位置…',self.set_album_directory))
        content,middle=panel(); split.addWidget(content)
        middle.addWidget(label("按日期、角色或系列收集圖片","Heading"))
        self.search=QLineEdit(); self.search.setPlaceholderText("搜尋此資料夾的檔名"); self.search.textChanged.connect(self.search_changed)
        middle.addWidget(self.search)
        self.images=QListWidget(); self.images.setViewMode(QListWidget.ViewMode.IconMode); self.images.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.images.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.images.setMovement(QListWidget.Movement.Static); self.images.setIconSize(QSize(156,156)); self.images.setGridSize(QSize(194,214)); self.images.setWordWrap(True)
        self.images.currentItemChanged.connect(self.select); self.images.itemDoubleClicked.connect(lambda item:open_file(self,item.data(Qt.ItemDataRole.UserRole)["path"]))
        self.images.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.images.customContextMenuRequested.connect(self.context)
        middle.addWidget(self.images,1)
        self.counter=label("尚未加入圖片","Subtle")
        middle.addLayout(row(self.counter,None,button("上一頁",lambda:self.turn(-1)),button("下一頁",lambda:self.turn(1))))
        self.copy_original=QCheckBox("另外複製原圖到應用資料夾（增加磁碟用量）")
        middle.addWidget(self.copy_original)
        middle.addLayout(row(button("加入圖片…",self.add_images),button("加入磁碟資料夾…",self.add_directory),None))
        detail,right=panel("InsetPanel"); split.addWidget(scrolling(detail)); split.setSizes([190,660,400])
        split.setChildrenCollapsible(False)
        self.preview=ImageDropLabel(window.store,keep_original=True); self.preview.filesReady.connect(self.start_import); self.preview.failed.connect(lambda message:import_error(window,message))
        self.preview.install_on(self.images); self.preview.setMinimumHeight(200); right.addWidget(self.preview)
        self.path=label("","Subtle",True); self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred); right.addWidget(self.path)
        self.metadata=QPlainTextEdit(); self.metadata.setReadOnly(True); right.addWidget(self.metadata,1)
        right.addWidget(button("查看原始生成資料",self.raw_metadata))
        self.restore_button=button('恢復這次模組組合',self.restore_combination)
        self.restore_button.setEnabled(False); right.addWidget(self.restore_button)
        self.img2img_button=button('用這張圖生圖',lambda:self.window.use_image_for_generation(self.record['path']) if self.record else None)
        self.img2img_button.setEnabled(False); right.addWidget(self.img2img_button)
        right.addLayout(row(button("開啟原圖",self.open_original),button("重新連結原圖",self.relink)))
        right.addLayout(row(button("附上目前工作區資料",self.attach),button("編輯備註",self.edit_notes)))
        right.addWidget(button('匯出圖片…',self.export_selected))
        right.addLayout(row(button("移至其它資料夾",self.move_album),button("移除圖片紀錄…",self.remove,"Danger")))
        self.refresh_albums()

    def refresh_albums(self):
        selected=self.album
        self.albums.blockSignals(True); self.albums.clear()
        rows=self.catalog.rows("album",limit=10000)
        for album in rows:
            item=QListWidgetItem(album["name"]); item.setData(Qt.ItemDataRole.UserRole,album["id"]); self.albums.addItem(item)
            if album["id"]==selected: self.albums.setCurrentItem(item)
        if self.albums.currentItem() is None and rows: self.albums.setCurrentRow(0)
        self.albums.blockSignals(False); self.choose_album(self.albums.currentItem())

    def choose_album(self,item):
        self.album=item.data(Qt.ItemDataRole.UserRole) if item else None; self.page=0; self.refresh()

    def album_context(self,pos):
        item=self.albums.itemAt(pos); menu=QMenu(self)
        if item:
            self.albums.setCurrentItem(item)
            menu.addAction('重新命名',self.rename_album)
            menu.addAction('設定收藏儲存位置…',self.set_album_directory)
            menu.addAction('匯出資料夾圖片…',self.export_album)
            menu.addAction('移除資料夾…',self.delete_album)
            menu.addSeparator()
        menu.addAction('新增資料夾',self.add_album)
        menu.open_at(self.albums.viewport().mapToGlobal(pos))

    def add_album(self):
        name,ok=QInputDialog.getText(self,"新增資料夾","自訂名稱，例如：9 月 10 日、下江小春")
        if ok and name.strip():
            self.album=uid(); self.catalog.put("album",dict(id=self.album,name=name.strip())); self.refresh_albums()

    def rename_album(self):
        if not self.album: return
        album=self.catalog.get(self.album); name,ok=QInputDialog.getText(self,"重新命名","資料夾名稱",text=album["name"])
        if ok and name.strip(): album["name"]=name.strip(); self.catalog.put("album",album); self.refresh_albums()

    def set_album_directory(self):
        if not self.album: self.window.notice('先選擇資料夾。'); return
        album=self.catalog.get(self.album)
        path=QFileDialog.getExistingDirectory(self,'指定此資料夾的收藏位置',album.get('directory',''))
        if path:
            album['directory']=path; self.catalog.put('album',album); self.window.recent.refresh_destinations()
            self.window.notice('已更新之後的收藏位置，既有圖片仍保留原處。')

    def delete_album(self):
        if self.importing: self.window.notice("請等待圖片匯入完成，或先取消。"); return
        if not self.album: return
        if self.catalog.count("image",self.album): self.window.notice("請先移出或移除資料夾內的圖片紀錄，再刪除此資料夾。"); return
        if ask(self,"移除空資料夾","只移除此應用內的空資料夾紀錄，不影響磁碟上的資料夾。"):
            self.catalog.delete(self.album); self.album=None; self.refresh_albums()

    def search_changed(self):
        self.page=0; self.refresh()

    def turn(self,delta):
        self.page=max(0,self.page+delta); self.refresh()

    def refresh(self):
        self.images.clear()
        if not self.album: self.counter.setText("新增資料夾後即可加入圖片"); return
        total=self.catalog.count("image",self.album,search=self.search.text().strip()); self.page=min(self.page,max(0,(total-1)//60))
        rows=self.catalog.rows("image",self.album,limit=60,offset=self.page*60,search=self.search.text().strip())
        for record in rows:
            item=QListWidgetItem(record["name"]); item.setData(Qt.ItemDataRole.UserRole,record)
            item.setIcon(record_icon(self.window.store,record,156))
            self.images.addItem(item)
        self.counter.setText(f"{total} 張 · 第 {self.page+1} 頁 · 每頁最多 60 張")

    def select(self,item):
        self.record=self.catalog.get(item.data(Qt.ItemDataRole.UserRole)["id"]) if item else None
        r=self.record or {}
        self.restore_button.setEnabled(bool(image_snapshots(r.get('metadata',{}))))
        self.img2img_button.setEnabled(bool(r.get('path') and Path(r['path']).is_file()))
        set_record_preview(self.preview,self.window.store,r)
        self.path.setText((r.get("name","")+"\n"+("應用內副本" if r.get("owned") else "連結原圖")+"\n儲存位置："+str(Path(r['path']).parent)) if r else "選取圖片即可查看原始資料與手動附註。")
        self.path.setToolTip(r.get("path",""))
        if r and not Path(r['path']).is_file(): self.path.setText(self.path.text()+'\n原圖遺失，可重新連結。')
        if r:
            self.metadata.setPlainText(readable_metadata(r))
        else: self.metadata.clear()

    def restore_combination(self,record=None):
        record=record or self.record
        if not record: return
        bindings=image_snapshots(record.get('metadata',{}))
        if not bindings: return
        selected=bindings[0]
        if len(bindings)>1:
            labels=[f"{b['snapshot']['state']['workspaces'][0]['name']} · 節點 {b.get('node_id','')} ({i+1})" for i,b in enumerate(bindings)]
            choice,ok=QInputDialog.getItem(self,'選擇模組快照','此圖片保存了多個文字節點的組合',labels,0,False)
            if not ok: return
            selected=bindings[labels.index(choice)]
        if not ask(self,'恢復圖片組合','會先備份目前狀態，再建立圖片工作區。素材已修改或遺失時，保留歷史文字，不覆蓋現有素材。'): return
        try:
            restored=restore_snapshot(self.window.state,selected['snapshot'])
            if not self.window.persist(): return
            backup=self.window.store.backup()
            self.window.store.save(restored)
            self.window.state=restored
            self.window.current_module=restored['modules'][0]['id'] if restored['modules'] else None
            self.window.refresh_workspaces(); self.window.refresh_modules()
            self.window.refresh_library(); self.window.refresh_builder()
            self.window.canvas_mode=restored.get('selection_view')=='canvas'
            self.window.tabs.setCurrentIndex(0)
            if self.window.canvas_mode: self.window.enter_canvas()
            else: self.window.leave_canvas()
            self.window.notice('已恢復圖片組合；先前工作狀態已保存於 '+backup.name)
        except Exception as exc: self.window.error(str(exc))

    def raw_metadata(self):
        if not self.record: return
        dialog=StudioDialog(self); dialog.setWindowTitle("圖片原始生成資料"); dialog.resize(850,650)
        layout=dialog.body; text=QPlainTextEdit(); text.setReadOnly(True)
        text.setPlainText(json.dumps(self.record.get("metadata",{}).get("raw",{}),ensure_ascii=False,indent=2))
        layout.addWidget(text); layout.addWidget(button("關閉",dialog.accept)); dialog.exec()

    def add_images(self):
        if not self.album: self.window.notice("請先建立或選擇資料夾。"); return
        files=QFileDialog.getOpenFileNames(self,"加入圖片（原檔預設保留原處）","","圖片 (*.png *.jpg *.jpeg *.webp *.bmp)")[0]
        self.start_import(files)

    def add_directory(self):
        if not self.album: self.window.notice("請先建立或選擇資料夾。"); return
        directory=QFileDialog.getExistingDirectory(self,"選擇資料夾（讀取本層圖片）")
        if directory:
            self.start_import([str(p) for p in Path(directory).iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTENSIONS])

    def start_import(self,files):
        if not files: return
        if not self.album: self.window.notice('請先建立或選擇資料夾，再拖入圖片。'); return
        if self.window.jobs.active or self.importing: self.window.notice("請先等待目前檔案工作完成。"); return
        self.queue=list(files); self.import_album=self.album; self.import_copy=self.copy_original.isChecked()
        self.importing=True; self.import_total=len(files); self.import_done=0; self.import_errors=[]
        self.next_image()

    def next_image(self):
        if not self.queue:
            self.importing=False; self.refresh()
            if self.import_errors:
                self.window.notice(''); import_error(self.window,f'已匯入 {self.import_done} 張，{len(self.import_errors)} 張失敗。\n'+self.import_errors[0])
            else: self.window.notice(f'已匯入 {self.import_done} 張圖片。')
            return
        source=self.queue.pop(0)
        def work(cancel):
            try: return import_image(source,self.window.store.directory,self.import_album,self.import_copy,cancel),""
            except Exception as exc: return None,f"{Path(source).name}：{error_message(exc)}"
        def done(result):
            record,error=result
            if record:
                original=Path(record['path'])
                if original.is_relative_to(self.window.store.directory/'originals'):
                    record.update(owned=True,original_relative=str(original.relative_to(self.window.store.directory)).replace('\\','/'))
                self.catalog.put("image",record,self.import_album); self.import_done+=1
            else: self.import_errors.append(error)
            if self.import_done%10==0: self.refresh()
            QTimer.singleShot(0,self.next_image)
        self.window.jobs.start(f"建立縮圖 {self.import_total-len(self.queue)} / {self.import_total}…",work,done)

    def open_original(self):
        if self.record: open_file(self,self.record["path"])

    def relink(self):
        if not self.record: return
        path=image_path(self)
        if path:
            if not ask(self,"重新連結","請確認這是同一張圖片；若選擇不同圖片，原有的手動附註仍會保留。內嵌資料將重新讀取。"): return
            try:
                replacement=import_image(path,self.window.store.directory,self.record["album"])
                self.record.update(path=replacement["path"],owned=False,original_relative="",thumb=replacement["thumb"],metadata=replacement["metadata"])
                self.save_record()
            except Exception as exc: self.window.error(str(exc))

    def save_record(self):
        self.catalog.put("image",self.record,self.record["album"])
        self.select(self.images.currentItem())

    def attach(self):
        if not self.record: return
        if self.record.get("manual") and not ask(self,"取代手動附註","此圖片已有工作區附註，是否改用目前的 Prompt 與建議參數？"): return
        self.record["manual"]=self.catalog.snapshot(self.window.state); self.save_record()

    def edit_notes(self):
        if not self.record: return
        text,ok=QInputDialog.getMultiLineText(self,"圖片備註","記錄這次生成的效果或調整方向",self.record.get("notes",""))
        if ok: self.record["notes"]=text; self.save_record()

    def move_album(self):
        if not self.record: return
        albums=self.catalog.rows("album",limit=10000)
        labels=[f"{r['name']} ({i+1})" for i,r in enumerate(albums)]
        name,ok=QInputDialog.getItem(self,"移動圖片紀錄","移到哪個資料夾？",labels,0,False)
        if ok: self.record["album"]=albums[labels.index(name)]["id"]; self.catalog.put("image",self.record,self.record["album"]); self.refresh()

    def remove(self):
        if self.record and ask(self,"移除圖片紀錄","只移除此應用中的圖片紀錄，保留原圖。應用內已複製的原圖與縮圖也不會被永久刪除。"):
            self.catalog.delete(self.record["id"]); self.refresh()

    def context(self,pos):
        item=self.images.itemAt(pos)
        if not item: return
        if not item.isSelected(): self.images.setCurrentItem(item)
        else: self.select(item)
        menu=QMenu(self)
        menu.addAction('匯出圖片…',self.export_selected)
        path=self.record['path']
        menu.addAction('顯示檔案位置',lambda:reveal_file(self,path))
        menu.addAction('複製檔案路徑',lambda:QApplication.clipboard().setText(path))
        menu.addAction("開啟原圖",self.open_original); menu.addAction("附上目前工作區資料",self.attach); menu.addAction("移至其它資料夾",self.move_album); menu.addAction("移除紀錄…",self.remove)
        menu.open_at(self.images.viewport().mapToGlobal(pos))

    def export_selected(self):
        paths=[i.data(Qt.ItemDataRole.UserRole)['path'] for i in self.images.selectedItems()]
        if paths: self.window.open_export(paths)
        else: self.window.notice('請先選擇圖片。')

    def export_album(self):
        if not self.album: return
        if self.catalog.count('image',self.album)>10000: self.window.notice('單次最多匯出 10,000 張。'); return
        paths=[r['path'] for r in self.catalog.rows('image',self.album,limit=10000)]
        if paths: self.window.open_export(paths)
        else: self.window.notice('此資料夾尚無圖片。')
