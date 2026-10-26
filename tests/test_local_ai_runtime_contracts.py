"""Offline backend contracts: synthetic files/HTTP only, no Qt or real engine.

Authored but not executed in the first local-chat implementation, per the
user's resource constraint. AST parsing this file is not a test pass.
"""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from prompt_calculus_studio.local_ai.engine import Cancellation, Cancelled
from prompt_calculus_studio.local_ai.registry import ModelRegistry, inspect_model, validate_model
from prompt_calculus_studio.local_ai.runtime import LlamaRuntime
from prompt_calculus_studio.local_ai.settings import DEFAULT_SETTINGS, normalize_settings, validate_messages


def model_header(path):
    # Only a registry/header fixture. This is deliberately not a loadable model.
    path.write_bytes(b'GGUF' + (3).to_bytes(4, 'little'))
    return path


def sse(*choices, done=True):
    body = b': heartbeat\r\n\r\n'
    for choice in choices:
        body += b'data: ' + json.dumps({'choices': [choice]}, ensure_ascii=False).encode('utf-8') + b'\r\n\r\n'
    return body + (b'data: [DONE]\r\n\r\n' if done else b'')


class FakeResponse(io.BytesIO):
    status = 200

    def getheader(self, key, default=None):
        return 'text/event-stream' if key == 'Content-Type' else default


