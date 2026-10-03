from PySide6.QtCore import Qt,QSize,QRect
from PySide6.QtGui import QIcon,QFont,QFontMetrics,QColor
from PySide6.QtWidgets import QWidget,QHBoxLayout,QSpinBox,QPushButton,QStyleOptionButton,QStylePainter,QStyle
from .widgets import button,label,RoundMenu
from .generation import direct_mode,active_profile,options
from .ui_icons import icon


class ActivityButton(QPushButton):
    """Keep the count prominent without a full-size navigation sentence."""
    def __init__(self,window,callback):
        super().__init__('0 個活動任務')
        self.owner=window;self.clicked.connect(callback);self.amount=0;self.detailed=False
        self.setObjectName('Quiet')

    def text_fonts(self):
        value=QFont(self.font());value.setWeight(QFont.Weight.DemiBold)
        caption=QFont(self.font())
        if caption.pixelSize()>0:caption.setPixelSize(max(11,round(caption.pixelSize()*.86)))
        else:caption.setPointSizeF(max(8,caption.pointSizeF()*.86))
        return value,caption

    def sizeHint(self):
        if not self.detailed:return super().sizeHint()
        value,caption=self.text_fonts()
        width=QFontMetrics(value).horizontalAdvance(str(max(99,self.amount)))
        width+=QFontMetrics(caption).horizontalAdvance('活動任務')+26
        return QSize(width,max(36,QFontMetrics(value).height()+14))

    def minimumSizeHint(self):return self.sizeHint()

    def paintEvent(self,event):
        if not self.detailed:return super().paintEvent(event)
        option=QStyleOptionButton();self.initStyleOption(option);option.text='';option.icon=QIcon()
        painter=QStylePainter(self);painter.drawControl(QStyle.ControlElement.CE_PushButton,option)
        value,caption=self.text_fonts();value_width=QFontMetrics(value).horizontalAdvance(str(max(99,self.amount)))
        caption_width=QFontMetrics(caption).horizontalAdvance('活動任務')
        left=(self.width()-value_width-caption_width-6)//2
        from .theme import visual_tokens
        settings=dict(self.owner.state['settings']);settings.update(getattr(self.owner,'appearance_preview',{}))
        colors=visual_tokens(settings)
        painter.setFont(value);painter.setPen(QColor(colors['text' if self.amount else 'secondary']))
        painter.drawText(QRect(left,0,value_width,self.height()),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,str(self.amount))
        painter.setFont(caption);painter.setPen(QColor(colors['secondary']))
        painter.drawText(QRect(left+value_width+6,0,caption_width,self.height()),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,'活動任務')


