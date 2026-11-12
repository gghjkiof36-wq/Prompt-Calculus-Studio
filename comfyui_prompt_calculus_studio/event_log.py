"""Transactional receipt outbox and bounded, off-executor native ingestion.

SQL triggers capture *all* source-table mutations. Service.connect drains them
through append before committing the same connection. A failed append therefore
rolls back the receipt too. The native queue is an external system: after it has
accepted work a DB failure is degraded health, never permission to submit again.
"""
import copy
import json
import sqlite3
import sys
import threading
import time
import uuid
from collections import OrderedDict, deque
from contextlib import contextmanager
from . import event_protocol as p

if not hasattr(sys, '_pcs_event_process_instance'):
    sys._pcs_event_process_instance = uuid.uuid4().hex
PROCESS_INSTANCE = sys._pcs_event_process_instance


class EventLog:
    def __init__(self, directory, *, clock=time.time):
        self.path = directory / 'integration.sqlite3'
        self.clock = clock
        self.epoch = uuid.uuid4().hex
        self.condition = threading.Condition()
        self.consistent = True
        self.failure = ''
        self.maintenance_error = None
        self._loss_lock = threading.Lock()  # Never held during IO or notification.
        self._recovery_lock = threading.Lock()
        self._pending_loss = None
        self._loss_version = 0
        self._retry_at = 0.0
        self.retry_seconds = 1.0
        self.waiters = threading.BoundedSemaphore(8)
        self.max_count, self.max_bytes, self.max_age = p.RETENTION_COUNT, p.RETENTION_BYTES, p.RETENTION_SECONDS
        self.fault = None  # Test seam, invoked inside the receipt transaction.
        with self.connection() as db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS event_meta (id INTEGER PRIMARY KEY CHECK(id=1),
                    server TEXT NOT NULL, epoch TEXT NOT NULL, latest INTEGER NOT NULL,
                    consistent INTEGER NOT NULL, reason TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS event_log (seq INTEGER PRIMARY KEY, created REAL NOT NULL,
                    size INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_log_age ON event_log(created);
                CREATE TABLE IF NOT EXISTS event_usage (id INTEGER PRIMARY KEY, count INTEGER NOT NULL, bytes INTEGER NOT NULL);
                INSERT OR IGNORE INTO event_usage VALUES (1,0,0);
                CREATE TRIGGER IF NOT EXISTS event_usage_insert AFTER INSERT ON event_log
                    BEGIN UPDATE event_usage SET count=count+1,bytes=bytes+NEW.size WHERE id=1; END;
                CREATE TRIGGER IF NOT EXISTS event_usage_delete AFTER DELETE ON event_log
                    BEGIN UPDATE event_usage SET count=count-1,bytes=bytes-OLD.size WHERE id=1; END;
                CREATE TABLE IF NOT EXISTS event_changes (n INTEGER PRIMARY KEY AUTOINCREMENT,
                    source TEXT NOT NULL, ident TEXT NOT NULL, body TEXT NOT NULL, deleted INTEGER NOT NULL);
                CREATE TABLE IF NOT EXISTS event_receipts (entity TEXT PRIMARY KEY, prompt TEXT NOT NULL,
                    digest TEXT NOT NULL, revision INTEGER NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS event_receipt_prompt ON event_receipts(prompt);
                CREATE INDEX IF NOT EXISTS event_native_projection_seq ON event_receipts(CAST(json_extract(body,'$.seq') AS INTEGER)) WHERE substr(entity,1,10)='execution:';
                CREATE TABLE IF NOT EXISTS event_fences (entity TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS event_snapshots (id TEXT PRIMARY KEY, epoch TEXT NOT NULL,
                    digest TEXT NOT NULL, as_of INTEGER NOT NULL, expires REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS event_snapshot_items (snapshot TEXT NOT NULL, entity TEXT NOT NULL,
                    body TEXT NOT NULL, PRIMARY KEY(snapshot,entity));
            ''')
            db.execute('BEGIN IMMEDIATE')
            old = db.execute('SELECT server,consistent,reason,epoch FROM event_meta WHERE id=1').fetchone()
            self.server_instance = old[0] if old else uuid.uuid4().hex
            db.execute('INSERT OR REPLACE INTO event_meta VALUES (1,?,?,0,1,\'\')',
                       (self.server_instance, self.epoch))
            # An old epoch is never replayed as the new epoch. Receipts/fences
            # survive; the new consumer must begin with an as-of snapshot.
            for table in ('event_log', 'event_changes', 'event_snapshots', 'event_snapshot_items'):
                db.execute('DELETE FROM '+table)
            db.execute("DELETE FROM event_receipts WHERE substr(entity,1,10)='execution:'")
            if old and not old[1]:
                # New epochs always require a snapshot and start consistent.
                # Carry unresolved loss as their FIRST event, atomically with
                # the epoch switch. Startup failure leaves the old marker intact.
                try: loss = json.loads(old[2])
                except (ValueError, TypeError): loss = None
                if not isinstance(loss, dict):
                    loss = dict(reason=old[2], occurred_at=self.clock())
                self.append(db, p.LOG_GAP, dict(loss, previous_epoch=old[3]), entity='log:gap')

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.create_function('pcs_event_epoch', 0, lambda: self.epoch)
        try:
            with db: yield db
        finally: db.close()

    def configure_connection(self, db):
        db.create_function('pcs_event_epoch', 0, lambda: self.epoch)

    def watch(self, db, *tables):
        """Install transactional guards before a module's first startup write."""
        for table in tables:
            key = p.WRITERS[table]
            columns = [row[1] for row in db.execute('PRAGMA table_info('+table+')')]
            for action in ('INSERT', 'UPDATE', 'DELETE'):
                ref = 'OLD' if action == 'DELETE' else 'NEW'
                row = 'json_object('+','.join("'"+c+"',"+ref+'.'+c for c in columns)+')'
                # Identifiers come exclusively from the fixed WRITERS registry.
                db.execute(f'''CREATE TRIGGER IF NOT EXISTS pcs_event_{table}_{action}
                    AFTER {action} ON {table} BEGIN
                    SELECT CASE WHEN pcs_event_epoch() != (SELECT epoch FROM event_meta WHERE id=1)
                        THEN RAISE(ABORT,'stale event publisher') END;
                    INSERT INTO event_changes(source,ident,body,deleted)
                        VALUES ('{table}',{ref}.{key},{row},{int(action == 'DELETE')}); END''')
            if table in ('native_operations', 'queue_attempts'):
                for action in ('INSERT', 'UPDATE'):
                    db.execute(f'''CREATE TRIGGER IF NOT EXISTS pcs_fence_{table}_{action}
                        BEFORE {action} ON {table}
                        WHEN EXISTS(SELECT 1 FROM event_fences WHERE entity='{table}:'||NEW.{key})
                        AND json_extract(NEW.body,'$.state') NOT IN ('failed','unconfirmed','retired')
                        AND COALESCE(json_extract(NEW.body,'$.queue_terminal'),0)=0
                        BEGIN SELECT RAISE(ABORT,'operation is fenced'); END''')
            # Upgrade existing receipts once, including never-mutated records.
            for ident, raw in db.execute(f'SELECT {key},'+('body' if 'body' in columns else "'{}'")+f' FROM {table}').fetchall():
                if not db.execute('SELECT 1 FROM event_receipts WHERE entity=?', (table+':'+ident,)).fetchone():
                    values = dict(zip(columns, db.execute(f'SELECT * FROM {table} WHERE {key}=?', (ident,)).fetchone()))
                    db.execute('INSERT INTO event_changes(source,ident,body,deleted) VALUES (?,?,?,0)',
                               (table, ident, json.dumps(values)))

    def drain_changes(self, db):
        for number, table, ident, raw, deleted in db.execute('SELECT n,source,ident,body,deleted FROM event_changes ORDER BY n').fetchall():
            row = json.loads(raw)
            value = json.loads(row['body']) if 'body' in row else row
            if table in ('input_bridge','input_requests'):value=dict(value,client=row['client'])
            if table == 'workflow_transfers': value = dict(row, body_sha256=__import__('hashlib').sha256(row['body'].encode()).hexdigest()); value.pop('body')
            if table == 'input_bridge': value = dict(value); value.pop('seen', None)
            kind, payload, scope = p.project(table, ident, value, bool(deleted))
            entity = table+':'+ident
            old = db.execute('SELECT body,digest FROM event_receipts WHERE entity=?', (entity,)).fetchone()
            if old and old[1] == payload['receipt_sha256'] and not deleted:
                db.execute('DELETE FROM event_changes WHERE n=?', (number,)); continue
            if old:
                previous_scope = json.loads(old[0])['scope']
                for name in ('library_id', 'workspace_id'):
                    if name in previous_scope:
                        if name in scope and scope[name] != previous_scope[name]:
                            raise ValueError('Receipt ownership changed')
                        scope[name] = previous_scope[name]
            # Diagnostic/job records inherit frozen ownership, never the current config.
            if table == 'native_diagnostics': related = 'native_operations:'+ident
            elif table == 'jobs': related = 'native_operations:'+str(value.get('generation', {}).get('native_operation', ''))
            elif table == 'queue_attempts': related = 'queue_captures:'+str(value.get('capture', ''))
            elif table == 'background_operations': related = 'background_states:'+str(value.get('key_hash', ''))
            else: related = ''
            if related:
                parent = db.execute('SELECT body FROM event_receipts WHERE entity=?', (related,)).fetchone()
                if parent: scope = json.loads(parent[0])['scope']
            self.append(db, kind, payload, entity=entity, scope=scope,
                        prompt_id=value.get('prompt_id') or (ident if table == 'jobs' else ''),
                        digest=payload['receipt_sha256'])
            if value.get('missing_fence') or value.get('abandoned') or value.get('cancel_confirmed'):
                db.execute('INSERT OR REPLACE INTO event_fences VALUES (?,?)', (entity, p.encode(payload).decode()))
            db.execute('DELETE FROM event_changes WHERE n=?', (number,))

    def append(self, db, kind, payload, *, entity, scope=None, prompt_id='', digest=''):
        """The only append entry, shared by SQL receipts and the native worker."""
        if len(p.encode(payload)) > p.MAX_PAYLOAD_BYTES: raise ValueError('Oversized event payload')
        current = db.execute('SELECT epoch,latest FROM event_meta WHERE id=1').fetchone()
        if current[0] != self.epoch: raise ValueError('Stale event publisher')
        seq = current[1] + 1
        old = db.execute('SELECT revision FROM event_receipts WHERE entity=?', (entity,)).fetchone()
        revision = old[0] + 1 if old else 1
        event = dict(schema=p.SCHEMA, server_instance=self.server_instance, epoch=self.epoch,
            seq=str(seq), kind=kind, entity=entity, scope=scope or p.scope(), prompt_id=prompt_id or '',
            receipt_revision=revision, created_at=self.clock(), payload=payload)
        raw = p.encode(event)
        # Payload alone is not the whole page: source identifiers/scope can be
        # large too. A persisted event must always fit into an otherwise empty
        # events or snapshot response (including its control envelope).
        # Snapshot requests repeat the previous and next entity in the page
        # envelope. Bound those cursors as well as the encoded event itself.
        if len(p.encode(entity)) > 1024 or len(raw) > p.MAX_PAGE_BYTES-4096:
            raise sqlite3.DataError('Event envelope exceeds a page')
        if self.fault: self.fault(event)
        db.execute('INSERT INTO event_log VALUES (?,?,?,?)', (seq, self.clock(), len(raw), raw.decode()))
        db.execute('UPDATE event_meta SET latest=? WHERE id=1', (seq,))
        db.execute('INSERT OR REPLACE INTO event_receipts VALUES (?,?,?,?,?)',
                   (entity, prompt_id or '', digest, revision, raw.decode()))
        self.prune(db)
        return event

    def prune(self, db):
        # Wall-clock corrections must not punch holes behind oldest_seq.
        # Expiration always removes a sequence prefix, even if creation times
        # went backwards; lagging readers then receive reset_required.
        expired = db.execute('SELECT MAX(seq) FROM event_log WHERE created<?',
                             (self.clock()-self.max_age,)).fetchone()[0]
        if expired is not None: db.execute('DELETE FROM event_log WHERE seq<=?', (expired,))
        count, size = db.execute('SELECT count,bytes FROM event_usage WHERE id=1').fetchone()
        if count > self.max_count or size > self.max_bytes:
            for seq, length in db.execute('SELECT seq,size FROM event_log ORDER BY seq'):
                if count <= self.max_count and size <= self.max_bytes: break
                db.execute('DELETE FROM event_log WHERE seq=?', (seq,)); count -= 1; size -= length
        floor=db.execute('SELECT MIN(seq) FROM event_log').fetchone()[0]
        if floor is None:floor=db.execute('SELECT latest+1 FROM event_meta WHERE id=1').fetchone()[0]
        # Native projections are rebuildable from queue/history; they must not
        # become an unbounded second copy of the retained event stream.
        db.execute("DELETE FROM event_receipts WHERE substr(entity,1,10)='execution:' AND CAST(json_extract(body,'$.seq') AS INTEGER)<?",(floor,))

    def committed(self):
        with self.condition: self.condition.notify_all()

    def record_loss(self, reason, *, prompt_id='', entity='', persist=True):
        """Only actual discarded native evidence or an accepted/unjournaled job.

        The send_sync hook uses persist=False: bounded memory work, no disk IO.
        Pending loss is coalesced, not an unbounded second ingestion queue.
        """
        now = self.clock()
        with self._loss_lock:
            if self._pending_loss is None:
                self._pending_loss = dict(reason=str(reason)[:200], occurred_at=now,
                                          last_occurred_at=now, count=0, affected=[])
            loss = self._pending_loss
            loss['last_occurred_at'] = now
            loss['count'] += 1
            # Even 16 pairs of four-byte Unicode identifiers must fit 64KiB.
            affected = {k: v for k, v in (('prompt_id', prompt_id), ('entity', entity))
                        if isinstance(v, str) and 0 < len(v) <= 256}
            if any(isinstance(v, str) and len(v) > 256 for v in (prompt_id, entity)):
                loss['affected_truncated'] = True
            if affected and affected not in loss['affected']:
                if len(loss['affected']) < 16: loss['affected'].append(affected)
                else: loss['affected_truncated'] = True
            self._loss_version += 1
            self.consistent, self.failure = False, loss['reason']
        if persist: self._flush_loss(recover=False)

    def recover_gap(self):
        """Retry at most once/second, independently of the 50ms ingestion tick."""
        return self._flush_loss(recover=True)

    def _flush_loss(self, *, recover):
        if not self._recovery_lock.acquire(blocking=False): return False
        committed = False
        try:
            if time.monotonic() < self._retry_at: return False
            with self._loss_lock:
                if self._pending_loss is None: return False
                loss, version = copy.deepcopy(self._pending_loss), self._loss_version
            # A separate commit keeps false durable even when gap append fails.
            try:
                with self.connection() as db:
                    db.execute('UPDATE event_meta SET consistent=0,reason=? WHERE epoch=?',
                               (p.encode(loss).decode(), self.epoch))
                if recover:
                    with self.connection() as db:
                        db.execute('BEGIN IMMEDIATE')
                        self.append(db, p.LOG_GAP, loss, entity='log:gap')
                        db.execute("UPDATE event_meta SET consistent=1,reason='' WHERE epoch=?", (self.epoch,))
                    committed = True
                    with self._loss_lock:
                        # A loss arriving during IO still needs its own gap.
                        if self._loss_version == version:
                            self._pending_loss = None
                            self.consistent, self.failure = True, ''
            except (sqlite3.Error, OSError, ValueError):
                pass  # Retain the marker and retry; never replay a submission.
            finally:
                self._retry_at = time.monotonic()+self.retry_seconds
        finally:
            self._recovery_lock.release()
        if committed: self.committed()
        return committed

    def check(self, identity, requested_scope, digest):
        if identity != (self.server_instance, self.epoch): raise ValueError('Wrong event server or epoch')
        requested_scope = p.scope(requested_scope)
        if digest != p.scope_digest(requested_scope): raise ValueError('Wrong event scope digest')
        return requested_scope

    def header(self, db, requested_scope):
        epoch, latest, consistent = db.execute('SELECT epoch,latest,consistent FROM event_meta WHERE id=1').fetchone()
        if epoch != self.epoch: raise ValueError('Stale event publisher')
        floor = db.execute('SELECT MIN(seq) FROM event_log').fetchone()[0]
        return dict(schema=p.SCHEMA, server_instance=self.server_instance, epoch=epoch,
                    latest_seq=str(latest), oldest_seq=str(floor if floor is not None else latest+1),
                    scope_digest=p.scope_digest(requested_scope), log_consistent=bool(consistent) and self.consistent)

    def page(self, since, requested_scope):
        start = p.cursor(since)
        with self.connection() as db:
            db.execute('BEGIN')
            page = dict(self.header(db, requested_scope), from_cursor=since, covered_to=since, events=[])
            if start > int(page['latest_seq']): raise ValueError('Future event cursor')
            if start < int(page['oldest_seq'])-1:
                return dict(page, reset_required=True)
            for seq, raw in db.execute('SELECT seq,body FROM event_log WHERE seq>? ORDER BY seq', (start,)):
                item = json.loads(raw)
                if p.matches_scope(item, requested_scope):
                    if len(page['events']) == p.MAX_EVENTS: break
                    page['events'].append(item)
                    if len(p.encode(dict(page, covered_to=str(seq)))) > p.MAX_PAGE_BYTES:
                        page['events'].pop(); break
                page['covered_to'] = str(seq)
            return page

    def events(self, since, wait, identity, requested_scope, digest):
        requested_scope = self.check(identity, requested_scope, digest)
        if type(wait) not in (int, float) or not 0 <= wait <= p.MAX_WAIT: raise ValueError('Invalid event wait')
        if not self.waiters.acquire(blocking=False): raise ValueError('Too many event waiters')
        try:
            end = time.monotonic()+wait
            # Condition covers read+wait, so a commit cannot be lost between them.
            with self.condition:
                while True:
                    page = self.page(since, requested_scope)
                    remaining = end-time.monotonic()
                    if page.get('reset_required') or page['covered_to'] != since or remaining <= 0:
                        return page
                    self.condition.wait(remaining)
        finally: self.waiters.release()

    def heartbeat(self, running, requested_scope=None):
        requested_scope = p.scope(requested_scope)
        publisher=getattr(self,'publisher',None)
        alive=None if publisher is None else publisher.healthy()
        with self.connection() as db:
            header = self.header(db, requested_scope)
            if requested_scope != p.scope():
                allowed = {prompt for prompt, raw in db.execute('SELECT prompt,body FROM event_receipts') if p.matches_scope(json.loads(raw), requested_scope)}
                running = [v for v in running if v in allowed] if running is not None else None
        return dict(header, running=running, queue_readable=running is not None,
                    publisher_alive=alive, maintenance_error=self.maintenance_error, observed_at=self.clock(),
                    evidence_seconds=p.EVIDENCE_SECONDS, diagnostic_seconds=p.DIAGNOSTIC_SECONDS)

    def snapshot(self, identity, requested_scope, digest, token='', after=''):
        requested_scope = self.check(identity, requested_scope, digest)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute('DELETE FROM event_snapshot_items WHERE snapshot IN (SELECT id FROM event_snapshots WHERE expires<?)', (self.clock(),))
            db.execute('DELETE FROM event_snapshots WHERE expires<?', (self.clock(),))
            if not token:
                if after: raise ValueError('Snapshot cursor without token')
                if db.execute('SELECT COUNT(*) FROM event_snapshots').fetchone()[0] >= 4: raise ValueError('Snapshot capacity reached')
                header = self.header(db, requested_scope)
                token = uuid.uuid4().hex
                db.execute('INSERT INTO event_snapshots VALUES (?,?,?,?,?)',
                           (token, self.epoch, digest, int(header['latest_seq']), self.clock()+60))
                for entity, raw in db.execute('SELECT entity,body FROM event_receipts').fetchall():
                    if p.matches_scope(json.loads(raw), requested_scope):
                        db.execute('INSERT INTO event_snapshot_items VALUES (?,?,?)', (token, entity, raw))
            record = db.execute('SELECT epoch,digest,as_of FROM event_snapshots WHERE id=?', (token,)).fetchone()
            if not record or record[:2] != (self.epoch, digest): raise ValueError('Expired or wrong-scope snapshot')
            if after and not db.execute('SELECT 1 FROM event_snapshot_items WHERE snapshot=? AND entity=?', (token, after)).fetchone():
                raise ValueError('Invalid snapshot cursor')
            page = dict(self.header(db, requested_scope), snapshot_token=token,
                        as_of_seq=str(record[2]), after=after, next_after='', done=True, receipts=[])
            for entity, raw in db.execute('SELECT entity,body FROM event_snapshot_items WHERE snapshot=? AND entity>? ORDER BY entity', (token, after)):
                if len(page['receipts']) == p.MAX_EVENTS:
                    page['done'] = False; break
                page['receipts'].append(json.loads(raw))
                if len(p.encode(dict(page, next_after=entity))) > p.MAX_PAGE_BYTES:
                    page['receipts'].pop(); page['done'] = False; break
                page['next_after'] = entity
            # Keep the last page repeatable until expiry, too.
            return page


class NativeEventBuffer:
    """One daemon per installed hook, never started by a bare test Service."""
    def __init__(self, service, queue=None, *, capacity=1024):
        self.service, self.queue, self.capacity = service, queue, capacity
        self.items, self.progress = deque(), OrderedDict()
        self.lock = threading.Lock()
        self.stop_event = threading.Event()
        self.thread = None
        self.last_progress = {}
        self.last_cycle=time.monotonic()
        service.events.publisher=self

    def healthy(self):
        if self.thread is None:return None
        return self.thread.is_alive() and time.monotonic()-self.last_cycle < 12

    def observe(self, event, data, sid=None):
        if event not in p.NATIVE_EVENTS or not isinstance(data, dict): return
        try:
            # Bounded structural copy; no serialization, SQLite, queue mutex or
            # disk access while the executor is calling send_sync.
            budget = [0]
            def clone(value, depth=0):
                budget[0] += len(value) if isinstance(value, (str, bytes)) else 16
                if budget[0] > 128*1024 or depth > 16: raise ValueError('native payload needs history')
                if isinstance(value, dict): return {clone(str(k), depth+1): clone(v, depth+1) for k, v in value.items()}
                if isinstance(value, (list, tuple)): return [clone(v, depth+1) for v in value]
                if value is None or type(value) in (str, bool, int, float): return value
                raise ValueError('non-JSON native event')
            try: payload = clone(data)
            except ValueError:
                def identity(name):
                    value=data.get(name)
                    return value if isinstance(value,str) and len(value)<=1024 else None
                payload = dict(prompt_id=identity('prompt_id'), node=identity('node'), fetch_required=True)
            item = (event, payload)
            with self.lock:
                key = str(payload.get('prompt_id', ''))
                coalesced = event in ('progress', 'progress_state')
                if len(self.items)+len(self.progress) >= self.capacity and not (coalesced and key in self.progress):
                    self.service.events.record_loss('native buffer overflow', prompt_id=key, persist=False); return
                if coalesced:
                    self.progress[key] = item
                else:
                    if event in p.TERMINAL_EVENTS:self.progress.pop(key,None)
                    self.items.append(item)
        except Exception:
            self.service.events.record_loss('native ingestion failure', prompt_id=data.get('prompt_id'), persist=False)

    def drain(self):
        now = time.monotonic()
        with self.lock:
            items = list(self.items); self.items.clear()
            for key, item in list(self.progress.items()):
                if now-self.last_progress.get(key, -1) >= .2:
                    items.append(item); del self.progress[key]; self.last_progress[key] = now
            # Bound coalescing metadata even over months of different prompts.
            self.last_progress = {k: v for k, v in self.last_progress.items() if now-v < 120}
        if not items: return
        try:
            with self.service.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                for event, data in items:
                    prompt = data.get('prompt_id')
                    prompt = prompt if isinstance(prompt, str) else ''
                    owner = db.execute("SELECT body FROM event_receipts WHERE prompt=? AND entity NOT LIKE 'execution:%' LIMIT 1", (prompt,)).fetchone() if prompt else None
                    scope = json.loads(owner[0])['scope'] if owner else p.scope()
                    entity = 'execution:'+prompt+':'+event+':'+str(data.get('node', ''))
                    self.service.events.append(db, 'native.'+event, p.native_payload(event, data),
                                               entity=entity, scope=scope, prompt_id=prompt)
                    if event == 'execution_interrupted' and prompt:
                        for table in ('native_operations','queue_attempts'):
                            for ident, raw in db.execute("SELECT id,body FROM "+table+" WHERE json_extract(body,'$.prompt_id')=?", (prompt,)).fetchall():
                                operation = json.loads(raw)
                                if operation.get('cancel_requested') and not operation.get('cancel_confirmed'):
                                    operation.update(cancel_confirmed=True, queue_terminal=True, terminal_action='cancel')
                                    db.execute('UPDATE '+table+' SET body=? WHERE id=?', (json.dumps(operation), ident))
        except Exception as exc:
            for event, data in items:
                self.service.events.record_loss('native append: '+str(exc),
                    prompt_id=data.get('prompt_id'), persist=False)
            self.service.events._flush_loss(recover=False)

    def start(self):
        if self.thread: return
        def run():
            maintenance = 0
            while not self.stop_event.wait(.05):
                self.drain()
                self.last_cycle=time.monotonic()
                self.service.events.recover_gap()
                if time.monotonic()-maintenance >= 1:
                    maintenance = time.monotonic()
                    try:
                        self.service.maintain_events(self.queue)
                        self.service.events.maintenance_error = None
                    except Exception as exc:
                        self.service.events.maintenance_error = dict(message=str(exc)[:200], observed_at=self.service.events.clock())
        self.thread = threading.Thread(target=run, name='pcs-event-writer', daemon=True)
        self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread: self.thread.join(timeout=12)
