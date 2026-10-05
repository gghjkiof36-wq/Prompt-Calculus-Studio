import json
import time
from pathlib import Path
from PySide6.QtCore import Qt, QSize, QTimer, QFile, QUrl, QEvent, Signal
from PySide6.QtGui import QIcon, QDesktopServices
from PySide6.QtWidgets import (QWidget, QBoxLayout, QVBoxLayout, QHBoxLayout, QSplitter, QListWidget,
    QListWidgetItem, QLineEdit, QComboBox, QPlainTextEdit, QFormLayout, QFileDialog,
    QCheckBox, QDialog, QSizePolicy, QFrame, QAbstractItemView, QApplication, QSlider, QGridLayout)
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
from .snapshots import image_snapshots, restore_snapshot
from .image_drop import ImageDropLabel
from .widgets import reveal_file, ElidedLabel, ActionHeader
from .ui_icons import icon


class ModelPage(QFrame):
    def __init__(self, window):
        super().__init__()
        self.window, self.catalog = window,window.catalog
        self.record = None
        self.loading = False
        self.page = 0
        self.setObjectName("WorkspaceSurface")
        outer = QHBoxLayout(self); self.outer=outer; outer.setContentsMargins(24,24,24,24); outer.setSpacing(0)
        self.content=QWidget(); outer.addWidget(self.content,1)
        layout = QVBoxLayout(self.content); layout.setContentsMargins(0,0,0,0); layout.setSpacing(18)
        self.compact_view=QComboBox(); self.compact_view.addItems(['模型清單','模型詳情']); self.compact_view.currentIndexChanged.connect(self.adapt_panels)
        self.import_button=button('匯入模型',self.import_file,'Primary'); self.import_button.setIcon(icon('plus',color='on-accent'))
        self.model_more=button('',self.model_menu,'IconButton'); self.model_more.setIcon(icon('menu')); self.model_more.setToolTip('模型操作'); self.model_more.setAccessibleName('模型操作')
        self.header_count=label('','Subtle'); self.header_count.hide()
        self.model_header=ActionHeader(label('模型資產','DialogTitle'),self.header_count,self.compact_view,self.import_button,self.model_more)
        layout.addWidget(self.model_header)
        self.root = QLineEdit(window.state["settings"]["model_root"]); self.root.setReadOnly(True)
        if self.root.text():
            try: self.root.setText(str(model_root(self.root.text())))
            except (ValueError,OSError): pass
        self.root.setPlaceholderText("選擇 ComfyUI 的 models 資料夾")
        # Keep the controller's canonical path input; display its folder summary
        # as context instead of an editable-looking, full-width empty field.
        self.root.setParent(self); self.root.hide()
        self.root_panel=QWidget(); root_body=QVBoxLayout(self.root_panel); root_body.setContentsMargins(0,0,0,0)
        self.root_summary=ElidedLabel(); self.root_summary.setMinimumWidth(0)
        self.root_summary.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        choose=button('更換資料夾',self.choose_root,'Quiet'); choose.setIcon(icon('folder')); choose.setAccessibleName('選擇模型資料夾')
        rescan=button('',self.scan,'IconButton'); rescan.setIcon(icon('update')); rescan.setToolTip('重新掃描'); rescan.setAccessibleName('重新掃描')
        root_line=row(self.root_summary,choose,rescan); root_line.setStretch(0,1)
        root_body.addLayout(root_line); layout.addWidget(self.root_panel)
        self.root.textChanged.connect(self.update_root_summary); self.update_root_summary()
        split = QSplitter(); self.split=split
        split.setHandleWidth(24)
        library, left = panel(); self.library_panel=library; split.addWidget(library)
        left.setContentsMargins(0,0,0,0); left.setSpacing(14)
        self.filters=QWidget(); filters=QVBoxLayout(self.filters); filters.setContentsMargins(0,0,0,0); filters.setSpacing(10)
        self.search = QLineEdit(); self.search.setPlaceholderText("搜尋名稱、檔名、觸發詞或備註")
        self.search.textChanged.connect(self.filter_changed)
        self.kind = QComboBox()
        for value in ['所有類型',*LOCAL_TYPES]: self.kind.addItem(value)
        self.kind.currentIndexChanged.connect(self.filter_changed)
        self.category_filter = QComboBox(); self.refresh_categories()
        self.category_filter.currentIndexChanged.connect(self.filter_changed)
        self.base_filter=QComboBox(); self.base_filter.addItem('所有 Base Model'); self.base_filter.currentIndexChanged.connect(self.filter_changed)
        self.filter_line=QBoxLayout(QBoxLayout.Direction.LeftToRight); self.filter_line.setSpacing(10)
        selectors=QWidget(); selector_row=row(self.kind,self.base_filter,self.category_filter); selector_row.setContentsMargins(0,0,0,0); selectors.setLayout(selector_row)
        for i,weight in enumerate((7,9,7)):selector_row.setStretch(i,weight)
        self.base_filter.setAccessibleName('Base Model 底模篩選')
        self.filter_line.addWidget(self.search,2); self.filter_line.addWidget(selectors,3)
        filters.addLayout(self.filter_line); layout.addWidget(self.filters)
        self.count = label('尚未掃描','Subtle')
        self.previous=button('上一頁',lambda:self.turn(-1),'Quiet'); self.next=button('下一頁',lambda:self.turn(1),'Quiet')
        self.results_header=QWidget(); results_row=row(self.count,None,self.previous,self.next); results_row.setContentsMargins(0,0,0,0); self.results_header.setLayout(results_row)
        layout.addWidget(self.results_header); layout.addWidget(split,1)
        from .model_library import ModelLibraryDelegate
        self.list = QListWidget(); self.list.setIconSize(QSize(76,76)); self.list.setSpacing(2)
        self.list.setMouseTracking(True); self.list.setItemDelegate(ModelLibraryDelegate(self.list))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setTextElideMode(Qt.TextElideMode.ElideRight)
        self.list.currentItemChanged.connect(self.select)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self.context)
        left.addWidget(self.list,1)
        from .explore_art import ExploreArtwork
        empty_container=QWidget(); empty=QVBoxLayout(empty_container); empty.setContentsMargins(0,24,0,24); empty.setSpacing(0)
        self.empty_card,empty_body=panel('SettingsGroup'); self.empty_card.setMaximumWidth(520)
        self.empty_card.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        empty_body.setContentsMargins(28,24,28,24); empty_body.setSpacing(14)
        empty_body.addWidget(ExploreArtwork(window,'assets',100))
        self.empty_title=label('加入本機模型','Heading',True); self.empty_hint=label('選擇 ComfyUI 的 models 資料夾。','Subtle',True)
        empty_body.addWidget(self.empty_title); empty_body.addWidget(self.empty_hint)
        self.empty_action=button('選擇資料夾',self.empty_model_action,'Primary'); empty_body.addWidget(self.empty_action)
        empty_body.addWidget(button('瀏覽 CivitAI 模型',lambda:self.window.settings('civitai'),'Quiet'))
        empty_row=QHBoxLayout(); empty_row.setContentsMargins(0,0,0,0)
        empty_row.addStretch(1); empty_row.addWidget(self.empty_card,100); empty_row.addStretch(1)
        empty.addLayout(empty_row); empty.addStretch()
        self.library_empty=scrolling(empty_container); self.library_empty.setMinimumWidth(0)
        self.library_empty.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        left.addWidget(self.library_empty,1)
        detail, right = panel("InsetPanel"); detail.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        right.setContentsMargins(18,18,18,18); right.setSpacing(12)
        detail_container=QWidget(); detail_layout=QVBoxLayout(detail_container); detail_layout.setContentsMargins(0,2,0,16); detail_layout.setSpacing(0)
        detail_layout.addWidget(detail,0,Qt.AlignmentFlag.AlignTop); detail_layout.addStretch()
        self.detail_scroll=scrolling(detail_container); self.detail_scroll.setMinimumWidth(0)
        self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        split.addWidget(self.detail_scroll); split.setSizes([580,380])
        self.detail=detail
        split.setChildrenCollapsible(False)
        close=button('',lambda:self.list.setCurrentRow(-1),'IconButton'); close.setIcon(icon('close')); close.setToolTip('關閉模型詳情'); close.setAccessibleName('關閉模型詳情')
        self.detail_title=label('模型詳情','Heading',True); self.detail_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        right.addLayout(row(self.detail_title,close)); right.itemAt(0).layout().setStretch(0,1)
        self.identity=label('','Subtle',True)
        self.overview=QWidget(); self.overview_layout=QGridLayout(self.overview)
        self.overview_layout.setContentsMargins(0,0,0,0); self.overview_layout.setSpacing(12)
        right.addWidget(self.overview)
        self.preview = ImageDropLabel(window.store); self.preview.pathReady.connect(self.receive_image); self.preview.failed.connect(window.notice); self.preview.setFixedSize(200,200); self.preview.setWordWrap(True)
        self.preview_actions=QWidget(); preview_actions=row(); preview_actions.setContentsMargins(0,0,0,0); self.preview_actions.setLayout(preview_actions)
        self.preview_buttons=[]
        for title,callback,symbol in (('設定預覽圖',self.choose_image,'media'),('移除預覽',self.clear_preview,'trash')):
            control=button(title,callback,'Quiet'); control.setToolTip(title); control.setAccessibleName(title)
            control.setIcon(icon(symbol)); preview_actions.addWidget(control); self.preview_buttons.append(control)
        self.compact_preview=None
        right.addWidget(label('本機資料','Heading'))
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
        for title,widget in [('顯示名稱',self.name),('用途分類',self.category),('觸發詞',self.trigger),('我的備註',self.notes)]: form.addRow(title,widget)
        right.addLayout(form)
        right.addLayout(row(button("複製觸發詞",lambda:self.window.copy_text(self.trigger.toPlainText()),'Quiet'),button("觸發詞加入組合",lambda:self.window.add_temporary(self.trigger.toPlainText()),'Quiet')))
        right.addWidget(label('來源資訊','Heading'))
        source_form=QFormLayout(); source_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        source_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        for title,widget in [('Type',self.source_type),('Base Model',self.base_model),('來源版本',self.source_version),('作者',self.creator),('CivitAI',self.civitai_status),('來源 Trigger',self.source_trigger),('相關網址',self.url)]:source_form.addRow(title,widget)
        right.addLayout(source_form); right.addWidget(self.source_link)
        right.addWidget(button('開啟網址',self.open_link,'Quiet'))
        right.addWidget(label('檔案','Heading'))
        self.path = label("選擇左側模型即可編輯。","Subtle",True); self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        # Long unbroken filenames must wrap inside the detail pane, rather than
        # forcing the entire scroll content wider than its viewport.
        self.path.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        right.addWidget(self.path)
        right.addWidget(button("顯示檔案位置",self.reveal,'Quiet'))
        right.addWidget(button("將模型檔案移至資源回收筒…",self.recycle,"Danger"))
        self.saver = QTimer(self); self.saver.setSingleShot(True); self.saver.timeout.connect(self.save)
        for widget in (self.name,self.url,self.trigger,self.notes): widget.textChanged.connect(self.edit_changed)
        self.category.currentTextChanged.connect(self.edit_changed)
        self.detail_relayout=QTimer(self); self.detail_relayout.setSingleShot(True)
        self.detail_relayout.timeout.connect(self.refresh_detail_layout)
        self.adapt_panels()

    def update_root_summary(self):
        path=self.root.text()
        self.root_summary.setText((Path(path).name or path)+' · 本機模型資料夾' if path else '尚未選擇模型資料夾')
        self.root_summary.setToolTip(path)
        self.root_panel.setVisible(bool(path)); self.import_button.setVisible(bool(path))

    def empty_model_action(self):
        if getattr(self,'empty_filtered',False):
            for widget in (self.search,self.kind,self.base_filter,self.category_filter): widget.blockSignals(True)
            self.search.clear(); self.kind.setCurrentIndex(0); self.base_filter.setCurrentIndex(0); self.category_filter.setCurrentIndex(0)
            for widget in (self.search,self.kind,self.base_filter,self.category_filter): widget.blockSignals(False)
            self.filter_changed()
        elif self.root.text():self.scan()
        else:self.choose_root()

    def model_menu(self):
        menu=QMenu(self)
        menu.addAction('CivitAI 搜尋',lambda:self.window.settings('civitai')); menu.addSeparator()
        menu.addAction('辨識來源',self.identify_civitai); menu.addAction('管理分類',self.manage_categories)
        menu.addSeparator(); menu.addAction('清除缺失項目',self.clean_missing); menu.addAction('還原已清除',self.restore_missing)
        menu.open_at(self.model_more.mapToGlobal(self.model_more.rect().bottomLeft()))

    def refresh_detail_layout(self):
        # A hidden tab can retain its form's old height-for-width geometry after
        # a font change. Refresh once after the final viewport width is assigned.
        if not self.isVisible(): return
        self.adapt_panels()
        self.detail.layout().invalidate(); self.detail.layout().activate(); self.detail.updateGeometry()

    def showEvent(self,event):
        super().showEvent(event)
        if hasattr(self,'detail_relayout'): self.detail_relayout.start(0)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'detail_relayout'): self.adapt_panels(); self.detail_relayout.start(0)

    def adapt_panels(self,*_):
        if not hasattr(self,'detail_scroll'):return
        from .settings_page import SETTINGS_READING_MARGIN,SETTINGS_READING_NARROW_MARGIN
        margin=SETTINGS_READING_NARROW_MARGIN if self.width()<720 else SETTINGS_READING_MARGIN
        self.outer.setContentsMargins(margin,margin,margin,margin)
        self.filter_line.setDirection(QBoxLayout.Direction.LeftToRight if self.content.width()>=max(900,self.fontMetrics().height()*48) else QBoxLayout.Direction.TopToBottom)
        narrow=self.compact_model_layout()
        selected=self.record is not None
        detail_only=narrow and selected and self.compact_view.currentIndex()==1
        self.compact_view.setVisible(narrow and selected)
        self.library_panel.setVisible(not narrow or not selected or self.compact_view.currentIndex()==0)
        self.detail_scroll.setVisible(selected and (not narrow or self.compact_view.currentIndex()==1))
        self.root_panel.setVisible(bool(self.root.text()) and not detail_only)
        self.import_button.setVisible(bool(self.root.text()) and not detail_only)
        self.filters.setVisible(bool(getattr(self,'empty_filtered',False)) and not detail_only)
        short=self.height()<720
        has_results=bool(getattr(self,'empty_filtered',False)) and not detail_only
        self.header_count.setVisible(has_results and short)
        self.count.setVisible(not short and self.list.count()>0)
        self.results_header.setVisible(has_results and (not short or not self.previous.isHidden() or not self.next.isHidden()))
        self.base_filter.setItemText(0,'底模' if narrow else '所有 Base Model')
        self.base_filter.setToolTip('Base Model 底模篩選：'+(self.base_filter.currentText() if self.base_filter.currentIndex()>0 else '所有底模'))
        self.adapt_preview(narrow and short)
        self.detail_scroll.setMaximumWidth(16777215 if narrow else 400)
        if not narrow and selected:self.split.setSizes([max(1,self.content.width()-404),380])

    def adapt_preview(self,compact):
        if self.compact_preview==compact:return
        self.compact_preview=compact
        grid=self.overview_layout
        for widget in (self.identity,self.preview,self.preview_actions):grid.removeWidget(widget)
        if compact:
            self.preview.setFixedSize(104,104)
            grid.addWidget(self.preview,0,0,2,1,Qt.AlignmentFlag.AlignTop)
            grid.addWidget(self.identity,0,1); grid.addWidget(self.preview_actions,1,1,Qt.AlignmentFlag.AlignLeft)
            grid.setColumnStretch(0,0); grid.setColumnStretch(1,1)
        else:
            self.preview.setFixedSize(200,200)
            grid.addWidget(self.identity,0,0,1,2)
            grid.addWidget(self.preview,1,0,1,2,Qt.AlignmentFlag.AlignHCenter)
            grid.addWidget(self.preview_actions,2,0,1,2)
            grid.setColumnStretch(0,1); grid.setColumnStretch(1,1)
        for control,title in zip(self.preview_buttons,('設定預覽圖','移除預覽')):
            control.setText('' if compact else title); control.setObjectName('IconButton' if compact else 'Quiet')
            control.style().unpolish(control); control.style().polish(control); control.updateGeometry()
        self.detail.layout().setSpacing(8 if compact else 12)
        inset=14 if compact else 18
        self.detail.layout().setContentsMargins(inset,inset,inset,inset)
        self.refresh_preview()

    def refresh_preview(self):
        set_preview(self.preview,self.window.store,(self.record or {}).get('thumb'),self.preview.width())
        if self.compact_preview and self.preview.pixmap().isNull():self.preview.setText('加入預覽')

    def compact_model_layout(self):
        return self.content.width()<max(950,self.fontMetrics().height()*45)

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
            item.setToolTip(record['name']+'\n'+record['path'])
            item.setIcon(thumb_icon(self.window.store,record.get("thumb"),100))
            self.list.addItem(item)
            if selected==record["id"]: self.list.setCurrentItem(item)
        self.count.setText(f"{len(rows)} 個模型"+(f" · {self.page+1} / {max(1,(len(rows)+59)//60)} 頁" if len(rows)>60 else ''))
        self.header_count.setText(self.count.text())
        self.previous.setEnabled(self.page>0); self.next.setEnabled((self.page+1)*60<len(rows))
        self.previous.setVisible(len(rows)>60); self.next.setVisible(len(rows)>60)
        self.list.setVisible(bool(rows)); self.library_empty.setVisible(not rows)
        filtered=bool(all_rows)
        self.empty_filtered=filtered
        self.filters.setVisible(filtered); self.count.setVisible(bool(rows))
        self.results_header.setVisible(filtered)
        self.empty_title.setText('沒有符合的模型' if filtered else '加入本機模型')
        self.empty_hint.setText('調整搜尋或篩選條件，或清除條件以查看全部模型。' if filtered else
            '選擇 ComfyUI 的 models 資料夾，整理模型預覽、觸發詞與備註。')
        self.empty_action.setText('清除篩選' if filtered else '重新掃描' if self.root.text() else '選擇資料夾')
        self.empty_action.setVisible(True)
        self.update_root_summary()
        self.list.blockSignals(False)
        self.select(self.list.currentItem())

    def record_text(self,record):
        source=record.get('civitai') or {}; status={'matched':'CivitAI 已連結','unverified':'CivitAI · 無官方校驗','stale':'來源需重新辨識'}.get(record.get('civitai_status'),'未連結來源')
        missing=record.get('missing') or not Path(record['path']).is_file()
        return f"{record['name']}\n{source.get('model_type') or record['kind']} · {record.get('base_model') or '底模未提供'} · {category_for(record)}\n{record['size']/1024**2:,.1f} MiB · {'檔案不存在' if missing else '檔案存在'} · {status}"

    def select(self,item):
        self.save(); self.loading=True
        self.record = self.catalog.get(item.data(Qt.ItemDataRole.UserRole)["id"]) if item else None
        if self.record and self.compact_model_layout():self.compact_view.setCurrentIndex(1)
        r = self.record or {}
        self.detail_title.setText(r.get('name') or '模型詳情')
        self.detail_title.setToolTip(r.get('name',''))
        if r:
            status='原檔不存在' if r.get('missing') or not Path(r['path']).is_file() else '本機檔案'
            self.identity.setText(' · '.join((str((r.get('civitai') or {}).get('model_type') or r.get('kind') or '模型'),r.get('base_model') or '底模未提供',status)))
        else:self.identity.clear()
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
        self.refresh_preview()
        self.path.setText(r.get("path","選擇左側模型即可編輯。"))
        self.path.setToolTip(r.get("path",""))
        self.loading=False
        self.adapt_panels()

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
            current.setData(Qt.ItemDataRole.UserRole,dict(r))
            current.setText(self.record_text(r))
            self.detail_title.setText(r['name']); self.detail_title.setToolTip(r['name'])

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


