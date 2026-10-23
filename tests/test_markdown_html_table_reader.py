"""The browser sanitizer must retain verified table spans and meter bounds."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.workspace_files import MarkdownPage


SOURCE = ('<table class="pcs-summary-card"><caption>合成範圍</caption><thead>'
          '<tr><th colspan="2" scope="colgroup">總覽</th></tr></thead><tbody>'
          '<tr><th rowspan="0" scope="rowgroup">風險</th><td>9.2 / 10</td></tr>'
          '<tr><td><meter class="pcs-meter-danger" min="0" value="9.2" max="10">9.2 / 10</meter></td></tr>'
          '</tbody></table>\n\n'
          '<table class="pcs-metric-grid"><tr><td><strong class="pcs-metric-value">001.20</strong></td>'
          '<td>10<sup>−6</sup></td></tr><tr><td>H<sub>2</sub>O</td>'
          '<td><a href="https://example.com/source">來源</a></td></tr></table>\n\n'
          '<table class="numbering"><tr><td><ol start="7"><li>甲</li><li value="12">乙</li></ol></td></tr></table>\n')


class HtmlTableReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-html-table-reader-')
        self.path = Path(self.area.name) / 'Table.md'
        self.path.write_bytes(SOURCE.encode('utf-8'))
        self.page = MarkdownPage(self.path)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.page.resize(1000, 700)
        self.page.show()
        self.page.md_surface.set_mode('read')
        self.wait(lambda: self.page.md_surface.reader.rich is not None and
                  self.page.md_surface.reader.rich.render_state == 'ready')

    def tearDown(self):
        self.page.close()
        self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.area.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 10
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def test_table_semantics_survive_both_sanitizers_and_keep_source_unchanged(self):
        values = []
        self.page.md_surface.reader.rich.page().runJavaScript('''JSON.stringify((()=>{
          const meter=document.querySelector('meter'), table=document.querySelector('.pcs-summary-card');
          const head=table.tHead.rows[0].cells[0], side=table.tBodies[0].rows[0].cells[0];
          const grid=document.querySelector('.pcs-metric-grid');
          return {meter:{min:meter.min,value:meter.value,max:meter.max,className:meter.className},
            header:{span:head.colSpan,scope:head.scope},side:{span:side.rowSpan,scope:side.scope},
            grid:[...grid.rows].map(r=>[...r.cells].map(c=>c.textContent)),
            sup:grid.querySelector('sup')?.textContent,sub:grid.querySelector('sub')?.textContent,
            link:grid.querySelector('a').getAttribute('href'),value:grid.querySelector('strong').className,
            numbering:{start:document.querySelector('ol').start,value:document.querySelector('li[value]').value}};
        })())''', values.append)
        self.wait(lambda: bool(values))
        measured = json.loads(values[0])
        self.assertEqual(measured['meter'], dict(min=0, value=9.2, max=10, className='pcs-meter-danger'))
        self.assertEqual(measured['header'], dict(span=2, scope='colgroup'))
        self.assertEqual(measured['side'], dict(span=0, scope='rowgroup'))
        self.assertEqual(measured['grid'], [['001.20', '10−6'], ['H2O', '來源']])
        self.assertEqual(measured['sup'], '−6')
        self.assertEqual(measured['sub'], '2')
        self.assertEqual(measured['value'], 'pcs-metric-value')
        self.assertIn('pcs-action:', measured['link'])
        self.assertEqual(measured['numbering'], dict(start=7, value=12))
        self.assertEqual(self.page.encoded(), SOURCE.encode('utf-8'))
        self.assertEqual(self.path.read_bytes(), SOURCE.encode('utf-8'))
        self.assertFalse(self.page.dirty)
        self.assertFalse(self.page.buffer.text.isUndoAvailable())


if __name__ == '__main__':
    unittest.main()
