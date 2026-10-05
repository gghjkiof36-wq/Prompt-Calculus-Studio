"""Full-window settings and a one-time interface choice."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt,QUrl,QTimer,QSize
from PySide6.QtGui import QDesktopServices,QIcon
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QListWidget,QListWidgetItem,
    QRadioButton,QButtonGroup,QLineEdit,QTabWidget,QScrollArea,QSizePolicy,QPushButton,
    QComboBox,QSpinBox,QCheckBox,QSlider,QPlainTextEdit,QStyledItemDelegate,QStyleOptionViewItem,QFormLayout)
from .widgets import label,button,row,panel,scrolling,ComboBox,ActionHeader
from .dialogs import SettingsDialog,AppearanceResetRow
from .ui_icons import icon
from .theme import visual_tokens
from .studio_navigation import ContentStack


SETTINGS_READING_WIDTH=740
SETTINGS_READING_MARGIN=24
SETTINGS_READING_NARROW_MARGIN=16


class ReadingPage(QScrollArea):
    """One reading column and gutter for every full-window settings form."""
    def __init__(self,content):
        super().__init__()
        self.setObjectName('SettingsReadingPage')
        self.content=content
        content.setMaximumWidth(SETTINGS_READING_WIDTH)
        content.setMinimumWidth(0)
        # Embedded forms inherit the page inset instead of adding another one.
        if content.layout():content.layout().setContentsMargins(0,0,0,0)
        self.container=QWidget(); self.container.setObjectName('ScrollContent')
        self.body=QHBoxLayout(self.container); self.body.setSpacing(0)
        self.body.setContentsMargins(*([SETTINGS_READING_MARGIN]*4))
        self.body.addStretch(); self.body.addWidget(content,1); self.body.addStretch()
        self.viewport().setObjectName('ScrollViewport')
        self.setFrameShape(QScrollArea.Shape.NoFrame)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        # Reserve the same track on short and long forms so headings never jump.
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOn)
        self.setWidget(self.container); content.show()

    def resizeEvent(self,event):
        margin=SETTINGS_READING_NARROW_MARGIN if self.viewport().width()<720 else SETTINGS_READING_MARGIN
        if self.body.contentsMargins().left()!=margin:self.body.setContentsMargins(*([margin]*4))
        super().resizeEvent(event)


def reading_page(widget):
    """Embed existing form controls without replacing their state or callbacks."""
    if isinstance(widget,QScrollArea):
        old=widget; widget=old.takeWidget(); old.deleteLater()
    return ReadingPage(widget)


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
        layout=QVBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(20)
        layout.addWidget(label('連線與工作流','DialogTitle'))
        box,body=panel('SettingsGroup'); layout.addWidget(box)
        body.addWidget(label('ComfyUI 連線','Heading'))
        self.address=QLineEdit(window.comfy.url); self.address.setPlaceholderText('http://127.0.0.1:8188')
        self.address.setAccessibleName('ComfyUI 伺服器網址'); self.address.setMaximumWidth(680)
        self.connect_button=button('連線',self.connect,'Primary'); self.connect_button.setIcon(icon('workflow',color='on-accent'))
        self.disconnect_button=button('中斷',window.comfy.disconnect,'Quiet')
        connection_row=row(self.address,self.connect_button,self.disconnect_button,None); connection_row.setStretch(0,1)
        body.addLayout(connection_row)
        self.status=label('','Subtle',True); body.addWidget(self.status)
        self.extension_button=button('安裝／更新 PCS 擴充',self.open_extension,'Quiet')
        body.addWidget(self.extension_button,0,Qt.AlignmentFlag.AlignLeft)
        box,body=panel('SettingsGroup'); layout.addWidget(box)
        self.mode=ComboBox(); self.mode.addItem('文生圖','txt2img'); self.mode.addItem('圖生圖','img2img')
        self.mode.currentIndexChanged.connect(self.change_mode)
        generation=window.generation_panel
        body.addWidget(ActionHeader(label('工作流','Heading'),self.mode,generation.mapping,generation.parameters,button('任務紀錄',generation.history,'Quiet')))
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
        self.connect_button.setText('重新連線' if self.window.comfy.connected else '連線')
        self.disconnect_button.setVisible(bool(self.window.comfy.enabled))
        self.mode.blockSignals(True); self.mode.setCurrentIndex(self.window.generation_panel.mode.currentIndex()); self.mode.blockSignals(False)

    def open_extension(self):
        self.window.settings('nodes')
        self.window.settings_page.manager.show_view('extension')

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


class SidebarNavigationDelegate(QStyledItemDelegate):
    def sizeHint(self,option,index):
        size=super().sizeHint(option,index);size.setHeight(size.height()+int(index.data(Qt.ItemDataRole.UserRole) or 0));return size

    def paint(self,painter,option,index):
        option=QStyleOptionViewItem(option)
        option.rect.adjust(0,0,0,-int(index.data(Qt.ItemDataRole.UserRole) or 0))
        super().paint(painter,option,index)


class NavigationSidebar(QWidget):
    """Separate upward navigation from the current area's destinations."""
    def __init__(self,return_button):
        super().__init__();self.setObjectName('ContextSidebar')
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        body=QVBoxLayout(self);body.setContentsMargins(12,12,12,12);body.setSpacing(0)
        self.back_area=QWidget();self.back_area.setObjectName('SidebarBackArea')
        self.back_area.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        back=QVBoxLayout(self.back_area);back.setContentsMargins(0,0,0,10);back.setSpacing(0)
        back.addWidget(return_button);body.addWidget(self.back_area)
        body.addSpacing(14)
        self.heading=label('設定','SidebarHeading');body.addWidget(self.heading)
        body.addSpacing(8)
        self.navigation=QListWidget();self.navigation.setObjectName('SidebarNavigation')
        self.navigation.setIconSize(QSize(18,18));self.navigation.setSpacing(2)
        self.navigation.setItemDelegate(SidebarNavigationDelegate(self.navigation))
        self.navigation.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Ignored)
        self.navigation.setMinimumHeight(0);self.navigation.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        body.addWidget(self.navigation,1)

