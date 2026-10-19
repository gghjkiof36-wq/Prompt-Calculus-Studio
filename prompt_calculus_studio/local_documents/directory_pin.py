"""Keep the Windows publication directory chain fixed until all I/O ends.

No path-based payload I/O is allowed before this context has been entered.
Each directory is opened relative to its pinned parent without following
reparse points. Denying delete sharing prevents renames; a delete-on-close
empty guard keeps the leaf nonempty, preventing an in-place junction change.
The pinned child directories keep every ancestor nonempty for the same reason.
"""
from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from functools import lru_cache
import os
from pathlib import Path
import uuid

from .windows import _api, _path


class _UnicodeString(ctypes.Structure):
    _fields_ = [('Length', wintypes.USHORT), ('MaximumLength', wintypes.USHORT),
                ('Buffer', wintypes.LPWSTR)]


class _ObjectAttributes(ctypes.Structure):
    _fields_ = [('Length', wintypes.ULONG), ('RootDirectory', wintypes.HANDLE),
                ('ObjectName', ctypes.POINTER(_UnicodeString)),
                ('Attributes', wintypes.ULONG), ('SecurityDescriptor', ctypes.c_void_p),
                ('SecurityQualityOfService', ctypes.c_void_p)]


class _IoStatus(ctypes.Structure):
    _fields_ = [('Status', ctypes.c_void_p), ('Information', ctypes.c_size_t)]


class _AttributeTag(ctypes.Structure):
    _fields_ = [('FileAttributes', wintypes.DWORD), ('ReparseTag', wintypes.DWORD)]


@lru_cache(maxsize=1)
def _native():
    api = _api()
    api.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                ctypes.c_void_p, wintypes.DWORD]
    api.GetFileInformationByHandleEx.restype = wintypes.BOOL
    nt = ctypes.WinDLL('ntdll')
    nt.NtCreateFile.argtypes = [ctypes.POINTER(wintypes.HANDLE), wintypes.DWORD,
        ctypes.POINTER(_ObjectAttributes), ctypes.POINTER(_IoStatus), ctypes.c_void_p,
        wintypes.ULONG, wintypes.ULONG, wintypes.ULONG, wintypes.ULONG,
        ctypes.c_void_p, wintypes.ULONG]
    nt.NtCreateFile.restype = wintypes.LONG
    nt.RtlNtStatusToDosError.argtypes = [wintypes.LONG]
    nt.RtlNtStatusToDosError.restype = wintypes.ULONG
    return api, nt


def _relative(parent, name, *, guard=False):
    """One component only; never reopen the ancestors by their path names."""
    if not name or name in ('.', '..') or any(c in name for c in '/\\:'):
        raise ValueError('保存資料夾的路徑無效。')
    _, nt = _native()
    buffer = ctypes.create_unicode_buffer(name)
    size = len(name.encode('utf-16-le'))
    if size > 65532:
        raise ValueError('保存資料夾的名稱過長。')
    value = _UnicodeString(size, size + 2, ctypes.cast(buffer, wintypes.LPWSTR))
    attributes = _ObjectAttributes(ctypes.sizeof(_ObjectAttributes), parent,
        ctypes.pointer(value), 0x1040, None, None)  # CASE_INSENSITIVE | DONT_REPARSE
    handle, status = wintypes.HANDLE(), _IoStatus()
    # SYNCHRONIZE | FILE_READ_ATTRIBUTES, plus DELETE for the guard or
    # FILE_LIST_DIRECTORY | FILE_TRAVERSE for a directory. Sharing excludes
    # FILE_SHARE_DELETE but permits ordinary child-file writes/replacements.
    access = 0x100080 | (0x10000 if guard else 0x21)
    options = 0x200020 | (0x1040 if guard else 0x1)
    result = nt.NtCreateFile(ctypes.byref(handle), access, ctypes.byref(attributes),
        ctypes.byref(status), None, 0x102 if guard else 0, 0x3,
        2 if guard else 1, options, None, 0)
    if result < 0:
        raise ctypes.WinError(nt.RtlNtStatusToDosError(result))
    return handle.value


def _check_directory(handle):
    api, _ = _native()
    info = _AttributeTag()
    if not api.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not info.FileAttributes & 0x10 or info.FileAttributes & 0x400:
        raise ValueError('保存資料夾不可經過連結或重解析點。')


@contextmanager
def pinned_parent(path):
    """Fail before staging bytes if a complete directory pin cannot be held.

    Locks cover preflight, candidate commit, native replacement, recovery
    diagnostics and cleanup. A zero-byte guard is removed by its handle even
    on failure; no pathname-based guard cleanup can target another directory.
    """
    if os.name != 'nt':
        raise OSError('目前系統無法固定保存資料夾，尚未保存。')
    parent = Path(path).absolute().parent
    api, _ = _native()
    with ExitStack() as handles:
        anchor = api.CreateFileW(_path(Path(parent.anchor)), 0x1000a1, 0x3,
                                 None, 3, 0x02200000, None)
        if anchor == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        handles.callback(api.CloseHandle, anchor)
        _check_directory(anchor)
        chain = [anchor]
        for component in parent.parts[1:]:
            child = _relative(chain[-1], component)
            handles.callback(api.CloseHandle, child)
            _check_directory(child)
            chain.append(child)
        guard = _relative(chain[-1], '.pcs-save-' + uuid.uuid4().hex + '.guard', guard=True)
        handles.callback(api.CloseHandle, guard)
        # Check again after creating the guard. Once all pinned directories
        # are nonempty, neither renaming nor converting them to junctions is
        # possible until we release the handles. No private bytes precede this.
        for handle in chain:
            _check_directory(handle)
        yield
