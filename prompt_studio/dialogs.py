import copy
import json
import time
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QLineEdit, QComboBox,
    QPlainTextEdit, QCheckBox, QSpinBox, QFontComboBox, QTabWidget, QWidget,
    QListWidget, QListWidgetItem, QScrollArea, QSlider, QAbstractItemView)
from .widgets import label, button, row, dialog_buttons, image_path, set_preview, ask, scrolling, panel, StudioDialog, CheckList, InputDialog as QInputDialog, ComboBox as QComboBox
from .completion import PromptEdit
from .core import uid, DEFAULT_SETTINGS, set_selection_separation
from .media import thumbnail
from .image_drop import ImageDropLabel


class ClearDraftDialog(StudioDialog):
    def __init__(self, window):
        super().__init__(window)
        self.setWindowTitle("清除手動內容"); self.resize(530,290)
        self.body.addWidget(label("清除目前的手動版本，恢復上方組合的提示詞？\n已選的模組與臨時片段會保留。",None,True))
        self.dont_ask_again=QCheckBox("不再提示")
        self.body.addWidget(self.dont_ask_again)
        confirm=button("清除內容",self.accept,"ClearDraft"); cancel=button("取消",self.reject)
        confirm.setMinimumWidth(88); cancel.setMinimumWidth(88)
        self.body.addLayout(row(None,cancel,confirm)); cancel.setDefault(True); cancel.setFocus()


class ModuleDialog(StudioDialog):
    def __init__(self,window,module=None):
        super().__init__(window); self.setWindowTitle("編輯模組" if module else "新增模組"); self.resize(490,380)
        module=module or {}; self.name=QLineEdit(module.get("name","")); self.name.setPlaceholderText("例如：角色、鏡頭、光線")
        self.body.addWidget(label("依照自己的工作流程分類提示詞。","Subtle",True))
        self.body.addWidget(label("模組名稱","Heading")); self.body.addWidget(self.name)
        self.mode=QComboBox(); self.mode.addItem("複選 · 可以選擇多個項目","multiple"); self.mode.addItem("單選 · 一次使用一個項目","single")
        self.mode.setCurrentIndex(max(0,self.mode.findData(module.get("mode","multiple"))))
        self.body.addWidget(label("選擇方式","Heading")); self.body.addWidget(self.mode)
        if module: self.body.addWidget(label("改為單選時，現有組合只保留第一個項目。","Subtle",True))
        self.validation=label("","Draft"); self.validation.hide(); self.body.addWidget(self.validation)
        self.body.addWidget(dialog_buttons(self,self.save)); self.name.setFocus()

    def save(self):
        if not self.name.text().strip(): self.validation.setText("請先填寫模組名稱。"); self.validation.show(); return
        self.accept()


