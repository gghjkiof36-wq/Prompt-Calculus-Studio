"""Read receipts, exclusive create and recoverable Windows saves.

A candidate is immutable to the caller. Only status='saved' permits clearing
dirty. Failed or uncertain writes retain visible sibling recovery files;
verified successful writes remove only their own temporary recovery files.
"""
from __future__ import annotations

import copy
from contextlib import ExitStack
from dataclasses import dataclass
import os
from pathlib import Path
import uuid

from . import assets
from .directory_pin import pinned_target
from .paths import checked, read_bytes
from .schema import DocumentError, MAX_BYTES, SUFFIXES, decode, digest, encode, fail


@dataclass(frozen=True)
class LoadReceipt:
    path: str
    device: int
    inode: int
    sha256: str
    document_id: str
    revision: str


@dataclass(frozen=True)
class Loaded:
    document: dict
    receipt: LoadReceipt
    issues: tuple
    dependencies: tuple


@dataclass(frozen=True)
class SaveResult:
    status: str
    document: dict | None = None
    receipt: LoadReceipt | None = None
    recovery_paths: tuple[str, ...] = ()
    error: str = ''
    name_collision: bool = False
    # Exclusive create published this name, even if a later readback failed.
    published: bool = False

    @property
    def dirty(self):
        return self.status != 'saved'


def _receipt(path, raw, info, document):
    return LoadReceipt(str(path), info.st_dev, info.st_ino, digest(raw),
                       document['document_id'], document['revision']['id'])


def _suffix(path, document):
    if path.suffix.casefold() != SUFFIXES[document['kind']]:
        fail('path', '副檔名與文件種類不符。')


def load(root, relative):
    path = checked(root, relative)
    raw, info = read_bytes(path)
    document = decode(raw)
    _suffix(path, document)
    return Loaded(document, _receipt(path, raw, info, document),
                  tuple(assets.inspect(document, path.parent)), tuple(assets.external_dependencies(document)))


def _checkpoint(phase):
    """Fault-injection seam; production performs no additional operation."""


def _write(path, raw):
    with path.open('xb') as output:
        output.write(raw)
        _checkpoint('temporary-written')
        output.flush()
        os.fsync(output.fileno())
        _checkpoint('temporary-flushed')


def _paths(path):
    prefix = path.name + '.recovery-' + uuid.uuid4().hex
    return {role: path.with_name(prefix + '.' + role)
            for role in ('candidate', 'temporary', 'before', 'external')}


def _existing(paths):
    return tuple(str(path) for path in paths.values() if path.exists())


def _cleanup_saved(paths):
    # Run only after publication and readback agree, while the parent directory
    # is still pinned. Older attempts may contain unresolved external changes.
    for path in paths.values():
        try:
            path.unlink(missing_ok=True)
        except OSError:
            # A locked recovery file cannot turn an accepted save into failure.
            # Report any leftovers to the caller without retrying publication.
            pass


def create(root, relative, document):
    recovery = {}
    published = False
    checking_name = False
    protection = ExitStack()
    try:
        raw = encode(document)
        protection.enter_context(pinned_target(root, relative))
        checking_name = True
        path = checked(root, relative, new=True)
        checking_name = False
        _suffix(path, document)
        if not path.parent.is_dir():
            fail('path', '目的資料夾不存在。')
        issues = assets.inspect(document, path.parent)
        if issues:
            fail('assets', '附件尚未完整保存。', issues=issues)
        recovery = _paths(path)
        _write(recovery['candidate'], raw)
        _write(recovery['temporary'], raw)
        checking_name = True
        checked(root, relative, new=True)
        checking_name = False
        _checkpoint('before-create')
        # link is atomic and exclusive: never the overwrite behavior of replace.
        checking_name = True
        os.link(recovery['temporary'], path)
        published = True
        checking_name = False
        recovery['temporary'].unlink()
        _checkpoint('after-create')
        loaded = load(root, relative)
        if loaded.receipt.sha256 != digest(raw):
            # External edit after publication: keep the in-memory candidate too.
            return SaveResult('conflict', recovery_paths=_existing(recovery), error='新建後文件已被外部修改。',
                              published=True)
        _cleanup_saved(recovery)
        return SaveResult('saved', loaded.document, loaded.receipt, _existing(recovery), published=True)
    except (DocumentError, OSError) as error:
        return SaveResult('conflict' if getattr(error, 'code', '') in ('exists', 'collision', 'conflict')
                          or isinstance(error, FileExistsError) else 'failed',
                          recovery_paths=_existing(recovery), error=str(error),
                          name_collision=checking_name and not published and (getattr(error, 'code', '') == 'exists'
                                                            or isinstance(error, FileExistsError)),
                          published=published)
    finally:
        protection.close()


