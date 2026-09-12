"""Main desktop window. UI calls small data helpers; no tensor or AI runtime."""
import copy
import json
from pathlib import Path
from PySide6.QtCore import Qt, QTimer, QSize, QUrl, QEvent, QVariantAnimation, QEasingCurve
from PySide6.QtGui import QIcon, QKeySequence, QShortcut, QDesktopServices
from PySide6.QtWidgets import (QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QComboBox,
    QSplitter, QListWidget, QListWidgetItem, QTreeWidget, QTreeWidgetItem, QPlainTextEdit,
    QTabWidget, QFileDialog, QAbstractItemView, QDialog, QSizeGrip, QSizePolicy, QFrame, QGraphicsOpacityEffect)
from .core import Storage, uid, build_prompt, compose_details, item_prompt, apply_workspace, validate_state, DEFAULT_SETTINGS, output_groups, reorder_output, TEMPORARY_GROUP
from .completion import CompletionService, PromptEdit
from .media import Catalog
from .jobs import Jobs
from .widgets import label, button, row, panel, ask, preview_path, WindowShell, thumb_icon, information, SplitterFold, ActionHeader, InputDialog as QInputDialog, RoundMenu as QMenu, ComboBox as QComboBox
from .views import PromptDelegate, PromptList, BuilderTree, DETAIL_ROLE
from .dialogs import ItemDialog, SettingsDialog, WorkspaceDialog, ModuleDialog, ClearDraftDialog
from .pages import ModelPage, GalleryPage
from .theme import stylesheet, apply_backdrop, update_window_shape
from .display import DisplayRecovery
from .comfy_client import ComfyClient
from .recent import RecentPage
from .run_controls import RunControls


