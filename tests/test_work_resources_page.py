"""Exercise the single-list workflow with actual Qt controls and isolated local files."""
import gc
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt, QEvent, QTimer, QPointF
from PySide6.QtGui import QTextCursor, QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog, QLineEdit, QDialogButtonBox
from prompt_calculus_studio.conversation_archive import canonical
from prompt_calculus_studio.workspace_shell import WorkspaceShell
from prompt_calculus_studio.work_resources import bundles
from prompt_calculus_studio.widgets import StudioDialog
from test_work_resources import example


@unittest.skipUnless(os.name == 'nt', 'Windows sources')
class ResourceWindowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def wait(self, condition):
        end = time.monotonic() + 25
        while time.monotonic() < end:
            self.app.processEvents()
            if self.errors:
                self.fail(str(self.errors))
            if condition():
                return
            time.sleep(.012)
        self.fail(f'操作逾時 · {self.page.status.text()}')

    def idle(self):
        p = self.page
        self.wait(lambda: not p.jobs.active and not p.search_timer.isActive() and not p.search_pending)

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.docs = self.base/'研究手記'; self.docs.mkdir()
        self.root = self.base/'工作區'; self.root.mkdir()
        (self.docs/'河岸.md').write_bytes('原文🙂\r\n選這一段\r\n其餘。'.encode())
        (self.docs/'時間.md').write_text('明天九點集合。', encoding='utf8')
        self.archive = self.base/'討論封存.json'
        self.archive.write_bytes(canonical([example()]))
        self.errors = []
        original_init = StudioDialog.__init__
        def quiet_dialog(dialog, *args, **kwargs):
            original_init(dialog, *args, **kwargs)
            dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
            dialog._prompt_instant_show = True
        self.dialog_patch = patch.object(StudioDialog, '__init__', quiet_dialog)
        self.dialog_patch.start()
        self.old_hook = sys.excepthook
        import traceback
        sys.excepthook = lambda t,v,b: self.errors.append(''.join(traceback.format_exception(t,v,b)))
        self.open_shell()

    def open_shell(self):
        self.w = WorkspaceShell(self.root, self.base/'data')
        self.w.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.w.resize(1240, 790)
        self.w.show()
        self.w.report_error = self.errors.append
        self.w.open_route('explore', 'work_resources')
        self.page = self.w.utility.settings_page.resources_content.load()
        self.page.host.error = self.errors.append
        self.idle()

    def close_shell(self):
        self.page.close_reader()
        self.idle()
        self.w.close(); self.w.deleteLater()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents(); gc.collect()

    def tearDown(self):
        try:
            self.close_shell()
        finally:
            sys.excepthook = self.old_hook
            self.dialog_patch.stop()
            self.tmp.cleanup()

    def click(self, widget):
        QTest.mouseClick(widget, Qt.MouseButton.LeftButton)
        self.app.processEvents()

    def action(self, control, name):
        self.click(control)
        popup = QApplication.activePopupWidget()
        self.assertIsNotNone(popup)
        action = next(a for a in popup.actions() if a.text() == name)
        QTest.mouseClick(popup, Qt.MouseButton.LeftButton, pos=popup.actionGeometry(action).center())
        self.app.processEvents()

    def hit(self, title):
        self.idle()
        tree = self.page.results
        item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                    if title in tree.topLevelItem(i).text(1))
        tree.setCurrentItem(item)
        QTest.keyClick(tree, Qt.Key.Key_Return)
        self.idle()

    def picture(self, name, widget=None):
        value = os.environ.get('PCS_RESOURCE_QA_SCREENSHOTS')
        if value:
            QTest.qWait(160)
            path = Path(value); path.mkdir(parents=True, exist_ok=True)
            self.assertTrue((widget or self.w).grab().save(str(path/(name+'.png'))))

    def check(self, title):
        tree = self.page.results
        item = next(tree.topLevelItem(i) for i in range(tree.topLevelItemCount())
                    if title in tree.topLevelItem(i).text(1))
        tree.scrollToItem(item)
        self.app.processEvents()
        index = tree.indexFromItem(item)
        rect = tree.check_rect(index)
        self.assertFalse(rect.isEmpty())
        # Native QTest.mouseMove uses the global cursor. Send only to this test viewport instead.
        self.app.sendEvent(tree.viewport(), QMouseEvent(QEvent.Type.MouseMove, QPointF(rect.center()),
            QPointF(tree.viewport().mapToGlobal(rect.center())), Qt.MouseButton.NoButton,
            Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier))
        self.assertEqual(tree.hover_row, index.row())
        QTest.mouseClick(tree.viewport(), Qt.MouseButton.LeftButton, pos=rect.center())
        self.app.processEvents()

    def save_named(self, name):
        def fill():
            dialog = QApplication.activeModalWidget()
            self.assertIsNotNone(dialog)
            dialog.findChild(QLineEdit).setText(name)
            box = dialog.findChild(QDialogButtonBox)
            self.click(box.button(QDialogButtonBox.StandardButton.Save))
        QTimer.singleShot(40, fill)
        self.click(self.page.save)
        self.idle()

    def test_check_partial_branch_save_restart_and_current_source(self):
        p = self.page
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(self.docs)):
            self.action(p.add_button, '加入資料夾')
        self.idle()
        self.assertEqual(p.results.topLevelItemCount(), 2)
        self.assertFalse(hasattr(p, 'tabs'))
        self.hit('河岸.md')
        reader = p.reader
        self.assertIn('原文🙂', reader.preview.toPlainText())
        cursor = reader.preview.textCursor()
        cursor.setPosition(5); cursor.setPosition(9, QTextCursor.MoveMode.KeepAnchor)
        reader.preview.setTextCursor(cursor)
        self.click(reader.use)
        self.assertEqual(next(iter(p.picks.values()))['spans'], [(4, 8)])
        QTest.keyClick(reader, Qt.Key.Key_Escape)
        self.assertFalse(reader.isVisible())
        self.check('時間.md')
        self.assertEqual(len(p.picks), 2)
        self.check('時間.md')
        self.assertEqual(len(p.picks), 1)
        self.check('時間.md')
        self.assertEqual(len(p.picks), 2)
        with patch.object(QFileDialog, 'getOpenFileName', return_value=(str(self.archive), '')):
            self.action(p.add_button, '匯入對話封存')
        self.idle()
        p.filter.setText('東岸'); self.idle()
        self.assertEqual(p.results.topLevelItemCount(), 1)
        self.hit('河岸會議'); reader = p.reader
        self.assertIn('選擇東岸', reader.preview.toPlainText())
        self.assertNotIn('m-east', reader.preview.toPlainText())
        self.assertNotIn('排除西岸', reader.preview.toPlainText())
        self.assertEqual(reader.branches.count(), 2)
        self.click(reader.use)
        self.picture('02-閱讀與搜尋', reader)
        reader.close()
        self.click(p.back_button)
        self.idle()
        self.assertEqual(p.results.topLevelItemCount(), 3)
        self.picture('01-勾選清單')
        self.save_named('河岸研究 · 第一輪')
        self.assertEqual(len(p.picks), 0)
        self.assertEqual(p.current_kind, 'bundle')
        self.hit('河岸研究')
        self.assertEqual(p.results.topLevelItemCount(), 3)
        self.hit('河岸.md')
        self.assertEqual(p.reader.preview.toPlainText(), '選這一段')
        p.reader.close()
        (self.docs/'河岸.md').write_text('更新後內容', encoding='utf8')
        self.close_shell(); self.open_shell(); p = self.page
        self.click(p.kind_buttons['bundle']); self.idle()
        self.hit('河岸研究'); self.hit('河岸.md')
        self.assertEqual(p.reader.preview.toPlainText(), '選這一段')
        self.click(p.reader.use); self.idle()
        self.assertEqual(p.reader.message.text(), '來源已變更')
        self.picture('03-保存內容查回', p.reader)

    def test_paging_keeps_selection_and_saved_groups_are_searchable(self):
        for index in range(123):
            (self.docs/f'研究-{index:03}.md').write_text('研究資料內容', encoding='utf8')
        p = self.page
        p.add_source(self.docs, 'folder'); self.idle()
        self.assertEqual((p.total, p.results.topLevelItemCount()), (125, 50))
        p.results.setFocus()
        QTest.keyClick(p.results, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
        QTest.keyClick(p.results, Qt.Key.Key_Space)
        self.assertEqual(len(p.picks), 50)
        self.click(p.next); self.idle()
        self.check('研究-080')
        p.filter.setText('研究-120'); self.idle()
        self.check('研究-120')
        p.filter.clear(); self.idle()
        self.assertEqual(len(p.picks), 52)
        self.assertTrue(all(p.results.topLevelItem(i).checkState(0) == Qt.CheckState.Checked
                            for i in range(p.results.topLevelItemCount())))
        self.assertFalse(hasattr(p, 'check_page'))
        self.assertFalse(hasattr(p, 'only_selected'))
        self.picture('04-大量檔案')
        self.save_named('跨頁研究')
        self.hit('跨頁研究')
        self.assertEqual(p.total, 52)
        self.assertEqual(p.results.topLevelItemCount(), 50)
        self.click(p.next); self.idle()
        self.assertEqual(p.results.topLevelItemCount(), 2)
        self.click(p.back_button); self.idle()
        row = p.store.browse(kind='document')[0][0]
        selection = bundles.select_document(p.store, row)
        for index in range(101):
            bundles.save(p.store, [selection], f'研究組合 {index:03}')
        self.click(p.refresh); self.idle()
        self.assertEqual((p.total, p.results.topLevelItemCount()), (102, 50))
        with patch.object(bundles, 'saved_rows', side_effect=AssertionError('重複讀取已快取的組合')):
            p.filter.setText('研究組合 100'); self.idle()
            self.assertEqual(p.total, 1)
            p.filter.clear(); self.idle()
            self.click(p.next); self.idle()
        self.picture('05-大量組合')

    def test_reader_full_text_search_copy_editor_undo_and_narrow_layout(self):
        p = self.page
        (self.docs/'長文.md').write_text('段落\n'*16000 + '界線測試🙂ABC' + '尾段\n'*20000, encoding='utf8')
        p.add_source(self.docs, 'folder'); self.idle()
        self.hit('長文.md'); reader = p.reader
        reader.filter.setText('界線測試🙂ABC')
        QTest.keyClick(reader.filter, Qt.Key.Key_Return)
        self.assertEqual(reader.preview.textCursor().selectedText(), '界線測試🙂ABC')
        self.assertLess(len(reader.preview.toPlainText()), 60100)
        self.click(reader.use)
        self.assertTrue(next(iter(p.picks.values()))['spans'])
        reader.close()
        self.hit('時間.md')
        self.action(p.reader.actions, '開啟原文分頁'); self.idle()
        tab = self.w.tabs_by_key[self.w.active_key]
        self.assertTrue(tab.widget.isReadOnly())
        self.assertEqual(tab.widget.toPlainText(), '明天九點集合。')
        self.w.open_route('explore', 'work_resources')
        self.hit('時間.md')
        self.action(p.reader.actions, '複製到工作區'); self.idle()
        tab = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(tab.path.parent, self.root)
        tab.widget.md_surface.set_mode('source')
        tab.widget.moveCursor(QTextCursor.MoveOperation.End)
        tab.widget.insertPlainText('改期')
        self.assertIn('改期', tab.widget.buffer.snapshot().text)
        tab.widget.undo()
        self.assertNotIn('改期', tab.widget.buffer.snapshot().text)
        tab.widget.insertPlainText('新版')
        self.assertTrue(tab.widget.save(), tab.widget.error)
        self.assertEqual((self.docs/'時間.md').read_text(encoding='utf8'), '明天九點集合。')
        self.w.open_route('explore', 'work_resources')
        self.w.resize(620, 690); self.app.processEvents()
        self.assertTrue(p.results.isColumnHidden(2))
        self.picture('06-窄窗清單')
        self.hit('河岸.md')
        self.picture('07-獨立閱讀視窗', p.reader)
