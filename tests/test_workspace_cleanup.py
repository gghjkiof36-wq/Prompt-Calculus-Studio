"""Persistence faults must not strand unrelated owners or finalize failed cleanup."""
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_calculus_studio.document_execution import DocumentExecution
from prompt_calculus_studio.workspace_runtime import WorkspaceRuntime
from prompt_calculus_studio.draft_cache import DraftCache


class Execution:
    close = DocumentExecution.close
    persist = DocumentExecution.persist

    def __init__(self, name, events, directory, failures):
        self.closed = False
        self.state = {'draft': '', 'draft_base': 'keep', 'synthetic': name}
        self.document = None
        self.runtime_token = name
        self.workspace_owner = None
        self.failures = failures
        def call(stage):
            events.append(name+'.'+stage)
            if self.failures.get(stage):
                self.failures[stage] -= 1
                raise sqlite3.OperationalError(name+' '+stage+' synthetic failure')
        self.client = SimpleNamespace(shutdown=lambda: call('shutdown'))
        self.store = SimpleNamespace(directory=directory, save=lambda state: call('save'),
                                     close=lambda: call('store.close'))


class Runtime:
    close = WorkspaceRuntime.close

    def __init__(self, events, directory, first=None, index_failures=0):
        self.closed = False
        self.executions = {name: Execution(name, events, directory/name,
            first if name == 'first' else {}) for name in ('first', 'second')}
        self.index_failures = index_failures
        def close():
            events.append('index.close')
            if self.index_failures:
                self.index_failures -= 1
                raise sqlite3.OperationalError('index synthetic failure')
        self.db = SimpleNamespace(close=close)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.root = Path(self.area.name)
        self.events = []

    def tearDown(self):self.area.cleanup()

    def test_save_failure_preserves_state_and_closes_all_owners(self):
        runtime = Runtime(self.events, self.root, {'save': 1})
        with self.assertRaises(OSError) as caught:runtime.close()
        self.assertIn('save synthetic failure', str(caught.exception))
        self.assertEqual(self.events, ['first.shutdown','first.save','first.store.close',
            'second.shutdown','second.save','second.store.close','index.close'])
        value = DraftCache(self.root/'first').read('execution-close-state')
        self.assertEqual(value['state'], runtime.executions['first'].state)
        self.assertTrue(runtime.closed)
        self.assertTrue(runtime.close_errors)
        before = list(self.events)
        runtime.close()
        self.assertEqual(self.events, before)

    def test_store_and_index_close_failures_remain_retryable(self):
        runtime = Runtime(self.events, self.root, {'store.close': 1}, index_failures=1)
        with self.assertRaises(OSError):runtime.close()
        self.assertFalse(runtime.closed)
        self.assertIn('second.store.close', self.events)
        self.assertIn('index.close', self.events)
        runtime.close()
        self.assertTrue(runtime.closed)
        self.assertEqual(self.events.count('first.store.close'), 2)
        self.assertEqual(self.events.count('first.save'), 1)
        self.assertEqual(self.events.count('second.store.close'), 1)
        self.assertEqual(self.events.count('index.close'), 2)

    def test_shutdown_failure_keeps_its_store_for_retry_but_closes_other_owners(self):
        runtime = Runtime(self.events, self.root, {'shutdown': 1})
        with self.assertRaises(OSError):runtime.close()
        self.assertFalse(runtime.closed)
        self.assertIn('second.store.close', self.events)
        self.assertNotIn('first.store.close', self.events)
        runtime.close()
        self.assertTrue(runtime.closed)
        self.assertEqual(self.events.count('first.shutdown'), 2)

    def test_normal_close_is_idempotent(self):
        runtime = Runtime(self.events, self.root, {})
        runtime.close()
        before = list(self.events)
        runtime.close()
        self.assertTrue(runtime.closed)
        self.assertEqual(self.events, before)

    def test_save_and_backup_failure_keeps_state_for_retry(self):
        runtime = Runtime(self.events, self.root, {'save': 1})
        first = runtime.executions['first']
        with patch.object(DraftCache, 'write', side_effect=OSError('synthetic full disk')):
            with self.assertRaises(OSError) as caught:runtime.close()
        self.assertIn('synthetic full disk', str(caught.exception))
        self.assertFalse(first.closed)
        self.assertFalse(runtime.closed)
        self.assertNotIn('first.store.close', self.events)
        self.assertTrue(runtime.executions['second'].closed)
        self.assertEqual(first.state['draft'], '')
        runtime.close()
        self.assertTrue(first.closed)
        self.assertTrue(runtime.closed)
        self.assertEqual(self.events.count('first.save'), 2)

    def test_document_backup_is_attempted_if_execution_backup_fails(self):
        runtime = Runtime(self.events, self.root, {'save': 1})
        first = runtime.executions['first']
        first.document = SimpleNamespace(path=self.root/'file', title='file')
        attempts = []
        def preserve(tab):
            attempts.append(tab.document)
            target = self.root/'retained.json';target.write_text('preserved')
            return str(target)
        first.workspace_owner = SimpleNamespace(close_recovery=SimpleNamespace(preserve=preserve))
        with patch.object(DraftCache, 'write', side_effect=OSError('synthetic cache fault')):
            with self.assertRaises(OSError):runtime.close()
        self.assertEqual(attempts,[first.document])
        self.assertTrue(runtime.closed)
        self.assertTrue(Path(first.document_recovery_path).is_file())