class ItemDialog(StudioDialog):
    def __init__(self, window, item=None, initial=""):
        super().__init__(window)
        self.window = window
        self.item = copy.deepcopy(item) if item else dict(id=uid(),preview="")
        self.setWindowTitle("編輯提示詞" if item else "新增提示詞")
        self.resize(680,780)
        layout = self.body
        layout.addWidget(label("把常用組合存成一個項目", "Heading"))
        form = QFormLayout()
        self.name = QLineEdit(self.item.get("name",""))
        self.module = QComboBox()
        for m in window.state["modules"]:
            self.module.addItem(m["name"],m["id"])
        self.module.setCurrentIndex(max(0,self.module.findData(self.item.get("module",window.current_module))))
        self.aliases = QLineEdit("、".join(self.item.get("aliases",[])))
        self.aliases.setPlaceholderText("例如：女孩、紅裙、紅色衣服")
        form.addRow("顯示名稱", self.name)
        form.addRow("所屬模組", self.module)
        form.addRow("中文別名", self.aliases)
        layout.addLayout(form)
        self.prompt = PromptEdit(window.completion)
        self.prompt.setObjectName("Prompt")
        self.prompt.setPlaceholderText("輸入英文查標籤；中文先查個人字典。選取候選後自動接逗號。")
        self.prompt.setPlainText(self.item.get("prompt",initial))
        layout.addWidget(self.prompt,1)
        self.notes = QPlainTextEdit(self.item.get("notes",""))
        self.notes.setPlaceholderText("備註：適用模型、效果或注意事項")
        self.notes.setMaximumHeight(75)
        layout.addWidget(self.notes)
        self.excludes=QPlainTextEdit('\n'.join(self.item.get('excludes',[]))); self.excludes.setMaximumHeight(68)
        self.excludes.setPlaceholderText('例如：red eyes, blindfold（以逗號或換行分隔）')
        layout.addWidget(label('使用此項目時，自動停用以下 Tag','Subtle'))
        layout.addWidget(self.excludes)
        self.preview = ImageDropLabel(window.store)
        self.preview.pathReady.connect(self.receive_image); self.preview.failed.connect(window.notice)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(100)
        set_preview(self.preview,window.store,self.item.get("preview"),120)
        layout.addWidget(self.preview)
        layout.addLayout(row(button("選擇預覽圖",self.choose_image),button("移除預覽",self.clear_image),None))
        layout.addWidget(dialog_buttons(self,self.save))

    def choose_image(self):
        path = image_path(self)
        if path: self.receive_image(path)

    def receive_image(self,path):
        try:
            self.item["preview"] = thumbnail(path,self.window.store.directory)
            set_preview(self.preview,self.window.store,self.item["preview"],120)
        except Exception as exc: self.window.error(str(exc))

    def clear_image(self):
        self.item["preview"] = ""
        set_preview(self.preview,self.window.store,"",120)

    def save(self):
        if not self.name.text().strip() or not self.prompt.toPlainText().strip() or not self.module.currentData():
            self.window.error("請填寫名稱、提示詞，並選擇所屬模組。")
            return
        aliases = self.aliases.text().replace("，","、").replace(",","、").split("、")
        self.item.update(name=self.name.text().strip(),module=self.module.currentData(),
                         prompt=self.prompt.toPlainText().strip().strip(", "),
                         aliases=list(dict.fromkeys(a.strip() for a in aliases if a.strip())),notes=self.notes.toPlainText())
        import re
        self.item['excludes']=list(dict.fromkeys(t.strip() for t in re.split(r'[,，\n]+',self.excludes.toPlainText()) if t.strip()))
        self.accept()


