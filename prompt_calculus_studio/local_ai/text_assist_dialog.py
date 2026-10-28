"""A local text candidate, previewed before its Markdown owner applies it."""
from pathlib import PureWindowsPath

from PySide6.QtCore import Qt, QEvent, QTimer
from PySide6.QtGui import QTextCursor
from PySide6.QtWidgets import QBoxLayout, QPlainTextEdit, QPushButton, QSizePolicy, QWidget, QVBoxLayout

from ..module_chrome import apply_module_dialog, dialog_context
from ..ui_icons import icon
from ..widgets import StudioDialog, ComboBox, ElidedLabel, button, label, row


class TextAssistDialog(StudioDialog):
    def __init__(self, parent, controller, apply_callback):
        super().__init__(parent)
        self.controller = controller
        self.apply_callback = apply_callback
        self._closed = False
        self._apply_error = ''
        self._generation_input = None
        self._models_signature = None
        self._preview_text = ''
        self._preview_opened = False
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(40)
        self._refresh_timer.timeout.connect(self.refresh)
        self._owns_backdrop = True
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setMinimumWidth(320)
        self.resize(600, 340)

        scope = controller.source_scope
        old_heading = self.heading
        self.heading = ElidedLabel()
        self.heading.setObjectName('DialogTitle')
        self.heading.setTextFormat(Qt.TextFormat.PlainText)
        self.heading.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        self.heading_row.replaceWidget(old_heading, self.heading)
        self.heading_row.setStretch(0, 1)
        self.heading_row.setStretch(1, 0)
        self.windowTitleChanged.disconnect(old_heading.setText)
        self.windowTitleChanged.connect(self.heading.setText)
        old_heading.deleteLater()
        name = PureWindowsPath(scope['path']).name if isinstance(scope.get('path'), str) and scope['path'] else ''
        title = '文字協作' + (' · ' + name if name else '')
        self.setWindowTitle(title)
        self.heading.setToolTip(title)
        self.heading.setAccessibleName(title)
        self.heading.show()
        paragraph = isinstance(scope, dict) and scope.get('kind') == 'paragraph'
        source_name = '目前段落' if paragraph else '選取文字'
        self.source_toggle = button(f'{source_name} · {len(controller.source_text)} 字', self._toggle_source, 'Quiet')
        self.source_toggle.setCheckable(True)
        self.source_toggle.setAccessibleName(source_name)
        self.source_toggle.setIcon(icon('chevron-right', size=16))
        self.body.addWidget(self.source_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.source = QPlainTextEdit(controller.source_text)
        self.source.setReadOnly(True)
        self.source.setAccessibleName(source_name + '原文')
        self.source.setMaximumHeight(104)
        self.source.setMinimumHeight(76)
        self.body.addWidget(self.source)
        self.source.hide()

        self.body.addWidget(label('需求', 'Subtle'))
        self.instruction = QPlainTextEdit()
        self.instruction.setAccessibleName('文字協作需求')
        self.instruction.setPlaceholderText('例如：保留原意，讓這段文字更精簡。')
        self.instruction.setMinimumHeight(78)
        self.instruction.setMaximumHeight(108)
        self.body.addWidget(self.instruction)

        self.models = ComboBox()
        self.models.setAccessibleName('本次使用的本機模型')
        self.models.setMinimumWidth(0)
        self.models.setMinimumContentsLength(8)
        self.models.setSizeAdjustPolicy(ComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.models.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.start_button = button('開始', self._start, 'Primary')
        self.stop_button = button('停止', controller.stop)
        self.stop_button.hide()
        self.body.addLayout(row(self.models, self.start_button, self.stop_button))

        self.status = label('', 'Subtle', True)
        self.status.setAccessibleName('文字協作狀態')
        self.body.addWidget(self.status)
        self.preview_area = QWidget()
        preview_layout = QVBoxLayout(self.preview_area)
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(8)
        preview_layout.addWidget(label('候選文字', 'Subtle'))
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setAccessibleName('候選文字預覽')
        self.preview.setPlaceholderText('正在準備候選文字…')
        self.preview.setMinimumHeight(130)
        preview_layout.addWidget(self.preview, 1)
        self.body.addWidget(self.preview_area, 1)
        self.replace_button = button('取代段落' if paragraph else '取代選取', lambda: self._apply('replace'), 'Primary')
        self.insert_button = button('接在後面', lambda: self._apply('insert'))
        self.apply_area = QWidget()
        actions = row(None, self.replace_button, self.insert_button)
        actions.setContentsMargins(0, 0, 0, 0)
        self.apply_area.setLayout(actions)
        self.body.addWidget(self.apply_area)
        for control in self.findChildren(QPushButton):
            control.setAutoDefault(False)
            control.setDefault(False)
        self.instruction.textChanged.connect(self.refresh)
        self.models.currentIndexChanged.connect(self._model_changed)
        controller.changed.connect(self._changed)
        self.destroyed.connect(controller.dispose)
        self.setTabOrder(self.source_toggle, self.instruction)
        self.setTabOrder(self.instruction, self.models)
        self.setTabOrder(self.models, self.start_button)
        self.setTabOrder(self.start_button, self.stop_button)
        self.setTabOrder(self.stop_button, self.preview)
        self.setTabOrder(self.preview, self.replace_button)
        self.setTabOrder(self.replace_button, self.insert_button)
        self.refresh()

    def _toggle_source(self):
        expanded = self.source_toggle.isChecked()
        self.source.setVisible(expanded)
        self.source_toggle.setIcon(icon('chevron-down' if expanded else 'chevron-right', size=16))

    def _model_changed(self):
        self.models.setToolTip(self.models.currentText())
        self.refresh()

    def _input_matches_candidate(self):
        return (self._generation_input is None or self._generation_input ==
                (self.instruction.toPlainText().strip(), self.models.currentData()))

    def _changed(self):
        if self._closed or not self.isVisible():
            return
        if self.controller.busy:
            if not self._refresh_timer.isActive():
                self._refresh_timer.start()
        else:
            self._refresh_timer.stop()
            self.refresh()

    def _sync_models(self):
        entries = tuple((entry['id'], entry['name']) for entry in self.controller.models)
        if entries == self._models_signature:
            return
        selected = self.models.currentData() or self.controller.selected_model_id
        self._models_signature = entries
        self.models.blockSignals(True)
        self.models.clear()
        for identity, name in entries:
            self.models.addItem(name, identity)
        index = self.models.findData(selected)
        self.models.setCurrentIndex(index if index >= 0 else (0 if entries else -1))
        if not entries:
            self.models.addItem('尚無本機模型', None)
        self.models.blockSignals(False)
        self.models.setToolTip(self.models.currentText())

    def _update_actions(self):
        if self._closed:
            return
        busy = self.controller.busy
        available = self.controller.state not in {'invalidated', 'stale', 'closed', 'disposed'}
        self.instruction.setReadOnly(busy or not available)
        self.models.setEnabled(not busy and available)
        self.start_button.setVisible(not busy)
        self.stop_button.setVisible(busy)
        self.stop_button.setEnabled(self.controller.state != 'stopping')
        self.start_button.setEnabled(available and not busy and bool(self.instruction.toPlainText().strip())
                                     and self.models.currentData() is not None
                                     and self.controller.service.can_accept)
        can_apply = self.controller.can_apply and self._input_matches_candidate()
        self.replace_button.setEnabled(can_apply)
        self.insert_button.setEnabled(can_apply)

    def _set_preview(self, text):
        if text == self._preview_text:
            return
        scrollbar = self.preview.verticalScrollBar()
        following = scrollbar.value() >= scrollbar.maximum() - 4
        position = scrollbar.value()
        if text.startswith(self._preview_text):
            cursor = QTextCursor(self.preview.document())
            cursor.movePosition(QTextCursor.MoveOperation.End)
            cursor.insertText(text[len(self._preview_text):])
        else:
            self.preview.setPlainText(text)
        self._preview_text = text
        scrollbar.setValue(scrollbar.maximum() if following else position)

    def refresh(self):
        if self._closed:
            return
        self._sync_models()
        candidate = self.controller.candidate
        self._set_preview(candidate)
        state = self.controller.state
        show_preview = bool(self.controller.busy or candidate or self._preview_opened)
        self.preview_area.setVisible(show_preview)
        self.apply_area.setVisible(show_preview)
        if show_preview and not self._preview_opened:
            self._preview_opened = True
            self.resize(self.width(), min(580, self.screen().availableGeometry().height() - 40))
        captions = {'loading': '正在準備模型…', 'generating': '正在產生候選文字…',
                    'stopping': '正在停止…', 'stopped': '已停止', 'completed': '候選文字已完成'}
        status = self._apply_error or self.controller.error or captions.get(state, '')
        if self.controller.can_apply and not self._input_matches_candidate():
            status = '需求或模型已變更，請重新產生候選。'
        if not status and state == 'idle':
            if not self.controller.models:
                status = '請先加入本機模型。'
            elif not self.controller.service.can_accept:
                status = '本機模型正忙碌，請稍後再試。'
        self.status.setText(status)
        self.status.setVisible(bool(status))
        self._update_actions()

    def _start(self):
        if not self.start_button.isEnabled():
            return
        self._apply_error = ''
        self._generation_input = (self.instruction.toPlainText().strip(), self.models.currentData())
        self.controller.start(*self._generation_input)
        self.refresh()

    def _apply(self, mode):
        if not self._input_matches_candidate():
            self.refresh()
            return
        try:
            candidate = self.controller.candidate_for_apply()
            if self.apply_callback(mode, candidate) is False:
                self._apply_error = '無法套用，請重新選取原文。'
                self.refresh()
                return
        except (ValueError, OSError) as error:
            self._apply_error = str(error)
            self.refresh()
            return
        self.accept()

    def showEvent(self, event):
        self.refresh()
        super().showEvent(event)
        QTimer.singleShot(0, self, self.instruction.setFocus)

    def hideEvent(self, event):
        self._refresh_timer.stop()
        super().hideEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'apply_area'):
            actions = self.apply_area.layout()
            needed = self.replace_button.sizeHint().width() + self.insert_button.sizeHint().width() + actions.spacing()
            direction = QBoxLayout.Direction.LeftToRight if self.apply_area.width() >= needed else QBoxLayout.Direction.TopToBottom
            if actions.direction() != direction:
                actions.setDirection(direction)

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.StyleChange and hasattr(self, 'controller'):
            context = dialog_context(self.parentWidget())
            if self._module_dialog_context != context:
                apply_module_dialog(self, *context)

    def done(self, result):
        if self._closed:
            return
        self._closed = True
        self._refresh_timer.stop()
        self.controller.dispose()
        super().done(result)
