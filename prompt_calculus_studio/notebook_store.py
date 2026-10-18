"""Local metadata and compressed recovery snapshots, separate from note files."""
from contextlib import closing
from pathlib import Path
import hashlib
import json
import sqlite3
import time
import zlib


class NotebookStore:
    def __init__(self, directory):
        self.path = Path(directory)/'notebook.sqlite3'; self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self.connect()) as db, db:
            db.executescript('''CREATE TABLE IF NOT EXISTS settings (scope TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS versions (id INTEGER PRIMARY KEY, path TEXT NOT NULL, at REAL NOT NULL,
                    digest TEXT NOT NULL, raw BLOB NOT NULL, reason TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS versions_path ON versions(path,at);''')

    def connect(self): return sqlite3.connect(self.path, timeout=5)

    def get(self, scope, default=None):
        with closing(self.connect()) as db:
            value = db.execute('SELECT value FROM settings WHERE scope=?', (scope,)).fetchone()
            if not value: return default
            try: return json.loads(value[0])
            except (ValueError, TypeError): return default

    def put(self, scope, value):
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
        with closing(self.connect()) as db, db:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)', (scope, raw))

    def capture(self, path, raw, reason='自動保存', force=False, interval=300, now=None):
        if len(raw) > 8*1024*1024: raise ValueError('復原版本超過 8 MiB。')
        key = str(Path(path).absolute()); now = time.time() if now is None else now
        digest = hashlib.sha256(raw).hexdigest()
        with closing(self.connect()) as db, db:
            last = db.execute('SELECT at,digest FROM versions WHERE path=? ORDER BY id DESC LIMIT 1', (key,)).fetchone()
            if last and (last[1] == digest or not force and now-last[0] < interval): return False
            db.execute('INSERT INTO versions(path,at,digest,raw,reason) VALUES (?,?,?,?,?)',
                       (key, now, digest, zlib.compress(raw, 3), reason))
            # Keep recent versions and at least the latest for every path.
            db.execute('DELETE FROM versions WHERE path=? AND at<? AND id NOT IN (SELECT id FROM versions WHERE path=? ORDER BY id DESC LIMIT 1)',
                       (key, now-7*86400, key))
            db.execute('DELETE FROM versions WHERE path=? AND id NOT IN (SELECT id FROM versions WHERE path=? ORDER BY id DESC LIMIT 200)', (key, key))
        return True

    def versions(self, path):
        with closing(self.connect()) as db:
            return db.execute('SELECT id,at,reason,length(raw) FROM versions WHERE path=? ORDER BY id DESC', (str(Path(path).absolute()),)).fetchall()

    def paths(self):
        with closing(self.connect()) as db:
            return [Path(row[0]) for row in db.execute('SELECT DISTINCT path FROM versions ORDER BY path')]

    def raw(self, ident):
        with closing(self.connect()) as db:
            item = db.execute('SELECT raw,digest FROM versions WHERE id=?', (ident,)).fetchone()
        if not item: raise ValueError('找不到這個復原版本。')
        decoder = zlib.decompressobj(); raw = decoder.decompress(item[0], 8*1024*1024+1)
        if len(raw) > 8*1024*1024 or not decoder.eof or hashlib.sha256(raw).hexdigest() != item[1]:
            raise ValueError('復原版本不完整。')
        return raw
