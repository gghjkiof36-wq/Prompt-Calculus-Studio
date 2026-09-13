"""Image function cards are separate from text composition and can dock to output."""
import copy
from pathlib import Path
from PySide6.QtCore import Qt,QRectF,QPointF
from PySide6.QtGui import QPainterPath
from PySide6.QtWidgets import QFrame,QVBoxLayout
from .widgets import label,button,row,ElidedLabel,RoundMenu
from .image_preview import SourcePreview
from .error_dialog import import_error
from .local_files import choose_file
from .canvas_items import TextCard
from .core import uid


class SourcePanel(QFrame):
    def __init__(self,owner,key):
        super().__init__(); self.owner=owner; self.key=key; self.current=None
        self.setObjectName('InsetPanel'); body=QVBoxLayout(self); body.setContentsMargins(14,14,14,14)
        self.preview=SourcePreview(owner.window.store); self.preview.setMinimumSize(240,160); self.preview.clicked.connect(self.choose_file)
        self.preview.setWordWrap(True); self.preview.pathReady.connect(self.load); self.preview.failed.connect(lambda message:import_error(owner.window,message))
        body.addWidget(self.preview,1); self.name=ElidedLabel(''); body.addWidget(self.name)
        self.choose=button('選擇圖片 ▾',self.source_menu,'Quiet'); self.attach_button=button('接到 Prompt',self.toggle_attachment,'Quiet')
        body.addLayout(row(self.choose,None,self.attach_button))
    def choose_file(self):
        path=choose_file(self.owner.window,'選擇圖片','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if path: self.load(path)
    def load(self,path): return self.owner.replace(self.key,path)
    def source_menu(self):
        menu=RoundMenu(self.owner.window); menu.addAction('選擇圖片…',self.choose_file)
        for name,kind in (('媒體庫','image'),('最近生成','recent')):
            menu.addAction(name,lambda checked=False,k=kind:self.owner.window.generation_panel.choose_catalog(k,self.load))
        menu.open_at(self.cursor().pos())
    def toggle_attachment(self): self.owner.attach(self.key,not self.owner.data()['images'][self.key].get('attached',False))
    def refresh(self):
        value=self.owner.data()['images'][self.key]; source=value.get('source'); self.current=source
        self.attach_button.setText('解除拼接' if value.get('attached') else '接到 Prompt')
        self.name.setVisible(bool(source)); self.name.setText(source['name'] if source else '')
        self.update_image()
    def update_image(self):
        self.preview.load_path(self.owner.window.store.directory/self.current['relative'] if self.current else None)


class SourceCard(TextCard):
    def __init__(self,owner,key):
        super().__init__(owner.canvas); self.owner=owner; self.key=key; self.title='載入圖片'
        self.panel=SourcePanel(owner,key); self.setToolTip('')
    def attach(self):
        if self.proxy.widget() is not self.panel: self.proxy.setWidget(self.panel)
        self.panel.setStyleSheet(self.canvas.window.styleSheet()); self.panel.ensurePolished()
        self.requested_size=self.canvas.window.state.get('text_sizes',{}).get(self.key,[320,350])
        self.layout_card(); self.panel.show(); self.panel.refresh()
    def layout_card(self):
        width,height=self.requested_size; minimum=self.panel.minimumSizeHint()
        top=78 if 'multi_output' in self.canvas.window.state else 46
        self.proxy.setGeometry(QRectF(14,top,max(280,minimum.width(),width-28),max(230,minimum.height(),height-top-14)))
        self.sync_bounds()
    def contextMenuEvent(self,event): self.owner.context(self.key,event.screenPos()); event.accept()
    def boundingRect(self): return QRectF(0,0,self.width+14,self.height)


class CanvasFunctions:
    def __init__(self,canvas): self.canvas=canvas; self.window=canvas.window; self.cards={}; self.laying_out=False
    def data(self,state=None):
        state=self.window.state if state is None else state
        return state.setdefault('canvas_functions',dict(images={},preview=True,preview_attached=False))
    def outline(self,card,path):
        def tab(x,y):
            join=QPainterPath(); join.moveTo(x,y); join.lineTo(x+7,y); join.lineTo(x+7,y-7)
            join.lineTo(x+14,y-7); join.lineTo(x+14,y+25); join.lineTo(x+7,y+25)
            join.lineTo(x+7,y+18); join.lineTo(x,y+18); join.closeSubpath(); return join
        if card.key in self.cards: return path.united(tab(card.width-1,82))
        if card is self.canvas.output:
            for key,value in self.data()['images'].items():
                if value.get('attached') and key in self.cards:
                    return path.subtracted(tab(1,card.height-self.cards[key].height+82))
        return path
    def add_image(self,path=None,position=None,source=None,attached=False):
        try:
            if path: source=self.window.generation_panel.import_source(path)
        except (ValueError,OSError) as exc: import_error(self.window,exc); return
        key='__source_'+uid()
        def apply(state):
            data=self.data(state); data['images'][key]=dict(source=copy.deepcopy(source),attached=False)
            if position is not None: state.setdefault('text_positions',{})[key]=[position.x(),position.y()]
            if attached: self.set_attachment(state,key,True)
        return key if self.canvas.commit(apply) else None
    def use_source(self,source):
        data=self.data(); key=next((k for k,v in data['images'].items() if v.get('attached')),None)
        if key:
            data['images'][key]['source']=copy.deepcopy(source); self.refresh(); return
        self.add_image(source=source,attached=True)
    def mode_changed(self):
        if self.window.generation_panel.settings()['mode']=='img2img' and not any(v.get('attached') for v in self.data()['images'].values()):
            self.add_image(source=self.window.generation_panel.settings().get('source'),attached=True)
    def set_attachment(self,state,key,attached):
        data=self.data(state)
        if key=='__result_preview__': data['preview_attached']=attached; return
        entry=data['images'][key]; was_attached=entry.get('attached',False)
        if attached:
            for value in data['images'].values(): value['attached']=False
            entry['attached']=True; settings=state.setdefault('generation',dict(mode='txt2img',profiles=[],chosen={},source=None))
            settings.update(mode='img2img',source=copy.deepcopy(entry.get('source')))
        elif was_attached:
            entry['attached']=False; state['generation'].update(mode='txt2img',source=None)
    def attach(self,key,attached=True):
        def apply(state):
            self.set_attachment(state,key,attached)
            if not attached:
                card=self.cards.get(key) or self.canvas.preview_card
                if card: state.setdefault('text_positions',{})[key]=[card.x()-64 if key!='__result_preview__' else card.x()+64,card.y()+48]
        self.canvas.commit(apply); self.window.generation_panel.changed()
    def replace(self,key,path):
        try: source=self.window.generation_panel.import_source(path)
        except (ValueError,OSError) as exc: import_error(self.window,exc); return
        def apply(state):
            value=self.data(state)['images'][key]; value['source']=source
            if value.get('attached'): state['generation']['source']=copy.deepcopy(source)
        result=self.canvas.commit(apply); self.window.generation_panel.changed(); return result is not False
    def remove(self,key):
        def apply(state):
            data=self.data(state)
            if key=='__result_preview__': data['preview']=False
            else: self.set_attachment(state,key,False); data['images'].pop(key,None)
        self.canvas.commit(apply); self.window.generation_panel.changed()
    def show_preview(self):
        self.canvas.commit(lambda state:self.data(state).update(preview=True)); self.canvas.fit()
    def refresh(self):
        if self.canvas.output is None: return
        data=self.data()
        for key in list(self.cards):
            if key not in data['images']:
                card=self.cards.pop(key); self.canvas.view.scene().removeItem(card); card.deleteLater()
        for index,key in enumerate(data['images']):
            if key not in self.cards:
                self.cards[key]=SourceCard(self,key); self.canvas.view.scene().addItem(self.cards[key])
            card=self.cards[key]; card.attach()
            default=[self.canvas.output.x()-card.width-44,self.canvas.output.y()+index*50]
            card.setPos(*self.window.state.get('text_positions',{}).get(key,default))
        if self.canvas.preview_card: self.canvas.preview_card.setVisible(data.get('preview',True))
        self.layout()
    def layout(self,skip=None):
        output=self.canvas.output
        if not output or self.laying_out: return
        self.laying_out=True
        try:
            for key,card in self.cards.items():
                if key!=skip and self.data()['images'][key].get('attached'):
                    card.setPos(output.x()-card.width+2,output.y()+output.height-card.height)
            preview=self.canvas.preview_card
            if preview and skip!='__result_preview__' and self.data().get('preview_attached'): preview.setPos(output.x()+output.width-2,output.y())
            output.update()
        finally: self.laying_out=False
    def moving(self,card,position):
        if card.key=='__text_output__': self.layout(); return
        self.canvas.output.dock_hover=self.canvas.output.sceneBoundingRect().contains(position); self.canvas.output.update()
    def dropped(self,card,position):
        if card.key=='__text_output__': return False
        if card.key not in self.cards and card.key!='__result_preview__': return False
        dock=self.canvas.output.sceneBoundingRect().contains(position); self.canvas.output.dock_hover=False; self.canvas.output.update()
        def apply(state):
            self.set_attachment(state,card.key,dock)
            state.setdefault('text_positions',{})[card.key]=[card.x(),card.y()]
        self.canvas.commit(apply); self.window.generation_panel.changed(); return True
    def menu(self,parent,position=None):
        menu=RoundMenu(parent); menu.setTitle('功能模組')
        menu.addAction('載入圖片',lambda:self.add_image(position=position or self.canvas.view.mapToScene(self.canvas.view.viewport().rect().center())))
        menu.addAction('圖片預覽',self.show_preview)
        menu.addAction('最終 Prompt',lambda:self.canvas.view.centerOn(self.canvas.output))
        return menu
    def context(self,key,position):
        menu=RoundMenu(self.window)
        attached=self.data().get('preview_attached') if key=='__result_preview__' else self.data()['images'][key].get('attached')
        menu.addAction('解除拼接' if attached else '接到 Prompt',lambda:self.attach(key,not attached))
        menu.addAction('調整尺寸…',lambda:self.canvas.size_dialog(key))
        if key in self.cards:
            menu.addAction('選擇圖片…',self.cards[key].panel.choose_file)
            source=self.data()['images'][key].get('source')
            if source: menu.addAction('載入圖片提示詞…',lambda:self.window.generation_panel.load_image_prompt(source))
        menu.addAction('移除',lambda:self.remove(key)); menu.open_at(position)
