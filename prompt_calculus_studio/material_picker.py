"""Shared lightweight, category-aware library picker for editors and flow readers."""
from PySide6.QtCore import Qt, QPoint, QEvent
from PySide6.QtWidgets import QLineEdit, QListWidgetItem
from . import materials, material_navigation as nav, prompt_materials as pm
from .widgets import button, label, row, ComboBox
from .material_folder_navigation import FolderNavigation
from .module_chrome import dialog_colors
from .theme import visual_tokens,window_appearance
from .quiet_splitter import QuietSplitter
from .palette_cards import PickerCardDelegate
from .stacked_card_list import StackedCardList
from .material_scope import MaterialScope, populate


class MaterialPickerMixin:
    def picker_back(self):
        self.stack.setCurrentIndex(0 if self.picker_kind in ('flow','combination') else 1)

    def fit_picker(self):
        if not hasattr(self,'category_pane'):return
        narrow=self.width()<680
        self.category_pane.setVisible(not narrow);self.category_combo.setVisible(narrow)
        self.picker.layout().setContentsMargins(0 if narrow else 12,0,0,0)

    def _picker(self):
        from .prompt_group_editor import page, tool
        widget, layout = page(); self.stack.addWidget(widget)
        self.picker_navigation,pl=page();pl.addLayout(row(tool('back', '返回組合', self.picker_back), label('加入素材', 'SectionTitle'), None,
                             button('完成', self.picker_back, 'Quiet')));layout.addWidget(self.picker_navigation)
        colors=lambda:dialog_colors(visual_tokens(window_appearance(self.host)))
        self.category_combo=ComboBox();self.category_combo.setAccessibleName('內容分類');layout.addWidget(self.category_combo);self.category_combo.hide()
        self.picker_split = QuietSplitter(); self.picker_split.setChildrenCollapsible(False);layout.addWidget(self.picker_split, 1)
        self.category_pane, cats=page();cats.setContentsMargins(0,0,12,0)
        self.folders = FolderNavigation(manage=False,colors=colors); self.folders.setProperty('moduleChoices',True);self.folders.setSpacing(3)
        cats.addWidget(self.folders);self.picker_split.addWidget(self.category_pane)
        self.picker, right = page();right.setContentsMargins(12,0,0,0);right.setSpacing(8);self.picker_split.addWidget(self.picker)
        self.picker_header, self.picker_header_layout=page();self.picker_header_layout.setSpacing(8);right.addWidget(self.picker_header)
        self.picker_heading=label('全部分類','Heading');self.picker_header_layout.addWidget(self.picker_heading)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋名稱、別名或內容');self.search.setClearButtonEnabled(True);self.picker_header_layout.addWidget(self.search)
        self.candidates = StackedCardList(lambda:self.host.state['settings'],colors)
        self.candidates.setAccessibleName('可加入的素材');self.candidates.setSpacing(3);self.candidates.setMouseTracking(True)
        self.candidates.setStyleSheet('QListWidget {background:transparent;border:0;padding:0;}')
        self.candidates.setItemDelegate(PickerCardDelegate(self.candidates));self.candidates.setUniformItemSizes(False)
        self.candidates.setMinimumSize(0,360);self.candidates.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.candidates.installEventFilter(self)
        self.candidates.viewport().installEventFilter(self);right.addWidget(self.candidates, 1)
        self.picker_empty = label('沒有符合的素材', 'Subtle'); right.addWidget(self.picker_empty)
        self.folders.currentItemChanged.connect(self.scope_changed); self.folders.toggleRequested.connect(self.toggle_folder)
        self.search.textChanged.connect(self.refresh_picker)
        self.candidates.itemClicked.connect(lambda item:self.pick_material(item.data(Qt.ItemDataRole.UserRole),getattr(self.candidates,'release_global',None)))
        self.category_combo.currentIndexChanged.connect(self.combo_scope_changed)
        self.picker_split.setSizes([220, 550]); self.refresh_folders()

    def refresh_folders(self):
        self.scope=MaterialScope(self.host.state,(self.picker_kind,))
        self.collapsed=self.scope.collapsed;self.category=self.scope.normalize(self.category)
        self.folders.blockSignals(True);self.category_combo.blockSignals(True)
        populate(self.scope,self.folders,self.category_combo)
        for at in range(self.folders.count()):
            if self.folders.item(at).data(Qt.ItemDataRole.UserRole)==self.category:self.folders.setCurrentRow(at)
        self.category_combo.setCurrentIndex(self.category_combo.findData(self.category));self.category_combo.blockSignals(False)
        self.folders.blockSignals(False); self.refresh_picker()

    def toggle_folder(self, ident):
        if nav.folder(self.host.state,ident) is None:return
        if ident in self.collapsed: self.collapsed.remove(ident)
        else: self.collapsed.add(ident)
        self.host.state['settings']['material_folder_collapsed']=sorted(self.collapsed)
        self.host.changed('settings',refresh=False)
        library=getattr(self.host,'prompt_editor',None)
        if library is not None:library._refresh_categories()
        self.refresh_folders()

    def scope_changed(self, current, previous):
        if current:
            self.category = current.data(Qt.ItemDataRole.UserRole)
            self.host._material_browser_category=self.category
            self.category_combo.blockSignals(True);self.category_combo.setCurrentIndex(self.category_combo.findData(self.category));self.category_combo.blockSignals(False)
            self.refresh_picker()

    def combo_scope_changed(self):
        self.category=self.category_combo.currentData();self.host._material_browser_category=self.category;self.refresh_folders()

    def refresh_picker(self):
        rows=[item for item in materials.search(self.host.state,self.search.text(),material_type=self.picker_kind)
              if self.scope.matches(item,self.category)]
        matches={r['id'] for r in rows};rows=[r for r in self.host.state['items'] if r['id'] in matches]
        scroll=self.candidates.verticalScrollBar().value();self.candidates.clear()
        for item in rows:
            entry=QListWidgetItem(item['name']+'\n'+pm.summary(item).replace('\n',' '));entry.setData(Qt.ItemDataRole.UserRole,item['id'])
            self.candidates.addItem(entry)
        self.candidates.verticalScrollBar().setValue(scroll);self.picker_empty.setVisible(not rows)
        category=self.scope.name(self.category)
        self.picker_heading.setText(f'{category} · {len(rows)} 個素材')
        self.candidates.doItemsLayout();self.align_picker()

    def align_picker(self):
        viewport=self.candidates.viewport();origin=viewport.mapTo(self.candidates,QPoint()).x()
        if self.candidates.count():
            card=self.candidates.visualItemRect(self.candidates.item(0));left=origin+card.x()+1;right=self.candidates.width()-(origin+card.x()+card.width()-2)
        else:left=4;right=5
        self.picker_header_layout.setContentsMargins(max(0,left),0,max(0,right),0)

    def eventFilter(self,watched,event):
        if hasattr(self,'candidates') and watched is self.candidates.viewport() and event.type()==QEvent.Type.Resize:self.align_picker()
        if hasattr(self,'candidates') and watched is self.candidates and event.type()==QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return,Qt.Key.Key_Enter,Qt.Key.Key_Space):
            item=self.candidates.currentItem()
            if item:self.pick_material(item.data(Qt.ItemDataRole.UserRole))
            return True
        return super().eventFilter(watched,event)
