"""Bounded immutable local bundles. Completion is a last, verified receipt."""
from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import uuid

from .conversation_archive import canonical, checkpoint, decode_json
from .local_documents import paths
from .local_documents.directory_pin import pinned_target, pinned_parent
from .local_documents.windows import reader

MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024
MAX_FILES = 2000
START = '保存開始.json'
COMPLETE = '保存完成.json'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def directory_identity(root):
    info = paths.root_path(root).stat()
    return info.st_dev, info.st_ino


def verify_directory(root, expected):
    if directory_identity(root) != expected:
        raise ValueError('選定資料夾已被替換，尚未保存；請重新選擇位置。')


def read_local(path, limit=MAX_FILE, cancel=None):
    path = Path(path).absolute()
    with pinned_parent(path) if os.name == 'nt' else nullcontext():
        paths.root_path(path.parent)
        if paths.is_link(path):
            raise ValueError('檔案不能是連結或重解析點。')
        with reader(path) if os.name == 'nt' else path.open('rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or before.st_size > limit:
                raise ValueError('檔案不是一般檔案，或超過容量限制：' + path.name)
            blocks, used = [], 0
            while True:
                checkpoint(cancel)
                block = stream.read(min(1024 * 1024, limit + 1 - used))
                if not block:
                    break
                blocks.append(block)
                used += len(block)
                if used > limit:
                    raise ValueError('檔案超過容量限制：' + path.name)
            after = os.fstat(stream.fileno())
            current = path.stat()
            identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
            if identity(before) != identity(after) or identity(after) != identity(current):
                raise ValueError('檔案讀取期間已改變，請重新預覽：' + path.name)
            return b''.join(blocks)


def inventory(files, *, hash_contents=True):
    if len(files) > MAX_FILES or sum(len(data) for _, data in files) > MAX_TOTAL:
        raise ValueError('本次檔案超過 2000 個或 512 MiB，請縮小範圍。')
    seen, names, rows = set(), set(), []
    for name, data in files:
        parts = paths.relative_parts(name)
        key = name.casefold()
        if key in seen or key in (START.casefold(), COMPLETE.casefold()):
            raise ValueError('檔案名稱重複或使用保留名稱：' + name)
        if len(data) > MAX_FILE:
            raise ValueError('單檔超過 256 MiB：' + name)
        seen.add(key)
        names.update('/'.join(parts[:i]).casefold() for i in range(1, len(parts)))
        row = dict(path=name, size=len(data))
        if hash_contents:row['sha256'] = digest(data)
        rows.append(row)
    if seen & names:
        raise ValueError('檔案與資料夾名稱衝突。')
    return rows


@dataclass(frozen=True)
class Bundle:
    files: tuple
    metadata: dict

    def validate(self):
        return inventory(self.files)


def write_new(root, relative, bundle, cancel=None, progress=lambda _: None, *, root_identity=None):
    """Never replace an existing destination; retain incomplete work for recovery."""
    rows = bundle.validate()
    expected = root_identity if root_identity is not None else directory_identity(root)
    checkpoint(cancel)
    with pinned_target(root, relative) as folder:
        verify_directory(root, expected)
        folder.mkdir()  # exclusive creation is the ownership boundary
        with pinned_target(folder, START) as started:
            operation = uuid.uuid4().hex
            _write(started, canonical(dict(version=1, state='incomplete', operation=operation)))
            for index, (name, data) in enumerate(bundle.files, 1):
                checkpoint(cancel)
                progress(f'保存 {index}/{len(rows)}：{name}')
                with pinned_target(folder, name, create_parents=True) as target:
                    _write(target, data)
            for row in rows:
                checkpoint(cancel)
                raw = read_local(paths.checked(folder, row['path']), cancel=cancel)
                if len(raw) != row['size'] or digest(raw) != row['sha256']:
                    raise ValueError('保存後內容已改變；未標記完成。')
            checkpoint(cancel)
            with pinned_target(folder, COMPLETE) as target:
                _write(target, canonical(dict(version=1, state='complete', operation=operation,
                                              files=rows, metadata=bundle.metadata)))
    return folder


def _write(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def load_record(path, kind):
    raw = decode_json(read_local(path, 8 * 1024 * 1024))
    if not isinstance(raw, dict) or raw.get('version') != 1 or raw.get('kind') != kind:
        raise ValueError('此關係資料的格式尚不支援；原件已保留。')
    return raw
