"""Compact, actionable guidance for the new Canvas starter."""
from PySide6.QtCore import QPointF,Qt,QEvent,QTimer
from PySide6.QtWidgets import QFrame,QVBoxLayout,QHBoxLayout,QGridLayout,QSizePolicy
from .widgets import label,button
from .canvas_starter import guide


def pending_setup(state):
    """Read setup needs without treating a configured node as runnable."""
    from .clip_flow import selected_binding
    data=state.get('multi_output',{})
    profiles={p['id']:p for p in state.get('generation',{}).get('profiles',[])}
    pending=[]
    for key in data.get('clip_inputs',{}):
        binding=selected_binding(state,key)
        profile=profiles.get(binding['workflow']) if binding else None
        inputs=(profile or {}).get('graph',{}).get((binding or {}).get('node'),{}).get('inputs',{})
        field=(binding or {}).get('field')
        if field not in ('text','text_g','text_l') or not isinstance(inputs.get(field),str):
            pending.append(('clip_inputs',key))
    for key,value in data.get('image_inputs',{}).items():
        node=profiles.get(value.get('workflow'),{}).get('graph',{}).get(value.get('node'),{})
        if node.get('class_type')!='LoadImage' or not isinstance(node.get('inputs',{}).get('image'),str):
            pending.append(('image_inputs',key))
    for key,value in data.get('stages',{}).items():
        if value.get('workflow') not in profiles:pending.append(('stages',key))
    return pending


def setup_action(canvas,action,kind,key,*,emphasize_pending=False):
    """Highlight setup actions without changing bindings or sequencing them."""
    pending=pending_setup(canvas.window.state);target=(kind,key)
    required=target in pending;current=bool(pending and pending[0]==target)
    highlighted=current or (required and emphasize_pending)
    role='Primary' if highlighted else ''
    action.setProperty('setupPending',required);action.setProperty('setupCurrent',current)
    action.setAccessibleDescription('目前待設定步驟' if current else '待設定' if required else '')
    if action.objectName()!=role:
        action.setObjectName(role);action.style().unpolish(action);action.style().polish(action);action.update()
    from .canvas_items import canvas_colors
    from .ui_icons import icon
    colors=canvas_colors(canvas)
    action.setIcon(icon(action.property('iconName') or 'workflow',colors['on_accent' if highlighted else 'text']))
    return required


def setup_badge(canvas,badge,pending,complete='已綁定'):
    from .canvas_items import canvas_colors
    colors=canvas_colors(canvas)
    badge.setFixedHeight(max(24,badge.fontMetrics().height()+6))
    badge.setText('待設定' if pending else complete)
    badge.setStyleSheet('QLabel {color:'+colors['text' if pending else 'secondary']+';background:'+
        (colors['selected'] if pending else 'transparent')+';border-radius:5px;padding:3px 7px;font-weight:'+
        ('600' if pending else '400')+';}')


