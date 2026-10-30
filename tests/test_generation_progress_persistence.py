"""Progress stays live without rewriting frozen jobs on every node/heartbeat."""
import copy
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal

from prompt_calculus_studio.generation_runner import GenerationRunner


class Client(QObject):
    stateChanged = Signal()

    def __init__(self, db):
        super().__init__()
        self.notices = []
        self.window = SimpleNamespace(store=SimpleNamespace(db=db), notice=self.notices.append)
        self.url = 'http://127.0.0.1:1'
        self.epoch = 0
        self.stopped = False
        self.input_flow = None
        self.requests = []

    def request(self, route, data=None, done=None, failed=None, **kwargs):
        self.requests.append((route, data, done, failed))


class GenerationProgressPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'journal.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.client = Client(self.db)
        self.runner = GenerationRunner(self.client)
        self.job = dict(id='local', prompt_id='actual', server=self.client.url, workspace='fixture',
                        state='running', created=1., started=2., node='3', node_started=2.,
                        last_seen=2., payload={'prompt': {'3': {'inputs': {'text': 'frozen  \n'}}}},
                        parameter_receipt={'fields': []})
        self.runner.jobs[self.job['id']] = self.job
        self.runner.save(self.job)

    def observe(self, now, node='3', state='running'):
        status = dict(running_ids=['actual'] if state == 'running' else [],
                      queued_ids=['actual'] if state == 'queued' else [], executing_node=node)
        with patch('prompt_calculus_studio.generation_runner.time.time', return_value=now):
            self.runner.observe(status)

    def durable(self):
        return json.loads(self.db.execute('SELECT body FROM generation_jobs WHERE id=?',
                                         (self.job['id'],)).fetchone()[0])

    def reopened(self):
        db = sqlite3.connect(self.path)
        self.addCleanup(db.close)
        client = Client(db)
        return client, GenerationRunner(client)

    def test_same_node_heartbeats_and_node_changes_do_not_write_frozen_job(self):
        before = self.durable()
        writes = self.db.total_changes
        self.observe(3.)
        self.observe(9.)
        self.observe(12., '6')
        self.observe(20., '6')
        self.assertEqual(self.db.total_changes, writes)
        self.assertEqual(self.durable(), before)
        self.assertEqual((self.job['node'], self.job['node_started'], self.job['last_seen']),
                         ('6', 12., 20.))
        self.assertEqual(self.client.requests, [])

    def test_state_transitions_still_persist_current_progress_and_started(self):
        self.job.update(state='queued')
        self.job.pop('started')
        self.runner.save(self.job)
        writes = self.db.total_changes
        self.observe(10., '5')
        saved = self.durable()
        self.assertGreater(self.db.total_changes, writes)
        self.assertEqual((saved['state'], saved['started'], saved['node'], saved['last_seen']),
                         ('running', 10., '5', 10.))
        self.observe(11., '6')
        self.assertEqual(self.durable(), saved)
        self.observe(12., state='queued')
        saved = self.durable()
        self.assertEqual((saved['state'], saved['node'], saved['node_started'], saved['last_seen']),
                         ('queued', '6', 11., 12.))

    def test_legacy_running_job_persists_first_started_time_only_once(self):
        self.job.pop('started')
        self.runner.save(self.job)
        self.observe(10.)
        self.assertEqual(self.durable()['started'], 10.)
        writes = self.db.total_changes
        self.observe(20., '6')
        self.assertEqual(self.db.total_changes, writes)
        self.assertEqual(self.runner.record('local')['started'], 10.)

    def test_failed_transition_write_retries_without_losing_first_started_time(self):
        for old_state in ('queued', 'running'):
            with self.subTest(old_state=old_state):
                self.job.update(state=old_state, node='3', last_seen=2.)
                self.job.pop('started', None)
                self.runner.save(self.job)
                original = self.durable()
                self.db.execute("""CREATE TEMP TRIGGER fail_progress BEFORE INSERT ON generation_jobs
                    BEGIN SELECT RAISE(ABORT, 'temporary journal failure'); END""")
                with self.assertRaisesRegex(sqlite3.IntegrityError, 'temporary journal failure'):
                    self.observe(10., '5')
                self.assertEqual(self.durable(), original)
                self.db.execute('DROP TRIGGER fail_progress')
                self.observe(11., '6')
                saved = self.durable()
                self.assertEqual((saved['state'], saved['started'], saved['node'], saved['last_seen']),
                                 ('running', 10., '6', 11.))
                writes = self.db.total_changes
                self.observe(20., '7')
                self.assertEqual(self.db.total_changes, writes)
                self.assertEqual(self.durable(), saved)
                self.assertEqual(self.client.requests, [])

    def test_record_and_records_overlay_only_progress_without_mutable_aliases(self):
        original = self.durable()
        self.observe(10., '6')
        # Unsaved non-progress fields must not escape through the display overlay.
        self.job['error'] = 'not durable'
        self.job['payload']['prompt']['3']['inputs']['text'] = 'unsaved mutation'
        for record in (self.runner.record('local'), self.runner.records()[0]):
            self.assertEqual((record['node'], record['node_started'], record['last_seen']),
                             ('6', 10., 10.))
            self.assertEqual(record['payload'], original['payload'])
            self.assertNotIn('error', record)
            record['node'] = 'caller change'
            record['payload']['prompt']['3']['inputs']['text'] = 'caller mutation'
        self.assertEqual(self.job['node'], '6')
        self.assertEqual(self.job['payload']['prompt']['3']['inputs']['text'], 'unsaved mutation')
        self.assertEqual(self.durable(), original)
        self.assertIsNone(self.runner.record('missing'))

    def test_legacy_missing_progress_and_terminal_records_keep_saved_values(self):
        for key in ('node', 'node_started', 'last_seen'):
            self.job.pop(key)
        self.runner.save(self.job)
        self.assertFalse({'node', 'node_started', 'last_seen'} & self.runner.record('local').keys())
        self.runner.jobs.pop('local')
        self.job.update(state='complete', node='old', node_started=3., last_seen=4.)
        self.runner.save(self.job)
        self.assertEqual(self.runner.record('local'), self.durable())
        self.assertEqual(self.runner.records(), [self.durable()])

    def test_completion_and_failure_save_final_progress_before_removing_live_job(self):
        original = copy.deepcopy(self.job['payload'])
        for failed in (False, True):
            with self.subTest(failed=failed):
                self.job.update(state='running', node='3', node_started=2., last_seen=2.)
                self.runner.jobs['local'] = self.job
                self.runner.save(self.job)
                self.observe(10., '6')
                self.observe(16., '6')
                self.observe(17., state='missing')
                route, _, done, _ = self.client.requests.pop()
                self.assertEqual(route, '/history/actual')
                status = dict(completed=not failed, status_str='error' if failed else 'success',
                              messages=[['execution_error', {'exception_message': 'fixture failure'}]] if failed else [])
                done({'actual': {'status': status, 'outputs': {}}})
                self.assertNotIn('local', self.runner.jobs)
                _, reopened = self.reopened()
                saved = reopened.record('local')
                self.assertEqual(saved['state'], 'failed' if failed else 'complete')
                self.assertEqual((saved['node'], saved['node_started'], saved['last_seen']),
                                 ('6', 10., 16.))
                self.assertEqual(saved['payload'], original)

    def test_receipt_persists_progress_but_stale_receipt_still_cannot_write(self):
        pending = {'fields': [{'seed_mode': 'increment', 'next_value': None}]}
        self.job.update(native_operation='operation', parameter_receipt=pending)
        self.runner.save(self.job)
        self.runner.refresh_parameters(self.job, force=True)
        route, data, done, _ = self.client.requests.pop()
        self.assertEqual((route, data), ('workflow/native/status', {'id': 'operation'}))
        self.client.epoch += 1
        before = self.durable()
        receipt = {'fields': [{'seed_mode': 'increment', 'next_value': 42}]}
        done({'prompt_id': 'actual', 'parameter_receipt': receipt})
        self.assertEqual(self.durable(), before)
        self.assertEqual(self.job['parameter_receipt'], pending)
        self.job.update(node='6', node_started=10., last_seen=16.)
        self.runner.refresh_parameters(self.job, force=True)
        self.client.requests.pop()[2]({'prompt_id': 'actual', 'parameter_receipt': receipt})
        saved = self.durable()
        self.assertEqual(saved['parameter_receipt'], receipt)
        self.assertEqual((saved['node'], saved['node_started'], saved['last_seen']), ('6', 10., 16.))
        receipt['fields'][0]['next_value'] = 999
        self.assertEqual(self.job['parameter_receipt']['fields'][0]['next_value'], 42)

    def test_reopen_without_latest_progress_reconciles_history_and_never_resubmits(self):
        self.observe(10., '6')
        client, reopened = self.reopened()
        self.assertEqual(reopened.record('local')['node'], '3')
        with patch('prompt_calculus_studio.generation_runner.time.time', return_value=40.):
            reopened.observe({'running_ids': [], 'queued_ids': []})
            self.assertEqual([r[0] for r in client.requests], ['/history/actual'])
            client.requests.pop()[2]({})
        self.assertEqual(reopened.record('local')['state'], 'unconfirmed')
        self.assertEqual(client.requests, [])
        self.assertEqual(reopened.record('local')['payload'], self.durable()['payload'])


if __name__ == '__main__':
    unittest.main()
