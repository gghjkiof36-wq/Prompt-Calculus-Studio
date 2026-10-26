"""A compact, centered local model library. Browsing never starts inference."""
from pathlib import Path
import os

from PySide6.QtCore import QProcess, QSize, QSignalBlocker, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QHBoxLayout, QLineEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QStackedWidget,
    QVBoxLayout, QWidget,
)
from ..management_reading import READING_CONTENT_WIDTH, READING_MARGIN, READING_NARROW_MARGIN
from ..theme import visual_tokens
from ..ui_icons import icon
from ..widgets import ActionHeader, ComboBox, ElidedLabel, RoundMenu, button, label, row, scrolling
from ..workspace_style import workspace_colors

BUSY_STATES = {'loading', 'generating', 'stopping', 'unloading', 'closed'}


def _file_size(size):
    value = max(0, int(size))
    if value >= 1024 ** 3:
        return f'{value / 1024 ** 3:,.2f} GiB'
    if value >= 1024 ** 2:
        return f'{value / 1024 ** 2:,.1f} MiB'
    return f'{value / 1024:,.1f} KiB' if value >= 1024 else f'{value:,} B'


class _ModelRow(QFrame):
    """Stable compact row; browsing has no load side effect."""
    def __init__(self, owner, record):
        super().__init__(); self.owner = owner; self.model_id = record['id']
        self._meta = None; self._icon_color = None
        self.setObjectName('LocalModelRow'); self.setFixedHeight(80)
        layout = QHBoxLayout(self); layout.setContentsMargins(12, 12, 12, 12); layout.setSpacing(12)
        self.glyph = label(''); self.glyph.setFixedSize(22, 22); layout.addWidget(self.glyph)
        text = QVBoxLayout(); text.setContentsMargins(0, 0, 0, 0); text.setSpacing(4)
        self.name = ElidedLabel(record['name']); self.name.setObjectName('Heading')
        self.name.setMinimumWidth(0); self.name.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.name.setToolTip(record['name']); self.name.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.meta = ElidedLabel(); self.meta.setObjectName('Subtle'); self.meta.setMinimumWidth(0)
        self.meta.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.meta.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        text.addWidget(self.name); text.addWidget(self.meta); layout.addLayout(text, 1)
        self.more = button('', lambda: owner.model_menu(self.model_id, self.more), 'Quiet')
        self.more.setFixedSize(32, 32); self.more.setToolTip('模型操作'); self.more.setAccessibleName(record['name'] + '：模型操作')
        self.gear = button('', lambda: owner.open_preset(self.model_id), 'Quiet')
        self.gear.setFixedSize(32, 32); self.gear.setToolTip('此模型的生成設定'); self.gear.setAccessibleName(record['name'] + '：生成設定')
        layout.addWidget(self.more); layout.addWidget(self.gear); self.refresh(record)

    def refresh(self, record):
        text = ' · '.join(filter(None, ('GGUF', _file_size(record['size']), self.owner._status_for(self.model_id))))
        metadata = (text, record['path'])
        if metadata != self._meta:
            self.meta.setText(text); self.meta.setToolTip(text + '\n' + record['path'])
            self._meta = metadata
        color = self.owner.tokens['secondary']
        if color != self._icon_color:
            self.glyph.setPixmap(icon('package', color).pixmap(22, 22))
            self.more.setIcon(icon('more-horizontal', color)); self.gear.setIcon(icon('gear', color))
            self._icon_color = color


