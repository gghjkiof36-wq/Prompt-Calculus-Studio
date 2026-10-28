"""Per-attempt thinking captions; synthetic clock, no model or user files."""
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from PySide6.QtCore import QEvent, QPoint, Qt
from PySide6.QtGui import QColor, QFont, QPixmap, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_chat_hover_model_menu import Document
from prompt_calculus_studio.local_ai.chat_page import ChatPage


class ThinkingDurationUI(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = Document()
        self.doc.payload['turns'] = self.doc.payload['turns'][-1:]
        self.raw = self.doc.payload['turns'][0]['attempts'][0]
        self.raw.update(status='receiving', text='<think>先比較條件，再整理回覆。')
        self.doc.busy = True
        self.doc.generation_elapsed_seconds = 13.2
        self.seconds = {'a4': 3.2}
        self.doc.thinking_elapsed_seconds = lambda identity: self.seconds.get(identity)
        self.page = ChatPage(self.doc, SimpleNamespace(display_settings=lambda: dict(ui_size=12)))
        self.page.resize(900, 700)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def show(self):
        self.page.show()
        QTest.qWait(40)
        return self.page._turns['4'].attempts['a4']

    def test_live_second_tick_only_updates_captions(self):
        item = self.show()
        self.assertEqual(item.thinking_toggle.text(), '思考中 · 3 秒')
        self.assertFalse(hasattr(self.page, 'elapsed_label'))
        before = copy.deepcopy(self.doc.payload)
        with patch.object(self.page, 'refresh', wraps=self.page.refresh) as refresh, \
             patch.object(item.thinking_content, 'set_content', wraps=item.thinking_content.set_content) as content, \
             patch.object(item, '_sync_thinking', wraps=item._sync_thinking) as disclosure, \
             patch.object(item.thinking_toggle.motion, 'start', wraps=item.thinking_toggle.motion.start) as motion:
            self.seconds['a4'] = 4.2
            self.doc.generation_elapsed_seconds = 14.2
            QTest.qWait(1100)
            self.assertEqual(item.thinking_toggle.text(), '思考中 · 4 秒')
            self.assertFalse(hasattr(self.page, 'elapsed_label'))
            self.assertEqual(refresh.call_count, 0)
            self.assertEqual(content.call_count, 0)
            self.assertEqual(disclosure.call_count, 0)
            self.assertEqual(motion.call_count, 0)
        self.assertEqual(self.doc.payload, before)

    def test_closed_thinking_freezes_and_timer_stops_while_answer_continues(self):
        item = self.show()
        self.seconds['a4'] = 8.4
        self.raw['thinking_seconds'] = 8.4
        self.raw['text'] += '</think>\n這是正文。'
        self.doc.changed.emit()
        QTest.qWait(170)
        self.assertEqual(item.thinking_toggle.text(), '思考 · 8 秒')
        self.assertFalse(item.thinking_toggle._thinking)
        self.doc.generation_elapsed_seconds = 40.2
        self.page._update_elapsed()
        self.assertEqual(item.thinking_toggle.text(), '思考 · 8 秒')
        self.assertFalse(self.page._elapsed_timer.isActive())
        self.raw['status'] = 'complete'
        self.doc.busy = False
        self.doc.changed.emit()
        QTest.qWait(30)
        self.assertFalse(self.page._elapsed_timer.isActive())
        with patch.object(self.page, '_update_thinking_elapsed', wraps=self.page._update_thinking_elapsed) as update:
            QTest.qWait(1100)
            self.assertEqual(update.call_count, 0)
        self.assertEqual(item.thinking_toggle.text(), '思考 · 8 秒')

    def test_hidden_page_stops_clock_and_show_catches_up(self):
        item = self.show()
        self.page.hide()
        self.assertFalse(self.page._elapsed_timer.isActive())
        with patch.object(item.thinking_toggle, 'set_elapsed', wraps=item.thinking_toggle.set_elapsed) as update:
            self.seconds['a4'] = 15.9
            self.doc.changed.emit()
            QTest.qWait(1100)
            self.assertEqual(update.call_count, 0)
        self.page.show()
        QTest.qWait(40)
        self.assertEqual(item.thinking_toggle.text(), '思考中 · 15 秒')
        self.assertTrue(self.page._elapsed_timer.isActive())

    def test_attempts_keep_independent_times_and_old_history_has_no_zero(self):
        self.raw.update(status='complete', text='<think>歷史內容。</think>\n先前答案。', thinking_seconds=8.2)
        self.doc.payload['turns'][0]['attempts'].append(
            dict(id='retry', text='<think>再次比較。', status='receiving', model='one'))
        self.seconds = {'a4': 8.2, 'retry': 1.9}
        first = self.show()
        second = self.page._turns['4'].attempts['retry']
        self.assertEqual(first.thinking_toggle.text(), '思考 · 8 秒')
        self.assertEqual(second.thinking_toggle.text(), '思考中 · 1 秒')
        self.seconds.pop('a4')
        self.raw.pop('thinking_seconds')
        self.doc.changed.emit()
        QTest.qWait(170)
        self.assertEqual(first.thinking_toggle.text(), '思考')
        self.assertEqual(second.thinking_toggle.text(), '思考中 · 1 秒')

    def test_stored_history_displays_without_a_live_clock(self):
        del self.doc.thinking_elapsed_seconds
        self.raw.update(status='complete', text='<think>先前內容。</think>\n完成。', thinking_seconds=12.6)
        self.doc.busy = False
        self.doc.generation_elapsed_seconds = None
        item = self.show()
        self.assertEqual(item.thinking_toggle.text(), '思考 · 12 秒')
        self.assertFalse(self.page._elapsed_timer.isActive())
        self.assertFalse(hasattr(self.page, 'elapsed_label'))

    def test_only_live_attempt_queries_clock_among_completed_history(self):
        active = self.doc.payload['turns'][0]
        self.doc.payload['turns'] = [dict(id=f'old-{i}', text=f'先前問題 {i}', attempts=[
            dict(id=f'old-a{i}', text='<think>先前思考。</think>\n先前答案。',
                 status='complete', model='one', thinking_seconds=i + .5)]) for i in range(24)] + [active]
        measure = Mock(side_effect=lambda identity: self.seconds.get(identity))
        self.doc.thinking_elapsed_seconds = measure
        self.show()
        measure.reset_mock()
        self.page._update_elapsed()
        measure.assert_called_once_with('a4')
        self.raw.update(status='complete', thinking_seconds=3.2,
                        text='<think>先比較條件，再整理回覆。</think>\n完成。')
        self.doc.busy = False
        self.doc.changed.emit()
        QTest.qWait(30)
        measure.reset_mock()
        self.page._update_elapsed()
        measure.assert_not_called()
        self.assertFalse(self.page._elapsed_timer.isActive())

    def test_large_finite_history_timing_keeps_caption_bounded(self):
        self.doc.busy = False
        self.raw.update(status='complete', thinking_seconds=1e308,
                        text='<think>歷史內容。</think>\n完成。')
        self.page.resize(500, 700)
        item = self.show()
        toggle = item.thinking_toggle
        self.assertEqual(toggle.text(), '思考 · 1e+308 秒')
        self.assertLess(toggle.sizeHint().width(), 240)
        self.assertLessEqual(toggle.width(), self.page.width())
        self.assertEqual(self.raw['thinking_seconds'], 1e308)

    def test_duration_preserves_reading_edge_disclosure_and_copy(self):
        item = self.show()
        toggle = item.thinking_toggle
        self.page.resize(500, 700)
        QTest.qWait(50)
        self.assertEqual(toggle.mapToGlobal(QPoint()).x(), item.content.mapToGlobal(QPoint()).x())
        original = item.thinking_content._content
        checked = toggle.isChecked()
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        QTest.qWait(220)
        self.assertNotEqual(toggle.isChecked(), checked)
        self.page._update_elapsed()
        self.assertNotEqual(toggle.isChecked(), checked)
        self.assertEqual(item.thinking_content._content, original)
        QTest.mouseClick(toggle, Qt.MouseButton.LeftButton)
        QTest.qWait(220)
        QTest.mouseClick(item.thinking_copy, Qt.MouseButton.LeftButton)
        self.assertEqual(self.app.clipboard().text(), '先比較條件，再整理回覆。')
        self.assertEqual(toggle.text(), '思考中 · 3 秒')

    def test_caption_glyphs_are_complete_for_fonts_sizes_and_narrow_layout(self):
        item = self.show()
        toggle = item.thinking_toggle
        self.page._elapsed_timer.stop()
        self.doc.busy = False
        self.page.resize(500, 700)
        self.page.composer.setFocus()
        QTest.mouseMove(self.page.composer, QPoint(20, 15))
        for family in ('Microsoft JhengHei UI', 'Segoe UI'):
            for points in (11, 12, 18):
                for receiving in (False, True):
                    with self.subTest(family=family, points=points, receiving=receiving):
                        toggle._system_motion = False
                        toggle.setFont(QFont(family, points))
                        toggle.set_thinking(receiving)
                        toggle.set_elapsed(123.8)
                        QTest.qWait(20)
                        toggle.resize(toggle.sizeHint().width(), toggle.minimumHeight())
                        image = toggle.grab().toImage()
                        metrics = toggle.fontMetrics()
                        width = metrics.horizontalAdvance(toggle.text())
                        self.assertGreaterEqual(toggle.width(), width + toggle.iconSize().width() + 4)
                        title = '思考中' if receiving else '思考'
                        background = image.pixelColor(0, 0)
                        # Match QWidget.grab's raster surface; QImage has
                        # different glyph antialiasing for real Windows fonts.
                        reference = QPixmap(width + 2, 100)
                        reference.fill(background)
                        painter = QPainter(reference)
                        painter.setFont(toggle.font())
                        painter.setPen(QColor(toggle._tokens['text']))
                        painter.drawText(QPoint(0, 50), title)
                        painter.setPen(QColor(toggle._tokens['secondary']))
                        painter.drawText(QPoint(metrics.horizontalAdvance(title), 50), ' · 123 秒')
                        painter.end()
                        def ink(bitmap):
                            return sum(bitmap.pixelColor(x, y) != background
                                       for y in range(bitmap.height()) for x in range(width + 1))
                        self.assertEqual(ink(image), ink(reference.toImage()))


if __name__ == '__main__':
    unittest.main()