class CanvasZoomControls(QFrame):
    """Visible view controls remain available after dismissing the starter."""
    def __init__(self,canvas):
        super().__init__(canvas.view.viewport());self.canvas=canvas
        self.setObjectName('InsetPanel')
        body=QHBoxLayout(self);body.setContentsMargins(4,3,4,3);body.setSpacing(2)
        self.minus=button('−',lambda:canvas.zoom(1/1.15),'Quiet');self.minus.setToolTip('縮小畫布')
        self.value=button('100%',lambda:canvas.zoom_to(1.),'Quiet');self.value.setToolTip('回到原始大小')
        self.plus=button('+',lambda:canvas.zoom(1.15),'Quiet');self.plus.setToolTip('放大畫布')
        self.overview=button('總覽',canvas.fit,'Quiet');self.overview.setToolTip('查看完整流程')
        for action in (self.minus,self.value,self.plus,self.overview):
            action.setStyleSheet('QPushButton { padding:4px 7px; }')
            action.setAccessibleName(action.toolTip());body.addWidget(action)
        self.value.setMinimumWidth(54)
        canvas.view.transformChanged.connect(self.refresh)
        canvas.view.viewport().installEventFilter(self);self.refresh()

    def refresh(self):
        self.value.setText(f'{round(self.canvas.view.transform().m11()*100)}%')
        self.place()

    def place(self):
        bar=getattr(self.canvas,'execution_bar',None)
        if bar and bar.isVisible() and getattr(bar,'compact',False):
            self.hide();return
        self.show()
        self.adjustSize();area=self.parentWidget().rect().adjusted(12,12,-12,-12)
        x=max(area.left(),area.right()-self.width()+1);y=max(area.top(),area.bottom()-self.height()+1)
        if bar and bar.isVisible() and bar.geometry().intersects(self.geometry().translated(x-self.x(),y-self.y())):
            y=max(area.top(),bar.y()-self.height()-10)
        self.move(x,y);self.raise_()

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.Resize,QEvent.Type.Show):QTimer.singleShot(0,self.place)
        return super().eventFilter(watched,event)


