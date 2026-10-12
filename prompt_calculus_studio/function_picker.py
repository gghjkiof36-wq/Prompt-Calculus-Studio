"""Searchable Canvas function chooser; text-canvas material picking stays separate."""
from PySide6.QtCore import Qt, QSize, QPointF
from PySide6.QtWidgets import QLineEdit, QListWidget, QListWidgetItem, QHBoxLayout, QVBoxLayout, QFrame, QPushButton
from .widgets import StudioDialog, label, button, row, RoundMenu
from .module_preview import ModulePreview
from .module_chrome import style_action
from .canvas_items import canvas_colors
from .ui_icons import icon


FUNCTIONS = (
    ('canvas', '文字畫布', '文字', '放置並組合文字模組', 'canvas'),
    ('output', 'Prompt 控制', '文字', '調整來源順序與完整 Prompt', 'copy'),
    ('clip', 'CLIP 輸入', '文字', '將文字送到工作流的指定欄位', 'workflow'),
    ('reader', '讀取文字', '文字', '選擇並讀取 Stage 的文字結果', 'list'),
    ('source', '圖片來源', '圖片', '提供圖片、整批圖片與附加提示詞', 'media'),
    ('single', '加載圖片（單張）', '圖片', '加入一張圖片', 'media'),
    ('image-input', 'ComfyUI 圖片輸入', '圖片', '將圖片送到工作流的 LoadImage', 'media'),
    ('preview', '預覽圖片', '圖片', '查看接入的圖片結果', 'media'),
    ('schedule', '預排程', '執行', '安排並管理等待項目', 'clock'),
    ('stage', 'Stage／執行階段', '執行', '選擇工作流並串接執行階段', 'workflow'),
)


class FunctionPicker(StudioDialog):
    def __init__(self, canvas, position=None):
        super().__init__(canvas.window); self.canvas = canvas
        self.position = QPointF(position) if position is not None else canvas.view.mapToScene(canvas.view.viewport().rect().center())
        self.owner = (canvas.window.store, canvas.window.state['workspace'])
        self.setWindowTitle('新增功能模組'); self.set_outside_dismiss(); self.resize(780, 660)
        self.category = '全部'; self.added = False; self.actions = canvas.functions.insertion_callbacks(self.position)
        colors=canvas_colors(canvas)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋功能模組'); self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('搜尋功能模組'); self.body.addWidget(self.search)
        filters = row(); self.filters = {}
        for category in ('全部', *dict.fromkeys(item[2] for item in FUNCTIONS)):
            control = button(category, lambda checked=False, c=category:self.set_category(c), 'Quiet')
            control.setCheckable(True); self.filters[category] = control; filters.addWidget(control)
            control.setStyleSheet(f'QPushButton {{background:transparent;border:1px solid transparent;border-radius:7px;padding:6px 12px;}} QPushButton:hover {{background:{colors["hover"]};}} QPushButton:checked {{background:{colors["selected"]};border-color:{colors["line"]};color:{colors["text"]};}}')
        filters.addStretch()
        self.more = button('', self.more_tools, 'Quiet'); self.more.setIcon(icon('more-horizontal'))
        self.more.setAccessibleName('更多工具'); self.more.setToolTip('更多工具'); filters.addWidget(self.more)
        self.body.addLayout(filters)
        columns = QHBoxLayout(); columns.setSpacing(20)
        self.list = QListWidget(); self.list.setAccessibleName('功能模組'); self.list.setMinimumWidth(260)
        self.list.setSpacing(4); columns.addWidget(self.list, 5)
        panel = QFrame(); panel.setObjectName('InsetPanel'); detail = QVBoxLayout(panel); detail.setContentsMargins(14,14,14,14)
        detail.addWidget(label('節點預覽', 'Subtle'))
        self.preview = ModulePreview(canvas); detail.addWidget(self.preview,1)
        self.name = label('', 'Heading', True); detail.addWidget(self.name)
        self.description = label('', 'Subtle', True); detail.addWidget(self.description)
        columns.addWidget(panel, 4); self.body.addLayout(columns, 1)
        self.empty = label('沒有符合的功能模組', 'Subtle'); self.empty.hide(); self.body.addWidget(self.empty)
        self.add_button = button('新增', self.insert_current, 'Primary')
        cancel = button('取消', self.reject, 'Quiet')
        self.body.addLayout(row(None,cancel,self.add_button))
        colors = canvas_colors(canvas); style_action(self.add_button,colors,primary=True); style_action(cancel,colors)
        for control in self.findChildren(QPushButton):control.setAutoDefault(False)
        self.list.currentItemChanged.connect(self.select_current)
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
            item = QListWidgetItem(icon(icon_name), title); item.setToolTip(description)
            item.setSizeHint(QSize(0,42)); item.setData(Qt.ItemDataRole.UserRole,ident); self.list.addItem(item)
            if ident == selected:self.list.setCurrentItem(item)
        if self.list.currentRow() < 0 and self.list.count():self.list.setCurrentRow(0)
        self.list.blockSignals(False); self.empty.setVisible(not self.list.count()); self.select_current()

    def select_current(self, *_):
        ident = self.current_id(); value = next((item for item in FUNCTIONS if item[0] == ident),None)
        self.add_button.setEnabled(value is not None)
        self.name.setText(value[1] if value else ''); self.description.setText(value[3] if value else '')
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
