"""Synthetic transfer persistence, receipt identity and bounded spool recovery."""
import base64
import hashlib
import json
from pathlib import Path
import shutil
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from prompt_calculus_studio import connector_store as storage
from prompt_calculus_studio.conversation_archive import canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, RECEIPT, import_selected
from test_connector_capture import sample


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.base = Path(area.name)
        self.root = self.base / 'workspace'; self.root.mkdir()
        self.destination = Destination.choose(self.root)
        self.store = storage.TransferStore(self.base / 'transport')

    def upload(self, value=None):
        data = canonical(sample() if value is None else value)
        operation = uuid.uuid4().hex
        self.store.begin(operation, len(data), hashlib.sha256(data).hexdigest())
        self.store.chunk(operation, 0, base64.b64encode(data).decode())
        record = self.store.finish(operation)
        return operation, record

    def write(self, value=None):
        operation, record = self.upload(value)
        self.store.accept(operation, self.destination)
        result = import_selected(load_archive(record['source']), (0,), self.destination, Event(),
                                 operation_id=operation, on_started=self.store.started,
                                 on_reused=self.store.reused)
        self.assertFalse(result.failed)
        return operation, result

    def restart(self):
        restarted = storage.TransferStore(self.store.directory)
        restarted.recover()
        return restarted

    def test_many_generated_sources_do_not_hide_later_ledgers_or_recovery(self):
        for index in range(600):
            operation = f'{index:032x}'
            self.store.path(operation).write_bytes(canonical(dict(
                version=1, operation_id=operation, state='cancelled', size=0,
                sha256='0' * 64, received=0, next_index=0, updated_at=index)))
            self.store.path(operation, '.source.json').write_text('{}')
        operation, result = self.write()
        # Reintroduce generated files: enumerating *.json before filtering was lossy.
        for index in range(600):
            self.store.path(f'{index:032x}', '.source.json').write_text('{}')
        self.assertEqual(len(self.store.records()), 601)
        restarted = self.restart()
        self.assertEqual(restarted.get(operation)['state'], 'committed')
        self.assertEqual(restarted.saved_path(operation), result.completed[0])

    def test_replaced_owned_folder_cannot_be_adopted_as_success(self):
        operation, result = self.write()
        target = result.completed[0]
        before = self.store.get(operation)['owned_identity']
        kept = self.base / 'preserved-original'
        target.parent.rename(kept)
        shutil.copytree(kept, target.parent)
        with self.assertRaisesRegex(ValueError, '保存位置已改變'):
            self.store.complete(operation, result)
        self.assertEqual(self.store.get(operation)['owned_identity'], before)
        self.assertEqual(self.restart().get(operation)['state'], 'unknown')
        self.assertTrue((kept / '對話.md').exists())

    def test_wrong_operation_or_source_receipt_cannot_complete_or_recover(self):
        for field in ('operation', 'source_id', 'original_hash', 'content_hash'):
            with self.subTest(field=field):
                value = sample(); value['conversation_id'] = 'chat-' + field
                operation, result = self.write(value)
                path = result.completed[0].parent / RECEIPT
                original = path.read_bytes()
                receipt = json.loads(original)
                receipt[field] = uuid.uuid4().hex if field == 'operation' else 'other-source'
                path.write_bytes(canonical(receipt))
                with self.assertRaises(ValueError):
                    self.store.complete(operation, result)
                self.assertEqual(self.restart().get(operation)['state'], 'unknown')
                path.write_bytes(original)
                self.assertEqual(self.restart().get(operation)['state'], 'committed')

    def test_changed_preview_or_original_is_not_a_matching_source(self):
        operation, record = self.upload()
        path = Path(record['source'])
        original = path.read_bytes()
        value = json.loads(original); value['title'] = 'changed'
        path.write_bytes(canonical(value))
        with self.assertRaisesRegex(ValueError, '預覽來源已改變'):
            self.store.accept(operation, self.destination)
        self.assertEqual(list(self.root.iterdir()), [])
        path.write_bytes(original)
        self.store.accept(operation, self.destination)
        result = import_selected(load_archive(path), (0,), self.destination, Event(),
                                 operation_id=operation, on_started=self.store.started)
        saved = result.completed[0].parent / '原始對話.json'
        saved.write_bytes(canonical(value))
        with self.assertRaisesRegex(ValueError, '已保存來源'):
            self.store.complete(operation, result)

    def test_skipped_then_crashed_recovers_old_receipt_and_edited_markdown(self):
        first, original = self.write()
        self.store.complete(first, original)
        target = original.completed[0]
        target.write_text('人工保留內容', encoding='utf-8')
        operation, result = self.write()
        self.assertEqual(result.skipped, [target])
        before = self.store.get(operation)
        self.assertTrue(before['reused'])
        self.assertEqual(before['receipt_operation'], first)
        self.assertNotEqual(operation, first)
        restarted = self.restart()  # No complete() call: process died after skip.
        self.assertEqual(restarted.get(operation)['state'], 'committed')
        self.assertEqual(restarted.saved_path(operation), target)
        self.assertEqual(target.read_text(encoding='utf-8'), '人工保留內容')
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_legacy_receipt_without_operation_can_be_reused(self):
        first, result = self.write()
        self.store.complete(first, result)
        path = result.completed[0].parent / RECEIPT
        receipt = json.loads(path.read_bytes()); receipt.pop('operation')
        path.write_bytes(canonical(receipt))
        operation, duplicate = self.write()
        self.assertEqual(duplicate.skipped, result.completed)
        self.store.complete(operation, duplicate)
        self.assertIsNone(self.store.get(operation)['receipt_operation'])
        self.assertEqual(self.store.saved_path(operation), result.completed[0])

    def test_reuse_callback_failure_does_not_create_or_claim_a_copy(self):
        first, result = self.write()
        self.store.complete(first, result)
        operation, record = self.upload()
        self.store.accept(operation, self.destination)
        archive = load_archive(record['source'])
        with patch.object(self.store, 'put', side_effect=OSError('ledger unavailable')):
            duplicate = import_selected(archive, (0,), self.destination, Event(),
                                        operation_id=operation, on_reused=self.store.reused)
        self.assertFalse(duplicate.skipped)
        self.assertEqual(len(duplicate.failed), 1)
        self.assertNotIn('owned_path', self.store.get(operation))
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_cancel_before_begin_blocks_late_payload_and_survives_restart(self):
        operation = uuid.uuid4().hex
        self.store.cancel(operation)
        restarted = self.restart()
        data = canonical(sample())
        self.assertEqual(restarted.begin(operation, len(data), hashlib.sha256(data).hexdigest())['state'], 'cancelled')
        with self.assertRaises(ValueError):
            restarted.chunk(operation, 0, base64.b64encode(data).decode())
        self.assertEqual(restarted.finish(operation)['state'], 'cancelled')
        self.assertFalse(restarted.path(operation, '.part').exists())
        self.assertEqual(list(self.root.iterdir()), [])

    def test_terminal_cleanup_is_bounded_and_never_removes_unknown_or_saved_files(self):
        operation, result = self.write()
        self.assertFalse(self.store.path(operation, '.part').exists())
        source = self.store.path(operation, '.source.json')
        receipt = result.completed[0].parent / RECEIPT
        original_receipt = receipt.read_bytes()
        receipt.write_text('{}')
        self.store.recover()
        self.assertEqual(self.store.get(operation)['state'], 'unknown')
        self.store.cancel(operation)
        self.assertEqual(self.store.get(operation)['state'], 'unknown')
        self.assertTrue(source.exists())
        receipt.write_bytes(original_receipt)
        self.store.recover()
        self.assertFalse(source.exists())
        self.assertTrue(result.completed[0].exists())
        cancelled, record = self.upload()
        cancelled_source = Path(record['source'])
        actual_unlink = Path.unlink
        def deny_source(path, *args, **kwargs):
            if path == cancelled_source:
                raise PermissionError('reader still closing')
            return actual_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', deny_source):
            self.assertEqual(self.store.cancel(cancelled)['state'], 'cancelled')
        self.assertTrue(cancelled_source.exists())
        data = canonical(sample())
        self.store.begin(uuid.uuid4().hex, len(data), hashlib.sha256(data).hexdigest())
        self.assertFalse(cancelled_source.exists())
        self.assertTrue(result.completed[0].exists())

    def test_total_spool_budget_blocks_reservations_and_normalized_growth(self):
        data = canonical(sample())
        with patch.object(storage, 'MAX_SPOOL', len(data) * 2 - 1):
            first = uuid.uuid4().hex
            self.store.begin(first, len(data), hashlib.sha256(data).hexdigest())
            with self.assertRaisesRegex(ValueError, '暫存空間'):
                self.store.begin(uuid.uuid4().hex, len(data), hashlib.sha256(data).hexdigest())
            self.store.chunk(first, 0, base64.b64encode(data).decode())
            with self.assertRaisesRegex(ValueError, '暫存空間'):
                self.store.finish(first)
            self.assertFalse(self.store.path(first, '.source.json').exists())
            self.store.cancel(first)
            self.assertFalse(self.store.path(first, '.part').exists())


if __name__ == '__main__':
    unittest.main()
