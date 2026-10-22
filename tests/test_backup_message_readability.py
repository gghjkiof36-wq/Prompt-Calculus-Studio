"""Recovery messages remain usable through the real backup/Jobs UI boundary."""
import os
import json
import tempfile
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton, QWidget

from prompt_calculus_studio import backup
from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.widgets import InformationDialog
from prompt_calculus_studio.window import Window


APP = QApplication.instance() or QApplication([])
# The isolated Windows offscreen runtime does not discover the system fonts.
# Load local OS faces before screenshots; this changes only this test process.
FONT_RECEIPTS = []
for name in ('segoeui.ttf', 'msjh.ttc', 'seguiemj.ttf'):
    font_path = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / name
    if font_path.is_file():
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        FONT_RECEIPTS.append(dict(path=str(font_path), loaded=font_id >= 0,
                                  families=QFontDatabase.applicationFontFamilies(font_id) if font_id >= 0 else []))
if any('Microsoft JhengHei' in family for item in FONT_RECEIPTS for family in item['families']):
    APP.setFont(QFont('Microsoft JhengHei', 10))


def events():
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    APP.processEvents()


def screenshot(widget, name):
    directory = os.environ.get('PCS_BACKUP_UI_EVIDENCE')
    if directory:
        target = Path(directory)
        target.mkdir(parents=True, exist_ok=True)
        (target / 'fonts.json').write_text(json.dumps(FONT_RECEIPTS, ensure_ascii=False, indent=2), encoding='utf-8')
        if not widget.grab().save(str(target / (name + '.png'))):
            raise AssertionError('Could not save UI evidence: ' + name)


