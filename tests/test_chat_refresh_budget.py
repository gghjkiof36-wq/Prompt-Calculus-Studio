"""Visible-only, coalesced chat projection; no engine, model or user files."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_chat_hover_model_menu import Document
from prompt_calculus_studio.local_ai.chat_page import ChatPage, ChatText
from prompt_calculus_studio.local_ai.attachment_strip import AttachmentStrip
from prompt_calculus_studio.theme import visual_tokens


class ChatRefreshBudget(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = Document()
        self.doc.payload['turns'] = self.doc.payload['turns'][-1:]
        self.raw = self.doc.payload['turns'][-1]['attempts'][-1]
        self.raw.update(text='', status='receiving')
        self.doc.busy = True
        self.page = ChatPage(self.doc, SimpleNamespace(display_settings=lambda: dict(ui_size=12)))
        self.page.resize(900, 700)

    def tearDown(self):
        self.page.close(); self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def show(self):
        self.page.show(); QTest.qWait(30)
        return self.page._turns['4'].attempts['a4']

    def test_hidden_views_skip_projection_and_show_latest_snapshot(self):
        self.assertFalse(self.page._turns)
        item = self.show()
        self.page.hide()
        with patch.object(item, 'update_attempt', wraps=item.update_attempt) as update:
            for i in range(40):
                self.raw['text'] += f'新增 {i}。'
                self.doc.changed.emit()
            QTest.qWait(170)
            self.assertEqual(update.call_count, 0)
            self.assertFalse(self.page._refresh_timer.isActive())
            self.assertFalse(self.page._visibility_timer.isActive())
            self.doc.busy = False; self.raw['status'] = 'complete'
            self.doc.changed.emit()
            self.page.show(); QTest.qWait(40)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(item.content._content[0], self.raw['text'])

    def test_first_fragment_is_immediate_and_stream_coalesces_latest(self):
        item = self.show()
        self.raw['text'] = '第一段'
        self.doc.changed.emit()
        self.assertEqual(self.page._refresh_timer.interval(), 0)
        QTest.qWait(20)
        self.assertEqual(item.content._content[0], '第一段')
        with patch.object(item, 'update_attempt', wraps=item.update_attempt) as update:
            for i in range(30):
                self.raw['text'] += f'，{i}'
                self.doc.changed.emit()
            self.assertEqual(self.page._refresh_timer.interval(), 120)
            self.assertEqual(update.call_count, 0)
            QTest.qWait(160)
            self.assertEqual(update.call_count, 1)
            self.assertEqual(item.content._content[0], self.raw['text'])

    def test_terminal_bypasses_pending_long_stream_delay(self):
        item = self.show()
        self.raw['text'] = '已有內容'; self.doc.changed.emit(); QTest.qWait(20)
        self.raw['text'] = '長內容。' * 6000
        self.doc.changed.emit()
        self.assertEqual(self.page._refresh_timer.interval(), 350)
        self.raw.update(status='stopped', reason='生成已停止')
        self.doc.busy = False; self.doc.changed.emit()
        self.assertEqual(self.page._refresh_timer.interval(), 0)
        QTest.qWait(40)
        self.assertEqual(item._status, 'stopped')
        self.assertEqual(item.content._content[0], self.raw['text'])

    def test_hidden_stream_preserves_selection_then_catches_up_when_released(self):
        item = self.show()
        self.raw['text'] = '保留可選取的文字'; self.doc.changed.emit(); QTest.qWait(20)
        cursor = item.content.textCursor()
        cursor.select(QTextCursor.SelectionType.Document)
        item.content.setTextCursor(cursor)
        self.page.hide()
        self.raw['text'] += '，稍後更新。'; self.doc.changed.emit()
        self.page.show(); QTest.qWait(40)
        self.assertEqual(item.content.textCursor().selectedText(), '保留可選取的文字')
        self.assertEqual(item.content._pending[0], self.raw['text'])
        cursor = item.content.textCursor(); cursor.clearSelection()
        item.content.setTextCursor(cursor)
        self.assertEqual(item.content._content[0], self.raw['text'])

    def test_unchanged_attachment_colors_do_not_reapply_styles(self):
        strip = AttachmentStrip()
        tokens = visual_tokens()
        with patch.object(strip, 'setStyleSheet', wraps=strip.setStyleSheet) as style:
            strip.apply_appearance(tokens)
            strip.apply_appearance(dict(tokens))
            self.assertEqual(style.call_count, 1)
            changed = dict(tokens, accent='#abcdef')
            strip.apply_appearance(changed)
            self.assertEqual(style.call_count, 2)
        strip.deleteLater()

    def test_native_height_does_not_relayout_during_formatting_or_when_unchanged(self):
        text = ChatText(); text.resize(500, 80)
        with patch.object(text, 'setFixedHeight', wraps=text.setFixedHeight) as resize:
            text._setting_text = True
            text._fit_height()
            self.assertEqual(resize.call_count, 0)
            text._setting_text = False
            text.set_content('段落。\n\n' * 40)
            count = resize.call_count
            text._fit_height(); text._fit_height()
            self.assertEqual(resize.call_count, count)
        text.deleteLater()

    def test_visible_elapsed_timer_updates_only_caption_and_survives_page_switch(self):
        self.doc.generation_elapsed_seconds = 12.
        self.show()
        self.assertEqual(self.page.elapsed_label.text(), '0:12')
        self.assertTrue(self.page._elapsed_timer.isActive())
        with patch.object(self.page, 'refresh', wraps=self.page.refresh) as refresh:
            self.doc.generation_elapsed_seconds = 13.
            QTest.qWait(1100)
            self.assertEqual(self.page.elapsed_label.text(), '0:13')
            self.assertEqual(refresh.call_count, 0)
        self.page.hide()
        self.assertFalse(self.page._elapsed_timer.isActive())
        self.doc.generation_elapsed_seconds = 65.
        self.page.show(); QTest.qWait(30)
        self.assertEqual(self.page.elapsed_label.text(), '1:05')
        self.doc.busy = False; self.doc.generation_elapsed_seconds = 65.
        self.doc.changed.emit(); QTest.qWait(20)
        self.assertTrue(self.page.elapsed_label.isVisible())
        self.assertEqual(self.page.elapsed_label.text(), '1:05')
        self.assertFalse(self.page._elapsed_timer.isActive())
        with patch.object(self.page, 'refresh', wraps=self.page.refresh) as refresh:
            QTest.qWait(1100)
            self.assertEqual(refresh.call_count, 0)
            self.assertEqual(self.page.elapsed_label.text(), '1:05')
        self.page.hide(); self.page.show(); QTest.qWait(30)
        self.assertEqual(self.page.elapsed_label.text(), '1:05')
        self.assertFalse(self.page._elapsed_timer.isActive())
        self.doc.generation_elapsed_seconds = None
        self.doc.changed.emit(); QTest.qWait(20)
        self.assertFalse(self.page.elapsed_label.isVisible())
        self.assertFalse(self.page._elapsed_timer.isActive())


if __name__ == '__main__':
    unittest.main()
