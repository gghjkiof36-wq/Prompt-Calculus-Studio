"""LLM/Obsidian math is display-only; source remains editable and exact."""
from pathlib import Path
import tempfile
import unittest

from prompt_calculus_studio.markdown_commands import format_edits
from prompt_calculus_studio.markdown_html import from_html
from prompt_calculus_studio.markdown_math import bracket_ranges
from prompt_calculus_studio.markdown_render import advanced, render
from prompt_calculus_studio.markdown_source import SourceSnapshot
from prompt_calculus_studio.markdown_syntax import line_info, spans, visible_text
from prompt_calculus_studio.markdown_table_cells import encode_cell, decode_cell


class MathProjectionTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.path = Path(self.area.name) / 'Physics.md'

    def tearDown(self):
        self.area.cleanup()

    def project(self, source):
        return render(source, self.path, self.path.parent)

    def test_four_delimiters_keep_exact_tex_and_select_rich_reader(self):
        formula = r'1.234567890123456789\times10^{-123}\,\mathrm{m\,s^{-2}}'
        for left, right, block in [('$', '$', False), ('$$', '$$', True),
                                   (r'\(', r'\)', False), (r'\[', r'\]', True)]:
            with self.subTest(left=left):
                source = left + formula + right
                self.assertTrue(advanced(source))
                self.assertEqual(self.project(source).math, [dict(source=formula, block=block)])

    def test_matrix_derivatives_and_physics_operators_are_not_markdown(self):
        formulas = [r'R(\theta)=\begin{pmatrix}\cos\theta&-\sin\theta\\\sin\theta&\cos\theta\end{pmatrix}',
                    r'\frac{\partial^2\psi}{\partial x^2}+\nabla\cdot\vec{E}+\nabla\times\mathbf{B}',
                    r'\int_0^\infty x^2\,\mathrm{d}x+\sum_{n=1}^\infty\frac{1}{n^2}+\sqrt[3]{x}',
                    r'\langle\psi|\hat H|\psi\rangle=\hbar\omega\left(n+\frac12\right)']
        source = '\n\n'.join(r'\[' + value + r'\]' for value in formulas)
        self.assertEqual([entry['source'] for entry in self.project(source).math], formulas)

    def test_multiline_formula_in_quote_and_list_keeps_container_boundaries(self):
        source = '> Formula\n> \\[\n> \\begin{aligned}\n> x&=1\\\\\n> y&=2\n> \\end{aligned}\n> \\]\n\nOutside\n\n- Value\n\n  \\[\n  \\sqrt{2}\n  \\]\n\n- Next\n'
        for value in (source, source.replace(r'\[', '$$').replace(r'\]', '$$')):
            with self.subTest(source=value):
                result = self.project(value)
                self.assertEqual(len(result.math), 2)
                self.assertEqual(result.math[0]['source'].strip(), '\\begin{aligned}\nx&=1\\\\\ny&=2\n\\end{aligned}')
                self.assertEqual(result.math[1]['source'].strip(), r'\sqrt{2}')
                self.assertIn('<blockquote>', result.body)
                self.assertIn('Outside', result.body)
                self.assertIn('Next', result.body)
                self.assertNotIn('> ', result.math[0]['source'])

    def test_bracket_block_interrupts_prose_and_inline_display_has_valid_wrapper(self):
        result = self.project('Before\n\\[x^2\\]\nAfter\n\nInline \\[y^2\\] tail $$z^2$$.')
        self.assertEqual([m['source'] for m in result.math], ['x^2', 'y^2', 'z^2'])
        self.assertEqual([m['block'] for m in result.math], [True, True, True])
        self.assertIn('>Inline <span class="formula"', result.body)

    def test_code_escaped_delimiters_currency_and_unclosed_input_remain_literal(self):
        source = ('`\\(x_1\\)`\n\n```tex\n\\[E=mc^2\\]\n```\n\n'
                  '    \\[code\\]\n\n\\\\(escaped\\\\)\n\nPrices $20 and $30.\n\nSuffix 20$ and 30$.\n\n'
                  '\\(unclosed\n\n\\[also unclosed\n')
        result = self.project(source)
        self.assertEqual(result.math, [])
        self.assertIn(r'\(x_1\)', result.body)
        self.assertEqual(result.codes, ['\\[E=mc^2\\]\n'])
        self.assertIn('$20 and $30', result.body)
        self.assertEqual(list(bracket_ranges(r'\\(escaped\\)')), [])

    def test_currency_in_one_paragraph_and_single_number_formula(self):
        source = r'貨幣控制：$5 和 $10；US$20.00；跳脫的 \$5。'
        self.assertEqual(self.project(source).math, [])
        self.assertEqual(self.project('$2$ and $x_1$').math,
                         [dict(source='2', block=False), dict(source='x_1', block=False)])
        self.assertEqual(self.project('$2$ = 2').math, [dict(source='2', block=False)])
        self.assertEqual(self.project('$x_1$ and $x_2$ at 10').math,
                         [dict(source='x_1', block=False), dict(source='x_2', block=False)])

    def test_highlight_only_note_selects_existing_rich_projection(self):
        self.assertTrue(advanced('==highlight=='))
        self.assertIn('<mark>highlight</mark>', self.project('==highlight==').body)

    def test_formula_in_link_label_table_and_footnote_is_recognized_once(self):
        source = ('[Energy \\(E=mc^2\\)](Note.md)\n\n| Quantity | Formula |\n| --- | --- |\n'
                  '| acceleration | \\(a=\\frac{dv}{dt}\\) |\n\nSee[^n].\n\n[^n]: \\(F=ma\\)\n')
        result = self.project(source)
        self.assertEqual([m['source'] for m in result.math], ['E=mc^2', r'a=\frac{dv}{dt}', 'F=ma'])
        self.assertEqual(result.links[0]['target'], 'Note.md')

    def test_literal_html_paste_and_katex_annotation_do_not_double_escape(self):
        formula = r'\[R=\begin{pmatrix}a&b\\c&d\end{pmatrix}\]'
        source = from_html('<p>Before ' + formula.replace('&', '&amp;') + '</p><p>After</p>')
        self.assertIn(formula, source)
        self.assertEqual(self.project(source).math[0]['source'], formula[2:-2])
        formula = r'\frac{\partial f}{\partial t}'
        source = from_html('<span class="katex"><math><annotation encoding="application/x-tex">' +
                           formula + '</annotation></math><span>duplicate</span></span>')
        self.assertEqual(source, '$' + formula + '$')
        self.assertEqual(self.project(source).math, [dict(source=formula, block=False)])
        self.assertEqual(self.project(from_html('<code>\\(literal\\)</code>')).math, [])

    def test_html_formulas_and_table_pipe_layers_keep_tex_exact(self):
        formulas = [r'\|\vec{x}\|', r'\langle x|\hat H|y\rangle',
                    r'1.230000000000000001\times10^{-123}\,\mathrm{m/s^2}']
        for left, right in [('$', '$'), ('$$', '$$'), (r'\(', r'\)'), (r'\[', r'\]')]:
            for formula in formulas:
                with self.subTest(left=left, formula=formula):
                    value = left + formula + right
                    self.assertEqual(from_html('<p>' + value + '</p>'), value)
                    self.assertEqual(decode_cell(encode_cell(value)), value)
                    source = from_html('<table><tr><th>Formula</th></tr><tr><td>' + value + '</td></tr></table>')
                    result = self.project(source)
                    self.assertEqual([item['source'] for item in result.math], [formula])
                    self.assertEqual(result.body.count('<td>'), 1)
        source = from_html(r'<table><tr><th>Literal</th></tr><tr><td><code>$\|x\|$</code></td></tr></table>')
        self.assertEqual(self.project(source).math, [])
        self.assertIn(r'$\|x\|$', self.project(source).body)

    def test_style_commands_and_live_source_display_preserve_formula_tokens(self):
        for source in [r'Before \(x_a + y_b + \text{a * b}\) after',
                       '\\[\n\\partial_x f + x_a\n\\]\n', '$$\n\\int f\n$$']:
            with self.subTest(source=source):
                target = 'x_a' if 'x_a' in source else r'\int'
                start = source.index(target)
                with self.assertRaises(ValueError):
                    format_edits(SourceSnapshot(source, 0), start, start + len(target), {'bold': True})
                if '\n' not in source:self.assertEqual(visible_text(source), source)
        literal = next(item for item in spans(r'\(x_a+y_b\)') if item.literal)
        self.assertEqual((literal.start, literal.end), (0, len(r'\(x_a+y_b\)')))
        self.assertTrue(all(item['protected'] for item in line_info('\\[\nx\n\\]')))


if __name__ == '__main__':
    unittest.main()
