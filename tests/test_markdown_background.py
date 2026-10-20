"""Bounded background work preserves source ownership and stale-result guards."""
import unittest
import sys
import traceback
from types import SimpleNamespace
from unittest.mock import patch
from PySide6.QtCore import QThreadPool, QEvent, QCoreApplication
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from shiboken6 import isValid
import test_workspace_panes as fixture


class MarkdownBackgroundTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    def setUp(self):
        fixture.WorkspacePaneTests.setUp(self)
        sys.excepthook=lambda kind,error,tb:self.errors.append(''.join(traceback.format_exception(kind,error,tb)))
    events = fixture.WorkspacePaneTests.events
    open = fixture.WorkspacePaneTests.open

    def tearDown(self):
        fixture.WorkspacePaneTests.tearDown(self)
        self.assertTrue(QThreadPool.globalInstance().waitForDone(10000))
        QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)

    def wait(self,predicate):
        for _ in range(1000):
            QTest.qWait(10)
            if predicate():return
        self.fail('Background operation did not reach its visible ready state.')

    def large(self):
        raw=b'\xef\xbb\xbf'+('文字 😀\r\nplain\n'*11000).encode()
        path=self.root/'Large.md';path.write_bytes(raw)
        tab=self.w.open_path(path,retained=True,new_tab=True)
        self.assertTrue(tab.widget.buffer.opening)
        return tab,raw

    def test_loading_guards_edits_and_restores_exact_bytes_shared_views_and_navigation(self):
        tab,raw=self.large();surface=tab.widget.md_surface
        tab.widget.insertPlainText('must not appear')
        self.assertFalse(tab.widget.save())
        self.assertEqual(tab.widget.encoded(),raw)
        other=self.w.duplicate_view(tab,True)
        surface.set_mode('source');surface.navigate(20)
        self.wait(lambda:not tab.widget.buffer.opening)
        self.assertEqual(surface.mode,'source')
        self.assertEqual(tab.widget.encoded(),raw)
        self.assertEqual(other.widget.encoded(),raw)
        self.assertEqual(tab.widget.toPlainText(),other.widget.toPlainText())
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        tab.widget.insertPlainText('尾 😀')
        self.assertTrue(tab.widget.encoded().endswith('尾 😀'.encode()))
        other.widget.undo()
        self.assertEqual(tab.widget.encoded(),raw)
        self.assertEqual(tab.path.read_bytes(),raw)

    def test_close_during_loading_has_no_partial_save_or_callback_to_closed_view(self):
        tab,raw=self.large();page=tab.widget
        self.w.close_tab(self.w.index_of(tab.key),decision='discard')
        self.events()
        self.wait(lambda:not isValid(page))
        self.assertEqual((self.root/'Large.md').read_bytes(),raw)
        self.assertTrue(QThreadPool.globalInstance().waitForDone(10000))
        self.events();self.assertEqual(self.errors,[])

    def test_loading_failure_keeps_original_bytes_and_reports_a_recoverable_state(self):
        with patch('prompt_calculus_studio.markdown_loading.line_info',side_effect=ValueError('injected parse failure')):
            tab,raw=self.large()
            self.wait(lambda:bool(tab.widget.error))
        self.assertTrue(tab.widget.md_surface.editor.isReadOnly())
        self.assertFalse(tab.widget.save())
        self.assertEqual(tab.widget.encoded(),raw)
        self.assertEqual(tab.path.read_bytes(),raw)
        self.assertIn('載入未完成',tab.widget.md_surface.statistics.text())

    def test_query_replacement_cancels_old_search_with_unicode_offsets(self):
        tab,raw=self.large()
        self.wait(lambda:not tab.widget.buffer.opening)
        surface=tab.widget.md_surface
        # Extend past the asynchronous-search threshold without changing fixture semantics.
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        tab.widget.insertPlainText('a 😀 needle\n'*26000)
        surface.open_find();surface.query.setText('needle')
        self.assertTrue(surface.search_highlight.searching)
        surface.query.setText('文字')
        self.wait(lambda:not surface.search_highlight.searching)
        self.assertEqual(len(surface.matches()),11000)
        surface.find_next()
        self.assertEqual(surface.editor.textCursor().selectedText(),'文字')
        surface.query.clear()
        self.assertEqual(surface.matches(),[])

    def deferred_poll(self,buffer):
        from prompt_calculus_studio.markdown_external import ExternalRefresh
        controller=ExternalRefresh(buffer)
        with patch('prompt_calculus_studio.markdown_external.QThreadPool',
                   SimpleNamespace(globalInstance=lambda:SimpleNamespace(start=lambda job:None))):
            controller.request()
        return controller,controller.job

    def test_external_completion_cannot_replace_local_draft_or_new_save_baseline(self):
        tab=self.open('Notes.md');original=tab.widget.encoded()
        controller,job=self.deferred_poll(tab.widget.buffer)
        tab.widget.insertPlainText('draft ')
        controller.finished(job.key,(('different',),b'# outside\n',''))
        self.assertIn('外部修改',tab.widget.error)
        self.assertIn('draft ',tab.widget.toPlainText())
        self.assertEqual(tab.path.read_bytes(),original)
        tab.widget.error=tab.widget.buffer.external_error=''
        self.assertTrue(self.w.save_tab(tab))
        saved=tab.widget.encoded()
        controller.finished(job.key,(('older',),b'# stale\n',''))
        self.assertEqual(tab.widget.encoded(),saved)
        self.assertEqual(tab.path.read_bytes(),saved)

    def test_external_completion_after_last_view_close_does_not_reopen_or_write(self):
        tab=self.open('Notes.md');raw=tab.path.read_bytes()
        controller,job=self.deferred_poll(tab.widget.buffer)
        self.w.close_tab(self.w.index_of(tab.key),decision='discard')
        self.events()
        controller.finished(job.key,(None,b'late bytes',''))
        self.assertEqual((self.root/'Notes.md').read_bytes(),raw)
        self.assertIsNone(self.w.tab_for_path(self.root/'Notes.md'))

    def test_complex_projection_discards_stale_work_and_publishes_latest_on_gui_thread(self):
        import threading
        from PySide6.QtCore import Qt
        from prompt_calculus_studio import markdown_render
        from prompt_calculus_studio.markdown_source import SourceEdit
        text='| Key | Value |\n|---|---|\n| one | two |\n\n'+'word '*27000
        path=self.root/'Rich.md';path.write_bytes(text.encode())
        tab=self.w.open_path(path,retained=True,new_tab=True);s=tab.widget.md_surface
        gate=threading.Event();entered=threading.Event();workers=[];completions=[]
        original=markdown_render.render;main=threading.get_ident()
        def slow(*args,**kwargs):
            workers.append(threading.get_ident());entered.set();gate.wait(4)
            return original(*args,**kwargs)
        with patch.object(markdown_render,'render',side_effect=slow):
            try:
                s.set_mode('read');self.assertTrue(entered.wait(2))
                rich=s.reader.rich;job=rich.projection_worker.job
                rich.projection_worker.ready.connect(lambda *args:completions.append(threading.get_ident()),
                                                     Qt.ConnectionType.DirectConnection)
                snapshot=s.page.buffer.snapshot()
                s.page.buffer.apply(snapshot,[SourceEdit(len(text),len(text),'\n\nLATEST 😀')])
                s.refresh();s.reader.refresh()
                self.assertIs(rich.projection_worker.job,job)
                self.assertIsNone(rich.projection)
                gate.set();self.wait(lambda:rich.render_state=='ready')
                self.assertEqual(len(workers),2)
                self.assertTrue(all(thread!=main for thread in workers))
                self.assertEqual(completions,[main])
                self.assertIn('LATEST 😀',rich.projection.body)
                self.assertEqual(path.read_text(encoding='utf-8'),text)
                s.set_mode('write');s.page.undo();self.assertEqual(s.page.encoded(),text.encode())
            finally:gate.set()

    def test_complex_worker_can_finish_after_document_view_is_destroyed(self):
        import threading
        from prompt_calculus_studio import markdown_render
        text='| Key | Value |\n|---|---|\n| one | two |\n\n'+'word '*27000
        path=self.root/'Rich.md';path.write_bytes(text.encode())
        tab=self.w.open_path(path,retained=True,new_tab=True)
        gate=threading.Event();entered=threading.Event()
        original=markdown_render.render
        def slow(*args,**kwargs):
            entered.set();gate.wait(4);return original(*args,**kwargs)
        with patch.object(markdown_render,'render',side_effect=slow):
            try:
                tab.widget.md_surface.set_mode('read');self.assertTrue(entered.wait(2))
                self.w.close_tab(self.w.index_of(tab.key),decision='discard')
                QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                gate.set();self.assertTrue(QThreadPool.globalInstance().waitForDone(5000))
                self.events()
                self.assertEqual(path.read_text(encoding='utf-8'),text)
                self.assertIsNone(self.w.tab_for_path(path));self.assertEqual(self.errors,[])
            finally:gate.set()


if __name__=='__main__':
    unittest.main()
