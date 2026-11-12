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
        ''')

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
        count, size = self.db.execute('SELECT COUNT(*),COALESCE(SUM(size),0) FROM inbox WHERE server=? AND epoch=?',
                                      (server, epoch)).fetchone()
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
                self._event_observation(identity, item)
            self.checkpoint('before_received')
            self.db.execute('UPDATE streams SET received=? WHERE server=? AND epoch=? AND scope=?', (covered, *key))
        return str(covered)

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
            for item in page['receipts']:
                self.db.execute('INSERT OR REPLACE INTO snapshot_items VALUES (?,?,?,?,?)',
                                (*key[:2], token, item['entity'], p.encode(item).decode()))
            count, size = self.db.execute('SELECT COUNT(*),COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM snapshot_items WHERE server=? AND epoch=? AND token=?',
                                          (*key[:2], token)).fetchone()
            if count > p.RETENTION_COUNT or size > p.RETENTION_BYTES:
                raise sqlite3.OperationalError('shadow snapshot capacity reached')
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
