"""Paged export preview and background export; no gallery import of clean copies."""
import copy
import json
from pathlib import Path
from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QFrame,QVBoxLayout,QSplitter,QWidget,QFormLayout,
    QLineEdit,QSpinBox,QCheckBox,QTabWidget,QFileDialog,QTableWidget,QTableWidgetItem,
    QHeaderView,QAbstractItemView,QProgressBar,QPlainTextEdit)
from .widgets import (label,button,row,panel,scrolling,ComboBox,StudioDialog,
                      InputDialog,open_file,RoundMenu)
from .export_views import ExportDelegate,ExportHeader
from . import clean_export as exporter
from .clean_metadata import inspect

def combo(choices):
    widget=ComboBox()
    for text,value in choices: widget.addItem(text,value)
    return widget

def spin(value,low,high):
    widget=QSpinBox(); widget.setRange(low,high); widget.setValue(value); return widget

class ExportPage(QFrame):
    progressed=Signal(int,int)
    def __init__(self,window):
        super().__init__(); self.window=window; self.records=exporter.ExportRecords(window.store)
        self.inputs=[]; self.rows=[]; self.plan=None; self.result=None; self.page=0; self.loading=False; self.busy=False
        self.pending_sources=None; self.pending_action=None
        window.jobs.became_idle.connect(self.resume_pending)
        self.setObjectName('WorkspaceSurface'); layout=QVBoxLayout(self); layout.setContentsMargins(20,16,20,16)
        self.add_files_button=button('選擇圖片…',self.choose_files)
        self.add_folder_button=button('選擇資料夾…',self.folder_menu)
        self.recursive=QCheckBox('包含子資料夾'); self.recursive.toggled.connect(self.rescan)
        self.clear_button=button('清空',self.clear,'Quiet'); self.rescan_button=button('重新掃描',self.rescan,'Quiet')
        layout.addLayout(row(self.add_files_button,self.add_folder_button,self.recursive,self.rescan_button,self.clear_button,None,button('匯出紀錄',self.history,'Quiet')))
        split=QSplitter(); split.setChildrenCollapsible(False); layout.addWidget(split,1)
        left,body=panel(); split.addWidget(left); body.setContentsMargins(0,6,12,0)
        self.summary=label('選擇圖片或資料夾','Heading',True); body.addWidget(self.summary)
        self.feedback=label('','Subtle',True); self.feedback.hide(); body.addWidget(self.feedback)
        self.table=QTableWidget(0,3)
        self.table.setHorizontalHeader(ExportHeader(self.table)); self.table.setItemDelegate(ExportDelegate(self.table))
        self.table.setHorizontalHeaderLabels(['原圖','輸出／檢查結果','狀態'])
        self.table.verticalHeader().hide(); self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setWordWrap(False); self.table.setShowGrid(False); self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionResizeMode(0,QHeaderView.ResizeMode.Interactive)
        self.table.setColumnWidth(0,180); self.table.horizontalHeader().setSectionResizeMode(1,QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2,QHeaderView.ResizeMode.ResizeToContents)
        body.addWidget(self.table,1)
        self.only_failed=QCheckBox('只看失敗'); self.only_failed.toggled.connect(self.render); self.only_failed.hide()
        body.addLayout(row(button('查看 Metadata',self.inspector,'Quiet'),self.only_failed,None,button('‹',lambda:self.turn(-1),'Quiet'),button('›',lambda:self.turn(1),'Quiet')))
        self.position=label('','Subtle'); body.addWidget(self.position)
        settings,form_area=panel('InsetPanel'); settings.setMinimumWidth(300); settings.setMaximumWidth(530); split.addWidget(settings); split.setSizes([760,410])
        self.preset=ComboBox(); self.refresh_presets(); self.preset.currentTextChanged.connect(self.apply_preset)
        form_area.addLayout(row(self.preset,button('儲存預設',self.save_preset,'Quiet')))
        self.settings_tabs=QTabWidget(); form_area.addWidget(self.settings_tabs,1)
        basic=QWidget(); basic_form=QFormLayout(basic); basic_form.setContentsMargins(8,16,8,16); basic_form.setSpacing(14)
        basic_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.mode=combo([('移除全部 Metadata','all'),('移除生成資訊','generation')])
        self.mode.setToolTip('移除生成資訊：移除文字、EXIF、XMP、IPTC 等可攜帶生成資料的欄位，保留色彩與解析度資料。')
        self.format=combo([('保持原格式','keep'),('PNG','png'),('JPEG','jpeg'),('WebP','webp')])
        self.quality=spin(90,1,100); self.quality_label=label('品質')
        self.resize=combo([('保持原尺寸','keep'),('限制長邊','long'),('百分比縮放','percent'),('自訂解析度','exact')])
        self.long_side=spin(2048,1,16000); self.long_side.setSuffix(' px'); self.long_label=label('長邊上限')
        self.percent=spin(50,1,400); self.percent.setSuffix(' %'); self.percent_label=label('縮放比例')
        self.width=spin(1024,1,16000); self.height=spin(1024,1,16000)
        self.exact=QWidget(); exact_layout=QVBoxLayout(self.exact); exact_layout.setContentsMargins(0,0,0,0)
        exact_layout.addLayout(row(self.width,label('×'),self.height)); self.aspect=QCheckBox('保持比例，限制在此範圍內'); self.aspect.setChecked(True); exact_layout.addWidget(self.aspect)
        self.exact_label=label('寬 × 高')
        for title,widget in [('清理方式',self.mode),('輸出格式',self.format),(self.quality_label,self.quality),('尺寸',self.resize),(self.long_label,self.long_side),(self.percent_label,self.percent),(self.exact_label,self.exact)]: basic_form.addRow(title,widget)
        basic_form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
        self.settings_tabs.addTab(scrolling(basic),'清理與尺寸')
        naming=QWidget(); naming_form=QFormLayout(naming); naming_form.setContentsMargins(8,16,8,16); naming_form.setSpacing(14)
        naming_form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.pattern=QLineEdit('{name}'); self.pattern.setToolTip('可用：{name} 原檔名、{index:04d} 序號、{date} 日期、{folder} 原資料夾名稱。副檔名會自動附加。')
        self.collision=combo([('自動重新命名','rename'),('跳過','skip'),('覆蓋同名匯出檔','overwrite')])
        self.collision.setToolTip('任何模式都不會覆蓋本次原圖或媒體庫已知的母檔。')
        self.structure=QCheckBox('保留原始資料夾結構'); self.structure.setChecked(True)
        self.sha=QCheckBox('記錄 SHA-256'); self.sha.setToolTip('原圖與匯出圖的雜湊只保存在本機資料庫。')
        naming_form.addRow('檔名規則',self.pattern); naming_form.addRow('遇到同名檔',self.collision); naming_form.addRow(self.structure); naming_form.addRow(self.sha)
        self.settings_tabs.addTab(scrolling(naming),'檔名與紀錄')
        self.directory=QLineEdit(); self.directory.setPlaceholderText('選擇輸出資料夾'); self.directory.setReadOnly(True)
        form_area.addLayout(row(self.directory,button('選擇…',self.choose_destination)))
        self.preview_button=button('預覽匯出',self.preview)
        self.export_button=button('開始匯出',self.export,'Primary'); self.export_button.setEnabled(False)
        form_area.addLayout(row(self.preview_button,self.export_button))
        self.progress=QProgressBar(); self.progress.setTextVisible(True); self.progress.hide(); layout.addWidget(self.progress)
        self.progressed.connect(self.update_progress)
        for widget in (self.mode,self.format,self.resize,self.collision): widget.currentIndexChanged.connect(self.invalidate)
        for widget in (self.quality,self.long_side,self.percent,self.width,self.height): widget.valueChanged.connect(self.invalidate)
        for widget in (self.aspect,self.structure,self.sha): widget.toggled.connect(self.invalidate)
        self.pattern.textChanged.connect(self.invalidate); self.directory.textChanged.connect(self.invalidate)
        self.update_fields()

    def refresh_presets(self):
        values=self.records.presets(); current=self.preset.currentText() if hasattr(self,'preset') else ''
        self.preset.blockSignals(True); self.preset.clear(); self.preset.addItems(list(values)); self.preset.setCurrentText(current or 'Public Clean'); self.preset.blockSignals(False)

    def values(self):
        return dict(mode=self.mode.currentData(),format=self.format.currentData(),quality=self.quality.value(),resize=self.resize.currentData(),
                    long_side=self.long_side.value(),percent=self.percent.value(),width=self.width.value(),height=self.height.value(),aspect=self.aspect.isChecked(),
                    structure=self.structure.isChecked(),pattern=self.pattern.text(),collision=self.collision.currentData(),sha256=self.sha.isChecked())

    def apply_preset(self,name):
        values=self.records.presets().get(name)
        if not values: return
        self.loading=True
        for key in ('mode','format','resize','collision'):
            widget=getattr(self,key); widget.setCurrentIndex(widget.findData(values[key]))
        for key in ('quality','long_side','percent','width','height'): getattr(self,key).setValue(values[key])
        for key in ('aspect','structure'): getattr(self,key).setChecked(values[key])
        self.sha.setChecked(values['sha256']); self.pattern.setText(values['pattern']); self.loading=False; self.invalidate()

    def save_preset(self):
        name,ok=InputDialog.getText(self,'儲存匯出預設','預設名稱',text=self.preset.currentText())
        if ok and name.strip():
            try:
                self.records.save_preset(name,self.values()); self.refresh_presets(); self.preset.setCurrentText(name.strip())
            except Exception as exc: self.window.error(str(exc))

    def update_fields(self):
        for widget in (self.quality,self.quality_label): widget.setVisible(self.format.currentData() in ('jpeg','webp'))
        for widgets,value in [((self.long_side,self.long_label),'long'),((self.percent,self.percent_label),'percent'),((self.exact,self.exact_label),'exact')]:
            for widget in widgets: widget.setVisible(self.resize.currentData()==value)

    def invalidate(self,*_):
        if self.loading: return
        self.pending_action=None
        self.plan=None; self.result=None; self.export_button.setEnabled(False); self.only_failed.hide(); self.update_fields(); self.render()

    def set_busy(self,busy):
        self.busy=busy
        for widget in (self.add_files_button,self.add_folder_button,self.rescan_button,self.recursive,self.clear_button,self.settings_tabs,self.preset,self.preview_button): widget.setEnabled(not busy)
        self.export_button.setEnabled(not busy and bool(self.plan) and any(r['action']=='write' for r in self.plan['entries']))
        self.progress.setVisible(busy)
        if busy: self.progress.setRange(0,0)

    def job(self,title,function,done):
        if self.busy or self.window.jobs.active:
            self.pending_action=(title,function,done)
            self.set_feedback('正在等待背景工作完成…',False); return
        def work(cancel):
            try: return function(cancel),''
            except Exception as exc: return None,str(exc)
        def finish(pair):
            self.set_busy(False)
            self.window.cancel_button.hide()
            if pair[1]: self.set_feedback(pair[1]); self.window.notice('匯出操作未完成。')
            else: done(pair[0])
        self.set_feedback('')
        self.set_busy(True)
        if not self.window.jobs.start(title,work,finish): self.set_busy(False)

    def set_feedback(self,text,error=True):
        self.feedback.setText(text); self.feedback.setStyleSheet('color: '+('#ef7777' if error else '#e4ba59')+';')
        self.feedback.setVisible(bool(text))

    def resume_pending(self):
        if self.window.closing or self.busy or self.window.jobs.active: return
        if self.pending_sources is not None:
            paths=self.pending_sources; self.pending_sources=None; self.pending_action=None
            self.load_sources(paths)
        elif self.pending_action:
            action=self.pending_action; self.pending_action=None; self.job(*action)

    def choose_files(self):
        files=QFileDialog.getOpenFileNames(self,'選擇要匯出的原圖','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')[0]
        if files: self.load_sources(files)

    def choose_folder(self):
        folder=QFileDialog.getExistingDirectory(self,'選擇原圖資料夾')
        if folder: self.load_sources([folder])

    def folder_menu(self):
        menu=RoundMenu(self)
        menu.addAction('媒體庫資料夾…',self.choose_album)
        menu.addAction('磁碟資料夾…',self.choose_folder)
        menu.open_at(self.add_folder_button.mapToGlobal(self.add_folder_button.rect().bottomLeft()))

    def choose_album(self):
        albums=self.window.catalog.rows('album',limit=10000)
        if not albums: self.set_feedback('媒體庫尚無資料夾。'); return
        names=[f"{r['name']} ({i+1})" for i,r in enumerate(albums)]
        name,ok=InputDialog.getItem(self,'選擇媒體庫資料夾','資料夾',names,0,False)
        if not ok: return
        album=albums[names.index(name)]['id']
        if self.window.catalog.count('image',album)>10000: self.set_feedback('單次最多匯出 10,000 張。'); return
        paths=[r['path'] for r in self.window.catalog.rows('image',album,limit=10000)]
        if paths: self.load_sources(paths)
        else: self.set_feedback('此資料夾尚無圖片。')

    def load_sources(self,paths):
        if self.busy or self.window.jobs.active:
            self.pending_sources=list(paths); self.pending_action=None
            self.set_feedback('背景工作完成後將載入所選圖片。',False); return
        self.pending_action=None; self.set_feedback('')
        self.inputs=list(paths); self.rows=[]; self.invalidate(); self.rescan()

    def rescan(self,*_):
        if not self.inputs: return
        paths=list(self.inputs); recursive=self.recursive.isChecked()
        self.invalidate()
        def done(rows): self.rows=rows; self.page=0; self.render(); self.window.notice(f'掃描完成：{len(rows)} 張。')
        self.job('正在掃描原圖…',lambda cancel:exporter.scan(paths,recursive,cancel),done)

    def clear(self):
        if self.busy: return
        self.pending_sources=None; self.pending_action=None; self.set_feedback('')
        self.inputs=[]; self.rows=[]; self.page=0; self.invalidate()

    def choose_destination(self):
        if self.busy: return
        path=QFileDialog.getExistingDirectory(self,'選擇圖片的輸出位置',self.directory.text())
        if path: self.directory.setText(path)

    def preview(self):
        if not self.rows: self.set_feedback('請先選擇圖片或資料夾。'); return
        if not self.directory.text(): self.choose_destination()
        if not self.directory.text(): self.set_feedback('請選擇輸出資料夾。'); return
        values=self.values(); directory=self.directory.text(); rows=copy.deepcopy(self.rows)
        masters=[]
        for raw in self.window.store.db.execute("SELECT json_object('path',json_extract(body,'$.path'),'owned',json_extract(body,'$.owned'),'original_relative',json_extract(body,'$.original_relative')) FROM resources WHERE kind IN ('image','recent')"):
            record=self.window.catalog.resolve_original(json.loads(raw[0]))
            if record.get('path'): masters.append(record['path'])
        preset=self.preset.currentText(); self.result=None; self.only_failed.hide()
        def done(plan):
            plan['preset']=preset if self.records.presets().get(preset)==values else preset+'（已調整）'
            self.plan=plan; self.export_button.setEnabled(any(r['action']=='write' for r in plan['entries'])); self.page=0; self.render()
            self.window.notice(self.summary.text())
        self.job('正在準備匯出預覽…',lambda cancel:exporter.prepare(rows,values,directory,masters,[self.window.store.directory],cancel),done)

    def export(self):
        if not self.plan: return
        plan=copy.deepcopy(self.plan)
        def done(result):
            self.records.save_run(result); self.result=result; self.plan=None; self.export_button.setEnabled(False); self.only_failed.setChecked(False)
            self.only_failed.show(); self.page=0; self.render(); self.window.notice(self.summary.text())
        self.job('正在建立乾淨圖片…',lambda cancel:exporter.execute(plan,cancel,self.progressed.emit),done)

    def update_progress(self,current,total): self.progress.setRange(0,total); self.progress.setValue(current)

    def visible_rows(self):
        rows=self.result['results'] if self.result else self.plan['entries'] if self.plan else self.rows
        if self.result and self.only_failed.isChecked(): rows=[r for r in rows if r['status']=='Failed']
        return rows

    def render(self,*_):
        rows=self.visible_rows(); self.page=min(self.page,max(0,(len(rows)-1)//100)); shown=rows[self.page*100:(self.page+1)*100]
        self.table.setRowCount(len(shown))
        for index,record in enumerate(shown):
            source=Path(record['source']); status=record.get('status','無法讀取' if record.get('error') else '已掃描')
            status={'Passed':'成功','Failed':'失敗','Skipped':'已跳過','Cancelled':'已取消'}.get(status,status)
            target=record.get('target','') or (' × '.join(str(record.get(k,'?')) for k in ('width','height'))+' · '+record.get('format','').upper())
            full_target=target
            if record.get('target') and (self.plan or self.result):
                try: target=str(Path(target).relative_to((self.plan or self.result)['root']))
                except ValueError: pass
                if record.get('output_size'): target+=' · '+' × '.join(map(str,record['output_size']))
            if record.get('error'): target=record['error']
            for column,value in enumerate((source.name,target,status)):
                item=QTableWidgetItem(value); item.setFlags(Qt.ItemFlag.ItemIsEnabled|Qt.ItemFlag.ItemIsSelectable)
                if column==2:
                    color='#75cd98' if status=='成功' else '#ef7777' if record.get('error') or status in ('失敗','無法讀取') else '#e4ba59' if status=='待匯出' else '#aaaaaa'
                    item.setForeground(QColor(color))
                item.setToolTip(str(source) if column==0 else full_target+('\n'+record['error'] if record.get('error') else '')+('\n'+record['note'] if record.get('note') else '')); self.table.setItem(index,column,item)
            self.table.setRowHeight(index,max(44,self.table.fontMetrics().height()+20))
        self.position.setText(f'{len(rows)} 張 · 第 {self.page+1} 頁' if rows else '')
        if self.result:
            counts={value:sum(r['status']==value for r in self.result['results']) for value in ('Passed','Failed','Skipped','Cancelled')}
            self.summary.setText(f"Passed {counts['Passed']} · Failed {counts['Failed']} · 跳過 {counts['Skipped']}"+
                (f" · 已取消，{counts['Cancelled']+self.result.get('remaining',0)} 張未完成" if counts['Cancelled'] or self.result.get('remaining') else ''))
        elif self.plan:
            counts={kind:sum(r['action']==kind for r in rows) for kind in ('write','skip','fail')}
            self.summary.setText(f"匯出預覽 · {counts['write']} 張可匯出"+(f" · 跳過 {counts['skip']}" if counts['skip'] else '')+(f" · 無法匯出 {counts['fail']}" if counts['fail'] else ''))
        else: self.summary.setText(f'原圖 · {len(rows)} 張' if rows else '選擇圖片或資料夾')

    def turn(self,delta): self.page=max(0,self.page+delta); self.render()

    def show_text(self,title,text):
        dialog=StudioDialog(self); dialog.setWindowTitle(title); dialog.resize(850,680)
        content=QPlainTextEdit(); content.setReadOnly(True); content.setPlainText(text); dialog.body.addWidget(content,1)
        dialog.body.addWidget(button('關閉',dialog.accept)); dialog.exec()

    def inspector(self):
        index=self.table.currentRow()+self.page*100; rows=self.visible_rows()
        if self.table.currentRow()<0 or index>=len(rows): self.window.notice('請先選擇一張圖片。'); return
        source=rows[index]['source']
        self.job('正在讀取 Metadata…',lambda cancel:inspect(source),lambda info:self.show_text('Metadata Inspector · '+Path(source).name,json.dumps(info,ensure_ascii=False,indent=2)))

    def history(self):
        records=self.records.history()
        if not records: self.window.notice('尚無匯出紀錄。'); return
        labels=[f"{r['time']} · {r['preset']} · {len(r['results'])} 張 ({i+1})" for i,r in enumerate(records)]
        chosen,ok=InputDialog.getItem(self,'匯出紀錄','最近 100 次匯出',labels,0,False)
        if ok: self.show_text('匯出紀錄',json.dumps(records[labels.index(chosen)],ensure_ascii=False,indent=2))
