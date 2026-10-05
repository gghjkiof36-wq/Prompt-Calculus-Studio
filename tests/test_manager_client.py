"""Local fake HTTP only; no ComfyUI process or package operation is started."""
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QCoreApplication, QEvent
from prompt_calculus_studio.manager_client import ManagerClient


class FakeManager(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass

    def do_GET(self):
        self.server.requests.append(('GET', self.path, dict(self.headers)))
        path = self.path.split('?')[0]
        if path == '/v2/manager/version':
            if self.server.redirect:
                self.send_response(302)
                self.send_header('Location', self.server.redirect)
                self.end_headers()
                return
            if self.server.legacy:
                return self.send_data(404, {})
            return self.send_data(200, '4.3')
        if path in ('/v2/customnode/installed', '/customnode/installed'):
            return self.send_data(200, {'paint': {'ver': '1.0', 'enabled': True, 'cnr_id': 'paint'}})
        if path == '/customnode/getlist':
            return self.send_data(200, {'node_packs': {'paint': {'title': 'Paint Tools', 'author': 'Example'}}})
        if path == '/v2/manager/queue/status':
            client_id = parse_qs(urlsplit(self.path).query).get('client_id', [''])[0]
            pending = len([task for task in self.server.tasks if task['ui_id'] not in self.server.history and (not client_id or task['client_id'] == client_id)])
            if not client_id:
                pending += self.server.foreign_pending
            return self.send_data(200, {'client_id': client_id, 'is_processing': False, 'total_count': pending, 'done_count': len(self.server.history), 'pending_count': pending})
        if path == '/v2/manager/queue/history':
            return self.send_data(200, {'history': self.server.history})
        return self.send_data(404, {})

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        self.server.requests.append(('POST', self.path, payload))
        if self.path == '/v2/manager/queue/task':
            if self.server.fail_task:
                return self.send_data(500, {'error': 'synthetic failure'})
            self.server.tasks.append(payload)
            if self.server.inject_foreign:
                self.server.foreign_pending = 1
            return self.send_data(200, None)
        if self.path == '/v2/manager/queue/start':
            for task in self.server.tasks:
                self.server.history[task['ui_id']] = dict(task, status={'completed': True, 'status_str': 'success'}, result='success')
            return self.send_data(200, None)
        return self.send_data(404, {})

    def send_data(self, status, data):
        body = json.dumps(data).encode() if not isinstance(data, str) else data.encode()
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class ManagerClientTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), FakeManager)
        self.server.requests, self.server.tasks, self.server.history = [], [], {}
        self.server.legacy = self.server.fail_task = False
        self.server.redirect = ''
        self.server.foreign_pending = 0
        self.server.inject_foreign = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.client = ManagerClient(Path(self.tmp.name))
        self.client.set_server('http://127.0.0.1:' + str(self.server.server_port))

    def tearDown(self):
        self.client.shutdown()
        self.client.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def wait_for(self, predicate):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            self.app.processEvents()
            if predicate():
                return
            time.sleep(.005)
        self.fail('Timed out: ' + self.client.message)

    def test_v2_updates_are_receipt_based_and_do_not_forward_token(self):
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        self.assertTrue(self.client.can_update)
        self.client.submit(self.client.plan())
        self.wait_for(lambda: self.client.journal.operations(self.client.server) and not self.client.journal.unresolved(self.client.server) and not self.client.busy)
        task = self.client.journal.operations(self.client.server)[0]['tasks'][0]
        self.assertEqual(task['state'], 'pending_restart')
        posts = [request for request in self.server.requests if request[0] == 'POST']
        self.assertEqual([p[1] for p in posts], ['/v2/manager/queue/task', '/v2/manager/queue/start'])
        self.assertEqual(posts[0][2]['params'], {'node_name': 'paint', 'node_ver': '1.0'})
        self.assertTrue(all('X-Prompt-Studio' not in r[2] for r in self.server.requests if r[0] == 'GET'))

    def test_server_error_remains_unknown_and_never_reposts(self):
        self.server.fail_task = True
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        self.client.submit(self.client.plan())
        self.wait_for(lambda: self.client.journal.unresolved(self.client.server) and not self.client.busy and not self.client.polling)
        self.assertEqual(self.client.journal.unresolved(self.client.server)[0]['state'], 'unknown')
        self.assertFalse(self.client.submit(self.client.plan()))
        self.client.poll()
        self.wait_for(lambda: not self.client.polling)
        self.assertEqual(len([r for r in self.server.requests if r[1] == '/v2/manager/queue/task']), 1)

    def test_legacy_is_readable_but_counters_do_not_authorize_updates(self):
        self.server.legacy = True
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        self.assertEqual(self.client.api, 'legacy')
        self.assertEqual(self.client.packages[0]['name'], 'Paint Tools')
        self.assertFalse(self.client.can_update)
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_redirect_is_not_followed(self):
        self.server.redirect = '/redirected'
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        self.assertFalse(self.client.available)
        self.assertEqual([r[1] for r in self.server.requests], ['/v2/manager/version'])

    def test_storage_failure_prevents_first_mutation(self):
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        def fail_save():
            raise OSError('synthetic storage failure')
        self.client.journal.save = fail_save
        self.client.submit(self.client.plan())
        self.wait_for(lambda: not self.client.busy)
        self.assertFalse(self.client.can_update)
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_switching_server_invalidates_pending_read_callbacks(self):
        self.client.refresh()
        self.client.set_server('http://127.0.0.1:1')
        for _ in range(20):
            self.app.processEvents()
            time.sleep(.005)
        self.assertEqual(self.client.server, 'http://127.0.0.1:1')
        self.assertFalse(self.client.available)
        self.assertFalse(self.client.packages)

    def test_generation_start_after_preflight_blocks_mutation(self):
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        checks = []
        def became_busy():
            checks.append(True)
            return len(checks) > 1
        self.client.activity = became_busy
        self.client.submit(self.client.plan())
        self.wait_for(lambda: not self.client.busy)
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_resume_queue_cannot_start_during_generation(self):
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        operation = self.client.journal.begin(self.client.server, self.client.plan())
        operation['tasks'][0]['state'] = 'queued'
        self.client.activity = lambda: True
        self.client.resume_queue()
        self.assertFalse(any(r[0] == 'POST' for r in self.server.requests))

    def test_shutdown_after_journal_failure_does_not_raise(self):
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        operation = self.client.journal.begin(self.client.server, self.client.plan())
        operation['tasks'][0]['state'] = 'unknown'
        self.client.journal.error = 'synthetic journal error'
        self.client.set_server('http://127.0.0.1:1')
        self.client.shutdown()

    def test_global_queue_start_is_blocked_when_foreign_work_appears(self):
        self.client.refresh()
        self.wait_for(lambda: not self.client.busy)
        self.server.inject_foreign = True
        self.client.submit(self.client.plan())
        self.wait_for(lambda: bool(self.server.tasks) and not self.client.busy)
        self.assertIn('其他待處理工作', self.client.message)
        self.assertFalse(any(r[1] == '/v2/manager/queue/start' for r in self.server.requests))


if __name__ == '__main__':
    unittest.main()