class RunControls(QWidget):
    def __init__(self,window,copy_fallback=False):
        super().__init__(); self.window=window; self.copy_fallback=copy_fallback; self.execution_only=False
        self.compact=False; self.activity_count=0; self.activity_available=False
        layout=QHBoxLayout(self); layout.setContentsMargins(0,0,0,0); layout.setSpacing(6)
        self.count=QSpinBox();self.count.setObjectName('RunCount'); self.count.setRange(1,100); self.count.setValue(window.state['settings'].get('comfy_count',1))
        self.count.setStyleSheet('QSpinBox {padding:4px 4px 4px 8px;} QSpinBox::up-button,QSpinBox::down-button {width:18px;}')
        self.count.setAccessibleName('運行次數'); self.count.setToolTip('運行次數，每次依照 ComfyUI 工作流的 Seed 規則提交。'); self.count.setFixedWidth(76)
        self.count.valueChanged.connect(self.count_changed); layout.addWidget(self.count)
        if window.state.get('multi_output',{}).get('version',1)>=4:self.count.setToolTip('執行輪數；一輪依序完成所有已連接工作流，同一工作流只提交一次。')
        self.run_button=button('運行',window.copy_final,'Run'); self.run_button.setMinimumHeight(40); self.run_button.setIconSize(QSize(18,18)); layout.addWidget(self.run_button,1)
        if not copy_fallback: self.run_button.setMaximumWidth(280)
        self.stop=button('取消',lambda:window.comfy.interrupt(),'StopRun'); self.stop.setProperty('textButton',True)
        self.stop.setProperty('iconName','close');self.stop.setIconSize(QSize(18,18))
        from .ui_icons import icon
        self.stop.setIcon(icon('close','on-stop'))
        self.stop.setAccessibleName('取消執行'); self.stop.setToolTip('停止目前生成；按右鍵可連同待執行佇列一起清除。')
        self.stop.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.stop.customContextMenuRequested.connect(self.stop_menu); layout.addWidget(self.stop)
        self.activity=ActivityButton(window,lambda:window.generation_panel.history());layout.addWidget(self.activity)
        self.activity.setIconSize(QSize(18,18));self.activity.setToolTip('查看任務與生成紀錄')
        if not copy_fallback: layout.addStretch()
        self.refresh()

    def set_execution_only(self,value):
        self.execution_only=value; self.stop.setProperty('textButton',not value)
        self.activity.detailed=value;self.activity.setObjectName('RunActivity' if value else 'Quiet')
        self.activity.style().unpolish(self.activity);self.activity.style().polish(self.activity)
        if not value:self.set_compact(False)
        self.stop.style().unpolish(self.stop); self.stop.style().polish(self.stop)
        self.refresh()

    def set_compact(self,value):
        """Shorten auxiliary navigation only; execution rules stay in refresh."""
        self.compact=bool(value and self.execution_only)
        self.present_activity()

    def present_activity(self):
        text=f'{self.activity_count} 個活動任務'
        if self.activity.property('compactActivity') != self.compact:
            from .ui_icons import icon
            self.activity.setProperty('compactActivity',self.compact)
            self.activity.setProperty('iconName','history' if self.compact else None)
            self.activity.setIcon(icon('history') if self.compact else QIcon())
        self.activity.setText('' if self.compact else text)
        self.activity.amount=self.activity_count;self.activity.updateGeometry();self.activity.update()
        self.activity.setAccessibleName(text+'，查看生成紀錄')
        self.activity.setToolTip(text+' · 查看生成紀錄')
        if self.execution_only:self.activity.setVisible(self.activity_available and not self.compact)

    def expanded_width_hint(self):
        width=self.minimumSizeHint().width()
        if self.activity_available and self.activity.isHidden():
            width+=self.activity.minimumSizeHint().width()+self.layout().spacing()
        if self.compact and self.activity_available and not self.activity.detailed:
            width+=self.activity.fontMetrics().horizontalAdvance(f'{self.activity_count} 個活動任務')-self.activity.iconSize().width()
        return width

    def configure_geometry(self):
        # Both actions follow the chosen UI font. Reserve space for the maximum
        # count and its unit so changing the number cannot move either button.
        self.stop.ensurePolished()
        self.count.setFixedWidth(max(76,self.count.fontMetrics().horizontalAdvance('100'+self.count.suffix())+36) if self.execution_only
                                 else max(88,self.count.fontMetrics().horizontalAdvance('100'+self.count.suffix())+58))
        if self.execution_only:
            for widget in (self.count,self.run_button,self.stop):widget.ensurePolished()
            height=max(36,self.count.fontMetrics().height()+14,self.run_button.fontMetrics().height()+14)
            for widget in (self.count,self.run_button,self.stop):widget.setFixedHeight(height)
            self.stop.setFixedWidth(height)
            self.run_button.setFixedWidth(max(78,self.run_button.fontMetrics().horizontalAdvance(self.run_button.text())+46))
        else:
            self.stop.setFixedWidth(max(84,self.stop.fontMetrics().horizontalAdvance('取消')+self.stop.iconSize().width()+28))
            self.count.setMinimumHeight(0); self.count.setMaximumHeight(16777215)
            self.run_button.setMinimumHeight(40); self.run_button.setMaximumHeight(16777215)
            self.stop.setFixedHeight(max(40,self.stop.fontMetrics().height()+18))

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
        self.count.setSuffix(' 輪' if chained else ' 次')
        self.count.setAccessibleName('流程輪數' if chained else '執行次數')
        if chained:self.count.setToolTip('完整串接的輪數；圖片清單每張處理一輪，請設 1。')
        if stage_mode:self.count.setToolTip('完整流程輪數；有預排程時代表加入的外層項目數。' if chained else '立即提交次數；有預排程時代表加入的項目數。無 Stage 只同步輸入。')
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
        self.run_button.setIcon(QIcon() if fallback else icon('play','on-run'))
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
            self.stop.setToolTip('取消目前項目；其他等待項目保留並暫停。右鍵可取消全部流程。' if stage_mode else '取消本次流程及可確認的 PCS 任務，保留紀錄。' if chained else '取消目前 PCS 工作；預排程的後續項目保留並暫停。')
        self.activity_count=client.running+client.pending if client else 0
        if modern and client:
            self.activity.setVisible(True)
            flow=client.input_flow
            ids=set(flow.last_status.get('running_ids',[]))|set(flow.last_status.get('queued_ids',[]))
            ids.update(item['prompt_id'] for item in flow.direct.submitted.values() if item['route']['server']==client.url)
            if flow.current and flow.current.get('waiting'):ids.add(flow.current['waiting'])
            self.activity_count=max(len(ids),client.running+client.pending)
        self.activity_available=not self.activity.isHidden()
        self.present_activity()
        if self.execution_only:
            self.stop.setText('')
            self.run_button.setToolTip(client.message if client else '請先連線至 ComfyUI')
            if not ready:self.run_button.setToolTip(client.message if client and client.connected and not client.native_supported else '請連線至 ComfyUI，並將 Prompt 輸出連到已綁定的 CLIP 輸入。')
            if busy and not modern:self.run_button.setToolTip('目前工作尚未結束。')
            self.stop.setAccessibleName('取消執行'); self.count.show(); self.stop.show()
        else: self.stop.setText('取消')
        self.configure_geometry()

    def stop_menu(self,pos):
        if self.window.state.get('multi_output',{}).get('version',1)>=7:
            menu=RoundMenu(self)
            menu.addAction('取消目前項目',lambda:self.window.comfy.interrupt())
            menu.addSeparator();menu.addAction('取消此工作區全部流程（含等待項目）',lambda:self.window.comfy.interrupt(True))
            menu.open_at(self.stop.mapToGlobal(pos));return
        if self.window.state.get('multi_output',{}).get('version',1)>=5:return
        menu=RoundMenu(self); action=menu.addAction('停止目前生成並清除待執行佇列',lambda:self.window.comfy.interrupt(True))
        action.setEnabled(bool(getattr(self.window,'comfy',None) and self.window.comfy.connected)); menu.open_at(self.stop.mapToGlobal(pos))
