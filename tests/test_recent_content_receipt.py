"""Real importer receipts protect accepted updates while coalescing unchanged work."""
import copy
import io
import os
from pathlib import Path
import struct
import threading
import time
import unittest
from unittest.mock import patch
import zlib

from PySide6.QtGui import QImage

from prompt_calculus_studio import image_capture, media, recent
from prompt_calculus_studio.pnginfo import png_metadata, png_metadata_stream
import test_recent_retarget as retarget
import test_workspace_recovery_handoff as helper


def png_bytes(color, note):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    pixels = b''.join(b'\0' + bytes(color) * 16 for _ in range(16))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 16, 16, 8, 2, 0, 0, 0))
            + chunk(b'tEXt', b'parameters\0' + note.encode())
            + chunk(b'IDAT', zlib.compress(pixels, 0)) + chunk(b'IEND', b''))


OLD = png_bytes((220, 30, 30), 'OLD')
NEW = png_bytes((30, 30, 220), 'NEW')


class ContentReceiptTests(unittest.TestCase):
    setUpClass = classmethod(helper.HandoffTests.setUpClass.__func__)
    window = helper.HandoffTests.window
    copy = retarget.RetargetTests.copy
    idle = retarget.RetargetTests.idle

    def setUp(self):
        helper.HandoffTests.setUp(self)
        self.network = patch('prompt_calculus_studio.comfy_client.ComfyClient.request',
                             side_effect=AssertionError('No service request in receipt tests'))
        self.network.start()

    def tearDown(self):
        try:helper.HandoffTests.tearDown(self)
        finally:self.network.stop()

    def opened(self):
        window = self.window()
        tab = window.open_path(self.paths[0], True, new_tab=True)
        return window, tab, tab.document.execution, tab.host.recent

    def source(self, owner, name='source'):
        path = Path(self.area.name) / (name + '.png')
        path.write_bytes(OLD)
        return dict(prompt_id='synthetic-' + name, node_id='9', path=str(path),
                    image=dict(filename=name + '.png', subfolder='', type='output'),
                    document_id=owner.document_id, runtime_token=owner.runtime_token)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while not predicate():
            self.assertLess(time.monotonic(), deadline, 'Receipt worker did not reach its gate')
            helper.events()

    def assert_image(self, store, row, note):
        self.assertEqual(row['metadata']['raw']['parameters'], note)
        image = QImage(str(store.directory / row['thumb']))
        self.assertFalse(image.isNull())
        color = image.pixelColor(0, 0)
        if note == 'NEW':self.assertGreater(color.blue(), color.red() + 150)
        else:self.assertGreater(color.red(), color.blue() + 150)

    def pair(self, change=None, *, first_failure=False, cancel=False, oversized=False):
        window, tab, owner, page = self.opened()
        source = self.source(owner)
        original_document = self.paths[0].read_bytes()
        original_state = helper.selected(tab.document.state)
        reached, release = threading.Event(), threading.Event()
        original_import = recent.import_image
        calls = []

        def held(*args, **kwargs):
            calls.append(args)
            if len(calls) == 1:
                value = None if first_failure else original_import(*args, **kwargs)
                reached.set()
                if not release.wait(5):raise AssertionError('Receipt gate was not released')
                if first_failure:raise OSError('synthetic first read failure')
                return value
            return original_import(*args, **kwargs)

        limit = 16 if oversized else image_capture.MAX_CAPTURE_BYTES
        with patch.object(image_capture, 'MAX_CAPTURE_BYTES', limit), \
                patch.object(recent, 'import_image', side_effect=held), \
                patch.object(media, 'thumbnail', wraps=media.thumbnail) as thumbnails, \
                patch.object(page.catalog, 'put', wraps=page.catalog.put) as writes:
            try:
                owner.client.resultsReceived.emit([source])
                self.wait_for(reached.is_set)
                update = copy.deepcopy(source)
                if change:update = change(update, window, tab, owner, page) or update
                owner.client.resultsReceived.emit([update])
                self.assertEqual(len(page.pending), 1)
                self.assertNotIn(recent.result_id(source), page.known)
                if cancel:tab.host.jobs.active.cancel.set()
            finally:release.set()
            self.idle(tab.document)
            row = page.catalog.get(recent.result_id(source))
            counts = (len(calls), thumbnails.call_count, writes.call_count)
            owner.client.resultsReceived.emit([update])
            helper.events()
            self.assertEqual(counts, (len(calls), thumbnails.call_count, writes.call_count))
        self.assertFalse(page.processing)
        self.assertFalse(page.pending)
        self.assertFalse(page._preview_receipts)
        self.assertEqual(self.paths[0].read_bytes(), original_document)
        self.assertEqual(helper.selected(tab.document.state), original_state)
        self.assertFalse(self.errors)
        return owner, row, counts, update

    def test_unchanged_inflight_input_uses_one_import_thumbnail_and_write(self):
        owner, row, counts, source = self.pair()
        self.assertEqual(counts, (1, 1, 1))
        self.assertEqual(row['source'], source)
        self.assert_image(owner.store, row, 'OLD')

    def test_same_id_new_path_and_record_publish_new_pixels_and_metadata(self):
        def change(source, *_):
            path = Path(self.area.name) / 'replacement.png'
            path.write_bytes(NEW)
            source.update(path=str(path), node_id='10')
            return source
        owner, row, counts, source = self.pair(change)
        self.assertEqual(counts, (2, 2, 2))
        self.assertEqual(row['source'], source)
        self.assert_image(owner.store, row, 'NEW')

    def test_same_path_same_size_mtime_changed_bytes_publish_new(self):
        def change(source, *_):
            path = Path(source['path']); before = path.stat()
            self.assertEqual(len(OLD), len(NEW))
            path.write_bytes(NEW)
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))
            after = path.stat()
            self.assertEqual((after.st_size, after.st_mtime_ns), (before.st_size, before.st_mtime_ns))
            return source
        owner, row, counts, source = self.pair(change)
        self.assertEqual(counts, (2, 2, 2))
        self.assertEqual(row['source'], source)
        self.assert_image(owner.store, row, 'NEW')

    def test_same_bytes_changed_record_is_not_coalesced(self):
        def change(source, *_):source['node_id'] = 'new-node'; return source
        owner, row, counts, source = self.pair(change)
        self.assertEqual(counts, (2, 2, 2))
        self.assertEqual(row['source'], source)
        self.assert_image(owner.store, row, 'OLD')

    def test_first_failure_keeps_accepted_retry(self):
        owner, row, counts, _ = self.pair(first_failure=True)
        self.assertEqual(counts, (2, 1, 1))
        self.assert_image(owner.store, row, 'OLD')

    def test_failure_without_pending_allows_later_retry(self):
        _, tab, owner, page = self.opened(); source = self.source(owner)
        with patch.object(recent, 'import_image', side_effect=OSError('synthetic read failure')):
            page.receive_results([source]); self.idle(tab.document)
        self.assertFalse(page.processing)
        self.assertFalse(page._preview_receipts)
        self.assertNotIn(recent.result_id(source), page.known)
        page.receive_results([source]); self.idle(tab.document)
        self.assert_image(owner.store, page.catalog.get(recent.result_id(source)), 'OLD')

    def test_failed_catalog_write_does_not_create_receipt_or_leak_preview(self):
        _, tab, owner, page = self.opened(); source = self.source(owner)
        with patch.object(page.catalog, 'put', side_effect=OSError('synthetic SQL failure')), \
                patch.object(tab.host, 'error') as errors:
            page.receive_results([source]); self.idle(tab.document)
            errors.assert_called_once()
        self.assertFalse(page._preview_receipts)
        self.assertNotIn(recent.result_id(source), page.known)
        self.assertEqual(list((owner.store.directory / 'thumbnails').glob('*.jpg')), [])
        page.receive_results([source]); self.idle(tab.document)
        self.assert_image(owner.store, page.catalog.get(recent.result_id(source)), 'OLD')

    def test_cancelled_import_releases_processing_cleans_preview_and_retries(self):
        owner, row, counts, _ = self.pair(cancel=True)
        self.assertEqual(counts, (2, 2, 1))
        self.assertEqual(list((owner.store.directory / 'thumbnails').glob('*.jpg')),
                         [owner.store.directory / row['thumb']])
        self.assert_image(owner.store, row, 'OLD')

    def test_above_capture_budget_keeps_supported_input_and_does_not_deduplicate(self):
        owner, row, counts, _ = self.pair(oversized=True)
        self.assertEqual(counts, (2, 2, 2))
        self.assert_image(owner.store, row, 'OLD')

    def test_capture_binds_pixels_and_metadata_even_after_source_replacement(self):
        _, _, owner, _ = self.opened()
        source = self.source(owner)
        captured = image_capture.capture_image(source['path'])
        Path(source['path']).write_bytes(NEW)
        row = media.import_image(source['path'], owner.store.directory, '', captured=captured)
        self.assert_image(owner.store, row, 'OLD')
        self.assertEqual(Path(source['path']).read_bytes(), NEW)
        Path(source['path']).unlink()
        row = media.import_image(source['path'], owner.store.directory, '', captured=captured)
        self.assert_image(owner.store, row, 'OLD')

    def test_capture_reads_non_png_supported_formats(self):
        _, _, owner, _ = self.opened()
        image = QImage(16, 16, QImage.Format.Format_RGB32); image.fill(0xffdc1e1e)
        for extension in ('jpg', 'bmp', 'webp'):
            with self.subTest(extension=extension):
                path = Path(self.area.name) / ('format.' + extension)
                self.assertTrue(image.save(str(path)))
                row = media.import_image(path, owner.store.directory, '', captured=image_capture.capture_image(path))
                self.assertEqual(row['metadata']['source'], 'none')
                self.assertFalse(QImage(str(owner.store.directory / row['thumb'])).isNull())

    def test_stream_metadata_preserves_path_behavior_and_caller_ownership(self):
        path = Path(self.area.name) / 'metadata.png'; path.write_bytes(OLD)
        stream = io.BytesIO(OLD)
        self.assertEqual(png_metadata_stream(stream), png_metadata(path))
        self.assertFalse(stream.closed)

    def test_start_refusal_preserves_pending_and_releases_processing(self):
        _, tab, owner, page = self.opened(); source = self.source(owner)
        with patch.object(tab.host.jobs, 'start', return_value=False):page.receive_results([source])
        self.assertFalse(page.processing)
        self.assertEqual(page.pending, [source])
        page.pump(); self.idle(tab.document)
        self.assert_image(owner.store, page.catalog.get(recent.result_id(source)), 'OLD')

    def test_start_exception_preserves_pending_and_releases_processing(self):
        _, tab, owner, page = self.opened(); source = self.source(owner)
        with patch.object(tab.host.jobs, 'start', side_effect=RuntimeError('synthetic dispatch failure')):
            with self.assertRaisesRegex(RuntimeError, 'synthetic dispatch'):page.receive_results([source])
        self.assertFalse(page.processing)
        self.assertEqual(page.pending, [source])
        page.pump(); self.idle(tab.document)
        self.assert_image(owner.store, page.catalog.get(recent.result_id(source)), 'OLD')

    def test_pending_source_is_copied_before_caller_mutates_it(self):
        _, tab, owner, page = self.opened(); source = self.source(owner)
        original = copy.deepcopy(source)
        with patch.object(tab.host.jobs, 'start', return_value=False):page.receive_results([source])
        source['node_id'] = 'mutated'; source['image']['filename'] = 'mutated.png'
        page.pump(); self.idle(tab.document)
        self.assertEqual(page.catalog.get(recent.result_id(original))['source'], original)

    def test_save_as_same_id_keeps_old_writer_and_new_owner_independent(self):
        window, tab, owner, page = self.opened(); source = self.source(owner)
        reached, release = threading.Event(), threading.Event()
        original_import = recent.import_image; calls = []
        def held(*args, **kwargs):
            calls.append(args); result = original_import(*args, **kwargs)
            if len(calls) == 1:
                reached.set()
                if not release.wait(5):raise AssertionError('Save As gate was not released')
            return result
        with patch.object(recent, 'import_image', side_effect=held):
            try:
                owner.client.resultsReceived.emit([source]); self.wait_for(reached.is_set)
                self.copy(window, tab)
                current = tab.document.execution
                page.receive_results([source])
                self.assertIsNot(current, owner)
            finally:release.set()
            self.idle(tab.document)
        self.assertEqual(len(calls), 2)
        old_row = media.Catalog(owner.store).get(recent.result_id(source))
        new_row = page.catalog.get(recent.result_id(source))
        self.assert_image(owner.store, old_row, 'OLD')
        self.assert_image(current.store, new_row, 'OLD')
        self.assertNotEqual(old_row['thumb'], new_row['thumb'])
        self.assertEqual(page.known, {recent.result_id(source)})
        self.assertFalse(page._preview_receipts)
        self.assertFalse(self.errors)

    def test_distinct_ids_both_import_identical_bytes(self):
        _, tab, owner, page = self.opened()
        first = self.source(owner, 'first'); second = self.source(owner, 'second')
        with patch.object(recent, 'import_image', wraps=recent.import_image) as imports:
            page.receive_results([first, second]); self.idle(tab.document)
        self.assertEqual(imports.call_count, 2)
        for source in (first, second):
            self.assert_image(owner.store, page.catalog.get(recent.result_id(source)), 'OLD')

    def test_close_drains_active_work_and_discards_pending_preview(self):
        window, tab, owner, page = self.opened(); source = self.source(owner)
        reached, release = threading.Event(), threading.Event()
        original_import = recent.import_image
        def held(*args, **kwargs):
            result = original_import(*args, **kwargs); reached.set()
            if not release.wait(5):raise AssertionError('Close gate was not released')
            return result
        with patch.object(recent, 'import_image', side_effect=held):
            try:
                owner.client.resultsReceived.emit([source]); self.wait_for(reached.is_set)
                page.receive_results([source])
                self.assertFalse(tab.host.close())
            finally:release.set()
            self.idle(tab.document)
            self.wait_for(lambda: getattr(tab.host, '_close_completed', False))
        self.assertFalse(page.pending)
        self.assertFalse(page._preview_receipts)
        self.assertFalse(self.errors)
