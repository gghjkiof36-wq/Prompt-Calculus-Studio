"""Public local-writer flows keep terminal receipts and finish cancellation."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from prompt_calculus_studio import clean_export, pages, recent, civitai_ui
from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.media import import_image
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])


class JobCallerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        state = initial_state()
        state['settings'].update(online=False, comfy_enabled=False)
        store = Storage(self.root / 'data'); store.save(state); store.close()
        self.w = Window(self.root / 'data')
        self.errors, self.notices, self.import_errors = [], [], []
        self.w.error, self.w.notice = self.errors.append, self.notices.append
        self.dialog_patch = patch.object(pages, 'import_error', side_effect=lambda *args: self.import_errors.append(args[-1]))
        self.dialog_patch.start()
        self.source = self.root / 'source.png'
        image = QImage(32, 24, QImage.Format.Format_RGB32)
        image.fill(Qt.GlobalColor.red)
        self.assertTrue(image.save(str(self.source)))

    def tearDown(self):
        self.assertTrue(self.w.jobs.pool.waitForDone(3000))
        APP.processEvents()
        # A failing assertion must not strand a fixture Window in its close loop.
        self.w.gallery.queue = []; self.w.gallery.importing = False
        self.w.completion.timer.stop()
        self.assertTrue(self.w.close())
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        APP.processEvents()
        self.dialog_patch.stop()
        self.tmp.cleanup()

    def wait(self, predicate, label='job'):
        deadline = time.monotonic() + 12
        while not predicate() and time.monotonic() < deadline:QTest.qWait(10)
        self.assertTrue(predicate(), label)

    def idle(self):
        self.wait(lambda: self.w.jobs.active is None)
        self.assertTrue(self.w.jobs.pool.waitForDone(1000))
        QTest.qWait(20)

    def album(self):
        self.w.catalog.put('album', {'id': 'a', 'name': 'Synthetic album'})
        self.w.gallery.album = 'a'
        self.w.gallery.copy_original.setChecked(False)
        return self.w.gallery

    def test_gallery_late_cancel_registers_completed_thumbnail_and_settles(self):
        gallery = self.album()
        original = pages.import_image
        def imported(path, directory, album, copy_original, cancel):
            result = original(path, directory, album, copy_original, cancel)
            cancel.set()
            return result
        with patch.object(pages, 'import_image', side_effect=imported):
            gallery.start_import([str(self.source)])
            self.wait(lambda: self.w.jobs.active is None and not gallery.importing, 'gallery settled')
        self.assertEqual(gallery.import_done, 1)
        rows = self.w.catalog.rows('image', 'a')
        self.assertEqual(len(rows), 1)
        self.assertTrue((self.w.store.directory / rows[0]['thumb']).is_file())
        self.assertFalse(self.import_errors)

    def test_gallery_pre_cancel_during_close_never_imports_or_opens_dialog(self):
        gallery = self.album()
        held = []
        real_start = self.w.jobs.pool.start
        with patch.object(self.w.jobs.pool, 'start', side_effect=held.append):
            gallery.start_import([str(self.source)])
        self.w.closing = True
        self.w.cancel_jobs()
        with patch.object(pages, 'import_image') as importer, patch.object(gallery, 'refresh') as refresh:
            real_start(held[0])
            self.wait(lambda: self.w.jobs.active is None and not gallery.importing, 'pre-cancel close settled')
            importer.assert_not_called(); refresh.assert_not_called()
        self.assertFalse(self.import_errors)
        self.assertEqual(self.w.catalog.count('image'), 0)
        self.assertEqual(len(gallery.import_errors), 1)
        self.assertFalse(gallery.queue)

    def test_gallery_waits_for_interleaved_worker_without_losing_queued_file(self):
        gallery = self.album()
        entered, release = threading.Event(), threading.Event()
        handed_off = []
        def competing(cancel):
            entered.set()
            if not release.wait(3):raise TimeoutError('fixture release missing')
            return 'other work'
        def idle_listener():
            if not handed_off:
                handed_off.append(True)
                self.assertTrue(self.w.jobs.start('interleaved work', competing, lambda _: None))
        self.w.jobs.became_idle.connect(idle_listener)
        try:
            gallery.start_import([str(self.source), str(self.source)])
            self.wait(entered.is_set, 'interleaved worker entered')
            QTest.qWait(30)
            self.assertEqual(gallery.import_done, 1)
            self.assertEqual(gallery.queue, [str(self.source)])
            self.assertTrue(gallery.importing)
        finally:release.set()
        self.wait(lambda: self.w.jobs.active is None and not gallery.importing, 'gallery resumed')
        self.assertEqual(gallery.import_done, 2)
        self.assertEqual(self.w.catalog.count('image', 'a'), 2)

    def test_gallery_declined_start_keeps_queue_for_next_idle(self):
        gallery = self.album()
        with patch.object(self.w.jobs, 'start', return_value=False):
            gallery.start_import([str(self.source)])
        self.assertEqual(gallery.queue, [str(self.source)])
        self.assertTrue(gallery.importing)
        self.w.jobs.became_idle.emit()
        self.wait(lambda: self.w.jobs.active is None and not gallery.importing, 'declined start resumed')
        self.assertEqual(gallery.import_done, 1)

    def test_gallery_failure_during_close_records_error_without_modal_or_next_worker(self):
        gallery = self.album()
        entered, release = threading.Event(), threading.Event()
        def fail(*args):
            entered.set()
            if not release.wait(3):raise TimeoutError('fixture release missing')
            raise OSError('controlled image failure')
        with patch.object(pages, 'import_image', side_effect=fail) as importer, patch.object(gallery, 'refresh') as refresh:
            gallery.start_import([str(self.source), str(self.source)])
            self.wait(entered.is_set, 'import entered')
            self.w.closing = True
            self.w.cancel_jobs()
            release.set()
            self.wait(lambda: self.w.jobs.active is None and not gallery.importing, 'failure close settled')
            self.assertEqual(importer.call_count, 1); refresh.assert_not_called()
        self.assertFalse(self.import_errors)
        self.assertIn('controlled image failure', gallery.import_errors[0])
        self.assertFalse(gallery.queue)

    def test_export_late_cancel_keeps_published_output_report_and_history(self):
        self.w.open_export([str(self.source)]); self.idle()
        page = self.w.clean_export
        destination = self.root / 'published'; destination.mkdir()
        page.directory.setText(str(destination)); page.preview(); self.idle()
        original = clean_export.execute
        def exported(plan, cancel, progress):
            result = original(plan, cancel, progress)
            cancel.set()
            return result
        with patch.object(clean_export, 'execute', side_effect=exported):
            page.export(); self.idle()
        self.assertFalse(page.busy)
        self.assertEqual(page.result['results'][0]['status'], 'Passed')
        self.assertTrue(Path(page.result['results'][0]['target']).is_file())
        self.assertEqual(len(page.records.history()), 1)

    def test_export_cancelled_read_clears_busy_without_accepting_rows(self):
        original = clean_export.scan
        def scanned(paths, recursive, cancel):
            rows = original(paths, recursive, cancel)
            cancel.set()
            return rows
        with patch.object(clean_export, 'scan', side_effect=scanned):
            self.w.open_export([str(self.source)]); self.idle()
        page = self.w.clean_export
        self.assertFalse(page.busy)
        self.assertEqual(page.rows, [])
        self.assertIn('已取消', page.feedback.text())

    def test_model_copy_late_cancel_keeps_file_and_runs_followup_scan(self):
        root = self.root / 'models'; (root / 'loras').mkdir(parents=True)
        source = self.root / 'tiny.safetensors'; source.write_bytes(b'synthetic model bytes')
        model_page = self.w.models; model_page.root.setText(str(root))
        original = pages.copy_model
        def copied(source, root, kind, cancel):
            result = original(source, root, kind, cancel)
            cancel.set()
            return result
        with (patch.object(pages.QFileDialog, 'getOpenFileName', return_value=(str(source), '')),
              patch.object(pages.QInputDialog, 'getItem', return_value=('loras', True)),
              patch.object(pages, 'ask', return_value=True),
              patch.object(pages, 'copy_model', side_effect=copied),
              patch.object(model_page, 'scan') as scan):
            model_page.import_file(); self.idle()
            scan.assert_called_once_with()
        self.assertEqual((root / 'loras' / source.name).read_bytes(), source.read_bytes())

    def test_accepted_recent_collection_late_cancel_finishes_captured_owner(self):
        self.album()
        page = self.w.recent
        record = import_image(self.source, self.w.store.directory, '')
        record['id'] = 'existing-result'; self.w.catalog.put('recent', record)
        original = recent.import_image
        def imported(*args, **kwargs):
            result = original(*args, **kwargs)
            self.w.jobs.active.cancel.set()
            return result
        page.collecting.add(record['id'])
        page.saves.append((record['id'], {'path': str(self.source)}, 'album:a', 'a', self.w.store, self.w.catalog))
        with patch.object(recent, 'import_image', side_effect=imported):
            page.pump(); self.idle()
        self.assertFalse(page.processing)
        self.assertFalse(page.collecting)
        self.assertEqual(self.w.catalog.count('image', 'a'), 1)
        self.assertEqual(self.w.catalog.get(record['id'])['collected']['album:a']['path'], str(self.source))

    def test_downloaded_receipt_late_cancel_still_reaches_registration(self):
        page = self.w.settings_page.civitai
        plan = {'id': 'a' * 32, 'name': 'Model', 'filename': 'model.safetensors',
                'target': str(self.root), 'state': 'queued', 'source': {}}
        page.receipts.save(plan); page.pending_download = plan
        def downloaded(receipts, plan, token, cancel, progress):
            path = self.root / plan['filename']; path.write_bytes(b'completed synthetic model')
            record = dict(plan, state='downloaded', result={'path': str(path), 'source': {'previews': []}})
            receipts.save(record)
            cancel.set()
            return record
        with (patch.object(civitai_ui, 'perform_download', side_effect=downloaded),
              patch.object(page.policy, 'track'),
              patch.object(page, 'finish_registration') as register):
            page.start_download(); self.idle()
            register.assert_called_once()
            self.assertEqual(register.call_args.args[0]['state'], 'downloaded')
        self.assertIsNone(page.active_download)
        self.assertEqual(page.receipts.get(plan['id'])['state'], 'downloaded')

    def test_accepted_recent_collection_pre_cancel_during_close_still_registers(self):
        self.album()
        page = self.w.recent
        record = import_image(self.source, self.w.store.directory, '')
        record['id'] = 'accepted-before-close'; self.w.catalog.put('recent', record)
        page.collecting.add(record['id'])
        page.saves.append((record['id'], {'path': str(self.source)}, 'album:a', 'a', self.w.store, self.w.catalog))
        held = []
        real_start = self.w.jobs.pool.start
        with patch.object(self.w.jobs.pool, 'start', side_effect=held.append):page.pump()
        self.assertEqual(len(held), 1)
        self.w.closing = True
        self.w.cancel_jobs()
        real_start(held[0]); self.idle()
        self.assertFalse(page.processing)
        self.assertFalse(page.collecting)
        self.assertEqual(self.w.catalog.count('image', 'a'), 1)
        self.assertEqual(self.w.catalog.get(record['id'])['collected']['album:a']['path'], str(self.source))

    def test_download_pre_cancel_settles_queued_receipt_without_entering_downloader(self):
        page = self.w.settings_page.civitai
        plan = {'id': 'b' * 32, 'name': 'Model', 'filename': 'model.safetensors',
                'target': str(self.root), 'state': 'queued', 'source': {}}
        page.receipts.save(plan); page.pending_download = plan
        held = []
        real_start = self.w.jobs.pool.start
        with patch.object(self.w.jobs.pool, 'start', side_effect=held.append), patch.object(page.policy, 'track'):
            page.start_download()
        self.w.cancel_jobs()
        with patch.object(civitai_ui, 'perform_download') as downloader:
            real_start(held[0]); self.idle()
            downloader.assert_not_called()
        self.assertIsNone(page.active_download)
        self.assertEqual(page.receipts.get(plan['id'])['state'], 'cancelled')
        self.assertFalse((self.root / plan['filename']).exists())
