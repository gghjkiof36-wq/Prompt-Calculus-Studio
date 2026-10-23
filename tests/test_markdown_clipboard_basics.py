"""Ordinary clipboard images, links and unsupported visuals retain meaning."""
import json
import html
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from PySide6.QtCore import QEvent, QMimeData, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_html import from_html
from prompt_calculus_studio.markdown_paste import clipboard_text
from prompt_calculus_studio.markdown_render import render
from prompt_calculus_studio.markdown_rich_view import RichView
from prompt_calculus_studio.workspace_files import MarkdownPage
from test_markdown_inline_png import inline_png


class ClipboardConversionTests(unittest.TestCase):
    def test_valid_png_is_kept_and_invalid_data_is_bounded(self):
        uri = inline_png(8, 4, (80, 120, 160, 255))
        self.assertEqual(from_html('<img alt="圖" src="' + uri + '">'), '![圖](' + uri + ')')
        for source in ('data:image/svg+xml,<svg onload="bad()"/>', 'data:image/png;base64,' + 'A' * 100_000):
            # Attribute encoding, not a loose tag, mirrors actual HTML MIME.
            import html
            result = from_html('<img alt="圖" src="' + html.escape(source, quote=True) + '">')
            self.assertLess(len(result), 100)
            self.assertIn('無法讀取', result)
            self.assertNotIn('data:', result)

    def test_inline_semantics_and_real_links_survive_conversion(self):
        source = from_html('<p>H<sub>2</sub>O、10<sup>-123</sup>、<mark>重點</mark> '
                           '<a href="https://example.com/source?x=1&amp;y=2">真正來源</a> '
                           '<a>無網址</a> <a href="javascript:alert(1)">不執行</a></p>')
        self.assertIn('H<sub>2</sub>O', source)
        self.assertIn('10<sup>-123</sup>', source)
        self.assertIn('<mark>重點</mark>', source)
        projection = render(source, Path('Note.md'), Path('.'))
        self.assertEqual([item['target'] for item in projection.links], ['https://example.com/source?x=1&y=2'])
        self.assertIn('無網址', projection.body)
        self.assertNotIn('javascript:', source)

    def test_article_link_keeps_thumbnail_source_and_title_without_nested_actions(self):
        article = 'https://example.com/article?id=1&language=zh'
        images = (inline_png(8, 4, (80, 120, 160, 255)),
                  'https://cdn.example.com/thumbnail.png?width=80&v=2')
        for target in images:
            with self.subTest(image=target[:45]):
                image = '<img alt="縮圖" src="' + html.escape(target, quote=True) + '">'
                source = from_html('<p>前文 <a href="' + html.escape(article, quote=True) + '">' +
                                   image + image + '文章標題</a> 後文</p>')
                projection = render(source, Path('Note.md'), Path('.'),
                                    lambda entry, index: RichView.resource(SimpleNamespace(), entry, index))
                self.assertEqual([item['target'] for item in projection.links], [article])
                self.assertEqual([item['target'] for item in projection.attachments], [target, target])
                linked = projection.body.split('<a href="pcs-action:link/0">', 1)[1].split('</a>', 1)[0]
                self.assertIn('文章標題', linked)
                self.assertNotIn('<a ', linked)
                self.assertIn('前文 ', projection.body)
                self.assertIn(' 後文', projection.body)
                if target.startswith('https:'):
                    self.assertEqual(projection.body.count('href="pcs-action:remote/'), 2)
                    self.assertNotIn('<img ', projection.body)
                else:self.assertEqual(linked.count('<img '), 2)
                without_href = render(from_html('<a>' + image + '無文章網址</a>'), Path('Note.md'), Path('.'))
                self.assertEqual(without_href.links, [])
                self.assertEqual(without_href.attachments[0]['target'], target)
                self.assertIn('無文章網址', without_href.body)

    def test_svg_canvas_and_embeds_have_description_or_real_source_not_silent_loss(self):
        source = from_html('<svg role="img"><title>折線圖</title><desc>五月 12、六月 15</desc>'
                           '<path d="M0 0L2 2"/><script>secret()</script></svg>'
                           '<canvas aria-label="量測圖">無法顯示時的說明</canvas>'
                           '<iframe title="互動模型" src="https://example.com/model"></iframe>'
                           '<object title="原始附件" data="https://example.com/file.pdf"></object>'
                           '<embed title="不信任位置" src="javascript:alert(1)">'
                           '<button><svg><title>選單圖示</title></svg></button>')
        for phrase in ('折線圖', '五月 12、六月 15', '量測圖', '無法顯示時的說明', '圖形未保留', '圖表未保留'):
            self.assertIn(phrase, source)
        projection = render(source, Path('Note.md'), Path('.'))
        self.assertEqual([entry['target'] for entry in projection.links],
                         ['https://example.com/model', 'https://example.com/file.pdf'])
        for excluded in ('<svg', '<iframe', 'secret()', 'javascript:', '選單圖示'):
            self.assertNotIn(excluded, source)

    def test_empty_html_falls_back_to_text_and_markdown_stays_authoritative(self):
        mime = QMimeData()
        mime.setHtml('<html><head><style>p{color:red}</style></head><body> </body></html>')
        mime.setText('Actual text\r\n\\(x_1\\)')
        self.assertEqual(clipboard_text(mime), 'Actual text\n\\(x_1\\)')
        mime.setData('text/markdown', b'**original**')
        self.assertEqual(clipboard_text(mime), '**original**')
        mime.removeFormat('text/markdown')
        mime.setHtml('<b>broken')
        with self.assertRaises(ValueError):clipboard_text(mime)


class ClipboardReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-clipboard-basics-')
        self.root = Path(self.area.name)
        self.path = self.root / 'Note.md'
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.close();page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.area.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:QTest.qWait(20)
        self.assertTrue(predicate())

    def open(self):
        page = MarkdownPage(self.path)
        self.pages.append(page)
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        page.resize(900, 650);page.show()
        page.md_surface.set_mode('write')
        return page

    def read_images(self, page):
        page.md_surface.set_mode('read')
        self.wait(lambda: page.md_surface.reader.rich is not None and
                  page.md_surface.reader.rich.render_state == 'ready')
        deadline = time.monotonic() + 15
        while True:
            values = []
            page.md_surface.reader.rich.page().runJavaScript(
                'JSON.stringify([...document.querySelectorAll("#document img")].map(n=>({loaded:n.complete,width:n.naturalWidth,height:n.naturalHeight})))', values.append)
            self.wait(lambda: bool(values))
            images = json.loads(values[0])
            if all(item['loaded'] for item in images):return images
            self.assertLess(time.monotonic(), deadline)
            QTest.qWait(20)

    def test_bitmap_and_html_png_paste_save_reopen_and_undo_keep_images(self):
        for bitmap in (True, False):
            with self.subTest(bitmap=bitmap):
                self.path.write_text('Before\n\n', encoding='utf-8')
                page = self.open();surface = page.md_surface
                cursor = surface.editor.textCursor();cursor.movePosition(cursor.MoveOperation.End)
                surface.editor.setTextCursor(cursor)
                mime = QMimeData()
                if bitmap:
                    image = QImage(8, 4, QImage.Format.Format_ARGB32);image.fill(Qt.GlobalColor.red)
                    mime.setImageData(image)
                    mime.setHtml('<img src="blob:https://example.com/bitmap">')
                else:
                    mime.setHtml('<p>Before image</p><img alt="圖" src="' + inline_png(8, 4, (40, 80, 120, 255)) + '">')
                surface.editor.insertFromMimeData(mime)
                self.wait(lambda: '![' in page.buffer.snapshot().text)
                after = page.buffer.snapshot().text
                if bitmap:
                    self.assertIn('assets/', after)
                    self.assertEqual(len(list((self.root / 'assets').glob('*.png'))), 1)
                    self.assertNotIn('blob:', after)
                else:self.assertIn('data:image/png;base64,', after)
                self.assertTrue(page.save())
                page.undo();self.assertEqual(page.buffer.snapshot().text, 'Before\n\n')
                page.redo();self.assertEqual(page.buffer.snapshot().text, after)
                self.assertTrue(page.save())
                saved = self.path.read_bytes()
                self.assertEqual(self.read_images(page), [dict(loaded=True, width=8, height=4)])
                self.assertEqual(page.buffer.snapshot().text, after)
                self.assertEqual(self.path.read_bytes(), saved)
                page.close()
                reopened = self.open()
                self.assertEqual(self.read_images(reopened), [dict(loaded=True, width=8, height=4)])
                self.assertFalse(reopened.dirty)
                self.assertEqual(reopened.encoded(), saved)

    def test_actual_reader_keeps_article_targets_and_remote_thumbnail_load_separate(self):
        png = inline_png(8, 4, (40, 80, 120, 255))
        source = from_html('<p>前文 <a href="https://example.com/inline"><img alt="內嵌縮圖" src="' + png +
                           '">內嵌文章</a>，<a href="https://example.com/remote"><img alt="遠端縮圖" '
                           'src="https://cdn.example.com/thumb.png">遠端文章</a> 後文</p>')
        self.path.write_text(source, encoding='utf-8')
        page = self.open()
        self.assertEqual(self.read_images(page), [dict(loaded=True, width=8, height=4)])
        results = []
        page.md_surface.reader.rich.page().runJavaScript('JSON.stringify({links: '
            '[...document.querySelectorAll("#document a")].map(n=>({href:n.getAttribute("href"),'
            'text:n.textContent,images:n.querySelectorAll("img").length,nested:n.querySelectorAll("a").length})), '
            'text:document.getElementById("document").textContent})', results.append)
        self.wait(lambda: bool(results))
        state = json.loads(results[0])
        for link in state['links']:
            self.assertRegex(link['href'], r'^pcs-action:\d+/(?:link|remote)/\d+$')
        self.assertEqual([item['href'].split('/', 1)[1] for item in state['links']],
                         ['link/0', 'link/1', 'remote/1'])
        self.assertEqual(state['links'][0]['text'], '內嵌文章')
        self.assertEqual(state['links'][0]['images'], 1)
        self.assertIn('遠端文章', state['links'][1]['text'])
        self.assertTrue(all(item['nested'] == 0 for item in state['links']))
        self.assertIn('前文 ', state['text'])
        self.assertIn(' 後文', state['text'])
        self.assertEqual(page.buffer.snapshot().text, source)
        self.assertFalse(page.dirty)

    def test_multimime_linked_thumbnail_keeps_article_or_falls_back_to_bitmap(self):
        uri = inline_png(8, 4, (40, 80, 120, 255))
        article = 'https://example.com/article?id=123&part=2'
        cases = [
            ('<a href="' + html.escape(article, quote=True) + '"><img src="' + uri + '" alt="縮圖"></a>', True),
            ('<a href="' + html.escape(article, quote=True) + '"><img src="https://cdn.example.com/thumb.png" alt="縮圖"></a>', True),
            ('', False),
            ('<a href="' + article + '"><img src="' + uri + '">', False),
            ('<a href="' + article + '"><img src="blob:https://example.com/image"></a>', False),
            ('<a href="javascript:alert(1)"><img src="' + uri + '"></a>', False),
        ]
        for index, (markup, linked) in enumerate(cases):
            with self.subTest(case=index, linked=linked):
                self.path = self.root / f'Mixed-{index}.md'
                self.path.write_text('Before\n\n', encoding='utf-8')
                page = self.open()
                cursor = page.md_surface.editor.textCursor();cursor.movePosition(cursor.MoveOperation.End)
                page.md_surface.editor.setTextCursor(cursor)
                mime = QMimeData();image = QImage(8, 4, QImage.Format.Format_ARGB32)
                image.fill(Qt.GlobalColor.red);mime.setImageData(image);mime.setText('');mime.setHtml(markup)
                page.md_surface.editor.setReadOnly(True)
                page.md_surface.editor.insertFromMimeData(mime)
                self.assertEqual(page.buffer.snapshot().text, 'Before\n\n')
                page.md_surface.editor.setReadOnly(False)
                page.md_surface.editor.insertFromMimeData(mime)
                self.wait(lambda: '![' in page.buffer.snapshot().text)
                after = page.buffer.snapshot().text
                projection = render(after, self.path, self.root)
                self.assertEqual([item['target'] for item in projection.links], [article] if linked else [])
                self.assertEqual(len(projection.attachments), 1)
                if linked:
                    self.assertNotIn('assets/', after)
                    self.assertIn(uri if index == 0 else 'https://cdn.example.com/thumb.png', after)
                else:
                    self.assertIn('assets/', after)
                    self.assertNotIn('blob:', after)
                self.assertTrue(page.save())
                page.undo();self.assertEqual(page.buffer.snapshot().text, 'Before\n\n')
                page.redo();self.assertEqual(page.buffer.snapshot().text, after)
                self.assertTrue(page.save());saved = self.path.read_bytes()
                page.close();reopened = self.open()
                self.assertEqual(reopened.encoded(), saved)
                self.assertFalse(reopened.dirty)

    def test_empty_html_text_fallback_applies_in_sync_and_worker_paths(self):
        for threshold in (1, 1024 * 1024):
            with self.subTest(threshold=threshold):
                self.path.write_text('Before\n\n', encoding='utf-8')
                page = self.open();surface = page.md_surface
                mime = QMimeData();mime.setHtml('<p> </p>');mime.setText('Actual \\(x_1\\)')
                with patch('prompt_calculus_studio.markdown_paste_job.LARGE_HTML', threshold):
                    surface.editor.insertFromMimeData(mime)
                    self.wait(lambda: 'Actual' in page.buffer.snapshot().text)
                self.assertIn('\\(x_1\\)', page.buffer.snapshot().text)
                page.undo();self.assertEqual(page.buffer.snapshot().text, 'Before\n\n')
                self.assertFalse(page.dirty)


if __name__ == '__main__':unittest.main()
