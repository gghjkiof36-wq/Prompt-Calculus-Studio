"""Explore's local chat model library; browsing never starts inference."""
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, QSize, QSignalBlocker, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPen
from PySide6.QtWidgets import (
    QAbstractItemView, QFileDialog, QFrame, QLineEdit, QListWidget,
    QListWidgetItem, QSizePolicy, QSplitter, QStyle, QStyledItemDelegate,
    QVBoxLayout, QWidget,
)

from ..management_reading import READING_MARGIN, READING_NARROW_MARGIN
from ..theme import visual_tokens
from ..ui_icons import icon
from ..widgets import ActionHeader, button, label, panel, row, scrolling
from ..workspace_style import workspace_colors


BUSY_STATES = {'loading', 'generating', 'stopping', 'unloading', 'closed'}


def _file_size(size):
    value = max(0, int(size))
    if value >= 1024 ** 3:
        return f'{value / 1024 ** 3:,.2f} GiB'
    if value >= 1024 ** 2:
        return f'{value / 1024 ** 2:,.1f} MiB'
    return f'{value / 1024:,.1f} KiB' if value >= 1024 else f'{value:,} B'


class _ModelRowDelegate(QStyledItemDelegate):
    """Use the existing model library's typography, spacing and row states."""

    def __init__(self, owner, parent=None):
        super().__init__(parent)
        self.owner = owner

    def sizeHint(self, option, index):
        return QSize(240, max(104, option.fontMetrics.lineSpacing() * 3 + 30))

    def paint(self, painter, option, index):
        record = index.data(Qt.ItemDataRole.UserRole) or {}
        tokens = self.owner.tokens
        rect = QRectF(option.rect.adjusted(1, 2, -7, -2))
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        if selected or hovered:
            painter.setBrush(QColor(tokens['selected'] if selected else tokens['hover']))
            painter.drawRoundedRect(rect, 10, 10)
        glyph = QRectF(rect.left() + 14, rect.center().y() - 15, 30, 30)
        icon('package', tokens['secondary']).paint(painter, glyph.toRect())
        text_rect = QRectF(glyph.right() + 16, rect.top() + 12,
                          max(0, rect.right() - glyph.right() - 30), rect.height() - 24)
        lines = [str(record.get('name', '模型')),
                 ' · '.join(filter(None, ('GGUF', _file_size(record.get('size', 0)), record.get('_status', '')))),
                 str(record.get('path', ''))]
        height = option.fontMetrics.lineSpacing()
        top = text_rect.center().y() - height * len(lines) / 2
        for number, text in enumerate(lines):
            font = QFont(option.font)
            if number == 0:
                font.setWeight(QFont.Weight.DemiBold)
            painter.setFont(font)
            painter.setPen(QColor(tokens['text'] if number == 0 else tokens['secondary']))
            elide = Qt.TextElideMode.ElideMiddle if number == 2 else Qt.TextElideMode.ElideRight
            painter.drawText(
                QRectF(text_rect.left(), top + number * height, text_rect.width(), height),
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                painter.fontMetrics().elidedText(text, elide, int(text_rect.width())),
            )
        if option.state & QStyle.StateFlag.State_HasFocus:
            pen = QPen(QColor(tokens['accent_outline']), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(rect.adjusted(1, 1, -1, -1), 10, 10)
        elif not selected:
            pen = QPen(QColor(tokens['divider']), 1)
            pen.setCosmetic(True)
            painter.setPen(pen)
            painter.drawLine(QPointF(rect.left() + 14, rect.bottom()),
                             QPointF(rect.right() - 14, rect.bottom()))
        painter.restore()


class LocalModelPage(QWidget):
    """Manage registered GGUF files using the application's shared service."""

    createChatRequested = Signal()

    def __init__(self, service, settings, parent=None):
        super().__init__(parent)
        self.service = service
        self.tokens = visual_tokens(settings)
        self._error = ''
        self._models = {}
        self._viewed_model = ''
        self._list_signature = None
        self.setObjectName('LocalModelPage')
        self.outer = QVBoxLayout(self)
        self.outer.setContentsMargins(*([READING_MARGIN] * 4))
        self.outer.setSpacing(18)
        self.count = label('', 'Subtle')
        self.import_button = button('加入模型…', self.import_model, 'Primary')
        self.import_button.setIcon(icon('plus', color='on-accent'))
        self.outer.addWidget(ActionHeader(label('對話模型', 'DialogTitle'), self.count, self.import_button))
        self.outer.addWidget(label('加入電腦中的 GGUF 模型，原檔會留在原本位置。', 'Subtle', True))

        engine, engine_layout = panel('LocalModelEngine')
        engine_layout.setContentsMargins(18, 14, 18, 14)
        engine_layout.setSpacing(6)
        self.engine_status = label('', 'Heading', True)
        self.engine_button = button('選擇引擎…', self.choose_engine, 'Quiet')
        engine_layout.addWidget(ActionHeader(self.engine_status, self.engine_button))
        self.engine_hint = label('', 'Subtle', True)
        engine_layout.addWidget(self.engine_hint)
        self.outer.addWidget(engine)
        self.error_label = label('', 'LocalModelError', True)
        self.error_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.error_label.setAccessibleName('模型操作錯誤')
        self.outer.addWidget(self.error_label)

        self.search = QLineEdit()
        self.search.setPlaceholderText('搜尋模型名稱或來源位置')
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName('搜尋對話模型')
        self.search.textChanged.connect(self.refresh)
        self.outer.addWidget(self.search)
        self.split = QSplitter()
        self.split.setChildrenCollapsible(False)
        self.split.setHandleWidth(24)
        self.list = QListWidget()
        self.list.setObjectName('LocalModelList')
        self.list.setAccessibleName('本機對話模型清單')
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.list.setMouseTracking(True)
        self.list.setSpacing(2)
        self.list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.list.setMinimumSize(0, 150)
        self.list.setItemDelegate(_ModelRowDelegate(self, self.list))
        self.list.currentItemChanged.connect(self._view_model)
        self.split.addWidget(self.list)

        details, detail_layout = panel('LocalModelDetails')
        detail_layout.setContentsMargins(22, 22, 22, 22)
        detail_layout.setSpacing(16)
        self.model_name = label('', 'Heading', True)
        self.model_size = label('', 'Subtle')
        self.model_state = label('', 'Subtle', True)
        detail_layout.addWidget(self.model_name)
        detail_layout.addWidget(self.model_size)
        detail_layout.addWidget(self.model_state)
        detail_layout.addWidget(label('來源位置', 'Subtle'))
        self.model_path = QLineEdit()
        self.model_path.setReadOnly(True)
        self.model_path.setAccessibleName('模型來源位置')
        self.model_path.setMinimumWidth(0)
        detail_layout.addWidget(self.model_path)
        self.select_button = button('選用此模型', self.select_model)
        self.load_button = button('載入模型', self.load_model)
        self.unload_button = button('卸載目前模型', self.unload_model, 'Quiet')
        detail_layout.addWidget(self.select_button)
        detail_layout.addLayout(row(self.load_button, self.unload_button))
        self.action_hint = label('', 'Subtle', True)
        detail_layout.addWidget(self.action_hint)
        detail_layout.addStretch(1)
        self.chat_button = button('開新對話', self.create_chat, 'Primary')
        detail_layout.addWidget(self.chat_button)
        self.details = scrolling(details)
        self.details.setObjectName('LocalModelDetailScroll')
        self.details.setMinimumSize(0, 170)
        self.details.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.split.addWidget(self.details)
        self.split.setStretchFactor(0, 3)
        self.split.setStretchFactor(1, 2)
        self.outer.addWidget(self.split, 1)

        empty_container = QWidget()
        empty_layout = QVBoxLayout(empty_container)
        empty_layout.setContentsMargins(0, 24, 0, 24)
        empty_layout.addStretch()
        self.empty_title = label('加入第一個對話模型', 'Heading', True)
        self.empty_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_hint = label('選擇已下載的單一 GGUF 檔案。加入後可在這裡選用與載入。', 'Subtle', True)
        self.empty_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty_layout.addWidget(self.empty_title)
        empty_layout.addWidget(self.empty_hint)
        self.empty_button = button('加入 GGUF 模型…', self._empty_action, 'Primary')
        empty_layout.addWidget(self.empty_button, 0, Qt.AlignmentFlag.AlignHCenter)
        empty_layout.addStretch()
        empty_layout.setSpacing(16)
        self.empty = scrolling(empty_container)
        self.empty.setObjectName('LocalModelEmpty')
        self.outer.addWidget(self.empty, 1)
        self.service.changed.connect(self.refresh)
        self.apply_appearance(settings)
        self.refresh()

    def _status_for(self, model_id):
        states = []
        if model_id == self.service.selected_model:
            states.append('已選用')
            if self.service.state == 'loading':
                states.append('載入中')
        if model_id == self.service.loaded_model:
            states.append({'generating': '回覆中', 'unloading': '卸載中',
                           'stopping': '停止中'}.get(self.service.state, '已載入'))
        return ' · '.join(states)

    def refresh(self, *_args):
        records = self.service.models
        self._models = {record['id']: record for record in records}
        query = self.search.text().strip().casefold()
        filtered = [record for record in records if not query or
                    query in (record['name'] + '\n' + record['path']).casefold()]
        signature = [(record['id'], record['name'], record['path'], record['size'],
                      self._status_for(record['id'])) for record in filtered]
        if signature != self._list_signature:
            current = self._viewed_model
            position = self.list.verticalScrollBar().value()
            with QSignalBlocker(self.list):
                self.list.clear()
                selected_item = None
                for record in filtered:
                    display = dict(record, _status=self._status_for(record['id']))
                    description = ' · '.join(filter(None, (_file_size(record['size']), display['_status'])))
                    item = QListWidgetItem('\n'.join((record['name'], description, record['path'])))
                    item.setData(Qt.ItemDataRole.UserRole, display)
                    item.setToolTip(record['name'] + '\n' + record['path'])
                    self.list.addItem(item)
                    if record['id'] == current:
                        selected_item = item
                if selected_item is None:
                    selected_item = next((self.list.item(index) for index in range(self.list.count())
                                          if self.list.item(index).data(Qt.ItemDataRole.UserRole)['id'] ==
                                          self.service.selected_model), None)
                if selected_item is None and self.list.count():
                    selected_item = self.list.item(0)
                if selected_item is not None:
                    self.list.setCurrentItem(selected_item)
            self.list.verticalScrollBar().setValue(position)
            self._viewed_model = selected_item.data(Qt.ItemDataRole.UserRole)['id'] if selected_item else ''
            self._list_signature = signature
        self.count.setText(f'{len(filtered)} / {len(records)} 個模型' if query else f'{len(records)} 個模型')
        self.search.setVisible(bool(records))
        self.split.setVisible(bool(filtered))
        self.empty.setVisible(not filtered)
        self.import_button.setVisible(bool(records))
        self.empty_title.setText('找不到相符的模型' if records else '加入第一個對話模型')
        self.empty_hint.setText('換個名稱或清除搜尋，即可查看全部模型。' if records else
                                '選擇已下載的單一 GGUF 檔案。加入後可在這裡選用與載入。')
        self.empty_button.setText('清除搜尋' if records else '加入 GGUF 模型…')
        locked = self.service.state in BUSY_STATES
        self.import_button.setEnabled(not locked)
        self.empty_button.setEnabled(bool(records) or not locked)
        self.engine_button.setEnabled(not locked)
        self._refresh_engine()
        self._refresh_details()
        self.error_label.setText(self._error or self.service.error or '')
        self.error_label.setVisible(bool(self.error_label.text()))

    def _view_model(self, item, _previous):
        # A row click is only inspection. The application model selection and
        # runtime change exclusively in the explicit actions below.
        self._viewed_model = item.data(Qt.ItemDataRole.UserRole)['id'] if item else ''
        self._refresh_details()

    def _refresh_engine(self):
        state = self.service.state
        loaded = self._models.get(self.service.loaded_model, {}).get('name', '目前模型')
        selected = self._models.get(self.service.selected_model, {}).get('name', '所選模型')
        headings = {
            'unavailable': '尚未準備本機引擎', 'unloaded': '模型尚未載入',
            'loading': '正在載入 ' + selected, 'ready': '已載入 ' + loaded,
            'generating': loaded + ' 正在回覆', 'stopping': '正在停止操作…',
            'unloading': '正在卸載 ' + loaded, 'error': '本機引擎需要處理',
            'closed': '本機引擎已關閉',
        }
        self.engine_status.setText(headings.get(state, '本機引擎尚未就緒'))
        if state == 'unavailable':
            hint = '可先加入模型。準備好 llama-server.exe 後，按「選擇引擎」。'
        elif state == 'generating':
            hint = '目前的對話仍在生成；要變更模型，請先回到對話停止回覆。'
        elif state == 'ready' and self.service.needs_reload:
            hint = '所選模型或載入設定已變更，請手動重新載入後再使用。'
        elif state in {'loading', 'stopping', 'unloading'}:
            hint = '操作完成後即可繼續使用模型。'
        else:
            hint = '選用模型後，按「載入模型」才會使用電腦資源。'
        self.engine_hint.setText(hint)
        configured = self.service.settings.get('runtime_path', '')
        self.engine_button.setText('更換引擎…' if configured else '選擇引擎…')
        self.engine_button.setToolTip(configured or '選擇電腦中的 llama-server.exe')

    def _refresh_details(self):
        record = self._models.get(self._viewed_model)
        self.details.setVisible(record is not None)
        if record is None:
            return
        state = self.service.state
        locked = state in BUSY_STATES
        selected = record['id'] == self.service.selected_model
        loaded = record['id'] == self.service.loaded_model
        self.model_name.setText(record['name'])
        self.model_size.setText('GGUF · ' + _file_size(record['size']))
        self.model_state.setText(self._status_for(record['id']) or '尚未選用')
        if self.model_path.text() != record['path']:
            self.model_path.setText(record['path'])
            self.model_path.setCursorPosition(0)
        self.model_path.setToolTip(record['path'])
        self.select_button.setText('已選用此模型' if selected else '選用此模型')
        self.select_button.setEnabled(not locked and not selected)
        self.load_button.setText('重新載入' if loaded else '載入模型')
        self.load_button.setEnabled(not locked and state != 'unavailable')
        self.unload_button.setText('取消載入' if state == 'loading' else '卸載目前模型')
        self.unload_button.setEnabled(state == 'loading' or
                                      bool(self.service.loaded_model and state not in BUSY_STATES))
        self.chat_button.setEnabled(state != 'closed' and (selected or not locked))
        self.chat_button.setText('開新對話' if selected else '選用並開新對話')
        self.action_hint.setText('可先開新對話準備內容，載入模型後再送出。' if not loaded else
                                 '開新對話會使用所選模型；原本的對話會保留。')

    def _action(self, callback):
        self._error = ''
        try:
            result = callback()
        except (ValueError, OSError, RuntimeError) as exc:
            self._error = str(exc)
            result = False
        self.refresh()
        return result

    def import_model(self):
        filename, _ = QFileDialog.getOpenFileName(self, '加入已下載的對話模型', '', 'GGUF 模型 (*.gguf)')
        if not filename:
            return
        record = self._action(lambda: self.service.register_model(filename))
        if isinstance(record, dict):
            self._viewed_model = record['id']
            self._list_signature = None
            self.search.clear()
            self.refresh()

    def choose_engine(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, '選擇本機引擎', self.service.settings.get('runtime_path', ''),
            '本機引擎 (llama-server.exe);;執行檔 (*.exe)')
        if filename:
            self._action(lambda: self.service.configure({'runtime_path': str(Path(filename))}))

    def select_model(self):
        if self._viewed_model and self.select_button.isEnabled():
            self._action(lambda: self.service.select_model(self._viewed_model))

    def load_model(self):
        if not self._viewed_model or not self.load_button.isEnabled():
            return
        model_id = self._viewed_model

        def load():
            if model_id != self.service.selected_model and not self.service.select_model(model_id):
                return False
            return self.service.load()

        self._action(load)

    def unload_model(self):
        if self.unload_button.isEnabled():
            self._action(self.service.unload)

    def create_chat(self):
        if not self._viewed_model or not self.chat_button.isEnabled():
            return
        if self._viewed_model != self.service.selected_model:
            if not self._action(lambda: self.service.select_model(self._viewed_model)):
                return
        self.createChatRequested.emit()

    def _empty_action(self):
        if self._models:
            self.search.clear()
        else:
            self.import_model()

    def prepare_workspace_show(self):
        self.refresh()
        self._adapt_layout()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'split'):
            self._adapt_layout()

    def _adapt_layout(self):
        orientation = Qt.Orientation.Vertical if self.width() < 820 else Qt.Orientation.Horizontal
        if self.split.orientation() != orientation:
            self.split.setOrientation(orientation)
            self.split.setSizes([300, 300] if orientation == Qt.Orientation.Vertical else [600, 380])
        margin = READING_NARROW_MARGIN if self.width() < 720 else READING_MARGIN
        self.outer.setContentsMargins(margin, margin, margin, margin)

    def apply_appearance(self, settings):
        self.tokens = visual_tokens(settings)
        tokens = self.tokens
        surface = workspace_colors(settings)['content']
        self.setStyleSheet(f'''
            QWidget#LocalModelPage {{ background:{surface}; }}
            QListWidget#LocalModelList {{ background:transparent; border:0; outline:0; }}
            QScrollArea#LocalModelDetailScroll, QScrollArea#LocalModelEmpty {{ background:transparent; border:0; }}
            QFrame#LocalModelEngine, QFrame#LocalModelDetails {{ background:{tokens['surface']};
                border:1px solid {tokens['divider']}; border-radius:12px; }}
            QLabel#LocalModelError {{ color:{tokens['error']}; }}
        ''')
        self.import_button.setIcon(icon('plus', color='on-accent'))
        self.list.viewport().update()