class SettingsPage(QWidget):
    def __init__(self,window):
        super().__init__(); self.window=window; self.preferences=None; self.preference_pages=[]
        self.setObjectName('SettingsSurface'); self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground,True)
        self.section='interface'; self.nav_routes=[]; self.manual_navigation=None
        outer=QVBoxLayout(self); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        self.back_button=button('‹',window.go_back,'Quiet'); self.back_button.setToolTip('上一頁 · Alt+←')
        self.forward_button=button('›',window.go_forward,'Quiet'); self.forward_button.setToolTip('下一頁 · Alt+→')
        self.sidebar_toggle=button('',self.toggle_navigation,'Quiet'); self.sidebar_toggle.setIcon(QIcon(str(Path(__file__).parent/'assets/sidebar.svg'))); self.sidebar_toggle.setFixedSize(36,36); self.sidebar_toggle.setToolTip('收合／展開設定導覽')
        self.return_button=button('返回工作區',self.return_parent,'SettingsReturn')
        self.return_button.setIcon(QIcon(str(Path(__file__).parent/'assets/arrow-left.svg')))
        self.compact_navigation=ComboBox(); self.compact_navigation.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
        self.compact_navigation.currentIndexChanged.connect(self.compact_navigate)
        # History and sidebar controls live once in the global navigation.
        for control in (self.sidebar_toggle,self.back_button,self.forward_button):control.hide()
        content=QHBoxLayout(); content.setContentsMargins(0,0,0,0); content.setSpacing(0); outer.addLayout(content,1)
        self.navigation_area=NavigationSidebar(self.return_button)
        self.navigation_area.setFixedWidth(240)
        self.navigation=self.navigation_area.navigation; content.addWidget(self.navigation_area)
        self.reading,reading=panel('SettingsReading'); reading.setContentsMargins(0,0,0,0); content.addWidget(self.reading,1)
        self.compact_navigation.hide()
        self.pages=ContentStack(); reading.addWidget(self.pages); self.index={}
        self.interface=InterfaceChoice(window); self.add('interface','使用介面',reading_page(self.interface))
        # Preferences remain attached while the settings page autosaves edits.
        for key,title in (('appearance','介面個人化'),('completion','候選與翻譯'),('dictionary','個人字典')): self.add(key,title,QWidget())
        from .civitai_ui import CivitAIPage
        self.civitai=CivitAIPage(window); self.add('civitai','CivitAI',self.civitai)
        self.civitai.tabs.tabBar().hide()
        # A single ComfyUI entry with full-size pages selected by its top tabs.
        self.comfy_content=QWidget(); comfy=QVBoxLayout(self.comfy_content); comfy.setSpacing(24)
        comfy.setContentsMargins(0,0,0,0); self.comfy_tabs=QTabWidget(); self.comfy_tabs.tabBar().hide(); comfy.addWidget(self.comfy_tabs)
        self.workflows=WorkflowSettings(window); self.comfy_tabs.addTab(reading_page(self.workflows),'連線與工作流')
        # This full-height page owns its scrolling; no extra strip below it.
        margins=window.models.layout().contentsMargins()
        window.models.layout().setContentsMargins(margins.left(),margins.top(),margins.right(),0)
        self.comfy_tabs.addTab(window.models,'模型資產')
        from .manager_page import ManagerPage
        self.manager=ManagerPage(window)
        self.comfy_tabs.addTab(self.manager,'管理與更新')
        self.workflow_manager=self.workflows.manager
        self.add('comfy','ComfyUI',self.comfy_content)
        self.add('data','資料與備份',self.make_data_page())
        self.add('explore','探索',self.make_explore_page())
        self.comfy_tabs.currentChanged.connect(self.comfy_tab_changed)
        self.civitai.tabs.currentChanged.connect(self.civitai_tab_changed)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.autosave=QTimer(self); self.autosave.setSingleShot(True); self.autosave.setInterval(350); self.autosave.timeout.connect(self.flush)
        self.syncing=False; self.entry_appearance=None
        for choice in self.interface.choices.buttons():choice.toggled.connect(self.schedule_save)
        self.configure_navigation('interface')

    def make_explore_page(self):
        from PySide6.QtWidgets import QBoxLayout
        from .explore_art import ExploreArtwork
        page=QWidget(); layout=QVBoxLayout(page); layout.setSpacing(18)
        layout.addWidget(label('探索','DialogTitle'))
        layout.addWidget(label('連接生成工具，整理模型與工作流。','Subtle',True))
        self.explore_cards=QBoxLayout(QBoxLayout.Direction.LeftToRight); self.explore_cards.setSpacing(20); layout.addLayout(self.explore_cards)
        for name,description,key,symbol in (
            ('ComfyUI','連線與工作流、模型資產、節點更新','workflows','workflow'),
            ('CivitAI','搜尋模型、選擇版本與管理下載','civitai','explore'),
        ):
            card,body=panel('ExploreCard'); self.explore_cards.addWidget(card,1)
            card.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Fixed)
            body.setContentsMargins(24,24,24,24); body.setSpacing(14)
            mark=label(''); mark.setProperty('largeIcon',symbol)
            body.addLayout(row(mark,label(name,'ExploreTitle'),None))
            body.addWidget(label(description,'Subtle',True))
            body.addWidget(ExploreArtwork(self.window,symbol))
            entry=button('開啟 '+name,lambda checked=False,k=key:self.window.settings(k))
            entry.setProperty('iconName',symbol); body.addWidget(entry,0,Qt.AlignmentFlag.AlignLeft)
        layout.addStretch()
        return reading_page(page)

    def configure_navigation(self,section):
        if section in ('civitai','downloads','civitai_settings'):
            routes=[('civitai','搜尋模型','search'),('downloads','下載記錄','download'),('civitai_settings','瀏覽設定','settings')]
            parent='返回探索';area='CivitAI'
        elif section in ('comfy','workflows','workflow_manager','models','nodes'):
            routes=[('workflows','連線與工作流','workflow'),('models','模型資產','media'),('nodes','管理與更新','package')]
            parent='返回探索';area='ComfyUI'
        elif section=='explore':
            routes=[('explore','全部入口','explore')]; parent='返回工作區';area='探索'
        else:
            routes=[('interface','使用介面','canvas'),('appearance','介面個人化','settings'),('completion','候選與翻譯','search'),('dictionary','個人字典','info'),('data','資料與備份','package')]
            parent='返回工作區';area='設定'
        self.nav_routes=routes; self.return_button.setText(parent)
        self.navigation_area.heading.setText(area);self.navigation.setAccessibleName(area+'導覽')
        self.navigation.blockSignals(True); self.compact_navigation.blockSignals(True)
        self.navigation.clear(); self.compact_navigation.clear()
        for i,(key,title,symbol) in enumerate(routes):
            item=QListWidgetItem(title)
            if area=='設定' and i in (1,3):item.setData(Qt.ItemDataRole.UserRole,8)
            self.navigation.addItem(item); self.compact_navigation.addItem(title,key)
        current=next((i for i,r in enumerate(routes) if r[0]==section),0)
        self.navigation.setCurrentRow(current); self.compact_navigation.setCurrentIndex(current)
        self.navigation.blockSignals(False); self.compact_navigation.blockSignals(False)
        self.refresh_icons(); self.adapt_navigation()
        self.navigation_area.updateGeometry()

    def refresh_icons(self):
        color=visual_tokens(self.window.state['settings'])['text']
        for i,(_,_,symbol) in enumerate(self.nav_routes):
            self.navigation.item(i).setIcon(icon(symbol,color)); self.compact_navigation.setItemIcon(i,icon(symbol,color))
        self.return_button.setIcon(icon('back',color)); self.sidebar_toggle.setIcon(icon('menu',color))
        for entry in self.findChildren(QPushButton):
            if entry.property('iconName'):entry.setIcon(icon(entry.property('iconName'),color))
        from PySide6.QtWidgets import QLabel
        for mark in self.findChildren(QLabel):
            if mark.property('largeIcon'):mark.setPixmap(icon(mark.property('largeIcon'),color).pixmap(36,36))

    def adapt_navigation(self):
        narrow=self.width()<900
        visible=(not narrow) if self.manual_navigation is None else self.manual_navigation
        if not self.navigation_area.property('pcsSidebarBorrowed'):self.navigation_area.setVisible(visible)
        self.compact_navigation.hide()

    def resizeEvent(self,event):
        if hasattr(self,'explore_cards'):
            from PySide6.QtWidgets import QBoxLayout
            self.explore_cards.setDirection(QBoxLayout.Direction.TopToBottom if self.width()<1050 else QBoxLayout.Direction.LeftToRight)
        self.adapt_navigation(); super().resizeEvent(event)

    def return_parent(self):
        if self.section in ('civitai','downloads','civitai_settings','comfy','workflows','workflow_manager','models','nodes'):self.window.settings('explore')
        else:self.cancel()

    def compact_navigate(self,index):
        if 0<=index<len(self.nav_routes):self.window.settings(self.nav_routes[index][0])

    def comfy_tab_changed(self,index):
        if not self.syncing:self.window.settings(('workflows','models','nodes')[index])

    def civitai_tab_changed(self,index):
        if not self.syncing:self.window.settings(('civitai','downloads','civitai_settings')[index])

    def toggle_navigation(self):
        self.manual_navigation=not self.navigation_area.isVisible(); self.adapt_navigation()

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
        keys=('font_family','ui_size','prompt_size','material','visual_palette','accent','density','mica_transparency','acrylic_transparency','menu_transparency','reduce_motion','connection_style','confirm_clear_draft')
        values=self.entry_appearance if restore else DEFAULT_SETTINGS
        self.flush(); self.window.state['settings'].update({k:values[k] for k in keys if k in values})
        self.clear_preferences(); self.open('appearance'); self.window.apply_theme(preserve_layout=True); self.window.changed('settings')

    def add(self,key,title,widget):
        self.index[key]=self.pages.addWidget(widget)

    def navigate(self,index):
        if not self.syncing and 0<=index<len(self.nav_routes):self.window.settings(self.nav_routes[index][0])

    def ensure_preferences(self):
        if self.preferences is not None:return
        self.syncing=True; self.preferences=SettingsDialog(self.window)
        self.entry_appearance=self.entry_appearance or copy.deepcopy(self.window.state['settings'])
        for key in ('appearance','completion','dictionary'):
            page=self.preferences.tabs.widget(0); self.preferences.tabs.removeTab(0); page.setParent(None)
            if key=='appearance':
                inner=page.widget() if isinstance(page,QScrollArea) else page
                inner.layout().insertWidget(0,label('介面個人化','DialogTitle'))
                inner.layout().insertWidget(inner.layout().count()-1,AppearanceResetRow(button('還原開啟設定時的外觀',lambda:self.reset_appearance(True),'Quiet'),button('重設為預設外觀',self.reset_appearance,'Quiet')))
            elif key=='completion':
                inner=page.widget() if isinstance(page,QScrollArea) else page
                inner.layout().insertRow(0,label('候選與翻譯','DialogTitle'))
                inner.layout().setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
                inner.layout().setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                inner.layout().setVerticalSpacing(16)
                for control in inner.findChildren(QComboBox):
                    control.setMinimumContentsLength(6)
                    control.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
            elif key=='dictionary':
                page.layout().insertWidget(0,label('個人字典','DialogTitle'))
                self.preferences.dictionary.setMaximumHeight(480)
                page.layout().addStretch()
            old=self.pages.widget(self.index[key]); self.pages.removeWidget(old); old.deleteLater()
            page=reading_page(page); self.pages.insertWidget(self.index[key],page); self.preference_pages.append(page)
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
        if section in ('civitai','downloads'): self.civitai.refresh_history()
        sub={'workflows':0,'models':1,'nodes':2,'workflow_manager':0}.get(section)
        if sub is not None:
            self.comfy_tabs.blockSignals(True); self.comfy_tabs.setCurrentIndex(sub); self.comfy_tabs.blockSignals(False)
        civitai_sub={'civitai':0,'downloads':1,'civitai_settings':2}.get(section)
        if civitai_sub is not None:
            # syncing guards navigation recursion while the page still receives
            # its thumbnail cancellation and browse-on-entry lifecycle.
            self.civitai.tabs.setCurrentIndex(civitai_sub)
        self.section=section
        self.pages.setCurrentIndex(self.index.get('comfy' if sub is not None else 'civitai' if civitai_sub is not None else section,0))
        self.configure_navigation(section); self.syncing=False
        self.window.record_navigation('settings',section)
        if section=='models' and self.window.first_models and self.window.models.root.text():
            self.window.first_models=False; self.window.models.scan()

    def clear_preferences(self):
        if self.preferences is None: return
        for key,page in zip(('appearance','completion','dictionary'),self.preference_pages):
            self.pages.removeWidget(page); page.deleteLater(); self.pages.insertWidget(self.index[key],QWidget())
        self.preferences.deleteLater(); self.preferences=None; self.preference_pages=[]

    def reload_state(self):
        """Discard editors for the old document after an explicit data import."""
        section=self.section
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
