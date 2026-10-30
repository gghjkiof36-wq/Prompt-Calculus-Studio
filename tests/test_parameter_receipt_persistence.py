"""Native receipt and Stage acceptance commit together without heartbeat writes."""
import copy
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from prompt_calculus_studio.generation_runner import GenerationRunner
from prompt_calculus_studio.stage_store import StageStore
from test_generation_progress_persistence import Client
from test_stage_parameters import config


class ParameterReceiptPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'journal.sqlite'
        self.db = sqlite3.connect(self.path); self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.client = Client(self.db)
        self.runner = GenerationRunner(self.client)
        self.store = StageStore(self.db)
        self.chain = SimpleNamespace(store=self.store, closed=False, later=Mock())
        self.client.input_flow = SimpleNamespace(chain=self.chain)
        self.config = config()
        self.config['patches'][0].update(field='seed', type='INT', value=10, base=500,
            min=0, max=2**50, seed_mode='increment', seed_timing='after', intent_id='intent')
        self.attempt = self.store.add('attempt', 'workspace', 'run', status='submitted', stage='one',
            parent='entry', snapshot_state={'multi_output': {'stages': {'one': {'parameters': self.config}}}})
        self.receipt = dict(version=1, identity=copy.deepcopy(self.config['identity']),
            owner=dict(workspace='workspace', stage='one', parent='entry'), fields=[dict(
                node='35', path=['35'], class_type='KSampler', field='seed', schema='schema-1',
                requested=10, actual=10, seed_mode='increment', seed_timing='after')])
        self.job = dict(id=self.attempt['id'], native_operation='native-operation', prompt_id='prompt-1',
            state='running', created=1., server=self.client.url, workspace='workspace', chain={'run': 'run'},
            payload={'prompt': {'35': {'class_type': 'KSampler', 'inputs': {'seed': 10}}},
                     'extra_data': {'extra_pnginfo': {'prompt_studio_request': {
                         'generation': {'stage_parameters': copy.deepcopy(self.receipt)}}}}})
        self.runner.jobs[self.job['id']] = self.job
        self.runner.save(self.job)
        self.sql = []; self.db.set_trace_callback(self.sql.append)

    def refresh(self, receipt=None, prompt='prompt-1'):
        self.assertTrue(self.runner.refresh_parameters(self.job, force=True))
        route, data, done, _ = self.client.requests.pop()
        self.assertEqual((route, data), ('workflow/native/status', {'id': 'native-operation'}))
        done(dict(prompt_id=prompt, parameter_receipt=copy.deepcopy(receipt or self.receipt)))

    def durable(self):
        # A distinct connection observes commits, not the connection's own
        # uncommitted writes after an injected second-row or COMMIT failure.
        with closing(sqlite3.connect(self.path)) as db:
            job = json.loads(db.execute('SELECT body FROM generation_jobs WHERE id=?',
                                        (self.job['id'],)).fetchone()[0])
            seeds = [json.loads(row[0]) for row in db.execute(
                "SELECT body FROM stage_journal WHERE kind='parameter_state' ORDER BY position")]
        return job, seeds

    def job_writes(self):
        return [sql for sql in self.sql if sql.upper().startswith('INSERT OR REPLACE INTO GENERATION_JOBS')]

    def commits(self):
        return [sql for sql in self.sql if sql.strip().upper() == 'COMMIT']

    def test_first_pending_is_saved_once_and_equal_receipt_does_not_write_or_commit(self):
        self.refresh()
        saved, seeds = self.durable()
        self.assertEqual(saved['parameter_receipt'], self.receipt)
        self.assertEqual(len(seeds), 1); self.assertIsNone(seeds[0]['next_seed'])
        self.assertEqual(len(self.commits()), 1)
        before = self.durable(); writes = self.db.total_changes; self.sql.clear()
        self.runner._pending_progress_save.add(self.job['id'])
        for _ in range(3): self.refresh()
        self.assertEqual(self.db.total_changes, writes)
        self.assertEqual(self.commits(), []); self.assertEqual(self.job_writes(), [])
        self.assertEqual(self.durable(), before)
        self.assertIn(self.job['id'], self.runner._pending_progress_save)
        self.assertEqual(self.chain.later.call_count, 4)

    def test_next_value_commits_both_rows_and_publishes_without_aliasing(self):
        self.refresh(); self.sql.clear()
        receipt = copy.deepcopy(self.receipt); receipt['fields'][0]['next_value'] = 11
        self.runner._pending_progress_save.add(self.job['id'])
        self.refresh(receipt)
        saved, seeds = self.durable()
        self.assertEqual(saved['parameter_receipt'], receipt)
        self.assertEqual(seeds[0]['next_seed'], 11)
        self.assertEqual(self.job['parameter_receipt'], receipt)
        self.assertEqual(len(self.commits()), 1)
        self.assertNotIn(self.job['id'], self.runner._pending_progress_save)
        self.assertEqual(self.store.resolve_parameters('workspace', 'one', self.config)['patches'][0]['resolved'], 11)
        receipt['fields'][0]['next_value'] = 999
        self.assertEqual(self.job['parameter_receipt']['fields'][0]['next_value'], 11)

    def test_second_stage_write_failure_rolls_back_job_and_retries_terminal_receipt(self):
        # Finished jobs can still be awaiting their native seed receipt.
        self.job['state'] = 'complete'; self.runner.save(self.job)
        self.store.update(self.attempt['id'], status='complete')
        before = self.durable(); live = copy.deepcopy(self.job)
        self.db.execute("""CREATE TEMP TRIGGER fail_stage BEFORE INSERT ON stage_journal
            WHEN NEW.kind='parameter_state' BEGIN SELECT RAISE(ABORT,'stage write failure'); END""")
        self.runner._pending_progress_save.add(self.job['id'])
        receipt = copy.deepcopy(self.receipt); receipt['fields'][0]['next_value'] = 11
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'stage write failure'):
            self.refresh(receipt)
        self.assertEqual(self.durable(), before); self.assertEqual(self.job, live)
        self.assertIn(self.job['id'], self.runner._pending_progress_save)
        self.assertFalse(self.db.in_transaction); self.assertEqual(self.store.transaction_depth, 0)
        self.chain.later.assert_not_called()
        self.db.execute('DROP TRIGGER fail_stage')
        self.refresh(receipt)
        self.assertEqual(self.durable()[1][0]['next_seed'], 11)
        self.assertNotIn(self.job['id'], self.runner._pending_progress_save)
        self.chain.later.assert_called_once()

    def test_actual_commit_failure_rolls_back_both_rows_without_publishing(self):
        # A deferred constraint fails at real SQLite COMMIT, after both writes
        # have succeeded. Nested context managers must not commit early.
        self.db.executescript("""
            CREATE TABLE receipt_guard_parent (id INTEGER PRIMARY KEY);
            CREATE TABLE receipt_guard_child (parent INTEGER REFERENCES receipt_guard_parent(id)
                DEFERRABLE INITIALLY DEFERRED);
            CREATE TEMP TRIGGER fail_receipt_commit AFTER INSERT ON stage_journal
                WHEN NEW.kind='parameter_state' BEGIN INSERT INTO receipt_guard_child VALUES (99); END;
        """)
        before = self.durable(); live = copy.deepcopy(self.job); self.sql.clear()
        self.runner._pending_progress_save.add(self.job['id'])
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'FOREIGN KEY constraint failed'):
            self.refresh()
        self.assertTrue(self.commits())
        self.assertTrue(any(sql.strip().upper() == 'ROLLBACK' for sql in self.sql))
        self.assertEqual(self.durable(), before); self.assertEqual(self.job, live)
        self.assertIn(self.job['id'], self.runner._pending_progress_save)
        self.assertFalse(self.db.in_transaction); self.assertEqual(self.store.transaction_depth, 0)
        self.chain.later.assert_not_called()
        self.db.execute('DROP TRIGGER fail_receipt_commit')
        self.refresh()
        self.assertEqual(len(self.durable()[1]), 1)
        self.assertEqual(self.job['parameter_receipt'], self.receipt)

    def test_equal_job_receipt_still_retries_missing_stage_acceptance(self):
        # Recovered history may already have the job receipt while the Stage
        # projection is absent. Equal job data must not suppress its retry.
        self.job['parameter_receipt'] = copy.deepcopy(self.receipt); self.runner.save(self.job)
        self.db.execute("""CREATE TEMP TRIGGER fail_stage BEFORE INSERT ON stage_journal
            WHEN NEW.kind='parameter_state' BEGIN SELECT RAISE(ABORT,'stage write failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'stage write failure'): self.refresh()
        self.assertEqual(self.durable()[1], [])
        self.db.execute('DROP TRIGGER fail_stage'); self.sql.clear()
        self.refresh()
        self.assertEqual(self.job_writes(), []); self.assertEqual(len(self.commits()), 1)
        self.assertEqual(len(self.durable()[1]), 1)
        self.chain.later.assert_called_once()

    def test_no_chain_job_save_failure_keeps_pending_receipt_retryable(self):
        self.client.input_flow = None
        before = self.durable(); live = copy.deepcopy(self.job)
        self.db.execute("""CREATE TEMP TRIGGER fail_job BEFORE INSERT ON generation_jobs
            BEGIN SELECT RAISE(ABORT,'job write failure'); END""")
        with self.assertRaisesRegex(sqlite3.IntegrityError, 'job write failure'): self.refresh()
        self.assertEqual(self.durable(), before); self.assertEqual(self.job, live)
        self.db.execute('DROP TRIGGER fail_job')
        self.refresh()
        self.assertEqual(self.durable()[0]['parameter_receipt'], self.receipt)
        self.sql.clear(); self.refresh()
        self.assertEqual(self.commits(), [])

    def test_owner_identity_payload_and_prompt_guards_still_reject_stale_acceptance(self):
        writes = self.db.total_changes
        self.refresh(prompt='another-prompt')
        self.assertEqual(self.db.total_changes, writes)
        for change in ('owner', 'identity', 'payload', 'replay'):
            job = copy.deepcopy(self.job); job['parameter_receipt'] = copy.deepcopy(self.receipt)
            attempt = copy.deepcopy(self.attempt)
            if change == 'owner': job['parameter_receipt']['owner']['parent'] = 'other-entry'
            if change == 'identity': job['parameter_receipt']['identity']['frontend_id'] = 'other-native'
            if change == 'payload': job['payload']['prompt']['35']['inputs']['seed'] = 999
            if change == 'replay': attempt['parameter_replay'] = True
            with self.subTest(change=change):
                self.store.record_parameters(attempt, job)
                self.assertEqual(self.db.total_changes, writes)
        self.assertEqual(self.commits(), [])

    def test_different_attempt_or_prompt_is_not_deduplicated_and_older_owner_cannot_rewind(self):
        self.refresh()
        first = copy.deepcopy(self.job)
        later = copy.deepcopy(self.attempt); later.update(id='later-attempt', parent='later-entry', created=later['created']+1)
        job = copy.deepcopy(self.job)
        job['parameter_receipt']['owner']['parent'] = 'later-entry'
        job['parameter_receipt']['fields'][0]['next_value'] = 12
        self.store.record_parameters(later, job)  # Same prompt, distinct owned attempt.
        row = self.durable()[1][0]
        self.assertEqual((row['attempt'], row['next_seed']), ('later-attempt', 12))
        self.store.record_parameters(self.attempt, first)
        self.assertEqual(self.durable()[1][0], row)
        job['prompt_id'] = 'new-prompt'; job['parameter_receipt']['fields'][0]['next_value'] = 13
        self.store.record_parameters(later, job)
        row = self.durable()[1][0]
        self.assertEqual((row['prompt_id'], row['next_seed']), ('new-prompt', 13))
        # A delayed pending receipt for that same accepted attempt cannot
        # erase its known next seed.
        job['parameter_receipt']['fields'][0].pop('next_value')
        self.store.record_parameters(later, job)
        self.assertEqual(self.durable()[1][0], row)


if __name__ == '__main__': unittest.main()