def save(root, relative, document, expected):
    recovery = {}
    replaced = False
    protection = ExitStack()
    try:
        # Validate before any filesystem mutation. Never mutate the dirty model.
        encode(document)
        protection.enter_context(pinned_target(root, relative))
        path = checked(root, relative)
        _suffix(path, document)
        if (not isinstance(expected, LoadReceipt) or expected.path != str(path) or
                expected.document_id != document['document_id'] or expected.revision != document['revision']['id']):
            fail('conflict', '保存對象或基準修訂不同。')
        issues = assets.inspect(document, path.parent)
        if issues:
            fail('assets', '附件未完整保存。', issues=issues)
        if os.name != 'nt':
            fail('unsupported', '此平台尚無已驗證的備份替換；原件保持。')
        candidate = copy.deepcopy(document)
        candidate['revision'] = dict(id=str(uuid.uuid4()), parent=expected.revision)
        raw = encode(candidate)
        recovery = _paths(path)
        _write(recovery['candidate'], raw)
        _write(recovery['temporary'], raw)
        from .windows import reader, replace
        with reader(path) as source:
            info = os.fstat(source.fileno())
            before = source.read(MAX_BYTES + 1)
            if (len(before) > MAX_BYTES or digest(before) != expected.sha256 or
                    (info.st_dev, info.st_ino) != (expected.device, expected.inode)):
                return SaveResult('conflict', recovery_paths=_existing(recovery), error='外部內容已改動，未替換原件。')
            _write(recovery['before'], before)
            checked(root, relative)
            _checkpoint('before-replace')
            replace(path, recovery['temporary'], recovery['external'])
            replaced = True
            _checkpoint('after-replace')
            # The displaced version is authoritative evidence of the race window.
            with reader(recovery['external']) as displaced:
                external = displaced.read(MAX_BYTES + 1)
                if len(external) > MAX_BYTES or digest(external) != expected.sha256:
                    return SaveResult('conflict', recovery_paths=_existing(recovery),
                                      error='替換期間有外部修改；外部與候選版本均在回復檔，需選擇後再保存。')
                loaded = load(root, relative)
                if loaded.receipt.sha256 != digest(raw):
                    return SaveResult('conflict', recovery_paths=_existing(recovery),
                                      error='替換後文件已改動；候選及原版本保留。')
        _cleanup_saved(recovery)
        return SaveResult('saved', loaded.document, loaded.receipt, _existing(recovery))
    except (DocumentError, OSError) as error:
        # ReplaceFile can report partial rename failure (e.g. 1177). Its actual
        # backup plus the independently flushed candidate are deliberately kept.
        uncertain = replaced or recovery.get('external') is not None and recovery['external'].exists()
        status = 'conflict' if uncertain or getattr(error, 'code', '') == 'conflict' else 'failed'
        return SaveResult(status, recovery_paths=_existing(recovery), error=str(error))
    finally:
        protection.close()


def recovery_files(root, relative):
    path = checked(root, relative)
    return tuple(p for p in path.parent.iterdir() if p.name.startswith(path.name + '.recovery-')
                 and p.is_file() and not p.is_symlink())
