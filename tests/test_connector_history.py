"""History visibility preserves transfer receipts, retry identity and files."""
import base64
import hashlib
from pathlib import Path
import tempfile
from threading import Event
import unittest
from unittest.mock import patch
import uuid

from prompt_calculus_studio import connector_store as storage
from prompt_calculus_studio.conversation_archive import canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, import_selected
from test_connector_capture import sample


class HistoryTests(unittest.TestCase):
    def setUp(self):
        area = tempfile.TemporaryDirectory()
        self.addCleanup(area.cleanup)
        self.base = Path(area.name)
        self.root = self.base / 'workspace'
        self.root.mkdir()
        self.store = storage.TransferStore(self.base / 'transport')

    def upload(self, value=None):
        data = canonical(sample() if value is None else value)
        operation = uuid.uuid4().hex
        self.store.begin(operation, len(data), hashlib.sha256(data).hexdigest())
        self.store.chunk(operation, 0, base64.b64encode(data).decode())
        return operation, self.store.finish(operation)

    def write(self):
        operation, record = self.upload()
        destination = Destination.choose(self.root)
        self.store.accept(operation, destination)
        result = import_selected(load_archive(record['source']), (0,), destination, Event(),
                                 operation_id=operation, on_started=self.store.started,
                                 on_reused=self.store.reused)
        self.assertFalse(result.failed)
        return operation, result

    def record(self, state, stamp, *, hidden=False):
        operation = uuid.uuid4().hex
        record = dict(version=1, operation_id=operation, state=state, size=1,
                      sha256='0' * 64, received=0, next_index=0, updated_at=stamp)
        if hidden:
            record['history_hidden'] = True
        storage.atomic_json(self.store.path(operation), record)
        return operation

    def test_hidden_saved_entry_keeps_receipt_files_order_and_retry_outcome(self):
        operation, result = self.write()
        self.store.complete(operation, result)
        target = result.completed[0]
        target.write_text('保留人工編輯', encoding='utf-8')
        files = {p: p.read_bytes() for p in target.parent.rglob('*') if p.is_file()}
        before = self.store.get(operation)

        self.assertTrue(self.store.hide_history(operation))
        restarted = storage.TransferStore(self.store.directory)
        self.assertEqual(restarted.history_records(), [])
        self.assertFalse(restarted.hide_history(operation))
        after = restarted.get(operation)
        self.assertEqual(after.pop('history_hidden'), True)
        self.assertEqual(after, before)
        self.assertEqual(len(restarted.records()), 1)
        self.assertEqual(restarted.saved_path(operation), target)
        self.assertEqual(restarted.begin(operation, before['size'], before['sha256'])['state'], 'committed')
        self.assertEqual(restarted.finish(operation)['state'], 'committed')
        with self.assertRaises(ValueError):
            restarted.accept(operation, Destination.choose(self.root))
        self.assertEqual({p: p.read_bytes() for p in target.parent.rglob('*') if p.is_file()}, files)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_hiding_rows_only_affects_terminal_history_and_does_not_reorder_operations(self):
        states = ('committed', 'cancelled', 'failed', 'unknown', 'receiving', 'previewing', 'committing')
        operations = [self.record(state, index) for index, state in enumerate(states)]
        before = {row['operation_id']: row for row in self.store.records()}
        self.assertEqual(sum(self.store.hide_history(operation) for operation in operations), 3)
        restarted = storage.TransferStore(self.store.directory)
        self.assertEqual([row['operation_id'] for row in restarted.history_records()], operations[:2:-1])
        self.assertEqual(sum(restarted.hide_history(operation) for operation in operations), 0)
        self.assertFalse(restarted.hide_history(uuid.uuid4().hex))
        for row in restarted.records():
            hidden = row.pop('history_hidden', False)
            self.assertEqual(hidden, row['state'] in storage.HIDEABLE)
            self.assertEqual(row, before[row['operation_id']])
            if not hidden:
                self.assertFalse(restarted.hide_history(row['operation_id']))

    def test_unknown_remains_visible_and_recoverable_even_with_stale_hidden_flag(self):
        operation, result = self.write()
        record = self.store.get(operation)
        record.update(state='unknown', history_hidden=True)
        self.store.put(record)
        self.assertFalse(self.store.hide_history(operation))
        self.assertEqual([row['operation_id'] for row in self.store.history_records()], [operation])
        self.assertTrue(Path(record['source']).is_file())
        restarted = storage.TransferStore(self.store.directory)
        restarted.recover()
        self.assertEqual(restarted.get(operation)['state'], 'committed')
        self.assertEqual(restarted.saved_path(operation), result.completed[0])
        self.assertEqual(restarted.history_records(), [])

    def test_hidden_cancellation_still_blocks_late_begin_and_finish(self):
        operation = uuid.uuid4().hex
        self.store.cancel(operation)
        self.assertTrue(self.store.hide_history(operation))
        restarted = storage.TransferStore(self.store.directory)
        data = canonical(sample())
        self.assertEqual(restarted.begin(operation, len(data), hashlib.sha256(data).hexdigest())['state'], 'cancelled')
        self.assertEqual(restarted.finish(operation)['state'], 'cancelled')
        with self.assertRaises(ValueError):
            restarted.chunk(operation, 0, base64.b64encode(data).decode())
        self.assertEqual(restarted.history_records(), [])
        self.assertEqual(list(self.root.iterdir()), [])

    def test_hiding_history_never_frees_receipt_admission_capacity(self):
        for index in range(1000):
            operation = f'{index:032x}'
            self.store.path(operation).write_bytes(canonical(dict(
                version=1, operation_id=operation, state='cancelled', size=0,
                sha256='0' * 64, received=0, next_index=0, updated_at=index,
                history_hidden=True)))
        self.assertEqual(self.store.history_records(), [])
        self.assertEqual(len(self.store.records()), 1000)
        with self.assertRaises(ValueError):
            self.store.begin(uuid.uuid4().hex, 1, '0' * 64)
        self.assertEqual(len(self.store.records()), 1000)
        self.assertEqual(self.store.begin('0' * 32, 1, '1' * 64)['state'], 'cancelled')

    def test_atomic_history_failure_preserves_record_and_reports_no_success(self):
        operation = self.record('failed', 23)
        before = self.store.path(operation).read_bytes()
        with patch.object(storage.os, 'replace', side_effect=OSError('isolated write failure')):
            with self.assertRaises(OSError):
                self.store.hide_history(operation)
        self.assertEqual(self.store.path(operation).read_bytes(), before)
        self.assertEqual(len(self.store.history_records()), 1)
        self.assertEqual(list(self.store.directory.glob('*.tmp')), [])
        self.assertTrue(self.store.hide_history(operation))

if __name__ == '__main__':
    unittest.main()
