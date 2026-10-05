"""Settings-only search and durable model installation; one bounded file worker."""
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import copy,time,hashlib,json
from collections import OrderedDict
from PySide6.QtCore import Qt,QSize,QPoint,QTimer,QEvent,Signal,QStringListModel
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QGridLayout,QButtonGroup,QSplitter,QListWidget,QListWidgetItem,QLineEdit,QPlainTextEdit,QFormLayout,QCheckBox,QFrame,QTabWidget,QDialog,QSizePolicy,QComboBox,QCompleter)
from .civitai import CivitAIClient,download_preview,normalize_version,version_file,preview_url,filter_content,visible_images,site_url
from .credentials import load_token,save_token,clear_token
from .civitai_network import network_policy
from .civitai_controls import DownloadDialog,selectable,details
from .civitai_assets import DownloadReceipts,perform_download,check_download,register_download,installed_version
from .media import thumbnail
from .jobs import Jobs
from .civitai_gallery import Gallery,Preview,ImageLoader
from .widgets import label,button,row,panel,scrolling,ComboBox,ElidedLabel,set_preview,open_url,thumb_icon,style_completion,sync_popup_appearance
from .ui_icons import icon

def compact_number(value):return f'{value:,}' if type(value) in (int,float) else '未提供'

class SearchToolbar(QWidget):
    """Keep search prominent, with its auxiliary controls on one compact row."""
    def __init__(self,query,sort,filters):
        super().__init__(); self.query=query; self.sort=sort; self.filters=filters; self.compact=None
        self.grid=QGridLayout(self); self.grid.setContentsMargins(0,0,0,0); self.grid.setSpacing(8)
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.relayout=QTimer(self); self.relayout.setSingleShot(True); self.relayout.timeout.connect(self.reflow)
        for control in (query,sort,filters):control.installEventFilter(self)
        self.reflow()
    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.StyleChange,QEvent.Type.FontChange,QEvent.Type.PolishRequest):self.relayout.start(0)
        return super().eventFilter(watched,event)
    def resizeEvent(self,event):
        super().resizeEvent(event); self.reflow()
    def reflow(self):
        compact=self.width()<620
        height=max(max(control.minimumSizeHint().height(),control.sizeHint().height()) for control in (self.query,self.sort,self.filters))
        self.setFixedHeight(height*(2 if compact else 1)+(8 if compact else 0))
        if compact==self.compact:return
        self.compact=compact
        for control in (self.query,self.sort,self.filters):self.grid.removeWidget(control)
        for column in range(3):self.grid.setColumnStretch(column,0)
        self.grid.setColumnStretch(0,1)
        self.grid.addWidget(self.query,0,0,1,3 if compact else 1)
        self.grid.addWidget(self.sort,1 if compact else 0,0 if compact else 1,1,2 if compact else 1)
        self.grid.addWidget(self.filters,1 if compact else 0,2)
        self.sort.setMaximumWidth(16777215 if compact else 180)


class SearchFilters(QFrame):
    """Draft filters: dismissing the popup never changes the active request."""
    def __init__(self,page):
        super().__init__(page,Qt.WindowType.Popup); self.page=page; self.setObjectName('PopupPanel')
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        body=QVBoxLayout(self); body.setContentsMargins(18,16,18,16); body.setSpacing(14)
        close=button('',self.hide,'Quiet'); close.setIcon(icon('close')); close.setFixedWidth(32); close.setAccessibleName('關閉篩選')
        body.addLayout(row(label('篩選模型','Heading'),None,close)); body.addWidget(label('模型類型','Section'))
        self.types=QButtonGroup(self); self.types.setExclusive(True); choices=QGridLayout(); choices.setSpacing(8)
        for index in range(page.kind.count()):
            choice=button(page.kind.itemText(index),lambda:None,'FilterChip'); choice.setCheckable(True)
            self.types.addButton(choice,index); choices.addWidget(choice,index//3,index%3)
        body.addLayout(choices); body.addWidget(label('基底模型','Section'))
        self.base=QLineEdit(); self.base.setPlaceholderText('所有基底模型'); self.base.setClearButtonEnabled(True)
        complete=QCompleter(['Illustrious','SDXL 1.0','Pony','SD 1.5','Flux.1 D','Anima'],self)
        complete.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive); self.base.setCompleter(complete); body.addWidget(self.base)
        style_completion(complete,self.base,page.window)
        self.base.returnPressed.connect(self.apply)
        self.clear_button=button('清除條件',self.clear,'Quiet'); self.apply_button=button('套用篩選',self.apply,'Primary')
        body.addLayout(row(self.clear_button,None,self.apply_button))
    def open(self):
        sync_popup_appearance(self,self.page.filter_button,self.page.window)
        self.types.button(self.page.kind.currentIndex()).setChecked(True); self.base.setText(self.page.base.text())
        available=self.page.window.screen().availableGeometry(); width=min(460,max(320,self.page.width()-24),available.width()-24)
        self.setFixedWidth(width); self.adjustSize()
        anchor=self.page.filter_button.mapToGlobal(QPoint(self.page.filter_button.width(),self.page.filter_button.height()+8))
        x=max(available.left()+8,min(anchor.x()-self.width(),available.right()-self.width()-8))
        y=max(available.top()+8,min(anchor.y(),available.bottom()-self.height()-8))
        self.move(x,y); self.show(); self.setFocus()
    def clear(self):
        self.types.button(0).setChecked(True); self.base.clear()
    def apply(self):
        self.page.apply_filters(self.types.checkedId(),self.base.text()); self.hide()
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Escape:self.hide(); event.accept()
        else:super().keyPressEvent(event)

