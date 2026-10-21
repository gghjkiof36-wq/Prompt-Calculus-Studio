"""Dense Markdown must not format or convert the full document on the GUI thread."""
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QThreadPool
from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtTest import QTest

from prompt_calculus_studio.markdown_source import SourceEdit
from prompt_calculus_studio.markdown_view import SourceView
import test_workspace_panes as fixture


class DenseRenderingTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def tearDown(self):
        QThreadPool.globalInstance().waitForDone(5000)
        fixture.WorkspacePaneTests.tearDown(self)

    def wait(self, predicate):
        deadline = time.monotonic()+15
        while not predicate() and time.monotonic()<deadline:QTest.qWait(10)
        self.assertTrue(predicate())

    def test_dense_source_formats_visible_blocks_and_preserves_edit_history(self):
        text = ('## 海岸筆記\n\n**晨光**與薄霧\n\n|方向|文字|\n|---|---|\n|甲|light|\n\n```text\nmist\n```\n\n'*780)+'**FINAL**'
        raw = b'\xef\xbb\xbf'+text.replace('\n','\r\n').encode()
        path = self.root/'Notes.md';path.write_bytes(raw)
        formatted = []
        original = SourceView.update_paragraphs
        def observe(editor, first=0, last=None):
            formatted.append((first, len(editor.lines) if last is None else last))
            return original(editor, first, last)
        with patch.object(SourceView, 'update_paragraphs', observe):
            tab = self.open('Notes.md');s = tab.widget.md_surface;self.events()
        self.assertLess(sum(last-first for first,last in formatted), 500)
        self.assertEqual(tab.widget.encoded(), raw)
        self.assertFalse(tab.widget.dirty)
        cursor = s.editor.textCursor();cursor.movePosition(QTextCursor.MoveOperation.End)
        s.editor.setTextCursor(cursor);s.editor.ensureCursorVisible();self.events()
        block = s.editor.document().lastBlock()
        self.assertTrue(any(item.format.fontWeight()==QFont.Weight.Bold for item in block.layout().formats()))
        snap = tab.widget.buffer.snapshot()
        tab.widget.buffer.apply(snap, [SourceEdit(len(snap.text),len(snap.text),' 😀')])
        changed = tab.widget.encoded();self.assertNotEqual(changed,raw)
        tab.widget.undo();self.assertEqual(tab.widget.encoded(),raw)
        tab.widget.redo();self.assertEqual(tab.widget.encoded(),changed)
        self.assertEqual(path.read_bytes(),raw)
        self.assertEqual(self.errors,[])

    def test_dense_rich_conversion_is_background_and_only_publishes_latest(self):
        from prompt_calculus_studio import markdown_render
        text = '| A | B |\n|---|---|\n| one | two |\n\n'*400
        path = self.root/'Notes.md';raw = text.encode();path.write_bytes(raw)
        s = self.open('Notes.md').widget.md_surface
        gate = threading.Event();entered = threading.Event();threads = [];calls = []
        original = markdown_render.render
        def slow(source, *args, **kwargs):
            threads.append(threading.get_ident());calls.append(source)
            entered.set();gate.wait(4)
            return original(source,*args,**kwargs)
        with patch.object(markdown_render, 'render', side_effect=slow), patch(
                'prompt_calculus_studio.markdown_rich_view.render', side_effect=slow):
            try:
                s.set_mode('read');self.assertTrue(entered.wait(2))
                self.assertNotIn(threading.get_ident(), threads)
                rich = s.reader.rich;job = rich.projection_worker.job
                snap = s.page.buffer.snapshot()
                s.page.buffer.apply(snap,[SourceEdit(len(snap.text),len(snap.text),'LATEST 😀')])
                s.refresh();s.reader.refresh()
                self.assertIs(rich.projection_worker.job,job)
                self.assertIsNone(rich.projection)
                gate.set();self.wait(lambda:rich.render_state=='ready')
                self.assertEqual(len(calls),2)
                self.assertNotIn(threading.get_ident(),threads)
                self.assertIn('LATEST 😀',rich.projection.body)
                self.assertEqual(s.page.encoded(),raw+'LATEST 😀'.encode())
                self.assertEqual(path.read_bytes(),raw)
                self.assertEqual(self.errors,[])
            finally:gate.set()
