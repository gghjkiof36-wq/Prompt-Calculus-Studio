"""Frozen thinking policy and safe context with synthetic HTTP/runtime only."""
import copy
import hashlib
import json
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.local_ai.engine import Cancellation
from prompt_calculus_studio.local_ai.registry import ModelRegistry
from prompt_calculus_studio.local_ai.runtime import LlamaRuntime
from prompt_calculus_studio.local_ai.settings import DEFAULT_REASONING, normalize_reasoning, normalize_settings
from prompt_calculus_studio.local_documents import chat, identity, schema
from test_local_ai_runtime_contracts import FakeConnection, sse
import test_local_ai_model_presets as preset_fixture


class ReasoningPolicyContracts(unittest.TestCase):
    def test_strict_policy_rejects_unknown_keys_types_and_answer_starvation(self):
        for policy in ({}, {'enable_thinking': True}, dict(DEFAULT_REASONING, tools=[]),
                       dict(DEFAULT_REASONING, enable_thinking=1),
                       dict(DEFAULT_REASONING, reasoning_budget_tokens=True),
                       dict(DEFAULT_REASONING, reasoning_budget_tokens=-2),
                       dict(DEFAULT_REASONING, reasoning_budget_tokens=128),
                       dict(enable_thinking=False, reasoning_budget_tokens=32)):
            with self.subTest(policy=policy), self.assertRaises(ValueError):
                normalize_reasoning(policy, max_tokens=128)
        for budget in (None, -1, 0, 64):
            expected = dict(enable_thinking=None, reasoning_budget_tokens=budget)
            self.assertEqual(normalize_reasoning(expected, max_tokens=128), expected)
        with self.assertRaises(ValueError):
            normalize_settings({'reasoning_budget_tokens': 64})


