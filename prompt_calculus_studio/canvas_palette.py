"""Choose a local text module or create a blank Canvas copy."""
import copy
from PySide6.QtCore import Qt, QPoint, QPointF, QSize, QRect, QEvent, QSignalBlocker
from PySide6.QtGui import QPainter, QColor
from PySide6.QtWidgets import (QDialog, QFrame, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QSizeGrip, QSizePolicy, QApplication, QPushButton, QLineEdit)
from . import materials, material_navigation as navigation
from .material_folder_navigation import FolderNavigation
from .widgets import label, button, ComboBox
from .quiet_splitter import QuietSplitter
from .palette_cards import PickerCardDelegate, ClosingClickGuard
from .ui_icons import icon
from .module_chrome import dialog_colors, module_dialog_styles
from .stacked_card_list import StackedCardList


class PaletteGrip(QSizeGrip):
    """Resize the centered sheet, leaving the modal backdrop over the window."""
    def __init__(self, palette):
        super().__init__(palette.surface); self.palette=palette; self.start=None
        self.setToolTip('調整面板大小'); self.setAccessibleName('調整面板大小')

    def mousePressEvent(self,event):
        if event.button()==Qt.MouseButton.LeftButton:
            self.start=(event.globalPosition().toPoint(),self.palette.surface.size()); event.accept()

    def mouseMoveEvent(self,event):
        if self.start is not None:
            delta=event.globalPosition().toPoint()-self.start[0]
            self.palette.sheet_size=QSize(self.start[1].width()+2*delta.x(),self.start[1].height()+2*delta.y())
            self.palette.place_sheet(); event.accept()

    def mouseReleaseEvent(self,event):
        self.start=None; self.palette.sheet_size=self.palette.surface.size(); self.palette.save_layout(); event.accept()


