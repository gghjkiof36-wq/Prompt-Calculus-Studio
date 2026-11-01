"""State pairs commit once, and publication never observes half acceptance."""
import json
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from prompt_calculus_studio.stage_runner import StageRunner
from prompt_calculus_studio.stage_store import StageStore


class StageAtomicTransitionsTests(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory()
        self.addCleanup(self.area.cleanup)
        self.path = Path(self.area.name) / 'stage.sqlite'
        self.db = sqlite3.connect(self.path)
        self.addCleanup(self.db.close)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.store = StageStore(self.db)
        self.reader = sqlite3.connect(self.path)
        self.addCleanup(self.reader.close)
        self.events = []
        self.notices = []
        self.runner = SimpleNamespace(store=self.store, closed=False, collecting=set(), results={},
            _result_save_errors=set(),
            client=SimpleNamespace(request=lambda *a, **k: self.fail('unexpected request')),
            window=SimpleNamespace(state={'workspace': 'w'}, notice=self.notices.append,
                                   store=SimpleNamespace(directory=Path(self.area.name))))
        self.runner.save = lambda run: StageRunner.save(self.runner, run)
        self.runner.pause = lambda *a, **k: self.fail('database failure must not pause a backend job')
        for name in ('remember_preview', 'refresh_results', 'settle', 'notify', 'later'):
            setattr(self.runner, name, lambda *a, name=name, **kwargs: self.publish(name))
        self.run = self.store.add('run', 'w', status='running', round=1, initial={}, stack=[],
            plan={'stages': {'stage': {'id': 'stage', 'workflow': 'workflow', 'auto': None,
                                      'text_output': ['3', 'text']}}})
        self.attempt = self.store.add('attempt', 'w', self.run['id'], status='submitted', stage='stage',
            snapshot_state={'generation': {'profiles': []}}, sources={'source': {'index': 2}})
        self.frame = dict(kind='stage', key='stage', job=self.attempt['id'], index=0, produced={})
        self.run = self.store.update(self.run['id'], stack=[self.frame])
        self.job = dict(prompt_id='actual-prompt', payload={'prompt': {
            '3': {'class_type': 'CLIPTextEncode', 'inputs': {'text': 'Frozen text'}}}}, outputs={})
        self.sql = []
        self.db.set_trace_callback(self.sql.append)

    def read(self, ident):
        return json.loads(self.reader.execute('SELECT body FROM stage_journal WHERE id=?', (ident,)).fetchone()[0])

    def publish(self, name):
        self.assertFalse(self.db.in_transaction)
        self.assertEqual(self.read(self.attempt['id'])['status'], 'complete')
        current = self.read(self.run['id'])
        if current['status'] != 'cancelled':
            self.assertEqual(current['stack'][0]['index'], 1)
            self.assertIsNone(current['stack'][0]['job'])
        self.events.append(name)

    def collect(self):
        StageRunner.collect(self.runner, self.attempt, self.job)

    def fail_write(self, ident, commit=False):
        if commit:
            self.db.executescript('''CREATE TABLE required_parent(id INTEGER PRIMARY KEY);
                CREATE TABLE commit_probe(parent INTEGER REFERENCES required_parent(id)
                    DEFERRABLE INITIALLY DEFERRED);''')
            statement = 'INSERT INTO commit_probe VALUES(1)'
        else:
            statement = "SELECT RAISE(ABORT, 'temporary write failure')"
        # Identifiers are UUIDs created by this isolated fixture.
        self.db.execute(f"""CREATE TEMP TRIGGER fail_transition AFTER UPDATE ON stage_journal
            WHEN NEW.id='{ident}' BEGIN {statement}; END""")

    def test_result_and_frame_commit_once_before_any_publication(self):
        self.collect()
        self.assertEqual(sum(s.upper() == 'COMMIT' for s in self.sql), 1)
        self.assertEqual(self.read(self.run['id'])['source_positions'], {'source': {'index': 2}})
        self.assertEqual(self.read(self.attempt['id'])['result']['text'], 'Frozen text')
        self.assertEqual(self.events, ['remember_preview', 'refresh_results', 'notify', 'later'])
        self.assertEqual(self.runner.collecting, set())

    def assert_failed_result_retries(self, fail_target, commit=False):
        before = [self.read(i['id']) for i in (self.attempt, self.run)]
        self.fail_write(fail_target, commit)
        self.collect()
        self.assertEqual([self.read(i['id']) for i in (self.attempt, self.run)], before)
        self.assertEqual(self.events, [])
        self.assertEqual(self.runner.results, {})
        self.assertEqual(self.runner.collecting, set())
        self.assertFalse(self.db.in_transaction)
        self.collect()
        self.assertEqual(len(self.notices), 1)
        self.db.execute('DROP TRIGGER fail_transition')
        self.collect()
        self.assertEqual(self.runner._result_save_errors, set())
        self.assertEqual(self.read(self.run['id'])['stack'][0]['index'], 1)
        self.assertEqual(self.read(self.run['id'])['stack'][0]['produced']['stage']['attempts'], [self.attempt['id']])

    def test_first_result_write_failure_remains_collectable(self):
        self.assert_failed_result_retries(self.attempt['id'])

    def test_second_result_write_failure_rolls_back_both_records(self):
        self.assert_failed_result_retries(self.run['id'])

    def test_result_commit_failure_rolls_back_both_records(self):
        self.assert_failed_result_retries(self.run['id'], commit=True)

    def test_image_result_db_failure_cannot_escape_into_http_failed_callback(self):
        image = dict(filename='fixture.png', subfolder='', type='output')
        self.job['outputs'] = {'3': {'images': [image]}}
        requests = []
        backend_failures = []
        rows = [dict(prompt_id=self.job['prompt_id'], node_id='3', image=image, path='isolated-fixture')]

        def request(route, done, failed):
            requests.append(route)
            # ComfyClient.request wraps done and forwards escaped exceptions
            # to failed, including exceptions unrelated to the network.
            try:
                done(rows)
            except Exception as exc:
                backend_failures.append(str(exc))
                failed(str(exc))

        self.runner.client.request = request
        self.fail_write(self.run['id'])
        with patch('prompt_calculus_studio.image_bindings.import_source',
                   return_value={'relative': 'fixture.png', 'sha256': 'fixture'}):
            self.collect()
            self.assertEqual(backend_failures, [])
            self.assertEqual(self.read(self.attempt['id'])['status'], 'submitted')
            self.assertEqual(self.read(self.run['id'])['status'], 'running')
            self.assertEqual(self.runner.collecting, set())
            self.db.execute('DROP TRIGGER fail_transition')
            self.collect()
        self.assertEqual(requests, ['desktop/results?prompt_id=actual-prompt'] * 2)
        self.assertEqual(self.read(self.run['id'])['stack'][0]['index'], 1)
        self.assertEqual(self.read(self.attempt['id'])['result']['images'][0]['relative'], 'fixture.png')

    def test_cancelled_result_is_saved_without_advancing_or_restarting(self):
        self.store.update(self.run['id'], status='cancelled')
        self.collect()
        self.assertEqual(self.read(self.run['id'])['stack'][0], self.frame)
        self.assertEqual(self.read(self.attempt['id'])['status'], 'complete')

    def test_other_workspace_result_does_not_refresh_current_canvas(self):
        self.runner.window.state['workspace'] = 'elsewhere'
        self.collect()
        self.assertEqual(self.events, ['notify', 'later'])
        self.assertEqual(self.runner.results, {})

    def activation(self):
        frame = dict(kind='loop', active=None, auto=None, node={'children': []}, ctx={'saved': {}},
                     initialized=True, produced={})
        run = self.store.update(self.run['id'], stack=[frame])
        item = self.store.add('entry', 'w', 'owner', status='waiting', scheduler='scheduler',
                              saved={'scene': {'title': 'frozen scene'}})
        return run, item

    def test_entry_activation_commits_row_and_owning_stack_once(self):
        run, item = self.activation()
        self.sql.clear()
        StageRunner.activate_entry(self.runner, run, run['stack'][0], item)
        self.assertEqual(sum(s.upper() == 'COMMIT' for s in self.sql), 1)
        self.assertEqual(self.read(item['id'])['status'], 'running')
        saved = self.read(run['id'])
        self.assertEqual(saved['stack'][0]['active'], item['id'])
        self.assertEqual(saved['stack'][1]['ctx']['parent'], item['id'])
        self.assertEqual(saved['stack'][1]['ctx']['scene'], {'title': 'frozen scene'})

    def test_activation_second_write_failure_rolls_back_before_next_pump(self):
        run, item = self.activation()
        self.fail_write(run['id'])
        with self.assertRaises(sqlite3.IntegrityError):
            StageRunner.activate_entry(self.runner, run, run['stack'][0], item)
        self.assertEqual(self.read(item['id'])['status'], 'waiting')
        self.assertEqual(len(self.read(run['id'])['stack']), 1)
        self.assertIsNone(self.read(run['id'])['stack'][0]['active'])
        self.db.execute('DROP TRIGGER fail_transition')
        fresh = self.store.read(run['id'])
        StageRunner.activate_entry(self.runner, fresh, fresh['stack'][0], item)
        self.assertEqual(len(self.read(run['id'])['stack']), 2)


if __name__ == '__main__':
    unittest.main()
