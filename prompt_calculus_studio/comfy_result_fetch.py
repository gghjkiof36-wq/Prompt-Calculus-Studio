"""Owned manifest acquisition. Workers own HTTP/bytes; GUI owns transactions.

Candidate intent precedes asset work. A sealed history collection and terminal
evidence remain separate until the existing Stage accepting transaction runs.
Only the serialized candidate writer opens its own SQLite connection. No worker
uses the GUI connection, live document state, or a widget/QPixmap.
"""
import copy
import hashlib
from io import BytesIO
from http.client import HTTPConnection, HTTPSConnection
import json
import os
import socket
from pathlib import Path
import threading
import time
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler, HTTPRedirectHandler, HTTPHandler, HTTPSHandler
import uuid

from PySide6.QtCore import QObject, Signal, Slot, QRunnable, QThreadPool, QTimer
from . import comfy_event_protocol as p

CAPABILITY = 'result_manifest_v1'
SCHEMA = 'pcs.result-manifest.v1'
BATCH = 16
FETCH_BATCH = 32
FINAL_BATCH = 64
PARALLEL = max(1, min(4, (os.cpu_count() or 2)-1))
# Candidate-only prefetch stays bounded, but the last outputs should reach disk
# before terminal history rather than wait behind a one-second display window.
CANDIDATE_INTERVAL = .25
MAX_IMAGE = 25 * 1024 * 1024
_pool = None


def result_pool():
    # Shared across owners, with process lifetime rather than a window parent.
    # Closing an owner cancels its work without destroying a running Qt pool.
    global _pool
    if _pool is None:
        _pool=QThreadPool();_pool.setMaxThreadCount(PARALLEL)
    return _pool


def digest(value):
    return hashlib.sha256(p.encode(value)).hexdigest()


def semantic(item):
    return digest({k:item[k] for k in ('node_path','occurrence','output_slot','item_index')})


def prepare_packet(packet):
    """Worker-owned serialization; the wire items themselves remain unchanged."""
    packet['records']={item['ordinal']:(semantic(item),p.encode(item).decode()) for item in packet['items']}
    packet['header_json']=p.encode(packet['header']).decode()
    return packet


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise ValueError('Result redirect refused')


class LazyHTTPSHandler(HTTPSHandler):
    def __init__(self):
        # Python 3.12's HTTPSHandler eagerly loads Windows certificate stores.
        # HTTP results need no SSL context. HTTPSConnection creates the default
        # verified context in the worker only when an HTTPS request is opened.
        HTTPHandler.__init__(self)


class Cancellation(threading.Event):
    """Interrupt active response reads as well as the next work item."""
    def __init__(self):
        super().__init__();self.lock=threading.Lock();self.sockets=set()

    def add(self, stream):
        with self.lock:
            if self.is_set():raise ValueError('Result owner closed')
            self.sockets.add(stream)

    def remove(self, stream):
        with self.lock:self.sockets.discard(stream)

    def set(self):
        super().set()
        with self.lock:
            for stream in self.sockets:
                try:stream.shutdown(socket.SHUT_RDWR)
                except OSError:pass


class ManifestReader:
    """Worker-local transport; injectable only at this I/O boundary in tests."""
    def __init__(self, url, token, identity, owner, cancel):
        self.url, self.token, self.identity, self.owner, self.cancel = url, token, identity, owner, cancel
        self.sockets=[]
        def connection(kind):
            def create(host,**kwargs):
                kwargs['timeout']=1
                client=kind(host,**kwargs);original=client.connect
                def connect():
                    original();stream=client.sock;stream.settimeout(6)
                    self.sockets.append(stream)
                    if isinstance(cancel,Cancellation):cancel.add(stream)
                client.connect=connect
                return client
            return create
        http=HTTPHandler();https=LazyHTTPSHandler()
        http.http_open=lambda request:http.do_open(connection(HTTPConnection),request)
        https.https_open=lambda request:https.do_open(connection(HTTPSConnection),request)
        self.opener = build_opener(ProxyHandler({}), NoRedirect(), http, https)

    def request(self, route, *, data=None, raw=False, **query):
        if self.cancel.is_set():raise ValueError('Result owner closed')
        scope = dict(p.scope(), **{k:self.owner[k] for k in ('library_id','workspace_id')})
        query.update(server_instance=self.identity['server_instance'], epoch=self.identity['epoch'],
                     scope=p.encode(scope).decode(), scope_digest=p.scope_digest(scope), owner=p.encode(self.owner).decode())
        request = Request(self.url+'/prompt_studio/results/'+route+'?'+urlencode(query),
                          data=p.encode(data) if data is not None else None,
                          headers={'X-Prompt-Studio':self.token, 'Content-Type':'application/json'})
        limit = MAX_IMAGE if raw else p.MAX_PAGE_BYTES
        # Connect is bounded; cancellation interrupts headers and body reads.
        try:
            with self.opener.open(request, timeout=6) as response:content=response.read(limit+1)
        finally:
            for stream in self.sockets:
                if isinstance(self.cancel,Cancellation):self.cancel.remove(stream)
            self.sockets.clear()
        if self.cancel.is_set():raise ValueError('Result owner closed')
        if len(content)>limit:raise ValueError('Result response exceeds limit')
        return content if raw else json.loads(content)

    def manifest(self, expected, reference=None):
        header = reference or self.request('manifests/bind', data=expected)
        ref = {k:header[k] for k in ('manifest_id','revision','checksum')}
        items, leases, after = [], {}, -1
        while True:
            page = self.request('manifest', **ref, after=after)
            current = page['header']
            check_header(current, expected)
            if any(current[k]!=ref[k] for k in ref):raise ValueError('Manifest revision changed')
            if items and current != header:raise ValueError('Conflicting manifest pages')
            header = current
            for item in page['items']:
                check_item(item, len(items))
                items.append(item)
                leases[item['ordinal']] = {k:page[k] for k in ('expires','asset_token')}
            if page['next_after'] != (items[-1]['ordinal'] if items else -1):
                raise ValueError('Invalid manifest cursor')
            if page['done']:break
            if page['next_after']<=after:raise ValueError('Truncated manifest page')
            after=page['next_after']
        if len(items)!=header['item_count'] or digest(items)!=header['items_digest']:
            raise ValueError('Incomplete manifest')
        return dict(header=header, items=items, leases=leases)


