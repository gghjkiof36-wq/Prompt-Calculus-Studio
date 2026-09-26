import copy
from pathlib import Path
"""Full-window settings and a one-time interface choice."""
from PySide6.QtCore import Qt,QUrl,QTimer
from PySide6.QtGui import QDesktopServices,QIcon
from PySide6.QtWidgets import QWidget,QVBoxLayout,QHBoxLayout,QStackedWidget,QListWidget,QRadioButton,QButtonGroup,QLineEdit,QFormLayout,QTabWidget,QScrollArea
from .widgets import label,button,row,panel,scrolling,ComboBox
from .dialogs import SettingsDialog


def reading_page(widget,width=1440):
    """Keep ordinary forms readable on wide monitors; model tools stay wide."""
    if isinstance(widget,QScrollArea):
        old=widget; widget=old.takeWidget(); old.deleteLater()
    widget.setMaximumWidth(width)
    container=QWidget(); layout=QHBoxLayout(container); layout.setContentsMargins(28,20,28,24)
    layout.addStretch(); layout.addWidget(widget,1); layout.addStretch()
    widget.show()
    return scrolling(container)


class InterfaceChoice(QWidget):
    def __init__(self,window,welcome=False):
        super().__init__(); self.window=window
        self.setObjectName('SettingsSurface'); self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        outer=QVBoxLayout(self); outer.setContentsMargins(36,28,36,28); outer.setSpacing(24)
        if welcome: outer.addStretch()
        outer.addWidget(label('選擇你的工作介面' if welcome else '使用介面','DialogTitle'))
        outer.addWidget(label('素材庫共用，可隨時更換介面。','Subtle',True))
        self.choices=QButtonGroup(self)
        for mode,title,description in (
            ('canvas','Canvas','在畫布上安排模組、連接 Prompt 與工作流。建議從這裡開始。'),
            ('list','清單','先選擇分類，再挑選素材，適合以固定分類快速整理提示詞。')):
            card,body=panel('SettingsGroup'); outer.addWidget(card)
            choice=QRadioButton(title); choice.setProperty('mode',mode); self.choices.addButton(choice); body.addWidget(choice)
            choice.setChecked(('canvas' if welcome else window.state.get('selection_view','list'))==mode); body.addWidget(label(description,'Subtle',True))
        outer.addWidget(label('兩邊的選項與手動稿會保留。','Subtle',True))
        if welcome: outer.addWidget(button('開始使用',lambda:window.set_interface_mode(self.mode()),'Primary'))
        outer.addStretch()

    def mode(self): return self.choices.checkedButton().property('mode')


