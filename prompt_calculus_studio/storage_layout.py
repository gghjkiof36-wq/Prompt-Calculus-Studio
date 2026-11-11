"""Read-before-write layout guard and explicit, single-copy migration registry."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3

from . import journal_codec as codec
from .sqlite_schema import schema_transaction

OLD_MESSAGE = '這份資料是舊版格式，請用搬遷功能匯入。'
NEW_MESSAGE = '這份資料來自較新版本，無法開啟。'
SUPPORTED = {'journal': 1, 'local_state': 0}
TABLES = {
    'stage_header': ('id', 'kind', 'workspace', 'owner', 'position', 'body', 'projection', 'content_paths'),
    'stage_content': ('sha256', 'encoding', 'bytes', 'body'),
    'stage_content_ref': ('record_id', 'pointer', 'sha256'),
    'storage_layout': ('key', 'version'),
}


class LayoutError(ValueError):
    pass


def inspect_layout(db):
    """No DDL, PRAGMA writes, data reads, or restart updates before this check."""
    objects = dict(db.execute("SELECT name,type FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'"))
    if not objects:
        return False
    if objects.get('storage_layout') != 'table':
        raise LayoutError(OLD_MESSAGE)
    try:
        versions = dict(db.execute('SELECT key,version FROM storage_layout'))
        if any(type(value) is not int or value > SUPPORTED.get(key, -1) for key, value in versions.items()):
            raise LayoutError(NEW_MESSAGE)
        if versions.get('journal') != 1 or 'stage_journal' in objects:
            raise LayoutError(OLD_MESSAGE)
        if 'local_state' in versions and versions['local_state'] != 0:
            raise LayoutError(OLD_MESSAGE)
        for table, fields in TABLES.items():
            columns = db.execute('PRAGMA table_info(' + table + ')').fetchall()
            if objects.get(table) != 'table' or tuple(row[1] for row in columns) != fields:
                raise LayoutError(OLD_MESSAGE)
            primary = ('record_id', 'pointer') if table == 'stage_content_ref' else (fields[0],)
            required = {'stage_header': {'body', 'projection', 'content_paths'},
                        'stage_content': {'encoding', 'bytes', 'body'},
                        'stage_content_ref': set(fields), 'storage_layout': {'version'}}[table]
            for column in columns:
                name = column[1]
                datatype = 'INTEGER' if name in ('position', 'bytes', 'version') else 'BLOB' if table == 'stage_content' and name == 'body' else 'TEXT'
                if column[2].upper() != datatype or bool(column[3]) != (name in required) or column[5] != (primary.index(name) + 1 if name in primary else 0):
                    raise LayoutError(OLD_MESSAGE)
        foreign = {(row[2], row[3], row[4], row[6]) for row in db.execute('PRAGMA foreign_key_list(stage_content_ref)')}
        if foreign != {('stage_header', 'record_id', 'id', 'CASCADE'), ('stage_content', 'sha256', 'sha256', 'NO ACTION')}:
            raise LayoutError(OLD_MESSAGE)
    except sqlite3.DatabaseError as exc:
        raise LayoutError(OLD_MESSAGE) from exc
    return True


def create_journal(db):
    """Caller owns the initialization/migration transaction; never commits."""
    db.execute('CREATE TABLE stage_header (id TEXT PRIMARY KEY,kind TEXT,workspace TEXT,owner TEXT,'
               'position INTEGER,body TEXT NOT NULL,projection TEXT NOT NULL,content_paths TEXT NOT NULL)')
    db.execute('CREATE TABLE stage_content (sha256 TEXT PRIMARY KEY,encoding TEXT NOT NULL,bytes INTEGER NOT NULL,body BLOB NOT NULL)')
    db.execute('CREATE TABLE stage_content_ref (record_id TEXT NOT NULL REFERENCES stage_header(id) ON DELETE CASCADE,'
               'pointer TEXT NOT NULL,sha256 TEXT NOT NULL REFERENCES stage_content(sha256),PRIMARY KEY(record_id,pointer))')
    db.execute('CREATE INDEX stage_ref_hash ON stage_content_ref(sha256)')
    create_indexes(db)


def create_indexes(db):
    indexes = {
        'owner': '(kind,workspace,owner,position)',
        'batch_control': "(workspace,owner,json_extract(body,'$.group')) WHERE kind='batch_control'",
        'position': '(position)',
        'owner_order': '(kind,owner,position)',
        'group': "(kind,workspace,json_extract(body,'$.scheduler'),json_extract(body,'$.execution_group'),position)",
        'entry_display': "(kind,workspace,json_extract(body,'$.scheduler'),json_extract(body,'$.status'),"
                         "COALESCE(json_extract(body,'$.execution_group'),'legacy:'||json_extract(body,'$.run')),"
                         "json_extract(body,'$.run'),id,position,json_extract(body,'$.created'),json_extract(body,'$.label'))",
        'latest': "(kind,workspace,json_extract(body,'$.stage'),position)",
        'active': "(kind,json_extract(body,'$.status'),workspace,owner,position)",
        'open_runs': "(workspace,position) WHERE kind='run' AND json_extract(body,'$.status') NOT IN ('complete','cancelled')",
        'parent': "(kind,workspace,json_extract(body,'$.parent'),position)",
    }
    for name, expression in indexes.items():
        db.execute('CREATE INDEX IF NOT EXISTS stage_header_' + name + ' ON stage_header' + expression)


def mark_journal(db):
    db.execute('CREATE TABLE IF NOT EXISTS storage_layout (key TEXT PRIMARY KEY,version INTEGER NOT NULL)')
    db.execute("INSERT INTO storage_layout VALUES ('journal',1)")


def initialize(db):
    """Explicit empty-database initialization, also used by the preview runtime."""
    if inspect_layout(db):
        with schema_transaction(db):
            create_indexes(db)
        return
    with schema_transaction(db):
        create_journal(db)
        mark_journal(db)


class MigrationError(ValueError):
    def __init__(self, record_ids, reason):
        self.record_ids = list(record_ids)
        super().__init__('Journal migration failed [' + ', '.join(map(str, record_ids)) + ']: ' + reason)


def inspect_legacy(db):
    tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if 'storage_layout' in tables or tables.intersection(TABLES.keys() - {'storage_layout'}):
        raise MigrationError([], 'source is not journal layout 0')
    return {'has_journal': 'stage_journal' in tables}


def verify_journal(db, old):
    rows = db.execute('SELECT id,body,content_paths,projection FROM stage_header ORDER BY position,id').fetchall()
    restored = {r['id']: r for r in codec.records(db, rows)}
    old_ids = {row[0] for row, body in old}
    if set(restored) != old_ids:
        raise MigrationError(sorted(old_ids ^ set(restored)), 'id set differs')
    for row, body in old:
        if not codec.identical(body, restored[row[0]]):
            raise MigrationError([row[0]], 'body value/type differs')
    expected = [row[:5] for row, body in old]
    actual = db.execute('SELECT id,kind,workspace,owner,position FROM stage_header ORDER BY position,id').fetchall()
    if actual != expected:
        raise MigrationError([], 'SQL identity/position differs')
    before = Counter((row[1], codec.canonical(body.get('status')).decode('utf-8')) for row, body in old)
    after = Counter((row[1], codec.canonical(restored[row[0]].get('status')).decode('utf-8')) for row in actual)
    if before != after:
        raise MigrationError([], 'kind/status counts differ')
    if db.execute('PRAGMA foreign_key_check').fetchall():
        raise MigrationError([], 'foreign key check failed')
    if db.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
        raise MigrationError([], 'integrity check failed')
    return dict(records=len(old), ids=[row[0] for row, body in old],
                positions=[[row[0], row[4]] for row, body in old],
                kind_status=[dict(kind=k, status=s, count=n) for (k, s), n in sorted(before.items())],
                contents=db.execute('SELECT COUNT(*) FROM stage_content').fetchone()[0],
                references=db.execute('SELECT COUNT(*) FROM stage_content_ref').fetchone()[0],
                integrity_check='ok')


def transform_journal(db, *, checkpoint=None):
    """Only for an already copied database. No StageStore and no restart pause.

    checkpoint is an optional progress/failure-injection hook; exceptions roll
    back every DDL/DML statement. VACUUM and directory publication belong to the
    caller. Existing caller transactions are preserved by the schema savepoint.
    """
    source = inspect_legacy(db)
    with migration_transaction(db):
        old = []
        invalid = []
        for row in db.execute('SELECT id,kind,workspace,owner,position,body FROM stage_journal ORDER BY position,id') if source['has_journal'] else ():
            try:
                body = codec.strict_loads(row[5])
                if not isinstance(body, dict) or body.get('id') != row[0]:
                    raise ValueError('record identity differs')
                encoded = codec.canonical(body)
                if not codec.identical(body, codec.strict_loads(encoded)):
                    raise ValueError('lossy JSON encoding')
                old.append((row, body))
            except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError):
                invalid.append(row[0])
        if invalid:
            raise MigrationError(invalid, 'non-standard or lossy JSON')
        create_journal(db)
        for row, body in old:
            try:
                codec.write(db, body, row[4], columns=row[:4])
            except (ValueError, TypeError, UnicodeError, OverflowError, RecursionError) as exc:
                raise MigrationError([row[0]], str(exc)) from exc
            if checkpoint:
                checkpoint('record', row[0])
        report = verify_journal(db, old)
        if checkpoint:
            checkpoint('verified', report)
        if source['has_journal']:
            db.execute('DROP TABLE stage_journal')
        mark_journal(db)
        if checkpoint:
            checkpoint('marked', report)
    return report


@contextmanager
def migration_transaction(db):
    # ROLLBACK TO + RELEASE of a root savepoint commits an empty transaction,
    # changing SQLite's file change counter. Use a real rollback at the root.
    if db.in_transaction:
        with schema_transaction(db):
            yield
        return
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.commit()
    except BaseException:
        db.rollback()
        raise


@dataclass(frozen=True)
class LayoutStep:
    key: str
    from_version: int
    to_version: int
    inspect: object
    transform: object
    verify: object


REGISTRY = (LayoutStep('journal', 0, 1, inspect_legacy, transform_journal, verify_journal),)
