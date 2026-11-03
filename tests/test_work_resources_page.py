"""Drive the actual Explore surface and the standard Markdown editor on synthetic files."""
import gc
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QEvent
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog
from prompt_calculus_studio.conversation_archive import canonical
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from test_work_resources import example


@unittest.skipUnless(os.name=='nt','Windows sources')
class ResourceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QApplication.instance() or QApplication([])

    def wait(self,condition):
        end=time.monotonic()+20
        while time.monotonic()<end:
            self.app.processEvents()
            if self.errors: self.fail(str(self.errors))
            if condition():return
            time.sleep(0.012)
        self.fail(f'操作逾時 · {self.page.status.text()} · 欄寬 {self.page.width()}')

    def idle(self):
        p=self.page
        self.wait(lambda:not p.jobs.active and not p.search_timer.isActive() and
                  not (p.search_pending and p.tabs.currentIndex()==0))

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.base=Path(self.tmp.name); self.docs=self.base/'研究手記';self.docs.mkdir()
        self.root=self.base/'工作區';self.root.mkdir()
        (self.docs/'河岸.md').write_bytes('原文🙂\r\n選這一段\r\n其餘。'.encode())
        (self.docs/'時間.md').write_text('明天九點集合。',encoding='utf8')
        self.archive=self.base/'討論封存.json';self.archive.write_bytes(canonical([example()]))
        self.errors=[]
        self.old_hook=sys.excepthook
        import traceback
        sys.excepthook=lambda t,v,b:self.errors.append(''.join(traceback.format_exception(t,v,b)))
        self.open_shell()

    def open_shell(self):
        self.w=WorkspaceShell(self.root,self.base/'data')
        self.w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating,True)
        self.w.resize(1240,790)
        self.w.show()
        self.w.report_error=self.errors.append
        self.w.open_route('explore','work_resources')
        self.page=self.w.utility.settings_page.resources_content.load()
        self.idle()

    def close_shell(self):
        self.idle()
        self.w.close();self.w.deleteLater()
        QApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
        self.app.processEvents();gc.collect()

    def tearDown(self):
        try:self.close_shell()
        finally:
            sys.excepthook=self.old_hook
            self.tmp.cleanup()

    def click(self,widget):
        QTest.mouseClick(widget,Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def action(self,control,name):
        self.click(control)
        popup=QApplication.activePopupWidget()
        self.assertIsNotNone(popup)
        action=next(a for a in popup.actions() if a.text()==name)
        QTest.mouseClick(popup,Qt.MouseButton.LeftButton,pos=popup.actionGeometry(action).center())
        self.app.processEvents()

    def tab(self,i):
        bar=self.page.tabs.tabBar()
        QTest.mouseClick(bar,Qt.MouseButton.LeftButton,pos=bar.tabRect(i).center())
        self.idle()

    def hit(self,title):
        p=self.page
        self.idle()
        item=next(p.results.item(i) for i in range(p.results.count()) if title in p.results.item(i).text())
        p.results.setCurrentItem(item)
        QTest.keyClick(p.results,Qt.Key.Key_Return)
        self.idle()

    def picture(self,name):
        value=os.environ.get('PCS_RESOURCE_QA_SCREENSHOTS')
        if value:
            path=Path(value);path.mkdir(parents=True,exist_ok=True)
            self.assertTrue(self.w.grab().save(str(path/(name+'.png'))))

    def test_native_source_selection_archive_save_restart(self):
        p=self.page
        self.assertIn('work_resources',[r[0] for r in self.w.utility.settings_page.nav_routes])
        with patch.object(QFileDialog,'getExistingDirectory',return_value=str(self.docs)):
            self.action(p.add_button,'引用資料夾')
        self.idle();self.assertEqual(p.results.count(),2)
        self.hit('河岸.md')
        self.assertIn('原文🙂',p.preview.toPlainText())
        cur=p.preview.textCursor()
        cur.setPosition(5);cur.setPosition(9,QTextCursor.MoveMode.KeepAnchor)
        p.preview.setTextCursor(cur)
        self.click(p.add);self.idle()
        self.assertEqual(p.selections[0].content,'選這一段'.encode())
        self.hit('時間.md');self.click(p.add);self.idle()
        self.picture('01-來源與預覽')
        with patch.object(QFileDialog,'getOpenFileName',return_value=(str(self.archive),'')):
            self.action(p.add_button,'匯入對話封存')
        self.idle();p.filter.setText('東岸');self.idle()
        self.hit('河岸會議')
        self.assertIn('選擇東岸',p.preview.toPlainText());self.assertNotIn('排除西岸',p.preview.toPlainText())
        self.assertEqual(p.branches.count(),2)
        self.click(p.add);self.idle()
        self.tab(1);p.pack_name.setText('河岸研究 · 第一輪')
        self.assertEqual(p.draft_list.count(),3)
        self.picture('02-資料包範圍')
        self.click(p.save);self.wait(lambda:p.saved_record is not None);self.idle()
        pack_id=p.library.currentData()
        self.assertEqual(len(p.saved_record[0]['selections']),3)
        (self.docs/'河岸.md').write_text('更新後內容',encoding='utf8')
        self.close_shell();self.open_shell();p=self.page;self.tab(2)
        self.wait(lambda:p.saved_record is not None)
        self.assertEqual(p.library.currentData(),pack_id)
        self.assertIn('選這一段',p.saved_detail.toPlainText())
        self.click(p.compare);self.idle()
        self.assertEqual(p.status.text(),'來源已變更')
        self.picture('03-保存後查回')

    def test_copy_editor_undo_readonly_original_and_narrow_back(self):
        p=self.page;p.add_source(self.docs,'folder');self.idle();self.hit('時間.md')
        self.action(p.actions,'開啟原文分頁');self.idle()
        tab=self.w.tabs_by_key[self.w.active_key]
        self.assertTrue(tab.widget.isReadOnly())
        self.assertEqual(tab.widget.toPlainText(),'明天九點集合。')
        self.w.open_route('explore','work_resources')
        self.action(p.actions,'複製到工作區');self.idle()
        tab=self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(tab.path.parent,self.root)
        tab.widget.md_surface.set_mode('source')
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        tab.widget.insertPlainText('改期')
        self.assertIn('改期',tab.widget.buffer.snapshot().text)
        tab.widget.undo();self.assertNotIn('改期',tab.widget.buffer.snapshot().text)
        tab.widget.insertPlainText('新版')
        self.assertTrue(tab.widget.save(),tab.widget.error)
        self.assertIn('新版',tab.path.read_text(encoding='utf8'))
        self.assertEqual((self.docs/'時間.md').read_text(encoding='utf8'),'明天九點集合。')
        self.w.open_route('explore','work_resources')
        self.w.resize(620,690);self.app.processEvents()
        self.assertTrue(p.narrow, f'頁面 {p.width()}，視窗 {self.w.width()}')
        p.sources.setCurrentIndex(0);self.idle();self.hit('河岸.md')
        self.assertTrue(p.go_back.isVisible());self.assertFalse(p.list_pane.isVisible())
        self.picture('04-窄窗原文')
        self.click(p.go_back)
        self.assertTrue(p.list_pane.isVisible());self.assertFalse(p.detail.isVisible())