def check_header(header, expected):
    if (header.get('schema')!=SCHEMA or header.get('identity')!=expected or
            header.get('manifest_id')!=digest(expected) or
            header.get('checksum')!=digest({k:v for k,v in header.items() if k!='checksum'}) or
            type(header.get('revision')) is not int or header['revision']<1 or
            type(header.get('item_count')) is not int or not 0<=header['item_count']<=100_000):
        raise ValueError('Wrong result identity/checksum')
    if header.get('sealed'):
        if (header.get('item_source')!='history' or header.get('terminal_state')!='success' or
                header.get('expected_count')!=header['item_count'] or
                header.get('final_digest')!=header['items_digest'] or
                (header['item_count']==0 and not header.get('zero_output'))):
            raise ValueError('Final requires a sealed successful history')
    elif header.get('item_source') not in (None, 'candidates'):
        raise ValueError('Unsealed final collection')


def check_item(item, ordinal):
    image=item.get('image',{})
    name=image.get('filename','')
    sub=image.get('subfolder','')
    if (item.get('ordinal')!=ordinal or type(item.get('item_index')) is not int or item['item_index']<0 or
            not isinstance(item.get('node_path'),list) or not item['node_path'] or
            not all(isinstance(v,str) and v for v in item['node_path']) or
            not isinstance(item.get('occurrence'),str) or item.get('output_slot')!='images' or
            not isinstance(item.get('node_id'),str) or not item['node_id'] or
            item.get('format')!='png' or type(item.get('size')) is not int or not 0<item['size']<=MAX_IMAGE or
            not isinstance(name,str) or not name or any(v in name for v in ('/','\\',':')) or
            not isinstance(sub,str) or '\\' in sub or ':' in sub or sub.startswith('/') or '..' in sub.split('/') or
            image.get('type') not in ('output','temp') or set(image)!={'filename','subfolder','type'}):
        raise ValueError('Invalid result item')
    sha=item.get('asset_sha256','')
    if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):raise ValueError('Invalid asset hash')
    if item.get('asset_key')!=digest([item['node_id'], image, sha]):raise ValueError('Invalid asset identity')


def sharing_retry(action, cancel=None):
    """Worker-only grace for Windows scanners; other errors use owner retry."""
    try:return action()
    except OSError as exc:
        if getattr(exc,'winerror',None)!=32:raise
    deadline=time.monotonic()+.5
    while True:
        delay=min(.01,max(0,deadline-time.monotonic()))
        if cancel is None:time.sleep(delay)
        elif cancel.wait(delay):raise ValueError('Result owner closed')
        try:return action()
        except OSError as exc:
            if getattr(exc,'winerror',None)!=32 or time.monotonic()>=deadline:raise


def atomic_file(root, relative, content, *, durable=True, repair=False, cancel=None):
    target=root/relative
    for part in (target,*target.parents):
        if part.is_symlink() or part.is_junction():raise ValueError('Redirected result destination')
        if part==root:break
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists():
        if target.read_bytes()==content:return
        if not repair:raise ValueError('Local result asset changed')
    temporary=target.with_name('.'+uuid.uuid4().hex+'.partial')
    try:
        with temporary.open('xb') as stream:
            stream.write(content);stream.flush()
            if durable:os.fsync(stream.fileno())
        try:
            move=os.replace if repair else os.rename
            sharing_retry(lambda:move(temporary,target),cancel)
            temporary=None  # Published: there is no partial left to unlink.
        except FileExistsError:
            if target.read_bytes()!=content:raise ValueError('Conflicting local result asset')
    finally:
        if temporary is not None:sharing_retry(lambda:temporary.unlink(missing_ok=True))


