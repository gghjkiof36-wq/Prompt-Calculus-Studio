"""Exercise the workspace's real Qt network path against an isolated HTTP peer."""
import copy
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from PySide6.QtCore import QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class Peer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), Handler)
        self.calls = []
        self.failure = False
        self.hold_status = None
        self.status = dict(ready=False, reason='fixture: no browser controller', running=0, pending=0,
                           snapshot_versions=[1, 2, 3, 4, 5, 6, 7],
                           capabilities=['native_queue_v1', 'native_bindings_v1',
                                         'stage_execution_v1', 'workflow_transfer_v1'])
        self.thread = threading.Thread(target=self.serve_forever, daemon=True)
        self.thread.start()

    @property
    def url(self):
        return 'http://127.0.0.1:'+str(self.server_port)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        self.respond()

    def do_POST(self):
        self.respond()

    def respond(self):
        size = int(self.headers.get('Content-Length', '0'))
        body = json.loads(self.rfile.read(size)) if size else None
        self.server.calls.append((self.command, self.path, body))
        if self.path.endswith('/desktop/status') and self.server.hold_status:
            self.server.hold_status.wait(5)
        if self.server.failure:
            status, result = 404, {'error': 'fixture: PCS extension unavailable'}
        elif self.path.endswith('/config'):
            status, result = 200, {'token': 'fixture-token'}
        elif self.path.endswith('/desktop/status'):
            status, result = 200, self.server.status
        elif self.path.endswith('/desktop/workflows'):
            status, result = 200, None
        else:
            status, result = 200, []
        data = json.dumps(result).encode()
        try:
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # Expected when testing disconnect while a reply is pending.


class WorkspaceConnectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.area = tempfile.TemporaryDirectory(prefix='pcs-connection-')
        self.root = Path(self.area.name)/'files'
        self.root.mkdir()
        self.data = Path(self.area.name)/'data'
        self.peer = Peer()
        self.errors = []
        self.old_hook = sys.excepthook
        sys.excepthook = lambda kind, error, trace: self.errors.append(str(error))
        self.w = self.window()
        self.w.comfy_connection.url = self.peer.url

    def window(self):
        window = WorkspaceShell(self.root, self.data)
        if self.app.platformName() == 'windows':
            window.setAttribute(Qt.WidgetAttribute.WA_DontShowOnScreen)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        window.show()
        QTest.qWait(30)
        return window

    def close_window(self):
        self.w.close()
        self.w.deleteLater()
        self.app.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.app.processEvents()

    def tearDown(self):
        if self.peer.hold_status:
            self.peer.hold_status.set()
        self.close_window()
        self.peer.shutdown()
        self.peer.server_close()
        self.peer.thread.join(2)
        sys.excepthook = self.old_hook
        self.area.cleanup()
        self.assertEqual(self.errors, [])

    def wait(self, predicate):
        deadline = time.monotonic()+5
        while not predicate() and time.monotonic() < deadline:
            QTest.qWait(15)
        self.assertTrue(predicate())

    def connected(self):
        self.wait(lambda: self.w.utility is not None and self.w.utility.comfy.connected)
        return self.w.utility.comfy

    def canvas(self, name):
        path = create_file(self.root, 'workflow', name)
        return self.w.open_path(path, True)

    def test_header_uses_http_and_leaves_navigation_and_documents_untouched(self):
        note = self.root/'Note.md'
        note.write_text('body', encoding='utf-8')
        tab = self.w.open_path(note)
        history, widget = list(self.w.history), self.w.workspace_stack.currentWidget()
        self.peer.hold_status = threading.Event()
        QTest.mouseClick(self.w.connection, Qt.MouseButton.LeftButton)
        self.assertIn('連線中', self.w.connection.brand.text())
        self.assertFalse(self.w.connection.isEnabled())
        self.peer.hold_status.set()
        client = self.connected()
        self.assertIn('已連線', self.w.connection.brand.text())
        self.assertTrue(self.w.connection.isEnabled())
        self.assertIn(self.peer.url, self.w.connection.toolTip())
        self.assertEqual(self.w.history, history)
        self.assertIs(self.w.workspace_stack.currentWidget(), widget)
        self.assertEqual(self.w.active_key, tab.key)
        self.assertFalse(client.can_run)
        self.assertTrue(all(method == 'GET' for method, _, _ in self.peer.calls))
        self.assertFalse(any('desktop/results' in route for _, route, _ in self.peer.calls))
        self.assertEqual(note.read_text(encoding='utf-8'), 'body')

    def test_settings_connect_and_disconnect_update_header_and_saved_preferences(self):
        self.w.open_route('settings', 'workflows')
        form = self.w.utility.settings_page.workflows
        form.address.setText(self.peer.url)
        QTest.mouseClick(form.connect_button, Qt.MouseButton.LeftButton)
        self.connected()
        self.assertIn('已連線', self.w.connection.brand.text())
        self.assertTrue(form.disconnect_button.isVisible())
        QTest.mouseClick(form.disconnect_button, Qt.MouseButton.LeftButton)
        self.assertFalse(self.w.utility.comfy.connected)
        self.assertFalse(self.w.utility.comfy.timer.isActive())
        self.assertIn('未連線', self.w.connection.brand.text())
        self.assertFalse(json.loads((self.data/'workspace-connection.json').read_text())['enabled'])

    def test_failure_releases_control_and_retry_recovers(self):
        self.peer.failure = True
        self.w.connection.click()
        client = self.w.utility.comfy
        self.wait(lambda: not client.connection_checking)
        self.assertFalse(client.connected)
        self.assertTrue(self.w.connection.isEnabled())
        self.assertIn('PCS extension unavailable', self.w.connection.toolTip())
        self.peer.failure = False
        QTest.qWait(800)
        self.w.connection.click()
        self.connected()
        self.assertIn('已連線', self.w.connection.brand.text())

    def test_runtime_restriction_is_not_reported_as_an_extension_update(self):
        available = copy.deepcopy(self.peer.status)
        reason = '此原生入口目前只支援 ComfyUI 單一使用者模式。'
        self.peer.status.update(capabilities=['direct_generation_v1', 'workflow_transfer_v1'],
                                native_unavailable_reason=reason)
        tab = self.canvas('Restricted')
        before = copy.deepcopy(tab.host.state['multi_output'])
        self.w.connection.click()
        client = tab.host.comfy
        self.wait(lambda: client.connected)
        client.timer.stop()
        self.assertFalse(client.snapshot_compatible)
        self.assertFalse(client.can_run)
        self.assertEqual(client.message, reason)
        tab.host.canvas.execution_bar.update_progress()
        message = tab.host.canvas.execution_bar.status.text()
        self.assertIn(reason, message)
        self.assertNotIn('更新', message)
        self.assertFalse(any(method == 'POST' and route.endswith('/config')
                             for method, route, _ in self.peer.calls))
        self.peer.status = available
        client.poll()
        self.wait(lambda: client.snapshot_compatible)
        self.assertTrue(client.can_run)
        tab.host.canvas.execution_bar.update_progress()
        self.assertNotIn(reason, tab.host.canvas.execution_bar.status.text())
        self.assertEqual(tab.host.state['multi_output'], before)

    def test_missing_execution_capability_does_not_invent_a_version_mismatch(self):
        self.peer.status['capabilities'] = ['direct_generation_v1']
        tab = self.canvas('Missing capability')
        self.w.connection.click()
        client = tab.host.comfy
        self.wait(lambda: client.connected)
        self.assertFalse(client.can_run)
        self.assertIn('未提供', client.message)
        self.assertNotIn('重啟', client.message)
        tab.host.canvas.execution_bar.update_progress()
        self.assertIn(client.compatibility_message, tab.host.canvas.execution_bar.status.text())

    def test_idle_disconnect_does_not_claim_to_preserve_nonexistent_work(self):
        tab = self.canvas('Idle')
        client = tab.host.comfy
        self.w.connection.click()
        self.wait(lambda: client.connected and not client.replies)
        for live in self.w.comfy_connection.live_clients():
            live.timer.stop()
        self.assertFalse(client.input_flow.has_work())
        before = copy.deepcopy(tab.host.state['multi_output'])
        with patch.object(client.window, 'notice') as notice:
            self.peer.failure = True
            client.poll()
            self.wait(lambda: not client.connected)
            self.assertFalse(any('保留' in str(call) for call in notice.call_args_list))
        self.assertFalse(client.input_flow.has_work())
        self.assertEqual(tab.host.state['multi_output'], before)

    def test_restart_restores_endpoint_and_disconnect_does_not_auto_reconnect(self):
        self.w.connection.click()
        self.connected()
        self.close_window()
        self.w = self.window()
        client = self.connected()
        self.assertEqual(client.url, self.peer.url)
        client.disconnect()
        self.close_window()
        count = len(self.peer.calls)
        self.w = self.window()
        QTest.qWait(100)
        self.assertIsNone(self.w.utility)
        self.assertEqual(self.w.comfy_connection.url, self.peer.url)
        self.assertFalse(self.w.comfy_connection.enabled)
        self.assertEqual(len(self.peer.calls), count)

    def test_late_reply_cannot_reconnect_after_disconnect(self):
        self.peer.hold_status = threading.Event()
        self.w.connection.click()
        client = self.w.utility.comfy
        self.wait(lambda: any(route.endswith('/desktop/status') for _, route, _ in self.peer.calls))
        client.disconnect()
        self.peer.hold_status.set()
        QTest.qWait(100)
        self.assertFalse(client.enabled)
        self.assertFalse(client.connected)
        self.assertFalse(client.connection_checking)
        self.assertTrue(self.w.connection.isEnabled())

    def test_immediate_reconnect_after_disconnect_is_not_debounced(self):
        self.w.connection.click()
        client = self.connected()
        client.disconnect()
        self.w.connection.click()
        self.assertTrue(client.enabled)
        self.assertTrue(client.timer.isActive())
        self.connected()

    def test_connecting_from_canvas_keeps_status_on_markdown_and_new_tab(self):
        canvas = self.canvas('First')
        self.assertIsNone(self.w.utility)
        self.w.connection.click()
        self.wait(lambda: canvas.host.comfy.connected)
        self.connected()
        note = self.root/'Note.md'
        note.write_text('body', encoding='utf-8')
        self.w.open_path(note)
        self.assertIn('已連線', self.w.connection.brand.text())
        self.w.new_tab()
        self.assertIn('已連線', self.w.connection.brand.text())

    def test_late_workflow_import_after_save_as_is_rejected_before_writing(self):
        canvas = self.canvas('First')
        client = canvas.host.comfy
        before = copy.deepcopy(canvas.host.state)
        callbacks, acknowledgements = [], []
        def request(route, data=None, done=None, **kwargs):
            if route == 'desktop/workflows':
                callbacks.append(done)
            elif route == 'desktop/workflows/ack':
                acknowledgements.append(data)
        with patch.object(client, 'request', side_effect=request):
            client.receive_workflow()
            self.assertEqual(len(callbacks), 1)
            execution = canvas.document.execution
            self.w.commands.files.run('save_as', self.w.commands.context('tab', tab=canvas),
                                      name='Copied', directory=self.root)
            saved = copy.deepcopy(execution.state)
            callbacks[0](dict(id='late-import', value={'invalid-if-applied': True}))
        self.assertEqual(execution.state, saved)
        self.assertEqual(before['multi_output'], execution.state['multi_output'])
        self.assertEqual(acknowledgements[0]['id'], 'late-import')
        self.assertIn('原文件已另存或關閉', acknowledgements[0]['error'])

    def test_documents_share_endpoint_but_keep_execution_and_library_owners(self):
        self.w.connection.click()
        self.connected()
        first = self.canvas('First')
        self.wait(lambda: first.host.comfy.connected)
        first_bytes = first.path.read_bytes()
        second = self.canvas('Second')
        self.wait(lambda: second.host.comfy.connected)
        duplicate = self.w.duplicate_view(first)
        self.assertIs(duplicate.host.comfy, first.host.comfy)
        self.assertIsNot(first.host.comfy, second.host.comfy)
        for tab in (first, second):
            self.assertEqual(tab.host.comfy.url, self.peer.url)
            self.assertIs(tab.host.comfy.window.document, tab.document)
            self.assertFalse(tab.document.dirty)
        self.w.focus_tab(first)
        self.assertTrue(first.host.comfy.register_library)
        prior = len(self.peer.calls)
        first.host.comfy.poll()
        expected_library = str(first.host.store.directory/'studio.sqlite3')
        self.wait(lambda: any(method == 'POST' and route.endswith('/config')
                              and body.get('library') == expected_library
                              for method, route, body in self.peer.calls[prior:]))
        registrations = [body['library'] for method, route, body in self.peer.calls
                         if method == 'POST' and route.endswith('/config')]
        self.assertEqual(registrations[-1], str(first.host.store.directory/'studio.sqlite3'))
        self.assertFalse(any('desktop/results' in route for _, route, _ in self.peer.calls))
        self.assertEqual(first.path.read_bytes(), first_bytes)
        self.w.utility.comfy.disconnect()
        self.assertTrue(all(not client.enabled for client in self.w.comfy_connection.live_clients()))

    def test_same_endpoint_recheck_preserves_work_and_server_change_is_rejected(self):
        first = self.canvas('First')
        client = first.host.comfy
        client.connect_to(self.peer.url)
        self.wait(lambda: client.connected and not client.replies)
        client.run_id = 'frozen-owner'
        epoch, state = client.epoch, copy.deepcopy(first.host.state['multi_output'])
        with patch.object(client, 'reset_connection') as reset, patch.object(client, 'check_run'):
            QTest.qWait(800)
            client.recheck_connection()
            self.wait(lambda: not client.connection_checking)
            reset.assert_not_called()
            self.assertFalse(client.connect_to('http://127.0.0.1:1'))
            reset.assert_not_called()
        self.assertEqual(client.url, self.peer.url)
        self.assertEqual(client.run_id, 'frozen-owner')
        self.assertEqual(client.epoch, epoch)
        self.assertEqual(first.host.state['multi_output'], state)
        client.run_id = ''

    def test_invalid_endpoint_and_preferences_write_failure_do_not_start_network(self):
        host = self.w.ensure_utility()
        with self.assertRaises(ValueError):
            host.comfy.connect_to('https://example.com')
        with patch.object(self.w.comfy_connection, 'save', side_effect=OSError('fixture disk failure')):
            self.assertFalse(host.comfy.connect_to(self.peer.url))
        self.assertFalse(host.comfy.enabled)
        self.assertFalse(self.w.comfy_connection.enabled)
        self.assertEqual(self.peer.calls, [])
        self.assertIn('無法儲存', self.w.notification.text())


if __name__ == '__main__':
    unittest.main()
