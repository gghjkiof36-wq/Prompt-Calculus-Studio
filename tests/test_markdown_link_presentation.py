"""Inactive links show complete labels; editing reveals intact source syntax."""
import os
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_source import utf16_position
from prompt_calculus_studio.workspace_files import MarkdownPage


class LinkPresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        for name in ('msjh.ttc', 'msjhbd.ttc', 'segoeui.ttf'):
            font = Path('C:/Windows/Fonts') / name
            if font.is_file():
                QFontDatabase.addApplicationFont(str(font))
        cls.app.setFont(QFont('Microsoft JhengHei UI', 12))

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-link-presentation-')
        self.path = Path(self.area.name) / '連結範例.md'
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.close()
            page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.area.cleanup()

    def open(self, text):
        raw = b'\xef\xbb\xbf' + text.replace('\n', '\r\n').encode('utf-8')
        self.path.write_bytes(raw)
        page = MarkdownPage(self.path)
        self.pages.append(page)
        page.resize(1000, 620)
        page.show()
        self.events()
        return page, raw

    def events(self):
        self.app.processEvents()
        QTest.qWait(20)
        self.app.processEvents()

    def shown(self, editor, number):
        block = editor.document().findBlockByNumber(number)
        formats = block.layout().formats()
        output = []
        for at, character in enumerate(block.text()):
            offset = utf16_position(block.text(), at)
            hidden = any(item.start <= offset < item.start + item.length
                         and item.format.foreground().style() != Qt.BrushStyle.NoBrush
                         and item.format.foreground().color().alpha() == 0 for item in formats)
            if not hidden:
                output.append(character)
        return ''.join(output)

    def click_line(self, editor, number):
        cursor = QTextCursor(editor.document().findBlockByNumber(number))
        cursor.movePosition(QTextCursor.MoveOperation.Right, QTextCursor.MoveMode.MoveAnchor, 3)
        QTest.mouseClick(editor.viewport(), Qt.MouseButton.LeftButton,
                        pos=editor.cursorRect(cursor).center())
        self.events()

    def test_labels_have_no_unmatched_markers_and_clicked_line_reveals_exact_source(self):
        lines = ['其他段落', '', '[返回對話索引](對話.md)',
                 '圖片：[圖片](https://www.google.com/s2/favicons?domain=example.invalid&sz=32)',
                 '前文 [😀 標籤](https://example.invalid/a_(b) "說明") 後文',
                 '![替代文字](assets/picture.png)']
        page, raw = self.open('\n'.join(lines))
        editor = page.md_surface.editor
        for number, expected in ((2, '返回對話索引'), (3, '圖片：圖片'),
                                 (4, '前文 😀 標籤 後文'), (5, '替代文字')):
            with self.subTest(line=number):
                self.assertEqual(self.shown(editor, number), expected)
                self.click_line(editor, number)
                self.assertEqual(editor.textCursor().blockNumber(), number)
                self.assertEqual(self.shown(editor, number), lines[number])
                self.click_line(editor, 0)
                self.assertEqual(self.shown(editor, number), expected)
        self.assertEqual(page.encoded(), raw)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assertFalse(page.dirty)
        directory = os.environ.get('PCS_LINK_SCREENSHOTS')
        if directory:
            Path(directory).mkdir(parents=True, exist_ok=True)
            self.assertTrue(page.md_surface.grab().save(str(Path(directory) / 'link-labels.png')))

    def test_code_escaped_and_incomplete_links_remain_literal(self):
        lines = ['游標位置', '', '`[code](literal)`', r'\[escaped](literal)',
                 '[incomplete](missing', '](unpaired)', '[ref]: notes.md',
                 '[[Wiki]]', '```text', '[fenced](literal)', '```',
                 r'\![標籤](target)', '[標籤](target "broken\')']
        page, raw = self.open('\n'.join(lines))
        editor = page.md_surface.editor
        self.assertEqual(self.shown(editor, 2), '[code](literal)')
        self.assertEqual(self.shown(editor, 3), '[escaped](literal)')
        self.assertEqual(self.shown(editor, 11), '!標籤')
        for number in (4, 5, 6, 7, 9, 12):
            self.assertEqual(self.shown(editor, number), lines[number])
        self.assertEqual(page.encoded(), raw)

    def test_source_mode_save_reopen_and_undo_keep_original_link_target(self):
        lines = ['其他段落', '', '[Label](notes_a_(b).md "title")']
        page, raw = self.open('\n'.join(lines))
        surface = page.md_surface
        surface.set_mode('source')
        self.events()
        self.assertEqual(self.shown(surface.editor, 2), lines[2])
        surface.set_mode('write')
        self.events()
        self.assertEqual(self.shown(surface.editor, 2), 'Label')
        self.click_line(surface.editor, 2)
        QTest.keyClicks(surface.editor, 'X')
        self.events()
        changed = page.encoded()
        self.assertNotEqual(changed, raw)
        self.assertIn(b'(notes_a_(b).md "title")', changed)
        self.assertTrue(page.save())
        reopened = MarkdownPage(self.path)
        self.pages.append(reopened)
        self.assertEqual(reopened.encoded(), changed)
        page.undo()
        self.assertEqual(page.encoded(), raw)
        page.redo()
        self.assertEqual(page.encoded(), changed)


if __name__ == '__main__':
    unittest.main()
