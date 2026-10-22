"""Plain fences retain exact Pygments output and per-occurrence source actions."""
import html
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter
from prompt_calculus_studio import markdown_render as renderer


class PlainFenceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def render(self,text):return renderer.render(text,self.root/'Notes.md',self.root)

    def test_plain_fences_keep_normalization_escaping_copy_and_source_positions(self):
        samples=['', '\n\n', "<&>\"' 😀\t資料\n", '\ufeffBOM\n', '\nfirst\n\nlast\n\n', '<script>alert(1)</script>\n']
        for code in samples:
            with self.subTest(code=code):
                source='```text\n'+code+'\n```\n'
                result=self.render(source)
                expected=highlight(result.codes[0],get_lexer_by_name('text'),HtmlFormatter(nowrap=True))
                self.assertIn('<pre><code>'+expected+'</code></pre>',result.body)
                self.assertIn('pcs-action:copy/0',result.body)
                self.assertIn('pcs-action:line/0',result.body)
                self.assertNotIn('<script>',result.body)

    def test_distinct_plain_blocks_do_not_build_unused_colour_stylesheets(self):
        source=''.join('```text\n第'+str(i)+'段 & < >\n```\n\n' for i in range(120))
        with patch.object(renderer,'HtmlFormatter',side_effect=AssertionError('plain text needs no colour formatter')) as formatter:
            result=self.render(source)
        formatter.assert_not_called()
        self.assertEqual(len(result.codes),120)
        for i in (0,59,119):
            self.assertEqual(result.codes[i],'第'+str(i)+'段 & < >\n')
            self.assertIn(f'pcs-action:copy/{i}',result.body)
            self.assertIn(f'pcs-action:line/{i*4}',result.body)

    def test_coloured_unknown_and_unlabelled_fences_keep_existing_behaviour(self):
        source='```python\nprint("hello")\n```\n\n```missing-language\n<&>\n```\n\n```\nplain & < >\n```\n'
        result=self.render(source)
        self.assertIn(highlight(result.codes[0],get_lexer_by_name('python'),HtmlFormatter(nowrap=True)),result.body)
        self.assertIn(html.escape(result.codes[1]),result.body)
        self.assertIn(html.escape(result.codes[2]),result.body)

    def test_block_markers_are_removed_only_in_prose_and_preserve_line_anchors(self):
        result=self.render('Start ^first\n\n`literal ^inline`\n\n```text\nkeep ^fenced\n```\n\nLast ^last\n')
        self.assertIn('Start',result.body);self.assertNotIn('Start ^first',result.body)
        self.assertIn('literal ^inline',result.body);self.assertIn('keep ^fenced',result.body)
        self.assertNotIn('Last ^last',result.body);self.assertIn('id="line-8"',result.body)

    def test_no_block_marker_skips_protected_prose_scan(self):
        with patch('prompt_calculus_studio.markdown_links.prose_mask',side_effect=AssertionError('unnecessary scan')):
            result=self.render('| A | B |\n|---|---|\n| one | two |\n\n```text\nsafe\n```\n')
        self.assertEqual(result.codes,['safe\n']);self.assertIn('<table',result.body)


if __name__=='__main__':unittest.main()
