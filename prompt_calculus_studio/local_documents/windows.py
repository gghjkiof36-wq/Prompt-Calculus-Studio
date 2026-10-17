"""Windows replacement with an actual displaced-file backup, not hash-as-CAS."""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import os


def _api():
    if os.name != 'nt':
        raise OSError('Verified replacement is available on Windows only.')
    api = ctypes.WinDLL('kernel32', use_last_error=True)
    api.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                               ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    api.CreateFileW.restype = wintypes.HANDLE
    api.ReplaceFileW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                wintypes.DWORD, ctypes.c_void_p, ctypes.c_void_p]
    api.ReplaceFileW.restype = wintypes.BOOL
    api.CloseHandle.argtypes = [wintypes.HANDLE]
    api.CloseHandle.restype = wintypes.BOOL
    return api


def _path(path):
    value = str(path.absolute())
    return '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value


@contextmanager
def reader(path):
    """Pin a stable file against in-place writers while allowing native replace.

    Replacement by another process can still occur. ReplaceFile's backup is
    checked separately; keeping this handle is not presented as a global CAS.
    """
    import msvcrt
    api = _api()
    handle = api.CreateFileW(_path(path), 0x80000000, 0x1 | 0x4, None, 3, 0x80, None)
    if handle == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        fd = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
    except BaseException:
        api.CloseHandle(handle)
        raise
    with os.fdopen(fd, 'rb') as stream:
        yield stream


def replace(destination, temporary, backup):
    api = _api()
    # Do not ignore ACL/merge failures. WRITE_THROUGH is unsupported by this API.
    if not api.ReplaceFileW(_path(destination), _path(temporary), _path(backup), 0, None, None):
        raise ctypes.WinError(ctypes.get_last_error())
