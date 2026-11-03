"""Local source registration, searchable revisions and immutable archive carriers."""
from contextlib import contextmanager, ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import uuid

from ..conversation_archive import Limits, checkpoint, fingerprint
from ..local_documents import paths
from ..local_documents.directory_pin import pinned_target
from ..retained_files import Bundle, digest, directory_identity, write_new
from . import source_io
from .stories import stories

SCHEMA = """
CREATE TABLE IF NOT EXISTS source(
 id TEXT PRIMARY KEY, kind TEXT NOT NULL, path TEXT NOT NULL, parent_id TEXT NOT NULL,
 generation TEXT NOT NULL DEFAULT '', state TEXT NOT NULL DEFAULT '待建立索引',
 current_sha TEXT NOT NULL DEFAULT '', removed INTEGER NOT NULL DEFAULT 0, copied_from TEXT);
CREATE TABLE IF NOT EXISTS document(
 id TEXT PRIMARY KEY, source TEXT NOT NULL, rel TEXT NOT NULL, file_key TEXT NOT NULL,
 sha TEXT NOT NULL, state TEXT NOT NULL, body TEXT NOT NULL, size INTEGER, modified TEXT);
CREATE INDEX IF NOT EXISTS doc_source ON document(source,rel);
CREATE TABLE IF NOT EXISTS archive(
 source TEXT, sha TEXT, object TEXT NOT NULL, filename TEXT NOT NULL, note TEXT NOT NULL,
 imported TEXT NOT NULL, original_path TEXT NOT NULL, PRIMARY KEY(source,sha));
CREATE TABLE IF NOT EXISTS story(
 source TEXT, sha TEXT, story TEXT, title TEXT, PRIMARY KEY(source,sha,story));
CREATE TABLE IF NOT EXISTS message(
 source TEXT, sha TEXT, story TEXT, node TEXT, body TEXT, day TEXT);
CREATE INDEX IF NOT EXISTS msg_story ON message(source,sha,story);
"""


def token():
    return uuid.uuid4().hex


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def root_key(identity):
    return ':'.join(str(v) for v in identity)


def root_identity(source):
    return tuple(int(v) for v in source['parent_id'].split(':'))


@contextmanager
def connection(store):
    with pinned_target(store.base, 'catalog.sqlite') as path, ExitStack() as held:
        if directory_identity(store.base) != store.base_id:
            raise ValueError('資源管理目錄已被替換。')
        for suffix in ('', '-journal'):
            held.enter_context(source_io.pin_database_file(Path(str(path)+suffix)))
        if any(os.path.lexists(str(path)+s) for s in ('-wal','-shm')):
            raise ValueError('資源索引含有未預期的共用檔案。')
        db = sqlite3.connect(path, timeout=8)
        db.row_factory = sqlite3.Row
        try:
            if db.execute('PRAGMA journal_mode=PERSIST').fetchone()[0] != 'persist':
                raise ValueError('無法保護資源索引的寫入紀錄。')
            db.execute('PRAGMA trusted_schema=OFF')
            with db:
                yield db
        finally:
            db.close()