class Window(QMainWindow):
    def __init__(self, data_dir):
        super().__init__()
        # Keep WS_CAPTION / WS_THICKFRAME. Alpha painting into a native frame
        # uses DWM redirection; FramelessWindowHint would use WS_EX_LAYERED.
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowTitleHint |
            Qt.WindowType.WindowSystemMenuHint | Qt.WindowType.WindowMinMaxButtonsHint |
            Qt.WindowType.WindowCloseButtonHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle("Prompt Studio")
        self.setWindowIcon(QIcon(str(Path(__file__).parent/"assets"/"studio.ico")))
        self.resize(1440,900); self.setMinimumSize(960,620)
        self.store=Storage(data_dir); self.state=self.store.load(); self.catalog=Catalog(self.store)
        self.completion=CompletionService(self); self.jobs=Jobs(self)
        self.updating=False; self.closing=False; self.library_page=0; self.first_models=True
        available={m["id"] for m in self.state["modules"]}
        self.current_module=self.state.get("current_module")
        if self.current_module not in available:
            self.current_module=self.state["items"][0]["module"] if self.state["items"] else next(iter(available),None)
        self.save_timer=QTimer(self); self.save_timer.setSingleShot(True); self.save_timer.timeout.connect(self.persist)
        self.filter_timer=QTimer(self); self.filter_timer.setSingleShot(True); self.filter_timer.timeout.connect(self.refresh_library)
        shell=WindowShell(self); self.setCentralWidget(shell)
        outer=QVBoxLayout(shell); outer.setContentsMargins(24,8,24,8); outer.setSpacing(4)
        content=QWidget(); content.setMaximumWidth(1560)
        center=QHBoxLayout(); center.setContentsMargins(0,0,0,0); center.addStretch(); center.addWidget(content,1); center.addStretch(); outer.addLayout(center,1)
        main=QVBoxLayout(content); main.setContentsMargins(0,12,0,0); main.setSpacing(16)
        self.workspace=QComboBox(); self.workspace.setMinimumWidth(190); self.workspace.setMaximumWidth(310); self.workspace.currentIndexChanged.connect(self.switch_workspace)
        self.tagline=label("工作區","Eyebrow")
        workspace_menu=button("⋯",self.workspace_menu,"Quiet"); workspace_menu.setFixedWidth(38); workspace_menu.setToolTip("工作區設定、新增與刪除")
        main.addLayout(row(self.tagline,self.workspace,workspace_menu,None,
            button('連接 ComfyUI',self.connect_comfy,'Quiet'),
            button("設定",self.settings,"Quiet"),button("資料與備份",self.data_menu,"Quiet")))
        self.comfy_status=label('未連線時可複製 Prompt；連線並綁定後由桌面直接運行。','Subtle',True); main.addWidget(self.comfy_status)
        self.tabs=QTabWidget(); main.addWidget(self.tabs,1)
        self.tabs.addTab(self.make_prompt_page(),"提示詞")
        self.models=ModelPage(self); self.tabs.addTab(self.models,"模型管理")
        self.gallery=GalleryPage(self); self.tabs.addTab(self.gallery,"圖片庫")
        self.recent=RecentPage(self); self.tabs.addTab(self.recent,'最近生成')
        self.comfy=ComfyClient(self); self.comfy.stateChanged.connect(self.comfy_changed); self.comfy.resultsReceived.connect(self.recent.receive_results)
        self.tabs.currentChanged.connect(self.page_changed)
        self.status=label("選擇模組，再點選項目，即可組合提示詞。","Subtle",True)
        self.status.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Preferred)
        self.cancel_button=button("取消背景工作",self.cancel_jobs,"Quiet"); self.cancel_button.hide()
        outer.addLayout(row(self.status,None,self.cancel_button,QSizeGrip(self)))
        self.refresh_workspaces(); self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.apply_theme()
        self.display_recovery=DisplayRecovery(self)
        for sequence,handler in [("Ctrl+F",self.focus_search),("Ctrl+Shift+C",self.copy_final),("Ctrl+,",self.settings)]:
            shortcut=QShortcut(QKeySequence(sequence),self); shortcut.activated.connect(handler)
        self.store.save(self.state)

    def make_prompt_page(self):
        page=QFrame(); page.setObjectName("WorkspaceSurface"); layout=QVBoxLayout(page); layout.setContentsMargins(12,16,12,16)
        self.quick=PromptEdit(self.completion); self.quick.setObjectName("QuickSearch"); self.quick.setFixedHeight(54)
        self.quick.setPlaceholderText("搜尋素材，或輸入 Tag／中文…")
        self.quick.textChanged.connect(self.search_changed); self.quick.accepted.connect(self.accept_quick)
        self.scope=QComboBox(); self.scope.addItems(["全部模組","目前模組"]); self.scope.currentIndexChanged.connect(self.search_changed)
        self.split=QSplitter(); layout.addWidget(self.split,1)
        self.module_panel,modules=panel("SidePanel"); self.split.addWidget(self.module_panel)
        self.module_panel.setMinimumWidth(150); self.module_panel.setMaximumWidth(280)
        modules.setContentsMargins(8,12,8,12)
        modules.addLayout(row(label("模組","Heading"),None,button("＋",self.new_module,"Quiet")))
        self.module_list=QListWidget(); self.module_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.module_list.setSpacing(3); self.module_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.module_list.currentItemChanged.connect(self.select_module); self.module_list.model().rowsMoved.connect(self.module_order_changed)
        self.module_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.module_list.customContextMenuRequested.connect(self.module_context)
        modules.addWidget(self.module_list,1)
        modules.addWidget(label("拖曳名稱調整順序","Eyebrow",True))
        modules.addWidget(button("編輯模組",self.edit_module,"Quiet"))
        self.library_panel,library=panel(); self.split.addWidget(self.library_panel)
        self.library_title=label("素材庫","Heading")
        library.setContentsMargins(8,12,14,12)
        toggle=button("☰",self.toggle_modules,"Quiet"); toggle.setToolTip("顯示／收合模組"); toggle.setFixedWidth(36)
        library.addLayout(row(toggle,self.library_title,None,button("＋ 新增",self.new_item)))
        self.library_hint=label("","Subtle",True); library.addWidget(self.library_hint)
        library.addWidget(self.quick)
        library.addLayout(row(self.scope,None,button("＋ 加入片段",lambda:self.add_temporary(self.quick.toPlainText(),clear=True),"Quiet")))
        self.library=PromptList(); self.library.weightRequested.connect(self.adjust_weight); self.library.setIconSize(QSize(88,88)); self.library.itemClicked.connect(self.toggle_item); self.library.itemDoubleClicked.connect(lambda item:self.edit_item(item.data(Qt.ItemDataRole.UserRole)))
        self.library.setItemDelegate(PromptDelegate(self)); self.library.setMouseTracking(True)
        self.library.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.library.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.library.customContextMenuRequested.connect(self.item_context)
        library.addWidget(self.library,1)
        self.library_count=label("","Subtle")
        self.library_previous=button("‹",lambda:self.turn_library(-1),"Quiet"); self.library_next=button("›",lambda:self.turn_library(1),"Quiet")
        library.addLayout(row(self.library_count,None,self.library_previous,self.library_next))
        builder,right=panel("InsetPanel"); self.split.addWidget(builder); self.split.setSizes([190,565,485])
        self.split.setChildrenCollapsible(False)
        self.builder_toggle=button("收合",self.toggle_builder,"Quiet")
        self.selection_count=label("","Eyebrow")
        self.selection_heading=label("目前組合","Heading")
        right.addLayout(row(self.selection_heading,self.selection_count,None,self.builder_toggle))
        self.builder_hint=label("拖曳群組或項目，調整輸出順序","Subtle",True); right.addWidget(self.builder_hint)
        self.builder_split=QSplitter(Qt.Orientation.Vertical); right.addWidget(self.builder_split,1)
        self.selected=BuilderTree(); self.selected.setMinimumHeight(90)
        self.selected.moveRequested.connect(self.reorder_builder); self.selected.removeRequested.connect(self.remove_builder_entry); self.selected.rejected.connect(self.notice)
        self.selected.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.selected.customContextMenuRequested.connect(self.builder_context)
        self.builder_split.addWidget(self.selected)
        output=QWidget(); out=QVBoxLayout(output); out.setContentsMargins(0,6,0,0); out.setSpacing(10)
        divider=QFrame(); divider.setObjectName("SoftDivider"); divider.setFixedHeight(1); out.addWidget(divider)
        self.draft_status=label("自動組合","Subtle",True)
        self.save_prompt=button("存入素材庫",lambda:self.new_item(self.final.toPlainText()),"Quiet")
        self.clear_draft=button("清除內容",self.regenerate,"ClearDraft")
        self.clear_draft.setToolTip("清除手動版本，恢復目前組合的提示詞")
        out.addWidget(ActionHeader(label("最終 Prompt","Heading"),self.save_prompt,self.clear_draft))
        out.addWidget(self.draft_status)
        self.final=QPlainTextEdit(); self.final.setObjectName("Prompt"); self.final.setPlaceholderText("選擇素材，或搜尋後加入臨時片段。\n也可以直接在這裡編輯提示詞。")
        self.final.setMinimumHeight(130)
        self.final.textChanged.connect(self.final_edited); out.addWidget(self.final,1)
        self.builder_split.addWidget(output); self.builder_split.setSizes([250,330])
        self.conflict_notice=label('','ConflictNotice',True); self.conflict_notice.hide(); out.addWidget(self.conflict_notice)
        self.run_controls=RunControls(self,copy_fallback=True); self.copy_button=self.run_controls.run_button; right.addWidget(self.run_controls)
        self.copy_button.setMinimumHeight(44)
        self.copy_timer=QTimer(self); self.copy_timer.setSingleShot(True); self.copy_timer.timeout.connect(self.reset_copy_feedback)
        self.latest_preview=button('查看最近生成',lambda:self.tabs.setCurrentWidget(self.recent),'Quiet'); self.latest_preview.setIconSize(QSize(96,72)); self.latest_preview.hide(); right.addWidget(self.latest_preview)
        self.module_fold=SplitterFold(self.split,self.module_panel)
        self.builder_fold=SplitterFold(self.builder_split,self.selected)
        self.draft_active=None; self.draft_fade=QVariantAnimation(self); self.draft_fade.setDuration(180)
        self.draft_fade.setEasingCurve(QEasingCurve.Type.OutCubic); self.draft_fade.valueChanged.connect(self.set_combination_opacity)
        self.selection_effects=[]
        for widget in (self.selection_heading,self.selection_count,self.builder_hint):
            effect=QGraphicsOpacityEffect(widget); effect.setOpacity(1); widget.setGraphicsEffect(effect); self.selection_effects.append(effect)
        return page

    def notice(self,text):
        if hasattr(self,"status"): self.status.setText(text)
        if hasattr(self,"cancel_button"): self.cancel_button.setVisible(bool(self.jobs.active))

    def error(self,text):
        information(self,"未能完成",text)

    def changed(self):
        self.save_timer.start(350)
        if hasattr(self,'comfy'): self.comfy.schedule_sync()

    def connect_comfy(self):
        address,ok=QInputDialog.getText(self,'連接 ComfyUI','填入本機網址；在 ComfyUI 綁定文字節點後，按「交給桌面版控制」。\n清空網址可中斷桌面連線。',text=self.comfy.url)
        if not ok: return
        if not address.strip(): self.comfy.disconnect(); return
        try: self.comfy.connect_to(address)
        except ValueError as exc: self.notice(str(exc))

    def comfy_changed(self):
        self.comfy_status.setText(self.comfy.message); self.run_controls.refresh(); self.recent.run_controls.refresh(); self.recent.update_save_button()

    def show_latest_generated(self,record):
        self.latest_preview.setIcon(thumb_icon(self.store,record.get('thumb'),96)); self.latest_preview.show()

    def persist(self):
        try:
            self.store.save(self.state)
            return True
        except Exception as exc:
            self.error("資料儲存失敗，請先匯出備份。\n"+str(exc))
            return False

    def apply_theme(self, preserve_layout=False, refresh_fonts=False):
        appearance=dict(self.state["settings"])
        appearance.update(getattr(self,"appearance_preview",{}))
        self.native_material=apply_backdrop(self,appearance["material"])
        if not self.native_material: appearance["material"]="solid"
        if refresh_fonts: self.setStyleSheet("")
        self.setStyleSheet(stylesheet(appearance))
        update_window_shape(self)
        self.tagline.setVisible(appearance["ui_size"]<16)
        self.quick.setFixedHeight(max(54,int(appearance["ui_size"]*2.4+22)))
        self.library.doItemsLayout()
        self.selected.doItemsLayout()
        if not preserve_layout and ((appearance["ui_size"]>=16 and self.height()<820) or self.height()<740):
            self.builder_fold.set_expanded(False,animated=False); self.builder_hint.hide(); self.builder_toggle.setText("展開組合")
        if not preserve_layout and appearance["ui_size"]>=16 and self.width()<1160: self.module_fold.set_expanded(False,animated=False)

    def showEvent(self,event):
        super().showEvent(event); self.apply_theme(preserve_layout=True)
        if hasattr(self,"display_recovery"): self.display_recovery.bind_window()

    def event(self,event):
        if event.type() in (QEvent.Type.DevicePixelRatioChange,QEvent.Type.ScreenChangeInternal):
            if hasattr(self,"display_recovery"): self.display_recovery.schedule("qt-display-change")
        return super().event(event)

    def nativeEvent(self,event_type,message):
        if hasattr(self,"display_recovery"):
            self.display_recovery.native_event(event_type,message)
        return super().nativeEvent(event_type,message)

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,"state"): update_window_shape(self)

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.WindowStateChange and hasattr(self,"state"):
            update_window_shape(self)

    def refresh_workspaces(self):
        self.workspace.blockSignals(True); self.workspace.clear()
        for workspace in self.state["workspaces"]: self.workspace.addItem(workspace["name"],workspace["id"])
        self.workspace.setCurrentIndex(self.workspace.findData(self.state["workspace"])); self.workspace.blockSignals(False)

    def switch_workspace(self):
        if self.workspace.currentData():
            apply_workspace(self.state,self.workspace.currentData()); self.refresh_library(); self.refresh_builder(); self.changed()
            self.notice("已還原此工作區的固定模組；其他選擇與臨時片段保留。")

    def workspace_settings(self):
        WorkspaceDialog(self).exec()

    def workspace_menu(self):
        menu=QMenu(self); menu.addAction("工作區設定",self.workspace_settings); menu.addAction("新增工作區…",self.new_workspace)
        menu.addSeparator(); menu.addAction("刪除目前工作區…",self.delete_workspace); menu.open_at(self.cursor().pos())

    def new_workspace(self):
        name,ok=QInputDialog.getText(self,"新增工作區","名稱，例如：Anima2")
        if ok and name.strip():
            workspace=dict(id=uid(),name=name.strip(),fixed=[],picks={},parameters={},history=[])
            self.state["workspaces"].append(workspace); self.state["workspace"]=workspace["id"]
            self.refresh_workspaces(); self.changed(); self.workspace_settings()

    def delete_workspace(self):
        if len(self.state["workspaces"])==1: self.notice("至少保留一個工作區。"); return
        if ask(self,"刪除工作區","移除此工作區及參數歷史？素材庫、圖片與模型不會刪除。"):
            self.state["workspaces"]=[w for w in self.state["workspaces"] if w["id"]!=self.state["workspace"]]
            apply_workspace(self.state,self.state["workspaces"][0]["id"]); self.refresh_workspaces(); self.refresh_library(); self.refresh_builder(); self.changed()

    def settings(self):
        SettingsDialog(self).exec()

    def refresh_modules(self):
        self.module_list.blockSignals(True); self.module_list.clear()
        for module in self.state["modules"]:
            mode="單選" if module["mode"]=="single" else "複選"
            item=QListWidgetItem(module['name']); item.setToolTip(f"{mode} · 拖曳調整順序，右鍵複製或編輯")
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
            item.setData(Qt.ItemDataRole.UserRole,module["id"]); self.module_list.addItem(item)
            if module["id"]==self.current_module: self.module_list.setCurrentItem(item)
        self.module_list.blockSignals(False)

    def select_module(self,item):
        self.current_module=item.data(Qt.ItemDataRole.UserRole) if item else None; self.library_page=0
        self.state["current_module"]=self.current_module; self.changed()
        self.refresh_library()

    def module_order_changed(self):
        if self.updating: return
        mapping={m["id"]:m for m in self.state["modules"]}
        self.state["modules"]=[mapping[self.module_list.item(i).data(Qt.ItemDataRole.UserRole)] for i in range(self.module_list.count())]
        self.refresh_builder(); self.changed()

    def new_module(self):
        dialog=ModuleDialog(self)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            module=dict(id=uid(),name=dialog.name.text().strip(),mode=dialog.mode.currentData())
            self.state["modules"].append(module); self.current_module=module["id"]; self.refresh_modules(); self.refresh_library(); self.changed()

    def edit_module(self):
        module=next((m for m in self.state["modules"] if m["id"]==self.current_module),None)
        if not module: return
        dialog=ModuleDialog(self,module)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            module.update(name=dialog.name.text().strip(),mode=dialog.mode.currentData())
            if module["mode"]=="single":
                for picks in [self.state["selections"]]+[w["picks"] for w in self.state["workspaces"]]:
                    if module["id"] in picks: picks[module["id"]]=picks[module["id"]][:1]
            self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.changed()

    def delete_module(self):
        mid=self.current_module
        module=next((m for m in self.state["modules"] if m["id"]==mid),None)
        if not module or not ask(self,"刪除模組",f"刪除「{module['name']}」及其全部提示詞項目？此動作不會刪除模型或圖片原檔。"): return
        self.state["modules"]=[m for m in self.state["modules"] if m["id"]!=mid]
        if self.state.get("temporary_before")==mid: self.state["temporary_before"]=None
        self.state["items"]=[i for i in self.state["items"] if i["module"]!=mid]; self.state["selections"].pop(mid,None)
        for w in self.state["workspaces"]:
            w["fixed"]=[m for m in w["fixed"] if m!=mid]; w["picks"].pop(mid,None)
        self.current_module=self.state["modules"][0]["id"] if self.state["modules"] else None
        self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.changed()

    def module_context(self,pos):
        item=self.module_list.itemAt(pos)
        if not item: return
        self.module_list.setCurrentItem(item); menu=QMenu(self)
        menu.addAction("複製本模組已選提示詞",lambda:self.copy_module(self.current_module))
        menu.addAction("複製本模組全部提示詞",lambda:self.copy_module(self.current_module,True))
        menu.addSeparator()
        menu.addAction('向上移動',lambda:self.move_module(-1)); menu.addAction('向下移動',lambda:self.move_module(1)); menu.addAction('移到最上方',lambda:self.move_module(-10000))
        menu.addSeparator(); menu.addAction("編輯名稱與選擇模式",self.edit_module); menu.addAction("清除此模組選擇",self.clear_module); menu.addAction("刪除模組…",self.delete_module)
        menu.open_at(self.module_list.viewport().mapToGlobal(pos))

    def copy_module(self,mid,all_items=False):
        items={i["id"]:i for i in self.state["items"]}
        ids=[i["id"] for i in self.state["items"] if i["module"]==mid] if all_items else self.state["selections"].get(mid,[])
        self.copy_text(", ".join(items[i]["prompt"] for i in ids))

    def move_module(self,delta):
        index=next((i for i,m in enumerate(self.state['modules']) if m['id']==self.current_module),-1)
        if index<0: return
        modules=self.state['modules']; target=max(0,min(len(modules)-1,index+delta))
        modules.insert(target,modules.pop(index)); self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.changed()

    def move_library_item(self,ident,delta):
        original=next(i for i in self.state['items'] if i['id']==ident)
        group=[i for i in self.state['items'] if i['module']==original['module']]
        index=next(n for n,i in enumerate(group) if i['id']==ident); target=max(0,min(len(group)-1,index+delta))
        group.insert(target,group.pop(index)); ordered=iter(group)
        self.state['items']=[next(ordered) if i['module']==original['module'] else i for i in self.state['items']]
        self.refresh_library(); self.changed()

    def clear_module(self):
        self.state["selections"][self.current_module]=[]; self.refresh_library(); self.refresh_builder(); self.changed()

    def search_changed(self):
        self.library_page=0; self.filter_timer.start(150)

    def turn_library(self,delta):
        self.library_page=max(0,self.library_page+delta); self.refresh_library()

    def refresh_library(self):
        if not hasattr(self,"library"): return
        self.library.clear()
        query=self.quick.toPlainText().strip().casefold()
        modules={m["id"]:m for m in self.state["modules"]}
        _,affected=compose_details(self.state)
        found=[]
        for item in self.state["items"]:
            if (not query or self.scope.currentIndex()==1) and item["module"]!=self.current_module: continue
            if query and query not in " ".join([item["name"],item["prompt"],item["notes"],*item["aliases"]]).casefold(): continue
            found.append(item)
        self.library_page=min(self.library_page,max(0,(len(found)-1)//60))
        self.library_title.setText("搜尋結果" if query else modules.get(self.current_module,{}).get("name","素材庫"))
        mode=modules.get(self.current_module,{}).get("mode")
        self.library_hint.setText("名稱、中文別名與 Prompt 都能搜尋" if query else "單選 · 選擇另一項會替換目前選擇" if mode=="single" else "複選 · 將常用項目自由組合")
        for item in found[self.library_page*60:(self.library_page+1)*60]:
            chosen=item["id"] in self.state["selections"].get(item["module"],[])
            prompt=item["prompt"].replace("\n"," ")
            text=("[已選]  " if chosen else "＋  ")+item["name"]+"\n"+prompt[:140]+("…" if len(prompt)>140 else "")
            if query: text+="\n"+modules[item["module"]]["name"]
            widget=QListWidgetItem(text); widget.setData(Qt.ItemDataRole.UserRole,item["id"]); widget.setToolTip(item["prompt"])
            widget.setIcon(thumb_icon(self.store,item.get("preview"),88))
            detail=dict(item,chosen=chosen,module_name=modules[item["module"]]["name"],weight=self.state.get('weights',{}).get(item['id'],10))
            detail['affected']=affected.get(item['id'])
            if detail['affected']:
                widget.setToolTip(item['prompt']+'\n\n暫時停用：'+', '.join(detail['affected']['tags'])+'\n來源：'+', '.join(detail['affected']['by']))
            detail['prompt']=item_prompt(self.state,item); widget.setData(DETAIL_ROLE,detail)
            self.library.addItem(widget)
        self.library_count.setText(f"{len(found)} 個項目 · 第 {self.library_page+1} 頁")
        self.library_previous.setVisible(len(found)>60); self.library_next.setVisible(len(found)>60)
        self.library_previous.setEnabled(self.library_page>0); self.library_next.setEnabled((self.library_page+1)*60<len(found))

    def adjust_weight(self,ident,delta):
        if self.state['draft'] is not None:
            self.notice('正在使用手動版本；清除手動內容後才能調整權重。'); return
        weights=self.state.setdefault('weights',{})
        weights[ident]=max(0,min(1000,weights.get(ident,10)+delta))
        self.refresh_library(); self.refresh_builder(); self.changed()

    def toggle_item(self,widget):
        item=next(i for i in self.state["items"] if i["id"]==widget.data(Qt.ItemDataRole.UserRole))
        module=next(m for m in self.state["modules"] if m["id"]==item["module"])
        selected=self.state["selections"].setdefault(module["id"],[])
        if item["id"] in selected: selected.remove(item["id"])
        elif module["mode"]=="single": self.state["selections"][module["id"]]=[item["id"]]
        else: selected.append(item["id"])
        self.refresh_library(); self.refresh_builder(); self.changed()

    def new_item(self,initial=""):
        if isinstance(initial,bool): initial=""
        if not self.state["modules"]: self.notice("請先新增模組。"); return
        dialog=ItemDialog(self,initial=initial)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            self.state["items"].append(dialog.item); self.current_module=dialog.item["module"]
            self.refresh_modules(); self.refresh_library(); self.changed()

    def edit_item(self,ident):
        original=next(i for i in self.state["items"] if i["id"]==ident)
        dialog=ItemDialog(self,original)
        if dialog.exec()==QDialog.DialogCode.Accepted:
            if original["module"]!=dialog.item["module"]: self.remove_selection(ident)
            self.state["items"]=[dialog.item if i["id"]==ident else i for i in self.state["items"]]
            self.refresh_library(); self.refresh_builder(); self.changed()

    def remove_selection(self,ident):
        for picks in [self.state["selections"]]+[w["picks"] for w in self.state["workspaces"]]:
            for mid in picks: picks[mid]=[i for i in picks[mid] if i!=ident]

    def delete_item(self,ident):
        if ask(self,"刪除提示詞","刪除此素材庫項目？相關模型與原圖不會刪除。"):
            self.remove_selection(ident); self.state["items"]=[i for i in self.state["items"] if i["id"]!=ident]
            self.refresh_library(); self.refresh_builder(); self.changed()

    def item_context(self,pos):
        widget=self.library.itemAt(pos)
        if not widget: return
        ident=widget.data(Qt.ItemDataRole.UserRole); item=next(i for i in self.state["items"] if i["id"]==ident)
        menu=QMenu(self); menu.addAction("複製此提示詞",lambda:self.copy_text(item["prompt"])); menu.addAction("加入／取消選擇",lambda:self.toggle_item(widget))
        menu.addSeparator(); menu.addAction('向上移動',lambda:self.move_library_item(ident,-1)); menu.addAction('向下移動',lambda:self.move_library_item(ident,1)); menu.addAction('移到最上方',lambda:self.move_library_item(ident,-10000))
        menu.addSeparator()
        menu.addAction("編輯提示詞與預覽圖",lambda:self.edit_item(ident)); menu.addSeparator(); menu.addAction("刪除項目…",lambda:self.delete_item(ident)); menu.open_at(self.library.viewport().mapToGlobal(pos))

    def accept_quick(self,_row):
        self.add_temporary(self.quick.toPlainText(),clear=True)

    def add_temporary(self,text,clear=False):
        text=text.strip().strip(",， ")
        if not text: self.notice("先輸入或選取要加入的提示詞。"); return
        self.state.setdefault("temporary",[]).append(text)
        if clear: self.quick.clear(); self.quick.completer.popup().hide()
        self.refresh_builder(); self.changed()
        self.notice("已加入臨時片段。"+ ("正在使用手動版本；清除手動內容後，會套用更新的組合。" if self.state["draft"] is not None else "可在右側組合中排序、複製或移除。"))

    def refresh_builder(self):
        if not hasattr(self,"selected"): return
        self.state["output_order"]=output_groups(self.state)
        self.reset_copy_feedback()
        collapsed={self.selected.topLevelItem(i).data(0,Qt.ItemDataRole.UserRole)[1] for i in range(self.selected.topLevelItemCount()) if not self.selected.topLevelItem(i).isExpanded()}
        self.selected.clear(); items={i["id"]:i for i in self.state["items"]}
        modules={m["id"]:m for m in self.state["modules"]}; count=0
        for mid in output_groups(self.state):
            if mid==TEMPORARY_GROUP:
                if not self.state["temporary"]: continue
                group=QTreeWidgetItem(["臨時片段"]); group.setData(0,Qt.ItemDataRole.UserRole,("group",TEMPORARY_GROUP)); self.selected.addTopLevelItem(group)
                for index,text in enumerate(self.state["temporary"]):
                    child=QTreeWidgetItem([text]); child.setToolTip(0,text); child.setData(0,Qt.ItemDataRole.UserRole,("temporary",index)); group.addChild(child); count+=1
                group.setExpanded(mid not in collapsed)
                continue
            module=modules[mid]; ids=self.state["selections"].get(mid,[])
            if not ids: continue
            group=QTreeWidgetItem([module["name"]]); group.setData(0,Qt.ItemDataRole.UserRole,("group",mid)); self.selected.addTopLevelItem(group)
            for ident in ids:
                weight=self.state.get('weights',{}).get(ident,10)
                name=items[ident]['name']+(f' · ×{weight/10:.1f}' if weight!=10 else '')
                child=QTreeWidgetItem([name]); child.setData(0,Qt.ItemDataRole.UserRole,("item",ident)); child.setToolTip(0,item_prompt(self.state,items[ident])); group.addChild(child); count+=1
            group.setExpanded(mid not in collapsed)
        self.selection_count.setText(f"{count} 項")
        generated=build_prompt(self.state)
        _,affected=compose_details(self.state)
        affected_names={i['id']:i['name'] for i in self.state['items']}
        notices=[affected_names.get(key,'臨時片段')+'：'+', '.join(detail['tags']) for key,detail in affected.items()]
        self.conflict_notice.setText(('手動版本保留原文；自動組合將停用：' if self.state['draft'] is not None else '已暫時停用衝突 Tag：')+'；'.join(notices))
        self.conflict_notice.setVisible(bool(notices))
        self.updating=True
        self.final.setPlainText(self.state["draft"] if self.state["draft"] is not None else generated)
        self.updating=False
        self.update_draft_status()

    def update_draft_status(self):
        draft=self.state["draft"] is not None
        text="正在使用手動版本" if draft else "自動組合 · 依照上方順序輸出"
        if draft and build_prompt(self.state)!=self.state.get("draft_base",""): text+=" · 清除後套用更新的組合"
        self.draft_status.setText(text); self.draft_status.setObjectName("Draft" if draft else "Subtle")
        self.draft_status.style().unpolish(self.draft_status); self.draft_status.style().polish(self.draft_status)
        self.clear_draft.setEnabled(draft)
        self.selected.setEnabled(not draft)
        self.builder_hint.setText("清除手動內容後，可繼續調整這份組合" if draft else "拖曳群組或項目，調整輸出順序")
        if self.draft_active!=draft:
            self.draft_fade.stop(); target=0.42 if draft else 1.0
            if self.draft_active is None: self.set_combination_opacity(target)
            else:
                self.draft_fade.setStartValue(self.selected.activity); self.draft_fade.setEndValue(target); self.draft_fade.start()
            self.draft_active=draft
        self.copy_button.setEnabled(bool(self.final.toPlainText().strip()))
        self.save_prompt.setEnabled(bool(self.final.toPlainText().strip()))
        if hasattr(self,'comfy'): self.run_controls.refresh(); self.recent.run_controls.refresh()

    def set_combination_opacity(self,value):
        self.selected.activity=value; self.selected.viewport().update()
        for effect in self.selection_effects: effect.setOpacity(value)

    def final_edited(self):
        if self.updating: return
        self.reset_copy_feedback()
        if self.state["draft"] is None: self.state["draft_base"]=build_prompt(self.state)
        self.state["draft"]=self.final.toPlainText(); self.update_draft_status(); self.changed()

    def regenerate(self):
        if self.state["draft"] is None: return
        if self.state["settings"].get("confirm_clear_draft",True):
            dialog=ClearDraftDialog(self)
            if dialog.exec()!=QDialog.DialogCode.Accepted: return
            if dialog.dont_ask_again.isChecked(): self.state["settings"]["confirm_clear_draft"]=False
        self.state["draft"]=None; self.state["draft_base"]=""; self.refresh_builder(); self.changed()

    def builder_context(self,pos):
        if not self.selected.isEnabled(): return
        item=self.selected.itemAt(pos)
        if not item: return
        data=item.data(0,Qt.ItemDataRole.UserRole)
        if not data: return
        self.selected.setCurrentItem(item)
        kind,ident=data; menu=QMenu(self)
        menu.addAction("向上移動",lambda:self.selected.move_current(-1)); menu.addAction("向下移動",lambda:self.selected.move_current(1)); menu.addSeparator()
        if kind=="group":
            if ident==TEMPORARY_GROUP: menu.addAction("複製所有臨時片段",lambda:self.copy_text(", ".join(self.state["temporary"])))
            else: menu.addAction("複製本模組已選提示詞",lambda:self.copy_module(ident))
        elif kind=="item":
            prompt=next(i["prompt"] for i in self.state["items"] if i["id"]==ident)
            menu.addAction("複製",lambda:self.copy_text(prompt)); menu.addAction("編輯",lambda:self.edit_item(ident))
            def remove():
                for mid,ids in self.state["selections"].items(): self.state["selections"][mid]=[i for i in ids if i!=ident]
                self.refresh_library(); self.refresh_builder(); self.changed()
            menu.addAction("取消選擇",remove)
        else:
            prompt=self.state["temporary"][ident]; menu.addAction("複製",lambda:self.copy_text(prompt)); menu.addAction("存入素材庫",lambda:self.new_item(prompt))
            def remove_temp():
                self.state["temporary"].pop(ident); self.refresh_builder(); self.changed()
            menu.addAction("移除此片段",remove_temp)
        menu.open_at(self.selected.viewport().mapToGlobal(pos))

    def reorder_builder(self,source,target,after=False):
        try:
            moved=reorder_output(self.state,tuple(source),tuple(target),after)
        except ValueError as exc: self.notice(str(exc)); return
        self.refresh_builder(); self.changed()
        for n in range(self.selected.topLevelItemCount()):
            group=self.selected.topLevelItem(n)
            for item in [group]+[group.child(i) for i in range(group.childCount())]:
                if tuple(item.data(0,Qt.ItemDataRole.UserRole))==moved:
                    self.selected.setCurrentItem(item); self.selected.scrollToItem(item)
        self.notice("順序已更新。"+("正在使用手動版本；清除手動內容後套用。" if self.state["draft"] is not None else "最終 Prompt 已同步更新。"))

    def remove_builder_entry(self,key):
        kind,ident=key
        if kind=="temporary": self.state["temporary"].pop(ident)
        elif kind=="item":
            for ids in self.state["selections"].values():
                if ident in ids: ids.remove(ident)
        self.refresh_library(); self.refresh_builder(); self.changed()

    def copy_text(self,text):
        if not text.strip(): self.notice("目前沒有可複製的提示詞。"); return False
        from PySide6.QtWidgets import QApplication
        QApplication.clipboard().setText(text); self.notice("已複製到剪貼簿。")
        return True

    def copy_final(self):
        if hasattr(self,'comfy') and self.comfy.ready:
            if self.recent.ensure_destination(): self.comfy.run(self.state['settings'].get('comfy_count',1))
            return
        if self.copy_text(self.final.toPlainText()):
            self.copy_button.setText("✓ 已複製"); self.copy_button.setProperty("feedback",True)
            self.copy_button.style().unpolish(self.copy_button); self.copy_button.style().polish(self.copy_button)
            self.copy_timer.start(1400)

    def reset_copy_feedback(self):
        self.copy_timer.stop(); self.copy_button.setText("複製完整 Prompt"); self.copy_button.setProperty("feedback",False)
        self.copy_button.style().unpolish(self.copy_button); self.copy_button.style().polish(self.copy_button)
        if hasattr(self,'run_controls'): self.run_controls.refresh()

    def toggle_modules(self): self.module_fold.toggle()
    def toggle_builder(self):
        self.builder_fold.toggle()
        self.builder_hint.setVisible(self.builder_fold.expanded)
        self.builder_toggle.setText("收合" if self.builder_fold.expanded else "展開組合")
    def focus_search(self): self.tabs.setCurrentIndex(0); self.quick.setFocus(); self.quick.selectAll()

    def page_changed(self,index):
        if index==1 and self.first_models:
            self.first_models=False; self.models.scan()
        if index==2: self.gallery.refresh_albums()
        if index==3: self.recent.refresh_destinations(); self.recent.request_refresh()

    def cancel_jobs(self):
        self.gallery.queue=[]
        if self.jobs.active: self.jobs.active.cancel.set(); self.notice("已要求取消，正在完成目前檔案的收尾。")

    def data_menu(self):
        menu=QMenu(self); menu.addAction("匯出 JSON（文字與檔案索引）",self.export_json); menu.addAction("匯入 JSON…",self.import_json)
        menu.addAction("備份資料庫與圖片副本（ZIP）",self.backup_zip)
        menu.addAction("開啟資料與縮圖資料夾",lambda:QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.store.directory))))
        menu.addSeparator(); menu.addAction("刪除目前工作區…",self.delete_workspace); menu.open_at(self.cursor().pos())

    def backup_zip(self):
        if self.jobs.active or self.gallery.importing: self.notice("請先等待背景工作完成。"); return
        path=QFileDialog.getSaveFileName(self,"備份資料（不含連結的外部原圖與模型檔）","prompt-studio-backup.zip","ZIP (*.zip)")[0]
        if not path: return
        try:
            self.models.save(); self.persist(); snapshot=self.store.backup()
            from .backup import archive_data
            self.jobs.start("正在備份資料庫、縮圖與應用內的圖片副本…",lambda cancel:archive_data(self.store.directory,snapshot,path,cancel),lambda _:self.notice("ZIP 備份完成；外部連結的原圖與模型需另外備份。"))
        except Exception as exc: self.error(str(exc))

    def export_json(self):
        path=QFileDialog.getSaveFileName(self,"匯出資料索引","prompt-studio.json","JSON (*.json)")[0]
        if not path: return
        try:
            self.models.save(); self.persist()
            # Stream metadata rather than keeping the whole image catalog in RAM.
            with Path(path).open("w",encoding="utf-8") as output:
                output.write('{"format":"prompt-studio","version":1,"state":')
                json.dump(self.state,output,ensure_ascii=False)
                output.write(',"resources":[')
                for index,r in enumerate(self.store.db.execute("SELECT * FROM resources")):
                    if index: output.write(",")
                    json.dump(dict(id=r[0],kind=r[1],parent=r[2],name=r[3],body=json.loads(r[4])),output,ensure_ascii=False)
                output.write("]}")
            self.notice("JSON 已匯出；圖片檔案不內嵌其中，完整備份請另存 data 資料夾與原圖。")
        except Exception as exc: self.error(str(exc))

    def import_json(self):
        if self.jobs.active or self.gallery.importing: self.notice("請先等待背景工作完成。"); return
        path=QFileDialog.getOpenFileName(self,"匯入資料索引","","JSON (*.json)")[0]
        if not path: return
        try:
            if Path(path).stat().st_size>64*1024*1024: raise ValueError("JSON 超過 64 MB，請改用資料庫備份還原。")
            bundle=json.loads(Path(path).read_text(encoding="utf-8-sig"))
            state=validate_state(bundle["state"] if bundle.get("format")=="prompt-studio" else bundle)
            resources=bundle.get("resources",[]) if bundle.get("format")=="prompt-studio" else []
            from .validation import validate_resources
            validate_resources(resources)
            if not ask(self,"匯入資料",f"以備份取代目前的素材與檔案索引？\n共 {len(state['items'])} 個提示詞、{len(resources)} 筆模型／圖片資料。\n\n會先建立 SQLite 備份；不複製、搬移或刪除外部原檔。"): return
            self.save_timer.stop(); self.models.save(); backup=self.store.backup()
            self.completion.serial+=1; self.completion.timer.stop()
            with self.store.db:
                self.store.db.execute("DELETE FROM resources")
                for r in resources: self.store.db.execute("INSERT INTO resources VALUES (?,?,?,?,?)",(r["id"],r["kind"],r["parent"],r["name"],json.dumps(r["body"],ensure_ascii=False)))
                self.store.db.execute("INSERT OR REPLACE INTO document VALUES (1,?)",(json.dumps(state,ensure_ascii=False),))
            self.state=self.store.load(); self.current_module=self.state["modules"][0]["id"] if self.state["modules"] else None
            self.models.record=None; self.models.root.setText(self.state["settings"]["model_root"])
            self.models.loading=True; self.models.category.clear(); self.models.category.addItems(self.state["settings"]["model_categories"]); self.models.loading=False
            self.models.refresh_categories(); self.models.refresh()
            self.gallery.album=None; self.gallery.refresh_albums(); self.refresh_workspaces(); self.refresh_modules(); self.refresh_library(); self.refresh_builder(); self.apply_theme()
            self.notice(f"已匯入；匯入前的資料庫備份：{backup.name}")
        except Exception as exc: self.error(str(exc))

    def closeEvent(self,event):
        if self.jobs.active or self.completion.tasks or self.gallery.importing or (hasattr(self,'recent') and self.recent.collecting):
            event.ignore(); self.closing=True; self.gallery.queue=[]
            if self.jobs.active: self.jobs.active.cancel.set()
            self.completion.serial+=1; self.completion.timer.stop()
            self.notice("正在收尾背景工作，完成後關閉。")
            QTimer.singleShot(300,self.close); return
        try: self.models.save()
        except Exception as exc:
            self.error(str(exc)); event.ignore(); return
        self.save_timer.stop()
        if not self.persist(): event.ignore(); return
        self.comfy.shutdown(); self.recent.refresh_timer.stop(); self.display_recovery.stop(); self.store.close(); event.accept()
