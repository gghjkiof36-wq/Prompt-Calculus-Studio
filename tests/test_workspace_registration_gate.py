"""S3 local/config failure terminal path and stale registration callbacks."""
import copy
import unittest
from unittest.mock import patch

from PySide6.QtCore import QObject, Signal, QByteArray
from PySide6.QtNetwork import QNetworkReply
from prompt_calculus_studio.comfy_client import ComfyClient, RequestFailure
from test_workspace_execution import WorkspaceExecutionTests as Fixture
from prompt_calculus_studio.flow_data import add_scheduler
from prompt_calculus_studio import multi_output

REAL_REQUEST = ComfyClient.request


class TimeoutReply(QObject):
    readyRead = Signal()
    finished = Signal()

    def readAll(self): return QByteArray()
    def error(self): return QNetworkReply.NetworkError.TimeoutError
    def attribute(self, key): return None


class RegistrationGateTests(unittest.TestCase):
    setUpClass = classmethod(Fixture.setUpClass.__func__)
    setUp = Fixture.setUp
    tearDown = Fixture.tearDown
    window = Fixture.window
    make = Fixture.make
    transport_request = Fixture.transport_request
    tick = Fixture.tick
    finish = Fixture.finish
    click = Fixture.click

    def fault(self, mode):
        client = self.a.host.comfy
        runner = client.input_flow.chain
        def schedule(state):
            key = add_scheduler(state)
            multi_output.connect(state, next(iter(state['multi_output']['stages'])), key+'::flow', 'flow')
        self.a.host.canvas.commit(schedule)
        runner.start(2); self.tick()
        run = runner.runs(True)[0]['id']
        job = copy.deepcopy(next(iter(client.generation.jobs.values())))
        connection = self.w.comfy_connection
        connection.registered_client = None
        self.notices.clear()
        replies = []
        original = client.request
        def hold(route, data=None, done=None, failed=None, **kwargs):
            if route != 'config': return original(route, data, done, failed, **kwargs)
            if mode == 'send': raise OSError('injected config send failure')
            replies.append((done, failed))
        if mode == 'persist':
            with patch.object(client.window, 'persist', side_effect=OSError('injected persist failure')):
                client.register_document_library()
        elif mode == 'timeout':
            reply = TimeoutReply(client)
            class Network:
                def post(inner, request, body):
                    self.assertEqual(request.transferTimeout(), 12000)
                    return reply
            with patch.object(client, 'network', Network()), patch.object(client, 'request',
                    side_effect=lambda *args, **kw: REAL_REQUEST(client, *args, **kw)):
                client.register_document_library()
                self.assertIsNotNone(connection.registering)
                reply.finished.emit()
        else:
            with patch.object(client, 'request', side_effect=hold):
                client.register_document_library()
            if mode == 'reject':
                self.assertIsNotNone(connection.registering)
                replies[0][1](RequestFailure('injected config rejection', rejected=True, status_code=400))
        self.assertIsNone(connection.registering)
        self.assertIsNone(connection.registered_client)
        record = runner.store.read(run)
        self.assertEqual((record['status'], record['pause_reason']), ('paused', 'error'))
        self.assertIn('工作流連線設定未完成', record['message'])
        self.assertEqual(len(self.notices), 1)
        self.assertEqual(client.generation.record(job['id']), job)
        if replies:
            replies[0][1]('duplicate failure'); replies[0][0]({})
            self.assertEqual(len(self.notices), 1)
            self.assertIsNone(connection.registered_client)
        self.click(self.b)
        self.assertEqual(len(self.peer.submissions), 1)
        self.assertEqual(self.w.scheduler.owner, self.a.document.runtime_token)
        self.finish()  # Original accepted A completes, then B may acquire.
        self.assertEqual(client.generation.record(job['id'])['state'], 'complete')
        runner.resume(run)
        for _ in range(8):
            self.tick()
            if self.peer.pending: self.finish()
        self.assertEqual(len(self.peer.submissions), 3, self.notices)
        self.assertEqual(len({p['prompt_id'] for p in self.peer.submissions}), 3)
        self.assertEqual(runner.store.read(run)['status'], 'complete')
        self.assertTrue(all(r['status'] == 'complete' for r in self.b.host.comfy.input_flow.chain.runs()))
        self.assertIsNone(connection.registering)
        self.assertIsNone(self.w.scheduler.owner)

    def test_persist_failure(self): self.fault('persist')
    def test_config_send_failure(self): self.fault('send')
    def test_async_config_rejection(self): self.fault('reject')
    def test_config_transport_timeout(self): self.fault('timeout')

    def test_old_success_and_failure_cannot_touch_new_registration(self):
        a, b = self.a.host.comfy, self.b.host.comfy
        connection = self.w.comfy_connection
        replies = []
        def hold(route, data=None, done=None, failed=None, **kwargs):
            self.assertEqual(route, 'config'); replies.append((done, failed))
        with patch.object(a, 'request', side_effect=hold): a.register_document_library()
        old = connection.registering
        replies[0][1]('first failed')
        self.assertIsNone(connection.registering)
        self.notices.clear()
        with patch.object(b, 'request', side_effect=hold): b.register_document_library()
        new = connection.registering
        self.assertIsNot(new, old)
        replies[0][0]({}); replies[0][1]('late failure')
        self.assertIs(connection.registering, new)
        self.assertIsNone(connection.registered_client)
        self.assertFalse(self.notices)
        replies[1][0]({})
        self.assertIs(connection.registered_client, b)
        replies[0][0]({}); replies[0][1]('even later failure')
        self.assertIs(connection.registered_client, b)
        self.assertIsNone(connection.registering)
        self.assertFalse(self.notices)

    def test_start_after_commit_persist_failure_is_contained_at_registration(self):
        runner = self.a.host.comfy.input_flow.chain
        self.w.comfy_connection.registered_client = None
        with patch.object(self.a.host.comfy.window, 'persist', side_effect=OSError('acceptance registration failure')):
            runner.start(1)
        self.assertIsNone(self.w.comfy_connection.registering)
        self.assertEqual(runner.runs(True)[0]['pause_reason'], 'error')
        self.assertEqual(sum('工作流連線設定未完成' in str(n) for n in self.notices), 1)
        self.assertFalse(self.peer.submissions)


del Fixture
