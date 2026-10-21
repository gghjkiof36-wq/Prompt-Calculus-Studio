import unittest
import threading
import time
from unittest.mock import patch
from PySide6.QtCore import QMimeData, Qt
from PySide6.QtGui import QImage, QTextCursor
from PySide6.QtTest import QTest
from prompt_calculus_studio.markdown_paste import from_html
from markdown_it import MarkdownIt
import test_workspace_panes as fixture


class ConversionTests(unittest.TestCase):
    def test_semantics_lists_math_code_table_and_controls(self):
        source = from_html('''<div role="toolbar">Copy</div><button>Retry</button>
<h3>Heading</h3><blockquote><p>one</p><p>two</p></blockquote>
<ol start="3"><li>three<ul><li><input type="checkbox" checked>task</li></ul></li><li>four</li></ol>
<pre><code class="language-python">  a

```</code></pre><span class="katex"><math><annotation encoding="application/x-tex">x^2</annotation></math><span>x2</span></span>
<table><tr><th align="right">A</th><th>B</th></tr><tr><td>1</td><td>2</td></tr></table>
<p><a href="https://example.invalid/a">source</a><strong>bold</strong><em>italic</em></p>''')
        self.assertNotIn('Retry', source)
        self.assertNotIn('Copy', source)
        self.assertIn('### Heading', source)
        self.assertIn('3. three', source)
        self.assertIn('4. four', source)
        self.assertIn('   - [x] task', source)
        self.assertEqual(source.count('$x^2$'), 1)
        self.assertNotIn('x2', source)
        self.assertIn('| ---: | --- |', source)
        code = [t for t in MarkdownIt().parse(source) if t.type == 'fence'][0]
        self.assertEqual(code.info, 'python')
        self.assertEqual(code.content, '  a\n\n```\n')
        self.assertIn('> two', source)
        self.assertIn('<br', MarkdownIt().render(from_html('<p>first<br>second</p>')))
        self.assertEqual(from_html('<h2>Title</h2>\n  <p>one</p>\n<p>two</p>'), '## Title\n\none\n\ntwo')
        self.assertEqual(from_html('<p>a<br><br>b</p>'), 'a  \n  \nb')

    def test_complex_table_is_inert_and_invalid_html_is_explicit(self):
        result = from_html('<table><tr><td colspan="2">KEEP</td></tr></table>')
        self.assertIn('合併儲存格', result)
        self.assertIn('colspan="2"', result)
        self.assertIn('```html', result)
        for value in ('<b>broken', '<p><b>broken</p>'):
            with self.assertRaises(ValueError):from_html(value)
        with patch('prompt_calculus_studio.markdown_html.MAX_INPUT', 10):
            with self.assertRaises(ValueError):from_html('<p>too large</p>')


class FormattedPasteTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def tearDown(self):
        self.app.clipboard().clear()
        fixture.WorkspacePaneTests.tearDown(self)

    def test_chunked_insertion_shared_views_and_close_keep_one_complete_edit(self):
        from prompt_calculus_studio.markdown_insertion import SourceInsertion
        from prompt_calculus_studio.markdown_source import SourceEdit
        from prompt_calculus_studio.markdown_syntax import line_info
        tab=self.open('Notes.md');other=self.w.duplicate_view(tab,True)
        buffer=tab.widget.buffer;before=buffer.snapshot()
        replacement='## Heading\n\n保留內容 😀\n'*1200
        merged=replacement+before.text
        buffer.remember_syntax(merged,line_info(merged))
        finished=[]
        insertion=SourceInsertion(buffer,before,[SourceEdit(0,0,replacement)],lambda:finished.append(True))
        insertion.step()
        self.assertIsNotNone(buffer.mutation)
        # Close while the transaction is between GUI turns, not only at rest.
        self.assertTrue(self.w.close_tab(self.w.index_of(tab.key),pane=self.w.pane_for(tab)))
        self.assertEqual(finished,[True])
        self.assertEqual(other.widget.toPlainText(),merged)
        self.assertEqual(tab.path.read_bytes(),other.widget.encoded())
        other.widget.undo();self.assertEqual(other.widget.toPlainText(),before.text)
        other.widget.redo();self.assertEqual(other.widget.toPlainText(),merged)

    def test_mime_priority_selection_shared_undo_save_and_reopen(self):
        (self.root/'Notes.md').write_text('before SELECT after', encoding='utf-8')
        tab = self.open('Notes.md')
        s = tab.widget.md_surface
        other = self.w.duplicate_view(tab, True)
        self.w.focus_tab(tab)
        for mode in ('write', 'source'):
            s.set_mode(mode)
            cursor = s.editor.textCursor()
            cursor.setPosition(7)
            cursor.setPosition(13, QTextCursor.MoveMode.KeepAnchor)
            s.editor.setTextCursor(cursor)
            mime = QMimeData()
            mime.setData('text/markdown', b'**Markdown**')
            mime.setHtml('<b>HTML</b>')
            mime.setText('plain')
            self.app.clipboard().setMimeData(mime)
            QTest.keyClick(s.editor, Qt.Key.Key_V, Qt.KeyboardModifier.ControlModifier)
            self.events()
            self.assertEqual(tab.widget.buffer.snapshot().text, 'before **Markdown** after')
            self.assertEqual(other.widget.toPlainText(), 'before **Markdown** after')
            tab.widget.undo()
            self.assertEqual(tab.widget.toPlainText(), 'before SELECT after')
            tab.widget.redo()
            self.assertTrue(self.w.save_tab(tab))
            self.assertEqual(tab.path.read_text(encoding='utf-8'), 'before **Markdown** after')
            tab.widget.undo()
        from prompt_calculus_studio.workspace_files import MarkdownPage
        reopened = MarkdownPage(tab.path)
        self.assertEqual(reopened.toPlainText(), 'before **Markdown** after')
        reopened.close()

    def test_failure_readonly_and_ime_preserve_document_and_clipboard(self):
        s = self.open('Notes.md').widget.md_surface
        before = s.page.toPlainText()
        mime = QMimeData()
        mime.setHtml('<strong>broken')
        mime.setText('original')
        self.app.clipboard().setMimeData(mime)
        with patch.object(s, 'report') as report:
            s.editor.paste()
            self.assertTrue(report.called)
        self.assertEqual(s.page.toPlainText(), before)
        self.assertEqual(self.app.clipboard().text(), 'original')
        mime.setHtml('<strong>valid</strong>')
        for state in ('read', 'readonly', 'ime'):
            s.set_mode('read' if state == 'read' else 'write')
            s.editor.setReadOnly(state == 'readonly')
            if state == 'ime':s.page.buffer.composing.add(123)
            s.editor.insertFromMimeData(mime)
            self.assertEqual(s.page.toPlainText(), before)
            s.page.buffer.composing.discard(123)
        s.editor.setReadOnly(False)

    def test_copied_image_with_html_wrapper_stays_a_local_attachment(self):
        s = self.open('Notes.md').widget.md_surface
        before = s.page.toPlainText()
        mime = QMimeData()
        image = QImage(2, 2, QImage.Format.Format_ARGB32)
        image.fill(Qt.GlobalColor.red)
        mime.setImageData(image)
        mime.setHtml('<img src="blob:https://example.invalid/image">')
        s.editor.insertFromMimeData(mime)
        self.assertIn('assets/', s.page.toPlainText())
        self.assertNotIn('blob:', s.page.toPlainText())
        self.assertEqual(len(list((self.root/'assets').glob('*.png'))), 1)
        s.page.undo()
        self.assertEqual(s.page.toPlainText(), before)

    def test_background_paste_keeps_original_owner_and_rejects_changed_source(self):
        from prompt_calculus_studio.markdown_html import from_html as convert
        s = self.open('Notes.md').widget.md_surface
        before = s.page.toPlainText()
        entered, release = threading.Event(), threading.Event()
        def delayed(value):
            entered.set()
            release.wait(3)
            return convert(value)
        mime = QMimeData()
        mime.setHtml('<b>prepared</b>')
        with patch('prompt_calculus_studio.markdown_paste_job.LARGE_HTML', 1), \
                patch('prompt_calculus_studio.markdown_paste_job.from_html', delayed):
            s.editor.insertFromMimeData(mime)
            for _ in range(100):
                QTest.qWait(5)
                time.sleep(.002)
                if entered.is_set():break
            other = self.open('Empty.md')
            other_before = other.widget.toPlainText()
            release.set()
            for _ in range(300):
                QTest.qWait(5)
                time.sleep(.002)
                if s.editor.prepared_paste.job is None:break
            self.assertIn('**prepared**', s.page.toPlainText())
            self.assertEqual(other.widget.toPlainText(), other_before)
            self.assertEqual(self.w.tabs_by_key[self.w.active_key], other)
            s.page.undo()
            self.assertEqual(s.page.toPlainText(), before)
            # File navigation can keep the original page in back/forward
            # history; reopen it through the same public navigation path.
            self.assertIs(self.open('Notes.md').widget, s.page)
            entered.clear()
            release.clear()
            s.editor.insertFromMimeData(mime)
            s.editor.insertPlainText('new edit')
            changed = s.page.toPlainText()
            release.set()
            for _ in range(300):
                QTest.qWait(5)
                time.sleep(.002)
                if s.editor.prepared_paste.job is None:break
            self.assertEqual(s.page.toPlainText(), changed)
            self.assertIn('沒有覆蓋', s.message)
