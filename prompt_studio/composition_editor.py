"""Full-content composition editor. The export renderer never sees UI objects."""
import copy,json
from PySide6.QtCore import Qt,QPointF,QRectF,QTimer,QPropertyAnimation,QEasingCurve
from PySide6.QtGui import QColor,QImage,QPixmap,QPen,QPainter,QKeySequence,QShortcut
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFrame,QGraphicsView,QGraphicsScene,QGraphicsPixmapItem,
    QGraphicsRectItem,QFileDialog,QDialog,QFormLayout,QSpinBox,QDoubleSpinBox,QCheckBox,QListWidget,QListWidgetItem,
    QColorDialog,QScrollArea,QLabel,QAbstractItemView)
from .widgets import button,label,row,StudioDialog,dialog_buttons,RoundMenu
from .text_canvas import TextCanvas
from .composition_image import document,layer,resize_layer,validate_image,render_image,PreviewCache
from .canvas_items import NodeCard
from . import composition as comp


class EditorTextCanvas:
    """Text-card gestures use separate composition editing coordinates."""
    def __init__(self,editor):
        self.editor=editor; self.outer=editor.canvas; self.window=self.outer.window; self.cards={}; self.entries={}; self.view=editor.view; self.insertion_key=None
        self.root_id=None; self.path=[]; self.drop_target=None
    def font(self): return self.outer.font()
    def move_cards(self,moves): self.commit(lambda s:s['multi_output']['canvases'][self.editor.key].setdefault('edit_positions',{}).update(moves))
    def commit(self,operation):
        def apply(state):
            before=copy.deepcopy(state.get('text_positions',{})); operation(state)
            data=state['multi_output']['canvases'][self.editor.key]
            for key in data['members']:
                value=state.get('text_positions',{}).get(key)
                if value is not None and value!=before.get(key):
                    data.setdefault('edit_positions',{})[key]=value
                    state['text_positions'][key]=before.get(key,[data['position'][0]+24,data['position'][1]+108])
        return self.outer.commit(apply)
    def add_root(self,state,root,module_id=None):
        from . import multi_output as model
        key=TextCanvas.add_root(self,state,root,module_id); model.assign(state,key,self.editor.key); return key
    def append_value(self,value,position=None):
        position=position or self.view.mapToScene(self.view.viewport().rect().center())
        return TextCanvas.append_value(self,value,position)
    def palette(self,position=None,target=None): return TextCanvas.palette(self,position,target)
    def preview_node_drop(self,*args): pass
    def finish_node_drop(self,*args): return False
    def __getattr__(self,name):
        from types import MethodType
        method=TextCanvas.__dict__.get(name)
        if callable(method): return MethodType(method,self)
        return getattr(self.outer,name)


