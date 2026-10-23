"""Reveal only verified saved files and report failed desktop dispatches."""
import base64
import hashlib
import os
from pathlib import Path
import tempfile
from threading import Event
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from prompt_calculus_studio.connector_service import ConnectorService
from prompt_calculus_studio.connector_store import TransferStore
from prompt_calculus_studio.conversation_archive import canonical, load_archive
from prompt_calculus_studio.conversation_import import Destination, import_selected
from test_connector_capture import sample


class ConnectorRevealTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-connector-reveal-')
        self.addCleanup(self.area.cleanup)
        self.base = Path(self.area.name)
        self.root = self.base / '中文 & # files'
        self.root.mkdir()
        self.store = TransferStore(self.base / 'private')
        self.service = SimpleNamespace(ensure_store=lambda: self.store)
        self.operation = uuid.uuid4().hex
        data = canonical(sample())
        self.store.begin(self.operation, len(data), hashlib.sha256(data).hexdigest())
        self.store.chunk(self.operation, 0, base64.b64encode(data).decode())
        record = self.store.finish(self.operation)
        destination = Destination.choose(self.root)
        self.store.accept(self.operation, destination)
        result = import_selected(load_archive(record['source']), (0,), destination, Event(),
                                 operation_id=self.operation, on_started=self.store.started)
        self.store.complete(self.operation, result)
        self.target = self.store.saved_path(self.operation)
        self.desktop = self.enterContext(patch('prompt_calculus_studio.connector_service.QDesktopServices.openUrl'))
        self.explorer = self.enterContext(patch('prompt_calculus_studio.connector_service.QProcess.startDetached'))

    def reveal(self):
        return ConnectorService.open_saved(self.service, self.operation, reveal=True)

    def explorer_fixture(self):
        windows = self.base / 'Mock Windows'
        windows.mkdir()
        executable = windows / 'explorer.exe'
        executable.write_bytes(b'mocked; never executed')
        self.enterContext(patch.dict(os.environ, SystemRoot=str(windows)))
        return executable

    def test_successful_desktop_dispatch_preserves_unicode_path_and_does_not_launch_twice(self):
        self.desktop.return_value = True
        before = self.target.read_bytes()
        self.assertTrue(self.reveal())
        self.desktop.assert_called_once()
        url = self.desktop.call_args.args[0]
        self.assertTrue(url.isLocalFile())
        self.assertEqual(Path(url.toLocalFile()), self.target.parent)
        self.explorer.assert_not_called()
        self.assertEqual(self.target.read_bytes(), before)

    @unittest.skipUnless(os.name == 'nt', 'Windows Explorer fallback')
    def test_rejected_desktop_dispatch_uses_explicit_explorer_and_one_folder_argument(self):
        executable = self.explorer_fixture()
        self.desktop.return_value = False
        self.explorer.return_value = (True, 123)
        self.assertTrue(self.reveal())
        self.explorer.assert_called_once_with(str(executable), [str(self.target.parent)])

    def test_failed_dispatch_raises_for_the_existing_page_error_handler(self):
        self.explorer_fixture()
        self.desktop.return_value = False
        self.explorer.return_value = (False, 0)
        with self.assertRaisesRegex(OSError, '無法開啟所在資料夾'):
            self.reveal()
        self.desktop.assert_called_once()

    def test_missing_document_is_rejected_before_any_desktop_request(self):
        self.target.unlink()
        with self.assertRaises(ValueError):
            self.reveal()
        self.desktop.assert_not_called()
        self.explorer.assert_not_called()

    def test_replaced_output_folder_is_rejected_even_when_a_document_exists(self):
        folder = self.target.parent
        folder.rename(folder.with_name(folder.name + '-original'))
        folder.mkdir()
        self.target.write_text('unrelated replacement', encoding='utf-8')
        with self.assertRaises(ValueError):
            self.reveal()
        self.desktop.assert_not_called()
        self.explorer.assert_not_called()


if __name__ == '__main__':
    unittest.main()
