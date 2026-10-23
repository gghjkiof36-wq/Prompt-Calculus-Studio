"""Captured tables retain structure without granting source HTML capabilities."""
import copy
import tempfile
import unittest
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from prompt_calculus_studio.connector_capture import validate_capture
from prompt_calculus_studio.conversation_markdown import message_text
from prompt_calculus_studio.markdown_render import advanced, render
from prompt_calculus_studio import markdown_table_html as tables


class Document(HTMLParser):
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.tags = []
        self.text = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))

    def handle_data(self, value):
        self.text.append(value)


class HtmlTableTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / '對話.md'

    def project(self, source):
        return render(source, self.path, self.root)

    def test_capture_save_reopen_preserves_two_by_two_metrics_and_literal_values(self):
        source = ('<table class="pcs-metric-grid"><tbody>'
                  '<tr><td><p>價格</p><strong>001.20</strong><small>USD</small></td>'
                  '<td><p>數量</p><strong>−0.0005</strong><small>前期</small></td></tr>'
                  '<tr><td><p>變化</p><strong>+1.20%</strong></td>'
                  '<td><p>倍率</p><strong>10<sup>−3</sup></strong><br>H<sub>2</sub>O</td></tr>'
                  '</tbody></table>')
        capture = dict(version=1, provider='chatgpt', conversation_id='synthetic',
                       title='合成四格', url='https://chatgpt.com/c/synthetic', method='dom',
                       scope='selection', adapter_version='1', captured_at='2026-10-24T00:00:00Z',
                       messages=[dict(id='one', role='assistant', content=source)])
        original = copy.deepcopy(capture)
        item = validate_capture(capture)
        note = message_text('one', item['mapping']['one'])
        self.assertEqual(capture, original)
        self.assertIn(source, note)
        self.path.write_text(note, encoding='utf-8')
        before = self.path.read_bytes()
        first = self.project(note)
        reopened = self.project(self.path.read_text(encoding='utf-8'))
        self.assertEqual(first.body, reopened.body)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertTrue(advanced(source))
        document = Document(first.body)
        self.assertEqual(sum(tag == 'tr' for tag, _ in document.tags), 2)
        self.assertEqual(sum(tag == 'td' for tag, _ in document.tags), 4)
        self.assertEqual(next(attrs['class'] for tag, attrs in document.tags if tag == 'table'), 'pcs-metric-grid')
        for value in ('001.20', '−0.0005', '+1.20%', '−3', 'H', '2', 'O'):
            self.assertIn(value, document.text)
        self.assertIn('<sup>−3</sup>', first.body)
        self.assertIn('<sub>2</sub>', first.body)
        self.assertIn('table-layout:fixed', first.body)

    def test_merged_nested_table_caption_lists_and_links_keep_semantics(self):
        source = ('<table><caption>成本 &amp; 數量</caption><thead><tr><th colspan="2" scope="colgroup">總覽</th></tr></thead>'
                  '<tbody><tr><th rowspan="0" scope="rowgroup">組別</th><td>'
                  '<p><a href="https://example.com/a?x=1&amp;y=2">原文</a></p>'
                  '<ul><li><em>甲</em> <code>&lt;tag&gt;</code></li></ul>'
                  '<table><tr><td>巢狀</td></tr></table></td></tr><tr><td>尾列</td></tr></tbody></table>')
        result = self.project(source)
        document = Document(result.body)
        self.assertEqual(sum(tag == 'table' for tag, _ in document.tags), 2)
        self.assertIn(('th', {'colspan': '2', 'scope': 'colgroup'}), document.tags)
        self.assertIn(('th', {'rowspan': '0', 'scope': 'rowgroup'}), document.tags)
        self.assertIn('成本 & 數量', document.text)
        self.assertIn('<code>&lt;tag&gt;</code>', result.body)
        self.assertEqual(result.links, [dict(target='https://example.com/a?x=1&y=2', path=str(self.path), wiki=False)])
        self.assertIn(('a', {'href': 'pcs-action:link/0'}), document.tags)

    def test_source_html_never_contributes_code_css_resource_urls_or_action_ids(self):
        source = ('<table class="pcs-metric-grid evil" style="background:url(https://evil.test)" id="document">'
                  '<tr onclick="attack()"><td colspan="999999" rowspan="-1" background="https://evil.test">'
                  '<script>alert(1)</script><style>body{display:none}</style>'
                  '<iframe src="https://evil.test">hidden</iframe><svg><a href="https://evil.test">svg</a></svg>'
                  '<img src="https://evil.test" onerror="attack()"><input autofocus>'
                  '<span data-math="0" class="formula" style="position:fixed">visible &lt;script&gt;</span>'
                  '<a href="jav&#x61;script:alert(1)">bad1</a><a href="pcs-action:delete/0">bad2</a>'
                  '<a href="data:text/html,attack">bad3</a><a href="//evil.test">bad4</a>'
                  '<a href="https://example.com/" target="_blank" onclick="attack()">good</a>'
                  '</td></tr></table>')
        result = self.project(source)
        document = Document(result.body)
        self.assertTrue({'script', 'style', 'iframe', 'svg', 'img', 'input'}.isdisjoint(tag for tag, _ in document.tags))
        self.assertNotIn('evil.test', result.body)
        self.assertNotIn('alert(1)', result.body)
        self.assertNotIn('attack()', result.body)
        for tag, attrs in document.tags:
            self.assertFalse(set(attrs) & {'onclick', 'onerror', 'id', 'background', 'data-math', 'target'})
            self.assertNotIn('style', attrs)
        self.assertEqual([attrs for tag, attrs in document.tags if tag == 'a'], [{'href': 'pcs-action:link/0'}])
        self.assertEqual(len(result.links), 1)
        self.assertIn('visible &lt;script&gt;', result.body)
        for text in ('bad1', 'bad2', 'bad3', 'bad4', 'good'):
            self.assertIn(text, ''.join(document.text))

    def test_literal_html_text_is_not_reinterpreted_as_markdown_or_formula(self):
        source = '<table><tr><td>**literal** [not a link](https://example.com) $5 and $10 &lt;b&gt; &amp; 😀</td></tr></table>'
        result = self.project(source)
        self.assertEqual(result.links, [])
        self.assertEqual(result.math, [])
        self.assertEqual(''.join(Document(result.body).text), '**literal** [not a link](https://example.com) $5 and $10 <b> & 😀')
        # Existing dollar-math projection also handles superscript/subscript TeX.
        formula = self.project('$x^{2} + y_{n}$')
        self.assertEqual(formula.math, [dict(source='x^{2} + y_{n}', block=False)])

    def test_malformed_and_over_budget_tables_remain_inert_readable_source(self):
        for source in ('<table><tr><td>KEEP</tr></table>', '<table><tr><td>KEEP', '<table><!DOCTYPE html></table>'):
            with self.subTest(source=source):
                result = self.project(source)
                self.assertIn('<pre class="unknown">', result.body)
                self.assertFalse(any(tag == 'table' for tag, _ in Document(result.body).tags))
        source = '<table><tr><td>KEEP</td></tr></table>'
        for limit, value in (('MAX_BYTES', 8), ('MAX_NODES', 2), ('MAX_DEPTH', 3)):
            with patch.object(tables, limit, value):
                result = self.project(source)
                self.assertIn('KEEP', result.body)
                self.assertIn('<pre class="unknown">', result.body)

    def test_summary_meter_bounds_and_fallback_do_not_change_value_text(self):
        source = ('<table class="pcs-summary-card"><tr><td colspan="2"><p>CVSS</p><h1>9.2 / 10</h1>'
                  '<meter min="0" value="9.2" max="10" style="position:fixed" onclick="attack()">9.2 / 10</meter>'
                  '</td></tr><tr><td>漏洞</td><td>CVE-2026-0000</td></tr></table>')
        result = self.project(source)
        document = Document(result.body)
        meter = next(attrs for tag, attrs in document.tags if tag == 'meter')
        self.assertEqual({key: meter[key] for key in ('min', 'value', 'max')}, dict(min='0', value='9.2', max='10'))
        self.assertEqual(meter['style'], 'width:100%;height:1em;vertical-align:middle')
        self.assertNotIn('onclick', meter)
        for attributes in ('min="0" value="nan" max="10"', 'min="0" value="1e309" max="10"',
                           'min="0" value="11" max="10"', 'min="1" value="1" max="1"',
                           'min="0" value="9007199254740993" max="9007199254740992"',
                           'value="9.2" max="10"'):
            result = self.project('<table><tr><td><meter '+attributes+'>KEEP 9.2 / 10</meter></td></tr></table>')
            self.assertNotIn('<meter', result.body)
            self.assertIn('KEEP 9.2 / 10', result.body)

    def test_explicit_list_numbers_survive_without_unbounded_attributes(self):
        source = '<table><tr><td><ol start="7"><li>甲</li><li value="12">乙</li></ol></td></tr></table>'
        result = self.project(source)
        self.assertIn('<ol start="7">', result.body)
        self.assertIn('<li value="12">乙</li>', result.body)
        result = self.project(source.replace('start="7"', 'start="999999999999999999"'))
        self.assertNotIn('999999999999999999', result.body)


if __name__ == '__main__':
    unittest.main()
