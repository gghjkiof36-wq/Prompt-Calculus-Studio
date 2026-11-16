"""Read-before-write layout guard and explicit, single-copy migration registry."""
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass
import sqlite3

from . import journal_codec as codec
from .sqlite_schema import schema_transaction

OLD_MESSAGE = '這份資料是舊版格式，請用搬遷功能匯入。'
NEW_MESSAGE = '這份資料來自較新版本，無法開啟。'
SUPPORTED = {'journal': 1, 'local_state': 0, 'event_apply': 2}
TABLES = {
    'stage_header': ('id', 'kind', 'workspace', 'owner', 'position', 'body', 'projection', 'content_paths'),
    'stage_content': ('sha256', 'encoding', 'bytes', 'body'),
    'stage_content_ref': ('record_id', 'pointer', 'sha256'),
    'storage_layout': ('key', 'version'),
}


class LayoutError(ValueError):
    pass


def inspect_layout(db, *, require_events=True):
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
        if require_events and versions.get('event_apply') != SUPPORTED['event_apply']:
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
        if versions.get('event_apply', 0) >= 1:
            verify_events(db)
        if versions.get('event_apply') == 2:
            verify_candidates(db, inventory=False)
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
        create_events(db)
        create_candidates(db)


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


EVENT_COLUMNS = ('server', 'epoch', 'seq', 'digest', 'entity', 'revision', 'disposition', 'created')


def verify_events(db, old=None):
    columns = db.execute('PRAGMA table_info(channel_applied_events)').fetchall()
    if tuple(row[1] for row in columns) != EVENT_COLUMNS or tuple(row[5] for row in columns) != (1, 2, 3, 0, 0, 0, 0, 0):
        raise LayoutError(OLD_MESSAGE)
    if any(not row[3] or row[2].upper()!=('INTEGER' if row[1]=='revision' else 'REAL' if row[1]=='created' else 'TEXT') for row in columns):
        raise LayoutError(OLD_MESSAGE)
    return dict(receipts=db.execute('SELECT COUNT(*) FROM channel_applied_events').fetchone()[0])


def create_events(db):
    db.execute('CREATE TABLE channel_applied_events (server TEXT NOT NULL,epoch TEXT NOT NULL,seq TEXT NOT NULL,'
               'digest TEXT NOT NULL,entity TEXT NOT NULL,revision INTEGER NOT NULL,disposition TEXT NOT NULL,'
               'created REAL NOT NULL,PRIMARY KEY(server,epoch,seq))')
    db.execute("INSERT INTO storage_layout VALUES ('event_apply',1)")


def inspect_before_events(db):
    inspect_layout(db, require_events=False)
    if db.execute("SELECT 1 FROM storage_layout WHERE key='event_apply'").fetchone():
        raise LayoutError('Event acceptance layout already present')


def transform_events(db, *, checkpoint=None):
    inspect_before_events(db)
    with migration_transaction(db):
        create_events(db)
        if checkpoint: checkpoint('event_apply', None)
        return verify_events(db)


def pending_steps(db):
    names = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    versions = dict(db.execute('SELECT key,version FROM storage_layout')) if 'storage_layout' in names else {}
    if any(type(v) is not int or v > SUPPORTED.get(k, -1) for k, v in versions.items()):
        raise LayoutError(NEW_MESSAGE)
    pending = []
    for step in REGISTRY:
        if versions.get(step.key, 0) == step.from_version:
            pending.append(step)
            versions[step.key] = step.to_version
    return pending


CANDIDATE_COLUMNS = ('manifest', 'source', 'semantic', 'owner', 'operation', 'ordinal', 'state', 'body', 'receipt', 'derived_pending')


