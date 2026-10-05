"""Desktop pending queue, separate from mutable documents and bounded history."""
import copy
import json
import time
import uuid
from .queued_work import ACTIVE, validate


class WorkQueue:
    def __init__(self, db):
        self.db = db
        with db:
            db.execute('CREATE TABLE IF NOT EXISTS work_queue (id TEXT PRIMARY KEY, position INTEGER NOT NULL, body TEXT NOT NULL)')

    def rows(self):
        return [json.loads(r[0]) for r in self.db.execute('SELECT body FROM work_queue ORDER BY position')]

    def read(self, ident):
        row = self.db.execute('SELECT body FROM work_queue WHERE id=?', (ident,)).fetchone()
        if not row:
            raise ValueError('Queue 工作不存在。')
        return json.loads(row[0])

    def add(self, work, capture, label=''):
        return self.add_many([(work,capture,label)])[0]

    def add_many(self, entries):
        items=[]
        for work,capture,label in entries:
            validate(work)
            items.append(dict(id=uuid.uuid4().hex, created=time.time(), state='waiting', work=copy.deepcopy(work),
                         capture=capture, label=label or work['generation']['workflow'], attempts=[], error=''))
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.db.execute('SELECT COUNT(*) FROM work_queue').fetchone()[0]+len(items) > 2000:
                raise ValueError('Queue 已有 2000 項紀錄，請先移除不再需要的等待項目。')
            for item in items:
                self.db.execute('INSERT INTO work_queue VALUES (?,(SELECT COALESCE(MAX(position),0)+1 FROM work_queue),?)',
                                (item['id'], json.dumps(item, ensure_ascii=False)))
        return items

    def update(self, ident, **changes):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            item = self.read(ident)
            item.update(copy.deepcopy(changes))
            self.db.execute('UPDATE work_queue SET body=? WHERE id=?', (json.dumps(item, ensure_ascii=False), ident))
        return item

    def claim(self, server):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            rows = [r for r in self.rows() if r['work']['scope']['server'] == server]
            if any(r['state'] in ACTIVE for r in rows):
                return None
            item = next((r for r in rows if r['state'] == 'waiting'), None)
            if item is None:
                return None
            attempt = uuid.uuid4().hex
            item.update(state='submitting', attempt=attempt, error='')
            item['attempts'].append(dict(id=attempt, created=time.time()))
            self.db.execute('UPDATE work_queue SET body=? WHERE id=?', (json.dumps(item, ensure_ascii=False), item['id']))
            return item

    def edit_pending(self, ident, action):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            item = self.read(ident)
            if item['state'] != 'waiting':
                raise ValueError('只可排序或移除尚未派送的工作。')
            if action == 'remove':
                self.db.execute('DELETE FROM work_queue WHERE id=?', (ident,))
                return
            rows = self.rows()
            pending = [r['id'] for r in rows if r['state'] == 'waiting']
            pos = pending.index(ident)
            pending.remove(ident)
            target = 0 if action == 'next' else max(0, pos-1) if action == 'up' else min(len(pending), pos+1)
            pending.insert(target, ident)
            iterator = iter(pending)
            order = [next(iterator) if r['state'] == 'waiting' else r['id'] for r in rows]
            for index, key in enumerate(order):
                self.db.execute('UPDATE work_queue SET position=? WHERE id=?', (index, key))

    def retry(self, ident):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            item = self.read(ident)
            if item['state'] != 'failed':
                raise ValueError('只可重試已確認失敗的工作；未知提交須先對帳。')
            item.update(state='waiting', error='')
            for key in ('attempt','prompt_id','payload','outputs','results'):
                item.pop(key, None)
            self.db.execute('UPDATE work_queue SET body=? WHERE id=?', (json.dumps(item, ensure_ascii=False), ident))

    def remove_finished(self, ident):
        with self.db:
            self.db.execute('BEGIN IMMEDIATE')
            if self.read(ident)['state'] not in ('complete','failed'):
                raise ValueError('未結案工作不能移除紀錄。')
            self.db.execute('DELETE FROM work_queue WHERE id=?',(ident,))
