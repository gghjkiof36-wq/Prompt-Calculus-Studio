"""Synthetic provenance contracts, actual hook/worker and pure route calls.

The injected adapter is a fixture, NOT a claim about native ComfyUI semantics.
"""
import ast
import asyncio
import copy
import functools
import hashlib
import importlib
import ipaddress
import json
import logging
import os
import secrets
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from urllib.parse import urlsplit
from unittest.mock import patch

from test_comfy_integration import Service, png, ROOT

m = importlib.import_module('integration_test.result_manifest')
events = importlib.import_module('integration_test.event_log')
background = importlib.import_module('integration_test.background_events')
p = importlib.import_module('integration_test.event_protocol')


class HTTPException(Exception):
    status = 403

    def __init__(self, *, text=''):
        super().__init__(text)


class Response:
    def __init__(self, *, body=b'', status=200, headers=None, **_):
        self.body, self.status, self.headers = body, status, headers or {}

    async def json(self):
        return json.loads(self.body)

    async def read(self):
        return self.body


class Routes(dict):
    def get(self, path):
        def register(function):
            self['GET', path] = function
            return function
        return register

    def post(self, path):
        def register(function):
            self['POST', path] = function
            return function
        return register


web = types.SimpleNamespace(HTTPException=HTTPException, HTTPForbidden=HTTPException,
    Response=Response, RouteTableDef=Routes,
    json_response=lambda value, status=200, dumps=json.dumps: Response(body=dumps(value).encode(), status=status))


class RouteClient:
    """Minimal transport stub; executes production decorators and handlers.

    No socket or aiohttp coverage is claimed. The request fields used by the
    real local/event guards remain explicit, including peer, Host and Origin.
    """
    def __init__(self, routes):
        self.routes = routes

    def make_url(self, path):
        return 'http://127.0.0.1:8188'+path

    async def get(self, path, *, params=None, headers=None, remote='127.0.0.1'):
        headers = headers or {}
        request = types.SimpleNamespace(query=params or {}, headers=headers, method='GET',
                                        remote=remote, host=headers.get('Host', '127.0.0.1:8188'))
        try:
            return await self.routes['GET', path](request)
        except HTTPException as exc:
            return Response(status=exc.status)


class Fixture:
    def __init__(self, root):
        self.root = Path(root)
        for name in ('output', 'temp'):
            (self.root/name).mkdir(exist_ok=True)
        self.service = Service(self.root/'integration', self.root/'output', self.root/'temp')
        self.store = self.service.result_manifests
        self.owner = dict(library_id='library', workspace_id='workspace', document_id='document')
        self.identity = dict(owner=self.owner, server_instance=self.service.events.server_instance,
                             operation=dict(kind='native', id='operation'), prompt_id='prompt',
                             graph_digest=m.digest({'9': {'class_type': 'SaveImage'}}))
        self.paths = [['9'], ['group-a', '9'], ['group-b', '9']]
        self.header = self.store.register(self.identity, self.paths)

    def items(self, count, *, offset=0, occurrence='occurrence-1', path=None, identical=False):
        result = []
        for index in range(count):
            ordinal = index+offset
            filename = 'same.png' if identical else f'image-{ordinal}.png'
            target = self.root/'output'/filename
            if not target.exists():
                png(target, dict(prompt_studio=dict(submission_id='prompt'), seed=ordinal))
            result.append(dict(occurrence=occurrence, node_path=path or ['9'], output_slot='images',
                               item_index=index, ordinal=ordinal,
                               image=dict(type='output', subfolder='', filename=filename)))
        return result

    def packet(self, items, *, source='capture', revision=1, **extra):
        return dict(identity=self.identity, source_id=source, source_revision=revision,
                    evidence_ref='fixture:exact-source', items=items, **extra)

    def latest(self):
        return self.store.headers(self.owner)['headers'][0]

    def page(self, header=None, **kwargs):
        header = header or self.latest()
        return self.store.page(header['manifest_id'], header['revision'], header['checksum'], self.owner, **kwargs)

    def all_items(self, header=None):
        result, after = [], -1
        while True:
            page = self.page(header, after=after)
            result.extend(page['items'])
            if page['done']:
                return result
            after = page['next_after']

    def seal(self, terminal='success', **overrides):
        rows = self.all_items()
        seal = dict(complete=True, terminal_state=terminal, expected_count=len(rows),
                    final_digest=m.digest(rows), zero_output=not rows)
        seal.update(overrides)
        return self.store.ingest(self.packet([], source='final', seal=seal))

    def asset(self, page=None, ordinal=0, **changes):
        page = page or self.page()
        header = page['header']
        row = next(row for row in page['items'] if row['ordinal'] == ordinal)
        kwargs = dict(ordinal=ordinal, asset_sha256=row['asset_sha256'],
                      expires=page['expires'], token=page['asset_token'])
        kwargs.update(changes)
        return self.store.asset(header['manifest_id'], header['revision'], header['checksum'], self.owner, **kwargs)


