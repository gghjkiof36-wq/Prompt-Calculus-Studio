"""Stream a portable ZIP, preserving the previous backup until publication."""
from contextlib import ExitStack, contextmanager
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


def _mark_delete(handle):
    """Delete this held object on close, never an unchecked pathname."""
    class Disposition(ctypes.Structure):
        _fields_ = [('delete', ctypes.c_ubyte)]  # Windows BOOLEAN, not BOOL.
    native = windows._api()
    native.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD]
    native.SetFileInformationByHandle.restype = wintypes.BOOL
    disposition = Disposition(1)
    if not native.SetFileInformationByHandle(handle, 4, ctypes.byref(disposition),
                                            ctypes.sizeof(disposition)):
        raise ctypes.WinError(ctypes.get_last_error())


def _delete_stream(stream):
    import msvcrt
    _mark_delete(msvcrt.get_osfhandle(stream.fileno()))


@contextmanager
def _read_original(path):
    """Hold the original against writes/replacement until our handle moves it."""
    import msvcrt
    native = windows._api()
    handle = native.CreateFileW(windows._path(path), 0x80010000, 1, None, 3,
                                0x00200080, None)  # READ|DELETE, don't follow links.
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        native.CloseHandle(handle)
        raise
    try:
        stream = os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise
    with stream:
        if getattr(os.fstat(fd), 'st_file_attributes', 0) & 0x400:
            raise ValueError('備份目的檔不能是重新解析點。')
        yield stream


def _rename_owned(stream, destination):
    """Rename the held object without replacing an existing destination."""
    import msvcrt
    class Rename(ctypes.Structure):
        _fields_ = [('replace', ctypes.c_ubyte), ('root', wintypes.HANDLE),
                    ('length', wintypes.DWORD), ('name', wintypes.WCHAR * 1)]
    encoded = windows._path(destination).encode('utf-16-le')
    buffer = ctypes.create_string_buffer(Rename.name.offset + len(encoded) + 2)
    info = ctypes.cast(buffer, ctypes.POINTER(Rename)).contents
    info.replace, info.root, info.length = 0, None, len(encoded)
    ctypes.memmove(ctypes.addressof(buffer) + Rename.name.offset, encoded, len(encoded))
    native = windows._api()
    native.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                 ctypes.c_void_p, wintypes.DWORD]
    native.SetFileInformationByHandle.restype = wintypes.BOOL
    if not native.SetFileInformationByHandle(msvcrt.get_osfhandle(stream.fileno()),
                                            3, buffer, len(buffer)):
        raise ctypes.WinError(ctypes.get_last_error())


def _publish(source, stream, destination, displaced):
    # No ACL merge and no overwrite of a late arrival. Between these moves the
    # destination name is absent; failure retains the original at displaced.
    if source is not None:
        _rename_owned(source, displaced)
    _rename_owned(stream, destination)


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
    handle = native.CreateFileW(windows._path(path), 0xC0010000, 0,
        ctypes.byref(security) if security is not None else None, 1, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    except BaseException:
        try:
            try:
                _mark_delete(handle)
            except OSError as cleanup_error:
                raise OSError('未發布的暫存檔無法清理：' + str(path)) from cleanup_error
        finally:
            native.CloseHandle(handle)
        raise
    try:
        return os.fdopen(fd, 'w+b')
    except BaseException:
        try:
            try:
                _mark_delete(handle)
            except OSError as cleanup_error:
                raise OSError('未發布的暫存檔無法清理：' + str(path)) from cleanup_error
        finally:
            os.close(fd)
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

    The original and candidate stay held through two exclusive handle renames.
    Their DACLs never merge with a competing destination. This is not a single
    atomic name replacement: there is a short gap, and an interrupted publish
    may leave the old ZIP only at its explicit recovery path. Never roll back
    over a late arrival. ZIP payloads and verification are streamed.
    """
    _check_cancel(cancel)
    directory = Path(directory).resolve()
    destination = Path(destination).absolute()
    # Pin before resolving the destination, through staging and cleanup.
    with pinned_parent(destination), ExitStack() as held:
        destination = paths.checked(destination.parent, destination.name)
        if destination.is_relative_to(directory):
            raise ValueError("請將 ZIP 備份儲存在 data 資料夾外。")
        source = (held.enter_context(_read_original(destination))
                  if destination.exists() else None)
        prefix = '.pcs-backup-' + uuid.uuid4().hex
        temporary, original, displaced = [destination.with_name(prefix + suffix)
                                          for suffix in ('.partial', '.before', '.external')]
        created = []
        recovery = None
        attempted = False
        try:
            stream = held.enter_context(_create_private(temporary, source))
            created.append((temporary, stream))
            _write_archive(directory, snapshot, stream, cancel)
            _check_cancel(cancel)  # Also covers a database-only backup.
            _flush(stream)
            stream.seek(0)
            expected = _digest(stream, cancel)
            if source is not None:
                recovery = held.enter_context(_create_private(original, source))
                created.append((original, recovery))
                before = _digest(source, cancel, recovery)
                _flush(recovery)
                paths.checked(destination.parent, destination.name)
                paths.checked(displaced.parent, displaced.name, new=True)
            else:
                paths.checked(destination.parent, destination.name, new=True)
            _check_cancel(cancel)
            # Cancellation after this boundary cannot undo publication.
            attempted = True
            _publish(source, stream, destination, displaced)
            if source is not None:
                source.seek(0)
                if _digest(source) != before:
                    raise ValueError('原件恢復副本內容無法確認；已保留所有版本。')
            paths.checked(destination.parent, destination.name)
            stream.seek(0)
            if _digest(stream) != expected:
                raise ValueError('發布後備份內容無法確認；請先檢查保留的恢復版本。')
        except Exception as error:
            if attempted:
                # The first rename may succeed and the second fail. Never
                # delete recovery objects or restore over a competing name.
                retained = [p for p in (temporary, original, displaced) if p.exists()]
                message = '備份發布未能確認完成；請先檢查目的 ZIP，再決定是否重試。'
                if retained:
                    message += '\n保留的恢復檔候選（路徑可能已被其他程式更動）：\n' + '\n'.join(map(str, retained))
                raise OSError(message + '\n原因：' + str(error)) from error
            leftovers = []
            for item, owned in created:
                try:
                    _delete_stream(owned)
                except OSError:
                    leftovers.append(str(item))
            if leftovers:
                raise OSError(str(error) + '\n未發布的暫存檔（不可當作完成備份）：\n'
                              + '\n'.join(leftovers)) from error
            raise
        # Only clean our continuously held recovery objects, never their names.
        leftovers = []
        if recovery is not None:
            try:
                _delete_stream(recovery)
            except OSError:
                leftovers.append(original)
            try:
                _delete_stream(source)
            except OSError:
                leftovers.append(displaced)
    return BackupResult(destination, leftovers)
