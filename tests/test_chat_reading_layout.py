"""Chat scrolls behind the composer without losing the last reply or draft."""
import unittest
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QPoint
from PySide6.QtGui import QFont
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from test_chat_hover_model_menu import Document
from prompt_calculus_studio.local_ai.chat_page import ChatPage, ChatText
from prompt_calculus_studio.local_ai.chat_typography import body_families, CODE_FAMILIES


class ChatReadingLayout(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.doc = Document()
        self.page = ChatPage(self.doc, SimpleNamespace(display_settings=lambda: dict(ui_size=12)))
        self.page.resize(1200, 800)
        self.page.show()
        QTest.qWait(80)

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)

    def test_full_height_viewport_and_click_through_gutters(self):
        for width in (1200, 620):
            with self.subTest(width=width):
                self.page.resize(width, 800)
                QTest.qWait(60)
                viewport = self.page.scroll.viewport()
                bottom = viewport.mapTo(self.page, QPoint(0, viewport.height())).y()
                frame = self.page.composer_frame
                self.assertLessEqual(frame.mapTo(self.page, QPoint(0, frame.height())).y(), bottom)
                host = self.page.composer_host
                self.assertFalse(host.mask().contains(QPoint(1, host.height() // 2)))
                self.assertTrue(host.mask().contains(self.page.composer_column.geometry().center()))

    def test_expanding_draft_keeps_last_actions_above_composer(self):
        draft = '\n'.join(f'草稿 {i}' for i in range(12))
        self.page.composer.setPlainText(draft)
        QTest.qWait(80)
        self.page._follow_output = True
        self.page._scroll_to_bottom()
        QTest.qWait(40)
        latest = self.page._turns['4'].attempts['a4']
        actions_bottom = latest.actions.mapTo(self.page, QPoint(0, latest.actions.height())).y()
        composer_top = self.page.composer_frame.mapTo(self.page, QPoint()).y()
        self.assertLess(actions_bottom + 12, composer_top)
        self.assertEqual(self.page.composer.toPlainText(), draft)
        self.assertEqual(self.doc.payload['draft'], draft)

    def test_empty_to_conversation_keeps_draft_and_composer_visible(self):
        self.doc.payload['turns'] = []
        self.doc.payload['draft'] = '尚未送出的文字'
        self.page.refresh()
        QTest.qWait(60)
        self.assertTrue(self.page.empty_heading.isVisible())
        self.assertFalse(self.page.scroll.isVisible())
        self.assertTrue(self.page.composer.isVisible())
        self.assertLess(self.page.composer_frame.mapTo(self.page, QPoint()).y(), self.page.height() * .75)
        self.doc.payload['turns'] = Document().payload['turns']
        self.page.refresh()
        QTest.qWait(60)
        self.assertTrue(self.page.scroll.isVisible())
        self.assertEqual(self.page.composer.toPlainText(), '尚未送出的文字')

    def test_keyboard_focus_reveals_actions_hidden_by_floating_input(self):
        self.page._follow_output = False
        bar = self.page.scroll.verticalScrollBar()
        bar.setValue(max(0, bar.maximum() - 130))
        control = self.page._turns['4'].attempts['a4'].copy_button
        self.page._keep_control_visible(None, control)
        QTest.qWait(40)
        bottom = control.mapTo(self.page, QPoint(0, control.height())).y()
        self.assertLess(bottom, self.page.composer_frame.mapTo(self.page, QPoint()).y())

    def test_browser_clip_keeps_full_transcript_height_when_covered(self):
        from PySide6.QtWidgets import QWidget
        widget = ChatText()
        try:
            widget.resize(500, 400)
            widget.rich = QWidget(widget)
            widget.rich.setGeometry(widget.rect())
            widget.set_reading_bottom(100)
            self.assertEqual(widget.rich.height(), 100)
            self.assertEqual(widget.height(), 400)
            widget.set_reading_bottom(0)
            self.assertEqual(widget.rich.height(), 0)
            self.assertEqual(widget.height(), 400)
        finally:
            widget.deleteLater()

    def test_native_inline_code_and_body_share_explicit_language_fallbacks(self):
        widget = ChatText()
        try:
            font = QFont('Arial', 13)
            widget.setFont(font)
            source = '正文 `docs/development/中文.md`\n\n結尾。'
            widget.set_content(source)
            self.assertEqual(body_families(font)[0], 'Arial')
            self.assertIn('Microsoft JhengHei UI', widget.document().defaultFont().families())
            block = widget.document().begin()
            fragment = block.begin()
            codes = []
            while not fragment.atEnd():
                value = fragment.fragment()
                if value.isValid() and value.charFormat().fontFixedPitch():
                    codes.append(value.charFormat().fontFamilies())
                fragment += 1
            self.assertTrue(codes)
            self.assertEqual(codes[0], list(CODE_FAMILIES))
            self.assertEqual(widget._content[0], source)
        finally:
            widget.deleteLater()


if __name__ == '__main__':
    unittest.main()