def verify_candidates(db, old=None, *, inventory=True):
    columns = db.execute('PRAGMA table_info(channel_result_candidates)').fetchall()
    if (tuple(row[1] for row in columns) != CANDIDATE_COLUMNS or
            tuple(row[5] for row in columns) != (1, 2, 3, 0, 0, 0, 0, 0, 0, 0) or
            any(not row[3] or row[2].upper() != ('INTEGER' if row[1] in ('ordinal','derived_pending') else 'TEXT') for row in columns)):
        raise LayoutError(OLD_MESSAGE)
    indexes = {row[1]: (row[2],row[4]) for row in db.execute('PRAGMA index_list(channel_result_candidates)')}
    for name, fields, unique in (
            ('channel_candidate_owner', ('owner', 'operation', 'state'), 0),
            ('channel_candidate_order', ('manifest', 'source', 'ordinal'), 1),
            ('channel_candidate_operation', ('operation',), 0)):
        if indexes.get(name) != (unique,0) or tuple(r[2] for r in db.execute('PRAGMA index_info('+name+')')) != fields:
            raise LayoutError(OLD_MESSAGE)
    for name, definition in CANDIDATE_PARTIAL_INDEXES.items():
        row=db.execute("SELECT sql FROM sqlite_master WHERE type='index' AND name=?",(name,)).fetchone()
        if indexes.get(name)!=(0,1) or not row or ' '.join(row[0].lower().split())!=' '.join(definition.lower().split()):
            raise LayoutError(OLD_MESSAGE)
    if columns[-1][4]!='0':raise LayoutError(OLD_MESSAGE)
    cursor=db.execute('PRAGMA table_info(channel_result_retention)').fetchall()
    if (tuple(row[1] for row in cursor)!=('id','after_rowid') or tuple(row[5] for row in cursor)!=(1,0)
            or any(row[2].upper()!='INTEGER' or not row[3] for row in cursor)):
        raise LayoutError(OLD_MESSAGE)
    return dict(candidates=db.execute('SELECT COUNT(*) FROM channel_result_candidates').fetchone()[0]) if inventory else {}


CANDIDATE_PARTIAL_INDEXES = {
    'channel_candidate_deriving': 'CREATE INDEX channel_candidate_deriving ON channel_result_candidates(operation) WHERE derived_pending=1',
    'channel_candidate_asset': "CREATE INDEX channel_candidate_asset ON channel_result_candidates(json_extract(receipt,'$.source.sha256')) WHERE source='history' AND state='ready'",
}


def create_candidates(db):
    db.execute('CREATE TABLE channel_result_candidates (manifest TEXT NOT NULL,source TEXT NOT NULL,'
               'semantic TEXT NOT NULL,owner TEXT NOT NULL,operation TEXT NOT NULL,ordinal INTEGER NOT NULL,'
               'state TEXT NOT NULL,body TEXT NOT NULL,receipt TEXT NOT NULL,derived_pending INTEGER NOT NULL DEFAULT 0,PRIMARY KEY(manifest,source,semantic))')
    db.execute('CREATE INDEX channel_candidate_owner ON channel_result_candidates(owner,operation,state)')
    db.execute('CREATE UNIQUE INDEX channel_candidate_order ON channel_result_candidates(manifest,source,ordinal)')
    db.execute('CREATE INDEX channel_candidate_operation ON channel_result_candidates(operation)')
    for definition in CANDIDATE_PARTIAL_INDEXES.values():db.execute(definition)
    db.execute('CREATE TABLE channel_result_retention (id INTEGER NOT NULL PRIMARY KEY CHECK(id=1),after_rowid INTEGER NOT NULL)')
    db.execute("UPDATE storage_layout SET version=2 WHERE key='event_apply'")


def inspect_before_candidates(db):
    inspect_layout(db, require_events=False)
    if db.execute("SELECT version FROM storage_layout WHERE key='event_apply'").fetchone() != (1,):
        raise LayoutError('Candidates require event acceptance layout 1')


def transform_candidates(db, *, checkpoint=None):
    inspect_before_candidates(db)
    with migration_transaction(db):
        create_candidates(db)
        if checkpoint: checkpoint('result_candidates', None)
        return verify_candidates(db)


REGISTRY = (LayoutStep('journal', 0, 1, inspect_legacy, transform_journal, verify_journal),
            LayoutStep('event_apply', 0, 1, inspect_before_events, transform_events, verify_events),
            LayoutStep('event_apply', 1, 2, inspect_before_candidates, transform_candidates, verify_candidates))
