"""B4-T1: missing native intent, cancellation and cross-document admission."""
import copy
import asyncio
import importlib
import sqlite3
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import test_event_acceptance_atomic as acceptance
from prompt_calculus_studio.workspace_runtime import unfinished


class LaneRecoveryTests(unittest.TestCase):
    setUpClass = classmethod(lambda cls: acceptance.AcceptanceTests.setUpClass())
    setUp = acceptance.AcceptanceTests.setUp
    bind = acceptance.AcceptanceTests.bind
    client = acceptance.AcceptanceTests.client
    drain = acceptance.AcceptanceTests.drain
    start = acceptance.AcceptanceTests.start
    schedule = acceptance.AcceptanceTests.schedule

    @property
    def runner(self):
        return self.client.input_flow.chain

    def missing(self):
        self.schedule(3)
        ident, prompt = self.start()
        self.runner.pause(self.runner.store.read(ident)['owner'], reason='route')
        self.peer.pending.clear()
        self.client.generation.recheck(ident)
        self.client.events.reconcile.tick()
        self.clock.now = 31
        self.client.events.reconcile.tick()
        self.assertTrue(self.client.generation.record(ident)['recovery']['fenced'])
        return ident, prompt

    def test_cancelled_missing_schedule_retires_in_one_click(self):
        ident, prompt = self.missing()
        from prompt_calculus_studio.stage_queue_actions import cancel_attempt
        with patch.object(self.client, 'request'):
            cancel_attempt(self.runner, self.runner.store.read(ident))
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'], 'retired', self.f.notices)
        self.assertFalse(unfinished(self.client.window.store.db))
        self.f.click(self.f.b)
        self.drain()
        self.assertEqual(len(self.peer.submissions), 2)

    def test_manual_exit_closes_entry_and_reconcile_before_next_timer(self):
        ident, prompt = self.missing()
        attempt = self.runner.store.read(ident)
        siblings=[e for e in self.runner.store.rows('entry') if e['id']!=attempt['parent']]
        controls=copy.deepcopy(self.runner.store.rows('batch_control'))
        self.client.events.ready = False
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'], 'retired')
        self.assertNotIn(ident, self.client.events.reconcile.pending)
        self.assertTrue(self.client.events.ready)
        self.assertEqual(self.runner.store.read(attempt['parent'])['status'], 'cancelled')
        self.assertEqual([e for e in self.runner.store.rows('entry') if e['id']!=attempt['parent']],siblings)
        self.assertEqual(self.runner.store.rows('batch_control'),controls)
        self.assertNotEqual(self.runner.store.read(attempt['owner'])['status'], 'running')
        self.assertFalse(unfinished(self.client.window.store.db))
        self.assertNotEqual(self.f.w.scheduler.owner, self.client.window.runtime_token)
        self.assertEqual(len(self.peer.submissions), 1)

    def test_other_document_inspect_denial_names_owner(self):
        self.missing()
        other = self.f.b.host.comfy
        failures = []
        value = other.events.aux.start('inspect', dict(target=dict(path='A.json')),
                                       failed=failures.append)
        self.assertIsNone(value)
        self.assertEqual(len(failures), 1)
        self.assertIn('A', str(failures[0]))
        self.assertNotEqual(str(failures[0]), '無法確認')

    def test_manual_exit_without_schedule_pauses_before_next_timer(self):
        ident,_=self.start()
        self.peer.pending.clear()
        self.client.generation.recheck(ident);self.client.events.reconcile.tick()
        self.assertTrue(self.client.generation.record(ident)['recovery']['fenced'])
        attempt=self.runner.store.read(ident)
        self.assertFalse(attempt.get('parent'))
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'],'retired')
        self.assertEqual(self.runner.store.read(attempt['owner'])['status'],'paused')
        self.assertFalse(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)

    def no_browser(self, *, unknown=False, browser='closed'):
        self.schedule(3)
        old = self.f.transport_request
        def request(client, route, data=None, done=None, failed=None, raw=None, content_type='application/json', **options):
            if route == 'workflow/native/start':
                begin = importlib.import_module('integration_test.native_handshake').begin
                def notify(event, value):
                    if event!='prompt_studio_native_probe' or browser=='closed':return
                    live=dict(self.peer.live,probe=value['probe'])
                    if browser=='missing_target':
                        live.update(identity=dict(path='other.json',frontend_id='other'),workflows=[])
                    elif browser=='stale_page':live['bindings_protocol']=None
                    self.peer.native.poll(live)
                try:
                    value = asyncio.run(begin(self.peer.native, data, client.url, 'queue', notify, .06))
                    if done: done(value)
                except ValueError as exc:
                    if unknown:
                        operation = self.peer.native.read(data['id'])
                        operation.update(state='unconfirmed', error='lost original outcome')
                        self.peer.native.save(operation)
                    if failed: failed(exc)
                return
            return old(client, route, data, done, failed, raw, content_type, **options)
        self.f.transport_request = request
        self.f.click(self.f.a)
        # Lose the terminal notification too: only the durable recovery endpoint
        # remains after reconnect, exactly the path used by a restored owner.
        self.since = self.peer.service.events.events('0', 0,
            (self.identity['server_instance'], self.identity['epoch']),
            acceptance.scope(), acceptance.scope_digest())['covered_to']
        self.drain()
        attempts = self.runner.store.rows('attempt')
        self.assertTrue(attempts, (self.f.errors, self.f.notices))
        attempt = attempts[0]
        self.assertEqual(attempt['submission_intent'], 'issued')
        self.assertFalse(self.client.generation.record(attempt['id']).get('prompt_id'))
        self.assertEqual(len(self.peer.submissions), 0)
        return attempt['id']

    def test_no_browser_durable_failure_is_not_unconfirmed(self):
        ident = self.no_browser()
        self.clock.now=31;self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'], 'failed')
        self.assertEqual(self.runner.store.read(self.runner.store.read(ident)['owner'])['status'],'paused')
        self.assertFalse(unfinished(self.client.window.store.db))

    def test_missing_native_workflow_is_failure_with_original_reason(self):
        ident=self.no_browser(browser='missing_target')
        record=self.client.generation.record(ident)
        self.assertEqual(record['state'],'failed')
        self.assertIn('native_target_missing',record['error'])

    def test_stale_web_extension_is_failure_with_original_reason(self):
        ident=self.no_browser(browser='stale_page')
        record=self.client.generation.record(ident)
        self.assertEqual(record['state'],'failed')
        self.assertIn('重新整理',record['error'])

    def test_no_prompt_cancel_missing_abandon(self):
        ident = self.no_browser(unknown=True)
        self.clock.now = 31
        self.client.events.reconcile.tick()
        from prompt_calculus_studio.stage_queue_actions import cancel_attempt
        with patch.object(self.client, 'request'):
            cancel_attempt(self.runner, self.runner.store.read(ident))
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'], 'retired', self.f.notices)
        self.assertFalse(unfinished(self.client.window.store.db))

    def test_cancel_and_fenced_absence_retire_without_second_click(self):
        ident = self.no_browser(unknown=True)
        from prompt_calculus_studio.stage_queue_actions import cancel_attempt
        cancel_attempt(self.runner, self.runner.store.read(ident))
        self.client.events.reconcile.tick()
        record = self.client.generation.record(ident)
        self.assertEqual(record['state'], 'retired')
        self.assertEqual(record['terminal_action'], 'abandon')
        self.assertFalse(record.get('cancel_confirmed'), 'absence is not proof of cancelled/unsent')
        self.assertFalse(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions), 0)

    def test_retire_transaction_failure_then_one_retry(self):
        ident, _ = self.missing()
        before = copy.deepcopy(self.runner.store.rows('entry'))
        self.client.events.store.fault = lambda point: (_ for _ in ()).throw(sqlite3.OperationalError('injected')) if point=='before_accept_commit' else None
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'], 'unconfirmed')
        self.assertEqual(self.runner.store.rows('entry'), before)
        self.assertTrue(unfinished(self.client.window.store.db))
        self.client.events.store.fault = None
        self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'], 'retired')
        self.assertFalse(unfinished(self.client.window.store.db))

    def test_stale_epoch_proof_needs_recheck_not_retirement(self):
        ident, _ = self.missing()
        self.client.events.identity = dict(self.identity, epoch='replacement')
        with patch.object(self.client, 'request'):
            self.client.generation.abandon(ident)
        self.assertEqual(self.client.generation.record(ident)['state'], 'unconfirmed')
        self.assertTrue(unfinished(self.client.window.store.db))

    def test_unavailable_after_cancel_never_retires_or_resubmits(self):
        ident,_=self.missing()
        self.client.events.remember_proof(ident,dict(recovery=dict(location='unavailable',can_abandon=False)))
        self.runner.store.patch(ident,cancel_requested=True)
        with patch.object(self.client,'request'):
            self.client.generation.abandon(ident)
            self.clock.now+=31;self.client.events.reconcile.tick()
        self.assertEqual(self.client.generation.record(ident)['state'],'unconfirmed')
        self.assertTrue(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)

    def test_retired_job_stays_terminal_after_reopen_and_duplicate_evidence(self):
        ident,prompt=self.missing()
        self.client.generation.abandon(ident)
        from test_stage_invariants import InvariantTests
        InvariantTests.reopen(self)
        for _ in range(2):
            self.client.events.accept_evidence(ident,dict(state='running',prompt_id=prompt),None,None,lambda:None,self.fail)
        self.assertEqual(self.client.generation.record(ident)['state'],'retired')
        self.assertFalse(unfinished(self.client.window.store.db))
        self.assertEqual(len(self.peer.submissions),1)

    def test_stage_exit_button_uses_same_retirement(self):
        from PySide6.QtTest import QTest
        from PySide6.QtCore import Qt
        from prompt_calculus_studio.stage_widgets import StagePanel
        LABEL='停止追蹤此筆（保留紀錄）'
        ident, _ = self.missing()
        stage = self.runner.store.read(ident)['stage']
        panel = StagePanel(self.f.a.host.canvas, stage)
        self.addCleanup(panel.deleteLater)
        panel.resize(340, 200);panel.show();panel.refresh()
        self.assertTrue(hasattr(panel,'release_button'),'missing Stage retirement exit')
        self.assertTrue(panel.release_button.isVisible())
        self.assertEqual(panel.release_button.text(), LABEL)
        if os.environ.get('PCS_T1_SCREENSHOTS'):
            panel.grab().save(str(Path(os.environ['PCS_T1_SCREENSHOTS'])/'stage-exit.png'))
        QTest.mouseClick(panel.release_button, Qt.MouseButton.LeftButton)
        self.assertEqual(self.client.generation.record(ident)['state'], 'retired')
        panel.refresh()
        self.assertFalse(panel.release_button.isVisible())

    def test_schedule_menu_exit_is_scoped_to_its_batch(self):
        from PySide6.QtCore import QPoint
        from prompt_calculus_studio.stage_widgets import SchedulePageContent
        LABEL='停止追蹤此筆（保留紀錄）'
        ident, _ = self.missing()
        entry = self.runner.store.read(self.runner.store.read(ident)['parent'])
        panel = SchedulePageContent(self.f.a.host.canvas, entry['scheduler'])
        self.addCleanup(panel.deleteLater)
        panel.refresh()
        def choose(menu, position):
            self.assertIn(LABEL,[a.text() for a in menu.actions()])
            action = next(a for a in menu.actions() if a.text()==LABEL)
            action.trigger()
        with patch('prompt_calculus_studio.stage_widgets.RoundMenu.open_at', choose):
            panel.group_menu(entry['execution_group'], QPoint())
        self.assertEqual(self.client.generation.record(ident)['state'], 'retired')
        self.assertFalse(unfinished(self.client.window.store.db))


