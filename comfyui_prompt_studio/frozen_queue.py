"""Server receipts for explicitly prepared work; never read current Web state."""
import copy
import hashlib
import json
import os
import time
import uuid
from pathlib import Path, PurePosixPath
from .shared.queued_work import ACTIVE, digest, seal, validate, envelope, result_state
from .shared.snapshots import validate_snapshot
from .shared.generation import text_fields


class FrozenQueue:
    def __init__(self, service, input_root=None):
        self.service = service
        self.input_root = Path(input_root).resolve() if input_root else None
        self.assets = service.directory / 'queue_assets'
        with service.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS queue_captures (id TEXT PRIMARY KEY, body TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS queue_attempts (id TEXT PRIMARY KEY, body TEXT NOT NULL)')

    def read(self, table, ident):
        if table not in ('queue_captures', 'queue_attempts'):
            raise ValueError('紀錄類型無效。')
        with self.service.connect() as db:
            row = db.execute('SELECT body FROM '+table+' WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('找不到已保存的 Queue 工作或嘗試。')
        return json.loads(row[0])

    def save_attempt(self, attempt):
        with self.service.connect() as db:
            prior=db.execute('SELECT body FROM queue_attempts WHERE id=?',(attempt['id'],)).fetchone()
            if prior and json.loads(prior[0]).get('transport_recorded'):
                attempt['transport_recorded']=True
                attempt['payload']=json.loads(prior[0])['payload']
            db.execute('UPDATE queue_attempts SET body=? WHERE id=?',
                       (json.dumps(attempt, ensure_ascii=False), attempt['id']))

    def unresolved(self):
        with self.service.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM queue_attempts')
                    if json.loads(r[0])['state'] in ACTIVE]

    def require_idle(self):
        if self.unresolved():
            raise ValueError('Queue 尚有未結案工作，請先完成或核對該工作。')

    def preserve_image(self, filename):
        if self.input_root is None or not isinstance(filename, str):
            raise ValueError('無法保存 Queue 圖片來源。')
        root = self.input_root
        for kind in ('input', 'output', 'temp'):
            suffix = ' ['+kind+']'
            if filename.endswith(suffix):
                filename = filename[:-len(suffix)]
                root = self.input_root if kind == 'input' else self.service.roots[kind]
                break
        relative = PurePosixPath(filename)
        if relative.is_absolute() or '..' in relative.parts or '\\' in filename or ':' in filename:
            raise ValueError('Queue 圖片路徑無效。')
        path = (root / filename).resolve(strict=True)
        if not path.is_relative_to(root.resolve()) or not path.is_file() or path.stat().st_size > 25*1024*1024:
            raise ValueError('Queue 圖片不存在或超過 25 MB。')
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        extension = path.suffix.lower()
        if extension not in ('.png', '.jpg', '.jpeg', '.webp', '.bmp'):
            raise ValueError('Queue 圖片格式不支援。')
        asset = dict(sha256=sha, size=len(raw), name=sha+extension,
                     uploaded='prompt_calculus_studio/queue/'+sha+extension)
        self.assets.mkdir(parents=True, exist_ok=True)
        target = self.assets / asset['name']
        if not target.exists():
            try:
                with target.open('xb') as stream:
                    stream.write(raw)
            except FileExistsError:
                pass
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError('Queue 圖片保存副本不一致。')
        self.materialize(asset)
        return asset

    def materialize(self, asset):
        name = asset['name']
        if Path(name).name != name or '/' in name or '\\' in name:
            raise ValueError('Queue 資產名稱無效。')
        raw = (self.assets / name).read_bytes()
        if len(raw) != asset['size'] or hashlib.sha256(raw).hexdigest() != asset['sha256']:
            raise ValueError('Queue 圖片副本已變更，未提交。')
        target = self.input_root / asset['uploaded']
        if not target.resolve().is_relative_to(self.input_root):
            raise ValueError('Queue 資產目的地無效。')
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == asset['sha256']:
            return
        temporary = target.with_name(target.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            temporary.write_bytes(raw)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)

    def capture(self, operation):
        graph, visual = copy.deepcopy(operation['graph']), copy.deepcopy(operation['visual'])
        assets = []
        for key, node in graph.items():
            if node['class_type'] != 'LoadImage':
                continue
            original = node['inputs'].get('image')
            asset = self.preserve_image(original)
            asset['node'] = key
            self.patch_visual(visual, key, original, asset['uploaded'], 'image')
            node['inputs']['image'] = asset['uploaded']
            assets.append(asset)
        marker = operation['marker']
        outputs = [key for key, node in graph.items() if node['class_type'] in ('SaveImage', 'PreviewImage')]
        work = seal(dict(protocol=1, capture=operation['id'], created=time.time(),
                         scope=dict(server=operation['server'], library=operation['snapshot']['library_id'],
                                    workspace=operation['snapshot']['state']['workspace'],
                                    workflow=operation['workflow'], frontend_id=operation.get('resolved_identity',operation['identity'])['frontend_id']),
                         snapshot=operation['snapshot'], graph=graph, visual=visual, outputs=outputs,
                         assets=assets, texts=marker['texts'], source_texts=marker['source_texts'],
                         generation=dict(marker['generation'], workflow_sha256=digest(graph)),
                         proof=dict(identity=operation.get('resolved_identity',operation['identity']), epoch=operation.get('resolved_epoch',operation['epoch']),
                                    graph_sha256=digest(operation['graph']))))
        with self.service.connect() as db:
            db.execute('INSERT INTO queue_captures VALUES (?,?)', (operation['id'], json.dumps(work, ensure_ascii=False)))
        return work

    @staticmethod
    def patch_visual(visual, key, before, after, field):
        node = next((n for n in visual['nodes'] if str(n.get('id')) == key), None)
        if node is None:
            raise ValueError('圖片／文字節點缺少可視工作流資料。')
        widgets = node.get('widgets_values')
        if isinstance(widgets, dict) and widgets.get(field) == before:
            widgets[field] = after
            return
        if not isinstance(widgets, list):
            raise ValueError('無法辨識可視工作流的欄位。')
        matches = [i for i, v in enumerate(widgets) if v == before]
        if len(matches) != 1:
            raise ValueError('可視工作流欄位不唯一，未建立不一致快照。')
        widgets[matches[0]] = after

    def payload(self, work, job, attempt, prompt):
        visual = copy.deepcopy(work['visual'])
        visual.setdefault('extra', {})['pcs_queue_attempt'] = attempt
        return dict(prompt_id=prompt, client_id='pcs-queue-'+attempt, prompt=copy.deepcopy(work['graph']),
                    extra_data=dict(extra_pnginfo=dict(workflow=visual,
                        prompt_studio=envelope(work, job, attempt, prompt))))

    def variant(self, value, server):
        """Derive only selected image/text fields from one confirmed template."""
        work=copy.deepcopy(validate(self.read('queue_captures',value.get('base'))))
        if work['sha256']!=value.get('sha256') or work['scope']['server']!=server:
            raise ValueError('圖片批次的固定模板不符。')
        ident=value.get('id')
        if not isinstance(ident,str) or not ident or len(ident)>100:raise ValueError('圖片工作識別無效。')
        node=value.get('node');graph=work['graph']
        if graph.get(node,{}).get('class_type')!='LoadImage':raise ValueError('請指定有效的 LoadImage 目標。')
        asset=self.preserve_image(value.get('image'));asset['node']=node
        self.patch_visual(work['visual'],node,graph[node]['inputs']['image'],asset['uploaded'],'image')
        graph[node]['inputs']['image']=asset['uploaded']
        work['assets']=[a for a in work['assets'] if a['node']!=node]+[asset]
        override=value.get('text')
        if override is not None:
            if (not isinstance(override,dict) or (override.get('node'),override.get('field')) not in text_fields(graph)
                    or not isinstance(override.get('value'),str)):
                raise ValueError('圖片 Prompt 對應無效。')
            key,field,text=override['node'],override['field'],override['value']
            before=graph[key]['inputs'][field]
            if before!=text:self.patch_visual(work['visual'],key,before,text,field)
            graph[key]['inputs'][field]=text
            work['texts']=[b for b in work['texts'] if (b['node'],b['field'])!=(key,field)]
            binding=dict(node=key,field=field,text=text,text_source='pcs',source='image-metadata' if value.get('input_snapshot') else 'batch-fixed')
            work['texts'].append(binding)
            work['source_texts']=[b for b in work['source_texts'] if (b['node'],b['field'])!=(key,field)]+[copy.deepcopy(binding)]
        if value.get('input_snapshot') is not None:
            work['input_snapshot']=copy.deepcopy(validate_snapshot(value['input_snapshot']))
        work.update(capture=ident,created=time.time(),parent=value['base'])
        work['generation']['workflow_sha256']=digest(graph)
        work=seal(work)
        with self.service.connect() as db:
            db.execute('INSERT INTO queue_captures VALUES (?,?)',(ident,json.dumps(work,ensure_ascii=False)))
        return dict(id=ident,work=work)

    async def submit(self, value, server, post, native_busy=False):
        self.service.native_queue.require_supported()
        ident, job = value.get('attempt'), value.get('job')
        if any(not isinstance(v, str) or not v or len(v)>100 for v in (ident, job)):
            raise ValueError('Queue 操作識別碼無效。')
        work = validate(self.read('queue_captures', value.get('capture')))
        if work['sha256'] != value.get('sha256') or work['scope']['server'] != server:
            raise ValueError('Queue 固定版本或目標服務不符。')
        fingerprint = digest(value)
        with self.service.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            prior = db.execute('SELECT body FROM queue_attempts WHERE id=?', (ident,)).fetchone()
            if prior:
                saved = json.loads(prior[0])
                if saved['request_hash'] != fingerprint:
                    raise ValueError('同一 Queue 嘗試內容不一致。')
                return saved
            active = [json.loads(r[0]) for r in db.execute('SELECT body FROM queue_attempts')]
            if native_busy or any(r['state'] in ACTIVE for r in active):
                raise ValueError('已有尚未結案的 PCS 工作；未派送另一項。')
            prompt = str(uuid.uuid4())
            payload = self.payload(work, job, ident, prompt)
            saved = dict(id=ident, job=job, capture=value['capture'], request_hash=fingerprint,
                         prompt_id=prompt, created=time.time(), state='submitting', payload=payload, error='')
            db.execute('INSERT INTO queue_attempts VALUES (?,?)', (ident, json.dumps(saved, ensure_ascii=False)))
        attempted = False
        try:
            for asset in work['assets']:
                self.materialize(asset)
            attempted = True
            status, response = await post(copy.deepcopy(payload))
            if status >= 400:
                error = response.get('error', {})
                rejected = status == 400 and isinstance(error, dict) and error.get('type') in (
                    'prompt_outputs_failed_validation', 'prompt_no_outputs', 'no_prompt')
                saved.update(state='failed' if rejected else 'unconfirmed', error=str(error))
            elif response.get('prompt_id') != prompt:
                saved.update(state='unconfirmed', error='Queue 提交回覆識別不符，不會重送。')
            elif not self.read('queue_attempts', ident).get('transport_recorded'):
                saved.update(state='unconfirmed', error='尚未核對標準提交入口的持久收據。')
            else:
                saved.update(state='queued', error='')
        except Exception as exc:
            saved.update(state='unconfirmed' if attempted else 'failed', error=str(exc))
        self.save_attempt(saved)
        return saved

    def decorate(self, data):
        extra = data.get('extra_data', {}).get('extra_pnginfo', {})
        visual = extra.get('workflow', {})
        marker=visual.get('extra') if isinstance(visual,dict) else None
        ident = marker.pop('pcs_queue_attempt', None) if isinstance(marker,dict) else None
        if not ident:
            return False
        recorded = False
        try:
            saved = self.read('queue_attempts', ident)
            expected = copy.deepcopy(saved['payload'])
            expected['extra_data']['extra_pnginfo']['workflow']['extra'].pop('pcs_queue_attempt', None)
            if saved['state'] != 'submitting' or digest(data) != digest(expected):
                raise ValueError('Queue 提交與已保存版本不符。')
            with self.service.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                actual = json.loads(db.execute('SELECT body FROM queue_attempts WHERE id=?', (ident,)).fetchone()[0])
                if actual.get('transport_recorded'):
                    raise ValueError('Queue 嘗試已派送，不可重送。')
                actual['transport_recorded'] = True
                actual['payload'] = copy.deepcopy(data)
                db.execute('UPDATE queue_attempts SET body=? WHERE id=?', (json.dumps(actual, ensure_ascii=False), ident))
                db.execute('INSERT INTO jobs VALUES (?,?,?)', (saved['prompt_id'], json.dumps(extra['prompt_studio'], ensure_ascii=False), time.time()))
            recorded = True
        finally:
            if not recorded:
                data['prompt'] = {}
                extra.pop('prompt_studio', None)
        return True

    def reconcile(self, ident, running, queued, history):
        saved = self.read('queue_attempts', ident)
        if saved['state'] not in ACTIVE:
            return saved
        prompt = saved['prompt_id']
        if prompt in running or prompt in queued:
            saved.update(state='running' if prompt in running else 'queued', error='')
        elif prompt in history:
            work = self.read('queue_captures', saved['capture'])
            state, error = result_state(work, prompt, history[prompt])
            saved.update(state=state, error=error, outputs=copy.deepcopy(history[prompt].get('outputs', {})))
            if state=='complete':
                try:
                    results=[]
                    for node in work['outputs']:
                        for image in saved['outputs'][node]['images']:
                            path=self.service.source(image)
                            results.append(dict(node=node,image=copy.deepcopy(image),sha256=self.service.digest(path)))
                    saved['results']=results
                except (ValueError,OSError,KeyError,TypeError) as exc:
                    saved.update(state='results_pending',error='結果尚未收齊：'+str(exc))
        elif time.time()-saved['created'] > 30:
            saved.update(state='unconfirmed', error='佇列與歷史找不到原嘗試；停止派送，不重送。')
        self.save_attempt(saved)
        return saved
