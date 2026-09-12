from pathlib import Path
from PySide6.QtCore import Qt,QSize
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QWidget,QHBoxLayout,QSpinBox
from .widgets import button,label,RoundMenu


class RunControls(QWidget):
    def __init__(self,window,copy_fallback=False):
        super().__init__(); self.window=window; self.copy_fallback=copy_fallback
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

    def count_changed(self,value):
        self.window.state['settings']['comfy_count']=value; self.window.changed()
        for bar in (getattr(self.window,'run_controls',None),getattr(getattr(self.window,'recent',None),'run_controls',None)):
            if bar and bar is not self:
                bar.count.blockSignals(True); bar.count.setValue(value); bar.count.blockSignals(False)

    def refresh(self):
        client=getattr(self.window,'comfy',None); ready=bool(client and client.ready)
        fallback=self.copy_fallback and not ready
        self.run_button.setText('複製完整 Prompt' if fallback else '正在提交…' if client and client.run_id else '運行')
        self.run_button.setIcon(QIcon() if fallback else QIcon(str(Path(__file__).parent/'assets'/'play.svg')))
        role='Primary' if fallback else 'Run'
        if self.run_button.objectName()!=role:
            self.run_button.setObjectName(role); self.run_button.style().unpolish(self.run_button); self.run_button.style().polish(self.run_button)
        self.run_button.setEnabled((fallback or ready) and bool(self.window.final.toPlainText().strip()) and not (client and client.run_id))
        for widget in (self.count,self.stop,self.activity): widget.setVisible(not fallback)
        self.count.setEnabled(ready); self.stop.setEnabled(bool(client and client.connected and (client.running or client.pending or client.run_id)))
        self.activity.setText(f'{client.running+client.pending if client else 0} 個活動任務')

    def stop_menu(self,pos):
        menu=RoundMenu(self); action=menu.addAction('停止目前生成並清除待執行佇列',lambda:self.window.comfy.interrupt(True))
        action.setEnabled(bool(getattr(self.window,'comfy',None) and self.window.comfy.connected)); menu.open_at(self.stop.mapToGlobal(pos))
