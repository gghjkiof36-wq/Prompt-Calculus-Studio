"""Save As keeps result catalogs, preview files and execution owners together."""
import copy
import json
import os
from pathlib import Path
import time
import threading
import unittest
from unittest.mock import patch

from PySide6.QtGui import QImage

import test_workspace_recovery_handoff as helper
from prompt_calculus_studio.recent import result_id


class RetargetTests(unittest.TestCase):
    setUpClass = classmethod(helper.HandoffTests.setUpClass.__func__)
    setUp = helper.HandoffTests.setUp
    tearDown = helper.HandoffTests.tearDown
    window = helper.HandoffTests.window

    def record(self, owner, name):
        path = Path(self.area.name) / (name + '.png')
        image = QImage(16, 16, QImage.Format.Format_RGB32)
        image.fill(0xff406080)
        self.assertTrue(image.save(str(path), 'PNG'))
        return dict(prompt_id='synthetic-' + name, node_id='9', path=str(path),
                    image=dict(filename=path.name, subfolder='', type='output'),
                    document_id=owner.document_id, runtime_token=owner.runtime_token)

    def idle(self, document):
        deadline = time.monotonic() + 8
        while any(h.recent.processing or h.recent.pending or h.jobs.active for h in document.hosts):
            self.assertLess(time.monotonic(), deadline, 'Recent processing did not finish')
            helper.events()

    def copy(self, window, tab):
        with patch.object(window, 'show_notice', wraps=window.show_notice) as notices:
            target = window.commands.files.run('save_as', window.commands.context('tab', tab=tab),
                                              name='Copy', directory=self.root)
        self.assertEqual(target, self.root / 'Copy.pcs-workflow', (self.errors, notices.call_args_list))
        helper.events()
        return target

    def test_new_results_use_new_catalog_and_preview_root(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        document, old = tab.document, tab.document.execution
        record = self.record(old, 'before-copy')
        old.client.resultsReceived.emit([record])
        self.idle(document)
        old_rows = list(old.store.db.execute('SELECT id,body FROM resources ORDER BY id'))
        old_files = sorted(str(p.relative_to(old.store.directory)) for p in old.store.directory.rglob('*.jpg'))
        self.assertTrue(old_rows)
        target = self.copy(window, tab)
        current = document.execution
        self.assertIsNone(old.document)
        self.assertIsNot(old, current)
        source_bytes, copy_bytes = self.paths[0].read_bytes(), target.read_bytes()
        state = helper.selected(document.state)
        new_record = self.record(current, 'after-copy')
        current.client.resultsReceived.emit([new_record])
        self.idle(document)
        new_rows = list(current.store.db.execute('SELECT id,body FROM resources ORDER BY id'))
        old_after = list(old.store.db.execute('SELECT id,body FROM resources ORDER BY id'))
        observations = dict(old_before=old_rows, old_after=old_after, new_rows=new_rows,
                            old_directory=str(old.store.directory), new_directory=str(current.store.directory),
                            old_previews_before=old_files,
                            new_previews=sorted(str(p.relative_to(current.store.directory)) for p in current.store.directory.rglob('*.jpg')),
                            recent_uses_current=[h.recent.catalog.db is current.store.db for h in document.hosts])
        if os.environ.get('PCS_REVIEW_OUT'):
            (Path(os.environ['PCS_REVIEW_OUT']) / 'catalog-ownership.json').write_text(
                json.dumps(observations, ensure_ascii=False, indent=2), encoding='utf-8')
        self.assertEqual(old_after, old_rows)
        self.assertEqual(len(new_rows), 1)
        for host in document.hosts:
            self.assertIs(host.recent.catalog.db, current.store.db)
            self.assertEqual(host.recent.known, {result_id(new_record)})
            row = host.recent.catalog.get(result_id(new_record))
            self.assertTrue((current.store.directory / row['thumb']).is_file())
            self.assertFalse((old.store.directory / row['thumb']).exists())
        old.client.resultsReceived.emit([self.record(old, 'late-old')])
        helper.events()
        self.assertEqual(current.received, [new_record])
        self.assertEqual(list(old.store.db.execute('SELECT id,body FROM resources ORDER BY id')), old_rows)
        self.assertEqual(helper.selected(document.state), state)
        self.assertEqual(self.paths[0].read_bytes(), source_bytes)
        self.assertEqual(target.read_bytes(), copy_bytes)
        self.assertFalse(self.errors)
        self.assertTrue(window.close())
        reopened = self.window()
        source_tab = reopened.open_path(self.paths[0], True, new_tab=True)
        copy_tab = reopened.open_path(target, True, new_tab=True)
        self.assertEqual(list(source_tab.host.store.db.execute('SELECT id,body FROM resources ORDER BY id')), old_rows)
        self.assertEqual(list(copy_tab.host.store.db.execute('SELECT id,body FROM resources ORDER BY id')), new_rows)
        for page in (source_tab, copy_tab):
            for row in page.host.catalog.rows('recent'):
                self.assertTrue((page.host.store.directory / row['thumb']).is_file())
        self.assertFalse(self.errors)
        self.assertTrue(reopened.close())

    def test_inflight_preview_finishes_in_original_owner(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        document, old = tab.document, tab.document.execution
        page = tab.host.recent
        record = self.record(old, 'inflight-old')
        callbacks = []
        # Hold the background dispatch boundary; run the actual image importer
        # and completion after Save As, as a delayed worker would do.
        def start(label, work, done, failed=None):
            callbacks.append((work, done))
            return True
        with patch.object(tab.host.jobs, 'start', side_effect=start):
            old.client.resultsReceived.emit([record])
        self.assertEqual(len(callbacks), 1)
        self.copy(window, tab)
        current = document.execution
        self.assertNotIn('recent_destination', document.document['payload']['state']['settings'])
        newer = self.record(current, 'queued-new')
        current.client.resultsReceived.emit([newer])
        work, done = callbacks[0]
        done(work(threading.Event()))
        self.idle(document)
        old_record = old.store.db.execute('SELECT body FROM resources WHERE id=?', (result_id(record),)).fetchone()
        self.assertIsNotNone(old_record)
        thumb = json.loads(old_record[0])['thumb']
        self.assertTrue((old.store.directory / thumb).is_file())
        self.assertFalse((current.store.directory / thumb).exists())
        self.assertEqual(page.known, {result_id(newer)})
        self.assertEqual([r[0] for r in current.store.db.execute('SELECT id FROM resources')], [result_id(newer)])
        self.assertFalse(self.errors)

    def test_external_destination_stays_local_when_saving(self):
        from PySide6.QtWidgets import QFileDialog
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        destination = Path(self.area.name) / 'collection-destination'
        destination.mkdir()
        with patch.object(QFileDialog, 'getExistingDirectory', return_value=str(destination)):
            tab.host.recent.choose_directory()
        self.assertEqual(tab.document.state['settings']['recent_external'], str(destination))
        self.assertTrue(window.save_tab(tab, quiet=True), tab.document.error)
        target = self.copy(window, tab)
        settings = tab.document.document['payload']['state']['settings']
        self.assertNotIn('recent_external', settings)
        self.assertNotIn('recent_destination', settings)
        self.assertNotIn(str(destination).encode(), target.read_bytes())
        self.assertEqual(tab.document.state['settings']['recent_external'], str(destination))
        self.assertFalse(self.errors)

    def test_late_collection_keeps_original_catalog_and_destination(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        document, old = tab.document, tab.document.execution
        page = tab.host.recent
        source = self.record(old, 'collect-old')
        old.client.resultsReceived.emit([source])
        self.idle(document)
        catalog = page.catalog
        catalog.put('album', dict(id='synthetic-album', name='Old album'))
        page.refresh_destinations()
        page.destination_picker.setCurrentIndex(page.destination_picker.findData('album:synthetic-album'))
        self.assertEqual(document.state['settings']['recent_destination'], 'album:synthetic-album')
        old.client.connected = True
        with patch.object(old.client, 'request') as request:
            page.collect(catalog.get(result_id(source)))
        self.assertEqual(request.call_count, 1)
        arguments = request.call_args.args
        self.assertEqual(arguments[0], 'desktop/collect')
        destination = arguments[1]['destination']
        self.assertTrue(Path(destination).is_relative_to(old.store.directory))
        old.client.connected = False
        self.copy(window, tab)
        current = document.execution
        saved = Path(destination) / 'collected.png'
        saved.write_bytes(Path(source['path']).read_bytes())
        arguments[2](dict(path=str(saved), has_generation=False, has_snapshot=False))
        self.idle(document)
        images = catalog.rows('image')
        self.assertEqual(len(images), 1)
        self.assertEqual(images[0]['path'], str(saved.resolve()))
        self.assertTrue((old.store.directory / images[0]['thumb']).is_file())
        self.assertEqual(current.store.db.execute('SELECT COUNT(*) FROM resources').fetchone()[0], 0)
        self.assertEqual(page.known, set())
        self.assertIn(destination, catalog.get(result_id(source))['collected'])
        self.assertFalse(self.errors)

    def test_late_refresh_does_not_feed_new_owner(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        old = tab.document.execution
        old.client.connected = True
        with patch.object(old.client, 'request') as request:
            tab.host.recent.request_refresh()
        old.client.connected = False
        self.copy(window, tab)
        request.call_args.kwargs['done']([self.record(old, 'old-refresh')])
        helper.events()
        self.assertEqual(tab.host.recent.pending, [])
        self.assertEqual(tab.host.recent.known, set())
        self.assertEqual(tab.document.execution.store.db.execute('SELECT COUNT(*) FROM resources').fetchone()[0], 0)
        self.assertFalse(self.errors)

    def test_unknown_and_malformed_preferences_are_rejected(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        before = self.paths[0].read_bytes()
        settings = tab.document.state['settings']
        for key, value in (('synthetic_unknown_setting', 'keep rejection'),
                           ('recent_destination', {'not': 'text'}), ('recent_external', [])):
            with self.subTest(key=key):
                settings[key] = value
                try:
                    self.assertFalse(window.save_tab(tab, quiet=True))
                    self.assertEqual(self.paths[0].read_bytes(), before)
                    self.assertTrue(tab.document.error)
                finally:
                    settings.pop(key)
        self.assertTrue(window.save_tab(tab, quiet=True), tab.document.error)
