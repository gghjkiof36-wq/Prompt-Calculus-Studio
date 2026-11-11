"""Typed relocation, fail-closed discovery, and logical migration inventories."""
from collections import Counter
import copy
import hashlib
import json
import ntpath
from pathlib import Path
import posixpath
import re

from . import journal_codec as codec
from .migration_files import IMAGES, SIDECARS, MigrationFailure


STUDIO = {
    'document': 'id body', 'stage_journal': 'id kind workspace owner position body',
    'content_history': 'id body', 'cache': 'key body saved', 'cache_access': 'key accessed',
    'usage': 'workspace tag count last', 'accepted': 'workspace tag body last',
    'resources': 'id kind parent name body', 'asset_sources': 'sha256 provider model_id version_id body saved',
    'generation_jobs': 'id body created', 'stage_editor_local': 'key body',
    'chain_runs': 'id workspace created body', 'chain_items': 'id run round stage position body',
    'chain_attempts': 'id item created body', 'chain_results': 'id item node position body',
    'input_queue': 'id owner position body', 'input_queue_control': 'owner body',
    'work_queue': 'id position body', 'workflow_receipts': 'id error',
    'workflow_deletions': 'id body', 'workspace_operations': 'id server route ended status',
    'clean_exports': 'id time body', 'clean_presets': 'name body',
}
SCHEMAS = {
    'studio': STUDIO,
    'execution-index': {'owners': 'token body', 'tickets': 'token position'},
    'notebook': {'settings': 'scope value', 'versions': 'id path at digest raw reason'},
    'catalog': {
        'source': 'id kind path parent_id generation state current_sha removed copied_from',
        'document': 'id source rel file_key sha state body size modified',
        'archive': 'source sha object filename note imported original_path',
        'story': 'source sha story title', 'message': 'source sha story node body day',
    },
    'synthetic-jobs': {'identity': 'slot instance', 'jobs': 'job_id principal idem fingerprint receipt'},
    'integration': {'collections': 'key path hash', 'jobs': 'id body created',
                    'workflow_transfers': 'id library body created status error'},
}
FROZEN = {'document', 'content_history', 'stage_journal', 'generation_jobs',
          'chain_runs', 'chain_items', 'chain_attempts', 'chain_results',
          'input_queue', 'input_queue_control', 'work_queue'}
INFLIGHT = {'submitted', 'collecting', 'preparing', 'unconfirmed', 'cancel_requested'}
JOB_INFLIGHT = INFLIGHT | {'submitting', 'queued', 'running', 'results_pending'}


def quoted(name):
    return '"' + name.replace('"', '""') + '"'


def hashed(value):
    return hashlib.sha256(codec.canonical(value)).hexdigest()


def cell(value):
    return {'blob': value.hex()} if isinstance(value, bytes) else value


def session_data(relative):
    """Only workspace-level, per-launch data; never documents or user files."""
    return tuple(part.casefold() for part in Path(relative).parts[:2]) == ('data', 'sessions')


def excluded(relative):
    name = Path(relative).name
    return session_data(relative) or re.fullmatch(r'before-import-.*\.sqlite3(?:-wal|-shm|-journal)?', name) is not None


def database_kind(relative):
    parts = Path(relative).parts
    if parts == ('data', 'studio.sqlite3') or (len(parts) == 4 and parts[:2] == ('data', 'documents') and parts[-1] == 'studio.sqlite3'):
        return 'studio'
    for name in ('execution-index', 'notebook', 'integration'):
        if parts == ('data', name + '.sqlite3'):
            return name
    if parts == ('data', 'work-resources', 'catalog.sqlite'):
        return 'catalog'
    if parts[0] == 'data' and parts[-1] == 'synthetic-jobs.sqlite3' and any('mcp' in p or 'probe' in p for p in parts[1:-1]):
        return 'synthetic-jobs'
    return None


