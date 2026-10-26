"""Explicit settings for a local conversation; opening never starts a runtime."""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialogButtonBox, QDoubleSpinBox, QFileDialog, QFormLayout, QLineEdit,
    QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)

from ..widgets import StudioDialog, button, label, row, scrolling


def _integer(value, minimum, maximum, message):
    try:
        result = int(value)
    except (TypeError, ValueError):
        raise ValueError(message) from None
    if not minimum <= result <= maximum:
        raise ValueError(message)
    return result


class ChatSettingsDialog(StudioDialog):
    """Stage changes locally and apply them only after an explicit click."""

    def __init__(self, document, parent=None):
        super().__init__(parent)
        self.chat_document = document
        self.service = document.service
        self.setWindowTitle('對話設定')
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.resize(620, 740)
        values = self.service.settings
        self._settings_snapshot = dict(values)
        self._prompt_snapshot = document.options.get('system_prompt', '')

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(18)
        layout.addWidget(label('本次對話', 'Heading'))
        layout.addWidget(label('上下文指示', 'Subtle'))
        self.system_prompt = QPlainTextEdit()
        self.system_prompt.setAccessibleName('本次對話的上下文指示')
        self.system_prompt.setPlaceholderText('例如：使用繁體中文回答，先說結論，再說明理由。')
        self.system_prompt.setPlainText(self._prompt_snapshot)
        self.system_prompt.setMinimumHeight(120)
        self.system_prompt.setMaximumHeight(180)
        layout.addWidget(self.system_prompt)
        layout.addWidget(label('這段指示只保存在此對話；變更會從下一次送出開始使用。', 'Subtle', True))

        layout.addWidget(label('模型與生成', 'Heading'))
        layout.addWidget(label('以下設定供本機對話共用。已收到的回覆會保留原本使用的參數。', 'Subtle', True))
        form = QFormLayout()
        form.setVerticalSpacing(14)
        form.setHorizontalSpacing(18)
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.context_size = self._spin(256, 1048576, values.get('context_size', 4096), 512)
        self.max_tokens = self._spin(1, 262144, values.get('max_tokens', 1024), 128)
        self.temperature = self._decimal(0, 2, values.get('temperature', .7), .1)
        self.top_p = self._decimal(.01, 1, values.get('top_p', .95), .05)
        self.seed = QLineEdit(str(values.get('seed', -1)))
        self.seed.setPlaceholderText('-1：每次隨機')
        self.seed.setAccessibleName('隨機種子，-1 表示每次隨機')
        self.gpu_layers = self._spin(-1, 999, values.get('gpu_layers', 0))
        self.gpu_layers.setSpecialValueText('全部可用層')
        self.idle_minutes = self._spin(0, 1440, values.get('idle_minutes', 10))
        self.idle_minutes.setSpecialValueText('不自動卸載')
        self.idle_minutes.setSuffix(' 分鐘')
        for name, field in (
            ('上下文容量（tokens）', self.context_size),
            ('回覆上限（tokens）', self.max_tokens),
            ('溫度', self.temperature), ('Top P', self.top_p),
            ('隨機種子', self.seed), ('GPU 層數', self.gpu_layers),
            ('閒置後卸載', self.idle_minutes),
        ):
            field.setAccessibleName(name)
            form.addRow(name, field)
        layout.addLayout(form)
        layout.addWidget(label(
            '回覆上限須小於上下文容量。GPU 層數為 0 時使用 CPU。'
            '變更上下文容量或 GPU 層數前，請先卸載模型；套用後再手動載入。', 'Subtle', True))

        layout.addWidget(label('本機引擎', 'Heading'))
        self.runtime_path = QLineEdit(values.get('runtime_path', ''))
        self.runtime_path.setAccessibleName('本機 llama-server 引擎位置')
        self.runtime_path.setPlaceholderText('留空時尋找 PCS 隨附引擎')
        layout.addLayout(row(self.runtime_path, button('選擇引擎…', self._pick_runtime)))
        layout.addWidget(label(
            '選擇已準備好的 llama-server.exe。套用設定不會下載或啟動引擎，也不會載入模型。',
            'Subtle', True))
        layout.addStretch()
        self.body.addWidget(scrolling(content), 1)
        self.error_label = label('', 'ChatSettingsError', True)
        self.error_label.setProperty('tone', 'error')
        self.error_label.setAccessibleName('設定錯誤')
        self.error_label.hide()
        self.body.addWidget(self.error_label)
        actions = QDialogButtonBox(QDialogButtonBox.StandardButton.Apply | QDialogButtonBox.StandardButton.Cancel)
        self.apply_button = actions.button(QDialogButtonBox.StandardButton.Apply)
        self.apply_button.setText('套用')
        self.apply_button.setObjectName('Primary')
        self.apply_button.clicked.connect(self._apply)
        actions.button(QDialogButtonBox.StandardButton.Cancel).setText('取消')
        actions.rejected.connect(self.reject)
        self.body.addWidget(actions)

    @staticmethod
    def _spin(minimum, maximum, value, step=1):
        result = QSpinBox()
        result.setRange(minimum, maximum)
        result.setSingleStep(step)
        result.setValue(value)
        return result

    @staticmethod
    def _decimal(minimum, maximum, value, step):
        result = QDoubleSpinBox()
        result.setRange(minimum, maximum)
        result.setDecimals(2)
        result.setSingleStep(step)
        result.setValue(value)
        return result

    def _pick_runtime(self):
        filename, _ = QFileDialog.getOpenFileName(
            self, '選擇本機引擎', self.runtime_path.text(), '本機引擎 (llama-server.exe);;執行檔 (*.exe)')
        if filename:
            self.runtime_path.setText(str(Path(filename)))

    def _apply(self):
        try:
            if self.service.settings != self._settings_snapshot:
                raise ValueError('模型設定已在其它視窗變更。請重新開啟設定，確認目前內容後再套用。')
            if self.chat_document.options.get('system_prompt', '') != self._prompt_snapshot:
                raise ValueError('上下文指示已在此對話的另一個視窗變更。請重新開啟設定。')
            seed = _integer(self.seed.text().strip(), -1, 4294967295,
                            '隨機種子請輸入 -1 到 4294967295 的整數。')
            if self.max_tokens.value() >= self.context_size.value():
                raise ValueError('回覆上限須小於上下文容量，請調整後再套用。')
            options = {'system_prompt': self.system_prompt.toPlainText()}
            if len(options['system_prompt']) > 1000000:
                raise ValueError('上下文指示過長，請縮短後再套用。')
            settings = dict(
                runtime_path=self.runtime_path.text().strip(),
                context_size=self.context_size.value(), max_tokens=self.max_tokens.value(),
                temperature=self.temperature.value(), top_p=self.top_p.value(), seed=seed,
                gpu_layers=self.gpu_layers.value(), idle_minutes=self.idle_minutes.value(),
            )
            if self.service.configure(settings) is False:
                raise ValueError(self.service.error or '設定尚未套用，請稍後重試。')
            if self.chat_document.set_options(options) is False:
                raise ValueError(self.chat_document.notice or self.chat_document.error or '上下文指示尚未保存。')
        except (ValueError, OSError, RuntimeError) as exc:
            self.error_label.setText(str(exc))
            self.error_label.show()
            return
        self.accept()