class MigrationPauseTests(unittest.TestCase):
    def test_allow_inflight_preserves_old_running_entry_and_pauses_first_open(self):
        import tempfile
        from contextlib import closing
        from migration_fixture import delivery, source_hashes
        from prompt_calculus_studio import workspace_migration, workspace_profile
        from prompt_calculus_studio.core import Storage
        from prompt_calculus_studio.stage_store import StageStore
        with tempfile.TemporaryDirectory() as temp:
            source, target = delivery(Path(temp), inflight=True, wal=False)
            path = source/'data/studio.sqlite3'
            with closing(sqlite3.connect(path)) as db, db:
                raw = db.execute("SELECT body FROM stage_journal WHERE kind='entry'").fetchone()[0]
                body = dict(json.loads(raw), status='running', created=1794432000)
                db.execute("UPDATE stage_journal SET body=? WHERE kind='entry'", (json.dumps(body),))
            before = source_hashes(source)
            report = workspace_migration.migrate(source, target, allow_inflight=True)
            self.assertEqual(report['status'], 'done', report)
            _, data = workspace_profile.read(target)
            for _ in range(2):
                storage = Storage(data)
                try:
                    store = StageStore(storage.db)
                    self.assertEqual(store.read('r0')['status'], 'paused')
                    self.assertEqual(store.read('r0')['pause_reason'], 'restart')
                    self.assertEqual(store.read('r1')['status'], 'running')
                    self.assertEqual(store.read('r2')['prompt_id'], 'preserved-prompt-id')
                finally: storage.close()
            self.assertEqual(source_hashes(source), before)


if __name__ == '__main__':
    unittest.main()
