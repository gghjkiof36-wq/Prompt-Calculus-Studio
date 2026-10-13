"""Searchable Canvas function chooser; text-canvas material picking stays separate."""
from PySide6.QtCore import Qt, QSize, QPointF, QRect, QEvent
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QHBoxLayout, QVBoxLayout, QFrame, QPushButton, QLabel, QSizePolicy, QScrollArea
from .widgets import label, button, row, RoundMenu
from .dialog_presentation import CanvasSheetDialog
from .module_preview import ModulePreview
from .module_chrome import style_action, apply_module_dialog
from .canvas_items import canvas_colors
from .ui_icons import icon


FUNCTIONS = (
    ('canvas', '文字畫布', '文字', '放置並組合文字模組', 'panel-left'),
    ('output', 'Prompt 控制', '文字', '調整來源順序與完整 Prompt', 'list-ordered'),
    ('clip', 'CLIP 輸入', '文字', '將文字送到工作流的指定欄位', 'type'),
    ('reader', '讀取文字', '文字', '選擇並讀取 Stage 的文字結果', 'file-text'),
    ('source', '圖片來源', '圖片', '提供圖片、整批圖片與附加提示詞', 'media'),
    ('single', '加載圖片（單張）', '圖片', '加入一張圖片', 'image-single'),
    ('image-input', 'ComfyUI 圖片輸入', '圖片', '將圖片送到工作流的 LoadImage', 'image-single'),
    ('preview', '預覽圖片', '圖片', '查看接入的圖片結果', 'scan-eye'),
    ('schedule', '預排程', '執行', '安排並管理等待項目', 'clock'),
    ('stage', 'Stage／執行階段', '執行', '選擇工作流並串接執行階段', 'workflow'),
)


class FunctionDetails(QFrame):
    """Stable text slots at the current width; the scroll area owns overflow."""
    def __init__(self, canvas, background):
        super().__init__(); self.setObjectName('FunctionDetails')
        detail=QVBoxLayout(self); detail.setContentsMargins(0,4,0,0); detail.setSpacing(10)
        detail.addWidget(label('節點預覽','Subtle'))
        self.preview=ModulePreview(canvas); self.preview.setMinimumWidth(0)
        self.preview.setBackgroundBrush(QColor(background)); detail.addWidget(self.preview,1)
        self.name=label('','Heading',True); self.description=label('','Subtle',True)
        self.kind=label('','Subtle')
        for text in (self.name,self.description,self.kind):detail.addWidget(text)
        self.fit_text()

    def fit_text(self):
        if not hasattr(self,'description'):return
        width=max(1,self.contentsRect().width())
        for text,column,lines in ((self.name,1,1),(self.description,3,2)):
            text.ensurePolished(); metrics=text.fontMetrics()
            height=max(metrics.height()*lines,max(metrics.boundingRect(
                QRect(0,0,width,10000),Qt.TextFlag.TextWordWrap,item[column]).height() for item in FUNCTIONS))+4
            text.setSizePolicy(QSizePolicy.Policy.Ignored,QSizePolicy.Policy.Fixed)
            if text.height()!=height or text.minimumHeight()!=height:text.setFixedHeight(height)

    def resizeEvent(self,event):
        super().resizeEvent(event); self.fit_text()

    def changeEvent(self,event):
        super().changeEvent(event)
        if event.type() in (QEvent.Type.FontChange,QEvent.Type.StyleChange):self.fit_text()


