"""S1/S1-X/S8: executable windows, preserved history and workspace FIFO."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from prompt_calculus_studio import clip_flow, multi_output as model, stage_batches
from prompt_calculus_studio import schedule_batch_actions as actions
from prompt_calculus_studio.flow_data import add_scheduler
from test_workspace_execution import WorkspaceExecutionTests as Fixture


class SchedulingReadinessTests(unittest.TestCase):
    setUpClass = classmethod(Fixture.setUpClass.__func__)
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    window = Fixture.window
    make = Fixture.make
    transport_request = Fixture.transport_request
    tick = Fixture.tick
    finish = Fixture.finish
    click = Fixture.click

    def scheduler(self, tab=None):
        host = (tab or self.a).host
        keys = []
        def setup(state):
            keys.append(add_scheduler(state))
            model.connect(state, next(iter(state['multi_output']['stages'])), keys[0]+'::flow', 'flow')
        self.assertTrue(host.canvas.commit(setup))
        return host.comfy.input_flow.chain, keys[0]

    def rewire(self):
        def change(state):
            clip = clip_flow.add(state)
            clip_flow.set_binding(state, 'flow', clip, ('7', 'text'))
            model.connect(state, state['multi_output']['current_output'], clip, 'clip')
            model.connect(state, clip, next(iter(state['multi_output']['stages'])), 'control')
        self.assertTrue(self.a.host.canvas.commit(change))

    def drain(self, expected):
        for _ in range(expected+8):
            self.tick()
            if self.peer.pending:
                self.finish()
            elif self.w.scheduler.owner is None:
                break
        diagnostic = dict(notices=self.notices, owner=self.w.scheduler.owner, waiting=self.w.scheduler.waiting,
                          registering=str(self.w.comfy_connection.registering),
                          runs={key: [(r['status'], r.get('pause_reason'), [(f['kind'], f.get('active'), f.get('job')) for f in r['stack']])
                                      for r in client.input_flow.chain.runs(True)] for key, client in self.w.scheduler.clients.items()})
        self.assertEqual(len(self.peer.submissions), expected, diagnostic)
        self.assertEqual(len({s['prompt_id'] for s in self.peer.submissions}), expected)
        self.assertFalse(self.peer.pending)

    def preserved(self, runner, run):
        return dict(entries=[e for e in runner.store.rows('entry') if e['run'] == run],
                    snapshots=[b for b in runner.store.rows('batch_snapshot') if b['run'] == run],
                    controls=[c for c in runner.store.rows('batch_control') if c['run'] == run],
                    positions=list(runner.store.db.execute('SELECT id,position FROM stage_header WHERE id IN (SELECT id FROM stage_header WHERE json_extract(body,\'$.run\')=?) ORDER BY position', (run,))))

    def route_case(self, cancel=False, restore=False):
        runner, key = self.scheduler()
        runner.start(30); self.tick()
        for _ in range(21): stage_batches.expand(runner, 20)
        old = runner.runs(True)[0]['id']
        self.assertEqual(len(runner.store.rows('entry')), 20)
        if cancel:
            self.w.focus_tab(self.a)
            controls = self.a.host.canvas.execution_bar.controls
            controls.refresh()
            QTest.mouseClick(controls.stop, Qt.MouseButton.LeftButton)
            self.tick()
            self.assertFalse(any(a.get('cancel_requested') for a in runner.store.rows('attempt')))
        self.rewire()
        if not cancel:
            self.click(self.b)
            self.assertEqual(len(self.peer.submissions), 1)  # Accepted A still owns the lane.
            self.finish()
        saved = copy.deepcopy(self.preserved(runner, old))
        self.assertEqual(sum(e['status'] == 'retained' for e in saved['entries']), 19)
        self.assertEqual(saved['snapshots'][0]['total']-saved['snapshots'][0]['expanded'], 10)
        self.assertEqual(self.a.host.comfy.scheduling_message, '等待：保留項目')
        runner.start(3)
        new = runner.runs(True)[-1]['id']
        if cancel: runner.resume(new)  # X preserves manual pause until the user resumes.
        if cancel: self.click(self.b)
        self.drain(5)
        self.assertEqual(runner.store.read(new)['status'], 'complete')
        self.assertEqual(self.preserved(runner, old), saved)
        self.assertTrue(all(r['status'] == 'complete' for r in self.b.host.comfy.input_flow.chain.runs()))
        if restore:
            self.a.host.canvas.undo()
            runner.resume(old)
            restored = [e for e in runner.store.rows('entry') if e['run'] == old]
            self.assertEqual([e['saved'] for e in restored], [e['saved'] for e in saved['entries']])
            self.assertEqual([e['id'] for e in restored], [e['id'] for e in saved['entries']])
            self.assertEqual(sum(e['status'] == 'waiting' for e in restored), 19)
            self.drain(34)
            self.assertEqual(runner.store.read(old)['status'], 'complete')
            self.assertEqual(sum(e['run'] == old for e in runner.store.rows('entry')), 30)

    def test_repro_capacity_preserves_old_group_and_b_runs(self):
        self.route_case(restore=True)

    def test_repro_cancel_capacity_preserves_old_group_and_b_runs(self):
        self.route_case(cancel=True)

    def test_all_bypassed_zero_expanded_releases_fifo_and_unbypass_runs(self):
        runner, key = self.scheduler()
        runner.start(3); runner.timer.stop()
        group = runner.store.schedule_groups(self.a.host.state['workspace'], key)[0]['id']
        before = copy.deepcopy(runner.store.rows('batch_snapshot'))
        actions.bypass(runner, key, group)
        self.tick()
        self.assertEqual(runner.store.rows('entry'), [])
        self.assertEqual(self.a.host.comfy.scheduling_message, '已繞過')
        controls = self.a.host.canvas.execution_bar.controls
        controls.refresh()
        self.assertEqual(controls.run_button.toolTip(), '已繞過')
        self.click(self.b); self.drain(1)
        self.assertEqual(runner.store.rows('batch_snapshot'), before)
        actions.bypass(runner, key, group)
        self.drain(4)
        self.assertTrue(all(r['status'] == 'complete' for r in runner.runs()))

    def test_completed_item_and_inflight_bypass_still_holds_lane(self):
        runner, key = self.scheduler()
        runner.start(3); self.tick(); self.finish()
        self.assertEqual(len(self.peer.submissions), 2)
        group = runner.store.rows('batch_control')[0]['group']
        actions.bypass(runner, key, group)
        self.click(self.b)
        self.assertEqual(self.w.scheduler.owner, self.a.document.runtime_token)
        self.assertEqual(len(self.peer.submissions), 2)
        self.finish(); self.drain(3)
        self.assertEqual(self.a.host.comfy.scheduling_message, '已繞過')
        actions.bypass(runner, key, group); self.drain(4)

    def test_future_run_can_be_promoted_when_older_run_is_all_bypassed(self):
        from prompt_calculus_studio.stage_model import compile_plan
        runner, key = self.scheduler()
        runner.start(3); runner.timer.stop()
        old = runner.store.rows('batch_control')[0]['group']
        actions.bypass(runner, key, old); self.tick()
        future = runner.create_run(compile_plan(self.a.host.state), 2, status='waiting')
        actions.prioritize(runner, key, future['execution_group'])
        self.drain(2)
        self.assertEqual(runner.store.read(future['id'])['status'], 'complete')
        self.assertTrue(runner.store.batch_control(self.a.host.state['workspace'], key, old)['bypassed'])
        self.assertEqual(self.a.host.comfy.scheduling_message, '已繞過')

    def test_priority_bypass_and_cancel_current_preserve_other_frozen_items(self):
        runner, key = self.scheduler()
        runner.start(3); runner.timer.stop()
        self.a.host.canvas.commit(lambda state: state['uses']['root'].update(prompt='second'))
        runner.start(2); runner.timer.stop()
        controls = runner.store.rows('batch_control')
        first, second = [c['group'] for c in controls]
        actions.prioritize(runner, key, second)
        self.tick()
        self.assertEqual(self.peer.submissions[0]['prompt']['6']['inputs']['text'], 'second')
        actions.cancel(runner, key, second)
        self.drain(4)
        self.assertEqual([s['prompt']['6']['inputs']['text'] for s in self.peer.submissions], ['second']+['alpha']*3)
        self.assertFalse(runner.store.batch_control(self.a.host.state['workspace'], key, first).get('cancelled'))

    def test_two_schedulers_have_independent_windows_and_submit_once_in_order(self):
        from prompt_calculus_studio import stage_model
        runner, first = self.scheduler()
        keys = [first]
        def second(state):
            previous = next(iter(state['multi_output']['stages']))
            stage = stage_model.add(state, workflow='flow')
            state['multi_output']['stages'][stage].update(output='9', text_output=['6', 'text'])
            clip = next(iter(state['multi_output']['clip_inputs']))
            model.connect(state, clip, stage, 'control')
            model.connect(state, previous, stage, 'done')
            key = add_scheduler(state); keys.append(key)
            model.connect(state, stage, key+'::flow', 'flow')
        self.assertTrue(self.a.host.canvas.commit(second))
        runner.start(21); runner.timer.stop()
        for _ in range(42): stage_batches.expand(runner, 20)
        runner.timer.stop()
        frozen = {e['id']: copy.deepcopy(e['saved']) for e in runner.store.rows('entry')}
        for key in keys:
            self.assertEqual(sum(e['scheduler'] == key for e in runner.store.rows('entry')), 20)
            control = runner.store.batch_controls(self.a.host.state['workspace'], key)
            self.assertEqual([(c['total'], c['expanded']) for c in control.values()], [(21, 20)])
        runner.later(); self.drain(42)
        entries = runner.store.rows('entry')
        self.assertEqual(len(entries), 42)
        self.assertTrue(all(e['status'] == 'complete' for e in entries))
        for e in entries:
            if e['id'] in frozen: self.assertEqual(e['saved'], frozen[e['id']])
        stages = [a['stage'] for a in runner.store.rows('attempt')]
        self.assertEqual(stages, [stages[0]]*21+[stages[-1]]*21)


del Fixture


class WindowBoundaryTests(unittest.TestCase):
    def test_19_20_21_100_keep_frozen_content_cursors_and_order(self):
        from test_stage_enqueue_progress import scenario
        for total in (19, 20, 21, 100):
            with self.subTest(total=total), scenario(False) as (case, config):
                runner = case.runner
                runner.start(total); runner.timer.stop()
                before = copy.deepcopy(runner.store.rows('batch_snapshot')[0])
                control = copy.deepcopy(runner.store.rows('batch_control')[0])
                for _ in range(total+1): stage_batches.expand(runner, 20)
                runner.timer.stop()
                entries = runner.store.rows('entry')
                self.assertEqual(len(entries), min(total, 20))
                self.assertEqual([e['execution_group'] for e in entries], [before['execution_group']]*len(entries))
                self.assertEqual(len({e['id'] for e in entries}), len(entries))
                after = runner.store.rows('batch_control')[0]
                for key in ('cursor_before', 'cursor_after', 'serial', 'total'):
                    self.assertEqual(after[key], control[key])
                if total > 20:
                    snapshot = runner.store.rows('batch_snapshot')[0]
                    self.assertEqual(snapshot, dict(before, expanded=20))
                else:
                    self.assertEqual(runner.store.rows('batch_snapshot'), [])

    def test_deferred_head_does_not_reserve_wanted_before_ready_later_group(self):
        from test_stage_enqueue_progress import scenario
        with scenario(False) as (case, config):
            runner = case.runner
            runner.start(30); runner.timer.stop()
            head = runner.store.rows('batch_snapshot')[0]
            runner.store.patch(head['id'], deferred=True)
            runner.start(3); runner.timer.stop()
            second = runner.store.rows('batch_snapshot')[-1]
            for _ in range(4): stage_batches.expand(runner, 20)
            runner.timer.stop()
            self.assertEqual(runner.store.read(head['id']), dict(head, deferred=True))
            self.assertEqual([e['execution_group'] for e in runner.store.rows('entry')], [second['execution_group']]*3)


class ReadinessProjectionTests(unittest.TestCase):
    def setUp(self):
        import sqlite3
        from prompt_calculus_studio.stage_store import StageStore
        self.db = sqlite3.connect(':memory:'); self.addCleanup(self.db.close)
        self.store = StageStore(self.db)

    def test_readiness_does_not_hydrate_frozen_scene_and_observes_each_change(self):
        from prompt_calculus_studio.stage_readiness import readiness_runs, run_can_progress
        frame = dict(kind='loop', initialized=True, active=None, owner='owner', node=dict(key='s'))
        run = self.store.add('run', 'w', status='running', execution_group='g',
            plan=dict(scopes={'s': {}}, stages={}), stack=[frame], frozen_scene={'text': 'saved '*140000})
        self.store.add('entry', 'w', 'owner', run=run['id'], scheduler='s', status='waiting', execution_group='g')
        self.store.set_batch_control('w', 's', 'g', run=run['id'], total=3, expanded=1)
        def check(expected):
            import sqlite3
            full = self.store.open_runs('w')
            before = self.db.total_changes
            # Control headers legitimately use records(); forbid the actual
            # content table instead of forbidding all journal header reads.
            self.db.set_authorizer(lambda action, table, *args:
                sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ and table == 'stage_content' else sqlite3.SQLITE_OK)
            try:
                projected = list(readiness_runs(self.store, 'w'))
                self.assertEqual([run_can_progress(self.store, r) for r in projected], [expected])
            finally: self.db.set_authorizer(None)
            self.assertEqual([run_can_progress(self.store, r) for r in full], [expected])
            self.assertNotIn('frozen_scene', projected[0])
            self.assertEqual(self.db.total_changes, before)
        check(True)
        self.store.set_batch_control('w', 's', 'g', bypassed=True); check(False)
        self.store.patch(run['id'], stack=[dict(frame, active='accepted-entry')]); check(True)
        self.store.patch(run['id'], status='paused'); check(False)
        self.store.patch(run['id'], status='waiting', stack=[frame]); check(False)
        self.store.set_batch_control('w', 's', 'g', bypassed=False); check(True)
        self.store.patch(run['id'], status='running', stack=[]); check(True)

    def test_many_group_wait_reasons_use_bounded_reads_without_stale_results(self):
        from prompt_calculus_studio.stage_readiness import group_wait_reasons
        groups = []
        for index in range(40):
            group = str(index)
            run = self.store.add('run', 'w', status='paused', pause_reason='route')
            self.store.set_batch_control('w', 's', group, run=run['id'])
            groups.append(dict(id=group, cancelled=False, bypassed=False, counts={}))
        sql = []; self.db.set_trace_callback(sql.append)
        try: reasons = group_wait_reasons(self.store, 'w', 's', groups)
        finally: self.db.set_trace_callback(None)
        self.assertEqual(set(reasons.values()), {'等待：保留項目'})
        self.assertLessEqual(len(sql), 2)
        self.store.patch(run['id'], pause_reason='manual')
        self.assertEqual(group_wait_reasons(self.store, 'w', 's', groups)[group], '')
