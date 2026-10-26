"""Bounded local discovery contracts; synthetic GGUF headers, no model engine."""
import copy
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_ai.library import ModelLibrary, ScanResult, default_model_folder, scan_folder
from prompt_calculus_studio.local_ai.registry import ModelRegistry, inspect_model
from prompt_calculus_studio.local_ai.service import LocalAIService


def fixture(folder, name='sample', extra=b''):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / (name + '.gguf')
    path.write_bytes(b'GGUF' + (3).to_bytes(4, 'little') + extra)
    return path


class RegistryDiscoveryContracts(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.registry = ModelRegistry(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def test_legacy_registry_and_optional_folder_round_trip(self):
        first = self.registry.register(fixture(self.root))
        raw = json.loads(self.registry.path.read_text(encoding='utf-8'))
        raw.pop('model_folder')
        self.registry.path.write_text(json.dumps(raw), encoding='utf-8')
        legacy = ModelRegistry(self.root / 'data')
        self.assertEqual(legacy.model_folder, '')
        legacy.update(model_folder=str(self.root))
        reopened = ModelRegistry(self.root / 'data')
        self.assertEqual(reopened.model_folder, str(self.root))
        self.assertEqual(reopened.selected_model, first['id'])

    def test_bulk_merge_preserves_manual_selection_and_unscanned_records(self):
        first = self.registry.register(fixture(self.root / 'manual', 'a'))
        b = inspect_model(fixture(self.root / 'scanned', 'b'))
        c = inspect_model(fixture(self.root / 'scanned', 'c'))
        before = copy.deepcopy(self.registry.models)
        self.assertEqual(self.registry.merge_scanned([b, c], baseline=before), (2, 0))
        self.assertEqual(self.registry.selected_model, first['id'])
        self.assertEqual(len(self.registry.models), 3)
        with patch('prompt_calculus_studio.local_ai.registry.atomic_json') as write:
            self.assertEqual(self.registry.merge_scanned([b, c, b], baseline=self.registry.models), (0, 0))
            write.assert_not_called()
        self.assertEqual(self.registry.models[0], first)

    def test_changed_weight_gets_new_identity_and_selected_follows(self):
        path = fixture(self.root)
        first = self.registry.register(path)
        baseline = copy.deepcopy(self.registry.models)
        path.write_bytes(path.read_bytes() + b'new weight fixture')
        self.assertEqual(self.registry.merge_scanned([inspect_model(path)], baseline=baseline), (0, 1))
        self.assertNotEqual(self.registry.selected_model, first['id'])
        self.assertEqual(self.registry.models[0]['id'], self.registry.selected_model)
        self.assertEqual(first['size'], 8)  # A previous request snapshot is unchanged.

    def test_manual_re_registration_wins_over_older_discovery(self):
        path = fixture(self.root)
        self.registry.register(path)
        baseline = copy.deepcopy(self.registry.models)
        stale = inspect_model(path)
        path.write_bytes(path.read_bytes() + b'newer')
        fresh = self.registry.register(path)
        with patch('prompt_calculus_studio.local_ai.registry.atomic_json') as write:
            self.assertEqual(self.registry.merge_scanned([stale], baseline=baseline), (0, 0))
            write.assert_not_called()
        self.assertEqual(self.registry.models, [fresh])
        self.assertEqual(self.registry.selected_model, fresh['id'])

    def test_manual_first_registration_wins_over_older_new_candidate(self):
        path = fixture(self.root)
        stale = inspect_model(path)
        path.write_bytes(path.read_bytes() + b'newer')
        fresh = self.registry.register(path)
        self.assertEqual(self.registry.merge_scanned([stale], baseline=[]), (0, 0))
        self.assertEqual(self.registry.models, [fresh])

    def test_oversized_merge_rolls_back_memory_and_disk(self):
        self.registry.register(fixture(self.root, 'first'))
        before = self.registry.path.read_bytes()
        models = copy.deepcopy(self.registry.models)
        found = inspect_model(fixture(self.root, 'second'))
        found['name'] = 'x' * (2 * 1024 * 1024)
        with self.assertRaises(ValueError):
            self.registry.merge_scanned([found])
        self.assertEqual(self.registry.path.read_bytes(), before)
        self.assertEqual(self.registry.models, models)

    def test_invalid_folder_setting_preserves_existing_registry(self):
        self.registry.register(fixture(self.root))
        before = self.registry.path.read_bytes()
        for value in ('relative/path', None, 3, 'invalid\x00path'):
            if value is None:  # None means unchanged in the update API.
                continue
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.registry.update(model_folder=value)
        self.assertEqual(self.registry.path.read_bytes(), before)

    def test_verified_managed_worktree_and_frozen_defaults(self):
        workspace = self.root / 'application'
        (workspace / 'repository').mkdir(parents=True)
        source = workspace / 'worktrees' / 'branch'
        source.mkdir(parents=True)
        self.assertEqual(default_model_folder(self.registry, source_root=source, frozen=False),
                         workspace / 'models' / 'local-chat')
        exe = self.root / 'portable' / 'PCS.exe'
        self.assertEqual(default_model_folder(self.registry, executable=exe, frozen=True),
                         exe.parent / 'models' / 'local-chat')

    def test_scanner_is_recursive_bounded_and_rejects_unsupported_files(self):
        good = fixture(self.root / 'models' / 'sub', 'good')
        fixture(self.root / 'models', 'shard-00001-of-00002')
        (self.root / 'models' / 'bad.gguf').write_bytes(b'not a model')
        (self.root / 'models' / 'notes.md').write_text('not weights')
        result = scan_folder(self.root / 'models', Event(), lambda *_: None)
        self.assertEqual([r['path'] for r in result.records], [str(good.resolve())])
        self.assertEqual(result.skipped, 2)
        bounded = scan_folder(self.root / 'models', Event(), lambda *_: None, max_entries=1)
        self.assertEqual(bounded.checked, 1)
        self.assertTrue(bounded.limit)
        cancel = Event()
        cancel.set()
        self.assertTrue(scan_folder(self.root / 'models', cancel, lambda *_: None).cancelled)

    def test_empty_pending_directories_obey_cancel_and_deadline(self):
        root = self.root / 'empty-tree'
        for name in ('one', 'two', 'three'):
            (root / name).mkdir(parents=True)
        actual_scandir = os.scandir
        for mode in ('cancel', 'deadline'):
            with self.subTest(mode=mode):
                cancel, calls, clock = Event(), [], [0.]

                def scandir(path):
                    calls.append(Path(path))
                    if Path(path) != root:
                        if mode == 'cancel':
                            cancel.set()
                        else:
                            clock[0] = 10.
                    return actual_scandir(path)

                with patch('prompt_calculus_studio.local_ai.library.os.scandir', side_effect=scandir), \
                     patch('prompt_calculus_studio.local_ai.library.time.monotonic', side_effect=lambda: clock[0]):
                    result = scan_folder(root, cancel, lambda *_: None, max_seconds=5)
                self.assertEqual(len(calls), 2)  # Root and first empty child only.
                self.assertEqual(result.cancelled, mode == 'cancel')
                self.assertEqual(bool(result.limit), mode == 'deadline')

    @unittest.skipUnless(os.name == 'nt', 'Windows canonical case behavior')
    def test_windows_case_variant_does_not_duplicate_model(self):
        path = fixture(self.root / 'models', 'mixed')
        first = self.registry.register(path)
        snapshot = inspect_model(path)
        snapshot['path'] = snapshot['path'].upper()
        self.assertEqual(self.registry.merge_scanned([snapshot], baseline=self.registry.models), (0, 0))
        self.assertEqual(self.registry.models, [first])


class LibraryLifecycleContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = LocalAIService(self.root / 'data', runtime_factory=self.unexpected_runtime)
        self.library = self.service._model_library
        self.library.default_folder = str(self.root / 'default-models')
        self.gates = []

    @staticmethod
    def unexpected_runtime():
        raise AssertionError('Folder operations must never construct an inference engine')

    def tearDown(self):
        self.service.shutdown()
        for gate in self.gates:
            gate.set()
        self.wait_for(lambda: self.library._worker is None)
        self.assertTrue(self.service.shutdown())
        delete(self.service)
        self.temp.cleanup()

    def wait_for(self, predicate, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail(f'Timed out; folder={self.library.state}; inference={self.service.state}')

    def test_construct_and_browse_never_scan_or_create_default(self):
        calls = []
        def scanner(*args):
            calls.append(args)
            return ScanResult()
        self.library._scanner = scanner
        self.assertFalse(Path(self.service.model_folder).exists())
        self.assertFalse(self.service.model_folder_busy)
        self.app.processEvents()
        self.assertEqual(calls, [])
        self.assertIsNone(self.service._worker)
        self.assertTrue(self.service.refresh_model_folder())
        self.wait_for(lambda: self.library._worker is None)
        self.assertEqual(len(calls), 1)

    def test_scan_preserves_selected_and_loaded_then_changed_file_needs_reprepare(self):
        path = fixture(self.root / 'models', 'a')
        first = self.service.register_model(path)
        self.service._loaded_model = first['id']
        self.service._loaded_settings = self.service.settings
        self.service.state = 'ready'
        fixture(self.root / 'models', 'b')
        self.assertTrue(self.service.set_model_folder(path.parent))
        self.wait_for(lambda: not self.service.model_folder_busy)
        self.assertEqual(self.service.selected_model, first['id'])
        self.assertEqual(self.service.loaded_model, first['id'])
        self.assertIsNone(self.service._worker)
        path.write_bytes(path.read_bytes() + b'replaced')
        self.assertTrue(self.service.refresh_model_folder())
        self.wait_for(lambda: not self.service.model_folder_busy)
        self.assertNotEqual(self.service.selected_model, first['id'])
        self.assertEqual(self.service.loaded_model, first['id'])
        self.assertTrue(self.service.needs_reload)

    def test_waiting_result_is_revoked_when_folder_changes(self):
        first = fixture(self.root / 'first', 'a')
        second = fixture(self.root / 'second', 'b')
        self.service.state = 'generating'
        self.assertTrue(self.service.set_model_folder(first.parent))
        self.wait_for(lambda: self.library.state == 'waiting')
        self.assertEqual(self.service.models, [])
        self.assertTrue(self.service.set_model_folder(second.parent))
        self.wait_for(lambda: self.library.state == 'waiting' and self.library._worker is None)
        self.service.state = 'unloaded'
        self.service.changed.emit()
        self.assertEqual([m['path'] for m in self.service.models], [str(second)])

    def test_waiting_result_cancel_never_accepts_later(self):
        path = fixture(self.root / 'models')
        self.service.state = 'generating'
        self.assertTrue(self.service.set_model_folder(path.parent))
        self.wait_for(lambda: self.library.state == 'waiting')
        self.assertTrue(self.service.cancel_model_folder_scan())
        self.service.state = 'unloaded'
        self.service.changed.emit()
        self.assertFalse(self.service.model_folder_busy)
        self.assertEqual(self.service.models, [])

    def test_changed_folder_discards_late_result_and_runs_only_latest(self):
        first = fixture(self.root / 'first', 'a')
        second = fixture(self.root / 'second', 'b')
        entered, release = Event(), Event()
        self.gates.append(release)
        calls = []

        def scanner(folder, cancel, progress):
            calls.append(folder)
            if folder == str(first.parent):
                entered.set()
                release.wait(4)
                return ScanResult(records=[inspect_model(first)])
            return ScanResult(records=[inspect_model(second)])

        self.library._scanner = scanner
        self.assertTrue(self.service.set_model_folder(first.parent))
        self.wait_for(entered.is_set)
        self.assertTrue(self.service.set_model_folder(second.parent))
        self.assertEqual(self.library.state, 'cancelling')
        release.set()
        self.wait_for(lambda: not self.library.busy)
        self.assertEqual(calls, [str(first.parent), str(second.parent)])
        self.assertEqual([m['path'] for m in self.service.models], [str(second)])

    def test_root_failure_preserves_registry_and_configured_folder(self):
        first = self.service.register_model(fixture(self.root / 'manual'))
        missing = self.root / 'missing'
        self.assertTrue(self.service.set_model_folder(missing))
        self.wait_for(lambda: not self.library.busy)
        self.assertEqual(self.library.state, 'error')
        self.assertEqual(self.service.models, [first])
        self.assertEqual(self.service.model_folder, str(missing))
        self.assertEqual(self.service.error, '')

    def test_partial_scan_remains_visible_as_warning(self):
        path = fixture(self.root / 'models')
        self.library._scanner = lambda *_: ScanResult(records=[inspect_model(path)], skipped=1, limit='partial')
        self.assertTrue(self.service.set_model_folder(path.parent))
        self.wait_for(lambda: not self.library.busy)
        self.assertEqual(self.service.model_folder_state, 'warning')
        self.assertIn('partial', self.service.model_folder_message)
        self.assertEqual(len(self.service.models), 1)

    def test_shutdown_retains_scanner_and_aborted_close_never_restarts(self):
        path = fixture(self.root / 'models')
        entered, release = Event(), Event()
        self.gates.append(release)
        calls = []

        def scanner(*_):
            calls.append(True)
            entered.set()
            release.wait(4)
            return ScanResult(records=[inspect_model(path)])

        self.library._scanner = scanner
        self.assertTrue(self.service.set_model_folder(path.parent))
        self.wait_for(entered.is_set)
        self.assertFalse(self.service.shutdown())
        self.assertFalse(self.service.resume_after_close_abort())
        self.assertIsNotNone(self.library._worker)
        release.set()
        self.wait_for(lambda: self.library._worker is None)
        self.assertTrue(self.service.shutdown())
        self.assertTrue(self.service.resume_after_close_abort())
        self.app.processEvents()
        self.assertEqual(calls, [True])
        self.assertEqual(self.service.models, [])
        self.assertIsNone(self.service._worker)
        self.assertFalse(self.service.model_folder_busy)
        self.assertIn(self.service.state, ('unavailable', 'unloaded'))


if __name__ == '__main__':
    unittest.main()