class ImageView(QGraphicsView):
    def __init__(self,editor):
        super().__init__(); self.editor=editor; self.setScene(QGraphicsScene(self)); self.pixmap=QGraphicsPixmapItem(); self.scene().addItem(self.pixmap)
        self.outline=QGraphicsRectItem(); self.outline.setZValue(5); self.outline.setPen(QPen(QColor('#8fc5ff'),1,Qt.PenStyle.DashLine)); self.scene().addItem(self.outline)
        self.outline.hide(); self.drag=None; self.start=None; self.working=None; self.original=None; self.pan=None; self.space=False
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff); self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setRenderHints(QPainter.RenderHint.Antialiasing|QPainter.RenderHint.SmoothPixmapTransform); self.setBackgroundBrush(QColor('#171b22'))
        self.setFrameShape(QFrame.Shape.NoFrame); self.setViewportUpdateMode(QGraphicsView.ViewportUpdateMode.MinimalViewportUpdate)
    def fit(self):
        doc=self.editor.image(); w,h=(doc['width'],doc['height']) if doc else (1024,1024)
        self.scene().setSceneRect(-100000,-100000,200000,200000)
        bounds=QRectF(-30,-30,w+60,h+60)
        if not doc:
            for card in self.editor.text_canvas.cards.values():
                if card.parentItem() is None: bounds=bounds.united(card.sceneBoundingRect().adjusted(-30,-30,30,30))
        self.fitInView(bounds,Qt.AspectRatioMode.KeepAspectRatio)
    def wheelEvent(self,event):
        scale=self.transform().m11(); factor=1.15**(event.angleDelta().y()/120)
        if .04<scale*factor<8: self.scale(factor,factor)
        event.accept()
    def text_at(self,point):
        item=self.itemAt(point)
        while item is not None:
            if isinstance(item,NodeCard): return True
            item=item.parentItem()
        return False
    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.MiddleButton or (self.space and event.button()==Qt.MouseButton.LeftButton):
            self.begin_pan(event); return
        if self.text_at(event.position().toPoint()): super().mousePressEvent(event); return
        if event.button()!=Qt.MouseButton.LeftButton: super().mousePressEvent(event); return
        e=self.editor; doc=e.image()
        if doc is None: self.begin_pan(event); return
        pos=self.mapToScene(event.position().toPoint()); self.start=pos; self.original=copy.deepcopy(doc); self.working=copy.deepcopy(doc)
        if e.tool=='select':
            selected=e.selected_layer(); scale=self.transform().m11()
            if selected and not selected['locked'] and (pos-QPointF(selected['x']+selected['width'],selected['y']+selected['height'])).manhattanLength()<22/max(scale,.05): self.drag='resize'
            else:
                selected=next((v for v in reversed(doc['layers']) if v['visible'] and not v['locked'] and QRectF(v['x'],v['y'],v['width'],v['height']).adjusted(-4,-4,4,4).contains(pos)),None)
                e.selected=selected['id'] if selected else None; self.drag='move' if selected else None; e.refresh_properties()
                if selected is None: self.begin_pan(event); return
        elif e.tool in ('rect','ellipse'):
            if not QRectF(0,0,doc['width'],doc['height']).contains(pos): return
            self.drag='shape'; item=layer(e.tool,pos.x(),pos.y(),1,1); item.update(fill=e.color,stroke=e.color,stroke_width=e.thickness.value() if e.outline_only.isChecked() else 0)
            if e.outline_only.isChecked(): item['fill']='#00000000'
            self.working['layers'].append(item); e.selected=item['id']
        else:
            if not QRectF(0,0,doc['width'],doc['height']).contains(pos): return
            self.drag='stroke'; item=layer('erase' if e.tool=='erase' else 'stroke',0,0,doc['width'],doc['height'],points=[[pos.x(),pos.y()]])
            item.update(stroke=e.color,stroke_width=e.thickness.value()); self.working['layers'].append(item); e.selected=item['id']
        self.show_selection(); event.accept()
    def mouseMoveEvent(self,event):
        if self.pan is not None:
            delta=event.position().toPoint()-self.pan; self.pan=event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value()-delta.x()); self.verticalScrollBar().setValue(self.verticalScrollBar().value()-delta.y())
            event.accept(); return
        if self.drag is None: super().mouseMoveEvent(event); return
        e=self.editor; pos=self.mapToScene(event.position().toPoint()); delta=pos-self.start
        item=next(v for v in self.working['layers'] if v['id']==e.selected)
        if self.drag in ('move','resize'):
            original=next(v for v in self.original['layers'] if v['id']==e.selected)
            if self.drag=='move': item.update(x=original['x']+delta.x(),y=original['y']+delta.y())
            else:
                width=max(1,original['width']+delta.x()); height=max(1,original['height']+delta.y())
                if item['type']=='image' or e.keep_ratio.isChecked(): height=width*original['height']/original['width']
                if item['type'] in ('stroke','erase'): item.update(points=copy.deepcopy(original['points']),width=original['width'],height=original['height'])
                resize_layer(item,width,height)
        elif self.drag=='shape':
            rect=QRectF(self.start,pos).normalized(); item.update(x=rect.x(),y=rect.y(),width=max(1,rect.width()),height=max(1,rect.height()))
        else:
            if len(item['points'])<10000: item['points'].append([pos.x(),pos.y()])
        e.paint_document(self.working); self.show_selection(self.working); event.accept()
    def mouseReleaseEvent(self,event):
        if self.pan is not None:
            self.pan=None; self.setCursor(Qt.CursorShape.ArrowCursor if self.editor.tool=='select' else Qt.CursorShape.CrossCursor); event.accept(); return
        if event.button()!=Qt.MouseButton.LeftButton: super().mouseReleaseEvent(event); return
        if self.drag is None: super().mouseReleaseEvent(event); return
        if self.drag=='stroke':
            item=self.working['layers'][-1]; points=item['points']; x=min(p[0] for p in points); y=min(p[1] for p in points)
            item.update(x=x,y=y,width=max(1,max(p[0] for p in points)-x),height=max(1,max(p[1] for p in points)-y),points=[[p[0]-x,p[1]-y] for p in points])
        value=self.working; self.drag=None; self.working=None; self.editor.commit_image(value); event.accept()
    def show_selection(self,value=None):
        value=value or self.editor.image(); item=next((v for v in value['layers'] if v['id']==self.editor.selected),None) if value else None
        if item and item['visible']:
            self.outline.setRect(item['x'],item['y'],item['width'],item['height']); self.outline.show()
        else: self.outline.hide()
    def keyPressEvent(self,event):
        if event.key()==Qt.Key.Key_Space: self.space=True; event.accept(); return
        if event.key() in (Qt.Key.Key_Delete,Qt.Key.Key_Backspace):
            cards=[v for v in self.scene().selectedItems() if isinstance(v,NodeCard)]
            if cards: self.editor.text_canvas.delete_selected()
            else: self.editor.remove_layer()
            event.accept(); return
        super().keyPressEvent(event)
    def keyReleaseEvent(self,event):
        if event.key()==Qt.Key.Key_Space: self.space=False; event.accept(); return
        super().keyReleaseEvent(event)
    def focusOutEvent(self,event): self.space=False; self.pan=None; super().focusOutEvent(event)
    def begin_pan(self,event):
        self.pan=event.position().toPoint(); self.setCursor(Qt.CursorShape.ClosedHandCursor); event.accept()
    def mouseDoubleClickEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton and not self.text_at(event.position().toPoint()):
            self.pan=None; self.editor.text_canvas.palette(self.mapToScene(event.position().toPoint())); event.accept(); return
        super().mouseDoubleClickEvent(event)
    def contextMenuEvent(self,event):
        if self.text_at(event.pos()): super().contextMenuEvent(event); return
        self.pan=None; position=self.mapToScene(event.pos()); e=self.editor; menu=RoundMenu(e.window)
        menu.addAction('加入 Tag 與素材',lambda:e.text_canvas.palette(position))
        menu.addAction('新增文字模組',lambda:e.text_canvas.add_new(position))
        menu.addAction('匯入圖片…',e.import_images); menu.addSeparator()
        menu.addAction('置中顯示',self.fit); menu.addAction('復原',e.undo).setEnabled(bool(e.canvas.undo_stack))
        menu.addAction('重做',e.redo).setEnabled(bool(e.canvas.redo_stack)); menu.open_at(event.globalPos()); event.accept()


