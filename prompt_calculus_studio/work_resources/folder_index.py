"""Stage a cancellable folder scan on disk, then replace one owner's index atomically."""
from datetime import datetime, timezone
import os
from pathlib import Path
import stat
from ..conversation_archive import checkpoint
from ..retained_files import digest
from . import source_io
from .catalog import connection, root_identity, root_key, token

TEXT = {'.md', '.txt', '.rst', '.csv', '.json', '.yaml', '.yml', '.toml', '.jsonl', '.log'}
LIMIT = 8 * 1024 * 1024
MAX_ENTRIES = 100_000


def decode(raw):
    value = raw.decode('utf-8-sig')
    if '\0' in value:
        raise ValueError('不是可預覽的 UTF-8 文字。')
    return value


def entries(root, expected, cancel, issues):
    pending = ['']
    counted = 0
    while pending:
        checkpoint(cancel)
        rel = pending.pop()
        try:
            with source_io.directory(root, rel, expected) as (handle, folder, _):
                with os.scandir(folder if os.name == 'nt' else handle) as listing:
                    for entry in listing:
                        checkpoint(cancel)
                        if entry.name.startswith('.') or entry.name.casefold() in ('assets', 'desktop.ini'):
                            continue
                        counted += 1
                        if counted > MAX_ENTRIES:
                            raise OverflowError('單一來源超過十萬項，請分別加入較小的資料夾。')
                        name = '/'.join(filter(None, (rel, entry.name)))
                        try:
                            source_io.parts(name)
                            info = entry.stat(follow_symlinks=False)
                            if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                                continue
                            if stat.S_ISDIR(info.st_mode):
                                pending.append(name)
                            elif stat.S_ISREG(info.st_mode):
                                yield name
                        except (ValueError, OSError):
                            issues.append(name)
        except (OSError, ValueError):
            if not rel:
                raise
            issues.append(rel+'/')


def index(store, source, cancel, progress):
    sid = source['id']
    location = Path(source['path'])
    folder = source['kind'] == 'folder'
    root = location if folder else location.parent
    expected = root_identity(source)
    issues = []
    names = entries(root, expected, cancel, issues) if folder else (location.name,)
    with connection(store) as db:
        db.execute('CREATE TEMP TABLE scan(rel TEXT,file_key TEXT,sha TEXT,state TEXT,body TEXT,size INTEGER,modified TEXT)')
        indexed = unsupported = total_bytes = 0
        for rel in names:
            checkpoint(cancel)
            if Path(rel).suffix.casefold() not in TEXT:
                unsupported += 1
                continue
            try:
                raw, info = source_io.read_source(root, rel, expected, LIMIT, cancel)
                body = decode(raw)
                total_bytes += len(raw)
                if total_bytes > 512*1024*1024:
                    raise OverflowError('單一來源的文字超過 512 MiB，請分別加入較小的資料夾。')
                row = (rel, root_key((info.st_dev, info.st_ino)), digest(raw), '可用', body, len(raw),
                       datetime.fromtimestamp(info.st_mtime, timezone.utc).date().isoformat())
            except source_io.OutsideSource:
                continue
            except (OSError, ValueError) as error:
                issues.append(rel)
                row = (rel, '', '', '無法讀取 · '+str(error), '', None, '')
            db.execute('INSERT INTO scan VALUES(?,?,?,?,?,?,?)', row)
            indexed += 1
            if indexed % 64 == 0:
                db.commit()
                progress(indexed)
        db.commit()
        checkpoint(cancel)
        with source_io.directory(root, expected=expected):
            summary = f'{indexed} 份文件'
            if unsupported:
                summary += f' · {unsupported} 個格式未支援'
            if issues:
                summary += f' · {len(issues)} 處無法讀取'
            store.publish(db, source, summary)
            old = list(db.execute('SELECT id,rel,file_key,state FROM document WHERE source=?', (sid,)))
            by_key = {v['file_key']: v['id'] for v in old if v['file_key'] and v['state'] != '已移除'}
            by_rel = {v['rel']:v['id'] for v in old}
            present = {r[0] for r in db.execute("SELECT file_key FROM scan WHERE file_key<>''")}
            reserved = {by_key[k] for k in present if k in by_key}
            db.execute("UPDATE document SET state='已移除',body='' WHERE source=?", (sid,))
            for row in db.execute('SELECT * FROM scan'):
                checkpoint(cancel)
                did = by_key.get(row['file_key'])
                if not did:
                    previous = by_rel.get(row['rel'])
                    did = previous if previous and previous not in reserved else token()
                db.execute('INSERT OR REPLACE INTO document VALUES(?,?,?,?,?,?,?,?,?)', (did, sid, *row))
            for name in issues:
                if name.endswith('/'):
                    db.execute("""UPDATE document SET state='無法查閱資料夾' WHERE source=? AND
                        state='已移除' AND substr(rel,1,?)=?""",(sid,len(name),name))
            checkpoint(cancel)
        return summary
