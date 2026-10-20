"""Rendering work stays local without changing source or selection semantics."""
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QRectF,QThreadPool,QObject,QEvent
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_source import SourceSnapshot,SourceEdit
from prompt_calculus_studio.markdown_statistics import WordStatistics,word_count
import test_workspace_panes as fixture


class MarkdownPerformanceTests(unittest.TestCase):
    setUpClass=classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp=fixture.WorkspacePaneTests.setUp
    open=fixture.WorkspacePaneTests.open
    events=fixture.WorkspacePaneTests.events
    def tearDown(self):
        self.w.notebook.stop();QThreadPool.globalInstance().waitForDone(5000)
        fixture.WorkspacePaneTests.tearDown(self)

    def test_visible_geometry_is_linear_and_matches_native_cursor_layout(self):
        from unittest.mock import patch
        text='# 地圖\n\n'+('\n\n'.join('中文段落 '+str(i)+' **重點** '+('words '*12) for i in range(80)))
        (self.root/'Notes.md').write_bytes(text.encode())
        s=self.open('Notes.md').widget.md_surface;e=s.editor
        for zoom in (100,200):
            s.set_zoom(zoom);self.events()
            for line in (0,25,80):
                e.setTextCursor(QTextCursor(e.document().findBlockByNumber(line)))
                e.ensureCursorVisible();self.events()
                with patch.object(e,'blockBoundingGeometry',wraps=e.blockBoundingGeometry) as geometry:
                    positions=list(e.visible_block_positions())
                    self.assertLessEqual(geometry.call_count,1)
                for block,top in positions:
                    expected=e.blockBoundingGeometry(block).translated(e.contentOffset()).top()
                    self.assertAlmostEqual(top,expected,places=3)
        self.assertEqual(s.page.encoded(),text.encode())

    def test_arrow_repeat_release_leaves_cursor_and_scroll_settled(self):
        from PySide6.QtGui import QKeyEvent
        text='\n\n'.join('第'+str(i)+'段。 **文字**' for i in range(100))
        (self.root/'Notes.md').write_bytes(text.encode())
        s=self.open('Notes.md').widget.md_surface;e=s.editor
        e.setFocus()
        for key in (Qt.Key.Key_Down,Qt.Key.Key_Up):
            for _ in range(70):
                self.app.sendEvent(e,QKeyEvent(QEvent.Type.KeyPress,key,Qt.KeyboardModifier.NoModifier,'',True,1))
                self.app.processEvents()
            self.app.sendEvent(e,QKeyEvent(QEvent.Type.KeyRelease,key,Qt.KeyboardModifier.NoModifier))
            at=e.textCursor().position();scroll=e.verticalScrollBar().value()
            QTest.qWait(180)
            self.assertEqual(e.textCursor().position(),at)
            self.assertEqual(e.verticalScrollBar().value(),scroll)
        self.assertFalse(s.page.dirty);self.assertEqual(s.page.encoded(),text.encode())

    def test_idle_marker_overlay_does_not_schedule_another_viewport_paint(self):
        (self.root/'Notes.md').write_text('# Heading\n\n- [ ] check\n\n正文。\n',encoding='utf-8')
        s=self.open('Notes.md').widget.md_surface;e=s.editor
        class Paints(QObject):
            def __init__(self,owner):
                super().__init__(owner);self.count=0;owner.installEventFilter(self)
            def eventFilter(self,owner,event):
                if event.type()==QEvent.Type.Paint:self.count+=1
                return False
        QTest.qWait(250)
        view=Paints(e.viewport());overlay=Paints(e.markers)
        # Let cursor blinking repaint, but do not let the transparent overlay
        # invalidate its underlay from inside every viewport paint.
        QTest.qWait(350)
        self.assertLessEqual(view.count,6)
        self.assertLessEqual(overlay.count,6)
        before=s.page.encoded();e.moveCursor(QTextCursor.MoveOperation.End)
        e.moveCursor(QTextCursor.MoveOperation.Start,QTextCursor.MoveMode.KeepAnchor)
        self.events();self.assertTrue(e.textCursor().hasSelection())
        self.assertEqual(s.page.encoded(),before)
        self.assertFalse(e.task_targets)
        e.moveCursor(QTextCursor.MoveOperation.End);self.events()
        self.assertTrue(e.task_targets)

    def test_many_search_hits_are_cached_visible_and_utf16_correct(self):
        text=('😀 地圖\n\n'*1800)+'最後的地圖'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface
        s.open_find();s.query.setText('地圖');self.events()
        spans=s.matches();self.assertEqual(len(spans),1801)
        self.assertIs(s.matches(),spans);self.assertLess(len(s.editor.extraSelections()),150)
        s.find_next(-1);self.events()
        self.assertEqual(s.match_count.text(),'1801/1801');self.assertEqual(s.editor.textCursor().selectedText(),'地圖')
        self.assertTrue(all(hit.cursor.selectedText()=='地圖' for hit in s.editor.extraSelections()))
        self.assertIn('#fff16a',{hit.format.background().color().name() for hit in s.editor.extraSelections()})
        s.query.clear();self.assertEqual(s.editor.extraSelections(),[]);self.assertFalse(s.editor.property('searchHit'))
        position=s.editor.textCursor().position();s.find_next();self.assertEqual(s.editor.textCursor().position(),position)
        self.assertEqual(s.page.encoded(),text.encode());self.assertFalse(s.page.dirty)

    def test_paragraph_measure_is_stable_and_indented_wrap_stays_in_view(self):
        text='<p style="margin-left:3em;text-align:right">'+('Words with space. '*30)+'</p>\n\n## Heading'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface;e=s.editor
        self.w.resize(1000,720);self.events();block=e.document().begin()
        baseline=e.blockBoundingRect(block)
        positions=[block.layout().lineAt(i).position() for i in range(block.layout().lineCount())]
        for _ in range(30):self.assertEqual(e.blockBoundingRect(block),baseline)
        self.assertEqual(positions,[block.layout().lineAt(i).position() for i in range(block.layout().lineCount())])
        for i in range(block.layout().lineCount()):
            line=block.layout().lineAt(i)
            self.assertGreaterEqual(line.x(),block.blockFormat().leftMargin())
            self.assertLessEqual(line.x()+line.naturalTextWidth(),e.viewport().width()+1)
        self.assertEqual(s.page.encoded(),text.encode())

    def test_blank_lines_keep_their_height_through_editing_and_undo(self):
        raw='## 地圖上的標記\n\n他沿著地圖上的河流。\n\n\n看看書店裡的那一晚。\n'
        (self.root/'Notes.md').write_bytes(raw.encode());s=self.open('Notes.md').widget.md_surface;e=s.editor
        self.w.resize(1440,960);self.events()
        for mode in ('write','source'):
            s.set_mode(mode)
            for zoom in (100,200):
                s.set_zoom(zoom);self.events()
                for number in (1,3,4):
                    with self.subTest(mode=mode,zoom=zoom,line=number):
                        block=e.document().findBlockByNumber(number)
                        cursor=QTextCursor(block);e.setTextCursor(cursor);e.ensureCursorVisible();self.events()
                        height=e.blockBoundingRect(block).height()
                        after=e.blockBoundingGeometry(block.next()).top()
                        self.assertGreaterEqual(height,e.cursorRect().height())
                        QTest.keyClicks(e,'1');self.events()
                        self.assertAlmostEqual(e.blockBoundingRect(block).height(),height,delta=1)
                        self.assertAlmostEqual(e.blockBoundingGeometry(block.next()).top(),after,delta=1)
                        QTest.keyClick(e,Qt.Key.Key_Backspace);self.events()
                        self.assertAlmostEqual(e.blockBoundingRect(block).height(),height,delta=1)
                        self.assertEqual(s.page.encoded(),raw.encode())
                        s.page.undo();self.events();self.assertEqual(block.text(),'1')
                        s.page.undo();self.events();self.assertEqual(s.page.encoded(),raw.encode())
                        self.assertAlmostEqual(e.blockBoundingRect(block).height(),height,delta=1)
                        self.assertAlmostEqual(e.blockBoundingGeometry(block.next()).top(),after,delta=1)

    def test_toolbar_shadow_fits_host_at_narrow_and_wide_sizes(self):
        s=self.open('Notes.md').widget.md_surface
        for width in (960,1440,2200):
            self.w.resize(width,760);self.events()
            shadow=s.toolbar_shadow.boundingRectFor(QRectF(s.toolbar.rect())).translated(s.toolbar.pos())
            self.assertTrue(QRectF(s.toolbar_host.rect()).contains(shadow))

    def test_repeated_resources_share_resolution_only_within_one_projection(self):
        from prompt_calculus_studio.markdown_links import Notes
        from prompt_calculus_studio.markdown_render import render
        asset=self.root/'asset.png';asset.write_bytes(b'synthetic image')
        notes=Notes(self.root);source=('![One|32](asset.png) ![Two|64](asset.png) ![](later.png)\n\n')*120
        with patch.object(notes,'resolve',wraps=notes.resolve) as resolve:
            result=render(source,self.root/'Notes.md',self.root,notes=notes)
            self.assertEqual(resolve.call_count,2)
        self.assertEqual(len(result.attachments),360)
        self.assertEqual([a['width'] for a in result.attachments[:3]],[32,64,None])
        self.assertEqual(result.attachments[0]['path'],str(asset))
        # A later render sees replacements and newly created resources even if
        # the caller supplies the same Notes instance.
        asset.unlink();(self.root/'later.png').write_bytes(b'new synthetic image')
        with patch.object(notes,'resolve',wraps=notes.resolve) as resolve:
            updated=render(source,self.root/'Notes.md',self.root,notes=notes)
            self.assertEqual(resolve.call_count,2)
        self.assertFalse(asset.exists())
        self.assertEqual(updated.attachments[2]['path'],str(self.root/'later.png'))
        escaped=render('![](../outside.png)\n\n'*2,self.root/'Notes.md',self.root,notes=notes)
        self.assertTrue(all(a['path'] is None for a in escaped.attachments))

    def test_repeated_image_headers_are_read_once_and_rechecked_on_refresh(self):
        from PySide6.QtGui import QImage,QColor
        from prompt_calculus_studio import markdown_rich_view as rich_module
        asset=self.root/'asset.png';image=QImage(24,24,QImage.Format.Format_RGB32);image.fill(QColor('#6d8a7d'));image.save(str(asset))
        source='![One|32](asset.png) ![Two|64](asset.png)\n\n'*30
        (self.root/'Notes.md').write_bytes(source.encode());s=self.open('Notes.md').widget.md_surface
        with patch.object(rich_module,'QImageReader',wraps=rich_module.QImageReader) as headers:
            s.set_mode('read');self.assertEqual(headers.call_count,1)
        self.assertEqual(s.reader.rich.projection.body.count('<img '),60)
        self.assertIn('alt="One" width="32"',s.reader.rich.projection.body)
        self.assertIn('alt="Two" width="64"',s.reader.rich.projection.body)
        asset.write_bytes(b'not an image');s.reader.refresh()
        self.assertNotIn('<img ',s.reader.rich.projection.body)
        self.assertIn('圖片無法顯示',s.reader.rich.projection.body)
        self.assertEqual(s.reader.rich.gate.allowed,set());self.assertEqual(s.page.encoded(),source.encode())

    def test_unwrapped_alignment_stays_within_the_available_measure(self):
        text='<p style="text-align:right">Right.</p>\n\n<p style="text-align:center">Centered.</p>'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface;e=s.editor
        s.set_preference('wrap',False);self.events()
        for number in (0,2):
            block=e.document().findBlockByNumber(number);e.blockBoundingRect(block);line=block.layout().lineAt(0)
            self.assertGreater(line.x(),e.viewport().width()/4)
            self.assertLessEqual(line.x()+line.naturalTextWidth(),e.viewport().width()+1)
        self.assertEqual(s.page.encoded(),text.encode())

    def test_unchanged_reading_view_is_not_rebuilt_and_new_text_invalidates_it(self):
        s=self.open('Notes.md').widget.md_surface;s.set_mode('read');self.events()
        with patch.object(s.reader,'refresh',wraps=s.reader.refresh) as refresh:
            s.refresh();s.set_mode('write');s.set_mode('read');self.assertEqual(refresh.call_count,0)
            snap=s.page.buffer.snapshot();s.page.buffer.apply(snap,[SourceEdit(len(snap.text),len(snap.text),' changed')]);s.refresh()
            self.assertEqual(refresh.call_count,1);self.assertIn('changed',s.reader.toPlainText())

    def test_shared_statistics_drop_an_outdated_count(self):
        s=self.open('Notes.md').widget.md_surface;counter=WordStatistics(s.page.buffer)
        old=SourceSnapshot('old word '*33000,1,'stats');new=SourceSnapshot('new '*90000,2,'stats')
        self.assertIsNone(counter.request(old));counter.start();counter.request(new)
        for _ in range(100):
            QTest.qWait(20)
            if counter.snapshot is new:break
        self.assertIs(counter.snapshot,new);self.assertEqual(counter.request(new),word_count(new.text))
        self.assertIsNone(counter.job);self.assertIsNone(counter.pending)

    def test_rich_refresh_keeps_existing_dom_for_duplicate_requests_and_ignores_old_completion(self):
        from prompt_calculus_studio.markdown_render import Projection
        (self.root/'Notes.md').write_bytes(b'| A | B |\n|---|---|\n| one | two |\n')
        s=self.open('Notes.md').widget.md_surface;s.set_mode('read');rich=s.reader.rich
        def wait_ready():
            for _ in range(250):
                if rich.render_state=='ready':break
                QTest.qWait(20)
            self.assertEqual(rich.render_state,'ready')
        def script(source):
            result=[];rich.page().runJavaScript(source,result.append)
            for _ in range(100):
                if result:return result[0]
                QTest.qWait(20)
            self.fail('Reading script did not complete')
        wait_ready()
        self.assertFalse(script('window.pcsUnsafeProbe === true'))
        script('window.originalTable=document.querySelector("table");window.refreshDocument();window.refreshDocument()')
        QTest.qWait(100)
        self.assertTrue(script('document.querySelector("table")===window.originalTable'))
        old_version=rich.version
        rich.refresh(s.page.buffer.snapshot().text,projection=Projection(body='<p>新版 &amp; 😀</p><script>window.pcsUnsafeProbe=true</script><img onerror="window.pcsUnsafeProbe=true"><a href="https://example.invalid/">blocked</a>'))
        rich.bridge.finished('ready',old_version)
        self.assertEqual(rich.render_state,'loading')
        wait_ready()
        self.assertEqual(json.loads(script('JSON.stringify([document.querySelector("main p").textContent,!!document.querySelector("main script,main [onerror],main a[href]"),window.pcsUnsafeProbe === true])')),['新版 & 😀',False,False])
        self.assertFalse(s.page.dirty)

    def test_large_reader_uses_visible_projection_without_native_html_copy(self):
        text='- [x] Done\n- [ ] Open\n\n'+('## Scene\n\nEvening.\n\n'*5000)+'## 最後一節\n\nEND 😀'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface
        for _ in range(500):
            if not s.page.buffer.opening:break
            QTest.qWait(10)
        self.assertFalse(s.page.buffer.opening)
        self.assertLess(len(text),128_000);self.assertTrue(s.editor.lazy_presentation)
        with patch.object(s.reader,'setHtml',side_effect=AssertionError('hidden rich-text import')):
            s.set_mode('read')
        rich=s.reader.rich
        for _ in range(250):
            if rich.render_state=='ready':break
            QTest.qWait(20)
        self.assertEqual(rich.render_state,'ready')
        result=[];rich.page().runJavaScript('JSON.stringify([document.querySelector("main").innerText.endsWith("END 😀"),document.querySelectorAll("input[type=checkbox]").length,document.querySelectorAll("input:checked").length])',result.append)
        for _ in range(100):
            if result:break
            QTest.qWait(20)
        self.assertEqual(json.loads(result[0]),[True,2,1]);self.assertTrue(s.reader.document().isEmpty())
        s.set_mode('write');self.assertEqual(s.page.encoded(),text.encode());self.assertFalse(s.page.dirty)

    def test_large_reading_conversion_does_not_block_and_keeps_only_latest_request(self):
        import threading
        from prompt_calculus_studio import markdown_read_projection as projection
        raw=('## Scene\n\nEvening.\n\n'*2600).encode()
        (self.root/'Notes.md').write_bytes(raw);s=self.open('Notes.md').widget.md_surface
        gate=threading.Event();entered=threading.Event();calls=[];worker_threads=[];completed_threads=[]
        main_thread=threading.get_ident()
        original=projection.reading_html
        def slow(text,*args,**kwargs):
            calls.append(text);worker_threads.append(threading.get_ident());entered.set();gate.wait(4)
            return original(text,*args,**kwargs)
        with patch.object(projection,'reading_html',side_effect=slow):
            try:
                s.set_mode('read')
                self.assertTrue(entered.wait(2))
                self.assertFalse(gate.is_set());self.assertEqual(s.reader.rich.render_state,'loading')
                previous_version=s.reader.rich.version
                first_job=s.reader.plain_projection.job
                s.reader.plain_projection.ready.connect(lambda *args:completed_threads.append(threading.get_ident()),Qt.ConnectionType.DirectConnection)
                # Another view can edit the shared source while conversion is
                # running. Do not publish that obsolete document or start an
                # unbounded worker per change.
                snapshot=s.page.buffer.snapshot()
                s.page.buffer.apply(snapshot,[SourceEdit(len(snapshot.text),len(snapshot.text),'LATEST')])
                s.refresh();s.reader.refresh()
                self.assertIs(s.reader.plain_projection.job,first_job)
                s.reader.rich.bridge.finished('ready',previous_version)
                self.assertEqual(s.reader.rich.render_state,'loading')
                self.assertIsNone(s.reader.rich.projection)
                gate.set()
                for _ in range(250):
                    if s.reader.rich.render_state=='ready':break
                    QTest.qWait(20)
                self.assertEqual(s.reader.rich.render_state,'ready')
                self.assertEqual(len(calls),2)
                self.assertTrue(all(thread!=main_thread for thread in worker_threads))
                self.assertEqual(completed_threads,[main_thread])
                self.assertIn('LATEST',s.reader.rich.projection.body)
                self.assertEqual(s.page.encoded(),raw+b'LATEST')
            finally:gate.set()

    def test_large_reading_job_can_finish_after_view_is_closed(self):
        import threading
        from prompt_calculus_studio import markdown_read_projection as projection
        raw=('plain\n\n'*5100).encode();(self.root/'Notes.md').write_bytes(raw)
        tab=self.open('Notes.md');s=tab.widget.md_surface;gate=threading.Event();entered=threading.Event()
        def slow(*args,**kwargs):entered.set();gate.wait(4);return '<p>complete</p>'
        with patch.object(projection,'reading_html',side_effect=slow):
            try:
                s.set_mode('read');self.assertTrue(entered.wait(2))
                self.w.close_tab(self.w.index_of(tab.key),decision='discard')
                self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete)
                gate.set();QThreadPool.globalInstance().waitForDone(5000);self.events()
                self.assertEqual((self.root/'Notes.md').read_bytes(),raw)
            finally:gate.set()

    def test_long_nested_highlight_preserves_explicit_ink_without_fragmenting(self):
        text='<span style="background-color:#fff16a">'+('plain '*2000)+'<span style="color:#123456">explicit</span></span>'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface
        formats=s.editor.document().begin().layout().formats();self.assertLess(len(formats),15)
        plain=text.index('plain');explicit=text.index('explicit')
        at=lambda pos:next(item.format for item in formats if item.start<=pos<item.start+item.length)
        self.assertEqual(at(plain).background().color().name(),'#fff16a')
        self.assertLess(at(plain).foreground().color().lightnessF(),.3)
        self.assertEqual(at(explicit).foreground().color().name(),'#123456')
        self.assertEqual(s.page.encoded(),text.encode())

    def test_folded_long_section_does_not_hide_later_visible_controls(self):
        text='## First\n\n'+('Hidden paragraph.\n\n'*5100)+'## Next\n\n- [ ] Visible task\n\n'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface;e=s.editor
        e.toggle_fold(0);QTest.qWait(50);e.present_visible();self.events()
        number=text[:text.index('## Next')].count('\n');block=e.document().findBlockByNumber(number)
        self.assertIn(number,[b.blockNumber() for b in e.visible_blocks()])
        self.assertIn(number,e._presented)
        self.assertEqual(e.fold_number_at(e.fold_control_rect(block).center()),number)
        self.assertTrue(any(n==number+2 for _,n in e.task_targets))
        self.assertEqual(s.page.encoded(),text.encode())


if __name__=='__main__':unittest.main()
