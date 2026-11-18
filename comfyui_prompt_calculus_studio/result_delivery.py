"""One disposable SQLite file per owned final delivery, outside ComfyUI's roots.

The complete history proof and its temp bytes commit together. The integration
DB can publish that proof after a crash without a second visit to live history.
An acknowledgement releases the entire file, including its allocated pages.
"""
import hashlib
import json
import sqlite3
from contextlib import closing

from .event_protocol import encode


class Deliveries:
    def __init__(self, directory, check_path):
        self.directory = directory / 'result-deliveries'
        self.check_path = check_path

    def path(self, ident):
        if len(ident) != 64 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('Invalid delivery identity')
        path = self.directory / (ident + '.sqlite3')
        # The guard checks every ancestor. Walk it once from the deepest
        # existing component, including a dangling link at the destination.
        if path.is_symlink() or path.exists():
            self.check_path(path)
        elif self.directory.is_symlink() or self.directory.exists():
            self.check_path(self.directory)
        else:
            self.check_path(self.directory.parent)
        return path

    @staticmethod
    def proof(db, ident):
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='proof'").fetchone():
            return None  # Interrupted before the first complete transaction.
        row = db.execute('SELECT manifest,epoch,history_digest,items_digest FROM proof').fetchone()
        if not row or row[0] != ident:
            raise ValueError('Foreign delivery proof')
        verified = [(json.loads(item), json.loads(stamp)) for item, stamp in
                    db.execute('SELECT item,stamp FROM originals ORDER BY ordinal')]
        if hashlib.sha256(encode([item for item, _ in verified])).hexdigest() != row[3]:
            raise ValueError('Damaged delivery inventory')
        return row[1], row[2], verified

    def recover(self, ident):
        path = self.path(ident)
        if path.exists():
            with closing(sqlite3.connect(path.as_uri()+'?mode=rw', uri=True)) as db:
                return self.proof(db, ident)

    def capture(self, ident, epoch, history_digest, originals):
        path = self.path(ident)
        self.directory.mkdir(exist_ok=True)
        self.check_path(self.directory)
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('PRAGMA synchronous=FULL')
            db.execute('BEGIN IMMEDIATE')
            existing = self.proof(db, ident)
            if existing:
                return existing
            db.execute('CREATE TABLE originals (ordinal INTEGER PRIMARY KEY,item TEXT,stamp TEXT,content BLOB)')
            db.execute('CREATE TABLE proof (manifest TEXT PRIMARY KEY,epoch TEXT,history_digest TEXT,items_digest TEXT)')
            verified = []
            for item, stamp, content in originals:
                db.execute('INSERT INTO originals VALUES (?,?,?,?)', (item['ordinal'],
                    encode(item).decode(), encode(stamp).decode(),
                    content if item['image']['type'] == 'temp' else None))
                verified.append((item, stamp))
            checksum = hashlib.sha256(encode([item for item, _ in verified])).hexdigest()
            db.execute('INSERT INTO proof VALUES (?,?,?,?)', (ident, epoch, history_digest, checksum))
        return epoch, history_digest, verified

    def asset(self, ident, item):
        path = self.path(ident)
        if not path.exists():
            return None  # Already acknowledged. The registered original may still exist.
        with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
            row = db.execute('SELECT content FROM originals WHERE ordinal=? AND length(content)=? '
                             'AND EXISTS(SELECT 1 FROM proof WHERE manifest=?)',
                             (item['ordinal'], item['size'], ident)).fetchone()
        if not row or hashlib.sha256(row[0]).hexdigest() != item['asset_sha256']:
            raise ValueError('Retained result does not match sealed ordinal')
        return row[0]

    def release(self, ident):
        path = self.path(ident)
        if path.exists():
            with closing(sqlite3.connect(path.as_uri()+'?mode=ro', uri=True)) as db:
                if db.execute('SELECT manifest FROM proof').fetchone() != (ident,):
                    raise ValueError('Foreign delivery file')
            path.unlink(missing_ok=True)
