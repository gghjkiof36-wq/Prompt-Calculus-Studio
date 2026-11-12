"""Durable writer/outbox failures, native source semantics and legacy hook safety."""
import ast
import copy
import importlib
import json
import sqlite3
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from contextlib import contextmanager
from unittest.mock import patch
from test_comfy_integration import Service
import test_native_queue as native_fixture
import test_work_queue as frozen_fixture
from prompt_calculus_studio import comfy_event_protocol as protocol

event_log = importlib.import_module('integration_test.event_log')
extension_protocol = importlib.import_module('integration_test.event_protocol')
background = importlib.import_module('integration_test.background_events')
ROOT = Path(__file__).resolve().parents[1]


def assert_health(test, log, consistent, gap_count=None):
    test.assertEqual(log.heartbeat([])['log_consistent'], consistent)
    with log.connection() as db:
        stored, reason = db.execute('SELECT consistent,reason FROM event_meta').fetchone()
        gaps = [json.loads(r[0]) for r in db.execute(
            "SELECT body FROM event_log WHERE json_extract(body,'$.kind')=?", (protocol.LOG_GAP,))]
    test.assertEqual(stored, int(consistent))
    test.assertEqual(bool(reason), not consistent)
    if gap_count is not None: test.assertEqual(len(gaps), gap_count)
    return gaps


def recover_after_backoff(log):
    with patch.object(event_log.time, 'monotonic', return_value=time.monotonic()+2):
        return log.recover_gap()


class EventLogTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.service = Service(self.root, self.root, self.root)
        self.log = self.service.events

    def page(self): return self.log.page('0', protocol.scope())

    def test_each_registered_table_insert_update_delete_is_atomic_with_outbox(self):
        for table, key in extension_protocol.WRITERS.items():
            with self.subTest(table=table):
                with self.service.connect() as db:
                    columns = [r[1] for r in db.execute('PRAGMA table_info('+table+')')]
                row = {c: 'value' for c in columns}
                row[key] = table+'-id'
                if 'body' in row: row['body'] = json.dumps(dict(state='unconfirmed', id=row[key]))
                if 'created' in row: row['created'] = time.time()
                names = ','.join(columns); values = tuple(row[c] for c in columns)
                sql = 'INSERT INTO '+table+' ('+names+') VALUES ('+','.join('?' for _ in columns)+')'
                before = self.page()['latest_seq']
                def fail(_): raise sqlite3.OperationalError('injected outbox failure')
                self.log.fault = fail
                with self.assertRaises(sqlite3.OperationalError), self.service.connect() as db: db.execute(sql, values)
                self.log.fault = None
                with self.service.connect() as db:
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table+' WHERE '+key+'=?', (row[key],)).fetchone()[0], 0)
                    self.assertEqual(db.execute('SELECT COUNT(*) FROM event_changes').fetchone()[0], 0)
                self.assertEqual(self.page()['latest_seq'], before)
                assert_health(self, self.log, True, 0)
                with self.service.connect() as db: db.execute(sql, values)
                update=('UPDATE '+table+' SET body=? WHERE '+key+'=?', (json.dumps(dict(state='failed',id=row[key])),row[key])) if 'body' in columns else ('UPDATE '+table+' SET hash=? WHERE '+key+'=?',('changed',row[key]))
                delete=('DELETE FROM '+table+' WHERE '+key+'=?',(row[key],))
                for statement,parameters in (update,delete):
                    watermark=self.page()['latest_seq'];self.log.fault=fail
                    with self.assertRaises(sqlite3.OperationalError),self.service.connect() as db:db.execute(statement,parameters)
                    self.log.fault=None
                    self.assertEqual(self.page()['latest_seq'],watermark)
                    assert_health(self, self.log, True, 0)
                    with self.service.connect() as db:self.assertEqual(db.execute('SELECT * FROM '+table+' WHERE '+key+'=?',(row[key],)).fetchone(),values)
                with self.service.connect() as db:
                    if 'body' in columns: db.execute('UPDATE '+table+' SET body=? WHERE '+key+'=?', (json.dumps(dict(state='failed', id=row[key])), row[key]))
                    else: db.execute('UPDATE '+table+' SET hash=? WHERE '+key+'=?', ('changed', row[key]))
                with self.service.connect() as db: db.execute('DELETE FROM '+table+' WHERE '+key+'=?', (row[key],))
                items = [e for e in self.page()['events'] if e['entity'] == table+':'+row[key]]
                self.assertEqual(len(items), 3)
                self.assertTrue(items[-1]['payload']['deleted'])
                self.assertEqual([e['receipt_revision'] for e in items], [1, 2, 3])

    def test_receipt_failure_also_rolls_back_seq_and_projection(self):
        with self.assertRaises(sqlite3.IntegrityError), self.service.connect() as db:
            db.execute('INSERT INTO jobs VALUES (?,?,?)', ('p', '{}', 0))
            db.execute('INSERT INTO jobs VALUES (?,?,?)', ('p', '{}', 0))
        self.assertEqual(self.page()['events'], [])
        self.assertEqual(self.page()['latest_seq'], '0')
        assert_health(self, self.log, True, 0)

    def test_reload_changes_epoch_preserves_server_and_fences_old_writer(self):
        self.service.native_queue.save(dict(id='old', state='unconfirmed', created=time.time()))
        old_epoch = self.log.epoch
        rebuilt = Service(self.root, self.root, self.root)
        self.assertEqual(rebuilt.events.server_instance, self.log.server_instance)
        self.assertNotEqual(rebuilt.events.epoch, old_epoch)
        self.assertEqual(rebuilt.native_queue.read('old')['id'], 'old')
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'stale event publisher'):
            self.service.native_queue.save(dict(id='late', state='pending'))
        with self.assertRaises(ValueError): rebuilt.native_queue.read('late')
        assert_health(self, rebuilt.events, True, 0)

    def test_ownership_and_storage_failures_without_external_effects_remain_consistent(self):
        self.service.native_queue.save(dict(id='owned', state='unconfirmed', scope=dict(library_id='a')))
        with self.assertRaisesRegex(ValueError, 'ownership'):
            self.service.native_queue.save(dict(id='owned', state='unconfirmed', scope=dict(library_id='b')))
        assert_health(self, self.log, True, 0)
        self.assertEqual(self.service.native_queue.read('owned')['scope']['library_id'], 'a')
        for error in (sqlite3.OperationalError('database is locked'), OSError('IO failure')):
            with self.subTest(error=error):
                self.log.fault = lambda _: (_ for _ in ()).throw(error)
                with self.assertRaises(type(error)):
                    self.service.native_queue.save(dict(id='not-accepted', state='pending'))
                self.log.fault = None
                assert_health(self, self.log, True, 0)
                with self.assertRaises(ValueError): self.service.native_queue.read('not-accepted')

    def test_retention_count_bytes_age_and_receipt_survives(self):
        self.log.max_count = 3
        for index in range(5): self.service.native_queue.save(dict(id=str(index), state='unconfirmed'))
        page = self.page(); self.assertTrue(page['reset_required']); self.assertEqual(page['oldest_seq'], '3')
        self.assertEqual(self.service.native_queue.read('0')['id'], '0')
        self.log.max_bytes = 1
        with self.log.connection() as db: self.log.prune(db)
        self.assertEqual(self.page()['oldest_seq'], '6')
        self.log.max_bytes = protocol.RETENTION_BYTES
        self.service.native_queue.save(dict(id='new', state='queued'))
        self.log.clock = lambda: time.time()+protocol.RETENTION_SECONDS+1
        with self.log.connection() as db: self.log.prune(db)
        self.assertEqual(self.page()['oldest_seq'], '7')
        for index, now in enumerate((200_000, 100_000, 200_000)):
            self.log.clock=lambda value=now:value
            self.service.native_queue.save(dict(id='clock-'+str(index),state='queued'))
        # seq 8 expired after a clock correction. Keeping seq 7 would falsely
        # advertise continuous coverage while silently skipping seq 8.
        self.assertEqual(self.page()['oldest_seq'],'9')
        self.assertTrue(self.log.page('7',protocol.scope())['reset_required'])
        self.assertEqual([e['seq'] for e in self.log.page('8',protocol.scope())['events']],['9'])

    def test_native_buffer_no_disk_under_hook_and_progress_five_hz(self):
        buffer = event_log.NativeEventBuffer(self.service, capacity=4)
        with patch.object(self.service, 'connect', side_effect=AssertionError('executor disk IO')):
            for i in range(100): buffer.observe('progress', dict(prompt_id='p', node='5', value=i))
            buffer.observe('executed', dict(prompt_id='p', node='8', output={'images': [{'filename': 'a.png'}, {'filename': 'b.png'}]}))
        self.assertEqual(len(buffer.progress), 1)
        with patch.object(event_log.time, 'monotonic', return_value=1): buffer.drain()
        buffer.observe('progress', dict(prompt_id='p', node='5', value=101))
        with patch.object(event_log.time, 'monotonic', return_value=1.19): buffer.drain()
        self.assertEqual(len([e for e in self.page()['events'] if e['kind']=='native.progress']), 1)
        with patch.object(event_log.time, 'monotonic', return_value=1.21): buffer.drain()
        self.assertEqual(len([e for e in self.page()['events'] if e['kind']=='native.progress']), 2)
        for _ in range(5): buffer.observe('execution_start', dict(prompt_id='p'))
        self.assertFalse(self.log.heartbeat([])['log_consistent'])

    def test_native_append_failure_is_degraded_and_not_replayed_as_a_submit(self):
        buffer = event_log.NativeEventBuffer(self.service)
        buffer.observe('execution_success', dict(prompt_id='accepted'))
        self.log.fault = lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('disk full'))
        buffer.drain();self.log.fault=None
        self.assertFalse(self.log.heartbeat(['accepted'])['log_consistent'])
        self.assertEqual(self.page()['events'], [])
        assert_health(self, self.log, False, 0)
        rebuilt = Service(self.root, self.root, self.root)
        gap = assert_health(self, rebuilt.events, True, 1)[0]
        self.assertEqual(gap['seq'], '1')
        self.assertEqual(gap['payload']['previous_epoch'], self.log.epoch)
        self.assertEqual(gap['payload']['affected'], [dict(prompt_id='accepted')])
        self.assertIn('disk full', gap['payload']['reason'])

    def test_buffer_overflow_has_one_gap_and_contiguous_sequence_after_recovery(self):
        buffer = event_log.NativeEventBuffer(self.service, capacity=1)
        with patch.object(self.log, 'connection', side_effect=AssertionError('hook disk IO')):
            buffer.observe('execution_start', dict(prompt_id='kept'))
            buffer.observe('execution_success', dict(prompt_id='lost'))
        self.assertFalse(self.log.heartbeat([])['log_consistent'])
        buffer.drain()
        self.assertTrue(self.log.recover_gap())
        gap = assert_health(self, self.log, True, 1)[0]
        self.assertEqual(gap['payload']['reason'], 'native buffer overflow')
        self.assertEqual(gap['payload']['affected'], [dict(prompt_id='lost')])
        self.assertIsInstance(gap['payload']['occurred_at'], float)
        self.assertEqual([e['seq'] for e in self.page()['events']], ['1', '2'])
        self.assertFalse(recover_after_backoff(self.log))
        assert_health(self, self.log, True, 1)

    def test_ingestion_failure_is_reported_without_executor_disk_io(self):
        class BrokenKey:
            def __str__(self): raise RuntimeError('bad native data')
        buffer = event_log.NativeEventBuffer(self.service)
        with patch.object(self.log, 'connection', side_effect=AssertionError('hook disk IO')):
            buffer.observe('executed', {BrokenKey(): 'bad', 'prompt_id': 'known'})
        self.assertTrue(self.log.recover_gap())
        gap = assert_health(self, self.log, True, 1)[0]
        self.assertEqual(gap['payload']['reason'], 'native ingestion failure')
        self.assertEqual(gap['payload']['affected'], [dict(prompt_id='known')])

    def test_native_append_failure_recovers_in_same_epoch_with_bounded_gap_payload(self):
        buffer = event_log.NativeEventBuffer(self.service)
        buffer.observe('execution_success', dict(prompt_id='lost-native'))
        self.log.fault = lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('append unavailable'))
        buffer.drain()
        self.log.fault = None
        assert_health(self, self.log, False, 0)
        for n in range(20):
            self.log.record_loss('native buffer overflow', prompt_id=str(n)+'\U0001f30f'*250,
                                 entity='execution:'+str(n)+'\U0001f30f'*245, persist=False)
        self.log.record_loss('oversized identity lost', prompt_id='p'*1025, persist=False)
        self.assertTrue(recover_after_backoff(self.log))
        gap = assert_health(self, self.log, True, 1)[0]
        self.assertEqual(gap['epoch'], self.log.epoch)
        self.assertEqual(gap['seq'], '1')
        self.assertTrue(gap['payload']['affected_truncated'])
        self.assertLessEqual(len(protocol.encode(gap['payload'])), protocol.MAX_PAYLOAD_BYTES)
        self.assertIn(dict(prompt_id='lost-native'), gap['payload']['affected'])

    def test_legacy_false_reason_is_visible_as_first_gap_in_healthy_new_epoch(self):
        with self.log.connection() as db:
            db.execute("UPDATE event_meta SET consistent=0,reason='legacy persisted loss'")
        self.assertFalse(self.log.heartbeat([])['log_consistent'])
        rebuilt = Service(self.root, self.root, self.root)
        gap = assert_health(self, rebuilt.events, True, 1)[0]
        self.assertEqual(gap['seq'], '1')
        self.assertEqual(gap['payload']['reason'], 'legacy persisted loss')
        self.assertEqual(gap['payload']['previous_epoch'], self.log.epoch)

    def test_gap_and_consistent_update_commit_together_and_retry_does_not_skip_seq(self):
        self.log.record_loss('native event lost', prompt_id='p')
        with self.log.connection() as db:
            db.execute("""CREATE TRIGGER fail_gap_health BEFORE UPDATE OF consistent ON event_meta
                WHEN NEW.consistent=1 BEGIN SELECT RAISE(ABORT,'health commit failure'); END""")
        self.assertFalse(recover_after_backoff(self.log))
        assert_health(self, self.log, False, 0)
        self.assertEqual(self.page()['latest_seq'], '0')
        with self.log.connection() as db: db.execute('DROP TRIGGER fail_gap_health')
        # A failed attempt schedules the next retry from its completion time.
        with patch.object(event_log.time, 'monotonic', return_value=time.monotonic()+4):
            self.assertTrue(self.log.recover_gap())
        self.assertEqual(assert_health(self, self.log, True, 1)[0]['seq'], '1')

    def test_loss_during_gap_commit_is_not_cleared_with_the_previous_loss(self):
        self.log.record_loss('first loss', prompt_id='first', persist=False)
        def second_loss(_): self.log.record_loss('second loss', prompt_id='second', persist=False)
        self.log.fault = second_loss
        self.assertTrue(self.log.recover_gap())
        self.log.fault = None
        self.assertFalse(self.log.heartbeat([])['log_consistent'])
        self.assertTrue(recover_after_backoff(self.log))
        gaps = assert_health(self, self.log, True, 2)
        self.assertIn(dict(prompt_id='second'), gaps[-1]['payload']['affected'])

    def test_worker_retries_persistent_database_failure_at_most_once_per_second(self):
        self.log.record_loss('native append failed', prompt_id='p')
        original = self.log.connection
        attempts = []
        class FailingWrites:
            def __init__(self, db): self.db = db
            def execute(self, sql, *args):
                if sql.startswith('UPDATE event_meta SET consistent=0'):
                    attempts.append(time.monotonic())
                    raise sqlite3.OperationalError('database still unavailable')
                return self.db.execute(sql, *args)
            def __getattr__(self, name): return getattr(self.db, name)
        @contextmanager
        def failing_connection():
            with original() as db: yield FailingWrites(db)
        buffer = event_log.NativeEventBuffer(self.service)
        began = time.monotonic()
        with patch.object(self.log, 'connection', failing_connection), patch.object(self.service, 'maintain_events'):
            buffer.start()
            try: threading.Event().wait(3.1)
            finally: buffer.stop()
        elapsed = time.monotonic()-began
        self.assertGreaterEqual(len(attempts), 2)
        self.assertLessEqual(len(attempts), 4)
        self.assertTrue(all(b-a >= .99 for a, b in zip(attempts, attempts[1:])))
        assert_health(self, self.log, False, 0)
        self.assertTrue(recover_after_backoff(self.log))
        assert_health(self, self.log, True, 1)
        print(json.dumps(dict(measurement='loss_retry_backoff', seconds=elapsed,
                              event_meta_attempts=len(attempts), offsets=[t-began for t in attempts])))

    def test_maintenance_error_is_diagnostic_and_clears_after_success(self):
        buffer = event_log.NativeEventBuffer(self.service)
        with patch.object(self.service, 'maintain_events', side_effect=OSError('maintenance IO failure')):
            buffer.start()
            deadline = time.monotonic()+2
            while self.log.maintenance_error is None and time.monotonic() < deadline:
                threading.Event().wait(.01)
            heartbeat = self.log.heartbeat([])
            self.assertEqual(heartbeat['maintenance_error']['message'], 'maintenance IO failure')
            self.assertGreater(heartbeat['maintenance_error']['observed_at'], 0)
            assert_health(self, self.log, True, 0)
        try:
            deadline = time.monotonic()+2
            while self.log.maintenance_error is not None and time.monotonic() < deadline:
                threading.Event().wait(.01)
            self.assertIsNone(self.log.heartbeat([])['maintenance_error'])
            assert_health(self, self.log, True, 0)
        finally: buffer.stop()

    def test_stalled_publisher_is_visible_even_when_http_and_queue_are_available(self):
        buffer=event_log.NativeEventBuffer(self.service)
        buffer.thread=types.SimpleNamespace(is_alive=lambda:True)
        buffer.last_cycle=time.monotonic()-13
        heartbeat=self.log.heartbeat(['still-running'])
        self.assertFalse(heartbeat['publisher_alive']);assert_health(self, self.log, True, 0)
        self.assertEqual(heartbeat['running'],['still-running'])

    def test_single_hook_preserves_legacy_hundred_active_jobs_on_reload(self):
        sent=[];server=types.SimpleNamespace(send_sync=lambda *args:sent.append(args))
        class Publisher:
            def __init__(self):self.seen=[];self.stopped=False
            def observe(self,*args):self.seen.append(args)
            def start(self):pass
            def stop(self):self.stopped=True
        first=Publisher();legacy=background.install_event_hook(server,first)
        for i in range(100):legacy.register(str(i),'sid')
        with self.assertRaises(ValueError):legacy.register('overflow','sid')
        second=Publisher();self.assertIs(background.install_event_hook(server,second),legacy)
        server.send_sync('executed',dict(prompt_id='0',output={}), 'sid')
        self.assertTrue(first.stopped);self.assertEqual(first.seen,[])
        self.assertEqual(len(second.seen),1);self.assertEqual(len(sent),1)
        self.assertEqual(len(legacy.jobs),100)

    def test_native_038_source_fixtures_preserve_identity_outputs_and_terminal_evidence(self):
        fixture=json.loads((ROOT/'tests/fixtures/comfy_events_0_38.json').read_text(encoding='utf8'))
        buffer=event_log.NativeEventBuffer(self.service)
        for scenario in fixture['scenarios']:
            for event,data in scenario['events']:buffer.observe(event,data,'web')
        buffer.drain()
        events=self.page()['events']
        no_prompt=[e for e in events if e['payload'].get('node')=='reconnect-node']
        self.assertEqual(no_prompt[0]['prompt_id'],'')
        output=next(e for e in events if e['payload'].get('node')=='group.0.8' and e['kind']=='native.executed')
        self.assertEqual(output['payload']['display_node'],'group')
        self.assertEqual(len(output['payload']['output']['images']),2)
        parent=next(e for e in events if e['payload'].get('node')=='group' and e['kind']=='native.executed')
        self.assertLess(int(parent['seq']),int(output['seq']))
        cached_empty=next(e for e in events if e['prompt_id']=='cache' and e['payload'].get('node')=='4')
        self.assertIsNone(cached_empty['payload']['output'])
        self.assertIn('native.execution_cached',[e['kind'] for e in events])
        for terminal in ('execution_success','execution_error','execution_interrupted'):
            self.assertIn('native.'+terminal,[e['kind'] for e in events])
        self.assertFalse(any(e['prompt_id']=='queued-cancel' and e['kind']=='native.execution_interrupted' for e in events))
        error=next(e for e in events if e['kind']=='native.execution_error')
        self.assertNotIn('current_inputs',error['payload'])

    def test_payload_limit_and_overflow_reference(self):
        with self.assertRaises(ValueError),self.log.connection() as db:
            self.log.append(db,'test',dict(value='x'*protocol.MAX_PAYLOAD_BYTES),entity='test')
        buffer=event_log.NativeEventBuffer(self.service)
        buffer.observe('executed',dict(prompt_id='p',node='8',output='x'*200_000));buffer.drain()
        self.assertTrue(self.page()['events'][0]['payload']['fetch_required'])

    def test_oversized_metadata_cannot_persist_an_event_that_stalls_pagination(self):
        for size in (1023, protocol.MAX_PAGE_BYTES):
            # JSON quotes count too; otherwise a small event with an enormous
            # entity could fill snapshot after/next_after without any receipts.
            with self.assertRaisesRegex(sqlite3.DataError,'envelope'),self.service.connect() as db:
                self.log.append(db,'test',dict(state='queued'),entity='x'*size)
        self.assertEqual(self.page()['latest_seq'],'0')
        assert_health(self, self.log, True, 0)
        buffer=event_log.NativeEventBuffer(self.service)
        buffer.observe('executed',dict(prompt_id='valid-prompt',node='x'*1_000_000,output={}))
        buffer.drain()
        page=self.page();self.assertEqual(page['covered_to'],'1')
        self.assertIsNone(page['events'][0]['payload']['node'])
        self.assertLessEqual(len(protocol.encode(page)),protocol.MAX_PAGE_BYTES)

    def test_deadlines_run_without_poll_and_long_generation_stays_queued(self):
        self.service.native_queue.save(dict(id='pending',state='pending',created=time.time()-31))
        self.service.native_queue.save(dict(id='long',state='queued',created=time.time()-3600,prompt_id='p'))
        self.service.maintain_events()
        self.assertEqual(self.service.native_queue.read('pending')['state'],'failed')
        self.assertEqual(self.service.native_queue.read('long')['state'],'queued')
        self.assertIn('diagnostic_at',self.service.native_queue.read('long'))

    def test_input_ack_tombstone_survives_restart_and_cannot_enqueue_same_click(self):
        bridge=self.service.native_queue.inputs
        identity=dict(workflow='',path='A.json',frontend_id='A')
        entry=dict(owner='owner',head='item',workflow='flow',frontend_id='A',path='A.json',paused=False)
        value=dict(client='db',session='desktop',entries=[entry])
        bridge.publish(value)
        self.assertTrue(bridge.claim(dict(id='click',identity=identity))['handled'])
        bridge.publish(dict(value,ack=['click']))
        rebuilt=Service(self.root,self.root,self.root)
        self.assertTrue(rebuilt.native_queue.inputs.claim(dict(id='click',identity=identity))['handled'])
        with rebuilt.connect() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM input_requests').fetchone()[0],0)
        with self.assertRaises(ValueError):rebuilt.native_queue.inputs.claim(dict(id='click',identity=dict(identity,frontend_id='foreign')))


