"""Direct destination acceptance and real asynchronous writer, isolated/offscreen."""
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import subprocess
from threading import Event
import time
import unittest
from unittest.mock import patch
import uuid

from PySide6.QtGui import QCloseEvent

from prompt_calculus_studio.conversation_archive import canonical
from prompt_calculus_studio.connector_destinations import DestinationCatalog
from test_connector_capture import sample
import test_connector_runtime as fixture


class DirectSaveTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ConnectorInterfaceTests.setUpClass.__func__(cls)

    setUp = fixture.ConnectorInterfaceTests.setUp
    events = fixture.ConnectorInterfaceTests.events
    wait_job = fixture.ConnectorInterfaceTests.wait_job
    off_thread = fixture.ConnectorInterfaceTests.off_thread

    def tearDown(self):
        self.wait_direct()
        fixture.ConnectorInterfaceTests.tearDown(self)

    def wait_direct(self):
        deadline = time.monotonic() + 15
        while self.service.writing and time.monotonic() < deadline:
            self.app.processEvents()
            time.sleep(.005)
        self.assertFalse(self.service.writing, self.service.store.records())
        self.assertFalse(self.service.direct_jobs)
        self.events()

    def received(self, *, destination_id=None, value=None):
        data = canonical(value or sample())
        operation = uuid.uuid4().hex
        if destination_id is None:
            destination_id = self.service.dispatch(dict(command='destinations'))['default_id']
        begin = dict(command='begin', operation_id=operation, destination_id=destination_id,
                     size=len(data), sha256=hashlib.sha256(data).hexdigest())
        self.assertEqual(self.service.dispatch(begin)['state'], 'receiving')
        self.service.dispatch(dict(command='chunk', operation_id=operation, index=0,
                                   data=base64.b64encode(data).decode()))
        return operation, begin

    def finish(self, operation):
        return self.service.dispatch(dict(command='finish', operation_id=operation))

    def status(self, operation):
        return self.service.dispatch(dict(command='status', operation_id=operation))

    def test_selected_folder_saves_without_dialog_focus_or_open_and_replays_once(self):
        listing = self.service.dispatch(dict(command='destinations'))
        folder = next(row for row in listing['folders'] if row['name'] == 'Folder')
        operation, begin = self.received(destination_id=folder['id'])
        with patch.object(self.service, 'show_preview') as preview, \
                patch.object(self.w, 'activateWindow') as activate, \
                patch.object(self.w, 'raise_') as raise_window, \
                patch.object(self.w, 'open_path') as open_path:
            self.assertIn(self.finish(operation)['state'], ('committing', 'committed'))
            self.wait_direct()
            self.assertEqual(self.finish(operation)['state'], 'committed')
            # An operation retry uses its persistent binding after catalog reset.
            self.service.destinations = DestinationCatalog(lambda: list(self.w.locations.roots),
                lambda: self.service.settings['destination'], self.service.check_isolation)
            self.assertEqual(self.service.dispatch(begin)['state'], 'committed')
            preview.assert_not_called(); activate.assert_not_called()
            raise_window.assert_not_called(); open_path.assert_not_called()
        self.assertEqual(self.service.dialogs, {})
        target = self.service.store.saved_path(operation)
        self.assertEqual(target.parent.parent, self.root / 'Folder')
        self.assertEqual(len(list((self.root / 'Folder').glob('*/匯入完成.json'))), 1)
        original = json.loads((target.parent / '原始對話.json').read_text(encoding='utf-8-sig'))
        self.assertEqual(original['pcs_capture']['scope'], sample()['scope'])
        self.assertIn(sample()['messages'][0]['content'], target.read_text(encoding='utf-8-sig'))

    def test_replaced_destination_fails_before_any_writer_or_preview(self):
        listing = self.service.dispatch(dict(command='destinations'))
        folder = next(row for row in listing['folders'] if row['name'] == 'Folder')
        operation, _ = self.received(destination_id=folder['id'])
        (self.root / 'Folder').rename(self.root / 'original-folder')
        (self.root / 'Folder').mkdir()
        with patch.object(self.service, 'show_preview') as preview:
            result = self.finish(operation)
            self.events()
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error_code'], 'destination_changed')
        self.assertFalse(self.service.writing)
        preview.assert_not_called()
        self.assertEqual(list(self.root.glob('**/匯入完成.json')), [])

    def test_removed_external_destination_is_not_silently_replaced_by_default(self):
        external = Path(self.area.name) / 'external'; external.mkdir()
        self.service.set_destination(external)
        operation, _ = self.received()
        self.service.set_destination(self.root)
        result = self.finish(operation)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual(result['error_code'], 'destination_changed')
        self.assertEqual(list(external.iterdir()), [])

    def test_queued_save_status_cancel_and_workspace_close_preserve_writer_owner(self):
        from prompt_calculus_studio.connector_service import import_selected
        entered, release = Event(), Event()

        def hold_writer(*args, **kwargs):
            entered.set()
            if not release.wait(8):
                raise TimeoutError('test writer gate timed out')
            return import_selected(*args, **kwargs)

        with patch('prompt_calculus_studio.connector_service.import_selected', side_effect=hold_writer):
            first, _ = self.received()
            self.finish(first)
            try:
                self.assertTrue(entered.wait(3))
                second_value = sample(); second_value['title'] = 'Second transfer'
                second, _ = self.received(value=second_value)
                self.finish(second)
                self.assertEqual(self.status(first)['state'], 'committing')
                self.assertEqual(self.status(second)['state'], 'committing')
                self.assertEqual(self.service.writing, {first, second})
                self.service.dispatch(dict(command='cancel', operation_id=second))
                with patch.object(self.w, 'show_notice'):
                    close = QCloseEvent(); self.w.closeEvent(close)
                self.assertFalse(close.isAccepted())
                with self.assertRaises(ValueError):
                    self.service.stop()
            finally:
                release.set()
            self.wait_direct()
        self.assertEqual(self.status(first)['state'], 'committed')
        self.assertEqual(self.status(second)['state'], 'cancelled')
        self.assertEqual(self.finish(second)['state'], 'cancelled')
        self.assertEqual(len(list(self.root.glob('*/匯入完成.json'))), 1)

    def test_direct_save_waits_for_legacy_writer_and_preserves_queue(self):
        # The compact dialog owns its worker separately; its operation must
        # drain before any new direct writer starts.
        legacy = uuid.uuid4().hex
        self.service.writing.add(legacy)
        operation, _ = self.received()
        self.finish(operation)
        try:
            self.events()
            self.assertFalse(self.service.direct_jobs[operation].submitted)
            self.assertEqual(self.status(operation)['state'], 'committing')
            self.assertEqual(list(self.root.glob('*/匯入完成.json')), [])
        finally:
            self.service.write_finished(legacy)
        self.wait_direct()
        self.assertEqual(self.status(operation)['state'], 'committed')

    def test_queued_destination_replacement_remains_a_known_failure(self):
        listing = self.service.dispatch(dict(command='destinations'))
        folder = next(row for row in listing['folders'] if row['name'] == 'Folder')
        legacy = uuid.uuid4().hex
        self.service.writing.add(legacy)
        operation, _ = self.received(destination_id=folder['id'])
        self.finish(operation)
        try:
            (self.root / 'Folder').rename(self.root / 'preserved-folder')
            (self.root / 'Folder').mkdir()
        finally:
            self.service.write_finished(legacy)
        self.wait_direct()
        self.assertEqual(self.status(operation)['state'], 'failed')
        self.assertEqual(self.status(operation)['error_code'], 'destination_changed')
        self.assertEqual(list(self.root.glob('**/匯入完成.json')), [])

    def test_queued_destination_removal_preserves_reselection_error(self):
        listing = self.service.dispatch(dict(command='destinations'))
        folder = next(row for row in listing['folders'] if row['name'] == 'Folder')
        legacy = uuid.uuid4().hex
        self.service.writing.add(legacy)
        operation, _ = self.received(destination_id=folder['id'])
        self.finish(operation)
        try:
            (self.root / 'Folder').rmdir()
        finally:
            self.service.write_finished(legacy)
        self.wait_direct()
        self.assertEqual(self.status(operation)['state'], 'failed')
        self.assertEqual(self.status(operation)['error_code'], 'destination_changed')
        self.assertFalse((self.root / 'Folder').exists())

    def test_failed_ledger_ack_recovers_writer_receipt_without_second_save(self):
        operation, _ = self.received()
        original_put = self.service.store.put
        failed = []

        def once(record):
            if record['state'] == 'committed' and not failed:
                failed.append(True)
                raise OSError('synthetic receipt acknowledgement failure')
            return original_put(record)

        with patch.object(self.service.store, 'put', side_effect=once):
            self.finish(operation)
            self.wait_direct()
        self.assertEqual(failed, [True])
        self.assertEqual(self.status(operation)['state'], 'committed')
        self.assertTrue(self.service.store.saved_path(operation).is_file())
        self.assertEqual(len(list(self.root.glob('*/匯入完成.json'))), 1)

    def test_cancel_before_finish_and_late_finish_never_start_writer(self):
        operation, _ = self.received()
        self.assertEqual(self.service.dispatch(dict(command='cancel', operation_id=operation))['state'], 'cancelled')
        with patch('prompt_calculus_studio.connector_service.import_selected') as writer:
            self.assertEqual(self.finish(operation)['state'], 'cancelled')
            self.events()
        writer.assert_not_called()

    @unittest.skipUnless(os.environ.get('PCS_CONNECTOR_HOST'), 'compiled native host supplied separately')
    def test_compiled_host_destinations_transfer_and_receipt_use_actual_qt_pipe(self):
        from tools.connector_host import ALLOWED_ORIGIN, read_frame, write_frame

        def request(command, **fields):
            value = dict(request_id=uuid.uuid4().hex, command=command, **fields)
            outgoing = io.BytesIO(); write_frame(outgoing, value)
            child = self.off_thread(lambda: subprocess.run(
                [os.environ['PCS_CONNECTOR_HOST'], ALLOWED_ORIGIN], input=outgoing.getvalue(),
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=20,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0))
            self.assertEqual(child.returncode, 0, child.stderr)
            self.assertEqual(child.stderr, b'')
            incoming = io.BytesIO(child.stdout); result = read_frame(incoming)
            self.assertIsNone(read_frame(incoming))
            self.assertEqual(result['request_id'], value['request_id'])
            self.assertTrue(result['ok'], result)
            return result['result']

        live = request('hello')['instances']
        instance = next(row for row in live if row['instance_id'] == self.service.instance_id)
        self.assertIn('save_destination_v1', instance['capabilities'])
        listing = request('destinations', instance_id=instance['instance_id'])
        folder_id = next(row['id'] for row in listing['folders'] if row['name'] == 'Folder')
        selected = request('destinations', instance_id=instance['instance_id'], folder_id=folder_id)
        self.assertEqual(selected['current']['path'], str(self.root / 'Folder'))
        capture = sample(); capture['title'] = 'Native 中文直存'
        data, operation = canonical(capture), uuid.uuid4().hex
        fields = dict(instance_id=instance['instance_id'], operation_id=operation)
        self.assertEqual(request('begin', destination_id=folder_id, size=len(data),
                                 sha256=hashlib.sha256(data).hexdigest(), **fields)['state'], 'receiving')
        request('chunk', index=0, data=base64.b64encode(data).decode(), **fields)
        self.assertIn(request('finish', **fields)['state'], ('committing', 'committed'))
        self.wait_direct()
        result = request('status', **fields)
        self.assertEqual(result['state'], 'committed')
        target = Path(result['path'])
        self.assertEqual(target.parent.parent, self.root / 'Folder')
        self.assertIn('Native 中文直存', target.read_text(encoding='utf-8-sig'))
        self.assertEqual(self.service.dialogs, {})
        self.assertEqual(request('finish', **fields)['path'], str(target))


if __name__ == '__main__':
    unittest.main()
