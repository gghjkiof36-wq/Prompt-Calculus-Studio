"""Read enrolled local files by handle. Never place guards in source folders."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
import ctypes
import os
import stat

from ..conversation_archive import identity, read_chunks
from ..local_documents import directory_pin as pin, paths


class OutsideSource(ValueError):
    pass


def parts(relative, *, allow_hidden=False):
    names = paths.relative_parts(relative)
    if not allow_hidden and any(name.startswith('.') for name in names):
        raise OutsideSource('不讀取隱藏檔案或內部資料夾。')
    return names


def local_folder(value, *, allow_hidden=False):
    path = Path(value)
    if not allow_hidden and any(p.startswith('.') for p in path.parts):
        raise OutsideSource('不讀取隱藏資料夾或其子目錄。')
    if not path.is_absolute() or '..' in path.parts or path == Path(path.anchor):
        raise OutsideSource('請選擇本機資料夾，範圍不能是整個磁碟。')
    if os.name == 'nt' and (len(path.drive) != 2 or path.drive[1] != ':'):
        raise OutsideSource('請選擇本機磁碟上的資料夾。')
    return path


@contextmanager
def directory(root, relative='', expected=None, *, allow_hidden=False):
    """Deny ancestor rename and pin the last directory against reparse changes."""
    root = local_folder(root, allow_hidden=allow_hidden)
    nested = parts(relative, allow_hidden=allow_hidden) if relative else []
    directory_path = root.joinpath(*nested)
    names = [*root.parts[1:], *nested]
    with ExitStack() as handles:
        if os.name == 'nt':
            api, _ = pin._native()
            handle = api.CreateFileW(pin._path(Path(root.anchor)), 0x1000a1, 3,
                                     None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                raise ctypes.WinError(ctypes.get_last_error())
            handles.callback(api.CloseHandle, handle)
            pin._check_directory(handle)
        else:
            flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
            handle = os.open(root.anchor, flags)
            handles.callback(os.close, handle)
        for index, name in enumerate(names):
            if os.name == 'nt':
                handle = pin._relative(handle, name, sharing=1 if index == len(names)-1 else 3)
                handles.callback(api.CloseHandle, handle)
                pin._check_directory(handle)
            else:
                handle = os.open(name, flags, dir_fd=handle)
                handles.callback(os.close, handle)
            if index + 2 == len(root.parts):
                info = root.stat() if os.name == 'nt' else os.fstat(handle)
                root_id = (info.st_dev, info.st_ino)
                if expected is not None and tuple(expected) != root_id:
                    raise OutsideSource('來源資料夾已被替換，請重新指定。')
        yield handle, directory_path, root_id


@contextmanager
def verified_stream(root, relative, expected=None, *, allow_hidden=False):
    names = parts(relative, allow_hidden=allow_hidden)
    with directory(root, '/'.join(names[:-1]), expected, allow_hidden=allow_hidden) as (parent, folder, _):
        if os.name == 'nt':
            import msvcrt
            api, _ = pin._native()
            try:
                handle = pin._relative(parent, names[-1], read_file=True, sharing=1)
            except OSError:
                if os.path.lexists(folder / names[-1]) and paths.is_link(folder / names[-1]):
                    raise OutsideSource('不讀取連結或重解析點。') from None
                raise
            try:
                flags = pin._AttributeTag()
                if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(flags), ctypes.sizeof(flags)):
                    raise ctypes.WinError(ctypes.get_last_error())
                if flags.FileAttributes & (0x400 | 0x10):
                    raise OutsideSource('不讀取連結或重解析點。')
                fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
            except BaseException:
                api.CloseHandle(handle)
                raise
        else:
            fd = os.open(names[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        with os.fdopen(fd, 'rb') as stream:
            before = os.fstat(fd)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
                raise OutsideSource('不讀取硬連結或特殊檔案。')
            yield stream, before
            latest = (os.stat(names[-1], dir_fd=parent, follow_symlinks=False) if os.name != 'nt'
                      else (folder / names[-1]).lstat())
            after = os.fstat(fd)
            if identity(before) != identity(after) or identity(after) != identity(latest) or after.st_nlink != 1:
                raise OutsideSource('來源在讀取期間已改變，請重新整理。')


def read_source(root, relative, expected=None, limit=8*1024*1024, cancel=None, *, allow_hidden=False):
    with verified_stream(root, relative, expected, allow_hidden=allow_hidden) as (stream, info):
        if info.st_size > limit:
            raise ValueError(f'檔案超過 {limit // 1024 // 1024} MiB。')
        raw = read_chunks(stream, limit, cancel)
        if len(raw) != info.st_size:
            raise ValueError('來源在讀取期間已改變。')
        return raw, info


@contextmanager
def pin_database_file(path):
    """SQLite reopens names. Deny replacement of its checked local files during use."""
    if os.name != 'nt':
        raise OSError('目前僅支援 Windows 本機工作資源。')
    import msvcrt
    api, _ = pin._native()
    handle = api.CreateFileW(pin._path(path), 0x80000000, 3, None, 4, 0x00200080, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        tag = pin._AttributeTag()
        if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(tag), ctypes.sizeof(tag)):
            raise ctypes.WinError(ctypes.get_last_error())
        if tag.FileAttributes & (0x400 | 0x10):
            raise OutsideSource('索引檔案位置含有重解析點。')
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OutsideSource('索引檔案不可為硬連結。')
        yield