class SettingsDialog(StudioDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        s = window.state["settings"]
        self.setWindowTitle("設定")
        self.resize(760,780)
        layout = self.body
        tabs = QTabWidget(); self.tabs=tabs
        layout.addWidget(tabs)
        appearance = QWidget(); appearance_layout=QVBoxLayout(appearance); appearance_layout.setContentsMargins(0,12,0,0); appearance_layout.setSpacing(18)
        appearance_layout.addWidget(label("閱讀與顯示","Heading"))
        reading,reading_layout=panel("SettingsGroup"); appearance_layout.addWidget(reading)
        form=QFormLayout(); form.setVerticalSpacing(18); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); reading_layout.addLayout(form)
        self.family = QFontComboBox(); self.family.setCurrentFont(QFont(s["font_family"]))
        form.addRow("介面字型",self.family)
        self.ui_size = QSpinBox(); self.ui_size.setRange(9,22); self.ui_size.setValue(s["ui_size"])
        self.prompt_size = QSpinBox(); self.prompt_size.setRange(9,22); self.prompt_size.setValue(s["prompt_size"])
        form.addRow("介面字級（pt）",self.ui_size); form.addRow("提示詞字級（pt）",self.prompt_size)
        appearance_layout.addWidget(label("材質與色彩","Heading"))
        material,material_layout=panel("SettingsGroup"); appearance_layout.addWidget(material)
        form=QFormLayout(); form.setVerticalSpacing(18); form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow); form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); material_layout.addLayout(form)
        self.material = QComboBox()
        for text,value in [("純黑實色 · 最省資源","solid"),("Mica · 桌布色調","mica"),("Acrylic · 系統背景模糊","acrylic")]: self.material.addItem(text,value)
        self.material.setCurrentIndex(self.material.findData(s["material"]))
        form.addRow("視窗材質",self.material)
        self.material_form=form
        self.transparency_values={k:s.get(k+'_transparency',61) for k in ('mica','acrylic')}
        self.transparency_material=self.material.currentData()
        self.transparency_row=QWidget(); transparency_layout=row()
        transparency_layout.setContentsMargins(0,0,0,0); self.transparency_row.setLayout(transparency_layout)
        self.transparency=QSlider(Qt.Orientation.Horizontal); self.transparency.setRange(0,100)
        self.transparency.setAccessibleName("材質透明度")
        self.transparency_value=QSpinBox(); self.transparency_value.setRange(0,100); self.transparency_value.setSuffix(" %")
        self.transparency_value.setAccessibleName("材質透明度百分比"); self.transparency_value.setMinimumWidth(96)
        self.transparency.setValue(self.transparency_values.get(self.transparency_material,61))
        self.transparency_value.setValue(self.transparency.value())
        transparency_layout.addWidget(self.transparency,1); transparency_layout.addWidget(self.transparency_value)
        form.addRow("透明度",self.transparency_row)
        self.transparency.setToolTip("調整周邊底色遮罩；數值越高，材質色調越明顯，不影響文字區。")
        self.transparency.valueChanged.connect(self.transparency_value.setValue)
        self.transparency_value.valueChanged.connect(self.transparency.setValue)
        form.setRowVisible(self.transparency_row,self.material.currentData() in ('mica','acrylic'))
        self.accent = QComboBox()
        for text,value in [("中性灰白","neutral"),("霧藍","blue"),("柔綠","green")]: self.accent.addItem(text,value)
        self.accent.setCurrentIndex(self.accent.findData(s["accent"]))
        self.density = QComboBox(); self.density.addItem("舒適","comfortable"); self.density.addItem("緊湊","compact")
        self.density.setCurrentIndex(self.density.findData(s["density"]))
        form.addRow("重點色",self.accent); form.addRow("清單間距",self.density)
        appearance_layout.addWidget(label("操作確認","Heading"))
        confirmations,confirmations_layout=panel("SettingsGroup"); appearance_layout.addWidget(confirmations)
        self.confirm_clear_draft=QCheckBox("清除手動內容前先詢問")
        self.confirm_clear_draft.setChecked(s.get("confirm_clear_draft",True)); confirmations_layout.addWidget(self.confirm_clear_draft)
        self.reduce_motion=QCheckBox('減少畫布展開動畫'); self.reduce_motion.setChecked(s.get('reduce_motion',False)); confirmations_layout.addWidget(self.reduce_motion)
        self.connection_style=QComboBox()
        for title,value in [('曲線','curve'),('直線','straight'),('直角 · 中段一次轉折','orthogonal')]: self.connection_style.addItem(title,value)
        self.connection_style.setCurrentIndex(self.connection_style.findData(s.get('connection_style','curve')))
        self.connection_style.setToolTip('直角線採水平、垂直、水平三段；只在中間轉折，不自動繞過模組。')
        if 'multi_output' in window.state:
            confirmations_layout.addWidget(label('畫布連線樣式','Subtle')); confirmations_layout.addWidget(self.connection_style)
        self.separate_selections=QCheckBox('禁止清單選項跟 Canvas 選項共用')
        self.separate_selections.setChecked(s.get('separate_selections',True)); confirmations_layout.addWidget(self.separate_selections)
        confirmations_layout.addWidget(label('勾選後各自保留選擇與手動稿；素材庫仍共用。取消勾選可合併使用兩邊的組合。','Subtle',True))
        appearance_layout.addStretch()
        tabs.addTab(scrolling(appearance),"外觀")
        network = QWidget(); net = QFormLayout(network)
        self.online = QCheckBox("允許聯網（候選與 CivitAI 共用）"); self.online.setChecked(s["online"])
        net.addRow(self.online)
        net.addRow(label("只送出目前輸入的片段，不上傳整份 Prompt、圖片或模型。停止輸入約 0.6 秒後查詢；已查內容會快取。","Subtle",True))
        self.translator = QComboBox(); self.translator.addItem("個人字典 + Danbooru 名稱查詢","dictionary"); self.translator.addItem("個人字典 + Google Cloud 翻譯","google")
        self.translator.setCurrentIndex(self.translator.findData(s["translator"]))
        self.key = QLineEdit(window.completion.api_key); self.key.setEchoMode(QLineEdit.EchoMode.Password)
        self.key.setPlaceholderText("自己的 Google Cloud Translation API 金鑰")
        net.addRow("中文候選",self.translator); net.addRow("Google 金鑰",self.key)
        net.addRow(label("Google 需自行開通服務，可能產生費用。金鑰只在本次開啟期間保存；翻譯文字不等於已核對的 Danbooru Tag。","Subtle",True))
        self.formatter = QComboBox(); self.formatter.addItem("空格：simple background","spaces"); self.formatter.addItem("原標籤：simple_background","original")
        self.formatter.setCurrentIndex(self.formatter.findData(s["formatter"]))
        self.artist = QCheckBox("繪師候選自動加上 @（適合 Anima）"); self.artist.setChecked(s["artist_prefix"])
        net.addRow("插入格式",self.formatter); net.addRow(self.artist)
        tabs.addTab(scrolling(network),"候選與翻譯")
        dictionary = QWidget(); dictionary_layout = QVBoxLayout(dictionary)
        dictionary_layout.addWidget(label("每行一個對照：中文 = 英文提示詞。這是可自行修改的小型字典。","Subtle",True))
        self.dictionary = QPlainTextEdit(s.get("dictionary_buffer", "\n".join(f"{k} = {v}" for k,v in window.state["dictionary"].items())))
        dictionary_layout.addWidget(self.dictionary,1)
        self.dictionary_feedback=label("", "Subtle", True); dictionary_layout.addWidget(self.dictionary_feedback)
        self.dictionary.setMinimumHeight(300)
        tabs.addTab(dictionary,"個人字典")
        layout.addWidget(dialog_buttons(self,self.save))
        self.material.currentIndexChanged.connect(self.preview_material)
        self.transparency.valueChanged.connect(self.preview_material)
        self.finished.connect(self.finish_preview)

    def preview_material(self, *_):
        material=self.material.currentData()
        if self.transparency_material in self.transparency_values:
            self.transparency_values[self.transparency_material]=self.transparency.value()
        if material!=self.transparency_material:
            self.transparency.blockSignals(True); self.transparency_value.blockSignals(True)
            self.transparency.setValue(self.transparency_values.get(material,61)); self.transparency_value.setValue(self.transparency.value())
            self.transparency.blockSignals(False); self.transparency_value.blockSignals(False); self.transparency_material=material
        self.material_form.setRowVisible(self.transparency_row,material in ('mica','acrylic'))
        self.window.appearance_preview=dict(material=material,**{k+'_transparency':v for k,v in self.transparency_values.items()})
        self.window.apply_theme(preserve_layout=True)

    def finish_preview(self, *_):
        self.window.appearance_preview={}
        self.window.apply_theme(preserve_layout=True)

    def save(self, accept=True):
        prior=dict(self.window.state["settings"]); dictionary = {}; valid=True
        for line in self.dictionary.toPlainText().splitlines():
            if not line.strip(): continue
            if "=" not in line or not all(v.strip() for v in line.split("=",1)):
                valid=False; break
            key,value = line.split("=",1); dictionary[key.strip()] = value.strip()
        changed_separation=self.separate_selections.isChecked()!=self.window.state['settings'].get('separate_selections',True)
        set_selection_separation(self.window.state,self.separate_selections.isChecked())
        if changed_separation:
            self.window.canvas.undo_stack.clear(); self.window.canvas.redo_stack.clear()
        self.window.state["settings"].update(ui_size=self.ui_size.value(),prompt_size=self.prompt_size.value(),
            font_family=self.family.currentFont().family(),material=self.material.currentData(),accent=self.accent.currentData(),
            acrylic_transparency=self.transparency_values['acrylic'],mica_transparency=self.transparency_values['mica'],confirm_clear_draft=self.confirm_clear_draft.isChecked(),reduce_motion=self.reduce_motion.isChecked(),
            connection_style=self.connection_style.currentData(),density=self.density.currentData(),online=self.online.isChecked(),translator=self.translator.currentData(),
            formatter=self.formatter.currentData(),artist_prefix=self.artist.isChecked())
        self.dictionary_feedback.setText('' if valid else '尚未套用：每行請使用「中文 = 英文」格式。輸入內容已暫存。')
        if valid:
            self.window.state["dictionary"] = dictionary
            self.window.state['settings'].pop('dictionary_buffer',None)
        else:self.window.state['settings']['dictionary_buffer']=self.dictionary.toPlainText()
        self.window.completion.api_key = self.key.text().strip()
        self.window.completion.serial += 1
        self.window.completion.timer.stop()
        settings=self.window.state['settings']
        prompt_changed=changed_separation or any(prior.get(k)!=settings.get(k) for k in ('formatter','artist_prefix'))
        theme_changed=any(prior.get(k)!=settings.get(k) for k in ('ui_size','prompt_size','font_family','material','accent','density','acrylic_transparency','mica_transparency','reduce_motion','connection_style'))
        self.window.changed('prompt' if prompt_changed else 'settings')
        if prompt_changed:self.window.refresh_builder()
        if theme_changed:
            self.window.appearance_preview={}; self.window.apply_theme(preserve_layout=True)
        if accept and valid:self.accept()
        return valid


