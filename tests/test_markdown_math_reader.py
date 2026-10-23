"""Actual offline KaTeX rendering, paste and document round-trip contracts."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent, QMimeData, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.workspace_files import MarkdownPage
from prompt_calculus_studio.markdown_html import from_html


FORMULAS = [
    ('微分與偏微分', r'\frac{\mathrm{d}f}{\mathrm{d}t},\quad\frac{\partial^2\psi}{\partial x^2},\quad\dot{x},\ddot{x},f^{\prime\prime}(x)'),
    ('積分與根號', r'\int_0^{\infty}e^{-x^2}\,\mathrm{d}x=\frac{\sqrt{\pi}}2,\quad\iint_S f\,\mathrm{d}S,\quad\oint_C\vec F\cdot\mathrm{d}\vec r,\quad\sqrt[3]{a^2+b^2}'),
    ('級數與極限', r'\sum_{n=1}^{\infty}\frac{1}{n^2}=\frac{\pi^2}6,\quad\prod_{i=1}^{N}x_i,\quad\lim_{h\to0}\frac{f(x+h)-f(x)}h'),
    ('旋轉矩陣', r'R(\theta)=\begin{pmatrix}\cos\theta&-\sin\theta\\\sin\theta&\cos\theta\end{pmatrix},\quad\mathbf e_y\longrightarrow(-\sin\theta,\cos\theta)'),
    ('向量與場', r'\nabla f,\quad\nabla\cdot\vec E=\frac{\rho}{\varepsilon_0},\quad\nabla\times\mathbf B=\mu_0\mathbf J+\mu_0\varepsilon_0\frac{\partial\mathbf E}{\partial t}'),
    ('物理符號與單位', r'E=mc^2,\quad\langle\psi|\hat H|\psi\rangle=\hbar\omega\left(n+\frac12\right),\quad9.80665\,\mathrm{m\,s^{-2}}'),
    ('精度與科學記號', r'1.234567890123456789\times10^{-123}\,\mathrm{kg\,m^2\,s^{-2}},\quad6.02214076\times10^{23}\,\mathrm{mol^{-1}}'),
    ('分段與對齊', r'\begin{aligned}f(x)&=\begin{cases}x^2&x\ge0\\-x&x<0\end{cases}\\a^2+b^2&=c^2\end{aligned}'),
]


class MathReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-math-reader-')
        self.path = Path(self.area.name) / 'Physics.md'
        self.pages = []

    def tearDown(self):
        for page in self.pages:
            page.close()
            page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.area.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 20
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def js(self, page, expression):
        values = []
        page.md_surface.reader.rich.page().runJavaScript('JSON.stringify(' + expression + ')', values.append)
        self.wait(lambda: bool(values))
        return json.loads(values[0])

    def open(self, source, mode='read'):
        self.path.write_bytes(source.encode('utf-8'))
        page = MarkdownPage(self.path)
        self.pages.append(page)
        page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        page.resize(1120, 1080)
        page.show()
        page.md_surface.set_mode(mode)
        if mode == 'read':
            self.ready(page)
        return page

    def ready(self, page):
        self.wait(lambda: page.md_surface.reader.rich is not None and
                  page.md_surface.reader.rich.render_state == 'ready')

    def test_common_math_and_physics_render_offline_with_exact_annotations(self):
        source = '# 數學與物理公式\n\n' + '\n\n'.join('### ' + title + '\n\n\\[' + tex + '\\]'
                                                      for title, tex in FORMULAS)
        page = self.open(source)
        self.assertEqual(self.js(page, 'document.querySelectorAll(".formula .katex").length'), len(FORMULAS))
        self.assertEqual(self.js(page, 'document.querySelectorAll(".render-error,.katex-error").length'), 0)
        self.assertEqual(self.js(page, '[...document.querySelectorAll(".formula annotation")].map(n=>n.textContent)'),
                         [tex for _, tex in FORMULAS])
        self.assertGreaterEqual(self.js(page, 'document.querySelectorAll(".formula mtable").length'), 3)
        self.assertEqual(page.buffer.snapshot().text, source)
        self.assertFalse(page.dirty)
        self.assertFalse(page.buffer.text.isUndoAvailable())
        self.assertEqual(self.path.read_bytes(), source.encode())
        output = os.environ.get('PCS_MATH_SCREENSHOT_DIR')
        if output:
            folder = Path(output)
            folder.mkdir(parents=True, exist_ok=True)
            QTest.qWait(150)
            self.assertTrue(page.md_surface.reader.rich.grab().save(str(folder / 'math-physics-reader-wide.png')))
            (folder / 'math-physics-source.md').write_text(source, encoding='utf-8')
        page.resize(420, 900)
        QTest.qWait(150)
        self.assertLessEqual(self.js(page, 'document.documentElement.scrollWidth'), self.js(page, 'innerWidth') + 2)
        if output:
            self.assertTrue(page.md_surface.reader.rich.grab().save(str(folder / 'math-physics-reader-narrow.png')))

    def test_shared_math_physics_catalog_renders_every_formula_exactly(self):
        catalog = Path(__file__).parent / 'fixtures/markdown_compatibility/formula-cases.json'
        cases = json.loads(catalog.read_text(encoding='utf-8'))
        source = '\n\n'.join('### ' + item['id'] + ' ' + item['label'] + '\n\n\\[' + item['tex'] + '\\]'
                             for item in cases)
        page = self.open(source)
        errors = self.js(page, '[...document.querySelectorAll(".render-error,.katex-error")].map(n=>n.textContent)')
        self.assertEqual(errors, [])
        self.assertEqual(self.js(page, '[...document.querySelectorAll(".formula annotation")].map(n=>n.textContent)'),
                         [item['tex'] for item in cases])
        self.assertEqual(page.buffer.snapshot().text, source)
        self.assertFalse(page.dirty)
        output = os.environ.get('PCS_MATH_SCREENSHOT_DIR')
        if output:
            (Path(output) / 'formula-catalog-receipt.json').write_text(json.dumps({
                'engine': self.js(page, 'katex.version'), 'count': len(cases),
                'ids': [item['id'] for item in cases], 'errors': errors,
                'source_unchanged': page.buffer.snapshot().text == source,
            }, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_plain_and_html_paste_save_reopen_undo_and_redo_keep_formulas(self):
        formula = r'\[R(\theta)=\begin{pmatrix}\cos\theta&-\sin\theta\\\sin\theta&\cos\theta\end{pmatrix}\]'
        for kind in ('text', 'html'):
            with self.subTest(kind=kind):
                page = self.open('Before\n\n', mode='write')
                surface = page.md_surface
                cursor = surface.editor.textCursor()
                cursor.movePosition(cursor.MoveOperation.End)
                surface.editor.setTextCursor(cursor)
                mime = QMimeData()
                if kind == 'html':
                    mime.setHtml('<p>' + formula.replace('&', '&amp;') + '</p>')
                else:
                    mime.setText(formula)
                surface.editor.insertFromMimeData(mime)
                self.wait(lambda: formula in page.buffer.snapshot().text)
                after = page.buffer.snapshot().text
                self.assertTrue(page.dirty)
                self.assertTrue(page.save())
                page.undo()
                self.assertEqual(page.buffer.snapshot().text, 'Before\n\n')
                page.redo()
                self.assertEqual(page.buffer.snapshot().text, after)
                self.assertTrue(page.save())
                saved = self.path.read_bytes()
                surface.set_mode('read')
                self.ready(page)
                self.assertEqual(self.js(page, 'document.querySelectorAll(".formula .katex").length'), 1)
                self.assertEqual(self.js(page, 'document.querySelector(".formula annotation").textContent'), formula[2:-2])
                self.assertEqual(self.path.read_bytes(), saved)
                page.close()
                reopened = self.open(saved.decode())
                self.assertEqual(reopened.encoded(), saved)
                self.assertEqual(self.js(reopened, 'document.querySelectorAll(".formula .katex").length'), 1)
                self.assertFalse(reopened.dirty)

    def test_number_at_start_and_html_table_norm_keep_exact_math(self):
        table = from_html(r'<table><tr><th>Norm</th></tr><tr><td>\(\|\vec{x}\|\)</td></tr>'
                          r'<tr><td>$\langle x|y\rangle$</td></tr></table>')
        source = '$2$ = 2\n\n' + table
        page = self.open(source)
        self.assertEqual(self.js(page, '[...document.querySelectorAll(".formula annotation")].map(n=>n.textContent)'),
                         ['2', r'\|\vec{x}\|', r'\langle x|y\rangle'])
        self.assertEqual(self.js(page, 'document.querySelectorAll(".render-error,.katex-error").length'), 0)
        self.assertEqual(self.js(page, 'document.querySelectorAll("td").length'), 2)
        self.assertEqual(page.buffer.snapshot().text, source)
        self.assertFalse(page.dirty)

    def test_unsupported_tex_preserves_visible_source_and_does_not_execute_html(self):
        source = r'\(\notARealTeXCommand{x}\)' + '\n\n' + r'\(\href{javascript:alert(1)}{unsafe}\)' + '\n\n' + r'\(\frac{1}{2}\)'
        page = self.open(source)
        self.assertEqual(self.js(page, 'document.querySelectorAll(".render-error").length'), 1)
        self.assertEqual(self.js(page, 'document.querySelector(".render-error").textContent'), r'\notARealTeXCommand{x}')
        self.assertEqual(self.js(page, 'document.querySelectorAll(".formula a").length'), 0)
        self.assertEqual(self.js(page, 'document.querySelectorAll(".formula annotation").length'), 2)
        self.assertEqual(page.buffer.snapshot().text, source)
        self.assertFalse(page.dirty)


if __name__ == '__main__':
    unittest.main()