def image_reader(content):
    from PySide6.QtCore import QByteArray, QBuffer, QIODevice, Qt
    from PySide6.QtGui import QImageReader
    buffer=QBuffer();buffer.setData(QByteArray(content));buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    decoder=QImageReader(buffer);decoder.setAutoTransform(True);decoder.setAllocationLimit(128)
    size=decoder.size()
    if (bytes(decoder.format())!=b'png' or not decoder.canRead() or not size.isValid() or
            size.width()*size.height()>40_000_000 or decoder.imageCount()>1):
        raise ValueError('Invalid single PNG result')
    return buffer,decoder,size


def thumbnail(decoder, size, limit):
    from PySide6.QtCore import Qt
    if max(size.width(),size.height())>limit:
        size.scale(limit,limit,Qt.AspectRatioMode.KeepAspectRatio);decoder.setScaledSize(size)
    preview=decoder.read()
    if preview.isNull():raise ValueError('Result thumbnail failed')
    if max(preview.width(),preview.height())>limit:
        preview=preview.scaled(limit,limit,Qt.AspectRatioMode.KeepAspectRatio,Qt.TransformationMode.SmoothTransformation)
    return preview


def asset(reader, packet, item, directory, previous=None, *, root=None):
    """Original ready is independent of regenerable thumbnail/metadata files."""
    root=Path(directory).resolve() if root is None else root
    content=None;reused=False
    if previous and previous.get('asset_key')==item['asset_key']:
        relative=previous['source']['relative']
        if relative=='originals/generation/'+item['asset_sha256']+'.png':
            path=root/relative
            if path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(root):
                content=path.read_bytes()
                if len(content)!=item['size'] or hashlib.sha256(content).hexdigest()!=item['asset_sha256']:
                    content=None  # A receipt is never proof that local bytes survived.
                else:reused=True
    if content is None:
        ref={k:packet['header'][k] for k in ('manifest_id','revision','checksum')}
        content=reader.request('asset', raw=True, **ref, **packet['leases'][item['ordinal']],
                              ordinal=item['ordinal'], asset_sha256=item['asset_sha256'])
    if not reused and (len(content)!=item['size'] or hashlib.sha256(content).hexdigest()!=item['asset_sha256']):
        raise ValueError('Result bytes/hash mismatch')
    if reused and previous['source'].get('sha256')==item['asset_sha256'] and packet['header'].get('item_source')=='history':
        return copy.deepcopy(previous),None
    buffer,decoder,size=image_reader(content)
    width,height=size.width(),size.height()
    sha=item['asset_sha256'];relative='originals/generation/'+sha+'.png'
    thumb='thumbnails/results/'+sha+'.png';meta='originals/generation/'+sha+'.json'
    if reader.cancel.is_set():raise ValueError('Result owner closed')
    atomic_file(root,relative,content,repair=True,cancel=reader.cancel)
    # Scheme C: early bytes are durable prefetch only. Do not decode a candidate
    # preview or schedule a GUI display before the sealed final is accepted.
    receipt=dict(asset_key=item['asset_key'],source=dict(relative=relative,name=item['image']['filename'],
        sha256=sha,width=width,height=height),thumb=thumb,metadata=meta,derived=None)
    return receipt,None


class OriginalUnavailable(ValueError):
    pass


def derivatives(directory, receipt, cancel):
    """Worker-only verification/repair. These files never gate frame acceptance."""
    from PySide6.QtCore import QBuffer, QIODevice
    from .pnginfo import png_metadata_stream
    root=Path(directory).resolve();sha=receipt['source']['sha256']
    relative='originals/generation/'+sha+'.png'
    if receipt['source']['relative']!=relative:raise ValueError('Wrong original path')
    path=root/relative
    if path.is_symlink() or not path.resolve().is_relative_to(root):raise ValueError('Redirected original')
    try:content=path.read_bytes()
    except FileNotFoundError as exc:raise OriginalUnavailable('Original requires reacquisition') from exc
    if hashlib.sha256(content).hexdigest()!=sha:raise OriginalUnavailable('Original requires reacquisition')
    result=copy.deepcopy(receipt)
    expected={'thumb':'thumbnails/results/'+sha+'.png','metadata':'originals/generation/'+sha+'.json'}
    hashes=receipt.get('derived') or {}
    if all(receipt.get(key)==name and (root/name).is_file() and not (root/name).is_symlink() and
           (root/name).resolve().is_relative_to(root) and hashlib.sha256((root/name).read_bytes()).hexdigest()==hashes.get(key)
           for key,name in expected.items()):return result,False
    if cancel.is_set():raise ValueError('Result owner closed')
    buffer,decoder,size=image_reader(content);preview=thumbnail(decoder,size,512)
    out=QBuffer();out.open(QIODevice.OpenModeFlag.WriteOnly)
    if not preview.save(out,'PNG'):raise ValueError('Result thumbnail save failed')
    contents={'thumb':bytes(out.data()),'metadata':p.encode(png_metadata_stream(BytesIO(content)))}
    for key,value in contents.items():
        if cancel.is_set():raise ValueError('Result owner closed')
        atomic_file(root,expected[key],value,durable=False,repair=True,cancel=cancel)
    result.update(expected,derived={key:hashlib.sha256(value).hexdigest() for key,value in contents.items()})
    return result,True