class ReasoningTransportContracts(unittest.TestCase):
    def setUp(self):
        self.runtime = LlamaRuntime()
        self.runtime.model = {'id': 'fixture-model'}
        self.runtime.context_size = 4096
        self.template = 'synthetic verified-profile template'
        self.profile = patch('prompt_calculus_studio.local_ai.runtime.QWEN35_TEMPLATE_SHA256',
                             hashlib.sha256(self.template.encode()).hexdigest())
        self.profile.start(); self.addCleanup(self.profile.stop)
        self.props = dict(build_info='b11900-fixture', chat_template=self.template)
        self.calls = []
        self.ignore_toggle = False
        self.unknown_thinking = True
        self.ignore_reasoning_content = False
        self.json_patch = patch.object(self.runtime, '_json', side_effect=self.json)
        self.json_patch.start(); self.addCleanup(self.json_patch.stop)
        self.params = dict(normalize_settings({'max_tokens': 1024}), reasoning=dict(DEFAULT_REASONING))

    def json(self, route, payload=None, **kwargs):
        self.calls.append((route, copy.deepcopy(payload)))
        if route == '/props':
            return copy.deepcopy(self.props)
        if route == '/apply-template':
            if payload.get('add_generation_prompt') is False:
                reasoning = payload['messages'][-1].get('reasoning_content')
                if reasoning and not self.ignore_reasoning_content:
                    return {'prompt': 'user: Hello\nassistant: <think>\n' + reasoning + '\n</think>\nAnswer'}
                return {'prompt': 'user: Hello\nassistant: Answer'}
            enabled = payload.get('chat_template_kwargs', {}).get('enable_thinking', True)
            prompt = 'user: Hello\nassistant: '
            if self.props['chat_template'] != self.template and not self.unknown_thinking:
                return {'prompt': prompt}
            return {'prompt': prompt + ('<think>\n' if enabled or self.ignore_toggle else '<think>\n\n</think>\n\n')}
        if route == '/tokenize':
            return {'tokens': list(range(12))}
        self.fail(route)

    def options(self, **policy):
        self.params['reasoning'] = dict(DEFAULT_REASONING, **policy)
        return self.runtime._reasoning_options(self.params, Cancellation())

    def test_verified_qwen_default_is_bounded_but_explicit_unlimited_wins(self):
        self.assertEqual(self.options(), {'reasoning_budget_tokens': 512})
        self.params['max_tokens'] = 128
        self.assertEqual(self.options(), {'reasoning_budget_tokens': 127})
        before = len(self.calls)
        self.assertEqual(self.options(reasoning_budget_tokens=-1), {})
        self.assertEqual(len(self.calls), before)
        self.assertEqual(self.options(enable_thinking=False), {'chat_template_kwargs': {'enable_thinking': False}})
        self.assertEqual(self.options(enable_thinking=True, reasoning_budget_tokens=0),
                         {'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_budget_tokens': 0})

    def test_unknown_model_keeps_default_and_budget_requires_reasoning_template(self):
        self.props['chat_template'] = 'another model'
        self.assertEqual(self.options(), {})
        with self.assertRaisesRegex(RuntimeError, '思考切換'):
            self.options(enable_thinking=False)
        self.assertEqual(self.options(reasoning_budget_tokens=64), {'reasoning_budget_tokens': 64})
        self.runtime._budget_checked = False
        self.unknown_thinking = False
        with self.assertRaisesRegex(RuntimeError, '思考上限'):
            self.options(reasoning_budget_tokens=64)

    def test_open_prefill_without_detectable_reasoning_end_is_not_budget_support(self):
        self.props['chat_template'] = 'template with an open prefill only'
        self.ignore_reasoning_content = True
        with self.assertRaisesRegex(RuntimeError, '思考上限'):
            self.options(reasoning_budget_tokens=64)

    def test_unknown_engine_or_ignored_toggle_fails_before_completion(self):
        self.props['build_info'] = 'b11899-fixture'
        with self.assertRaisesRegex(RuntimeError, 'b11900'):
            self.options()
        self.runtime._reasoning_props = None
        self.props['build_info'] = 'b11900-fixture'
        self.ignore_toggle = True
        with self.assertRaisesRegex(RuntimeError, '未套用'):
            self.options(enable_thinking=False)

    def test_preflight_and_completion_use_identical_frozen_control_options(self):
        messages = [dict(role='user', content='test')]
        for policy, expected in ((dict(DEFAULT_REASONING), {'reasoning_budget_tokens': 512}),
                                 (dict(enable_thinking=False, reasoning_budget_tokens=None), {'chat_template_kwargs': {'enable_thinking': False}}),
                                 (dict(enable_thinking=True, reasoning_budget_tokens=32), {'chat_template_kwargs': {'enable_thinking': True}, 'reasoning_budget_tokens': 32})):
            with self.subTest(policy=policy):
                connection = FakeConnection(sse({'delta': {'content': 'answer'}, 'finish_reason': 'stop'}))
                self.params['reasoning'] = policy
                with patch('prompt_calculus_studio.local_ai.runtime.validate_model'), patch.object(self.runtime, '_connect', return_value=connection):
                    result = self.runtime.generate(messages, self.params, Cancellation(), Mock())
                self.assertEqual(result, ('completed', 'answer'))
                applied = [payload for route, payload in self.calls if route == '/apply-template'][-1]
                self.assertEqual(applied, dict(messages=messages, **expected))
                self.assertEqual(connection.sent[2], dict(model='fixture-model', messages=messages, stream=True,
                    max_tokens=1024, temperature=.7, top_p=.95, seed=-1, **expected))