class FunctionPicker(CanvasSheetDialog):
    def __init__(self, canvas, position=None):
        super().__init__(canvas.window); self.canvas = canvas
        self.position = QPointF(position) if position is not None else canvas.view.mapToScene(canvas.view.viewport().rect().center())
        self.owner = (canvas.window.store, canvas.window.state['workspace'])
        self.setWindowTitle('新增功能模組'); self.resize(1000, 900)
        self.category = '全部'; self.added = False; self.actions = canvas.functions.insertion_callbacks(self.position)
        colors=canvas_colors(canvas)
        from .theme import font_pixels
        heading_size=max(16,font_pixels(canvas.window.state['settings']['ui_size']))
        background,line=apply_module_dialog(self,colors,heading_size)
        self.setObjectName('FunctionPicker')
        self.setStyleSheet(self.styleSheet()+f'''
            QLineEdit#FunctionSearch {{background:{colors['surface']};border:1px solid {line};border-radius:8px;padding:10px 12px;}}
            QLineEdit#FunctionSearch:focus {{border-color:{colors['accent']};}}
            QFrame#FunctionDetails {{background:transparent;border:0;}}
            QScrollArea#FunctionDetailScroll, QScrollArea#FunctionDetailScroll > QWidget > QWidget {{background:transparent;border:0;}}
            QFrame#FunctionFooter {{background:transparent;border:0;border-top:1px solid {line};}}
        ''')
        surface=self.findChild(QFrame,'DialogSurface')
        title_icon=QLabel();title_icon.setPixmap(icon('package',colors['secondary']).pixmap(18,18))
        surface.layout().itemAt(0).layout().insertWidget(0,title_icon)
        self.search = QLineEdit(); self.search.setObjectName('FunctionSearch')
        self.search.setPlaceholderText('搜尋功能模組…'); self.search.setClearButtonEnabled(True)
        self.search.addAction(icon('search',colors['secondary']),QLineEdit.ActionPosition.LeadingPosition)
        self.search.setAccessibleName('搜尋功能模組'); self.body.addWidget(self.search)
        filters = row(); self.filters = {}
        for category in ('全部', *dict.fromkeys(item[2] for item in FUNCTIONS)):
            control = button(category, lambda checked=False, c=category:self.set_category(c), 'Quiet')
            control.setCheckable(True); self.filters[category] = control; filters.addWidget(control)
            control.setStyleSheet(f'QPushButton {{background:transparent;border:0;border-radius:7px;padding:7px 12px;color:{colors["secondary"]};}} QPushButton:hover {{background:{colors["hover"]};}} QPushButton:checked {{background:{colors["surface"]};color:{colors["text"]};}}')
        filters.addStretch()
        self.more = button('', self.more_tools, 'Quiet'); self.more.setIcon(icon('more-horizontal'))
        self.more.setAccessibleName('更多工具'); self.more.setToolTip('更多工具'); filters.addWidget(self.more)
        self.body.addLayout(filters)
        columns = QHBoxLayout(); columns.setSpacing(18)
        self.list = QListWidget(); self.list.setObjectName('FunctionList');self.list.setProperty('moduleChoices',True)
        self.list.setAccessibleName('功能模組'); self.list.setMinimumWidth(220); self.list.setIconSize(QSize(18,18))
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setSpacing(2); columns.addWidget(self.list, 4)
        panel=FunctionDetails(canvas,background)
        self.preview=panel.preview; self.name=panel.name; self.description=panel.description; self.kind=panel.kind
        self.details=QScrollArea(); self.details.setObjectName('FunctionDetailScroll')
        self.details.setFrameShape(QFrame.Shape.NoFrame); self.details.setWidgetResizable(True)
        self.details.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.details.setWidget(panel); columns.addWidget(self.details,5); self.body.addLayout(columns,1)
        self.empty = label('沒有符合的功能模組', 'Subtle'); self.empty.hide(); self.body.addWidget(self.empty)
        self.add_button = button('新增模組', self.insert_current, 'Primary'); self.add_button.setIcon(icon('plus','on-accent'))
        footer=QFrame();footer.setObjectName('FunctionFooter'); actions=QHBoxLayout(footer)
        actions.setContentsMargins(0,14,0,0); actions.addStretch();actions.addWidget(self.add_button)
        self.body.addWidget(footer); style_action(self.add_button,colors,primary=True)
        for control in self.findChildren(QPushButton):control.setAutoDefault(False)
        self.list.currentItemChanged.connect(self.select_current)
        self.list.setMouseTracking(True)
        self.list.itemEntered.connect(self.list.setCurrentItem)
        self.list.itemClicked.connect(self.insert_current)
        self.list.itemActivated.connect(self.insert_current)
        self.search.textChanged.connect(self.filter_items); self.search.returnPressed.connect(self.insert_current)
        self.set_category('全部'); self.search.setFocus()

    def set_category(self, category):
        self.category = category
        for name, control in self.filters.items():control.setChecked(name == category)
        self.filter_items()

    def current_id(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def filter_items(self):
        selected = self.current_id(); query = self.search.text().strip().casefold()
        self.list.blockSignals(True); self.list.clear()
        for ident, title, category, description, icon_name in FUNCTIONS:
            if self.category != '全部' and self.category != category:continue
            if query and query not in (title+' '+description).casefold():continue
            item = QListWidgetItem(icon(icon_name,canvas_colors(self.canvas)['secondary'],18), title); item.setToolTip(description)
            item.setSizeHint(QSize(0,max(42,self.list.fontMetrics().height()+16))); item.setData(Qt.ItemDataRole.UserRole,ident); self.list.addItem(item)
            if ident == selected:self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():self.list.setCurrentRow(0)
        self.list.blockSignals(False); self.empty.setVisible(not self.list.count()); self.select_current()

    def select_current(self, *_):
        ident = self.current_id(); value = next((item for item in FUNCTIONS if item[0] == ident),None)
        self.add_button.setEnabled(value is not None)
        self.name.setText(value[1] if value else ''); self.description.setText(value[3] if value else '')
        self.kind.setText(value[2]+'模組' if value else '')
        self.preview.show_kind(ident)

    def insert_current(self, *_):
        ident = self.current_id()
        if not ident or self.added:return
        if self.owner != (self.canvas.window.store,self.canvas.window.state['workspace']):self.reject();return
        self.added = True; self.accept(); self.actions[ident]()

    def more_tools(self):
        menu = RoundMenu(self)
        def migrate():
            if self.owner == (self.canvas.window.store,self.canvas.window.state['workspace']):
                self.accept(); self.canvas.migrate_stages()
        menu.addAction('依現有綁定補建 Stage',migrate); menu.open_for(self.more)
