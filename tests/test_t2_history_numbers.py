"""Numeric normalization must not change submission or manifest identity."""
import copy
import json
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from _comfy_history import normalized_prompt
from test_result_manifest_native import NativeFixture, m
from prompt_calculus_studio.native_graph import graph_matches
from prompt_calculus_studio.queued_work import result_state


GRAPH = {
    '3': dict(class_type='KSampler', inputs=dict(denoise=1, cfg=7, seed=9007199254740993)),
    '4': dict(class_type='LoraLoader', inputs=dict(strength_model=1, strength_clip=1)),
    '8': dict(class_type='CustomFloat', inputs=dict(scale=1)),
    '9': dict(class_type='PreviewImage', inputs=dict(images=['3', 0])),
}


class HistoryNumberTests(unittest.TestCase):
    def fixture(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        return NativeFixture(tmp.name, copy.deepcopy(GRAPH))

    def test_finite_numeric_equivalence_without_schema_or_lossy_casts(self):
        for left, right in ((1, 1.0), (0, -0.0), (2**100, float(2**100)),
                            ({'a': [1, None, True, '1']}, {'a': [1.0, None, True, '1']})):
            with self.subTest(left=left):
                a = {'x': dict(class_type='CustomFloat', inputs=dict(value=left))}
                b = {'x': dict(class_type='CustomFloat', inputs=dict(value=right))}
                self.assertTrue(graph_matches(a, b))
                self.assertTrue(graph_matches(b, a))
        for left, right in ((True, 1), (False, 0.0), (1, '1'), (None, 0),
                            (9007199254740993, float(9007199254740993)),
                            (1, 1.0000000000000002), ([1], (1,)),
                            ({'a': 1}, {'b': 1}), ([1, 2], [2, 1]),
                            (float('nan'), float('nan')), (float('inf'), float('inf')),
                            (float('-inf'), float('-inf'))):
            with self.subTest(left=left, right=right):
                self.assertFalse(graph_matches({'x': {'inputs': {'v': left}}}, {'x': {'inputs': {'v': right}}}))
        self.assertFalse(graph_matches({'x': {}}, {'x': {'inputs': {}}}))

    def test_seal_normalized_history_retains_original_digest_and_bind_identity(self):
        f = self.fixture()
        before = json.dumps(f.graph, sort_keys=True)
        f.completed({'9': dict(images=f.images(4))}, expected=['9'])
        self.assertNotEqual(m.digest(f.history['prompt']['prompt'][2]), f.identity['graph_digest'])
        self.assertFalse(f.store.seal_history(f.header['manifest_id'], f.history))
        header = f.latest()
        self.assertTrue(header['sealed'])
        self.assertEqual(header['identity'], f.identity)
        self.assertEqual(header['manifest_id'], f.header['manifest_id'])
        self.assertEqual(header['item_count'], 4)
        self.assertEqual(header['final_digest'], m.digest(f.all_items()))
        self.assertEqual(json.dumps(f.graph, sort_keys=True), before)
        self.assertEqual(f.store.bind(f.identity)['manifest_id'], header['manifest_id'])

    def test_seal_rejects_non_numeric_changes_and_missing_or_changed_original(self):
        f = self.fixture()
        f.completed({'9': dict(images=f.images(1))}, expected=['9'])
        correct = copy.deepcopy(f.history)
        for value in (True, '1', 0.5, float('nan'), float('inf'), float('-inf')):
            f.history = copy.deepcopy(correct)
            f.history['prompt']['prompt'][2]['3']['inputs']['denoise'] = value
            self.assertTrue(f.store.seal_history(f.header['manifest_id'], f.history))
            self.assertFalse(f.latest()['sealed'])
        with f.service.connect() as db:
            original = db.execute("SELECT body FROM native_operations WHERE id='operation'").fetchone()[0]
            operation = json.loads(original)
            # Even an equivalent rewrite of the *submitted* graph must not
            # change the stored digest/identity contract.
            operation['payload']['prompt'] = normalized_prompt(f.graph)
            db.execute("UPDATE native_operations SET body=? WHERE id='operation'", (json.dumps(operation),))
        self.assertTrue(f.store.seal_history(f.header['manifest_id'], correct))
        with self.assertRaises(ValueError): f.store.bind(f.identity)
        with f.service.connect() as db: db.execute("DELETE FROM native_operations WHERE id='operation'")
        self.assertTrue(f.store.seal_history(f.header['manifest_id'], correct))

    def test_native_reconcile_terminal_and_cancel_accept_normalized_queue(self):
        f = self.fixture()
        native = f.service.native_queue
        marker = dict(native_operation='operation')
        operation = native.read('operation')
        operation.update(state='submitted', created=time.time(), graph=copy.deepcopy(GRAPH), marker=dict(generation=marker))
        native.save(operation)
        entry = [0, 'prompt', normalized_prompt(GRAPH), dict(extra_pnginfo=dict(prompt_studio=dict(generation=marker)))]
        native.reconcile('operation', [entry])
        self.assertEqual(native.read('operation')['state'], 'queued')
        history = {'prompt': dict(prompt=entry, status=dict(completed=True, status_str='success'))}
        native.observe_terminal('operation', history)
        self.assertTrue(native.read('operation')['terminal_result']['completed'])
        backend = SimpleNamespace(mutex=threading.RLock(), queue=[entry], currently_running={})
        self.assertEqual(native.cancel('operation', backend, lambda: self.fail('interrupt'))['state'], 'failed')
        self.assertEqual(backend.queue, [])

    def test_frozen_cancel_and_both_result_state_consumers(self):
        f = self.fixture()
        queue = f.service.work_queue
        marker = dict(generation=dict(queue_attempt='frozen'))
        payload = dict(prompt=copy.deepcopy(GRAPH), extra_data=dict(extra_pnginfo=dict(prompt_studio=marker)))
        saved = dict(id='frozen', state='queued', prompt_id='prompt', payload=payload)
        with f.service.connect() as db:
            db.execute('INSERT INTO queue_attempts VALUES (?,?)', ('frozen', json.dumps(saved)))
        entry = [0, 'prompt', normalized_prompt(GRAPH), payload['extra_data']]
        foreign = [1, 'foreign', {}, {}]
        backend = SimpleNamespace(mutex=threading.RLock(), queue=[entry, foreign], currently_running={})
        self.assertEqual(queue.cancel('frozen', backend, lambda: self.fail('interrupt'))['state'], 'failed')
        self.assertEqual(backend.queue, [foreign])
        history = dict(prompt=entry, outputs={}, status=dict(completed=True, status_str='success'))
        self.assertEqual(result_state(dict(graph=GRAPH, outputs=[]), 'prompt', history)[0], 'complete')
        # The extension imports the same Qt-free source through its shared path.
        import integration_test.shared.queued_work as shared
        self.assertEqual(shared.result_state(dict(graph=GRAPH, outputs=[]), 'prompt', history)[0], 'complete')

    def test_frozen_bind_and_seal_use_original_attempt_not_normalized_history(self):
        f = self.fixture()
        capture = dict(snapshot=dict(library_id='library', state=dict(workspace='workspace')))
        operation = dict(id='frozen', capture='capture', prompt_id='prompt', payload=dict(prompt=GRAPH))
        with f.service.connect() as db:
            db.execute('INSERT INTO queue_captures VALUES (?,?)', ('capture', json.dumps(capture)))
            db.execute('INSERT INTO queue_attempts VALUES (?,?)', ('frozen', json.dumps(operation)))
        identity = dict(f.identity, operation=dict(kind='frozen', id='frozen'))
        header = f.store.bind(identity)
        f.completed({'9': dict(images=f.images(1))}, expected=['9'])
        self.assertFalse(f.store.seal_history(header['manifest_id'], f.history))
        with f.service.connect() as db:
            final = f.store._header(db, header['manifest_id'])
        self.assertTrue(final['sealed'])
        self.assertEqual(final['identity'], identity)
        with self.assertRaises(ValueError):
            f.store.bind(dict(identity, graph_digest=m.digest(normalized_prompt(GRAPH))))


if __name__ == '__main__':
    unittest.main()
