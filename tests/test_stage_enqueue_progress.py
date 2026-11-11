"""Durable click routing, including the interpreter's 80-transition yield."""
from contextlib import contextmanager
import copy
import unittest
from unittest.mock import patch, PropertyMock

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio import prompt_sources as supply, stage_model
from prompt_calculus_studio.stage_runner import StageRunner
import test_prompt_batches as batches


class Scenario(unittest.TestCase):
    setUp = batches.BatchExecutionTests.setUp
    tearDown = batches.BatchExecutionTests.tearDown
    stages = batches.BatchExecutionTests.stages
    click = batches.BatchExecutionTests.click
    workflows = batches.BatchExecutionTests.workflows
    submitted_texts = batches.BatchExecutionTests.submitted_texts
    pipeline = batches.BatchExecutionTests.pipeline
    setup_batch = batches.BatchExecutionTests.setup_batch


@contextmanager
def scenario(auto, outer=False):
    case = Scenario()
    try:
        case.setUp()
        config = case.setup_batch(length=2)
        if not auto:
            case.assertTrue(case.c.commit(lambda state: supply.source(state, config['reader']).update(supply='step')))
            case.controls.count.setValue(2)
        if outer:
            from prompt_calculus_studio.flow_data import add_scheduler
            from prompt_calculus_studio import multi_output

            def enclose(state):
                key = add_scheduler(state)
                state['multi_output']['schedulers'][key]['mode'] = 'stage'
                multi_output.connect(state, config['stage'], key + '::flow', 'flow')

            case.assertTrue(case.c.commit(enclose), case.notices)
            case.assertEqual(stage_model.compile_plan(case.w.state)['tree'][0]['kind'], 'scope')
        case.controls.refresh()
        yield case, config
    finally:
        try:
            if hasattr(case, 'w'):
                case.tearDown()
        finally:
            if not case.doCleanups():
                raise AssertionError('Scenario cleanup failed')


def finish(case):
    QTest.qWait(40)
    for _ in range(40):
        if not case.executor.pending:
            QTest.qWait(40)
            if not case.executor.pending:
                break
        case.executor.finish()
    case.assertFalse(case.executor.pending)
    runs = case.runner.store.rows('run')
    case.assertTrue(all(run['status'] == 'complete' for run in runs), case.notices)
    entries = case.runner.store.rows('entry')
    return dict(runs=len(runs), entries=len(entries), attempts=len(case.runner.store.rows('attempt')),
                submitted=len(case.executor.submissions),
                completed=sum(v['status']['status_str'] == 'success' for v in case.executor.history.values()),
                # Nested manual entries inherit their parent's text; {} is not
                # another frozen string. Preserve every actual value, including "".
                frozen=[value.get('value') for entry in entries for value in entry['saved'].get('values', {}).values()],
                texts=case.submitted_texts())


