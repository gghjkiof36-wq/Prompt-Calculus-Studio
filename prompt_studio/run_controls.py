from pathlib import Path
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget,QHBoxLayout,QSpinBox
from .widgets import button,label,RoundMenu
from .generation import direct_mode,active_profile,options


class RunControls(QWidget):
    def __init__(self,window,copy_fallback=False):
        super().__init__(); self.window=window; self.copy_fallback=copy_fallback; self.execution_only=False
        layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        self.count=QSpinBox(); self.count.setRange(1,100); self.count.setValue(window.state['settings'].get('comfy_count',1))
        self.count.setStyleSheet('QSpinBox {padding:4px 24px 4px 8px;} QSpinBox::up-button,QSpinBox::down-button {width:20px;}')
        self.count.setAccessibleName('運行次數'); self.count.setToolTip('運行次數，每次依照 ComfyUI 工作流的 Seed 規則提交。'); self.count.setFixedWidth(76)
        self.count.valueChanged.connect(self.count_changed); layout.addWidget(self.count)
        if window.state.get('multi_output',{}).get('version',1)>=4:self.count.setToolTip('執行輪數；一輪依序完成所有已連接工作流，同一工作流只提交一次。')
        self.run_button=button('運行',window.copy_final,'Run'); self.run_button.setMinimumHeight(40); self.run_button.setIconSize(QSize(18,18)); layout.addWidget(self.run_button,1)
        if not copy_fallback: self.run_button.setMaximumWidth(280)
        self.stop=button('×',lambda:window.comfy.interrupt(),'StopRun'); self.stop.setFixedSize(40,40)
        self.stop.setAccessibleName('停止目前生成'); self.stop.setToolTip('停止目前生成；按右鍵可連同待執行佇列一起清除。')
        self.stop.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.stop.customContextMenuRequested.connect(self.stop_menu); layout.addWidget(self.stop)
        self.activity=button('0 個活動任務',lambda:window.generation_panel.history(),'Quiet');layout.addWidget(self.activity)
        self.activity.setToolTip('查看任務與生成紀錄')
        if not copy_fallback: layout.addStretch()
        self.refresh()

    def set_execution_only(self,value):
        self.execution_only=value; self.stop.setProperty('textButton',False)
        self.stop.style().unpolish(self.stop); self.stop.style().polish(self.stop)
        self.refresh(); self.configure_geometry()

    def configure_geometry(self):
        # Text actions follow the chosen UI font; the standalone stop glyph
        # keeps its own square button. Count edits never change these sizes.
        if self.execution_only:
            for widget in (self.count,self.run_button,self.stop):widget.ensurePolished()
            height=max(40,self.count.fontMetrics().height()+18,self.run_button.fontMetrics().height()+18)
            for widget in (self.count,self.run_button,self.stop):widget.setFixedHeight(height)
            self.count.setFixedWidth(max(88,self.count.fontMetrics().horizontalAdvance('100'+self.count.suffix())+58))
            self.run_button.setFixedWidth(max(94,self.run_button.fontMetrics().horizontalAdvance('執行')+52))
            self.stop.setFixedWidth(height)
        else:
            self.count.setMinimumHeight(0); self.count.setMaximumHeight(16777215)
            self.run_button.setMinimumHeight(40); self.run_button.setMaximumHeight(16777215)
            self.stop.setFixedSize(40,40)

    def count_changed(self,value):
        self.window.state['settings']['comfy_count']=value; self.window.changed('settings')
        for bar in (getattr(self.window,'run_controls',None),getattr(getattr(self.window,'recent',None),'run_controls',None)):
            if bar and bar is not self:
                bar.count.blockSignals(True); bar.count.setValue(value); bar.count.blockSignals(False)

    def refresh(self):
        modern=self.window.state.get('multi_output',{}).get('version',1)>=5
        single_native=self.window.state.get('multi_output',{}).get('version',1)==4
        self.count.blockSignals(True); self.count.setMaximum(1 if single_native else 100); self.count.blockSignals(False)
        if single_native:self.count.setToolTip('本候選每次執行一個工作流；圖片批量大小沿用 ComfyUI 設定。多輪與跨流程暫擱。')
        if modern:self.count.setToolTip('提交次數。未接預排程時立即交給 ComfyUI；接上預排程後，忙碌時保存下一份輸入。')
        from .chain_model import enabled
        chained=modern and enabled(self.window.state)
        stage_mode=self.window.state.get('multi_output',{}).get('version',1)>=7
        if stage_mode:chained=len(self.window.state['multi_output'].get('stages',{}))>1
        self.count.setSuffix(' 輪' if chained else '')
        if chained:self.count.setToolTip('完整串接的輪數；圖片清單每張處理一輪，請設 1。')
        if stage_mode:self.count.setToolTip('完整流程輪數；有預排程時代表加入的外層項目數。' if chained else '立即提交次數；有預排程時代表加入的項目數。無 Stage 只同步輸入。')
        if self.execution_only:
            self.count.setFixedWidth(max(88,self.count.fontMetrics().horizontalAdvance('100 輪' if chained else '100')+58))
        client=getattr(self.window,'comfy',None); ready=bool(client and client.can_run)
        incompatible=bool(client and client.connected and not client.snapshot_compatible)
        needs=bool(client and client.connected and direct_mode(self.window.state) and not ready and not incompatible)
        missing='請更新擴充' if needs and not client.direct_supported else '請選擇工作流' if active_profile(self.window.state) is None else '請載入來源圖片'
        if needs and client.direct_supported and active_profile(self.window.state) and 'multi_output' in self.window.state: missing='請綁定 CLIP 輸入'
        if needs and client.direct_supported and self.window.state.get('multi_output',{}).get('version',1)>=4: missing='請綁定 CLIP 輸入'
        if client and hasattr(client,'queue') and client.queue.busy():missing='佇列工作尚未結案'
        busy=bool(client and (client.run_id or getattr(getattr(client,'generation',None),'batch',None) or hasattr(client,'queue') and client.queue.busy()))
        fallback=self.copy_fallback and not self.execution_only and not ready and not incompatible and not needs
        self.run_button.setText(('執行中' if busy and not modern else '執行') if self.execution_only else '請重啟 ComfyUI' if incompatible else missing if needs else '複製完整 Prompt' if fallback else '正在提交…' if client and client.run_id and not modern else '運行')
        self.run_button.setIcon(QIcon() if fallback else QIcon(str(Path(__file__).parent/'assets'/'play.svg')))
        role='Primary' if fallback else 'Run'
        if self.run_button.objectName()!=role:
            self.run_button.setObjectName(role); self.run_button.style().unpolish(self.run_button); self.run_button.style().polish(self.run_button)
        has_text=bool(self.window.final.toPlainText().strip()) or (ready and 'multi_output' in self.window.state)
        self.run_button.setEnabled((fallback or ready) and has_text and (modern or not (client and client.run_id)))
        for widget in (self.count,self.stop): widget.setVisible(self.execution_only or not fallback)
        self.activity.setVisible(not self.execution_only and not fallback)
        self.count.setEnabled(ready); self.stop.setEnabled(bool(client and client.connected and (client.running or client.pending or client.run_id)))
        if modern and client:
            self.stop.setEnabled(client.connected and client.input_flow.has_work())
            self.stop.setToolTip('取消本次流程及可確認的 PCS 任務，保留紀錄。' if stage_mode or chained else '取消目前 PCS 工作；預排程的後續項目保留並暫停。')
        self.activity.setText(f'{client.running+client.pending if client else 0} 個活動任務')
        if modern and client:
            self.activity.setVisible(True)
            flow=client.input_flow
            ids=set(flow.last_status.get('running_ids',[]))|set(flow.last_status.get('queued_ids',[]))
            ids.update(item['prompt_id'] for item in flow.direct.submitted.values() if item['route']['server']==client.url)
            if flow.current and flow.current.get('waiting'):ids.add(flow.current['waiting'])
            self.activity.setText(f'{max(len(ids),client.running+client.pending)} 個活動任務')
        if self.execution_only:
            self.stop.setText('×')
            self.run_button.setToolTip(client.message if client else '請先連線至 ComfyUI')
            if not ready:self.run_button.setToolTip(client.message if client and client.connected and not client.native_supported else '請連線至 ComfyUI，並將 Prompt 輸出連到已綁定的 CLIP 輸入。')
            if busy and not modern:self.run_button.setToolTip('目前工作尚未結束。')
            self.stop.setAccessibleName('取消執行'); self.count.show(); self.stop.show()
        else: self.stop.setText('×')

    def stop_menu(self,pos):
        if self.window.state.get('multi_output',{}).get('version',1)>=5:return
        menu=RoundMenu(self); action=menu.addAction('停止目前生成並清除待執行佇列',lambda:self.window.comfy.interrupt(True))
        action.setEnabled(bool(getattr(self.window,'comfy',None) and self.window.comfy.connected)); menu.open_at(self.stop.mapToGlobal(pos))
