"""Exercise the final WebEngine sanitizer, not only the HTML projection."""
import json
from pathlib import Path
import tempfile
import time
import unittest

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio.workspace_files import MarkdownPage


class CompatibilityReaderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def wait(self,predicate):
        deadline=time.monotonic()+20
        while not predicate() and time.monotonic()<deadline:QTest.qWait(25)
        self.assertTrue(predicate())

    def js(self,page,code):
        values=[]
        page.md_surface.reader.rich.page().runJavaScript('JSON.stringify('+code+')',values.append)
        self.wait(lambda:bool(values))
        return json.loads(values[0])

    def test_task_checked_and_disabled_survive_sanitizer_without_trusting_raw_input(self):
        with tempfile.TemporaryDirectory(prefix='pcs-task-reader-') as folder:
            path=Path(folder)/'Tasks.md'
            forged='<input class="task-list-item-checkbox" disabled="disabled" checked="checked" type="checkbox">'
            source='# Tasks\n\n==Task states==\n\n- [ ] 未完成\n- [x] 已完成\n\n原始 HTML：'+forged+'\n'
            path.write_text(source,encoding='utf-8');original=path.read_bytes()
            page=MarkdownPage(path)
            try:
                page.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
                page.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
                page.resize(800,600);page.show();page.md_surface.set_mode('read')
                self.wait(lambda:page.md_surface.reader.rich is not None and page.md_surface.reader.rich.render_state=='ready')
                query='[...document.querySelectorAll("main input")].map(e=>({type:e.type,disabled:e.disabled,checked:e.checked}))'
                expected=[dict(type='checkbox',disabled=True,checked=False),dict(type='checkbox',disabled=True,checked=True)]
                self.assertEqual(self.js(page,query),expected)
                self.assertIn(forged,self.js(page,'document.querySelector("main").textContent'))
                self.js(page,'(()=>{document.querySelectorAll("main input").forEach(e=>e.click());return true;})()')
                self.assertEqual(self.js(page,query),expected)
                self.assertEqual(page.buffer.snapshot().text,source)
                self.assertEqual(page.encoded(),original)
                self.assertEqual(path.read_bytes(),original)
                self.assertFalse(page.dirty)
                self.assertFalse(page.buffer.text.isUndoAvailable())
            finally:
                page.close();page.deleteLater()
                self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()


if __name__=='__main__':unittest.main()
