"""Actual Qt input boundaries: MIME, IME, wrapped text and emoji."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from PySide6.QtCore import QMimeData, QUrl, Qt
from PySide6.QtGui import QImage, QColor, QInputMethodEvent, QKeyEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest
from prompt_calculus_studio.local_ai.chat_page import ChatComposer, ChatTurn
from prompt_calculus_studio.theme import visual_tokens


class ComposerInputContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.composer = ChatComposer()
        self.composer.resize(400, 80)
        self.composer.show()
        self.app.processEvents()

    def tearDown(self):
        self.composer.close()
        self.composer.deleteLater()
        self.app.processEvents()

    def test_long_wrapped_single_paragraph_grows_then_scrolls(self):
        self.composer.setPlainText('很長的單一段落文字 ' * 300)
        self.app.processEvents()
        self.assertEqual(self.composer.height(), 200)
        self.assertGreater(self.composer.verticalScrollBar().maximum(), 0)
        self.composer.setPlainText('短句')
        self.app.processEvents()
        self.assertEqual(self.composer.height(), 80)

    def test_rich_clipboard_keeps_plain_text_and_emoji(self):
        text = '🙂👍🏽👨‍👩‍👧‍👦🇹🇼❤️'
        mime = QMimeData(); mime.setText(text); mime.setHtml('<b>' + text + '</b>')
        self.composer.insertFromMimeData(mime)
        self.assertEqual(self.composer.toPlainText(), text)

    def test_inline_editor_has_independent_compact_height_and_scroll_limit(self):
        editor = ChatComposer(minimum_height=32)
        editor.resize(400, 32)
        editor.show()
        editor.setPlainText('修改這一句')
        self.app.processEvents()
        self.assertLessEqual(editor.height(), 40)
        editor.setPlainText('單段長文字 ' * 300)
        self.app.processEvents()
        self.assertEqual(editor.height(), 200)
        self.assertGreater(editor.verticalScrollBar().maximum(), 0)
        self.assertEqual(self.composer.height(), 80)
        editor.close()
        editor.deleteLater()

    def test_files_and_images_emit_snapshots_without_inserting_paths(self):
        values=[]; self.composer.attachmentsRequested.connect(values.append)
        mime=QMimeData(); mime.setUrls([QUrl.fromLocalFile('C:/not-read/example.txt')])
        self.composer.insertFromMimeData(mime)
        self.assertEqual(values[0], ['C:/not-read/example.txt'])
        image=QImage(8,8,QImage.Format.Format_RGB32); image.fill(QColor('red'))
        mime=QMimeData(); mime.setImageData(image)
        self.composer.insertFromMimeData(mime)
        snapshot = values[1][0]['image']
        self.assertIsInstance(snapshot, QImage)
        self.assertEqual(snapshot.pixelColor(0, 0), QColor('red'))
        image.fill(QColor('blue'))
        self.assertEqual(snapshot.pixelColor(0, 0), QColor('red'))
        self.assertEqual(self.composer.toPlainText(), '')

    def test_ime_enter_commit_does_not_send_and_shift_return_keeps_newline(self):
        sent=[]; self.composer.submitted.connect(lambda:sent.append(True))
        self.composer.inputMethodEvent(QInputMethodEvent('測', []))
        self.assertTrue(self.composer.composing)
        self.assertEqual(self.composer.placeholderText(), '')
        self.composer.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        event=QInputMethodEvent(); event.setCommitString('測')
        self.composer.inputMethodEvent(event)
        self.composer.keyPressEvent(QKeyEvent(QKeyEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier))
        self.assertFalse(sent)
        self.app.processEvents()
        QTest.keyClick(self.composer, Qt.Key.Key_Return, Qt.KeyboardModifier.ShiftModifier)
        self.assertEqual(self.composer.toPlainText(), '測\n')
        QTest.keyClick(self.composer, Qt.Key.Key_Return)
        self.assertEqual(sent, [True])

    def test_user_copy_preserves_original_and_edit_is_separate(self):
        original = '  原文 🙂👍🏽👨‍👩‍👧‍👦🇹🇼❤️\n第二行  '
        turn = ChatTurn()
        turn.resize(640, 200)
        turn.update_turn(dict(id='stable-turn', text=original, attempts=[]), {}, visual_tokens())
        branches = []
        edits = []
        turn.branchRequested.connect(lambda *args: branches.append(args))
        turn.editRequested.connect(edits.append)
        turn.show()
        self.app.processEvents()
        turn.user_copy_button.click()
        self.assertEqual(self.app.clipboard().text(), original)
        self.assertFalse(branches)
        turn.user_edit_button.click()
        self.assertEqual(edits, ['stable-turn'])
        self.assertFalse(branches)
        turn.begin_edit(True)
        self.assertEqual(turn.editor.toPlainText(), original)
        turn.editor.setPlainText('修改內容')
        turn.cancel_edit()
        self.assertEqual(turn._user_source, original)
        turn.close()
        turn.deleteLater()
        self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
