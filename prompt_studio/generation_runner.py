"""Submit frozen workflows directly to ComfyUI; interrupted submits never retry."""
import copy
import hashlib
import json
import time
import uuid
from urllib.parse import quote
from .generation import active_profile, options, submission, uploaded_image, validate_profile, image_target

ACTIVE=('submitting','queued','running','unconfirmed')


class GenerationRunner:
    def __init__(self,client):
        self.client=client; self.window=client.window; self.batch=None; self.checking=set(); self.message=''
        self.db=self.window.store.db
        self.db.execute('CREATE TABLE IF NOT EXISTS generation_jobs (id TEXT PRIMARY KEY, body TEXT NOT NULL, created REAL NOT NULL)'); self.db.commit()
        self.jobs={}
        for row in self.db.execute("SELECT body FROM generation_jobs WHERE json_extract(body,'$.state') IN ('submitting','queued','running','unconfirmed') ORDER BY created DESC LIMIT 100"):
            job=json.loads(row[0]); self.jobs[job['id']]=job

    def save(self,job):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO generation_jobs VALUES (?,?,?)',(job['id'],json.dumps(job,ensure_ascii=False),job['created']))
            self.db.execute("DELETE FROM generation_jobs WHERE id IN (SELECT id FROM generation_jobs WHERE json_extract(body,'$.state') NOT IN ('submitting','queued','running','unconfirmed') ORDER BY created DESC LIMIT -1 OFFSET 200)")

    def status(self,text):
        self.message=text
        if hasattr(self.window,'generation_panel'): self.window.generation_panel.status.setText(text); self.window.generation_panel.status.setVisible(bool(text))
        self.client.stateChanged.emit()

    def run(self,count):
        if self.batch or self.client.run_id: return
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

    def finish_batch(self,message=''):
        if self.batch: self.client.run_id=''
        self.batch=None
        if message: self.status(message); self.window.notice(message)

    def disconnected(self):
        self.checking.clear()
        if self.batch: self.finish_batch('連線中斷，已停止後續提交；已送出的任務須重新連線確認，不會自動重送。')

    def observe(self,status):
        running=set(status.get('running_ids',[])); queued=set(status.get('queued_ids',[]))
        for job in list(self.jobs.values()):
            if job['server']!=self.client.url or job['state'] not in ACTIVE: continue
            ident=job.get('prompt_id',job['id'])
            state='running' if ident in running else 'queued' if ident in queued else None
            if state:
                if job['state']!=state: job['state']=state; self.save(job)
                continue
            if ident in self.checking or len(self.checking)>=4: continue
            self.checking.add(ident)
            def done(history,job=job,ident=ident):
                self.checking.discard(ident); entry=history.get(ident)
                if not entry:
                    if time.time()-job['created']>30 and job['state']!='unconfirmed':
                        job.update(state='unconfirmed',error='佇列與後端紀錄中找不到這項任務；可能已移除或清空，未自動重送。'); self.save(job)
                        if not self.batch: self.status(job['error'])
                    return
                status=entry.get('status',{}); failed=status.get('status_str')=='error'
                if not status.get('completed') and not failed: return
                errors=[str(message[1].get('exception_message',message[1].get('node_type','生成失敗'))) for message in status.get('messages',[]) if isinstance(message,list) and len(message)>1 and message[0] in ('execution_error','execution_interrupted') and isinstance(message[1],dict)]
                count=sum(len(output.get('images',[])) for output in entry.get('outputs',{}).values() if isinstance(output,dict))
                job.update(state='failed' if failed else 'complete',error='；'.join(errors),output_count=count); self.save(job)
                self.jobs.pop(job['id'],None)
                if not self.batch:
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
