"""Asynchronous localhost transport. Never retries a generation automatically."""
import json
import time
import uuid
from urllib.parse import urlsplit
from PySide6.QtCore import QObject, Signal, QTimer, QUrl
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from .snapshots import make_snapshot


def local_address(value):
    parts=urlsplit(value.strip())
    if parts.scheme not in ('http','https') or parts.hostname not in ('localhost','127.0.0.1','::1') or parts.username or parts.password or parts.path not in ('','/') or parts.query or parts.fragment:
        raise ValueError('請填本機 ComfyUI 網址，例如 http://127.0.0.1:8188。')
    return value.strip().rstrip('/')


class ComfyClient(QObject):
    stateChanged=Signal()
    resultsReceived=Signal(list)

    def __init__(self,window):
        super().__init__(window); self.window=window; self.network=QNetworkAccessManager(self)
        self.url=window.state['settings'].get('comfy_url','http://127.0.0.1:8188')
        self.enabled=window.state['settings'].get('comfy_enabled',False)
        self.connected=False; self.ready=False; self.lease=''; self.target=''; self.token=''
        self.message='尚未連接 ComfyUI'; self.polling=False; self.stopped=False; self.replies=set()
        self.run_id=''; self.run_started=0; self.checking_run=False; self.last_signature=''; self.last_results=0
        self.running=0; self.pending=0; self.epoch=0
        self.timer=QTimer(self); self.timer.setInterval(1500); self.timer.timeout.connect(self.poll)
        self.sync_timer=QTimer(self); self.sync_timer.setSingleShot(True); self.sync_timer.setInterval(350); self.sync_timer.timeout.connect(self.sync)
        if self.enabled: self.timer.start(); QTimer.singleShot(0,self.poll)

    def request(self,route,data=None,done=None,failed=None):
        if self.stopped: return
        epoch=self.epoch
        request=QNetworkRequest(QUrl(self.url+'/prompt_studio/'+route)); request.setTransferTimeout(12000)
        request.setRawHeader(b'X-Prompt-Studio',self.token.encode()); request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,'application/json')
        reply=self.network.get(request) if data is None else self.network.post(request,json.dumps(data,ensure_ascii=False).encode())
        self.replies.add(reply); chunks=bytearray()
        def read():
            chunks.extend(bytes(reply.readAll()))
            if len(chunks)>4*1024*1024: reply.abort()
        def finish():
            read(); self.replies.discard(reply)
            if self.stopped or epoch!=self.epoch: reply.deleteLater(); return
            try:
                if len(chunks)>4*1024*1024: raise ValueError('ComfyUI 回應過大。')
                try: result=json.loads(chunks)
                except (ValueError,UnicodeError): result={}
                if reply.error()!=QNetworkReply.NetworkError.NoError:
                    raise ValueError(result.get('error') or 'ComfyUI 未連線或擴充尚未更新，請確認已啟動並重新整理網頁。')
                if done: done(result)
            except Exception as exc:
                if failed: failed(str(exc))
                else: self.window.notice(str(exc))
            finally: reply.deleteLater()
        reply.readyRead.connect(read); reply.finished.connect(finish)

    def connect_to(self,address):
        address=local_address(address); self.reset_connection()
        self.url=address; self.enabled=True; self.token=''; self.ready=False
        self.window.state['settings'].update(comfy_url=self.url,comfy_enabled=True); self.window.changed()
        self.timer.start(); self.poll()

    def disconnect(self):
        self.reset_connection()
        self.enabled=False; self.ready=False; self.connected=False; self.token=''; self.lease=''
        self.timer.stop(); self.sync_timer.stop(); self.message='已中斷桌面連線；可繼續複製 Prompt。'
        self.run_id=''
        self.window.state['settings']['comfy_enabled']=False; self.window.changed(); self.stateChanged.emit()

    def reset_connection(self):
        self.epoch+=1
        self.sync_timer.stop()
        for reply in list(self.replies): reply.abort()
        self.polling=False; self.checking_run=False; self.connected=False; self.ready=False
        self.lease=''; self.target=''; self.last_signature=''; self.last_results=0
        self.run_id=''; self.running=0; self.pending=0

    def poll(self):
        if self.stopped or not self.enabled or self.polling: return
        self.polling=True
        def fail(message):
            self.polling=False; self.connected=False; self.ready=False; self.token=''; self.last_signature=''
            self.message=message; self.stateChanged.emit()
            if self.run_id:
                self.window.notice('生成提交的連線中斷，結果尚未確認；請查看 ComfyUI 紀錄，不會自動重送。'); self.run_id=''
        def received(status):
            self.polling=False
            if not self.enabled: return
            old=self.lease; self.connected=True; self.ready=bool(status.get('ready')); self.lease=status.get('lease',''); self.target=status.get('target','')
            self.running=status.get('running',0); self.pending=status.get('pending',0)
            self.message=('已連線 · '+self.target) if self.ready else status.get('reason','請在 ComfyUI 啟用桌面版控制。')
            self.stateChanged.emit()
            if self.ready and old!=self.lease: self.last_signature=''; self.sync_timer.start()
            if self.run_id: self.check_run()
            if time.monotonic()-self.last_results>3:
                self.last_results=time.monotonic(); self.request('desktop/results',done=self.resultsReceived.emit,failed=self.window.notice)
        def status(_=None): self.request('desktop/status',done=received,failed=fail)
        if not self.token:
            def configured(data): self.token=data['token']; status()
            self.request('config',done=configured,failed=fail)
        else: status()

    def snapshot(self):
        library=str(uuid.uuid5(uuid.NAMESPACE_URL,str((self.window.store.directory/'studio.sqlite3').resolve()).casefold()))
        return make_snapshot(self.window.state,library,'desktop-live')

    def schedule_sync(self):
        if self.ready: self.sync_timer.start()

    def sync(self):
        if not self.ready or not self.enabled or self.stopped: return
        try:
            snapshot=self.snapshot(); signature=json.dumps(snapshot,ensure_ascii=False,sort_keys=True)
            if signature==self.last_signature: return
            self.last_signature=signature
            def fail(message): self.last_signature=''; self.window.notice('同步未完成：'+message)
            self.request('desktop/command',dict(lease=self.lease,id=uuid.uuid4().hex,kind='sync',snapshot=snapshot),failed=fail)
        except Exception as exc: self.window.notice(str(exc))

    def run(self,count=1):
        if not self.ready or self.run_id: return
        self.sync_timer.stop(); ident=uuid.uuid4().hex; snapshot=self.snapshot()
        self.run_id=ident; self.run_started=time.monotonic(); self.stateChanged.emit()
        def fail(message):
            self.run_id=''; self.stateChanged.emit()
            self.window.notice('生成提交未確認：'+message+' 不會自動重送，請先查看 ComfyUI 生成紀錄。')
        self.request('desktop/command',dict(lease=self.lease,id=ident,kind='run',count=count,snapshot=snapshot),done=lambda _:self.check_run(),failed=fail)

    def interrupt(self,clear_pending=False):
        if not self.connected: return
        def done(_): self.window.notice('已要求停止目前生成'+('，並清空待執行佇列。' if clear_pending else '；其他待執行任務保留。')); self.poll()
        self.request('desktop/interrupt',{'clear_pending':clear_pending},done=done)

    def check_run(self):
        if not self.run_id or self.checking_run: return
        self.checking_run=True; ident=self.run_id
        def done(result):
            self.checking_run=False
            if self.run_id!=ident: return
            if result['state']=='done':
                self.run_id=''; self.window.notice('已提交生成；圖片完成後會出現在「最近生成」。'); self.stateChanged.emit()
            elif result['state']=='error':
                self.run_id=''; self.window.notice('未完成生成提交：'+result.get('error','')); self.stateChanged.emit()
            elif time.monotonic()-self.run_started>30:
                self.run_id=''; self.window.notice('ComfyUI 尚未確認提交，請查看生成紀錄；不會自動重送。'); self.stateChanged.emit()
        def fail(message): self.checking_run=False; self.window.notice(message)
        self.request('desktop/command/'+ident,done=done,failed=fail)

    def shutdown(self):
        self.stopped=True; self.timer.stop(); self.sync_timer.stop()
        for reply in list(self.replies): reply.abort()
