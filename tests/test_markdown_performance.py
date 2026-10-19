"""Rendering work stays local without changing source or selection semantics."""
import json
import unittest
from unittest.mock import patch
from PySide6.QtCore import Qt,QRectF,QThreadPool
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

    def test_large_reader_uses_visible_projection_without_native_html_copy(self):
        text='- [x] Done\n- [ ] Open\n\n'+('## Scene\n\nEvening.\n\n'*5000)+'## 最後一節\n\nEND 😀'
        (self.root/'Notes.md').write_bytes(text.encode());s=self.open('Notes.md').widget.md_surface
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
