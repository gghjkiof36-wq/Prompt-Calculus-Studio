"""Submit frozen workflows directly to ComfyUI; interrupted submits never retry."""
import copy
import hashlib
import json
import time
import uuid
from urllib.parse import quote
from PySide6.QtCore import QTimer
from .generation import active_profile, options, submission, uploaded_image, validate_profile, image_target

ACTIVE=('submitting','queued','running','unconfirmed')


class GenerationRunner:
    def __init__(self,client):
        self.client=client; self.window=client.window; self.batch=None; self.checking=set(); self.message=''; self.pipeline=None; self.native_waiting=set()
        self.db=self.window.store.db
        self.db.execute('CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)'); self.db.commit()
        self.jobs={}
        for row in self.db.execute("SELECT body FROM generation_jobs WHERE json_extract(body,'$.state') IN ('submitting','queued','running','unconfirmed') ORDER BY created DESC LIMIT 100"):
            job=json.loads(row[0])
            if 'workspace' not in job:
                from .job_details import job_marker
                marker=job_marker(job);snapshot=marker.get('snapshot') or next((b.get('snapshot',{}) for b in marker.get('bindings',[]) if b.get('snapshot')), {})
                if snapshot.get('state',{}).get('workspace'):job['workspace']=snapshot['state']['workspace']
            if not job.get('queue_job'):self.jobs[job['id']]=job

    def save(self,job):
        if self.client.stopped:return
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO generation_jobs VALUES (?,?,?)',(job['id'],json.dumps(job,ensure_ascii=False),job['created']))
            self.db.execute("DELETE FROM generation_jobs WHERE id IN (SELECT id FROM generation_jobs WHERE json_extract(body,'$.queue_job') IS NULL AND json_extract(body,'$.chain') IS NULL AND json_extract(body,'$.state') NOT IN ('submitting','queued','running','unconfirmed') ORDER BY created DESC LIMIT -1 OFFSET 200)")

    def status(self,text):
        self.message=text
        if hasattr(self.window,'generation_panel'): self.window.generation_panel.status.setText(text); self.window.generation_panel.status.setVisible(bool(text))
        self.client.stateChanged.emit()

    def run(self,count):
        if self.window.state.get('multi_output',{}).get('version',1)>=5:
            return self.client.input_flow.execute(count)
        if hasattr(self.client,'queue') and self.client.queue.busy():raise ValueError('Queue 尚未結案，請先暫停並核對目前工作。')
        if self.batch or self.client.run_id: return
        if self.window.state.get('multi_output',{}).get('version',1)>=4:
            from .workflow_runner import WorkflowRun
            self.pipeline=WorkflowRun(self)
            try:self.pipeline.start(count)
            except Exception:
                self.finish_batch(); raise
            return
        profile=active_profile(self.window.state)
        if not profile: raise ValueError('請先匯入並選擇對應工作流。')
        validate_profile(profile)
        if type(count) is not int or not 1<=count<=100: raise ValueError('運行次數須介於 1–100。')
        source=copy.deepcopy(options(self.window.state).get('source'))
        profile=copy.deepcopy(profile); snapshot=self.client.snapshot(); content=None
        if 'multi_output' in snapshot['state']:
            from .multi_output import bound_texts
            bound_texts(snapshot['state'],profile)
            from .composition_image import freeze_source
            source=freeze_source(snapshot['state'],self.window.store.directory,optional=True)
            if source and not image_target(profile): raise ValueError('圖片無法載入工作流：請先綁定可接收圖片的 LoadImage 欄位。')
            snapshot['state']['generation']['source']=copy.deepcopy(source)
        upload_source=source is not None if 'multi_output' in snapshot['state'] else profile['mode']=='img2img'
        if upload_source:
            if not source: raise ValueError('請先選擇來源圖片。')
            path=(self.window.store.directory/source['relative']).resolve()
            if not path.is_relative_to(self.window.store.directory.resolve()) or not path.is_file(): raise ValueError('來源圖片不存在，請重新選圖。')
            if path.stat().st_size>25*1024*1024: raise ValueError('來源圖片超過 25 MB。')
            content=path.read_bytes()
            if hashlib.sha256(content).hexdigest()!=source['sha256']: raise ValueError('來源圖片已變更，請重新選圖。')
        # Freeze before the first asynchronous request. Later UI edits belong to a new run.
        token=uuid.uuid4().hex; self.client.run_id=token
        self.batch=dict(token=token,profile=profile,snapshot=snapshot,source=source,count=count,index=0,image='')
        self.status('正在準備來源圖片…' if content is not None else '正在提交工作流…')
        if content is None: self.submit_next(token)
        else:
            boundary='PromptStudio'+uuid.uuid4().hex
            filename='ps_'+source['sha256']+path.suffix.lower()
            fields={'type':'input','subfolder':'prompt_studio','overwrite':'false'}
            body=b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{value}\r\n').encode() for key,value in fields.items())
            body+=(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+content+f'\r\n--{boundary}--\r\n'.encode()
            def ready(result):
                if not self.batch or self.batch['token']!=token: return
                try: self.batch['image']=uploaded_image(result); self.submit_next(token)
                except (ValueError,KeyError) as exc: self.finish_batch(str(exc))
            def failed(error):
                if self.batch and self.batch['token']==token: self.finish_batch('來源圖片上傳失敗：'+error)
            self.client.request('/upload/image',done=ready,failed=failed,raw=body,content_type='multipart/form-data; boundary='+boundary)

    def submit_next(self,token):
        batch=self.batch
        if not batch or batch['token']!=token: return
        if batch['index']>=batch['count']:
            self.finish_batch(f"已提交 {batch['count']} 次生成；原來源圖片保留。"); self.client.poll(); return
        payload=submission(batch['profile'],batch['snapshot'],batch['source'],batch['image'],batch['index'])
        ident=payload['prompt_id']; job=dict(id=ident,state='submitting',created=time.time(),server=self.client.url,payload=payload,error='')
        self.jobs[ident]=job; self.save(job)
        self.status(f"正在提交 {batch['index']+1}／{batch['count']}…")
        def done(result):
            actual=result.get('prompt_id')
            if not isinstance(actual,str) or not actual: failed('後端沒有回傳任務編號。'); return
            job['prompt_id']=actual; job['state']='queued'; self.save(job)
            if self.batch and self.batch['token']==token:
                self.batch['index']+=1; self.submit_next(token)
        def failed(error):
            rejected=getattr(error,'rejected',False)
            job.update(state='failed' if rejected else 'unconfirmed',error=str(error)); self.save(job)
            if rejected: self.jobs.pop(job['id'],None)
            if self.batch and self.batch['token']==token:
                self.finish_batch(('後端拒絕生成：' if rejected else '提交未確認：')+error+'；已停止後續提交，不會自動重送。請查看任務紀錄。')
        self.client.request('/prompt',payload,done,failed)

    def submit_payload(self,payload,queued,failed):
        """The same durable job journal owns each sequential workflow submit."""
        ident=payload['prompt_id']; job=dict(id=ident,state='submitting',created=time.time(),server=self.client.url,payload=payload,error='')
        self.jobs[ident]=job; self.save(job)
        def done(result):
            actual=result.get('prompt_id')
            if not isinstance(actual,str) or not actual:fail('後端沒有回傳任務編號。');return
            job.update(prompt_id=actual,state='queued');self.save(job);queued(job)
        def fail(error):
            rejected=getattr(error,'rejected',False)
            job.update(state='failed' if rejected else 'unconfirmed',error=str(error));self.save(job)
            if rejected:self.jobs.pop(ident,None)
            failed(('後端拒絕生成：' if rejected else '提交未確認，不會自動重送：')+str(error))
        self.client.request('/prompt',payload,done,fail)

    def submit_native(self,snapshot,workflow,queued,failed,valid,image='',images=None,ident=None):
        """Journal an operation, then observe the actual native queue receipt."""
        ident=ident or uuid.uuid4().hex
        self.native_waiting.add(ident)
        job=dict(id=ident,native_operation=ident,state='submitting',created=time.time(),server=self.client.url,error='',
                 workspace=snapshot['state']['workspace'],requested_workflow=workflow,
                 entry_point=snapshot['state'].get('_entry_point','pcs'),
                 output_nodes=sorted({v['binding']['node'] for v in snapshot['state'].get('canvas_functions',{}).get('images',{}).values()
                    if (v.get('binding') or {}).get('workflow')==workflow and v['binding'].get('node') and
                    next((p for p in snapshot['state'].get('generation',{}).get('profiles',[]) if p['id']==workflow),{}).get('graph',{}).get(v['binding']['node'],{}).get('class_type') in ('SaveImage','PreviewImage')}),
                 typed_inputs=snapshot['state'].get('multi_output',{}).get('version',0)>=5)
        if snapshot.get('chain'):job['chain']=copy.deepcopy(snapshot['chain'])
        self.jobs[ident]=job;self.save(job)
        def fail(error):
            if self.client.stopped:return
            if job['state'] in ('failed','complete'):return
            self.native_waiting.discard(ident)
            job.setdefault('first_error',str(error))
            job.update(state='failed' if getattr(error,'rejected',False) else 'unconfirmed',error=str(error));self.save(job)
            if job['state']=='failed':self.jobs.pop(ident,None)
            if valid():failed('原生提交未完成：'+str(error)+'；不會自動重送。')
        def received(result):
            if self.client.stopped:return
            if job['state'] in ('failed','complete'):return
            for key in ('diagnostics','first_error','terminal_action','issued_at'):
                if key in result:job[key]=copy.deepcopy(result[key])
            if result.get('payload'):job['payload']=result['payload']
            if result.get('prompt_id'):job['prompt_id']=result['prompt_id']
            state=result.get('state')
            if state=='queued' and job.get('prompt_id'):
                self.native_waiting.discard(ident)
                job.update(state='queued',error=result.get('error',''));self.save(job)
                if valid():queued(job)
            elif state in ('failed','unconfirmed'):
                self.native_waiting.discard(ident)
                job.update(state=state,error=result.get('error',''));self.save(job)
                if state=='failed':self.jobs.pop(ident,None)
                if valid():failed(job['error'])
            elif time.time()-job['created']>35:
                fail('原生提交逾時；請查看任務紀錄。')
            else:
                self.save(job)
                # Keep reconciling the receipt even if a user cancelled the
                # batch; a sent operation must not vanish from the journal.
                QTimer.singleShot(500,self.client,lambda:self.client.request('workflow/native/status',dict(id=ident),received,fail) if not self.client.stopped else None)
        request=dict(id=ident,snapshot=snapshot,workflow=workflow,image=image)
        if images is not None:request['images']=images
        self.client.request('workflow/native/start',request,received,fail)
        return ident

    def finish_batch(self,message=''):
        if self.batch: self.client.run_id=''
        self.batch=None
        self.pipeline=None
        if message: self.status(message); self.window.notice(message)

    def disconnected(self):
        self.checking.clear();self.native_waiting.clear()
        if self.batch: self.finish_batch('連線中斷，已停止後續提交；已送出的任務須重新連線確認，不會自動重送。')

    def observe(self,status):
        if self.pipeline:self.pipeline.observe(status)
        running=set(status.get('running_ids',[])); queued=set(status.get('queued_ids',[]))
        for job in list(self.jobs.values()):
            if job['server']!=self.client.url or job['state'] not in ACTIVE: continue
            if job.get('native_operation') and not job.get('prompt_id'):
                operation=job['native_operation']
                if operation not in self.native_waiting and operation not in self.checking:
                    self.checking.add(operation)
                    def reconciled(result,job=job,operation=operation):
                        self.checking.discard(operation)
                        if job['state'] in ('failed','complete'):return
                        for key in ('payload','prompt_id','error','diagnostics','first_error','terminal_action','issued_at'):
                            if key in result:job[key]=result[key]
                        if result.get('state') in ('queued','failed','unconfirmed'):job['state']=result['state']
                        self.save(job)
                        if job['state']=='failed':self.jobs.pop(job['id'],None)
                    self.client.request('workflow/native/status',dict(id=operation),reconciled,
                                        lambda error,operation=operation:self.checking.discard(operation))
                continue
            ident=job.get('prompt_id',job['id'])
            state='running' if ident in running else 'queued' if ident in queued else None
            if state:
                now=time.time(); changed=job['state']!=state
                job['state']=state
                if state=='running':
                    job.setdefault('started',now)
                    node=status.get('executing_node')
                    if node is not None and node!=job.get('node'):
                        job.update(node=str(node),node_started=now); changed=True
                if changed or now-job.get('last_seen',0)>=5:
                    job['last_seen']=now; self.save(job)
                continue
            if ident in self.checking or len(self.checking)>=4: continue
            self.checking.add(ident)
            def done(history,job=job,ident=ident):
                self.checking.discard(ident); entry=history.get(ident)
                if job['state'] in ('failed','complete'):return
                if not entry:
                    if time.time()-job['created']>30 and job['state']!='unconfirmed':
                        job.update(state='unconfirmed',error='佇列與後端紀錄中找不到這項任務；可能已移除或清空，未自動重送。'); self.save(job)
                        if self.pipeline and self.pipeline.waiting==ident:self.pipeline.fail(job['error'])
                        flow=getattr(self.client,'input_flow',None)
                        if flow and flow.current and flow.current['operation']==job['id']:flow.fail(job['error'])
                        if not self.batch: self.status(job['error'])
                    return
                status=entry.get('status',{}); failed=status.get('status_str')=='error' or any(isinstance(m,list) and m and m[0] in ('execution_error','execution_interrupted') for m in status.get('messages',[]))
                if not status.get('completed') and not failed: return
                flow=getattr(self.client,'input_flow',None)
                reason=''
                if job.get('typed_inputs') and not failed:
                    from .queued_work import result_state
                    graph=job.get('payload',{}).get('prompt',{})
                    original=entry.get('prompt',[])
                    executed=original[4] if len(original)>4 and isinstance(original[4],list) else list(graph)
                    targets=sorted(set(job.get('output_nodes',[]))|{str(k) for k in executed if graph.get(str(k),{}).get('class_type') in ('SaveImage','PreviewImage')})
                    checked,reason=result_state(dict(graph=graph,outputs=targets,input_types=job.get('payload',{}).get('input_types')),ident,entry)
                    if checked!='complete':
                        if checked=='unconfirmed':
                            job.update(state=checked,error=reason);self.save(job)
                            if flow and flow.current and flow.current['operation']==job['id']:flow.fail(reason)
                            return
                        if checked!='failed':return
                        failed=True
                    job['input_check']='mismatch' if failed else 'matched'
                errors=[str(message[1].get('exception_message',message[1].get('node_type','生成失敗'))) for message in status.get('messages',[]) if isinstance(message,list) and len(message)>1 and message[0] in ('execution_error','execution_interrupted') and isinstance(message[1],dict)]
                count=sum(len(output.get('images',[])) for output in entry.get('outputs',{}).values() if isinstance(output,dict))
                job.update(state='failed' if failed else 'complete',execution_state='failed' if status.get('status_str')=='error' else 'complete',
                           error=reason or '；'.join(errors),output_count=count,finished=time.time(),
                           outputs=copy.deepcopy(entry.get('outputs',{})),events=copy.deepcopy(status.get('messages',[])))
                self.save(job)
                self.jobs.pop(job['id'],None)
                self.native_waiting.discard(job['id'])
                handled=self.pipeline.finished(job,entry) if self.pipeline else False
                if getattr(self.client,'input_flow',None):handled=self.client.input_flow.finished(job,entry) or handled
                if not self.batch and not handled:
                    self.status('生成失敗：'+(job['error'] or '請查看 ComfyUI 紀錄。') if failed else
                                '生成完成' if count else
                                '工作流已完成，未回傳新圖片；可能沿用快取，或工作流沒有圖片輸出。')
            self.client.request('/history/'+quote(ident,safe=''),done=done,failed=lambda error,i=ident:self.checking.discard(i))
        active=[job for job in self.jobs.values() if job['server']==self.client.url]
        if not self.batch and active:
            n=sum(job.get('prompt_id',job['id']) in running for job in active)
            q=sum(job.get('prompt_id',job['id']) in queued for job in active)
            if n or q: self.status(f'生成中 {n} 項 · 等待中 {q} 項')

    def records(self):
        return [json.loads(row[0]) for row in self.db.execute('SELECT body FROM generation_jobs ORDER BY created DESC LIMIT 100')]

    def recheck(self,ident):
        job=self.jobs.get(ident) or self.record(ident)
        if not job or job['server']!=self.client.url:
            self.window.notice('請連線至這項工作的原 ComfyUI 服務。');return
        if job['state'] not in ACTIVE:
            self.window.notice('這項工作已結案：'+job['state']);return
        self.jobs[ident]=job
        self.status('正在重新核對這項工作的原生紀錄…')
        if not job.get('native_operation'):
            self.client.poll();return
        def done(result):
            if job['state'] in ('failed','complete'):return
            proof=result.get('recovery',{})
            if not proof:
                failed('擴充未回傳恢復核對結果，請換用同版擴充並重啟 ComfyUI。');return
            job['recovery']=proof
            for key in ('payload','prompt_id','diagnostics','first_error','terminal_action','issued_at'):
                if result.get(key):job[key]=result[key]
            if result.get('state')=='failed':
                job.update(state='failed',error=result.get('error','原生要求已結案。'))
                self.jobs.pop(ident,None)
            elif proof.get('location')=='missing':job['state']='unconfirmed'
            self.save(job)
            if job['state']=='failed':self.client.input_flow.finished(job,{})
            self.client.input_flow.observe(self.client.input_flow.last_status)
            self.status(proof['reason']);self.window.notice(proof['reason']);self.client.poll()
        def failed(error):
            if job['state'] in ('failed','complete'):return
            job['recovery']=dict(location='unavailable',can_abandon=False,reason='核對失敗：'+str(error),checked=time.time())
            self.save(job);self.status(job['recovery']['reason']);self.window.notice(job['recovery']['reason'])
        self.client.request('workflow/native/recovery',dict(id=job['native_operation']),done=done,failed=failed)

    def retired(self,ident,result):
        # Mutate the same object held by in-flight callbacks before removing it;
        # a delayed history/native reply must never resurrect an abandoned job.
        job=self.jobs.get(ident) or self.record(ident)
        if not job or job['state'] not in ACTIVE:return
        if job.get('error'):job.setdefault('first_error',job['error'])
        job.update(state='failed',execution_state='unknown',input_check='unconfirmed',
                   terminal_action='abandon',
                   recovery=result.get('recovery',{}),
                   error='已解除這筆舊任務的追蹤；保留紀錄，沒有重送。',finished=time.time())
        self.save(job);self.jobs.pop(ident,None);self.native_waiting.discard(ident)
        flow=self.client.input_flow
        flow.finished(job,{})
        flow.observe(flow.last_status)
        for item in flow.store.rows():
            if item.get('operation')==ident:
                flow.store.update(item['id'],state='cancelled',error=job['error'])
                flow.store.set_control(item['owner'],paused=True)
        flow.notify('已停止追蹤這筆舊工作，原紀錄保留；沒有重新提交。')

    def abandon(self,ident):
        job=self.record(ident)
        if not job or job['server']!=self.client.url or not job.get('native_operation'):
            self.window.notice('無法核對這項工作的原生歸屬。');return
        if job['state'] not in ACTIVE:
            self.window.notice('這項工作已結案，不需要解除追蹤。');return
        def done(result):
            if result.get('state')!='abandoned':
                self.window.notice(result.get('reason','工作仍待核對，尚未略過。'));self.recheck(ident);return
            self.retired(ident,result)
        self.client.request('workflow/native/abandon',dict(id=job['native_operation']),done=done,failed=self.window.notice)

    def record(self,ident):
        row=self.db.execute('SELECT body FROM generation_jobs WHERE id=?',(ident,)).fetchone()
        return json.loads(row[0]) if row else None