class WorkflowSettings(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.setAcceptDrops(True)
        layout=QVBoxLayout(self); layout.setSpacing(20)
        box,body=panel('SettingsGroup'); layout.addWidget(box)
        self.address=QLineEdit(window.comfy.url); self.address.setPlaceholderText('http://127.0.0.1:8188')
        body.addLayout(row(self.address,button('連線',self.connect),button('中斷',window.comfy.disconnect,'Quiet')))
        self.status=label('','Subtle',True); body.addWidget(self.status)
        box,body=panel('SettingsGroup'); layout.addWidget(box)
        self.mode=ComboBox(); self.mode.addItem('文生圖','txt2img'); self.mode.addItem('圖生圖','img2img')
        self.mode.currentIndexChanged.connect(self.change_mode)
        generation=window.generation_panel
        body.addLayout(row(self.mode,generation.mapping,generation.parameters,None,button('任務紀錄',generation.history,'Quiet')))
        if 'multi_output' in window.state: self.mode.hide()
        # The catalog is the single workflow selector. Keep the legacy combo hidden for its controller signals.
        generation.workflow.hide()
        from .workflow_manager import WorkflowManager
        self.manager=WorkflowManager(window); body.addWidget(self.manager)
        self.feedback=label('','Subtle',True); body.addWidget(self.feedback); self.feedback.hide()
        generation.importFeedback.connect(self.show_feedback)
        layout.addStretch(); window.comfy.stateChanged.connect(self.refresh); self.refresh()

    def refresh(self):
        self.status.setText(self.window.comfy.message)
        self.mode.blockSignals(True); self.mode.setCurrentIndex(self.window.generation_panel.mode.currentIndex()); self.mode.blockSignals(False)

    def change_mode(self): self.window.generation_panel.mode.setCurrentIndex(self.mode.currentIndex())
    def show_feedback(self,text): self.feedback.setText(text); self.feedback.setVisible(bool(text))
    def dragEnterEvent(self,event):
        if any(u.isLocalFile() and u.toLocalFile().lower().endswith('.json') for u in event.mimeData().urls()): event.acceptProposedAction()
    def dragMoveEvent(self,event): self.dragEnterEvent(event)
    def dropEvent(self,event):
        paths=[u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile() and u.toLocalFile().lower().endswith('.json')]
        if paths:
            event.acceptProposedAction()
            for path in paths: self.window.generation_panel.import_workflow(path)
    def connect(self):
        try: self.window.comfy.connect_to(self.address.text())
        except ValueError as exc: self.window.notice(str(exc)); self.status.setText(str(exc))


class SettingsPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.preferences=None; self.preference_pages=[]
        self.setObjectName('SettingsSurface'); self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.back_button=button('‹',window.go_back,'Quiet'); self.back_button.setToolTip('上一頁 · Alt+←')
        self.forward_button=button('›',window.go_forward,'Quiet'); self.forward_button.setToolTip('下一頁 · Alt+→')
        self.sidebar_toggle=button('',self.toggle_navigation,'Quiet'); self.sidebar_toggle.setIcon(QIcon(str(Path(__file__).parent/'assets/sidebar.svg'))); self.sidebar_toggle.setFixedSize(36,36); self.sidebar_toggle.setToolTip('收合／展開設定導覽')
        self.return_button=button('返回工作區',self.cancel,'SettingsReturn')
        self.return_button.setIcon(QIcon(str(Path(__file__).parent/'assets/arrow-left.svg')))
        header=row(self.sidebar_toggle,self.back_button,self.forward_button,None)
        header.setContentsMargins(12,12,12,12); outer.addLayout(header)
        content=QHBoxLayout(); content.setContentsMargins(0,0,0,0); content.setSpacing(0); outer.addLayout(content,1)
        self.navigation_area=QWidget(); nav=QVBoxLayout(self.navigation_area); nav.setContentsMargins(12,0,12,0); nav.addWidget(self.return_button)
        self.navigation=QListWidget(); self.navigation.setFixedWidth(204); nav.addWidget(self.navigation); content.addWidget(self.navigation_area)
        self.reading,reading=panel('SettingsReading'); reading.setContentsMargins(0,8,0,0); content.addWidget(self.reading,1)
        self.pages=QStackedWidget(); reading.addWidget(self.pages); self.index={}
        self.interface=InterfaceChoice(window); self.add('interface','使用介面',reading_page(self.interface))
        # Preferences remain attached while the settings page autosaves edits.
        for key,title in (('appearance','介面個人化'),('completion','候選與翻譯'),('dictionary','個人字典')): self.add(key,title,QWidget())
        from .civitai_ui import CivitAIPage
        self.civitai=CivitAIPage(window); self.add('civitai','CivitAI',self.civitai)
        # A single ComfyUI entry with full-size pages selected by its top tabs.
        self.comfy_content=QWidget(); comfy=QVBoxLayout(self.comfy_content); comfy.setSpacing(24)
        comfy.setContentsMargins(0,0,0,0); self.comfy_tabs=QTabWidget(); comfy.addWidget(self.comfy_tabs)
        self.workflows=WorkflowSettings(window); self.comfy_tabs.addTab(reading_page(self.workflows,16777215),'連線與工作流')
        # This full-height page owns its scrolling; no extra strip below it.
        margins=window.models.layout().contentsMargins()
        window.models.layout().setContentsMargins(margins.left(),margins.top(),margins.right(),0)
        self.comfy_tabs.addTab(window.models,'模型資產')
        info,body=panel('SettingsGroup'); self.nodes_heading=label('節點與 Manager','Heading'); body.addWidget(self.nodes_heading)
        body.addWidget(label('管理操作尚未整合，可先使用原有 Manager。','Subtle',True))
        body.addWidget(button('開啟 ComfyUI',lambda:QDesktopServices.openUrl(QUrl(window.comfy.url))))
        body.addStretch()
        nodes=QWidget(); nodes_layout=QVBoxLayout(nodes); nodes_layout.setContentsMargins(16,16,16,0); nodes_layout.addWidget(info)
        self.comfy_tabs.addTab(scrolling(nodes),'節點與 Manager')
        self.workflow_manager=self.workflows.manager
        self.add('comfy','ComfyUI',self.comfy_content)
        self.add('data','資料與備份',self.make_data_page())
        self.comfy_tabs.currentChanged.connect(lambda _:self.navigate(self.navigation.currentRow()))
        self.navigation.currentRowChanged.connect(self.navigate)
        self.autosave=QTimer(self); self.autosave.setSingleShot(True); self.autosave.setInterval(350); self.autosave.timeout.connect(self.flush)
        self.syncing=False; self.entry_appearance=None
        for choice in self.interface.choices.buttons():choice.toggled.connect(self.schedule_save)

    def toggle_navigation(self):
        visible=not self.navigation_area.isVisible()
        self.navigation_area.setVisible(visible); self.navigation.setVisible(visible)

    def make_data_page(self):
        page=QWidget(); layout=QVBoxLayout(page); layout.setSpacing(20)
        layout.addWidget(label('資料與備份','DialogTitle'))
        for title,description,actions in (
            ('完整備份','保存資料庫、縮略圖與應用內圖片副本；ZIP 還原需關閉程式後放回獨立資料目錄。', [('備份 ZIP…',self.window.backup_zip)]),
            ('文字與索引','匯入或匯出文字、設定及檔案索引。', [('匯入 JSON…',self.window.import_json),('匯出 JSON…',self.window.export_json)]),
            ('資料位置','開啟目前版本使用的資料資料夾。', [('開啟資料夾',lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.window.store.directory))))])):
            box,body=panel('SettingsGroup'); layout.addWidget(box); body.addWidget(label(title,'Heading')); body.addWidget(label(description,'Subtle',True))
            body.addLayout(row(*(button(name,action) for name,action in actions),None))
        layout.addStretch(); return reading_page(page)

    def schedule_save(self,*_):
        if not self.syncing:self.autosave.start()

    def flush(self):
        self.autosave.stop()
        if self.syncing:return
        self.syncing=True
        try:
            if self.preferences is not None:self.preferences.save(accept=False)
            mode=self.interface.mode()
            if mode!=self.window.state.get('selection_view','list'):
                self.window.set_interface_mode(mode); self.window.surface_stack.setCurrentWidget(self)
            self.window.models.save(); self.window.persist()
        finally:self.syncing=False

    def reset_appearance(self,restore=False):
        from .core import DEFAULT_SETTINGS
        keys=('font_family','ui_size','prompt_size','material','accent','density','mica_transparency','acrylic_transparency','reduce_motion','connection_style','confirm_clear_draft')
        values=self.entry_appearance if restore else DEFAULT_SETTINGS
        self.flush(); self.window.state['settings'].update({k:values[k] for k in keys if k in values})
        self.clear_preferences(); self.open('appearance'); self.window.apply_theme(preserve_layout=True); self.window.changed('settings')

    def add(self,key,title,widget):
        self.index[key]=self.pages.addWidget(widget); self.navigation.addItem(title)

    def navigate(self,index):
        if self.preferences is None and hasattr(self,'autosave'):self.ensure_preferences()
        self.pages.setCurrentIndex(index)
        key=next((key for key,value in self.index.items() if value==index),'interface')
        if key=='comfy': key=('workflows','models','nodes')[self.comfy_tabs.currentIndex()]
        self.window.record_navigation('settings',key)
        if key=='models' and self.window.first_models and self.window.models.root.text():
            self.window.first_models=False; self.window.models.scan()

    def ensure_preferences(self):
        if self.preferences is not None:return
        self.syncing=True; self.preferences=SettingsDialog(self.window)
        self.entry_appearance=self.entry_appearance or copy.deepcopy(self.window.state['settings'])
        for key in ('appearance','completion','dictionary'):
            page=self.preferences.tabs.widget(0); self.preferences.tabs.removeTab(0); page.setParent(None)
            if key=='appearance':
                inner=page.widget() if isinstance(page,QScrollArea) else page
                inner.layout().insertLayout(inner.layout().count()-1,row(button('還原介面調整',lambda:self.reset_appearance(True),'Quiet'),button('重置介面設定',self.reset_appearance,'Quiet'),None))
            old=self.pages.widget(self.index[key]); self.pages.removeWidget(old); old.deleteLater()
            page=reading_page(page); self.pages.insertWidget(self.index[key],page); self.preference_pages.append(page)
        from PySide6.QtWidgets import QComboBox,QSpinBox,QCheckBox,QSlider,QPlainTextEdit
        for widget in self.preferences.__dict__.values():
            if isinstance(widget,(QLineEdit,QPlainTextEdit)):widget.textChanged.connect(self.schedule_save)
            elif isinstance(widget,QComboBox):widget.currentIndexChanged.connect(self.schedule_save)
            elif isinstance(widget,(QSpinBox,QSlider)):widget.valueChanged.connect(self.schedule_save)
            elif isinstance(widget,QCheckBox):widget.toggled.connect(self.schedule_save)
        self.syncing=False

    def open(self,section='interface'):
        self.ensure_preferences(); self.syncing=True
        for choice in self.interface.choices.buttons(): choice.setChecked(choice.property('mode')==self.window.state.get('selection_view','list'))
        self.window.generation_panel.refresh(); self.workflows.refresh(); self.workflow_manager.refresh()
        self.civitai.online.blockSignals(True); self.civitai.online.setChecked(self.window.state['settings'].get('online',True)); self.civitai.online.blockSignals(False)
        if section=='civitai': self.civitai.refresh_history()
        sub={'workflows':0,'models':1,'nodes':2,'workflow_manager':0}.get(section)
        if sub is not None:
            self.comfy_tabs.blockSignals(True); self.comfy_tabs.setCurrentIndex(sub); self.comfy_tabs.blockSignals(False)
        self.navigation.setCurrentRow(self.index.get('comfy' if sub is not None else section,0)); self.navigate(self.navigation.currentRow()); self.syncing=False

    def clear_preferences(self):
        if self.preferences is None: return
        for key,page in zip(('appearance','completion','dictionary'),self.preference_pages):
            self.pages.removeWidget(page); page.deleteLater(); self.pages.insertWidget(self.index[key],QWidget())
        self.preferences.deleteLater(); self.preferences=None; self.preference_pages=[]

    def reload_state(self):
        """Discard editors for the old document after an explicit data import."""
        section=next((key for key,index in self.index.items() if index==self.navigation.currentRow()),'interface')
        self.autosave.stop(); self.clear_preferences(); self.entry_appearance=None
        self.civitai.stop_images(); self.civitai.search_cache.clear(); self.civitai.list.clear(); self.civitai.results=[]; self.civitai.initial_requested=False
        for control,key,default in ((self.civitai.filter_minor,'civitai_filter_minor',True),(self.civitai.nsfw,'civitai_mature',True)):
            control.blockSignals(True); control.setChecked(self.window.state['settings'].get(key,default)); control.blockSignals(False)
        self.open(section)

    def cancel(self):
        self.flush(); self.window.return_to_prompt()

    def save(self):
        self.flush(); self.window.return_to_prompt()

    def saved(self):self.save()