def classify(source, manifest):
    selected, databases, unknown = {}, {}, []
    for relative, info in manifest.items():
        parts = Path(relative).parts
        if parts[0] not in ('files', 'data') or excluded(relative):
            continue
        path = source / relative
        with path.open('rb') as stream:
            header = stream.read(16)
        kind = database_kind(relative)
        sqlite = header == b'SQLite format 3\x00' or path.suffix.casefold() in {'.sqlite', '.sqlite3', '.db'}
        if kind or sqlite:
            if not kind or header != b'SQLite format 3\x00':
                unknown.append(relative)
            else:
                selected[relative] = 'database'
                databases[relative] = kind
        elif any(relative.endswith(s) for s in SIDECARS):
            selected[relative] = 'sidecar'
        elif parts[0] == 'files':
            selected[relative] = 'file'
        elif path.suffix.casefold() in IMAGES:
            selected[relative] = 'image' if any(p in ('originals', 'thumbnails') for p in parts[1:-1]) else 'file'
        elif path.suffix.casefold() in {'.json', '.jsonl', '.md', '.txt', '.log', '.lock', '.yaml', '.yml', '.toml', '.csv', '.pcs-chat', '.pcs-category', '.pcs-workflow'}:
            selected[relative] = 'file'
        elif parts[1:2] == ('work-resources',) and (path.suffix.casefold() in {'.zip', '.bin'} or path.name in ('COMPLETE', 'INCOMPLETE')):
            selected[relative] = 'file'
        else:
            unknown.append(relative)
    for relative, kind in selected.items():
        if kind == 'sidecar' and not any(relative == db + suffix for db in databases for suffix in SIDECARS):
            unknown.append(relative)
    if unknown:
        raise MigrationFailure('找到未支援的 SQLite 或資料類型，搬遷已停止。', locations=sorted(set(unknown)))
    return selected, databases


def lexical_path(value):
    """Normalize an absolute path without inspecting it (aliases may not exist)."""
    text = str(value).replace('\\', '/')
    if not text or '\x00' in text:
        raise ValueError()
    drive, tail = ntpath.splitdrive(text)
    if drive:
        if not tail.startswith('/') and not (drive.startswith('//') and not tail):
            raise ValueError()
        return ntpath.normpath(text).replace('\\', '/')
    if text.startswith('/'):
        return posixpath.normpath(text)
    raise ValueError()


def old_pattern(prefix):
    # Embedded strings and either slash convention, but not old-copy siblings.
    root = prefix.rstrip('/')
    return re.compile(re.escape(root) + r'(?=$|[/\"\'<>])' if root else '/', re.IGNORECASE)


