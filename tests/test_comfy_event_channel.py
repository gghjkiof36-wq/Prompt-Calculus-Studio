"""Real Qt worker/network/Workspace integration against an isolated HTTP peer."""
import copy
from contextlib import closing
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from PySide6.QtCore import QEvent,QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from channel_fixture import Clock,Peer
from prompt_calculus_studio.comfy_client import ComfyClient
from prompt_calculus_studio.comfy_event_inbox import EventInbox
from prompt_calculus_studio.extension_install import InstallPreferences
from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.workspace_shell import WorkspaceShell


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):self.respond()
    def do_POST(self):self.respond()
    def respond(self):
        size=int(self.headers.get('Content-Length','0'))
        body=json.loads(self.rfile.read(size)) if size else None
        path=urlsplit(self.path).path
        self.server.calls.append((self.command,self.path,body))
        status=200
        if path=='/prompt_studio/config':value=dict(token='test-token')
        elif path=='/prompt_studio/desktop/status':
            value=dict(ready=False,running=0,pending=0,snapshot_versions=list(range(1,8)),
                       reason='fixture',extension=self.server.fixture.capability()['extension_info'])
            value.update(self.server.fixture.capability() if self.server.channel_supported else dict(capabilities=[]))
            value['capabilities']+=['native_queue_v1','native_bindings_v1','stage_execution_v1']
            value.update(self.server.status_extra)
        elif path.startswith('/prompt_studio/events'):
            try:value=self.server.fixture.response(self.path.removeprefix('/prompt_studio/'))
            except Exception as exc:status,value=400,dict(error=str(exc))
        elif path.startswith('/history/'):
            prompt=path.removeprefix('/history/')
            value={prompt:dict(status=dict(completed=True,status_str='success'))}
        elif path=='/prompt_studio/workflow/native/recovery':value=dict(recovery=dict(location='missing',fenced=True))
        elif path=='/prompt_studio/workflow/native/cancel':value=dict(ok=True,state='failed')
        elif path=='/prompt_studio/desktop/workflows':value=None
        else:value=[]
        data=json.dumps(value).encode()
        try:
            self.send_response(status);self.send_header('Content-Type','application/json')
            self.send_header('Content-Length',str(len(data)));self.end_headers()
            if path=='/drip':
                for byte in data:
                    self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.15)
            else:self.wfile.write(data)
        except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass


class HTTPPeer(ThreadingHTTPServer):
    daemon_threads=True
    def __init__(self,root):
        root.mkdir(parents=True,exist_ok=True)
        self.fixture=Peer(root,Clock());self.calls=[];self.channel_supported=True;self.status_extra={}
        super().__init__(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.serve_forever,daemon=True);self.thread.start()
    @property
    def url(self):return f'http://127.0.0.1:{self.server_port}'
    def stop(self):self.shutdown();self.server_close();self.thread.join(2)


class QtChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.files=self.root/'files';self.files.mkdir();self.data=self.root/'data'
        self.peer=HTTPPeer(self.root/'peer')
        self.w=WorkspaceShell(self.files,self.data)
        self.installer=patch('prompt_calculus_studio.extension_page.ExtensionPage.install').start()
        self.addCleanup(patch.stopall)
    def wait(self,predicate,timeout=8):
        end=time.monotonic()+timeout
        while not predicate() and time.monotonic()<end:QTest.qWait(20)
        self.assertTrue(predicate())
    def tearDown(self):
        self.w.comfy_connection.disconnect()
        self.wait(self.w.comfy_connection.channels_stopped)
        self.assertTrue(self.w.close());self.w.deleteLater()
        self.app.sendPostedEvents(None,QEvent.Type.DeferredDelete);self.app.processEvents()
        self.peer.stop();self.temp.cleanup()
    def connect(self):
        self.assertTrue(self.w.comfy_connection.connect_to(self.peer.url))
        self.wait(lambda:self.w.comfy_connection.channel is not None)
        self.wait(lambda:self.scalar('SELECT COUNT(*) FROM streams')==1)
        return self.w.comfy_connection.channel
    def scalar(self,query):
        try:
            with closing(sqlite3.connect(self.data/'comfy-channel.sqlite3',timeout=.1)) as db:return db.execute(query).fetchone()[0]
        except sqlite3.Error:return -1

    def test_one_channel_multi_document_close_save_as_and_no_state_changes(self):
        a=self.w.open_path(create_file(self.files,'workflow','A'),True)
        b=self.w.open_path(create_file(self.files,'workflow','B'),True)
        channel=self.connect()
        tokens={a.document.runtime_token,b.document.runtime_token}
        self.wait(lambda:self.scalar('SELECT COUNT(*) FROM owners')==2)
        before={t:copy.deepcopy(self.w.runtimes.executions[t].state['multi_output']) for t in tokens}
        self.peer.fixture.append(kind='native.execution_success')
        self.wait(lambda:self.scalar('SELECT MAX(received) FROM streams')==1)
        for token in tokens:self.assertEqual(self.w.runtimes.executions[token].state['multi_output'],before[token])
        self.assertIs(self.w.comfy_connection.channel,channel)
        context=self.w.commands.context('page',tab=a)
        target=self.w.commands.files.run('save_as',context,name='A copy',directory=self.files)
        self.assertIsNotNone(target)
        self.wait(lambda:self.scalar('SELECT COUNT(*) FROM owners')>=3)
        self.assertTrue(self.w.close_tab(self.w.index_of(b.key),decision='discard',make_home=False))
        self.assertIs(self.w.comfy_connection.channel,channel)
        self.assertEqual(self.scalar('SELECT MAX(applied) FROM streams'),0)
        self.assertFalse(any(path=='/prompt' for method,path,body in self.peer.calls))

    def test_network_parse_and_persist_on_worker_during_gui_stall(self):
        threads=[]
        original=ComfyClient.request;original_page=EventInbox.page
        def request(client,route,*args,**kwargs):
            if route.startswith('events'):threads.append(QThread.currentThread() is self.app.thread())
            return original(client,route,*args,**kwargs)
        def page(inbox,*args,**kwargs):
            threads.append(QThread.currentThread() is self.app.thread())
            return original_page(inbox,*args,**kwargs)
        with patch.object(ComfyClient,'request',request),patch.object(EventInbox,'page',page):
            self.connect()
            self.peer.fixture.append(kind='native.execution_success')
            time.sleep(2.2)  # Deliberately block only the test's GUI thread.
            self.assertEqual(self.scalar('SELECT MAX(received) FROM streams'),1)
        self.assertTrue(threads);self.assertFalse(any(threads))

    def test_request_absolute_deadline_and_abort_return_handle(self):
        self.connect();client=self.w.utility.comfy
        errors=[];received=[]
        started=time.monotonic()
        reply=client.request('/drip',done=received.append,failed=errors.append,deadline=started+.08)
        self.assertIsNotNone(reply)
        self.wait(lambda:bool(errors),timeout=2)
        self.assertLess(time.monotonic()-started,1)
        self.assertEqual(received,[])
        errors.clear()
        reply=client.request('/drip',done=received.append,failed=errors.append)
        reply.abort();self.wait(lambda:bool(errors),timeout=2)
        self.assertEqual(received,[])

    def test_snapshot_pages_advance_without_waiting_for_health_timer(self):
        for index in range(300):
            self.peer.fixture.append(entity=f'native_operations:{index:04}')
        with patch('prompt_calculus_studio.comfy_event_transport.WORKER_TICK_MS',5000):
            self.connect()
            self.wait(lambda:self.scalar('SELECT MAX(received) FROM streams')==300,timeout=3)
        pages=[path for _,path,_ in self.peer.calls if '/events/snapshot?' in path]
        self.assertEqual(len(pages),3)
        self.assertEqual(self.scalar('SELECT MAX(applied) FROM streams'),0)

    def test_reconnect_and_switch_server_fence_old_generation(self):
        first=self.connect();generation=self.w.comfy_connection.channel_generation
        self.w.comfy_connection.connect_to(self.peer.url)
        self.wait(lambda:self.w.comfy_connection.channel is not None and self.w.comfy_connection.channel is not first)
        self.assertTrue(first.worker.cancelled.is_set())
        self.assertGreater(self.w.comfy_connection.channel_generation,generation)
        second=HTTPPeer(self.root/'other')
        try:
            self.w.comfy_connection.disconnect();self.wait(self.w.comfy_connection.channels_stopped)
            self.assertTrue(self.w.comfy_connection.connect_to(second.url))
            self.wait(lambda:self.w.comfy_connection.channel is not None)
            self.assertEqual(self.w.comfy_connection.channel.identity['server_instance'],second.fixture.identity['server_instance'])
            self.peer.fixture.append(kind='native.execution_success')
            QTest.qWait(300)
            self.assertEqual(self.scalar('SELECT MAX(received) FROM streams'),0)
        finally:second.stop()

    def test_shadow_success_cancel_missing_and_artificial_mismatch(self):
        self.connect();client=self.w.utility.comfy
        self.peer.fixture.append(kind='native.execution_success',entity='execution:p',prompt='p')
        client.request('/history/p',done=lambda result:None)
        self.peer.fixture.append(kind='operation.cancel_confirmed',entity='native_operations:cancel',prompt='')
        client.request('workflow/native/cancel',dict(id='cancel'),done=lambda result:None)
        self.peer.fixture.append(kind='operation.missing',entity='native_operations:missing',prompt='')
        client.request('workflow/native/recovery',dict(id='missing'),done=lambda result:None)
        self.wait(lambda:self.scalar("SELECT COUNT(*) FROM diagnostics WHERE kind='shadow_match'")==3)
        self.peer.fixture.append(kind='native.execution_error',entity='execution:p',prompt='p')
        self.wait(lambda:self.scalar("SELECT COUNT(*) FROM diagnostics WHERE kind='shadow_mismatch'")==1)
        self.assertEqual(self.scalar('SELECT MAX(applied) FROM streams'),0)

    def test_autobind_refreshes_existing_page_without_installer(self):
        extension=self.peer.fixture.root/'custom_nodes'/'comfyui_prompt_calculus_studio'
        extension.mkdir(parents=True);(self.peer.fixture.root/'main.py').write_text('# inert')
        page=self.w.ensure_utility().settings_page.manager.extension
        self.connect()
        self.wait(lambda:InstallPreferences(self.data).value['root']==str(self.peer.fixture.root))
        self.assertEqual(page.preferences.path,self.data/'extension-install.json')
        self.assertEqual(page.location.text(),str(self.peer.fixture.root))
        self.installer.assert_not_called()

    def test_old_extension_does_not_start_channel_or_disable_dispatch(self):
        self.peer.channel_supported=False
        self.w.comfy_connection.connect_to(self.peer.url)
        self.wait(lambda:self.w.utility.comfy.connected)
        self.assertIsNone(self.w.comfy_connection.channel)
        self.assertFalse((self.data/'comfy-channel.sqlite3').exists())
        self.assertFalse(any('/events' in path for _,path,_ in self.peer.calls))


if __name__=='__main__':unittest.main()
