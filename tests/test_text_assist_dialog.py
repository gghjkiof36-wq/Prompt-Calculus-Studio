"""Bounded text-assist presentation checks with an isolated fake controller."""
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QWidget

from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.local_ai.text_assist_dialog import TextAssistDialog
from prompt_calculus_studio.theme import stylesheet, widget_palette


class Controller(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.source_scope = {'kind': 'selection'}
        self.source_text = '這是一段原始文字。\n保留 **Markdown** 與標點。'
        self.models = [{'id': 'one', 'name': '第一個本機模型'}, {'id': 'two', 'name': '第二個本機模型'}]
        self.selected_model_id = 'one'
        self.service = SimpleNamespace(can_accept=True)
        self.state = 'idle'
        self.candidate = ''
        self.error = ''
        self.busy = False
        self.can_apply = False
        self.starts = []
        self.stops = 0
        self.disposals = 0

    def start(self, instruction, model_id=None):
        self.starts.append((instruction, model_id))
        self.state = 'loading'
        self.busy = True
        self.can_apply = False
        self.candidate = ''
        self.error = ''
        self.changed.emit()
        return True

    def stop(self):
        self.stops += 1
        self.state = 'stopped'
        self.busy = False
        self.can_apply = False
        self.changed.emit()

    def complete(self, text='這是 **精簡後的候選文字**。'):
        self.state = 'completed'
        self.candidate = text
        self.busy = False
        self.can_apply = True
        self.changed.emit()

    def candidate_for_apply(self):
        if not self.can_apply:
            raise ValueError('候選尚未完成。')
        return self.candidate

    def dispose(self):
        if self.state == 'disposed':
            return
        self.disposals += 1
        self.state = 'disposed'
        self.busy = False
        self.can_apply = False


class TextAssistDialogUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = initial_state()['settings']
        self.app.setPalette(widget_palette(self.settings))
        self.app.setStyleSheet(stylesheet(self.settings))
        self.parent = QWidget()
        self.parent.display_settings = lambda: self.settings
        self.parent.resize(1000, 800)
        self.parent.show()
        self.controller = Controller()
        self.applied = []
        self.dialog = TextAssistDialog(self.parent, self.controller, self.apply)
        self.dialog.show()
        QTest.qWait(40)

    def apply(self, mode, text):
        self.applied.append((mode, text))
        return True

    def tearDown(self):
        from shiboken6 import isValid
        if isValid(self.dialog):
            self.dialog.close()
        self.parent.close()
        self.parent.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_initial_nonmodal_compact_source_disclosure_and_focus(self):
        dialog = self.dialog
        self.assertEqual(dialog.windowTitle(), '文字協作')
        self.assertEqual(dialog.windowModality(), Qt.WindowModality.NonModal)
        self.assertFalse(dialog._outside_dismiss)
        self.assertIsNone(dialog._backdrop)
        self.assertTrue(dialog.source.isHidden())
        self.assertTrue(dialog.preview_area.isHidden())
        self.assertTrue(dialog.apply_area.isHidden())
        self.assertFalse(dialog.start_button.isEnabled())
        self.assertIs(self.app.focusWidget(), dialog.instruction)
        self.assertEqual(dialog.models.currentData(), 'one')
        dialog.source_toggle.click()
        self.assertTrue(dialog.source.isVisible())
        self.assertEqual(dialog.source.toPlainText(), self.controller.source_text)
        self.assertTrue(dialog.source.isReadOnly())
        dialog.source_toggle.click()
        self.assertTrue(dialog.source.isHidden())
        self.assertEqual(self.applied, [])

    def test_only_explicit_start_uses_per_request_model_choice(self):
        dialog = self.dialog
        dialog.instruction.setPlainText('  請縮短文字。  ')
        dialog.models.setCurrentIndex(1)
        self.controller.changed.emit()
        self.assertEqual(dialog.models.currentData(), 'two')
        self.assertEqual(self.controller.selected_model_id, 'one')
        QTest.keyClick(dialog.instruction, Qt.Key.Key_Return)
        self.assertEqual(self.controller.starts, [])
        dialog.start_button.click()
        self.assertEqual(self.controller.starts, [('請縮短文字。', 'two')])
        self.assertTrue(dialog.instruction.isReadOnly())
        self.assertFalse(dialog.models.isEnabled())
        self.assertTrue(dialog.start_button.isHidden())
        self.assertTrue(dialog.stop_button.isVisible())
        self.assertTrue(dialog.preview_area.isVisible())
        self.assertFalse(dialog.replace_button.isEnabled())
        self.assertFalse(dialog.insert_button.isEnabled())
        self.assertEqual(self.applied, [])

    def test_stream_preview_preserves_native_selection_and_never_applies(self):
        dialog = self.dialog
        dialog.instruction.setPlainText('精簡')
        dialog.start_button.click()
        self.controller.state = 'generating'
        self.controller.candidate = '第一段文字。'
        self.controller.changed.emit()
        QTest.qWait(55)
        cursor = dialog.preview.textCursor()
        cursor.setPosition(0)
        cursor.setPosition(3, QTextCursor.MoveMode.KeepAnchor)
        dialog.preview.setTextCursor(cursor)
        selected = dialog.preview.textCursor().selectedText()
        self.controller.candidate += '\n第二段 **Markdown**。'
        self.controller.changed.emit()
        QTest.qWait(55)
        self.assertEqual(dialog.preview.textCursor().selectedText(), selected)
        self.assertEqual(dialog.preview.toPlainText(), self.controller.candidate)
        self.assertTrue(dialog.preview.isReadOnly())
        self.assertFalse(dialog.replace_button.isEnabled())
        self.assertEqual(self.applied, [])

    def test_stopped_and_failed_candidates_are_visible_but_cannot_apply(self):
        dialog = self.dialog
        dialog.instruction.setPlainText('改寫')
        dialog.start_button.click()
        self.controller.candidate = '只有部分內容'
        self.controller.changed.emit()
        dialog.stop_button.click()
        self.assertEqual(self.controller.stops, 1)
        self.assertEqual(dialog.preview.toPlainText(), '只有部分內容')
        self.assertEqual(dialog.status.text(), '已停止')
        self.assertTrue(dialog.start_button.isEnabled())
        self.assertFalse(dialog.replace_button.isEnabled())
        self.controller.state = 'failed'
        self.controller.error = '模型無法載入，請選擇其他模型。'
        self.controller.changed.emit()
        self.assertEqual(dialog.status.text(), self.controller.error)
        self.assertFalse(dialog.insert_button.isEnabled())
        self.assertEqual(self.applied, [])

    def test_complete_replace_passes_exact_candidate_then_disposes_once(self):
        self.controller.complete()
        self.assertEqual(self.dialog.status.text(), '候選文字已完成')
        self.assertTrue(self.dialog.replace_button.isEnabled())
        self.dialog.replace_button.click()
        self.assertEqual(self.applied, [('replace', '這是 **精簡後的候選文字**。')])
        self.assertEqual(self.controller.disposals, 1)
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.controller.disposals, 1)

    def test_insert_has_separate_apply_intent(self):
        self.controller.complete('候選\n\n原文格式')
        self.dialog.insert_button.click()
        self.assertEqual(self.applied, [('insert', '候選\n\n原文格式')])

    def test_stale_apply_error_keeps_preview_and_original_untouched(self):
        self.controller.complete()
        def stale(*_):
            raise ValueError('原文已變更，請重新選取。')
        self.dialog.apply_callback = stale
        self.dialog.replace_button.click()
        self.assertTrue(self.dialog.isVisible())
        self.assertEqual(self.dialog.status.text(), '原文已變更，請重新選取。')
        self.assertEqual(self.dialog.preview.toPlainText(), self.controller.candidate)
        self.assertEqual(self.applied, [])
        self.controller.state = 'stale'
        self.controller.can_apply = False
        self.controller.changed.emit()
        self.assertFalse(self.dialog.replace_button.isEnabled())
        self.assertFalse(self.dialog.start_button.isEnabled())

    def test_changed_requirement_or_model_cannot_apply_the_previous_candidate(self):
        dialog = self.dialog
        dialog.instruction.setPlainText('縮短')
        dialog.start_button.click()
        self.controller.complete('原需求的候選')
        self.assertTrue(dialog.replace_button.isEnabled())
        dialog.instruction.setPlainText('改成條列')
        self.assertFalse(dialog.replace_button.isEnabled())
        self.assertFalse(dialog.insert_button.isEnabled())
        self.assertEqual(dialog.preview.toPlainText(), '原需求的候選')
        self.assertEqual(dialog.status.text(), '需求或模型已變更，請重新產生候選。')
        dialog._apply('replace')
        self.assertEqual(self.applied, [])
        dialog.instruction.setPlainText('縮短')
        self.assertTrue(dialog.replace_button.isEnabled())
        dialog.models.setCurrentIndex(1)
        self.assertFalse(dialog.replace_button.isEnabled())
        dialog.models.setCurrentIndex(0)
        self.assertTrue(dialog.replace_button.isEnabled())
        dialog.instruction.setPlainText('改成條列')
        dialog.start_button.click()
        self.controller.complete('- 新的候選')
        dialog.insert_button.click()
        self.assertEqual(self.applied, [('insert', '- 新的候選')])

    def test_source_file_title_uses_basename_and_long_title_does_not_expand_dialog(self):
        self.dialog.close()
        self.controller = Controller()
        filename = '很長的來源筆記名稱' * 10 + '.md'
        self.controller.source_scope['path'] = 'D:/synthetic-notes/' + filename
        self.dialog = TextAssistDialog(self.parent, self.controller, self.apply)
        self.dialog.resize(320, 340)
        self.dialog.show()
        QTest.qWait(40)
        self.assertEqual(self.dialog.windowTitle(), '文字協作 · ' + filename)
        self.assertEqual(self.dialog.heading.toolTip(), self.dialog.windowTitle())
        self.assertNotIn('synthetic-notes', self.dialog.heading.text())
        self.assertEqual(self.dialog.width(), 320)
        self.assertTrue(self.dialog.heading.isVisible())
        self.assertGreater(self.dialog.heading.width(), 180)

    def test_escape_disposes_without_applying_or_stopping_other_work(self):
        self.dialog.instruction.setPlainText('改寫')
        self.dialog.start_button.click()
        QTest.keyClick(self.dialog, Qt.Key.Key_Escape)
        self.assertEqual(self.controller.disposals, 1)
        self.assertEqual(self.controller.stops, 0)
        self.assertEqual(self.applied, [])

    def test_busy_service_or_no_models_cannot_start(self):
        self.dialog.instruction.setPlainText('改寫')
        self.controller.service.can_accept = False
        self.controller.changed.emit()
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertEqual(self.dialog.status.text(), '本機模型正忙碌，請稍後再試。')
        self.controller.service.can_accept = True
        self.controller.models = []
        self.controller.changed.emit()
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.assertEqual(self.dialog.status.text(), '請先加入本機模型。')

    def test_stream_updates_are_coalesced_and_hidden_dialog_catches_up(self):
        dialog = self.dialog
        dialog.instruction.setPlainText('改寫')
        dialog.start_button.click()
        updates = []
        original = dialog._set_preview
        def record(text):
            updates.append(text)
            original(text)
        dialog._set_preview = record
        for index in range(80):
            self.controller.candidate += str(index) + ' '
            self.controller.changed.emit()
        self.assertEqual(updates, [])
        QTest.qWait(55)
        self.assertEqual(len(updates), 1)
        self.assertEqual(dialog.preview.toPlainText(), self.controller.candidate)
        dialog.hide()
        self.controller.candidate += 'hidden'
        self.controller.changed.emit()
        QTest.qWait(55)
        self.assertEqual(len(updates), 1)
        dialog.show()
        self.assertEqual(dialog.preview.toPlainText(), self.controller.candidate)
        self.controller.complete('完整候選')
        self.assertEqual(dialog.preview.toPlainText(), '完整候選')
        self.assertFalse(dialog._refresh_timer.isActive())

    def test_large_font_narrow_dialog_keeps_both_apply_actions_reachable(self):
        from PySide6.QtGui import QFont
        previous = self.app.font()
        try:
            self.app.setFont(QFont('Microsoft JhengHei UI', 18))
            self.settings['ui_size'] = 18
            self.dialog.close()
            self.controller = Controller()
            self.dialog = TextAssistDialog(self.parent, self.controller, self.apply)
            self.controller.complete()
            self.dialog.resize(320, 700)
            self.dialog.show()
            QTest.qWait(55)
            self.assertEqual(self.dialog.width(), 320)
            for widget in (self.dialog.replace_button, self.dialog.insert_button):
                bottom = widget.mapTo(self.dialog, widget.rect().bottomRight())
                self.assertLess(bottom.x(), self.dialog.width())
                self.assertLess(bottom.y(), self.dialog.height())
                self.assertGreaterEqual(widget.width(), widget.sizeHint().width())
        finally:
            self.app.setFont(previous)

    def test_paragraph_label_and_narrow_bounds_with_long_model_name(self):
        self.dialog.close()
        self.controller = Controller()
        self.controller.source_scope = {'kind': 'paragraph'}
        self.controller.models[0]['name'] = '很長的本機模型名稱-' * 12
        self.dialog = TextAssistDialog(self.parent, self.controller, self.apply)
        self.controller.complete('候選文字 ' * 100)
        self.dialog.resize(320, 580)
        self.dialog.show()
        QTest.qWait(50)
        self.assertTrue(self.dialog.source_toggle.text().startswith('目前段落 · '))
        self.assertEqual(self.dialog.replace_button.text(), '取代段落')
        self.assertEqual(self.dialog.width(), 320)
        for widget in (self.dialog.models, self.dialog.start_button, self.dialog.preview,
                       self.dialog.replace_button, self.dialog.insert_button):
            top = widget.mapTo(self.dialog, widget.rect().topLeft())
            bottom = widget.mapTo(self.dialog, widget.rect().bottomRight())
            self.assertGreaterEqual(top.x(), 0, widget.accessibleName())
            self.assertLess(bottom.x(), self.dialog.width(), widget.accessibleName())
            self.assertLess(bottom.y(), self.dialog.height(), widget.accessibleName())
        self.assertEqual(self.dialog.models.toolTip(), self.controller.models[0]['name'])


if __name__ == '__main__':
    unittest.main()
