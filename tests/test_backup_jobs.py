"""Public backup entry and real Jobs completion, using synthetic local data."""
import tempfile
import threading
import time
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication
from PySide6.QtTest import QTest

from prompt_calculus_studio import backup
from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.window import Window


APP = QApplication.instance() or QApplication([])


class BackupJobsTests(unittest.TestCase):
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
        self.notices, self.errors, self.details = [], [], []
        # This hidden-window test observes callbacks; real modal interactions
        # are exercised separately by test_backup_message_readability.
        detail_patch=patch('prompt_calculus_studio.window.information',
            side_effect=lambda parent,title,text:self.details.append((title,text)))
        detail_patch.start();self.addCleanup(detail_patch.stop)
        self.w.notice = self.notices.append
        self.w.error = self.errors.append
        self.destination = self.root / 'backup.zip'
        with zipfile.ZipFile(self.destination, 'w') as archive:
            archive.writestr('healthy.txt', b'previous healthy backup')
        self.before = self.destination.read_bytes()

    def tearDown(self):
        self.assertIsNone(self.w.jobs.active)
        self.assertTrue(self.w.jobs.pool.waitForDone(1000))
        self.w.completion.timer.stop()
        self.w.close()
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.tmp.cleanup()

    def invoke(self, operation, after_start=None):
        with (patch('prompt_calculus_studio.window.QFileDialog.getSaveFileName',
                    return_value=(str(self.destination), 'ZIP (*.zip)')),
              patch.object(backup, 'archive_data', side_effect=operation)):
            self.w.backup_zip()
            if after_start is not None:
                after_start()
            deadline = time.monotonic() + 10
            while self.w.jobs.active and time.monotonic() < deadline:
                QTest.qWait(10)
            self.assertIsNone(self.w.jobs.active, 'backup worker did not finish')
        self.assertTrue(self.w.jobs.pool.waitForDone(1000))
        self.assertEqual(self.w.jobs.pool.activeThreadCount(), 0)

    def test_committed_backup_reports_success_even_when_jobs_observes_late_cancel(self):
        archive_data = backup.archive_data
        def commit_then_cancel(directory, snapshot, destination, cancel):
            result = archive_data(directory, snapshot, destination, cancel)
            cancel.set()  # Real Job.run observes this after work() returns.
            return result
        self.invoke(commit_then_cancel)
        self.assertFalse(self.errors)
        self.assertEqual(self.notices[-1], 'ZIP 備份完成；外部連結的原圖與模型需另外備份。')
        with zipfile.ZipFile(self.destination) as archive:
            self.assertIn('data/studio.sqlite3', archive.namelist())
            self.assertNotIn('healthy.txt', archive.namelist())
        self.assertEqual(list(self.root.glob('.pcs-backup-*')), [])

    def test_public_cancel_preserves_previous_zip_and_reports_cancellation(self):
        archive_data = backup.archive_data
        entered = threading.Event()
        def cancel_then_write(directory, snapshot, destination, cancel):
            entered.set()
            self.assertTrue(cancel.wait(2), 'public cancel did not reach the job')
            return archive_data(directory, snapshot, destination, cancel)
        def cancel_started_backup():
            # This case exercises the backup checkpoint. Runner pre-cancel is
            # separately checked to prevent entering the worker at all.
            self.assertTrue(entered.wait(2), 'backup worker did not enter')
            self.w.cancel_jobs()
        self.invoke(cancel_then_write, cancel_started_backup)
        self.assertFalse(self.errors)
        self.assertEqual(self.notices[-1], '已取消備份；既有 ZIP 未變更。')
        self.assertEqual(self.destination.read_bytes(), self.before)

    def test_missing_source_reports_error_without_success_or_losing_old_zip(self):
        archive_data = backup.archive_data
        def missing(directory, snapshot, destination, cancel):
            return archive_data(directory, Path(snapshot).with_name('absent.sqlite3'), destination, cancel)
        self.invoke(missing)
        self.assertEqual(len(self.errors), 1)
        self.assertFalse(any('ZIP 備份完成' in text for text in self.notices))
        self.assertEqual(self.destination.read_bytes(), self.before)
        self.assertEqual(list(self.root.glob('.pcs-backup-*')), [])

    def test_success_with_cleanup_warning_shows_retained_paths(self):
        def fail_recovery_cleanup(*args):
            raise PermissionError('controlled recovery cleanup refusal')
        with patch.object(backup, '_delete_stream', fail_recovery_cleanup):
            self.invoke(backup.archive_data)
        self.assertFalse(self.errors)
        self.assertIn('ZIP 備份完成', self.notices[-1])
        self.assertIn('恢復檔未能清理', self.notices[-1])
        self.assertEqual(self.details,[('備份訊息',self.notices[-1])])
        for path in self.root.glob('.pcs-backup-*'):
            self.assertIn(str(path), self.notices[-1])
            self.assertEqual(path.read_bytes(), self.before)

    def test_success_with_cleanup_warning_during_close_keeps_notice_without_modal(self):
        archive_data = backup.archive_data
        ready, release = threading.Event(), threading.Event()
        def work(directory, snapshot, destination, cancel):
            result = archive_data(directory, snapshot, destination, cancel)
            ready.set()
            self.assertTrue(release.wait(2), 'close fixture did not release')
            return result
        def close_after_publish():
            self.assertTrue(ready.wait(2), 'backup did not publish')
            self.w.closing = True
            self.w.cancel_jobs()
            release.set()
        with patch.object(backup, '_delete_stream', side_effect=PermissionError('controlled cleanup refusal')):
            self.invoke(work, close_after_publish)
        self.assertFalse(self.errors)
        self.assertFalse(self.details)
        self.assertIn('ZIP 備份完成', self.notices[-1])
        self.assertIn('恢復檔未能清理', self.notices[-1])
        retained = list(self.root.glob('.pcs-backup-*'))
        self.assertTrue(retained)
        for path in retained:self.assertIn(str(path), self.notices[-1])

    def test_late_cancel_with_gap_collision_reports_recovery_not_cancellation(self):
        archive_data, native_rename = backup.archive_data, backup._rename_owned
        foreign = b'foreign destination created after the original moved'
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
        self.invoke(operation)
        self.assertEqual(len(self.errors), 1)
        self.assertIn('備份發布未能確認完成', self.errors[0])
        self.assertFalse(any('ZIP 備份完成' in text or '既有 ZIP 未變更' in text
                             for text in self.notices))
        self.assertEqual(self.destination.read_bytes(), foreign)
        retained = {p.suffix: p for p in self.root.glob('.pcs-backup-*')}
        self.assertEqual(set(retained), {'.partial', '.before', '.external'})
        for suffix, path in retained.items():
            self.assertIn(str(path), self.errors[0])
            if suffix != '.partial':
                self.assertEqual(path.read_bytes(), self.before)
        with zipfile.ZipFile(retained['.partial']) as archive:
            self.assertIn('data/studio.sqlite3', archive.namelist())
            self.assertIsNone(archive.testzip())
