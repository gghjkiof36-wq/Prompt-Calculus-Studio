"""Canvas insertion sheet; shares completion and canvas transactions."""
import copy
from PySide6.QtCore import Qt, QPoint, QPointF, QSize, QRect, QEvent
from PySide6.QtGui import QPainter, QColor, QTextCursor
from PySide6.QtWidgets import (QDialog, QFrame, QWidget, QVBoxLayout, QSplitter,
    QListWidget, QListWidgetItem, QPlainTextEdit, QSizeGrip)
from .completion import PromptEdit
from .core import format_candidate
from .widgets import label, button, RoundMenu
from .quiet_splitter import QuietSplitter


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
        return self.palette.isVisible() and not self.palette.closed and not self.composing

    def lookup_notice(self, text):
        if not self.palette.closed: self.palette.suggestions.setToolTip(text)

    def schedule(self):
        if self.inserting or self.composing: return
        expected=(self.toPlainText(),self.context())
        if self.expected!=expected:
            self.expected=None; self.candidates={}; self.palette.set_suggestions([])
        if self.lookup_active(): self.service.schedule(self)

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
        if self.lookup_active(): super().insert_completion(title)

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
    def __init__(self,canvas,position):
        super().__init__(canvas.window)
        self.canvas=canvas; self.position=QPointF(position) if isinstance(position,QPointF) else None
        self.inserted=False; self.closed=False; self.net_return=('query',None); self.search_cursor=None
        self.setWindowTitle('新增模塊')
        self.setWindowFlags(Qt.WindowType.Dialog|Qt.WindowType.FramelessWindowHint)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.surface=QFrame(self); self.surface.setObjectName('DialogSurface')
        self.body=QVBoxLayout(self.surface); self.body.setContentsMargins(20,20,20,18); self.body.setSpacing(14)
        self.query=CanvasTagEdit(self); self.query.setPlaceholderText('搜尋素材或輸入 Tag')
        self.body.addWidget(self.query)
        self.split=QuietSplitter(); self.split.setChildrenCollapsible(False); self.body.addWidget(self.split,1)
        self.modules=QListWidget(); self.modules.setAccessibleName('類別')
        self.assets=QListWidget(); self.assets.setAccessibleName('本地模塊')
        self.assets.setObjectName('PaletteAssets'); self.assets.setSpacing(4)
        self.suggestions=QListWidget(); self.suggestions.setAccessibleName('聯網查詢建議')
        for title,listing in (('類別',self.modules),('本地模塊',self.assets),('聯網查詢',self.suggestions)):
            pane=QWidget(); pane.setMinimumWidth(104)
            layout=QVBoxLayout(pane); layout.setContentsMargins(0 if listing is self.modules else 12,0,0 if listing is self.suggestions else 12,0); layout.setSpacing(8)
            heading=label(title,'Subtle'); layout.addWidget(heading); layout.addWidget(listing,1)
            if listing is self.assets: self.assets_heading=heading
            listing.setMinimumSize(0,0); listing.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            listing.setTextElideMode(Qt.TextElideMode.ElideRight)
            listing.installEventFilter(self)
            if listing is self.modules:
                layout.addWidget(button('新增分類',self.manage,'Quiet'))
            if listing is self.assets:
                layout.addWidget(button('新增空白模組',self.new_module,'Quiet'))
            self.split.addWidget(pane)
        self.grip=PaletteGrip(self)
        self.layout_owner=(canvas.window.store,canvas.window.state['workspace'])
        saved=canvas.window.state['settings'].get('canvas_palette',{})
        self.sheet_size=QSize(*saved.get('size',[960,620]))
        self.split_sizes=saved.get('columns',[170,370,350])
        self.split.splitterMoved.connect(self.save_layout)
        self.modules.addItem(self.entry('全部模組',None))
        for module in canvas.window.state['modules']: self.modules.addItem(self.entry(module['name'],module['id']))
        self.modules.addItem(self.entry('功能模組','@functions'))
        self.function_actions=dict(canvas.functions.insertion_actions(self.position))
        self.modules.currentRowChanged.connect(self.refresh)
        self.query.textChanged.connect(self.refresh); self.query.textChanged.connect(self.resize_query)
        self.query.accepted.connect(lambda _:self.submit_tag(completed=True))
        self.assets.itemClicked.connect(lambda _:self.insert()); self.assets.itemActivated.connect(lambda _:self.insert())
        self.suggestions.itemClicked.connect(lambda _:self.insert_suggestion())
        self.suggestions.itemActivated.connect(lambda _:self.insert_suggestion())
        self.assets.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.assets.customContextMenuRequested.connect(self.asset_menu)
        self.modules.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu); self.modules.customContextMenuRequested.connect(self.module_menu)
        self.modules.setCurrentRow(0); self.resize_query()
        canvas.window.installEventFilter(self)
        # Establish native geometry and the final backdrop before the first
        # show. Toggling an opacity effect after showing caused an extra flash.
        self.fit_window()

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
        self.query.setFixedHeight(min(112,max(44,lines*self.query.fontMetrics().lineSpacing()+24)))

    def refresh(self):
        ident=self.selected_id(self.assets); had_focus=self.assets.hasFocus()
        self.assets.clear(); mid=self.selected_id(self.modules); query=self.query.toPlainText().strip().casefold()
        self.assets_heading.setText('功能模組' if mid=='@functions' else '本地模塊')
        if mid=='@functions':
            for title in self.function_actions:
                if query in title.casefold(): self.assets.addItem(self.entry(title,title))
        for asset in self.canvas.window.state['items']:
            if mid is not None and asset['module']!=mid: continue
            if query not in ' '.join([asset['name'],asset['prompt'],*asset['aliases']]).casefold(): continue
            entry=self.entry(asset['name']+'\n'+asset['prompt'].replace('\n',' ')[:100],asset['id'])
            entry.setToolTip(asset['prompt']); self.assets.addItem(entry)
        valid=self.restore_selection(self.assets,ident)
        if had_focus and not valid: self.focus_search()

    def set_suggestions(self,titles):
        ident=self.selected_id(self.suggestions); had_focus=self.suggestions.hasFocus()
        self.suggestions.clear()
        for title in titles: self.suggestions.addItem(self.entry(title,title))
        valid=self.restore_selection(self.suggestions,ident)
        if had_focus and not valid: self.focus_search()

    def focus_search(self):
        self.query.setFocus(Qt.FocusReason.OtherFocusReason)
        if self.search_cursor is not None:
            self.query.inserting=True
            self.query.setTextCursor(self.search_cursor)
            self.query.inserting=False

    def focus_list(self,listing,origin,first=False):
        if not listing.count(): return False
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
            order=[self.query,self.assets,self.suggestions]
            index=order.index(source) if source in order else 0
            step=-1 if key==Qt.Key.Key_Backtab or modifiers&Qt.KeyboardModifier.ShiftModifier else 1
            for offset in range(1,4):
                target=order[(index+step*offset)%3]
                if target is self.query: self.focus_search(); break
                if self.focus_list(target,source): break
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
        if watched is self.canvas.window and event.type() in (QEvent.Type.Resize,QEvent.Type.Move):
            if self.isVisible(): self.fit_window()
        elif watched in (self.assets,self.suggestions,self.modules) and event.type()==QEvent.Type.KeyPress:
            if self.navigate(watched,event): return True
        return super().eventFilter(watched,event)

    def keyPressEvent(self,event):
        if not self.navigate(self.focusWidget(),event): super().keyPressEvent(event)

    def submit_tag(self,completed=False):
        if self.inserted or self.closed: return
        text=self.query.toPlainText()
        if completed and text.endswith(', '): text=text[:-2]
        if not text.strip() or not self.canvas.add_tag(text,self.position): return
        self.inserted=True; self.query.clear(); self.accept()

    def insert_suggestion(self):
        if self.closed: return
        ident=self.selected_id(self.suggestions)
        if ident is not None: self.query.insert_completion(ident)

    def insert(self):
        if self.inserted or self.closed: return
        ident=self.selected_id(self.assets)
        if self.selected_id(self.modules)=='@functions':
            action=self.function_actions.get(ident)
            if action is not None: self.inserted=True; self.accept(); action()
            return
        asset=next((a for a in self.canvas.window.state['items'] if a['id']==ident),None)
        if asset is None: self.refresh(); return
        self.inserted=True; self.accept(); self.canvas.insert_asset(copy.deepcopy(asset),self.position)

    def new_module(self): self.accept(); self.canvas.add_new(self.position)
    def manage(self): self.accept(); self.canvas.window.new_module()

    def asset_menu(self,pos):
        item=self.assets.itemAt(pos)
        if item is None: return
        if self.selected_id(self.modules)=='@functions': return
        ident=item.data(Qt.ItemDataRole.UserRole); menu=RoundMenu(self)
        menu.addAction('加入畫布',lambda:(self.assets.setCurrentItem(item),self.insert()))
        menu.addAction('編輯素材原型',lambda:(self.accept(),self.canvas.window.edit_item(ident)))
        menu.addAction('刪除素材…',lambda:(self.accept(),self.canvas.window.delete_item(ident)))
        menu.open_at(self.assets.viewport().mapToGlobal(pos))

    def module_menu(self,pos):
        item=self.modules.itemAt(pos)
        if item is None or item.data(Qt.ItemDataRole.UserRole) in (None,'@functions'): return
        ident=item.data(Qt.ItemDataRole.UserRole); menu=RoundMenu(self)
        def edit():
            self.canvas.window.current_module=ident; self.accept(); self.canvas.window.edit_module()
        menu.addAction('編輯分類與選擇模式',edit); menu.addAction('新增分類',self.manage)
        menu.open_at(self.modules.viewport().mapToGlobal(pos))

    def fit_window(self):
        parent=self.canvas.window
        bounds=QRect(parent.mapToGlobal(QPoint()),parent.size()).intersected(parent.screen().availableGeometry())
        self.setGeometry(bounds); self.place_sheet()

    def place_sheet(self):
        area=self.rect().adjusted(12,12,-12,-12)
        size=self.sheet_size
        minimum=self.surface.minimumSizeHint()
        width=min(area.width(),max(minimum.width(),min(640,area.width()),size.width()))
        height=min(area.height(),max(minimum.height(),min(350,area.height()),size.height()))
        left=area.center().x()-width//2; top=area.center().y()-height//2
        self.surface.setGeometry(left,top,width,height)
        self.grip.resize(18,18); self.grip.move(width-20,height-20); self.grip.raise_()

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
        if self.layout_owner!=(self.canvas.window.store,self.canvas.window.state['workspace']): return
        settings=self.canvas.window.state['settings']
        settings['canvas_palette']=dict(size=[self.sheet_size.width(),self.sheet_size.height()],columns=self.split.sizes())
        self.canvas.window.changed('settings')

    def done(self,result):
        self.closed=True; self.query.service.cancel(self.query)
        self.query.completer.popup().hide(); self.canvas.window.removeEventFilter(self)
        super().done(result)

    def exec(self):
        result=super().exec(); self.deleteLater(); return result