class Relocation:
    def __init__(self, source, staging, aliases=()):
        self.source, self.staging = source, staging
        aliases = tuple(aliases)
        try:
            prefixes = [lexical_path(source), *(lexical_path(alias) for alias in aliases)]
        except ValueError:
            raise MigrationFailure('舊位置別名必須是絕對路徑。') from None
        self.patterns = [old_pattern(prefix) for prefix in prefixes]
        self.prefixes = sorted(set(prefixes), key=len, reverse=True)
        self.aliases = [dict(path=str(alias), hits=0) for alias in aliases]

    def old(self, value):
        return isinstance(value, str) and any(pattern.search(value.replace('\\', '/')) for pattern in self.patterns)

    def observe(self, value):
        """Count original JSON scalars/keys once, never verification/rewrite passes."""
        if isinstance(value, dict):
            for key, item in value.items():
                self.observe(key)
                self.observe(item)
        elif isinstance(value, list):
            for item in value:
                self.observe(item)
        elif isinstance(value, str):
            for receipt, pattern in zip(self.aliases, self.patterns[1:]):
                receipt['hits'] += sum(1 for _ in pattern.finditer(value.replace('\\', '/')))

    def path(self, value, *, folders=('files', 'data')):
        if not isinstance(value, str):
            return value
        try:
            path = lexical_path(value)
        except ValueError:
            return value
        for prefix in self.prefixes:
            for folder in folders:
                base = prefix.rstrip('/') + '/' + folder
                if path.casefold() == base.casefold():
                    return str(self.staging / folder)
                if path.casefold().startswith(base.casefold() + '/'):
                    return str(self.staging / folder / Path(*path[len(base) + 1:].split('/')))
        return value

    def scan(self, value, file, pointer='', *, frozen=()):
        problems = []
        if pointer in frozen:
            return problems
        if isinstance(value, dict):
            for key, item in value.items():
                part = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                if self.old(key):
                    # Do not expose a key that may itself contain private text.
                    problems.append(file + '#' + pointer + '/<key>')
                problems += self.scan(item, file, part, frozen=frozen)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                problems += self.scan(item, file, pointer + '/' + str(index), frozen=frozen)
        elif self.old(value):
            problems.append(file + '#' + pointer)
        return problems

    def owner(self, value):
        value = copy.deepcopy(value)
        if not isinstance(value, dict) or not isinstance(value.get('path'), str) or not isinstance(value.get('receipt'), dict) or not isinstance(value['receipt'].get('path'), str):
            raise MigrationFailure('任務位置紀錄格式不受支援。', locations=['data/execution-index.sqlite3:owners/body'])
        value['path'] = self.path(value['path'])
        value['receipt']['path'] = self.path(value['receipt']['path'])
        return value

    def config(self, relative, value):
        value = copy.deepcopy(value)
        try:
            if relative == 'data/workspace-layout.json':
                if value['version'] != 1 or not isinstance(value['panes'], list):
                    raise ValueError()
                for pane in value['panes']:
                    for view in pane['views']:
                        if view.get('path') is not None and not isinstance(view['path'], str):
                            raise ValueError()
                        if 'path' in view:
                            view['path'] = self.path(view['path'])
            elif relative == 'data/workspace-tree.json':
                if value['version'] != 1 or not isinstance(value['expanded'], list) or any(not isinstance(v, str) for v in value['expanded']):
                    raise ValueError()
                if value.get('selected') is not None and not isinstance(value['selected'], str):
                    raise ValueError()
                value['expanded'] = [self.path(v, folders=('files',)) for v in value['expanded']]
                if 'selected' in value:
                    value['selected'] = self.path(value['selected'], folders=('files',))
            elif relative == 'data/recent-files.json':
                if not isinstance(value, list) or any(not isinstance(v, str) for v in value):
                    raise ValueError()
                value = [self.path(v) for v in value]
            elif relative == 'data/linked-folders.json':
                if value['version'] != 1 or not isinstance(value['roots'], list) or any(not isinstance(v, str) for v in value['roots']):
                    raise ValueError()
                if value.get('current') is not None and not isinstance(value['current'], str):
                    raise ValueError()
                value['roots'] = [self.path(v) for v in value['roots']]
                if 'current' in value:
                    value['current'] = self.path(value['current'])
            elif relative == 'data/md-models.json':
                if value['version'] != 1 or not isinstance(value['models'], dict):
                    raise ValueError()
                models = {self.path(k): v for k, v in value['models'].items()}
                if len(models) != len(value['models']) or any(not isinstance(v, str) for v in models.values()):
                    raise ValueError()
                value['models'] = models
            elif relative == 'data/local_ai/registry.json':
                if value['version'] != 1 or not isinstance(value.get('models', []), list):
                    raise ValueError()
                settings = value.get('settings', {})
                if not isinstance(settings, dict):
                    raise ValueError()
                for record, field in [(value, 'model_folder'), (settings, 'runtime_path')]:
                    if field in record:
                        if not isinstance(record[field], str):
                            raise ValueError()
                        original = record[field]
                        record[field] = self.path(original)
                        if record[field] != original:
                            target = Path(record[field])
                            if not (target.is_dir() if field == 'model_folder' else target.is_file()):
                                raise ValueError()
                for model in value.get('models', []):
                    if not isinstance(model, dict) or not isinstance(model['path'], str):
                        raise ValueError()
                    original = model['path']
                    model['path'] = self.path(original)
                    if model['path'] != original and not Path(model['path']).is_file():
                        raise ValueError()
        except (TypeError, ValueError, KeyError, AttributeError):
            raise MigrationFailure('設定資料格式不受支援。', locations=[relative]) from None
        return value


def recovery_snapshot(relative):
    parts = Path(relative).parts
    return Path(relative).suffix.casefold() == '.json' and (
        len(parts) == 3 and parts[:2] == ('data', 'recovery') or
        len(parts) == 5 and parts[:2] == ('data', 'documents') and parts[3] == 'recovery')


def skip_recovery(source, selected, relocation):
    """Drop old-location snapshots before copying; source inventory stays whole."""
    skipped = []
    for relative in list(selected):
        if not recovery_snapshot(relative):
            continue
        try:
            value = codec.strict_loads((source / relative).read_text(encoding='utf-8-sig'))
        except (ValueError, UnicodeError, TypeError):
            raise MigrationFailure('JSON 資料無法辨識。', locations=[relative]) from None
        if relocation.scan(value, relative):
            relocation.observe(value)
            skipped.append(relative)
            del selected[relative]
    return sorted(skipped)


