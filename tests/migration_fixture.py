"""Synthetic old delivery only; no installed application or user data is read."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid
import zlib

from prompt_calculus_studio.core import initial_state
from prompt_calculus_studio.migration_files import inventory
from journal_fixtures import bodies


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')


def old_database(path, *, inflight=False):
    path.parent.mkdir(parents=True, exist_ok=True)
    state = initial_state()
    state['content_history'] = dict(version=1, entries=[])
    state['settings']['migration_frozen_path'] = str(path.parent / 'originals' / 'image.png')
    records = bodies()
    for body in records:
        if body['kind'] == 'attempt':
            body.update(status='submitted' if inflight else 'complete', prompt_id='preserved-prompt-id')
        elif body['kind'] == 'input':
            body.update(sent=True, ended=not inflight)
        elif body['kind'] == 'run':
            body.update(status='running' if inflight else 'paused')
        body['frozen_path'] = str(path.parent / 'originals' / 'image.png')
    with closing(sqlite3.connect(path)) as db, db:
        db.execute('CREATE TABLE document(id INTEGER PRIMARY KEY,body TEXT NOT NULL)')
        db.execute('INSERT INTO document VALUES(1,?)', (json.dumps(state, ensure_ascii=False),))
        db.execute('CREATE TABLE stage_journal(id TEXT PRIMARY KEY,kind TEXT,workspace TEXT,owner TEXT,position INTEGER,body TEXT NOT NULL)')
        for index, body in enumerate(records):
            db.execute('INSERT INTO stage_journal VALUES(?,?,?,?,?,?)',
                       (body['id'], body['kind'], body['workspace'], body['owner'], index * 7 + 3, json.dumps(body, ensure_ascii=False)))
        db.execute('CREATE TABLE cache(key TEXT PRIMARY KEY,body TEXT NOT NULL,saved REAL NOT NULL)')
        db.execute('INSERT INTO cache VALUES(?,?,?)', ('wal', 'before crash', 1.0))
        db.execute('CREATE TABLE generation_jobs(id TEXT PRIMARY KEY,body TEXT NOT NULL,created REAL NOT NULL)')
        db.execute('INSERT INTO generation_jobs VALUES(?,?,?)', ('g1', json.dumps(dict(id='g1', state='unconfirmed' if inflight else 'complete', prompt_id='generation-prompt')), 1.0))
    return state


def leave_wal(path):
    script = """import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=WAL')
db.execute('PRAGMA wal_autocheckpoint=0')
db.execute("UPDATE cache SET body='committed in WAL'")
db.commit()
os._exit(0)
"""
    subprocess.run([sys.executable, '-B', '-c', script, str(path)], check=True)
    assert Path(str(path) + '-wal').stat().st_size > 0


def leave_hot_journal(path):
    script = """import os,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
