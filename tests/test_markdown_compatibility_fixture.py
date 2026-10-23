"""Offline core Markdown/Obsidian examples, independent of GUI and plugins.

These checks cover the fixture's source-to-projection contract. They do not
claim that an Obsidian runtime or native PCS input/save UI was exercised.
"""
from collections import Counter
from hashlib import sha256
from html.parser import HTMLParser
import json
from pathlib import Path
import unittest
from urllib.parse import unquote

from prompt_calculus_studio.markdown_render import advanced, render


ROOT = Path(__file__).parent / 'fixtures' / 'markdown_compatibility'
DOCUMENT = ROOT / 'Markdown-相容性測試.md'


class ProjectedTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth = 0
        self.tables = []
        self.current = self.row = self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.depth += 1
            if self.depth == 1:
                self.current = []
                self.tables.append(self.current)
        if self.depth == 1:
            if tag == 'tr':
                self.row = []
                self.current.append(self.row)
            elif tag in ('td', 'th'):
                self.cell = []

    def handle_data(self, value):
        if self.depth == 1 and self.cell is not None:
            self.cell.append(value)

    def handle_endtag(self, tag):
        if self.depth == 1 and tag in ('td', 'th') and self.cell is not None:
            self.row.append(''.join(self.cell))
            self.cell = None
        if tag == 'table':
            self.depth -= 1


class CompatibilityFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ROOT.resolve()
        cls.document = DOCUMENT.resolve()
        cls.source = cls.document.read_text(encoding='utf-8')
        cls.catalog = json.loads((cls.root / 'formula-cases.json').read_text(encoding='utf-8'))
        cls.projection = render(cls.source, cls.document, cls.root)

    def test_each_catalog_formula_reaches_renderer_once_with_exact_tex(self):
        self.assertGreaterEqual(len(self.catalog), 60)
        self.assertEqual(len({item['id'] for item in self.catalog}), len(self.catalog))
        # Block delimiters contribute outer line breaks; the TeX itself must
        # remain byte-for-byte identical, including internal spaces and slashes.
        formulas = Counter(item['source'].strip('\n') for item in self.projection.math)
        for item in self.catalog:
            with self.subTest(formula=item['id'], label=item['label']):
                self.assertEqual(formulas[item['tex']], 1)
                self.assertIn('原文：`' + item['tex'] + '`', self.source)

    def test_core_structure_styles_callouts_and_hidden_comments(self):
        body = self.projection.body
        for opening in ('<h1 ', '<h2 ', '<h3 ', '<h4 ', '<h5 ', '<h6 ',
                        '<strong>', '<em>', '<s>', '<mark>', '<code>', '<br>',
                        '<blockquote>', '<aside class="callout note">',
                        '<details class="callout success" open>', '<details class="callout warning">'):
            with self.subTest(tag=opening):
                self.assertIn(opening, body)
        self.assertIn('<mark>螢光標示</mark>', body)
        self.assertNotIn('這段 Obsidian 註解只應在原文中出現', body)
        self.assertNotIn('HTML 註解也不應出現在閱讀畫面', body)
        self.assertNotIn('test_version:', body)
        self.assertEqual(body.count('type="checkbox"'), 6)

    def test_table_cells_escaped_pipe_alias_and_exact_numeric_strings(self):
        parser = ProjectedTables()
        parser.feed(self.projection.body)
        self.assertEqual(len(parser.tables), 2)
        table = parser.tables[0]
        self.assertEqual(table[0], ['項目', '內容', '數值'])
        self.assertTrue(all(len(row) == 3 for row in table))
        self.assertEqual([row[-1] for row in table[1:]], [
            '001.2300', '1.4142135623730951', '6.02214076e23',
            '1.0000000000000001', '9007199254740993',
            '-0.0000000000000001', '299792458',
        ])
        self.assertEqual(table[5][1], 'a|b')
        self.assertEqual(table[6][1], '別名')
        self.assertIn('rowspan="2"', self.projection.body)
        self.assertIn('colspan="2"', self.projection.body)

    def test_reference_wiki_heading_and_block_links_keep_destinations(self):
        targets = [(unquote(item['target']), item['wiki']) for item in self.projection.links]
        for expected in (
            ('輔助筆記.md', False), ('輔助筆記', True),
            ('輔助筆記#連結目標', True), ('輔助筆記#^test-block', True),
            ('https://help.obsidian.md/advanced-syntax', False),
        ):
            self.assertIn(expected, targets)
        self.assertIn('>顯示別名</a>', self.projection.body)
        self.assertIn('>參照連結</a>', self.projection.body)
        self.assertNotIn('[[這不是連結]]', [item['target'] for item in self.projection.links])

    def test_images_and_note_embeds_resolve_relative_to_fixture(self):
        attachments = self.projection.attachments
        self.assertEqual(len(attachments), 4)
        self.assertEqual([(item['width'], item['height']) for item in attachments],
                         [(None, None), (48, 24), (48, None), (48, None)])
        for image in attachments:
            self.assertEqual(Path(image['path']).resolve(), self.root / 'assets' / 'red-blue.png')
        self.assertEqual(self.projection.embedded_count, 2)
        self.assertEqual(self.projection.body.count('class="note-embed"'), 2)
        self.assertIn(self.root / '輔助筆記.md', self.projection.dependencies)
        self.assertNotIn('^test-block</p>', self.projection.body)
        self.assertNotIn('找不到筆記', self.projection.body)

    def test_literal_code_currency_unicode_and_unsupported_examples(self):
        codes = self.projection.codes
        self.assertEqual(len(codes), 3)
        self.assertIn('%% literal comment %%', codes[1])
        self.assertIn(r'\usepackage{physics}', codes[2])
        formulas = [item['source'] for item in self.projection.math]
        self.assertNotIn('5 和 ', formulas)
        self.assertNotIn(r'\usepackage{physics}', formulas)
        self.assertNotIn(r'\dv{x}{t}', formulas)
        for token in ('µ μ Ω ω ° Å ± × ÷ ∂ ∇ ∫ ∑ √ ∞ ≈ ≠ ≤ ≥ → ↔ ℏ', 's⁻¹', 'H₂O'):
            self.assertIn(token, self.projection.body)
        self.assertEqual(len(self.projection.diagrams), 1)
        self.assertIn('flowchart LR', self.projection.diagrams[0])

    def test_standalone_highlight_uses_rich_route(self):
        self.assertTrue(advanced('只有 ==螢光標示== 的筆記。'))
        result = render('只有 ==螢光標示== 的筆記。', self.document, self.root)
        self.assertIn('<mark>螢光標示</mark>', result.body)

    def test_only_generated_tasks_become_disabled_checkboxes(self):
        forged='<input class="task-list-item-checkbox" disabled="disabled" type="checkbox">'
        text=('- [ ] 開始\n- [x] 完成\n  - [X]\t巢狀完成\n\n'+forged+
              '\n\n- [ ] 原始 HTML：'+forged+'\n\n`'+forged+'`\n')
        body=render(text,self.document,self.root).body
        class Inputs(HTMLParser):
            def __init__(self):super().__init__();self.inputs=[]
            def handle_starttag(self,tag,attrs):
                if tag=='input':self.inputs.append(dict(attrs))
        parser=Inputs();parser.feed(body)
        self.assertEqual(len(parser.inputs),4)
        self.assertEqual(sum('checked' in attrs for attrs in parser.inputs),2)
        self.assertTrue(all(attrs.get('type')=='checkbox' and 'disabled' in attrs for attrs in parser.inputs))
        self.assertEqual(body.count('&lt;input '),3)

    def test_projection_leaves_all_fixture_files_byte_identical(self):
        before = {p.relative_to(self.root).as_posix(): sha256(p.read_bytes()).hexdigest()
                  for p in self.root.rglob('*') if p.is_file()}
        render(self.source, self.document, self.root)
        after = {p.relative_to(self.root).as_posix(): sha256(p.read_bytes()).hexdigest()
                 for p in self.root.rglob('*') if p.is_file()}
        self.assertEqual(after, before)


if __name__ == '__main__':
    unittest.main()