class EnqueueProgressTests(unittest.TestCase):
    def compare_clicks(self, auto, clicks, outer=False):
        observed = []
        for gap in (0, 25):
            with scenario(auto, outer=outer) as (case, config):
                for index in range(clicks):
                    if index and gap:
                        QTest.qWait(gap)
                    QTest.mouseClick(case.controls.run_button, Qt.MouseButton.LeftButton)
                    if not gap:
                        # Every click really precedes the first pump, not merely a short delay.
                        self.assertEqual(case.executor.submissions, [])
                        run = case.runner.store.rows('run')[0]
                        self.assertEqual([f['kind'] for f in run['stack']], ['seq'])
                        self.assertEqual(run['scope_progress']['state'], 'pending')
                        self.assertEqual(run['scope_progress']['auto'], auto)
                observed.append(finish(case))
        self.assertEqual(observed[0], observed[1])
        expected = 2 * clicks
        self.assertEqual({key: observed[0][key] for key in ('runs', 'entries', 'attempts', 'submitted', 'completed')},
                         dict(runs=1, entries=expected * (2 if outer else 1), attempts=expected, submitted=expected, completed=expected))

    def test_auto_two_clicks_before_pump(self):
        self.compare_clicks(True, 2)

    def test_auto_three_clicks_before_pump(self):
        self.compare_clicks(True, 3)

    def test_manual_two_clicks_before_pump(self):
        self.compare_clicks(False, 2)

    def test_manual_three_clicks_before_pump(self):
        self.compare_clicks(False, 3)

    def test_manual_outer_scope_two_clicks_before_pump(self):
        self.compare_clicks(False, 2, outer=True)

    def test_manual_outer_scope_three_clicks_before_pump(self):
        self.compare_clicks(False, 3, outer=True)

    def compare_yield(self, auto):
        observed = []
        for after_enter in (False, True):
            with scenario(auto) as (case, config):
                plan = stage_model.compile_plan(case.w.state)
                # Forty empty Stage frames consume exactly 80 push/pop transitions.
                # The real step/advance/later machinery then yields before the scope.
                plan['tree'] = [dict(kind='stage', key=config['stage']) for _ in range(40)] + plan['tree']
                original = case.runner.variants
                calls = []

                def variants(run, stage, ctx, frame):
                    if len(run['stack']) == 2:
                        calls.append(stage['id'])
                        return []
                    return original(run, stage, ctx, frame)

                with patch('prompt_calculus_studio.stage_runner.compile_plan', return_value=plan), \
                        patch.object(case.runner, 'variants', side_effect=variants):
                    case.runner.start(1 if auto else 2)
                    case.runner.timer.stop()
                    run = case.runner.current(plan)
                    case.runner.step(run)
                    saved = case.runner.store.read(run['id'])
                    self.assertEqual(len(calls), 40)
                    self.assertEqual(len(saved['stack']), 1)
                    self.assertEqual(saved['stack'][0]['index'], 40)
                    self.assertEqual(saved['scope_progress']['state'], 'pending')
                    self.assertTrue(case.runner.timer.isActive())
                    self.assertEqual(case.executor.submissions, [])
                    if after_enter:
                        QTest.qWait(25)
                        self.assertEqual(case.runner.store.read(run['id'])['scope_progress']['state'], 'entered')
                    case.runner.start(1 if auto else 2)
                    observed.append(finish(case))
        self.assertEqual(observed[0], observed[1])
        self.assertEqual(observed[0]['runs'], 1)
        self.assertEqual(observed[0]['entries'], 4)

    def test_t4_auto_at_actual_eighty_transition_yield(self):
        self.compare_yield(True)

    def test_t4_manual_at_actual_eighty_transition_yield(self):
        self.compare_yield(False)

    def test_manual_waiting_run_accepts_before_pump(self):
        with scenario(False) as (case, config):
            run = case.runner.create_run(stage_model.compile_plan(case.w.state), 2, status='waiting')
            case.runner.start(2)
            self.assertEqual(len(case.runner.store.rows('run')), 1)
            self.assertEqual(len(case.runner.store.rows('entry')), 0)
            self.assertEqual(sum(g['pending'] for g in case.runner.store.schedule_groups(case.w.state['workspace'], config['scheduler'])), 4)
            self.assertEqual(case.runner.store.read(run['id'])['status'], 'waiting')

    def test_legacy_running_and_waiting_runs_keep_frame_decisions(self):
        for status, entered in (('running', False), ('waiting', False), ('running', True)):
            for auto in (False, True):
                with self.subTest(status=status, entered=entered, auto=auto), scenario(auto) as (case, config):
                    # Build an actual pre-Q2 body, not a new snapshot run with
                    # only one feature flag removed.
                    with patch.object(case.runner, '_create_run', side_effect=case.runner._create_legacy_run):
                        run = case.runner.create_run(stage_model.compile_plan(case.w.state), 1 if auto else 2, status=status)
                    if entered:
                        case.runner.pump()
                        QTest.qWait(25)
                    # Hand-write a pre-Q1 journal body: no progress key at all.
                    with case.runner.store.transaction():
                        case.runner.store.db.execute("UPDATE stage_header SET body=json_remove(body,'$.scope_progress') WHERE id=?", (run['id'],))
                    before = len(case.runner.store.rows('entry'))
                    case.runner.start(1 if auto else 2)
                    self.assertNotIn('scope_progress', case.runner.store.read(run['id']))
                    self.assertEqual(len(case.runner.store.rows('run')), 1 if entered else 2)
                    if entered:
                        self.assertEqual(len(case.runner.store.rows('entry')), before if auto else before + 2)
                        expected = '執行中…' if auto else '已保存 2 項至 '
                    else:
                        # The old run keeps its frame decision, but a newly
                        # created subsequent run uses the accepted Q2 model.
                        created=next(r for r in case.runner.store.rows('run') if r['id']!=run['id'])
                        self.assertTrue(created['batch_snapshots'])
                        self.assertEqual(created['status'],'waiting')
                        expected = '已排入第 1 組（2 項）'
                    self.assertTrue(any(note.startswith(expected) for note in case.notices), case.notices)

    def test_dispatch_claim_sees_committed_progress_and_entries(self):
        with scenario(False) as (case, config):
            counts = []

            def claimed():
                self.assertFalse(case.runner.store.db.in_transaction)
                run = case.runner.store.rows('run')[0]
                self.assertEqual(run['scope_progress']['state'], 'pending')
                self.assertEqual(len(case.runner.store.rows('entry')), 0)
                counts.append(sum(g['pending'] for g in case.runner.store.schedule_groups(case.w.state['workspace'], config['scheduler'])))
                return True

            with patch.object(type(case.runner.client), 'dispatch_allowed', new_callable=PropertyMock, side_effect=claimed):
                case.runner.start(2)
                case.runner.start(2)
            self.assertEqual(counts, [2, 4])

    def test_progress_and_entries_roll_back_with_failed_acceptance(self):
        with scenario(False) as (case, config):
            before = copy.deepcopy(case.w.state)
            with patch.object(case.runner, 'reserve_supply', side_effect=OSError('failed acceptance')):
                with self.assertRaisesRegex(OSError, 'failed acceptance'):
                    case.runner.start(2)
            self.assertEqual(case.runner.store.rows('run'), [])
            self.assertEqual(case.runner.store.rows('entry'), [])
            self.assertEqual(case.w.state, before)


