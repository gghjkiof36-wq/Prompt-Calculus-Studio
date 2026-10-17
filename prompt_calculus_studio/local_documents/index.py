"""Rebuildable metadata only; the files remain the sole content authority."""
from dataclasses import asdict, dataclass
import json
import os
from pathlib import Path
import uuid

from .identity import Registry
from .io import load
from .paths import checked, is_link, root_path
from .schema import DocumentError, SUFFIXES


@dataclass(frozen=True)
class Entry:
    relative: str
    kind: str
    size: int
    mtime_ns: int


def scan(root):
    """Lazy directory inventory; no document is parsed by this iterator."""
    root = root_path(root)
    pending = [root]
    seen = set()
    suffixes = {v: k for k, v in SUFFIXES.items()} | {'.md': 'markdown'}
    while pending:
        directory = pending.pop()
        info = directory.stat()
        identity = info.st_dev, info.st_ino
        if identity in seen:
            continue
        seen.add(identity)
        for path in sorted(directory.iterdir(), key=lambda p: p.name.casefold()):
            if path.name.casefold() in ('.pcs', '.obsidian') or is_link(path):
                continue
            if path.is_dir():
                pending.append(path)
            elif path.is_file() and path.suffix.casefold() in suffixes:
                info = path.stat()
                yield Entry(path.relative_to(root).as_posix(), suffixes[path.suffix.casefold()], info.st_size, info.st_mtime_ns)


def _inspect(root, entry):
    if entry.kind == 'markdown':
        return dict(**asdict(entry), title=Path(entry.relative).stem), None
    loaded = load(root, entry.relative)
    document = loaded.document
    payload = document['payload']
    title = payload['category']['name'] if document['kind'] == 'category' else payload['state']['workspaces'][0]['name']
    return dict(**asdict(entry), title=title, document_id=document['document_id'],
                revision=document['revision']['id'], sha256=loaded.receipt.sha256,
                issues=list(loaded.issues)), loaded.receipt


def inspect_entry(root, entry):
    return _inspect(root, entry)[0]


def rebuild(root):
    entries = []
    errors = []
    registry = Registry()
    for entry in scan(root):
        try:
            summary, receipt = _inspect(root, entry)
            if receipt is not None:
                status = registry.register(receipt)
                summary['identity_status'] = status
                if status in ('duplicate-id', 'changed-identity'):
                    errors.append(dict(path=entry.relative, code=status,
                                       other=(registry.by_id.get(receipt.document_id) or
                                              registry.by_file[(receipt.device, receipt.inode)]).path))
            entries.append(summary)
        except (DocumentError, OSError) as error:
            errors.append(dict(path=entry.relative, code=getattr(error, 'code', 'io'), message=str(error)))
    return dict(index_version=1, entries=entries, errors=errors)


def write_cache(root, snapshot):
    # This contains no payload, so deleting it cannot delete unique content.
    allowed = {'relative', 'kind', 'size', 'mtime_ns', 'title', 'document_id', 'revision',
               'sha256', 'issues', 'identity_status'}
    if any(set(entry) - allowed for entry in snapshot['entries']):
        raise ValueError('Index must contain metadata only.')
    path = checked(root, '.pcs/document-index.json')
    path.parent.mkdir(exist_ok=True)
    checked(root, '.pcs/document-index.json')
    temporary = path.with_name('document-index.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            json.dump(snapshot, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return path