class Catalog:
    def __init__(self, data):
        self.data = paths.root_path(data)
        with pinned_target(self.data, 'work-resources/catalog.sqlite', create_parents=True) as path:
            self.base = path.parent
            self.base_id = directory_identity(self.base)
        for name in ('originals', 'bundles'):
            with pinned_target(self.base, name+'/record', create_parents=True):
                pass
        self.originals = self.base/'originals'
        self.bundles = self.base/'bundles'
        self.originals_id = directory_identity(self.originals)
        self.bundles_id = directory_identity(self.bundles)
        with connection(self) as db:
            db.executescript(SCHEMA)
            if 'original_path' not in {r[1] for r in db.execute('PRAGMA table_info(archive)')}:
                db.execute("ALTER TABLE archive ADD COLUMN original_path TEXT NOT NULL DEFAULT ''")

    def register(self, value, kind='folder', *, copied_from=None):
        if kind not in ('folder', 'archive', 'copy'):
            raise ValueError('來源類型無效。')
        path = Path(value)
        root = path if kind == 'folder' else path.parent
        if self.data == path or self.data in path.parents or (kind == 'folder' and path in self.data.parents):
            raise ValueError('請選擇獨立的內容資料夾，排除程式內部資料。')
        if kind != 'folder':
            source_io.parts(path.name)
        with source_io.directory(root) as (_, _, ident), connection(self) as db:
            other = db.execute('SELECT * FROM source WHERE kind=? AND path=? COLLATE NOCASE AND removed=0',
                               (kind, str(path))).fetchone()
            if other:
                if other['parent_id'] != root_key(ident):
                    raise ValueError('原資料夾已被替換，請重新指定。')
                return other['id']
            sid = token()
            db.execute('INSERT INTO source(id,kind,path,parent_id,copied_from) VALUES(?,?,?,?,?)',
                       (sid, kind, str(path), root_key(ident), copied_from))
        return sid

    def sources(self):
        with connection(self) as db:
            return [dict(row) for row in db.execute('SELECT * FROM source WHERE removed=0 ORDER BY rowid')]

    def source(self, sid):
        with connection(self) as db:
            row = db.execute('SELECT * FROM source WHERE id=?', (sid,)).fetchone()
        if not row or row['removed']:
            raise ValueError('來源已移出管理清單。')
        return dict(row)

    def remove(self, sid):
        with connection(self) as db:
            db.execute('UPDATE source SET removed=1, generation=? WHERE id=?', (token(), sid))

    def relink(self, sid, value):
        old = self.source(sid)
        path = Path(value)
        root = path if old['kind'] == 'folder' else path.parent
        if path == self.data or self.data in path.parents or (old['kind'] == 'folder' and path in self.data.parents):
            raise ValueError('不能指定程式內部資料。')
        if old['kind'] != 'folder':
            source_io.parts(path.name)
        with source_io.directory(root) as (_, _, ident), connection(self) as db:
            if db.execute('SELECT 1 FROM source WHERE id<>? AND removed=0 AND kind=? AND path=? COLLATE NOCASE',
                          (sid, old['kind'], str(path))).fetchone():
                raise ValueError('此位置已加入。')
            db.execute("UPDATE source SET path=?,parent_id=?,generation=?,state='待建立索引' WHERE id=? AND removed=0",
                       (str(path), root_key(ident), token(), sid))
            db.execute("UPDATE document SET body='',state='待重建',file_key='' WHERE source=?", (sid,))
            db.execute("UPDATE source SET current_sha='' WHERE id=?", (sid,))

    def rebuild(self, sid, cancel=None, progress=lambda n: None):
        with connection(self) as db:
            gen = token()
            db.execute('UPDATE source SET generation=? WHERE id=? AND removed=0', (gen, sid))
        source = self.source(sid)
        if source['generation'] != gen:
            raise ValueError('索引工作已被取代。')
        try:
            if source['kind'] == 'archive':
                return self._archive(source, cancel)
            from .folder_index import index
            return index(self, source, cancel, progress)
        except (ValueError, OSError) as error:
            with connection(self) as db:
                db.execute('UPDATE source SET state=? WHERE id=? AND generation=?',
                           ('來源需核對 · '+str(error), sid, gen))
            raise

    @staticmethod
    def publish(db, source, state, sha=''):
        # Serialize publication before changing any live row. An owner may be
        # removed/relinked while this worker is preparing its private results.
        if db.execute('UPDATE source SET state=?,current_sha=? WHERE id=? AND generation=? AND removed=0',
                      (state, sha, source['id'], source['generation'])).rowcount != 1:
            raise ValueError('來源已重新指定，此次索引未採用。')

    def _archive(self, source, cancel):
        path = Path(source['path'])
        raw, _ = source_io.read_source(path.parent, path.name, root_identity(source), Limits().source, cancel)
        sha = digest(raw)
        sid = source['id']
        with connection(self) as db:
            known = db.execute('SELECT note FROM archive WHERE source=? AND sha=?', (sid, sha)).fetchone()
            if known:
                checkpoint(cancel)
                self.publish(db, source, known['note'] or '已封存', sha)
                return sha
        try:
            items = stories(path.name, raw, cancel)
            note = '' if items else '封存沒有訊息。'
        except (ValueError, TypeError, OverflowError, RecursionError) as error:
            items, note = [], '已保留原件 · '+str(error)
        obj = token()
        write_new(self.originals, obj, Bundle((('source.bin', raw),), dict(source=sid, sha256=sha, name=path.name)),
                  cancel, root_identity=self.originals_id)
        # Parsing a retained snapshot may take time. Verify the enrolled source
        # again, including its actual contents, before publishing as current.
        with source_io.verified_stream(path.parent,path.name,root_identity(source)) as (stream,info):
            if info.st_size!=len(raw) or fingerprint(stream,cancel)!=sha:
                raise ValueError('解析期間來源內容已變更，請重新建立索引。')
        with connection(self) as db:
            checkpoint(cancel)
            self.publish(db, source, note or '已封存', sha)
            db.execute('INSERT INTO archive VALUES(?,?,?,?,?,?,?)', (sid, sha, obj, path.name, note, now(), str(path)))
            for item in items:
                checkpoint(cancel)
                db.execute('INSERT INTO story VALUES(?,?,?,?)', (sid, sha, item.key, item.title))
                def messages():
                    for node in item.nodes.values():
                        checkpoint(cancel)
                        stamp = node['timestamp']
                        try:
                            day = datetime.fromtimestamp(stamp, timezone.utc).date().isoformat() if stamp is not None else ''
                        except (ValueError, OSError, OverflowError):
                            day = ''
                        if node['role'] != 'root':
                            yield sid, sha, item.key, node['node_id'], node['text'], day
                db.executemany('INSERT INTO message VALUES(?,?,?,?,?,?)', messages())
            checkpoint(cancel)
        return sha

    def original(self, sid, sha, cancel=None):
        with connection(self) as db:
            row = db.execute('SELECT * FROM archive WHERE source=? AND sha=?', (sid, sha)).fetchone()
        if not row:
            raise ValueError('找不到此版本的封存原件。')
        raw, _ = source_io.read_source(self.originals, row['object']+'/source.bin', self.originals_id, Limits().source, cancel)
        if digest(raw) != sha:
            raise ValueError('封存原件與保存版本不符。')
        return raw, row['filename']

    def results(self, sid='', query='', offset=0, limit=50, cancel=None):
        # Literal matching keeps %, _ and quotation marks ordinary search text.
        term = '%'+query.strip().replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
        sql = """
        WITH results AS (
          SELECT d.source, d.id AS item, '' AS story, '' AS node, d.sha, d.rel AS title,
                 s.path AS location, CASE WHEN s.state LIKE '來源需核對%' THEN '需重新核對' ELSE d.state END AS state,
                 'document' AS kind, d.modified AS date
          FROM document d JOIN source s ON s.id=d.source
          WHERE s.removed=0 AND d.state<>'待重建' AND (?='' OR s.id=?) AND
          (d.rel LIKE ? ESCAPE '\\' OR s.path LIKE ? ESCAPE '\\' OR d.body LIKE ? ESCAPE '\\' OR d.modified LIKE ? ESCAPE '\\')
          UNION ALL
          SELECT a.source,a.object,coalesce(t.story,''),'',a.sha,coalesce(t.title,a.filename),
                 coalesce(nullif(a.original_path,''),a.filename),CASE WHEN a.note<>'' THEN a.note WHEN a.sha=s.current_sha THEN '封存原件' ELSE '先前版本' END,
                 'archive',a.imported FROM archive a JOIN source s ON s.id=a.source
                 LEFT JOIN story t ON t.source=a.source AND t.sha=a.sha
          WHERE s.removed=0 AND (?='' OR s.id=?) AND (a.filename LIKE ? ESCAPE '\\' OR
                t.title LIKE ? ESCAPE '\\' OR a.original_path LIKE ? ESCAPE '\\' OR a.imported LIKE ? ESCAPE '\\')
          UNION ALL
          SELECT m.source,a.object,m.story,m.node,m.sha,t.title,coalesce(nullif(a.original_path,''),a.filename),'原訊息','message',m.day
          FROM message m JOIN archive a ON a.source=m.source AND a.sha=m.sha
            JOIN story t ON t.source=m.source AND t.sha=m.sha AND t.story=m.story JOIN source s ON s.id=m.source
          WHERE s.removed=0 AND (?='' OR s.id=?) AND ?<>'' AND
              (m.body LIKE ? ESCAPE '\\' OR m.day LIKE ? ESCAPE '\\' OR m.node LIKE ? ESCAPE '\\')
        ) SELECT * FROM results ORDER BY location,title,kind,date,item,story,node LIMIT ? OFFSET ?
        """
        with connection(self) as db:
            args = (sid,sid,term,term,term,term, sid,sid,term,term,term,term,
                    sid,sid,query.strip(),term,term,term, min(max(1,limit),101), max(0,offset))
            if cancel is not None:
                db.set_progress_handler(lambda:int(cancel.is_set()),4000)
            try:
                return [dict(row) for row in db.execute(sql,args)]
            except sqlite3.OperationalError:
                checkpoint(cancel)
                raise