class NativeReceiptChannelTests(unittest.TestCase):
    setUp=native_fixture.NativeQueueTests.setUp
    prepared=native_fixture.NativeQueueTests.prepared

    def executor(self, entries=()):
        return types.SimpleNamespace(mutex=threading.RLock(),queue=list(entries),currently_running={},get_history=lambda **_: {})

    def test_queue_accepted_then_db_failure_cannot_resubmit(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        sent=[payload['prompt_id']]
        self.service.events.fault=lambda _:(_ for _ in ()).throw(sqlite3.OperationalError('accepted then disk fail'))
        with self.assertRaises(sqlite3.OperationalError):
            self.queue.reply(dict(self.live,id='operation-1',prompt_id=payload['prompt_id']))
        self.service.events.fault=None
        self.assertFalse(self.service.events.heartbeat(sent)['log_consistent'])
        assert_health(self, self.service.events, False, 0)
        self.assertTrue(recover_after_backoff(self.service.events))
        gap = assert_health(self, self.service.events, True, 1)[0]
        self.assertIn(dict(prompt_id=sent[0], entity='native_operations:operation-1'), gap['payload']['affected'])
        self.assertEqual(self.queue.start(self.request)['prompt_id'],sent[0])
        self.assertEqual(self.queue.poll(self.live)['commands'],[])
        self.assertEqual(len(sent),1)

    def test_queued_cancel_has_two_durable_receipts_without_interrupted(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        self.queue.reply(dict(self.live,id='operation-1',prompt_id=payload['prompt_id']))
        queue=self.executor([(0,payload['prompt_id'],payload['prompt'],payload['extra_data'])])
        self.queue.cancel('operation-1',queue,lambda:self.fail('queued must not interrupt'))
        self.assertEqual(queue.queue,[])
        kinds=[e['kind'] for e in self.service.events.page('0',protocol.scope())['events']]
        self.assertIn('operation.cancel_requested',kinds);self.assertIn('operation.cancel_confirmed',kinds)
        self.assertNotIn('native.execution_interrupted',kinds)

    def test_fenced_missing_rejects_late_submit_and_unavailable_cannot_retire(self):
        payload=self.prepared();stale=self.queue.read('operation-1')
        current=copy.deepcopy(stale);current.update(state='unconfirmed',submit_inflight=False);self.queue.save(current)
        proof=self.queue.recovery('operation-1',self.executor())['recovery']
        self.assertTrue(proof['fenced'])
        with self.assertRaisesRegex(sqlite3.IntegrityError,'fenced'):self.queue.save(stale)
        assert_health(self, self.service.events, True, 0)
        self.service.prepare_prompt(payload);self.assertEqual(payload['prompt'],{})
        self.queue.abandon('operation-1',self.executor())
        self.assertTrue(self.queue.read('operation-1')['abandoned'])
        other=dict(id='unknown',state='unconfirmed',created=time.time(),submit_inflight=False)
        self.queue.save(other)
        unavailable=self.executor();unavailable.get_history=None
        with self.assertRaises(ValueError):self.queue.abandon('unknown',unavailable)
        self.assertFalse(self.queue.read('unknown').get('abandoned',False))

    def test_service_reload_does_not_fence_a_late_same_process_submit(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        rebuilt=Service(self.root,self.root,self.root)
        proof=rebuilt.native_queue.recovery('operation-1',self.executor())['recovery']
        self.assertEqual(proof['location'],'submitting');self.assertFalse(proof['can_abandon'])

    def test_cancel_fence_rejects_a_writer_that_read_before_the_cancel(self):
        self.prepared();stale=self.queue.read('operation-1')
        self.queue.cancel_pending()
        stale.update(state='submitted',prompt_id='late')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'fenced'):self.queue.save(stale)
        assert_health(self, self.service.events, True, 0)

    def test_running_cancel_terminal_confirmation_and_evidence_timeout(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        self.queue.reply(dict(self.live,id='operation-1',prompt_id=payload['prompt_id']))
        queue=self.executor();queue.currently_running={1:(0,payload['prompt_id'],payload['prompt'],payload['extra_data'])}
        calls=[];self.queue.cancel('operation-1',queue,lambda:calls.append(1))
        value=self.queue.read('operation-1');value['cancel_deadline_at']=time.time()-1;self.queue.save(value)
        self.service.maintain_events()
        self.assertTrue(self.queue.read('operation-1')['cancel_unconfirmed'])
        self.assertFalse(self.queue.read('operation-1')['cancel_confirmed'])
        buffer=event_log.NativeEventBuffer(self.service)
        buffer.observe('execution_interrupted',dict(prompt_id=payload['prompt_id'],node_id='5'));buffer.drain()
        self.assertTrue(self.queue.read('operation-1')['cancel_confirmed']);self.assertEqual(calls,[1])

    def test_terminal_history_repairs_missing_push_without_changing_old_state_contract(self):
        payload=self.prepared();self.service.prepare_prompt(payload)
        entry=(0,payload['prompt_id'],payload['prompt'],payload['extra_data'])
        queue=self.executor();queue.get_current_queue_volatile=lambda:([],[])
        history={payload['prompt_id']:dict(prompt=entry,status=dict(completed=True,status_str='success',messages=[['execution_success',{'prompt_id':payload['prompt_id']}]]))}
        queue.get_history=lambda **_:history
        self.service.maintain_events(queue)
        receipt=self.queue.read('operation-1')
        self.assertEqual(receipt['state'],'queued');self.assertTrue(receipt['queue_terminal'])
        self.assertEqual(receipt['terminal_result']['messages'],['execution_success'])
        self.assertIn('operation.terminal',[e['kind'] for e in self.service.events.page('0',protocol.scope())['events']])


class FrozenReceiptChannelTests(unittest.TestCase):
    setUp=frozen_fixture.QueueTests.setUp
    upgrade=frozen_fixture.QueueTests.upgrade
    capture=frozen_fixture.QueueTests.capture
    request_for=frozen_fixture.QueueTests.request_for
    post=frozen_fixture.QueueTests.post
    run_submit=frozen_fixture.QueueTests.run_submit

    def executor(self, entries=()):
        return types.SimpleNamespace(mutex=threading.RLock(),queue=list(entries),currently_running={},get_history=lambda **_: {})

    def test_frozen_cancel_confirmed_and_missing_fence_persist(self):
        work=self.capture();saved=self.run_submit(work)
        entry=(0,saved['prompt_id'],saved['payload']['prompt'],saved['payload']['extra_data'])
        queue=self.executor([entry]);self.service.work_queue.cancel(saved['id'],queue,lambda:self.fail())
        self.assertEqual(queue.queue,[])
        kinds=[e['kind'] for e in self.service.events.page('0',protocol.scope())['events'] if e['entity']=='queue_attempts:'+saved['id']]
        self.assertIn('operation.submit',kinds);self.assertIn('operation.queued',kinds)
        self.assertIn('operation.cancel_requested',kinds);self.assertIn('operation.cancel_confirmed',kinds)
        result=self.service.work_queue.recovery(saved['id'],queue,retire=True)
        self.assertTrue(result['recovery']['fenced']);self.assertEqual(result['state'],'abandoned')
        with self.assertRaisesRegex(sqlite3.IntegrityError,'fenced'):self.service.work_queue.save_attempt(saved)
        assert_health(self, self.service.events, True, 0)

    def test_frozen_accepted_db_failure_no_duplicate_and_foreign_cancel_rejected(self):
        work=self.capture();calls=[]
        async def post(payload):
            calls.append(copy.deepcopy(payload));self.service.prepare_prompt(payload)
            self.service.events.fault=lambda _:(_ for _ in ()).throw(sqlite3.OperationalError('post accepted'))
            return 200,dict(prompt_id=payload['prompt_id'])
        with self.assertRaises(sqlite3.OperationalError):self.run_submit(work,post=post)
        self.service.events.fault=None
        saved=self.run_submit(work,post=post);self.assertEqual(len(calls),1)
        self.assertFalse(self.service.events.heartbeat([])['log_consistent'])
        assert_health(self, self.service.events, False, 0)
        self.assertTrue(recover_after_backoff(self.service.events))
        assert_health(self, self.service.events, True, 1)
        queue=self.executor([(0,saved['prompt_id'],saved['payload']['prompt'],{})])
        with self.assertRaisesRegex(ValueError,'歸屬'):self.service.work_queue.cancel(saved['id'],queue,lambda:self.fail())
        self.assertEqual(len(queue.queue),1)


if __name__=='__main__':unittest.main()