class CanvasOnboarding(QFrame):
    def __init__(self,canvas):
        super().__init__(canvas);self.canvas=canvas;self.columns=None
        # The guide is a card inside the workspace, rather than an extension of
        # the application header. Keep its outside breathing room independent
        # of the card's natural content height.
        self.setObjectName('CanvasGuideInset')
        self.setSizePolicy(QSizePolicy.Policy.Expanding,QSizePolicy.Policy.Maximum)
        canvas.layout().setSpacing(0)
        self.outer=QVBoxLayout(self);self.outer.setContentsMargins(24,20,24,16);self.outer.setSpacing(0)
        self.card=QFrame(self);self.card.setObjectName('CanvasGuide');self.outer.addWidget(self.card)
        body=QVBoxLayout(self.card);body.setContentsMargins(16,8,16,8);body.setSpacing(4)
        self.setToolTip('拖曳空白處平移畫布，滾輪縮放。')
        top=QHBoxLayout();self.title=label('','Heading',True);self.title.setMinimumWidth(0);top.addWidget(self.title,1)
        self.overview=button('流程總覽',canvas.fit,'Quiet');top.addWidget(self.overview)
        self.close_button=button('隱藏',self.dismiss,'Quiet');self.close_button.setToolTip('隱藏入門引導');top.addWidget(self.close_button)
        for action in (self.overview,self.close_button):action.setStyleSheet('QPushButton { padding:3px 10px; }')
        body.addLayout(top)
        self.steps=QGridLayout();self.steps.setContentsMargins(0,0,0,0);self.steps.setSpacing(6);body.addLayout(self.steps)
        self.actions=[button('1  加入文字',self.add_text,'Quiet'),button('2  連線與工作流',self.connection,'Quiet'),
                      button('3  綁定 CLIP',self.bind_clip,'Quiet'),button('4  設定 Stage',self.stage,'Quiet')]
        for item in self.actions:
            item.setMinimumWidth(0);item.setStyleSheet('QPushButton { padding:4px 10px; }')
        self.refresh()

    def refresh(self):
        value=guide(self.canvas.window.state)
        self.setVisible(bool(value and not value.get('dismissed')))
        if not value:return
        state=self.canvas.window.state;data=state['multi_output']
        self.actions[0].setEnabled(value.get('canvas') in data['canvases'])
        self.actions[2].setEnabled(value.get('clip') in data['clip_inputs'])
        self.actions[3].setEnabled(value.get('stage') in data['stages'])
        stage=data['stages'].get(value.get('stage'),{})
        from .clip_flow import selected_binding
        bound=selected_binding(state,value['clip']) if value.get('clip') in data['clip_inputs'] else None
        text=bool(data['canvases'].get(value.get('canvas'),{}).get('members')) or bool(data['outputs'].get(value.get('output'),{}).get('draft'))
        if not text:current,title=0,'從文字開始，建立第一個流程'
        elif not state.get('generation',{}).get('profiles'):current,title=1,'連接 ComfyUI，選擇一份工作流'
        elif not bound:current,title=2,'將 Prompt 接到工作流的 CLIP'
        elif stage.get('workflow')!=bound['workflow']:current,title=3,'選擇 Stage 的執行工作流'
        elif not getattr(self.canvas.window.comfy,'connected',False):current,title=1,'連線 ComfyUI，準備執行'
        else:current,title=None,'流程已設定，使用底部「執行」'
        self.title.setText(title)
        for index,action in enumerate(self.actions):
            active=index==current;role='Primary' if active else 'Quiet'
            action.setAccessibleDescription('目前步驟' if active else '')
            action.setProperty('currentStep',active)
            if action.objectName()!=role:
                action.setObjectName(role);action.style().unpolish(action);action.style().polish(action);action.update()
        self.arrange()

    def arrange(self):
        inset=12 if self.width()<1100 else 24
        self.outer.setContentsMargins(inset,12 if inset==12 else 20,inset,12 if inset==12 else 16)
        width=max(action.sizeHint().width() for action in self.actions)
        available=self.width()-inset*2-32
        count=4 if available>=width*4+18 else 2 if available>=width*2+6 else 1
        if count==self.columns:return
        self.columns=count
        for item in self.actions:self.steps.removeWidget(item)
        for i,item in enumerate(self.actions):self.steps.addWidget(item,i//count,i%count)
        for col in range(4):self.steps.setColumnStretch(col,1 if col<count else 0)

    def resizeEvent(self,event):
        super().resizeEvent(event);self.arrange()

    def dismiss(self):
        value=guide(self.canvas.window.state)
        if value:value['dismissed']=True;self.canvas.window.changed('settings',refresh=False)
        self.hide()

    def focus(self,key):
        card=(self.canvas.containers.get(key) or self.canvas.outputs.get(key) or self.canvas.clips.get(key)
              or self.canvas.flow_cards.get(key))
        if card:
            view=self.canvas.view;view.resetTransform();view.scale(.9,.9);view.centerOn(card)
        return card

    def add_text(self):
        value=guide(self.canvas.window.state)
        if not value:return
        card=self.focus(value.get('canvas'))
        if card:
            self.canvas.insertion_canvas=card.key
            try:self.canvas.palette(card.pos()+QPointF(40,95))
            finally:self.canvas.insertion_canvas=None

    def connection(self):self.canvas.window.settings('comfy')

    def bind_clip(self):
        value=guide(self.canvas.window.state)
        if value and value.get('clip') in self.canvas.data()['clip_inputs']:
            self.focus(value['clip']);self.canvas.bind_dialog(value['clip']);self.refresh()

    def stage(self):
        value=guide(self.canvas.window.state)
        if value and value.get('stage') in self.canvas.data()['stages']:
            from .stage_parameter_panel import open_parameters
            self.focus(value['stage']);open_parameters(self.canvas,value['stage']);self.refresh()

    def first_view(self):
        value=guide(self.canvas.window.state)
        if not value:return False
        card=self.canvas.containers.get(value.get('canvas'))
        if card is None:return False
        view=self.canvas.view
        execution=getattr(self.canvas,'execution_bar',None)
        bottom=execution.height()+24 if execution is not None and execution.isVisible() else 80
        scale=min(.86,max(.8,(view.viewport().height()-12-bottom)/max(1,card.height)))
        view.resetTransform();view.scale(scale,scale)
        # Keep the starting canvas readable; later steps and the overview move
        # through the same left-to-right graph without changing saved positions.
        view.centerOn(card.x()+view.viewport().width()/scale/2-48,
                      card.y()+view.viewport().height()/scale/2-12/scale)
        return True
