import json
import threading
import unittest
import time
from unittest.mock import patch
from PySide6.QtCore import Qt, QPoint, QPointF, QMimeData, QUrl
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtTest import QTest
from shiboken6 import isValid
import test_workspace_panes as fixture
from test_conversation_import import sample
from test_conversation_crc import TITLES, ZIP_DEFLATED, ZIP_STORED, write_attachment_archive
from prompt_calculus_studio.conversation_dialog import open_import
from prompt_calculus_studio.conversation_archive import Cancelled
from prompt_calculus_studio.conversation_import import ImportResult


class ImportDialogTests(unittest.TestCase):
    setUpClass = classmethod(fixture.WorkspacePaneTests.setUpClass.__func__)
    setUp = fixture.WorkspacePaneTests.setUp
    open = fixture.WorkspacePaneTests.open
    events = fixture.WorkspacePaneTests.events

    def tearDown(self):
        dialog = getattr(self, 'dialog', None)
        if dialog is not None and isValid(dialog):
            dialog.reject()
            for _ in range(300):
                QTest.qWait(10)
                if not isValid(dialog) or dialog.job is None:break
            if isValid(dialog):dialog.close()
        fixture.WorkspacePaneTests.tearDown(self)

    def create(self):
        tab = self.open('Notes.md')
        self.dialog = open_import(tab.widget.md_surface)
        self.source = self.root.parent/'chat.json'
        self.source.write_text(json.dumps([sample(), sample('second', '寫作筆記')]), encoding='utf-8')
        return self.dialog

    def wait_job(self):
        for _ in range(500):
            QTest.qWait(10)
            # QTest's nested wait can retain the Python GIL on Windows; give
            # the actual Python file worker time, as app.exec does in production.
            time.sleep(.002)
            if not isValid(self.dialog) or self.dialog.job is None:return
        self.fail('background import did not finish: '+str((self.dialog.job_kind, self.dialog.status.text(), self.errors)))

    def test_preview_selection_filter_and_no_write_until_confirm(self):
        d = self.create()
        self.assertTrue(d.drop_area.isVisible())
        self.assertFalse(d.search.isVisible())
        self.assertFalse(d.split.isVisible())
        self.assertFalse(d.location_row.isVisible())
        existing = set(self.root.rglob('*'))
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile(str(self.source))])
        enter=QDragEnterEvent(QPoint(20,50),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(d,enter);self.assertTrue(enter.isAccepted())
        drop=QDropEvent(QPointF(20,50),Qt.DropAction.CopyAction,mime,Qt.MouseButton.LeftButton,Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(d,drop);self.assertTrue(drop.isAccepted())
        self.wait_job()
        self.assertFalse(d.drop_area.isVisible())
        self.assertTrue(d.search.isVisible())
        self.assertEqual(d.list.count(), 2)
        self.assertEqual(d.selected(), ())
        self.assertFalse(d.import_button.isEnabled())
        self.assertIn('共同歷史', d.preview.toPlainText())
        d.branches.setCurrentIndex(1)
        self.assertIn('回答 B', d.preview.toPlainText())
        self.assertEqual(d.selected(), ())
        second = d.list.item(1)
        QTest.mouseClick(d.list.viewport(), Qt.MouseButton.LeftButton,
                        pos=d.list.visualItemRect(second).center())
        self.assertEqual(d.selected(), ())
        d.search.setText('寫作')
        d.select_visible.click()
        self.assertEqual(d.selected(), (1,))
        d.search.clear()
        self.assertEqual(d.selected(), (1,))
        self.assertEqual(set(self.root.rglob('*')), existing)

    def test_import_opens_index_retains_dirty_tab_and_uses_bound_destination(self):
        d = self.create()
        tab = self.w.tabs_by_key[self.w.active_key]
        tab.widget.insertPlainText('dirty note ')
        before = tab.widget.toPlainText()
        d.load_source(self.source)
        self.wait_job()
        d.list.item(0).setCheckState(Qt.CheckState.Checked)
        d.branches.setCurrentIndex(1)
        self.w.new_tab()
        d.import_button.click()
        self.wait_job()
        self.assertTrue(d.completed)
        self.assertIn('完成 1 段', d.status.text())
        self.assertEqual(tab.widget.toPlainText(), before)
        self.assertIn(tab.key, self.w.tabs_by_key)
        active = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(active.path.name, '對話.md')
        self.assertEqual(active.path.parent.parent, self.root)
        self.assertEqual(len(list(active.path.parent.glob('分支-*.md'))), 2)
        self.assertEqual(d.import_button.text(), '完成')
        self.assertTrue(d.import_button.isEnabled())
        self.assertFalse(d.list.isEnabled())
        self.assertFalse(d.location_button.isEnabled())
        self.assertTrue(d.source_button.isEnabled())
        self.assertTrue(d.search.isHidden())
        self.assertTrue(d.split.widget(0).isHidden())
        self.assertTrue(d.count.isHidden())
        from prompt_calculus_studio.markdown_relations import open_link
        open_link(active.widget.md_surface, '分支-002-001.md')
        self.events()
        branch = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(branch.path.name, '分支-002-001.md')
        self.assertIn('重新生成回答 B', branch.widget.toPlainText())
        self.assertNotIn('回答 A', branch.widget.toPlainText())
        count = len(self.w.tabs_by_key)
        open_link(active.widget.md_surface, '分支-002-001.md')
        self.events()
        self.assertEqual(len(self.w.tabs_by_key), count)
        d.load_source(self.source)
        self.wait_job()
        self.assertFalse(d.completed)
        self.assertTrue(d.list.isEnabled())
        self.assertFalse(d.search.isHidden())
        self.assertFalse(d.split.widget(0).isHidden())
        self.assertEqual(d.selected(), ())
        self.assertFalse(self.errors, self.errors)

    def test_late_attachment_crc_reaches_partial_summary_through_import_job(self):
        self.assert_attachment_error_summary(ZIP_STORED, 'CRC')

    def test_invalid_deflate_reaches_partial_summary_through_import_job(self):
        self.assert_attachment_error_summary(ZIP_DEFLATED, 'invalid block type')

    def assert_attachment_error_summary(self, compression, error_text):
        d = self.create()
        self.source = self.root.parent/'crc-export.zip'
        original = write_attachment_archive(self.source, compression=compression)
        d.load_source(self.source)
        self.wait_job()
        d.check_visible(True)
        d.submit()
        self.wait_job()
        self.assertTrue(d.completed)
        self.assertEqual(d.status.text(), '完成 1 段 · 略過 0 段 · 未完成 1 段 · 未處理 1 段')
        self.assertEqual(d.import_button.text(), '關閉')
        for title in TITLES:
            self.assertIn(title, d.preview.toPlainText())
        self.assertIn(error_text, d.preview.toPlainText())
        receipts = list(self.root.glob('*/匯入完成.json'))
        self.assertEqual(len(receipts), 1)
        active = self.w.tabs_by_key[self.w.active_key]
        self.assertEqual(active.path, receipts[0].parent/'對話.md')
        self.assertEqual(self.source.read_bytes(), original)
        self.assertFalse(self.errors, self.errors)

    def test_close_during_background_read_cancels_without_stale_ui(self):
        d = self.create()
        entered = threading.Event()
        def delayed(path, cancel, progress):
            entered.set()
            cancel.wait(2)
            raise Cancelled()
        with patch('prompt_calculus_studio.conversation_dialog.load_archive', delayed):
            d.load_source(self.source)
            for _ in range(100):
                if entered.is_set():break
                QTest.qWait(10)
            d.reject()
            self.assertTrue(d.job.cancel.is_set())
            self.wait_job()
        self.events()
        self.assertFalse(isValid(d) and d.isVisible())
        self.assertFalse(list(self.root.glob('*/匯入開始.json')))
        self.assertFalse(self.errors, self.errors)
        self.dialog = open_import(self.w.tabs_by_key[self.w.active_key].widget.md_surface)
        self.assertIsNot(self.dialog, d)

    def test_narrow_layout_and_safe_offline_preview(self):
        d = self.create()
        d.resize(600, 760)
        self.events()
        self.assertEqual(d.split.orientation(), Qt.Orientation.Vertical)
        d.resize(960, 720)
        self.events()
        self.assertEqual(d.split.orientation(), Qt.Orientation.Horizontal)
        self.assertIsNone(d.preview.loadResource(2, 'https://example.invalid/private'))
        self.assertFalse(d.preview.openExternalLinks())
        self.assertFalse(d.preview.openLinks())
        self.assertEqual(d.windowModality(), Qt.WindowModality.NonModal)
        self.assertFalse(self.errors, self.errors)

    def test_workspace_close_cancels_pending_read(self):
        d = self.create()
        entered = threading.Event()
        def delayed(path, cancel, progress):
            entered.set()
            cancel.wait(3)
            raise Cancelled()
        with patch('prompt_calculus_studio.conversation_dialog.load_archive', delayed):
            d.load_source(self.source)
            for _ in range(100):
                QTest.qWait(5)
                time.sleep(.002)
                if entered.is_set():break
            flag = d.job.cancel
            self.w.close()
            self.assertTrue(flag.is_set())
            self.wait_job()
        self.assertFalse(self.errors, self.errors)

    def test_cancel_without_writes_does_not_claim_saved_results(self):
        d = self.create()
        d.job_finished(ImportResult(cancelled=True, unprocessed=['尚未匯入']), '')
        self.assertIn('完成 0 段', d.status.text())
        self.assertIn('未處理 1 段', d.status.text())
        self.assertEqual(d.import_button.text(), '關閉')
        self.assertFalse(d.list.isEnabled())
        self.assertFalse(self.errors, self.errors)
