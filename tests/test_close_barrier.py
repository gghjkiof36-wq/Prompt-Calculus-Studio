"""Closing rejects new work while accepted collection receipts still settle."""
import copy
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication

from _qt_cleanup import close_window
from prompt_calculus_studio import recent
from prompt_calculus_studio.core import Storage, initial_state
from prompt_calculus_studio.job_outcomes import JobOutcome
from prompt_calculus_studio.window import Window

APP = QApplication.instance() or QApplication([])


class CloseBarrierTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.root = Path(self.area.name)
        state = initial_state()
        state['settings'].update(online=False, comfy_enabled=False)
        store = Storage(self.root / 'data')
        store.save(state)
        store.close()
        self.w = Window(self.root / 'data')
        self.errors, self.notices, self.qt_errors = [], [], []
        self.w.error, self.w.notice = self.errors.append, self.notices.append
        hook = patch.object(sys, 'excepthook', lambda k, v, tb: self.qt_errors.append(str(v)))
        hook.start()
        self.addCleanup(hook.stop)
        self.source = self.root / 'source.png'
        picture = QImage(24, 24, QImage.Format.Format_RGB32)
        picture.fill(Qt.GlobalColor.red)
        self.assertTrue(picture.save(str(self.source)))
        self.result = dict(path=str(self.source), prompt_id='synthetic', node_id='9',
                           image=dict(filename='source.png', subfolder='', type='temp'))

    def tearDown(self):
        close_window(self.w)
        self.w.store.close()  # Also release the deliberately non-owning host case.
        self.w.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.area.cleanup()
        self.assertEqual(self.qt_errors, [])
        self.assertEqual(self.errors, [])

    def check_direct_close(self):
        page = self.w.recent
        callbacks = []
        self.w.comfy.connected = True
        with patch.object(self.w.comfy, 'request', side_effect=lambda *a, **k: callbacks.append(k['done'])) as request:
            page.request_refresh()
            self.assertEqual(len(callbacks), 1)
            # Arm the actual busy-worker retry, then let that worker become idle
            # without processing the timer before the direct close.
            page.pending = [copy.deepcopy(self.result)]
            self.w.jobs.active = object()
            page.pump()
            self.w.jobs.active = None
            self.assertTrue(page.pump_timer.isActive())
            page.refresh_timer.start()
            before = sorted(self.w.store.directory.glob('thumbnails/*'))
            idle = []
            self.w.jobs.became_idle.connect(lambda: idle.append(True))
            with patch.object(self.w.jobs.pool, 'start') as start, patch.object(recent, 'import_image') as importer:
                self.assertTrue(self.w.close())
                self.assertTrue(self.w.closing)
                self.assertTrue(self.w._close_completed)
                self.assertTrue(self.w.is_closed())
                self.assertFalse(page.pump_timer.isActive())
                self.assertFalse(page.refresh_timer.isActive())
                # The completed flag must also stand on its own.
                self.w.closing = False
                page.pending = [copy.deepcopy(self.result)]
                page.pump()
                page.pump_timer.timeout.emit()
                callbacks[0]([self.result])
                page.receive_results([self.result])
                page.request_refresh()
                for accepted in (False, True):
                    self.assertFalse(self.w.jobs.start('late', lambda cancel: None, lambda value: None,
                                                       finish_accepted=accepted))
                self.w.jobs.finished(None, 'late worker failure')
                self.w.jobs.callback = lambda value: (_ for _ in ()).throw(ValueError('late callback failure'))
                self.w.jobs.finished(None, '')
                APP.processEvents()
                start.assert_not_called()
                importer.assert_not_called()
            self.assertEqual(request.call_count, 1)
            self.assertEqual(idle, [])
            self.assertEqual(page.pending, [])
            self.assertIsNone(self.w.jobs.active)
            self.assertEqual(sorted(self.w.store.directory.glob('thumbnails/*')), before)
            self.assertIn('late worker failure', self.notices)
            self.assertIn('late callback failure', self.notices)

    def test_direct_close_blocks_late_callbacks_and_workers(self):
        self.check_direct_close()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.w.store.db.execute('SELECT 1')

    def test_non_owning_document_close_also_blocks_late_work(self):
        self.w.owns_store = False
        self.check_direct_close()
        self.assertEqual(self.w.store.db.execute('SELECT 1').fetchone(), (1,))

    def test_queued_release_pump_cannot_import_next_preview_after_close(self):
        held = []
        second = dict(self.result, prompt_id='second')
        with patch.object(self.w.jobs.pool, 'start', side_effect=held.append):
            self.w.recent.receive_results([self.result, second])
            self.assertEqual(len(held), 1)
            held[0].run()  # Completion queues the real release() singleShot.
            self.assertIsNone(self.w.jobs.active)
            self.assertEqual(len(self.w.recent.pending), 1)
            before = sorted(self.w.store.directory.glob('thumbnails/*'))
            self.assertEqual(len(before), 1)
            close_window(self.w)
            self.assertTrue(self.w._close_completed)
            APP.processEvents()
            self.assertEqual(len(held), 1)
            self.assertEqual(sorted(self.w.store.directory.glob('thumbnails/*')), before)

    def test_deferred_close_sets_barrier_before_worker_completion(self):
        held, delivered = [], []
        def done(value):
            delivered.append((value, self.w.closing, getattr(self.w, '_close_completed', False)))
        with patch.object(self.w.jobs.pool, 'start', side_effect=held.append):
            self.assertTrue(self.w.jobs.start('existing writer', lambda cancel: JobOutcome.completed('receipt'), done))
            # The write has completed; keep its queued Qt receipt pending until
            # after close requests cancellation, as a real worker can do.
            outcome = held[0].function(held[0].cancel)
            self.assertFalse(self.w.close())
            self.assertTrue(self.w.closing)
            self.assertFalse(getattr(self.w, '_close_completed', False))
            self.assertEqual(self.w.store.db.execute('SELECT 1').fetchone(), (1,))
            self.assertFalse(self.w.jobs.start('new work', lambda cancel: None, lambda value: None))
            self.assertEqual(len(held), 1)
            held[0].signals.finished.emit(outcome.value, outcome.message)
            self.assertEqual(delivered, [('receipt', True, False)])
            self.assertTrue(self.w.close())
            self.assertTrue(self.w._close_completed)

    def test_accepted_collection_arriving_during_close_registers_before_store_closes(self):
        page = self.w.recent
        self.w.catalog.put('album', dict(id='album', name='Synthetic album'))
        self.w.state['settings']['recent_destination'] = 'album:album'
        page.refresh_destinations()
        record = recent.import_image(self.source, self.w.store.directory, '')
        record.update(id='recent-source', source=self.result, collected={})
        self.w.catalog.put('recent', record)
        self.w.comfy.connected = True
        requests, held = [], []
        with patch.object(self.w.comfy, 'request', side_effect=lambda *a: requests.append(a)), \
                patch.object(self.w.jobs.pool, 'start', side_effect=held.append):
            self.w.jobs.start('existing read', lambda cancel: None, lambda value: None)
            page.collect(record)
            self.assertEqual(requests[0][0], 'desktop/collect')
            self.assertFalse(self.w.close())
            self.assertTrue(self.w.closing)
            requests[0][2]({'path': str(self.source)})
            self.assertEqual(len(page.saves), 1)
            self.assertFalse(page.pump_timer.isActive())
            held[0].run()
            # The existing close retry drains accepted receipts even when an
            # unrelated worker held the slot at the time the reply arrived.
            self.assertFalse(self.w.close())
            self.assertEqual(len(held), 2)
            self.assertTrue(held[1].finish_accepted)
            self.assertTrue(held[1].cancel.is_set())
            held[1].run()
            self.assertFalse(page.collecting)
            self.assertFalse(page.processing)
            self.assertEqual(self.w.catalog.count('image', 'album'), 1)
            saved = self.w.catalog.get(record['id'])
            destination = requests[0][1]['destination']
            self.assertEqual(saved['collected'][destination]['path'], str(self.source))
            self.assertTrue(self.w.close())
            APP.processEvents()
            self.assertEqual(len(held), 2)

    def test_declined_collection_start_retains_registration_obligation(self):
        page = self.w.recent
        save = ('accepted', {'path': str(self.source)}, 'external', '', self.w.store, self.w.catalog)
        page.saves.append(save)
        with patch.object(self.w.jobs, 'start', return_value=False):
            page.pump()
        self.assertFalse(page.processing)
        self.assertEqual(page.saves, [save])
        page.saves.clear()

    def test_rejected_close_does_not_raise_barrier(self):
        with patch.object(self.w, 'flush_editor_drafts', return_value=False):
            self.assertFalse(self.w.close())
        self.assertFalse(self.w.closing)
        self.assertFalse(self.w.is_closed())
