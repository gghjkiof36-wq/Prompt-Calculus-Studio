"""A light Graphics View over the same selected compositions as the list."""
import copy
from PySide6.QtCore import Qt, QPointF, QRectF, QTimer
from PySide6.QtWidgets import (QWidget, QVBoxLayout, QLineEdit, QPlainTextEdit, QFormLayout,
    QDoubleSpinBox, QCheckBox, QDialog, QListWidget, QListWidgetItem)

from . import composition as comp
from .canvas_palette import CanvasPalette, CanvasTagEdit
from .canvas_items import TextCard, PreviewCard, NodeCard, CanvasView
from .core import uid, validate_state, output_groups, TEMPORARY_GROUP
from .widgets import label, button, row, StudioDialog, dialog_buttons, RoundMenu, InputDialog
from .error_dialog import import_error


class NodeDialog(StudioDialog):
    def __init__(self, parent, value=None, title='新增模組'):
        super().__init__(parent); self.setWindowTitle(title); self.resize(550, 550)
        value = value or comp.node('')
        self.description = label('只修改這次組合；素材庫原文保留。', 'Subtle', True)
        self.body.addWidget(self.description)
        form = QFormLayout(); self.body.addLayout(form)
        self.name = QLineEdit(value['name']); form.addRow('名稱', self.name)
        self.prompt = QPlainTextEdit(value['prompt']); self.prompt.setPlaceholderText('可留空作為群組，再加入子模組。')
        self.body.addWidget(self.prompt, 1)
        self.weight = QDoubleSpinBox(); self.weight.setRange(0, 100); self.weight.setDecimals(1); self.weight.setSingleStep(.1)
        self.weight.setValue(value['weight'] / 10); form.addRow('整組權重', self.weight)
        self.enabled = QCheckBox('啟用'); self.enabled.setChecked(value['enabled']); form.addRow(self.enabled)
        self.excludes = QPlainTextEdit('\n'.join(value['excludes'])); self.excludes.setMaximumHeight(75)
        self.excludes.setPlaceholderText('使用時排除的完整 Tag，每行一項。')
        self.body.addWidget(self.excludes)
        self.error = label('', 'Draft', True); self.body.addWidget(self.error)
        self.body.addWidget(dialog_buttons(self, self.save))

    def save(self):
        if not self.name.text().strip(): self.error.setText('請填寫模組名稱。'); return
        self.accept()

    def values(self):
        return dict(name=self.name.text().strip(), prompt=self.prompt.toPlainText(),
                    weight=round(self.weight.value() * 10), enabled=self.enabled.isChecked(),
                    excludes=[v.strip() for v in self.excludes.toPlainText().splitlines() if v.strip()])


class AssetDialog(StudioDialog):
    def __init__(self, window):
        super().__init__(window); self.setWindowTitle('加入既有素材'); self.resize(550, 540)
        self.window = window; self.query = QLineEdit(); self.query.setPlaceholderText('搜尋名稱、別名或 Prompt')
        self.body.addWidget(self.query); self.list = QListWidget(); self.body.addWidget(self.list, 1)
        self.body.addWidget(label('加入素材原型的副本；之後可獨立調整這次使用。', 'Subtle', True))
        self.body.addWidget(dialog_buttons(self, self.save)); self.query.textChanged.connect(self.refresh)
        self.list.itemDoubleClicked.connect(lambda _: self.save()); self.refresh()

    def refresh(self):
        self.list.clear(); query = self.query.text().casefold()
        for asset in self.window.state['items']:
            if query not in ' '.join([asset['name'], asset['prompt'], *asset['aliases']]).casefold(): continue
            entry = QListWidgetItem(asset['name']); entry.setData(Qt.ItemDataRole.UserRole, asset['id'])
            entry.setToolTip(asset['prompt']); self.list.addItem(entry)

    def save(self):
        if self.list.currentItem(): self.accept()

    def asset(self):
        ident = self.list.currentItem().data(Qt.ItemDataRole.UserRole)
        return next(i for i in self.window.state['items'] if i['id'] == ident)


HISTORY_FIELDS = ('version', 'modules', 'items', 'selections', 'temporary', 'weights', 'instances', 'output_order', 'text_positions', 'text_sizes', 'uses', 'workspaces', 'workspace', 'prompt_layout', 'view_drafts','canvas_functions','generation')


