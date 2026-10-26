"""Per-model configuration and saved attempt contracts using a fake engine.

Owner saves use a deterministic isolated writer; Windows publication races are
covered separately by the native document writer suite. No real model is run.
"""
import copy
import json
import os
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_ai.registry import MODEL_PARAMETERS, ModelRegistry, inspect_model
from prompt_calculus_studio.local_ai.service import LocalAIService
from prompt_calculus_studio.local_documents import chat, io, schema
from prompt_calculus_studio.service_adapters import ServiceRequest
from test_local_ai_auto_prepare import FakeRuntime


class ModelPresetContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.runtime = FakeRuntime()
        self.factory_calls = []
        def factory():
            self.factory_calls.append(True)
            return self.runtime
        self.service = LocalAIService(self.root / 'data', runtime_factory=factory)
        runtime_path = self.root / ('llama-server.exe' if os.name == 'nt' else 'llama-server')
        runtime_path.write_bytes(b'fixture only')
        self.assertTrue(self.service.configure(dict(runtime_path=str(runtime_path), idle_minutes=0)))
        self.a = self.add_model('a')
        self.b = self.add_model('b')
        self.service.select_model(self.a['id'])
        self.results = []
        self.service.finished.connect(lambda *args: self.results.append(args))
        self.workspace = self.owner = None
        self.writer = None

    def tearDown(self):
        if self.owner is not None:
            self.owner.dispose()
        self.service.shutdown()
        self.runtime.start_release.set()
        self.runtime.generate_release.set()
        self.wait_for(lambda: self.service._worker is None)
        self.assertTrue(self.service.shutdown())
        delete(self.service)
        if self.workspace is not None:
            delete(self.workspace)
        if self.writer is not None:
            self.writer.stop()
        self.temp.cleanup()

    def add_model(self, name):
        path = self.root / (name + '.gguf')
        path.write_bytes(b'GGUF' + (3).to_bytes(4, 'little'))
        return self.service.register_model(path)

    def wait_for(self, predicate, timeout=5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail(f'Timed out: {self.service.state} {self.service.error}')

    def preset(self, model, **values):
        prompt = values.pop('system_prompt', None)
        snapshot = self.service.model_preset(model['id'])
        self.assertTrue(self.service.configure_model(model['id'], values, system_prompt=prompt,
                                                    expected_revision=snapshot['revision']), self.service.error)
        return self.service.model_preset(model['id'])

    def request(self):
        model = self.service.selected_model
        return ServiceRequest('local_ai', 'chat', scope={'document_id': 'isolated-preset'},
                              input=dict(model=model, params=self.service.settings_for_model(model),
                                         messages=[dict(role='user', content='合成問題')]))

    def complete(self, request):
        self.assertTrue(self.service.submit(request), self.service.error)
        self.wait_for(lambda: self.results and self.results[-1][0] == request and self.service.state == 'ready')

    def owner_fixture(self):
        path = self.root / 'conversation.pcs-chat'
        path.write_bytes(schema.encode(chat.empty()))
        self.workspace = QObject()
        self.workspace.data = self.root / 'data'
        self.workspace.local_ai = self.service
        self.workspace.autosave = SimpleNamespace(paused=set())
        self.workspace.all_pages = lambda: []
        self.owner = ChatDocument(path, self.workspace)

        def save(root, relative, candidate, expected):
            current = io.load(root, relative)
            if current.receipt != expected:
                return io.SaveResult('conflict', error='synthetic conflict')
            committed = copy.deepcopy(candidate)
            committed['revision'] = dict(id=str(uuid.uuid4()), parent=expected.revision)
            (Path(root) / relative).write_bytes(schema.encode(committed))
            loaded = io.load(root, relative)
            return io.SaveResult('saved', loaded.document, loaded.receipt)

        self.writer = patch('prompt_calculus_studio.local_ai.chat_document.io.save', side_effect=save)
        self.writer.start()
        return self.owner

    def test_models_inherit_defaults_until_saved_without_loading(self):
        self.assertEqual(self.service.settings_for_model(self.a['id']), self.service.settings)
        self.assertEqual(self.service.model_preset(self.a['id'])['overrides'], {})
        self.service.configure({'temperature': .45})
        self.assertEqual(self.service.settings_for_model(self.b['id'])['temperature'], .45)
        first = self.preset(self.a, context_size=2048, max_tokens=128, system_prompt='模型 A 指示')
        self.assertEqual(set(first['overrides']), set(MODEL_PARAMETERS))
        self.service.configure({'context_size': 8192, 'max_tokens': 4096, 'temperature': .8})
        self.assertEqual(self.service.settings_for_model(self.a['id'])['context_size'], 2048)
        self.assertEqual(self.service.settings_for_model(self.a['id'])['max_tokens'], 128)
        self.assertEqual(self.service.settings_for_model(self.b['id'])['max_tokens'], 4096)
        self.assertEqual(self.factory_calls, [])
        self.assertIsNone(self.service._worker)

    def test_each_uuid_round_trips_and_selection_changes_effective_parameters(self):
        a = self.preset(self.a, context_size=2048, max_tokens=128, temperature=.2, system_prompt='A')
        b = self.preset(self.b, context_size=8192, max_tokens=512, temperature=1.2, system_prompt='B')
        restored = ModelRegistry(self.root / 'data')
        self.assertEqual(restored.model_presets[self.a['id']]['parameters'], a['parameters'])
        self.assertEqual(restored.model_presets[self.b['id']]['system_prompt'], 'B')
        self.service.select_model(self.b['id'])
        self.assertEqual(self.service.generation_settings['temperature'], b['parameters']['temperature'])
        self.service.select_model(self.a['id'])
        self.assertEqual(self.service.generation_settings['temperature'], a['parameters']['temperature'])
        self.assertEqual(self.factory_calls, [])

    def test_reset_only_one_model_returns_to_current_defaults(self):
        a = self.preset(self.a, temperature=.1, system_prompt='A')
        b = self.preset(self.b, temperature=1.4, system_prompt='B')
        self.assertTrue(self.service.reset_model_preset(self.a['id'], expected_revision=a['revision']))
        self.assertEqual(self.service.model_preset(self.a['id'])['overrides'], {})
        self.assertEqual(self.service.model_preset(self.a['id'])['system_prompt'], '')
        self.assertEqual(self.service.settings_for_model(self.a['id']), self.service.settings)
        self.assertEqual(self.service.model_preset(self.b['id']), b)

    def test_stale_form_rejected_after_preset_or_common_base_change(self):
        stale = self.service.model_preset(self.a['id'])
        fresh = self.preset(self.a, temperature=.2)
        before = self.service._registry.path.read_bytes()
        self.assertFalse(self.service.configure_model(self.a['id'], {'temperature': .8}, expected_revision=stale['revision']))
        self.assertEqual(self.service._registry.path.read_bytes(), before)
        self.service.configure({'idle_minutes': 20})
        self.assertFalse(self.service.reset_model_preset(self.a['id'], expected_revision=fresh['revision']))
        self.assertEqual(self.service.settings_for_model(self.a['id'])['temperature'], .2)

    def test_bad_parameters_or_prompt_never_change_registry(self):
        snapshot = self.service.model_preset(self.a['id'])
        before = self.service._registry.path.read_bytes()
        for values, prompt in (({'context_size': 256, 'max_tokens': 256}, ''),
                              ({'runtime_path': 'not per model'}, ''), ({}, 42), ({}, 'x' * 1_000_001)):
            with self.subTest(keys=list(values), prompt_type=type(prompt).__name__):
                self.assertFalse(self.service.configure_model(self.a['id'], values, system_prompt=prompt,
                                                             expected_revision=snapshot['revision']))
        self.assertEqual(self.service._registry.path.read_bytes(), before)

    def test_replaced_model_uuid_does_not_inherit_preset_or_accept_old_form(self):
        stale = self.preset(self.a, temperature=.1, system_prompt='old weights')
        path = Path(self.a['path'])
        path.write_bytes(path.read_bytes() + b'replaced')
        new = self.service.register_model(path)
        self.assertNotEqual(new['id'], self.a['id'])
        self.assertFalse(self.service.configure_model(self.a['id'], {}, expected_revision=stale['revision']))
        self.assertEqual(self.service.model_preset(new['id'])['overrides'], {})
        self.assertNotIn(self.a['id'], ModelRegistry(self.root / 'data').model_presets)
        self.assertEqual(stale['system_prompt'], 'old weights')

    def test_scan_replacement_prunes_old_uuid_presets_but_keeps_other_model(self):
        self.preset(self.a, temperature=.1)
        b = self.preset(self.b, temperature=1.1)
        path = Path(self.a['path']); path.write_bytes(path.read_bytes() + b'new')
        self.service._registry.merge_scanned([inspect_model(path)])
        restored = ModelRegistry(self.root / 'data')
        self.assertEqual(restored.error, '')
        self.assertNotIn(self.a['id'], restored.model_presets)
        self.assertEqual(restored.model_presets[self.b['id']]['parameters'], b['parameters'])

    def test_prepare_and_generate_reject_preset_edits(self):
        before = self.service.model_preset(self.a['id'])
        self.runtime.start_release.clear(); self.runtime.generate_release.clear()
        request = self.request()
        self.assertTrue(self.service.submit(request))
        self.wait_for(self.runtime.start_entered.is_set)
        self.assertFalse(self.service.configure_model(self.a['id'], {'temperature': .1}, expected_revision=before['revision']))
        self.assertFalse(self.service.reset_model_preset(self.b['id'], expected_revision=self.service.model_preset(self.b['id'])['revision']))
        self.runtime.start_release.set()
        self.wait_for(self.runtime.generate_entered.is_set)
        self.assertFalse(self.service.configure_model(self.a['id'], {'temperature': .1}, expected_revision=before['revision']))
        self.assertEqual(self.service.model_preset(self.a['id']), before)

    def test_frozen_request_survives_later_preset_edit_and_next_send_reprepares(self):
        self.preset(self.a, context_size=2048, max_tokens=128, temperature=.2, gpu_layers=4)
        frozen = self.request()
        self.preset(self.a, context_size=4096, max_tokens=256, temperature=.4, gpu_layers=8)
        self.complete(frozen)
        self.assertEqual(self.runtime.starts[0][1], frozen.input['params'])
        self.assertEqual(self.runtime.generations[0][2], frozen.input['params'])
        self.assertTrue(self.service.needs_reload)
        second = self.request(); self.complete(second)
        self.assertEqual(self.runtime.starts[-1][1], second.input['params'])
        self.assertFalse(self.service.needs_reload)

    def test_explicit_compatibility_load_uses_model_preset(self):
        self.preset(self.a, context_size=2048, max_tokens=128, gpu_layers=6)
        self.assertTrue(self.service.load())
        self.wait_for(lambda: self.service.state == 'ready')
        self.assertEqual(self.runtime.starts[-1][1], self.service.settings_for_model(self.a['id']))

    def test_document_sends_model_prompt_and_records_actual_snapshots_before_acceptance(self):
        self.preset(self.a, context_size=2048, max_tokens=128, temperature=.2, system_prompt='模型 A 指示')
        self.preset(self.b, context_size=8192, max_tokens=256, temperature=.8, system_prompt='模型 B 指示')
        owner = self.owner_fixture()
        accepted = []
        original_submit = self.service.submit
        def submit(request):
            accepted.append((request, io.load(owner.path.parent, owner.path.name).document))
            return original_submit(request)
        with patch.object(self.service, 'submit', side_effect=submit):
            self.assertTrue(owner.send('問題 A'))
            self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
            first = copy.deepcopy(owner.payload['turns'][0]['attempts'][0])
            self.service.select_model(self.b['id'])
            self.assertTrue(owner.send('問題 B'))
            self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        self.assertEqual(owner.payload['system_prompt'], '')
        self.assertEqual(owner.payload['turns'][0]['attempts'][0], first)
        self.assertEqual(first['system_prompt'], '模型 A 指示')
        self.assertEqual(owner.payload['turns'][1]['attempts'][0]['system_prompt'], '模型 B 指示')
        for request, document in accepted:
            attempt = document['payload']['turns'][-1]['attempts'][-1]
            self.assertEqual(attempt['parameters'], {k: request.input['params'][k] for k in MODEL_PARAMETERS})
            self.assertEqual(attempt['system_prompt'], request.input['messages'][0]['content'])
            self.assertEqual(attempt['status'], 'receiving')
        self.assertEqual(self.runtime.generations[0][1][0], dict(role='system', content='模型 A 指示'))
        self.assertEqual(self.runtime.generations[1][1][0], dict(role='system', content='模型 B 指示'))
        self.assertEqual(io.load(owner.path.parent, owner.path.name).document['payload'], owner.payload)

    def test_document_instruction_wins_without_being_rewritten_by_preset(self):
        self.preset(self.a, system_prompt='模型預設')
        owner = self.owner_fixture()
        owner.set_options(dict(system_prompt='  文件指示保留空白  '))
        self.assertTrue(owner.send('問題'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        saved = copy.deepcopy(owner.payload)
        self.preset(self.a, system_prompt='下一次模型預設')
        self.assertEqual(owner.payload, saved)
        self.assertEqual(self.runtime.generations[0][1][0]['content'], '  文件指示保留空白  ')
        self.assertEqual(saved['turns'][0]['attempts'][0]['system_prompt'], saved['system_prompt'])
        owner.set_options(dict(system_prompt=' \n '))
        self.assertTrue(owner.send('下一題'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        self.assertEqual(owner.payload['system_prompt'], ' \n ')
        self.assertEqual(self.runtime.generations[1][1][0]['content'], '下一次模型預設')

    def test_editing_signal_reentrant_preset_change_does_not_rewrite_frozen_request(self):
        self.preset(self.a, temperature=.2, system_prompt='先前指示')
        owner = self.owner_fixture()
        changed = []
        def revise_once():
            if not changed:
                changed.append(True)
                self.preset(self.a, temperature=.9, system_prompt='新的指示')
        owner.editing.connect(revise_once)
        self.assertTrue(owner.send('問題'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        original = copy.deepcopy(owner.payload['turns'][0]['attempts'][0])
        self.assertEqual(original['parameters']['temperature'], .2)
        self.assertEqual(original['system_prompt'], '先前指示')
        self.assertEqual(self.runtime.generations[0][1][0]['content'], '先前指示')
        self.assertEqual(self.runtime.generations[0][2]['temperature'], .2)
        self.assertTrue(owner.retry())
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        self.assertEqual(owner.payload['turns'][0]['attempts'][0], original)
        latest = owner.payload['turns'][0]['attempts'][1]
        self.assertEqual(latest['parameters']['temperature'], .9)
        self.assertEqual(latest['system_prompt'], '新的指示')
        self.assertNotEqual(latest['profile_epoch'], original['profile_epoch'])


if __name__ == '__main__':
    unittest.main()
