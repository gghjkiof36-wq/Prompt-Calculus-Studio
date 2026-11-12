"""Durable shadow inbox. This module never opens a document or runs a reducer."""
import json
from pathlib import Path
import sqlite3
import time

from . import comfy_event_protocol as p

FILENAME = 'comfy-channel.sqlite3'


def terminal(item):
    kind, body = item.get('kind', ''), item.get('payload', {})
    if kind == 'operation.missing' or body.get('missing_fence'): return 'missing'
    if kind == 'operation.cancel_confirmed' or body.get('cancel_confirmed'): return 'cancelled'
    event = body.get('terminal_result') or kind.removeprefix('native.')
    return {'execution_success': 'success', 'execution_error': 'error',
            'execution_interrupted': 'cancelled'}.get(event) if isinstance(event, str) else None


class EventInbox:
    def __init__(self, directory, *, clock=time.time):
        self.path = Path(directory) / FILENAME
        self.clock = clock
        self.fault = None
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, timeout=.2)
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS streams(server TEXT, epoch TEXT, scope TEXT,
                received INTEGER NOT NULL DEFAULT 0, applied INTEGER NOT NULL DEFAULT 0,
                snapshot TEXT NOT NULL DEFAULT '', health TEXT NOT NULL DEFAULT '{}',
                PRIMARY KEY(server,epoch,scope));
            CREATE TABLE IF NOT EXISTS inbox(server TEXT,epoch TEXT,seq INTEGER,body TEXT NOT NULL,
                size INTEGER NOT NULL,PRIMARY KEY(server,epoch,seq));
            CREATE TABLE IF NOT EXISTS snapshots(server TEXT,epoch TEXT,scope TEXT,token TEXT,
                as_of INTEGER,after_key TEXT,complete INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(server,epoch,scope,token));
            CREATE TABLE IF NOT EXISTS snapshot_items(server TEXT,epoch TEXT,token TEXT,entity TEXT,
                body TEXT NOT NULL,PRIMARY KEY(server,epoch,token,entity));
            CREATE TABLE IF NOT EXISTS owners(token TEXT PRIMARY KEY,body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS observations(server TEXT,epoch TEXT,entity TEXT,source TEXT,
                outcome TEXT,owner TEXT,at REAL,PRIMARY KEY(server,epoch,entity,source));
            CREATE TABLE IF NOT EXISTS diagnostics(key TEXT PRIMARY KEY,at REAL,kind TEXT,body TEXT);
            CREATE TABLE IF NOT EXISTS budgets(server TEXT,epoch TEXT,count INTEGER NOT NULL,size INTEGER NOT NULL,
                PRIMARY KEY(server,epoch));
            CREATE TABLE IF NOT EXISTS delivery_offsets(server TEXT,epoch TEXT,scope TEXT,owner TEXT,
                cursor INTEGER NOT NULL DEFAULT 0,snapshot TEXT NOT NULL DEFAULT '',after_key TEXT NOT NULL DEFAULT '',
                PRIMARY KEY(server,epoch,scope,owner));
            CREATE TABLE IF NOT EXISTS inbox_times(server TEXT,epoch TEXT,seq INTEGER,received REAL,
                PRIMARY KEY(server,epoch,seq));
            CREATE TABLE IF NOT EXISTS snapshot_budgets(server TEXT,epoch TEXT,token TEXT,count INTEGER,size INTEGER,
                PRIMARY KEY(server,epoch,token));
        ''')
        # One-time sidecar accounting upgrade, never on the per-page path.
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO budgets SELECT server,epoch,COUNT(*),SUM(size) FROM inbox GROUP BY server,epoch')
            self.db.execute('INSERT OR IGNORE INTO inbox_times SELECT server,epoch,seq,? FROM inbox', (self.clock(),))
            self.db.execute('INSERT OR IGNORE INTO snapshot_budgets SELECT server,epoch,token,COUNT(*),SUM(length(CAST(body AS BLOB))) FROM snapshot_items GROUP BY server,epoch,token')

    def close(self):
        self.db.close()

    def checkpoint(self, point):
        if self.fault: self.fault(point)

    def key(self, identity, scope):
        return identity['server_instance'], identity['epoch'], p.scope_digest(scope)

    def stream(self, identity, scope):
        row = self.db.execute('SELECT received,applied,snapshot,health FROM streams WHERE server=? AND epoch=? AND scope=?',
                              self.key(identity, scope)).fetchone()
        return dict(received=str(row[0]), applied=str(row[1]), snapshot=row[2], health=json.loads(row[3])) if row else None

    def _ensure(self, key):
        self.db.execute('INSERT OR IGNORE INTO streams(server,epoch,scope) VALUES (?,?,?)', key)

    def _budget(self, server, epoch, extra_count, extra_bytes):
        count, size = self.db.execute('SELECT count,size FROM budgets WHERE server=? AND epoch=?',
                                      (server, epoch)).fetchone() or (0, 0)
        if count + extra_count > p.RETENTION_COUNT or size + extra_bytes > p.RETENTION_BYTES:
            raise sqlite3.OperationalError('shadow inbox capacity reached; unapplied data retained')

    def page(self, identity, scope, since, page):
        covered = p.validate_page(page, identity, scope, since)
        key = self.key(identity, scope)
        with self.db:
            self._ensure(key)
            received = int(self.stream(identity, scope)['received'])
            duplicate = received == covered and received >= p.cursor(since)
            if received != p.cursor(since) and not duplicate: raise ValueError('Stale inbox cursor')
            fresh = []
            for item in page['events']:
                raw = p.encode(item)
                old = self.db.execute('SELECT body FROM inbox WHERE server=? AND epoch=? AND seq=?',
                                      (*key[:2], p.cursor(item['seq']))).fetchone()
                if old and old[0] != raw.decode(): raise ValueError('Conflicting duplicate event')
                if not old:
                    if duplicate and received != p.cursor(since): raise ValueError('Incomplete duplicate page')
                    fresh.append((item, raw))
            self._budget(*key[:2], len(fresh), sum(len(raw) for _, raw in fresh))
            for item, raw in fresh:
                self.db.execute('INSERT INTO inbox VALUES (?,?,?,?,?)', (*key[:2], p.cursor(item['seq']), raw.decode(), len(raw)))
                self.db.execute('INSERT INTO inbox_times VALUES (?,?,?,?)', (*key[:2], p.cursor(item['seq']), self.clock()))
                self._event_observation(identity, item)
            self.db.execute('INSERT INTO budgets VALUES (?,?,?,?) ON CONFLICT(server,epoch) DO UPDATE SET count=count+excluded.count,size=size+excluded.size',
                            (*key[:2], len(fresh), sum(len(raw) for _, raw in fresh)))
            self.checkpoint('before_received')
            self.db.execute('UPDATE streams SET received=? WHERE server=? AND epoch=? AND scope=?', (covered, *key))
        return str(covered)

    def delivery(self, identity, scope, owner, *, limit=16):
        """One bounded page per owner; no SQLite objects cross the worker boundary."""
        key = self.key(identity, scope)
        stream = self.stream(identity, scope)
        if not stream or not stream['snapshot']: return None
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO delivery_offsets(server,epoch,scope,owner) VALUES (?,?,?,?)', (*key, owner))
        cursor, snapshot, after = self.db.execute('SELECT cursor,snapshot,after_key FROM delivery_offsets WHERE server=? AND epoch=? AND scope=? AND owner=?', (*key, owner)).fetchone()
        target = stream['snapshot']
        if snapshot != target:
            if not after.startswith(target + ':'): after = ''
            else: after = after[len(target) + 1:]
            rows = self.db.execute('SELECT entity,body FROM snapshot_items WHERE server=? AND epoch=? AND token=? AND entity>? ORDER BY entity LIMIT ?',
                                   (*key[:2], target, after, limit)).fetchall()
            rows, truncated = self._small_page(rows)
            watermark = self.db.execute('SELECT as_of FROM snapshots WHERE server=? AND epoch=? AND scope=? AND token=? AND complete=1', (*key, target)).fetchone()
            if watermark is None: return None
            return dict(owner=owner, identity=identity, scope=scope, snapshot=target, after=after,
                        next_after=rows[-1][0] if rows else '', covered_to=str(watermark[0]),
                        done=not truncated and len(rows)<limit, events=[json.loads(r[1]) for r in rows])
        rows = self.db.execute('SELECT seq,body FROM inbox WHERE server=? AND epoch=? AND seq>? ORDER BY seq LIMIT ?', (*key[:2], cursor, limit)).fetchall()
        rows, truncated = self._small_page(rows)
        if not rows and cursor == int(stream['received']): return None
        covered = rows[-1][0] if truncated or len(rows) == limit else int(stream['received'])
        return dict(owner=owner, identity=identity, scope=scope, snapshot='', from_cursor=str(cursor),
                    covered_to=str(covered), events=[json.loads(r[1]) for r in rows])

    @staticmethod
    def _small_page(rows):
        size=0
        for index,(_,body) in enumerate(rows):
            size+=len(body.encode('utf-8'))
            if index and size>p.MAX_PAGE_BYTES-8192:return rows[:index],True
        return rows,False

    def delivered(self, page):
        """Called only after every document acceptance in this page committed."""
        key = self.key(page['identity'], page['scope'])
        with self.db:
            if page['snapshot']:
                if self.stream(page['identity'],page['scope'])['snapshot']!=page['snapshot']:return
                if page['done']:
                    self.db.execute('UPDATE delivery_offsets SET cursor=MAX(cursor,?),snapshot=?,after_key=? WHERE server=? AND epoch=? AND scope=? AND owner=?',
                                    (int(page['covered_to']), page['snapshot'], '', *key, page['owner']))
                    # A completed replacement snapshot covers prior epochs for
                    # this owner only. Other, unopened owners remain pending.
                    self.db.execute('''UPDATE delivery_offsets SET cursor=COALESCE((SELECT received FROM streams s
                        WHERE s.server=delivery_offsets.server AND s.epoch=delivery_offsets.epoch AND s.scope=delivery_offsets.scope),cursor)
                        WHERE server=? AND epoch!=? AND scope=? AND owner=?''',(*key,page['owner']))
                    self.db.execute('''UPDATE streams SET applied=COALESCE((SELECT MIN(cursor) FROM delivery_offsets d
                        WHERE d.server=streams.server AND d.epoch=streams.epoch AND d.scope=streams.scope),applied)
                        WHERE server=? AND epoch!=? AND scope=?''',key)
                else:
                    self.db.execute('UPDATE delivery_offsets SET after_key=? WHERE server=? AND epoch=? AND scope=? AND owner=?',
                                    (page['snapshot'] + ':' + page['next_after'], *key, page['owner']))
            else:
                self.db.execute('UPDATE delivery_offsets SET cursor=MAX(cursor,?) WHERE server=? AND epoch=? AND scope=? AND owner=? AND cursor=?',
                                (int(page['covered_to']), *key, page['owner'], int(page['from_cursor'])))
            self.checkpoint('before_applied')
            self.db.execute('UPDATE streams SET applied=COALESCE((SELECT MIN(cursor) FROM delivery_offsets WHERE server=? AND epoch=? AND scope=?),applied) WHERE server=? AND epoch=? AND scope=?', (*key, *key))

    def prune(self, identity):
        """Keep pending owners forever; trim only fully delivered events after 24h."""
        cutoff = self.clock() - p.RETENTION_SECONDS
        with self.db:
            rows = self.db.execute('''SELECT i.server,i.epoch,i.seq,i.size FROM inbox i JOIN inbox_times t
                USING(server,epoch,seq) WHERE t.received<? AND i.seq<=COALESCE(
                (SELECT MIN(applied) FROM streams s WHERE s.server=i.server AND s.epoch=i.epoch),0) LIMIT 1024''', (cutoff,)).fetchall()
            for server, epoch, seq, size in rows:
                self.db.execute('DELETE FROM inbox WHERE server=? AND epoch=? AND seq=?', (server,epoch,seq))
                self.db.execute('DELETE FROM inbox_times WHERE server=? AND epoch=? AND seq=?', (server,epoch,seq))
                self.db.execute('UPDATE budgets SET count=count-1,size=size-? WHERE server=? AND epoch=?', (size,server,epoch))
            # Old epochs are collectible only once every owner has acknowledged
            # their final received watermark. Unopenable owners remain pending.
            old = self.db.execute('SELECT server,epoch FROM budgets WHERE server=? AND epoch!=? AND count=0',
                                  (identity['server_instance'], identity['epoch'])).fetchall()
            for server, epoch in old:
                if self.db.execute('SELECT 1 FROM streams WHERE server=? AND epoch=? AND applied<received', (server,epoch)).fetchone(): continue
                for table in ('streams','snapshots','snapshot_items','snapshot_budgets','delivery_offsets','observations','budgets'):
                    self.db.execute('DELETE FROM '+table+' WHERE server=? AND epoch=?', (server,epoch))

    def snapshot_page(self, identity, scope, page, *, token='', after='', as_of=None):
        p.validate_snapshot(page, identity, scope, token=token, after=after, as_of=as_of)
        key = self.key(identity, scope)
        token, watermark = page['snapshot_token'], p.cursor(page['as_of_seq'])
        with self.db:
            self._ensure(key)
            self.db.execute('INSERT OR IGNORE INTO snapshots VALUES (?,?,?,?,?,?,0)', (*key, token, watermark, ''))
            record = self.db.execute('SELECT as_of,after_key FROM snapshots WHERE server=? AND epoch=? AND scope=? AND token=?',
                                     (*key, token)).fetchone()
            if record != (watermark, after): raise ValueError('Stale snapshot page')
            count,size=self.db.execute('SELECT count,size FROM snapshot_budgets WHERE server=? AND epoch=? AND token=?',(*key[:2],token)).fetchone() or (0,0)
            for item in page['receipts']:
                raw=p.encode(item).decode()
                old=self.db.execute('SELECT length(CAST(body AS BLOB)) FROM snapshot_items WHERE server=? AND epoch=? AND token=? AND entity=?',(*key[:2],token,item['entity'])).fetchone()
                count+=int(old is None)
                size+=len(raw.encode())-(old[0] if old else 0)
                self.db.execute('INSERT OR REPLACE INTO snapshot_items VALUES (?,?,?,?,?)',
                                (*key[:2], token, item['entity'], raw))
            if count > p.RETENTION_COUNT or size > p.RETENTION_BYTES:
                raise sqlite3.OperationalError('shadow snapshot capacity reached')
            self.db.execute('INSERT OR REPLACE INTO snapshot_budgets VALUES (?,?,?,?,?)',(*key[:2],token,count,size))
            self.checkpoint('before_snapshot_received')
            self.db.execute('UPDATE snapshots SET after_key=?,complete=? WHERE server=? AND epoch=? AND scope=? AND token=?',
                            (page['next_after'], int(page['done']), *key, token))
            if page['done']:
                for raw, in self.db.execute('SELECT body FROM snapshot_items WHERE server=? AND epoch=? AND token=?', (*key[:2], token)):
                    self._event_observation(identity, json.loads(raw))
                self.db.execute('UPDATE streams SET received=MAX(received,?),snapshot=? WHERE server=? AND epoch=? AND scope=?',
                                (watermark, token, *key))
                # Aborted/older snapshots of this scope have no consumer. Keep
                # the complete active snapshot and every inbox event for C.
                old = [r[0] for r in self.db.execute('SELECT token FROM snapshots WHERE server=? AND epoch=? AND scope=? AND token!=?', (*key, token))]
                for previous in old:
                    self.db.execute('DELETE FROM snapshot_items WHERE server=? AND epoch=? AND token=?', (*key[:2], previous))
                    self.db.execute('DELETE FROM snapshot_budgets WHERE server=? AND epoch=? AND token=?',(*key[:2],previous))
                self.db.execute('DELETE FROM snapshots WHERE server=? AND epoch=? AND scope=? AND token!=?', (*key, token))

    def health(self, identity, scope, value):
        key, raw = self.key(identity, scope), p.encode(value).decode()
        with self.db:
            self._ensure(key)
            self.checkpoint('health')
            self.db.execute('UPDATE streams SET health=? WHERE server=? AND epoch=? AND scope=? AND health!=?', (raw, *key, raw))

    def owner(self, value):
        with self.db:
            previous = self.db.execute('SELECT body FROM owners WHERE token=?', (value['token'],)).fetchone()
            if previous:
                value = dict(value, servers=sorted(set(value.get('servers', [])) | set(json.loads(previous[0]).get('servers', []))))
            self.db.execute('INSERT OR REPLACE INTO owners VALUES (?,?)', (value['token'], p.encode(value).decode()))

    def diagnostic(self, kind, value, *, key=None):
        with self.db: self._diagnostic(kind, value, key)

    def _diagnostic(self, kind, value, key=None):
        raw = p.encode(value).decode()
        key = key or kind + ':' + __import__('hashlib').sha256(raw.encode()).hexdigest()
        self.db.execute('INSERT OR IGNORE INTO diagnostics VALUES (?,?,?,?)', (key, self.clock(), kind, raw))

    def _event_observation(self, identity, item):
        outcome = terminal(item)
        if not outcome: return
        prompt = item.get('prompt_id')
        for entity in {item.get('entity'), 'prompt:' + prompt if prompt else None} - {None, ''}:
            self._observe(identity, entity, 'event', outcome, '')

    def observe_poll(self, identity, entity, outcome, owner=''):
        with self.db: self._observe(identity, entity, 'poll', outcome, owner)

    def _observe(self, identity, entity, source, outcome, owner):
        key = identity['server_instance'], identity['epoch'], entity
        self.db.execute('INSERT OR REPLACE INTO observations VALUES (?,?,?,?,?,?,?)', (*key, source, outcome, owner, self.clock()))
        rows = dict(self.db.execute('SELECT source,outcome FROM observations WHERE server=? AND epoch=? AND entity=?', key))
        if 'poll' in rows and 'event' in rows:
            self._diagnostic('shadow_match' if rows['poll'] == rows['event'] else 'shadow_mismatch',
                             dict(server=key[0], epoch=key[1], entity=entity, owner=owner, **rows))
