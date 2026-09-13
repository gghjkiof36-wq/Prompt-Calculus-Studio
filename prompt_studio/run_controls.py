from pathlib import Path
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget,QHBoxLayout,QSpinBox
from .widgets import button,label,RoundMenu
from .generation import direct_mode,active_profile,options


class RunControls(QWidget):
    def __init__(self,window,copy_fallback=False):
        super().__init__(); self.window=window; self.copy_fallback=copy_fallback; self.execution_only=False
        layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(8)
        self.count=QSpinBox(); self.count.setRange(1,100); self.count.setValue(window.state['settings'].get('comfy_count',1))
        self.count.setAccessibleName('運行次數'); self.count.setToolTip('運行次數，每次依照 ComfyUI 工作流的 Seed 規則提交。'); self.count.setFixedWidth(96)
        self.count.valueChanged.connect(self.count_changed); layout.addWidget(self.count)
        self.run_button=button('運行',window.copy_final,'Run'); self.run_button.setMinimumHeight(40); self.run_button.setIconSize(QSize(18,18)); layout.addWidget(self.run_button,1)
        if not copy_fallback: self.run_button.setMaximumWidth(280)
        self.stop=button('×',lambda:window.comfy.interrupt(),'StopRun'); self.stop.setFixedSize(40,40)
        self.stop.setAccessibleName('停止目前生成'); self.stop.setToolTip('停止目前生成；按右鍵可連同待執行佇列一起清除。')
        self.stop.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.stop.customContextMenuRequested.connect(self.stop_menu); layout.addWidget(self.stop)
        self.activity=label('0 個活動任務','Subtle'); layout.addWidget(self.activity)
        if not copy_fallback: layout.addStretch()
        self.refresh()

    def set_execution_only(self,value):
        self.execution_only=value; self.stop.setProperty('textButton',value)
        self.stop.style().unpolish(self.stop); self.stop.style().polish(self.stop)
        self.refresh(); self.configure_geometry()

    def configure_geometry(self):
        # Text actions follow the chosen UI font; the standalone stop glyph
        # keeps its own square button. Count edits never change these sizes.
        if self.execution_only:
            for widget in (self.count,self.run_button,self.stop):widget.ensurePolished()
            height=max(40,self.count.fontMetrics().height()+18,self.run_button.fontMetrics().height()+18)
            for widget in (self.count,self.run_button,self.stop):widget.setFixedHeight(height)
            self.stop.setFixedWidth(max(72,self.stop.fontMetrics().horizontalAdvance('取消')+24))
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
        client=getattr(self.window,'comfy',None); ready=bool(client and client.can_run)
        incompatible=bool(client and client.connected and not client.snapshot_compatible)
        needs=bool(client and client.connected and direct_mode(self.window.state) and not ready and not incompatible)
        missing='請更新擴充' if needs and not client.direct_supported else '請選擇工作流' if active_profile(self.window.state) is None else '請載入來源圖片'
        fallback=self.copy_fallback and not ready and not incompatible and not needs
        self.run_button.setText('執行' if self.execution_only else '請重啟 ComfyUI' if incompatible else missing if needs else '複製完整 Prompt' if fallback else '正在提交…' if client and client.run_id else '運行')
        self.run_button.setIcon(QIcon() if fallback else QIcon(str(Path(__file__).parent/'assets'/'play.svg')))
        role='Primary' if fallback else 'Run'
        if self.run_button.objectName()!=role:
            self.run_button.setObjectName(role); self.run_button.style().unpolish(self.run_button); self.run_button.style().polish(self.run_button)
        has_text=bool(self.window.final.toPlainText().strip()) or (ready and 'multi_output' in self.window.state)
        self.run_button.setEnabled((fallback or ready) and has_text and not (client and client.run_id))
        for widget in (self.count,self.stop): widget.setVisible(self.execution_only or not fallback)
        self.activity.setVisible(not self.execution_only and not fallback)
        self.count.setEnabled(ready); self.stop.setEnabled(bool(client and client.connected and (client.running or client.pending or client.run_id)))
        self.activity.setText(f'{client.running+client.pending if client else 0} 個活動任務')
        if self.execution_only:
            self.stop.setText('取消')
            self.run_button.setToolTip(client.message if client else '請先連線至 ComfyUI')
            if not self.window.state.get('multi_output',{}).get('connections'): self.run_button.setToolTip('請先將 Prompt 連到執行操作。')
            self.stop.setAccessibleName('取消執行'); self.count.show(); self.stop.show()
        else: self.stop.setText('×')

    def stop_menu(self,pos):
        menu=RoundMenu(self); action=menu.addAction('停止目前生成並清除待執行佇列',lambda:self.window.comfy.interrupt(True))
        action.setEnabled(bool(getattr(self.window,'comfy',None) and self.window.comfy.connected)); menu.open_at(self.stop.mapToGlobal(pos))
