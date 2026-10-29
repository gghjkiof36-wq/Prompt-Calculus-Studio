"""Inline instructions and review controls; the Markdown owner projects candidates."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, Qt, Signal
from PySide6.QtGui import QColor, QFont, QInputMethodEvent, QTextCharFormat, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QComboBox, QDialog, QPlainTextEdit, QWidget
from shiboken6 import isValid

from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.color_roles import contrast
from prompt_calculus_studio.local_ai.text_assist_panel import TextAssistPanel
from prompt_calculus_studio.theme import stylesheet, widget_palette


class Controller(QObject):
    changed = Signal()

    def __init__(self):
        super().__init__()
        self.source_scope = {'kind': 'selection'}
        self.source_text = '這是一段原始文字。\n保留 **Markdown** 與標點。'
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


class TextAssistPanelUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.settings = initial_state()['settings']
        self.app.setPalette(widget_palette(self.settings))
        self.app.setStyleSheet(stylesheet(self.settings))
        self.parent = QWidget()
        self.parent.display_settings = lambda: self.settings
        self.parent.resize(1000, 850)
        self.editor = QPlainTextEdit(self.parent)
        self.editor.setGeometry(0, 0, 1000, 850)
        self.editor.setPlainText('這是正在編輯的原文。')
        self.parent.show()
        self.editor.setFocus()
        self.controller = Controller()
        self.applied = []
        self.panel = TextAssistPanel(self.parent, self.controller, self.apply)
        self.panel.setGeometry(30, 100, 500, self.panel.preferred_height(500))
        self.panel.show()
        QTest.qWait(35)

    def apply(self, mode, text):
        self.applied.append((mode, text))
        return True

    def tearDown(self):
        if isValid(self.panel):
            self.panel.close()
        if isValid(self.parent):
            self.parent.close()
            self.parent.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def place(self, width=500):
        width = self.panel.preferred_width(width)
        self.panel.resize(width, self.panel.preferred_height(width))
        self.panel.layout().activate()
        QTest.qWait(20)
        self.panel.resize(width, self.panel.preferred_height(width))
        self.panel.layout().activate()
        QTest.qWait(20)

    def start(self):
        self.panel.instruction.setPlainText('請精簡這段文字。')
        self.panel.start_button.click()
        self.place()

    def test_compact_true_child_has_no_model_source_or_candidate_widget_and_preserves_focus(self):
        panel = self.panel
        self.assertFalse(panel.isWindow())
        self.assertNotIsInstance(panel, QDialog)
        self.assertEqual(panel.windowType(), Qt.WindowType.Widget)
        self.assertIs(panel.parentWidget(), self.parent)
        self.assertEqual(panel.findChildren(QComboBox), [])
        for name in ('models', 'source', 'heading', 'preview', 'replace_button', 'insert_button'):
            self.assertFalse(hasattr(panel, name), name)
        self.assertEqual(panel.findChildren(QPlainTextEdit), [panel.instruction])
        self.assertEqual(panel.instruction.placeholderText(), '描述你想如何編輯…')
        self.assertGreaterEqual(panel.preferred_height(500), 50)
        self.assertLessEqual(panel.preferred_height(500), 56)
        self.assertEqual(panel.instruction.parentWidget(), panel.controls)
        self.assertEqual(panel.start_button.parentWidget(), panel.controls)
        self.assertIs(self.app.focusWidget(), self.editor)
        self.assertTrue(panel.review_controls.isHidden())
        for control in (panel.start_button, panel.stop_button, panel.close_button):
            self.assertEqual(control.text(), '')
            self.assertFalse(control.icon().isNull())
            self.assertTrue(control.accessibleName())
        self.assertFalse(panel.start_button.isEnabled())

    def test_requires_a_real_parent(self):
        with self.assertRaises(ValueError):
            TextAssistPanel(None, self.controller, self.apply)

    def test_ime_preedit_hides_placeholder_and_themes_clause_without_selecting_all(self):
        editor = self.panel.instruction
        self.panel.focus_input()
        formatting = QTextCharFormat()
        formatting.setBackground(QColor('white'))
        formatting.setForeground(QColor('black'))
        formatting.setUnderlineStyle(QTextCharFormat.UnderlineStyle.DashUnderline)
        attributes = [QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.TextFormat, 0, 2, formatting),
                      QInputMethodEvent.Attribute(QInputMethodEvent.AttributeType.Cursor, 1, 1, None)]
        observed = []
        base = QPlainTextEdit.inputMethodEvent
        def receive(widget, event):
            observed.extend(event.attributes())
            return base(widget, event)
        with patch.object(QPlainTextEdit, 'inputMethodEvent', receive):
            self.app.sendEvent(editor, QInputMethodEvent('中文', attributes))
        self.assertEqual(editor.placeholderText(), '')
        self.assertTrue(editor.composing)
        self.assertEqual(editor.toPlainText(), '')
        self.assertFalse(self.panel.start_button.isEnabled())
        themed = observed[0].value.toCharFormat()
        self.assertEqual(themed.background().color().name(), editor.tokens['selected'])
        self.assertEqual(themed.foreground().color().name(), editor.tokens['text'])
        self.assertEqual(themed.underlineStyle(), formatting.underlineStyle())
        self.assertEqual(observed[1].start, 1)
        QTest.keyClick(editor, Qt.Key.Key_Escape)
        self.assertTrue(self.panel.isVisible())
        commit = QInputMethodEvent()
        commit.setCommitString('中文')
        self.app.sendEvent(editor, commit)
        self.assertEqual(editor.toPlainText(), '中文')
        self.assertEqual(editor.placeholderText(), '描述你想如何編輯…')
        self.assertFalse(editor.composing)
        self.assertFalse(editor.textCursor().hasSelection())
        self.assertTrue(self.panel.start_button.isEnabled())
        self.panel.hide()
        self.panel.show()
        self.assertFalse(editor.textCursor().hasSelection())
        self.assertEqual(self.controller.starts, [])

    def test_ime_commit_replacement_and_selection_colors_follow_production_palettes(self):
        editor = self.panel.instruction
        editor.setPlainText('原文')
        editor.moveCursor(QTextCursor.MoveOperation.End)
        self.app.sendEvent(editor, QInputMethodEvent('中文', []))
        self.assertEqual(editor.toPlainText(), '原文')
        self.assertFalse(self.panel.start_button.isEnabled())
        commit = QInputMethodEvent()
        commit.setCommitString('字', -1, 1)
        self.app.sendEvent(editor, commit)
        self.assertEqual(editor.toPlainText(), '原字')
        editor.undo()
        self.assertEqual(editor.toPlainText(), '原文')
        for palette in ('graphite', 'mist', 'paper'):
            self.settings['visual_palette'] = palette
            self.app.setPalette(widget_palette(self.settings))
            self.app.setStyleSheet(stylesheet(self.settings))
            self.panel._apply_theme()
            tokens = editor.tokens
            self.assertGreaterEqual(contrast(tokens['text'], tokens['raised']), 4.5)
            self.assertGreaterEqual(contrast(tokens['text'], tokens['selected']), 4.5)
            self.assertGreaterEqual(contrast(tokens['secondary'], tokens['raised']), 4.5)

    def test_start_uses_controller_model_and_enter_remains_multiline(self):
        panel = self.panel
        self.controller.selected_model_id = 'two'
        panel.instruction.setPlainText('精簡')
        panel.focus_input()
        QTest.keyClick(panel.instruction, Qt.Key.Key_Return)
        self.assertEqual(self.controller.starts, [])
        panel.start_button.click()
        self.assertEqual(self.controller.starts, [('精簡', 'two')])
        self.assertTrue(panel.instruction.isHidden())
        self.assertTrue(panel.controls.isHidden())
        self.assertTrue(panel.review_controls.isVisible())
        self.assertTrue(panel.stop_button.isVisible())
        self.assertTrue(panel.retry_button.isHidden())
        self.assertFalse(panel.keep_button.isEnabled())
        self.assertEqual(self.applied, [])

    def test_geometry_signal_requests_control_row_without_moving_or_resizing_itself(self):
        before = self.panel.geometry()
        requests = []
        self.panel.geometry_needed.connect(lambda: requests.append(self.panel.preferred_height(self.panel.width())))
        self.panel.instruction.setPlainText('精簡')
        self.panel.start_button.click()
        self.assertEqual(self.panel.geometry(), before)
        self.assertTrue(requests)
        self.assertGreaterEqual(requests[-1], 32)
        self.assertLessEqual(requests[-1], 70)
        self.assertEqual(self.panel.pos(), QPoint(30, 100))

    def test_keep_replaces_exact_candidate_once_and_disposes(self):
        self.start()
        self.controller.complete('候選 **Markdown**\n第二行')
        self.assertEqual(self.applied, [])
        self.assertTrue(self.panel.keep_button.isEnabled())
        closed = []
        self.panel.closed.connect(lambda: closed.append(True))
        self.panel.keep_button.click()
        self.assertEqual(self.applied, [('replace', '候選 **Markdown**\n第二行')])
        self.assertEqual(self.controller.disposals, 1)
        self.assertEqual(closed, [True])

    def test_keep_dropdown_preserves_insert_use(self):
        self.start()
        self.controller.complete('接在後面的候選')
        self.panel.keep_menu_button.click()
        menu = self.panel._keep_menu
        self.assertEqual([action.text() for action in menu.actions()], ['接在原文後'])
        menu.actions()[0].trigger()
        self.assertEqual(self.applied, [('insert', '接在後面的候選')])
        self.assertEqual(self.controller.disposals, 1)

    def test_retry_preserves_original_instruction_and_source(self):
        self.start()
        source = self.controller.source_text
        scope = dict(self.controller.source_scope)
        self.controller.complete('第一個候選，不是新的來源')
        self.panel.retry_button.click()
        self.assertEqual(self.controller.starts, [('請精簡這段文字。', 'one')] * 2)
        self.assertEqual(self.controller.source_text, source)
        self.assertEqual(self.controller.source_scope, scope)
        self.assertTrue(self.panel.stop_button.isVisible())
        self.assertFalse(self.panel.keep_button.isEnabled())
        self.assertEqual(self.applied, [])

    def test_delete_discards_without_apply_and_signals_once(self):
        self.start()
        self.controller.complete('不採用的候選')
        closed = []
        self.panel.closed.connect(lambda: closed.append(True))
        self.panel.delete_button.click()
        self.panel.dismiss()
        self.assertEqual(self.applied, [])
        self.assertEqual(self.controller.disposals, 1)
        self.assertEqual(closed, [True])

    def test_replacement_callback_failure_keeps_review_and_original(self):
        self.start()
        self.controller.complete('候選')
        def stale(*_):
            raise ValueError('原文已變更，請重新選取。')
        self.panel.apply_callback = stale
        self.panel.keep_button.click()
        self.assertTrue(self.panel.isVisible())
        self.assertEqual(self.panel.status.text(), '原文已變更，請重新選取。')
        self.assertEqual(self.applied, [])

    def test_changed_instruction_or_external_model_disables_old_candidate(self):
        self.start()
        self.controller.complete('舊候選')
        self.panel.instruction.setPlainText('改成條列')
        self.assertFalse(self.panel.keep_button.isEnabled())
        self.assertEqual(self.panel.status.text(), '需求或模型已變更，請重新產生候選。')
        self.panel._apply('replace')
        self.assertEqual(self.applied, [])
        self.panel.instruction.setPlainText('請精簡這段文字。')
        self.assertTrue(self.panel.keep_button.isEnabled())
        self.controller.selected_model_id = 'two'
        self.controller.changed.emit()
        self.assertFalse(self.panel.keep_menu_button.isEnabled())
        self.controller.selected_model_id = 'one'
        self.controller.changed.emit()
        self.assertTrue(self.panel.keep_menu_button.isEnabled())

    def test_stopped_failed_limited_allow_retry_delete_but_not_keep(self):
        self.start()
        self.controller.candidate = '未完成的候選'
        self.controller.changed.emit()
        self.panel.stop_button.click()
        self.assertEqual(self.controller.stops, 1)
        self.assertFalse(self.panel.keep_button.isEnabled())
        for state in ('stopped', 'failed', 'limited'):
            self.controller.state = state
            self.controller.error = state
            self.controller.changed.emit()
            self.assertFalse(self.panel.keep_button.isEnabled())
            self.assertTrue(self.panel.retry_button.isEnabled())
            self.assertTrue(self.panel.delete_button.isEnabled())
        self.controller.state = 'stale'
        self.controller.changed.emit()
        self.assertFalse(self.panel.retry_button.isEnabled())
        self.assertTrue(self.panel.delete_button.isEnabled())

    def test_stream_controls_coalesce_and_hidden_timer_catches_up(self):
        self.start()
        updates = []
        original = self.panel._sync_metrics
        def record():
            updates.append(True)
            original()
        self.panel._sync_metrics = record
        self.controller.state = 'generating'
        for _ in range(80):
            self.controller.candidate += '更多文字。'
            self.controller.changed.emit()
        self.assertEqual(updates, [])
        QTest.qWait(85)
        self.assertEqual(len(updates), 1)
        self.assertEqual(self.panel.status.text(), '生成中…')
        self.panel.hide()
        self.controller.state = 'stopped'
        self.controller.busy = False
        self.controller.changed.emit()
        QTest.qWait(55)
        self.assertEqual(len(updates), 1)
        self.assertFalse(self.panel._refresh_timer.isActive())
        self.panel.show()
        self.assertEqual(self.panel.status.text(), '已停止')
        self.controller.complete('完成候選')
        self.assertEqual(self.panel.status.text(), '')
        self.assertTrue(self.panel.status.isHidden())
        self.assertTrue(self.panel.keep_button.isEnabled())
        self.assertFalse(self.panel._refresh_timer.isActive())

    def test_escape_disposes_once_without_stopping_unrelated_work(self):
        self.start()
        closed = []
        self.panel.closed.connect(lambda: closed.append(True))
        QTest.keyClick(self.panel, Qt.Key.Key_Escape)
        self.panel.dismiss()
        self.assertEqual(self.controller.disposals, 1)
        self.assertEqual(self.controller.stops, 0)
        self.assertEqual(closed, [True])
        self.assertEqual(self.applied, [])

    def test_destroying_parent_disposes_controller(self):
        self.parent.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(self.controller.disposals, 1)

    def test_narrow_review_controls_keep_all_actions_reachable(self):
        self.start()
        self.controller.complete('很長的候選文字。\n' * 400)
        self.place(320)
        self.assertGreaterEqual(self.panel.width(), 220)
        self.assertLessEqual(self.panel.width(), 280)
        self.assertGreaterEqual(self.panel.height(), 32)
        self.assertLessEqual(self.panel.height(), 36)
        self.assertEqual(self.panel.findChildren(QPlainTextEdit), [self.panel.instruction])
        for control in (self.panel.keep_button, self.panel.keep_menu_button,
                        self.panel.retry_button, self.panel.delete_button):
            bottom = control.mapTo(self.panel, control.rect().bottomRight())
            self.assertLess(bottom.x(), self.panel.width())
            self.assertLess(bottom.y(), self.panel.height())
            self.assertTrue(control.isVisible())

    def test_large_font_narrow_review_preserves_actions_and_full_error_tooltip(self):
        previous = self.app.font()
        try:
            self.app.setFont(QFont('Microsoft JhengHei UI', 18))
            self.settings['ui_size'] = 18
            self.settings['visual_palette'] = 'paper'
            self.app.setPalette(widget_palette(self.settings))
            self.app.setStyleSheet(stylesheet(self.settings))
            self.start()
            self.controller.complete('候選文字' * 100)
            self.controller.error = '這是一段很長但必須可完整讀取的錯誤說明。' * 10
            self.controller.changed.emit()
            self.place(320)
            self.assertEqual(self.panel.status.toolTip(), self.controller.error)
            self.assertLessEqual(self.panel.width(), 320)
            self.assertGreaterEqual(self.panel.status.width(), self.panel.status.fontMetrics().horizontalAdvance('錯誤說明'))
            for control in (self.panel.keep_button, self.panel.keep_menu_button,
                            self.panel.retry_button, self.panel.delete_button):
                bottom = control.mapTo(self.panel, control.rect().bottomRight())
                self.assertLess(bottom.x(), self.panel.width())
                self.assertLess(bottom.y(), self.panel.height())
                self.assertGreaterEqual(control.width(), control.minimumSizeHint().width())
        finally:
            self.app.setFont(previous)

    def test_input_grows_with_wrapping_and_newlines_then_scrolls_after_six_lines(self):
        panel = self.panel
        initial = panel.preferred_height(500)
        panel.instruction.setPlainText('保留原意，讓文字更自然。')
        self.place()
        self.assertEqual(panel.height(), initial)
        panel.instruction.setPlainText('第一行\n第二行\n第三行')
        self.place()
        self.assertGreater(panel.height(), initial)
        three = panel.height()
        panel.instruction.setPlainText('這是會在窄視窗內自然折行的修改要求。' * 6)
        self.place(320)
        self.assertGreater(panel.height(), three)
        panel.instruction.setPlainText('\n'.join('請保持原意。' for _ in range(20)))
        self.place(320)
        maximum = panel.height()
        metrics = panel.instruction.fontMetrics()
        self.assertLessEqual(maximum, 20 + 6 * max(metrics.height(), metrics.lineSpacing()) + 10)
        self.assertGreater(panel.instruction.verticalScrollBar().maximum(), 0)
        self.assertEqual(panel.start_button.geometry().bottom(), panel.controls.rect().bottom())
        self.assertEqual(panel.close_button.geometry().bottom(), panel.controls.rect().bottom())
        panel.instruction.setPlainText('精簡')
        self.place(320)
        self.assertEqual(panel.height(), initial)
        self.assertFalse(panel.instruction.textCursor().hasSelection())
        self.assertEqual(panel.instruction.verticalScrollBar().maximum(), 0)

    def test_review_pill_uses_content_width_and_quiet_dropdown_without_completion_caption(self):
        self.start()
        self.controller.complete('候選')
        self.place()
        self.assertEqual(self.panel.status.text(), '')
        self.assertTrue(self.panel.status.isHidden())
        self.assertLessEqual(self.panel.preferred_width(500), 280)
        self.assertEqual(self.panel.keep_menu_button.objectName(), 'Quiet')
        self.assertLessEqual(self.panel.keep_menu_button.width(), 26)
        self.assertLess(self.panel.delete_button.mapTo(self.panel, QPoint()).x(),
                        self.panel.keep_button.mapTo(self.panel, QPoint()).x())

    def test_large_font_narrow_empty_input_fits_the_full_wrapped_placeholder(self):
        previous = self.app.font()
        try:
            self.app.setFont(QFont('Microsoft JhengHei UI', 18))
            self.settings['ui_size'] = 18
            self.app.setPalette(widget_palette(self.settings))
            self.app.setStyleSheet(stylesheet(self.settings))
            self.place(320)
            editor = self.panel.instruction
            text = editor.placeholderText()
            self.assertEqual(text, '描述你想如何編輯…')
            bounds = editor.fontMetrics().boundingRect(QRect(0, 0, editor.viewport().width(), 1000),
                                                      Qt.TextFlag.TextWordWrap, text)
            self.assertGreater(bounds.height(), editor.fontMetrics().height())
            self.assertLessEqual(bounds.height(), editor.viewport().height())
            self.assertEqual(editor.verticalScrollBar().maximum(), 0)
            self.assertIs(self.app.focusWidget(), self.editor)
        finally:
            self.app.setFont(previous)


if __name__ == '__main__':
    unittest.main()
