"""Deterministic checks for Python virtual callbacks under QObject teardown."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import tempfile
import threading
import time
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PySide6.QtCore import QCoreApplication, QEvent, QEventLoop, QThread, QTimer
from PySide6.QtNetwork import QNetworkAccessManager
from PySide6.QtWidgets import QApplication
from shiboken6 import isValid

from prompt_calculus_studio import comfy_client, comfy_event_transport as transport
from test_comfy_event_channel import HTTPPeer


def wait_until(predicate, seconds=5):
    end=time.monotonic()+seconds
    while not predicate() and time.monotonic()<end:
        loop=QEventLoop();timer=QTimer();timer.setSingleShot(True)
        timer.timeout.connect(loop.quit);timer.start(10);loop.exec()
    if not predicate():raise AssertionError('Qt operation did not complete')


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args):pass
    def do_GET(self):
        self.server.calls.append(self.path)
        if self.path=='/slow':time.sleep(.25)
        data=json.dumps(dict(error='fixture') if self.path=='/error' else dict(ok=True)).encode()
        try:
            self.send_response(500 if self.path=='/error' else 200)
            self.send_header('Content-Length',str(len(data)));self.end_headers();self.wfile.write(data)
        except (BrokenPipeError,ConnectionError):pass


def exercise(url, scenario):
    calls=[];phase='request';done=[];failed=[];finished=[]
    class Timer(QTimer):
        def disconnectNotify(self, signal):
            calls.append((phase,'timeout',bytes(signal.methodSignature()).decode()))
            super().disconnectNotify(signal)
    class Manager(QNetworkAccessManager):
        def childEvent(self, event):
            if event.removed():calls.append((phase,'parent','ChildRemoved'))
            super().childEvent(event)
        def disconnectNotify(self,signal):
            calls.append((phase,'manager.disconnectNotify',bytes(signal.methodSignature()).decode()))
            super().disconnectNotify(signal)
    host=SimpleNamespace(network=Manager(),stopped=False,epoch=0,replies=set(),url=url,token='fixture')
    route='/slow' if scenario in ('deadline','abort') else '/error' if scenario=='error' else '/ok'
    deadline=time.monotonic()+(.08 if scenario=='deadline' else 2)
    with patch.object(comfy_client,'QTimer',Timer):
        reply=comfy_client.ComfyClient.request(host,route,done=done.append,failed=failed.append,deadline=deadline)
    timer=reply.findChild(QTimer)
    def observed():
        nonlocal phase
        finished.append(dict(parent_detached=reply.parent() is None,timer_stopped=not timer.isActive(),
                             untracked=not host.replies,still_alive=isValid(reply)))
        phase='deferred_delete'
    reply.finished.connect(observed)
    if scenario=='stale':host.epoch+=1
    if scenario=='stopped':host.stopped=True
    if scenario=='abort':reply.abort()
    wait_until(lambda:bool(finished))
    QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    result=dict(calls=calls,finished=finished,done=len(done),failed=len(failed),
                deleted=not isValid(reply),timer_deleted=not isValid(timer),thread=threading.get_ident())
    host.network.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)
    return result


class RequestLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])

    def test_request_cleanup_precedes_deferred_delete_in_both_threads(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler);server.daemon_threads=True;server.calls=[]
        serving=threading.Thread(target=server.serve_forever,daemon=True);serving.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        url=f'http://127.0.0.1:{server.server_port}';main=threading.get_ident()
        for worker in (False,True):
            for scenario in ('success','error','deadline','abort','stale','stopped'):
                with self.subTest(worker=worker,scenario=scenario):
                    if worker:
                        class Thread(QThread):
                            def run(self):
                                try:self.result=exercise(url,scenario)
                                except BaseException as exc:self.error=exc
                        thread=Thread();thread.error=None;thread.start()
                        wait_until(lambda:thread.wait(0))
                        if thread.error:raise thread.error
                        result=thread.result;thread.deleteLater()
                    else:result=exercise(url,scenario)
                    self.assertEqual(result['thread']!=main,worker)
                    self.assertEqual(len(result['finished']),1)
                    self.assertTrue(all(result['finished'][0].values()),result)
                    self.assertTrue(result['deleted'] and result['timer_deleted'])
                    self.assertFalse([c for c in result['calls'] if c[0]=='deferred_delete'],result)
                    self.assertTrue(any(c[1]=='timeout' for c in result['calls']),result)
                    self.assertTrue(any(c[1]=='parent' for c in result['calls']),result)
                    self.assertEqual(result['done'],int(scenario=='success'))
                    self.assertEqual(result['failed'],int(scenario in ('error','deadline','abort')))
        # No retry is introduced. Immediate abort can prevent sending at all.
        self.assertLessEqual(len(server.calls),12)

    def test_worker_shutdown_disconnects_python_senders_before_destruction(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);peer=HTTPPeer(root/'peer');self.addCleanup(peer.stop)
            audit=[]
            def note(name, signal):
                frame=sys._getframe();in_stop=False
                while frame:
                    in_stop |= frame.f_code is transport._Worker.stop.__code__
                    frame=frame.f_back
                audit.append((in_stop,name,bytes(signal.methodSignature()).decode()))
            class Timer(QTimer):
                def disconnectNotify(self, signal):note('timer',signal);super().disconnectNotify(signal)
            class Thread(QThread):
                def disconnectNotify(self, signal):note('thread',signal);super().disconnectNotify(signal)
            class Channel(transport.QtChannel):
                def disconnectNotify(self,signal):note('channel',signal);super().disconnectNotify(signal)
            identity=peer.fixture.identity
            with patch.object(transport,'QThread',Thread),patch.object(transport,'QTimer',Timer):
                channel=Channel(root/'pcs',peer.url,'test-token',identity)
                wait_until(lambda:channel.worker.core is not None)
                channel.stop();wait_until(lambda:not channel.running)
            self.assertTrue(audit)
            self.assertTrue(all(row[0] for row in audit),audit)
            self.assertEqual({row[1] for row in audit},{'channel','timer','thread'})
            self.assertFalse(isValid(channel.worker))
            channel.deleteLater();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete)


if __name__=='__main__':unittest.main()
