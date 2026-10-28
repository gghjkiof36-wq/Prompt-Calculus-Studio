"""Bounded disclosure geometry/scroll tests; synthetic text, no engine or files."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QFont, QPixmap, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_chat_hover_model_menu import Document
from prompt_calculus_studio.local_ai.chat_page import ChatPage


MODULE = 'prompt_calculus_studio.local_ai.chat_page.'


class ThinkingDisclosure(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.motion = patch(MODULE + 'system_motion_enabled', return_value=True)
        self.motion.start()
        self.doc = Document()
        self.doc.payload['turns'][0]['attempts'][0]['text'] = '<think>' + ('固定段落與閱讀位置。\n\n' * 16) + '</think>\n正式回答。'
        self.page = ChatPage(self.doc, SimpleNamespace(display_settings=lambda: dict(ui_size=12)))
        self.page.resize(850, 750); self.page.show(); QTest.qWait(100)
        self.page._follow_output = False
        self.bar = self.page.scroll.verticalScrollBar()
        self.bar.setValue(0)
        self.item = self.page._turns['0'].attempts['a0']
        self.body, self.toggle = self.item.thinking_body, self.item.thinking_toggle
        QTest.qWait(30)

    def tearDown(self):
        self.page.close(); self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.motion.stop()

    def click(self):
        QTest.mouseClick(self.toggle, Qt.MouseButton.LeftButton)

    def samples(self):
        result = []
        for _ in range(16):
            result.append((self.body.height(), self.item.thinking_content.height(),
                           self.item.thinking_content.mapTo(self.body, QPoint()).y(),
                           self.toggle.mapTo(self.page.scroll.viewport(), QPoint()).y()))
            QTest.qWait(16)
        return result

    def test_collapse_does_not_clear_then_rebound_or_reflow_text(self):
        self.click(); QTest.qWait(220)
        before = self.body.height()
        self.click()
        self.assertEqual(self.body.height(), before)
        frames = self.samples()
        heights = [f[0] for f in frames]
        self.assertEqual(heights, sorted(heights, reverse=True))
        self.assertEqual(heights[-1], 0)
        self.assertEqual(len({f[1:] for f in frames}), 1, frames)
        self.assertFalse(self.body.reveal.state() == self.body.reveal.State.Running)

    def test_manual_expand_preserves_reading_position_instead_of_following_bottom(self):
        self.page._follow_output = True
        self.click()
        frames = self.samples()
        self.assertEqual(self.bar.value(), 0)
        self.assertFalse(self.page._follow_output)
        self.assertFalse(self.page._manual_disclosures)
        self.assertEqual(len({f[3] for f in frames}), 1)
        self.assertGreater(self.bar.maximum(), 0)

    def test_rapid_reverse_starts_at_current_visible_height(self):
        self.click(); QTest.qWait(64)
        middle = self.body.height()
        self.assertGreater(middle, 0)
        self.click(); self.assertEqual(self.body.height(), middle)
        QTest.qWait(48)
        middle = self.body.height()
        self.click(); self.assertEqual(self.body.height(), middle)
        QTest.qWait(220)
        self.assertEqual(self.body.height(), self.body._content_height() + 8)
        self.assertFalse(self.page._manual_disclosures)

    def test_reduced_motion_is_immediate_and_hiding_stops_animation(self):
        with patch(MODULE + 'reduced_motion', return_value=True):
            self.click()
            self.assertEqual(self.body.height(), self.body._content_height() + 8)
            self.assertFalse(self.body.reveal.state() == self.body.reveal.State.Running)
            self.click(); self.assertEqual(self.body.height(), 0)
        self.click(); QTest.qWait(32)
        self.page.hide(); QTest.qWait(20)
        self.assertFalse(self.body.reveal.state() == self.body.reveal.State.Running)
        self.assertFalse(self.page._disclosure_settle.isActive())
        self.assertFalse(self.page._manual_disclosures)

    def test_completion_collapses_once_and_later_refresh_preserves_manual_choice(self):
        turn = self.page._turns['4']
        raw = '<think>' + '串流思考。\n\n' * 15 + '</think>\n正式回答。'
        receiving = dict(id='new', text=raw, status='receiving', model='one')
        data = dict(id='4', text='繼續', attempts=[receiving])
        turn.update_turn(data, {}, self.page._tokens); QTest.qWait(220)
        item = turn.attempts['new']; self.assertTrue(item.thinking_toggle.isChecked())
        self.page._follow_output = True
        complete = dict(receiving, status='complete')
        height = item.thinking_body.height()
        answer_y = item.content.mapTo(item, QPoint()).y()
        item.update_attempt(complete, 0, 1, {})
        self.assertEqual(item.thinking_body.height(), height)
        self.assertEqual(item.content.mapTo(item, QPoint()).y(), answer_y)
        QTest.qWait(220)
        self.assertEqual(item.thinking_body.height(), 0)
        self.assertEqual(self.bar.value(), self.bar.maximum())
        QTest.mouseClick(item.thinking_toggle, Qt.MouseButton.LeftButton); QTest.qWait(220)
        item.update_attempt(complete, 0, 1, {})
        self.assertTrue(item.thinking_toggle.isChecked())
        self.assertGreater(item.thinking_body.height(), 0)

    def test_reading_axis_and_content_width_affordance_are_preserved(self):
        self.assertEqual(self.toggle.mapToGlobal(QPoint()).x(), self.item.content.mapToGlobal(QPoint()).x())
        natural = self.toggle.fontMetrics().horizontalAdvance(self.toggle.text()) + self.toggle.iconSize().width()
        self.assertLessEqual(self.toggle.width(), natural + 12)
        self.toggle.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(self.toggle, Qt.Key.Key_Space); QTest.qWait(220)
        self.assertTrue(self.toggle.isChecked())
        self.page.resize(500, 750); QTest.qWait(100)
        self.assertEqual(self.body.height(), self.body._content_height() + 8)
        self.assertEqual(self.item.thinking_content.mapTo(self.body, QPoint()).y(), 8)

    def test_compact_header_preserves_complete_cjk_glyphs_with_style_padding(self):
        self.toggle.setStyleSheet('QPushButton { padding:8px 14px; border:1px solid transparent; }')
        self.page.composer.setFocus()
        QTest.mouseMove(self.page.composer, QPoint(20, 15))
        for points in (11, 12, 18):
            with self.subTest(points=points):
                font = QFont(self.toggle.font()); font.setPointSize(points)
                self.toggle.setFont(font); QTest.qWait(30)
                # Use the compressed size that long transcripts actually get.
                self.toggle.resize(self.toggle.sizeHint().width(), self.toggle.minimumHeight())
                image = self.toggle.grab().toImage()
                width = self.toggle.fontMetrics().horizontalAdvance(self.toggle.text())
                background = image.pixelColor(0, 0)
                reference = QPixmap(width + 2, 100)
                reference.fill(background)
                painter = QPainter(reference)
                painter.setFont(self.toggle.font())
                painter.setPen(QColor(self.toggle._tokens['text']))
                # A generously sized reference must contain every glyph row.
                painter.drawText(QPoint(0, 50), self.toggle.text()); painter.end()
                def ink(bitmap):
                    return sum(bitmap.pixelColor(x, y) != background
                               for y in range(bitmap.height()) for x in range(width + 1))
                self.assertEqual(ink(image), ink(reference.toImage()))


if __name__ == '__main__':
    unittest.main()
