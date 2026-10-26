"""Send-time model preparation with a fake engine and isolated registry.

The real Qt worker runs, but no executable, GPU, model or network service does.
Events pause the fake engine at acceptance boundaries rather than racing sleeps.
"""
import copy
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

from prompt_calculus_studio.local_ai.service import LocalAIService
from prompt_calculus_studio.service_adapters import ServiceRequest


class FakeRuntime:
    def __init__(self):
        self.model = self.settings = None
        self.alive = False
        self.starts, self.generations = [], []
        self.start_entered, self.start_release = Event(), Event()
        self.generate_entered, self.generate_release = Event(), Event()
        self.start_release.set()
        self.generate_release.set()
        self.start_error = ''
        self.cancel_after_start = False

    def start(self, model, settings, cancel):
        self.stop()
        cancel.check()
        self.starts.append((copy.deepcopy(model), copy.deepcopy(settings)))
        self.start_entered.set()
        while not self.start_release.wait(.01):
            cancel.check()
        cancel.check()
        if self.start_error:
            raise RuntimeError(self.start_error)
        self.model, self.settings = copy.deepcopy(model), copy.deepcopy(settings)
        self.alive = True
        if self.cancel_after_start:
            cancel.cancel()

    def stop(self):
        self.alive = False
        self.model = self.settings = None

    def generate(self, messages, params, cancel, on_delta):
        cancel.check()
        if not self.alive:
            raise RuntimeError('Fake engine was not prepared.')
        self.generations.append((self.model['id'], copy.deepcopy(messages), copy.deepcopy(params)))
        self.generate_entered.set()
        on_delta('合成回覆')
        while not self.generate_release.wait(.01):
            cancel.check()
        cancel.check()
        return 'completed', '合成回覆'

    def ensure_idle(self, timeout=3):
        return self.alive


class AutoPrepareContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.runtime_path = self.root / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
        # It is only a path fixture. The injected engine never executes it.
        self.runtime_path.write_bytes(b'synthetic engine path')
        self.runtime = FakeRuntime()
        self.factories = []

        def factory():
            self.factories.append(True)
            return self.runtime

        self.service = LocalAIService(self.root / 'data', runtime_factory=factory)
        self.assertTrue(self.service.configure({'runtime_path': str(self.runtime_path), 'idle_minutes': 0}))
        self.first = self.add_model('first')
        self.results, self.deltas, self.states = [], [], []
        self.service.finished.connect(lambda *event: self.results.append(event))
        self.service.delta.connect(lambda *event: self.deltas.append(event))
        self.service.changed.connect(lambda: self.states.append(self.service.state))

    def tearDown(self):
        self.service.shutdown()
        self.runtime.start_release.set()
        self.runtime.generate_release.set()
        self.wait_for(lambda: self.service._worker is None, 'worker cleanup')
        self.assertTrue(self.service.shutdown())
        delete(self.service)
        self.temporary.cleanup()

    def wait_for(self, predicate, label='condition', timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail(f'Timed out waiting for {label}; state={self.service.state}, error={self.service.error}')

    def add_model(self, name):
        path = self.root / (name + '.gguf')
        path.write_bytes(b'GGUF' + (3).to_bytes(4, 'little'))
        record = self.service.register_model(path)
        self.assertIsNotNone(record, self.service.error)
        return record

    def request(self, *, model=None, params=None):
        return ServiceRequest('local_ai', 'chat', scope={'document_id': 'synthetic-owner'},
                              input={'model': model or self.service.selected_model,
                                     'messages': [{'role': 'user', 'content': '合成問題'}],
                                     'params': params or self.service.settings})

    def complete(self, request):
        self.assertTrue(self.service.submit(request), self.service.error)
        self.wait_for(lambda: any(event[0] == request for event in self.results)
                      and self.service.state == 'ready', 'completed request')

    def test_read_only_submission_preflight_rejects_invalid_parameters_and_changed_model(self):
        request = self.request()
        self.assertEqual(self.service.validate_submission(request)[0]['id'], self.first['id'])
        params = dict(self.service.settings, max_tokens=self.service.settings['context_size'])
        with self.assertRaises(ValueError):
            self.service.validate_submission(self.request(params=params))
        params = dict(self.service.settings, runtime_path=str(self.root / 'missing.exe'))
        with self.assertRaises((ValueError, OSError)):
            self.service.validate_submission(self.request(params=params))
        Path(self.first['path']).write_bytes(b'GGUF' + (3).to_bytes(4, 'little') + b'changed')
        with self.assertRaises(ValueError):
            self.service.validate_submission(request)
        self.assertIsNone(self.service._worker)
        self.assertIsNone(self.service._active)
        self.assertEqual(self.factories, [])

    def test_selection_and_configuration_do_not_start_until_send(self):
        second = self.add_model('second')
        self.assertTrue(self.service.select_model(second['id']))
        self.assertTrue(self.service.configure({'context_size': 8192}))
        self.assertTrue(self.service.can_submit)
        self.assertIsNone(self.service._worker)
        self.assertEqual(self.factories, [])
        self.runtime.start_release.clear()
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.assertEqual(self.service.state, 'loading')
        self.assertFalse(self.service.can_submit)
        self.wait_for(self.runtime.start_entered.is_set, 'fake model preparation')
        self.assertEqual(self.runtime.generations, [])
        self.runtime.start_release.set()
        self.wait_for(lambda: len(self.results) == 1 and self.service.state == 'ready')
        self.assertEqual(self.results, [(request, 'completed', '合成回覆')])
        self.assertEqual(self.deltas, [(request, '合成回覆')])
        self.assertEqual(self.runtime.starts[0][0]['id'], second['id'])
        self.assertEqual(self.runtime.starts[0][1]['context_size'], 8192)
        self.assertEqual(len(self.factories), 1)

    def test_same_model_reuses_engine_with_new_sampling_snapshot(self):
        self.complete(self.request())
        self.assertTrue(self.service.configure({'temperature': .25, 'top_p': .8, 'seed': 43, 'max_tokens': 256}))
        request = self.request()
        self.complete(request)
        self.assertEqual(len(self.runtime.starts), 1)
        self.assertEqual(self.runtime.generations[-1][2], request.input['params'])
        self.assertEqual(len(self.runtime.generations), 2)

    def test_selecting_another_model_switches_only_at_its_next_send(self):
        self.complete(self.request())
        second = self.add_model('second')
        self.assertTrue(self.service.select_model(second['id']))
        self.assertEqual(self.service.loaded_model, self.first['id'])
        self.assertEqual(len(self.runtime.starts), 1)
        self.assertTrue(self.service.needs_reload)
        request = self.request()
        self.complete(request)
        self.assertEqual([item[0]['id'] for item in self.runtime.starts], [self.first['id'], second['id']])
        self.assertEqual(self.runtime.generations[-1][0], second['id'])
        self.assertEqual(self.results[-1][0], request)

    def test_changed_load_settings_are_used_before_generation(self):
        self.complete(self.request())
        self.assertTrue(self.service.configure({'context_size': 2048, 'gpu_layers': 8}))
        self.assertEqual(len(self.runtime.starts), 1)
        self.assertTrue(self.service.needs_reload)
        request = self.request()
        self.complete(request)
        self.assertEqual(len(self.runtime.starts), 2)
        self.assertEqual(self.runtime.starts[-1][1], request.input['params'])
        self.assertEqual(self.service.loaded_settings, request.input['params'])

    def test_preparation_uses_frozen_request_even_if_settings_changed_before_acceptance(self):
        request = self.request()
        self.assertTrue(self.service.configure({'context_size': 8192, 'temperature': .25}))
        self.complete(request)
        self.assertEqual(self.runtime.starts[0][1], request.input['params'])
        self.assertEqual(self.runtime.generations[0][2], request.input['params'])
        self.assertTrue(self.service.needs_reload)
        self.complete(self.request())
        self.assertEqual(self.runtime.starts[-1][1], self.service.settings)

    def test_preparation_holds_single_slot_and_blocks_config_changes(self):
        self.runtime.start_release.clear()
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(self.runtime.start_entered.is_set)
        self.assertFalse(self.service.submit(self.request()))
        self.assertFalse(self.service.configure({'context_size': 2048}))
        self.assertFalse(self.service.select_model(self.first['id']))
        self.assertIs(self.service._active, request)
        self.assertEqual(len(self.runtime.starts), 1)
        self.runtime.start_release.set()
        self.wait_for(lambda: len(self.results) == 1 and self.service.state == 'ready')
        self.assertEqual(len(self.runtime.generations), 1)

    def test_preparation_failure_finishes_original_request_and_can_retry(self):
        self.runtime.start_error = '合成載入失敗'
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(lambda: len(self.results) == 1 and self.service.state == 'error')
        self.assertEqual(self.results, [(request, 'error', '合成載入失敗')])
        self.assertEqual(self.runtime.generations, [])
        self.assertFalse(self.runtime.alive)
        self.assertTrue(self.service.can_submit)
        self.runtime.start_error = ''
        self.complete(self.request())
        self.assertEqual(len(self.runtime.starts), 2)

    def test_cancel_during_preparation_never_generates(self):
        self.runtime.start_release.clear()
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(self.runtime.start_entered.is_set)
        self.assertTrue(self.service.cancel(request.request_id))
        self.assertEqual(self.service.state, 'stopping')
        self.assertFalse(self.service.can_submit)
        self.wait_for(lambda: len(self.results) == 1 and self.service.state == 'unloaded')
        self.assertEqual(self.results, [(request, 'cancelled', '')])
        self.assertEqual(self.runtime.generations, [])
        self.assertFalse(self.runtime.alive)
        self.runtime.start_release.set()
        self.complete(self.request())

    def test_cancel_at_prepared_boundary_unloads_before_any_generation(self):
        self.runtime.cancel_after_start = True
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(lambda: len(self.results) == 1 and self.service.state == 'unloaded')
        self.assertEqual(self.results, [(request, 'cancelled', '')])
        self.assertEqual(self.runtime.generations, [])
        self.assertFalse(self.runtime.alive)

    def test_cancel_generation_keeps_received_text_and_releases_slot(self):
        self.runtime.generate_release.clear()
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(lambda: len(self.deltas) == 1 and self.service.state == 'generating')
        self.assertTrue(self.service.cancel(request.request_id))
        self.wait_for(lambda: len(self.results) == 1 and self.service.state == 'ready')
        self.assertEqual(self.results, [(request, 'cancelled', '合成回覆')])
        self.assertTrue(self.service.can_submit)

    def test_shutdown_during_preparation_cancels_and_resume_does_not_load(self):
        self.runtime.start_release.clear()
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(self.runtime.start_entered.is_set)
        self.assertFalse(self.service.shutdown())
        self.wait_for(lambda: self.service._worker is None)
        self.assertEqual(self.results, [(request, 'cancelled', '')])
        self.assertEqual(self.runtime.generations, [])
        self.assertTrue(self.service.shutdown())
        self.assertTrue(self.service.resume_after_close_abort())
        self.assertEqual(self.service.state, 'unloaded')
        self.assertIsNone(self.service._worker)
        self.assertEqual(len(self.factories), 1)

    def test_missing_engine_rejects_without_starting_or_reserving_request(self):
        with patch('prompt_calculus_studio.local_ai.service.resolve_runtime', side_effect=ValueError('合成引擎不存在')):
            self.assertFalse(self.service.submit(self.request()))
        self.assertEqual(self.service.error, '合成引擎不存在')
        self.assertIsNone(self.service._worker)
        self.assertIsNone(self.service._active)
        self.assertEqual(self.factories, [])

    def test_engine_that_exited_since_ready_is_prepared_again(self):
        self.complete(self.request())
        self.runtime.alive = False
        self.complete(self.request())
        self.assertEqual(len(self.runtime.starts), 2)
        self.assertEqual(len(self.runtime.generations), 2)

    def test_factory_failure_reports_error_instead_of_successful_cancellation(self):
        def broken_factory():
            raise RuntimeError('合成引擎初始化失敗')

        self.service._runtime_factory = broken_factory
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(lambda: self.service._worker is None)
        self.assertEqual(self.results, [(request, 'error', '合成引擎初始化失敗')])
        self.assertTrue(self.service.can_submit)


if __name__ == '__main__':
    unittest.main()
