"""Serialized candidate-only writes. Each unit closes its worker connection."""
import sqlite3

from PySide6.QtCore import QThreadPool
from .comfy_event_protocol import encode

_pool=None


def pool():
    global _pool
    if _pool is None:
        _pool=QThreadPool();_pool.setMaxThreadCount(1)
    return _pool


def configure(db):
    path=next((path for _,name,path in db.execute('PRAGMA database_list') if name=='main'),None)
    if not path:raise ValueError('Candidate writer requires a document file')
    if db.execute('PRAGMA journal_mode=WAL').fetchone()[0]!='wal':raise ValueError('Candidate writer requires WAL')
    # A GUI callback never waits a default five seconds behind the writer.
    # Busy acceptance follows the same existing retry as any other DB failure.
    db.execute('PRAGMA busy_timeout=8')
    return path


def write(path, phase, packet, identity, ident, values, cancel, current):
    """Only this pool opens a writing connection; no job/document SQL here."""
    def check():
        if not current() or cancel.is_set():raise ValueError('Stale candidate writer')
    check()
    db=sqlite3.connect(path,timeout=.008)
    try:
        db.execute('PRAGMA busy_timeout=8');db.execute('PRAGMA synchronous=FULL')
        db.execute('PRAGMA wal_autocheckpoint=0')
        # Enforce the boundary even if a future caller accidentally adds SQL.
        def authorize(action, table, column, database, trigger):
            if action in (sqlite3.SQLITE_INSERT,sqlite3.SQLITE_UPDATE,sqlite3.SQLITE_DELETE) and table!='channel_result_candidates':
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        db.set_authorizer(authorize)
        header=packet['header'];source=header.get('item_source','candidates');manifest=header['manifest_id']
        owner=encode(identity['owner']).decode()
        db.execute('BEGIN IMMEDIATE')
        if phase=='intent':
            for item in packet['items']:
                check();key,body=packet['records'][item['ordinal']]
                old=db.execute('SELECT body,owner,operation,ordinal FROM channel_result_candidates WHERE manifest=? AND source=? AND semantic=?',
                               (manifest,source,key)).fetchone()
                if old is not None:
                    if old!=(body,owner,ident,item['ordinal']):raise ValueError('Conflicting result item')
                else:db.execute('INSERT INTO channel_result_candidates (manifest,source,semantic,owner,operation,ordinal,state,body,receipt) VALUES (?,?,?,?,?,?,?,?,?)',
                    (manifest,source,key,owner,ident,item['ordinal'],'pending',body,'{}'))
        else:
            for item,receipt,_ in values:
                check();key,body=packet['records'][item['ordinal']];raw=encode(receipt).decode()
                updated=db.execute("UPDATE channel_result_candidates SET state='ready',receipt=? WHERE manifest=? AND source=? AND semantic=? AND owner=? AND operation=? AND body=?",
                    (raw,manifest,source,key,owner,ident,body))
                if updated.rowcount!=1:raise ValueError('Candidate intent changed')
        check()  # Generation and cancellation are fenced immediately before COMMIT.
        db.commit()
    finally:
        db.close()
    return packet if phase=='intent' else values
