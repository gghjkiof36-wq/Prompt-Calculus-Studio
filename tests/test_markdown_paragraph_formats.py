"""Paragraph reuse preserves independent block formats and source history."""
from pathlib import Path
import tempfile
import unittest

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QTextBlockFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_source import SourceEdit
from prompt_calculus_studio.workspace_files import MarkdownPage


class MarkdownParagraphFormatTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.path = self.root / 'Paragraphs.md'
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.close()
            page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.tmp.cleanup()

    def open(self, raw, buffer=None):
        if buffer is None:
            self.path.write_bytes(raw.encode('utf-8') if isinstance(raw, str) else raw)
        page = MarkdownPage(self.path, buffer)
        self.pages.append(page)
        page.resize(880, 620)
        page.show()
        self.app.processEvents()
        return page

    def block(self, editor, number):
        block = editor.document().findBlockByNumber(number)
        self.assertTrue(block.isValid(), number)
        return block

    def seed(self, editor, number, **values):
        """Give the real document an independently owned, non-default format."""
        block = self.block(editor, number)
        fmt = QTextBlockFormat(block.blockFormat())
        for name, value in values.items():
            getattr(fmt, 'set' + name)(value)
        was_syncing = editor.syncing
        editor.syncing = True
        try:
            QTextCursor(block).setBlockFormat(fmt)
        finally:
            editor.syncing = was_syncing
        return QTextBlockFormat(block.blockFormat())

    def assert_geometry(self, editor, number, *, left=0, alignment=Qt.AlignmentFlag.AlignLeft,
                        top=0, bottom=0, height=150):
        fmt = self.block(editor, number).blockFormat()
        self.assertEqual(fmt.leftMargin(), left, number)
        self.assertEqual(fmt.alignment(), alignment, number)
        self.assertEqual(fmt.topMargin(), top, number)
        self.assertEqual(fmt.bottomMargin(), bottom, number)
        self.assertEqual(fmt.lineHeight(), height, number)
        self.assertEqual(fmt.lineHeightType(), QTextBlockFormat.LineHeightTypes.ProportionalHeight.value)

    def test_same_geometry_keeps_distinct_uncontrolled_properties_and_repeated_formats(self):
        page = self.open('alpha\nbeta\ngamma\ndelta')
        editor = page.md_surface.editor
        property_id = QTextFormat.Property.UserProperty.value + 17
        expected = [(11, True, 'first'), (11, True, 'first'),
                    (29, False, 'second'), (11, True, 'first')]
        for number, (right, keep, marker) in enumerate(expected):
            fmt = self.seed(editor, number, RightMargin=right, NonBreakableLines=keep,
                            LeftMargin=91, TopMargin=77)
            fmt.setProperty(property_id, marker)
            QTextCursor(self.block(editor, number)).setBlockFormat(fmt)
        editor.update_paragraphs()
        for number, (right, keep, marker) in enumerate(expected):
            self.assert_geometry(editor, number)
            fmt = self.block(editor, number).blockFormat()
            self.assertEqual((fmt.rightMargin(), fmt.nonBreakableLines(), fmt.property(property_id)),
                             (right, keep, marker))
        # A later call must not reuse a format from a previous invocation.
        self.seed(editor, 1, RightMargin=47, NonBreakableLines=False, LeftMargin=91)
        editor.update_paragraphs()
        self.assertEqual(self.block(editor, 1).blockFormat().rightMargin(), 47)
        self.assertFalse(self.block(editor, 1).blockFormat().nonBreakableLines())
        self.assertEqual(self.block(editor, 0).blockFormat().rightMargin(), 11)
        self.assertFalse(page.dirty)
        self.assertFalse(page.document().isUndoAvailable())

    def test_heading_list_quote_and_only_full_line_alignment_have_their_own_geometry(self):
        page = self.open('\n'.join((
            '# First', 'plain', '## Later', '- item', '> quote',
            '<p style="margin-left:3em;text-align:right">整行 😀</p>',
            '<span style="text-align:center">center</span>',
            '<div style="text-align:justify">justify</div>',
            'prefix <p style="margin-left:3em;text-align:right">partial</p>',
            ' <p style="margin-left:3em;text-align:right">leading space</p>',
            '<p style="margin-left:3em;text-align:right">partial</p> suffix',
            'last')))
        editor = page.md_surface.editor
        editor.update_paragraphs()
        self.assert_geometry(editor, 0, top=2, bottom=6, height=120)
        self.assert_geometry(editor, 1)
        self.assert_geometry(editor, 2, top=6, bottom=6, height=120)
        self.assert_geometry(editor, 3, left=22)
        self.assert_geometry(editor, 4, left=22)
        self.assert_geometry(editor, 5, left=3 * editor.fontMetrics().horizontalAdvance('M'),
                             alignment=Qt.AlignmentFlag.AlignRight)
        self.assert_geometry(editor, 6, alignment=Qt.AlignmentFlag.AlignHCenter)
        self.assert_geometry(editor, 7, alignment=Qt.AlignmentFlag.AlignJustify)
        for number in (8, 9, 10, 11):
            self.assert_geometry(editor, number)

    def test_mode_preferences_and_zoom_recompute_without_losing_uncontrolled_properties(self):
        page = self.open('# Title\n- item\n> quote\n<p style="margin-left:2em;text-align:right">line</p>\nlast')
        surface = page.md_surface
        editor = surface.editor
        for number in range(5):
            self.seed(editor, number, RightMargin=number + 7, NonBreakableLines=True)
        surface.set_mode('source')
        for number in range(5):
            self.assert_geometry(editor, number, height=130)
        surface.set_preference('line_height', 190)
        self.assert_geometry(editor, 4, height=130)
        surface.set_mode('write')
        surface.set_preference('reading_size', 18)
        surface.set_zoom(150)
        self.assert_geometry(editor, 0, top=2, bottom=6, height=120)
        self.assert_geometry(editor, 1, left=22, height=190)
        self.assert_geometry(editor, 2, left=22, height=190)
        self.assert_geometry(editor, 3, left=2 * editor.fontMetrics().horizontalAdvance('M'),
                             alignment=Qt.AlignmentFlag.AlignRight, height=190)
        self.assert_geometry(editor, 4, height=190)
        for number in range(5):
            fmt = self.block(editor, number).blockFormat()
            self.assertEqual(fmt.rightMargin(), number + 7)
            self.assertTrue(fmt.nonBreakableLines())
        self.assertFalse(page.dirty)
        self.assertFalse(page.document().isUndoAvailable())

    def test_first_last_are_half_open_and_default_last_includes_the_final_block(self):
        page = self.open('zero\none\ntwo\nthree\nfour\nlast')
        editor = page.md_surface.editor
        before = [self.seed(editor, number, LeftMargin=91, TopMargin=77, RightMargin=number + 1)
                  for number in range(6)]
        editor.update_paragraphs(1, 4)
        for number in (0, 4, 5):
            self.assertEqual(self.block(editor, number).blockFormat(), before[number])
        for number in (1, 2, 3):
            self.assert_geometry(editor, number)
            self.assertEqual(self.block(editor, number).blockFormat().rightMargin(), number + 1)
        after = [QTextBlockFormat(self.block(editor, number).blockFormat()) for number in range(6)]
        editor.update_paragraphs(3, 3)
        editor.update_paragraphs(99)
        self.assertEqual([self.block(editor, number).blockFormat() for number in range(6)], after)
        editor.update_paragraphs(4)
        self.assert_geometry(editor, 4)
        self.assert_geometry(editor, 5)
        self.assertEqual(self.block(editor, 0).blockFormat(), before[0])

    def test_complete_update_formats_and_lays_out_tail_before_scrolling_to_it(self):
        raw = ('ordinary paragraph\n' * 360) + '## Tail\n\nLast 😀 paragraph'
        page = self.open(raw)
        surface = page.md_surface
        editor = surface.editor
        self.assertFalse(editor.lazy_paragraphs)
        surface.set_preference('line_height', 180)
        # The tail is outside the first 160 blocks and has not been visited.
        self.assertLess(editor.firstVisibleBlock().blockNumber(), 10)
        self.assert_geometry(editor, 359, height=180)
        self.assert_geometry(editor, 360, top=6, bottom=6, height=120)
        self.assert_geometry(editor, 361, height=180)
        self.assert_geometry(editor, 362, height=180)
        tail = editor.document().lastBlock()
        geometry = editor.blockBoundingRect(tail)
        self.assertGreater(geometry.height(), 0)
        editor.setTextCursor(QTextCursor(tail))
        editor.ensureCursorVisible()
        self.app.processEvents()
        self.assertEqual(editor.blockBoundingRect(tail), geometry)
        self.assertTrue(editor.viewport().rect().intersects(editor.cursorRect()))
        self.assertEqual(page.encoded(), raw.encode('utf-8'))

    def test_shared_source_keeps_bom_line_endings_and_undo_while_view_formats_stay_local(self):
        raw = b'\xef\xbb\xbf' + '# Title 😀\r\n- item\n> quote\r\nlast'.encode('utf-8')
        left = self.open(raw)
        right = self.open(None, left.buffer)
        left.md_surface.set_preference('line_height', 190)
        right.md_surface.set_mode('source')
        left.md_surface.editor.update_paragraphs()
        right.md_surface.editor.update_paragraphs()
        self.assert_geometry(left.md_surface.editor, 3, height=190)
        self.assert_geometry(right.md_surface.editor, 3, height=130)
        for page in (left, right):
            self.assertEqual(page.encoded(), raw)
            self.assertFalse(page.dirty)
            self.assertFalse(page.document().isUndoAvailable())
        snap = left.buffer.snapshot()
        left.buffer.apply(snap, [SourceEdit(len(snap.text), len(snap.text), ' changed 😀')])
        changed = raw + ' changed 😀'.encode('utf-8')
        self.assertEqual(right.encoded(), changed)
        left.md_surface.set_mode('source')
        left.md_surface.set_mode('write')
        left.md_surface.editor.update_paragraphs()
        right.md_surface.editor.update_paragraphs()
        right.undo()
        self.assertEqual(left.encoded(), raw)
        right.redo()
        self.assertEqual(left.encoded(), changed)
        self.assertEqual(self.path.read_bytes(), raw)
        self.assert_geometry(left.md_surface.editor, 3, height=190)
        self.assert_geometry(right.md_surface.editor, 3, height=130)


if __name__ == '__main__':
    unittest.main()