class LocalModelPage(QWidget):
    createChatRequested = Signal()

    def __init__(self, service, settings, parent=None, *, workspace=None):
        super().__init__(parent)
        self.service = service; self.workspace = workspace; self.tokens = visual_tokens(settings)
        self._error = ''; self._models = {}; self._rows = {}; self._presets = {}; self._list_signature = None
        self.setObjectName('LocalModelPage')
        self.outer = QHBoxLayout(self); self.outer.setSpacing(0)
        self.column = QWidget(); self.column.setMaximumWidth(READING_CONTENT_WIDTH); self.column.setMinimumWidth(0)
        self.column.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.outer.addStretch(); self.outer.addWidget(self.column, 1); self.outer.addStretch()
        body = QVBoxLayout(self.column); body.setContentsMargins(0, 0, 0, 0); body.setSpacing(18)
        self.heading = ElidedLabel('我的模型'); self.heading.setObjectName('DialogTitle'); self.heading.setMinimumWidth(0)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.back = button('模型庫', self.show_library, 'Quiet'); self.back.setIcon(icon('back'))
        self.engine_button = button('引擎設定', self.open_engine, 'Quiet')
        self.instructions_button = button('對話指示', self.open_instructions, 'Quiet')
        header = QHBoxLayout(); header.setSpacing(16)
        header.addWidget(self.back); header.addWidget(self.heading, 1)
        header.addWidget(self.instructions_button); header.addWidget(self.engine_button)
        for control in (self.back, self.instructions_button, self.engine_button):
            control.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
        body.addLayout(header)
        self.stack = QStackedWidget(); body.addWidget(self.stack, 1)
        self.library = QWidget(); self.library_layout = QVBoxLayout(self.library)
        self.library_layout.setContentsMargins(0, 0, 0, 0); self.library_layout.setSpacing(16)
        self.stack.addWidget(self.library)
        from .chat_settings import ModelSettingsPanel, ConversationInstructionsPanel
        self.engine_settings = ModelSettingsPanel(service)
        self.engine_page = scrolling(self.engine_settings); self.stack.addWidget(self.engine_page)
        self.instructions = ConversationInstructionsPanel(workspace) if workspace else None
        self.instructions_page = None
        if self.instructions is not None:
            self.instructions_page = scrolling(self.instructions); self.stack.addWidget(self.instructions_page)

        self.folder_row = QFrame(); self.folder_row.setObjectName('ModelFolderRow')
        folder_layout = QHBoxLayout(self.folder_row); folder_layout.setContentsMargins(14, 12, 14, 12); folder_layout.setSpacing(12)
        self.folder_icon = label(''); self.folder_icon.setFixedSize(24, 24); folder_layout.addWidget(self.folder_icon)
        folder_text = QVBoxLayout(); folder_text.setSpacing(3); folder_text.setContentsMargins(0, 0, 0, 0)
        folder_text.addWidget(label('模型資料夾'))
        self.folder_path = ElidedLabel(); self.folder_path.setObjectName('Subtle'); self.folder_path.setMinimumWidth(0)
        self.folder_path.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        folder_text.addWidget(self.folder_path); folder_layout.addLayout(folder_text, 1)
        self.folder_change = button('更改', self.choose_folder, 'Quiet'); folder_layout.addWidget(self.folder_change)
        self.folder_change.setToolTip('更改掃描位置；已加入的模型會保留')
        self.folder_more = button('', self.folder_menu, 'Quiet'); self.folder_more.setFixedSize(32, 32)
        self.folder_more.setToolTip('模型資料夾選單'); self.folder_more.setAccessibleName('模型資料夾選單'); folder_layout.addWidget(self.folder_more)
        self.library_layout.addWidget(self.folder_row)
        self.search = QLineEdit(); self.search.setPlaceholderText('搜尋模型…'); self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('搜尋本地模型'); self.search.textChanged.connect(self.refresh); self.library_layout.addWidget(self.search)
        self.count = label('', 'Subtle')
        self.sort = ComboBox(); self.sort.setAccessibleName('模型排序')
        for title, key in (('名稱', 'name'), ('最新', 'latest'), ('大小', 'size')): self.sort.addItem(title, key)
        self.sort.setItemData(1, '依模型檔案修改時間，由新到舊', Qt.ItemDataRole.ToolTipRole)
        self.sort.setItemData(2, '由大到小', Qt.ItemDataRole.ToolTipRole)
        self.sort.currentIndexChanged.connect(self.refresh)
        self.import_button = button('加入模型…', self.import_model, 'Quiet')
        self.library_layout.addWidget(ActionHeader(self.count, self.import_button, self.sort))
        self.status = label('', 'Subtle', True); self.status.setAccessibleName('模型資料夾狀態'); self.library_layout.addWidget(self.status)
        self.error_label = label('', 'LocalModelError', True); self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.library_layout.addWidget(self.error_label)
        self.list = QListWidget(); self.list.setObjectName('LocalModelList'); self.list.setAccessibleName('我的模型清單')
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection); self.list.setMouseTracking(True)
        self.list.setMinimumSize(0, 100); self.list.setSpacing(3)
        self.list.setResizeMode(QListWidget.ResizeMode.Adjust); self.list.setUniformItemSizes(True)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.library_layout.addWidget(self.list, 1)
        self.empty = QWidget(); empty_layout = QVBoxLayout(self.empty); empty_layout.addStretch()
        self.empty_title = label('', 'Subtle', True); self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title); empty_layout.addStretch(2); self.library_layout.addWidget(self.empty, 1)
        self.service.changed.connect(self.refresh)
        self.apply_appearance(settings); self.refresh(); self.show_library(); self._adapt_layout()

    def _status_for(self, model_id):
        states = []
        if model_id == self.service.selected_model: states.append('已選用')
        if model_id == self.service.loaded_model:
            states.append({'generating':'回覆中','unloading':'釋放中','stopping':'停止中'}.get(self.service.state,'已準備'))
        return ' · '.join(states)

    def refresh(self, *_args):
        records = self.service.models; self._models = {record['id']: record for record in records}
        query = self.search.text().strip().casefold()
        filtered = [record for record in records if not query or query in (record['name'] + '\n' + record['path']).casefold()]
        order = self.sort.currentData()
        if order == 'latest': filtered.sort(key=lambda record: (-record.get('mtime_ns',0), record['name'].casefold(), record['id']))
        elif order == 'size': filtered.sort(key=lambda record: (-record['size'], record['name'].casefold(), record['id']))
        else: filtered.sort(key=lambda record: (record['name'].casefold(), record['id']))
        signature = [(record['id'],record['name'],record['path'],record['size']) for record in filtered]
        if signature != self._list_signature:
            position = self.list.verticalScrollBar().value()
            with QSignalBlocker(self.list):
                self.list.clear(); self._rows = {}
                for record in filtered:
                    item = QListWidgetItem(); item.setData(Qt.ItemDataRole.UserRole, dict(record))
                    item.setSizeHint(QSize(240, 80))
                    item.setToolTip(record['name'] + '\n' + record['path']); self.list.addItem(item)
                    compact = _ModelRow(self, record); self._rows[record['id']] = compact
                    self.list.setItemWidget(item, compact)
            self.list.verticalScrollBar().setValue(position); self._list_signature = signature
        else:
            # Keep keyboard focus on the same controls when service status changes.
            for model_id, compact in self._rows.items(): compact.refresh(self._models[model_id])
        self.count.setText(f'顯示 {len(filtered)} / {len(records)} 個模型' if query else f'{len(records)} 個模型')
        self.list.setVisible(bool(filtered)); self.empty.setVisible(not filtered)
        self.empty_title.setText('找不到相符的模型' if records else '尚未加入模型')
        self.folder_path.setText(self.service.model_folder); self.folder_path.setToolTip(self.service.model_folder)
        scanning = self.service.model_folder_busy
        self.folder_change.setEnabled(not scanning and self.service.state not in BUSY_STATES)
        self.import_button.setEnabled(self.service.state not in BUSY_STATES)
        message = self.service.model_folder_message if scanning or self.service.model_folder_state in ('error','warning') else ''
        self.status.setText(message); self.status.setVisible(bool(message))
        self.error_label.setText(self._error or self.service.error or '')
        self.error_label.setVisible(bool(self.error_label.text()))

    def _show_page(self, page, title):
        self.stack.setCurrentWidget(page); self.heading.setText(title); self.heading.setToolTip(title)
        library = page is self.library
        self.back.setVisible(not library); self.engine_button.setVisible(library)
        self.instructions_button.setVisible(library and self.instructions is not None and self.instructions.document() is not None)

    def show_library(self):
        self._show_page(self.library, '我的模型')

    def open_engine(self):
        self.engine_settings.refresh(); self._show_page(self.engine_page, '引擎設定')

    def open_instructions(self):
        if self.instructions is not None and self.instructions.bind(getattr(self.workspace, 'local_model_settings_context', None)):
            self._show_page(self.instructions_page, '對話指示')

    def open_preset(self, model_id):
        record = self._models.get(model_id)
        if record is None: return
        if model_id not in self._presets:
            from .chat_settings import ModelPresetPanel
            panel = ModelPresetPanel(self.service, model_id)
            page = scrolling(panel); self._presets[model_id] = (panel, page); self.stack.addWidget(page)
        panel, page = self._presets[model_id]; panel.refresh()
        self._show_page(page, record['name'])

    def model_menu(self, model_id, anchor):
        record = self._models.get(model_id)
        if record is None: return
        menu = RoundMenu(self); selected = model_id == self.service.selected_model
        select = menu.addAction('已選用' if selected else '選用此模型', lambda: self.select_model(model_id))
        select.setEnabled(not selected and self.service.state not in BUSY_STATES)
        chat = menu.addAction('開新對話' if selected else '選用並開新對話', lambda: self.create_chat(model_id))
        chat.setEnabled(self.service.state != 'closed' and (selected or self.service.state not in BUSY_STATES))
        menu.addSeparator(); menu.addAction('在檔案總管中顯示', lambda: self.reveal_model(model_id))
        self._model_menu = menu; menu.open_for(anchor, align_right=True)

    def reveal_model(self, model_id):
        record = self._models.get(model_id)
        if record is None: return False
        target = Path(record['path'])
        if not target.is_file():
            self._error = '模型檔案已搬動或不存在。'; self.refresh(); return False
        if os.name == 'nt':
            explorer = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'explorer.exe'
            if explorer.is_file():
                started, _ = QProcess.startDetached(str(explorer), ['/select,', str(target)])
                if started: return True
        return self.open_folder(target.parent)

    def _action(self, callback):
        self._error = ''
        try: result = callback()
        except (ValueError, OSError, RuntimeError) as exc: self._error = str(exc); result = False
        self.refresh(); return result

    def choose_folder(self):
        path = QFileDialog.getExistingDirectory(self,'選擇模型資料夾',self.service.model_folder)
        if path: self._action(lambda:self.service.set_model_folder(path))

    def folder_menu(self):
        menu = RoundMenu(self)
        if self.service.model_folder_busy:
            menu.addAction('取消掃描',lambda:self._action(self.service.cancel_model_folder_scan))
        else:
            menu.addAction('回復預設資料夾',lambda:self._action(self.service.reset_model_folder))
            menu.addAction('重新整理模型清單',lambda:self._action(self.service.refresh_model_folder))
        menu.addSeparator(); menu.addAction('在檔案總管中開啟',lambda:self.open_folder(self.service.model_folder))
        self._folder_menu = menu; menu.open_for(self.folder_more,align_right=True)

    def open_folder(self, path):
        target = Path(path)
        if not target.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(target))):
            self._error = '無法開啟資料夾，請確認路徑仍然存在。'; self.refresh(); return False
        return True

    def import_model(self):
        filename,_ = QFileDialog.getOpenFileName(self,'加入已下載的模型',self.service.model_folder,'GGUF 模型 (*.gguf)')
        if not filename: return
        record = self._action(lambda:self.service.register_model(filename))
        if isinstance(record,dict):
            self.search.clear(); self.refresh()
            for index in range(self.list.count()):
                item = self.list.item(index)
                if item.data(Qt.ItemDataRole.UserRole)['id'] == record['id']: self.list.setCurrentItem(item); break

    def select_model(self, model_id):
        if model_id in self._models and self.service.state not in BUSY_STATES:
            return self._action(lambda: self.service.select_model(model_id))
        return False

    def create_chat(self, model_id):
        if model_id not in self._models or self.service.state == 'closed': return
        if model_id != self.service.selected_model and not self.select_model(model_id): return
        self.createChatRequested.emit()

    def prepare_workspace_show(self):
        self.engine_settings.refresh()
        if self.instructions is not None:
            self.instructions.bind(getattr(self.workspace, 'local_model_settings_context', None))
        if self.stack.currentWidget() is self.library: self.show_library()
        for panel, _page in self._presets.values(): panel.refresh()
        self.refresh(); self._adapt_layout()

    def resizeEvent(self,event):
        super().resizeEvent(event); self._adapt_layout()

    def _adapt_layout(self):
        margin = READING_NARROW_MARGIN if self.width() < 720 else READING_MARGIN
        self.outer.setContentsMargins(margin,margin,margin,margin)

    def apply_appearance(self,settings):
        self.tokens = visual_tokens(settings); tokens=self.tokens; surface=workspace_colors(settings)['content']
        self.setStyleSheet(f'''QWidget#LocalModelPage {{ background:{surface}; }}
            QListWidget#LocalModelList {{ background:transparent; border:0; outline:0; }}
            QListWidget#LocalModelList::item {{ padding:0; margin:0; border:0; background:transparent; }}
            QFrame#ModelFolderRow {{ background:transparent; border:1px solid {tokens['divider']}; border-radius:10px; }}
            QFrame#LocalModelRow {{ background:{tokens['surface']}; border:1px solid {tokens['divider']}; border-radius:8px; }}
            QLabel#LocalModelError {{ color:{tokens['error']}; }}''')
        self.folder_icon.setPixmap(icon('folder',tokens['secondary']).pixmap(22,22))
        self.folder_more.setIcon(icon('more-horizontal',tokens['secondary']))
        self.import_button.setIcon(icon('plus',tokens['secondary']))
        for model_id, compact in self._rows.items(): compact.refresh(self._models[model_id])
        self.list.viewport().update()