class WorkspaceDialog(StudioDialog):
    def __init__(self, window):
        super().__init__(window)
        self.window = window
        self.workspace = copy.deepcopy(next(w for w in window.state["workspaces"] if w["id"]==window.state["workspace"]))
        self.setWindowTitle("工作區 · 固定組合與參數版本")
        self.resize(740,760)
        layout = self.body
        self.name = QLineEdit(self.workspace["name"])
        layout.addWidget(self.name)
        tabs = QTabWidget(); layout.addWidget(tabs)
        fixed = QWidget(); fixed_layout = QVBoxLayout(fixed)
        fixed_layout.addWidget(label("切換到此工作區時，還原勾選模組的已存選擇；其他模組保留目前選擇。","Subtle",True))
        self.fixed = CheckList()
        for module in window.state["modules"]:
            item = QListWidgetItem(module["name"])
            item.setData(Qt.ItemDataRole.UserRole,module["id"])
            item.setCheckState(Qt.CheckState.Checked if module["id"] in self.workspace["fixed"] else Qt.CheckState.Unchecked)
            self.fixed.addItem(item)
        fixed_layout.addWidget(self.fixed)
        self.capture = QCheckBox("以目前選擇更新固定組合"); fixed_layout.addWidget(self.capture)
        tabs.addTab(fixed,"固定模組")
        params = QWidget(); form = QFormLayout(params)
        form.addRow(label("建議參數由你填寫，尚未與 ComfyUI 同步。空白表示未指定。","Subtle",True))
        self.fields = {}
        for key,title in [("ckpt","CKPT / Diffusion 模型"),("loras","LoRA 與建議權重"),("cfg","CFG"),("steps","步數"),("sampler","採樣器"),("scheduler","調度器"),("denoise","Denoise"),("seed","Seed"),("notes","其他備註")]:
            widget = QLineEdit(str(self.workspace.get("parameters",{}).get(key,"")))
            self.fields[key] = widget; form.addRow(title,widget)
        self.version_note = QLineEdit(); self.version_note.setPlaceholderText("例如：Anima2 降低 CFG、調整 Artist 權重")
        form.addRow("這次版本說明",self.version_note)
        form.addRow(button("從模型庫選擇 CKPT／Diffusion",lambda:self.pick_model(False)),button("從模型庫加入 LoRA",lambda:self.pick_model(True)))
        tabs.addTab(scrolling(params),"建議參數")
        history = QWidget(); hist_layout = QVBoxLayout(history)
        self.history = QListWidget()
        for entry in reversed(self.workspace.get("history",[])):
            item = QListWidgetItem(time.strftime("%Y-%m-%d %H:%M",time.localtime(entry["time"]))+"  "+entry.get("note",""))
            item.setData(Qt.ItemDataRole.UserRole,entry); self.history.addItem(item)
        hist_layout.addWidget(self.history)
        self.history_preview = QPlainTextEdit(); self.history_preview.setReadOnly(True)
        self.history.currentItemChanged.connect(self.show_version)
        hist_layout.addWidget(self.history_preview)
        hist_layout.addWidget(button("將選定版本帶回編輯",self.restore))
        tabs.addTab(history,"參數歷史")
        layout.addWidget(dialog_buttons(self,self.save))

    def show_version(self,item):
        self.history_preview.setPlainText(json.dumps(item.data(Qt.ItemDataRole.UserRole).get("parameters",{}),ensure_ascii=False,indent=2) if item else "")

    def pick_model(self,lora):
        models=[r for r in self.window.catalog.rows("model",limit=20000) if not r.get("missing") and (r["kind"]=="LoRA" if lora else r["kind"]!="LoRA")]
        if not models:
            self.window.notice("請先到模型管理掃描模型資料夾。"); return
        names=[f"{r['name']} · {r['relative']}" for r in models]
        value,ok=QInputDialog.getItem(self,"選擇建議模型","這只更新工作區建議，不會改動 ComfyUI。",names,0,False)
        if ok:
            model=models[names.index(value)]
            if lora:
                prior=self.fields["loras"].text().strip()
                self.fields["loras"].setText((prior+"; " if prior else "")+model["relative"])
            else: self.fields["ckpt"].setText(model["relative"])

    def restore(self):
        item = self.history.currentItem()
        if item:
            entry = item.data(Qt.ItemDataRole.UserRole)
            for key,field in self.fields.items(): field.setText(str(entry["parameters"].get(key,"")))
            self.version_note.setText("還原："+entry.get("note","歷史版本"))
            self.window.notice("歷史參數已帶入，按儲存後生效。")

    def save(self):
        if not self.name.text().strip():
            self.window.error("工作區名稱不能空白。"); return
        parameters = {key:field.text().strip() for key,field in self.fields.items()}
        for key in ("cfg","denoise"):
            if parameters[key]:
                try:
                    value = float(parameters[key])
                    import math
                    if not math.isfinite(value) or value < 0 or (key=="denoise" and value>1): raise ValueError()
                except ValueError:
                    self.window.error(f"{key} 請填有效數值。Denoise 範圍是 0–1。"); return
        for key in ("steps","seed"):
            if parameters[key]:
                try:
                    value = int(parameters[key])
                    if key=="steps" and value<=0: raise ValueError()
                except ValueError:
                    self.window.error(f"{key} 請填整數，步數需大於零。"); return
        history = self.workspace.setdefault("history",[])
        if not history and self.workspace.get("parameters"):
            history.append(dict(time=time.time(),note="原先參數",parameters=copy.deepcopy(self.workspace["parameters"])))
        if parameters != self.workspace.get("parameters",{}) or self.version_note.text().strip():
            history.append(dict(time=time.time(),note=self.version_note.text().strip() or "參數更新",parameters=copy.deepcopy(parameters)))
        self.workspace.update(name=self.name.text().strip(),parameters=parameters,
            fixed=[self.fixed.item(i).data(Qt.ItemDataRole.UserRole) for i in range(self.fixed.count()) if self.fixed.item(i).checkState()==Qt.CheckState.Checked])
        if self.capture.isChecked():
            self.workspace["picks"] = {mid:list(self.window.state["selections"].get(mid,[])) for mid in self.workspace["fixed"]}
            chosen={i for ids in self.workspace['picks'].values() for i in ids}
            self.workspace['uses']={k:copy.deepcopy(v) for k,v in self.window.state.get('uses',{}).items() if v.get('source_module') in self.workspace['fixed'] or k in self.workspace.get('fixed_uses',[])}
            if 'multi_output' in self.window.state:
                from .multi_output import owner
                self.workspace['canvas_owners']={k:owner(self.window.state,k) for k in self.workspace['uses']}
            self.workspace['instances']={k:copy.deepcopy(v) for k,v in self.window.state.get('instances',{}).items() if k in chosen}
            self.workspace['weights']={i:self.window.state.get('weights',{}).get(i,10) for ids in self.workspace['picks'].values() for i in ids}
        self.window.state["workspaces"] = [self.workspace if w["id"]==self.workspace["id"] else w for w in self.window.state["workspaces"]]
        self.window.changed(); self.window.refresh_workspaces(); self.accept()


