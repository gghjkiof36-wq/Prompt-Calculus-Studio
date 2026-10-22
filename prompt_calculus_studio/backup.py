"""Stream a portable ZIP, preserving the previous backup until publication."""
from contextlib import ExitStack
import ctypes
from ctypes import wintypes
import hashlib
import os
from pathlib import Path
import uuid
import zipfile

from .local_documents import paths, windows
from .local_documents.directory_pin import pinned_parent


class BackupResult(str):
    """Keep the path API while exposing optional recovery-cleanup warnings."""
    def __new__(cls, path, recovery_paths=()):
        result = super().__new__(cls, str(path))
        result.recovery_paths = tuple(map(str, recovery_paths))
        return result


def _create_private(path, source):
    """Create exclusively with the existing backup's DACL, before any bytes.

    A destination may be more restricted than its parent directory. Copy its
    DACL from the held handle to both staging and recovery files; inheriting
    the directory alone could expose their contents before native replacement.
    New destinations retain the parent directory's normal inheritance.
    """
    import msvcrt
    descriptor = security = None
    if source is not None:
        api = ctypes.WinDLL('advapi32', use_last_error=True)
        api.GetKernelObjectSecurity.argtypes = [wintypes.HANDLE, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        api.GetKernelObjectSecurity.restype = wintypes.BOOL
        size = wintypes.DWORD()
        handle = msvcrt.get_osfhandle(source.fileno())
        if not api.GetKernelObjectSecurity(handle, 4, None, 0, ctypes.byref(size)):
            if ctypes.get_last_error() != 122:  # ERROR_INSUFFICIENT_BUFFER
                raise ctypes.WinError(ctypes.get_last_error())
        descriptor = ctypes.create_string_buffer(size.value)
        if not api.GetKernelObjectSecurity(handle, 4, descriptor, size, ctypes.byref(size)):
            raise ctypes.WinError(ctypes.get_last_error())
        class SecurityAttributes(ctypes.Structure):
            _fields_ = [('length', wintypes.DWORD), ('descriptor', ctypes.c_void_p),
                        ('inherit_handle', wintypes.BOOL)]
        security = SecurityAttributes(ctypes.sizeof(SecurityAttributes),
            ctypes.cast(descriptor, ctypes.c_void_p), False)
    native = windows._api()
    handle = native.CreateFileW(windows._path(path), 0xC0000000, 0,
        ctypes.byref(security) if security is not None else None, 1, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        native.CloseHandle(handle)
        path.unlink(missing_ok=True)
        raise
    try:
        return os.fdopen(fd, 'w+b')
    except BaseException:
        os.close(fd)
        path.unlink(missing_ok=True)
        raise


def _check_cancel(cancel):
    if cancel is not None and cancel.is_set():
        raise ValueError("已取消備份；既有 ZIP 未變更。")


def _digest(stream, cancel=None, copy_to=None):
    """Bound memory even when the existing backup is many gigabytes."""
    digest = hashlib.sha256()
    while True:
        _check_cancel(cancel)
        chunk = stream.read(1024 * 1024)
        if not chunk:
            return digest.digest()
        digest.update(chunk)
        if copy_to is not None:
            copy_to.write(chunk)


def _flush(stream):
    stream.flush()
    os.fsync(stream.fileno())


def _write_archive(directory, snapshot, stream, cancel):
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        _check_cancel(cancel)
        archive.write(snapshot, "data/studio.sqlite3")
        for name in ("thumbnails", "originals", "civitai/downloads"):
            folder = directory / name
            if not folder.is_dir():
                continue
            for path in folder.rglob('*'):
                _check_cancel(cancel)
                if path.is_file() and path.resolve().is_relative_to(directory):
                    archive.write(path, "data/" + path.relative_to(directory).as_posix())


def archive_data(directory, snapshot, destination, cancel=None):
    """Publish a complete ZIP or report explicitly retained recovery files.

    As with document publication, Windows replacement keeps the actual
    displaced file and merges its ACL. A preflight hash is not atomic CAS;
    an uncertain replacement retains recovery files and never rolls back a
    possibly newer destination. ZIP payloads and verification are streamed.
    """
    _check_cancel(cancel)
    directory = Path(directory).resolve()
    destination = Path(destination).absolute()
    # Pin before resolving the destination, through staging and cleanup.
    with pinned_parent(destination), ExitStack() as held:
        destination = paths.checked(destination.parent, destination.name)
        if destination.is_relative_to(directory):
            raise ValueError("請將 ZIP 備份儲存在 data 資料夾外。")
        source = (held.enter_context(windows.reader(destination))
                  if destination.exists() else None)
        prefix = '.pcs-backup-' + uuid.uuid4().hex
        temporary, original, displaced = [destination.with_name(prefix + suffix)
                                          for suffix in ('.partial', '.before', '.external')]
        created = []
        attempted = False
        try:
            with _create_private(temporary, source) as stream:
                created.append(temporary)
                _write_archive(directory, snapshot, stream, cancel)
                _check_cancel(cancel)  # Also covers a database-only backup.
                _flush(stream)
                stream.seek(0)
                expected = _digest(stream, cancel)
            if source is not None:
                with _create_private(original, source) as recovery:
                    created.append(original)
                    before = _digest(source, cancel, recovery)
                    _flush(recovery)
                paths.checked(destination.parent, destination.name)
                with windows.reader(destination) as current:
                    if _digest(current, cancel) != before:
                        raise ValueError('備份檔已被其他程式修改；尚未覆寫，請重新選擇。')
                paths.checked(displaced.parent, displaced.name, new=True)
            else:
                paths.checked(destination.parent, destination.name, new=True)
            _check_cancel(cancel)
            # Cancellation after this boundary cannot undo publication.
            attempted = True
            if source is None:
                os.rename(temporary, destination)  # Exclusive on Windows.
            else:
                windows.replace(destination, temporary, displaced)
                with windows.reader(displaced) as previous:
                    if _digest(previous) != before:
                        raise ValueError('替換時發現外部修改；已保留恢復版本，請先檢查。')
            paths.checked(destination.parent, destination.name)
            with windows.reader(destination) as published:
                if _digest(published) != expected:
                    raise ValueError('發布後備份檔再次被修改；請先檢查保留的恢復版本。')
        except Exception as error:
            if attempted:
                # ReplaceFile may move the original and then fail. Never
                # delete the displaced file or blindly restore over a newer one.
                recovery = [p for p in (temporary, original, displaced) if p.exists()]
                message = '備份發布未能確認完成；請先檢查目的 ZIP，再決定是否重試。'
                if recovery:
                    message += '\n保留的恢復檔：\n' + '\n'.join(map(str, recovery))
                raise OSError(message + '\n原因：' + str(error)) from error
            leftovers = []
            for item in created:
                try:
                    item.unlink(missing_ok=True)
                except OSError:
                    leftovers.append(str(item))
            if leftovers:
                raise OSError(str(error) + '\n未發布的暫存檔（不可當作完成備份）：\n'
                              + '\n'.join(leftovers)) from error
            raise
        # Verified publication is successful even if optional recovery cleanup
        # is denied. These uniquely named files never masquerade as the ZIP.
        leftovers = []
        for item in (temporary, original, displaced):
            try:
                item.unlink(missing_ok=True)
            except OSError:
                leftovers.append(item)
    return BackupResult(destination, leftovers)
