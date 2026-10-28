"""Embedded model settings and explicitly scoped conversation instructions."""
from pathlib import Path
import weakref
from PySide6.QtWidgets import QDoubleSpinBox, QFileDialog, QFormLayout, QLineEdit, QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget
from ..widgets import ComboBox, button, label, panel, row
from .reasoning import DEFAULT_REASONING


def open_model_settings(workspace, document=None):
    """Carry the originating document explicitly; never guess from active tabs."""
    workspace.local_model_settings_context = ((weakref.ref(document), document.key, document.owner_epoch) if document is not None else None)
    workspace.open_route('explore', 'local_models')


def _integer(value, minimum, maximum, message):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if not minimum <= result <= maximum:
        raise ValueError(message)
    return result


class ModelSettingsPanel(QWidget):
    """Shared engine settings only; generation preferences belong to each model."""
    def __init__(self, service, parent=None):
        super().__init__(parent); self.service = service
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 8, 0, 0); layout.setSpacing(18)
        card, body = panel(); layout.addWidget(card)
        body.addWidget(label('本機引擎', 'Heading'))
        self.runtime_path = QLineEdit(); self.runtime_path.setAccessibleName('本機 llama-server 引擎位置')
        self.runtime_path.setPlaceholderText('留空時使用 PCS 隨附引擎')
        self.runtime_path.setToolTip('選擇 llama-server.exe；所有模型共用，於下次送出時使用。')
        body.addLayout(row(self.runtime_path, button('選擇引擎…', self._pick_runtime)))
        self.idle_minutes = self._spin(0, 1440); self.idle_minutes.setSpecialValueText('不自動釋放'); self.idle_minutes.setSuffix(' 分鐘')
        self.idle_minutes.setAccessibleName('閒置後釋放模型')
        form = self._form(); form.addRow('閒置後釋放模型', self.idle_minutes); body.addLayout(form)
        self.idle_minutes.setToolTip('閒置達到這段時間後釋放模型；0 表示不自動釋放。')
        self.message = label('', 'Subtle', True); self.message.setAccessibleName('引擎設定保存狀態'); layout.addWidget(self.message)
        self.apply_button = button('儲存設定', self.apply, 'Primary')
        self.reload_button = button('重新讀取', self.reload, 'Quiet')
        layout.addLayout(row(None, self.reload_button, self.apply_button)); layout.addStretch(); self.reload()

    @staticmethod
    def _spin(minimum, maximum, step=1):
        result = QSpinBox(); result.setRange(minimum, maximum); result.setSingleStep(step); return result

    @staticmethod
    def _decimal(minimum, maximum, step):
        result = QDoubleSpinBox(); result.setRange(minimum, maximum); result.setDecimals(2); result.setSingleStep(step); return result

    @staticmethod
    def _form():
        result = QFormLayout(); result.setVerticalSpacing(18); result.setHorizontalSpacing(24)
        result.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows); return result

    def values(self):
        return dict(runtime_path=self.runtime_path.text().strip(), idle_minutes=self.idle_minutes.value())

    def _current(self):
        return {key: self.service.settings[key] for key in ('runtime_path', 'idle_minutes')}

    def reload(self):
        values = self._current(); self._snapshot = dict(values)
        self.idle_minutes.setValue(values['idle_minutes']); self.runtime_path.setText(values['runtime_path']); self.message.setText('')

    def refresh(self):
        if self.values() == self._snapshot: self.reload()

    def _pick_runtime(self):
        filename, _ = QFileDialog.getOpenFileName(self, '選擇本機引擎', self.runtime_path.text(), '本機引擎 (llama-server.exe);;執行檔 (*.exe)')
        if filename: self.runtime_path.setText(str(Path(filename)))

    def apply(self):
        try:
            if self._current() != self._snapshot:
                raise ValueError('引擎設定已在另一個視窗變更。請先重新讀取，再調整。')
            if self.service.configure(self.values()) is False:
                raise ValueError(self.service.error or '設定尚未保存，請稍後重試。')
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc)); return False
        self._snapshot = self._current(); self.message.setText('已儲存'); return True


