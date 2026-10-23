"""Standard source footnotes remain portable; PCS adds a local hover view."""
import json
import os
from pathlib import Path
import re
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtGui import QFontDatabase, QImage, QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.markdown_render import render
from prompt_calculus_studio.workspace_files import MarkdownPage


LABEL = 'pcs-source-0123456789abcdefabcd-1'
SECOND = 'pcs-source-fedcba9876543210abcd-1'
SOURCE = f'''# 來源與研究

Chrome 的安全更新與研究報告。[^{LABEL}]

同一組來源再次引用。[^{LABEL}] 另一份紀錄。[^{SECOND}]

一般腳註仍是一般腳註。[^ordinary]

[後續閱讀](https://example.test/after)

[^{LABEL}]: Wiz Blog +1

    - **Wiz Blog** · 2026-10-21\x20\x20
      [Security research and technical findings](https://example.test/research?item=1&mode=full)
      ![研究縮圖](thumb.png)
    - **Chromium** · 2026-10-22\x20\x20
      沒有可取得網址的文章標題

[^{SECOND}]: Chromium

    - **Chromium** · 2026-10-23\x20\x20
      [Release notes](https://example.test/releases)

[^ordinary]: 普通腳註內容。
'''


class CitationProjectionTests(unittest.TestCase):
    def test_standard_body_links_thumbnail_and_repeated_reference_render_once(self):
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'note.md'
            path.write_text(SOURCE, encoding='utf-8')
            projection = render(SOURCE, path, path.parent)
            self.assertEqual(projection.body.count('class="pcs-citation"'), 3)
            self.assertEqual(projection.body.count('class="footnote-item pcs-citation-note"'), 2)
            targets = re.findall(r'data-citation-target="([^"]+)"', projection.body)
            self.assertEqual(targets[0], targets[1])
            self.assertNotEqual(targets[0], targets[2])
            self.assertEqual(sum(link['target'].startswith('https://example.test/research') for link in projection.links), 1)
            self.assertEqual(projection.attachments[0]['target'], 'thumb.png')
            self.assertIn('沒有可取得網址的文章標題', projection.body)
            self.assertIn('footnote-backref', projection.body)
            self.assertIn('編輯腳註', projection.body)
            self.assertIn('<li value="3" id="fn3"', projection.body)
            self.assertEqual(path.read_text(encoding='utf-8'), SOURCE)

    def test_inert_label_bad_links_and_embedded_id_isolation(self):
        with tempfile.TemporaryDirectory() as area:
            path = Path(area) / 'note.md'
            child = Path(area) / 'child.md'
            child.write_text(SOURCE, encoding='utf-8')
            unsafe = SOURCE.replace('Wiz Blog +1', '&lt;img src=x onerror=alert(1)&gt;').replace(
                'https://example.test/research?item=1&mode=full', 'javascript:alert(1)')
            projection = render(unsafe + '\n![[child]]\n\n![[child]]\n', path, path.parent)
            self.assertNotIn('<img src=x', projection.body)
            self.assertFalse(any(link['target'].startswith('javascript:') for link in projection.links))
            identities = re.findall(r'<li id="([^"]+)" class="footnote-item pcs-citation-note"', projection.body)
            self.assertEqual(len(identities), 6)
            self.assertEqual(len(identities), len(set(identities)))


class CitationReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        font = Path('C:/Windows/Fonts/msjh.ttc')
        if font.exists():
            QFontDatabase.addApplicationFont(str(font))

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-citation-')
        self.path = Path(self.area.name) / 'note.md'
        self.page = None
        self.source = SOURCE
        image = QImage(240, 90, QImage.Format.Format_RGB32)
        image.fill(QColor('#476d8d'))
        image.save(str(self.path.parent / 'thumb.png'))

    def tearDown(self):
        if self.page:
            self.assertEqual(self.page.encoded(), self.source.encode('utf-8'))
            self.assertFalse(self.page.dirty)
            self.assertFalse(self.page.buffer.text.isUndoAvailable())
            self.page.close();self.page.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.area.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic() + 12
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(20)
        self.assertTrue(predicate())

    def open(self, width=960):
        self.path.write_bytes(self.source.encode('utf-8'))
        self.page = MarkdownPage(self.path)
        self.page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
        self.page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.page.resize(width, 680);self.page.show()
        self.page.md_surface.set_mode('read')
        self.wait(lambda: self.page.md_surface.reader.rich is not None and self.page.md_surface.reader.rich.render_state == 'ready')
        QTest.qWait(60)

    def js(self, script):
        result = []
        self.page.md_surface.reader.rich.page().runJavaScript('JSON.stringify((()=>{'+script+'})())', result.append)
        self.wait(lambda: bool(result))
        return json.loads(result[0])

    def hover(self, selector):
        # An offscreen WebEngine is never the native focused window. Exercise
        # the browser event path explicitly, without moving the user's mouse.
        self.js('document.querySelector('+json.dumps(selector)+').dispatchEvent(new PointerEvent("pointerover",{bubbles:true}));return true;')
        QTest.qWait(70)

    def focus(self, selector, index=0):
        self.js('const p=document.querySelectorAll('+json.dumps(selector)+')['+str(index)+'];p.focus();p.dispatchEvent(new FocusEvent("focusin",{bubbles:true}));return true;')

    def screenshot(self, name):
        folder = os.environ.get('PCS_CITATION_SCREENSHOTS')
        if folder:
            target = Path(folder);target.mkdir(parents=True, exist_ok=True)
            self.assertTrue(self.page.md_surface.reader.rich.grab().save(str(target/name)))

    def test_hover_enter_card_and_external_action_route(self):
        self.open()
        self.hover('.pcs-citation')
        self.assertTrue(self.js('return !document.querySelector(".pcs-citation-popover").hidden;'))
        self.js('document.querySelector(".pcs-citation").dispatchEvent(new PointerEvent("pointerout",{bubbles:true}));return true;')
        self.hover('.pcs-citation-popover')
        QTest.qWait(240)
        detail = self.js('const p=document.querySelector(".pcs-citation-popover");return {open:!p.hidden,text:p.innerText,images:[...p.querySelectorAll("img")].map(i=>i.complete&&i.naturalWidth>0),url:p.querySelector(".pcs-citation-url").textContent,ids:p.querySelectorAll("[id]").length};')
        self.assertTrue(detail['open'])
        self.assertIn('Chromium', detail['text']);self.assertIn('2026-10-21', detail['text'])
        self.assertEqual(detail['images'], [True]);self.assertEqual(detail['ids'], 0)
        self.assertEqual(detail['url'], 'https://example.test/research?item=1&mode=full')
        self.screenshot('citation-wide.png')
        with patch('prompt_calculus_studio.markdown_relations.QDesktopServices.openUrl', return_value=True) as launch:
            self.js('document.querySelector(".pcs-citation-popover a[href]").click();return true;')
            self.wait(lambda: launch.called)
            self.assertEqual(launch.call_args.args[0].toString(), detail['url'])
        self.js('document.querySelector(".pcs-citation-popover").dispatchEvent(new PointerEvent("pointerout",{bubbles:true}));return true;')
        self.hover('h1');QTest.qWait(240)
        self.assertTrue(self.js('return document.querySelector(".pcs-citation-popover").hidden;'))

    def test_keyboard_focus_card_tab_escape_and_regular_footnote(self):
        self.open()
        self.focus('.pcs-citation')
        self.assertTrue(self.js('return !document.querySelector(".pcs-citation-popover").hidden;'))
        self.js('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Tab",bubbles:true,cancelable:true}));return true;')
        self.assertTrue(self.js('return document.querySelector(".pcs-citation-popover").contains(document.activeElement);'))
        self.js('document.activeElement.dispatchEvent(new KeyboardEvent("keydown",{key:"Escape",bubbles:true,cancelable:true}));return true;')
        self.assertTrue(self.js('return document.querySelector(".pcs-citation-popover").hidden&&document.activeElement.matches(".pcs-citation");'))
        self.assertTrue(self.js('const n=document.querySelector(".footnote-item:not(.pcs-citation-note)");return n.getClientRects().length>0&&n.textContent.includes("普通腳註內容");'))
        self.assertEqual(self.path.read_text(encoding='utf-8'), self.source)

    def test_only_sources_hide_endnotes_and_refresh_closes_stale_card(self):
        self.source = SOURCE.split('一般腳註')[0] + SOURCE[SOURCE.index(f'[^'+LABEL+']:'):SOURCE.index('[^ordinary]:')]
        self.open()
        self.focus('.pcs-citation')
        self.assertTrue(self.js('return [...document.querySelectorAll(".pcs-citations-only")].every(e=>!e.getClientRects().length);'))
        self.assertTrue(self.js('return !document.querySelector(".pcs-citation-popover").hidden;'))
        rich = self.page.md_surface.reader.rich
        rich.refresh(self.source)
        self.wait(lambda: rich.render_state == 'ready')
        self.assertTrue(self.js('return document.querySelector(".pcs-citation-popover").hidden;'))
        self.focus('.pcs-citation')
        self.assertTrue(self.js('return !document.querySelector(".pcs-citation-popover").hidden;'))

    def test_narrow_long_card_scroll_clamps_and_only_one_card(self):
        extra = ''.join(f'    - **來源 {i}** · 2026-10-24  \n      [Article {i}](https://example.test/{i})\n' for i in range(20))
        self.source = SOURCE.replace(f'\n[^{SECOND}]:', '\n'+extra+f'\n[^{SECOND}]:')
        self.open(360)
        self.focus('.pcs-citation')
        metrics = self.js('const p=document.querySelector(".pcs-citation-popover"),r=p.getBoundingClientRect();return {x:r.left,y:r.top,right:r.right,bottom:r.bottom,w:innerWidth,h:innerHeight,scroll:p.scrollHeight,client:p.clientHeight};')
        self.assertGreaterEqual(metrics['x'], 11);self.assertGreaterEqual(metrics['y'], 11)
        self.assertLessEqual(metrics['right'], metrics['w']-11);self.assertLessEqual(metrics['bottom'], metrics['h']-11)
        self.assertGreater(metrics['scroll'], metrics['client'])
        self.screenshot('citation-narrow.png')
        self.js('const p=document.querySelector(".pcs-citation-popover");p.scrollTop=p.scrollHeight;return true;')
        self.assertGreater(self.js('return document.querySelector(".pcs-citation-popover").scrollTop;'), 0)
        self.focus('.pcs-citation', 2)
        self.assertEqual(self.js('return document.querySelectorAll(".pcs-citation-popover:not([hidden])").length;'), 1)
        self.assertIn('Release notes', self.js('return document.querySelector(".pcs-citation-popover").textContent;'))


if __name__ == '__main__':
    unittest.main()