class CategoryDialog(StudioDialog):
    def __init__(self,window):
        super().__init__(window)
        self.window=window; self.setWindowTitle("管理模型分類"); self.resize(470,470)
        layout=self.body; layout.addWidget(label("分類可自訂；重新命名會一併更新現有模型。","Subtle",True))
        from .civitai_assets import categories,LOCAL_TYPES
        self.scopes=copy.deepcopy(window.state['settings'].get('model_category_types',{})); self.scope_loading=False
        self.list=QListWidget(); self.list.addItems(categories(window.state['settings'])); layout.addWidget(self.list,1)
        self.scope=QComboBox(); self.scope.addItem('所有模型類型',''); self.scope.addItems(LOCAL_TYPES)
        layout.addLayout(row(label('適用模型類型'),self.scope)); self.scope.currentIndexChanged.connect(self.change_scope); self.list.currentItemChanged.connect(self.select_scope)
        self.list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        for i in range(self.list.count()): self.list.item(i).setFlags(self.list.item(i).flags() & ~Qt.ItemFlag.ItemIsDropEnabled)
        self.mapping={name:name for name in window.state["settings"]["model_categories"]}
        layout.addLayout(row(button("新增",self.add),button("重新命名",self.rename),button("刪除",self.remove)))
        layout.addLayout(row(button("上移",lambda:self.move_category(-1)),button("下移",lambda:self.move_category(1)),None))
        layout.addWidget(dialog_buttons(self))

    def names(self): return [self.list.item(i).text() for i in range(self.list.count())]

    def add(self):
        name,ok=QInputDialog.getText(self,"新增分類","分類名稱")
        if ok and name.strip() and name.strip() not in self.names():
            item=QListWidgetItem(name.strip()); item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsDropEnabled); self.list.addItem(item)

    def rename(self):
        item=self.list.currentItem()
        if not item or item.text()=="未分類": return
        old=item.text(); name,ok=QInputDialog.getText(self,"重新命名分類","新的分類名稱",text=old)
        if ok and name.strip() and (name.strip()==old or name.strip() not in self.names()):
            item.setText(name.strip())
            self.scopes[name.strip()]=self.scopes.pop(old,[])
            self.mapping={k:name.strip() if v==old else v for k,v in self.mapping.items()}

    def remove(self):
        item=self.list.currentItem()
        if not item or item.text()=="未分類": return
        old=item.text()
        if ask(self,"刪除分類",f"刪除「{old}」分類？其中的模型會歸入「未分類」，不會刪除檔案。"):
            self.list.takeItem(self.list.row(item)); self.scopes.pop(old,None); self.mapping={k:'未分類' if v==old else v for k,v in self.mapping.items()}

    def select_scope(self,item):
        self.scope_loading=True; values=self.scopes.get(item.text(),[]) if item else []
        self.scope.setCurrentText(values[0] if values else '所有模型類型'); self.scope.setEnabled(bool(item and item.text()!='未分類')); self.scope_loading=False

    def change_scope(self):
        item=self.list.currentItem()
        if not self.scope_loading and item:self.scopes[item.text()]=[self.scope.currentText()] if self.scope.currentIndex()>0 else []

    def move_category(self,delta):
        index=self.list.currentRow(); target=index+delta
        if index>=0 and 0<=target<self.list.count():
            item=self.list.takeItem(index); self.list.insertItem(target,item); self.list.setCurrentRow(target)
