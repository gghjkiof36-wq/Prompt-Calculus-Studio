"""Native-shaped executed/history fixtures through the DEFAULT observer.

Prompt tuple/status shapes and success-before-task_done order follow the local
ComfyUI 0.38.0 reference. No adapter or self-asserted seal fields are installed.
"""
import asyncio
import copy
import json
import sqlite3
import tempfile
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from test_result_manifest import Fixture, Service, png, m, events, background, until, RouteClient, route_app


class NativeFixture(Fixture):
    def __init__(self, root, graph=None, *, bind=True):
        self.root = Path(root)
        for name in ('output', 'temp'):
            (self.root/name).mkdir(exist_ok=True)
        self.service = Service(self.root/'integration', self.root/'output', self.root/'temp')
        self.store = self.service.result_manifests
        self.graph = graph or {'9': {'class_type': 'SaveImage', 'inputs': {}}}
        self.owner = dict(library_id='library', workspace_id='workspace', document_id='document')
        self.identity = dict(owner=self.owner, server_instance=self.service.events.server_instance,
                             operation=dict(kind='native', id='operation'), prompt_id='prompt',
                             graph_digest=m.digest(self.graph))
        operation = dict(id='operation', state='queued', prompt_id='prompt', payload=dict(prompt=self.graph),
                         snapshot=dict(library_id='library', state=dict(workspace='workspace')))
        with self.service.connect() as db:
            db.execute('INSERT INTO native_operations VALUES (?,?)', ('operation', json.dumps(operation)))
        self.header = self.store.bind(self.identity) if bind else None
        self.history = {}
        self.reads = 0
        self.sent = []
        self.queue = types.SimpleNamespace(get_current_queue_volatile=lambda: ([], []), get_history=self.get_history)
        self.publisher = events.NativeEventBuffer(self.service, self.queue)
        self.server = types.SimpleNamespace(send_sync=lambda *args: self.sent.append(args))

    def get_history(self, *, prompt_id):
        assert prompt_id == 'prompt'
        self.reads += 1
        return copy.deepcopy(self.history)

    def start(self):
        background.install_event_hook(self.server, self.publisher)
        assert self.publisher.results.adapter is None

    def images(self, count, *, offset=0):
        return [row['image'] for row in self.items(count, offset=offset)]

    def executed(self, node, images, **extra):
        data = dict(prompt_id='prompt', node=node, display_node=node, output=dict(images=images), **extra)
        self.server.send_sync('executed', data, 'native-client')
        assert self.sent[-1] == ('executed', data, 'native-client')

    def completed(self, outputs, *, expected=None):
        self.history = {'prompt': dict(prompt=[0, 'prompt', self.graph, {}, list(self.graph) if expected is None else expected],
            outputs=outputs, status=dict(status_str='success', completed=True,
                messages=[['execution_start', {'prompt_id': 'prompt'}],
                          ['execution_success', {'prompt_id': 'prompt'}]]))}

    def terminal(self, kind='success'):
        self.server.send_sync('execution_'+kind, dict(prompt_id='prompt'))


class NativeManifestTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, graph=None, *, bind=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = NativeFixture(tmp.name, graph, bind=bind)
        f.start()
        self.addCleanup(f.publisher.stop)
        return f

    def assert_final(self, f, count):
        header = f.latest()
        rows = f.all_items()
        self.assertTrue(header['sealed'])
        self.assertEqual(header['item_source'], 'history')
        self.assertEqual(header['expected_count'], count)
        self.assertEqual(header['final_digest'], m.digest(rows))
        self.assertEqual([row['ordinal'] for row in rows], list(range(count)))
        self.assertTrue(all(row['occurrence'] == 'history' for row in rows))
        return rows

    async def test_single_node_candidates_then_history_and_asset_reuse_key(self):
        f = self.fixture()
        images = f.images(3)
        f.executed('9', images)
        await until(lambda: f.latest()['item_count'] == 3)
        early = f.latest()
        candidates = f.all_items(early)
        self.assertFalse(early['sealed'])
        self.assertEqual(early['item_source'], 'candidates')
        copied = {row['asset_key']: f.asset(f.page(early), row['ordinal']) for row in candidates}
        f.completed({'9': dict(images=images)})
        f.terminal()
        await until(lambda: f.latest()['sealed'])
        rows = self.assert_final(f, 3)
        # D-B can reuse downloaded bytes by exact node/descriptor/hash key.
        self.assertEqual([row['asset_key'] for row in rows], [row['asset_key'] for row in candidates])
        self.assertEqual([copied[row['asset_key']] for row in rows], [f.asset(ordinal=i) for i in range(3)])
        self.assertEqual(f.all_items(early), candidates)

    async def test_multiple_nodes_history_order_wins_over_arrival_and_nested_display_path(self):
        f = self.fixture({'9': {'class_type': 'SaveImage'}, '10': {'class_type': 'PreviewImage'}})
        a, b = f.images(2), f.images(2, offset=2)
        f.executed('10', b, node_path=['group', '10'])
        f.executed('9', a)
        await until(lambda: f.latest()['item_count'] == 4)
        self.assertEqual(f.all_items()[0]['node_path'], ['group', '10'])
        f.completed({'9': dict(images=a), '10': dict(images=b)})
        f.terminal()
        await until(lambda: f.latest()['sealed'])
        rows = self.assert_final(f, 4)
        self.assertEqual([r['node_id'] for r in rows], ['9', '9', '10', '10'])
        self.assertEqual(rows[2]['node_path'], ['10'])

    async def test_137_single_node_and_burst_not_truncated(self):
        for burst in (False, True):
            with self.subTest(burst=burst):
                f = self.fixture()
                images = f.images(137)
                for batch in ([images] if not burst else [[image] for image in images]):
                    f.executed('9', batch)
                await until(lambda: f.latest()['item_count'] == 137, timeout=25)
                f.completed({'9': dict(images=images)})
                f.terminal()
                await until(lambda: f.latest()['sealed'])
                self.assert_final(f, 137)
                self.assertEqual([len(f.page(after=n)['items']) for n in (-1, 63, 127)], [64, 64, 9])

    async def test_partial_and_all_cache_with_old_png_metadata(self):
        for all_cache in (False, True):
            with self.subTest(all_cache=all_cache):
                f = self.fixture({'9': {'class_type': 'SaveImage'}, '10': {'class_type': 'SaveImage'}})
                a, b = f.images(2), f.images(2, offset=2)
                for image in b:
                    png(f.root/'output'/image['filename'], dict(prompt_studio=dict(submission_id='previous-prompt')))
                if not all_cache:
                    f.executed('9', a)
                    await until(lambda: f.latest()['item_count'] == 2)
                f.server.send_sync('execution_cached', dict(prompt_id='prompt', nodes=['9', '10'] if all_cache else ['10']))
                f.completed({'9': dict(images=a), '10': dict(images=b)})
                f.terminal()
                await until(lambda: f.latest()['sealed'])
                self.assert_final(f, 4)
                self.assertTrue(f.asset(ordinal=3))

    async def test_no_image_history_explicit_text_output_is_zero(self):
        f = self.fixture({'7': {'class_type': 'TextOutput'}})
        f.completed({'7': dict(text=['done'])})
        f.terminal()
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 0)
        self.assertTrue(f.latest()['zero_output'])

    async def test_flattened_history_keys_do_not_require_candidate_path_mapping(self):
        f = self.fixture({'group': {'class_type': 'CustomSubgraph'}})
        images = f.images(2)
        f.executed('group.9', images, node_path=['group', '9'])
        await until(lambda: f.latest()['item_count'] == 2)
        early_keys = {row['asset_key'] for row in f.all_items()}
        f.completed({'expanded-child': dict(images=images)})
        f.terminal()
        await until(lambda: f.latest()['sealed'])
        rows = self.assert_final(f, 2)
        self.assertEqual([r['node_id'] for r in rows], ['expanded-child']*2)
        self.assertFalse(early_keys.intersection(row['asset_key'] for row in rows))

    async def test_ambiguous_empty_custom_output_does_not_prove_zero(self):
        f = self.fixture({'7': {'class_type': 'CustomOutput'}})
        f.completed({'7': {}})
        f.terminal()
        await until(lambda: f.latest()['terminal_state'] == 'success')
        self.assertFalse(f.latest()['sealed'])
        f.completed({'7': dict(text=['no images'])})
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 0)

    async def test_missing_then_partial_history_then_complete_without_second_terminal(self):
        f = self.fixture({'9': {'class_type': 'SaveImage'}, '10': {'class_type': 'SaveImage'}})
        a, b = f.images(1), f.images(1, offset=1)
        f.terminal()
        await until(lambda: f.latest()['terminal_state'] == 'success')
        self.assertFalse(f.latest()['sealed'])
        f.completed({'9': dict(images=a)})
        before = f.reads
        await until(lambda: f.reads > before)
        self.assertFalse(f.latest()['sealed'])
        f.completed({'9': dict(images=a), '10': dict(images=b)})
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 2)

    async def test_all_cache_missing_or_empty_expected_images_never_zero(self):
        f = self.fixture()
        f.server.send_sync('execution_cached', dict(prompt_id='prompt', nodes=['9']))
        f.completed({})
        f.terminal()
        await until(lambda: f.latest()['terminal_state'] == 'success')
        for output in ({}, {'9': dict(images=[])}):
            f.completed(output)
            before = f.reads
            await until(lambda: f.reads > before)
            self.assertFalse(f.latest()['sealed'])
        f.completed({'9': dict(images=f.images(1))})
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 1)

    async def test_same_node_twice_identical_callbacks_distinct_display_occurrences(self):
        f = self.fixture()
        images = f.images(2)
        f.executed('9', images)
        f.executed('9', images)
        await until(lambda: f.latest()['item_count'] == 4)
        early = f.latest()
        self.assertEqual([r['occurrence'] for r in f.all_items()], ['0', '0', '1', '1'])
        f.completed({'9': dict(images=images)})
        f.terminal()
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 2)
        self.assertEqual(len(f.all_items(early)), 4)

    async def test_error_and_interrupted_keep_candidates_even_with_success_history(self):
        for terminal in ('error', 'interrupted'):
            with self.subTest(terminal=terminal):
                f = self.fixture()
                images = f.images(2)
                f.executed('9', images)
                await until(lambda: f.latest()['item_count'] == 2)
                f.terminal(terminal)
                await until(lambda: f.latest()['terminal_state'] == terminal)
                f.completed({'9': dict(images=images)})
                await asyncio.to_thread(f.store.reconcile_native, 'prompt', f.queue)
                self.assertFalse(f.latest()['sealed'])
                self.assertEqual(f.latest()['item_count'], 2)
                self.assertTrue(f.asset())

    async def test_final_replacement_does_not_reuse_changed_bytes(self):
        f = self.fixture()
        images = f.images(1)
        f.executed('9', images)
        await until(lambda: f.latest()['item_count'] == 1)
        early = f.page()
        original_key = early['items'][0]['asset_key']
        png(f.root/'output'/images[0]['filename'], dict(seed='changed'))
        f.completed({'9': dict(images=images)})
        f.terminal()
        await until(lambda: f.latest()['sealed'])
        self.assertNotEqual(f.all_items()[0]['asset_key'], original_key)
        with self.assertRaises(ValueError):
            f.asset(early)
        self.assertTrue(f.asset())

    async def test_late_bind_and_restart_recover_durable_candidates_and_success(self):
        f = self.fixture(bind=False)
        images = f.images(2)
        f.executed('9', images)
        f.terminal()
        await until(lambda: not f.publisher.results.items)
        f.header = f.store.bind(f.identity)
        await until(lambda: f.latest()['item_count'] == 2)
        early = f.latest()
        f.publisher.stop()
        f.service = Service(f.root/'integration', f.root/'output', f.root/'temp')
        f.store = f.service.result_manifests
        f.completed({'9': dict(images=images)})
        f.publisher = events.NativeEventBuffer(f.service, f.queue)
        f.start()
        self.addCleanup(f.publisher.stop)
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 2)
        self.assertEqual(len(f.all_items(early)), 2)

    async def test_history_identity_incomplete_and_missing_asset_are_retried(self):
        f = self.fixture()
        images = f.images(1)
        f.completed({'9': dict(images=images)})
        correct = copy.deepcopy(f.history)
        for change in ('prompt', 'graph', 'completed', 'asset'):
            f.history = copy.deepcopy(correct)
            entry = f.history['prompt']
            if change == 'prompt': entry['prompt'][1] = 'foreign'
            if change == 'graph': entry['prompt'][2] = {'foreign': {}}
            if change == 'completed': entry['status']['completed'] = False
            if change == 'asset': entry['outputs']['9']['images'][0]['filename'] = 'missing.png'
            f.terminal()
            before = f.reads
            await until(lambda: f.reads > before)
            self.assertFalse(f.latest()['sealed'], change)
        f.history = correct
        f.publisher.results.request('prompt')
        await until(lambda: f.latest()['sealed'])
        self.assert_final(f, 1)

    async def test_history_items_header_outbox_rollback_is_atomic(self):
        f = self.fixture()
        images = f.images(2)
        f.executed('9', images)
        await until(lambda: f.latest()['item_count'] == 2)
        f.publisher.stop()
        f.completed({'9': dict(images=images)})
        early = f.latest()
        with patch.object(f.service.events, 'append', side_effect=sqlite3.OperationalError('outbox fault')):
            with self.assertRaises(sqlite3.OperationalError):
                f.store.seal_history(early['manifest_id'], f.history)
        self.assertEqual(f.latest(), early)
        with f.service.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM result_items').fetchone()[0], 0)
        f.store.seal_history(early['manifest_id'], f.history)
        self.assert_final(f, 2)

    async def test_candidate_transaction_retry_does_not_consume_occurrence_twice(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = NativeFixture(tmp.name)
        buffer = f.publisher.results
        images = f.images(2)
        buffer.observe('executed', dict(prompt_id='prompt', node='9', output=dict(images=images)))
        with patch.object(f.service.events, 'append', side_effect=sqlite3.OperationalError('outbox fault')):
            buffer.drain()
        self.assertEqual(f.latest()['item_count'], 0)
        buffer.retry_at = 0
        buffer.drain()
        self.assertEqual([row['occurrence'] for row in f.all_items()], ['0', '0'])
        buffer.observe('executed', dict(prompt_id='prompt', node='9', output=dict(images=images)))
        buffer.drain()
        self.assertEqual([row['occurrence'] for row in f.all_items()], ['0', '0', '1', '1'])
        buffer.stop()

    async def test_recovery_failure_retries_without_busy_wake(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        f = NativeFixture(tmp.name)
        buffer = f.publisher.results
        original = f.store.pending_native
        attempts = []
        def scan():
            attempts.append(time.monotonic())
            if len(attempts) == 1:
                raise sqlite3.OperationalError('temporarily locked')
            return original()
        with patch.object(f.store, 'pending_native', scan):
            buffer.start()
            self.addCleanup(buffer.stop)
            await until(lambda: not buffer.recovery_pending)
            self.assertEqual(len(attempts), 2)
            self.assertGreaterEqual(attempts[1]-attempts[0], .95)
            self.assertLessEqual(buffer.wait_returns, 2)

    async def test_idle_worker_no_timed_wake_and_stop_is_prompt(self):
        f = self.fixture()
        await asyncio.sleep(.1)  # Let the one startup recovery scan finish.
        before = f.publisher.results.wait_returns
        writes = f.store.write_transactions
        await asyncio.sleep(1.1)
        self.assertEqual(f.publisher.results.wait_returns-before, 0)
        self.assertEqual(f.store.write_transactions, writes)
        start = time.perf_counter()
        await asyncio.to_thread(f.publisher.results.stop)
        self.assertLess(time.perf_counter()-start, .5)
        self.assertFalse(f.publisher.results.thread.is_alive())

    async def test_capability_requires_default_worker_and_history_reader(self):
        f = self.fixture()
        buffer = f.publisher.results
        self.assertTrue(buffer.available())
        client = RouteClient(route_app(f.service))
        async def advertised():
            response = await client.get('/prompt_studio/events/capability', headers={'X-Prompt-Studio': 'fixture-token'})
            self.assertEqual(response.status, 200)
            return (await response.json())['capabilities']
        self.assertIn(m.CAPABILITY, await advertised())
        with patch.object(buffer, 'queue', None):
            self.assertFalse(buffer.available())
            self.assertNotIn(m.CAPABILITY, await advertised())
        with patch.object(buffer, 'adapter', lambda *_: None): self.assertFalse(buffer.available())
        buffer.stop()
        self.assertFalse(buffer.available())
        self.assertNotIn(m.CAPABILITY, await advertised())


if __name__ == '__main__':
    unittest.main()