class TextCanvas(QWidget):
    def __init__(self, window):
        super().__init__(); self.window = window; self.root_id = None; self.path = []
        self.undo_stack = []; self.redo_stack = []; self.last_state = None; self.entries = {}; self.output=None; self.preview_card=None; self.cards={}
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 0, 0, 0)
        self.view = CanvasView(self); layout.addWidget(self.view, 1)
        from .canvas_functions import CanvasFunctions
        from .image_drop import ImageDropLabel
        self.functions=CanvasFunctions(self); self.drop_position=None; self.drop_loader=ImageDropLabel(window.store)
        self.drop_loader.setParent(self); self.drop_loader.hide()
        self.drop_loader.filesReady.connect(self.import_images); self.drop_loader.failed.connect(lambda message:import_error(window,message))
        self.drop_target=None

    def palette(self,position=None,target=None):
        previous=(self.root_id,list(self.path)); self.root_id=None; self.path=[]
        if target is not None: self.enter(target)
        self.insertion_key=target
        if target in self.cards: self.cards[target].setSelected(True); self.cards[target].update()
        try: CanvasPalette(self,position if isinstance(position,QPointF) else None).exec()
        finally:
            self.insertion_key=None; self.root_id,self.path=previous
            if target in self.cards: self.cards[target].update()

    def update_output(self):
        if self.output is not None: self.output.update_text()
        if self.preview_card is not None: self.preview_card.update_text()
        self.functions.layout()

    def showEvent(self,event):
        super().showEvent(event)
        QTimer.singleShot(0,self.present)

    def present(self):
        if not self.isVisible() or self.window.closing: return
        # Startup may select this page before the outer window is shown.
        # Populate only after Qt has assigned the full viewport geometry.
        if self.parentWidget() and self.parentWidget().layout(): self.parentWidget().layout().activate()
        self.layout().activate(); self.refresh(); self.restore_view(); self.view.viewport().update()

    def restore_view(self):
        saved=self.window.state.get('canvas_view')
        if saved:
            self.view.resetTransform();self.view.scale(saved[0],saved[0]);self.view.centerOn(saved[1],saved[2])
        else:self.fit()

    def history_state(self):
        return {k: copy.deepcopy(self.window.state[k]) for k in HISTORY_FIELDS if k in self.window.state}

    def commit(self, operation):
        before = self.history_state(); value = copy.deepcopy(self.window.state)
        try:
            operation(value); comp.prune_instances(value)
            if value.get('uses'): value.setdefault('prompt_layout','paragraphs')
            value['output_order']=output_groups(value,include_hidden=True); validate_state(value)
            if value == self.window.state: return
            if value['version'] > self.window.state['version']:
                self.window.store.save(self.window.state)
                self.window.store.backup()
            self.window.state = value
            after = self.history_state(); self.undo_stack.append((before, after)); self.undo_stack = self.undo_stack[-30:]
            self.redo_stack.clear(); self.last_state = after
            from .changes import layout_only
            self.sync('layout' if layout_only(before,after) else 'prompt')
            return True
        except (ValueError, KeyError, StopIteration) as exc:
            self.window.notice(str(exc))
            return False

    def sync(self,scope='prompt'):
        if scope=='layout':self.refresh_layout(); self.window.changed('layout',refresh=False); return
        self.window.generation_panel.refresh()
        self.window.refresh_modules(); self.window.refresh_library(); self.window.refresh_builder(); self.window.changed('prompt',refresh=False)

    def refresh_layout(self):
        """Apply only saved geometry; do not rebuild text or alter output resolution."""
        state=self.window.state
        for item in getattr(self,'containers',{}).values():item.refresh()
        cards=list(self.cards.values())+list(self.functions.cards.values())+list(getattr(self,'outputs',{}).values())+list(getattr(self,'clips',{}).values())
        cards.extend(c for c in (self.output,self.preview_card,getattr(self,'generator',None),getattr(self,'order_card',None)) if c is not None)
        for card in set(cards):
            key=card.key; card.restore_size(); card.layout_card()
            position=state.get('text_positions',{}).get(key,getattr(self,'layout_defaults',{}).get(key))
            if card.parentItem() is None and position is not None:card.setPos(*position)
        if hasattr(self,'ensure_bounds'):self.ensure_bounds()
        self.functions.layout()
        if hasattr(self,'update_lines'):self.update_lines()
        self.view.scene().setSceneRect(self.content_bounds().adjusted(-2400,-1800,2400,1800))

    def remember_layout_defaults(self):
        # Older states omit positions until the first drag. Undo must restore that
        # original placement without rebuilding or recompiling the Prompt.
        if not hasattr(self,'layout_defaults'):self.layout_defaults={}
        for card in self.view.scene().items():
            if hasattr(card,'key') and card.parentItem() is None and card.key not in self.window.state.get('text_positions',{}):
                self.layout_defaults[card.key]=[card.x(),card.y()]

    def restore_history(self, source, destination, after):
        from .snapshot_history import restore as restore_import
        if restore_import(self,source,destination,after):return
        if not source: return
        before, later = source[-1]
        expected, target = (before, later) if after else (later, before)
        if self.history_state() != expected:
            source.clear(); self.window.notice('組合已在其他位置更新；已清除過期的 Canvas 復原紀錄。'); self.refresh(); return
        state = copy.deepcopy(self.window.state)
        for key in HISTORY_FIELDS: state.pop(key, None)
        state.update(copy.deepcopy(target)); validate_state(state)
        source.pop(); destination.append((before, later)); self.window.state = state
        self.last_state = self.history_state()
        from .changes import layout_only
        self.sync('layout' if layout_only(expected,target) else 'prompt')

    def undo(self): self.restore_history(self.undo_stack, self.redo_stack, False)
    def redo(self): self.restore_history(self.redo_stack, self.undo_stack, True)

    def current(self, state=None, mutable=False):
        state = self.window.state if state is None else state
        if self.root_id is None: return None
        root = comp.usage_root(state, self.root_id, mutable)
        if root is None: return None
        current = comp.find(root, self.path[-1]) if self.path else root
        return current

    def refresh(self):
        if 'canvas_functions' not in self.window.state:
            data=self.functions.data(); source=self.window.state.get('generation',{}).get('source')
            if source: data['images']['__source_'+uid()]=dict(source=copy.deepcopy(source),attached=True)
        now=self.history_state()
        if self.last_state is not None and now!=self.last_state:
            self.undo_stack.clear(); self.redo_stack.clear()
        self.last_state=now
        if self.root_id is not None and self.current() is None: self.root_id=None; self.path=[]
        selected_keys={v.key for v in self.view.scene().selectedItems() if isinstance(v,NodeCard)}
        self.entries={}; entries=[]; state=self.window.state
        modules={m['id']:m for m in state['modules']}; items={i['id']:i for i in state['items']}
        for mid in output_groups(state):
            if mid==TEMPORARY_GROUP:
                for index,text in enumerate(state['temporary']):
                    value=comp.node('臨時片段',text); key=f'temporary:{index}'
                    entries.append((key,value,'臨時片段',False,None)); self.entries[key]=(None,value['id'])
                continue
            for ident in ([mid] if mid in state.get('uses',{}) else state['selections'].get(mid,[])):
                value=copy.deepcopy(comp.usage_root(state,ident))
                if ident not in state.get('uses',{}): value['weight']=state.get('weights',{}).get(ident,10)
                self.entries[ident]=(ident,value['id'])
                for child in comp.walk(value):
                    if child is not value: self.entries[ident+':'+child['id']]=(ident,child['id'])
                title=f'{len(entries)+1} · 模組'
                entries.append((ident,value,title,not value['enabled'],ident))
        if not self.isVisible(): return
        for card in list(self.cards.values()):
            if card.parentItem() is None:
                self.view.scene().removeItem(card); card.deleteLater()
        self.cards={}
        if self.output is None:
            self.output=TextCard(self); self.view.scene().addItem(self.output)
        self.output.attach(); self.update_output()
        if self.preview_card is None:
            self.preview_card=PreviewCard(self); self.view.scene().addItem(self.preview_card)
        self.preview_card.attach(); self.results.refresh()
        self.output.setPos(*state.get('text_positions',{}).get('__text_output__',[-self.output.width/2,-self.output.height/2]))
        self.preview_card.setPos(*state.get('text_positions',{}).get('__result_preview__',[self.output.x()+self.output.width+64,self.output.y()]))
        self.functions.refresh()
        bounds=self.output.sceneBoundingRect(); left_y=bounds.top()
        left_boundary=min([bounds.left()]+[card.x() for key,card in self.functions.cards.items() if self.functions.data()['images'][key].get('attached')])
        for index,(key,value,subtitle,muted,ident) in enumerate(entries):
            card=NodeCard(self,key,value,subtitle,muted); self.view.scene().addItem(card)
            default=[left_boundary-card.width-48,left_y]; left_y+=card.height+28
            card.setPos(*state.get('text_positions',{}).get(key,default))
        for key,card in self.cards.items(): card.setSelected(key in selected_keys)
        if '__result_preview__' not in state.get('text_positions',{}):
            roots=[card for card in self.cards.values() if card.parentItem() is None]
            for _ in range(len(roots)):
                hits=[card.sceneBoundingRect() for card in roots if card.sceneBoundingRect().intersects(self.preview_card.sceneBoundingRect())]
                if not hits: break
                self.preview_card.setX(max(rect.right() for rect in hits)+48)
        self.functions.layout()
        bounds=self.content_bounds()
        self.view.scene().setSceneRect(bounds.adjusted(-2400,-1800,2400,1800))
        self.remember_layout_defaults()

    def release_output(self):
        if self.output is not None: self.output.detach()

    def enter(self,key):
        # Kept as an insertion target for callers; the scene always shows every root.
        if key not in self.entries: return
        ident,node_id=self.entries[key]
        if ident is None: return
        self.root_id=ident
        root=comp.usage_root(self.window.state,ident)
        self.path=[] if root['id']==node_id else [node_id]

    def focus_node(self,key):
        if not self.isVisible(): self.window.enter_canvas()
        self.view.scene().clearSelection()
        card=self.cards.get(key)
        if card:
            card.setSelected(True)
            QTimer.singleShot(0,lambda:self.view.centerOn(self.cards[key]) if key in self.cards else None)

    def open_root(self,ident): self.focus_node(ident)

    def back(self): self.home()

    def fit(self):
        self.view.resetTransform()
        bounds=self.content_bounds()
        center=bounds.center()
        width=2*max(abs(bounds.left()-center.x()),abs(bounds.right()-center.x()))+50
        height=2*max(abs(bounds.top()-center.y()),abs(bounds.bottom()-center.y()))+50
        scale=min(1,self.view.viewport().width()/max(1,width),self.view.viewport().height()/max(1,height))
        self.view.scale(max(.3,scale),max(.3,scale)); self.view.centerOn(center)

    def content_bounds(self):
        rect=QRectF()
        for item in self.view.scene().items():
            if item.parentItem() is None and item.isVisible(): rect=rect.united(item.sceneBoundingRect())
        return rect

    def zoom(self, factor):
        scale = self.view.transform().m11()
        if .3 <= scale*factor <= 2.5: self.view.scale(factor, factor)

    def move_cards(self, positions):
        self.commit(lambda s: s.setdefault('text_positions', {}).update(positions))

    def import_images(self,paths):
        position=self.drop_position or self.view.mapToScene(self.view.viewport().rect().center())
        for index,path in enumerate(paths):
            if str(path).lower().endswith('.json'):
                self.window.generation_panel.import_workflow(path); continue
            attached=bool(self.output and self.output.sceneBoundingRect().contains(position) and index==0)
            self.functions.add_image(path=path,position=position+QPointF(index*40,index*40),attached=attached)
        self.window.generation_panel.changed()

    def clear_drop_preview(self):
        target=self.drop_target; self.drop_target=None
        if target is not None and target.key in self.cards:
            target.drop_size=None; target.layout_card()

    def preview_node_drop(self,card,position):
        candidates=[]
        for candidate in self.cards.values():
            if candidate is card or candidate.isAncestorOf(card) or card.isAncestorOf(candidate): continue
            if self.entries[candidate.key][0] is None: continue
            if candidate.sceneBoundingRect().contains(position): candidates.append(candidate)
        target=min(candidates,key=lambda c:c.width*c.height) if candidates else None
        if target is self.drop_target: return
        self.clear_drop_preview()
        if target:
            self.drop_target=target; target.drop_size=(card.width,card.height); target.layout_card()

    def finish_node_drop(self,card,position=None):
        target=self.drop_target; target_key=target.key if target else None; self.clear_drop_preview()
        if target_key: self.nest(card.key,target_key); return True
        parent=card.parentItem()
        if parent is not None and position is not None and not parent.sceneBoundingRect().contains(position):
            key=card.key; placed=QPointF(card.scenePos())
            QTimer.singleShot(0,lambda:self.extract_node(key,placed)); return True
        return False

    def extract_node(self,key,position):
        added=[]
        def apply(state):
            ident,root,part=self.selected_node(state,key)
            if part is root: return
            order=output_groups(state,include_hidden=True)
            owner=ident if ident in state.get('uses',{}) else next(i['module'] for i in state['items'] if i['id']==ident)
            comp.container(root,part['id']).remove(part)
            new_id=self.add_root(state,part); added.append(new_id)
            order.insert(order.index(owner)+1,new_id); state['output_order']=order
            sizes=state.setdefault('text_sizes',{})
            for node in comp.walk(part):
                old_key=ident+':'+node['id']; new_key=new_id if node is part else new_id+':'+node['id']
                if old_key in sizes: sizes[new_key]=sizes.pop(old_key)
            state.setdefault('text_positions',{})[new_id]=[position.x(),position.y()]
        success=self.commit(apply)
        if success and added:
            self.view.scene().clearSelection()
            if added[0] in self.cards: self.cards[added[0]].setSelected(True)
        return success

    def nest(self,source,target):
        def apply(state):
            source_id,root,part=self.selected_node(state,source)
            target_id,target_root,parent=self.selected_node(state,target)
            if source==target or (source_id==target_id and comp.find(part,parent['id']) is not None):
                raise ValueError('不能放入自己或自己的子元素。')
            moved=comp.clone_node(part)
            if root is part and source_id not in state.get('uses',{}): moved['weight']=state.get('weights',{}).get(source_id,10)
            if not moved['children'] and not moved['overlays'] and moved['weight']==10: moved['grouped']=False
            for old,new in zip(comp.walk(part),comp.walk(moved)):
                old_key=source_id if old is root else source_id+':'+old['id']
                if old_key in state.get('text_sizes',{}): state['text_sizes'][target_id+':'+new['id']]=state['text_sizes'][old_key]
            if root is part: comp.remove_usage(state,source_id)
            else: comp.container(root,part['id']).remove(part)
            parent['grouped']=True; target_root['grouped']=True; parent['children'].append(moved)
        return self.commit(apply)

    def add_root(self, state, root, module_id=None):
        root = copy.deepcopy(root); root['grouped'] = True
        if module_id: root.setdefault('source_module', module_id)
        ident = uid(); state['version'] = max(3,state['version'])
        state.setdefault('prompt_layout','paragraphs')
        state.setdefault('uses', {})[ident] = root
        return ident

    def change_weight(self, key, delta=None, value=None):
        def apply(state):
            ident, root, part = self.selected_node(state,key)
            legacy = root is part and ident not in state.get('uses',{})
            weight = state.get('weights',{}).get(ident,10) if legacy else part['weight']
            weight = max(0,min(1000, value if value is not None else weight+delta))
            if legacy: state.setdefault('weights',{})[ident] = weight
            else: part['weight'] = weight
        self.commit(apply)

    def weight_dialog(self, key):
        ident, root, part = self.selected_node(copy.deepcopy(self.window.state),key)
        weight = self.window.state.get('weights',{}).get(ident,10) if root is part and ident not in self.window.state.get('uses',{}) else part['weight']
        dialog=StudioDialog(self.window); dialog.setWindowTitle('調整權重'); dialog.resize(380,220)
        field=QDoubleSpinBox(); field.setRange(0,100); field.setDecimals(1); field.setSingleStep(.1); field.setValue(weight/10)
        dialog.body.addWidget(label(part['name'],'Heading')); dialog.body.addWidget(field)
        dialog.body.addWidget(dialog_buttons(dialog,dialog.accept)); field.setFocus(); field.selectAll()
        if dialog.exec()==QDialog.DialogCode.Accepted: self.change_weight(key,value=round(field.value()*10))

    def resize_card(self,key,width,height,position=None):
        def apply(s):
            s.setdefault('text_sizes',{})[key]=[min(10000,width),min(10000,height)]
            if position is not None: s.setdefault('text_positions',{})[key]=position
        self.commit(apply)

    def size_dialog(self,key):
        card = getattr(self,'outputs',{}).get(key) or getattr(self,'clips',{}).get(key) or (getattr(self,'order_card',None) if key=='__workflow_order__' else self.output if key=='__text_output__' else self.preview_card if key=='__result_preview__' else self.functions.cards.get(key) or self.cards.get(key))
        if card is None: return
        dialog = StudioDialog(self.window); dialog.setWindowTitle('調整模組尺寸')
        form=QFormLayout(); dialog.body.addLayout(form); fields=[]
        for title,current in [('寬度',card.width),('高度',card.height)]:
            field=QDoubleSpinBox(); field.setDecimals(0); field.setRange(100,10000); field.setValue(current)
            form.addRow(title,field); fields.append(field)
        dialog.body.addWidget(label('尺寸至少容納模組內容；也可拖曳四角調整。','Subtle',True))
        dialog.body.addWidget(dialog_buttons(dialog,dialog.accept))
        if dialog.exec()==QDialog.DialogCode.Accepted: self.resize_card(key,*(f.value() for f in fields))

    def reset_size(self,key):
        self.commit(lambda s:s.get('text_sizes',{}).pop(key,None))

    def append_value(self, value, position=None):
        added=[]; parent=self.current(); parent_name=parent['name'] if parent else None
        def apply(state):
            current = self.current(state, mutable=True)
            if current is None: key = self.add_root(state,value)
            else:
                # Adding a child promotes even a previously plain Tag to a module.
                comp.usage_root(state,self.root_id,True)['grouped'] = True
                if self.path: current['grouped'] = True
                current['children'].append(copy.deepcopy(value)); key=self.root_id+':'+value['id']
            if isinstance(position,QPointF): state.setdefault('text_positions',{})[key]=[position.x(),position.y()]
            added.append(key)
        success=self.commit(apply)
        if success:
            self.view.scene().clearSelection()
            if added[0] in self.cards: self.cards[added[0]].setSelected(True)
            self.window.notice('已加入「'+value['name']+'」'+('，位於「'+parent_name+'」內。' if parent_name else '。')+('手動稿保留，清除內容後套用。' if self.window.state['draft'] is not None else '最終 Prompt 已更新。'))
        return success

    def add_tag(self, text, position=None):
        if not text.strip(): return False
        return self.append_value(comp.node(text.strip(),text),position)

    def add_new(self, position=None):
        dialog = NodeDialog(self.window)
        if self.root_id is None:
            dialog.description.setText('建立當次使用的模組；需要重複使用時，可另存到素材庫。')
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        value = comp.node(dialog.name.text().strip()); value.update(dialog.values())
        value['grouped'] = True
        self.append_value(value,position)

    def add_asset(self):
        dialog = AssetDialog(self.window)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        self.insert_asset(copy.deepcopy(dialog.asset()))

    def insert_asset(self,asset,position=None):
        value = comp.clone_node(comp.prototype(asset))
        value.update(source_id=asset['id'],source_module=asset['module'])
        self.append_value(value,position)

    def selected_node(self, state, key):
        ident, node_id = self.entries[key]
        if ident is None: raise ValueError('請使用臨時片段的右鍵選單或中央目前組合。')
        root = comp.usage_root(state, ident, True); part = comp.find(root, node_id)
        if part is None: raise ValueError('組合已更新，請重新選取。')
        return ident, root, part

    def edit(self, key):
        ident, node_id = self.entries[key]
        if ident is None: return
        root = comp.usage_root(self.window.state, ident)
        value = copy.deepcopy(comp.find(root, node_id))
        is_root = node_id == root['id'] and ident not in self.window.state.get('uses',{})
        if is_root: value['weight'] = self.window.state.get('weights', {}).get(ident, 10)
        dialog = NodeDialog(self.window, value, '編輯當次模組')
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        def apply(state):
            item_id, root, part = self.selected_node(state, key)
            changes = dialog.values()
            if is_root: state.setdefault('weights', {})[item_id] = changes.pop('weight')
            part.update(changes)
        self.commit(apply)

    def override(self, key):
        dialog = AssetDialog(self.window)
        if dialog.exec() != QDialog.DialogCode.Accepted: return
        value = comp.clone_node(comp.prototype(dialog.asset()))
        self.commit(lambda s: self.selected_node(s, key)[2]['overlays'].append(value))
        self.focus_node(key)

    def toggle(self, key):
        def apply(state):
            part = self.selected_node(state, key)[2]; part['enabled'] = not part['enabled']
        self.commit(apply)

    def remove(self, key):
        def apply(state):
            ident, root, part = self.selected_node(state, key)
            if root is part:
                comp.remove_usage(state,ident)
            else: comp.container(root, part['id']).remove(part)
        self.commit(apply)

    def delete_selected(self):
        keys=[i.key for i in self.view.scene().selectedItems() if isinstance(i,NodeCard)]
        function_keys=[i.key for i in self.view.scene().selectedItems() if isinstance(i,TextCard) and i.key!='__text_output__']
        self.remove_keys(keys)
        for key in function_keys: self.functions.remove(key)

    def remove_keys(self,keys):
        if not keys: return
        # Record the targets first; deleting a parent already removes its descendants.
        targets=[self.entries[k] for k in keys if k in self.entries and self.entries[k][0] is not None]
        temporary=sorted({int(k.split(':')[1]) for k in keys if k.startswith('temporary:')},reverse=True)
        def apply(state):
            by_root={}
            for ident,node_id in targets: by_root.setdefault(ident,set()).add(node_id)
            for ident,ids in by_root.items():
                root=comp.usage_root(state,ident,True)
                if root['id'] in ids:
                    comp.remove_usage(state,ident)
                    continue
                def prune(parent):
                    for field in ('children','overlays'):
                        parent[field]=[n for n in parent[field] if n['id'] not in ids]
                        for child in parent[field]: prune(child)
                prune(root)
            for index in temporary:
                if index<len(state['temporary']): state['temporary'].pop(index)
        self.commit(apply)

    def reorder(self, source, target, after=False):
        def apply(state):
            ident, root, part = self.selected_node(state, source)
            other_id, other_root, other = self.selected_node(state, target)
            if ident != other_id: raise ValueError('子項只能在同一個群組內排序。')
            values = comp.container(root, part['id'])
            if other not in values: raise ValueError('子項只能在同一個群組內排序。')
            values.remove(part); values.insert(values.index(other)+int(after), part)
        self.commit(apply)

    def group(self):
        cards=[item for item in self.view.scene().selectedItems() if isinstance(item,NodeCard)]
        keys = [item.key for item in cards]
        if len(keys) < 2: self.window.notice('請按 Ctrl 或拖曳框選至少兩個同層模組。'); return
        if len({card.parentItem() for card in cards})!=1:
            self.window.notice('請框選同一個模組內的元素，或同為最外層的模組。'); return
        name, ok = InputDialog.getText(self.window, '建立複合模組', '群組名稱')
        if not ok or not name.strip(): return
        previous=(self.root_id,list(self.path)); self.root_id=None; self.path=[]
        if cards[0].parentItem() is not None: self.enter(cards[0].parentItem().key)
        def apply(state):
            if self.root_id is None:
                ids = [self.entries[k][0] for k in keys]
                if None in ids: raise ValueError('臨時片段請先轉成模組。')
                order=output_groups(state,include_hidden=True)
                ordered=[ident for mid in order for ident in ([mid] if mid in state.get('uses',{}) else state['selections'].get(mid,[])) if ident in ids]
                children=[]
                for ident in ordered:
                    child=comp.clone_node(comp.usage_root(state,ident))
                    if ident not in state.get('uses',{}):
                        asset=next(i for i in state['items'] if i['id']==ident)
                        child.update(source_id=ident,source_module=asset['module'])
                        weight=state.get('weights',{}).get(ident,10)
                        if weight!=10: child=comp.node(child['name'],children=[child]); child['weight']=weight
                    if not child['children']: child['grouped']=False
                    children.append(child)
                affected={ident if ident in state.get('uses',{}) else next(i['module'] for i in state['items'] if i['id']==ident) for ident in ids}
                at=min(order.index(mid) for mid in affected)
                for ident in ids: comp.remove_usage(state,ident)
                new_id=self.add_root(state,comp.node(name.strip(),children=children))
                order=[mid for mid in order if mid in output_groups(state,include_hidden=True)]
                order.insert(min(at,len(order)),new_id); state['output_order']=order
            else:
                current = self.current(state, mutable=True); selected_ids = {self.entries[k][1] for k in keys}
                comp.usage_root(state,self.root_id,True)['grouped']=True
                children = [n for n in current['children'] if n['id'] in selected_ids]
                if len(children) != len(keys): raise ValueError('覆蓋層請分別操作；只能組合一般子項。')
                at = min(current['children'].index(n) for n in children)
                current['children'] = [n for n in current['children'] if n not in children]
                current['children'].insert(at, comp.node(name.strip(), children=children,grouped=True))
        try: self.commit(apply)
        finally: self.root_id,self.path=previous

    def detach(self, key):
        def apply(state):
            ident, root, part = self.selected_node(state, key)
            if root is part:
                if state.get('weights', {}).get(ident, 10) != 10: raise ValueError('拆解前請將整組權重設為 1.0。')
                wrapper = comp.node('拆解', children=[root]); comp.detach(wrapper, part['id'])
                order=output_groups(state,include_hidden=True)
                mid=ident if ident in state.get('uses',{}) else next(i['module'] for i in state['items'] if i['id']==ident)
                at=order.index(mid); comp.remove_usage(state,ident)
                children=[self.add_root(state,comp.clone_node(n)) for n in wrapper['children']]
                order=[key for key in order if key in output_groups(state,include_hidden=True)]
                order[at:at]=children; state['output_order']=order
            else: comp.detach(root, part['id'])
        self.commit(apply)

    def save_prototype(self, key):
        ident,node_id=self.entries[key]
        if ident is None: return
        value=comp.clone_node(comp.find(comp.usage_root(self.window.state,ident),node_id))
        modules=self.window.state['modules']
        if not modules: self.window.notice('請先在素材庫新增分類。'); return
        names=[m['name'] for m in modules]; source=value.get('source_module')
        current=next((i for i,m in enumerate(modules) if m['id']==source),0)
        category,ok=InputDialog.getItem(self.window,'另存為素材','存入分類',names,current,False)
        if not ok: return
        mid=modules[names.index(category)]['id']
        name,ok=InputDialog.getText(self.window,'另存為複合素材','素材名稱',text=value['name'])
        if not ok or not name.strip(): return
        value['name']=name.strip()
        def apply(state):
            state['version']=max(2,state['version'])
            state['items'].append(dict(id=uid(),module=mid,name=value['name'],prompt=comp.render(value),aliases=[],notes='',excludes=[],composition=value))
        self.commit(apply)

    def context(self, key, position, selected_keys=None):
        if key not in self.entries: return
        if self.entries[key][0] is None:
            index=int(key.split(':')[1]); menu=RoundMenu(self.window)
            menu.addAction('複製片段',lambda:self.window.copy_text(self.window.state['temporary'][index]))
            menu.addAction('存入素材庫',lambda:self.window.new_item(self.window.state['temporary'][index]))
            menu.addAction('移除此片段',lambda:self.remove_keys([key])); menu.open_at(position); return
        menu = RoundMenu(self.window)
        menu.addAction('編輯當次內容…', lambda: self.edit(key))
        menu.addAction('調整權重…', lambda: self.weight_dialog(key))
        menu.addAction('調整模組尺寸…', lambda: self.size_dialog(key))
        menu.addAction('恢復自動尺寸', lambda: self.reset_size(key))
        menu.addAction('加入子元素…', lambda: self.palette(target=key))
        menu.addAction('使用素材覆蓋…', lambda: self.override(key))
        menu.addAction('啟用／停用', lambda: self.toggle(key)); menu.addSeparator()
        menu.addAction('將選取項目組合', self.group)
        menu.addAction('拆解群組', lambda: self.detach(key))
        menu.addAction('另存為複合素材…', lambda: self.save_prototype(key)); menu.addSeparator()
        menu.addAction('移除當次組合', lambda: self.remove(key))
        menu.addAction('刪除選取項目 · Del / Backspace',self.delete_selected if selected_keys is None else lambda:self.remove_keys(selected_keys))
        menu.open_at(position)

    def tools_menu(self,position=None):
        menu = RoundMenu(self.window)
        menu.addAction('加入 Tag 與素材',self.palette)
        scene_position=self.view.mapToScene(self.view.viewport().mapFromGlobal(position)) if position is not None else None
        menu.addMenu(self.functions.menu(menu,scene_position))
        menu.addAction('新增模組',self.add_new)
        menu.addSeparator()
        menu.addAction('將選取項目組合', self.group)
        menu.addAction('刪除選取項目 · Del / Backspace',self.delete_selected)
        menu.addAction('復原',self.undo).setEnabled(bool(self.undo_stack))
        menu.addAction('重做',self.redo).setEnabled(bool(self.redo_stack))
        menu.addAction('顯示全部模組與輸出',self.fit)
        menu.addAction('調整最終 Prompt 欄尺寸…',lambda:self.size_dialog('__text_output__'))
        menu.addAction('恢復最終 Prompt 欄自動尺寸',lambda:self.reset_size('__text_output__'))
        menu.addAction('調整圖片預覽尺寸…',lambda:self.size_dialog('__result_preview__'))
        menu.addSeparator()
        menu.addAction('複製完整 Prompt',lambda:self.window.copy_text(self.window.final.toPlainText()))
        menu.addAction('清除手動稿，恢復組合',self.window.regenerate).setEnabled(self.window.state['draft'] is not None)
        menu.addAction('運行 ComfyUI',self.window.copy_final).setEnabled(hasattr(self.window,'comfy') and self.window.comfy.can_run)
        menu.open_at(position if position is not None else self.view.viewport().mapToGlobal(self.view.viewport().rect().center()))

    def home(self):
        self.root_id = None; self.path = []; self.refresh(); self.fit()
