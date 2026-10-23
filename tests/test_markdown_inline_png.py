"""The Chrome Markdown export opens without importing JSON or writing assets."""
import base64
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import time
from types import SimpleNamespace
import unittest
import zlib

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.capture_images import MAX_IMAGE_BYTES, PREFIX
from prompt_calculus_studio.markdown_rich_view import RichView
from prompt_calculus_studio.workspace_files import MarkdownPage


def inline_png(width, height, color):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    raw = (b'\0' + bytes(color) * width) * height
    image = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', width, height, 8, 6, 0, 0, 0))
             + chunk(b'IDAT', zlib.compress(raw)) + chunk(b'IEND', b''))
    return PREFIX + base64.b64encode(image).decode('ascii')


class InlinePngResourceTests(unittest.TestCase):
    def resource(self, target, alt=''):
        return RichView.resource(SimpleNamespace(), dict(target=target, path=None, alt=alt, width=None, height=None), 0)

    def test_only_bounded_valid_png_reaches_the_image_element(self):
        good = inline_png(2, 2, (18, 52, 86, 255))
        self.assertIn('<img src="' + good + '"', self.resource(good))
        self.assertIn('alt="圖片"', self.resource(good))
        corrupt = base64.b64decode(good[len(PREFIX):])
        corrupt = corrupt[:-1] + bytes([corrupt[-1] ^ 1])
        invalid = [PREFIX + base64.b64encode(corrupt).decode('ascii'),
                   PREFIX + 'not-base64', PREFIX + 'A' * (((MAX_IMAGE_BYTES + 2) // 3) * 4 + 4),
                   'data:image/svg+xml,<svg onload="alert(1)"/>', 'data:text/html,<script>alert(1)</script>']
        for target in invalid:
            with self.subTest(prefix=target[:24]):
                result = self.resource(target)
                self.assertIn('resource missing', result)
                self.assertNotIn('<img', result)
                self.assertNotIn(target, result)
                self.assertLess(len(result), 200)


class InlinePngReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-inline-png-')
        self.path = Path(self.area.name) / 'Downloaded.md'
        self.page = None

    def tearDown(self):
        if self.page:
            self.page.close()
            self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.area.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 15
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def js(self, expression):
        values = []
        self.page.md_surface.reader.rich.page().runJavaScript('JSON.stringify(' + expression + ')', values.append)
        self.wait(lambda: bool(values))
        return json.loads(values[0])

    def open(self, text):
        self.path.write_bytes(text.encode('utf-8'))
        self.page = MarkdownPage(self.path)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.page.resize(1000, 700)
        self.page.show()
        self.page.md_surface.set_mode('read')
        self.wait(lambda: self.page.md_surface.reader.rich is not None and
                  self.page.md_surface.reader.rich.render_state == 'ready')

    def test_real_markdown_export_decodes_five_images_without_changing_source_or_files(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('Node is needed to invoke the Chrome Markdown exporter')
        fixture = os.environ.get('PCS_INLINE_PNG_CAPTURE')
        if fixture:
            payload = json.loads(Path(fixture).read_text(encoding='utf-8'))
            capture = payload.get('capture', payload)
        else:
            body = '\n\n'.join(f'![Image {index}]({inline_png(80 + index, 40 + index, (index * 40, 52, 86, 255))})'
                                for index in range(5))
            capture = dict(title='Inline PNG', url='https://chatgpt.com/c/synthetic-inline-png', scope='loaded',
                           messages=[dict(role='assistant', content=body)])
        module = (Path(__file__).resolve().parents[1] / 'extensions/chrome/core.mjs').as_uri()
        script = 'import {readFileSync} from "node:fs";import {markdownDocument} from ' + json.dumps(module) + ';process.stdout.write(markdownDocument(JSON.parse(readFileSync(0,"utf8"))));'
        result = subprocess.run([node, '--input-type=module', '-e', script], input=json.dumps(capture).encode('utf-8'),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True, timeout=15)
        original = result.stdout
        self.open(original.decode('utf-8'))
        self.wait(lambda: self.js('document.querySelectorAll("main img").length') == 5)
        self.wait(lambda: self.js('[...document.querySelectorAll("main img")].every(i=>i.complete&&i.naturalWidth>0)'))
        for width in (1000, 360):
            self.page.resize(width, 700)
            QTest.qWait(80)
            state = self.js('''({sources:[...document.querySelectorAll('main img')].map(i=>i.getAttribute('src')),
                missing:document.querySelectorAll('main .missing').length,
                width:innerWidth,scroll:document.documentElement.scrollWidth})''')
            self.assertTrue(all(source.startswith(PREFIX) for source in state['sources']))
            self.assertEqual(state['missing'], 0)
            self.assertLessEqual(state['scroll'], state['width'] + 1)
            screenshots = os.environ.get('PCS_INLINE_PNG_SCREENSHOTS')
            if screenshots:
                output = Path(screenshots)
                output.mkdir(parents=True, exist_ok=True)
                self.assertTrue(self.page.md_surface.reader.rich.grab().save(str(output / f'inline-png-{width}.png')))
        self.assertFalse(self.page.md_surface.reader.rich.gate.blocked)
        for mode in ('source', 'write', 'read'):
            self.page.md_surface.set_mode(mode)
            self.app.processEvents()
            self.assertEqual(self.page.encoded(), original)
            self.assertFalse(self.page.dirty)
            self.assertFalse(self.page.buffer.text.isUndoAvailable())
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual([path.name for path in self.path.parent.iterdir()], [self.path.name])

    def test_invalid_png_is_a_short_missing_placeholder_in_the_actual_reader(self):
        text = '# Invalid PNG\n\n![](' + PREFIX + 'YmFk)\n'
        self.open(text)
        state = self.js('''({images:document.querySelectorAll('main img').length,
            missing:[...document.querySelectorAll('main .missing')].map(i=>i.textContent)})''')
        self.assertEqual(state['images'], 0)
        self.assertEqual(len(state['missing']), 1)
        self.assertIn('圖片', state['missing'][0])
        self.assertNotIn(PREFIX, state['missing'][0])
        self.assertLess(len(state['missing'][0]), 100)
        self.assertEqual(self.page.encoded(), text.encode('utf-8'))
        self.assertFalse(self.page.dirty)


if __name__ == '__main__':
    unittest.main()
