"""Disconnected status is not a repeating work interruption; no server or model."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtTest import QSignalSpy, QTest
from PySide6.QtWidgets import QApplication, QWidget

from prompt_calculus_studio.workspace_notice import WorkspaceNotice
from prompt_calculus_studio.input_runner import InputRunner
import test_navigation_connection as fixture


NOTICE = '連線中斷，待執行內容已保留。'


class DisconnectNoticeTests(unittest.TestCase):
    setUp = fixture.NavigationConnectionTests.setUp
    tearDown = fixture.NavigationConnectionTests.tearDown
    finish_status = fixture.NavigationConnectionTests.finish_status

    def fail_poll(self):
        self.client.enabled = True
        self.client.poll()
        self.calls[-1]['failed']('fixture: offline')

    def add_run(self, status):
        return self.client.input_flow.chain.store.add('run', self.w.state['workspace'],
            status=status, server=self.client.url, plan={}, stack=[],
            pause_reason='manual' if status == 'paused' else '', message='preserved')

    def test_repeated_failed_polls_do_not_repause_or_reannounce_work(self):
        run = self.add_run('running')
        notices = []
        with patch.object(self.w, 'notice', notices.append):
            self.fail_poll()
            rows = list(self.w.store.db.execute('SELECT * FROM stage_journal'))
            changes = self.w.store.db.total_changes
            changed = QSignalSpy(self.client.stateChanged)
            for _ in range(12):
                self.fail_poll()
            self.assertEqual(changed.count(), 0)
            self.assertEqual(self.w.store.db.total_changes, changes)
            self.assertEqual(list(self.w.store.db.execute('SELECT * FROM stage_journal')), rows)
        self.assertEqual(notices.count(NOTICE), 1)
        self.assertEqual(self.client.input_flow.chain.store.read(run['id'])['status'], 'paused')

    def test_existing_paused_work_and_idle_disconnect_are_silent(self):
        notices = []
        with patch.object(self.w, 'notice', notices.append):
            self.fail_poll()
            run = self.add_run('paused')
            changes = self.w.store.db.total_changes
            self.client.input_flow.disconnected()
            self.assertEqual(self.w.store.db.total_changes, changes)
        self.assertFalse(notices)
        self.assertEqual(self.client.input_flow.chain.store.read(run['id']), run)
        self.assertTrue(self.client.input_flow.has_work())  # Keep endpoint safety intact.

    def test_successful_reconnect_allows_a_new_real_interruption_notice(self):
        run = self.add_run('running')
        notices = []
        with patch.object(self.w, 'notice', notices.append):
            self.fail_poll()
            self.client.token = 'fixture-token'
            self.client.poll()
            self.finish_status()
            self.client.input_flow.chain.store.update(run['id'], status='running')
            self.fail_poll()
            self.fail_poll()
        self.assertEqual(notices.count(NOTICE), 2)

    def test_status_only_client_does_not_process_document_work(self):
        with patch.object(type(self.client), 'document_polling', False), \
             patch.object(self.client.generation, 'disconnected') as generation, \
             patch.object(self.client.input_flow, 'disconnected') as inputs, \
             patch.object(self.client.queue, 'disconnected') as queue:
            self.fail_poll()
            self.client.reset_connection()
            generation.assert_not_called()
            inputs.assert_not_called()
            queue.assert_not_called()

    def test_new_transport_error_remains_visible_without_reinterrupting_work(self):
        run = self.add_run('waiting')
        self.fail_poll()
        changes = self.w.store.db.total_changes
        changed = QSignalSpy(self.client.stateChanged)
        self.client.poll()
        self.calls[-1]['failed']('fixture: extension rejected request')
        self.assertEqual(self.client.message, 'fixture: extension rejected request')
        self.assertEqual(changed.count(), 1)
        self.assertEqual(self.w.store.db.total_changes, changes)
        self.assertEqual(self.client.input_flow.chain.store.read(run['id'])['status'], 'paused')

    def test_uncertain_generation_warns_once_without_resubmission(self):
        self.client.run_id = 'accepted-with-lost-reply'
        notices = []
        with patch.object(self.w, 'notice', notices.append):
            self.fail_poll()
            self.fail_poll()
        uncertain = [text for text in notices if '結果尚未確認' in text]
        self.assertEqual(len(uncertain), 1)
        self.assertEqual(self.client.run_id, '')
        self.assertFalse(any(call['route'] in ('generate', 'workflow/native/start')
                             for call in self.calls))


class WorkspaceNoticeLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = QWidget()
        self.window.resize(800, 600)
        self.notice = WorkspaceNotice(self.window)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def test_repeated_identical_text_keeps_original_deadline(self):
        self.notice.present(NOTICE, 90)
        QTest.qWait(40)
        self.notice.present(NOTICE, 5000)
        self.assertLess(self.notice.expire.remainingTime(), 100)
        QTest.qWait(100)
        self.assertTrue(self.notice.isHidden())
        self.notice.present(NOTICE, 90)  # A later explicit occurrence still works.
        self.assertFalse(self.notice.isHidden())

    def test_new_text_replaces_old_notice_and_empty_text_dismisses(self):
        self.notice.present(NOTICE, 90)
        self.notice.present('New operation', 5000)
        self.assertGreater(self.notice.expire.remainingTime(), 4500)
        self.assertEqual(self.notice.text(), 'New operation')
        self.notice.present('')
        self.assertTrue(self.notice.isHidden())
        self.assertFalse(self.notice.expire.isActive())
        self.assertEqual(self.notice.text(), '')


class LegacyDisconnectNoticeTests(unittest.TestCase):
    def runner(self, paused):
        runner = InputRunner.__new__(InputRunner)
        control = dict(paused=paused, route=dict(workspace='one', server='fixture'))
        item = dict(owner='schedule:one:first', state='waiting', inputs={'text':'preserved'})
        runner.window = SimpleNamespace(state=dict(workspace='one', multi_output=dict(version=6)))
        runner.client = SimpleNamespace(url='fixture')
        runner.current = None
        runner.chain = SimpleNamespace(current=lambda: None, disconnected=Mock())
        runner.direct = SimpleNamespace(busy=lambda: False, disconnected=Mock())
        runner.controls = lambda: [('schedule:one:first', control)]
        runner.store = SimpleNamespace(rows=lambda: [item], set_control=Mock())
        runner.notify = Mock()
        return runner

    def test_already_paused_waiting_input_does_not_notify_or_rewrite(self):
        runner = self.runner(True)
        runner.disconnected()
        runner.notify.assert_called_once_with('')
        runner.store.set_control.assert_not_called()

    def test_active_waiting_input_is_paused_and_notified(self):
        runner = self.runner(False)
        runner.disconnected()
        runner.notify.assert_called_once_with(NOTICE)
        runner.store.set_control.assert_called_once_with('schedule:one:first', paused=True)
        self.assertEqual(runner.store.rows()[0]['inputs'], {'text':'preserved'})


if __name__ == '__main__':
    unittest.main()