MEDIA_LIST_SCROLL_GAP=12
MEDIA_LIST_COLUMN_WIDTH=320
MEDIA_LIST_COLUMN_GAP=20


class GalleryPage(QFrame):
    sidebarExpansionChanged=Signal(bool)

    def __init__(self,window):
        super().__init__()
        self.window,self.catalog=window,window.catalog
        self.album=None; self.page=0; self.record=None; self.queue=[]; self.importing=False
        self._sidebar_preference=None; self._last_sidebar_state=None; self._detail_open=False
        self._detail_width=380
        self.list_columns=1
        self.view_mode='images'; self._view_positions={}
        self._pending_anchor=None; self._finishing_gallery_layout=False
        self._gallery_layout_timer=QTimer(self); self._gallery_layout_timer.setSingleShot(True)
        self._gallery_layout_timer.timeout.connect(self.finish_gallery_layout)
        self.setObjectName("WorkspaceSurface")
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(0)
        self.import_options=button('匯入選項',self.import_menu,'Quiet')
        from .media_gallery import GallerySplitter, GalleryDetailSplitter, GalleryDetailCard, GalleryPreview
        self.sidebar_split=GallerySplitter(); layout.addWidget(self.sidebar_split,1)
        sidebar,left=panel('MediaSidebar'); self.folder_panel=sidebar; self.sidebar_split.addWidget(sidebar); sidebar.setFixedWidth(240)
        left.setContentsMargins(14,22,14,14); left.setSpacing(12)
        self.new_folder=button('',self.add_album,'IconButton'); self.new_folder.setIcon(icon('plus')); self.new_folder.setToolTip('新增資料夾'); self.new_folder.setAccessibleName('新增資料夾')
        self.folder_more=button('',self.folder_menu,'IconButton'); self.folder_more.setIcon(icon('more-horizontal')); self.folder_more.setToolTip('資料夾操作'); self.folder_more.setAccessibleName('資料夾操作')
        left.addLayout(row(label('資料夾','Heading'),None,self.new_folder,self.folder_more))
        self.albums=QListWidget(); self.albums.setObjectName('MediaFolders'); self.albums.setIconSize(QSize(18,18)); self.albums.currentItemChanged.connect(self.choose_album); left.addWidget(self.albums,1)
        self.albums.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.albums.customContextMenuRequested.connect(self.album_context)
        self.media_workspace=QFrame(); self.media_workspace.setObjectName('MediaContent')
        workspace_layout=QVBoxLayout(self.media_workspace); workspace_layout.setContentsMargins(0,0,0,0); workspace_layout.setSpacing(0)
        self.media_group=QWidget()
        group_layout=QVBoxLayout(self.media_group); group_layout.setContentsMargins(0,0,0,0); group_layout.setSpacing(12)
        self.header_area=QWidget(); self.header_layout=QVBoxLayout(self.header_area); self.header_layout.setContentsMargins(24,22,24,0)
        group_layout.addWidget(self.header_area)
        split=GalleryDetailSplitter(Qt.Orientation.Horizontal); split.setHandleWidth(32); self.split=split; group_layout.addWidget(split,1)
        workspace_layout.addWidget(self.media_group)
        self.sidebar_split.addWidget(self.media_workspace); self.sidebar_split.setStretchFactor(0,0); self.sidebar_split.setStretchFactor(1,1)
        self.sidebar_split.setChildrenCollapsible(False)
        content,outer=panel('MediaContent'); self.image_panel=content; split.addWidget(content); outer.setContentsMargins(24,0,12,16)
        self.browser_column=QWidget(); middle=QVBoxLayout(self.browser_column); middle.setContentsMargins(0,0,0,0); middle.setSpacing(12)
        centered=QHBoxLayout(); centered.setContentsMargins(0,0,0,0); centered.addStretch(); centered.addWidget(self.browser_column,1); centered.addStretch(); outer.addLayout(centered)
        self.album_title=ElidedLabel('媒體庫'); self.album_title.setObjectName('DialogTitle'); self.album_title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.import_button=button('匯入圖片',self.add_images,'Primary'); self.import_button.setIcon(icon('plus','on-accent'))
        header=row(self.album_title,self.import_button,self.import_options); self.media_header=header; header.setStretch(0,1); self.header_layout.addLayout(header)
        self.search=QLineEdit(); self.search.setPlaceholderText("搜尋此資料夾的檔名"); self.search.textChanged.connect(self.search_changed)
        self.image_mode=button('圖片',lambda:self.set_view_mode('images'),'Navigation'); self.image_mode.setIcon(icon('media')); self.image_mode.setCheckable(True); self.image_mode.setChecked(True)
        self.list_mode=button('清單',lambda:self.set_view_mode('list'),'Navigation'); self.list_mode.setIcon(icon('menu')); self.list_mode.setCheckable(True)
        self.thumbnail_control=QWidget(); thumb_row=QHBoxLayout(self.thumbnail_control); thumb_row.setContentsMargins(0,0,0,0); thumb_row.setSpacing(8)
        thumb_row.addWidget(label('縮圖','Subtle'))
        self.thumbnail_size=QSlider(Qt.Orientation.Horizontal); self.thumbnail_size.setRange(0,12); self.thumbnail_size.setFixedWidth(100)
        self.thumbnail_size.setAccessibleName('縮圖大小'); self.thumbnail_size.setToolTip('縮圖大小 · Ctrl + 滾輪')
        try:columns=max(8,min(20,int(window.state['settings'].get('gallery_columns',12))))
        except (TypeError,ValueError):columns=12
        self.thumbnail_size.setValue(20-columns); self.thumbnail_size.valueChanged.connect(self.thumbnail_size_changed); thumb_row.addWidget(self.thumbnail_size)
        self.media_toolbar=row(self.search,self.thumbnail_control); self.media_toolbar.setStretch(0,1); middle.addLayout(self.media_toolbar)
        header.addWidget(self.image_mode); header.addWidget(self.list_mode)
        self.empty_start=QWidget(); empty=QVBoxLayout(self.empty_start); empty.setContentsMargins(20,44,20,20)
        self.empty_title=label('建立第一個圖片資料夾','Heading',True); empty.addWidget(self.empty_title)
        self.empty_hint=label('依作品、角色或日期整理圖片。','Subtle',True); empty.addWidget(self.empty_hint)
        self.empty_action=button('新增資料夾',self.empty_action_clicked,'Primary')
        empty.addLayout(row(self.empty_action,None)); empty.addStretch()
        middle.addWidget(self.empty_start,1)
        from .media_gallery import MediaGalleryDelegate, MediaGalleryView
        self.images=MediaGalleryView(); self.images.setViewMode(QListWidget.ViewMode.IconMode); self.images.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.images.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.images.setMovement(QListWidget.Movement.Static); self.images.setIconSize(QSize(208,208)); self.images.setGridSize(QSize(230,256)); self.images.setWordWrap(False)
        self.images.setSpacing(0); self.images.setObjectName('MediaImages'); self.images.setUniformItemSizes(True)
        self.images.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.images.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.images.setItemDelegate(MediaGalleryDelegate(self))
        self.images.zoomRequested.connect(lambda steps:self.thumbnail_size.setValue(self.thumbnail_size.value()+steps))
        self.images.viewportAboutToResize.connect(self.queue_gallery_layout)
        self.images.viewportResized.connect(self.queue_gallery_layout)
        self.images.currentItemChanged.connect(self.select); self.images.itemDoubleClicked.connect(lambda item:open_file(self,item.data(Qt.ItemDataRole.UserRole)["path"]))
        self.images.itemSelectionChanged.connect(self.update_selection_summary)
        self.images.itemClicked.connect(lambda item:self.select(item) if not self._detail_open else None)
        self.images.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.images.customContextMenuRequested.connect(self.context)
        middle.addWidget(self.images,1)
        self.counter=label('0 張','Subtle'); self.selection_counter=label('','Subtle'); self.page_counter=label('','Subtle')
        self.previous=button('‹',lambda:self.turn(-1),'IconButton'); self.previous.setToolTip('上一頁'); self.previous.setAccessibleName('上一頁')
        self.next=button('›',lambda:self.turn(1),'IconButton'); self.next.setToolTip('下一頁'); self.next.setAccessibleName('下一頁')
        self.export_selection=button('匯出已選圖片…',self.export_selected,'Quiet')
        self.media_footer=row(self.counter,self.selection_counter,self.export_selection,None,self.page_counter,self.previous,self.next); middle.addLayout(self.media_footer)
        self.copy_original=QCheckBox("另外複製原圖到應用資料夾（增加磁碟用量）")
        self.copy_original.hide()
        self.detail_card=GalleryDetailCard(); right=QVBoxLayout(self.detail_card); right.setContentsMargins(20,20,20,20); right.setSpacing(12)
        self.detail_card.setMinimumWidth(300)
        self.detail_card.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Maximum)
        detail_container=QWidget(); detail_layout=QVBoxLayout(detail_container); detail_layout.setContentsMargins(0,0,0,0); detail_layout.setSpacing(0)
        self.detail_batch=QWidget(); batch_layout=QHBoxLayout(self.detail_batch); batch_layout.setContentsMargins(0,0,0,12)
        self.return_to_files=button('返回清單',self.hide_details,'Quiet'); self.return_to_files.setIcon(icon('back'))
        self.detail_selection=label('','Subtle')
        self.detail_export_selection=button('匯出已選圖片…',self.export_selected,'Quiet')
        batch_layout.addWidget(self.return_to_files); batch_layout.addStretch(); batch_layout.addWidget(self.detail_selection); batch_layout.addWidget(self.detail_export_selection)
        detail_layout.addWidget(self.detail_batch)
        self.detail_alignment=QWidget(); self.detail_alignment.setFixedHeight(0); detail_layout.addWidget(self.detail_alignment)
        detail_layout.addWidget(self.detail_card); detail_layout.addStretch()
        self.detail_scroll=scrolling(detail_container); self.detail_scroll.setMinimumWidth(300); self.detail_scroll.setMaximumWidth(430)
        split.addWidget(self.detail_scroll); split.setSizes([900,380]); split.setStretchFactor(0,1); split.setStretchFactor(1,0)
        split.setChildrenCollapsible(False)
        close_detail=button('',self.hide_details,'IconButton'); close_detail.setIcon(icon('close')); close_detail.setToolTip('關閉圖片詳情'); close_detail.setAccessibleName('關閉圖片詳情')
        self.detail_name=ElidedLabel(''); self.detail_name.setObjectName('Heading'); self.detail_name.setAccessibleName('目前檢視的圖片')
        self.detail_name.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        name_row=row(self.detail_name,close_detail); name_row.setStretch(0,1); right.addLayout(name_row)
        self.detail_body=QBoxLayout(QBoxLayout.Direction.TopToBottom); self.detail_body.setSpacing(12); right.addLayout(self.detail_body)
        self.preview=GalleryPreview(window.store); self.preview.filesReady.connect(self.start_import); self.preview.failed.connect(lambda message:import_error(window,message))
        self.preview.install_on(self.images); self.detail_body.addWidget(self.preview,0,Qt.AlignmentFlag.AlignTop)
        self.detail_actions=QWidget(); action_layout=QVBoxLayout(self.detail_actions); action_layout.setContentsMargins(0,0,0,0); action_layout.setSpacing(12)
        self.detail_body.addWidget(self.detail_actions,1,Qt.AlignmentFlag.AlignTop)
        self.preview.install_on(self.empty_start)
        self.img2img_button=button('用這張圖生圖',lambda:self.window.use_image_for_generation(self.record['path']) if self.record else None,'Primary')
        self.img2img_button.setEnabled(False); action_layout.addWidget(self.img2img_button)
        self.open_original_button=button('開啟原圖',self.open_original,'Quiet'); self.open_original_button.setIcon(icon('external-link'))
        self.detail_more=button('更多',self.detail_menu,'Quiet'); self.detail_more.setIcon(icon('menu')); self.detail_more.setAccessibleName('目前圖片的更多操作')
        self.path=label("","Subtle",True); self.path.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.path.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.source_block=QVBoxLayout(); self.source_block.setSpacing(4)
        self.source_actions=row(self.open_original_button,None,self.detail_more); self.source_actions.setSpacing(4)
        self.source_block.addLayout(self.source_actions); self.source_block.addWidget(self.path); action_layout.addLayout(self.source_block)
        divider=QFrame(); divider.setObjectName('SoftDivider'); divider.setFixedHeight(1); action_layout.addWidget(divider)
        self.metadata_hint=label('','Subtle',True)
        self.metadata_button=button('查看圖片資料 ›',self.raw_metadata,'Quiet'); self.metadata_button.setStyleSheet('text-align:left;')
        self.metadata_layout=QVBoxLayout(); self.metadata_layout.setSpacing(4)
        self.metadata_layout.addWidget(self.metadata_button); self.metadata_layout.addWidget(self.metadata_hint); action_layout.addLayout(self.metadata_layout)
        self.restore_button=button('恢復這次模組組合',self.restore_combination,'Quiet')
        self.restore_button.setEnabled(False); action_layout.addWidget(self.restore_button)
        self.detail_card.resized.connect(self.fit_detail_card)
        self.split.splitterMoved.connect(self.remember_detail_width)
        self.refresh_albums()

    @property
    def sidebar_expanded(self):
        return not self.folder_panel.isHidden()

    def set_sidebar_expanded(self,expanded):
        self._sidebar_preference=bool(expanded); self.adapt_panels()

    def toggle_sidebar(self):
        self.set_sidebar_expanded(not self.sidebar_expanded)

    def hide_details(self):
        self._detail_open=False; self.adapt_panels()

    def remember_detail_width(self,*_):
        if self.width()>=1100 and not self.detail_scroll.isHidden():
            self._detail_width=self.detail_scroll.width(); self.queue_gallery_layout()

    def empty_action_clicked(self):
        if not self.album:self.add_album()
        elif self.search.text():self.search.clear()
        else:self.add_images()

    def resizeEvent(self,event):
        if hasattr(self,'detail_scroll'):self.queue_gallery_layout()
        super().resizeEvent(event)
        if hasattr(self,'detail_scroll'):self.adapt_panels()

    def adapt_panels(self,*_):
        if not hasattr(self,'detail_scroll'):return
        self.queue_gallery_layout()
        narrow=self.width()<1100
        sidebar=(not narrow) if self._sidebar_preference is None else self._sidebar_preference
        borrowed=bool(self.folder_panel.property('pcsSidebarBorrowed'))
        if borrowed:sidebar=False
        details=self._detail_open and self.record is not None
        if not borrowed:
            if not self.folder_panel.property('pcsSidebarManaged'):self.folder_panel.setFixedWidth(240)
            self.folder_panel.setVisible(sidebar)
            if sidebar:
                width=self.folder_panel.width()
                self.sidebar_split.setSizes([width,max(1,self.sidebar_split.width()-width)])
        self.image_panel.setVisible(not narrow or not(sidebar or details))
        inset=12 if narrow else 24
        self.detail_card.parentWidget().layout().setContentsMargins(inset if narrow else 0,inset if narrow else 0,inset,inset)
        self.detail_batch.setVisible(narrow and details)
        self.detail_alignment.setFixedHeight(0 if narrow else self.search.sizeHint().height()+12)
        self.detail_scroll.setMinimumWidth(300+24+self.detail_scroll.verticalScrollBar().sizeHint().width())
        self.detail_scroll.setMaximumWidth(16777215 if narrow else 454)
        self.detail_scroll.setVisible(details and (not narrow or not sidebar))
        reading=self.view_mode=='list'
        gutter=MEDIA_LIST_SCROLL_GAP+self.images.verticalScrollBar().sizeHint().width() if reading else 0
        for toolbar in (self.media_toolbar,self.media_footer):toolbar.setContentsMargins(0,0,gutter,0)
        self.images.set_reading_gutter(MEDIA_LIST_SCROLL_GAP if reading else 0)
        self.media_workspace.layout().activate()
        self.media_group.layout().activate()
        if details and not narrow:
            self.split.setSizes([max(1,self.split.width()-self._detail_width-self.split.handleWidth()),self._detail_width])
        if sidebar!=self._last_sidebar_state:
            self._last_sidebar_state=sidebar; self.sidebarExpansionChanged.emit(sidebar)
        self.fit_image_grid()
        self.fit_detail_card()

    def fit_detail_card(self):
        if not hasattr(self,'detail_actions'):return
        horizontal=self.detail_card.width()>=620 and self.detail_card.fontMetrics().height()<=24
        direction=QBoxLayout.Direction.LeftToRight if horizontal else QBoxLayout.Direction.TopToBottom
        if self.detail_body.direction()!=direction:self.detail_body.setDirection(direction)
        self.detail_body.setSpacing(24 if horizontal else 12)
        self.preview.setSizePolicy(QSizePolicy.Policy.Fixed if horizontal else QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.preview.setMinimumWidth(220 if horizontal else 0)
        self.preview.setMaximumWidth(220 if horizontal else 16777215)
        self.preview.setProperty('horizontalCard',horizontal)
        available=self.detail_actions.width()
        inline=(self.open_original_button.sizeHint().width()+self.detail_more.sizeHint().width()+self.path.fontMetrics().horizontalAdvance(self.path.text())+24<=available)
        self.path.setMinimumWidth(self.path.fontMetrics().horizontalAdvance(self.path.text()) if inline else 0)
        path_inline=self.source_actions.indexOf(self.path)>=0
        if inline!=path_inline:
            if inline:
                self.source_block.removeWidget(self.path); self.source_actions.insertWidget(1,self.path)
            else:
                self.source_actions.removeWidget(self.path); self.source_block.addWidget(self.path)
        self.preview.fit_preview()

    def fullscreen_image_width(self):
        """Keep the chosen physical thumbnail size when a window becomes narrow."""
        screen=self.window.screen()
        width=max(screen.availableGeometry().width() if screen else 0,self.window.size().width())
        preference=getattr(self.window,'sidebar_pinned_preference',None)
        if preference is None:preference=self._sidebar_preference
        rail=getattr(self.window,'context_rail',None)
        resize=getattr(self.window,'sidebar_resize',None)
        sidebar_width=resize.preferred_width() if resize and self.folder_panel.property('pcsSidebarManaged') else self.folder_panel.width()
        # A borrowed drawer does not change the full-width thumbnail preference.
        sidebar=(rail.width() if rail is not None else 0) if preference is False else sidebar_width
        # QListView also reserves a scrollbar extent while calculating icon wrapping.
        gutters=self.images.verticalScrollBar().sizeHint().width()*2
        # The size control refers to the full gallery. Opening or resizing the
        # detail card changes the number of columns, never the preferred size.
        return max(64,width-sidebar-48-gutters-1)

    def fit_image_grid(self):
        if self.view_mode=='list':
            width=max(1,self.images.viewport().width())
            minimum=max(MEDIA_LIST_COLUMN_WIDTH,self.images.fontMetrics().height()*18)
            self.list_columns=max(1,min(3,(width+MEDIA_LIST_COLUMN_GAP)//(minimum+MEDIA_LIST_COLUMN_GAP)))
            self.images.setFlow(QListWidget.Flow.LeftToRight if self.list_columns>1 else QListWidget.Flow.TopToBottom)
            self.images.setWrapping(self.list_columns>1)
            height=max(38,self.images.fontMetrics().lineSpacing()+18)
            size=QSize(max(1,(width-2)//self.list_columns),height) if self.list_columns>1 else QSize()
            if self.images.gridSize()!=size:self.images.setGridSize(size)
            return
        columns=20-self.thumbnail_size.value()
        reference=self.fullscreen_image_width()
        width=max(64,reference//columns)
        # Keep the preferred physical size until even one tile cannot fit.
        available=self.images.viewport().width()-self.images.verticalScrollBar().sizeHint().width()-2
        if self.images.isVisible():width=min(width,max(1,available))
        size=QSize(max(1,width-20),max(1,width-20))
        grid=QSize(width,width)
        if self.images.iconSize()!=size:self.images.setIconSize(size)
        if self.images.gridSize()!=grid:self.images.setGridSize(grid)

    def gallery_anchor(self):
        for i in range(self.images.count()):
            item=self.images.item(i); rect=self.images.visualItemRect(item)
            if rect.intersects(self.images.viewport().rect()):return (item.data(Qt.ItemDataRole.UserRole)['id'],rect.top())
        return None

    def queue_gallery_layout(self):
        if not hasattr(self,'images') or self._finishing_gallery_layout:return
        if self._pending_anchor is None:self._pending_anchor=self.gallery_anchor()
        self._gallery_layout_timer.start(0)

    def finish_gallery_layout(self):
        anchor=self._pending_anchor; self._finishing_gallery_layout=True
        try:
            self.fit_image_grid(); self.images.doItemsLayout()
            if anchor:
                for i in range(self.images.count()):
                    item=self.images.item(i)
                    if item.data(Qt.ItemDataRole.UserRole)['id']==anchor[0]:
                        bar=self.images.verticalScrollBar(); bar.setValue(bar.value()+self.images.visualItemRect(item).top()-anchor[1]); break
        finally:
            self._pending_anchor=None; self._finishing_gallery_layout=False

    def thumbnail_size_changed(self,value):
        self._pending_anchor=self.gallery_anchor()
        self.window.state['settings']['gallery_columns']=20-value
        self.window.changed('settings',refresh=False)
        self.finish_gallery_layout(); self.queue_gallery_layout()

    def set_view_mode(self,mode):
        if mode not in ('images','list') or mode==self.view_mode:return
        anchor=self.gallery_anchor(); self._view_positions[self.view_mode]=anchor
        self.view_mode=mode; grid=mode=='images'
        self.image_mode.setChecked(grid); self.list_mode.setChecked(not grid)
        self.import_options.setText('匯入選項' if grid else '')
        self.import_options.setIcon(QIcon() if grid else icon('menu'))
        self.import_options.setToolTip('匯入選項'); self.import_options.setAccessibleName('匯入選項')
        self.thumbnail_control.setVisible(grid and bool(self.album))
        self.images.setViewMode(QListWidget.ViewMode.IconMode if grid else QListWidget.ViewMode.ListMode)
        self.images.setMovement(QListWidget.Movement.Static); self.images.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.images.setFlow(QListWidget.Flow.LeftToRight if grid else QListWidget.Flow.TopToBottom)
        self.images.setWrapping(grid); self.images.setWordWrap(False); self.images.setSpacing(0)
        self.images.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self._pending_anchor=self._view_positions.get(mode,anchor)
        self.adapt_panels(); self.images.doItemsLayout(); self.queue_gallery_layout()

    def folder_menu(self,trigger=None):
        menu=QMenu(self)
        menu.addAction('新增資料夾',self.add_album)
        for text,callback in (('重新命名',self.rename_album),('設定收藏儲存位置…',self.set_album_directory),('匯出資料夾圖片…',self.export_album),('移除資料夾…',self.delete_album)):
            action=menu.addAction(text,callback); action.setEnabled(self.album is not None)
        trigger=trigger or self.folder_more
        menu.open_at(trigger.mapToGlobal(trigger.rect().bottomLeft()))

    def detail_menu(self):
        if not self.record:return
        menu=QMenu(self)
        has_snapshot=bool(image_snapshots(self.record.get('metadata',{})))
        restore=menu.addAction('恢復模組組合' if has_snapshot else '恢復模組組合（無快照）',self.restore_combination)
        restore.setEnabled(has_snapshot); restore.setToolTip('此圖片未保存模組快照。' if not has_snapshot else '恢復這張圖片保存的模組組合')
        menu.addSeparator()
        for text,callback in (('重新連結原圖',self.relink),('附上目前工作區資料',self.attach),('編輯備註',self.edit_notes),('移至其它資料夾',self.move_album)):
            menu.addAction(text,callback)
        menu.addSeparator(); menu.addAction('移除圖片紀錄…',self.remove)
        menu.open_at(self.detail_more.mapToGlobal(self.detail_more.rect().bottomLeft()))

    def import_menu(self):
        menu=QMenu(self)
        menu.addAction('加入磁碟資料夾…',self.add_directory)
        menu.addSeparator()
        copy=menu.addAction('複製原圖到應用資料夾'); copy.setCheckable(True); copy.setChecked(self.copy_original.isChecked())
        copy.toggled.connect(self.copy_original.setChecked)
        menu.open_at(self.import_options.mapToGlobal(self.import_options.rect().bottomLeft()))

    def refresh_albums(self):
        selected=self.album
        self.albums.blockSignals(True); self.albums.clear()
        rows=self.catalog.rows("album",limit=10000)
        for album in rows:
            item=QListWidgetItem(icon('folder'),album['name']); item.setData(Qt.ItemDataRole.UserRole,album['id']); self.albums.addItem(item)
            if album["id"]==selected: self.albums.setCurrentItem(item)
        if self.albums.currentItem() is None and rows: self.albums.setCurrentRow(0)
        self.albums.blockSignals(False); self.choose_album(self.albums.currentItem(),collapse_sidebar=False)

    def choose_album(self,item,previous=None,*,collapse_sidebar=True):
        previous_album=self.album
        self.album=item.data(Qt.ItemDataRole.UserRole) if item else None
        self.album_title.setText(item.text() if item else '媒體庫'); self.album_title.setToolTip(item.text() if item else '')
        if self.album!=previous_album:
            self.page=0; self._view_positions.clear(); self.images.verticalScrollBar().setValue(0)
        if collapse_sidebar and item and self.width()<1100:self._sidebar_preference=False
        self.refresh()

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

    def folder_removal_busy(self):
        if self.importing:
            self.window.notice('請等待圖片匯入完成，或先取消。'); return True
        recent=self.window.recent
        if recent.collecting or recent.saves:
            self.window.notice('請等待圖片收藏完成。'); return True
        return False

    def delete_album(self):
        if self.folder_removal_busy():return
        if not self.album: return
        ident=self.album; album=self.catalog.get(ident)
        if not album:return
        count=self.catalog.count('image',ident)
        text=f'移除「{album["name"]}」'+(f'及其中 {count} 張圖片紀錄？' if count else '？')
        text+='\n磁碟原圖、應用內副本與縮圖都會保留。'
        if not ask(self,'移除資料夾',text):return
        # Confirmation runs a nested event loop; a collection may have started
        # after the first check. Keep its destination alive until it finishes.
        if self.folder_removal_busy():return
        # Remove only catalog entries in one transaction. The original files,
        # cached previews, other albums and generation history are untouched.
        with self.catalog.db:
            self.catalog.db.execute("DELETE FROM resources WHERE (kind='album' AND id=?) OR (kind='image' AND parent=?)",(ident,ident))
        self.album=None; self.record=None; self._detail_open=False; self.page=0
        self._pending_anchor=None; self._view_positions.clear()
        self.search.blockSignals(True); self.search.clear(); self.search.blockSignals(False)
        self.refresh_albums()
        if self.window.state['settings'].get('recent_destination')=='album:'+ident:
            self.window.state['settings']['recent_destination']=''; self.window.changed('settings',refresh=False)
        self.window.recent.refresh_destinations(); self.window.recent.update_save_button()

    def search_changed(self):
        self.page=0; self.refresh()

    def turn(self,delta):
        self.page=max(0,self.page+delta); self.refresh()

    def refresh(self):
        selected={item.data(Qt.ItemDataRole.UserRole)['id'] for item in self.images.selectedItems()}
        current=self.record['id'] if self.record else None
        position=self.images.verticalScrollBar().value()
        self.images.blockSignals(True); self.images.clear()
        self.previous.setEnabled(False); self.next.setEnabled(False)
        total=self.catalog.count('image',self.album,search=self.search.text().strip()) if self.album else 0
        self.page=min(self.page,max(0,(total-1)//60))
        self.empty_start.setVisible(total==0); self.images.setVisible(total>0)
        self.empty_title.setText('建立第一個圖片資料夾' if not self.album else '沒有符合的圖片' if self.search.text() else '加入第一張圖片')
        self.empty_hint.setText('依作品、角色或日期整理圖片。' if not self.album else '試試其他檔名。' if self.search.text() else '匯入圖片，或拖曳到這裡。')
        self.empty_action.setText('新增資料夾' if not self.album else '清除搜尋' if self.search.text() else '匯入圖片')
        self.import_button.setEnabled(bool(self.album)); self.import_options.setEnabled(bool(self.album)); self.search.setEnabled(bool(self.album))
        for widget in (self.import_button,self.import_options,self.search,self.image_mode,self.list_mode):widget.setVisible(bool(self.album))
        self.thumbnail_control.setVisible(bool(self.album) and self.view_mode=='images')
        self.previous.setEnabled(self.page>0); self.next.setEnabled((self.page+1)*60<total)
        for widget in (self.previous,self.next,self.page_counter):widget.setVisible(total>60)
        rows=self.catalog.rows('image',self.album,limit=60,offset=self.page*60,search=self.search.text().strip()) if self.album else []
        current_item=None
        for record in rows:
            item=QListWidgetItem(record['name']); item.setData(Qt.ItemDataRole.UserRole,record); item.setToolTip(record['name'])
            item.setData(Qt.ItemDataRole.AccessibleTextRole,record['name'])
            item.setIcon(record_icon(self.window.store,record,384))
            self.images.addItem(item)
            if record['id']==current:current_item=item
        if current_item:self.images.setCurrentItem(current_item)
        for i in range(self.images.count()):
            item=self.images.item(i); item.setSelected(item.data(Qt.ItemDataRole.UserRole)['id'] in selected)
        self.counter.setText(f'{total} 張'); self.page_counter.setText(f'{self.page+1} / {max(1,(total+59)//60)} 頁')
        self.images.blockSignals(False); self.select(self.images.currentItem(),open_detail=False)
        self.fit_image_grid(); self.images.verticalScrollBar().setValue(position)

    def select(self,item,previous=None,*,open_detail=True):
        self.record=self.catalog.get(item.data(Qt.ItemDataRole.UserRole)["id"]) if item else None
        if open_detail:self._detail_open=self.record is not None
        self.adapt_panels()
        r=self.record or {}
        self.detail_name.setText(r.get('name','')); self.detail_name.setToolTip(r.get('name',''))
        has_snapshot=bool(image_snapshots(r.get('metadata',{})))
        self.restore_button.setEnabled(has_snapshot); self.restore_button.setVisible(has_snapshot)
        self.img2img_button.setEnabled(bool(r.get('path') and Path(r['path']).is_file()))
        set_record_preview(self.preview,self.window.store,r,512)
        self.path.setText(("應用內副本" if r.get("owned") else "連結原圖") if r else '')
        if r and not Path(r['path']).is_file(): self.path.setText(self.path.text()+'\n原圖遺失，可重新連結。')
        meta=r.get('metadata',{})
        parts=['含內嵌資料' if meta.get('raw') or meta.get('nodes') else '無內嵌資料']
        if r.get('notes'):parts.append('備註')
        if r.get('manual') is not None:parts.append('手動附註')
        self.metadata_hint.setText(' · '.join(parts) if r else '')
        self.update_selection_summary(); self.images.viewport().update()

    def update_selection_summary(self):
        count=len(self.images.selectedItems())
        self.selection_counter.setText(f'· 已選 {count} 張' if count else '')
        self.selection_counter.setVisible(count>0)
        self.detail_selection.setText(f'已選 {count} 張')
        for control in (self.export_selection,self.detail_export_selection):
            control.setText(f'匯出 {count} 張…' if count else '匯出圖片…'); control.setEnabled(count>0); control.setVisible(count>0)

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
            from .snapshot_assets import missing_images
            from .snapshot_history import capture as capture_restore
            missing=missing_images(selected['snapshot'],self.window.store.directory)
            if not self.window.persist(): return
            backup=self.window.store.backup()
            before=capture_restore(self.window.canvas)
            undo=list(self.window.canvas.undo_stack)
            self.window.store.save(restored)
            if hasattr(self.window.comfy,'input_flow'):self.window.comfy.input_flow.workspace_changed(self.window.state['workspace'])
            self.window.state=restored
            self.window.canvas.last_state=self.window.canvas.history_state()
            self.window.current_module=restored['modules'][0]['id'] if restored['modules'] else None
            self.window.refresh_workspaces(); self.window.refresh_modules()
            self.window.refresh_library(); self.window.refresh_builder()
            self.window.canvas_mode=restored.get('selection_view')=='canvas'
            self.window.tabs.setCurrentIndex(0)
            if self.window.canvas_mode: self.window.enter_canvas()
            else: self.window.leave_canvas()
            self.window.canvas.undo_stack=(undo+[(before,capture_restore(self.window.canvas))])[-30:]
            self.window.canvas.redo_stack.clear()
            self.window.canvas.last_state=self.window.canvas.history_state()
            self.window.notice('已恢復圖片組合；先前工作狀態已保存於 '+backup.name+
                ('；以下圖片缺失或內容不符，請重新指定：'+'、'.join(missing) if missing else ''))
        except Exception as exc: self.window.error(str(exc))

    def raw_metadata(self):
        if not self.record: return
        from .media_gallery import ImageMetadataDialog
        ImageMetadataDialog(self,self.record).exec()

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
