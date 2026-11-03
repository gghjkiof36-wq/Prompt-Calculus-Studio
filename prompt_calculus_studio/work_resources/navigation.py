"""Real-directory browsing and purpose labels; no instruction activation."""
import os
from pathlib import Path
from ..conversation_archive import checkpoint
from ..local_documents import paths
from . import source_io
from .catalog import root_identity


def purpose(value):
    path = Path(value)
    name = path.name.casefold()
    if name == 'skill.md':
        return 'skill'
    if name in ('agents.md', 'claude.md'):
        return 'rule'
    if name in ('agent.md', 'agent.toml', 'agent.yaml', 'agent.yml') or 'agents' in [p.casefold() for p in path.parts[:-1]]:
        return 'agent'
    return 'document'


PURPOSES = {'document': '文件', 'rule': '規則', 'skill': 'Skill 定義', 'agent': 'Agent 定義'}


def document_path(store, row):
    source = store.source(row['source'])
    root = Path(source['path'])
    return root / row['title'] if source['kind'] == 'folder' else root


def folder_row(path, sid=''):
    return dict(kind='folder', source=sid, item=str(path), sha='', story='', node='', title=path.name,
                location=str(path), state='資料夾', date='')


def roots(store, workspace_roots):
    result = {}
    for path in workspace_roots:
        path = Path(path)
        if path.is_dir():
            result[os.path.normcase(str(path))] = folder_row(path, store.register(path))
    for source in store.sources():
        if source['kind'] == 'folder':
            path = Path(source['path'])
            result.setdefault(os.path.normcase(str(path)), folder_row(path, source['id']))
    return list(result.values())


def browse(store, root_rows, location, query, kind, offset, limit, order, cancel, source=''):
    """Read only enrolled roots; combine direct subfolders with indexed documents."""
    folders = []
    if location is None:
        if query or kind != 'all':
            for entry in root_rows:
                source_row = store.source(entry['source'])
                if not source_row['generation'] or source_row['state'] == '待建立索引':
                    store.rebuild(source_row['id'], cancel)
        if not query and not source:
            folders = root_rows
        rows, total = store.browse(query, 'archive' if not query and kind == 'all' and not source else kind,
                                   max(0, offset-len(folders)), limit, order, source, cancel)
    else:
        path = Path(location)
        parent = max((r for r in root_rows if path == Path(r['location']) or Path(r['location']) in path.parents),
                     key=lambda r: len(Path(r['location']).parts), default=None)
        if parent is None:
            raise ValueError('此資料夾尚未加入來源。')
        source = store.source(parent['source'])
        root = Path(source['path'])
        rel = path.relative_to(root).as_posix()
        with source_io.directory(root, '' if rel == '.' else rel, root_identity(source)) as (_, directory, _):
            if not source['generation'] or source['state'] == '待建立索引':
                store.rebuild(source['id'], cancel)
            if not query:
                with os.scandir(directory) as entries:
                    for entry in entries:
                        checkpoint(cancel)
                        if entry.name.startswith('.') or entry.name.casefold() in ('assets',):
                            continue
                        child = Path(entry.path)
                        if not paths.is_link(child) and entry.is_dir(follow_symlinks=False):
                            folders.append(folder_row(child, source['id']))
        rows, total = store.browse(query, kind, max(0, offset-len(folders)), limit, order, cancel=cancel,
                                   directory=path, recursive=bool(query))
    folders.sort(key=lambda r: r['title'].casefold())
    shown_folders = folders[offset:offset+limit]
    return shown_folders + rows[:limit-len(shown_folders)], total + len(folders)