class CanvasPalette(QDialog):
    UNCATEGORIZED = '@uncategorized'

    def __init__(self,canvas,position,destination_id=None,choose_destination=False):
        super().__init__(canvas.window)
        self.canvas=canvas; self.position=QPointF(position) if isinstance(position,QPointF) else None
        self.inserted=False; self.closed=False; self.busy=False
        self.last_mouse_position=None
        self.destination_id=(destination_id if destination_id is not None else
                             getattr(canvas,'insertion_canvas',None) or getattr(getattr(canvas,'editor',None),'key',None))
        self.choose_destination=bool(choose_destination and self.destination_id is None)
        self.layout_owner=(canvas.window.store,canvas.window.state['workspace'])
        self.setWindowTitle('加入素材')
        self.setWindowFlags(Qt.WindowType.Dialog|Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.surface=QFrame(self); self.surface.setObjectName('DialogSurface')
        from .canvas_items import canvas_colors
        from .theme import font_pixels
        colors=canvas_colors(canvas);self.colors=dialog_colors(colors)
        self._module_dialog_context=(colors,max(16,font_pixels(canvas.window.state['settings']['ui_size'])))
        self.setStyleSheet(module_dialog_styles(*self._module_dialog_context))
        self.body=QVBoxLayout(self.surface); self.body.setContentsMargins(24,24,24,20); self.body.setSpacing(12)
        header=QHBoxLayout(); header.setSpacing(12)
        self.title=label('加入素材','DialogTitle',True); header.addWidget(self.title,1)
        self.close_button=button('',self.reject,'Quiet'); self.close_button.setIcon(icon('close'))
        self.close_button.setAccessibleName('關閉文字挑選'); self.close_button.setToolTip('關閉')
        header.addWidget(self.close_button,0,Qt.AlignmentFlag.AlignTop); self.body.addLayout(header)
        self.destination_label=label('','Subtle',True); self.body.addWidget(self.destination_label)
        self.destination=ComboBox(); self.destination.setAccessibleName('目的畫布')
        self.destination.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.destination.addItem('選擇目的畫布',None)
        for ident,value in self.canvases().items(): self.destination.addItem(value['name'],ident)
        self.destination.setCurrentIndex(max(0,self.destination.findData(self.destination_id)))
        self.body.addWidget(self.destination); self.destination.setVisible(self.choose_destination)
        self.destination.currentIndexChanged.connect(self.destination_changed)

        # Only the two lists scroll. The sole footer action stays visible.
        self.content=QWidget(); self.content.setMinimumWidth(0)
        content=QVBoxLayout(self.content)
        content.setContentsMargins(0,8,0,0); content.setSpacing(12)
        self.body.addWidget(self.content,1)
        self.narrow_categories=QWidget(); catrow=QHBoxLayout(self.narrow_categories); catrow.setContentsMargins(0,0,0,0)
        self.category=ComboBox(); self.category.setAccessibleName('內容分類')
        self.category.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        catrow.addWidget(self.category,1)
        content.addWidget(self.narrow_categories)
        self.split=QuietSplitter(); self.split.setChildrenCollapsible(False); content.addWidget(self.split,1)
        self.category_pane=QWidget(); cats=QVBoxLayout(self.category_pane); cats.setContentsMargins(0,0,12,0); cats.setSpacing(8)
        self.modules=FolderNavigation(manage=False,colors=lambda:self.colors)
        self.modules.setProperty('moduleChoices',True); self.modules.setSpacing(3)
        self.modules.toggleRequested.connect(self.toggle_folder)
        self.modules.setAccessibleName('內容分類'); cats.addWidget(self.modules,1)
        self.split.addWidget(self.category_pane)
        local=QWidget(); local.setMinimumWidth(0); rows=QVBoxLayout(local); rows.setContentsMargins(12,0,0,0); rows.setSpacing(8)
        self.assets_header=QWidget(); self.assets_header_layout=QVBoxLayout(self.assets_header)
        self.assets_header_layout.setContentsMargins(0,0,0,0); self.assets_header_layout.setSpacing(8)
        self.assets_heading=label('本地素材','Heading'); self.assets_header_layout.addWidget(self.assets_heading)
        self.search=QLineEdit(); self.search.setAccessibleName('搜尋目前分類的素材')
        self.search.setPlaceholderText('搜尋名稱、別名或內容'); self.search.setClearButtonEnabled(True)
        self.assets_header_layout.addWidget(self.search); rows.addWidget(self.assets_header)
        self.assets=StackedCardList(lambda:canvas.window.state['settings'],lambda:self.colors)
        self.assets.setAccessibleName('本地素材'); self.assets.setSpacing(3)
        self.assets.setStyleSheet('QListWidget {background:transparent;border:0;padding:0;}')
        self.assets.setItemDelegate(PickerCardDelegate(self.assets)); self.assets.setMouseTracking(True)
        self.assets.setUniformItemSizes(False); rows.addWidget(self.assets,1)
        self.empty=label('','Subtle',True); rows.addWidget(self.empty)
        self.empty_spacer=QWidget(); self.empty_spacer.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Expanding)
        rows.addWidget(self.empty_spacer,1)
        self.split.addWidget(local)
        self.status=label('',None,True); self.status.hide(); self.body.addWidget(self.status)
        self.footer=QHBoxLayout(); self.footer.setSpacing(10)
        self.new_button=button('新增空白模組',self.new_module); self.new_button.setIcon(icon('plus'))
        self.footer.addWidget(self.new_button); self.footer.addStretch()
        self.body.addLayout(self.footer)
        for listing in (self.modules,self.assets):
            listing.setMinimumSize(0,0); listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            listing.setTextElideMode(Qt.TextElideMode.ElideRight); listing.installEventFilter(self)
        self.assets.viewport().installEventFilter(self)
        self.grip=PaletteGrip(self)
        saved=canvas.window.state['settings'].get('canvas_palette',{})
        self.sheet_size=QSize(*saved.get('size',[900,660]))
        columns=saved.get('columns',[200,550,0]); self.split_sizes=[columns[0],sum(columns[1:])]
        self.split.splitterMoved.connect(self.save_layout)
        self.populate_categories()
        self.modules.currentRowChanged.connect(self.category_changed)
        self.category.currentIndexChanged.connect(self.combo_category_changed)
        # Both Qt activation paths can fire for the same click. The operation guard
        # closes only after a successful transaction and blocks the second path.
        self.assets.itemClicked.connect(lambda _:self.insert())
        self.search.textChanged.connect(self.refresh)
        self.refresh(); self.update_destination()
        for control in self.findChildren(QPushButton): control.setAutoDefault(False)
        canvas.window.installEventFilter(self)
        QApplication.instance().installEventFilter(self)
        # Establish native geometry and the final backdrop before the first
        # show. Toggling an opacity effect after showing caused an extra flash.
        self.fit_window()

    def canvases(self):
        return self.canvas.window.state.get('multi_output',{}).get('canvases',{})

    def destination_changed(self):
        self.destination_id=self.destination.currentData(); self.update_destination()

    def update_destination(self):
        value=self.canvases().get(self.destination_id)
        if value:
            self.destination_label.clear()
        elif not self.canvases():
            self.destination_label.setText('尚無畫布；請關閉此頁，返回 Canvas 建立畫布。')
        else:
            self.destination_label.setText('請先選擇目的畫布。' if self.choose_destination else '目的畫布已不存在，請返回 Canvas 重新開啟。')
        self.destination_label.setVisible(value is None)
        for control in (self.assets,self.new_button):
            control.setEnabled(value is not None and not self.busy)

    def ready(self):
        if self.closed or self.inserted or self.busy: return False
        if self.layout_owner!=(self.canvas.window.store,self.canvas.window.state['workspace']):
            self.show_error('工作區已切換，請關閉並從目前畫布重新開啟。'); return False
        if self.destination_id not in self.canvases():
            self.update_destination(); self.show_error('請選擇有效的目的畫布後再加入。'); return False
        return True

    def uncategorized_ids(self):
        return {m['id'] for m in self.canvas.window.state['modules'] if m['name']=='未分類'}

    def populate_categories(self):
        selected=(self.get_browser_category() if self.category.count() else
                  getattr(self.canvas.window,'_material_browser_category',None))
        state=self.canvas.window.state
        if selected==self.UNCATEGORIZED:
            selected=next((m['id'] for m in state['modules'] if m['name']=='未分類'),selected)
        blocked=QSignalBlocker(self.modules); combo_blocked=QSignalBlocker(self.category)
        self.modules.clear(); self.category.clear()
        value=navigation.view(state); folders={v['id']:v for v in value['folders']}
        categories={v['id']:v for v in state['modules']}
        collapsed=set(state['settings'].get('material_folder_collapsed',[]))
        self.modules.has_folders=bool(folders)
        def add(name,ident,kind='category',parent=None):
            count=sum(1 for item in state['items'] if self.in_category(item,ident))
            caption=f'{folders[parent]["name"]} / {name}' if parent else name
            self.category.addItem(f'{caption} · {count}',ident)
            if kind=='folder': self.category.setItemIcon(self.category.count()-1,icon('folder',self.colors['secondary']))
            if parent not in collapsed:
                self.modules.add_row(ident,name,count,kind,parent,ident in collapsed)
        add('全部素材',None,'shortcut')
        for ident in value['order']:
            if ident in folders:
                folder=folders[ident]; add(folder['name'],ident,'folder')
                for cid in folder['categories']: add(categories[cid]['name'],cid,parent=ident)
            else: add(categories[ident]['name'],ident)
        unclassified=next((m['id'] for m in state['modules'] if m['name']=='未分類'),self.UNCATEGORIZED)
        add('未分類',unclassified,'shortcut')
        self.category.setCurrentIndex(max(0,self.category.findData(selected)))
        self.restore_selection(self.modules,self.category.currentData())
        del blocked,combo_blocked
        self.canvas.window._material_browser_category=self.get_browser_category()

    def toggle_folder(self,ident):
        window=self.canvas.window
        if self.closed or self.layout_owner!=(window.store,window.state['workspace']): return
        if navigation.folder(window.state,ident) is None: return
        collapsed=set(window.state['settings'].get('material_folder_collapsed',[]))
        if ident in collapsed: collapsed.remove(ident)
        else: collapsed.add(ident)
        window.state['settings']['material_folder_collapsed']=sorted(collapsed)
        # Folding changes navigation visibility, never the active filter or copy target.
        self.populate_categories(); window.changed('settings',refresh=False)

    def in_category(self,item,ident):
        if ident is None: return True
        if navigation.is_folder(ident):
            folder=navigation.folder(self.canvas.window.state,ident)
            return folder is not None and item.get('module') in folder['categories']
        if ident==self.UNCATEGORIZED:
            known={m['id'] for m in self.canvas.window.state['modules']}
            return item.get('module') in self.uncategorized_ids() or item.get('module') not in known
        return item.get('module')==ident

    def category_name(self):
        ident=self.get_browser_category()
        if ident is None: return '全部分類'
        if ident==self.UNCATEGORIZED: return '未分類'
        if navigation.is_folder(ident):
            folder=navigation.folder(self.canvas.window.state,ident)
            return folder['name'] if folder else '目前資料夾'
        return next((m['name'] for m in self.canvas.window.state['modules'] if m['id']==ident),'目前分類')

    def category_changed(self):
        blocked=QSignalBlocker(self.category)
        self.category.setCurrentIndex(self.category.findData(self.selected_id(self.modules)))
        del blocked
        self.canvas.window._material_browser_category=self.get_browser_category()
        self.refresh()

    def get_browser_category(self):
        return self.category.currentData()

    def set_browser_category(self,ident):
        if ident==self.UNCATEGORIZED:
            ident=next((m['id'] for m in self.canvas.window.state['modules'] if m['name']=='未分類'),ident)
        blocked=QSignalBlocker(self.modules); combo_blocked=QSignalBlocker(self.category)
        self.category.setCurrentIndex(max(0,self.category.findData(ident)))
        self.restore_selection(self.modules,self.category.currentData())
        del blocked,combo_blocked
        self.canvas.window._material_browser_category=self.get_browser_category()
        self.refresh()

    def combo_category_changed(self):
        self.set_browser_category(self.category.currentData())

    def show_error(self,text):
        self.status.setText(text); self.status.show()

    @staticmethod
    def entry(text,ident):
        item=QListWidgetItem(text); item.setData(Qt.ItemDataRole.UserRole,ident); return item

    @staticmethod
    def selected_id(listing):
        item=listing.currentItem(); return item.data(Qt.ItemDataRole.UserRole) if item else None

    @staticmethod
    def restore_selection(listing,ident):
        for i in range(listing.count()):
            if listing.item(i).data(Qt.ItemDataRole.UserRole)==ident:
                listing.setCurrentRow(i); return True
        listing.setCurrentRow(-1); return False

    def refresh(self):
        ident=self.selected_id(self.assets); had_focus=self.assets.hasFocus()
        self.assets.clear(); mid=self.get_browser_category()
        query=self.search.text().strip()
        matches={asset['id'] for asset in materials.search(self.canvas.window.state,query)} if query else None
        for asset in self.canvas.window.state['items']:
            if not self.in_category(asset,mid): continue
            if matches is not None and asset['id'] not in matches: continue
            entry=self.entry(asset['name']+'\n'+asset['prompt'].replace('\n',' '),asset['id'])
            entry.setToolTip(asset['name']+'\n'+asset['prompt'])
            self.assets.addItem(entry)
        self.assets_heading.setText(f'{self.category_name()} · {self.assets.count()} 個素材')
        folder=navigation.is_folder(mid)
        self.search.setAccessibleName('搜尋此資料夾的素材' if folder else '搜尋目前分類的素材')
        self.search.setPlaceholderText('搜尋此資料夾的名稱、別名或內容' if folder else '搜尋名稱、別名或內容')
        self.empty.setText('找不到符合搜尋的素材，請修改或清除搜尋。' if query else '此資料夾尚無素材' if folder else '此分類尚無素材')
        self.empty.setVisible(self.assets.count()==0)
        self.empty_spacer.setVisible(self.assets.count()==0)
        self.assets.setVisible(self.assets.count()>0)
        valid=self.restore_selection(self.assets,ident)
        if had_focus and not valid: self.new_button.setFocus()
        self.assets.doItemsLayout(); self.align_search()

    def align_search(self):
        # Use the actual painted card edges, including the list's spacing and
        # current scroll gutter, for both the heading and its search field.
        viewport=self.assets.viewport(); origin=viewport.mapTo(self.assets,QPoint()).x()
        if self.assets.count():
            card=self.assets.visualItemRect(self.assets.item(0))
            left=origin+card.x()+1; right=self.assets.width()-(origin+card.x()+card.width()-2)
        else:
            left=origin+self.assets.spacing()+1
            right=self.assets.width()-origin-viewport.width()+self.assets.spacing()+2
        self.assets_header_layout.setContentsMargins(max(0,left),0,max(0,right),0)

    def focus_list(self,listing,first=False):
        if not listing.count() or not listing.isVisible() or not listing.isEnabled(): return False
        if first or listing.currentRow()<0: listing.setCurrentRow(0)
        listing.setFocus(Qt.FocusReason.OtherFocusReason); listing.scrollToItem(listing.currentItem()); return True

    def navigate(self,source,event):
        key=event.key(); modifiers=event.modifiers()
        if key==Qt.Key.Key_Escape: self.reject(); return True
        if key in (Qt.Key.Key_Tab,Qt.Key.Key_Backtab):
            order=[self.destination,self.category if self.narrow_categories.isVisible() else self.modules,
                   self.search,self.assets,self.new_button,self.close_button]
            index=order.index(source) if source in order else -1
            step=-1 if key==Qt.Key.Key_Backtab or modifiers&Qt.KeyboardModifier.ShiftModifier else 1
            for offset in range(1,len(order)+1):
                target=order[(index+step*offset)%len(order)]
                if not target.isVisible() or not target.isEnabled(): continue
                if isinstance(target,QListWidget):
                    if self.focus_list(target): break
                else: target.setFocus(); break
            return True
        if modifiers!=Qt.KeyboardModifier.NoModifier: return False
        if source is self.search and key in (Qt.Key.Key_Down,Qt.Key.Key_Return,Qt.Key.Key_Enter):
            self.focus_list(self.assets,first=True); return True
        if source is self.modules and key==Qt.Key.Key_Right:
            if navigation.is_folder(self.get_browser_category()) and self.get_browser_category() in self.canvas.window.state['settings'].get('material_folder_collapsed',[]):
                return False  # The folder row handles Right as expand first.
            self.focus_list(self.assets,first=True); return True
        if source is self.assets:
            if key==Qt.Key.Key_Left:
                target=self.category if self.narrow_categories.isVisible() else self.modules
                target.setFocus(); return True
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter): self.insert(); return True
        return False

    def eventFilter(self,watched,event):
        if watched is self.assets.viewport() and event.type()==QEvent.Type.Resize:
            self.align_search()
        if event.type() in (QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonRelease,QEvent.Type.MouseButtonDblClick):
            if isinstance(watched,QWidget) and (watched is self or self.isAncestorOf(watched)):
                self.last_mouse_position=event.globalPosition().toPoint()
        if watched is self.canvas.window and event.type() in (QEvent.Type.Resize,QEvent.Type.Move):
            if self.isVisible(): self.fit_window()
        elif watched in (self.assets,self.modules,self.category,self.destination,self.search,self.new_button,self.close_button) and event.type()==QEvent.Type.KeyPress:
            if self.navigate(watched,event): return True
        return super().eventFilter(watched,event)

    def keyPressEvent(self,event):
        if not self.navigate(self.focusWidget(),event): super().keyPressEvent(event)

    def insert(self):
        if not self.ready(): return
        ident=self.selected_id(self.assets)
        asset=next((a for a in self.canvas.window.state['items'] if a['id']==ident),None)
        if asset is None: self.refresh(); return
        self.perform(lambda:self.canvas.insert_asset_with_destination(copy.deepcopy(asset),self.position,self.destination_id))

    def perform(self,operation,report_cancel=True):
        self.busy=True; self.status.hide()
        try:
            success=operation()
        except Exception as error:
            self.show_error('未能加入，選擇已保留。'+str(error)); return
        finally:
            self.busy=False
        if success:
            self.inserted=True; self.accept()
        elif report_cancel:
            self.show_error('未能加入，選擇已保留；請確認畫布後重試。')

    def new_module(self):
        if self.ready(): self.perform(lambda:self.canvas.add_new_with_destination(self.position,self.destination_id),False)

    def fit_window(self):
        parent=self.canvas.window
        bounds=QRect(parent.mapToGlobal(QPoint()),parent.size()).intersected(parent.screen().availableGeometry())
        self.setGeometry(bounds); self.place_sheet()

    def place_sheet(self):
        area=self.rect().adjusted(12,12,-12,-12)
        size=self.sheet_size
        width=max(1,min(area.width(),max(min(640,area.width()),size.width())))
        height=max(1,min(area.height(),max(min(350,area.height()),size.height())))
        left=area.center().x()-width//2; top=area.center().y()-height//2
        self.surface.setGeometry(left,top,width,height)
        self.arrange()
        self.grip.resize(18,18); self.grip.move(width-20,height-20); self.grip.raise_()

    def arrange(self):
        scale=max(1,self.canvas.window.state['settings'].get('ui_size',11)/11)
        narrow=self.surface.width()<740*scale
        self.category_pane.setVisible(not narrow); self.narrow_categories.setVisible(narrow)
        margin=16 if narrow else 24
        self.body.setContentsMargins(margin,margin,margin,18)
        self.split.widget(1).layout().setContentsMargins(0 if narrow else 12,0,0,0)
        line=self.title.fontMetrics().height()
        self.close_button.setFixedSize(max(36,line+16),max(36,line+16))
        for control in (self.new_button,self.destination,self.category,self.search):
            control.setMinimumHeight(max(36,control.fontMetrics().height()+16))
        self.assets.doItemsLayout(); self.align_search()

    def paintEvent(self,event):
        painter=QPainter(self); painter.fillRect(self.rect(),QColor(0,0,0,120))

    def showEvent(self,event):
        super().showEvent(event); self.fit_window(); self.split.setSizes(self.split_sizes)
        (self.destination if self.choose_destination else
         self.category if self.narrow_categories.isVisible() else
         self.modules if self.modules.currentRow()>=0 else self.search).setFocus()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if hasattr(self,'sheet_size'): self.place_sheet()

    def mousePressEvent(self,event):
        if not self.surface.geometry().contains(event.position().toPoint()): self.reject()
        event.accept()

    def mouseReleaseEvent(self,event): event.accept()
    def mouseDoubleClickEvent(self,event): event.accept()

    def save_layout(self,*_):
        if self.closed or self.layout_owner!=(self.canvas.window.store,self.canvas.window.state['workspace']): return
        settings=self.canvas.window.state['settings']
        sizes=self.split.sizes() if not self.category_pane.isHidden() else self.split_sizes
        # The persisted schema has always used three columns. Keep that shape;
        # the old lookup-column share now belongs to the local card area.
        settings['canvas_palette']=dict(size=[self.sheet_size.width(),self.sheet_size.height()],columns=[*sizes,0])
        self.canvas.window.changed('settings')

    def done(self,result):
        if self.closed: return
        self.closed=True; self.canvas.window.removeEventFilter(self)
        QApplication.instance().removeEventFilter(self)
        if self.last_mouse_position is not None:
            ClosingClickGuard(self.canvas.view.viewport(),self.last_mouse_position)
        super().done(result)

    def exec(self):
        result=super().exec(); self.deleteLater(); return result