async def until(check, timeout=10):
    end = asyncio.get_running_loop().time()+timeout
    while not await asyncio.to_thread(check):
        if asyncio.get_running_loop().time() >= end:
            raise AssertionError('worker did not reach expected durable state')
        await asyncio.sleep(.01)


class ManifestTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)

    def hook(self, adapter=True, capacity=256):
        queue = types.SimpleNamespace(get_current_queue_volatile=lambda: ([], []), get_history=lambda **_: {})
        publisher = events.NativeEventBuffer(self.f.service, queue)
        publisher.results.capacity = capacity
        if adapter:
            publisher.results.adapter = lambda event, data: data.get('fixture_contract')
        sent = []
        server = types.SimpleNamespace(send_sync=lambda *args: sent.append(args))
        background.install_event_hook(server, publisher)
        self.addCleanup(publisher.stop)
        return server, publisher, sent

    async def test_137_single_node_large_capture_pages_reverse_asset_arrival(self):
        f = self.f
        server, publisher, sent = self.hook()
        items = f.items(137)
        packet = f.packet(items)
        data = dict(prompt_id='prompt', node='9', output=dict(images=[item['image'] for item in items]),
                    fixture_contract=packet, padding='x'*70000)
        self.assertGreater(len(p.encode(data)), 64*1024)
        # All original executed contents still reach the original sender.
        server.send_sync('executed', data, 'client')
        await until(lambda: f.latest()['item_count'] == 137)
        self.assertIs(sent[0][1], data)
        publisher.results.stop()
        header = f.seal()
        self.assertEqual([r['ordinal'] for r in f.all_items(header)], list(range(137)))
        self.assertEqual([len(f.page(header, after=n)['items']) for n in (-1, 63, 127)], [64, 64, 9])
        # Real asynchronous reads complete in reverse ordinal order.
        completed = []
        async def read(index):
            await asyncio.sleep((136-index)*.0005)
            page = f.page(header, after=index-1, limit=1)
            content = await asyncio.to_thread(f.asset, page, index)
            completed.append((index, hashlib.sha256(content).hexdigest()))
        await asyncio.gather(*(read(i) for i in range(137)))
        self.assertNotEqual([i for i, _ in completed], list(range(137)))
        self.assertEqual([hash_ for _, hash_ in sorted(completed)], [r['asset_sha256'] for r in f.all_items(header)])
        with f.service.connect() as db:
            refs = [json.loads(r[0]) for r in db.execute("SELECT body FROM event_log WHERE json_extract(body,'$.kind')='result.manifest'")]
        self.assertTrue(refs)
        self.assertTrue(all(len(p.encode(event['payload'])) < 256 and not event['prompt_id'] for event in refs))
        self.assertTrue(all(set(event['payload']) == {'manifest_id', 'revision', 'checksum'} for event in refs))

    async def test_multiple_nodes_reverse_order_replay_and_identical_hash_outputs(self):
        f = self.f
        server, _, _ = self.hook()
        a = f.packet(f.items(70, path=['group-a', '9'], identical=True), source='a')
        b = f.packet(f.items(67, offset=70, path=['group-b', '9']), source='b')
        for packet in (b, a, b, a):
            server.send_sync('executed', dict(prompt_id='prompt', fixture_contract=packet))
        await until(lambda: f.latest()['item_count'] == 137)
        self.assertEqual([r['ordinal'] for r in f.all_items()], list(range(137)))
        self.assertEqual(len({r['asset_sha256'] for r in f.all_items()[:70]}), 1)
        self.assertEqual(f.seal()['expected_count'], 137)

    async def test_same_node_two_occurrences_two_images_each(self):
        f = self.f
        first = f.packet(f.items(2, identical=True))
        second = f.packet(f.items(2, offset=2, occurrence='occurrence-2'), source='second')
        f.store.ingest(second)
        f.store.ingest(first)
        transactions = f.store.write_transactions
        before = f.latest()
        f.store.ingest(first)
        f.store.ingest(dict(first, source_id='another-notification'))
        self.assertEqual(f.store.write_transactions, transactions)
        self.assertEqual(f.latest(), before)
        self.assertEqual([r['occurrence'] for r in f.all_items()], ['occurrence-1']*2+['occurrence-2']*2)
        self.assertEqual(f.latest()['item_count'], 4)

    async def test_cache_all_cache_and_unknown_cache_do_not_guess_latest(self):
        f = self.f
        server, _, _ = self.hook()
        server.send_sync('execution_cached', dict(prompt_id='prompt', nodes=['9']))
        await until(lambda: f.store.evidence(f.header['manifest_id'], f.header['revision'], f.header['checksum'], f.owner)['observation'] is not None)
        self.assertFalse(f.latest()['sealed'])
        self.assertEqual(f.latest()['item_count'], 0)
        cached = f.packet(f.items(3), source='exact-cache-evidence')
        server.send_sync('execution_cached', dict(prompt_id='prompt', fixture_contract=cached))
        await until(lambda: f.latest()['item_count'] == 3)
        f.store.ingest(cached)
        self.assertEqual(f.seal()['expected_count'], 3)

    async def test_zero_requires_explicit_complete_terminal_and_zero_disposition(self):
        f = self.f
        self.assertFalse(f.latest()['sealed'])
        for changes in (dict(complete=False), dict(zero_output=False), dict(expected_count=1), dict(final_digest='0'*64)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                f.seal(**changes)
        header = f.seal()
        self.assertTrue(header['sealed'])
        self.assertTrue(header['zero_output'])
        self.assertEqual(header['expected_count'], 0)

    async def test_partial_error_and_interrupted_preserve_ordered_items(self):
        f = self.f
        f.store.ingest(f.packet(f.items(3)))
        for terminal in ('error', 'interrupted'):
            with self.subTest(terminal=terminal):
                if terminal == 'interrupted':
                    tmp = tempfile.TemporaryDirectory()
                    self.addCleanup(tmp.cleanup)
                    f = Fixture(tmp.name)
                    f.store.ingest(f.packet(f.items(3)))
                header = f.seal(terminal)
                self.assertEqual(header['result_state'], 'partial')
                self.assertEqual(header['expected_count'], 3)
                self.assertEqual(len(f.all_items()), 3)

    async def test_unmapped_nested_path_and_missing_occurrence_leave_pending_gap(self):
        f = self.f
        server, publisher, _ = self.hook()
        items = f.items(1, path=['display-label/9'])
        server.send_sync('executed', dict(prompt_id='prompt', fixture_contract=f.packet(items)))
        await until(lambda: self.gap_count() == 1)
        items = f.items(1)
        del items[0]['occurrence']
        server.send_sync('executed', dict(prompt_id='prompt', fixture_contract=f.packet(items)))
        await until(lambda: self.gap_count() == 2)
        self.assertEqual(f.latest()['item_count'], 0)
        self.assertFalse(f.latest()['sealed'])

    def gap_count(self):
        with self.f.service.connect() as db:
            return db.execute('SELECT COUNT(*) FROM result_gaps').fetchone()[0]

    async def test_hook_no_io_bounded_overflow_and_oversized_capture_gap(self):
        f = self.f
        buffer = m.ResultManifestBuffer(f.service, capacity=1)
        data = dict(prompt_id='prompt', output=dict(images=[dict(filename='image.png')]))
        with patch.object(f.service, 'connect', side_effect=AssertionError('hook SQLite')), patch.object(Path, 'open', side_effect=AssertionError('hook file IO')):
            buffer.observe('executed', data)
            buffer.observe('executed', dict(data, node='2'))
            buffer.observe('executed', dict(data, too_big='x'*(m.MAX_CAPTURE+1)))
        self.assertEqual(len(buffer.items), 1)
        self.assertLessEqual(buffer.bytes, m.MAX_QUEUE_BYTES)
        await asyncio.to_thread(buffer.drain)
        self.assertGreaterEqual(self.gap_count(), 1)
        self.assertTrue(f.service.events.recover_gap())
        self.assertEqual(f.latest()['item_count'], 0)

    async def test_native_input_cannot_self_assert_an_occurrence_or_seal(self):
        f = self.f
        server, _, _ = self.hook(adapter=False)
        packet = f.packet(f.items(2))
        server.send_sync('executed', dict(prompt_id='prompt', fixture_contract=packet))
        server.send_sync('execution_success', dict(prompt_id='prompt'))
        await until(lambda: self.observation_count() == 2)
        self.assertEqual(f.latest()['item_count'], 0)
        self.assertFalse(f.latest()['sealed'])

    async def test_unowned_capture_does_not_block_later_items_and_stop_flushes_gap(self):
        f = self.f
        buffer = m.ResultManifestBuffer(f.service, adapter=lambda _, data: data.get('fixture_contract'))
        buffer.observe('executed', dict(node='9'))
        buffer.observe('executed', dict(prompt_id='prompt', fixture_contract=f.packet(f.items(1))))
        await asyncio.to_thread(buffer.drain)
        self.assertEqual(len(buffer.items), 0)
        self.assertEqual(f.latest()['item_count'], 1)
        self.assertEqual(self.gap_count(), 1)
        empty = m.ResultManifestBuffer(f.service, capacity=0)
        empty.observe('executed', dict(prompt_id='prompt', node='9'))
        self.assertEqual(len(empty.items), 0)
        await asyncio.to_thread(empty.stop)
        self.assertEqual(empty.pending_gaps, {})
        self.assertEqual(self.gap_count(), 2)

    def observation_count(self):
        with self.f.service.connect() as db:
            return db.execute('SELECT COUNT(*) FROM result_observations').fetchone()[0]

    async def test_replayed_native_and_progress_write_no_manifest_transactions(self):
        f = self.f
        buffer = m.ResultManifestBuffer(f.service)
        data = dict(prompt_id='prompt', output={'images': []})
        buffer.observe('executed', data)
        await asyncio.to_thread(buffer.drain)
        count = f.store.write_transactions
        for _ in range(137):
            buffer.observe('progress', dict(prompt_id='prompt', value=1))
            buffer.observe('executed', data)
        while buffer.items:
            await asyncio.to_thread(buffer.drain)
        self.assertEqual(f.store.write_transactions, count)

    async def test_same_revision_different_digest_and_same_semantic_conflict_rejected(self):
        f = self.f
        packet = f.packet(f.items(1))
        f.store.ingest(packet)
        before = f.latest()
        altered = copy.deepcopy(packet)
        altered['evidence_ref'] = 'different-proof'
        with self.assertRaisesRegex(ValueError, 'revision digest'):
            f.store.ingest(altered)
        altered['source_revision'] = 2
        altered['items'][0]['ordinal'] = 1
        with self.assertRaisesRegex(ValueError, 'semantic'):
            f.store.ingest(altered)
        with self.assertRaisesRegex(ValueError, 'revision/checksum'):
            f.store.page(before['manifest_id'], before['revision'], '0'*64, f.owner)
        self.assertEqual(f.latest(), before)

    async def test_item_header_outbox_rollback_and_retry(self):
        f = self.f
        before = f.latest()
        packet = f.packet(f.items(2))
        def fault(event):
            if event['kind'] == 'result.manifest':
                raise sqlite3.OperationalError('injected outbox failure')
        f.service.events.fault = fault
        with self.assertRaises(sqlite3.OperationalError):
            f.store.ingest(packet)
        self.assertEqual(f.latest(), before)
        self.assertEqual(f.all_items(), [])
        f.service.events.fault = None
        f.store.ingest(packet)
        self.assertEqual(len(f.all_items()), 2)

    async def test_worker_db_failure_keeps_capture_and_retries(self):
        f = self.f
        buffer = m.ResultManifestBuffer(f.service, adapter=lambda _, data: data['fixture_contract'])
        buffer.observe('executed', dict(prompt_id='prompt', fixture_contract=f.packet(f.items(2))))
        with patch.object(f.store, 'ingest', side_effect=sqlite3.OperationalError('disk full')):
            await asyncio.to_thread(buffer.drain)
        self.assertEqual(len(buffer.items), 1)
        self.assertGreater(buffer.retry_at, 0)
        buffer.retry_at = 0
        await asyncio.to_thread(buffer.drain)
        self.assertEqual(len(buffer.items), 0)
        self.assertEqual(f.latest()['item_count'], 2)

    async def test_old_revision_pages_restart_log_floor_and_foreign_identity(self):
        f = self.f
        packet = f.packet(f.items(2))
        partial = f.store.ingest(packet)
        old_page = f.page(partial)
        f.store.ingest(f.packet(f.items(2, offset=2, occurrence='second'), source='second'))
        final = f.seal()
        self.assertEqual(len(f.all_items(partial)), 2)
        f.service.events.max_count = 1
        with f.service.connect() as db:
            f.service.events.prune(db)
        old_epoch = f.service.events.epoch
        service = Service(f.root/'integration', f.root/'output', f.root/'temp')
        self.assertNotEqual(old_epoch, service.events.epoch)
        store = service.result_manifests
        self.assertEqual(store.register(f.identity, f.paths), final)
        self.assertEqual(store.ingest(dict(packet, source_epoch=service.events.epoch)), final)
        with self.assertRaises(ValueError):
            store.asset(partial['manifest_id'], partial['revision'], partial['checksum'], f.owner,
                        ordinal=0, asset_sha256=old_page['items'][0]['asset_sha256'],
                        expires=old_page['expires'], token=old_page['asset_token'])
        for change in (dict(prompt_id='other'), dict(graph_digest='1'*64),
                       dict(owner=dict(f.owner, document_id='other')), dict(server_instance='foreign')):
            with self.subTest(change=change), self.assertRaises(ValueError):
                store.register(dict(f.identity, **change), f.paths)
        with self.assertRaises(ValueError):
            f.store.observe_native('executed', dict(prompt_id='prompt'), old_epoch)
        self.assertEqual(store.headers(f.owner)['headers'][0], final)

    async def test_exact_asset_expiry_scope_checksum_missing_overwrite_and_format(self):
        f = self.f
        items = f.items(1)
        f.store.ingest(f.packet(items))
        page = f.page()
        self.assertTrue(f.asset(page).startswith(b'\x89PNG'))
        for changes in (dict(expires=0), dict(token='wrong'), dict(asset_sha256='0'*64)):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                f.asset(page, **changes)
        header = page['header']
        with self.assertRaises(ValueError):
            f.store.page(header['manifest_id'], header['revision'], header['checksum'], dict(f.owner, document_id='foreign'))
        target = f.root/'output'/items[0]['image']['filename']
        png(target, dict(prompt_studio=dict(submission_id='foreign')))
        with self.assertRaises(ValueError):
            f.asset(page)
        target.unlink()
        with self.assertRaises(OSError):
            f.asset(page)
        target.write_bytes(b'not a png')
        with self.assertRaises(ValueError):
            f.store.ingest(f.packet(items, source='invalid'))

    async def test_traversal_input_absolute_ads_and_redirected_paths(self):
        f = self.f
        items = f.items(1)
        for image in (dict(type='input', subfolder='', filename='image-0.png'),
                      dict(type='output', subfolder='..', filename='image-0.png'),
                      dict(type='output', subfolder='/outside', filename='image-0.png'),
                      dict(type='output', subfolder='', filename='../image-0.png'),
                      dict(type='output', subfolder='', filename='C:\\image-0.png'),
                      dict(type='output', subfolder='', filename='image-0.png:stream'),
                      dict(type='output', subfolder='%2e%2e', filename='image-0.png'),
                      dict(type='output', subfolder='', filename='image.jpg')):
            with self.subTest(image=image), self.assertRaises(ValueError):
                f.store.ingest(f.packet([dict(items[0], image=image)]))
        actual_lstat = Path.lstat
        def reparse(path):
            if path == f.root/'output':
                return types.SimpleNamespace(st_mode=stat_mode, st_file_attributes=0x400)
            return actual_lstat(path)
        stat_mode = (f.root/'output').stat().st_mode
        with patch.object(Path, 'lstat', reparse), self.assertRaisesRegex(ValueError, 'Redirected'):
            f.store.ingest(f.packet(items))

    async def test_windows_path_and_handle_ctime_semantics_and_size_limit(self):
        f = self.f
        items = f.items(1)
        original = os.fstat
        def handle_stat(fd):
            info = original(fd)
            values = {key: getattr(info, key) for key in ('st_dev', 'st_ino', 'st_size', 'st_mtime_ns', 'st_ctime_ns')}
            values['st_ctime_ns'] += 12345
            return types.SimpleNamespace(**values)
        with patch.object(os, 'fstat', handle_stat):
            f.store.ingest(f.packet(items))
            self.assertTrue(f.asset().startswith(b'\x89PNG'))
        oversized = f.root/'output'/'too-big.png'
        with oversized.open('wb') as stream:
            stream.truncate(m.MAX_IMAGE+1)
        item = dict(items[0], ordinal=1, occurrence='large', image=dict(type='output', subfolder='', filename=oversized.name))
        with self.assertRaisesRegex(ValueError, 'size/type'):
            f.store.ingest(f.packet([item], source='oversized'))

    async def test_header_paging_frozen_as_of_and_bind_checks_persisted_owner_graph(self):
        f = self.f
        first = f.store.headers(f.owner, limit=1)
        value = copy.deepcopy(f.identity)
        value['operation']['id'] = 'other'
        value['prompt_id'] = 'other'
        f.store.register(value, [['9']])
        frozen = f.store.headers(f.owner, after=first['next_after'], as_of=first['as_of'])
        self.assertEqual(frozen['headers'], [])
        graph = {'9': {'class_type': 'SaveImage'}}
        operation = dict(id='bound', prompt_id='bound-prompt', snapshot=dict(library_id='library', state=dict(workspace='workspace')),
                         payload=dict(prompt=graph), state='queued')
        with f.service.connect() as db:
            db.execute('INSERT INTO native_operations VALUES (?,?)', ('bound', json.dumps(operation)))
        value.update(operation=dict(kind='native', id='bound'), prompt_id='bound-prompt')
        header = f.store.bind(value)
        self.assertFalse(header['sealed'])
        for change in (dict(graph_digest='1'*64), dict(prompt_id='wrong'), dict(owner=dict(f.owner, library_id='wrong'))):
            with self.subTest(change=change), self.assertRaises(ValueError):
                f.store.bind(dict(value, **change))


def route_app(service):
    """Compile actual route functions, using an isolated service instead of ComfyUI."""
    tree = ast.parse((ROOT/'comfyui_prompt_calculus_studio/__init__.py').read_text(encoding='utf-8'))
    names = {'local_route', 'event_route', 'event_query', 'event_response', 'body',
             'result_query', 'result_reference', 'result_bind', 'result_headers',
             'result_page', 'result_evidence', 'result_asset', 'event_capability', 'events_capability'}
    routes = web.RouteTableDef()
    context = dict(asyncio=asyncio, functools=functools, ipaddress=ipaddress, json=json,
                   logging=logging, secrets=secrets, urlsplit=urlsplit, web=web,
                   service=service, event_protocol=p, result_manifest=m, routes=routes,
                   event_publisher=getattr(service.events, 'publisher',
                       types.SimpleNamespace(results=types.SimpleNamespace(available=lambda: False))),
                   LOADED_BUILD={},
                   _token='fixture-token', CaptureRejected=type('CaptureRejected', (Exception,), {}))
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<actual result routes>', 'exec'), context)
    return routes


class ResultRouteTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)
        self.f.store.ingest(self.f.packet(self.f.items(2)))
        self.client = RouteClient(route_app(self.f.service))

    def query(self):
        f = self.f
        header = f.latest()
        scope = p.scope(dict(domain=p.DOMAIN, user_scope='default', library_id='library', workspace_id='workspace'))
        return dict(server_instance=f.service.events.server_instance, epoch=f.service.events.epoch,
                    scope=json.dumps(scope), scope_digest=p.scope_digest(scope), owner=json.dumps(f.owner),
                    manifest_id=header['manifest_id'], revision=str(header['revision']), checksum=header['checksum'])

    async def test_137_assets_through_routes_arrive_in_reverse_manifest_order(self):
        f = self.f
        f.store.ingest(f.packet(f.items(135, offset=2, occurrence='remaining'), source='remaining'))
        f.seal()
        query = self.query()
        headers = {'X-Prompt-Studio': 'fixture-token'}
        after, pages, rows = -1, [], []
        while True:
            response = await self.client.get('/prompt_studio/results/manifest', params=dict(query, after=str(after)), headers=headers)
            self.assertEqual(response.status, 200)
            page = await response.json()
            pages.append(page)
            rows.extend((item, page) for item in page['items'])
            if page['done']:
                break
            after = page['next_after']
        self.assertEqual([len(page['items']) for page in pages], [64, 64, 9])
        received = []
        # Deliver actual handler responses in reverse logical order. The result
        # contract must never use network arrival to assign an item ordinal.
        for item, page in reversed(rows):
            params = dict(query, ordinal=str(item['ordinal']), asset_sha256=item['asset_sha256'],
                          expires=str(page['expires']), asset_token=page['asset_token'])
            response = await self.client.get('/prompt_studio/results/asset', params=params, headers=headers)
            self.assertEqual(response.status, 200)
            content = await response.read()
            self.assertEqual(hashlib.sha256(content).hexdigest(), item['asset_sha256'])
            received.append(item['ordinal'])
        self.assertEqual(received, list(reversed(range(137))))
        self.assertEqual([item['ordinal'] for item in f.all_items()], list(range(137)))

    async def test_exact_asset_route_and_guards(self):
        query = self.query()
        headers = {'X-Prompt-Studio': 'fixture-token'}
        response = await self.client.get('/prompt_studio/results/manifest', params=query, headers=headers)
        self.assertEqual(response.status, 200)
        page = await response.json()
        asset_query = dict(query, ordinal='0', asset_sha256=page['items'][0]['asset_sha256'],
                           expires=str(page['expires']), asset_token=page['asset_token'])
        response = await self.client.get('/prompt_studio/results/asset', params=asset_query, headers=headers)
        self.assertEqual(response.status, 200)
        self.assertEqual(await response.read(), self.f.asset(page))
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        for bad_headers in ({}, {'X-Prompt-Studio': 'wrong'}, dict(headers, Host='evil:8188'),
                            dict(headers, Origin='null'), dict(headers, Origin='http://evil'),
                            dict(headers, **{'Sec-Fetch-Site': 'cross-site'})):
            with self.subTest(headers=bad_headers):
                response = await self.client.get('/prompt_studio/results/asset', params=asset_query, headers=bad_headers)
                self.assertEqual(response.status, 403)
        for changes in (dict(epoch='old'), dict(server_instance='foreign'), dict(checksum='0'*64),
                        dict(owner=json.dumps(dict(self.f.owner, document_id='foreign'))),
                        dict(asset_sha256='0'*64), dict(expires='0'), dict(asset_token='wrong')):
            with self.subTest(changes=changes):
                response = await self.client.get('/prompt_studio/results/asset', params=dict(asset_query, **changes), headers=headers)
                self.assertEqual(response.status, 400)
        response = await self.client.get('/prompt_studio/results/asset', params=asset_query,
                                         headers=headers, remote='192.0.2.1')
        self.assertEqual(response.status, 403)

    async def test_routes_require_exact_scope_and_allow_same_origin(self):
        query = self.query()
        headers = {'X-Prompt-Studio': 'fixture-token'}
        origin = str(self.client.make_url('/')).rstrip('/')
        response = await self.client.get('/prompt_studio/results/manifests', params=query, headers=dict(headers, Origin=origin))
        self.assertEqual(response.status, 200)
        scope = p.scope()
        query.update(scope=json.dumps(scope), scope_digest=p.scope_digest(scope))
        response = await self.client.get('/prompt_studio/results/manifests', params=query, headers=headers)
        self.assertEqual(response.status, 400)
        query = self.query()
        scope = json.loads(query['scope'])
        scope['entities'] = ['foreign-entity']
        query.update(scope=json.dumps(scope), scope_digest=p.scope_digest(scope))
        response = await self.client.get('/prompt_studio/results/manifest', params=query, headers=headers)
        self.assertEqual(response.status, 400)


if __name__ == '__main__':
    unittest.main()
