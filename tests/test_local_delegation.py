"""Real document writer and local IPC; controlled service, no model or GPU."""
import copy
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import tempfile
from threading import Event
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from shiboken6 import delete

from prompt_calculus_studio.local_agents.delegation import DelegationService
from prompt_calculus_studio.local_agents.delegation_protocol import DelegationError, fingerprint, validate
from prompt_calculus_studio.local_agents.delegation_store import TaskStore
from prompt_calculus_studio.local_agents.store import AgentStore
from prompt_calculus_studio.local_ai.chat_document import ChatDocument
from prompt_calculus_studio.local_documents import chat, io
from tools.pcs_agent import AgentClient, discover
from tools import connector_host
from test_local_agent_owners import AgentService, register
from test_local_chat_owner import FakeWorkspace


class ControlledService(AgentService):
    def __init__(self, parent):
        super().__init__(parent)
        self._active = None

    def submit(self, request):
        if not self.can_accept:
            return False
        accepted = super().submit(request)
        self._active = request
        return accepted

    def cancel(self, identity):
        if self._active is None or self._active.request_id != identity:
            return False
        self.cancelled.append(identity)
        self.state = 'stopping'
        return True

    def complete(self, request, text, status='completed'):
        self.state, self._active = 'ready', None
        self.finished.emit(request, status, text)


class DelegationContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.root = self.base/'files'; self.root.mkdir()
        self.environment = patch.dict(os.environ, {'PROMPT_STUDIO_DATA': str(self.base/'runtime'),
                                                  'PCS_AGENT_DISCOVERY': str(self.base/'discovery')})
        self.environment.start()
        self.workspace = FakeWorkspace(self.base)
        self.workspace.root = self.root
        self.service = self.workspace.local_ai = ControlledService(self.workspace)
        self.registry, self.skill, self.agent = register(self.workspace.data, self.base, 'fixture-model')
        self.project = self.agent['project']
        self.open_calls = []
        self.workspace.open_path = self.open_path
        self.delegation = DelegationService(self.workspace)
        self.delegation.set_enabled(self.project, True)
        self.original_registry = self.registry.path.read_bytes()

    def open_path(self, path, **options):
        self.open_calls.append((path, options))
        owner = ChatDocument(path, self.workspace)
        tab = SimpleNamespace(document=owner)
        self.workspace.pages.append(tab)
        self.service.read_document = lambda: io.load(path.parent, path.name).document
        return tab

    def tearDown(self):
        self.delegation.shutdown()
        for tab in self.workspace.pages:
            tab.document.dispose()
            dialog = getattr(tab.document, '_approval_dialog', None)
            if dialog is not None:
                dialog.reject()
        self.app.processEvents()
        delete(self.workspace)
        self.environment.stop()
        self.tmp.cleanup()

    def wait(self, predicate):
        deadline = time.monotonic()+5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate(): return
            time.sleep(.005)
        self.fail('operation did not settle')

    def submit(self, key='one', task='Inspect the saving rule.'):
        return self.delegation.submit(self.project, self.agent['id'], task, key)

    def finish(self, text='<think>private reasoning</think>Verified draft', status='completed'):
        self.service.complete(self.service.submitted[-1], text, status)
        self.app.processEvents()

    def command(self, command, **fields):
        server = self.delegation.receiver
        return dict(version=1, request_id='test', instance_id=server.instance_id,
                    connection_id=server.connection_id, token=server.token, command=command, **fields)

    def result(self, task_id):
        return self.delegation.receiver.dispatch(self.command('result', task_id=task_id))

    def test_admission_saved_identity_and_duplicate_never_repeat(self):
        first = self.submit()
        self.assertEqual(len(self.open_calls), 1)
        self.assertEqual(self.open_calls[0][1], dict(retained=True, background=True))
        request = self.service.submitted[0]
        saved = self.service.persisted_at_submit[0]
        self.assertEqual(saved['document_id'], first['document_id'])
        attempt = saved['payload']['turns'][0]['attempts'][0]
        self.assertEqual(attempt['id'], first['attempt_id'])
        record = self.delegation.store.read(first['task_id'])
        self.assertEqual(record['scope'], request.scope)
        self.assertEqual(record['accepted_configuration'], request.input)
        self.assertEqual(self.submit()['task_id'], first['task_id'])
        with self.assertRaisesRegex(DelegationError, '另一項'):
            self.submit(task='Different task')
        self.finish()
        self.assertEqual(self.submit()['state'], 'complete')
        self.assertEqual(len(self.service.submitted), 1)
        self.assertEqual(len(self.open_calls), 1)
        self.assertEqual(self.registry.path.read_bytes(), self.original_registry)

    def test_readable_name_is_bounded_and_collision_preserves_existing_file(self):
        from prompt_calculus_studio.local_agents.delegation import conversation_path
        target = conversation_path(self.root, 'CON/../<>:*?\\\n'+ '🙂'*150, 'a'*64)
        self.assertEqual(target.parent, self.root)
        self.assertLess(len(target.name.encode('utf-16-le')), 160)
        self.assertNotIn('/', target.name)
        expected = conversation_path(self.root, self.agent['name'], fingerprint('one'))
        expected.write_bytes(b'original sibling')
        first = self.submit()
        self.assertNotEqual(Path(first['path']), expected)
        self.assertIn(self.agent['name'], Path(first['path']).stem)
        self.assertEqual(expected.read_bytes(), b'original sibling')
        self.assertEqual(self.submit()['path'], first['path'])
        self.assertEqual(len(self.service.submitted), 1)

    def test_duplicate_survives_agent_removal_but_still_requires_enabled_project(self):
        first = self.submit()
        self.finish()
        frozen = self.result(first['task_id'])
        self.registry.reload()
        self.registry.value['agents'] = []
        self.registry.save()
        self.assertEqual(self.submit()['task_id'], first['task_id'])
        self.assertEqual(self.result(first['task_id']), frozen)
        with self.assertRaises(DelegationError):
            self.submit(task='A different input')
        with self.assertRaises((DelegationError, ValueError)):
            self.submit(key='a new task')
        self.assertEqual(len(self.service.submitted), 1)
        self.assertEqual(len(self.open_calls), 1)
        self.delegation.set_enabled(self.project, False)
        with self.assertRaises(DelegationError):
            self.submit()

    def test_frozen_answer_evidence_and_reopen_survive_later_chat_and_rename(self):
        first = self.submit()
        request = self.service.submitted[0]
        for operation, content in [('rule', 'actual rule'), ('read_file', '1: actual source'),
                                   ('projection', 'internal wire'), ('budget', 'private accounting')]:
            self.service.activity.emit(request, dict(operation=operation, target='save.py:1',
                content=content, sha256='f'*64, status='read', skill=''))
        self.finish()
        result = self.result(first['task_id'])
        self.assertTrue(result['result']['saved'])
        self.assertEqual(result['result']['draft'], 'Verified draft')
        self.assertEqual([e['operation'] for e in result['result']['evidence']], ['rule', 'read_file'])
        self.assertNotIn('private reasoning', json.dumps(result))
        self.assertNotIn('internal wire', json.dumps(result))
        owner = self.workspace.pages[0].document
        owner.payload['turns'][0]['attempts'][0]['text'] = 'User edit after result'
        owner._edited(); self.assertTrue(owner.save())
        owner.path.rename(owner.path.with_name('renamed.pcs-chat'))
        self.assertEqual(self.result(first['task_id']), result)
        self.delegation.shutdown()
        self.delegation = DelegationService(self.workspace)
        self.assertEqual(self.result(first['task_id']), result)
        self.assertEqual(len(self.open_calls), 1)

    def test_busy_rejects_before_acceptance_or_new_tab(self):
        self.submit()
        with self.assertRaisesRegex(DelegationError, '其他工作'):
            self.submit(key='two')
        self.assertIsNone(self.delegation.store.read(fingerprint('two')))
        self.assertEqual(len(self.open_calls), 1)

    def test_engine_failure_and_output_limit_are_saved_but_not_complete(self):
        for state, expected in [('failed', 'failed'), ('limited', 'limited')]:
            first = self.submit(key=state)
            self.finish('engine failed' if state == 'failed' else '<think>hidden partial', state)
            result = self.result(first['task_id'])['result']
            self.assertEqual(result['state'], expected)
            self.assertTrue(result['saved'])
            self.assertTrue(result['review_required'])
            self.assertNotIn('hidden partial', json.dumps(result))

    def test_final_receipt_failure_retains_candidate_and_only_retries_storage(self):
        first = self.submit()
        original = self.delegation.store.write
        def fail_terminal(record):
            if record.get('result') is not None:
                raise OSError('synthetic receipt disk full')
            return original(record)
        with patch.object(self.delegation.store, 'write', side_effect=fail_terminal):
            self.finish()
            result = self.result(first['task_id'])
            self.assertEqual(result['state'], 'save_failed')
            self.assertTrue(result['result']['saved'])
            self.assertEqual(result['result']['draft'], 'Verified draft')
        self.assertEqual(self.result(first['task_id']), result)
        self.assertNotIn(first['task_id'], self.delegation.active)
        self.assertEqual(len(self.service.submitted), 1)

    def test_bounded_protocol_and_wrong_saved_attempt_identity(self):
        command = self.command('submit', project_id=self.project, agent_id=self.agent['id'],
                               task='x'*16001, idempotency_key='huge')
        with self.assertRaises(DelegationError): self.delegation.receiver.dispatch(command)
        first = self.submit()
        self.finish()
        # A crash recovery may not attach another request's answer to this ID.
        record = self.delegation.store.read(first['task_id'])
        record.update(state='working', result=None, request_id='another-request')
        self.delegation.store.write(record)
        result = self.result(first['task_id'])['result']
        self.assertEqual(result['state'], 'interrupted')
        self.assertFalse(result['saved'])
        self.assertEqual(result['draft'], '')

    def test_approval_is_observed_and_stop_waits_for_service_ack(self):
        first = self.submit()
        request = self.service.submitted[0]
        ticket = dict(done=Event(), allow=False, action=SimpleNamespace(operation='read_file', arguments={}))
        # The real approval dialog is covered by test_local_agent_review. This
        # case observes the same ticket without granting an external decision.
        with patch('prompt_calculus_studio.local_agents.dialogs.request_read_approval'):
            self.service.review.emit(request, ticket)
        self.assertEqual(self.delegation.status(first['task_id'])['state'], 'waiting_approval')
        self.assertFalse(ticket['allow'])
        with self.assertRaises(DelegationError):
            validate(self.command('approve', task_id=first['task_id']))
        stop = self.delegation.receiver.dispatch(self.command('stop', task_id=first['task_id']))
        self.assertEqual(stop['state'], 'stopping')
        with self.assertRaisesRegex(DelegationError, '處理中'):
            self.result(first['task_id'])
        self.finish('', 'cancelled')
        self.assertEqual(self.result(first['task_id'])['state'], 'stopped')
        self.assertEqual(self.service.cancelled, [request.request_id])
        frozen = self.result(first['task_id'])
        self.service.finished.emit(request, 'completed', 'late answer')
        self.app.processEvents()
        self.assertEqual(self.result(first['task_id']), frozen)

    def test_last_owner_dispose_stops_and_keeps_saved_partial(self):
        first = self.submit()
        request = self.service.submitted[0]
        self.service.delta.emit(request, 'partial')
        owner = self.workspace.pages[0].document
        owner.dispose()
        self.assertEqual(self.delegation.status(first['task_id'])['state'], 'stopping')
        self.finish('partial', 'cancelled')
        result = self.result(first['task_id'])
        self.assertEqual(result['state'], 'stopped')
        self.assertEqual(result['result']['draft'], 'partial')
        self.assertTrue(result['result']['saved'])

    def test_first_save_failure_never_calls_engine_or_retries(self):
        failure = SimpleNamespace(status='failed', error='synthetic disk failure', recovery_paths=[])
        with patch('prompt_calculus_studio.local_ai.chat_document.io.save', return_value=failure):
            first = self.submit()
        self.assertEqual(first['state'], 'save_failed')
        self.assertFalse(self.result(first['task_id'])['result']['saved'])
        self.assertEqual(self.submit()['task_id'], first['task_id'])
        self.assertEqual(self.service.submitted, [])

    def test_final_save_failure_returns_candidate_not_saved_completion(self):
        first = self.submit()
        failure = SimpleNamespace(status='failed', error='synthetic disk failure', recovery_paths=[])
        with patch('prompt_calculus_studio.local_ai.chat_document.io.save', return_value=failure):
            self.finish()
        result = self.result(first['task_id'])
        self.assertEqual(result['state'], 'save_failed')
        self.assertFalse(result['result']['saved'])
        self.assertEqual(result['result']['draft'], 'Verified draft')
        self.assertTrue(self.workspace.pages[0].document.save())
        self.assertEqual(self.result(first['task_id']), result)
        self.assertEqual(len(self.service.submitted), 1)

    def test_receipt_before_submit_failure_prevents_engine_and_retains_id(self):
        write = self.delegation.store.write
        def failing(record):
            if record.get('scope') and record['state'] == 'preparing':
                raise OSError('synthetic receipt failure')
            return write(record)
        with patch.object(self.delegation.store, 'write', side_effect=failing):
            first = self.submit()
        self.assertEqual(first['state'], 'failed')
        self.assertEqual(self.service.submitted, [])
        self.assertEqual(self.submit()['task_id'], first['task_id'])

    def test_disable_only_stops_its_delegation_and_revokes_reads(self):
        first = self.submit()
        server = self.delegation.receiver
        old = server.connection_id, server.token
        self.delegation.set_enabled(self.project, False)
        self.assertFalse(server.server.isListening())
        with self.assertRaisesRegex(DelegationError, '未允許'):
            self.delegation.status(first['task_id'])
        self.finish('', 'cancelled')
        self.delegation.set_enabled(self.project, True)
        self.assertNotEqual((server.connection_id, server.token), old)
        self.assertEqual(self.delegation.status(first['task_id'])['state'], 'stopped')
        # An ordinary follow-up in the same saved chat is not delegated work.
        owner = self.workspace.pages[0].document
        self.assertTrue(owner.send('ordinary follow-up'))
        count = len(self.service.cancelled)
        self.delegation.set_enabled(self.project, False)
        self.assertEqual(len(self.service.cancelled), count)
        self.assertTrue(owner.busy)

    def test_unknown_wrong_role_instance_token_and_overrides_rejected(self):
        store = AgentStore(self.workspace.data)
        other = store.save_project('Other', self.root, [])
        self.delegation.set_enabled(other['id'], True)
        with self.assertRaisesRegex(DelegationError, '不屬於'):
            self.delegation.submit(other['id'], self.agent['id'], 'read', 'other')
        for field, value in [('cwd', str(self.base)), ('model', 'other'), ('approval', 'all'),
                             ('output_path', str(self.base/'escape.pcs-chat')), ('skills', [])]:
            command = self.command('submit', project_id=self.project, agent_id=self.agent['id'],
                                   task='read', idempotency_key='bad')
            command[field] = value
            with self.assertRaises(DelegationError): self.delegation.receiver.dispatch(command)
        for field, value in [('instance_id', 'other'), ('connection_id', 'old'), ('token', 'bad')]:
            command = self.command('catalog'); command[field] = value
            with self.assertRaises(DelegationError): self.delegation.receiver.dispatch(command)
        with self.assertRaises(DelegationError): self.delegation.status('f'*64)
        self.assertEqual(self.open_calls, [])

    def test_interrupted_recovery_and_completed_save_before_receipt_recovery(self):
        first = self.submit()
        record = copy.deepcopy(self.delegation.store.read(first['task_id']))
        # A durable receiving attempt on restart is partial, never resubmitted.
        other = DelegationService(self.workspace)
        try:
            recovered = other.status(first['task_id'])
            self.assertEqual(recovered['state'], 'interrupted')
        finally:
            other.shutdown(); delete(other)
        self.assertEqual(len(self.service.submitted), 1)
        self.finish()
        self.delegation.store.write(record)  # Crash after chat save, before receipt.
        recovered = self.result(first['task_id'])
        self.assertEqual(recovered['state'], 'complete')
        self.assertTrue(recovered['result']['saved'])
        self.assertEqual(len(self.service.submitted), 1)

    @unittest.skipUnless(os.name == 'nt', 'actual Windows named pipe')
    def test_real_pipe_roundtrip_response_loss_readback_and_chrome_separation(self):
        client = AgentClient()
        server = self.delegation.receiver
        with ThreadPoolExecutor(max_workers=1) as pool:
            def call(command, fields=None):
                future = pool.submit(client.request, server.instance_id, command, fields)
                self.wait(future.done)
                return future.result()
            catalog = call('catalog')
            self.assertEqual(catalog['result']['agents'][0]['id'], self.agent['id'])
            fields = dict(project_id=self.project, agent_id=self.agent['id'], task='read', idempotency_key='pipe')
            first = call('submit', fields)['result']
            self.finish()
            # Same key after a lost response returns the existing saved result.
            self.assertEqual(call('submit', fields)['result']['task_id'], first['task_id'])
            result = call('result', dict(task_id=first['task_id']))
            self.assertEqual(result['result']['result']['draft'], 'Verified draft')
            self.assertEqual(len(self.open_calls), 1)
        self.assertEqual(connector_host.discover_instances(self.base/'discovery'), [])
        with self.assertRaises(connector_host.ConnectorError):
            connector_host.validate_request(dict(command='submit', request_id='x'))
        self.assertNotIn(server.token, json.dumps(result))
        self.assertEqual(len(discover(self.base/'discovery')), 1)


