"""Scheduling and evidence contracts for the isolated test runner (no product UI)."""
import io
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import fast_suite as runner


class RunnerTests(unittest.TestCase):
    def test_class_fixture_errors_do_not_corrupt_last_test_or_claim_extra_tests(self):
        class BrokenSetup(unittest.TestCase):
            @classmethod
            def setUpClass(cls):
                raise RuntimeError('fixture setup failed')

            def test_not_reached(self):
                self.fail('must not run')

        class BrokenTeardown(unittest.TestCase):
            def test_passes(self):
                self.assertTrue(True)

            @classmethod
            def tearDownClass(cls):
                raise RuntimeError('fixture teardown failed')

        records, fixture_outcomes = [], []
        guard = SimpleNamespace(events=[])
        borrow = SimpleNamespace(events=[])
        suite = unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(cls)
                                   for cls in (BrokenSetup, BrokenTeardown))
        result = unittest.TextTestRunner(stream=io.StringIO(),
            resultclass=runner.result_class(guard, borrow, records, fixture_outcomes)).run(suite)
        self.assertFalse(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]['outcomes'], [dict(status='pass')])
        self.assertGreaterEqual(records[0]['seconds'], 0)
        self.assertEqual(len(fixture_outcomes), 2)
        self.assertTrue(all(row['outcomes'][0]['status'] == 'error' for row in fixture_outcomes))
        self.assertIn('fixture setup failed', fixture_outcomes[0]['outcomes'][0]['detail'])
        self.assertIn('fixture teardown failed', fixture_outcomes[1]['outcomes'][0]['detail'])

    def test_parallel_files_finish_before_and_after_exclusive_barriers(self):
        names = ['test_first.py', 'test_second.py', 'test_workspace_performance.py',
                 'test_third.py', 'test_fourth.py']
        barriers = [threading.Barrier(2), threading.Barrier(2)]
        active, events = set(), []
        lock = threading.Lock()

        def run(name):
            with lock:
                if runner.exclusive_file(name):
                    self.assertFalse(active)
                    self.assertEqual(len(events), 4)
                else:
                    self.assertNotIn('test_workspace_performance.py', active)
                active.add(name)
                events.append(('start', name))
            if name != 'test_workspace_performance.py':
                barriers[0 if name in names[:2] else 1].wait(timeout=5)
            with lock:
                active.remove(name)
                events.append(('end', name))
            return dict(file=name)

        rows = list(runner.execute_files(names, 2, run))
        self.assertCountEqual([row['file'] for row in rows], names)
        self.assertEqual(events[4:6], [('start', names[2]), ('end', names[2])])
        self.assertFalse(active)

    def test_serial_mode_keeps_input_order_and_all_files(self):
        names = ['test_a.py', 'test_stage_status_performance.py', 'test_b.py::quick']
        seen = []
        rows = list(runner.execute_files(names, 1, lambda name: seen.append(name) or name))
        self.assertEqual(seen, names)
        self.assertEqual(rows, names)
        self.assertEqual(list(runner.scheduling_groups(names)), [[name] for name in names])

    def test_processes_keep_distinct_data_and_temporary_directories(self):
        environments = []
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=Path(directory), vendor='fixture-vendor', timeout=120,
                                   guard='on', borrow_guard=True)

            def spawn(command, **kwargs):
                env = kwargs['env']
                environments.append(env)
                self.assertIn('-I', command)
                self.assertEqual(kwargs['cwd'], runner.ROOT)
                self.assertEqual(env['QT_QPA_PLATFORM'], 'offscreen')
                self.assertEqual(env['PCS_BORROW_GUARD'], '1')
                self.assertEqual(env['PCS_TEST_MODAL_GUARD'], '1')
                self.assertTrue(all(env[key] == env['TEMP'] for key in ('TMP', 'TMPDIR')))
                result = Path(command[command.index('--result') + 1])
                runner.write_json(result, dict(tests=[dict(id='fixture.test', seconds=.1,
                                                         outcomes=[dict(status='pass')])]))
                return SimpleNamespace(wait=lambda **kwargs: 0)

            with patch.object(runner.subprocess, 'Popen', side_effect=spawn):
                rows = [runner.run_file(args, [], name) for name in ('test_one.py', 'test_two.py')]
            self.assertNotEqual(environments[0]['PROMPT_STUDIO_DATA'], environments[1]['PROMPT_STUDIO_DATA'])
            self.assertNotEqual(environments[0]['TEMP'], environments[1]['TEMP'])
            self.assertTrue(all(row['accepted'] and row['passed'] == 1 for row in rows))

    def test_parallel_reporting_preserves_failure_skip_and_guard_rejection(self):
        cases = [
            ('skip', dict(status='skip', detail='GL unavailable'), {}, [], False),
            ('failure', dict(status='fail', detail='actual failure'), {}, [], False),
            ('known', dict(status='fail', detail='actual failure'), {},
             [dict(test_ids=['fixture.test'], match='actual failure')], True),
            ('modal', dict(status='pass'), dict(modal_timeouts=[dict(test='fixture.test')]), [], False),
            ('borrow', dict(status='pass'), dict(borrow_events=[dict(expected=False)]), [], False),
        ]
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=Path(directory), vendor='fixture-vendor', timeout=120,
                                   guard='on', borrow_guard=True)
            for name, outcome, extra, known, accepted in cases:
                with self.subTest(case=name):
                    def spawn(command, **kwargs):
                        result = Path(command[command.index('--result') + 1])
                        runner.write_json(result, dict(tests=[dict(id='fixture.test', seconds=.1,
                                                                 outcomes=[outcome])], **extra))
                        return SimpleNamespace(wait=lambda **kwargs: 1 if outcome['status'] != 'pass' else 0)
                    with patch.object(runner.subprocess, 'Popen', side_effect=spawn):
                        row = runner.run_file(args, known, f'test_{name}.py')
                    self.assertEqual(row['accepted'], accepted)
                    self.assertEqual(row['skipped'], int(outcome['status'] == 'skip'))
                    self.assertEqual(row['passed'], int(outcome['status'] == 'pass'))

    def test_incomplete_result_is_neither_a_pass_nor_accepted(self):
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(output=Path(directory), vendor='fixture-vendor', timeout=120,
                                   guard='on', borrow_guard=False)
            def spawn(command, **kwargs):
                result = Path(command[command.index('--result') + 1])
                runner.write_json(result, dict(tests=[dict(id='fixture.test', seconds=.1, outcomes=[])]))
                return SimpleNamespace(wait=lambda **kwargs: 0)
            with patch.object(runner.subprocess, 'Popen', side_effect=spawn):
                row = runner.run_file(args, [], 'test_empty_result.py')
            self.assertEqual(row['passed'], 0)
            self.assertEqual(row['incomplete_tests'], ['fixture.test'])
            self.assertFalse(row['accepted'])

    def test_load_counters_and_rankings_are_machine_data_not_pass_criteria(self):
        sampler = runner.LoadSampler()
        with patch.object(runner, 'system_load_snapshot', side_effect=[
                dict(cpu_idle=100, cpu_total=200), dict(cpu_idle=125, cpu_total=300)]):
            sampler._sample()
            sampler._sample()
        self.assertEqual(sampler.samples[-1]['cpu_busy_percent'], 75)
        self.assertEqual(sampler.summary()['cpu_busy_mean_percent'], 75)
        rows = [dict(file='a', seconds=2, test_timings=[dict(id='slow', seconds=1.5)]),
                dict(file='b', seconds=3, test_timings=[dict(id='fast', seconds=.2)])]
        rankings = runner.timing_rankings(rows)
        self.assertEqual([row['file'] for row in rankings['files']], ['b', 'a'])
        self.assertEqual([row['id'] for row in rankings['tests']], ['slow', 'fast'])
        self.assertEqual([row['file'] for row in rows], ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
