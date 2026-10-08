"""Canvas insertion sheet; shares completion and canvas transactions."""
import copy
from PySide6.QtCore import Qt, QPoint, QPointF, QSize, QRect, QEvent, QSignalBlocker
from PySide6.QtGui import QPainter, QColor, QTextCursor
from PySide6.QtWidgets import (QDialog, QFrame, QWidget, QVBoxLayout, QHBoxLayout,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSizeGrip, QScrollArea,
    QBoxLayout, QSizePolicy, QApplication, QPushButton)
from .completion import PromptEdit
from .core import format_candidate, insertion
from .widgets import label, button, RoundMenu, ComboBox
from .quiet_splitter import QuietSplitter
from .palette_cards import PickerCardDelegate, ClosingClickGuard
from .ui_icons import icon


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


class CanvasTagEdit(PromptEdit):
    def __init__(self, palette):
        self.palette=palette
        super().__init__(palette.canvas.window.completion, palette.surface)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAccessibleName('搜尋素材或輸入 Tag')

    def lookup_active(self):
        pane=getattr(self.palette,'lookup_pane',None)
        return self.palette.isVisible() and pane is not None and not pane.isHidden() and not self.palette.closed and not self.composing

    def lookup_notice(self, text):
        if self.lookup_active(): self.palette.lookup_status.setText(text)

    def schedule(self):
        if self.inserting or self.composing: return
        expected=(self.toPlainText(),self.context())
        if self.expected!=expected:
            self.expected=None; self.candidates={}; self.palette.set_suggestions([])
        if self.lookup_active():
            self.palette.lookup_status.setText('正在查詢…' if self.context() else '輸入 Tag 或中文查詢。')
            self.service.schedule(self)

    def inputMethodEvent(self,event):
        super().inputMethodEvent(event)
        if self.composing:
            self.expected=None; self.candidates={}; self.palette.set_suggestions([])

    def display(self,token,rows):
        if not self.lookup_active() or token!=self.context(): return
        self.expected=(self.toPlainText(),token); self.candidates={}
        for candidate in rows[:12]:
            value=format_candidate(candidate,self.service.window.state['settings'],token.artist)
            title=value+'    · '+candidate['source']
            self.candidates[title]=(value,candidate)
        self.palette.set_suggestions(list(self.candidates))

    def insert_completion(self,title):
        # A lookup choice only edits the query. It is never an insertion action.
        if not self.lookup_active() or not self.expected or title not in self.candidates: return
        text, token = self.expected
        if self.toPlainText() != text or self.context() != token or self.composing: return
        start, end, replacement = insertion(text, token, self.candidates[title][0])
        self.inserting = True
        cursor = self.textCursor(); cursor.beginEditBlock()
        cursor.setPosition(len(text[:start].encode('utf-16-le')) // 2)
        cursor.setPosition(len(text[:end].encode('utf-16-le')) // 2, QTextCursor.MoveMode.KeepAnchor)
        cursor.insertText(replacement); cursor.endEditBlock(); self.setTextCursor(cursor)
        self.inserting = False; self.expected = None; self.candidates = {}
        self.service.cancel(self); self.palette.set_suggestions([])
        self.palette.search_cursor = QTextCursor(cursor)
        self.palette.lookup_status.setText('候選已填入；按「加入輸入文字」才會加入文字畫布。')
        self.palette.focus_search()

    def resizeEvent(self,event):
        super().resizeEvent(event)
        if event.size().width()!=event.oldSize().width() and getattr(self.palette,'query',None) is self:
            self.palette.resize_query()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type()==QEvent.Type.FontChange and getattr(self.palette,'query',None) is self:
            self.palette.resize_query()

    def keyPressEvent(self,event):
        if self.palette.navigate(self,event): return
        # QCompleter remains available to the shared insertion method, but never
        # opens over this sheet. Enter here always means the user's typed text.
        QPlainTextEdit.keyPressEvent(self,event)


class CanvasPalette(QDialog):
    UNCATEGORIZED = '@uncategorized'

    def __init__(self,canvas,position,destination_id=None,choose_destination=False):
        super().__init__(canvas.window)
        self.canvas=canvas; self.position=QPointF(position) if isinstance(position,QPointF) else None
        self.inserted=False; self.closed=False; self.busy=False
        self.net_return=('query',None); self.search_cursor=None; self.last_mouse_position=None
        self.destination_id=(destination_id if destination_id is not None else
                             getattr(canvas,'insertion_canvas',None) or getattr(getattr(canvas,'editor',None),'key',None))
        self.choose_destination=bool(choose_destination and self.destination_id is None)
        self.layout_owner=(canvas.window.store,canvas.window.state['workspace'])
        self.setWindowTitle('加入文字模組')
        self.setWindowFlags(Qt.WindowType.Dialog|Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.surface=QFrame(self); self.surface.setObjectName('DialogSurface')
        self.body=QVBoxLayout(self.surface); self.body.setContentsMargins(24,24,24,20); self.body.setSpacing(12)
        header=QHBoxLayout(); header.setSpacing(12)
        self.title=label('加入文字模組','DialogTitle',True); header.addWidget(self.title,1)
        self.close_button=button('',self.reject,'Quiet'); self.close_button.setIcon(icon('close'))
        self.close_button.setAccessibleName('關閉文字挑選'); self.close_button.setToolTip('關閉')
        header.addWidget(self.close_button,0,Qt.AlignmentFlag.AlignTop); self.body.addLayout(header)
        self.destination_label=label('','Subtle',True); self.body.addWidget(self.destination_label)
        self.destination=ComboBox(); self.destination.setAccessibleName('目的文字畫布')
        self.destination.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        self.destination.addItem('選擇目的文字畫布',None)
        for ident,value in self.canvases().items(): self.destination.addItem(value['name'],ident)
        self.destination.setCurrentIndex(max(0,self.destination.findData(self.destination_id)))
        self.body.addWidget(self.destination); self.destination.setVisible(self.choose_destination)
        self.destination.currentIndexChanged.connect(self.destination_changed)

        # The body scrolls at small window sizes; controls keep natural text heights.
        self.scroll=QScrollArea(); self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setWidgetResizable(True); self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content=QWidget(); self.content.setObjectName('ScrollContent')
        self.content.setMinimumWidth(0); content=QVBoxLayout(self.content)
        content.setContentsMargins(0,8,0,0); content.setSpacing(12)
        self.scroll.setWidget(self.content); self.body.addWidget(self.scroll,1)
        self.query=CanvasTagEdit(self); self.query.setPlaceholderText('搜尋名稱、別名或內容；也可輸入 Tag')
        content.addWidget(self.query)
        self.scope=label('','Subtle',True); content.addWidget(self.scope)
        self.lookup_toggle=button('展開標籤查詢',self.toggle_lookup,'Quiet')
        self.lookup_toggle.setIcon(icon('chevron-down')); self.lookup_toggle.setStyleSheet('text-align:left; padding-left:0;')
        content.addWidget(self.lookup_toggle)
        self.lookup_pane=QWidget(); lookup=QVBoxLayout(self.lookup_pane)
        lookup.setContentsMargins(0,0,0,0); lookup.setSpacing(8)
        self.lookup_status=label('輸入 Tag 或中文查詢。','Subtle',True); lookup.addWidget(self.lookup_status)
        self.suggestions=QListWidget(); self.suggestions.setAccessibleName('聯網查詢建議')
        self.suggestions.setMinimumSize(0,0); lookup.addWidget(self.suggestions)
        self.submit_button=button('加入輸入文字',self.submit_tag); lookup.addWidget(self.submit_button,0,Qt.AlignmentFlag.AlignLeft)
        content.addWidget(self.lookup_pane); self.lookup_pane.hide()
        self.narrow_categories=QWidget(); catrow=QHBoxLayout(self.narrow_categories); catrow.setContentsMargins(0,0,0,0)
        self.category=ComboBox(); self.category.setAccessibleName('內容分類')
        self.category.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
        catrow.addWidget(self.category,1)
        self.category_manage=button('',self.category_actions,'Quiet'); self.category_manage.setIcon(icon('more-horizontal'))
        self.category_manage.setAccessibleName('管理分類'); catrow.addWidget(self.category_manage)
        content.addWidget(self.narrow_categories)
        self.split=QuietSplitter(); self.split.setChildrenCollapsible(False); content.addWidget(self.split,1)
        self.category_pane=QWidget(); cats=QVBoxLayout(self.category_pane); cats.setContentsMargins(0,0,12,0); cats.setSpacing(8)
        self.modules=QListWidget(); self.modules.setAccessibleName('內容分類'); cats.addWidget(self.modules,1)
        cats.addWidget(button('管理分類',self.category_actions,'Quiet'))
        self.split.addWidget(self.category_pane)
        local=QWidget(); local.setMinimumWidth(0); rows=QVBoxLayout(local); rows.setContentsMargins(12,0,0,0); rows.setSpacing(8)
        self.assets_heading=label('本地文字模組','Heading'); rows.addWidget(self.assets_heading)
        self.assets=QListWidget(); self.assets.setAccessibleName('本地文字模組'); self.assets.setSpacing(3)
        self.assets.setItemDelegate(PickerCardDelegate(self.assets)); self.assets.setMouseTracking(True)
        self.assets.setUniformItemSizes(False); rows.addWidget(self.assets,1)
        self.empty=label('','Subtle',True); rows.addWidget(self.empty)
        self.search_all=button('搜尋全部分類',self.search_all_categories,'Quiet'); rows.addWidget(self.search_all,0,Qt.AlignmentFlag.AlignLeft)
        self.empty_spacer=QWidget(); self.empty_spacer.setSizePolicy(QSizePolicy.Policy.Preferred,QSizePolicy.Policy.Expanding)
        rows.addWidget(self.empty_spacer,1)
        self.split.addWidget(local)
        self.status=label('',None,True); self.status.hide(); content.addWidget(self.status)
        self.footer=QBoxLayout(QBoxLayout.Direction.LeftToRight); self.footer.setSpacing(10)
        self.new_button=button('新增空白模組',self.new_module); self.new_button.setIcon(icon('plus'))
        self.material_button=button('新建可重用素材',self.new_material,'Quiet')
        self.footer.addWidget(self.new_button); self.footer.addWidget(self.material_button); self.footer.addStretch()
        content.addLayout(self.footer)
        for listing in (self.modules,self.assets,self.suggestions):
            listing.setMinimumSize(0,0); listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            listing.setTextElideMode(Qt.TextElideMode.ElideRight); listing.installEventFilter(self)
        self.grip=PaletteGrip(self)
        saved=canvas.window.state['settings'].get('canvas_palette',{})
        self.sheet_size=QSize(*saved.get('size',[900,660]))
        columns=saved.get('columns',[200,550,0]); self.split_sizes=[columns[0],sum(columns[1:])]
        self.split.splitterMoved.connect(self.save_layout)
        self.populate_categories()
        self.modules.currentRowChanged.connect(self.category_changed)
        self.category.currentIndexChanged.connect(self.combo_category_changed)
        self.query.textChanged.connect(self.refresh); self.query.textChanged.connect(self.resize_query)
        # Both Qt activation paths can fire for the same click. The operation guard
        # closes only after a successful transaction and blocks the second path.
        self.assets.itemClicked.connect(lambda _:self.insert())
        self.suggestions.itemClicked.connect(lambda _:self.insert_suggestion())
        self.assets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.assets.customContextMenuRequested.connect(self.asset_menu)
        self.modules.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.modules.customContextMenuRequested.connect(self.module_menu)
        self.refresh(); self.resize_query(); self.update_destination()
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
            self.destination_label.setText('加入至「'+value['name']+'」 · 點選模組加入')
        elif not self.canvases():
            self.destination_label.setText('尚無文字畫布；請關閉此頁，返回 Canvas 建立文字畫布。')
        else:
            self.destination_label.setText('請先選擇目的文字畫布。' if self.choose_destination else '目的文字畫布已不存在，請返回 Canvas 重新開啟。')
        for control in (self.assets,self.new_button,self.material_button,self.submit_button):
            control.setEnabled(value is not None and not self.busy)

    def ready(self):
        if self.closed or self.inserted or self.busy: return False
        if self.layout_owner!=(self.canvas.window.store,self.canvas.window.state['workspace']):
            self.show_error('工作區已切換，請關閉並從目前文字畫布重新開啟。'); return False
        if self.destination_id not in self.canvases():
            self.update_destination(); self.show_error('請選擇有效的目的文字畫布後再加入。'); return False
        return True

    def uncategorized_ids(self):
        return {m['id'] for m in self.canvas.window.state['modules'] if m['name']=='未分類'}

    def populate_categories(self):
        selected=(self.selected_id(self.modules) if self.modules.count() else
                  getattr(self.canvas.window,'_material_browser_category',None))
        state=self.canvas.window.state
        if selected==self.UNCATEGORIZED:
            selected=next((m['id'] for m in state['modules'] if m['name']=='未分類'),selected)
        blocked=QSignalBlocker(self.modules); combo_blocked=QSignalBlocker(self.category)
        self.modules.clear(); self.category.clear()
        categories=[('全部',None)]+[(m['name'],m['id']) for m in state['modules']]
        if not self.uncategorized_ids(): categories.append(('未分類',self.UNCATEGORIZED))
        for name,ident in categories:
            count=sum(1 for item in state['items'] if self.in_category(item,ident))
            self.modules.addItem(self.entry(f'{name}   {count}',ident)); self.category.addItem(f'{name} · {count}',ident)
        if not self.restore_selection(self.modules,selected): self.modules.setCurrentRow(0)
        self.category.setCurrentIndex(max(0,self.category.findData(self.selected_id(self.modules))))
        del blocked,combo_blocked
        self.canvas.window._material_browser_category=self.get_browser_category()

    def in_category(self,item,ident):
        if ident is None: return True
        if ident==self.UNCATEGORIZED:
            known={m['id'] for m in self.canvas.window.state['modules']}
            return item.get('module') in self.uncategorized_ids() or item.get('module') not in known
        return item.get('module')==ident

    def category_name(self):
        ident=self.selected_id(self.modules)
        if ident is None: return '全部分類'
        if ident==self.UNCATEGORIZED: return '未分類'
        return next((m['name'] for m in self.canvas.window.state['modules'] if m['id']==ident),'目前分類')

    def category_changed(self):
        blocked=QSignalBlocker(self.category)
        self.category.setCurrentIndex(self.category.findData(self.selected_id(self.modules)))
        del blocked
        self.canvas.window._material_browser_category=self.get_browser_category()
        self.refresh()

    def get_browser_category(self):
        return self.selected_id(self.modules)

    def set_browser_category(self,ident):
        if ident==self.UNCATEGORIZED:
            ident=next((m['id'] for m in self.canvas.window.state['modules'] if m['name']=='未分類'),ident)
        if not self.restore_selection(self.modules,ident): self.modules.setCurrentRow(0)

    def combo_category_changed(self):
        self.restore_selection(self.modules,self.category.currentData())

    def search_all_categories(self):
        self.restore_selection(self.modules,None); self.focus_search()

    def show_error(self,text):
        self.status.setText(text); self.status.show(); self.scroll.ensureWidgetVisible(self.status)

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

    def resize_query(self):
        available=max(1,self.query.viewport().width())
        lines=sum(max(1,(self.query.fontMetrics().horizontalAdvance(line)+available-1)//available) for line in self.query.toPlainText().split('\n'))
        line=self.query.fontMetrics().lineSpacing()
        self.query.setFixedHeight(max(line+24,min(line*3+24,lines*line+24)))

    def refresh(self):
        ident=self.selected_id(self.assets); had_focus=self.assets.hasFocus()
        self.assets.clear(); mid=self.selected_id(self.modules); query=self.query.toPlainText().strip().casefold()
        for asset in self.canvas.window.state['items']:
            if not self.in_category(asset,mid): continue
            if query not in ' '.join([asset['name'],asset['prompt'],*asset.get('aliases',[])]).casefold(): continue
            entry=self.entry(asset['name']+'\n'+asset['prompt'].replace('\n',' '),asset['id'])
            entry.setToolTip(asset['name']+'\n'+asset['prompt'])
            self.assets.addItem(entry)
        self.assets_heading.setText(f'{self.category_name()} · {self.assets.count()} 個模組')
        self.scope.setText('搜尋範圍：'+self.category_name()+' · 名稱、別名、內容')
        self.empty.setText('沒有符合的模組；可搜尋全部分類或新增文字模組。' if query else '這個分類尚無文字模組；可在下方新增。')
        self.empty.setVisible(self.assets.count()==0)
        self.empty_spacer.setVisible(self.assets.count()==0)
        self.search_all.setVisible(self.assets.count()==0 and mid is not None)
        self.assets.setVisible(self.assets.count()>0)
        valid=self.restore_selection(self.assets,ident)
        if had_focus and not valid: self.focus_search()

    def set_suggestions(self,titles):
        ident=self.selected_id(self.suggestions); had_focus=self.suggestions.hasFocus()
        self.suggestions.clear()
        for title in titles: self.suggestions.addItem(self.entry(title,title))
        self.suggestions.setVisible(bool(titles))
        self.lookup_status.setText(f'{len(titles)} 個候選 · 選擇只填入文字' if titles else '沒有候選，可繼續輸入或直接加入文字。')
        self.resize_suggestions()
        valid=self.restore_selection(self.suggestions,ident)
        if had_focus and not valid: self.focus_search()

    def focus_search(self):
        self.query.setFocus(Qt.FocusReason.OtherFocusReason)
        if self.search_cursor is not None:
            self.query.inserting=True
            self.query.setTextCursor(self.search_cursor)
            self.query.inserting=False

    def resize_suggestions(self):
        row=max(30,self.suggestions.fontMetrics().height()+16)
        self.suggestions.setFixedHeight(min(3,max(1,self.suggestions.count()))*row+4)

    def focus_list(self,listing,origin,first=False):
        if not listing.count() or not listing.isVisible() or not listing.isEnabled(): return False
        if origin is self.query: self.search_cursor=QTextCursor(self.query.textCursor())
        if listing is self.suggestions:
            self.net_return=('assets',self.selected_id(self.assets)) if origin is self.assets else ('query',None)
        if first or listing.currentRow()<0: listing.setCurrentRow(0)
        listing.setFocus(Qt.FocusReason.OtherFocusReason); listing.scrollToItem(listing.currentItem()); return True

    def navigate(self,source,event):
        key=event.key(); modifiers=event.modifiers()
        if self.query.composing:
            # Do not let QDialog's default button / Escape handling see an IME key.
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Escape,Qt.Key.Key_Tab,Qt.Key.Key_Backtab,
                       Qt.Key.Key_Up,Qt.Key.Key_Down,Qt.Key.Key_Left,Qt.Key.Key_Right):
                event.accept(); return True
            return False
        if key==Qt.Key.Key_Escape: self.reject(); return True
        if key in (Qt.Key.Key_Tab,Qt.Key.Key_Backtab):
            order=[self.query,self.assets,self.suggestions,self.submit_button,self.new_button,self.material_button,
                   self.category if self.narrow_categories.isVisible() else self.modules,
                   self.lookup_toggle,self.close_button,self.destination]
            index=order.index(source) if source in order else 0
            step=-1 if key==Qt.Key.Key_Backtab or modifiers&Qt.KeyboardModifier.ShiftModifier else 1
            for offset in range(1,len(order)+1):
                target=order[(index+step*offset)%len(order)]
                if not target.isVisible() or not target.isEnabled(): continue
                if target is self.query: self.focus_search(); break
                if isinstance(target,QListWidget):
                    if self.focus_list(target,source): break
                else: target.setFocus(); self.scroll.ensureWidgetVisible(target); break
            return True
        if modifiers!=Qt.KeyboardModifier.NoModifier: return False
        if source is self.query:
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter): self.submit_tag(); return True
            if key==Qt.Key.Key_Down: self.focus_list(self.assets,source,first=True); return True
            cursor=self.query.textCursor()
            if key==Qt.Key.Key_Right and cursor.atEnd() and not cursor.hasSelection():
                self.focus_list(self.suggestions,source,first=True); return True
        elif source is self.assets:
            if key==Qt.Key.Key_Up and self.assets.currentRow()==0: self.focus_search(); return True
            if key==Qt.Key.Key_Right: self.focus_list(self.suggestions,source); return True
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter): self.insert(); return True
        elif source is self.suggestions:
            if key==Qt.Key.Key_Left:
                kind,ident=self.net_return
                if kind=='assets' and self.restore_selection(self.assets,ident): self.assets.setFocus()
                else: self.focus_search()
                return True
            if key in (Qt.Key.Key_Return,Qt.Key.Key_Enter): self.insert_suggestion(); return True
        return False

    def eventFilter(self,watched,event):
        if event.type() in (QEvent.Type.MouseButtonPress,QEvent.Type.MouseButtonRelease,QEvent.Type.MouseButtonDblClick):
            if isinstance(watched,QWidget) and (watched is self or self.isAncestorOf(watched)):
                self.last_mouse_position=event.globalPosition().toPoint()
        if watched is self.canvas.window and event.type() in (QEvent.Type.Resize,QEvent.Type.Move):
            if self.isVisible(): self.fit_window()
        elif watched in (self.assets,self.suggestions,self.modules) and event.type()==QEvent.Type.KeyPress:
            if self.navigate(watched,event): return True
        return super().eventFilter(watched,event)

    def keyPressEvent(self,event):
        if not self.navigate(self.focusWidget(),event): super().keyPressEvent(event)

    def submit_tag(self,completed=False):
        if not self.ready(): return
        text=self.query.toPlainText()
        if not text.strip(): return
        self.perform(lambda:self.canvas.add_tag_with_destination(text,self.position,self.destination_id))

    def insert_suggestion(self):
        if self.closed: return
        ident=self.selected_id(self.suggestions)
        if ident is not None: self.query.insert_completion(ident)

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
            self.show_error('未能加入，選擇已保留；請確認文字畫布後重試。')

    def new_module(self):
        if self.ready(): self.perform(lambda:self.canvas.add_new_with_destination(self.position,self.destination_id),False)

    def new_material(self):
        if not self.ready(): return
        destination=self.destination_id
        self.accept()
        self.canvas.window.open_new_material(destination)

    def toggle_lookup(self):
        visible=self.lookup_pane.isHidden()
        self.lookup_pane.setVisible(visible)
        self.lookup_toggle.setText('收合標籤查詢' if visible else '展開標籤查詢')
        self.lookup_toggle.setIcon(icon('chevron-up' if visible else 'chevron-down'))
        if visible:self.query.setFocus();self.query.schedule()
        else:
            cursor=QTextCursor(self.query.textCursor())
            self.canvas.window.completion.cancel(self.query)
            self.query.expected=None; self.query.candidates={}
            self.set_suggestions([]); self.search_cursor=cursor; self.focus_search()
        self.arrange()

    def manage(self):
        self.canvas.window.new_module(); self.populate_categories(); self.refresh()

    def category_actions(self):
        menu=RoundMenu(self); menu.addAction('新增分類',self.manage)
        ident=self.selected_id(self.modules)
        if ident not in (None,self.UNCATEGORIZED):
            menu.addAction('編輯分類與選擇模式',lambda:self.edit_category(ident))
        menu.open_at(self.category_manage.mapToGlobal(self.category_manage.rect().bottomLeft()) if self.narrow_categories.isVisible()
                     else self.modules.mapToGlobal(self.modules.rect().bottomLeft()))

    def edit_category(self,ident):
        self.canvas.window.current_module=ident; self.canvas.window.edit_module()
        self.populate_categories(); self.refresh()

    def asset_menu(self,pos):
        item=self.assets.itemAt(pos)
        if item is None: return
        ident=item.data(Qt.ItemDataRole.UserRole); menu=RoundMenu(self)
        menu.addAction('加入文字畫布',lambda:(self.assets.setCurrentItem(item),self.insert()))
        menu.addAction('編輯素材原型',lambda:(self.accept(),self.canvas.window.edit_item(ident)))
        menu.addAction('刪除素材…',lambda:(self.accept(),self.canvas.window.delete_item(ident)))
        menu.open_at(self.assets.viewport().mapToGlobal(pos))

    def module_menu(self,pos):
        item=self.modules.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) in (None,self.UNCATEGORIZED): return
        ident=item.data(Qt.ItemDataRole.UserRole); menu=RoundMenu(self)
        menu.addAction('編輯分類與選擇模式',lambda:self.edit_category(ident)); menu.addAction('新增分類',self.manage)
        menu.open_at(self.modules.viewport().mapToGlobal(pos))

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
        self.footer.setDirection(QBoxLayout.Direction.TopToBottom if self.surface.width()<600*scale
                                 else QBoxLayout.Direction.LeftToRight)
        margin=16 if narrow else 24
        self.body.setContentsMargins(margin,margin,margin,18)
        self.split.widget(1).layout().setContentsMargins(0 if narrow else 12,0,0,0)
        self.split.setMinimumHeight(round(170*scale))
        self.resize_suggestions()
        line=self.query.fontMetrics().height()
        self.close_button.setFixedSize(max(36,line+16),max(36,line+16))
        for control in (self.new_button,self.material_button,self.submit_button,self.lookup_toggle,
                        self.destination,self.category,self.category_manage):
            control.setMinimumHeight(max(36,control.fontMetrics().height()+16))
        self.assets.doItemsLayout(); self.resize_query()

    def paintEvent(self,event):
        painter=QPainter(self); painter.fillRect(self.rect(),QColor(0,0,0,120))

    def showEvent(self,event):
        super().showEvent(event); self.fit_window(); self.split.setSizes(self.split_sizes)
        self.query.setFocus(); self.query.schedule()

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
        self.closed=True; self.query.service.cancel(self.query)
        self.query.completer.popup().hide(); self.canvas.window.removeEventFilter(self)
        QApplication.instance().removeEventFilter(self)
        if self.last_mouse_position is not None:
            ClosingClickGuard(self.canvas.view.viewport(),self.last_mouse_position)
        super().done(result)

    def exec(self):
        result=super().exec(); self.deleteLater(); return result