class CompositionEditor(QWidget):
    def __init__(self,canvas,key):
        super().__init__(); self.canvas=canvas; self.window=canvas.window; self.key=key; self.selected=None; self.tool='select'; self.color='#718caa'
        self.cache=PreviewCache(); self.signature=None; self.refreshing=False; self.properties_updating=False
        self.outer_transform=canvas.view.transform(); self.outer_center=canvas.view.mapToScene(canvas.view.viewport().rect().center())
        self.setObjectName('WorkspaceSurface'); body=QVBoxLayout(self); body.setContentsMargins(14,12,14,12)
        self.title=label(self.data()['name'],'Heading'); self.size_button=button('啟用底圖',self.dimensions,'Quiet')
        self.undo_button=button('復原',self.undo,'Quiet'); self.redo_button=button('重做',self.redo,'Quiet')
        body.addLayout(row(button('‹ 返回工作區',self.close_editor,'Quiet'),self.title,None,self.size_button))
        body.addLayout(row(self.undo_button,self.redo_button,None,button('背景',self.background,'Quiet'),button('輸出預覽',self.preview,'Quiet'),button('匯出底圖',self.export,'Quiet')))
        tools=QVBoxLayout(); tools.setSpacing(8); self.tool_buttons={}
        for key,title in [('select','選取／移動'),('brush','筆刷'),('erase','橡皮擦'),('rect','矩形'),('ellipse','橢圓')]:
            b=button(title,lambda checked=False,k=key:self.set_tool(k),'Quiet'); b.setCheckable(True); self.tool_buttons[key]=b; tools.addWidget(b)
        tools.addWidget(button('匯入圖片',self.import_images,'Quiet')); self.color_button=button('顏色',self.choose_color,'Quiet'); tools.addWidget(self.color_button)
        self.thickness=QSpinBox(); self.thickness.setRange(1,300); self.thickness.setValue(12); self.thickness.setSuffix(' px'); tools.addWidget(label('筆寬／外框','Subtle')); tools.addWidget(self.thickness)
        self.outline_only=QCheckBox('只畫外框'); tools.addWidget(self.outline_only)
        self.show_text=QCheckBox('顯示文字模組'); self.show_text.setChecked(True); self.show_text.toggled.connect(self.refresh_text); tools.addWidget(self.show_text)
        self.layer_toggle=QCheckBox('圖層與屬性'); self.layer_toggle.setChecked(True); tools.addWidget(self.layer_toggle); tools.addStretch()
        center=QHBoxLayout(); body.addLayout(center,1)
        tools_body=QWidget(); tools_body.setObjectName('ScrollContent'); tools_body.setLayout(tools); tools_scroll=QScrollArea(); tools_scroll.viewport().setObjectName('ScrollViewport'); tools_scroll.setWidgetResizable(True); tools_scroll.setWidget(tools_body); tools_scroll.setFrameShape(QFrame.Shape.NoFrame)
        tools_scroll.setMinimumWidth(155); tools_scroll.setMaximumWidth(230); center.addWidget(tools_scroll)
        self.view=ImageView(self); center.addWidget(self.view,1); self.text_canvas=EditorTextCanvas(self)
        self.sidebar=QFrame(); self.sidebar.setObjectName('ScrollContent'); side=QVBoxLayout(self.sidebar); side.setContentsMargins(8,0,0,0)
        self.sidebar_scroll=QScrollArea(); self.sidebar_scroll.viewport().setObjectName('ScrollViewport'); self.sidebar_scroll.setWidgetResizable(True); self.sidebar_scroll.setWidget(self.sidebar); self.sidebar_scroll.setMinimumWidth(200); self.sidebar_scroll.setMaximumWidth(300); self.sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame); center.addWidget(self.sidebar_scroll)
        self.layer_toggle.toggled.connect(self.sidebar_scroll.setVisible)
        self.layers=QListWidget(); self.layers.setMinimumHeight(130); self.layers.currentItemChanged.connect(self.selected_changed); side.addWidget(self.layers,1)
        side.addLayout(row(button('上移',lambda:self.stack(1),'Quiet'),button('下移',lambda:self.stack(-1),'Quiet'),button('刪除',self.remove_layer,'Quiet')))
        self.properties=QWidget(); form=QFormLayout(self.properties); form.setContentsMargins(0,0,0,0); self.fields={}
        for field,title in [('x','X'),('y','Y'),('width','寬度'),('height','高度')]:
            box=QDoubleSpinBox(); box.setDecimals(1); box.setRange(1 if field in ('width','height') else -100000,100000); box.editingFinished.connect(lambda f=field:self.change_geometry(f)); self.fields[field]=box; form.addRow(title,box)
        self.keep_ratio=QCheckBox('等比例縮放'); self.keep_ratio.setChecked(True); form.addRow(self.keep_ratio)
        self.visible=QCheckBox('顯示'); self.locked=QCheckBox('鎖定'); self.visible.toggled.connect(lambda v:self.property_change('visible',v)); self.locked.toggled.connect(lambda v:self.property_change('locked',v)); form.addRow(self.visible,self.locked)
        self.fill_button=button('填色',lambda:self.layer_color('fill'),'Quiet'); self.stroke_button=button('外框色',lambda:self.layer_color('stroke'),'Quiet'); form.addRow(self.fill_button,self.stroke_button)
        self.stroke_width=QSpinBox(); self.stroke_width.setRange(0,300); self.stroke_width.editingFinished.connect(lambda:self.property_change('stroke_width',self.stroke_width.value())); form.addRow('外框／筆寬',self.stroke_width)
        self.crop_button=button('裁切圖片',self.crop,'Quiet'); form.addRow(self.crop_button); side.addWidget(self.properties)
        self.message=label('','Subtle',True); body.addWidget(self.message)
        self.animation=None; self.closing=False; self.tool_buttons['select'].setChecked(True); self.refresh(); QTimer.singleShot(0,self.view.fit)
        for shortcut,callback in [('Ctrl+Z',self.undo),('Ctrl+Y',self.redo)]:
            command=QShortcut(QKeySequence(shortcut),self); command.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut); command.activated.connect(callback)
    def data(self): return self.canvas.data()['canvases'][self.key]
    def image(self):
        from .composition_image import canvas_document
        try: return canvas_document(self.window.state,self.key)
        except ValueError: return self.data().get('image')
    def animate_open(self):
        if self.window.state['settings'].get('reduce_motion',False): return
        container=self.canvas.containers[self.key]; viewport=self.canvas.view.viewport()
        rect=self.canvas.view.mapFromScene(container.sceneBoundingRect()).boundingRect(); rect.moveTopLeft(self.window.surface_stack.mapFromGlobal(viewport.mapToGlobal(rect.topLeft())))
        self.animation=QPropertyAnimation(self,b'geometry',self); self.animation.setDuration(180); self.animation.setStartValue(rect); self.animation.setEndValue(self.window.surface_stack.rect()); self.animation.setEasingCurve(QEasingCurve.Type.OutCubic); self.animation.start()
    def close_editor(self):
        if self.closing: return
        self.closing=True
        if self.animation: self.animation.stop()
        if self.window.state['settings'].get('reduce_motion',False): self.finish_close(); return
        container=self.canvas.containers[self.key]; viewport=self.canvas.view.viewport()
        rect=self.canvas.view.mapFromScene(container.sceneBoundingRect()).boundingRect(); rect.moveTopLeft(self.window.surface_stack.mapFromGlobal(viewport.mapToGlobal(rect.topLeft())))
        self.animation=QPropertyAnimation(self,b'geometry',self); self.animation.setDuration(180); self.animation.setStartValue(self.geometry()); self.animation.setEndValue(rect); self.animation.setEasingCurve(QEasingCurve.Type.InCubic)
        self.animation.finished.connect(self.finish_close); self.animation.start()
    def finish_close(self):
        self.window.state['canvas_view']=[self.outer_transform.m11(),self.outer_center.x(),self.outer_center.y()]
        self.canvas.editor_page=None; self.window.surface_stack.setCurrentWidget(self.window.canvas_shell)
        self.canvas.refresh(); self.canvas.view.setTransform(self.outer_transform); self.canvas.view.centerOn(self.outer_center)
        self.window.surface_stack.removeWidget(self); self.deleteLater()
    def commit_image(self,value):
        value=copy.deepcopy(value)
        value['layers']=[v for v in value['layers'] if v['id']!='connected-base']
        try: validate_image(value)
        except ValueError as exc: self.message.setText(str(exc)); self.refresh(); return False
        success=self.canvas.commit(lambda s:s['multi_output']['canvases'][self.key].update(image=copy.deepcopy(value)))
        self.signature=None; self.refresh(); return success
    def dimensions(self,checked=False,initial=None):
        current=self.image(); width,height=initial or ((current['width'],current['height']) if current else (1024,1024))
        dialog=StudioDialog(self.window); dialog.setWindowTitle('底圖尺寸'); form=QFormLayout(); dialog.body.addLayout(form)
        w=QSpinBox(); h=QSpinBox()
        for box,value in ((w,width),(h,height)): box.setRange(1,16384); box.setValue(value)
        form.addRow('寬度',w); form.addRow('高度',h)
        description=label('修改輸出範圍；圖層保持原座標與大小。','Subtle',True); dialog.body.addWidget(description)
        preview=QLabel(); preview.setFixedSize(300,220); preview.setAlignment(Qt.AlignmentFlag.AlignCenter); dialog.body.addWidget(preview)
        def show():
            draft=copy.deepcopy(current) if current else document(); draft.update(width=w.value(),height=h.value())
            try: picture=render_image(draft,self.window.store.directory,self.cache,300,False); preview.setPixmap(QPixmap.fromImage(picture).scaled(300,220,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            except ValueError as exc: preview.setText(str(exc))
        w.valueChanged.connect(show); h.valueChanged.connect(show); show(); dialog.body.addWidget(dialog_buttons(dialog,dialog.accept))
        if dialog.exec()!=QDialog.DialogCode.Accepted: return False
        value=copy.deepcopy(current) if current else document(); value.update(width=w.value(),height=h.value())
        result=self.commit_image(value); self.view.fit(); return result
    def set_tool(self,key):
        if key!='select' and self.image() is None and not self.dimensions(): return
        self.tool=key
        for name,b in self.tool_buttons.items(): b.setChecked(name==key)
        self.view.setCursor(Qt.CursorShape.ArrowCursor if key=='select' else Qt.CursorShape.CrossCursor)
    def choose_color(self):
        color=QColorDialog.getColor(QColor(self.color),self.window,'筆刷／圖形顏色')
        if color.isValid(): self.color=color.name(); self.color_button.setStyleSheet('color:'+self.color)
    def background(self):
        if self.image() is None and not self.dimensions(): return
        color=QColorDialog.getColor(QColor(self.image()['background']),self.window,'底圖背景')
        if color.isValid():
            doc=copy.deepcopy(self.image()); doc['background']=color.name(); self.commit_image(doc)
    def import_images(self,checked=False,paths=None):
        if paths is None: paths,_=QFileDialog.getOpenFileNames(self.window,'匯入構圖圖片','','圖片 (*.png *.jpg *.jpeg *.webp *.bmp)')
        if not paths: return
        try:
            sources=[self.window.generation_panel.import_source(path) for path in paths]
            if self.image() is None and not self.dimensions(initial=(sources[0]['width'],sources[0]['height'])): return
            value=copy.deepcopy(self.image())
            for index,source in enumerate(sources):
                scale=min(1,value['width']/source['width'],value['height']/source['height'])
                item=layer('image',index*20,index*20,source['width']*scale,source['height']*scale,source=source,crop=[0,0,1,1]); item['name']=source['name']; value['layers'].append(item); self.selected=item['id']
            self.commit_image(value)
        except (ValueError,OSError) as exc: self.message.setText(str(exc))
    def paint_document(self,value):
        try:
            image=render_image(value,self.window.store.directory,self.cache,2048,False); self.view.pixmap.setPixmap(QPixmap.fromImage(image)); self.view.pixmap.setScale(value['width']/image.width())
            self.message.setText('')
        except (ValueError,OSError) as exc: self.message.setText(str(exc)); self.view.pixmap.setPixmap(QPixmap())
    def refresh(self):
        if self.refreshing: return
        self.refreshing=True
        try:
            self.title.setText(self.data()['name']); doc=self.image(); signature=json.dumps(doc,sort_keys=True)
            if signature!=self.signature:
                if doc: self.paint_document(doc)
                else: self.view.pixmap.setPixmap(QPixmap()); self.message.setText('純文字畫布。匯入圖片或選擇繪圖工具時，再設定底圖尺寸。')
                self.signature=signature
            self.size_button.setText(f"{doc['width']} × {doc['height']}" if doc else '啟用底圖')
            self.layers.blockSignals(True); self.layers.clear()
            for item in reversed(doc['layers'] if doc else []):
                entry=QListWidgetItem(('🔒 ' if item['locked'] else '')+item['name']+(' · 隱藏' if not item['visible'] else '')); entry.setData(Qt.ItemDataRole.UserRole,item['id']); self.layers.addItem(entry)
                if item['id']==self.selected: self.layers.setCurrentItem(entry)
            self.layers.blockSignals(False); self.refresh_properties(); self.refresh_text()
            self.undo_button.setEnabled(bool(self.canvas.undo_stack)); self.redo_button.setEnabled(bool(self.canvas.redo_stack))
        finally: self.refreshing=False
    def refresh_text(self):
        for card in list(self.text_canvas.cards.values()):
            if card.parentItem() is None: self.view.scene().removeItem(card); card.deleteLater()
        self.text_canvas.cards={}; self.text_canvas.entries={}
        if not self.show_text.isChecked(): return
        data=self.data(); state=self.window.state
        for key in data['members']:
            root=state['uses'][key]; self.text_canvas.entries[key]=(key,root['id'])
            for part in comp.walk(root):
                if part is not root: self.text_canvas.entries[key+':'+part['id']]=(key,part['id'])
            card=NodeCard(self.text_canvas,key,copy.deepcopy(root),'文字模組',not root['enabled']); self.view.scene().addItem(card); card.setZValue(10)
            outer=state.get('text_positions',{}).get(key,data['position']); default=[outer[0]-data['position'][0],outer[1]-data['position'][1]]
            card.setPos(*data.get('edit_positions',{}).get(key,default))
    def selected_layer(self): return next((v for v in (self.image() or {}).get('layers',[]) if v['id']==self.selected),None)
    def selected_changed(self,item,*args):
        self.selected=item.data(Qt.ItemDataRole.UserRole) if item else None; self.refresh_properties()
    def refresh_properties(self):
        self.properties_updating=True; item=self.selected_layer(); self.properties.setEnabled(item is not None)
        if item:
            for key,box in self.fields.items(): box.setValue(item[key]); box.setEnabled(not item['locked'])
            self.visible.setChecked(item['visible']); self.locked.setChecked(item['locked']); self.stroke_width.setValue(round(item['stroke_width']))
            self.crop_button.setVisible(item['type']=='image'); self.fill_button.setVisible(item['type'] in ('rect','ellipse')); self.stroke_button.setVisible(item['type']!='image')
        self.view.show_selection(); self.properties_updating=False
    def property_change(self,key,value):
        if self.properties_updating or self.selected_layer() is None: return
        doc=copy.deepcopy(self.image()); item=next(v for v in doc['layers'] if v['id']==self.selected); item[key]=value; self.commit_image(doc)
    def change_geometry(self,key):
        item=self.selected_layer()
        if item is None or item['locked'] or self.properties_updating: return
        value=self.fields[key].value()
        if value==item[key]: return
        doc=copy.deepcopy(self.image()); target=next(v for v in doc['layers'] if v['id']==self.selected)
        width=value if key=='width' else item['width']; height=value if key=='height' else item['height']
        if (item['type']=='image' or self.keep_ratio.isChecked()) and key in ('width','height'):
            if key=='width': height=value*item['height']/item['width']
            else: width=value*item['width']/item['height']
        if key in ('width','height'): resize_layer(target,width,height)
        else: target[key]=value
        self.commit_image(doc)
    def layer_color(self,key):
        item=self.selected_layer()
        if item is None: return
        color=QColorDialog.getColor(QColor(item[key]),self.window,'圖層顏色',QColorDialog.ColorDialogOption.ShowAlphaChannel)
        if color.isValid(): self.property_change(key,color.name(QColor.NameFormat.HexArgb))
    def stack(self,delta):
        doc=copy.deepcopy(self.image()); items=doc['layers'] if doc else []; index=next((i for i,v in enumerate(items) if v['id']==self.selected),None)
        if index is None: return
        at=max(0,min(len(items)-1,index+delta)); items.insert(at,items.pop(index)); self.commit_image(doc)
    def remove_layer(self):
        if self.selected_layer() is None or self.selected_layer()['locked']: return
        doc=copy.deepcopy(self.image()); doc['layers']=[v for v in doc['layers'] if v['id']!=self.selected]; self.selected=None; self.commit_image(doc)
    def crop(self):
        selected=self.selected_layer()
        if selected is None or selected['type']!='image' or selected['locked']: return
        dialog=StudioDialog(self.window); dialog.setWindowTitle('裁切圖片'); form=QFormLayout(); dialog.body.addLayout(form); fields=[]
        for name,value in zip(('左邊界 %','上邊界 %','右邊界 %','下邊界 %'),selected.get('crop',[0,0,1,1])):
            box=QDoubleSpinBox(); box.setRange(0,100); box.setValue(value*100); form.addRow(name,box); fields.append(box)
        preview=QLabel(); preview.setFixedSize(320,240); preview.setAlignment(Qt.AlignmentFlag.AlignCenter); dialog.body.addWidget(preview)
        def show():
            doc=copy.deepcopy(self.image()); item=next(v for v in doc['layers'] if v['id']==self.selected); item['crop']=[f.value()/100 for f in fields]
            try: picture=render_image(doc,self.window.store.directory,self.cache,320,False); preview.setPixmap(QPixmap.fromImage(picture).scaled(320,240,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation))
            except ValueError as exc: preview.setText(str(exc))
        for f in fields: f.valueChanged.connect(show)
        show(); dialog.body.addWidget(dialog_buttons(dialog,dialog.accept))
        if dialog.exec()==QDialog.DialogCode.Accepted:
            crop=[f.value()/100 for f in fields]
            if crop[2]<=crop[0] or crop[3]<=crop[1]: self.message.setText('裁切範圍不能為空。'); return
            doc=copy.deepcopy(self.image()); target=next(v for v in doc['layers'] if v['id']==self.selected); target['crop']=crop
            source=target['source']; target['height']=target['width']*source['height']*(crop[3]-crop[1])/(source['width']*(crop[2]-crop[0])); self.commit_image(doc)
    def preview(self):
        if self.image() is None: return
        try: image=render_image(self.image(),self.window.store.directory,self.cache,0,True)
        except (ValueError,OSError) as exc: self.message.setText(str(exc)); return
        dialog=StudioDialog(self.window); dialog.setWindowTitle('輸出預覽'); preview=QLabel(); preview.setPixmap(QPixmap.fromImage(image).scaled(900,700,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)); dialog.body.addWidget(preview); dialog.exec()
    def export(self):
        if self.image() is None: return
        path,_=QFileDialog.getSaveFileName(self.window,'匯出底圖',self.data()['name']+'.png','PNG (*.png)')
        if not path: return
        try:
            image=render_image(self.image(),self.window.store.directory,self.cache,0,True)
            if not image.save(path,'PNG'): raise ValueError('底圖匯出失敗。')
            self.message.setText('已匯出底圖。')
        except (ValueError,OSError) as exc: self.message.setText(str(exc))
    def undo(self): self.canvas.undo(); self.signature=None; self.refresh()
    def redo(self): self.canvas.redo(); self.signature=None; self.refresh()