class FakeConnection:
    def __init__(self, body):
        self.response = FakeResponse(body)
        self.sent = None
        self.closed = False

    def request(self, method, path, body=None, headers=None):
        self.sent = (method, path, json.loads(body), headers)

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class SettingsContracts(unittest.TestCase):
    def test_invalid_settings_are_rejected_without_mutating_input(self):
        for value in ({'api_key': 'not-supported'}, {'context_size': True},
                      {'temperature': float('nan')}, {'temperature': 10 ** 400},
                      {'top_p': 10 ** 400}, {'top_p': 0}, {'gpu_layers': -2},
                      {'max_tokens': 4096, 'context_size': 4096}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                normalize_settings(value)
        original = {'context_size': 8192, 'seed': 42}
        normalized = normalize_settings(original)
        self.assertEqual(original, {'context_size': 8192, 'seed': 42})
        self.assertEqual(normalized['max_tokens'], DEFAULT_SETTINGS['max_tokens'])

    def test_messages_copy_exact_unicode_but_reject_tools_and_empty_user(self):
        original = [{'role': 'system', 'content': '繁體中文'}, {'role': 'user', 'content': '🙂\n```py\nx=1\n```'}]
        result = validate_messages(original)
        self.assertEqual(result, original)
        result[0]['content'] = 'changed'
        self.assertEqual(original[0]['content'], '繁體中文')
        for value in ([{'role': 'tool', 'content': 'tool result'}],
                      [{'role': 'user', 'content': '  '}],
                      [{'role': 'user', 'content': 'text', 'tool_calls': []}]):
            with self.subTest(value=value), self.assertRaises(ValueError):
                validate_messages(value)


class RegistryContracts(unittest.TestCase):
    def test_reference_roundtrip_and_changed_file_gets_new_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = model_header(root / '模型.gguf')
            store = ModelRegistry(root / 'app-data')
            first = store.register(source)
            self.assertEqual(store.register(source)['id'], first['id'])
            reloaded = ModelRegistry(root / 'app-data')
            self.assertEqual(reloaded.selected_model, first['id'])
            self.assertEqual(Path(reloaded.models[0]['path']), source.resolve())
            source.write_bytes(source.read_bytes() + b'changed')
            with self.assertRaises(ValueError):
                validate_model(first)
            replacement = store.register(source)
            self.assertNotEqual(replacement['id'], first['id'])
            self.assertEqual(len(store.models), 1)

    def test_invalid_header_and_split_file_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            invalid = root / 'invalid.gguf'
            invalid.write_bytes(b'not-GGUF')
            split = model_header(root / 'model-00001-of-00002.gguf')
            for path in (invalid, split):
                with self.subTest(path=path), self.assertRaises(ValueError):
                    inspect_model(path)

    def test_corrupt_registry_is_preserved_and_not_overwritten(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / 'local_ai' / 'registry.json'
            path.parent.mkdir()
            original = b'{broken original'
            path.write_bytes(original)
            store = ModelRegistry(root)
            self.assertTrue(store.error)
            with self.assertRaises(ValueError):
                store.update(settings=DEFAULT_SETTINGS)
            self.assertEqual(path.read_bytes(), original)

    def test_failed_atomic_replace_keeps_previous_settings_and_file(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ModelRegistry(directory)
            store.update(settings=DEFAULT_SETTINGS)
            original = store.path.read_bytes()
            with patch('prompt_calculus_studio.local_ai.registry.os.replace', side_effect=OSError('synthetic')):
                with self.assertRaises(OSError):
                    store.update(settings={**DEFAULT_SETTINGS, 'temperature': 1.1})
            self.assertEqual(store.settings, DEFAULT_SETTINGS)
            self.assertEqual(store.path.read_bytes(), original)
            self.assertEqual(list(store.path.parent.glob('.local-ai-*.tmp')), [])


class CancellationContracts(unittest.TestCase):
    def test_cancel_interrupts_bound_socket_and_remains_latched(self):
        control = Cancellation()
        sock = Mock()
        control.bind(sock)
        control.cancel()
        sock.shutdown.assert_called_once()
        with self.assertRaises(Cancelled):
            control.check()
        control.unbind()
        control.cancel()
        sock.shutdown.assert_called_once()

    def test_cancel_before_socket_bind_interrupts_new_connection(self):
        control = Cancellation()
        control.cancel()
        sock = Mock()
        with self.assertRaises(Cancelled):
            control.bind(sock)
        sock.shutdown.assert_called_once()


class StreamContracts(unittest.TestCase):
    def setUp(self):
        self.runtime = LlamaRuntime()  # Constructor has no process or network work.
        self.runtime.model = {'id': 'synthetic-model'}
        self.runtime.context_size = 4096
        self.messages = [{'role': 'user', 'content': '請回答。'}]
        self.params = normalize_settings({'max_tokens': 128, 'temperature': 0.3, 'top_p': 0.8, 'seed': 12})

    def generate(self, body, chunks):
        connection = FakeConnection(body)
        with patch('prompt_calculus_studio.local_ai.runtime.validate_model'), \
                patch.object(self.runtime, '_prompt_tokens', return_value=8), \
                patch.object(self.runtime, '_connect', return_value=connection):
            result = self.runtime.generate(self.messages, self.params, Cancellation(), chunks.append)
        self.assertTrue(connection.closed)
        return result, connection

    def test_unicode_stream_and_sampling_parameters_are_preserved(self):
        chunks = []
        result, connection = self.generate(sse(
            {'index': 0, 'delta': {'content': '你好🙂\n'}},
            {'index': 0, 'delta': {'content': '第二行'}, 'finish_reason': 'stop'}), chunks)
        self.assertEqual(result, ('completed', '你好🙂\n第二行'))
        self.assertEqual(chunks, ['你好🙂\n', '第二行'])
        method, route, payload, _headers = connection.sent
        self.assertEqual((method, route), ('POST', '/v1/chat/completions'))
        self.assertEqual(payload, dict(model='synthetic-model', messages=self.messages, stream=True,
                                      max_tokens=128, temperature=0.3, top_p=0.8, seed=12))

    def test_length_limit_has_distinct_terminal_status(self):
        result, _ = self.generate(sse({'delta': {'content': '部分'}, 'finish_reason': 'length'}), [])
        self.assertEqual(result, ('limited', '部分'))

    def test_disconnect_keeps_deltas_but_is_not_completed(self):
        chunks = []
        with self.assertRaises(RuntimeError):
            self.generate(sse({'delta': {'content': '收到的部分'}}, done=False), chunks)
        self.assertEqual(chunks, ['收到的部分'])

    def test_context_preflight_never_sends_completion_when_over_limit(self):
        with patch('prompt_calculus_studio.local_ai.runtime.validate_model'), \
                patch.object(self.runtime, '_prompt_tokens', return_value=4090), \
                patch.object(self.runtime, '_connect') as connect:
            with self.assertRaisesRegex(RuntimeError, '上下文不足'):
                self.runtime.generate(self.messages, self.params, Cancellation(), Mock())
            connect.assert_not_called()

    def test_tool_calls_are_refused(self):
        with self.assertRaisesRegex(RuntimeError, '工具呼叫'):
            self.generate(sse({'delta': {'tool_calls': [{'id': 'synthetic-call'}]}}), [])


class RuntimeOwnershipContracts(unittest.TestCase):
    def test_stop_retains_ownership_and_rejects_success_while_child_is_alive(self):
        runtime = LlamaRuntime()
        child, job = Mock(), Mock()
        child.poll.return_value = None
        # Simulate an OS/adapter that returned from wait without confirming exit.
        child.wait.return_value = None
        runtime.process, runtime.job = child, job
        with self.assertRaisesRegex(RuntimeError, '尚未完全停止'):
            runtime.stop()
        self.assertIs(runtime.process, child)
        self.assertIs(runtime.job, job)
        child.terminate.assert_called_once()
        job.close.assert_called_once()
        child.poll.return_value = 0
        runtime.stop()
        self.assertIsNone(runtime.process)
        self.assertIsNone(runtime.job)


if __name__ == '__main__':
    unittest.main()