def configs(staging, selected, relocation):
    """Only schema-owned fields change; every other JSON string/key is scanned."""
    expected, problems = {}, []
    for relative in selected:
        path = staging / relative
        if path.suffix not in ('.json', '.jsonl'):
            continue
        try:
            raw = path.read_text(encoding='utf-8-sig')
            values = [codec.strict_loads(line) for line in raw.splitlines() if line.strip()] if path.suffix == '.jsonl' else [codec.strict_loads(raw)]
            for value in values:
                relocation.observe(value)
            moved = [relocation.config(relative, value) for value in values]
        except (ValueError, UnicodeError, TypeError) as exc:
            if isinstance(exc, MigrationFailure):
                raise
            raise MigrationFailure('JSON 資料無法辨識。', locations=[relative]) from None
        for index, value in enumerate(moved):
            if not relative.startswith('data/work-resources/'):
                problems += relocation.scan(value, relative, '/' + str(index) if path.suffix == '.jsonl' else '')
        if moved != values:
            path.write_text(json.dumps(moved[0], ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        expected[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    if problems:
        raise MigrationFailure('找到清單以外的舊路徑，搬遷已停止。', locations=sorted(set(problems)))
    return expected


def tables(db, kind, relative):
    names = [row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    problems = []
    result = {}
    for table in names:
        columns = tuple(r[1] for r in db.execute('PRAGMA table_info(' + quoted(table) + ')'))
        expected = SCHEMAS[kind].get(table)
        if expected is None or set(columns) != set(expected.split()):
            problems.append(relative + ':' + table)
        result[table] = columns
    if db.execute("SELECT 1 FROM sqlite_master WHERE type IN ('trigger','view') LIMIT 1").fetchone():
        problems.append(relative + ':sqlite_master')
    required = {'execution-index': {'owners', 'tickets'}, 'notebook': {'settings', 'versions'},
                'catalog': {'source', 'document', 'archive', 'story', 'message'},
                'synthetic-jobs': {'identity', 'jobs'}, 'integration': {'collections', 'jobs', 'workflow_transfers'}}
    if kind == 'studio' and not {'document', 'stage_journal'}.intersection(names):
        problems.append(relative + ':document/stage_journal')
    for missing in required.get(kind, set()) - set(names):
        problems.append(relative + ':' + missing)
    if problems:
        raise MigrationFailure('資料庫包含未知結構，搬遷已停止。', locations=problems)
    return result


def moved_row(kind, table, columns, row, relocation):
    row = list(row)
    if kind == 'execution-index' and table == 'owners':
        index = columns.index('body')
        old = codec.strict_loads(row[index])
        new = relocation.owner(old)
        if old != new:
            row[index] = codec.canonical(new).decode('utf-8')
    elif kind == 'notebook' and table == 'versions':
        index = columns.index('path')
        row[index] = relocation.path(row[index])
    return row


def audit_database(db, kind, relative, relocation):
    schema = tables(db, kind, relative)
    problems, inflight = [], []
    resources = 0
    for table, columns in schema.items():
        for raw in db.execute('SELECT * FROM ' + quoted(table)):
            record = dict(zip(columns, raw))
            for value in raw:
                if isinstance(value, str):
                    try:
                        value = codec.strict_loads(value)
                    except (ValueError, TypeError):
                        pass
                    relocation.observe(value)
            if kind == 'catalog' and table == 'source' and relocation.path(record['path'], folders=('files',)) != record['path']:
                resources += 1
            if kind == 'studio' and table in FROZEN and 'body' in columns:
                try:
                    body = codec.strict_loads(record['body'])
                except (ValueError, TypeError):
                    if table == 'content_history':
                        body = None
                    else:
                        raise MigrationFailure('資料庫 JSON 無法辨識。', locations=[relative + ':' + table + '/body']) from None
                active_states = INFLIGHT if table == 'stage_journal' else JOB_INFLIGHT
                if isinstance(body, dict) and (body.get('status') in active_states or body.get('state') in active_states or body.get('cancel_requested') or
                        record.get('kind', body.get('kind')) == 'input' and body.get('sent') and not body.get('ended')):
                    inflight.append(dict(file=relative, table=table, id=str(record.get('id', ''))))
            if kind == 'studio' and table == 'workspace_operations' and not record['ended']:
                inflight.append(dict(file=relative, table=table, id=record['id']))
            mapped = moved_row(kind, table, columns, raw, relocation)
            for column, value in zip(columns, mapped):
                if not isinstance(value, str):
                    continue
                if kind == 'catalog' or kind == 'studio' and table in FROZEN and column == 'body':
                    continue
                at = relative + ':' + table + '/' + column
                if kind == 'execution-index' and table == 'owners' and column == 'body':
                    problems += relocation.scan(codec.strict_loads(value), at,
                        frozen=('/document', '/runtime_document', '/baseline'))
                elif kind == 'studio' and table == 'resources' and column == 'body':
                    body = codec.strict_loads(value)
                    relative_image = body.get('original_relative') if isinstance(body, dict) else None
                    managed = (body.get('owned') and isinstance(relative_image, str) and
                               not Path(relative_image).is_absolute() and '..' not in Path(relative_image).parts and
                               Path(relative_image).parts[:1] == ('originals',) and
                               (relocation.staging / Path(relative).parent / relative_image).is_file())
                    problems += relocation.scan(body, at, frozen=('/path',) if managed else ())
                else:
                    try:
                        decoded = codec.strict_loads(value)
                    except (ValueError, TypeError):
                        decoded = value
                    problems += relocation.scan(decoded, at)
    if problems:
        raise MigrationFailure('找到清單以外的舊路徑，搬遷已停止。', locations=sorted(set(problems)))
    return schema, inflight, resources


def logical_inventory(db, kind, schema, *, relocation=None, new_journal=False):
    result = {'tables': {}}
    for table, columns in sorted(schema.items()):
        if table == 'stage_journal' and kind == 'studio':
            continue
        rows = []
        for row in db.execute('SELECT * FROM ' + quoted(table)):
            row = moved_row(kind, table, columns, row, relocation) if relocation else row
            rows.append(codec.canonical([cell(value) for value in row]).decode('utf-8'))
        result['tables'][table] = dict(count=len(rows), sha256=hashed(sorted(rows)))
        if kind == 'studio' and table == 'document':
            from .snapshots import revision
            documents = []
            for ident, raw in db.execute('SELECT id,body FROM document ORDER BY id'):
                body = codec.strict_loads(raw)
                history = body.get('content_history')
                entries = history.get('entries', []) if isinstance(history, dict) else history or []
                documents.append(dict(id=ident, sha256=hashed({k: v for k, v in body.items() if k != 'content_history'}),
                                      revision=revision(body), content_history=dict(count=len(entries), sha256=hashed(history))))
            result['documents'] = documents
    if kind == 'studio':
        if new_journal:
            # Read through the codec API, without StageStore's restart writes.
            rows = db.execute('SELECT id,kind,workspace,owner,position,body,content_paths,projection FROM stage_header ORDER BY position,id').fetchall()
            bodies = codec.records(db, [(r[0], r[5], r[6], r[7]) for r in rows])
        elif 'stage_journal' in schema:
            rows = db.execute('SELECT id,kind,workspace,owner,position,body FROM stage_journal ORDER BY position,id').fetchall()
            bodies = [codec.strict_loads(row[5]) for row in rows]
        else:
            rows, bodies = [], []
        counts = Counter((row[1], codec.canonical(body.get('status')).decode('utf-8')) for row, body in zip(rows, bodies))
        result['journal'] = dict(count=len(rows), records={row[0]: hashed(body) for row, body in zip(rows, bodies)},
            positions=[[row[0], row[4]] for row in rows], identity_sha256=hashed([list(row[:5]) for row in rows]),
            kind_status=[dict(kind=kind, status=status, count=count) for (kind, status), count in sorted(counts.items())])
    return result


def rewrite_database(db, kind, schema, relocation):
    with db:
        for table, columns in schema.items():
            if (kind, table) not in {('execution-index', 'owners'), ('notebook', 'versions')}:
                continue
            for record in db.execute('SELECT rowid,* FROM ' + quoted(table)).fetchall():
                moved = moved_row(kind, table, columns, record[1:], relocation)
                if moved != list(record[1:]):
                    db.execute('UPDATE ' + quoted(table) + ' SET ' + ','.join(quoted(c) + '=?' for c in columns) + ' WHERE rowid=?', (*moved, record[0]))


def verify_owners(db, staging):
    count = 0
    for raw, in db.execute('SELECT body FROM owners'):
        owner = codec.strict_loads(raw)
        for path in (owner['path'], owner['receipt']['path']):
            if not Path(path).is_relative_to(staging / 'files'):
                raise MigrationFailure('任務文件不在已搬遷的 files 資料夾。', locations=['data/execution-index.sqlite3:owners/body'])
        count += 1
    return count