class ReasoningOwnerContracts(unittest.TestCase):
    setUpClass = classmethod(preset_fixture.ModelPresetContracts.setUpClass.__func__)
    setUp = preset_fixture.ModelPresetContracts.setUp
    tearDown = preset_fixture.ModelPresetContracts.tearDown
    add_model = preset_fixture.ModelPresetContracts.add_model
    wait_for = preset_fixture.ModelPresetContracts.wait_for
    owner_fixture = preset_fixture.ModelPresetContracts.owner_fixture

    def configure_reasoning(self, model, value):
        current = self.service.model_preset(model['id'])
        self.assertTrue(self.service.configure_model(model['id'], {}, reasoning=value, expected_revision=current['revision']), self.service.error)
        return self.service.model_preset(model['id'])

    def test_per_model_roundtrip_reset_and_stale_form_do_not_change_selection_or_load(self):
        default = self.service.model_preset(self.a['id'])
        self.assertEqual(default['reasoning'], DEFAULT_REASONING)
        chosen = dict(enable_thinking=True, reasoning_budget_tokens=128)
        latest = self.configure_reasoning(self.a, chosen)
        self.assertEqual(self.service.model_preset(self.b['id'])['reasoning'], DEFAULT_REASONING)
        restored = ModelRegistry(self.root / 'data')
        self.assertEqual(restored.model_presets[self.a['id']]['reasoning'], chosen)
        self.assertEqual(self.service.selected_model, self.a['id'])
        self.assertIsNone(self.service._worker)
        before = self.service._registry.path.read_bytes()
        self.assertFalse(self.service.configure_model(self.a['id'], {}, reasoning=dict(DEFAULT_REASONING), expected_revision=default['revision']))
        self.assertEqual(self.service._registry.path.read_bytes(), before)
        self.assertTrue(self.service.reset_model_preset(self.a['id'], expected_revision=latest['revision']))
        self.assertEqual(self.service.model_preset(self.a['id'])['reasoning'], DEFAULT_REASONING)

    def test_owner_freezes_reasoning_before_save_reentry_and_retry_gets_new_policy(self):
        first = dict(enable_thinking=True, reasoning_budget_tokens=64)
        second = dict(enable_thinking=False, reasoning_budget_tokens=None)
        self.configure_reasoning(self.a, first)
        owner = self.owner_fixture()
        reentered = []
        def reenter():
            if not reentered:
                reentered.append(True)
                self.configure_reasoning(self.a, second)
        owner.editing.connect(reenter)
        self.assertTrue(owner.send('test'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        self.assertEqual(self.runtime.generations[0][2]['reasoning'], first)
        self.assertEqual(owner.payload['turns'][0]['attempts'][0]['reasoning'], first)
        self.assertEqual(set(owner.payload['turns'][0]['attempts'][0]['parameters']), set(preset_fixture.MODEL_PARAMETERS))
        saved = schema.decode(owner.path.read_bytes())
        self.assertIn(chat.REASONING_FEATURE, saved['required_features'])
        self.assertTrue(owner.retry())
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        self.assertEqual(self.runtime.generations[-1][2]['reasoning'], second)
        self.assertEqual(owner.payload['turns'][0]['attempts'][0]['reasoning'], first)

    def test_schema_roundtrip_clone_archive_and_required_feature_are_strict(self):
        owner = self.owner_fixture()
        self.assertTrue(owner.send('test'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        document = owner.project()
        chat.archive_revision(document['payload'], reason='restore')
        chat.sync_features(document)
        clone = identity.duplicate(document).document
        self.assertEqual(clone['payload']['turns'][0]['attempts'][0]['reasoning'], DEFAULT_REASONING)
        self.assertEqual(clone['payload']['revisions'][0]['turns'][0]['attempts'][0]['reasoning'], DEFAULT_REASONING)
        self.assertEqual(schema.decode(schema.encode(clone)), clone)
        missing = copy.deepcopy(clone); missing['required_features'].remove(chat.REASONING_FEATURE)
        with self.assertRaises(schema.DocumentError): schema.validate(missing)
        widened = copy.deepcopy(clone)
        widened['payload']['turns'][0]['attempts'][0]['parameters']['enable_thinking'] = False
        with self.assertRaises(schema.DocumentError): schema.validate(widened)
        archived_only = copy.deepcopy(clone); archived_only['payload']['turns'] = []
        schema.validate(archived_only)
        old = chat.empty()
        self.assertNotIn(chat.REASONING_FEATURE, old['required_features'])
        self.assertEqual(schema.decode(schema.encode(old)), old)

    def test_unfinished_unclosed_thinking_is_omitted_from_context_but_source_is_preserved(self):
        owner = self.owner_fixture()
        self.assertTrue(owner.send('first'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        attempt = owner.payload['turns'][0]['attempts'][0]
        original = '<think>unfinished internal work'
        attempt['text'] = original
        for status in ('stopped', 'interrupted', 'failed', 'limited'):
            with self.subTest(status=status):
                attempt['status'] = status
                before = copy.deepcopy(owner.payload)
                refs = chat.context_references(owner.payload)
                messages = chat.context_messages(owner.payload, 'next')
                self.assertEqual([m['role'] for m in messages], ['user', 'user'])
                self.assertEqual(owner.payload, before)
                self.assertEqual(chat.context_references(owner.payload), refs)
        attempt['text'] = '<think>done</think>Answer'
        self.assertEqual(chat.context_messages(owner.payload, 'next')[1]['content'], attempt['text'])
        attempt['text'] = '```xml\n<think>literal example\n```'
        self.assertEqual(chat.context_messages(owner.payload, 'next')[1]['content'], attempt['text'])
        attempt['text'] = 'partial ordinary answer'
        self.assertEqual(chat.context_messages(owner.payload, 'next')[1]['content'], attempt['text'])

    def test_completed_open_think_tag_is_preserved_as_literal_context(self):
        owner = self.owner_fixture()
        self.assertTrue(owner.send('first'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        attempt = owner.payload['turns'][0]['attempts'][0]
        attempt.update(text='<think> 是一個標記', status='complete')
        for enabled in (None, True, False):
            with self.subTest(enable_thinking=enabled):
                attempt['reasoning'] = dict(enable_thinking=enabled, reasoning_budget_tokens=None)
                before = copy.deepcopy(owner.payload)
                refs = chat.context_references(owner.payload)
                messages = chat.context_messages(owner.payload, 'next')
                self.assertEqual([m['role'] for m in messages], ['user', 'assistant', 'user'])
                self.assertEqual(messages[1]['content'], attempt['text'])
                self.assertEqual(owner.payload, before)
                self.assertEqual(chat.context_references(owner.payload), refs)

    def test_thinking_disabled_partial_open_tag_is_preserved_as_literal_context(self):
        owner = self.owner_fixture()
        self.assertTrue(owner.send('first'))
        self.wait_for(lambda: not owner.busy and self.service.state == 'ready')
        attempt = owner.payload['turns'][0]['attempts'][0]
        attempt.update(text='<think> 是一個標記',
                       reasoning=dict(enable_thinking=False, reasoning_budget_tokens=None))
        for status in ('stopped', 'interrupted', 'failed', 'limited'):
            with self.subTest(status=status):
                attempt['status'] = status
                before = copy.deepcopy(owner.payload)
                refs = chat.context_references(owner.payload)
                messages = chat.context_messages(owner.payload, 'next')
                self.assertEqual(messages[1]['role'], 'assistant')
                self.assertEqual(messages[1]['content'], attempt['text'])
                self.assertEqual(owner.payload, before)
                self.assertEqual(chat.context_references(owner.payload), refs)

    def test_model_form_saves_policy_and_preserves_unapplied_choice(self):
        from prompt_calculus_studio.local_ai.chat_settings import ModelPresetPanel
        panel = ModelPresetPanel(self.service, self.a['id'])
        self.addCleanup(panel.deleteLater)
        panel.thinking_mode.setCurrentIndex(1)
        panel.thinking_budget_mode.setCurrentIndex(2)
        panel.thinking_budget.setValue(64)
        panel.refresh()
        self.assertEqual(panel.reasoning_values(), dict(enable_thinking=True, reasoning_budget_tokens=64))
        self.assertTrue(panel.apply())
        self.assertEqual(self.service.model_preset(self.a['id'])['reasoning'], panel.reasoning_values())
        panel.thinking_mode.setCurrentIndex(2)
        self.assertFalse(panel.thinking_budget_mode.isEnabled())
        self.assertTrue(panel.apply())
        self.assertEqual(self.service.model_preset(self.a['id'])['reasoning'], dict(enable_thinking=False, reasoning_budget_tokens=None))