class ProgressDecisionTests(unittest.TestCase):
    def test_new_run_uses_progress_even_when_frames_disagree(self):
        manual = dict(kind='scope', key='outer', children=[])
        auto = dict(kind='data', key='inner', auto='reader', children=[])
        for node in (manual, auto):
            for state in ('pending', 'entered'):
                run = dict(plan={'tree': [node]}, scope_progress=dict(index=0, key=node['key'],
                    state=state, owner='saved-owner', auto=bool(node.get('auto'))),
                    stack=[dict(kind='loop', auto=not bool(node.get('auto')), owner='wrong-owner', node=auto)])
                target = StageRunner.enqueue_scope(None, run)
                self.assertEqual(target, dict(node=node, owner='saved-owner', auto=bool(node.get('auto'))))
                run['scope_progress']['state'] = 'left'
                self.assertIsNone(StageRunner.enqueue_scope(None, run))

    def test_exit_selects_next_outer_scope_and_then_marks_left(self):
        nodes = [dict(kind='scope', key='a'), dict(kind='stage', key='s'), dict(kind='data', key='b', auto='reader')]
        run = dict(id='run', plan={'tree': nodes}, initial={'a': {'owner': 'original'}}, scope_progress={'state': 'left'})
        StageRunner.record_next_scope(None, run)
        self.assertEqual(run['scope_progress']['owner'], 'original')
        run['scope_progress']['state'] = 'entered'
        StageRunner.record_next_scope(None, run, 1)
        self.assertEqual((run['scope_progress']['index'], run['scope_progress']['state'], run['scope_progress']['auto']), (2, 'pending', True))
        StageRunner.record_next_scope(None, run, 3)
        self.assertEqual(run['scope_progress']['state'], 'left')
