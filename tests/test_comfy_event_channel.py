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

from PySide6.QtCore import QEvent,QEventLoop,QThread,QTimer
from PySide6.QtWidgets import QApplication

from channel_fixture import Clock,Peer
from prompt_calculus_studio.comfy_client import ComfyClient
from prompt_calculus_studio.comfy_event_inbox import EventInbox
from prompt_calculus_studio.extension_install import InstallPreferences
from prompt_calculus_studio.workspace_files import create_file
from prompt_calculus_studio.workspace_shell import WorkspaceShell


def qt_wait(milliseconds):
    # This build's QTest.qWait holds the GIL, unlike the application's event
    # loop; it can deadlock concurrent QObject destruction on a Python worker.
    loop=QEventLoop();timer=QTimer();timer.setSingleShot(True)
    timer.timeout.connect(loop.quit);timer.start(milliseconds);loop.exec()


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
            value={prompt:self.server.histories.get(prompt,dict(status=dict(completed=True,status_str='success')))}
        elif path=='/prompt_studio/workflow/native/recovery':value=self.server.receipts.get(body['id'],dict(recovery=dict(location='missing',fenced=True)))
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
        self.fixture=Peer(root,Clock());self.calls=[];self.channel_supported=True;self.status_extra={};self.receipts={};self.histories={}
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
        while not predicate() and time.monotonic()<end:qt_wait(20)
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

    def test_healthy_periodic_status_is_coalesced_but_input_aux_and_manual_are_not(self):
        tab=self.w.open_path(create_file(self.files,'workflow','cadence'),True)
        client=tab.host.comfy;self.connect()
        self.wait(lambda:client.events.ready and self.w.comfy_connection.channel_receiving and
                  self.w.comfy_connection.registered_client is client and
                  self.w.comfy_connection.registering is None)
        for target in self.w.comfy_connection.live_clients():target.timer.stop()
        now=[0.];calls=[]
        def request(route,data=None,done=None,failed=None,**options):
            calls.append((now[0],route))
            if route=='desktop/status':client.polling=False
            elif done:done(dict(state='pending'))
        def tick(at):now[0]=at;client.timer.timeout.emit()
        def statuses():return [at for at,route in calls if route=='desktop/status']
        with patch.object(client,'request',request),patch.object(client,'_status_clock',lambda:now[0]):
            client._desktop_status_at=0
            for at in (1.5,3,4.5,6,7.5):tick(at)
            self.assertEqual(statuses(),[])
            tick(9);self.assertEqual(statuses(),[9])
            client.poll();self.assertEqual(statuses(),[9,9])  # Explicit recheck.
            with client.window.store.db:
                client.window.store.db.execute('INSERT INTO workspace_operations VALUES (?,?,?,0,?)',
                    ('aux',client.url,'workflow/native/inspect','submitting'))
            tick(10.5);self.assertEqual(statuses()[-1],10.5)
            self.assertIn((10.5,'workflow/native/status'),calls)
            with client.window.store.db:client.window.store.db.execute("UPDATE workspace_operations SET ended=1 WHERE id='aux'")
            row=client.input_flow.chain.store.add('input',client.window.state['workspace'],
                sent=True,ended=False,server=client.url,status='submitting')
            tick(12);self.assertEqual(statuses()[-1],12)
            client.input_flow.chain.store.update(row['id'],ended=True)
            for at,name,value in ((13.5,'applying',{}),(15,'apply_queue',[{}]),(16.5,'apply_cancellations',{'one'})):
                # A current apply is a nonempty operation in normal execution.
                value=value or {'id':'apply'}
                with patch.object(client.input_flow.chain,name,value,create=True):
                    tick(at);self.assertEqual(statuses()[-1],at)
            with patch.object(self.w.comfy_connection,'channel_receiving',False):
                tick(18);self.assertEqual(statuses()[-1],18)
            with patch.object(client.events,'ready',False):
                tick(19.5);self.assertEqual(statuses()[-1],19.5)
            with patch.object(client,'event_capable',False):
                tick(21);self.assertEqual(statuses()[-1],21)
            self.assertTrue(client.owns_library_registration)
            with patch.object(client,'register_library',True):
                tick(22.5);self.assertEqual(statuses()[-1],22.5)
            tick(24);self.assertEqual(statuses()[-1],22.5)
            # C3-owned apply has a durable intent and bounded reconciliation;
            # unlike the legacy inputs above, it keeps the healthy cadence.
            client.events.aux.known.add('covered')
            with patch.object(client.input_flow.chain,'applying','covered'):
                tick(25.5);self.assertEqual(statuses()[-1],22.5)
                tick(31.5);self.assertEqual(statuses()[-1],31.5)
            client.events.aux.known.discard('covered')

    def test_utility_status_uses_the_same_healthy_channel_deadline(self):
        # A document owner accepts the initial snapshot. Without an owner the
        # channel conservatively remains pending reconciliation and stays fast.
        self.w.open_path(create_file(self.files,'workflow','owner'),True)
        self.connect();client=self.w.utility.comfy
        self.wait(lambda:self.w.comfy_connection.channel_receiving and client.connected)
        client.timer.stop();calls=[];now=[0.]
        def request(route,*args,**kwargs):
            if route=='desktop/status':calls.append(now[0]);client.polling=False
        with patch.object(client,'request',request),patch.object(client,'_status_clock',lambda:now[0]):
            client._desktop_status_at=0
            now[0]=1.5;client.timer.timeout.emit();self.assertEqual(calls,[])
            now[0]=9;client.timer.timeout.emit();self.assertEqual(calls,[9])

    def test_twelve_second_channel_deadline_projects_existing_connection_check_state(self):
        from prompt_calculus_studio.comfy_event_transport import ShadowChannel
        tab=self.w.open_path(create_file(self.files,'workflow','deadline'),True)
        client=tab.host.comfy;channel=self.connect()
        self.wait(lambda:client.events.ready and self.w.comfy_connection.channel_receiving)
        clients=self.w.comfy_connection.live_clients()
        for target in clients:target.timer.stop()
        before=copy.deepcopy(tab.document.state)
        peer=self.peer.fixture;manager=self.w.comfy_connection;generation=manager.channel_generation
        def project(value):
            if 'health' in value or 'heartbeat' in value:manager.channel_health(value,generation)
        with closing(EventInbox(self.data/'deadline-projection')) as inbox:
            core=ShadowChannel(inbox,peer.send,peer.identity,clock=peer.clock,
                notify=project)
            peer.advance(core,2)
            observed=min(core.last_events,core.last_heartbeat)
            peer.hold={'events','events/heartbeat','events/snapshot'}
            peer.advance(core,observed+12-peer.clock()-.25)
            self.assertTrue(all(not target.connection_checking for target in clients))
            peer.advance(core,.25)
            self.assertEqual(peer.clock()-observed,12)
            self.assertTrue(all(target.connection_checking for target in clients))
            self.assertTrue(all(target.message=='正在確認 ComfyUI 連線…' for target in clients))
            self.assertFalse(manager.channel_receiving)
            self.assertEqual(tab.document.state,before)
            self.assertEqual(peer.submissions,1)
            peer.hold.clear();core.stop()
        client.poll();self.wait(lambda:not client.polling and not client.connection_checking)

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
        self.wait(lambda:self.scalar('SELECT MAX(applied) FROM streams')==1)
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
            # C1 first authorizes targeted reconciliation on the GUI. Once
            # the initial watermark is ready, network progress is independent.
            self.wait(lambda:self.scalar("SELECT COUNT(*) FROM streams WHERE snapshot!=''")>0)
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
        channel=self.connect()
        self.wait(lambda:self.scalar('SELECT MAX(received) FROM streams')==300,timeout=3)
        self.assertTrue(channel.worker.timer.isSingleShot())
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
            qt_wait(300)
            self.assertEqual(self.scalar('SELECT MAX(received) FROM streams'),0)
        finally:second.stop()

    def test_http_replies_no_longer_run_shadow_comparisons(self):
        self.connect();client=self.w.utility.comfy
        replies=[]
        with patch.object(self.w.comfy_connection,'observe_poll') as observe:
            self.peer.fixture.append(kind='native.execution_success',entity='execution:p',prompt='p')
            client.request('/history/p',done=replies.append)
            self.peer.fixture.append(kind='operation.cancel_confirmed',entity='native_operations:cancel',prompt='')
            client.request('workflow/native/cancel',dict(id='cancel'),done=replies.append)
            self.peer.fixture.append(kind='operation.missing',entity='native_operations:missing',prompt='')
            client.request('workflow/native/recovery',dict(id='missing'),done=replies.append)
            self.wait(lambda:len(replies)==3 and self.scalar('SELECT MAX(received) FROM streams')==3)
            observe.assert_not_called()
        self.assertEqual(self.scalar("SELECT COUNT(*) FROM diagnostics WHERE kind LIKE 'shadow_%'"),0)
        self.assertEqual(self.scalar('SELECT MAX(applied) FROM streams'),0)

    def test_unchanged_status_reuses_recognition_and_has_no_owner_commands(self):
        from prompt_calculus_studio import comfy_event_protocol as protocol
        channel=self.connect();client=self.w.utility.comfy
        value=self.peer.fixture.capability()
        manager=self.w.comfy_connection
        manager.observe_status(client,value)
        with patch.object(protocol,'recognize',wraps=protocol.recognize) as recognize,\
             patch.object(manager,'remember_event_owner') as owners,\
             patch.object(manager,'stop_channel') as stop:
            for node in range(1000):
                value['executing_node']=str(node)
                manager.observe_status(client,value)
                self.assertEqual(client.event_identity(value)['epoch'],channel.identity['epoch'])
            recognize.assert_not_called();owners.assert_not_called();stop.assert_not_called()
            # Mutating a reused response must not mutate the cached signature.
            value['extension_info']['version']='changed'
            manager.observe_status(client,value);client.event_identity(value)
            self.assertEqual(recognize.call_count,1)
            self.assertEqual(client.event_identity(value)['extension_info']['version'],'changed')

    def test_autobind_refreshes_existing_page_without_installer(self):
        extension=self.peer.fixture.root/'custom_nodes'/'comfyui_prompt_calculus_studio'
        extension.mkdir(parents=True);(self.peer.fixture.root/'main.py').write_text('# inert')
        page=self.w.ensure_utility().settings_page.manager.extension
        self.connect()
        self.wait(lambda:InstallPreferences(self.data).value['root']==str(self.peer.fixture.root))
        self.assertEqual(page.preferences.path,self.data/'extension-install.json')
        self.assertEqual(page.location.text(),str(self.peer.fixture.root))
        self.installer.assert_not_called()

    def test_old_extension_does_not_start_channel_and_blocks_new_dispatch(self):
        self.peer.channel_supported=False
        self.w.comfy_connection.connect_to(self.peer.url)
        self.wait(lambda:self.w.utility.comfy.connected)
        self.assertIsNone(self.w.comfy_connection.channel)
        self.assertFalse((self.data/'comfy-channel.sqlite3').exists())
        self.assertFalse(any('/events' in path for _,path,_ in self.peer.calls))
        self.assertFalse(self.w.utility.comfy.can_run)
        self.assertFalse(self.w.utility.comfy.dispatch_allowed)
        self.peer.channel_supported=True
        self.w.utility.comfy.poll()
        self.wait(lambda:self.w.comfy_connection.channel is not None)
        self.assertTrue(self.w.utility.comfy.event_capable)

    def test_independent_host_accepts_channel_and_stops_worker_on_close(self):
        from prompt_calculus_studio.window import Window
        window=Window(self.root/'standalone')
        try:
            client=window.comfy;client.url=self.peer.url
            client.enabled=True;client.token='test-token';client.register_library=False
            client.poll()
            self.wait(lambda:client.events.ready)
            self.peer.fixture.append(kind='native.execution_success')
            def delivered():
                try:
                    with closing(sqlite3.connect(window.store.directory/'comfy-channel.sqlite3',timeout=.1)) as db:
                        return db.execute('SELECT MAX(applied) FROM streams').fetchone()[0]==1
                except sqlite3.Error:return False
            self.wait(delivered)
            self.assertIsNotNone(client.event_session.channel)
        finally:window.close()
        self.assertTrue(client.event_session.stopped())

    def test_real_worker_delivers_terminal_only_after_document_commit(self):
        tab=self.w.open_path(create_file(self.files,'workflow','Owned'),True)
        self.connect();client=tab.host.comfy
        self.wait(lambda:client.events.ready)
        job=dict(id='owned',native_operation='owned',prompt_id='prompt',state='queued',created=time.time(),
                 server=self.peer.url,workspace=client.window.state['workspace'],payload={},error='')
        client.generation.jobs[job['id']]=copy.deepcopy(job);client.generation.save(job)
        self.peer.receipts['owned']=dict(id='owned',state='queued',prompt_id='prompt',recovery=dict(location='history'))
        self.peer.fixture.append(entity='execution:prompt',kind='native.execution_success',prompt='prompt')
        self.wait(lambda:client.generation.record('owned')['state']=='complete')
        self.wait(lambda:self.scalar('SELECT MAX(applied) FROM streams')==1)
        self.assertEqual(client.window.store.db.execute('SELECT COUNT(*) FROM channel_applied_events').fetchone()[0],1)
        self.assertFalse(any(path=='/prompt' for _,path,_ in self.peer.calls))


if __name__=='__main__':unittest.main()
