"""UI adapter for persistent work. All queue starts are explicit user actions."""
import copy
import hashlib
import json
import time
import uuid
from pathlib import Path
from PySide6.QtCore import QTimer
from .queued_work import ACTIVE
from .work_queue import WorkQueue


class QueueRunner:
    def __init__(self, client):
        self.client = client
        self.window = client.window
        self.store = WorkQueue(self.window.store.db)
        self.dispatching = False
        self.preparing = None
        self.checking = set()
        self.message = '加入工作後按「開始」。重開後不會自動派送。'

    def notify(self, message=None):
        if message is not None:
            self.message = message
        self.client.stateChanged.emit()
        dialog = getattr(self.window, 'queue_dialog', None)
        if dialog is not None:
            dialog.refresh()

    def pause(self):
        self.dispatching = False
        self.notify('已暫停後續派送；已提交的工作繼續完成。')

    def disconnected(self):
        self.dispatching = False
        self.preparing = None
        self.checking.clear()
        self.notify('連線中斷；未結案項目會核對原嘗試，不會自動重送。')

    def busy(self):
        return self.dispatching or any(r['state'] in ACTIVE and r['work']['scope']['server'] == self.client.url
                                      for r in self.store.rows())

    def require_connection(self):
        if not self.client.connected or not getattr(self.client, 'queue_supported', False):
            raise ValueError('請連線至支援 Queue 的配套擴充。')

    def enqueue_current(self):
        from .workflow_flow import execution_profiles
        from .workflow_runner import image_sources, bound_sources
        from .composition_image import freeze_image_source
        self.require_connection()
        if self.preparing:
            raise ValueError('正在保存工作快照，請稍候。')
        snapshot = copy.deepcopy(self.client.snapshot())
        profiles = execution_profiles(snapshot['state'])
        if len(profiles) != 1:
            raise ValueError('本輪 Queue 請選擇單一工作流。')
        profile = profiles[0]
        keys = image_sources(snapshot['state'], profile)
        dynamic = bound_sources(snapshot['state'], keys)
        token = uuid.uuid4().hex
        self.preparing = token
        self.notify('正在取得排入時的工作版本…')
        def failed(error):
            if self.preparing == token:
                self.preparing = None
                self.notify('工作未加入：'+str(error))
        def ready(values, errors):
            if self.preparing != token:
                return
            if errors:
                failed('；'.join(errors.values()))
                return
            try:
                batches=[]
                for key, collection in values.items():
                    value=snapshot['state']['canvas_functions']['images'][key]
                    choice=value.get('selection')
                    items=collection['items']
                    if choice:
                        if choice.get('collection')!=collection['collection']:
                            raise ValueError('指定單張的圖片集合已更新，請重新選擇。')
                        items=[item for item in items if item['reference']['image']==choice.get('image')]
                        if len(items)!=1:raise ValueError('指定圖片已不在原集合中。')
                    batches.append(items)
                    value['source']=copy.deepcopy(items[0])
                if batches and any(len(items)>1 for items in batches):
                    signature=[item['sha256'] for item in batches[0]]
                    if any([item['sha256'] for item in batch]!=signature for batch in batches):
                        raise ValueError('同一工作流連入不同圖片集合，請統一來源。')
                    if len(keys)!=1:raise ValueError('圖片逐張執行請使用一個明確的來源連線。')
                    self.preparing=None
                    self.enqueue_images(profile,batches[0],snapshot=snapshot,preserve_bindings=True)
                    return
                sources = [freeze_image_source(snapshot['state'], self.window.store.directory, key) for key in keys]
                if len({s['sha256'] for s in sources}) > 1:
                    raise ValueError('同一工作流接入不同圖片，請明確選擇來源。')
                self.prepare(snapshot, profile, sources[0] if sources else None, token, failed)
            except (ValueError, OSError) as exc:
                failed(exc)
        self.client.images.resolve(snapshot['state'], dynamic, self.window.store.directory, {}, ready, failed,
                                   valid=lambda:self.preparing == token,collections=True)

    def upload(self, source, done, failed):
        from .generation import uploaded_image
        path = (self.window.store.directory / source['relative']).resolve()
        if not path.is_relative_to(self.window.store.directory.resolve()):
            raise ValueError('Queue 圖片路徑無效。')
        raw = path.read_bytes()
        if len(raw)>25*1024*1024 or hashlib.sha256(raw).hexdigest()!=source['sha256']:
            raise ValueError('Queue 圖片副本已變更。')
        boundary = 'PCSQueue'+uuid.uuid4().hex
        filename = source['sha256']+path.suffix
        body = b''.join((f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n').encode()
                        for k, v in dict(type='input', subfolder='prompt_calculus_studio/queue', overwrite='false').items())
        body += (f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: application/octet-stream\r\n\r\n').encode()+raw+f'\r\n--{boundary}--\r\n'.encode()
        self.client.request('/upload/image', done=lambda r:done(uploaded_image(r)), failed=failed,
                            raw=body, content_type='multipart/form-data; boundary='+boundary)

    def prepare(self, snapshot, profile, source, token, failed, done=None, store_result=True, queue_texts=None):
        def capture(image=''):
            if self.preparing != token:
                return
            ident = uuid.uuid4().hex
            started = time.monotonic()
            def receive(result):
                if self.preparing != token:
                    return
                if result.get('state') == 'captured':
                    try:
                        item = self.store.add(result['work'], ident) if store_result else dict(work=result['work'],capture=ident)
                    except (ValueError, OSError) as exc:
                        failed(exc)
                        return
                    if done:
                        done(item)
                    else:
                        self.preparing = None
                        self.notify('工作已加入；可繼續編輯，開始後使用這份固定快照。')
                        if self.dispatching:
                            self.pump()
                elif result.get('state') in ('failed', 'unconfirmed'):
                    failed(result.get('error') or '工作快照未確認。')
                elif time.monotonic()-started > 35:
                    failed('取得工作快照逾時；未派送生成。')
                else:
                    QTimer.singleShot(500, lambda:self.client.request('workflow/native/status', dict(id=ident), receive, failed)
                                      if self.preparing == token else None)
            request=dict(id=ident, snapshot=snapshot, workflow=profile['id'], image=image)
            if queue_texts is not None:request['queue_texts']=queue_texts
            self.client.request('workflow/queue/capture', request, receive, failed)
        if source:
            self.upload(source, capture, failed)
        else:
            capture()

    def enqueue_images(self, profile, sources, mode='none', target=None, choice=None, fixed='', snapshot=None, preserve_bindings=False):
        from .image_iteration import plan_inputs
        from .generation import image_target
        self.require_connection()
        if self.preparing:raise ValueError('正在保存工作快照，請稍候。')
        if not image_target(profile):raise ValueError('請明確選擇接收圖片的 LoadImage 節點。')
        # Freeze all local bytes and metadata before any asynchronous Web read.
        entries=plan_inputs(self.window.store.directory,copy.deepcopy(sources),mode,target,choice,fixed)
        snapshot=copy.deepcopy(snapshot or self.client.snapshot())
        profiles=snapshot['state']['generation']['profiles']
        index=next((i for i,p in enumerate(profiles) if p['id']==profile['id']),None)
        if index is None:raise ValueError('所選工作流已移除。')
        profiles[index]=copy.deepcopy(profile)
        token=uuid.uuid4().hex;self.preparing=token
        self.notify('正在保存整批圖片與共同工作流版本…')
        def failed(error):
            if self.preparing==token:
                self.preparing=None;self.dispatching=False
                self.notify('圖片批次未加入：'+str(error))
        def template(base):
            saved=[]
            def next_image(index):
                if self.preparing!=token:return
                if index==len(entries):
                    try:self.store.add_many(saved)
                    except (ValueError,OSError) as exc:failed(exc);return
                    self.preparing=None
                    self.notify(f'已依序加入 {len(saved)} 項圖片工作。預覽切換不改已保存的輸入。')
                    if self.dispatching:self.pump()
                    return
                entry=entries[index]
                def uploaded(image):
                    if self.preparing!=token:return
                    request=dict(id=uuid.uuid4().hex,base=base['capture'],sha256=base['work']['sha256'],node=image_target(profile),image=image)
                    for key in ('text','input_snapshot'):
                        if key in entry:request[key]=entry[key]
                    def received(value):
                        if self.preparing!=token:return
                        saved.append((value['work'],value['id'],profile['name']+' · '+entry['source']['name']))
                        QTimer.singleShot(0,lambda:next_image(index+1))
                    self.client.request('workflow/queue/variant',request,received,failed)
                try:self.upload(entry['source'],uploaded,failed)
                except (ValueError,OSError) as exc:failed(exc)
            next_image(0)
        bindings=None if preserve_bindings else []
        if entries[0].get('text'):
            text=entries[0]['text']
            bindings=[dict(node=text['node'],field=text['field'],text=text['value'],text_source='pcs')]
        try:self.prepare(snapshot,profile,entries[0]['source'],token,failed,template,False,bindings)
        except (ValueError,OSError) as exc:failed(exc)

    def start(self):
        self.require_connection()
        legacy = self.client.generation
        if legacy.batch or self.client.run_id or any(j['server']==self.client.url and j['state'] in ACTIVE for j in legacy.jobs.values()):
            raise ValueError('原生成入口尚有未結案工作，請先完成或核對。')
        self.dispatching = True
        self.notify('Queue 正在依序派送。')
        self.pump()

    def pump(self):
        if not self.dispatching or not self.client.connected:
            return
        item = self.store.claim(self.client.url)
        if item is None:
            if not any(r['state'] in ACTIVE for r in self.store.rows() if r['work']['scope']['server']==self.client.url):
                self.dispatching = False
                self.notify('Queue 已跑完；再加入工作後請重新按開始。')
            return
        self.notify()
        def failed(error):
            self.store.update(item['id'], state='failed' if getattr(error, 'rejected', False) else 'unconfirmed', error=str(error))
            self.dispatching = False
            self.notify('派送未完成，已暫停；未知提交不會自動重送。')
        self.client.request('workflow/queue/submit', dict(job=item['id'], attempt=item['attempt'],
                            capture=item['capture'], sha256=item['work']['sha256']),
                            lambda result:self.received(item['id'], result), failed)

    def received(self, ident, result):
        item = self.store.read(ident)
        if result.get('id') != item.get('attempt') or result.get('capture') != item['capture']:
            self.store.update(ident, state='unconfirmed', error='Queue 回覆不屬於原工作。')
            self.dispatching = False
            self.notify('回覆識別不符，已暫停派送。')
            return
        changes = {k:copy.deepcopy(result[k]) for k in ('state', 'prompt_id', 'error', 'outputs', 'results', 'payload') if k in result}
        item = self.store.update(ident, **changes)
        # Keep ordinary job-detail/history tools usable without letting their
        # bounded cleanup own the persistent queue's references.
        record = dict(id=item['attempt'], queue_job=ident, server=item['work']['scope']['server'],
                      created=item['attempts'][-1]['created'], **changes)
        self.client.generation.save(record)
        if item['state'] in ('failed', 'unconfirmed', 'results_pending'):
            self.dispatching = False
            self.notify(item.get('error') or '工作尚未結案，已暫停派送。')
        else:
            self.notify()
        if item['state'] == 'complete':
            QTimer.singleShot(0, self.pump)

    def observe(self):
        if not self.client.connected or not getattr(self.client, 'queue_supported', False):
            return
        for item in self.store.rows():
            if item['state'] not in ACTIVE or item['work']['scope']['server'] != self.client.url or item['id'] in self.checking:
                continue
            self.checking.add(item['id'])
            def receive(result, ident=item['id']):
                self.checking.discard(ident)
                self.received(ident, result)
            def failed(error, ident=item['id']):
                self.checking.discard(ident)
                self.dispatching = False
                self.notify('原嘗試暫時無法核對：'+str(error))
            self.client.request('workflow/queue/status', dict(attempt=item['attempt']), receive, failed)