db.execute('PRAGMA journal_mode=DELETE')
db.execute('PRAGMA cache_size=1')
db.execute('BEGIN IMMEDIATE')
db.execute("UPDATE cache SET body=?", ('uncommitted'*100000,))
os._exit(0)
"""
    subprocess.run([sys.executable, '-B', '-c', script, str(path)], check=True)
    journal = Path(str(path) + '-journal')
    assert journal.stat().st_size > 512
    assert journal.read_bytes()[:8] == bytes.fromhex('d9d505f920a163d7')


def delivery(base, *, inflight=False, wal=True, root_database=True):
    source = base / 'old'
    (source / 'files' / 'nested' / 'empty').mkdir(parents=True)
    (source / 'data').mkdir()
    tokens = [str(uuid.uuid4()), str(uuid.uuid4())]
    names = ['first.md', 'nested/second.md']
    for name in names:
        (source / 'files' / name).write_text('# 合成資料\n保留原件。\n', encoding='utf-8')
    if root_database:
        old_database(source / 'data/studio.sqlite3', inflight=inflight)
    for index, token in enumerate(tokens):
        old_database(source / 'data/documents' / token / 'studio.sqlite3',
                     inflight=inflight and not root_database and index == 0)
    primary = source / 'data/studio.sqlite3' if root_database else source / 'data/documents' / tokens[0] / 'studio.sqlite3'
    with closing(sqlite3.connect(source / 'data/execution-index.sqlite3')) as db, db:
        db.executescript('CREATE TABLE owners(token TEXT PRIMARY KEY,body TEXT NOT NULL); CREATE TABLE tickets(token TEXT PRIMARY KEY,position INTEGER NOT NULL);')
        for token, name in zip(tokens, names):
            path = source / 'files' / name
            owner = dict(token=token, path=str(path), receipt=dict(path=str(path), sha256=hashlib.sha256(path.read_bytes()).hexdigest()), document_id=token)
            db.execute('INSERT INTO owners VALUES(?,?)', (token, json.dumps(owner)))
            db.execute('INSERT INTO tickets VALUES(?,?)', (token, 7))
    with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
        db.executescript('CREATE TABLE settings(scope TEXT PRIMARY KEY,value TEXT NOT NULL); CREATE TABLE versions(id INTEGER PRIMARY KEY,path TEXT NOT NULL,at REAL NOT NULL,digest TEXT NOT NULL,raw BLOB NOT NULL,reason TEXT NOT NULL);')
        raw = (source / 'files/first.md').read_bytes()
        db.execute('INSERT INTO versions VALUES(1,?,1,?,?,?)', (str(source / 'files/first.md'), hashlib.sha256(raw).hexdigest(), zlib.compress(raw), '合成'))
        db.execute('INSERT INTO settings VALUES(?,?)', ('editor', '{"mode":"read"}'))
    (source / 'data/work-resources').mkdir()
    from prompt_calculus_studio.work_resources.catalog import SCHEMA
    with closing(sqlite3.connect(source / 'data/work-resources/catalog.sqlite')) as db, db:
        db.executescript(SCHEMA)
        db.execute('INSERT INTO source(id,kind,path,parent_id) VALUES(?,?,?,?)', ('s1', 'folder', str(source / 'files'), 'old-identity'))
        db.execute('INSERT INTO document(id,source,rel,file_key,sha,state,body) VALUES(?,?,?,?,?,?,?)', ('d1', 's1', 'first.md', 'f1', 'sha', 'ready', 'indexed text'))
    write_json(source / 'data/workspace-layout.json', dict(version=1, panes=[dict(views=[dict(id='v1', path=str(source / 'files/first.md'))], current='v1')]))
    write_json(source / 'data/recent-files.json', [str(source / 'files/first.md'), str(base / 'external.md')])
    write_json(source / 'data/linked-folders.json', dict(version=1, roots=[str(source / 'files'), str(base / 'external')], current=str(source / 'files')))
    write_json(source / 'data/md-models.json', dict(version=1, models={str(source / 'files/first.md'): 'model-test'}))
    write_json(source / 'data/settings.json', {'online': False, 'empty_manual': '', 'auto': None})
    for folder in ('originals', 'thumbnails'):
        (primary.parent / folder).mkdir()
        from test_comfy_integration import png
        png(primary.parent / folder / 'image.png', {'fixture': 'synthetic'})
    with closing(sqlite3.connect(primary)) as db, db:
        db.execute('CREATE TABLE resources(id TEXT PRIMARY KEY,kind TEXT,parent TEXT,name TEXT,body TEXT)')
        db.execute('INSERT INTO resources VALUES(?,?,?,?,?)', ('image-1', 'image', '', 'synthetic', json.dumps(dict(
            id='image-1', owned=True, path=str(primary.parent / 'originals/image.png'),
            original_relative='originals/image.png', thumb='thumbnails/image.png'))))
    (source / 'data/before-import-old.sqlite3').write_bytes(b'excluded backup')
    for folder in ('logs', 'temp', 'historical-test', '__pycache__'):
        (source / folder).mkdir()
        (source / folder / 'excluded.txt').write_text('preserved at source')
    if wal:
        leave_wal(primary)
    return source, base / 'new'


def realistic_delivery(base):
    """Observed path shapes only, with invented data and no root studio DB."""
    from PySide6.QtGui import QImage
    from prompt_calculus_studio.draft_cache import DraftCache
    from test_comfy_integration import png
    source, target = delivery(base, root_database=False)
    for index in range(3):
        service = source / 'data/sessions' / uuid.uuid4().hex / 'app-services'
        old_database(service / 'studio.sqlite3', inflight=True)
        preview = service / 'civitai/previews' / (hashlib.sha256(str(index).encode()).hexdigest() + '.image')
        preview.parent.mkdir(parents=True)
        png(preview, {'fixture': 'synthetic'})
        thumbnail = service / 'thumbnails' / (hashlib.sha256(str(index).encode()).hexdigest() + '.jpg')
        thumbnail.parent.mkdir()
        image = QImage(2, 2, QImage.Format.Format_RGB32)
        image.fill(0xff204060)
        assert image.save(str(thumbnail), 'JPEG')
        # A scan would reject this path. It is intentionally inside the skipped
        # session to prove that no settings inspection occurs there.
        write_json(service / 'extension-install.json', {'fixture_path': str(source / 'data')})
        if index == 0:
            leave_hot_journal(service / 'studio.sqlite3')
    (source / 'data/sessions' / uuid.uuid4().hex).mkdir()
    DraftCache(source / 'data').write('local-state', {'settings': {'empty_manual': '', 'auto': None}})
    for index, directory in enumerate(sorted((source / 'data/documents').iterdir())):
        DraftCache(directory).write('execution-close-state', {'token': directory.name, 'state': {'fixture': True}})
        (directory / f'before-import-{index}.sqlite3').write_bytes(b'synthetic excluded backup')
        generation = directory / 'originals/generation'
        generation.mkdir(parents=True)
        png(generation / 'synthetic.png', {'fixture': 'synthetic'})
        assert image.save(str(generation / 'synthetic.jpeg'), 'JPEG')
    write_json(source / 'data/local_ai/registry.json', {
        'version': 1, 'models': [], 'settings': {}, 'model_folder': '', 'model_presets': {}, 'selected_model': ''})
    write_json(source / 'data/workspace-ui.json', {'sidebar_width': 240, 'navigation_position': 'side'})
    write_json(source / 'data/workspace-tree.json', {'version': 1, 'expanded': [], 'selected': '', 'scroll': 0})
    write_json(source / 'data/workspace-connection.json', {'url': 'http://127.0.0.1:8188', 'enabled': False})
    (source / 'data/error.log').write_text('Synthetic log only.\n', encoding='utf-8')
    return source, target


def aliased_delivery(base):
    """Source A contains typed references to absent former delivery B."""
    from prompt_calculus_studio.draft_cache import DraftCache
    from prompt_calculus_studio.local_ai.registry import ModelRegistry
    source, target = realistic_delivery(base)
    alias = base / 'former-delivery'
    folder = source / 'files/models'
    folder.mkdir()
    model = folder / 'synthetic.gguf'
    model.write_bytes(b'GGUF' + (3).to_bytes(4, 'little') + b'synthetic only')
    engine = source / 'files/llama-server.exe'
    engine.write_bytes(b'synthetic only; never executed')
    registry = ModelRegistry(source / 'data')
    record = registry.register(model)
    registry.update(model_folder=str(folder), selected_model=record['id'],
                    settings={**registry.settings, 'runtime_path': str(engine)})

    def previous(value):
        if isinstance(value, dict):
            return {previous(key): previous(item) for key, item in value.items()}
        if isinstance(value, list):
            return [previous(item) for item in value]
        if isinstance(value, str):
            return value.replace(str(source), str(alias))
        return value

    for name in ('workspace-layout.json', 'recent-files.json', 'linked-folders.json', 'md-models.json', 'local_ai/registry.json'):
        path = source / 'data' / name
        write_json(path, previous(json.loads(path.read_text(encoding='utf-8'))))
    write_json(source / 'data/workspace-tree.json', dict(version=1, selected=str(alias / 'files/first.md'),
        expanded=[str(alias / 'files'), str(alias / 'files/nested'), str(base / 'former-delivery-sibling/files'), str(base / 'external')],
        scroll=37, extra={'preserve': True}))
    with closing(sqlite3.connect(source / 'data/execution-index.sqlite3')) as db, db:
        for index, (token, raw) in enumerate(db.execute('SELECT token,body FROM owners').fetchall()):
            body = previous(json.loads(raw))
            if index == 0:
                body['document'] = {'frozen_path': str(alias / 'files/frozen.md')}
            db.execute('UPDATE owners SET body=? WHERE token=?', (json.dumps(body), token))
    with closing(sqlite3.connect(source / 'data/notebook.sqlite3')) as db, db:
        db.execute('UPDATE versions SET path=?', (str(alias / 'files/first.md'),))
    for directory in (source / 'data', next((source / 'data/documents').iterdir())):
        DraftCache(directory).write('document:' + str(alias / 'files/first.md').casefold(), dict(
            path=str(alias / 'files/first.md'), data=str(alias / 'data/sessions' / ('a' * 32)),
            raw='synthetic private recovery content'))
    # The physical source is still an old location even when aliases are given.
    DraftCache(source / 'data').write('document:' + str(source / 'files/first.md').casefold(), dict(
        path=str(source / 'files/first.md'), raw='synthetic private recovery content'))
    return source, target, alias


def source_hashes(source):
    return {name: row['sha256'] for name, row in inventory(source).items()}