class CivitAIPage(QFrame):
    progress=Signal(str,object,object)
    def __init__(self,window):
        super().__init__(); self.window=window; self.policy=network_policy(window)
        self.results=[]; self.current=None; self.source=None; self.cursor=''; self.search_args=None
        self.serial=0; self.preview_generation=0; self.token_revision=0; self.search_cache=OrderedDict(); self.initial_requested=False
        self.pending_search=None; self.pending_detail=None; self.pending_preview=None; self.pending_download=None
        self.preview_job=None; self.search_job=None; self.thumbs=set(); self.thumb_failed=set(); self.active_download=None
        self.browse_jobs=Jobs(window); self.browse_jobs.became_idle.connect(self.drain)
        self.receipts=DownloadReceipts(window.store.directory); self.receipts.recover()
        self.setObjectName('CivitAIPage'); outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0)
        self.tabs=QTabWidget(); outer.addWidget(self.tabs)
        search=QWidget(); layout=QVBoxLayout(search); self.search_layout=layout; layout.setContentsMargins(24,20,24,16); layout.setSpacing(12); self.tabs.addTab(search,'搜尋模型')
        self.page_title=label('搜尋模型','DialogTitle'); layout.addWidget(self.page_title)
        self.search_controls=QWidget(); controls=QVBoxLayout(self.search_controls); controls.setContentsMargins(0,0,0,0); controls.setSpacing(12); layout.addWidget(self.search_controls)
        self.query=QLineEdit(); self.query.setPlaceholderText('搜尋模型名稱或風格'); self.query.setClearButtonEnabled(True)
        self.query.addAction(icon('search'),QLineEdit.ActionPosition.LeadingPosition)
        self.search_timer=QTimer(self); self.search_timer.setSingleShot(True); self.search_timer.setInterval(450); self.search_timer.timeout.connect(self.search)
        self.query.textEdited.connect(self.queue_search); self.query.returnPressed.connect(self.search)
        self.suggestions=QStringListModel(self); self.completer=QCompleter(self.suggestions,self)
        self.completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive); self.completer.setFilterMode(Qt.MatchFlag.MatchContains)
        self.completer.setMaxVisibleItems(7);style_completion(self.completer,self.query,window); self.query.setCompleter(self.completer)
        self.completer.activated.connect(self.choose_suggestion)
        self.detail_toggle=button('詳情',self.toggle_details,'Quiet'); self.detail_toggle.setEnabled(False)
        self.refresh_button=button('',self.force_refresh,'Quiet'); self.refresh_button.setIcon(icon('update')); self.refresh_button.setToolTip('重新整理'); self.refresh_button.setAccessibleName('重新整理'); self.refresh_button.setFixedWidth(36)
        self.counter=selectable(''); self.counter.setMaximumWidth(180)
        self.counter.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Preferred)
        self.more=button('載入更多',self.load_more); self.more.setEnabled(False)
        self.kind=ComboBox(self); self.kind.hide()
        for title,value in [('所有類型',''),('LoRA','LORA'),('Checkpoint','Checkpoint'),('VAE','VAE'),('Embedding','TextualInversion'),('Upscaler','Upscaler')]:self.kind.addItem(title,value)
        self.base=QLineEdit(self); self.base.hide()
        self.sort=ComboBox()
        for title,value in [('最高評價','Highest Rated'),('最多下載','Most Downloaded'),('最新發布','Newest')]:self.sort.addItem(title,value)
        self.sort.setAccessibleName('模型排序'); self.sort.setMinimumWidth(142)
        self.filter_button=button('篩選',self.open_filters); self.filter_button.setIcon(icon('settings')); self.filter_button.setMinimumWidth(88)
        self.search_toolbar=SearchToolbar(self.query,self.sort,self.filter_button); controls.addWidget(self.search_toolbar)
        self.active_filters=ElidedLabel(''); self.active_filters.setObjectName('Subtle'); self.active_filters.hide(); self.active_filters.setMaximumWidth(260)
        self.clear_filters_button=button('清除',lambda:self.apply_filters(0,''),'Quiet'); self.clear_filters_button.hide()
        controls.addLayout(row(self.counter,self.active_filters,self.clear_filters_button,None,self.refresh_button,self.detail_toggle,self.more))
        self.filters_popup=SearchFilters(self)
        self.sort.currentIndexChanged.connect(self.queue_search)
        self.split=QSplitter(); layout.addWidget(self.split,1); self.split.setChildrenCollapsible(False)
        left_box=QWidget(); self.results_pane=left_box; left=QVBoxLayout(left_box); left.setContentsMargins(0,0,0,0); left.setSpacing(10); self.split.addWidget(left_box)
        self.list=Gallery(); self.list.currentItemChanged.connect(self.select); self.list.verticalScrollBar().valueChanged.connect(self.schedule_thumbnails)
        left.addWidget(self.list,1)
        self.detail_pane=QWidget(); self.detail_pane.setMinimumWidth(300); self.detail_pane.setMaximumWidth(460)
        self.detail_pane.setObjectName('CivitaiDetails'); right_column=QVBoxLayout(self.detail_pane); right_column.setContentsMargins(4,0,0,0); right_column.setSpacing(10); self.split.addWidget(self.detail_pane)
        self.detail_card,card=panel('InsetPanel'); card.setContentsMargins(16,14,16,16); card.setSpacing(8); right_column.addWidget(self.detail_card,1)
        self.detail_body=QWidget(); right=QVBoxLayout(self.detail_body); self.detail_layout=right; right.setContentsMargins(0,0,0,0); right.setSpacing(10)
        self.detail_scroll=scrolling(self.detail_body); self.detail_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); card.addWidget(self.detail_scroll,1)
        self.title=label('','Heading',True); self.title.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Preferred)
        self.detail_back=button('',self.hide_details,'Quiet'); self.detail_back.setIcon(icon('close')); self.detail_back.setFixedWidth(32); self.detail_back.setToolTip('返回搜尋結果'); self.detail_back.setAccessibleName('返回搜尋結果')
        right.addLayout(row(self.title,self.detail_back))
        self.creator=selectable(); right.addWidget(self.creator)
        self.version=ComboBox(); self.version.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon); self.version.setMinimumContentsLength(8); self.version.currentIndexChanged.connect(self.refresh_version)
        self.detail_state=selectable(''); self.detail_state.hide(); right.addWidget(self.detail_state)
        self.preview=Preview(); right.addWidget(self.preview)
        self.brief=QWidget(); self.brief.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred); brief=QFormLayout(self.brief); brief.setContentsMargins(0,0,0,0); brief.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.fields={}
        for key,title in (('Type','類型'),('Base Model','基底模型')):
            field=selectable(); self.fields[key]=field; brief.addRow(title,field)
        right.addWidget(self.brief); version_row=row(label('版本','Subtle'),self.version); version_row.setStretch(1,1); right.addLayout(version_row)
        self.details_toggle=button('模型資訊',self.toggle_fields,'Quiet'); self.details_toggle.setStyleSheet('text-align:left; padding-left:0;'); self.details_toggle.setIcon(icon('chevron-down')); right.addWidget(self.details_toggle)
        self.details_box=QWidget(); form=QFormLayout(self.details_box); self.detail_form=form; form.setContentsMargins(0,0,0,0); form.setVerticalSpacing(7)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        for key,title in (('Stats','使用統計'),('Reviews','評價'),('Published','發布日期'),('Usage Tips','使用建議')):
            field=selectable(); field.setAlignment(Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignTop); self.fields[key]=field; form.addRow(title,field)
        self.base_model=self.fields['Base Model']; self.stats=self.fields['Stats']
        self.trigger=selectable(); self.fields['AIR']=selectable()
        self.copy_trigger=button('複製',lambda:window.copy_text(self.trigger.text()),'Quiet'); self.copy_trigger.setAccessibleName('複製觸發詞'); self.copy_trigger.setToolTip('複製觸發詞')
        self.copy_air=button('複製',lambda:window.copy_text(self.fields['AIR'].text()),'Quiet'); self.copy_air.setAccessibleName('複製 AIR'); self.copy_air.setToolTip('複製 AIR')
        for title,field,control in (('觸發詞',self.trigger,self.copy_trigger),('AIR',self.fields['AIR'],self.copy_air)):
            field_row=QWidget(); field_layout=row(field,control); field_layout.setContentsMargins(0,0,0,0); field_layout.setStretch(0,1); field_row.setLayout(field_layout)
            control.setEnabled(False); form.addRow(title,field_row)
        right.addWidget(self.details_box); right.addStretch()
        self.link=button('開啟 CivitAI',self.open_source,'Quiet'); self.link.setIcon(icon('external-link')); card.addWidget(self.link)
        self.install_state=selectable(''); card.addWidget(self.install_state)
        self.download=button('下載…',self.download_selected,'Primary'); self.download.setIcon(icon('download',color='on-accent')); self.download.setEnabled(False)
        self.view_asset=button('查看資產',self.show_selected_asset); self.view_asset.hide(); card.addLayout(row(self.download,self.view_asset))
        self.details_open=False; self.detail_pane.hide(); self.split.setSizes([900,360]); self.configure_fonts()
        self.create_history(); self.create_connection()
        self.images=ImageLoader(self)
        self.thumb_timer=QTimer(self); self.thumb_timer.setSingleShot(True); self.thumb_timer.timeout.connect(self.start_thumbnails)
        window.jobs.became_idle.connect(self.drain); self.progress.connect(self.update_progress); self.refresh_history()
        self.tabs.currentChanged.connect(self.tab_changed)

    def configure_fonts(self):
        size=max(13,min(18,round(self.window.state['settings']['ui_size']*1.1)))
        self.detail_pane.setStyleSheet(f'QWidget#CivitaiDetails QLabel, QWidget#CivitaiDetails QPushButton, QWidget#CivitaiDetails QComboBox {{font-size:{size}px;}} QWidget#CivitaiDetails QLabel#Heading {{font-size:{max(19,size+3)}px;font-weight:600;}}')
    def showEvent(self,event):
        super().showEvent(event); self.schedule_thumbnails()
        QTimer.singleShot(0,self.browse_on_entry)
    def browse_on_entry(self):
        if not self.isVisible() or not self.policy.refresh():return
        if not self.initial_requested:self.search()
        elif self.current and self.preview.image.isNull():self.refresh_version()
    def hideEvent(self,event):
        super().hideEvent(event); self.stop_images(); self.thumb_timer.stop()
        self.filters_popup.hide()
        self.search_timer.stop(); self.pending_search=self.pending_detail=None; self.serial+=1; self.preview_generation+=1
        if self.browse_jobs.active:self.browse_jobs.active.cancel.set()
        if not self.results:self.initial_requested=False
    def tab_changed(self,index):
        if index==0:self.browse_on_entry(); self.schedule_thumbnails()
        else:self.stop_images()
    def queue_search(self,*_):
        self.serial+=1
        if self.search_job is not None:self.search_job.cancel.set()
        self.search_timer.start()
    def choose_suggestion(self,text):self.query.setText(text); self.search()
    def force_refresh(self):self.search_cache.clear(); self.thumb_failed.clear(); self.search()
    def open_filters(self):self.filters_popup.open()
    def apply_filters(self,kind_index,base):
        base=base.strip(); changed=(self.kind.currentIndex(),self.base.text())!=(kind_index,base)
        self.kind.setCurrentIndex(kind_index); self.base.setText(base)
        titles=([self.kind.currentText()] if self.kind.currentData() else [])+([base] if base else [])
        self.active_filters.setText(' · '.join(titles)); self.active_filters.setToolTip(' · '.join(titles))
        self.active_filters.setVisible(bool(titles) and self.width()>=900); self.clear_filters_button.setVisible(bool(titles))
        self.filter_button.setText('篩選'+(f' · {len(titles)}' if titles else ''))
        if changed:self.queue_search()
    def hide_details(self):
        self.details_open=False; self.adapt_details(); self.list.setFocus(); self.schedule_thumbnails()
    def toggle_details(self):
        self.details_open=not self.details_open and self.current is not None; self.adapt_details(); self.schedule_thumbnails()
    def adapt_details(self):
        narrow=self.width()<900
        self.active_filters.setVisible(not narrow and bool(self.active_filters.text()))
        self.search_layout.setContentsMargins(*(16,12,16,12) if narrow else (24,20,24,16))
        self.detail_card.layout().setContentsMargins(*(12,10,12,12) if narrow else (16,14,16,16))
        self.detail_card.layout().setSpacing(6 if narrow else 8); self.detail_layout.setSpacing(6 if narrow else 10)
        self.preview.height_limit=max(200,min(320,round(self.height()*.44))) if narrow else max(260,min(560,round(self.height()*.55)))
        self.preview.fit_height()
        self.page_title.setVisible(not (narrow and self.details_open))
        self.results_pane.setVisible(not (narrow and self.details_open))
        self.search_controls.setVisible(not (narrow and self.details_open))
        self.detail_pane.setMaximumWidth(16777215 if narrow else 460)
        self.detail_pane.setVisible(self.details_open)
        self.detail_back.setText('返回結果' if narrow else '')
        self.detail_back.setIcon(icon('back' if narrow else 'close'))
        self.detail_back.setFixedWidth(108 if narrow else 32)
    def toggle_fields(self):
        self.details_box.setVisible(not self.details_box.isVisible()); self.details_toggle.setIcon(icon('chevron-down' if self.details_box.isVisible() else 'chevron-up'))
    def stop_images(self):
        if hasattr(self,'images'):self.images.cancel()
        if self.window.closing:
            self.search_timer.stop(); self.pending_search=self.pending_detail=None
            if self.browse_jobs.active:self.browse_jobs.active.cancel.set()
    def content_settings(self):return dict(self.window.state['settings'])

    def create_connection(self):
        box=QWidget(); outer=QVBoxLayout(box); outer.setContentsMargins(24,24,24,24)
        group,body=panel('SettingsGroup'); outer.addWidget(group); outer.addStretch()
        self.online=QCheckBox('允許聯網'); self.online.setChecked(self.policy.refresh()); self.online.toggled.connect(self.set_online); body.addWidget(self.online)
        self.token=QLineEdit(); self.token.setEchoMode(QLineEdit.EchoMode.Password); self.token.setPlaceholderText('選填 CivitAI Token')
        try:self.token.setText(load_token(self.window.store.directory))
        except ValueError as exc:self.window.notice(str(exc))
        self.token.textChanged.connect(self.token_changed)
        body.addLayout(row(self.token,button('測試並儲存',self.test_connection),button('清除',self.clear_token,'Quiet')))
        self.connection=selectable('Token 由 Windows 目前帳號加密保存，不寫入資料庫或一般備份。'); body.addWidget(self.connection); self.tabs.addTab(box,'連線設定')
        self.filter_minor=QCheckBox('隱藏未成年／兒童主題'); self.filter_minor.setChecked(self.window.state['settings'].get('civitai_filter_minor',True)); body.addWidget(self.filter_minor)
        self.nsfw=QCheckBox('包含成人內容'); self.nsfw.setChecked(self.window.state['settings'].get('civitai_mature',True)); body.addWidget(self.nsfw)
        body.addWidget(selectable('使用 CivitAI Red；主題過濾依來源標記與標籤，無標記的內容無法保證識別。'))
        self.filter_minor.toggled.connect(self.content_changed); self.nsfw.toggled.connect(self.content_changed)
    def content_changed(self):
        self.window.state['settings'].update(civitai_filter_minor=self.filter_minor.isChecked(),civitai_mature=self.nsfw.isChecked()); self.window.changed('settings')
        self.stop_images(); self.search_cache.clear(); self.list.clear(); self.results=[]; self.serial+=1
        if self.policy.refresh():self.search()

    def set_online(self,online):
        self.window.state['settings']['online']=online; self.window.changed('settings')
        preferences=self.window.settings_page.preferences if hasattr(self.window,'settings_page') else None
        if preferences is not None and hasattr(preferences,'online'):preferences.online.setChecked(online)
        if not online:self.stop_images(); self.drain()
        elif not self.results:self.search()
    def token_changed(self):self.token_revision+=1
    def allowed(self):
        if self.policy.refresh():return True
        self.window.notice('目前已關閉聯網，可在 CivitAI 連線設定中開啟。'); return False
    def start_job(self,message,task,done,failed=None):
        if not self.allowed():return False
        def accept(result):
            if self.policy.refresh():done(result)
        started=self.browse_jobs.start(message,task,accept,failed)
        if started:self.policy.track(self.browse_jobs.active.cancel)
        return started
    def test_connection(self):
        if not self.allowed():return
        token=self.token.text(); revision=self.token_revision; directory=self.window.store.directory
        def done(result):
            if revision!=self.token_revision:return
            save_token(directory,token); self.connection.setText('Token 連線正常，已儲存' if result['authenticated'] else '公開連線正常')
        self.start_job('正在測試 CivitAI…',lambda cancel:CivitAIClient(token,cancel=cancel).test_connection(),done,lambda error:self.connection.setText(error))
    def clear_token(self):
        self.token_revision+=1; clear_token(self.window.store.directory); self.token.clear(); self.connection.setText('已清除 Token')

    def search(self):
        self.search_timer.stop()
        self.initial_requested=True
        if not self.allowed():return
        self.serial+=1; self.cursor=''; self.more.setEnabled(False)
        self.search_args=dict(query=self.query.text().strip(),types=(self.kind.currentData(),) if self.kind.currentData() else (),base_models=(self.base.text().strip(),) if self.base.text().strip() else (),sort=self.sort.currentData(),nsfw=self.nsfw.isChecked(),limit=30)
        if self.preview_job is not None:self.preview_job.cancel.set()
        if self.search_job is not None:self.search_job.cancel.set()
        self.pending_detail=self.pending_preview=None
        self.pending_search=(self.serial,copy.deepcopy(self.search_args),self.token.text(),False)
        self.counter.setText('正在搜尋…'); self.drain()
    def load_more(self):
        if not self.cursor or not self.search_args or self.pending_search:return
        self.more.setEnabled(False); self.pending_search=(self.serial,{**self.search_args,'cursor':self.cursor},self.token.text(),True); self.drain()
    def drain(self):
        if self.window.closing:return
        if not self.policy.refresh():
            self.pending_search=self.pending_preview=self.pending_detail=None
            if self.pending_download:
                plan=self.pending_download; self.pending_download=None; plan.update(state='cancelled',error='聯網已關閉'); self.receipts.save(plan); self.refresh_history()
            self.counter.setText('聯網已關閉'); return
        if self.pending_download and not self.window.jobs.active:self.start_download()
        if self.browse_jobs.active:return
        if self.pending_search:
            serial,args,token,append=self.pending_search; self.pending_search=None
            settings=self.content_settings(); cache_key=json.dumps([args,hashlib.sha256(token.encode()).hexdigest(),settings.get('civitai_filter_minor',True)],sort_keys=True)
            def done(result):
                self.search_job=None
                if serial!=self.serial:return
                self.search_cache[cache_key]=(time.monotonic(),copy.deepcopy(result)); self.search_cache.move_to_end(cache_key)
                while len(self.search_cache)>12:self.search_cache.popitem(last=False)
                result=filter_content(result,settings)
                if not append:self.stop_images(); self.results=[]; self.thumbs.clear(); self.thumb_failed.clear(); self.list.clear()
                existing={r.get('id') for r in self.results}
                for record in result['items']:
                    if not isinstance(record,dict) or record.get('id') in existing:continue
                    record=copy.deepcopy(record); record.pop('description',None)
                    for version in record.get('modelVersions',[]):version.pop('description',None)
                    self.results.append(record); existing.add(record.get('id')); item=QListWidgetItem(self.result_text(record)); item.setData(Qt.ItemDataRole.UserRole,record); self.list.addItem(item)
                names=[r.get('name','') for r in self.results]; self.suggestions.setStringList(names[:60])
                if self.query.hasFocus() and self.query.text().strip():self.completer.setCompletionPrefix(self.query.text()); self.completer.complete()
                metadata=result.get('metadata') or {}; cursor=metadata.get('nextCursor')
                if cursor is None and metadata.get('nextPage'):cursor=parse_qs(urlsplit(metadata['nextPage']).query).get('cursor',[''])[0]
                self.cursor=str(cursor) if cursor is not None else ''
                if self.cursor==str(args.get('cursor','')):self.cursor=''
                self.more.setEnabled(bool(self.cursor) and len(self.results)<1000)
                self.counter.setText(f'{len(self.results)} 個結果'+(' · 請縮小搜尋範圍' if len(self.results)>=1000 else '') if self.results else '沒有相符結果'); self.schedule_thumbnails()
            def failed(error):
                self.search_job=None
                if serial==self.serial:self.counter.setText(error); self.more.setEnabled(bool(self.cursor))
            cached=self.search_cache.get(cache_key)
            if cached and time.monotonic()-cached[0]<120:done(copy.deepcopy(cached[1])); return
            if self.start_job('正在搜尋 CivitAI…',lambda cancel:CivitAIClient(token,cancel=cancel).search_models(**args),done,failed):self.search_job=self.browse_jobs.active
            return
        if self.pending_detail:self.start_detail(); return
        if self.pending_preview:self.start_preview(); return
        self.schedule_thumbnails()
    def result_text(self,record):
        versions=record.get('modelVersions') or []; version=versions[0] if versions else {}; creator=record.get('creator') or {}; stats=record.get('stats') or {}
        rating=('喜歡 '+compact_number(stats.get('thumbsUpCount'))) if stats.get('thumbsUpCount') is not None else '評價未提供'
        state='已安裝此版本' if installed_version(self.window.catalog,version.get('id')) else '未安裝'
        return f"{record.get('name','未命名')} · {creator.get('username','未提供')}\n{record.get('type','未提供')} · {version.get('baseModel','未提供')}\n{rating} · {state}"
    def select(self,item):
        self.current=item.data(Qt.ItemDataRole.UserRole) if item else None; self.version.blockSignals(True); self.version.clear()
        for version in (self.current or {}).get('modelVersions',[]):
            if isinstance(version,dict):self.version.addItem(version.get('name','未命名版本'),version)
        self.version.blockSignals(False); self.details_open=self.current is not None; self.adapt_details(); self.detail_toggle.setEnabled(self.current is not None); self.refresh_version()
    def refresh_version(self):
        self.preview_generation+=1; generation=self.preview_generation
        self.images.cancel_except({key for key in (*self.images.pending,*self.images.active) if key[0]!='preview'})
        if self.preview_job is not None:self.preview_job.cancel.set()
        self.pending_preview=None; self.pending_detail=None; self.source=None
        record=self.current or {}; version=self.version.currentData()
        self.title.setText(record.get('name','尚未選取模型')); self.creator.setText((record.get('creator') or {}).get('username','未提供'))
        self.set_detail_state(''); self.preview.clear(); self.preview.setText('未提供預覽'); self.render_details()
        if not isinstance(version,dict):return
        try:self.source=normalize_version(version,parent=record)
        except Exception as exc:self.set_detail_state(str(exc)); return
        self.render_details()
        if not version.get('_details_loaded') and self.policy.refresh():
            self.pending_detail=(generation,version.get('id'),self.token.text()); self.set_detail_state('正在讀取版本…'); self.drain()
        else:self.queue_preview(generation,version)
    def start_detail(self):
        generation,ident,token=self.pending_detail; self.pending_detail=None
        def done(version):
            if generation!=self.preview_generation:return
            version={**version,'_details_loaded':True}; version['images']=visible_images(version.get('images',[]),self.content_settings()); self.version.blockSignals(True); self.version.setItemData(self.version.currentIndex(),version); self.version.blockSignals(False)
            self.set_detail_state(''); self.source=normalize_version(version,parent=self.current); self.render_details(); self.queue_preview(generation,version)
        def failed(error):
            if generation==self.preview_generation:self.set_detail_state(error); self.download.setEnabled(False)
        self.start_job('正在讀取模型版本…',lambda cancel:CivitAIClient(token,cancel=cancel).version(ident),done,failed)
    def set_detail_state(self,text):
        self.detail_state.setText(text); self.detail_state.setVisible(bool(text))

    def render_details(self):
        source=self.source or {}
        for key,value in details(source).items():
            if key not in self.fields:continue
            if key=='Published' and value:value=str(value)[:10]
            self.fields[key].setText(value or '未提供')
            if key in ('Stats','Reviews','Published','Usage Tips'):self.detail_form.setRowVisible(self.fields[key],bool(value))
        trigger=', '.join(source.get('trained_words',[])); self.trigger.setText(trigger or '未提供')
        self.copy_trigger.setEnabled(bool(trigger.strip())); self.copy_air.setEnabled(bool(str(source.get('air') or '').strip()))
        self.link.setEnabled(bool(source.get('url')))
        version=self.version.currentData() or {}; self.download.setEnabled(bool(source) and any(f.get('downloadUrl') for f in version.get('files',[])))
        asset=installed_version(self.window.catalog,source.get('version_id')) if source else None
        self.install_state.setText('已安裝此版本' if asset else ('未安裝' if self.download.isEnabled() else '未提供下載檔案' if source else '')); self.view_asset.setVisible(bool(asset))
    def queue_preview(self,generation,version):
        url=next((i.get('url') for i in visible_images(version.get('images',[]),self.content_settings()) if i.get('url')),None)
        if not url:self.preview.setText('這個版本沒有可用預覽'); return
        if not self.policy.refresh():self.preview.setText('聯網已關閉'); return
        self.preview.setText('正在載入預覽…'); key=('preview',generation)
        def done(image):
            if generation==self.preview_generation:self.preview.set_image(image)
        def failed(error):
            if generation==self.preview_generation:self.preview.clear(); self.preview.setText(error)
        self.images.fetch(key,preview_url(url,450),done,failed,priority=True)
    def start_preview(self):pass
    def schedule_thumbnails(self,*_):
        if hasattr(self,'thumb_timer') and self.isVisible() and self.tabs.currentIndex()==0:self.thumb_timer.start(80)
    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'details_open'):self.adapt_details()
        self.schedule_thumbnails()
    def start_thumbnails(self):
        if not self.isVisible() or not self.policy.refresh() or self.tabs.currentIndex()!=0:return
        visible=[]; rect=self.list.viewport().rect(); keys={('preview',self.preview_generation)}
        for index in range(self.list.count()):
            item=self.list.item(index); record=item.data(Qt.ItemDataRole.UserRole); ident=record.get('id')
            if not self.list.visualItemRect(item).intersects(rect):
                if not item.icon().isNull():item.setIcon(QIcon()); self.thumbs.discard(ident)
                continue
            key=('thumb',self.serial,ident); keys.add(key)
            if ident in self.thumbs or ident in self.thumb_failed:continue
            versions=record.get('modelVersions') or []; images=visible_images(versions[0].get('images',[]) if versions else [],self.content_settings())
            url=next((i.get('url') for i in images if i.get('url')),None)
            if url:visible.append((key,ident,preview_url(url,320)))
            else:item.setData(Qt.ItemDataRole.UserRole+1,True)
        self.images.cancel_except(keys)
        serial=self.serial
        for key,ident,url in visible:
            def done(image,ident=ident):
                if serial!=self.serial:return
                from PySide6.QtGui import QPixmap
                for index in range(self.list.count()):
                    item=self.list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole).get('id')==ident:item.setIcon(QIcon(QPixmap.fromImage(image))); self.thumbs.add(ident); break
            def failed(error,ident=ident):
                if serial!=self.serial:return
                self.thumb_failed.add(ident)
                for index in range(self.list.count()):
                    item=self.list.item(index)
                    if item.data(Qt.ItemDataRole.UserRole).get('id')==ident:item.setData(Qt.ItemDataRole.UserRole+1,True); item.setToolTip(error)
            self.images.fetch(key,url,done,failed)
    def open_source(self):
        if self.source:open_url(self,site_url(self.source['url']))
    def show_selected_asset(self):
        if self.source:
            asset=installed_version(self.window.catalog,self.source['version_id'])
            if asset:self.show_asset(asset)
    def show_asset(self,asset):
        models=self.window.models; models.save(); models.record=None
        self.window.first_models=False; self.window.settings('models')
        models.root.setText(asset['root']); models.kind.setCurrentIndex(0); models.base_filter.setCurrentIndex(0); models.category_filter.setCurrentIndex(0)
        models.page=0; models.search.setText(asset['path']); models.refresh()
        for index in range(models.list.count()):
            if models.list.item(index).data(Qt.ItemDataRole.UserRole)['id']==asset['id']:models.list.setCurrentRow(index); break

    def create_history(self):
        box=QWidget(); layout=QVBoxLayout(box); layout.setContentsMargins(20,20,20,20)
        self.history_list=QListWidget(); self.history_list.currentItemChanged.connect(self.history_selected); layout.addWidget(self.history_list,1)
        self.history_detail=selectable('選取下載紀錄'); layout.addWidget(self.history_detail)
        self.history_retry=button('重新下載',self.retry_download); self.history_register=button('重新登記',self.retry_register)
        self.history_view=button('查看資產',self.history_view_asset); self.history_cancel=button('取消下載',self.cancel_download,'Quiet')
        layout.addLayout(row(self.history_retry,self.history_register,self.history_view,None,self.history_cancel)); self.tabs.addTab(box,'下載紀錄')
    def refresh_history(self):
        selected=self.history_list.currentItem(); ident=selected.data(Qt.ItemDataRole.UserRole) if selected else None
        self.history_list.blockSignals(True); self.history_list.clear()
        titles={'queued':'等待下載','downloading':'下載中','downloaded':'檔案已下載，待登記','registering':'正在登記','registration_failed':'檔案已下載，登記失敗','completed':'已加入模型資產','failed':'下載失敗','cancelled':'已取消','interrupted':'上次下載未完成'}
        for record in self.receipts.rows():
            item=QListWidgetItem(f"{record.get('name','模型')} · {record.get('source',{}).get('version_name','')}\n{record.get('filename','')} · {titles.get(record.get('state'),record.get('state',''))}")
            item.setData(Qt.ItemDataRole.UserRole,record['id']); self.history_list.addItem(item)
            if record['id']==ident:self.history_list.setCurrentItem(item)
        self.history_list.blockSignals(False)
        if self.history_list.currentRow()<0 and self.history_list.count():self.history_list.setCurrentRow(0)
        self.history_selected()
    def history_record(self):
        item=self.history_list.currentItem(); return self.receipts.get(item.data(Qt.ItemDataRole.UserRole)) if item else None
    def history_selected(self,*_):
        record=self.history_record() or {}; state=record.get('state'); result=record.get('result') or {}
        verified={'verified':'已核對官方 SHA256','unavailable':'缺少官方 Hash，僅有本機校驗值'}.get(result.get('verification'),'')
        self.history_detail.setText('\n'.join(str(v) for v in [str(Path(record.get('target',''))/record.get('filename','')),verified,record.get('error','')] if v) if record else '尚無下載紀錄')
        self.history_retry.setEnabled(state in ('failed','cancelled','interrupted')); self.history_register.setEnabled(state in ('downloaded','registration_failed'))
        self.history_view.setEnabled(state=='completed'); self.history_cancel.setEnabled(bool(self.active_download or self.pending_download))
    def download_selected(self):
        if not self.allowed() or not self.current:return
        if self.active_download or self.pending_download:self.window.notice('請等待目前下載完成，或先取消。'); return
        dialog=DownloadDialog(self.window,copy.deepcopy(self.current),copy.deepcopy(self.version.currentData()))
        if dialog.exec()!=QDialog.DialogCode.Accepted:return
        self.receipts.save(dialog.plan); self.pending_download=dialog.plan
        if self.preview_job is not None:self.preview_job.cancel.set()
        self.refresh_history(); self.install_state.setText('等待下載'); self.drain()
    def start_download(self):
        plan=self.pending_download; self.pending_download=None; self.active_download=plan['id']; token=self.token.text(); receipts=self.receipts
        last=[0.0]; emit=self.progress.emit; ident=plan['id']
        def progress(done,total):
            now=time.monotonic()
            if now-last[0]>.2:last[0]=now; emit(ident,done,total)
        def done(record):self.active_download=None; self.finish_registration(record)
        def failed(error):
            self.active_download=None; record=receipts.get(ident)
            self.install_state.setText('檔案已下載，可在下載紀錄重新登記' if record.get('state')=='downloaded' else error); self.refresh_history()
        directory=self.window.store.directory
        def task(cancel):
            record=perform_download(receipts,plan,token,cancel,progress)
            previews=record['result']['source'].get('previews') or []
            if previews and not cancel.is_set():
                try:
                    relative=download_preview(preview_url(previews[0]['url'],450),directory,cancel)
                    if not cancel.is_set():record['thumb']=thumbnail(Path(directory)/relative,directory); receipts.save(record)
                except Exception:pass  # A preview failure never loses the completed model.
            return record
        started=self.window.jobs.start('正在下載模型…',task,done,failed)
        if started:self.policy.track(self.window.jobs.active.cancel)
        else:self.pending_download=plan; self.active_download=None
        self.refresh_history()
    def finish_registration(self,record):
        if self.window.closing:return
        try:
            record.update(state='registering'); self.receipts.save(record); self.window.models.save(); thumb=record.get('thumb',''); source=record['result']['source']
            if source.get('previews'):
                url=preview_url(source['previews'][0]['url'],450); cached=self.window.store.directory/'civitai/previews'/(hashlib.sha256(url.encode()).hexdigest()+'.image')
                if not thumb and cached.is_file():
                    try:thumb=thumbnail(cached,self.window.store.directory)
                    except ValueError:pass
            asset=register_download(self.window.catalog,record,thumb); record.update(state='completed',asset_id=asset['id'],error=''); self.receipts.save(record)
            if not self.window.models.root.text():
                self.window.models.root.setText(record['root']); self.window.state['settings']['model_root']=record['root']; self.window.changed('settings')
            self.window.models.refresh(); self.render_details(); self.install_state.setText('已加入模型資產')
            for index in range(self.list.count()):
                item=self.list.item(index); item.setText(self.result_text(item.data(Qt.ItemDataRole.UserRole)))
            self.window.notice('已加入模型資產')
        except Exception as exc:
            record.update(state='registration_failed',error=str(exc)); self.receipts.save(record); self.install_state.setText('檔案已下載，登記失敗；可在下載紀錄重新登記')
        self.refresh_history()
    def update_progress(self,ident,done,total):
        if ident!=self.active_download:return
        self.install_state.setText(f'下載中 · {done/1024**2:,.1f} MiB'+(f' / {total/1024**2:,.1f} MiB' if total else ''))
        if (self.history_record() or {}).get('id')==ident:self.history_detail.setText(self.install_state.text())
    def cancel_download(self):
        if self.pending_download:
            plan=self.pending_download; self.pending_download=None; plan.update(state='cancelled',error='已取消'); self.receipts.save(plan)
        if self.active_download and self.window.jobs.active:self.window.jobs.active.cancel.set()
        self.refresh_history()
    def retry_download(self):
        record=self.history_record()
        if not record or record['state'] not in ('failed','cancelled','interrupted') or not self.allowed():return
        if self.active_download or self.pending_download:self.window.notice('已有下載進行中'); return
        if (Path(record['target'])/record['filename']).exists():self.history_detail.setText('同名檔案已存在，請檢查位置或重新選擇下載檔名。'); return
        self.pending_download=record; self.drain()
    def retry_register(self):
        record=self.history_record()
        if not record or record['state'] not in ('downloaded','registration_failed'):return
        self.window.jobs.start('正在檢查已下載檔案…',lambda cancel:check_download(record,cancel),self.finish_registration,lambda error:self.history_detail.setText(error))
    def history_view_asset(self):
        record=self.history_record() or {}; asset=self.window.catalog.get(record.get('asset_id',''))
        if asset:self.show_asset(asset)
