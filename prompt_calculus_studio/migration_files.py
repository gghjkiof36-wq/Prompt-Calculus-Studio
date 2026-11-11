"""Filesystem primitives for copy-first migration (no SQLite or Qt on sources)."""
from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import shutil
import socket
import stat


CLOSE_OLD = '請先關閉舊版 PCS。'
LOCK = '.pcs-migration.lock'
SIDECARS = ('-wal', '-shm', '-journal')
IMAGES = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.bmp', '.tif', '.tiff', '.avif'}


class MigrationFailure(ValueError):
    def __init__(self, reason, *, locations=(), inflight=()):
        super().__init__(reason)
        self.locations = list(locations)
        self.inflight = list(inflight)


def reparse(path):
    info = Path(path).lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def physical(path):
    path = Path(os.path.abspath(path))
    for part in (path, *path.parents):
        if os.path.lexists(part) and reparse(part):
            raise MigrationFailure('請選擇實體資料夾，不能使用符號連結或 junction。')
    return path.resolve()


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def inventory(root, *, hashes=True):
    """Include excluded files too: source stability applies to the whole delivery."""
    root = physical(root)
    result = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in sorted(directory.iterdir()):
            if reparse(path):
                raise MigrationFailure('來源包含連結或重解析點。', locations=[str(path.relative_to(root))])
            info = path.stat()
            if stat.S_ISDIR(info.st_mode):
                pending.append(path)
            elif stat.S_ISREG(info.st_mode):
                row = dict(size=info.st_size, mtime_ns=info.st_mtime_ns)
                if hashes:
                    row['sha256'] = digest(path)
                result[path.relative_to(root).as_posix()] = row
            else:
                raise MigrationFailure('來源包含未知檔案類型。', locations=[str(path.relative_to(root))])
    return result


def unchanged(root, before, *, hashes=False):
    try:
        after = inventory(root, hashes=hashes)
    except (OSError, MigrationFailure):
        raise MigrationFailure(CLOSE_OLD) from None
    expected = before if hashes else {k: {f: v[f] for f in ('size', 'mtime_ns')} for k, v in before.items()}
    if after != expected:
        raise MigrationFailure(CLOSE_OLD)


def process_name(pid):
    """Return a live process image; never signal, lock, or open a source DB."""
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, pid)
        if not handle:
            # Access denied is not proof of a stale lock.
            if ctypes.get_last_error() == 5:
                return '?'
            return None
        try:
            size = wintypes.DWORD(32768)
            value = ctypes.create_unicode_buffer(size.value)
            return value.value if kernel.QueryFullProcessImageNameW(handle, 0, value, ctypes.byref(size)) else '?'
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        return '?'
    try:
        return os.readlink('/proc/' + str(pid) + '/exe')
    except OSError:
        return '?'


def check_source_locks(source):
    for relative in ('data/workspace.lock', 'data/studio.lock', 'studio.lock'):
        path = source / relative
        if not path.exists():
            continue
        physical(path)
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
            pid = int(lines[0])
        except (ValueError, IndexError, UnicodeError):
            raise MigrationFailure('舊版鎖檔無法辨識，請先確認舊版已關閉。', locations=[relative]) from None
        if pid <= 0:
            continue
        if len(lines) > 2 and lines[2] and lines[2].casefold() != socket.gethostname().casefold():
            raise MigrationFailure(CLOSE_OLD)
        name = process_name(pid)
        if name is not None:
            short = Path(name).name.casefold().replace(' ', '').replace('_', '').replace('-', '')
            if name == '?' or 'python' in short or 'pcs' in short or 'promptcalculus' in short or 'promptstudio' in short:
                raise MigrationFailure(CLOSE_OLD)


@contextmanager
def destination_lock(base):
    """OS locks release on process death; keep the inode to avoid unlink races."""
    path = physical(base / LOCK)
    with path.open('a+b') as stream:
        if path.stat().st_size == 0:
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise MigrationFailure('這個目的地正在搬遷，請等候完成。') from None
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == 'nt':
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def remove_staging(base, folder):
    """Only direct, physical, marked children may be recursively removed."""
    base, folder = physical(base), physical(folder)
    if folder.parent != base or not folder.name.startswith('migrating-') or not (folder / 'INCOMPLETE').is_file():
        raise MigrationFailure('暫存目錄不符合清理條件。')
    # Refuse nested reparse points too; never traverse a replaced subtree.
    inventory(folder, hashes=False)
    shutil.rmtree(folder)


def require_space(base, required):
    free = shutil.disk_usage(base).free
    if free < required:
        raise MigrationFailure('目的地空間不足，尚未完成搬遷。')
    return free


def copy_file(source, target, *, hardlink=False, reserve=0):
    physical(source)
    physical(target.parent)
    info = source.stat()
    target.parent.mkdir(parents=True, exist_ok=True)
    if hardlink:
        try:
            os.link(source, target)
            return 'hardlink'
        except OSError:
            require_space(target.parent, reserve + source.stat().st_size)
    with source.open('rb') as incoming, target.open('xb') as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    # ModelRegistry validates registered weights by size and mtime as well as
    # path. Copy metadata without opening or modifying the source via SQLite.
    os.utime(target, ns=(info.st_atime_ns, info.st_mtime_ns))
    return 'copy'
