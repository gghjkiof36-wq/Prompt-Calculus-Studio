import unittest
from unittest.mock import patch
from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_source import SourceEdit,utf16_position
import test_workspace_panes as fixture


class LargeMarkdownTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events
    def tearDown(self):
        self.w.notebook.stop();QThreadPool.globalInstance().waitForDone(3000)
        fixture.WorkspacePaneTests.tearDown(self)

    def test_large_document_formats_visible_tail_and_preserves_source_undo(self):
        raw=('## Scene\n\n雨停了。\n\n'*2600+'## Last\n\n結尾。').encode()
        (self.root/'Notes.md').write_bytes(raw);s=self.open('Notes.md').widget.md_surface;e=s.editor
        self.assertTrue(e.lazy_presentation);self.assertLess(len(e._presented),500)
        self.assertEqual(s.outline.items,[])
        text=s.page.buffer.snapshot().text
        e.moveCursor(QTextCursor.MoveOperation.End);QTest.qWait(50);e.present_visible()
        number=text[:text.index('## Last')].count('\n');block=e.document().findBlockByNumber(number)
        self.assertIn(number,e._presented)
        self.assertEqual(block.blockFormat().lineHeight(),120)
        self.assertTrue(any(item.format.fontPointSize()>=20 for item in block.layout().formats()))
        self.assertEqual(s.page.encoded(),raw);self.assertFalse(s.page.dirty)
        s.apply([SourceEdit(len(text),len(text),'改')]);self.assertTrue(s.page.encoded().endswith('結尾。改'.encode()))
        s.page.undo();self.assertEqual(s.page.encoded(),raw)
        s.set_mode('source');QTest.qWait(50);e.present_visible()
        self.assertFalse(any(item.format.fontPointSize()>=20 for item in block.layout().formats()))
        s.set_mode('write');QTest.qWait(50);e.present_visible()
        self.assertTrue(any(item.format.fontPointSize()>=20 for item in block.layout().formats()))

    def test_layout_pending_hit_test_cannot_expand_work_to_whole_document(self):
        raw=('line\n'*10020).encode();(self.root/'Notes.md').write_bytes(raw)
        e=self.open('Notes.md').widget.md_surface.editor
        tail=QTextCursor(e.document());tail.movePosition(QTextCursor.MoveOperation.End)
        with patch.object(e,'cursorForPosition',return_value=tail),patch.object(e,'firstVisibleBlock',return_value=e.document().begin()):
            first,last=e.presentation_range()
            self.assertEqual(first,0);self.assertLess(last,500)