class ModelPresetPanel(QWidget):
    """A staged preset tied to an immutable model ID, never the active selection."""
    def __init__(self, service, model_id, parent=None):
        super().__init__(parent); self.service = service; self.model_id = model_id; self._snapshot = None
        self._reasoning_supported = False
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 8, 0, 0); layout.setSpacing(18)
        prompt_card, prompt_body = panel(); layout.addWidget(prompt_card)
        prompt_body.addWidget(label('提示詞', 'Heading'))
        self.system_prompt = QPlainTextEdit(); self.system_prompt.setAccessibleName('此模型的預設指示'); self.system_prompt.setFixedHeight(130)
        self.system_prompt.setPlaceholderText('例如：使用繁體中文回答，先說結論。')
        prompt_body.addWidget(self.system_prompt)
        self.system_prompt.setToolTip('此模型的預設指示；對話另有指示時，優先使用對話指示。')
        resource_card, resource_body = panel(); layout.addWidget(resource_card)
        resource_body.addWidget(label('上下文與資源', 'Heading'))
        self.context_size = ModelSettingsPanel._spin(256, 1048576, 512)
        self.gpu_layers = ModelSettingsPanel._spin(-1, 999); self.gpu_layers.setSpecialValueText('全部可用層')
        resources = ModelSettingsPanel._form()
        for name, field in (('上下文容量（tokens）', self.context_size), ('GPU 層數', self.gpu_layers)):
            field.setAccessibleName(name); resources.addRow(name, field)
        resource_body.addLayout(resources); self.gpu_layers.setToolTip('0 使用 CPU；全部可用層會盡量使用 GPU。')
        generation_card, generation_body = panel(); layout.addWidget(generation_card)
        generation_body.addWidget(label('生成', 'Heading'))
        self.max_tokens = ModelSettingsPanel._spin(1, 262144, 128)
        self.temperature = ModelSettingsPanel._decimal(0, 2, .1); self.top_p = ModelSettingsPanel._decimal(.01, 1, .05)
        self.seed = QLineEdit(); self.seed.setPlaceholderText('-1：每次隨機')
        generation = ModelSettingsPanel._form()
        for name, field in (('回覆上限（tokens）', self.max_tokens), ('溫度', self.temperature), ('Top P', self.top_p), ('隨機種子', self.seed)):
            field.setAccessibleName(name); generation.addRow(name, field)
        generation_body.addLayout(generation)
        self.thinking_mode = ComboBox()
        for text, value in (('依模型', None), ('開啟', True), ('關閉', False)):
            self.thinking_mode.addItem(text, value)
        self.thinking_mode.setAccessibleName('思考模式')
        self.thinking_budget_mode = ComboBox()
        for text, value in (('依模型', 'default'), ('不限', 'unlimited'), ('自訂', 'custom')):
            self.thinking_budget_mode.addItem(text, value)
        self.thinking_budget_mode.setAccessibleName('思考上限模式')
        self.thinking_budget = ModelSettingsPanel._spin(0, 262144, 128)
        self.thinking_budget.setValue(512); self.thinking_budget.setSuffix(' tokens')
        self.thinking_budget.setAccessibleName('思考上限')
        self.thinking_controls = QWidget()
        thinking = ModelSettingsPanel._form(); thinking.setContentsMargins(0, 0, 0, 0)
        thinking.addRow('思考模式', self.thinking_mode)
        thinking.addRow('思考上限', row(self.thinking_budget_mode, self.thinking_budget))
        self.thinking_controls.setLayout(thinking); generation_body.addWidget(self.thinking_controls)
        self.thinking_mode.currentIndexChanged.connect(self._sync_thinking_controls)
        self.thinking_budget_mode.currentIndexChanged.connect(self._sync_thinking_controls)
        self.max_tokens.setToolTip('回覆上限須小於上下文容量，為輸入保留空間。')
        self.message = label('', 'Subtle', True); self.message.setAccessibleName('模型預設保存狀態'); layout.addWidget(self.message)
        self.apply_button = button('儲存模型設定', self.apply, 'Primary')
        self.reload_button = button('重新讀取', self.reload, 'Quiet')
        self.reset_button = button('回復預設', self.reset, 'Quiet')
        layout.addLayout(row(self.reset_button, None, self.reload_button, self.apply_button)); layout.addStretch(); self.reload()

    def values(self):
        return dict(context_size=self.context_size.value(), max_tokens=self.max_tokens.value(), temperature=self.temperature.value(),
                    top_p=self.top_p.value(), seed=_integer(self.seed.text().strip(), -1, 4294967295, '隨機種子請輸入 -1 到 4294967295 的整數。'),
                    gpu_layers=self.gpu_layers.value())

    def reasoning_values(self):
        enabled = self.thinking_mode.currentData()
        mode = self.thinking_budget_mode.currentData()
        budget = self.thinking_budget.value() if mode == 'custom' else (-1 if mode == 'unlimited' else None)
        return dict(enable_thinking=enabled, reasoning_budget_tokens=None if enabled is False else budget)

    def _sync_thinking_controls(self):
        enabled = self.thinking_mode.currentData() is not False
        self.thinking_budget_mode.setEnabled(enabled)
        self.thinking_budget.setVisible(enabled and self.thinking_budget_mode.currentData() == 'custom')

    def reload(self):
        try: snapshot = self.service.model_preset(self.model_id)
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc)); self.apply_button.setEnabled(False); self.reset_button.setEnabled(False); return False
        self._snapshot = snapshot; values = snapshot['parameters']
        for key in ('context_size', 'max_tokens', 'temperature', 'top_p', 'gpu_layers'): getattr(self, key).setValue(values[key])
        self.seed.setText(str(values['seed'])); self.system_prompt.setPlainText(snapshot['system_prompt'])
        # A running preview may open this lazy page after source updates while
        # its existing service still has the previous preset contract. Do not
        # advertise controls that that service cannot persist or execute.
        self._reasoning_supported = 'reasoning' in snapshot
        self.thinking_controls.setVisible(self._reasoning_supported)
        reasoning = snapshot.get('reasoning', DEFAULT_REASONING)
        self.thinking_mode.setCurrentIndex(0 if reasoning['enable_thinking'] is None else (1 if reasoning['enable_thinking'] else 2))
        budget = reasoning['reasoning_budget_tokens']
        self.thinking_budget_mode.setCurrentIndex(0 if budget is None else (1 if budget == -1 else 2))
        self.thinking_budget.setValue(512 if budget is None or budget < 0 else budget)
        self._sync_thinking_controls()
        self.apply_button.setEnabled(True); self.reset_button.setEnabled(True); self.message.setText(''); return True

    def refresh(self):
        try:
            if self._snapshot is None or (self.values() == self._snapshot['parameters'] and self.system_prompt.toPlainText() == self._snapshot['system_prompt']
                                         and (not self._reasoning_supported or self.reasoning_values() == self._snapshot['reasoning'])):
                self.reload()
        except ValueError: pass  # Keep an unfinished local edit, including an invalid seed.

    def apply(self):
        if self._snapshot is None: return False
        try:
            options = {'reasoning': self.reasoning_values()} if self._reasoning_supported else {}
            if not self.service.configure_model(self.model_id, self.values(), system_prompt=self.system_prompt.toPlainText(),
                                                expected_revision=self._snapshot['revision'], **options):
                raise ValueError(self.service.error or '模型設定尚未保存。')
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc)); return False
        self.reload(); self.message.setText('已儲存'); return True

    def reset(self):
        if self._snapshot is None: return False
        try:
            if not self.service.reset_model_preset(self.model_id, expected_revision=self._snapshot['revision']):
                raise ValueError(self.service.error or '模型設定尚未重置。')
        except (ValueError, OSError, RuntimeError) as exc:
            self.message.setText(str(exc)); return False
        self.reload(); self.message.setText('已回復預設。'); return True