class Signals(QObject):
    completed=Signal(object)


class Work(QRunnable):
    def __init__(self, action, context):
        super().__init__();self.action,self.context=action,context;self.signals=Signals()

    def run(self):
        try:value=self.action();error=''
        except Exception as exc:value=None;error=str(exc)
        self.signals.completed.emit((self,self.context,value,error))


class ResultFetch(QObject):
    repaired=Signal(str)

    def __init__(self, reducer):
        super().__init__(reducer.client)
        self.reducer,self.client,self.window=reducer,reducer.client,reducer.window
        self.pending={};self.manifests={};self.work=set()
        self.cancel=Cancellation();self.generation=0;self.derived={};self.derived_at=0
        self.suspended=set();self.healthy=True;self.refresh_requested=False;self.closed=False
        self.display_checks={};self.graphs={};self.writer_path=None
        self.window.store.result_repair=self.request_repair
        self.reader_type=ManifestReader
        self.timer=QTimer(self);self.timer.setSingleShot(True);self.timer.timeout.connect(self.tick)
        self.notice=QTimer(self);self.notice.setSingleShot(True);self.notice.timeout.connect(self.notify)

    @property
    def enabled(self):return bool((self.reducer.identity or {}).get(CAPABILITY))

    def eligible(self, job):
        return (self.enabled and job.get('server')==self.client.url and
                job.get('event_server') in (None,self.reducer.identity['server_instance']) and
                bool(job.get('native_operation') or job.get('queue_job')) and bool(job.get('prompt_id')) and
                isinstance(job.get('payload',{}).get('prompt'),dict))

    def identity(self, job):
        graph=job['payload']['prompt'];saved=self.graphs.get(job['id'])
        # Frozen graph equality also detects an in-place owner change. Never
        # cache by object identity: a late receipt may replace that payload.
        if saved is None or saved[0]!=graph:
            if len(self.graphs)>=32:self.graphs.pop(next(iter(self.graphs)))
            saved=self.graphs[job['id']]=(copy.deepcopy(graph),digest(graph))
        return dict(owner=dict(library_id=job.get('event_library') or self.window.store.library_id,
            workspace_id=job.get('workspace',self.window.state['workspace']),
            document_id=getattr(self.window,'document_id',None) or self.window.store.library_id),
            server_instance=self.reducer.identity['server_instance'],
            operation=dict(kind='frozen' if job.get('queue_job') else 'native',id=job['id']),
            prompt_id=job['prompt_id'],graph_digest=saved[1])

    def ensure(self, job, *, event=None, done=None, force=False):
        ident=job['id']
        if ident in self.suspended and not (force or event):
            if done:done()
            return
        if force or event:self.suspended.discard(ident)
        stopped=job['state'] in ('failed','cancelled','interrupted') and job.get('result_terminal')!='success'
        if not self.eligible(job) or job['state'] in ('complete','retired') or stopped and not (force or event):
            row=self.pending.pop(ident,None)
            if row:row['cancel'].set()
            if stopped:
                self.cancel.set();self.cancel=Cancellation()
                self.derived={key:value for key,value in self.derived.items() if value['operation']!=ident}
            if done:done()
            self.schedule()
            return
        if event is not None and self.reducer.store.contains(event):
            if done:done()
            return
        row=self.pending.get(ident)
        if row is None:
            expected=self.identity(job);manifest=digest(expected)
            self.manifests[manifest]=ident
            row=self.pending[ident]=dict(identity=expected,at=time.monotonic(),busy=False,
                tries=0,events=[],phase='manifest',packet=None,remaining=[],verified=[],cancel=Cancellation())
        # Notices do not touch the frozen graph. Every dispatched unit and final
        # transaction validate it, so a dense notice stream needs no full compare.
        if row.get('display_state')!=job['state']:
            row['display_state']=job['state']
            if job['state']=='results_pending' and not self.notice.isActive():self.notice.start(150)
        if event is not None:
            if self.reducer.store.contains(event):
                if done:done()
                return
            row['events'].append((copy.deepcopy(event),done))
        elif done:done()
        if force:
            row['at']=time.monotonic()
            if row['phase']=='manifest' and not stopped and job.get('result_terminal')!='success' and not row['tries']:
                row['at']=max(row['at'],row.get('next_manifest',0))
            if row['busy']:row['again']=True
        self.schedule()

    def receive(self, event, done):
        manifest=event.get('payload',{}).get('manifest_id')
        ident=self.manifests.get(manifest)
        if ident is None and manifest:
            row=self.reducer.store.db.execute('SELECT operation FROM channel_result_candidates WHERE manifest=? LIMIT 1',(manifest,)).fetchone()
            if row:ident=row[0]
        job=self.reducer.job(ident) if ident else None
        if not job or not self.eligible(job):done();return
        owner=self.identity(job)['owner']
        if any(event.get('scope',{}).get(k)!=owner[k] for k in ('library_id','workspace_id')):
            self.reducer.diagnostic('wrong result owner',event);done();return
        self.ensure(job,event=event,done=done,force=True)

    def schedule(self):
        if self.work:self.timer.stop();return
        due=[r['at'] for r in self.pending.values() if not r['busy']]
        if self.derived and not self.pending:due.append(self.derived_at)
        if due:self.timer.start(max(0,int((min(due)-time.monotonic())*1000)))
        else:self.timer.stop()

    def launch(self, ident, row, action, *, writer=False):
        row['busy']=True
        row['units']=row.get('units',0)+1
        work=Work(action,(self.generation,ident,row['phase'],row['cancel']))
        work.signals.completed.connect(self.completed)
        self.work.add(work)
        if writer:
            from .comfy_result_writer import pool
            pool().start(work)
        else:result_pool().start(work)

    def write_candidates(self, ident, row, packet, values=None):
        from . import comfy_result_writer as writer
        if self.writer_path is None:self.writer_path=writer.configure(self.reducer.store.db)
        phase='intent' if values is None else 'ready'
        self.reducer.store.checkpoint('before_candidate_'+phase+'_commit')
        row['phase']=phase+'_write';generation=self.generation;cancel=row['cancel']
        path=self.writer_path;identity=row['identity']
        self.launch(ident,row,lambda:writer.write(path,phase,packet,identity,ident,values,cancel,
                    lambda:generation==self.generation and not self.closed),writer=True)

    def tick(self):
        if self.client.stopped or not self.enabled:return
        # One bounded work unit per owner. Other owners have independent queues.
        if self.work:return
        for ident,row in tuple(self.pending.items()):
            if row['at']>time.monotonic():continue
            job=self.reducer.job(ident)
            if not job or self.identity(job)!=row['identity']:
                row['cancel'].set();self.pending.pop(ident,None);continue
            identity=dict(self.reducer.identity);expected=row['identity']
            reader_type,url,token=self.reader_type,self.client.url,self.client.token
            cancel=row['cancel']
            if row['phase']=='manifest':
                self.launch(ident,row,lambda:prepare_packet(reader_type(url,token,identity,expected['owner'],cancel).manifest(expected)))
            elif row['phase']=='projection':
                from .comfy_result_projection import prepare
                attempt=self.reducer.chain.store.read(ident)
                if attempt and attempt.get('kind')!='attempt':attempt=None
                run=self.reducer.chain.store.read(attempt['owner']) if attempt else None
                fence=self.reducer.store.db.execute('SELECT body,content_paths FROM stage_header WHERE id=?',(run['id'],)).fetchone() if run else None
                job_fence=self.reducer.store.db.execute('SELECT body FROM generation_jobs WHERE id=?',(ident,)).fetchone()
                directory=self.window.store.directory;assets=tuple(row['verified']);snapshot=dict(job)
                self.launch(ident,row,lambda:prepare(directory,snapshot,assets,attempt,run,fence,job_fence))
            else:
                packet=row['packet'];directory=self.window.store.directory
                # History is frozen; a bounded larger receipt batch avoids extra
                # writer round trips without changing per-original durability.
                limit=FINAL_BATCH if packet['header'].get('item_source')=='history' else FETCH_BATCH
                batch=row['remaining'][:limit]
                if 'cached' not in row:
                    receipts=(json.loads(receipt) for receipt, in self.reducer.store.db.execute(
                        "SELECT receipt FROM channel_result_candidates WHERE manifest=? AND state='ready'",(digest(expected),)))
                    row['cached']={receipt['asset_key']:receipt for receipt in receipts}
                cached=row['cached']
                def collect(items):
                    transport=reader_type(url,token,identity,expected['owner'],cancel)
                    # Pin this chunk to one canonical document root. Each reused
                    # path still resolves beneath it; atomic_file still checks
                    # every destination ancestor for symlink/junction redirects.
                    root=Path(directory).resolve()
                    values=[]
                    for item in items:
                        if cancel.is_set():raise ValueError('Result owner closed')
                        receipt,preview=asset(transport,packet,item,directory,cached.get(item['asset_key']),root=root)
                        values.append((item,receipt,preview))
                    return values
                for lane in range(min(PARALLEL,len(batch))):
                    chunk=batch[lane::PARALLEL]
                    self.launch(ident,row,lambda chunk=chunk:collect(chunk))
            return
        if self.derived and not self.pending and self.derived_at<=time.monotonic():
            values=list(self.derived.items())[:BATCH];directory=self.window.store.directory;cancel=self.cancel
            recovery={}
            for ident in {entry['operation'] for _,entry in values}:
                job=self.reducer.job(ident)
                if job and job['state']=='complete' and job.get('result_manifest',{}).get('sealed'):
                    recovery[ident]=(self.identity(job),copy.deepcopy(job['result_manifest']))
            reader_type,url,token=self.reader_type,self.client.url,self.client.token
            identity=copy.deepcopy(self.reducer.identity)
            def build(value):
                key,entry=value
                try:receipt,rebuilt=derivatives(directory,entry['receipt'],cancel)
                except OriginalUnavailable:
                    context=recovery.get(entry['operation'])
                    if context is None:raise
                    expected,header=context
                    reader=reader_type(url,token,identity,expected['owner'],cancel)
                    packet=reader.manifest(expected,reference=header)
                    item=next((item for item in packet['items'] if item['asset_key']==key),None)
                    if item is None:raise ValueError('Original is not in the accepted final manifest')
                    original,_=asset(reader,packet,item,directory)
                    receipt,_=derivatives(directory,original,cancel);rebuilt=True
                return key,entry,receipt,rebuilt
            self.deriving=dict(phase='derivatives',cancel=cancel)
            for lane in range(min(PARALLEL,len(values))):
                chunk=values[lane::PARALLEL]
                self.launch(None,self.deriving,lambda chunk=chunk:[build(value) for value in chunk])
        self.schedule()

    @Slot(object)
    def completed(self, outcome):
        work,(generation,ident,phase,cancel),value,error=outcome
        self.work.discard(work)
        if self.closed or self.client.stopped:return
        if generation!=self.generation or cancel.is_set():self.schedule();return
        row=self.deriving if phase=='derivatives' else self.pending.get(ident)
        if row is None or row['cancel'] is not cancel:self.tick();self.schedule();return
        row['units']-=1
        if error:row['work_error']=error
        else:row.setdefault('values',[]).extend([value] if phase in ('manifest','intent_write','projection') else value)
        if row['units']:return
        values=row.pop('values',[]);error=row.pop('work_error','')
        value=values[0] if phase in ('manifest','intent_write','projection') and values else values
        row['busy']=False
        if phase=='derivatives':
            try:
                if error:raise ValueError(error)
                with self.reducer.chain.accepting():
                    for key,entry,receipt,rebuilt in value:
                        raw=p.encode(receipt).decode()
                        for location in entry['locations']:
                            self.reducer.store.db.execute("UPDATE channel_result_candidates SET receipt=?,derived_pending=0 WHERE manifest=? AND source=? AND semantic=? AND state='ready' AND (receipt!=? OR derived_pending!=0)",(raw,*location,raw))
                        def published(key=key,entry=entry,receipt=receipt,rebuilt=rebuilt):
                            self.derived.pop(key,None)
                            for relative in entry.get('display_paths',()):self.display_checks[relative]=self.display_stamp(relative)
                            if rebuilt and entry.get('display_paths'):self.repaired.emit(receipt['source']['relative'])
                        self.reducer.chain.store._transactions.after_commit(published)
            except Exception:
                self.derived_at=time.monotonic()+30
            self.tick();self.schedule();return
        try:
            # A sibling chunk may have failed after these originals reached
            # durable storage. Preserve verified receipts for its retry.
            if phase=='assets' and value:
                row.setdefault('cached',{}).update((item['asset_key'],receipt) for item,receipt,_ in value)
            if error:raise ValueError(error)
            if phase in ('manifest','intent_write'):self.intent(ident,row,value,written=phase=='intent_write')
            elif phase=='projection':
                row['prepared']=value
                self.finished(ident,row)
            else:
                value.sort(key=lambda v:v[0]['ordinal'])
                self.ready(ident,row,value,written=phase=='ready_write')
                job=self.reducer.job(ident)
                if (not row['busy'] and ident in self.pending and row['packet']['header'].get('item_source')=='candidates' and
                        job and job.get('result_terminal')=='success'):
                    row.update(phase='manifest',at=time.monotonic())
            row['tries']=0
        except (ValueError,OSError,KeyError,TypeError) as exc:self.retry(row,exc)
        except Exception as exc:
            import sqlite3
            if not isinstance(exc,sqlite3.Error):raise
            self.retry(row,exc)
        self.tick();self.schedule()

    def retry(self, row, error):
        job=self.reducer.job(row['identity']['operation']['id'])
        if job and job['state'] in ('failed','cancelled','interrupted') and job.get('result_terminal')!='success':
            self.pending.pop(job['id'],None);row['cancel'].set();return
        row['tries']+=1;row['error']=str(error)
        # Re-read leases/immutable intent on any failure. No submit route exists.
        row.update(phase='manifest',at=time.monotonic()+min(30,2**min(row['tries']-1,5)))

    def intent(self, ident, row, packet, *, written=False):
        header=packet['header'];check_header(header,row['identity'])
        job=self.reducer.job(ident)
        if not job or not self.eligible(job) or self.identity(job)!=row['identity']:raise ValueError('Stale result owner')
        previous=job.get('result_manifest')
        if previous and (header['revision']<previous['revision'] or
                header['revision']==previous['revision'] and header['checksum']!=previous['checksum']):
            raise ValueError('Conflicting result revision')
        source=header.get('item_source','candidates');manifest=header['manifest_id']
        events=list(row['events']);changed=previous!=header
        old_packet=row.get('packet')
        if old_packet and old_packet['header']==header and not events:
            row['packet']=packet  # Refresh expiring asset leases on retry.
            if not row['remaining']:self.finished(ident,row)
            else:row.update(phase='assets',at=time.monotonic())
            return
        retained={old_packet['records'][item['ordinal']][0]:(item,receipt) for item,receipt in row['verified']} if (
            old_packet and old_packet['header'].get('item_source')==header.get('item_source')) else {}
        records=packet['records']
        if not written:
            self.write_candidates(ident,row,packet);return
        with self.reducer.chain.accepting():
            db=self.reducer.store.db
            if changed:
                job=dict(job,result_manifest=header)
                updated=db.execute("UPDATE generation_jobs SET body=json_set(body,'$.result_manifest',json(?)) WHERE id=?",
                                   (packet['header_json'],ident))
                if not updated.rowcount:self.client.generation._write_job(job)
            for event,_ in events:
                if not self.reducer.store.contains(event):self.reducer.store.accept(event,'result_intent')
            def publish():
                if changed and not job.get('queue_job'):self.client.generation.jobs[ident]=job
                row['events']=row['events'][len(events):]
                for _,done in events:
                    if done:done()
            self.reducer.chain.store._transactions.after_commit(publish)
        if job['state'] in ('failed','cancelled','interrupted') and job.get('result_terminal')!='success':
            # A new notice may refresh the immutable intent once. It does not
            # restart cancelled copies or create another polling deadline.
            self.pending.pop(ident,None);row['cancel'].set();return
        verified=[retained[records[item['ordinal']][0]] for item in packet['items'] if records[item['ordinal']][0] in retained and retained[records[item['ordinal']][0]][0]==item]
        keys={item['ordinal'] for item,_ in verified}
        row.update(packet=packet,remaining=[item for item in packet['items'] if item['ordinal'] not in keys],
                   verified=verified,phase='assets',at=time.monotonic())
        if source=='candidates' and packet['items']:
            row['next_manifest']=time.monotonic()+CANDIDATE_INTERVAL
        if header.get('sealed') and not job.get('result_terminal') and job['state'] not in ('failed','retired','complete'):
            self.reducer.reconcile.require(ident,'manifest_success',force=True)
        if not row['remaining']:self.finished(ident,row)

    def ready(self, ident, row, values, *, written=False):
        header=row['packet']['header'];source=header.get('item_source','candidates')
        if not written:
            self.write_candidates(ident,row,row['packet'],values);return
        # The writer emitted this summary only after its durable COMMIT.
        row['verified'].extend((item,receipt) for item,receipt,_ in values)
        row.setdefault('cached',{}).update((item['asset_key'],receipt) for item,receipt,_ in values)
        row['verified'].sort(key=lambda value:value[0]['ordinal'])
        del row['remaining'][:len(values)]
        job=self.reducer.job(ident)
        if source=='history' and job and job['state'] not in ('failed','cancelled','interrupted'):
            for item,receipt,_ in values:
                if not receipt.get('derived'):self.queue_derivatives(ident,receipt,(header['manifest_id'],source,row['packet']['records'][item['ordinal']][0]))
        row['phase']='assets'
        row['at']=time.monotonic()
        if not row['remaining']:self.finished(ident,row)

    def finished(self, ident, row):
        job=self.reducer.job(ident);header=row['packet']['header']
        if not job:return
        if row['events']:
            row.update(phase='manifest',at=time.monotonic())
            if job.get('result_terminal')!='success' and not row['tries']:
                row['at']=max(row['at'],row.get('next_manifest',0))
            return
        if header.get('sealed') and job.get('result_terminal')=='success' and job['state']=='results_pending':
            if 'prepared' not in row:
                row.update(phase='projection',at=time.monotonic());return
            self.reducer.result_ready(job,header,row['verified'],row.pop('prepared'))
            if self.reducer.job(ident)['state']=='complete':
                self.pending.pop(ident,None)
                self.graphs.pop(ident,None)
            else:
                row.update(phase='manifest',at=time.monotonic()+1)
        elif job['state'] in ('complete','retired') or job['state'] in ('failed','cancelled','interrupted') and job.get('result_terminal')!='success':
            self.pending.pop(ident,None)
        else:
            row.update(phase='manifest',at=time.monotonic()+ (0 if row.pop('again',False) else 5))
            if job.get('result_terminal')!='success' and not row['tries']:
                row['at']=max(row['at'],row.get('next_manifest',0))

    def queue_derivatives(self, ident, receipt, location):
        key=receipt['asset_key']
        # Receipts are transferred, never mutated; derivatives returns a new one.
        entry=self.derived.setdefault(key,dict(operation=ident,receipt=receipt,locations=set()))
        entry['locations'].add(location)
        self.derived_at=0

    def recover_derivatives(self):
        # The partial index contains only the crash window after frame commit
        # and before derivative receipt. Completed historical bytes stay cold.
        for manifest,source,key,ident,raw in self.reducer.store.db.execute(
                "SELECT manifest,source,semantic,operation,receipt FROM channel_result_candidates INDEXED BY channel_candidate_deriving WHERE derived_pending=1"):
            job=self.reducer.job(ident)
            if job and job['state']=='complete':self.queue_derivatives(ident,json.loads(raw),(manifest,source,key))
        self.refresh_stopped(epoch_only=not self.refresh_requested)
        self.refresh_requested=False
        self.schedule()

    def refresh_stopped(self, *, epoch_only=False):
        if not self.enabled:return
        # Use the existing unfinished owner index, not retained candidate rows.
        # A new owned manifest event can still request one check after closure.
        for job in self.reducer.active():
            if job and job['state'] in ('failed','cancelled','interrupted') and job.get('result_terminal')!='success':
                if not epoch_only or job.get('result_manifest',{}).get('source_epoch')!=self.reducer.identity.get('epoch'):
                    self.ensure(job,force=True)

    def display_stamp(self, relative):
        try:
            info=(self.window.store.directory/relative).stat()
            return info.st_size,info.st_mtime_ns,info.st_ctime_ns
        except OSError:return None

    def request_repair(self, relative, *, failed=False):
        """Single displayed asset only; all byte/hash checks stay in workers.

        P6 may call the same entry point after its asynchronous decoder fails.
        Existing thumbnail display also requests validation on a changed file.
        """
        if self.closed or not self.enabled or not isinstance(relative,str):return
        path=Path(relative)
        if path.parent.as_posix() not in ('thumbnails/results','originals/generation'):return
        sha=path.stem
        if len(sha)!=64 or any(c not in '0123456789abcdef' for c in sha):return
        stamp=self.display_stamp(relative)
        if not failed and relative in self.display_checks and self.display_checks[relative]==stamp:return
        row=self.reducer.store.db.execute("SELECT manifest,source,semantic,operation,receipt FROM channel_result_candidates INDEXED BY channel_candidate_asset WHERE source='history' AND state='ready' AND json_extract(receipt,'$.source.sha256')=? LIMIT 1",(sha,)).fetchone()
        if row is None:return
        manifest,source,key,ident,raw=row
        job=self.reducer.job(ident)
        if not job or job['state']!='complete':return
        receipt=json.loads(raw)
        self.queue_derivatives(ident,receipt,(manifest,source,key))
        self.derived[receipt['asset_key']].setdefault('display_paths',set()).add(relative)
        self.display_checks[relative]=stamp
        self.schedule()

    def health(self, health):
        if not self.enabled:return
        healthy=not health.get('needs_reconcile') and health.get('state')=='receiving'
        if healthy and not self.healthy:self.refresh_stopped()
        self.healthy=healthy

    def heartbeat(self):
        if not self.enabled:return
        if not self.healthy:self.healthy=True;self.refresh_stopped()

    def stop_operation(self, ident):
        self.suspended.add(ident)
        row=self.pending.pop(ident,None)
        if row:row['cancel'].set()
        self.cancel.set();self.cancel=Cancellation()
        self.derived={key:value for key,value in self.derived.items() if value['operation']!=ident}
        self.schedule()

    def preview(self, key):
        # Keep the existing projection hook; only accepted final results display.
        return None

    def awaiting(self, key):
        if not self.enabled:return False
        from .flow_data import incoming
        state=self.window.state;stages=state['multi_output'].get('stages',{});seen=set()
        while key not in stages:
            if not key or key in seen:return False
            seen.add(key)
            key=incoming(state,key,'image') or state.get('canvas_functions',{}).get('images',{}).get(key,{}).get('stage_reference')
        return any(self.reducer.job(ident).get('state')=='results_pending' and
            self.reducer.store.db.execute("SELECT 1 FROM stage_header WHERE id=? AND json_extract(body,'$.stage')=?",(ident,key)).fetchone()
            for ident in self.pending if self.reducer.job(ident))

    def notify(self):
        if self.client.stopped:return
        document=getattr(self.window,'document',None)
        hosts=document.hosts if document is not None else [self.window]
        for host in hosts:
            canvas=getattr(host,'canvas',None)
            if canvas is not None:canvas.refresh_result_previews()

    def reset(self):
        self.closed=False
        self.generation+=1;self.cancel.set();self.cancel=Cancellation()
        for row in self.pending.values():row['cancel'].set()
        self.timer.stop();self.notice.stop();self.pending.clear();self.manifests.clear();self.derived.clear();self.suspended.clear();self.display_checks.clear();self.graphs.clear();self.healthy=True

    def close(self):
        self.reset();self.closed=True