class InformationReadabilityTests(unittest.TestCase):
    def test_long_plain_message_wraps_scrolls_and_copies_at_small_and_large_text_sizes(self):
        path = 'D:/備份 😀/專案資料/' + 'long-unbroken-folder-name-' * 8
        message = '備份發布未能確認完成。\n保留的恢復檔候選：\n' + '\n'.join(
            path + '/.pcs-backup-' + 'a' * 32 + suffix
            for suffix in ('.partial', '.before', '.external')) + '\n原因：最後一行 <原文> & 完整保留。'
        for point_size in (12, 24):
            with self.subTest(point_size=point_size):
                parent = QWidget()
                parent.resize(640, 480)
                parent.show()
                dialog = InformationDialog(parent, '未能完成', message)
                dialog._prompt_instant_show = True
                dialog.message.setFont(QFont(APP.font().family(), point_size))
                dialog.resize(480, 320)
                try:
                    dialog.show()
                    QTest.qWait(20)
                    self.assertEqual(dialog.message.toPlainText(), message)
                    self.assertTrue(dialog.message.isReadOnly())
                    self.assertEqual(dialog.message.horizontalScrollBar().maximum(), 0)
                    self.assertGreater(dialog.message.verticalScrollBar().maximum(), 0)
                    self.assertTrue(dialog.rect().contains(dialog.confirm.geometry().translated(
                        dialog.confirm.mapTo(dialog, dialog.confirm.rect().topLeft()) - dialog.confirm.pos())))
                    screenshot(dialog, f'long-message-{point_size}-top')
                    dialog.message.setFocus()
                    QTest.keyClick(dialog.message, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
                    self.assertEqual(dialog.message.textCursor().position(),
                                     dialog.message.document().characterCount() - 1)
                    self.assertTrue(dialog.message.viewport().rect().intersects(dialog.message.cursorRect()))
                    self.assertGreater(dialog.message.verticalScrollBar().value(), 0)
                    screenshot(dialog, f'long-message-{point_size}-bottom')
                    QTest.keyClick(dialog.message, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier)
                    QTest.keyClick(dialog.message, Qt.Key.Key_C, Qt.KeyboardModifier.ControlModifier)
                    self.assertEqual(APP.clipboard().text(), message)
                    APP.clipboard().setText('replace me')
                    QTest.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
                    self.assertEqual(APP.clipboard().text(), message)
                    dialog.message.setFocus()
                    QTest.keyClick(dialog.message, Qt.Key.Key_Tab)
                    self.assertIs(APP.focusWidget(), dialog.copy_button)
                    QTest.keyClick(dialog, Qt.Key.Key_Escape)
                    self.assertFalse(dialog.isVisible())
                finally:
                    dialog.close()
                    dialog.deleteLater()
                    parent.close()
                    parent.deleteLater()
                    events()

    def test_short_message_keeps_acknowledge_action_and_literal_text(self):
        dialog = InformationDialog(None, '找不到原檔', '原檔 <圖片> & 😀 可能已搬動。')
        dialog._prompt_instant_show = True
        try:
            dialog.show()
            QTest.qWait(10)
            self.assertEqual(dialog.confirm.text(), '知道了')
            self.assertIsNone(dialog.copy_button)
            self.assertEqual(dialog.message.toPlainText(), '原檔 <圖片> & 😀 可能已搬動。')
            self.assertEqual(dialog.message.verticalScrollBar().maximum(), 0)
            screenshot(dialog, 'short-message')
            QTest.keyClick(dialog.confirm, Qt.Key.Key_Return)
            self.assertFalse(dialog.isVisible())
        finally:
            dialog.close()
            dialog.deleteLater()
            events()


class BackupMessageFlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        data = self.root / 'data'
        state = initial_state()
        state['settings']['online'] = False
        store = Storage(data)
        store.save(state)
        store.close()
        self.w = Window(data)
        self.w.completion.timer.stop()
        self.w.start_interface()
        self.w.resize(640, 480)
        self.w.show()
        QTest.qWait(20)
        folder = self.root / ('備份目的地-😀-' + 'long-name-' * 4)
        folder.mkdir()
        self.destination = folder / '備份.zip'
        with zipfile.ZipFile(self.destination, 'w') as archive:
            archive.writestr('healthy.txt', b'previous healthy backup')
        self.before = self.destination.read_bytes()
        self.dialog_texts, self.dialog_errors = [], []
        self.dialog_timer = QTimer()

    def tearDown(self):
        self.dialog_timer.stop()
        self.assertIsNone(self.w.jobs.active)
        self.assertTrue(self.w.jobs.pool.waitForDone(1000))
        self.w.completion.timer.stop()
        owner = getattr(self, 'shell', self.w)
        owner.close()
        owner.deleteLater()
        events()
        self.tmp.cleanup()

    def invoke(self, operation, after_start=None, start=None):
        with (patch('prompt_calculus_studio.window.QFileDialog.getSaveFileName',
                    return_value=(str(self.destination), 'ZIP (*.zip)')),
              patch.object(backup, 'archive_data', side_effect=operation)):
            (start or self.w.backup_zip)()
            if after_start is not None:
                after_start()
            deadline = time.monotonic() + 10
            while self.w.jobs.active and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(self.w.jobs.active, 'backup worker did not finish')
        self.assertTrue(self.w.jobs.pool.waitForDone(1000))
        self.assertEqual(self.dialog_errors, [])

    def settings_backup_button(self, *, embedded=False):
        if embedded:
            from prompt_calculus_studio.workspace_shell import WorkspaceShell
            self.w.close()
            self.w.deleteLater()
            events()
            files = self.root / 'workspace'
            files.mkdir()
            self.shell = WorkspaceShell(files, self.root / 'workspace-data')
            self.shell.resize(960, 640)
            self.shell.show()
            self.shell.open_route('settings', 'data')
            self.w = self.shell.utility
            self.w.completion.timer.stop()
        else:
            self.w.settings('data')
        QTest.qWait(20)
        buttons = [entry for entry in self.w.settings_page.findChildren(QPushButton)
                   if entry.text() == '備份 ZIP…']
        self.assertEqual(len(buttons), 1)
        self.assertTrue(buttons[0].isVisible())
        self.assertFalse(self.w.status.isVisible())
        self.assertFalse(self.w.canvas_status.isVisible())
        screenshot(getattr(self, 'shell', self.w),
                   'backup-embedded-settings-entry' if embedded else 'backup-settings-entry')
        return buttons[0]

    def watch_message(self, name):
        """Inspect a real modal while its normal exec loop is running."""
        deadline = time.monotonic() + 10
        def inspect():
            dialog = APP.activeModalWidget()
            if not isinstance(dialog, InformationDialog):
                if time.monotonic() >= deadline:
                    self.dialog_timer.stop()
                    self.dialog_errors.append('Expected information dialog did not appear')
                return
            self.dialog_timer.stop()
            try:
                message = dialog.message.toPlainText()
                self.dialog_texts.append(message)
                self.assertIsNotNone(dialog.copy_button)
                self.assertEqual(dialog.message.horizontalScrollBar().maximum(), 0)
                screenshot(dialog, name + '-top')
                dialog.message.setFocus()
                QTest.keyClick(dialog.message, Qt.Key.Key_End, Qt.KeyboardModifier.ControlModifier)
                self.assertTrue(dialog.message.viewport().rect().intersects(dialog.message.cursorRect()))
                screenshot(dialog, name + '-bottom')
                QTest.mouseClick(dialog.copy_button, Qt.MouseButton.LeftButton)
                self.assertEqual(APP.clipboard().text(), message)
            except Exception as error:
                self.dialog_errors.append(repr(error))
            finally:
                dialog.accept()
        self.dialog_timer.timeout.connect(inspect)
        self.dialog_timer.start(20)

    def open_status_details(self, name):
        status = self.w.canvas_status
        self.assertTrue(status.isVisible())
        self.assertFalse(status.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(status.textFormat(), Qt.TextFormat.RichText)
        self.assertIn('backup-details', status.text())
        self.assertEqual(status.toolTip(), '查看完整備份訊息')
        screenshot(self.w, name + '-notice')
        self.watch_message(name)
        status.setFocus(Qt.FocusReason.TabFocusReason)
        QTest.keyClick(status, Qt.Key.Key_Return)
        self.assertEqual(self.dialog_errors, [])
        self.assertEqual(len(self.dialog_texts), 1, 'keyboard did not open the details action')

    def test_cleanup_warning_has_keyboard_details_and_complete_recovery_paths(self):
        def refuse_cleanup(stream):
            raise PermissionError('controlled recovery cleanup refusal')
        with patch.object(backup, '_delete_stream', side_effect=refuse_cleanup):
            self.invoke(backup.archive_data)
        self.open_status_details('backup-cleanup')
        message = self.dialog_texts[0]
        self.assertIn('ZIP 備份完成', message)
        self.assertIn('恢復檔未能清理', message)
        leftovers = list(self.destination.parent.glob('.pcs-backup-*'))
        self.assertEqual({path.suffix for path in leftovers}, {'.before', '.external'})
        for path in leftovers:
            self.assertIn(str(path), message)
            self.assertEqual(path.read_bytes(), self.before)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIn('data/studio.sqlite3', archive.namelist())
            self.assertIsNone(archive.testzip())
        self.w.notice('後續短訊息')
        self.assertEqual(self.w.canvas_status.textFormat(), Qt.TextFormat.PlainText)
        self.assertTrue(self.w.canvas_status.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
        self.assertEqual(self.w.canvas_status.text(), '後續短訊息')
        self.assertIsNone(self.w._backup_notice_text)

    def test_public_cancel_keeps_short_notice_and_original_zip(self):
        archive_data = backup.archive_data
        def cancelled(directory, snapshot, destination, cancel):
            self.assertTrue(cancel.wait(2), 'public cancel did not reach the worker')
            return archive_data(directory, snapshot, destination, cancel)
        self.invoke(cancelled, self.w.cancel_jobs)
        self.assertEqual(self.destination.read_bytes(), self.before)
        self.assertEqual(self.w.canvas_status.text(), '已取消備份；既有 ZIP 未變更。')
        self.assertEqual(self.w.canvas_status.textFormat(), Qt.TextFormat.PlainText)
        self.assertEqual(list(self.destination.parent.glob('.pcs-backup-*')), [])
        screenshot(self.w, 'backup-cancel')

    def test_cancel_cleanup_failure_exposes_unpublished_temporary_path(self):
        archive_data, write_archive = backup.archive_data, backup._write_archive
        def operation(directory, snapshot, destination, cancel):
            def write_then_cancel(*args):
                write_archive(*args)
                cancel.set()
            with (patch.object(backup, '_write_archive', side_effect=write_then_cancel),
                  patch.object(backup, '_delete_stream', side_effect=PermissionError('controlled cleanup refusal'))):
                return archive_data(directory, snapshot, destination, cancel)
        self.invoke(operation)
        self.open_status_details('backup-cancel-cleanup')
        message = self.dialog_texts[0]
        self.assertIn('未發布的暫存檔（不可當作完成備份）', message)
        self.assertNotIn('ZIP 備份完成', message)
        self.assertEqual(self.destination.read_bytes(), self.before)
        leftovers = list(self.destination.parent.glob('.pcs-backup-*'))
        self.assertEqual(len(leftovers), 1)
        self.assertEqual(leftovers[0].suffix, '.partial')
        self.assertIn(str(leftovers[0]), message)

    def test_gap_collision_error_shows_all_real_recovery_paths_and_final_reason(self):
        archive_data, native_rename = backup.archive_data, backup._rename_owned
        foreign = b'foreign arrival must not be overwritten'
        def operation(directory, snapshot, destination, cancel):
            def rename(stream, target):
                if target == self.destination:
                    self.assertFalse(target.exists())
                    target.write_bytes(foreign)
                result = native_rename(stream, target)
                if target.suffix == '.external':
                    cancel.set()
                return result
            with patch.object(backup, '_rename_owned', side_effect=rename):
                return archive_data(directory, snapshot, destination, cancel)
        self.watch_message('backup-gap-collision')
        self.invoke(operation)
        self.assertEqual(len(self.dialog_texts), 1)
        message = self.dialog_texts[0]
        self.assertIn('備份發布未能確認完成', message)
        self.assertIn('\n原因：', message)
        self.assertNotIn('既有 ZIP 未變更', message)
        self.assertEqual(self.destination.read_bytes(), foreign)
        leftovers = {path.suffix: path for path in self.destination.parent.glob('.pcs-backup-*')}
        self.assertEqual(set(leftovers), {'.partial', '.before', '.external'})
        for suffix, path in leftovers.items():
            self.assertIn(str(path), message)
            if suffix != '.partial':
                self.assertEqual(path.read_bytes(), self.before)
        with zipfile.ZipFile(leftovers['.partial']) as archive:
            self.assertIn('data/studio.sqlite3', archive.namelist())
            self.assertIsNone(archive.testzip())

    def test_settings_backup_button_opens_full_cleanup_details_when_statuses_are_hidden(self):
        entry = self.settings_backup_button()
        self.watch_message('backup-settings-cleanup')
        with patch.object(backup, '_delete_stream', side_effect=PermissionError('controlled cleanup refusal')):
            self.invoke(backup.archive_data,
                        start=lambda: QTest.mouseClick(entry, Qt.MouseButton.LeftButton))
        self.assertEqual(len(self.dialog_texts), 1)
        message = self.dialog_texts[0]
        self.assertIn('ZIP 備份完成', message)
        leftovers = list(self.destination.parent.glob('.pcs-backup-*'))
        self.assertEqual({path.suffix for path in leftovers}, {'.before', '.external'})
        for path in leftovers:
            self.assertIn(str(path), message)
            self.assertEqual(path.read_bytes(), self.before)
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIn('data/studio.sqlite3', archive.namelist())
            self.assertIsNone(archive.testzip())
        self.assertFalse(self.w.canvas_status.isVisible())

    def test_embedded_settings_collision_opens_full_error_without_changing_general_toasts(self):
        entry = self.settings_backup_button(embedded=True)
        archive_data, native_rename = backup.archive_data, backup._rename_owned
        foreign = b'foreign destination preserved through workspace backup'
        def operation(directory, snapshot, destination, cancel):
            def rename(stream, target):
                if target == self.destination:
                    self.assertFalse(target.exists())
                    target.write_bytes(foreign)
                return native_rename(stream, target)
            with patch.object(backup, '_rename_owned', side_effect=rename):
                return archive_data(directory, snapshot, destination, cancel)
        self.watch_message('backup-embedded-gap-collision')
        self.invoke(operation, start=lambda: QTest.mouseClick(entry, Qt.MouseButton.LeftButton))
        self.assertEqual(len(self.dialog_texts), 1)
        message = self.dialog_texts[0]
        self.assertIn('備份發布未能確認完成', message)
        self.assertIn('\n原因：', message)
        self.assertEqual(self.destination.read_bytes(), foreign)
        leftovers = {path.suffix: path for path in self.destination.parent.glob('.pcs-backup-*')}
        self.assertEqual(set(leftovers), {'.partial', '.before', '.external'})
        for suffix, path in leftovers.items():
            self.assertIn(str(path), message)
            if suffix != '.partial':
                self.assertEqual(path.read_bytes(), self.before)
        with zipfile.ZipFile(leftovers['.partial']) as archive:
            self.assertIn('data/studio.sqlite3', archive.namelist())
            self.assertIsNone(archive.testzip())
        self.w.notice('後續短訊息')
        self.assertEqual(self.shell.notification.text(), '後續短訊息')
        self.w.error('一般文件錯誤')
        self.assertEqual(self.shell.notification.text(), '一般文件錯誤')
        self.assertEqual(len(self.dialog_texts), 1)


if __name__ == '__main__':
    unittest.main()