class DelegationWorkerApproval(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_real_worker_waits_for_original_owner_and_stop_ack(self):
        from test_local_agent_review import AgentReviewOwnership
        fixture = AgentReviewOwnership()
        fixture.app = self.app
        fixture.setUp()
        fixture.workspace.root = fixture.root
        with patch.dict(os.environ, {'PCS_AGENT_DISCOVERY': str(fixture.root/'discovery')}):
            delegation = DelegationService(fixture.workspace)
            def opened(path, **kwargs):
                owner = ChatDocument(path, fixture.workspace)
                fixture.owners.append(owner)
                tab = SimpleNamespace(document=owner)
                fixture.workspace.pages.append(tab)
                return tab
            fixture.workspace.open_path = opened
            try:
                delegation.set_enabled(fixture.agent['project'], True)
                first = delegation.submit(fixture.agent['project'], fixture.agent['id'], '@saving inspect', 'worker')
                fixture.wait(lambda: fixture.reviews)
                request, ticket = fixture.reviews[0]
                self.assertEqual(delegation.status(first['task_id'])['state'], 'waiting_approval')
                self.assertFalse(ticket['done'].is_set())
                self.assertEqual(fixture.reads(), [])
                self.assertEqual(fixture.service.approval_mode, 'ask')
                delegation.dispatch(dict(command='stop', task_id=first['task_id']))
                self.assertEqual(delegation.status(first['task_id'])['state'], 'stopping')
                fixture.wait(lambda: fixture.service.can_accept and ticket['done'].is_set())
                fixture.app.processEvents()
                self.assertEqual(delegation.status(first['task_id'])['state'], 'stopped')
                self.assertFalse(ticket['allow'])
                self.assertEqual(fixture.reads(), [])
                self.assertEqual(fixture.b.path.read_bytes(), fixture.other_bytes)
            finally:
                delegation.shutdown()
                fixture.tearDown()


if __name__ == '__main__':
    unittest.main()
