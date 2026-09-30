"""Asynchronous localhost transport. Never retries a generation automatically."""
import json
import time
import uuid
from PySide6.QtCore import QObject, Signal, QTimer, QUrl, QEventLoop
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest, QNetworkReply
from .snapshots import make_snapshot
from .core import local_address
from .generation import direct_mode, active_profile, options
from .generation_runner import GenerationRunner

class RequestFailure(str):
    def __new__(cls,message,rejected=False,status_code=None):
        value=super().__new__(cls,message); value.rejected=rejected; value.status_code=status_code; return value


class ComfyClient(QObject):
    stateChanged=Signal()
    resultsReceived=Signal(list)

    def __init__(self,window):
        super().__init__(window); self.window=window; self.network=QNetworkAccessManager(self)
        self.url=window.state['settings'].get('comfy_url','http://127.0.0.1:8188')
        self.enabled=window.state['settings'].get('comfy_enabled',False)
        self.connected=False; self.ready=False; self.lease=''; self.target=''; self.token=''
        self.native_supported=False
        self.native_inspect_supported=False
        self.native_open_supported=False;self.opening_native=False;self.native_unavailable_reason=''
        self.message='尚未連接 ComfyUI'; self.polling=False; self.stopped=False; self.replies=set()
        self.run_id=''; self.run_started=0; self.checking_run=False; self.last_signature=''; self.last_results=0
        self.running=0; self.pending=0; self.epoch=0
        self.had_control=False; self.control_interrupted=False
        self.direct_supported=False; self.snapshot_versions=None; self.generation=GenerationRunner(self)
        from .queue_runner import QueueRunner
        self.queue_supported=False; self.queue=QueueRunner(self)
        from .input_runner import InputRunner
        self.input_flow=InputRunner(self)
        self.flow_supported=None; self.fetching_workflow=False; self.register_library=True
        from .image_bindings import ImageBindings
        self.images=ImageBindings(self); self.images_supported=False
        self.window.store.db.execute('CREATE TABLE IF NOT EXISTS workflow_receipts (id TEXT PRIMARY KEY, error TEXT NOT NULL)'); self.window.store.db.commit()
        self.timer=QTimer(self); self.timer.setInterval(1500); self.timer.timeout.connect(self.poll)
        self.sync_timer=QTimer(self); self.sync_timer.setSingleShot(True); self.sync_timer.setInterval(350); self.sync_timer.timeout.connect(self.sync)
        if self.enabled: self.timer.start(); QTimer.singleShot(0,self.poll)

    @property
    def can_run(self):
        if self.window.state.get('multi_output',{}).get('version',1)>=7:
            return bool(self.connected and self.native_supported and self.snapshot_compatible and self.flow_supported)
        if self.window.state.get('multi_output',{}).get('version',1)>=5:
            if not(self.connected and self.native_supported and self.snapshot_compatible and self.flow_supported):return False
            try:
                from .chain_model import enabled
                if enabled(self.window.state):return True
                from .workflow_flow import execution_profiles
                return len(execution_profiles(self.window.state))==1
            except ValueError:return False
        if hasattr(self,'queue') and self.queue.busy():return False
        if not self.snapshot_compatible: return False
        if self.window.state.get('multi_output',{}).get('version',1)>=2:
            from .multi_output import connected_outputs
            if not connected_outputs(self.window.state): return False
        if direct_mode(self.window.state):
            if 'multi_output' in self.window.state:
                if self.window.state['multi_output']['version']>=4:
                    from .workflow_flow import execution_profiles
                    from .multi_output import bound_texts
                    if not (self.connected and self.native_supported):return False
                    try:return all(bound_texts(self.window.state,p) for p in execution_profiles(self.window.state))
                    except ValueError:return False
                profile=active_profile(self.window.state)
                if not (self.connected and self.direct_supported and profile is not None): return False
                from .multi_output import bound_texts
                try: return bool(bound_texts(self.window.state,profile))
                except ValueError: return False
            return self.connected and self.direct_supported and active_profile(self.window.state) is not None and (options(self.window.state)['mode']!='img2img' or bool(options(self.window.state).get('source')))
        if self.window.state.get('multi_output',{}).get('version',1)>=2:
            data=self.window.state['multi_output']; output=data['current_output']
            if output not in connected_outputs(self.window.state) or not data['outputs'][output]['canvas']: return False
        return self.ready

    @property
    def snapshot_compatible(self):
        return (self.snapshot_versions is None or self.window.state['version'] in self.snapshot_versions) and not (self.window.state.get('multi_output',{}).get('version',1)>=2 and self.flow_supported is False)

    def request(self,route,data=None,done=None,failed=None,raw=None,content_type='application/json'):
        if self.stopped: return
        epoch=self.epoch
        request=QNetworkRequest(QUrl(self.url+(route if route.startswith('/') else '/prompt_studio/'+route))); request.setTransferTimeout(60000 if raw is not None else 12000)
        request.setRawHeader(b'X-Prompt-Studio',self.token.encode()); request.setHeader(QNetworkRequest.KnownHeaders.ContentTypeHeader,content_type)
        reply=self.network.post(request,raw) if raw is not None else self.network.get(request) if data is None else self.network.post(request,json.dumps(data,ensure_ascii=False).encode())
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
                    error=result.get('error') or 'ComfyUI 未連線或擴充尚未更新，請確認已啟動。'
                    if isinstance(error,dict): error=error.get('message',str(error))+' '+str(error.get('details',''))
                    details=result.get('node_errors')
                    if details: error+=' '+json.dumps(details,ensure_ascii=False)[:1800]
                    raise ValueError(error)
                if done: done(result)
            except Exception as exc:
                status=reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
                if failed: failed(RequestFailure(str(exc),isinstance(status,int) and 400<=status<500,status))
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
        self.native_supported=False;self.native_open_supported=False;self.opening_native=False;self.native_unavailable_reason=''
        self.images.reset(); self.images_supported=False
        self.generation.disconnected(); self.direct_supported=False; self.snapshot_versions=None
        self.input_flow.disconnected()
        self.queue_supported=False;self.queue.disconnected()
        self.flow_supported=None; self.fetching_workflow=False; self.register_library=True
        self.had_control=False; self.control_interrupted=False
        self.sync_timer.stop()
        self.flush_input_cancellations()
        for reply in list(self.replies): reply.abort()
        self.polling=False; self.checking_run=False; self.connected=False; self.ready=False
        self.lease=''; self.target=''; self.last_signature=''; self.last_results=0
        self.run_id=''; self.running=0; self.pending=0
        if hasattr(self.window,'recent'):
            self.window.recent.collecting.clear(); self.window.recent.update_save_button()

    def poll(self):
        if self.stopped or not self.enabled or self.polling: return
        self.polling=True
        def fail(message):
            self.polling=False; self.connected=False; self.ready=False; self.token=''; self.last_signature=''
            self.control_interrupted=self.had_control
            self.message=message; self.stateChanged.emit()
            self.generation.disconnected()
            self.input_flow.disconnected()
            self.queue.disconnected()
            if self.run_id:
                self.window.notice('生成提交的連線中斷，結果尚未確認；請查看 ComfyUI 紀錄，不會自動重送。'); self.run_id=''
        def received(status):
            self.polling=False
            if not self.enabled: return
            old=self.lease; self.connected=True; self.ready=bool(status.get('ready')); self.lease=status.get('lease',''); self.target=status.get('target','')
            self.had_control=self.had_control or self.ready
            self.control_interrupted=self.had_control and not self.ready
            self.running=status.get('running',0); self.pending=status.get('pending',0)
            self.direct_supported='direct_generation_v1' in status.get('capabilities',[])
            self.queue_supported='frozen_queue_v1' in status.get('capabilities',[])
            self.native_supported=all(c in status.get('capabilities',[]) for c in ('native_queue_v1','native_bindings_v1'))
            self.native_open_supported='native_open_v1' in status.get('capabilities',[])
            self.native_inspect_supported='native_inspect_v1' in status.get('capabilities',[])
            self.native_unavailable_reason=status.get('native_unavailable_reason','')
            self.images_supported='workflow_images_v1' in status.get('capabilities',[])
            self.input_bridge_supported='input_run_bridge_v1' in status.get('capabilities',[])
            version=self.window.state.get('multi_output',{}).get('version',1)
            required='stage_execution_v1' if version>=7 else 'typed_inputs_v2' if version>=6 else 'typed_inputs_v1' if version>=5 else 'workflow_images_v1' if version>=4 else 'clip_inputs_v3' if version>=3 else 'flow_connections_v2'
            self.flow_supported=required in status.get('capabilities',[])
            if self.register_library and self.flow_supported and self.window.state.get('multi_output',{}).get('version',1)>=2:
                self.register_library=False; self.window.persist()
                self.request('config',dict(library=str(self.window.store.directory/'studio.sqlite3')),failed=self.window.notice)
            self.snapshot_versions=status.get('snapshot_versions',[1,2,3] if self.direct_supported else [1])
            self.message=('已連線 · '+self.target) if self.ready else status.get('reason','請在 ComfyUI 啟用桌面版控制。')
            if direct_mode(self.window.state):
                self.message='已連線 · 工作流直接生成' if self.direct_supported else '請更新 ComfyUI 擴充以啟用工作流直接生成。'
                if version>=4:
                    self.message='已連線 · 請保持已綁定的 ComfyUI 工作流網頁開啟' if self.native_supported else '請更新配套 ComfyUI 擴充並重新整理網頁，以啟用文字與圖片綁定。'
                    if self.queue_supported and version<5:self.message='已連線 · 加入新工作需開啟網頁；已保存佇列可關頁執行'
                self.control_interrupted=False
            if version>=5 and not self.flow_supported:self.message='請安裝本次配套 ComfyUI 擴充並重新整理網頁，以啟用圖片來源與預排程。'
            if not self.snapshot_compatible:
                self.message='ComfyUI 仍載入舊快照版本；請重啟 ComfyUI 後端以載入已更新的擴充。'
                self.control_interrupted=True; self.sync_timer.stop()
            self.stateChanged.emit()
            self.generation.observe(status)
            self.input_flow.observe(status)
            self.queue.observe()
            self.images.poll()
            if 'workflow_transfer_v1' in status.get('capabilities',[]) and 'multi_output' in self.window.state: self.receive_workflow()
            if self.ready and old!=self.lease and not direct_mode(self.window.state): self.last_signature=''; self.sync_timer.start()
            if self.run_id and not self.generation.batch: self.check_run()
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

    def receive_workflow(self):
        if self.fetching_workflow: return
        self.fetching_workflow=True
        library=str(uuid.uuid5(uuid.NAMESPACE_URL,str((self.window.store.directory/'studio.sqlite3').resolve()).casefold()))
        def received(data):
            self.fetching_workflow=False
            if not data: return
            ident=data['id']; db=self.window.store.db; prior=db.execute('SELECT error FROM workflow_receipts WHERE id=?',(ident,)).fetchone(); error=prior[0] if prior else ''
            if prior is None:
                try:
                    from .workflow_transfer import apply_transfer
                    state,profile=apply_transfer(self.window.state,data['value'])
                    self.window.store.backup(); self.window.store.save(state)
                    self.window.state=state; self.window.canvas.last_state=None
                    self.window.generation_panel.changed(); self.window.canvas.refresh()
                    self.window.notice('已自動匯入工作流：'+profile['name'])
                    settings=getattr(self.window,'settings_page',None)
                    if settings and hasattr(settings,'workflow_manager'): settings.workflow_manager.refresh()
                except (ValueError,KeyError,TypeError,OSError) as exc:
                    error=str(exc); self.window.notice('工作流未匯入：'+error)
                with db: db.execute('INSERT OR REPLACE INTO workflow_receipts VALUES (?,?)',(ident,error))
            self.request('desktop/workflows/ack',dict(id=ident,library=library,error=error))
        def failed(message): self.fetching_workflow=False; self.window.notice(message)
        self.request('desktop/workflows',dict(library=library),done=received,failed=failed)

    def schedule_sync(self):
        if self.ready and self.snapshot_compatible and not direct_mode(self.window.state): self.sync_timer.start()

    def sync(self):
        if not self.ready or not self.enabled or self.stopped or direct_mode(self.window.state) or not self.snapshot_compatible or not self.can_run: return
        try:
            snapshot=self.snapshot(); signature=json.dumps(snapshot,ensure_ascii=False,sort_keys=True)
            if signature==self.last_signature: return
            self.last_signature=signature
            def fail(message): self.last_signature=''; self.window.notice('同步未完成：'+message)
            self.request('desktop/command',dict(lease=self.lease,id=uuid.uuid4().hex,kind='sync',snapshot=snapshot),failed=fail)
        except Exception as exc: self.window.notice(str(exc))

    def run(self,count=1):
        if not self.snapshot_compatible:
            self.window.notice('請先重啟 ComfyUI 後端，載入支援 Canvas 快照的擴充。'); return
        if direct_mode(self.window.state):
            if not self.can_run or (self.run_id and self.window.state.get('multi_output',{}).get('version',1)<5): return
            self.sync_timer.stop()
            try: self.generation.run(count)
            except (ValueError,OSError) as exc: self.window.notice(str(exc))
            return
        if not self.can_run or self.run_id: return
        self.sync_timer.stop(); ident=uuid.uuid4().hex; snapshot=self.snapshot()
        self.run_id=ident; self.run_started=time.monotonic(); self.stateChanged.emit()
        def fail(message):
            self.run_id=''; self.stateChanged.emit()
            self.window.notice('生成提交未確認：'+message+' 不會自動重送，請先查看 ComfyUI 生成紀錄。')
        self.request('desktop/command',dict(lease=self.lease,id=ident,kind='run',count=count,snapshot=snapshot),done=lambda _:self.check_run(),failed=fail)

    def interrupt(self,clear_pending=False):
        if not self.connected: return
        if self.window.state.get('multi_output',{}).get('version',1)>=5:
            self.input_flow.cancel();return
        if self.queue.busy():
            self.queue.pause();self.window.notice('已停止 Queue 後續派送；已提交工作保留，未中斷其他來源任務。');return
        self.generation.finish_batch('已停止後續生成提交。' if self.generation.batch else '')
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

    def flush_input_cancellations(self,timeout_ms=250):
        """Bounded best effort before aborting transports; absent ACK stays unknown."""
        pending=getattr(self.input_flow.chain,'apply_cancellations',set())
        if not pending:return
        loop=QEventLoop();poll=QTimer(loop);deadline=QTimer(loop);deadline.setSingleShot(True)
        poll.timeout.connect(lambda:loop.quit() if not pending else None)
        deadline.timeout.connect(loop.quit);poll.start(10);deadline.start(timeout_ms)
        try:loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        finally:poll.stop();deadline.stop()

    def shutdown(self):
        self.timer.stop();self.sync_timer.stop()
        self.input_flow.disconnected()
        self.queue.disconnected()
        self.generation.disconnected()
        self.flush_input_cancellations()
        if hasattr(self.input_flow.chain,'close'):self.input_flow.chain.close()
        self.stopped=True; self.timer.stop(); self.sync_timer.stop()
        for reply in list(self.replies): reply.abort()