class ConversationInstructionsPanel(QWidget):
    """Instructions belong to one live document, including after navigation."""
    def __init__(self, workspace, parent=None):
        super().__init__(parent)
        self.workspace = workspace; self.context = None; self._snapshot = ''; self._drafts = {}
        layout = QVBoxLayout(self); layout.setContentsMargins(0, 24, 0, 0); layout.setSpacing(18)
        self.heading = label('', 'Heading', True); layout.addWidget(self.heading)
        self.system_prompt = QPlainTextEdit(); self.system_prompt.setAccessibleName('這份對話的上下文指示')
        self.system_prompt.setToolTip('只影響這份對話，從下一次送出開始使用。')
        self.system_prompt.setPlaceholderText('例如：使用繁體中文回答，先說結論，再說明理由。'); self.system_prompt.setMinimumHeight(200)
        layout.addWidget(self.system_prompt, 1)
        self.message = label('', 'Subtle', True); layout.addWidget(self.message)
        self.apply_button = button('儲存對話指示', self.apply, 'Primary')
        layout.addLayout(row(None, button('重新讀取', self.reload, 'Quiet'), self.apply_button))

    def document(self):
        if not self.context:
            return None
        ref, key, epoch = self.context; doc = ref()
        if (doc is None or doc._disposed or doc.key != key or doc.owner_epoch != epoch
                or not any(tab.document is doc for tab in self.workspace.all_pages())):
            return None
        return doc

    def bind(self, context):
        if context != self.context:
            if self.context:
                self._drafts[self.context] = (self._snapshot, self.system_prompt.toPlainText())
            self._drafts = {key: value for key, value in self._drafts.items() if key[0]() is not None}
            self.context = context; doc = self.document()
            current = doc.options['system_prompt'] if doc else ''
            self._snapshot, text = self._drafts.get(context, (current, current))
            self.system_prompt.setPlainText(text); self.message.setText('')
        doc = self.document()
        if doc:
            self.heading.setText('對話指示 · ' + doc.title)
            if self.system_prompt.toPlainText() == self._snapshot:
                self._snapshot = doc.options['system_prompt']
                self.system_prompt.setPlainText(self._snapshot)
        self.apply_button.setEnabled(doc is not None)
        return doc is not None

    def reload(self):
        doc = self.document()
        if doc:
            self._snapshot = doc.options['system_prompt']
            self.system_prompt.setPlainText(self._snapshot)
            self.message.setText('')

    def apply(self):
        doc = self.document()
        if doc is None:
            self.message.setText('來源對話已關閉或另存，請從該對話重新開啟設定。'); return False
        if doc.options['system_prompt'] != self._snapshot:
            self.message.setText('這份對話的指示已變更，請重新讀取後再調整。'); return False
        if not doc.flush_views():
            self.message.setText('請先完成對話中的文字輸入，再儲存指示。'); return False
        value = self.system_prompt.toPlainText()
        if not doc.set_options({'system_prompt': value}):
            self.message.setText(doc.notice or '對話指示尚未保存。'); return False
        self._snapshot = value  # Owner keeps the draft if disk saving fails.
        if not doc.save():
            self.message.setText(doc.error or '指示已保留於對話草稿，尚未寫入檔案。'); return False
        self.message.setText('已儲存到 ' + doc.path.name)
        return True
