"""Readable Markdown tables preserve source, anchors and narrow-view scrolling."""
import json
import os
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_render import render
from prompt_calculus_studio.workspace_files import MarkdownPage


SOURCE = ('# Thermal measurements\n\n'
          '| Component | Temperature | Limit | Status |\n'
          '| --- | ---: | ---: | --- |\n'
          '| CPU | 72.5 C | 95 C | Within limit |\n'
          '| GPU | 68.2 C | 90 C | Within limit |\n\n'
          'Recorded values remain editable.\n')


class TablePresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-table-presentation-')
        self.path = Path(self.area.name) / 'Measurements.md'
        self.path.write_bytes(SOURCE.encode('utf-8'))
        self.page = None

    def tearDown(self):
        if self.page is not None:
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

    def open(self, width):
        self.page = MarkdownPage(self.path)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.page.resize(width, 680)
        self.page.show()
        self.page.md_surface.set_mode('read')
        self.wait(lambda: self.page.md_surface.reader.rich is not None and
                  self.page.md_surface.reader.rich.render_state == 'ready')
        QTest.qWait(60)

    def javascript(self, script):
        values = []
        self.page.md_surface.reader.rich.page().runJavaScript(script, values.append)
        self.wait(lambda: bool(values))
        return json.loads(values[0])

    def metrics(self):
        return self.javascript('''JSON.stringify((()=>{
          const wrap=document.querySelector('.pcs-table-scroll'), table=wrap.querySelector('table');
          const cell=table.querySelector('td'), header=table.querySelector('th'), style=getComputedStyle(cell);
          return {viewport:innerWidth, body:document.documentElement.scrollWidth,
            wrap:wrap.clientWidth, scroll:wrap.scrollWidth, table:table.getBoundingClientRect().width,
            anchor:table.id, rows:table.rows.length, text:table.innerText,
            side:[style.borderLeftWidth,style.borderRightWidth,style.borderTopWidth],
            divider:style.borderBottomWidth, header:getComputedStyle(header).textAlign,
            font:getComputedStyle(table).fontFamily, bodyFont:getComputedStyle(document.querySelector('main')).fontFamily,
            numeric:getComputedStyle(table.rows[1].cells[1]).textAlign};
        })())''')

    def screenshot(self, name):
        location = os.environ.get('PCS_TABLE_SCREENSHOTS')
        if location:
            path = Path(location)
            path.mkdir(parents=True, exist_ok=True)
            self.assertTrue(self.page.md_surface.reader.rich.grab().save(str(path / name)))

    def test_projection_keeps_table_line_anchors_and_one_scroll_container(self):
        source = SOURCE + '\n| A | B |\n| --- | --- |\n| 1 | 2 |\n'
        projection = render(source, self.path, self.path.parent)
        self.assertEqual(projection.body.count('class="pcs-table-scroll"'), 2)
        self.assertIn('<table id="line-2">', projection.body)
        self.assertIn('<table id="line-9">', projection.body)
        self.assertEqual(projection.body.count('</table></div>'), 2)
        self.assertEqual(self.path.read_bytes(), SOURCE.encode('utf-8'))

    def test_normal_width_uses_reading_measure_and_horizontal_rules_without_source_edits(self):
        self.open(1000)
        measure = self.metrics()
        self.assertGreater(measure['wrap'], 600)
        self.assertAlmostEqual(measure['table'], measure['wrap'], delta=1)
        self.assertLessEqual(measure['scroll'], measure['wrap'] + 1)
        self.assertEqual(measure['side'], ['0px', '0px', '0px'])
        self.assertEqual(measure['divider'], '1px')
        self.assertEqual(measure['header'], 'left')
        self.assertEqual(measure['font'], measure['bodyFont'])
        self.assertEqual(measure['numeric'], 'right')
        self.assertEqual(measure['anchor'], 'line-2')
        self.assertEqual(measure['rows'], 3)
        for text in ('72.5 C', '95 C', '68.2 C', '90 C'):
            self.assertIn(text, measure['text'])
        self.screenshot('table-wide.png')
        for mode in ('source', 'write', 'read'):
            self.page.md_surface.set_mode(mode)
            self.app.processEvents()
            self.assertEqual(self.page.encoded(), SOURCE.encode('utf-8'))
            self.assertFalse(self.page.dirty)
            self.assertFalse(self.page.buffer.text.isUndoAvailable())
        self.assertEqual(self.path.read_bytes(), SOURCE.encode('utf-8'))

    def test_narrow_width_scrolls_table_without_expanding_document(self):
        self.open(360)
        measure = self.metrics()
        self.assertLessEqual(measure['viewport'], 360)
        self.assertGreater(measure['scroll'], measure['wrap'])
        self.assertLessEqual(measure['body'], measure['viewport'] + 1)
        self.assertEqual(measure['rows'], 3)
        self.screenshot('table-narrow.png')
        moved = self.javascript('''JSON.stringify((()=>{
          const wrap=document.querySelector('.pcs-table-scroll');wrap.scrollLeft=wrap.scrollWidth;
          return {offset:wrap.scrollLeft,last:wrap.querySelector('tr:last-child td:last-child').innerText};
        })())''')
        self.assertGreater(moved['offset'], 0)
        self.assertEqual(moved['last'], 'Within limit')
        QTest.qWait(60)
        self.screenshot('table-narrow-scrolled.png')
        self.assertEqual(self.page.encoded(), SOURCE.encode('utf-8'))
        self.assertFalse(self.page.dirty)


if __name__ == '__main__':
    unittest.main()
