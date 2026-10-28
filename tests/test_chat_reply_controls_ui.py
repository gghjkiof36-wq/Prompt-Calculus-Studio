"""Reply history stays with the current reply controls; no model or files."""
import copy
from types import SimpleNamespace
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel

from test_chat_hover_model_menu import Document
from prompt_calculus_studio.local_ai.chat_page import ChatPage


class ChatReplyControlsUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = Document()
        self.doc.payload['turns'] = [dict(id='one', text='請說明結果。', attempts=[
            dict(id=f'a{i}', text=f'<think>檢查第 {i + 1} 次內容。</think>\n第 {i + 1} 次回覆。',
                 status='complete', model='one', thinking_seconds=3. + i) for i in range(3)])]
        self.doc.generation_elapsed_seconds = 138.
        self.doc.thinking_elapsed_seconds = lambda identity: 7.3 if identity == 'retry' else None
        self.page = ChatPage(self.doc, SimpleNamespace(display_settings=lambda: dict(ui_size=12)))
        self.page.resize(700, 700)
        self.page.show()
        QTest.qWait(60)
        self.turn = self.page._turns['one']

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def change(self):
        self.doc.changed.emit()
        QTest.qWait(160)

    def click_history(self):
        self.page.scroll.ensureWidgetVisible(self.turn.history_button)
        QTest.qWait(20)
        QTest.mouseClick(self.turn.history_button, Qt.MouseButton.LeftButton)
        QTest.qWait(30)

    def test_history_icon_is_in_latest_reply_row_without_top_placeholder(self):
        latest = self.turn.attempts['a2']
        button = self.turn.history_button
        self.assertEqual(button.text(), '')
        self.assertFalse(button.icon().isNull())
        self.assertIs(button.parentWidget(), latest.actions)
        self.assertEqual(self.turn.stack.indexOf(button), -1)
        self.assertEqual(self.turn.stack.count(), 4)  # User plus the three replies.
        self.assertEqual(latest.actions.layout().indexOf(button), 3)
        self.assertLess(latest.actions.layout().indexOf(button), latest.actions.layout().indexOf(latest.more_button))
        self.assertEqual(button.toolTip(), '查看先前 2 次回覆')
        self.assertEqual(button.accessibleName(), button.toolTip())
        self.assertEqual(button.size().width(), 30)
        self.assertEqual(button.size().height(), 30)
        self.assertTrue(button.isVisible())
        self.assertFalse(hasattr(self.page, 'elapsed_label'))
        self.assertIsNone(self.page.findChild(QLabel, 'ChatElapsed'))
        self.assertEqual(latest.thinking_toggle.text(), '思考 · 5 秒')

    def test_history_expands_and_collapses_without_changing_saved_content(self):
        before = copy.deepcopy(self.doc.payload)
        self.assertFalse(self.turn.attempts['a0'].isVisible())
        self.click_history()
        self.assertTrue(all(attempt.isVisible() for attempt in self.turn.attempts.values()))
        self.assertEqual(self.turn.history_button.toolTip(), '收起先前 2 次回覆')
        self.click_history()
        self.assertFalse(self.turn.attempts['a0'].isVisible())
        self.assertTrue(self.turn.attempts['a2'].isVisible())
        self.assertEqual(self.doc.payload, before)

    def test_retry_moves_history_and_keeps_it_reachable_during_receiving_and_failure(self):
        old = self.turn.attempts['a2']
        self.doc.busy = True
        self.doc.payload['turns'][0]['attempts'].append(
            dict(id='retry', text='<think>重新檢查。', status='receiving', model='one'))
        self.change()
        current = self.turn.attempts['retry']
        self.assertIs(self.turn.history_button.parentWidget(), current.actions)
        self.assertEqual(old.actions.layout().indexOf(self.turn.history_button), -1)
        self.assertTrue(current.actions.isVisible())
        self.assertTrue(self.turn.history_button.isVisible())
        self.assertFalse(current.copy_button.isVisible())
        self.assertFalse(current.retry_icon_button.isVisible())
        self.assertFalse(current.branch_button.isVisible())
        self.assertFalse(current.more_button.isVisible())
        self.assertEqual(current.thinking_toggle.text(), '思考中 · 7 秒')
        self.click_history()
        self.assertTrue(old.isVisible())
        self.doc.busy = False
        raw = self.doc.payload['turns'][0]['attempts'][-1]
        for status in ('stopped', 'failed'):
            with self.subTest(status=status):
                raw.update(status=status, thinking_seconds=7.3, reason='fixture failure' if status == 'failed' else '生成已停止')
                self.change()
                self.assertTrue(self.turn.history_button.isVisible())
                self.assertTrue(current.retry_icon_button.isVisible())
                self.assertTrue(current.copy_button.isVisible())
                self.assertTrue(current.branch_button.isVisible())
                self.assertEqual(current.error_frame.isVisible(), status == 'failed')
        self.assertFalse(self.page._elapsed_timer.isActive())

    def test_no_previous_reply_has_no_history_icon_or_empty_header_row(self):
        self.doc.payload['turns'][0]['attempts'] = self.doc.payload['turns'][0]['attempts'][-1:]
        self.change()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(self.turn.history_button.isVisible())
        self.assertEqual(self.turn.stack.count(), 2)
        self.assertTrue(self.turn.attempts['a2'].copy_button.isVisible())

    def test_history_survives_replacing_or_removing_all_attempts(self):
        attempts = self.doc.payload['turns'][0]['attempts']
        self.doc.payload['turns'][0]['attempts'] = []
        self.change()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertFalse(self.turn.history_button.isVisible())
        self.assertIs(self.turn.history_button.parentWidget(), self.turn)
        self.doc.payload['turns'][0]['attempts'] = attempts
        self.change()
        self.assertTrue(self.turn.history_button.isVisible())
        self.assertIs(self.turn.history_button.parentWidget(), self.turn.attempts['a2'].actions)

    def test_narrow_reply_row_preserves_icon_alignment_and_keyboard_access(self):
        self.page.resize(500, 700)
        QTest.qWait(50)
        latest = self.turn.attempts['a2']
        history = self.turn.history_button
        self.assertEqual(history.geometry().y(), latest.copy_button.geometry().y())
        self.assertLess(history.geometry().right(), latest.actions.width())
        history.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(history, Qt.Key.Key_Space)
        self.assertTrue(history.isChecked())
        self.assertTrue(self.turn.attempts['a0'].isVisible())

    def test_receiving_and_finished_controls_keep_the_same_reserved_height(self):
        for previous in (False, True):
            with self.subTest(previous=previous):
                attempts = [dict(id='active', text='短回覆。', status='receiving', model='one')]
                if previous:
                    attempts.insert(0, dict(id='prior', text='較早回覆。', status='complete', model='one'))
                self.doc.payload['turns'][0]['attempts'] = attempts
                self.doc.busy = True
                self.change()
                current = self.turn.attempts['active']
                before = current.actions.height()
                self.assertGreaterEqual(before, 30)
                self.assertEqual(current.actions.isVisible(), previous)
                attempts[-1]['status'] = 'complete'
                self.doc.busy = False
                self.change()
                self.assertEqual(current.actions.height(), before)


if __name__ == '__main__':
    unittest.main()
